import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../../api/client";
import type { ExceptionPage, ExceptionsWidget as ExceptionsWidgetT } from "../../api/types";
import { timeAgo } from "../../lib/format";
import { EmptyState, ErrorBox, Pill, Spinner } from "../ui";
import { paramsToQuery } from "./MetricWidget";

/** A compact list of the most recent matching exceptions (#57 envelope `items`),
 *  capped at the widget's limit (1..10). Each row links into the workspace with
 *  the same params + the selected id; a footer links to the full filtered view.
 *  The dashboard is a glanceable surface — triage happens in the workspace. */
export default function ExceptionsWidget({ widget }: { widget: ExceptionsWidgetT }) {
  const { params, limit } = widget.config;
  const qs = paramsToQuery(params);

  const { data, isLoading, error } = useQuery({
    queryKey: ["widget-exceptions", qs, limit],
    queryFn: () => api.get<ExceptionPage>(`/exceptions?${qs}${qs ? "&" : ""}limit=${limit}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });

  if (error) return <ErrorBox error={error} />;
  if (isLoading && !data) return <Spinner label="Loading exceptions…" />;

  const items = data?.items ?? [];
  if (items.length === 0) {
    return <EmptyState title="Nothing here" hint="No exceptions match these filters." />;
  }

  return (
    <div className="cd-dense">
      <ul className="cd-dense-list">
        {items.map((e) => (
          <li key={e.id}>
            <Link to={`/exceptions?${qs}${qs ? "&" : ""}sel=${e.id}`} className="cd-dense-row">
              <Pill value={e.status} />
              <span className="cd-dense-reason" title={e.reason}>
                {e.reason || e.check_name}
              </span>
              <span className="cd-dense-meta">{e.dataset_name}</span>
              <span className="cd-dense-time">{timeAgo(e.created_at)}</span>
            </Link>
          </li>
        ))}
      </ul>
      <Link to={`/exceptions?${qs}`} className="cd-dense-all">
        View all{data && data.total > items.length ? ` (${data.total})` : ""} →
      </Link>
    </div>
  );
}
