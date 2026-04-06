"""
mesh.py — Asynchronous TCP Mesh Network Layer

This module provides the reliable data-transmission backbone for the Echo
P2P chat application. It sits directly above the UDP discovery layer:

    ┌─────────────┐      ┌─────────────┐
    │  Discovery   │──▶──│  MeshNetwork │──▶── asyncio.Queue (incoming)
    │  (UDP mcast) │      │  (TCP mesh)  │
    └─────────────┘      └─────────────┘

Design Decisions
────────────────
• Length-prefixed framing: Every message on the wire is preceded by a 4-byte
  big-endian uint32 containing the payload length. This avoids the classic
  "where does this message end?" TCP-stream problem.

• Fire-and-forget sends: `send_payload` opens a short-lived TCP stream,
  writes the frame, and closes. This keeps the implementation simple and
  avoids the complexity of persistent connection pools in Phase 2.
  (A connection-pool optimisation can be layered on later.)

• All errors during send/receive are caught and logged — the event loop
  is never allowed to crash from a single bad peer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ─── Constants ────────────────────────────────────────────────────────────────

TCP_PORT: int = 50693
HEADER_FORMAT: str = "!I"           # 4-byte big-endian unsigned int
HEADER_SIZE: int = struct.calcsize(HEADER_FORMAT)
MAX_PAYLOAD_SIZE: int = 16 * 1024 * 1024   # 16 MiB safety cap
CONNECT_TIMEOUT: float = 5.0       # seconds
READ_TIMEOUT: float = 10.0         # seconds

logger: logging.Logger = logging.getLogger("echo.mesh")


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class IncomingMessage:
    """An inbound message pulled from the queue."""
    sender_ip: str
    sender_port: int
    payload: Dict[str, Any]
    received_at: float = field(default_factory=time.time)


# ─── Wire Protocol Helpers ────────────────────────────────────────────────────

def _frame_payload(payload: Dict[str, Any]) -> bytes:
    """Serialize a dict to length-prefixed JSON bytes.

    Wire format:
        [4 bytes: payload length (big-endian)] [N bytes: UTF-8 JSON]
    """
    raw: bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header: bytes = struct.pack(HEADER_FORMAT, len(raw))
    return header + raw


async def _read_frame(
    reader: asyncio.StreamReader,
    timeout: float = READ_TIMEOUT,
) -> Optional[Dict[str, Any]]:
    """Read exactly one length-prefixed JSON frame from the stream.

    Returns None if the stream is closed or the frame is invalid.
    """
    try:
        header: bytes = await asyncio.wait_for(
            reader.readexactly(HEADER_SIZE), timeout=timeout
        )
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
        return None

    (length,) = struct.unpack(HEADER_FORMAT, header)

    if length == 0 or length > MAX_PAYLOAD_SIZE:
        logger.warning("Invalid frame length: %d bytes — dropping.", length)
        return None

    try:
        raw: bytes = await asyncio.wait_for(
            reader.readexactly(length), timeout=timeout
        )
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
        logger.warning("Incomplete payload read — dropping frame.")
        return None

    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Malformed JSON in frame: %s", exc)
        return None


# ─── Core Mesh Network ───────────────────────────────────────────────────────

class MeshNetwork:
    """
    Asynchronous TCP mesh layer for peer-to-peer message exchange.

    Usage
    ─────
        mesh = MeshNetwork(node_id="abc123", port=50693)
        await mesh.start()

        # Send a message to a known peer
        ok = await mesh.send_payload("192.168.1.42", {
            "type": "chat",
            "body": "Hello!",
        })

        # Consume incoming messages
        msg = await mesh.incoming.get()
        print(msg.payload)

        await mesh.stop()
    """

    def __init__(
        self,
        node_id: str,
        port: int = TCP_PORT,
        connect_timeout: float = CONNECT_TIMEOUT,
        on_message: Optional[Callable[[IncomingMessage], Any]] = None,
    ) -> None:
        self._node_id: str = node_id
        self._port: int = port
        self._connect_timeout: float = connect_timeout
        self._on_message: Optional[Callable[[IncomingMessage], Any]] = on_message

        # Public queue for consumers (e.g. the WebSocket bridge)
        self.incoming: asyncio.Queue[IncomingMessage] = asyncio.Queue()

        # Internal state
        self._server: Optional[asyncio.Server] = None
        self._running: bool = False

        # Stats
        self._stats: Dict[str, int] = {
            "messages_sent": 0,
            "messages_received": 0,
            "send_failures": 0,
        }

        logger.info(
            "MeshNetwork initialised — node_id=%s, port=%d",
            self._node_id, self._port,
        )

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the TCP server to accept incoming peer connections."""
        if self._running:
            logger.warning("MeshNetwork already running — ignoring start().")
            return

        try:
            self._server = await asyncio.start_server(
                self._handle_connection,
                host="0.0.0.0",
                port=self._port,
                reuse_address=True,
            )
        except OSError as exc:
            logger.error(
                "Failed to bind TCP server on port %d: %s. "
                "Another instance may be running.",
                self._port, exc,
            )
            raise

        self._running = True
        addrs = [str(s.getsockname()) for s in self._server.sockets]
        logger.info("TCP mesh server listening on %s", ", ".join(addrs))

    async def stop(self) -> None:
        """Gracefully shut down the TCP server."""
        if not self._running:
            return

        self._running = False

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info(
            "TCP mesh server stopped. Stats: sent=%d, received=%d, failures=%d",
            self._stats["messages_sent"],
            self._stats["messages_received"],
            self._stats["send_failures"],
        )

    # ── Sending ───────────────────────────────────────────────────────────

    async def send_payload(
        self,
        ip: str,
        payload: Dict[str, Any],
        port: Optional[int] = None,
    ) -> bool:
        """
        Send a JSON payload to a peer via a short-lived TCP stream.

        Automatically injects `_sender` and `_ts` metadata into the payload.

        Returns True on success, False on failure.
        """
        target_port = port or self._port

        # Inject sender metadata
        envelope: Dict[str, Any] = {
            **payload,
            "_sender": self._node_id,
            "_ts": time.time(),
        }

        frame: bytes = _frame_payload(envelope)

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, target_port),
                timeout=self._connect_timeout,
            )
        except (
            ConnectionRefusedError,
            ConnectionResetError,
            asyncio.TimeoutError,
            OSError,
        ) as exc:
            self._stats["send_failures"] += 1
            logger.warning(
                "Send failed to %s:%d — %s: %s",
                ip, target_port, type(exc).__name__, exc,
            )
            return False

        try:
            writer.write(frame)
            await writer.drain()
            self._stats["messages_sent"] += 1
            logger.debug(
                "Sent %d bytes to %s:%d [type=%s]",
                len(frame), ip, target_port, payload.get("type", "?"),
            )
        except (ConnectionError, OSError) as exc:
            self._stats["send_failures"] += 1
            logger.warning("Write failed to %s:%d — %s", ip, target_port, exc)
            return False
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

        return True

    async def broadcast_payload(
        self,
        peer_ips: List[str],
        payload: Dict[str, Any],
    ) -> Dict[str, bool]:
        """
        Send a payload to multiple peers concurrently.

        Returns a dict of {ip: success_bool}.
        """
        tasks = {
            ip: asyncio.create_task(self.send_payload(ip, payload))
            for ip in peer_ips
        }
        results: Dict[str, bool] = {}
        for ip, task in tasks.items():
            try:
                results[ip] = await task
            except Exception as exc:
                logger.error("Unexpected error broadcasting to %s: %s", ip, exc)
                results[ip] = False

        success = sum(1 for v in results.values() if v)
        logger.info(
            "Broadcast complete: %d/%d peers reached.", success, len(peer_ips)
        )
        return results

    # ── Receiving ─────────────────────────────────────────────────────────

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an inbound TCP connection from a peer."""
        peer_addr: Tuple[str, int] = writer.get_extra_info("peername", ("?", 0))
        logger.debug("Incoming connection from %s:%d", *peer_addr)

        try:
            payload = await _read_frame(reader)
            if payload is None:
                logger.debug("Empty/invalid frame from %s:%d — closing.", *peer_addr)
                return

            msg = IncomingMessage(
                sender_ip=peer_addr[0],
                sender_port=peer_addr[1],
                payload=payload,
            )

            # Enqueue for consumers
            await self.incoming.put(msg)
            self._stats["messages_received"] += 1

            logger.debug(
                "Received message from %s:%d [type=%s]",
                peer_addr[0], peer_addr[1], payload.get("type", "?"),
            )

            # Fire callback if registered
            if self._on_message:
                try:
                    result = self._on_message(msg)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("on_message callback error")

        except Exception:
            logger.exception("Unhandled error on connection from %s:%d", *peer_addr)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass


# ─── Standalone Test Runner ──────────────────────────────────────────────────

async def main() -> None:
    """Run a mesh node standalone for testing alongside the discovery daemon."""
    import sys
    import uuid

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    # Import discovery from Phase 1
    from discovery import PeerDiscovery, PeerInfo

    node_id = uuid.uuid4().hex[:12]
    username = sys.argv[1] if len(sys.argv) > 1 else f"user-{node_id[:4]}"

    # ── Message handler ──
    async def on_message(msg: IncomingMessage) -> None:
        logger.info(
            "💬 [%s] %s",
            msg.payload.get("type", "?"),
            msg.payload.get("body", msg.payload),
        )

    # ── Start mesh ──
    mesh = MeshNetwork(node_id=node_id, on_message=on_message)
    await mesh.start()

    # ── Start discovery with auto-greeting ──
    async def on_peer_joined(peer: PeerInfo) -> None:
        logger.info("Sending greeting to new peer %s @ %s", peer.username, peer.ip)
        await mesh.send_payload(peer.ip, {
            "type": "chat",
            "body": f"Hey {peer.username}, I'm {username}! 👋",
        })

    discovery = PeerDiscovery(
        username=username,
        node_id=node_id,
        on_peer_joined=on_peer_joined,
    )
    await discovery.start()

    try:
        logger.info("Node running as '%s' (%s). Press Ctrl+C to stop.", username, node_id)
        while True:
            await asyncio.sleep(30)
            logger.info("Stats: %s | Online peers: %d", mesh.stats, discovery.get_peer_count())
    except asyncio.CancelledError:
        pass
    finally:
        await discovery.stop()
        await mesh.stop()


if __name__ == "__main__":
    asyncio.run(main())
