"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { WSAction, WSEvent } from "@/lib/types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8765";
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

type ConnectionStatus = "connecting" | "connected" | "disconnected";

interface UseWebSocketReturn {
  status: ConnectionStatus;
  send: (action: WSAction) => void;
  lastEvent: WSEvent | null;
  subscribe: (handler: (event: WSEvent) => void) => () => void;
}

export function useWebSocket(): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Set<(event: WSEvent) => void>>(new Set());
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [lastEvent, setLastEvent] = useState<WSEvent | null>(null);

  const connect = useCallback(() => {
    // Don't reconnect if already open
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    setStatus("connecting");

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      reconnectAttemptRef.current = 0;
      console.log("[WS] Connected to backend");
    };

    ws.onmessage = (e) => {
      try {
        const event: WSEvent = JSON.parse(e.data);
        setLastEvent(event);
        handlersRef.current.forEach((handler) => handler(event));
      } catch (err) {
        console.error("[WS] Failed to parse message:", err);
      }
    };

    ws.onclose = () => {
      setStatus("disconnected");
      wsRef.current = null;
      scheduleReconnect();
    };

    ws.onerror = (err) => {
      console.error("[WS] Error:", err);
      ws.close();
    };
  }, []);

  const scheduleReconnect = useCallback(() => {
    const attempt = reconnectAttemptRef.current;
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
    reconnectAttemptRef.current = attempt + 1;

    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${attempt + 1})…`);

    reconnectTimerRef.current = setTimeout(() => {
      connect();
    }, delay);
  }, [connect]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((action: WSAction) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(action));
    } else {
      console.warn("[WS] Cannot send — not connected");
    }
  }, []);

  const subscribe = useCallback((handler: (event: WSEvent) => void) => {
    handlersRef.current.add(handler);
    return () => {
      handlersRef.current.delete(handler);
    };
  }, []);

  return { status, send, lastEvent, subscribe };
}
