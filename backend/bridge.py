"""
bridge.py — WebSocket Bridge (Backend Daemon ↔ Frontend UI)

This module bridges the asyncio-based P2P backend with the Next.js
frontend over a local WebSocket connection.

Architecture
────────────
    ┌──────────┐  ws://localhost:8765  ┌──────────────┐
    │ Next.js  │◄════════════════════►│  BridgeServer │
    │ Frontend │                      │  (Python)     │
    └──────────┘                      └───────┬───────┘
                                              │
                               ┌──────────────┼──────────────┐
                               │              │              │
                          MeshNetwork   PeerDiscovery    Crypto
                          (TCP mesh)    (UDP mcast)      (E2EE)

Protocol (JSON messages over WS)
────────────────────────────────
  Frontend → Backend:
    { "action": "send_message", "to": "<node_id>", "body": "..." }
    { "action": "send_code",    "to": "<node_id>", "code": "...", "language": "python" }
    { "action": "send_file",    "to": "<node_id>", "filename": "...", "data": "<b64>" }
    { "action": "get_peers" }
    { "action": "get_diagnostics" }

  Backend → Frontend:
    { "event": "peer_joined",  "peer": { ... } }
    { "event": "peer_left",    "peer": { ... } }
    { "event": "peers_list",   "peers": [ ... ] }
    { "event": "message",      "from": "<node_id>", "body": "...", ... }
    { "event": "code_snippet", "from": "<node_id>", "code": "...", "language": "..." }
    { "event": "diagnostics",  "data": { ... } }
    { "event": "error",        "message": "..." }
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional, Set

import websockets
from websockets.asyncio.server import Server, ServerConnection

from crypto import (
    KeyPair,
    decrypt_payload,
    derive_shared_secret,
    encrypt_payload,
    generate_keypair,
    public_key_from_b64,
    public_key_to_b64,
)
from discovery import PeerDiscovery, PeerInfo
from mesh import IncomingMessage, MeshNetwork

# ─── Constants ────────────────────────────────────────────────────────────────

WS_HOST: str = "0.0.0.0"
WS_PORT: int = 8765

logger: logging.Logger = logging.getLogger("echo.bridge")


# ─── Bridge Server ────────────────────────────────────────────────────────────

class BridgeServer:
    """
    WebSocket server that wires together Discovery, Mesh, and Crypto
    modules and exposes them to the frontend UI.
    """

    def __init__(
        self,
        discovery: PeerDiscovery,
        mesh: MeshNetwork,
        keys: KeyPair,
        host: str = WS_HOST,
        port: int = WS_PORT,
    ) -> None:
        self._discovery = discovery
        self._mesh = mesh
        self._keys = keys
        self._host = host
        self._port = port

        # Connected frontend clients
        self._clients: Set[ServerConnection] = set()

        # Shared secrets cache: node_id → AES-256 key
        self._shared_secrets: Dict[str, bytes] = {}

        # WS server handle
        self._server: Optional[Server] = None
        self._relay_task: Optional[asyncio.Task[None]] = None
        self._running: bool = False

        logger.info("BridgeServer initialised on ws://%s:%d", host, port)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WebSocket server and the mesh→WS relay loop."""
        if self._running:
            return

        self._server = await websockets.asyncio.server.serve(
            self._handle_client,
            self._host,
            self._port,
        )
        self._running = True

        # Start relaying incoming mesh messages to WS clients
        self._relay_task = asyncio.create_task(
            self._relay_loop(), name="bridge-relay"
        )

        logger.info("WebSocket bridge is live on ws://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Shut down the bridge."""
        self._running = False

        if self._relay_task and not self._relay_task.done():
            self._relay_task.cancel()
            try:
                await self._relay_task
            except asyncio.CancelledError:
                pass

        if self._server:
            self._server.close()
            await self._server.wait_closed()

        logger.info("WebSocket bridge stopped.")

    # ── Client Handling ───────────────────────────────────────────────────

    async def _handle_client(self, ws: ServerConnection) -> None:
        """Handle a single frontend WebSocket connection."""
        self._clients.add(ws)
        remote = ws.remote_address
        logger.info("Frontend client connected: %s", remote)

        # Send initial peer list
        await self._send_peers_list(ws)

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._handle_action(ws, msg)
                except json.JSONDecodeError:
                    await self._send_error(ws, "Invalid JSON")
                except Exception as exc:
                    logger.exception("Error handling action from %s", remote)
                    await self._send_error(ws, str(exc))
        except websockets.exceptions.ConnectionClosed:
            logger.info("Frontend client disconnected: %s", remote)
        finally:
            self._clients.discard(ws)

    async def _handle_action(
        self, ws: ServerConnection, msg: Dict[str, Any]
    ) -> None:
        """Route a frontend action to the appropriate handler."""
        action = msg.get("action")

        if action == "send_message":
            await self._action_send_message(msg)
        elif action == "send_code":
            await self._action_send_code(msg)
        elif action == "get_peers":
            await self._send_peers_list(ws)
        elif action == "get_diagnostics":
            await self._action_diagnostics(ws)
        else:
            await self._send_error(ws, f"Unknown action: {action}")

    # ── Actions ───────────────────────────────────────────────────────────

    async def _action_send_message(self, msg: Dict[str, Any]) -> None:
        """Send a chat message to a peer."""
        target_id: str = msg["to"]
        body: str = msg["body"]

        peer = self._discovery.get_peers().get(target_id)
        if not peer:
            logger.warning("Cannot send — peer %s not found.", target_id)
            await self._broadcast_event({
                "event": "error",
                "message": f"Peer {target_id} is offline.",
            })
            return

        payload: Dict[str, Any] = {
            "type": "chat",
            "from_node": self._mesh.node_id,
            "from_user": self._discovery.username,
            "body": body,
            "ts": time.time(),
        }

        # Encrypt if we have a shared secret
        secret = self._shared_secrets.get(target_id)
        if secret:
            encrypted = encrypt_payload(json.dumps(payload), secret)
            wire_payload: Dict[str, Any] = {"type": "encrypted", **encrypted}
        else:
            wire_payload = payload

        ok = await self._mesh.send_payload(peer.ip, wire_payload)
        if ok:
            # Echo back to frontend so the sender sees their own message
            await self._broadcast_event({
                "event": "message",
                "from": self._mesh.node_id,
                "from_user": self._discovery.username,
                "to": target_id,
                "body": body,
                "ts": payload["ts"],
                "self": True,
            })
        else:
            await self._broadcast_event({
                "event": "error",
                "message": f"Failed to deliver message to {peer.username}.",
            })

    async def _action_send_code(self, msg: Dict[str, Any]) -> None:
        """Send a code snippet to a peer."""
        target_id: str = msg["to"]
        code: str = msg["code"]
        language: str = msg.get("language", "plaintext")

        peer = self._discovery.get_peers().get(target_id)
        if not peer:
            await self._broadcast_event({
                "event": "error",
                "message": f"Peer {target_id} is offline.",
            })
            return

        payload: Dict[str, Any] = {
            "type": "code",
            "from_node": self._mesh.node_id,
            "from_user": self._discovery.username,
            "code": code,
            "language": language,
            "ts": time.time(),
        }

        ok = await self._mesh.send_payload(peer.ip, payload)
        if ok:
            await self._broadcast_event({
                "event": "code_snippet",
                "from": self._mesh.node_id,
                "from_user": self._discovery.username,
                "to": target_id,
                "code": code,
                "language": language,
                "ts": payload["ts"],
                "self": True,
            })

    async def _action_diagnostics(self, ws: ServerConnection) -> None:
        """Send network diagnostics to the requesting client."""
        peers = self._discovery.get_peers()
        stats = self._mesh.stats

        await self._send_event(ws, {
            "event": "diagnostics",
            "data": {
                "node_id": self._mesh.node_id,
                "username": self._discovery.username,
                "peer_count": len(peers),
                "mesh_stats": stats,
                "uptime_peers": {
                    nid: {
                        "username": p.username,
                        "ip": p.ip,
                        "connected_since": p.first_seen,
                        "last_seen": p.last_seen,
                    }
                    for nid, p in peers.items()
                },
            },
        })

    # ── Mesh → Frontend Relay ─────────────────────────────────────────────

    async def _relay_loop(self) -> None:
        """Continuously pull messages from the mesh queue and push to WS."""
        logger.debug("Mesh → WS relay loop started.")

        while self._running:
            try:
                msg: IncomingMessage = await asyncio.wait_for(
                    self._mesh.incoming.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            payload = msg.payload
            msg_type = payload.get("type", "unknown")

            # Handle encrypted payloads
            if msg_type == "encrypted":
                sender_id = payload.get("_sender", "")
                secret = self._shared_secrets.get(sender_id)
                if secret:
                    try:
                        decrypted = decrypt_payload(payload, secret)
                        payload = json.loads(decrypted)
                        msg_type = payload.get("type", "unknown")
                    except Exception:
                        logger.warning("Failed to decrypt message from %s", sender_id)
                        continue
                else:
                    logger.warning(
                        "No shared secret for %s — cannot decrypt.", sender_id
                    )
                    continue

            # Handle key exchange
            if msg_type == "key_exchange":
                await self._handle_key_exchange(payload, msg.sender_ip)
                continue

            # Route to frontend
            if msg_type == "chat":
                await self._broadcast_event({
                    "event": "message",
                    "from": payload.get("from_node", "?"),
                    "from_user": payload.get("from_user", "Unknown"),
                    "body": payload.get("body", ""),
                    "ts": payload.get("ts", time.time()),
                    "self": False,
                })
            elif msg_type == "code":
                await self._broadcast_event({
                    "event": "code_snippet",
                    "from": payload.get("from_node", "?"),
                    "from_user": payload.get("from_user", "Unknown"),
                    "code": payload.get("code", ""),
                    "language": payload.get("language", "plaintext"),
                    "ts": payload.get("ts", time.time()),
                    "self": False,
                })
            else:
                logger.debug("Unhandled mesh message type: %s", msg_type)

    async def _handle_key_exchange(
        self, payload: Dict[str, Any], sender_ip: str
    ) -> None:
        """Process an incoming ECDH public key from a peer."""
        peer_node_id = payload.get("from_node", "")
        peer_pub_b64 = payload.get("public_key", "")

        if not peer_node_id or not peer_pub_b64:
            return

        try:
            peer_pub = public_key_from_b64(peer_pub_b64)
            shared = derive_shared_secret(self._keys.private_key, peer_pub)
            self._shared_secrets[peer_node_id] = shared
            logger.info("🔑 Key exchange complete with %s", peer_node_id)
        except Exception:
            logger.exception("Key exchange failed with %s", peer_node_id)

    # ── Key Exchange Initiation ───────────────────────────────────────────

    async def initiate_key_exchange(self, peer: PeerInfo) -> None:
        """Send our public key to a newly discovered peer."""
        payload: Dict[str, Any] = {
            "type": "key_exchange",
            "from_node": self._mesh.node_id,
            "public_key": public_key_to_b64(self._keys.public_key),
        }
        await self._mesh.send_payload(peer.ip, payload)
        logger.info("🔑 Key exchange initiated with %s @ %s", peer.username, peer.ip)

    # ── Event Broadcasting ────────────────────────────────────────────────

    async def _broadcast_event(self, event: Dict[str, Any]) -> None:
        """Push an event to ALL connected frontend clients."""
        if not self._clients:
            return

        data = json.dumps(event)
        dead: list[ServerConnection] = []

        for ws in self._clients:
            try:
                await ws.send(data)
            except websockets.exceptions.ConnectionClosed:
                dead.append(ws)

        for ws in dead:
            self._clients.discard(ws)

    async def _send_event(
        self, ws: ServerConnection, event: Dict[str, Any]
    ) -> None:
        """Push an event to a single client."""
        try:
            await ws.send(json.dumps(event))
        except websockets.exceptions.ConnectionClosed:
            self._clients.discard(ws)

    async def _send_error(self, ws: ServerConnection, message: str) -> None:
        await self._send_event(ws, {"event": "error", "message": message})

    async def _send_peers_list(self, ws: ServerConnection) -> None:
        """Send the current peer registry to a client."""
        peers = self._discovery.get_peers()
        await self._send_event(ws, {
            "event": "peers_list",
            "peers": [
                {
                    "node_id": p.node_id,
                    "username": p.username,
                    "ip": p.ip,
                    "online": True,
                }
                for p in peers.values()
            ],
        })


# ─── Standalone Runner (Full Stack) ──────────────────────────────────────────

async def main() -> None:
    """Boot all backend services: Discovery + Mesh + Crypto + Bridge."""
    import sys
    import uuid

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    node_id = uuid.uuid4().hex[:12]
    username = sys.argv[1] if len(sys.argv) > 1 else f"user-{node_id[:4]}"

    # Generate ephemeral E2EE keys
    keys = generate_keypair()

    # Mesh network
    mesh = MeshNetwork(node_id=node_id)
    await mesh.start()

    # Bridge server
    bridge = BridgeServer(discovery=None, mesh=mesh, keys=keys)  # type: ignore

    # Discovery with lifecycle hooks
    async def on_peer_joined(peer: PeerInfo) -> None:
        await bridge.initiate_key_exchange(peer)
        await bridge._broadcast_event({
            "event": "peer_joined",
            "peer": {
                "node_id": peer.node_id,
                "username": peer.username,
                "ip": peer.ip,
                "online": True,
            },
        })

    async def on_peer_left(peer: PeerInfo) -> None:
        await bridge._broadcast_event({
            "event": "peer_left",
            "peer": {
                "node_id": peer.node_id,
                "username": peer.username,
                "ip": peer.ip,
                "online": False,
            },
        })

    discovery = PeerDiscovery(
        username=username,
        node_id=node_id,
        on_peer_joined=on_peer_joined,
        on_peer_left=on_peer_left,
    )
    bridge._discovery = discovery

    await discovery.start()
    await bridge.start()

    logger.info(
        "═══ Echo backend fully operational ═══\n"
        "  Node   : %s (%s)\n"
        "  WS     : ws://localhost:%d\n"
        "  Mesh   : TCP :%d\n"
        "  Mcast  : UDP 239.77.69.83:%d\n",
        username, node_id, WS_PORT,
        mesh.port, 50692,
    )

    try:
        await asyncio.Future()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await bridge.stop()
        await discovery.stop()
        await mesh.stop()


if __name__ == "__main__":
    asyncio.run(main())
