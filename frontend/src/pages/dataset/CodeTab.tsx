// Dataset "Code" tab (issue #51): the table/view definition as stored in (or
// synthesized from) the source database, with a copy-to-clipboard affordance.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { DatasetDdl } from "../../api/types";
import { ErrorBox, Icon, Spinner } from "../../components/ui";

export default function CodeTab({ datasetId }: { datasetId: number }) {
  const [copied, setCopied] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["dataset-ddl", datasetId],
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

  if (isLoading) return <Spinner label="Reading definition…" />;
  if (error) {
    return (
      <div className="card card-pad">
        <ErrorBox error={error} />
      </div>
    );
  }
  if (!data) return null;

  return (
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
  );
}
