import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { api } from "../api/client";
import { qk } from "../api/queryKeys";
import type { ChatMessage, ChatSession, ChatStep, ChatWsEvent, Health } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { ChatComposer } from "../components/assistant/ChatComposer";
import { MessageView } from "../components/assistant/MessageView";
import { SessionsSidebar } from "../components/assistant/SessionsSidebar";
import { StepList } from "../components/assistant/StepList";
import { useConfirm } from "../components/confirm";
import { EmptyState, ErrorBox } from "../components/ui";
import { useChatSocket } from "../lib/useChatSocket";

const SUGGESTIONS = [
  "What's broken right now?",
  "Summarize the datasets we monitor and how healthy they are",
  "Investigate the most recent failed check and find the root cause",
  "Pick the busiest dataset and chart its daily row volume",
];

export default function AssistantPage() {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
  const confirm = useConfirm();

  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [liveSteps, setLiveSteps] = useState<ChatStep[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wsError, setWsError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const pendingRef = useRef<string | null>(null);
  const sendMessageRef = useRef<(text: string) => void>(() => {});
  const threadEndRef = useRef<HTMLDivElement | null>(null);

  const { data: health } = useQuery({ queryKey: qk.health.get(), queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? true;

  const sessions = useQuery({
    queryKey: qk.chatSessions.list(),
    queryFn: () => api.get<ChatSession[]>("/chat/sessions"),
  });

  const onEvent = useCallback(
    (e: ChatWsEvent) => {
      if (e.type === "session") {
        setMessages(e.messages);
        setLiveSteps([]);
        setBusy(false);
        setStatus(null);
        if (pendingRef.current) {
          const text = pendingRef.current;
          pendingRef.current = null;
          sendMessageRef.current(text);
        }
      } else if (e.type === "message_saved") {
        setMessages((m) => [...m, e.message]);
      } else if (e.type === "status") {
        setStatus(
          e.state === "thinking"
            ? "Thinking…"
            : `Running ${e.tool?.replace(/_/g, " ") ?? "a tool"}${e.detail ? ` — ${e.detail}` : ""}…`,
        );
      } else if (e.type === "step") {
        setLiveSteps((s) => [...s, e.step]);
      } else if (e.type === "assistant_message") {
        setMessages((m) => [...m, e.message]);
        setLiveSteps([]);
        setStatus(null);
      } else if (e.type === "error") {
        setWsError(e.detail);
      } else if (e.type === "done") {
        setBusy(false);
        setStatus(null);
        qc.invalidateQueries({ queryKey: qk.chatSessions.all });
        // The assistant can author checks/SLAs (#186); refresh those views so a
        // newly-created object shows up without a manual reload.
        for (const family of [qk.checks.all, qk.runs.all, qk.sla.all, qk.reliability.all]) {
          qc.invalidateQueries({ queryKey: family });
        }
      }
    },
    [qc],
  );

  const socket = useChatSocket(sessionId, onEvent);

  // wipe thread state when switching sessions (the socket re-sends history)
  useEffect(() => {
    setMessages([]);
    setLiveSteps([]);
    setBusy(false);
    setStatus(null);
    setWsError(null);
  }, [sessionId]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, liveSteps, status]);

  const sendMessage = useCallback(
    (text: string) => {
      const content = text.trim();
      if (!content) return;
      setWsError(null);
      setBusy(true);
      socket.send(content);
      setInput("");
    },
    [socket.send],
  );
  sendMessageRef.current = sendMessage;

  const createSession = useMutation({
    mutationFn: () => api.post<ChatSession>("/chat/sessions", {}),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: qk.chatSessions.all });
      setSessionId(s.id);
    },
  });

  const deleteSession = useMutation({
    mutationFn: (id: number) => api.del<void>(`/chat/sessions/${id}`),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: qk.chatSessions.all });
      if (id === sessionId) setSessionId(null);
    },
  });

  const onDeleteSession = async (s: ChatSession) => {
    if (
      await confirm({
        title: "Delete conversation",
        danger: true,
        confirmLabel: "Delete",
        body: (
          <>
            Delete <strong>{s.title || "New conversation"}</strong>? Its messages will be removed.
          </>
        ),
      })
    )
      deleteSession.mutate(s.id);
  };

  const startWith = (text: string) => {
    if (!editable) return;
    if (sessionId === null) {
      pendingRef.current = text;
      createSession.mutate();
    } else {
      sendMessage(text);
    }
  };

  const onComposerKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!busy && socket.state === "open") sendMessage(input);
    }
  };

  const disconnectedMidTurn = busy && socket.state === "closed";

  return (
    <div className="chat-layout">
      <SessionsSidebar
        sessions={sessions.data ?? []}
        loading={sessions.isLoading}
        sessionId={sessionId}
        editable={editable}
        createPending={createSession.isPending}
        onSelect={setSessionId}
        onCreate={() => createSession.mutate()}
        onDelete={onDeleteSession}
      />

      <section className="chat-main">
        <div className="chat-thread">
          {!llm && (
            <div className="info-box">
              The assistant needs an LLM. Set <code>DQ_LLM_API_KEY</code> + <code>DQ_LLM_MODEL</code> (OpenRouter or any
              OpenAI-compatible endpoint) or <code>ANTHROPIC_API_KEY</code> in the backend environment and restart.
            </div>
          )}
          {!editable && (
            <div className="info-box">The assistant runs read-only SQL against your sources, which requires the editor role.</div>
          )}

          {sessionId === null ? (
            <EmptyState
              title="Ask the assistant"
              hint="Root-cause analysis, questions about your datasets and checks, or charts — it investigates with guarded read-only SQL."
            >
              <div className="chat-suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="chat-chip" onClick={() => startWith(s)} disabled={!editable}>
                    {s}
                  </button>
                ))}
              </div>
            </EmptyState>
          ) : (
            <>
              {messages.map((m) => (
                <MessageView key={m.id} message={m} />
              ))}
              {(liveSteps.length > 0 || busy) && (
                <div className="chat-msg assistant">
                  <StepList steps={liveSteps} />
                  {status && (
                    <div className="chat-status">
                      <span className="spinner" /> {status}
                    </div>
                  )}
                </div>
              )}
              {messages.length === 0 && !busy && socket.state === "open" && (
                <EmptyState title="What do you want to know?" hint="Try one of these:">
                  <div className="chat-suggestions">
                    {SUGGESTIONS.map((s) => (
                      <button key={s} className="chat-chip" onClick={() => startWith(s)} disabled={!editable || busy}>
                        {s}
                      </button>
                    ))}
                  </div>
                </EmptyState>
              )}
            </>
          )}
          <ErrorBox error={wsError} />
          {disconnectedMidTurn && (
            <div className="info-box">
              Connection lost while answering.{" "}
              <button className="small" onClick={socket.reconnect}>
                Reconnect
              </button>
            </div>
          )}
          <div ref={threadEndRef} />
        </div>

        <ChatComposer
          input={input}
          placeholder={
            sessionId === null
              ? "Start a new conversation…"
              : "Ask about your data, investigate a failure, or request a chart… (Enter to send)"
          }
          busy={busy}
          textareaDisabled={!editable || sessionId === null || busy || socket.state !== "open"}
          sendDisabled={!editable || sessionId === null || !input.trim() || socket.state !== "open"}
          onInput={setInput}
          onKey={onComposerKey}
          onSend={() => sendMessage(input)}
          onStop={socket.stop}
        />
      </section>
    </div>
  );
}
