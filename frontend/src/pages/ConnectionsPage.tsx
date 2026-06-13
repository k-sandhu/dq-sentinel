import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type { Connection, ConnectionHealth, ConnectionTest, EngineInfo } from "../api/types";
import { isAdmin, useAuth } from "../auth";
import { ConfirmModal, EmptyState, ErrorBox, Icon, Modal, Spinner } from "../components/ui";
import { fmtDateTime } from "../lib/format";

const GENERIC_DSN_PLACEHOLDER = "dialect+driver://user:pass@host:port/dbname";

function AddConnectionModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [dsn, setDsn] = useState("");
  const [kind, setKind] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ConnectionTest | null>(null);

  const engines = useQuery({
    queryKey: ["engines"],
    queryFn: () => api.get<EngineInfo[]>("/connections/engines"),
    staleTime: Infinity,
  });
  const selected = (engines.data ?? []).find((e) => e.kind === kind);

  const test = useMutation({
    mutationFn: () => api.post<ConnectionTest>("/connections/test", { name: name || "test", dsn }),
    onSuccess: setTestResult,
  });
  const create = useMutation({
    mutationFn: () => api.post<Connection>("/connections", { name, dsn }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connections"] });
      onClose();
    },
  });

  const pickEngine = (engine: EngineInfo) => {
    if (engine.kind === kind) {
      setKind(null); // toggle back to freeform
      return;
    }
    setKind(engine.kind);
    // Prefill only when the field is empty or still holds an untouched example,
    // so users edit a template instead of typing a DSN from scratch.
    const examples = (engines.data ?? []).map((e) => e.dsn_example);
    if (!dsn.trim() || examples.includes(dsn)) setDsn(engine.dsn_example);
  };

  return (
    <Modal
      title="Add a connection"
      onClose={onClose}
      footer={
        <>
          <button onClick={() => test.mutate()} disabled={!dsn || test.isPending}>
            {test.isPending ? "Testing…" : "Test connection"}
          </button>
          <button className="primary" onClick={() => create.mutate()} disabled={!name || !dsn || create.isPending}>
            Save
          </button>
        </>
      }
    >
      <ErrorBox error={create.error || test.error} />
      {testResult && (
        <div className={testResult.ok ? "info-box" : "error-box"}>{testResult.message}</div>
      )}
      <label className="field">
        Display name <span className="req">*</span>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Shop database" />
      </label>
      {(engines.isLoading || (engines.data?.length ?? 0) > 0) && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--text-dark)" }}>
            Engine{" "}
            <span className="field-hint" style={{ display: "inline", marginLeft: 2 }}>
              optional — picking one fills in a DSN template
            </span>
          </div>
          {engines.isLoading ? (
            <div style={{ marginTop: 8 }}>
              <Spinner label="Loading engines…" />
            </div>
          ) : (
            <>
              <div className="grid cols-3" style={{ gap: 6, marginTop: 7 }}>
                {(engines.data ?? []).map((e) => (
                  <button
                    key={e.kind}
                    type="button"
                    className="small"
                    onClick={() => pickEngine(e)}
                    title={
                      e.driver_installed
                        ? `${e.label} — driver installed`
                        : `${e.label} — driver not installed on the API server`
                    }
                    style={{
                      justifyContent: "flex-start",
                      fontWeight: 600,
                      borderColor: e.kind === kind ? "var(--brand)" : undefined,
                      background: e.kind === kind ? "var(--hover-soft)" : undefined,
                      boxShadow: e.kind === kind ? "0 0 0 1px var(--brand) inset" : undefined,
                    }}
                  >
                    <span className={`env-dot${e.driver_installed ? "" : " neutral"}`} style={{ width: 7, height: 7 }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.label}</span>
                  </button>
                ))}
              </div>
              <div className="field-hint" style={{ display: "flex", alignItems: "center", gap: 5, marginTop: 6 }}>
                <span className="env-dot" style={{ width: 7, height: 7 }} /> driver installed
                <span className="env-dot neutral" style={{ width: 7, height: 7, marginLeft: 10 }} /> driver not installed
              </div>
            </>
          )}
        </div>
      )}
      <label className="field">
        Connection string (SQLAlchemy DSN) <span className="req">*</span>
        <input
          type="text"
          value={dsn}
          onChange={(e) => setDsn(e.target.value)}
          placeholder={selected?.dsn_example ?? GENERIC_DSN_PLACEHOLDER}
          style={{ fontFamily: "var(--mono)", fontSize: 12 }}
        />
        <div className="field-hint">
          Sources are always opened <strong>read-only</strong>.
          {selected?.notes && <div style={{ marginTop: 3 }}>{selected.notes}</div>}
        </div>
      </label>
      {selected && !selected.driver_installed && (
        <div className="info-box">
          The {selected.label} driver is not installed on the API server, so connection tests will
          fail
          {selected.install_extra ? (
            <>
              {" "}
              until it is installed there with{" "}
              <code>pip install "dqsentinel[{selected.install_extra}]"</code>.
            </>
          ) : (
            <> until the driver is installed there.</>
          )}{" "}
          You can still save the connection now.
        </div>
      )}
    </Modal>
  );
}

