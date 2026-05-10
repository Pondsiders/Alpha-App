/**
 * useWebSocket — persistent bidirectional connection to the Alpha backend.
 *
 * Generic transport hook. Knows how to open a WebSocket to the backend,
 * handle reconnects with exponential backoff, receive raw JSON messages,
 * and send raw JSON messages. Does NOT know about the protocol shape —
 * the caller (useAlphaWebSocket) owns all validation and discrimination
 * via Zod schemas from lib/protocol.ts.
 *
 * This hook was previously hardcoded to an older protocol shape (with
 * `type:` discriminators on both sides). That coupling is gone — the
 * transport layer now speaks raw objects and leaves typing to the
 * consumer, which means protocol changes don't ripple into this file.
 *
 * For the app-specific event routing, see src/hooks/useAlphaWebSocket.ts.
 *
 * Usage:
 *   const { send, connected } = useWebSocket({
 *     onEvent: (raw) => { ... },       // raw is unknown; validate it
 *   });
 *   send(Commands.joinChat({ chatId: "abc123" }));
 */

import { useEffect, useRef, useState, useCallback } from "react";

interface UseWebSocketOptions {
  /** Called for each raw JSON message from the server. The caller is
   *  responsible for validating the shape (e.g. via Zod). */
  onEvent: (raw: unknown) => void;
  /** Called when connection state changes */
  onConnectionChange?: (connected: boolean) => void;
}

// Build the WebSocket URL from the current page location.
// In dev (Vite proxy), this hits the Vite dev server which proxies to FastAPI.
// In production, the backend serves the frontend so same origin works.
function getWebSocketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  let url = `${protocol}//${window.location.host}/ws`;
  // Suggest which chat to restore from localStorage.
  try {
    const lastChat = localStorage.getItem("alpha-lastChatId");
    if (lastChat) url += `?lastChat=${encodeURIComponent(lastChat)}`;
  } catch { /* localStorage unavailable */ }
  return url;
}

// Exponential backoff, max 16s
const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000];

export function useWebSocket({
  onEvent,
  onConnectionChange,
}: UseWebSocketOptions) {
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
    )
      return;

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
        const parsed: unknown = JSON.parse(event.data);
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

      // Auto-reconnect with exponential backoff.
      // Don't reconnect on normal closure (1000) or going away (1001).
      if (event.code !== 1000 && event.code !== 1001) {
        const attempt = reconnectAttemptRef.current;
        const delay =
          RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)];
        console.log(
          `[Alpha WS] Reconnecting in ${delay}ms (attempt ${attempt + 1})`,
        );
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
      const ws = wsRef.current;
      if (!ws) return;

      // Null stale handlers first so nothing fires during teardown.
      // (Original fix for StrictMode remount clobbering wsRef.current
      // via async onclose — still needed.)
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;

      if (ws.readyState === WebSocket.CONNECTING) {
        // WebKit bug 247943 — closing a CONNECTING WebSocket breaks
        // every subsequent WebSocket to the same origin in Safari.
        // Defer the close until onopen fires, then close cleanly.
        // The socket finishes its handshake, fires the replacement
        // onopen, closes itself, and gets garbage-collected. No leak.
        // Rails hit this first and landed the same pattern in
        // rails/rails#44304.
        ws.onopen = () => ws.close(1000, "Component unmounting");
      } else {
        ws.onopen = null;
        ws.close(1000, "Component unmounting");
      }

      wsRef.current = null;
    };
  }, [connect]);

  // Send a message to the server. Accepts any JSON-serializable object —
  // the protocol schema is owned by the consumer (useAlphaWebSocket), not
  // this generic transport layer.
  const send = useCallback((message: unknown) => {
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
