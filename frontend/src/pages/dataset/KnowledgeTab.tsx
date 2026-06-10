import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { Knowledge } from "../../api/types";
import { canEdit, useAuth } from "../../auth";
import { ErrorBox, Spinner } from "../../components/ui";

const EMPTY: Knowledge = {
  business_context: "",
  known_issues: "",
  importance: "medium",
  owner: "",
  freshness_sla_hours: null,
  pii_columns: [],
  notes: "",
};

export default function KnowledgeTab({ datasetId }: { datasetId: number }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const editable = canEdit(user);
  const [form, setForm] = useState<Knowledge>(EMPTY);
  const [piiText, setPiiText] = useState("");
  const [saved, setSaved] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["knowledge", datasetId],
    queryFn: () => api.get<Knowledge>(`/datasets/${datasetId}/knowledge`),
  });

  useEffect(() => {
    if (data) {
      setForm({ ...EMPTY, ...data });
      setPiiText((data.pii_columns ?? []).join(", "));
    }
  }, [data]);

  const save = useMutation({
    mutationFn: () =>
      api.put<Knowledge>(`/datasets/${datasetId}/knowledge`, {
        ...form,
        freshness_sla_hours: form.freshness_sla_hours || null,
        pii_columns: piiText.split(",").map((s) => s.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge", datasetId] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  if (isLoading) return <Spinner />;

  const set = <K extends keyof Knowledge>(key: K, value: Knowledge[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

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
          <label className="field">
            Other notes
            <textarea value={form.notes} onChange={(e) => set("notes", e.target.value)} placeholder="Anything else an analyst (or the AI) should know" />
          </label>
          {editable && (
            <button className="primary" onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save knowledge"}
            </button>
          )}
        </fieldset>
      </div>
      <div className="card card-pad" style={{ background: "#fbfcfd" }}>
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
