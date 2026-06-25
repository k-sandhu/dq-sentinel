import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type MutableRefObject } from "react";
import { api } from "../../api/client";
import { qk } from "../../api/queryKeys";
import type { Knowledge } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import { ErrorBox, Spinner } from "../../components/ui";

const EMPTY: Knowledge = {
  business_context: "",
  known_issues: "",
  importance: "medium",
  owner: "",
  domain: "",
  team: "",
  freshness_sla_hours: null,
  slo_target_score: null,
  slo_window_days: null,
  slo_enabled: true,
  pii_columns: [],
  notes: "",
};

function parseOptionalNumber(raw: string): { value: number | null; invalid: boolean } {
  if (raw.trim() === "") return { value: null, invalid: false };
  const value = Number(raw);
  return Number.isFinite(value) ? { value, invalid: false } : { value: null, invalid: true };
}

export default function KnowledgeTab({
  datasetId,
  dirtyRef,
}: {
  datasetId: number;
  /** Set by this tab so the parent can warn before a tab switch discards edits (BF-3). */
  dirtyRef?: MutableRefObject<boolean>;
}) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const editable = canEdit(user);
  const [form, setForm] = useState<Knowledge>(EMPTY);
  const [piiText, setPiiText] = useState("");
  const [sloTargetText, setSloTargetText] = useState("");
  const [sloWindowText, setSloWindowText] = useState("");
  const [saved, setSaved] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: qk.knowledge.detail(datasetId),
    queryFn: () => api.get<Knowledge>(`/datasets/${datasetId}/knowledge`),
  });

  useEffect(() => {
    if (data) {
      setForm({ ...EMPTY, ...data });
      setPiiText((data.pii_columns ?? []).join(", "));
      setSloTargetText(data.slo_target_score == null ? "" : String(data.slo_target_score));
      setSloWindowText(data.slo_window_days == null ? "" : String(data.slo_window_days));
    }
  }, [data]);

  const set = <K extends keyof Knowledge>(key: K, value: Knowledge[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const targetParsed = parseOptionalNumber(sloTargetText);
  const windowParsed = parseOptionalNumber(sloWindowText);
  const targetInvalid =
    targetParsed.invalid || (targetParsed.value !== null && (targetParsed.value < 0 || targetParsed.value > 100));
  const windowInvalid = windowParsed.invalid || (windowParsed.value !== null && windowParsed.value <= 0);

  const save = useMutation({
    mutationFn: () => {
      if (targetInvalid || windowInvalid) throw new Error("Fix invalid SLO fields before saving.");
      return api.put<Knowledge>(`/datasets/${datasetId}/knowledge`, {
        ...form,
        freshness_sla_hours: form.freshness_sla_hours || null,
        slo_target_score: targetParsed.value,
        slo_window_days: windowParsed.value,
        pii_columns: piiText.split(",").map((s) => s.trim()).filter(Boolean),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.knowledge.detail(datasetId) });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  // Dirty = the loaded form differs from what's on the server (and we're not mid-save).
  const baseline =
    data != null
      ? `${JSON.stringify({ ...EMPTY, ...data })}|${(data.pii_columns ?? []).join(", ")}|${
          data.slo_target_score ?? ""
        }|${data.slo_window_days ?? ""}`
      : null;
  const dirty =
    baseline !== null && !save.isPending && `${JSON.stringify(form)}|${piiText}|${sloTargetText}|${sloWindowText}` !== baseline;

  useEffect(() => {
    if (dirtyRef) dirtyRef.current = dirty;
    return () => {
      if (dirtyRef) dirtyRef.current = false;
    };
  }, [dirty, dirtyRef]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  if (isLoading) return <Spinner />;

  const sloTargetLabel = !form.slo_enabled
    ? "disabled"
    : targetInvalid
      ? "invalid target"
      : targetParsed.value === null
      ? "importance default"
      : "explicit target";

  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      <div className="card card-pad">
        <h3>Table knowledge</h3>
        <p style={{ fontSize: 12.5, color: "var(--text-light)", marginTop: 0 }}>
          Everything you record here is fed to the AI when it generates checks, explores the data and
          investigates failures — the more context, the sharper the checks.
        </p>
        <ErrorBox error={error || save.error} />
        {saved && <div className="info-box">Saved.</div>}
        <fieldset disabled={!editable} style={{ border: "none", padding: 0, margin: 0 }}>
          <label className="field">
            Business context
            <textarea
              value={form.business_context}
              onChange={(e) => set("business_context", e.target.value)}
              placeholder="What this table represents, where it comes from, who consumes it…"
            />
          </label>
          <label className="field">
            Known issues
            <textarea
              value={form.known_issues}
              onChange={(e) => set("known_issues", e.target.value)}
              placeholder="Issues that usually come up: late-arriving partitions, dup events after retries, currency typos…"
            />
          </label>
          <div className="form-row">
            <label className="field">
              Importance
              <select value={form.importance} onChange={(e) => set("importance", e.target.value as Knowledge["importance"])}>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
                <option value="critical">critical</option>
              </select>
            </label>
            <label className="field">
              Owner
              <input type="text" value={form.owner} onChange={(e) => set("owner", e.target.value)} placeholder="team-data@company.com" />
            </label>
          </div>
          <div className="form-row">
            <label className="field">
              Domain
              <input type="text" value={form.domain} onChange={(e) => set("domain", e.target.value)} placeholder="Finance" />
            </label>
            <label className="field">
              Team
              <input type="text" value={form.team} onChange={(e) => set("team", e.target.value)} placeholder="Revenue ops" />
            </label>
          </div>
          <div className="form-row">
            <label className="field">
              Freshness SLA (hours)
              <input
                type="number"
                value={form.freshness_sla_hours ?? ""}
                onChange={(e) => set("freshness_sla_hours", e.target.value ? Number(e.target.value) : null)}
                placeholder="24"
              />
              <div className="field-hint">Used as the threshold for generated freshness checks</div>
            </label>
            <label className="field">
              PII columns
              <input type="text" value={piiText} onChange={(e) => setPiiText(e.target.value)} placeholder="email, full_name" />
              <div className="field-hint">Comma-separated. Values in these columns are redacted before being sent to the LLM.</div>
            </label>
          </div>
          <div className="form-row">
            <label className="field">
              Reliability SLO
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 500, marginTop: 8 }}>
                <input
                  type="checkbox"
                  checked={form.slo_enabled}
                  onChange={(e) => set("slo_enabled", e.target.checked)}
                  style={{ width: "auto", marginTop: 0 }}
                />
                Enabled
              </label>
              <div className="field-hint">Target source: {sloTargetLabel}</div>
            </label>
            <label className="field">
              Target score
              <input
                type="number"
                min={0}
                max={100}
                step={0.1}
                value={sloTargetText}
                onChange={(e) => {
                  const raw = e.target.value;
                  setSloTargetText(raw);
                  const parsed = parseOptionalNumber(raw);
                  if (!parsed.invalid) set("slo_target_score", parsed.value);
                }}
                placeholder="Importance default"
                aria-invalid={targetInvalid}
              />
              {targetInvalid && <div className="field-hint" style={{ color: "var(--danger-dark)" }}>Use 0-100.</div>}
            </label>
            <label className="field">
              Window days
              <input
                type="number"
                min={1}
                step={1}
                value={sloWindowText}
                onChange={(e) => {
                  const raw = e.target.value;
                  setSloWindowText(raw);
                  const parsed = parseOptionalNumber(raw);
                  if (!parsed.invalid) set("slo_window_days", parsed.value);
                }}
                placeholder="30"
                aria-invalid={windowInvalid}
              />
              {windowInvalid && <div className="field-hint" style={{ color: "var(--danger-dark)" }}>Use a positive number.</div>}
            </label>
          </div>
          <label className="field">
            Other notes
            <textarea value={form.notes} onChange={(e) => set("notes", e.target.value)} placeholder="Anything else an analyst (or the AI) should know" />
          </label>
          {editable && (
            <button className="primary" onClick={() => save.mutate()} disabled={save.isPending || targetInvalid || windowInvalid}>
              {save.isPending ? "Saving…" : "Save knowledge"}
            </button>
          )}
        </fieldset>
      </div>
      <div className="card card-pad" style={{ background: "var(--card2)" }}>
        <h3>How knowledge is used</h3>
        <ul style={{ fontSize: 13, lineHeight: 1.8, paddingLeft: 18, margin: 0 }}>
          <li><strong>Known issues</strong> → the AI proposes checks that catch recurrences.</li>
          <li><strong>Freshness SLA</strong> → sets thresholds on generated freshness checks (severity becomes <em>error</em>).</li>
          <li><strong>Importance</strong> → scales the severity of proposals.</li>
          <li><strong>PII columns</strong> → values are redacted from every LLM prompt and tool result.</li>
          <li><strong>Context & notes</strong> → grounds the root-cause analyst when it investigates failures.</li>
          <li>Triage decisions (marking exceptions <em>expected</em> with notes) build up institutional memory alongside this page.</li>
        </ul>
      </div>
    </div>
  );
}
