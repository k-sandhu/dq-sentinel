import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { qk } from "../api/queryKeys";
import type {
  AuditPage,
  Connection,
  Dataset,
  Grant,
  GrantIn,
  GrantRole,
  Health,
  McpServer,
  NotificationRule,
  NotifyChannel,
  Role,
  Severity,
  User,
} from "../api/types";
import { isAdmin, useAuth } from "../auth";
import { useConfirm } from "../components/confirm";
import { EmptyState, ErrorBox, Icon, Modal, SeverityBadge, Spinner, StatusPill } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { getLanding, LANDING_OPTIONS, type NamedLanding, setLanding } from "../lib/prefs";

/** Personalization (#59): per-browser preferences (localStorage today; see
 *  prefs.ts for the v2 server-swap contract). */
function PreferencesCard() {
  // The dropdown only offers the named pages; a stored custom-dashboard landing
  // (#68) isn't one of them, so fall back to "Home" for display in that case.
  const [landing, setLandingState] = useState<NamedLanding>(() => {
    const stored = getLanding();
    return LANDING_OPTIONS.some((o) => o.value === stored) ? (stored as NamedLanding) : "/";
  });
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
            const next = e.target.value as NamedLanding;
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
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [description, setDescription] = useState("");
  const dirty = name !== "" || url !== "" || token !== "" || description !== "";

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
                      <button
                        className="small danger"
                        onClick={async () => {
                          if (
                            await confirm({
                              title: "Delete MCP server",
                              danger: true,
                              confirmLabel: "Delete",
                              body: (
                                <>
                                  Delete <strong>{s.name}</strong>? AI calls will stop using it.
                                </>
                              ),
                            })
                          )
                            remove.mutate(s.id);
                        }}
                      >
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
          dirty={dirty}
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

const CHANNELS: Record<
  NotifyChannel,
  {
    label: string;
    targetLabel: string;
    placeholder: string;
    hint: string;
    required: boolean;
    targetSecret: boolean;
  }
> = {
  slack: {
    label: "Slack",
    targetLabel: "Webhook URL",
    placeholder: "Blank = global Slack webhook",
    hint: "Incoming webhook URL. Leave blank to use DQ_NOTIFY_SLACK_WEBHOOK_URL.",
    required: false,
    targetSecret: true,
  },
  email: {
    label: "Email",
    targetLabel: "Recipients",
    placeholder: "alice@co.com, oncall@co.com",
    hint: "Comma-separated recipients. Requires SMTP configured via DQ_SMTP_* env vars.",
    required: true,
    targetSecret: false,
  },
  webhook: {
    label: "Webhook",
    targetLabel: "HTTPS endpoint",
    placeholder: "https://hooks.example.com/dq",
    hint: "Generic HTTPS destination. Leave blank to use the backend's configured webhook default.",
    required: false,
    targetSecret: true,
  },
  teams: {
    label: "Teams",
    targetLabel: "Incoming webhook URL",
    placeholder: "Blank = configured Teams webhook",
    hint: "Microsoft Teams incoming-webhook URL. Leave blank to use backend environment configuration.",
    required: false,
    targetSecret: true,
  },
  pagerduty: {
    label: "PagerDuty",
    targetLabel: "Service override",
    placeholder: "Blank = configured routing key",
    hint: "PagerDuty uses the backend routing key from environment configuration; target is an optional route label.",
    required: false,
    targetSecret: false,
  },
  jira: {
    label: "Jira",
    targetLabel: "Project key",
    placeholder: "Blank = configured Jira project",
    hint: "Creates or updates Jira issues. Site URL, credentials, and default project come from backend configuration.",
    required: false,
    targetSecret: false,
  },
  servicenow: {
    label: "ServiceNow",
    targetLabel: "Assignment group or table",
    placeholder: "Blank = configured assignment group",
    hint: "Creates or updates ServiceNow incidents. Instance URL, credentials, and defaults come from backend configuration.",
    required: false,
    targetSecret: false,
  },
};

function displayTarget(rule: NotificationRule): string {
  if (!rule.target) return "global default";
  if (rule.channel === "email" || rule.channel === "jira" || rule.channel === "servicenow") return rule.target;
  try {
    const parsed = new URL(rule.target);
    return `${parsed.hostname}/...`;
  } catch {
    return "configured";
  }
}

function NotificationsCard() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const [datasetId, setDatasetId] = useState<string>(""); // "" = all datasets
  const [minSeverity, setMinSeverity] = useState<Severity>("error");
  const [channel, setChannel] = useState<NotifyChannel>("slack");
  const [target, setTarget] = useState("");
  const [onErrorRuns, setOnErrorRuns] = useState(true);
  const [dedupeWindow, setDedupeWindow] = useState("60");
  const [escalationDelay, setEscalationDelay] = useState("");
  const [maxEscalationLevel, setMaxEscalationLevel] = useState("0");
  const [tested, setTested] = useState<Record<number, string>>({});

  const { data, error } = useQuery({
    queryKey: ["notification-rules"],
    queryFn: () => api.get<NotificationRule[]>("/notifications/rules"),
  });
  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  const meta = CHANNELS[channel];
  const invalidate = () => qc.invalidateQueries({ queryKey: ["notification-rules"] });
  const reset = () => {
    setAdding(false);
    setDatasetId("");
    setMinSeverity("error");
    setChannel("slack");
    setTarget("");
    setOnErrorRuns(true);
    setDedupeWindow("60");
    setEscalationDelay("");
    setMaxEscalationLevel("0");
  };
  const create = useMutation({
    mutationFn: () => {
      const escalationDelayMinutes = Number(escalationDelay);
      const body: Record<string, unknown> = {
        dataset_id: datasetId ? Number(datasetId) : null,
        min_severity: minSeverity,
        channel,
        target,
        on_error_runs: onErrorRuns,
      };
      if (dedupeWindow.trim()) body.dedupe_window_minutes = Number(dedupeWindow);
      if (Number.isFinite(escalationDelayMinutes) && escalationDelayMinutes > 0) {
        body.escalation_delay_minutes = escalationDelayMinutes;
      }
      if (maxEscalationLevel.trim()) body.max_escalation_level = Number(maxEscalationLevel);
      return api.post<NotificationRule>("/notifications/rules", body);
    },
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
    onSuccess: (res, id) => setTested((t) => ({ ...t, [id]: res.ok ? "sent" : res.message })),
  });

  const addDisabled = create.isPending || (meta.required && !target.trim());

  return (
    <div className="card" style={{ marginBottom: 18 }}>
      <div className="card-pad" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", paddingBottom: 8 }}>
        <div>
          <h3>Notifications and incident integrations</h3>
          <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "4px 0 0" }}>
            Route failed runs and grouped incidents to Slack, email, webhooks, Teams, PagerDuty, Jira, or
            ServiceNow. Channel credentials live in backend configuration; saved rules show route targets and policy state.
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
                <th>Policy</th>
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
                  <td><SeverityBadge severity={r.min_severity} /></td>
                  <td>{CHANNELS[r.channel]?.label ?? r.channel}</td>
                  <td className="mono" style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {displayTarget(r)}
                  </td>
                  <td style={{ fontSize: 12, color: "var(--text-light)", whiteSpace: "nowrap" }}>
                    {r.dedupe_window_minutes ? `dedupe ${r.dedupe_window_minutes}m` : "dedupe default"}
                    {" / "}
                    {r.escalation_delay_minutes ? `escalate ${r.escalation_delay_minutes}m` : "escalate default"}
                    {r.max_escalation_level ? ` / max L${r.max_escalation_level}` : ""}
                  </td>
                  <td>{r.on_error_runs ? "yes" : "no"}</td>
                  <td>{r.enabled ? <StatusPill value="active" /> : <StatusPill value="disabled" />}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {tested[r.id] && <span style={{ fontSize: 11, color: "var(--text-light)", marginRight: 6 }}>{tested[r.id]}</span>}
                    <button className="small" onClick={() => test.mutate(r.id)} disabled={test.isPending}>Test</button>{" "}
                    <button className="small" onClick={() => toggle.mutate(r)}>{r.enabled ? "Disable" : "Enable"}</button>{" "}
                    <button
                      className="small danger"
                      onClick={async () => {
                        if (
                          await confirm({
                            title: "Delete notification rule?",
                            body: `Failures matching this ${CHANNELS[r.channel]?.label ?? r.channel} rule will stop notifying anyone.`,
                            confirmLabel: "Delete rule",
                            danger: true,
                          })
                        )
                          remove.mutate(r.id);
                      }}
                    >
                      Delete
                    </button>
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
                <option value="info">info - alert on any check</option>
                <option value="warn">warn - warn and error checks</option>
                <option value="error">error - error checks only</option>
              </select>
            </label>
          </div>
          <label className="field">
            Channel
            <select value={channel} onChange={(e) => setChannel(e.target.value as NotifyChannel)}>
              {Object.entries(CHANNELS).map(([value, option]) => (
                <option key={value} value={value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="field">
            {meta.targetLabel}
            {meta.required && <span className="req"> *</span>}
            <input
              type={meta.targetSecret ? "password" : "text"}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder={meta.placeholder}
              style={{ fontFamily: "var(--mono)", fontSize: 12 }}
            />
            <div className="field-hint">{meta.hint}</div>
          </label>
          <div className="form-row">
            <label className="field">
              Dedupe window (minutes)
              <input type="number" min={0} value={dedupeWindow} onChange={(e) => setDedupeWindow(e.target.value)} />
            </label>
            <label className="field">
              Escalation delay (minutes)
              <input type="number" min={0} value={escalationDelay} onChange={(e) => setEscalationDelay(e.target.value)} />
            </label>
          </div>
          <label className="field">
            Max escalation level
            <input type="number" min={0} max={9} value={maxEscalationLevel} onChange={(e) => setMaxEscalationLevel(e.target.value)} />
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

// ── Per-connection grants (#288) ────────────────────────────────────────────────
// Mirrors backend/app/security.py: ROLE_RANK + connection_role(). Kept here (not
// in a shared lib) because the grants editor is the only surface that has to
// *explain* the rule to an admin.
const ROLE_RANK: Record<Role, number> = { viewer: 0, editor: 1, admin: 2 };

/** Effective role on one connection for a *granted* user: least privilege — the
 *  lower-ranked of their global role and the grant's role. A grant scopes access
 *  and never elevates it, so a global viewer with an editor grant stays a viewer.
 *  Global admins bypass grants entirely and are handled by the caller. */
function effectiveRole(globalRole: Role, grantRole: GrantRole): Role {
  return ROLE_RANK[grantRole] <= ROLE_RANK[globalRole] ? grantRole : globalRole;
}

/** Admin-only editor for one user's per-connection grants (#288). Until this
 *  existed the endpoints were curl-only, so an admin could not see *why* a
 *  teammate's Home panels showed less data. */
function UserGrantsModal({ target, onClose }: { target: User; onClose: () => void }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [connectionId, setConnectionId] = useState("");
  const [newRole, setNewRole] = useState<GrantRole>("viewer");
  const [, setTick] = useState(0); // forces a re-render so a cancelled <select> snaps back

  const grantsKey = qk.grants.byUser(target.id);
  const { data: grants, isLoading, error } = useQuery({
    queryKey: grantsKey,
    queryFn: () => api.get<Grant[]>(`/auth/users/${target.id}/grants`),
  });
  const { data: connections, isLoading: connectionsLoading } = useQuery({
    queryKey: qk.connections.list(),
    queryFn: () => api.get<Connection[]>("/connections"),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: grantsKey });
  const save = useMutation({
    mutationFn: (body: GrantIn) => api.post<Grant>(`/auth/users/${target.id}/grants`, body),
    onSuccess: () => {
      setConnectionId("");
      setNewRole("viewer");
      invalidate();
    },
  });
  const revoke = useMutation({
    mutationFn: (cid: number) => api.del(`/auth/users/${target.id}/grants/${cid}`),
    onSuccess: invalidate,
  });

  const rows = grants ?? [];
  const scoped = rows.length > 0;
  const grantedIds = new Set(rows.map((g) => g.connection_id));
  const addable = (connections ?? []).filter((c) => !grantedIds.has(c.id));
  const busy = save.isPending || revoke.isPending;
  const isGlobalAdmin = target.role === "admin";

  const addGrant = async () => {
    const cid = Number(connectionId);
    const conn = connections?.find((c) => c.id === cid);
    if (!conn) return;
    // The FIRST grant flips the user from "sees everything at their global role"
    // to grant-only visibility — never let that happen silently.
    if (!scoped) {
      const ok = await confirm({
        title: isGlobalAdmin ? "Record a grant for an admin?" : "Restrict this user to granted connections?",
        confirmLabel: "Grant access",
        body: isGlobalAdmin ? (
          <>
            <strong>{target.email}</strong> is a global admin, and admins bypass grants — they will
            keep seeing every connection. This grant is stored but only starts scoping them if you
            lower their role, at which point they would see <strong>only {conn.name}</strong>.
          </>
        ) : (
          <>
            <strong>{target.email}</strong> has no grants today, so they can see{" "}
            <strong>every connection</strong> at their global {target.role} role. Adding this first
            grant switches them to grant-only visibility: from now on they will see{" "}
            <strong>only {conn.name}</strong> until you grant more.
          </>
        ),
      });
      if (!ok) return;
    }
    save.mutate({ connection_id: cid, role: newRole });
  };

  const changeRole = async (g: Grant, role: GrantRole) => {
    if (role === g.role) return;
    const ok = await confirm({
      title: "Change grant role",
      confirmLabel: "Change role",
      body: (
        <>
          Change <strong>{target.email}</strong>'s access to <strong>{g.connection_name}</strong>{" "}
          from <strong>{g.role}</strong> to <strong>{role}</strong>?{" "}
          {isGlobalAdmin
            ? "They are a global admin, so this is recorded but changes nothing until you lower their role."
            : `This takes effect immediately and can never exceed their global ${target.role} role.`}
        </>
      ),
    });
    if (ok) save.mutate({ connection_id: g.connection_id, role });
    else setTick((t) => t + 1);
  };

  const removeGrant = async (g: Grant) => {
    const last = rows.length === 1;
    const ok = await confirm({
      title: "Remove connection grant",
      danger: true,
      confirmLabel: "Remove grant",
      body: isGlobalAdmin ? (
        <>
          Remove the stored <strong>{g.connection_name}</strong> grant for{" "}
          <strong>{target.email}</strong>? They are a global admin, so admins bypass grants and
          their visibility does not change — this only clears the scoping that would apply if you
          lowered their role.
        </>
      ) : last ? (
        <>
          Remove <strong>{target.email}</strong>'s last grant (
          <strong>{g.connection_name}</strong>)? With zero grants they revert to global-role
          visibility and will see <strong>every connection</strong> again at their {target.role}{" "}
          role — this widens their access, it does not remove it.
        </>
      ) : (
        <>
          Remove <strong>{target.email}</strong>'s access to <strong>{g.connection_name}</strong>?
          They will no longer see that connection, its datasets, checks, or exceptions.
        </>
      ),
    });
    if (ok) revoke.mutate(g.connection_id);
  };

  return (
    <Modal
      wide
      title={`Connection access — ${target.email}`}
      onClose={onClose}
      dirty={connectionId !== ""}
      footer={<button onClick={onClose}>Done</button>}
    >
      <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "0 0 12px", lineHeight: 1.55 }}>
        A grant scopes <em>which</em> connections this user can see and at what level. Least
        privilege: a grant never elevates — the effective access is the lower of their global role
        and the grant role, so a global viewer with an editor grant is still read-only. Home,
        datasets, checks, and exceptions are all filtered by these grants.
      </p>

      {isGlobalAdmin ? (
        <div className="empty compact" style={{ textAlign: "left", marginBottom: 12 }}>
          <strong>{target.email} is a global admin.</strong> Admins bypass grants entirely and can
          see every connection, so anything set here only takes effect if you lower their role.
        </div>
      ) : (
        <div className="empty compact" style={{ textAlign: "left", marginBottom: 12 }}>
          {scoped ? (
            <>
              <strong>Scoped.</strong> {target.email} sees only the{" "}
              {rows.length === 1 ? "connection" : `${rows.length} connections`} listed below.
              Removing the last grant restores access to every connection.
            </>
          ) : (
            <>
              <strong>No grants.</strong> {target.email} currently sees every connection at their
              global {target.role} role. Adding the first grant switches them to grant-only
              visibility.
            </>
          )}
        </div>
      )}

      <ErrorBox error={error || save.error || revoke.error} />

      {isLoading ? (
        <Spinner />
      ) : !rows.length ? (
        <div className="empty compact">No connection grants for this user.</div>
      ) : (
        <div className="table-wrap" style={{ marginBottom: 14 }}>
          <table className="data">
            <thead>
              <tr>
                <th>Connection</th>
                <th>Granted role</th>
                <th>Effective access</th>
                <th>Granted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => {
                const effective = isGlobalAdmin ? "admin" : effectiveRole(target.role, g.role);
                return (
                  <tr key={g.id}>
                    <td style={{ fontWeight: 600, color: "var(--text-dark)" }}>
                      {g.connection_name || `#${g.connection_id}`}
                    </td>
                    <td>
                      <select
                        value={g.role}
                        aria-label={`Granted role for ${g.connection_name || `connection #${g.connection_id}`}`}
                        disabled={busy}
                        onChange={(e) => changeRole(g, e.target.value as GrantRole)}
                        style={{ marginTop: 0, width: 110 }}
                      >
                        <option value="viewer">viewer</option>
                        <option value="editor">editor</option>
                      </select>
                    </td>
                    <td>
                      <span className="badge">{effective}</span>
                      {!isGlobalAdmin && effective !== g.role && (
                        <span style={{ fontSize: 11.5, color: "var(--text-light)", marginLeft: 6 }}>
                          capped by their global {target.role} role
                        </span>
                      )}
                    </td>
                    <td style={{ color: "var(--text-light)", whiteSpace: "nowrap" }}>
                      {fmtDateTime(g.created_at)}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="small danger"
                        disabled={busy}
                        onClick={() => removeGrant(g)}
                        aria-label={`Remove grant for ${g.connection_name || `connection #${g.connection_id}`}`}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="form-row">
        <label className="field" style={{ marginBottom: 0 }}>
          Grant access to connection
          <select
            value={connectionId}
            disabled={!addable.length || busy}
            onChange={(e) => setConnectionId(e.target.value)}
          >
            <option value="">Select a connection…</option>
            {addable.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <div className="field-hint">
            {connectionsLoading
              ? "Loading connections…"
              : !connections?.length
                ? "No connections registered yet."
                : !addable.length
                  ? "Every connection is already granted to this user."
                  : "Only connections without a grant are listed; change an existing one in the table above."}
          </div>
        </label>
        <label className="field" style={{ marginBottom: 0 }}>
          Grant role
          <select
            value={newRole}
            disabled={!addable.length || busy}
            onChange={(e) => setNewRole(e.target.value as GrantRole)}
          >
            <option value="viewer">viewer — read this connection</option>
            <option value="editor">editor — also manage its checks &amp; triage</option>
          </select>
          <div className="field-hint">Capped at their global {target.role} role.</div>
        </label>
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <button className="primary" onClick={addGrant} disabled={!connectionId || busy}>
          <Icon name="plus" size={13} /> Add grant
        </button>
      </div>
    </Modal>
  );
}

function NewUserModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const dirty = email !== "" || name !== "" || password !== "" || role !== "viewer";

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
      dirty={dirty}
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
  const confirm = useConfirm();
  const [, setTick] = useState(0);
  const [inviting, setInviting] = useState(false);
  const [grantsFor, setGrantsFor] = useState<User | null>(null);

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
        <div className="header-actions">
          <Link to="/status" className="btn">
            View status page →
          </Link>
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
          ) : health?.llm_disabled_reason ? (
            // A key IS configured but unusable (#266) — telling this operator to
            // "set a key" would send them to fix something that is already set.
            <div className="hint" style={{ color: "var(--warn-strong)" }}>
              {health.llm_disabled_reason}
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

      {isAdmin(user) && <NotificationsCard />}

      {isAdmin(user) ? (
        <>
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-pad" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", paddingBottom: 8 }}>
            <div>
              <h3>Users</h3>
              <p style={{ fontSize: 12.5, color: "var(--text-light)", margin: "4px 0 0" }}>
                The global role is the ceiling on what someone can do anywhere. Per-connection
                grants narrow that down to specific sources — use <strong>Manage access</strong> to
                see and change which connections a teammate can reach.
              </p>
            </div>
            <button className="primary small" onClick={() => setInviting(true)} style={{ flex: "none" }}>
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
                    <th>Connection access</th>
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
                          onChange={async (e) => {
                            const role = e.target.value as Role;
                            if (role === u.role) return;
                            const ok = await confirm({
                              title: "Change role",
                              confirmLabel: "Change role",
                              body: (
                                <>
                                  Change <strong>{u.email}</strong> from <strong>{u.role}</strong> to{" "}
                                  <strong>{role}</strong>? This takes effect immediately.
                                </>
                              ),
                            });
                            if (ok) update.mutate({ id: u.id, body: { role } });
                            else setTick((t) => t + 1); // snap the <select> back to the server value
                          }}
                          style={{ marginTop: 0, width: 110 }}
                        >
                          <option value="viewer">viewer</option>
                          <option value="editor">editor</option>
                          <option value="admin">admin</option>
                        </select>
                      </td>
                      <td>{u.is_active ? <StatusPill value="active" /> : <StatusPill value="disabled" />}</td>
                      <td style={{ color: "var(--text-light)" }}>{fmtDateTime(u.created_at)}</td>
                      <td>
                        <button
                          className="small"
                          onClick={() => setGrantsFor(u)}
                          aria-label={`Manage connection access for ${u.email}`}
                        >
                          <Icon name="shield" size={13} /> Manage access
                        </button>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {u.id !== user?.id && (
                          <button
                            className="small"
                            onClick={async () => {
                              if (
                                u.is_active &&
                                !(await confirm({
                                  title: "Deactivate user",
                                  danger: true,
                                  confirmLabel: "Deactivate",
                                  body: (
                                    <>
                                      Deactivate <strong>{u.email}</strong>? They will be unable to sign
                                      in.
                                    </>
                                  ),
                                }))
                              )
                                return;
                              update.mutate({ id: u.id, body: { is_active: !u.is_active } });
                            }}
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
      {grantsFor && <UserGrantsModal target={grantsFor} onClose={() => setGrantsFor(null)} />}
    </div>
  );
}
