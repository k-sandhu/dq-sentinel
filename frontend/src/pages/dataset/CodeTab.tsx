// Dataset "Code" tab (issue #51): the table/view definition as stored in (or
// synthesized from) the source database, with a copy-to-clipboard affordance.
// Plus (issue #41) a "Pinned queries" card surfacing saved queries pinned to this
// dataset, with deep-links into the workbench.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { DatasetDdl, SavedQuery } from "../../api/types";
import { EmptyState, ErrorBox, Icon, Spinner } from "../../components/ui";

function PinnedQueries({ datasetId }: { datasetId: number }) {
  const { data, isLoading, error } = useQuery({
    queryKey: qk.savedQueries.byDataset(datasetId),
    queryFn: () => api.get<SavedQuery[]>(`/queries?dataset_id=${datasetId}`),
    staleTime: 30_000,
  });

  return (
    <div className="card card-pad">
      <h3 style={{ marginBottom: 4 }}>Pinned queries</h3>
      <div style={{ fontSize: 11.5, color: "var(--text-light)", marginBottom: 10 }}>
        Saved workbench queries pinned to this dataset — the team's starting points for investigations.
      </div>
      <ErrorBox error={error} />
      {isLoading ? (
        <Spinner label="Loading…" />
      ) : !data?.length ? (
        <EmptyState
          title="No pinned queries yet"
          hint="Save a query in the Workbench and pin it to this dataset to surface it here."
        />
      ) : (
        data.map((q) => (
          <div key={q.id} className="insight" style={{ borderColor: "var(--purple)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
              <div className="t">{q.name}</div>
              <Link
                className="small"
                to={`/workbench?dataset_id=${datasetId}&saved_query_id=${q.id}`}
                style={{ whiteSpace: "nowrap" }}
              >
                Open in workbench →
              </Link>
            </div>
            {q.description && (
              <div style={{ fontSize: 11.5, color: "var(--text-light)", margin: "2px 0 6px" }}>{q.description}</div>
            )}
            {q.tags.length > 0 && (
              <div className="chip-row" style={{ marginBottom: 6 }}>
                {q.tags.map((t) => (
                  <span key={t} className="badge">{t}</span>
                ))}
              </div>
            )}
            <pre className="result" style={{ maxHeight: 110, fontSize: 11 }}>{q.sql}</pre>
          </div>
        ))
      )}
    </div>
  );
}

export default function CodeTab({ datasetId }: { datasetId: number }) {
  const [copied, setCopied] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: qk.datasetDdl.detail(datasetId),
    queryFn: () => api.get<DatasetDdl>(`/datasets/${datasetId}/ddl`),
    retry: false,
    staleTime: 60_000,
  });

  function copy() {
    if (!data) return;
    navigator.clipboard
      .writeText(data.ddl)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      })
      .catch(() => {
        /* clipboard unavailable (e.g. non-secure context) — quietly do nothing */
      });
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {isLoading ? (
        <Spinner label="Reading definition…" />
      ) : error ? (
        <div className="card card-pad">
          <ErrorBox error={error} />
        </div>
      ) : data ? (
        <div className="card card-pad">
          <div className="toolbar">
            <span className="badge">
              {data.source === "database" ? "from database catalog" : "synthesized from introspection"}
            </span>
            <span className="badge kind">{data.kind}</span>
            <div className="right">
              <button className="small" onClick={copy} title="Copy definition to clipboard">
                <Icon name={copied ? "check" : "copy"} size={12} /> {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
          <pre className="code-block">{data.ddl}</pre>
        </div>
      ) : null}

      <PinnedQueries datasetId={datasetId} />
    </div>
  );
}
