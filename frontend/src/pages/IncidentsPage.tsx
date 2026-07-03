import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api/client";
import type {
  Dataset,
  IncidentDetail,
  IncidentEvent,
  IncidentExternalRefs,
  IncidentRecord,
  IncidentSeverity,
  IncidentStatus,
} from "../api/types";
import { canEdit, useAuth } from "../auth";
import { useConfirm } from "../components/confirm";
import { Breadcrumbs, EmptyState, ErrorBox, Modal, SeverityBadge, Spinner, StatusPill } from "../components/ui";
import { fmtDateTime, fmtNum, timeAgo } from "../lib/format";

const PAGE_SIZE = 25;
const STATUSES: { value: IncidentStatus | ""; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "resolved", label: "Resolved" },
];
const SEVERITIES: { value: IncidentSeverity | ""; label: string }[] = [
  { value: "", label: "All severities" },
  { value: "error", label: "Error" },
  { value: "warn", label: "Warn" },
  { value: "info", label: "Info" },
];

function refsToEntries(refs: IncidentExternalRefs | null | undefined): { key: string; label: string; url: string | null }[] {
  return Object.entries(refs ?? {}).flatMap(([provider, raw]) => {
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      const obj = raw as Record<string, unknown>;
      const label = String(obj.key ?? obj.id ?? obj.dedup_key ?? obj.number ?? provider);
      const url = typeof obj.url === "string" ? obj.url : null;
      return [{ key: provider, label: `${provider}: ${label}`, url }];
    }
    if (raw == null || raw === "") return [];
    return [{ key: provider, label: `${provider}: ${String(raw)}`, url: null }];
  });
}

function exceptionHref(incident: IncidentRecord): string {
  const params = new URLSearchParams();
  params.set("dataset_id", String(incident.dataset_id));
  if (incident.current_run_id != null) params.set("run_id", String(incident.current_run_id));
  return `/exceptions?${params.toString()}`;
}

function ExternalRefs({ refs }: { refs: IncidentExternalRefs }) {
  const entries = refsToEntries(refs);
  if (!entries.length) return <span style={{ color: "var(--text-light)" }}>none</span>;
  return (
    <div className="incident-refs">
      {entries.map((ref) =>
        ref.url ? (
          <a key={ref.key} href={ref.url} target="_blank" rel="noreferrer" className="badge">
            {ref.label}
          </a>
        ) : (
          <span key={ref.key} className="badge">
            {ref.label}
          </span>
        ),
      )}
    </div>
  );
}

function IncidentLinks({ incident }: { incident: IncidentRecord }) {
  return (
    <div className="incident-links">
      <Link to={`/datasets/${incident.dataset_id}`}>Dataset</Link>
      <Link to={`/checks/${incident.check_id}`}>Check</Link>
      {incident.current_run_id != null && <Link to={`/runs/${incident.current_run_id}`}>Run</Link>}
      <Link to={exceptionHref(incident)}>Exceptions</Link>
    </div>
  );
}

