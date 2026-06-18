import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api/client";
import type { Check, DashboardConsole, DatasetMover } from "../api/types";
import { EmptyState, ErrorBox, Icon, SeverityDot, Spinner, StatCard, StatusPill } from "../components/ui";
import { fmtNum, timeAgo } from "../lib/format";

/** Active checks failing/erroring right now — error first. Each links to its
 *  detail; "view exceptions" deep-links into the (check-scoped) triage queue. */
function FailingNowList({ checks }: { checks: Check[] }) {
  if (checks.length === 0) {
    return <EmptyState title="Nothing failing right now" hint="Active checks that fail or error will show up here." />;
  }
  return (
    <div className="dense-list">
      {checks.map((c) => (
        <div key={c.id} className="dense-item mywork-fail">
          <SeverityDot severity={c.severity} />
          <div className="mywork-fail-main">
            <Link to={`/checks/${c.id}`} className="mywork-strong" title={c.name}>
              {c.name}
            </Link>
            <div className="sub">
              {c.dataset_name} · {timeAgo(c.last_run_at)}
            </div>
          </div>
          <div className="mywork-fail-side">
            <StatusPill value={c.last_status} />
            <Link to={`/exceptions?check_id=${c.id}&status=open`} className="mywork-viewlink">
              View exceptions →
            </Link>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Datasets with the most new exceptions in the last 24h. */
function MoversList({ movers }: { movers: DatasetMover[] }) {
  if (movers.length === 0) {
    return <EmptyState title="No new exceptions in the last 24h" hint="Datasets with fresh exceptions will rank here." />;
  }
  return (
    <div className="dense-list">
      {movers.map((m) => (
        <Link
          key={m.dataset_id}
          to={`/datasets/${m.dataset_id}/exceptions`}
          className="dense-item clickable mywork-mover"
        >
          <div className="mywork-strong">{m.dataset_name}</div>
          <div className="mywork-mover-stats">
            <span className="mywork-stat up">+{fmtNum(m.opened_24h)} new</span>
            <span className="mywork-stat down">−{fmtNum(m.resolved_24h)} resolved</span>
            <span className="mywork-stat">{fmtNum(m.open_total)} open</span>
          </div>
        </Link>
      ))}
    </div>
  );
}

/** The analyst's 9am work queue (#64): what's mine, what's new, what regressed,
 *  what's failing now, which datasets moved most — a work queue, not a status
 *  brochure. All "24h" figures are rolling windows labeled "last 24h" so non-UTC
 *  analysts can reconcile them honestly. Every card/row deep-links into a
 *  correctly-filtered triage view. Read-only — viewers can be assignees too. */
export default function MyWorkPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard-console"],
    queryFn: () => api.get<DashboardConsole>("/dashboard/console"),
    refetchInterval: 30_000,
  });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>My work</h1>
          <div className="sub">
            Your queue for right now — what&rsquo;s yours, what&rsquo;s new, what regressed.
            {data && (
              <>
                {" "}
                Last 24h: <strong>{fmtNum(data.new_exceptions_24h)}</strong> new ·{" "}
                <strong>{fmtNum(data.resolved_24h)}</strong> resolved.
              </>
            )}
          </div>
        </div>
        <div className="header-actions">
          <Link to="/exceptions" className="btn">
            <Icon name="alert" size={14} />
            All exceptions
          </Link>
        </div>
      </div>

      {error ? (
        <ErrorBox error={error} />
      ) : isLoading && !data ? (
        <Spinner label="Loading your work queue…" />
      ) : data ? (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <StatCard
              label="Assigned to me"
              value={fmtNum(data.assigned_to_me_open)}
              hint="open"
              to="/exceptions?assignee=me&status=open"
              ariaLabel={`${data.assigned_to_me_open} open exceptions assigned to me`}
            />
            <StatCard
              label="New · last 24h"
              value={fmtNum(data.new_exceptions_24h)}
              hint="first seen in 24h"
              to="/exceptions?recurrence=new&status=open"
            />
            <StatCard
              label="Regressed"
              value={fmtNum(data.regressed_open)}
              hint="recurred after triage"
              to="/exceptions?recurrence=recurring&status=open"
            />
            <StatCard
              label="Open total"
              value={fmtNum(data.open_total)}
              tone={data.open_total ? "danger" : "ok"}
              hint="all open"
              to="/exceptions?status=open"
            />
          </div>

          <div className="split">
            <div className="card card-pad">
              <div className="section-title" style={{ margin: "0 0 12px" }}>
                <h2>Failing now</h2>
                <Link to="/checks?status=active&last_status=fail&last_status=error" className="btn small">
                  All failing
                </Link>
              </div>
              <FailingNowList checks={data.failing_now} />
            </div>
            <div className="card card-pad">
              <div className="section-title" style={{ margin: "0 0 12px" }}>
                <h2>Biggest movers</h2>
                <span className="badge" title="Datasets with the most new exceptions in the last 24h">
                  last 24h
                </span>
              </div>
              <MoversList movers={data.movers} />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
