import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { qk } from "../api/queryKeys";
import type { DataHealth, DataStatus } from "../api/types";
import { EmptyState, ErrorBox, Spinner } from "../components/ui";
import { timeAgo } from "../lib/format";

// Read-only stakeholder status page (D9 / #179). Zero mutation controls: it only
// reflects health + the redacted incident-update timeline from GET /status. Health
// color always pairs with its text label (no colour-only meaning); times are
// honest UTC-relative via timeAgo.

const HEALTH_TONE: Record<DataHealth, string> = {
  operational: "tone-ok",
  delayed: "tone-warn",
  degraded: "tone-danger",
  unknown: "tone-neutral",
};

// Map a safe incident-update kind to a timeline dot tone.
const UPDATE_TONE: Record<string, string> = {
  resolved: "ok",
  recovered: "ok",
  acknowledged: "warn",
  opened: "danger",
  occurred: "danger",
};

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export default function StatusPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: qk.status.get(),
    queryFn: () => api.get<DataStatus>("/status"),
    refetchInterval: 60_000, // the stakeholder view auto-updates
  });

  if (isLoading) return <Spinner label="Loading status…" />;

  const overall: DataHealth = data?.overall ?? "unknown";
  const openIncidents = data ? data.datasets.reduce((n, d) => n + d.open_incidents, 0) : 0;
  // Honest headline: never imply "operational" when the fetch failed or nothing is monitored.
  const headline = error
    ? "status unavailable"
    : openIncidents > 0
      ? plural(openIncidents, "open incident")
      : data && data.datasets.length > 0
        ? "all systems operational"
        : "no monitored datasets";

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Data status</h1>
          <div className="sub">Read-only view for stakeholders · auto-updated</div>
        </div>
        <span className={`pill ${error ? "tone-neutral" : HEALTH_TONE[overall]}`}>{headline}</span>
      </div>

      <ErrorBox error={error} />

      {/* Split states so the page never claims "operational" when the status is
          unavailable or empty: error -> ErrorBox only; datasets -> tiles (each
          carries its own health); zero datasets -> an honest empty card. */}
      {error ? null : data && data.datasets.length > 0 ? (
        <div className="grid cols-3" style={{ marginBottom: 16 }}>
          {data.datasets.map((d) => (
            <div key={d.id} className="card card-pad status-tile">
              <div>
                <div className="status-tile-name">{d.name}</div>
                <div className="status-tile-cap">
                  {d.open_incidents > 0
                    ? plural(d.open_incidents, "open incident")
                    : d.last_incident_at
                      ? `last incident ${timeAgo(d.last_incident_at)}`
                      : "no recent incidents"}
                </div>
              </div>
              <span className={`pill ${HEALTH_TONE[d.health]}`}>{d.health}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="card">
          <EmptyState
            title="No monitored datasets"
            hint="Datasets appear here once they have active checks reporting health."
          />
        </div>
      )}

      <div className="card">
        <div className="card-pad">
          <h3 style={{ marginBottom: 4 }}>Incident updates</h3>
          {data && data.updates.length > 0 ? (
            <div className="status-timeline">
              {data.updates.map((u, i) => (
                <div key={i} className={`status-update ${UPDATE_TONE[u.kind] ?? ""}`}>
                  <span className="status-dot" aria-hidden="true" />
                  <div>
                    <div className="status-when">
                      {timeAgo(u.at)} · {u.kind}
                    </div>
                    <div className="status-what">
                      {u.dataset_name} — {u.title}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No recent incident updates"
              hint="Updates appear here as incidents open and resolve."
            />
          )}
        </div>
      </div>
    </div>
  );
}