function detailValue(value: unknown): string {
  if (value == null || value === "") return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function EventDetail({ event }: { event: IncidentEvent }) {
  const entries = Object.entries(event.detail ?? {}).filter(([, value]) => value != null && value !== "");
  if (!entries.length) return null;
  return (
    <div className="xw-event-comment">
      {entries.slice(0, 6).map(([key, value]) => (
        <div key={key}>
          <strong>{key}:</strong> {detailValue(value)}
        </div>
      ))}
    </div>
  );
}

function IncidentDetailModal({
  incident,
  detail,
  loading,
  error,
  editable,
  acting,
  onAction,
  onClose,
}: {
  incident: IncidentRecord;
  detail: IncidentDetail | undefined;
  loading: boolean;
  error: unknown;
  editable: boolean;
  acting: boolean;
  onAction: (incident: IncidentRecord, action: "ack" | "resolve") => void;
  onClose: () => void;
}) {
  const current = detail ?? incident;
  const events = detail?.events ?? [];
  return (
    <Modal
      title={`Incident #${incident.id}`}
      onClose={onClose}
      wide
      footer={
        <>
          <button onClick={onClose}>Close</button>
          {editable && current.status === "open" && (
            <button onClick={() => onAction(current, "ack")} disabled={acting}>
              Acknowledge
            </button>
          )}
          {editable && current.status !== "resolved" && (
            <button className="primary" onClick={() => onAction(current, "resolve")} disabled={acting}>
              Resolve
            </button>
          )}
        </>
      }
    >
      <div className="incident-detail-head">
        <div>
          <h3>{current.title}</h3>
          <p>
            {current.check_name || "Unknown check"} is currently {current.failure_status} on {current.dataset_name || "unknown dataset"}.
          </p>
        </div>
        <div className="incident-badges">
          <StatusPill value={current.status} />
          <SeverityBadge severity={current.severity} />
        </div>
      </div>

      <div className="grid cols-4 incident-stats">
        <div>
          <span>Occurrences</span>
          <strong>{fmtNum(current.occurrence_count)}</strong>
        </div>
        <div>
          <span>Failure</span>
          <strong>{current.failure_status}</strong>
        </div>
        <div>
          <span>First seen</span>
          <strong>{fmtDateTime(current.first_seen_at)}</strong>
        </div>
        <div>
          <span>Escalation</span>
          <strong>Level {current.escalation_level}</strong>
        </div>
      </div>

      <div className="incident-detail-grid">
        <section>
          <h4>Context</h4>
          <IncidentLinks incident={current} />
          <div className="incident-context-lines">
            <div>Dataset: {current.dataset_name || "unknown"}</div>
            <div>Check: {current.check_name || "unknown"}</div>
            <div>Last seen: {fmtDateTime(current.last_seen_at)} ({timeAgo(current.last_seen_at)})</div>
            <div>Next escalation: {fmtDateTime(current.next_escalation_at)}</div>
          </div>
        </section>
        <section>
          <h4>External refs</h4>
          <ExternalRefs refs={current.external_refs ?? {}} />
        </section>
      </div>

      <section className="incident-section">
        <h4>Timeline</h4>
        <ErrorBox error={error} />
        {loading ? (
          <Spinner />
        ) : !events.length ? (
          <div className="empty compact">No timeline events returned for this incident.</div>
        ) : (
          <div className="timeline">
            {events.map((event) => (
              <div
                key={event.id}
                className={`timeline-item${event.kind === "escalated" || event.kind === "notified" ? " warn" : ""}`}
              >
                <span className="timeline-dot" />
                <div className="title">{event.kind}</div>
                <div className="meta">
                  {fmtDateTime(event.created_at)}
                  {event.user ? ` by ${event.user}` : ""}
                </div>
                <EventDetail event={event} />
              </div>
            ))}
          </div>
        )}
      </section>
    </Modal>
  );
}

export default function IncidentsPage() {
  const { user } = useAuth();
  const editable = canEdit(user);
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [sp, setSp] = useSearchParams();
  const [qDraft, setQDraft] = useState(sp.get("q") ?? "");
  const [selected, setSelected] = useState<IncidentRecord | null>(null);

  const status = (sp.get("status") ?? "") as IncidentStatus | "";
  const severity = (sp.get("severity") ?? "") as IncidentSeverity | "";
  const datasetId = sp.get("dataset_id") ?? "";
  const q = sp.get("q") ?? "";
  const offset = Number(sp.get("offset") ?? 0);

  const updateParams = (patch: Record<string, string>, resetOffset = true) => {
    const next = new URLSearchParams(sp);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (resetOffset) next.delete("offset");
    setSp(next);
  };

  const listParams = new URLSearchParams();
  listParams.set("limit", String(PAGE_SIZE));
  listParams.set("offset", String(offset));
  if (status) listParams.set("status", status);
  if (severity) listParams.set("severity", severity);
  if (datasetId) listParams.set("dataset_id", datasetId);

  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });

  const incidents = useQuery({
    queryKey: ["incidents", listParams.toString()],
    queryFn: () => api.get<IncidentRecord[]>(`/incidents?${listParams.toString()}`),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });

  const detail = useQuery({
    queryKey: ["incident-detail", selected?.id],
    queryFn: () => api.get<IncidentDetail>(`/incidents/${selected!.id}`),
    enabled: selected != null,
  });

  const action = useMutation({
    mutationFn: ({ incident, kind }: { incident: IncidentRecord; kind: "ack" | "resolve" }) =>
      api.post<IncidentDetail>(`/incidents/${incident.id}/${kind}`, {}),
    onSuccess: (updated) => {
      setSelected((cur) => (cur?.id === updated.id ? updated : cur));
      qc.invalidateQueries({ queryKey: ["incidents"] });
      qc.invalidateQueries({ queryKey: ["incident-detail", updated.id] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  // Resolving is a one-way door that stops escalation/paging, so confirm it (#D6).
  // (It reopens automatically only if the check fails again.)
  const doAction = async (incident: IncidentRecord, kind: "ack" | "resolve") => {
    if (kind === "resolve") {
      const ok = await confirm({
        title: "Resolve incident?",
        body: "Resolving stops escalations and paging for this incident. It reopens automatically only if the check fails again.",
        confirmLabel: "Resolve",
      });
      if (!ok) return;
    }
    action.mutate({ incident, kind });
  };

  const serverRows = incidents.data ?? [];
  const rows = q
    ? serverRows.filter((incident) => {
        const haystack = [
          incident.title,
          incident.dataset_name,
          incident.check_name,
          incident.dedupe_key,
          JSON.stringify(incident.external_refs ?? {}),
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(q.toLowerCase());
      })
    : serverRows;
  const from = rows.length === 0 ? 0 : offset + 1;
  const to = offset + rows.length;
  const canPageNext = serverRows.length === PAGE_SIZE;

  return (
    <div className="page wide">
      <Breadcrumbs items={[{ label: "Incidents" }]} />
      <div className="page-header">
        <div>
          <h1>Incidents</h1>
          <div className="sub">Failure lifecycle, escalation, and external incident destinations</div>
        </div>
      </div>

      <div className="incident-filterbar">
        <select value={status} onChange={(e) => updateParams({ status: e.target.value })}>
          {STATUSES.map((s) => (
            <option key={s.value || "all"} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <select value={severity} onChange={(e) => updateParams({ severity: e.target.value })}>
          {SEVERITIES.map((s) => (
            <option key={s.value || "all"} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <select value={datasetId} onChange={(e) => updateParams({ dataset_id: e.target.value })}>
          <option value="">All datasets</option>
          {datasets?.map((d) => (
            <option key={d.id} value={d.id}>
              {d.display_name || d.table_name}
            </option>
          ))}
        </select>
        <form
          className="incident-search"
          onSubmit={(e) => {
            e.preventDefault();
            updateParams({ q: qDraft.trim() });
          }}
        >
          <input
            type="text"
            aria-label="Filter incidents"
            value={qDraft}
            onChange={(e) => setQDraft(e.target.value)}
            placeholder="Filter title, check, external ref"
          />
          <button type="submit">Filter</button>
          {q && (
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setQDraft("");
                updateParams({ q: "" });
              }}
            >
              Clear
            </button>
          )}
        </form>
      </div>

      <ErrorBox error={incidents.error || action.error} />

      {incidents.isLoading ? (
        <Spinner label="Loading incidents..." />
      ) : !rows.length ? (
        <div className="card">
          <EmptyState title="No incidents match these filters" hint="New incidents will appear here when checks fail and the incident lifecycle groups them." />
        </div>
      ) : (
        <div className="card incident-table-card">
          <div className="table-wrap">
            <table className="data incident-table">
              <thead>
                <tr>
                  <th>Incident</th>
                  <th>Status</th>
                  <th>Severity</th>
                  <th>Occurrences</th>
                  <th>Seen</th>
                  <th>Escalation</th>
                  <th>External refs</th>
                  <th>Links</th>
                  {editable && <th style={{ textAlign: "right" }}>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((incident) => (
                  <tr key={incident.id} className="clickable" onClick={() => setSelected(incident)}>
                    <td>
                      <button
                        type="button"
                        className="incident-title-btn incident-title"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelected(incident);
                        }}
                      >
                        {incident.title}
                      </button>
                      <div className="incident-sub">
                        {incident.dataset_name || "unknown dataset"} - {incident.check_name || "unknown check"}
                      </div>
                    </td>
                    <td>
                      <StatusPill value={incident.status} />
                    </td>
                    <td>
                      <SeverityBadge severity={incident.severity} />
                    </td>
                    <td className="num">
                      <strong>{fmtNum(incident.occurrence_count)}</strong>
                      <div className="incident-sub">{incident.failure_status}</div>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <div>{timeAgo(incident.last_seen_at)}</div>
                      <div className="incident-sub">first {fmtDateTime(incident.first_seen_at)}</div>
                    </td>
                    <td>
                      <span className="badge">level {incident.escalation_level}</span>
                      <div className="incident-sub">
                        {incident.next_escalation_at ? `next ${fmtDateTime(incident.next_escalation_at)}` : "not scheduled"}
                      </div>
                    </td>
                    <td>
                      <ExternalRefs refs={incident.external_refs ?? {}} />
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <IncidentLinks incident={incident} />
                    </td>
                    {editable && (
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }} onClick={(e) => e.stopPropagation()}>
                        {incident.status === "open" && (
                          <button
                            className="small"
                            onClick={() => doAction(incident, "ack")}
                            disabled={action.isPending}
                          >
                            Ack
                          </button>
                        )}{" "}
                        {incident.status !== "resolved" && (
                          <button
                            className="small primary"
                            onClick={() => doAction(incident, "resolve")}
                            disabled={action.isPending}
                          >
                            Resolve
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="xw-pager">
            <span className="xw-pager-info">
              {rows.length === 0 ? "No incidents" : `${from}-${to}`}
              {incidents.isFetching ? " ..." : ""}
            </span>
            <span className="xw-pager-btns">
              <button
                className="small"
                disabled={offset === 0 || incidents.isFetching}
                onClick={() => updateParams({ offset: String(Math.max(0, offset - PAGE_SIZE)) }, false)}
              >
                Prev
              </button>
              <button
                className="small"
                disabled={!canPageNext || incidents.isFetching}
                onClick={() => updateParams({ offset: String(offset + PAGE_SIZE) }, false)}
              >
                Next
              </button>
            </span>
          </div>
        </div>
      )}

      {selected && (
        <IncidentDetailModal
          incident={selected}
          detail={detail.data}
          loading={detail.isLoading}
          error={detail.error}
          editable={editable}
          acting={action.isPending}
          onAction={(incident, kind) => doAction(incident, kind)}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
