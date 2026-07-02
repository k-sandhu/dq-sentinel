import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api/client";
import { qk } from "../api/queryKeys";
import type { CatalogEntry } from "../api/types";
import { canEdit, isAdmin, useAuth } from "../auth";
import { useConfirm } from "../components/confirm";
import { EmptyState, ErrorBox, Icon, Spinner } from "../components/ui";

const IMPORTANCE_TONE: Record<string, string> = {
  critical: "danger",
  high: "warn",
  medium: "neutral",
  low: "neutral",
};

function CatalogCard({
  entry,
  onConnect,
  onDisconnect,
  connecting,
  disconnecting,
}: {
  entry: CatalogEntry;
  onConnect: (key: string) => void;
  onDisconnect: (entry: CatalogEntry) => void;
  connecting: boolean;
  disconnecting: boolean;
}) {
  const { user } = useAuth();
  const tone = IMPORTANCE_TONE[entry.importance] ?? "neutral";
  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <span className="badge">{entry.domain}</span>
        <span className="badge kind">{entry.engine}</span>
        <span className={`pill tone-${tone}`}>{entry.importance}</span>
        {entry.pii && (
          <span className="pill tone-warn" title="Contains PII columns (redacted from LLM prompts)">
            PII
          </span>
        )}
        {entry.connected && <span className="pill tone-ok" style={{ marginLeft: "auto" }}>Connected</span>}
      </div>

      <div>
        <div style={{ fontWeight: 700, fontSize: 15, color: "var(--text-dark)" }}>{entry.title}</div>
        <div className="field-hint" style={{ marginTop: 1 }}>{entry.source_system}</div>
      </div>

      <p style={{ margin: 0, color: "var(--text)", fontSize: 13, lineHeight: 1.45, flex: 1 }}>
        {entry.description}
      </p>

      <div className="field-hint" style={{ display: "flex", flexWrap: "wrap", gap: "2px 10px" }}>
        <span>Owner: <strong style={{ color: "var(--text)" }}>{entry.owner || "—"}</strong></span>
        <span>· {entry.table_count} dataset{entry.table_count === 1 ? "" : "s"}</span>
        <span>· {entry.check_count} check{entry.check_count === 1 ? "" : "s"}</span>
        {entry.has_contract && <span>· contract</span>}
      </div>

      {entry.tags.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
          {entry.tags.map((t) => (
            <span key={t} className="badge" style={{ fontWeight: 500 }}>#{t}</span>
          ))}
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2 }}>
        {entry.connected ? (
          <>
            {entry.connection_id != null && (
              <Link to={`/connections/${entry.connection_id}`} className="btn small">
                <Icon name="search" size={12} /> Open source
              </Link>
            )}
            {isAdmin(user) && (
              <button
                className="small danger"
                onClick={() => onDisconnect(entry)}
                disabled={disconnecting}
              >
                {disconnecting ? "Disconnecting…" : "Disconnect"}
              </button>
            )}
          </>
        ) : canEdit(user) ? (
          <button className="primary" onClick={() => onConnect(entry.key)} disabled={connecting}>
            {connecting ? (
              <span className="spinner" style={{ width: 13, height: 13 }} />
            ) : (
              <Icon name="plus" size={14} />
            )}
            {connecting ? "Connecting…" : "Connect"}
          </button>
        ) : (
          <span className="field-hint">Editor role required to connect</span>
        )}
      </div>
    </div>
  );
}

export default function CatalogPage() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading, error } = useQuery({
    queryKey: qk.catalog.list(),
    queryFn: () => api.get<CatalogEntry[]>("/catalog"),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: qk.catalog.all });
    qc.invalidateQueries({ queryKey: qk.connections.all });
    qc.invalidateQueries({ queryKey: qk.datasets.all });
  };

  const connect = useMutation({
    mutationFn: (key: string) => api.post<CatalogEntry>(`/catalog/${key}/connect`),
    onSuccess: invalidate,
  });
  const disconnect = useMutation({
    mutationFn: (key: string) => api.del(`/catalog/${key}/disconnect`),
    onSuccess: invalidate,
  });

  const onDisconnect = async (entry: CatalogEntry) => {
    if (
      await confirm({
        title: "Disconnect dataset",
        danger: true,
        confirmLabel: "Disconnect",
        body: (
          <>
            This removes the <strong>{entry.source_system}</strong> connection and its{" "}
            {entry.table_count} dataset(s), along with all checks, contracts, and SLAs created when
            you connected it. The generated sample data file is left in place, so you can reconnect
            later.
          </>
        ),
      })
    )
      disconnect.mutate(entry.key);
  };

  const connectedCount = (data ?? []).filter((e) => e.connected).length;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Data catalog</h1>
          <div className="sub">
            Curated, fully-governed sample datasets across business domains. Connect any with one
            click to explore what a mature enterprise dataset looks like — real data, a profile,
            ownership, a data contract, checks, and SLAs.
            {data && data.length > 0 && (
              <span style={{ marginLeft: 8 }}>
                · <strong>{connectedCount}/{data.length}</strong> connected
              </span>
            )}
          </div>
        </div>
      </div>

      <ErrorBox error={error || connect.error || disconnect.error} />

      {isLoading ? (
        <Spinner />
      ) : !data?.length ? (
        <div className="card">
          <EmptyState title="No catalog datasets" hint="The built-in catalog is empty." />
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: 14,
          }}
        >
          {data.map((entry) => (
            <CatalogCard
              key={entry.key}
              entry={entry}
              onConnect={(k) => connect.mutate(k)}
              onDisconnect={onDisconnect}
              connecting={connect.isPending && connect.variables === entry.key}
              disconnecting={disconnect.isPending && disconnect.variables === entry.key}
            />
          ))}
        </div>
      )}
    </div>
  );
}
