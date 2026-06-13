import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Health, McpServer, Role, User } from "../api/types";
import { isAdmin, useAuth } from "../auth";
import { EmptyState, ErrorBox, Icon, Modal, Pill, Spinner } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { getLanding, LANDING_OPTIONS, type LandingPref, setLanding } from "../lib/prefs";

/** Personalization (#59): per-browser preferences (localStorage today; see
 *  prefs.ts for the v2 server-swap contract). */
function PreferencesCard() {
  const [landing, setLandingState] = useState<LandingPref>(() => getLanding());
  return (
    <div className="card card-pad" style={{ marginBottom: 18 }}>
      <h3>Preferences</h3>
      <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "4px 0 14px" }}>
        Saved in this browser only. Personal comforts — they don't change anything for your
        teammates.
      </p>
      <label className="field" style={{ maxWidth: 320, marginBottom: 0 }}>
        Default landing page
        <select
          value={landing}
          onChange={(e) => {
            const next = e.target.value as LandingPref;
            setLandingState(next);
            setLanding(next);
          }}
          style={{ marginTop: 5 }}
        >
          {LANDING_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <div className="field-hint">
          Where a fresh tab opens. Deep links (notification emails, shared URLs) always override
          this.
        </div>
      </label>
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
                  <td>{s.enabled ? <Pill value="active" /> : <Pill value="disabled" />}</td>
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
            {health ? <Pill value="pass" /> : <Pill value="unknown" />} v{health?.version ?? "…"}
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

      <PreferencesCard />

      <McpServersCard />

      {isAdmin(user) ? (
        <div className="card">
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
                      <td>{u.is_active ? <Pill value="active" /> : <Pill value="disabled" />}</td>
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
      ) : (
        <div className="card">
          <EmptyState title="User management is admin-only" hint="Ask an admin to change roles or invite teammates." />
        </div>
      )}
      {inviting && <NewUserModal onClose={() => setInviting(false)} />}
    </div>
  );
}
