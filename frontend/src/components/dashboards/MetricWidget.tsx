import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../../api/client";
import type { ExceptionPage, MetricWidget as MetricWidgetT } from "../../api/types";
import { fmtNum } from "../../lib/format";
import { ErrorBox } from "../ui";

/** Build a stable query string from the stored params. The SAME string drives
 *  both the count query and the click-through Link — that equality is the trust
 *  contract: the number must match what the triage queue shows for these filters. */
export function paramsToQuery(params: Record<string, string>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== "" && v !== undefined && v !== null) sp.append(k, v);
  }
  return sp.toString();
}

/** A single stat-card number resolved live through GET /exceptions (#57 envelope
 *  `total`). Tone goes danger at >= danger_at, warn at >= warn_at (null = never);
 *  the tone is paired with the label + number, never color-alone. The whole card
 *  links to the same /exceptions view. */
export default function MetricWidget({ widget }: { widget: MetricWidgetT }) {
  const { params, warn_at, danger_at } = widget.config;
  const qs = paramsToQuery(params);

  const { data, isLoading, error } = useQuery({
    queryKey: ["widget-metric", qs],
    queryFn: () => api.get<ExceptionPage>(`/exceptions?${qs}${qs ? "&" : ""}limit=1`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev, // keep last value while refetching — no flicker
  });

  if (error) return <ErrorBox error={error} />;

  const total = data?.total;
  let tone = "";
  let toneLabel = "";
  if (total !== undefined) {
    if (danger_at !== null && total >= danger_at) {
      tone = "danger";
      toneLabel = "over threshold";
    } else if (warn_at !== null && total >= warn_at) {
      tone = "warn";
      toneLabel = "elevated";
    } else {
      tone = "ok";
      toneLabel = "within range";
    }
  }

  return (
    <Link to={`/exceptions?${qs}`} className="cd-metric" aria-label={`${widget.title}: ${total ?? "loading"}. View in workspace`}>
      <div className={`cd-metric-value ${tone}`}>{isLoading && total === undefined ? "…" : fmtNum(total ?? null)}</div>
      {toneLabel && (
        <div className={`cd-metric-tone ${tone}`}>
          <span className={`cd-dot ${tone}`} aria-hidden /> {toneLabel}
        </div>
      )}
      <div className="cd-metric-link">View in workspace →</div>
    </Link>
  );
}
