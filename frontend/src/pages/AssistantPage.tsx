import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { api } from "../api/client";
import type { ChatMessage, ChatSession, ChatStep, ChatWsEvent, Health } from "../api/types";
import { canEdit, useAuth } from "../auth";
import { useConfirm } from "../components/confirm";
import ErrorBoundary from "../components/ErrorBoundary";
import Markdown from "../components/Markdown";
import PanelChart from "../components/PanelChart";
import { EmptyState, ErrorBox, Icon, Spinner } from "../components/ui";
import { timeAgo } from "../lib/format";
import { useChatSocket } from "../lib/useChatSocket";

const SUGGESTIONS = [
  "What's broken right now?",
  "Summarize the datasets we monitor and how healthy they are",
  "Investigate the most recent failed check and find the root cause",
  "Pick the busiest dataset and chart its daily row volume",
];

function StepList({ steps }: { steps: ChatStep[] }) {
  const out: ReactNode[] = [];
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    if (s.type === "text") {
      out.push(<Markdown key={i}>{s.content}</Markdown>);
    } else if (s.type === "sql") {
      // pair each query with its result in one collapsible
      const next = steps[i + 1];
      const result = next?.type === "result" ? next : null;
      if (result) i++;
      out.push(
        <details key={i} className="chat-activity">
          <summary>
            <Icon name="search" size={12} /> {s.purpose || "Ran a query"}
            {result?.error && <span className="badge danger">failed</span>}
          </summary>
          <pre className="sql">{s.sql}</pre>
          {result && (
            <pre className="result" style={result.error ? { borderColor: "var(--danger)", color: "var(--danger-dark)" } : undefined}>
              {result.content}
            </pre>
          )}
        </details>,
      );
    } else if (s.type === "result") {
      // orphan result (its sql was rendered in a previous batch) — show plainly
      out.push(
        <details key={i} className="chat-activity">
          <summary>{s.error ? "Query failed" : "Result"}</summary>
          <pre className="result">{s.content}</pre>
        </details>,
      );
    } else if (s.type === "tool") {
      const next = steps[i + 1];
      const result = next?.type === "result" ? next : null;
      if (result) i++;
      out.push(
        <details key={i} className="chat-activity">
          <summary>
            <Icon name="book" size={12} /> Looked at {s.name.replace(/_/g, " ").replace(/^get /, "")}
            {result?.error && <span className="badge danger">failed</span>}
          </summary>
          {result && <pre className="result">{result.content}</pre>}
        </details>,
      );
    } else if (s.type === "chart") {
      out.push(
        <div key={i} className="chat-chart">
          <div className="chat-chart-title">{s.title}</div>
          <ErrorBoundary fallback={<div className="error-box">Could not render this chart.</div>}>
            <PanelChart columns={s.columns ?? []} rows={s.rows ?? []} viz={s.viz} height={220} />
          </ErrorBoundary>
        </div>,
      );
    } else if (s.type === "error") {
      out.push(
        <div key={i} className="error-box">
          {s.content}
        </div>,
      );
    }
  }
  return <>{out}</>;
}

function MessageView({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return <div className="chat-msg user">{message.content}</div>;
  }
  return (
    <div className="chat-msg assistant">
      <StepList steps={message.steps} />
      {message.steps.length === 0 && message.content && <Markdown>{message.content}</Markdown>}
    </div>
  );
}

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

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const llm = health?.llm_enabled ?? true;

  const sessions = useQuery({
    queryKey: ["chat-sessions"],
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
        qc.invalidateQueries({ queryKey: ["chat-sessions"] });
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
      qc.invalidateQueries({ queryKey: ["chat-sessions"] });
      setSessionId(s.id);
    },
  });

  const deleteSession = useMutation({
    mutationFn: (id: number) => api.del<void>(`/chat/sessions/${id}`),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["chat-sessions"] });
      if (id === sessionId) setSessionId(null);
    },
  });

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
      <aside className="chat-sessions">
        <button
          className="primary"
          style={{ width: "100%", justifyContent: "center" }}
          onClick={() => createSession.mutate()}
          disabled={createSession.isPending || !editable}
        >
          <Icon name="plus" size={14} /> New conversation
        </button>
        <div className="chat-session-list">
          {sessions.isLoading ? (
            <Spinner />
          ) : (
            (sessions.data ?? []).map((s) => (
              <div
                key={s.id}
                className={`chat-session-item${s.id === sessionId ? " active" : ""}`}
                onClick={() => setSessionId(s.id)}
              >
                <div className="title">{s.title || "New conversation"}</div>
                <div className="meta">
                  {s.message_count > 0 ? `${s.message_count} messages · ` : ""}
                  {timeAgo(s.updated_at)}
                </div>
                <button
                  className="ghost small del"
                  title="Delete conversation"
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (
                      await confirm({
                        title: "Delete conversation",
                        danger: true,
                        confirmLabel: "Delete",
                        body: (
                          <>
                            Delete <strong>{s.title || "New conversation"}</strong>? Its messages will be
                            removed.
                          </>
                        ),
                      })
                    )
                      deleteSession.mutate(s.id);
                  }}
                >
                  <Icon name="x" size={12} />
                </button>
              </div>
            ))
          )}
        </div>
      </aside>

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

        <div className="chat-composer">
          <textarea
            rows={2}
            placeholder={
              sessionId === null
                ? "Start a new conversation…"
                : "Ask about your data, investigate a failure, or request a chart… (Enter to send)"
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onComposerKey}
            disabled={!editable || sessionId === null || busy || socket.state !== "open"}
          />
          {busy ? (
            <button className="danger" onClick={socket.stop} title="Stop generating">
              <Icon name="x" size={14} /> Stop
            </button>
          ) : (
            <button
              className="primary"
              onClick={() => sendMessage(input)}
              disabled={!editable || sessionId === null || !input.trim() || socket.state !== "open"}
            >
              <Icon name="bolt" size={14} /> Send
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
