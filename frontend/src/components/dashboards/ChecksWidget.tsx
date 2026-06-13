import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../../api/client";
import type { Check, ChecksWidget as ChecksWidgetT } from "../../api/types";
import { timeAgo } from "../../lib/format";
import { EmptyState, ErrorBox, SeverityDot, Spinner } from "../ui";

const FAILING = new Set(["fail", "error"]);

/** Active checks for a chosen set of datasets. One shared query (GET
 *  /checks?status=active, cache key ["checks","active"]); we client-filter to the
 *  widget's dataset_ids and, when only_failing, to last_status in (fail, error).
 *  Rows link to the dataset's checks tab. Empty = "All passing". */
export default function ChecksWidget({ widget }: { widget: ChecksWidgetT }) {
  const { dataset_ids, only_failing } = widget.config;

  const { data, isLoading, error } = useQuery({
    queryKey: ["checks", "active"],
    queryFn: () => api.get<Check[]>("/checks?status=active"),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });

  if (error) return <ErrorBox error={error} />;
  if (isLoading && !data) return <Spinner label="Loading checks…" />;

  const wanted = new Set(dataset_ids);
  let rows = (data ?? []).filter((c) => wanted.size === 0 || wanted.has(c.dataset_id));
  if (only_failing) rows = rows.filter((c) => c.last_status && FAILING.has(c.last_status));

  if (dataset_ids.length === 0) {
    return <EmptyState title="No datasets selected" hint="Configure this widget to pick datasets." />;
  }
  if (rows.length === 0) {
    return <EmptyState title="All passing ✓" hint={only_failing ? "No failing checks." : undefined} />;
  }

  return (
    <ul className="cd-dense-list">
      {rows.slice(0, 50).map((c) => (
        <li key={c.id}>
          <Link to={`/datasets/${c.dataset_id}/checks`} className="cd-dense-row">
            <SeverityDot severity={c.severity} />
            <span className="cd-dense-reason" title={c.name}>
              {c.name}
            </span>
            <span className="cd-dense-meta">{c.dataset_name}</span>
            <span className={`cd-dense-time ${c.last_status && FAILING.has(c.last_status) ? "danger" : ""}`}>
              {c.last_status ?? "—"} · {timeAgo(c.last_run_at)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
