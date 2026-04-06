"""
discovery.py — Zero-Configuration Peer Discovery via UDP Multicast

This module implements a fully asynchronous peer discovery daemon for a
decentralized LAN chat application. Peers announce their presence on a
multicast group and automatically discover each other without any manual
IP configuration.

Multicast Group Configuration
─────────────────────────────
  Group Address : 239.77.69.83   (administratively-scoped, safe for LANs)
  Port          : 50692
  TTL           : 1              (packets never leave the local subnet)

The group address 239.x.x.x falls within the "Administratively Scoped"
range (239.0.0.0/8) defined in RFC 2365, which is specifically reserved
for organisation-local multicast — perfect for LAN-only traffic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

# ─── Constants ────────────────────────────────────────────────────────────────

MULTICAST_GROUP: str = "239.77.69.83"
MULTICAST_PORT: int = 50692
MULTICAST_TTL: int = 1  # Stay on the local subnet

BROADCAST_INTERVAL: float = 5.0   # seconds between presence announcements
PRUNE_INTERVAL: float = 5.0       # how often the janitor runs
PEER_TIMEOUT: float = 15.0        # mark peer offline after this silence

logger: logging.Logger = logging.getLogger("echo.discovery")


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PeerInfo:
    """Immutable snapshot of a discovered peer."""
    node_id: str
    username: str
    ip: str
    first_seen: float
    last_seen: float


@dataclass
class _PeerRecord:
    """Internal mutable record for a tracked peer."""
    node_id: str
    username: str
    ip: str
    first_seen: float
    last_seen: float

    def to_info(self) -> PeerInfo:
        return PeerInfo(
            node_id=self.node_id,
            username=self.username,
            ip=self.ip,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
        )


# ─── Multicast Protocol (asyncio transport/protocol layer) ───────────────────

class _MulticastListenerProtocol(asyncio.DatagramProtocol):
    """asyncio protocol that feeds received datagrams into a callback."""

    def __init__(self, on_datagram: Callable[[bytes, tuple[str, int]], None]) -> None:
        self._on_datagram = on_datagram

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport
        logger.debug("Multicast listener transport established.")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.error("Multicast socket error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            logger.warning("Multicast listener connection lost: %s", exc)


# ─── Core Discovery Engine ───────────────────────────────────────────────────

class PeerDiscovery:
    """
    Manages zero-configuration peer discovery over UDP multicast.

    Usage
    ─────
        discovery = PeerDiscovery(username="alice")
        await discovery.start()
        ...
        peers = discovery.get_peers()
        ...
        await discovery.stop()
    """

    def __init__(
        self,
        username: str,
        node_id: Optional[str] = None,
        multicast_group: str = MULTICAST_GROUP,
        multicast_port: int = MULTICAST_PORT,
        broadcast_interval: float = BROADCAST_INTERVAL,
        peer_timeout: float = PEER_TIMEOUT,
        on_peer_joined: Optional[Callable[[PeerInfo], Any]] = None,
        on_peer_left: Optional[Callable[[PeerInfo], Any]] = None,
    ) -> None:
        self._username: str = username
        self._node_id: str = node_id or uuid.uuid4().hex[:12]
        self._group: str = multicast_group
        self._port: int = multicast_port
        self._broadcast_interval: float = broadcast_interval
        self._peer_timeout: float = peer_timeout

        # Callbacks for peer lifecycle events
        self._on_peer_joined: Optional[Callable[[PeerInfo], Any]] = on_peer_joined
        self._on_peer_left: Optional[Callable[[PeerInfo], Any]] = on_peer_left

        # Peer registry: node_id -> _PeerRecord
        self._peers: Dict[str, _PeerRecord] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

        # Internal handles
        self._broadcast_task: Optional[asyncio.Task[None]] = None
        self._prune_task: Optional[asyncio.Task[None]] = None
        self._listener_transport: Optional[asyncio.DatagramTransport] = None
        self._sender_sock: Optional[socket.socket] = None
        self._running: bool = False

        logger.info(
            "PeerDiscovery initialised — node_id=%s, user=%s, group=%s:%d",
            self._node_id, self._username, self._group, self._port,
        )

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def username(self) -> str:
        return self._username

    @property
    def is_running(self) -> bool:
        return self._running

    def get_peers(self) -> Dict[str, PeerInfo]:
        """Return a snapshot of the current peer registry."""
        return {nid: rec.to_info() for nid, rec in self._peers.items()}

    def get_peer_count(self) -> int:
        return len(self._peers)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Spin up the broadcaster, listener, and pruner."""
        if self._running:
            logger.warning("Discovery daemon already running — ignoring start().")
            return

        self._running = True
        logger.info("Starting discovery daemon …")

        # 1. Create the sender socket (plain UDP, multicast TTL restricted)
        self._sender_sock = self._create_sender_socket()

        # 2. Bind the multicast listener via asyncio
        await self._bind_listener()

        # 3. Launch background tasks
        self._broadcast_task = asyncio.create_task(
            self._broadcast_loop(), name="discovery-broadcast"
        )
        self._prune_task = asyncio.create_task(
            self._prune_loop(), name="discovery-prune"
        )

        logger.info("Discovery daemon is live.")

    async def stop(self) -> None:
        """Gracefully tear down all sockets and tasks."""
        if not self._running:
            return

        self._running = False
        logger.info("Shutting down discovery daemon …")

        # Cancel tasks
        for task in (self._broadcast_task, self._prune_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close transports / sockets
        if self._listener_transport:
            self._listener_transport.close()
            self._listener_transport = None

        if self._sender_sock:
            self._sender_sock.close()
            self._sender_sock = None

        logger.info("Discovery daemon stopped.")

    # ── Socket Setup ──────────────────────────────────────────────────────

    def _create_sender_socket(self) -> socket.socket:
        """Create a UDP socket configured for multicast sending."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, MULTICAST_TTL)
        # Allow loopback so we can test on a single machine
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.setblocking(False)
        logger.debug("Sender socket created (TTL=%d).", MULTICAST_TTL)
        return sock

    async def _bind_listener(self) -> None:
        """Bind a multicast listener using the asyncio event loop."""
        loop = asyncio.get_running_loop()

        # Build a socket that can receive multicast
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Allow multiple instances on the same host (macOS / Linux)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                logger.debug("SO_REUSEPORT not available on this platform.")

        try:
            sock.bind(("", self._port))
        except OSError as exc:
            logger.error(
                "Failed to bind listener on port %d: %s. "
                "Another instance may already be running.",
                self._port, exc,
            )
            sock.close()
            raise

        # Join the multicast group
        mreq = struct.pack(
            "4sL",
            socket.inet_aton(self._group),
            socket.INADDR_ANY,
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)

        transport, _ = await loop.create_datagram_endpoint(
            lambda: _MulticastListenerProtocol(self._handle_datagram),
            sock=sock,
        )
        self._listener_transport = transport  # type: ignore[assignment]
        logger.info("Listening on multicast group %s:%d", self._group, self._port)

    # ── Broadcasting ──────────────────────────────────────────────────────

    def _build_presence_payload(self) -> bytes:
        """Build the JSON presence announcement."""
        payload: Dict[str, Any] = {
            "type": "presence",
            "node_id": self._node_id,
            "username": self._username,
            "ts": time.time(),
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    async def _broadcast_loop(self) -> None:
        """Periodically announce our presence on the multicast group."""
        logger.debug("Broadcast loop started (interval=%.1fs).", self._broadcast_interval)
        loop = asyncio.get_running_loop()

        while self._running:
            try:
                data = self._build_presence_payload()
                await loop.sock_sendto(
                    self._sender_sock,  # type: ignore[arg-type]
                    data,
                    (self._group, self._port),
                )
                logger.debug("Presence broadcast sent (%d bytes).", len(data))
            except OSError as exc:
                logger.error("Broadcast send failed: %s", exc)

            await asyncio.sleep(self._broadcast_interval)

    # ── Receiving ─────────────────────────────────────────────────────────

    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process an incoming multicast datagram (called from the protocol)."""
        try:
            payload: Dict[str, Any] = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Malformed datagram from %s: %s", addr, exc)
            return

        if payload.get("type") != "presence":
            logger.debug("Ignoring non-presence payload from %s.", addr)
            return

        node_id: Optional[str] = payload.get("node_id")
        username: Optional[str] = payload.get("username")

        if not node_id or not username:
            logger.warning("Incomplete presence payload from %s.", addr)
            return

        # Ignore our own announcements
        if node_id == self._node_id:
            return

        now = time.time()
        sender_ip = addr[0]

        # Update or insert peer — schedule the coroutine from sync context
        asyncio.ensure_future(
            self._upsert_peer(node_id, username, sender_ip, now)
        )

    async def _upsert_peer(
        self, node_id: str, username: str, ip: str, now: float
    ) -> None:
        """Insert a new peer or refresh its last-seen timestamp."""
        async with self._lock:
            existing = self._peers.get(node_id)
            if existing:
                existing.last_seen = now
                existing.username = username  # allow name changes
                existing.ip = ip
                logger.debug(
                    "Peer refreshed: %s (%s) @ %s", username, node_id, ip
                )
            else:
                record = _PeerRecord(
                    node_id=node_id,
                    username=username,
                    ip=ip,
                    first_seen=now,
                    last_seen=now,
                )
                self._peers[node_id] = record
                logger.info("🟢 Peer joined: %s (%s) @ %s", username, node_id, ip)

                if self._on_peer_joined:
                    try:
                        result = self._on_peer_joined(record.to_info())
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("on_peer_joined callback error")

    # ── Pruning ───────────────────────────────────────────────────────────

    async def _prune_loop(self) -> None:
        """Periodically remove peers that have gone silent."""
        logger.debug(
            "Prune loop started (interval=%.1fs, timeout=%.1fs).",
            PRUNE_INTERVAL, self._peer_timeout,
        )

        while self._running:
            await asyncio.sleep(PRUNE_INTERVAL)
            await self._prune_stale_peers()

    async def _prune_stale_peers(self) -> None:
        """Remove peers whose last heartbeat exceeds the timeout threshold."""
        now = time.time()
        stale_ids: list[str] = []

        async with self._lock:
            for nid, rec in self._peers.items():
                if (now - rec.last_seen) > self._peer_timeout:
                    stale_ids.append(nid)

            for nid in stale_ids:
                removed = self._peers.pop(nid)
                logger.info(
                    "🔴 Peer left (timed out): %s (%s) @ %s",
                    removed.username, removed.node_id, removed.ip,
                )

                if self._on_peer_left:
                    try:
                        result = self._on_peer_left(removed.to_info())
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("on_peer_left callback error")

        if stale_ids:
            logger.debug("Pruned %d stale peer(s).", len(stale_ids))


# ─── Standalone Runner ────────────────────────────────────────────────────────

async def main() -> None:
    """Run the discovery daemon standalone for testing."""
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    username = sys.argv[1] if len(sys.argv) > 1 else f"user-{uuid.uuid4().hex[:4]}"

    def on_join(peer: PeerInfo) -> None:
        logger.info(">>> CALLBACK: %s is now online!", peer.username)

    def on_leave(peer: PeerInfo) -> None:
        logger.info(">>> CALLBACK: %s went offline.", peer.username)

    discovery = PeerDiscovery(
        username=username,
        on_peer_joined=on_join,
        on_peer_left=on_leave,
    )

    await discovery.start()

    try:
        # Keep running; print peer count every 10s
        while True:
            await asyncio.sleep(10)
            peers = discovery.get_peers()
            logger.info(
                "Online peers (%d): %s",
                len(peers),
                ", ".join(p.username for p in peers.values()) or "(none)",
            )
    except asyncio.CancelledError:
        pass
    finally:
        await discovery.stop()


if __name__ == "__main__":
    asyncio.run(main())
