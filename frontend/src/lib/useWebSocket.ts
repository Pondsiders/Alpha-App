/**
 * useWebSocket — persistent bidirectional connection to the Alpha backend.
 *
 * Phase 2: Protocol v2 — all messages carry chatId.
 *
 * Usage:
 *   const { send, connected } = useWebSocket({
 *     onEvent: (event) => { ... }
 *   });
 *   send({ type: "send", chatId: "abc123", content: "Hello" });
 */

import { useEffect, useRef, useState, useCallback } from "react";

// Messages FROM the server
export interface ServerEvent {
  type:
    | "text-delta"
    | "thinking-delta"
    | "tool-call"
    | "tool-result"
    | "chat-created"
    | "chat-state"
    | "chat-list"
    | "context-update"
    | "user-message"
    | "error"
    | "done"
    | "interrupted";
  chatId?: string;
  data?: unknown;
}

// Messages TO the server
export interface ClientMessage {
  type: "send" | "interrupt" | "create-chat" | "list-chats";
  chatId?: string;
  content?: string | Array<Record<string, unknown>>;
}

interface UseWebSocketOptions {
  /** Called for each event from the server */
  onEvent: (event: ServerEvent) => void;
  /** Called when connection state changes */
  onConnectionChange?: (connected: boolean) => void;
}

// Build the WebSocket URL from the current page location.
// In dev (Vite proxy), this hits the Vite dev server which proxies to FastAPI.
// In production, the backend serves the frontend so same origin works.
function getWebSocketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000]; // Exponential backoff, max 16s

export function useWebSocket({ onEvent, onConnectionChange }: UseWebSocketOptions) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Store callbacks in refs so reconnect logic always has the latest version
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const onConnectionChangeRef = useRef(onConnectionChange);
  onConnectionChangeRef.current = onConnectionChange;

  const connect = useCallback(() => {
    // Don't connect if we already have a live or in-progress connection
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) return;

    const url = getWebSocketUrl();
    console.log("[Alpha WS] Connecting to", url);
    const ws = new WebSocket(url);

    ws.onopen = () => {
      console.log("[Alpha WS] Connected");
      reconnectAttemptRef.current = 0;
      setConnected(true);
      onConnectionChangeRef.current?.(true);
    };

    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as ServerEvent;
        onEventRef.current(parsed);
      } catch (err) {
        console.warn("[Alpha WS] Failed to parse message:", event.data, err);
      }
    };

    ws.onclose = (event) => {
      console.log("[Alpha WS] Disconnected", event.code, event.reason);
      wsRef.current = null;
      setConnected(false);
      onConnectionChangeRef.current?.(false);

      // Auto-reconnect with exponential backoff
      // Don't reconnect on normal closure (1000) or going away (1001)
      if (event.code !== 1000 && event.code !== 1001) {
        const attempt = reconnectAttemptRef.current;
        const delay = RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)];
        console.log(`[Alpha WS] Reconnecting in ${delay}ms (attempt ${attempt + 1})`);
        reconnectTimerRef.current = setTimeout(() => {
          reconnectAttemptRef.current++;
          connect();
        }, delay);
      }
    };

    ws.onerror = (error) => {
      console.error("[Alpha WS] Error:", error);
      // onclose will fire after this, triggering reconnect
    };

    wsRef.current = ws;
  }, []);

  // Connect on mount, clean up on unmount
  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        // Null handlers BEFORE closing to prevent stale callbacks from
        // firing after StrictMode remount and clobbering the fresh WS ref.
        wsRef.current.onopen = null;
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.onmessage = null;
        wsRef.current.close(1000, "Component unmounting");
        wsRef.current = null;
      }
    };
  }, [connect]);

  // Send a message to the server
  const send = useCallback((message: ClientMessage) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn("[Alpha WS] Cannot send — not connected");
      return false;
    }
    ws.send(JSON.stringify(message));
    return true;
  }, []);

  return { send, connected };
}