export default function ConnectionsPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Connection | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["connections"],
    queryFn: () => api.get<Connection[]>("/connections"),
  });

  const health = useQuery({
    queryKey: ["fleet-health"],
    queryFn: () => api.get<ConnectionHealth[]>("/connections/health"),
    enabled: false, // on demand — probing dozens of sources is deliberate
  });
  const healthById = new Map((health.data ?? []).map((h) => [h.id, h]));

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/connections/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connections"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
    },
  });

  const okCount = (health.data ?? []).filter((h) => h.ok).length;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Connections</h1>
          <div className="sub">
            Databases DQ Sentinel can profile and monitor (opened read-only)
            {health.data && (
              <span style={{ marginLeft: 8 }}>
                · fleet: <strong style={{ color: okCount === health.data.length ? "var(--ok)" : "var(--danger-dark)" }}>
                  {okCount}/{health.data.length} reachable
                </strong>
              </span>
            )}
          </div>
        </div>
        <div className="header-actions">
          <button onClick={() => health.refetch()} disabled={health.isFetching}>
            {health.isFetching ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Icon name="bolt" size={13} />}
            {health.isFetching ? "Probing…" : "Check fleet health"}
          </button>
          {isAdmin(user) && (
            <button className="primary" onClick={() => setAdding(true)}>
              <Icon name="plus" size={14} /> Add connection
            </button>
          )}
        </div>
      </div>
      <ErrorBox error={error || remove.error || health.error} />
      {isLoading ? (
        <Spinner />
      ) : !data?.length ? (
        <div className="card">
          <EmptyState title="No connections yet" hint="Add a database to start profiling tables. Try the bundled sample: run `python data/generate_sample_data.py`, then connect sqlite:///<repo>/samples/shopdb.sqlite">
            {isAdmin(user) && (
              <button className="primary" onClick={() => setAdding(true)}>Add your first connection</button>
            )}
          </EmptyState>
        </div>
      ) : (
        <div className="card table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Engine</th>
                <th>DSN</th>
                <th className="num">Datasets</th>
                <th>Added</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((c) => (
                <tr key={c.id}>
                  <td style={{ fontWeight: 700, color: "var(--text-dark)" }}>
                    <Link to={`/connections/${c.id}`}>{c.name}</Link>
                  </td>
                  <td>
                    {healthById.has(c.id) ? (
                      <span
                        className={`pill ${healthById.get(c.id)!.ok ? "pass" : "fail"}`}
                        title={healthById.get(c.id)!.message}
                      >
                        {healthById.get(c.id)!.ok ? `up · ${healthById.get(c.id)!.latency_ms}ms` : "down"}
                      </span>
                    ) : (
                      <span className="pill unknown">—</span>
                    )}
                  </td>
                  <td><span className="badge kind">{c.kind}</span></td>
                  <td className="mono" style={{ maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.dsn_masked}</td>
                  <td className="num">{c.dataset_count}</td>
                  <td style={{ color: "var(--text-light)" }}>{fmtDateTime(c.created_at)}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <Link to={`/connections/${c.id}/browse`} className="btn small" style={{ marginRight: 6 }}>
                      <Icon name="search" size={12} /> Browse tables
                    </Link>
                    {isAdmin(user) && (
                      <button
                        className="small danger"
                        onClick={() => setDeleteTarget(c)}
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {adding && <AddConnectionModal onClose={() => setAdding(false)} />}
      {deleteTarget && (
        <ConfirmModal
          title="Delete connection"
          confirmLabel="Delete connection"
          pending={remove.isPending}
          requireText={deleteTarget.name}
          requireTextLabel="Type the connection name to confirm"
          onClose={() => setDeleteTarget(null)}
          onConfirm={() =>
            remove.mutate(deleteTarget.id, {
              onSuccess: () => setDeleteTarget(null),
            })
          }
        >
          <ErrorBox error={remove.error} />
          <p>
            Delete <strong>{deleteTarget.name}</strong> and its {deleteTarget.dataset_count} dataset(s), checks,
            runs, exceptions, and history?
          </p>
        </ConfirmModal>
      )}
    </div>
  );
}
