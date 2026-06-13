import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type {
  AuditPage,
  Dataset,
  Health,
  McpServer,
  NotificationRule,
  NotifyChannel,
  Role,
  Severity,
  User,
} from "../api/types";
import { isAdmin, useAuth } from "../auth";
import { EmptyState, ErrorBox, Icon, Modal, Spinner, StatusPill } from "../components/ui";
import { fmtDateTime } from "../lib/format";

const AUDIT_PAGE = 25;
const ENTITY_TYPES = ["user", "connection", "check", "exception", "dataset", "mcp"];
const SINCE_PRESETS: { label: string; hours: number | null }[] = [
  { label: "Any time", hours: null },
  { label: "Last 24h", hours: 24 },
  { label: "Last 7 days", hours: 24 * 7 },
  { label: "Last 30 days", hours: 24 * 30 },
];

function AuditCard() {
  const [entityType, setEntityType] = useState("");
  const [action, setAction] = useState("");
  const [sinceHours, setSinceHours] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);

  const since =
    sinceHours == null ? undefined : new Date(Date.now() - sinceHours * 3600_000).toISOString();
  const params = new URLSearchParams();
  params.set("limit", String(AUDIT_PAGE));
  params.set("offset", String(offset));
  if (entityType) params.set("entity_type", entityType);
  if (action) params.set("q", action); // action prefix match
  if (since) params.set("since", since);

  const { data, error, isLoading, isFetching } = useQuery({
    queryKey: ["audit", entityType, action, sinceHours, offset],
    queryFn: () => api.get<AuditPage>(`/audit?${params.toString()}`),
    placeholderData: keepPreviousData,
  });

  // Any filter change resets paging to the first page.
  const resetting = <T,>(setter: (v: T) => void) => (v: T) => {
    setOffset(0);
    setter(v);
  };

  const total = data?.total ?? 0;
  const shown = data?.items.length ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = offset + shown;

  return (
    <div className="card" style={{ marginBottom: 18 }}>
      <div className="card-pad" style={{ paddingBottom: 8 }}>
        <h3>Audit log</h3>
        <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "4px 0 10px" }}>
          Append-only trail of security- and config-relevant actions — logins, user/connection/check
          changes, and triage. Secrets, DSNs, and source rows are never recorded.
        </p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="field" style={{ margin: 0, minWidth: 150 }}>
            Entity
            <select value={entityType} onChange={(e) => resetting(setEntityType)(e.target.value)}>
              <option value="">All entities</option>
              {ENTITY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ margin: 0, minWidth: 180 }}>
            Action starts with
            <input
              type="text"
              value={action}
              placeholder="e.g. login, check"
              onChange={(e) => resetting(setAction)(e.target.value)}
            />
          </label>
          <label className="field" style={{ margin: 0, minWidth: 150 }}>
            Since
            <select
              value={sinceHours ?? ""}
              onChange={(e) =>
                resetting(setSinceHours)(e.target.value ? Number(e.target.value) : null)
              }
            >
              {SINCE_PRESETS.map((p) => (
                <option key={p.label} value={p.hours ?? ""}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <ErrorBox error={error} />
      {isLoading ? (
        <Spinner />
      ) : !data?.items.length ? (
        <div className="empty" style={{ padding: 18 }}>
          No audit entries match these filters.
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th style={{ whiteSpace: "nowrap" }}>When</th>
                <th>Who</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <td style={{ color: "var(--text-light)", whiteSpace: "nowrap" }}>
                    {fmtDateTime(row.created_at)}
                  </td>
                  <td>{row.user ?? <span style={{ color: "var(--text-light)" }}>system</span>}</td>
                  <td>
                    <code>{row.action}</code>
                  </td>
                  <td style={{ color: "var(--text-light)" }}>
                    {row.entity_type}
                    {row.entity_id != null ? ` #${row.entity_id}` : ""}
                  </td>
                  <td style={{ maxWidth: 360 }}>
                    {Object.keys(row.detail).length ? (
                      <details>
                        <summary style={{ cursor: "pointer", color: "var(--brand)" }}>view</summary>
                        <pre
                          className="mono"
                          style={{ fontSize: 11.5, whiteSpace: "pre-wrap", margin: "6px 0 0" }}
                        >
                          {JSON.stringify(row.detail, null, 2)}
                        </pre>
                      </details>
                    ) : (
                      <span style={{ color: "var(--text-light)" }}>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div
        className="card-pad"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 10 }}
      >
        <span style={{ fontSize: 12, color: "var(--text-light)" }}>
          {total === 0 ? "No entries" : `${from}–${to} of ${total}`}
          {isFetching ? " · …" : ""}
        </span>
        <span style={{ whiteSpace: "nowrap" }}>
          <button
            className="small"
            disabled={offset === 0 || isFetching}
            onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE))}
          >
            Prev
          </button>{" "}
          <button
            className="small"
            disabled={to >= total || isFetching}
            onClick={() => setOffset(offset + AUDIT_PAGE)}
          >
            Next
          </button>
        </span>
      </div>
    </div>
  );
}

