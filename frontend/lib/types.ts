// ─── Types for the Echo P2P Chat Application ─────────────────────────────────

export interface Peer {
  node_id: string;
  username: string;
  ip: string;
  online: boolean;
}

export interface ChatMessage {
  id: string;
  from: string;
  from_user: string;
  to?: string;
  body: string;
  ts: number;
  self: boolean;
  type: "message";
}

export interface CodeSnippet {
  id: string;
  from: string;
  from_user: string;
  to?: string;
  code: string;
  language: string;
  ts: number;
  self: boolean;
  type: "code_snippet";
}

export type MessageEntry = ChatMessage | CodeSnippet;

// ─── WebSocket Protocol Types ────────────────────────────────────────────────

export interface WSActionSendMessage {
  action: "send_message";
  to: string;
  body: string;
}

export interface WSActionSendCode {
  action: "send_code";
  to: string;
  code: string;
  language: string;
}

export interface WSActionGetPeers {
  action: "get_peers";
}

export interface WSActionGetDiagnostics {
  action: "get_diagnostics";
}

export type WSAction =
  | WSActionSendMessage
  | WSActionSendCode
  | WSActionGetPeers
  | WSActionGetDiagnostics;

// ─── Incoming Events ─────────────────────────────────────────────────────────

export interface WSEventPeerJoined {
  event: "peer_joined";
  peer: Peer;
}

export interface WSEventPeerLeft {
  event: "peer_left";
  peer: Peer;
}

export interface WSEventPeersList {
  event: "peers_list";
  peers: Peer[];
}

export interface WSEventMessage {
  event: "message";
  from: string;
  from_user: string;
  to?: string;
  body: string;
  ts: number;
  self: boolean;
}

export interface WSEventCodeSnippet {
  event: "code_snippet";
  from: string;
  from_user: string;
  to?: string;
  code: string;
  language: string;
  ts: number;
  self: boolean;
}

export interface WSEventDiagnostics {
  event: "diagnostics";
  data: {
    node_id: string;
    username: string;
    peer_count: number;
    mesh_stats: {
      messages_sent: number;
      messages_received: number;
      send_failures: number;
    };
    uptime_peers: Record<
      string,
      {
        username: string;
        ip: string;
        connected_since: number;
        last_seen: number;
      }
    >;
  };
}

export interface WSEventError {
  event: "error";
  message: string;
}

export type WSEvent =
  | WSEventPeerJoined
  | WSEventPeerLeft
  | WSEventPeersList
  | WSEventMessage
  | WSEventCodeSnippet
  | WSEventDiagnostics
  | WSEventError;
