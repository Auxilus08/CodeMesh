"use client";

import { useCallback, useEffect, useState } from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import PeerSidebar from "./PeerSidebar";
import ChatWindow from "./ChatWindow";
import type {
  ChatMessage,
  CodeSnippet,
  MessageEntry,
  Peer,
  WSEvent,
} from "@/lib/types";

// ── Unique ID generator ──────────────────────────────────────────────────────
let _idCounter = 0;
function uniqueId(): string {
  return `msg-${Date.now()}-${_idCounter++}`;
}

// ── Constants ────────────────────────────────────────────────────────────────
const USERNAME = typeof window !== "undefined"
  ? new URLSearchParams(window.location.search).get("user") ?? "EchoUser"
  : "EchoUser";

export default function ChatDashboard() {
  const { status, send, subscribe } = useWebSocket();

  const [peers, setPeers] = useState<Peer[]>([]);
  const [activePeerId, setActivePeerId] = useState<string | null>(null);

  // Messages keyed by peer node_id for per-conversation history
  const [messageHistory, setMessageHistory] = useState<
    Record<string, MessageEntry[]>
  >({});

  // ── Handle incoming WS events ──────────────────────────────────────────
  const handleEvent = useCallback((event: WSEvent) => {
    switch (event.event) {
      case "peers_list":
        setPeers(event.peers);
        break;

      case "peer_joined":
        setPeers((prev) => {
          // Update if exists, otherwise add
          const exists = prev.some((p) => p.node_id === event.peer.node_id);
          if (exists) {
            return prev.map((p) =>
              p.node_id === event.peer.node_id ? { ...p, online: true } : p
            );
          }
          return [...prev, event.peer];
        });
        break;

      case "peer_left":
        setPeers((prev) =>
          prev.map((p) =>
            p.node_id === event.peer.node_id ? { ...p, online: false } : p
          )
        );
        break;

      case "message": {
        const chatMsg: ChatMessage = {
          id: uniqueId(),
          from: event.from,
          from_user: event.from_user,
          to: event.to,
          body: event.body,
          ts: event.ts,
          self: event.self,
          type: "message",
        };
        // Determine which conversation this belongs to
        const peerKey = event.self ? (event.to ?? event.from) : event.from;
        setMessageHistory((prev) => ({
          ...prev,
          [peerKey]: [...(prev[peerKey] ?? []), chatMsg],
        }));
        break;
      }

      case "code_snippet": {
        const codeMsg: CodeSnippet = {
          id: uniqueId(),
          from: event.from,
          from_user: event.from_user,
          to: event.to,
          code: event.code,
          language: event.language,
          ts: event.ts,
          self: event.self,
          type: "code_snippet",
        };
        const codePeerKey = event.self
          ? (event.to ?? event.from)
          : event.from;
        setMessageHistory((prev) => ({
          ...prev,
          [codePeerKey]: [...(prev[codePeerKey] ?? []), codeMsg],
        }));
        break;
      }

      case "error":
        console.error("[Echo Error]", event.message);
        break;

      default:
        break;
    }
  }, []);

  useEffect(() => {
    const unsub = subscribe(handleEvent);
    return unsub;
  }, [subscribe, handleEvent]);

  // ── Actions ────────────────────────────────────────────────────────────
  const handleSendMessage = useCallback(
    (body: string) => {
      if (!activePeerId) return;
      send({ action: "send_message", to: activePeerId, body });
    },
    [activePeerId, send]
  );

  const handleSendCode = useCallback(
    (code: string, language: string) => {
      if (!activePeerId) return;
      send({ action: "send_code", to: activePeerId, code, language });
    },
    [activePeerId, send]
  );

  // ── Derived State ─────────────────────────────────────────────────────
  const activePeer = peers.find((p) => p.node_id === activePeerId) ?? null;
  const activeMessages = activePeerId
    ? messageHistory[activePeerId] ?? []
    : [];

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#0e0f15] text-white">
      <PeerSidebar
        peers={peers}
        activePeerId={activePeerId}
        onSelectPeer={setActivePeerId}
        connectionStatus={status}
        username={USERNAME}
      />
      <ChatWindow
        messages={activeMessages}
        activePeerUsername={activePeer?.username ?? null}
        onSendMessage={handleSendMessage}
        onSendCode={handleSendCode}
      />
    </div>
  );
}