function McpServersCard() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const admin = isAdmin(user);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [description, setDescription] = useState("");

  const { data, error } = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: () => api.get<McpServer[]>("/mcp-servers"),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["mcp-servers"] });
  const create = useMutation({
    mutationFn: () =>
      api.post<McpServer>("/mcp-servers", { name, url, auth_token: token, description }),
    onSuccess: () => {
      setAdding(false);
      setName("");
      setUrl("");
      setToken("");
      setDescription("");
      invalidate();
    },
  });
  const toggle = useMutation({
    mutationFn: (s: McpServer) => api.patch<McpServer>(`/mcp-servers/${s.id}`, { enabled: !s.enabled }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/mcp-servers/${id}`),
    onSuccess: invalidate,
  });

  return (
    <div className="card" style={{ marginBottom: 18 }}>
      <div className="card-pad" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", paddingBottom: 8 }}>
        <div>
          <h3>MCP servers — code context for AI agents <span className="badge">experimental</span></h3>
          <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "4px 0 0" }}>
            Registered servers are attached to every AI call (check generation, exploration, root-cause
            analysis) via the Claude MCP connector, letting agents read upstream code — dbt models, ETL
            repos, docs — while investigating. Tokens are write-only and never returned by the API.
          </p>
        </div>
        {admin && (
          <button className="primary small" onClick={() => setAdding(true)} style={{ flex: "none" }}>
            <Icon name="plus" size={13} /> Add server
          </button>
        )}
      </div>
      <ErrorBox error={error || create.error || toggle.error || remove.error} />
      {!data?.length ? (
        <div className="empty" style={{ padding: 18 }}>No MCP servers registered.</div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Name</th>
                <th>URL</th>
                <th>Auth</th>
                <th>Status</th>
                <th>Description</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((s) => (
                <tr key={s.id}>
                  <td style={{ fontWeight: 700, color: "var(--text-dark)" }}>{s.name}</td>
                  <td className="mono" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.url}</td>
                  <td>{s.has_token ? <span className="badge">token set</span> : <span style={{ color: "var(--text-light)" }}>none</span>}</td>
                  <td>{s.enabled ? <StatusPill value="active" /> : <StatusPill value="disabled" />}</td>
                  <td style={{ fontSize: 12, color: "var(--text-light)" }}>{s.description || "—"}</td>
                  {admin && (
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <button className="small" onClick={() => toggle.mutate(s)}>
                        {s.enabled ? "Disable" : "Enable"}
                      </button>{" "}
                      <button className="small danger" onClick={() => remove.mutate(s.id)}>
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && (
        <Modal
          title="Add MCP server"
          onClose={() => setAdding(false)}
          footer={
            <>
              <button onClick={() => setAdding(false)}>Cancel</button>
              <button className="primary" onClick={() => create.mutate()} disabled={!name || !url || create.isPending}>
                Add
              </button>
            </>
          }
        >
          <ErrorBox error={create.error} />
          <label className="field">
            Name <span className="req">*</span>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="dbt-models" />
          </label>
          <label className="field">
            URL <span className="req">*</span>
            <input type="text" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://mcp.example.com/sse" style={{ fontFamily: "var(--mono)", fontSize: 12 }} />
            <div className="field-hint">Streamable-HTTP MCP endpoint, reachable from the Anthropic API (not from this app).</div>
          </label>
          <label className="field">
            Authorization token
            <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="optional bearer token" />
          </label>
          <label className="field">
            Description
            <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What code/context lives here" />
          </label>
        </Modal>
      )}
    </div>
  );
}

function NotificationsCard() {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [datasetId, setDatasetId] = useState<string>(""); // "" = all datasets
  const [minSeverity, setMinSeverity] = useState<Severity>("error");
  const [channel, setChannel] = useState<NotifyChannel>("slack");
  const [target, setTarget] = useState("");
  const [onErrorRuns, setOnErrorRuns] = useState(true);
  const [tested, setTested] = useState<Record<number, string>>({});

  const { data, error } = useQuery({
    queryKey: ["notification-rules"],
    queryFn: () => api.get<NotificationRule[]>("/notifications/rules"),
  });
  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["notification-rules"] });
  const reset = () => {
    setAdding(false);
    setDatasetId("");
    setMinSeverity("error");
    setChannel("slack");
    setTarget("");
    setOnErrorRuns(true);
  };
  const create = useMutation({
    mutationFn: () =>
      api.post<NotificationRule>("/notifications/rules", {
        dataset_id: datasetId ? Number(datasetId) : null,
        min_severity: minSeverity,
        channel,
        target,
        on_error_runs: onErrorRuns,
      }),
    onSuccess: () => {
      reset();
      invalidate();
    },
  });
  const toggle = useMutation({
    mutationFn: (r: NotificationRule) =>
      api.patch<NotificationRule>(`/notifications/rules/${r.id}`, { enabled: !r.enabled }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/notifications/rules/${id}`),
    onSuccess: invalidate,
  });
  const test = useMutation({
    mutationFn: (id: number) => api.post<{ ok: boolean; message: string }>(`/notifications/rules/${id}/test`),
    onSuccess: (res, id) => setTested((t) => ({ ...t, [id]: res.ok ? "✓ sent" : res.message })),
  });

  // Slack target may be blank (falls back to the global webhook); email needs a target.
  const addDisabled = create.isPending || (channel === "email" && !target.trim());

  return (
    <div className="card" style={{ marginBottom: 18 }}>
      <div className="card-pad" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", paddingBottom: 8 }}>
        <div>
          <h3>Notifications — alerts on failed runs</h3>
          <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "4px 0 0" }}>
            Route check failures to Slack or email. Alerts fire on the transition into failure (and once
            on recovery) — a check that keeps failing stays quiet, so you are not paged twice for the same
            break. A rule with no dataset applies to all datasets; the severity gate compares against each
            check's severity. Configure transports (SMTP, a global Slack webhook) via <code>DQ_</code> env vars.
          </p>
        </div>
        <button className="primary small" onClick={() => setAdding(true)} style={{ flex: "none" }}>
          <Icon name="plus" size={13} /> Add rule
        </button>
      </div>
      <ErrorBox error={error || create.error || toggle.error || remove.error || test.error} />
      {!data?.length ? (
        <div className="empty" style={{ padding: 18 }}>No notification rules. Failures will not be pushed anywhere.</div>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Min severity</th>
                <th>Channel</th>
                <th>Target</th>
                <th>On errors</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontWeight: 600, color: "var(--text-dark)" }}>
                    {r.dataset_id === null ? <span className="badge">all datasets</span> : r.dataset_name || `#${r.dataset_id}`}
                  </td>
                  <td><Pill value={r.min_severity} /></td>
                  <td>{r.channel}</td>
                  <td className="mono" style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.target || <span style={{ color: "var(--text-light)" }}>global default</span>}
                  </td>
                  <td>{r.on_error_runs ? "yes" : "no"}</td>
                  <td>{r.enabled ? <Pill value="active" /> : <Pill value="disabled" />}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {tested[r.id] && <span style={{ fontSize: 11, color: "var(--text-light)", marginRight: 6 }}>{tested[r.id]}</span>}
                    <button className="small" onClick={() => test.mutate(r.id)} disabled={test.isPending}>Test</button>{" "}
                    <button className="small" onClick={() => toggle.mutate(r)}>{r.enabled ? "Disable" : "Enable"}</button>{" "}
                    <button className="small danger" onClick={() => remove.mutate(r.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && (
        <Modal
          title="Add notification rule"
          onClose={reset}
          footer={
            <>
              <button onClick={reset}>Cancel</button>
              <button className="primary" onClick={() => create.mutate()} disabled={addDisabled}>
                Add rule
              </button>
            </>
          }
        >
          <ErrorBox error={create.error} />
          <div className="form-row">
            <label className="field">
              Dataset
              <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
                <option value="">All datasets</option>
                {datasets?.map((d) => (
                  <option key={d.id} value={d.id}>{d.display_name || d.table_name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              Minimum severity
              <select value={minSeverity} onChange={(e) => setMinSeverity(e.target.value as Severity)}>
                <option value="info">info — alert on any check</option>
                <option value="warn">warn — warn & error checks</option>
                <option value="error">error — error checks only</option>
              </select>
            </label>
          </div>
          <label className="field">
            Channel
            <select value={channel} onChange={(e) => setChannel(e.target.value as NotifyChannel)}>
              <option value="slack">Slack webhook</option>
              <option value="email">Email (SMTP)</option>
            </select>
          </label>
          <label className="field">
            {channel === "slack" ? "Webhook URL" : "Recipients"}
            {channel === "email" && <span className="req"> *</span>}
            <input
              type="text"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder={channel === "slack" ? "https://hooks.slack.com/services/… (blank = global default)" : "alice@co.com, oncall@co.com"}
              style={{ fontFamily: "var(--mono)", fontSize: 12 }}
            />
            <div className="field-hint">
              {channel === "slack"
                ? "Slack incoming-webhook URL. Leave blank to use the global DQ_NOTIFY_SLACK_WEBHOOK_URL."
                : "Comma-separated email addresses. Requires SMTP configured via DQ_SMTP_* env vars."}
            </div>
          </label>
          <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={onErrorRuns} onChange={(e) => setOnErrorRuns(e.target.checked)} style={{ width: "auto" }} />
            Also fire on run errors (infra failures, not just data violations)
          </label>
        </Modal>
      )}
    </div>
  );
}

function NewUserModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");

  const create = useMutation({
    mutationFn: () => api.post<User>("/auth/users", { email, name, password, role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
  });

  return (
    <Modal
      title="Invite user"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={() => create.mutate()} disabled={!email || password.length < 8 || create.isPending}>
            Create user
          </button>
        </>
      }
    >
      <ErrorBox error={create.error} />
      <label className="field">
        Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label className="field">
        Name
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="form-row">
        <label className="field">
          Password (min 8 chars)
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label className="field">
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="viewer">viewer — read only</option>
            <option value="editor">editor — manage checks & triage</option>
            <option value="admin">admin — connections & users</option>
          </select>
        </label>
      </div>
    </Modal>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [inviting, setInviting] = useState(false);

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/health") });
  const { data: users, isLoading, error } = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<User[]>("/auth/users"),
    enabled: isAdmin(user),
    retry: false,
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      api.patch<User>(`/auth/users/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <div className="sub">Instance health, AI integration, and user management</div>
        </div>
      </div>

      <div className="grid cols-3" style={{ marginBottom: 18 }}>
        <div className="card stat-card">
          <div className="label">API</div>
          <div className="value" style={{ fontSize: 18 }}>
            {health ? <StatusPill value="pass" /> : <StatusPill value="unknown" />} v{health?.version ?? "…"}
          </div>
        </div>
        <div className="card stat-card">
          <div className="label">LLM features</div>
          <div className="value" style={{ fontSize: 18 }}>
            {health?.llm_enabled ? <span className="badge ai">enabled</span> : <span className="badge">disabled</span>}
          </div>
          {health?.llm_enabled ? (
            <div className="hint">
              provider <strong>{health.llm_provider}</strong> · model <code>{health.llm_model}</code>
            </div>
          ) : (
            <div className="hint">
              Provider-agnostic: set ANTHROPIC_API_KEY (native) or DQ_LLM_API_KEY + DQ_LLM_MODEL for any
              OpenAI-compatible endpoint (OpenRouter by default; DQ_LLM_BASE_URL to point elsewhere).
            </div>
          )}
        </div>
        <div className="card stat-card">
          <div className="label">Signed in as</div>
          <div className="value" style={{ fontSize: 18 }}>{user?.email}</div>
          <div className="hint">role: {user?.role}</div>
        </div>
      </div>

      <McpServersCard />

      {isAdmin(user) && <NotificationsCard />}

      {isAdmin(user) ? (
        <>
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-pad" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingBottom: 8 }}>
            <h3>Users</h3>
            <button className="primary small" onClick={() => setInviting(true)}>
              <Icon name="plus" size={13} /> Invite user
            </button>
          </div>
          <ErrorBox error={error || update.error} />
          {isLoading ? (
            <Spinner />
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Email</th>
                    <th>Name</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {users?.map((u) => (
                    <tr key={u.id}>
                      <td style={{ fontWeight: 600, color: "var(--text-dark)" }}>{u.email}</td>
                      <td>{u.name || "—"}</td>
                      <td>
                        <select
                          value={u.role}
                          disabled={u.id === user?.id}
                          onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } })}
                          style={{ marginTop: 0, width: 110 }}
                        >
                          <option value="viewer">viewer</option>
                          <option value="editor">editor</option>
                          <option value="admin">admin</option>
                        </select>
                      </td>
                      <td>{u.is_active ? <StatusPill value="active" /> : <StatusPill value="disabled" />}</td>
                      <td style={{ color: "var(--text-light)" }}>{fmtDateTime(u.created_at)}</td>
                      <td style={{ textAlign: "right" }}>
                        {u.id !== user?.id && (
                          <button
                            className="small"
                            onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}
                          >
                            {u.is_active ? "Deactivate" : "Activate"}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <AuditCard />
        </>
      ) : (
        <div className="card">
          <EmptyState title="User management is admin-only" hint="Ask an admin to change roles or invite teammates." />
        </div>
      )}
      {inviting && <NewUserModal onClose={() => setInviting(false)} />}
    </div>
  );
}
