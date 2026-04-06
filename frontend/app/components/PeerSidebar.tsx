"use client";

import { type Peer } from "@/lib/types";

interface PeerSidebarProps {
  peers: Peer[];
  activePeerId: string | null;
  onSelectPeer: (nodeId: string) => void;
  connectionStatus: "connecting" | "connected" | "disconnected";
  username: string;
}

export default function PeerSidebar({
  peers,
  activePeerId,
  onSelectPeer,
  connectionStatus,
  username,
}: PeerSidebarProps) {
  const statusColor = {
    connected: "bg-emerald-400",
    connecting: "bg-amber-400 animate-pulse",
    disconnected: "bg-red-400",
  }[connectionStatus];

  const statusText = {
    connected: "Online",
    connecting: "Connecting…",
    disconnected: "Offline",
  }[connectionStatus];

  return (
    <aside className="w-72 shrink-0 flex flex-col border-r border-white/[0.06] bg-[#1a1b23]">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="p-5 border-b border-white/[0.06]">
        <div className="flex items-center gap-3 mb-4">
          <div className="relative">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white font-bold text-sm shadow-lg shadow-violet-500/20">
              {username.charAt(0).toUpperCase()}
            </div>
            <span
              className={`absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full border-2 border-[#1a1b23] ${statusColor}`}
            />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white truncate">{username}</p>
            <p className="text-xs text-zinc-500">{statusText}</p>
          </div>
        </div>

        <h2 className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
          Peers — {peers.length} online
        </h2>
      </div>

      {/* ── Peers List ──────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto py-2 scrollbar-thin">
        {peers.length === 0 ? (
          <div className="px-5 py-8 text-center">
            <div className="w-12 h-12 mx-auto mb-3 rounded-2xl bg-white/[0.03] flex items-center justify-center">
              <svg className="w-6 h-6 text-zinc-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
              </svg>
            </div>
            <p className="text-xs text-zinc-500">No peers on the network yet.</p>
            <p className="text-xs text-zinc-600 mt-1">Waiting for discovery…</p>
          </div>
        ) : (
          peers.map((peer) => {
            const isActive = peer.node_id === activePeerId;
            return (
              <button
                key={peer.node_id}
                onClick={() => onSelectPeer(peer.node_id)}
                className={`w-full flex items-center gap-3 px-4 py-2.5 transition-all duration-150 group cursor-pointer ${
                  isActive
                    ? "bg-white/[0.06] border-l-2 border-violet-500"
                    : "hover:bg-white/[0.03] border-l-2 border-transparent"
                }`}
              >
                <div className="relative shrink-0">
                  <div
                    className={`w-9 h-9 rounded-lg flex items-center justify-center text-sm font-semibold transition-colors ${
                      isActive
                        ? "bg-violet-500/20 text-violet-300"
                        : "bg-white/[0.05] text-zinc-400 group-hover:text-zinc-300"
                    }`}
                  >
                    {peer.username.charAt(0).toUpperCase()}
                  </div>
                  <span
                    className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-[#1a1b23] ${
                      peer.online ? "bg-emerald-400" : "bg-zinc-600"
                    }`}
                  />
                </div>
                <div className="min-w-0 text-left">
                  <p
                    className={`text-sm truncate ${
                      isActive ? "text-white font-medium" : "text-zinc-300"
                    }`}
                  >
                    {peer.username}
                  </p>
                  <p className="text-[11px] text-zinc-600 truncate font-mono">
                    {peer.ip}
                  </p>
                </div>
              </button>
            );
          })
        )}
      </div>

      {/* ── Footer ──────────────────────────────────────────── */}
      <div className="p-4 border-t border-white/[0.06]">
        <div className="flex items-center gap-2 text-[11px] text-zinc-600">
          <span className={`w-1.5 h-1.5 rounded-full ${statusColor}`} />
          <span>Echo P2P • LAN Mesh</span>
        </div>
      </div>
    </aside>
  );
}
