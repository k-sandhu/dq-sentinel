import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../../api/client";
import type { ExceptionSeriesOut, TrendWidget as TrendWidgetT } from "../../api/types";
import { fmtNum } from "../../lib/format";
import { ErrorBox, Spinner } from "../ui";
import { paramsToQuery } from "./MetricWidget";

const TOOLTIP_STYLE = {
  fontSize: 12,
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--card)",
  color: "var(--text-dark)",
};
const AXIS = { fontSize: 10, fill: "var(--text-light)" };

/** New exceptions per UTC day for a stored exceptions filter (the trend #67
 *  deferred). Renders the GET /insights/exception-series result; the footer total
 *  links to the same /exceptions view, mirroring the metric widget's trust
 *  contract. */
export default function TrendWidget({ widget }: { widget: TrendWidgetT }) {
  const { params, days } = widget.config;
  const base = paramsToQuery(params);
  const qs = `${base}${base ? "&" : ""}days=${days}`;

  const { data, isLoading, error } = useQuery({
    queryKey: ["widget-trend", qs],
    queryFn: () => api.get<ExceptionSeriesOut>(`/insights/exception-series?${qs}`),
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });

  if (error) return <ErrorBox error={error} />;
  if (isLoading && !data) return <Spinner label="Loading trend…" />;
  if (!data) return null;

  const series = data.points.map((p) => ({ ...p, day: p.t.slice(5) }));

  return (
    <div>
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={series} margin={{ top: 6, right: 6, left: -20, bottom: 0 }}>
          <CartesianGrid stroke="var(--border-light)" vertical={false} />
          <XAxis
            dataKey="day"
            tick={AXIS}
            tickLine={false}
            axisLine={{ stroke: "var(--border)" }}
            minTickGap={22}
          />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} allowDecimals={false} width={26} />
          <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(l) => `${l} (UTC)`} />
          <Area
            type="monotone"
            dataKey="value"
            name="new exceptions"
            stroke="var(--brand)"
            fill="var(--brand-light)"
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
      <div className="cd-trend-foot">
        {data.total === 0 ? (
          <span className="cd-asof">none in this window</span>
        ) : (
          <Link to={`/exceptions?${base}`} className="cd-dense-all">
            {fmtNum(data.total)} new in {days}d →
          </Link>
        )}
      </div>
    </div>
  );
}
