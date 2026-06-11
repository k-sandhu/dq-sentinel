// WebSocket client for the assistant chat. One socket per selected session;
// events stream to the caller, sends are fire-and-forget JSON frames.
import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "../api/client";
import type { ChatWsEvent } from "../api/types";

export type SocketState = "connecting" | "open" | "closed";

export function useChatSocket(sessionId: number | null, onEvent: (e: ChatWsEvent) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const [state, setState] = useState<SocketState>("closed");
  const [generation, setGeneration] = useState(0);
  // Keep the latest handler without re-opening the socket on every render.
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    if (sessionId === null) {
      setState("closed");
      return;
    }
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const token = encodeURIComponent(getToken() ?? "");
    const ws = new WebSocket(`${proto}://${window.location.host}/api/v1/chat/ws/${sessionId}?token=${token}`);
    wsRef.current = ws;
    setState("connecting");
    ws.onopen = () => setState("open");
    ws.onmessage = (e) => {
      try {
        handlerRef.current(JSON.parse(e.data) as ChatWsEvent);
      } catch {
        /* ignore non-JSON frames */
      }
    };
    ws.onclose = () => {
      if (wsRef.current === ws) setState("closed");
    };
    return () => {
      wsRef.current = null;
      ws.close();
    };
  }, [sessionId, generation]);

  const send = useCallback((content: string) => {
    wsRef.current?.send(JSON.stringify({ type: "user_message", content }));
  }, []);

  const stop = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: "stop" }));
  }, []);

  const reconnect = useCallback(() => setGeneration((g) => g + 1), []);

  return { state, send, stop, reconnect };
}
