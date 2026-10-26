import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { qk } from "../api/queryKeys";
import type { ApiToken, ApiTokenCreated, Connection } from "../api/types";
import { fmtDateTime } from "../lib/format";
import { useUnsavedGuard } from "../lib/useUnsavedGuard";
import { useConfirm } from "./confirm";
import { ErrorBox, Modal } from "./ui";

function isExpired(iso: string): boolean {
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/i.test(iso) ? iso : `${iso}Z`;
  const time = Date.parse(normalized);
  return Number.isFinite(time) && time <= Date.now();
}

export function ApiTokensCard() {
  const client = useQueryClient();
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState<"viewer" | "editor">("viewer");
  const [scope, setScope] = useState<number[]>([]);
  const [days, setDays] = useState(400);
  const [secret, setSecret] = useState("");
  const [copyError, setCopyError] = useState(false);
  const tokens = useQuery({
    queryKey: qk.apiTokens.list(),
    queryFn: () => api.get<ApiToken[]>("/tokens"),
  });
  const connections = useQuery({
    queryKey: qk.connections.list(),
    queryFn: () => api.get<Connection[]>("/connections"),
  });
  const refresh = () => client.invalidateQueries({ queryKey: qk.apiTokens.all });
  const create = useMutation({
    mutationFn: () => api.post<ApiTokenCreated>("/tokens", {
      name, role, connection_scope: scope, expires_in_days: days,
    }),
    onSuccess: (token) => {
      setSecret(token.token);
      void refresh();
    },
  });
  useUnsavedGuard(
    open && (create.isPending || !!secret || !!name || scope.length > 0),
    "The token or draft will be lost.",
  );
  const revoke = useMutation({
    mutationFn: (id: number) => api.del(`/tokens/${id}`),
    onSuccess: refresh,
  });
  const close = async () => {
    if (create.isPending) return;
    if ((secret || name || scope.length) && !(await confirm({
      title: "Close token dialog?",
      body: secret ? "The token will no longer be shown." : "Your draft will be lost.",
    }))) return;
    setOpen(false);
    setSecret("");
    setCopyError(false);
    setName("");
    setScope([]);
    create.reset();
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopyError(false);
    } catch {
      setCopyError(true);
    }
  };

  return (
    <div className="card card-pad" style={{ marginBottom: 18 }}>
      <h3>API tokens</h3>
      <p>Scoped credentials for integrations. Rotate a token by creating a replacement and revoking the old one.</p>
      <ErrorBox error={tokens.error || revoke.error || connections.error} />
      <button onClick={() => setOpen(true)}>Create token</button>
      {(tokens.data ?? []).map((token) => {
        const status = token.revoked_at ? "Revoked" : isExpired(token.expires_at) ? "Expired" : "Active";
        const names = (token.connection_scope ?? []).map((id) => {
          const connection = connections.data?.find((item) => item.id === id);
          return connection ? `${connection.name} (#${id})` : `Connection #${id}`;
        });
        return (
          <div key={token.id} style={{ marginTop: 12, padding: 12, border: "1px solid var(--border)", borderRadius: 8 }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
              <strong>{token.name}</strong>
              <span>{token.role}</span>
              <span>{status}</span>
              {status === "Active" && (
                <button onClick={async () => {
                  if (await confirm({
                    title: "Revoke token?",
                    body: `Integrations using ${token.name} will stop working.`,
                    danger: true,
                  })) revoke.mutate(token.id);
                }}>Revoke</button>
              )}
            </div>
            <div>Connections: {names.length ? names.join(", ") : "None"}</div>
            <div>Expires: {fmtDateTime(token.expires_at)}</div>
            <div>Last used: {token.last_used_at ? fmtDateTime(token.last_used_at) : "Never"}</div>
            {token.revoked_at && <div>Revoked: {fmtDateTime(token.revoked_at)}</div>}
          </div>
        );
      })}
      {open && (
        <Modal title="Create API token" onClose={() => void close()} footer={
          <button disabled={create.isPending} onClick={() => void close()}>Close</button>
        }>
          {secret ? (
            <>
              <p>You won't see this token again. Copy it now.</p>
              <code>{secret}</code>{" "}
              <button onClick={() => void copy()}>Copy</button>
              {copyError && <p role="alert">Copy failed. Select and copy the token manually.</p>}
            </>
          ) : (
            <>
              <p>Choose a descriptive name, such as airflow-prod-orders. At least one connection is required.</p>
              <ErrorBox error={create.error || connections.error} />
              <label className="field">Name
                <input value={name} maxLength={255} onChange={(event) => setName(event.target.value)} />
              </label>
              <label className="field">Role
                <select value={role} onChange={(event) => setRole(event.target.value as "viewer" | "editor")}>
                  <option value="viewer">Viewer</option>
                  <option value="editor">Editor</option>
                </select>
              </label>
              <label className="field">Expires in days
                <input type="number" min={1} max={400} value={days} onChange={(event) => setDays(Number(event.target.value))} />
              </label>
              <fieldset>
                <legend>Connections</legend>
                {connections.data?.map((connection) => (
                  <label key={connection.id} style={{ display: "block" }}>
                    <input type="checkbox" checked={scope.includes(connection.id)} onChange={(event) => setScope(
                      event.target.checked ? [...scope, connection.id] : scope.filter((id) => id !== connection.id),
                    )} /> {connection.name}
                  </label>
                ))}
              </fieldset>
              <button className="primary" disabled={!name || !scope.length || days < 1 || days > 400 || create.isPending} onClick={() => create.mutate()}>
                Create
              </button>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
