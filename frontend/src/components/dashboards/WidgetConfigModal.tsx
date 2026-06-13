import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type {
  Connection,
  Dataset,
  VizType,
  Widget,
  WidgetParams,
  WidgetType,
} from "../../api/types";
import Markdown from "../Markdown";
import { Modal } from "../ui";

const STATUS_OPTIONS = ["open", "acknowledged", "expected", "resolved", "muted"];
const SEVERITY_OPTIONS = ["info", "warn", "error"];
const RECURRENCE_OPTIONS = [
  { value: "", label: "Any recurrence" },
  { value: "new", label: "New (last 24h)" },
  { value: "recurring", label: "Recurring" },
];
const ASSIGNEE_OPTIONS = [
  { value: "", label: "Anyone" },
  { value: "me", label: "Me" },
  { value: "none", label: "Unassigned" },
];
const VIZ_TYPES: VizType[] = ["number", "bar", "line", "area", "pie", "table"];

/** Parse a copied "/exceptions?status=open&severity=error" workspace URL (or a
 *  bare query string) into a params record, keeping only the allowlisted keys
 *  the metric/exceptions widgets accept. This is how analysts actually configure
 *  widgets: filter in the workspace, copy the URL, paste here. */
const ALLOWED_PARAM_KEYS = new Set([
  "status",
  "severity",
  "check_type",
  "dataset_id",
  "check_id",
  "run_id",
  "assignee",
  "recurrence",
  "seen_since",
  "q",
  "sort",
]);

function parseFiltersFromUrl(input: string): WidgetParams {
  const out: WidgetParams = {};
  const qIndex = input.indexOf("?");
  const qs = qIndex >= 0 ? input.slice(qIndex + 1) : input;
  const sp = new URLSearchParams(qs);
  // collapse repeats into comma lists (status/severity are multi)
  const grouped: Record<string, string[]> = {};
  for (const [k, v] of sp.entries()) {
    if (!ALLOWED_PARAM_KEYS.has(k) || !v) continue;
    (grouped[k] ??= []).push(v);
  }
  for (const [k, vals] of Object.entries(grouped)) out[k] = vals.join(",");
  return out;
}

function csvToList(v: string | undefined): string[] {
  return (v ?? "").split(",").map((s) => s.trim()).filter(Boolean);
}

function toggleInCsv(current: string | undefined, value: string): string {
  const list = csvToList(current);
  const next = list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
  return next.join(",");
}

/** A compact filter form (mini FilterBar) producing the `params` record for
 *  metric/exceptions widgets, plus a paste-from-URL field. */
function FilterForm({
  params,
  onChange,
  datasets,
}: {
  params: WidgetParams;
  onChange: (p: WidgetParams) => void;
  datasets: Dataset[];
}) {
  const [pasteValue, setPasteValue] = useState("");
  const set = (k: string, v: string) => {
    const next = { ...params };
    if (v) next[k] = v;
    else delete next[k];
    onChange(next);
  };

  return (
    <div className="cd-filter-form">
      <label className="cd-field">
        <span>Dataset</span>
        <select value={params.dataset_id ?? ""} onChange={(e) => set("dataset_id", e.target.value)}>
          <option value="">All datasets</option>
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.table_name}
            </option>
          ))}
        </select>
      </label>

      <div className="cd-field">
        <span>Status</span>
        <div className="cd-chips" role="group" aria-label="Status filter">
          {STATUS_OPTIONS.map((s) => {
            const on = csvToList(params.status).includes(s);
            return (
              <button
                type="button"
                key={s}
                className={`cd-chip${on ? " on" : ""}`}
                aria-pressed={on}
                onClick={() => set("status", toggleInCsv(params.status, s))}
              >
                {s}
              </button>
            );
          })}
        </div>
      </div>

      <div className="cd-field">
        <span>Severity</span>
        <div className="cd-chips" role="group" aria-label="Severity filter">
          {SEVERITY_OPTIONS.map((s) => {
            const on = csvToList(params.severity).includes(s);
            return (
              <button
                type="button"
                key={s}
                className={`cd-chip${on ? " on" : ""}`}
                aria-pressed={on}
                onClick={() => set("severity", toggleInCsv(params.severity, s))}
              >
                {s}
              </button>
            );
          })}
        </div>
      </div>

      <div className="cd-field-row">
        <label className="cd-field">
          <span>Recurrence</span>
          <select value={params.recurrence ?? ""} onChange={(e) => set("recurrence", e.target.value)}>
            {RECURRENCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="cd-field">
          <span>Assignee</span>
          <select value={params.assignee ?? ""} onChange={(e) => set("assignee", e.target.value)}>
            {ASSIGNEE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="cd-field">
        <span>Search text (reason / check)</span>
        <input
          type="text"
          value={params.q ?? ""}
          onChange={(e) => set("q", e.target.value)}
          placeholder="optional free text…"
        />
      </label>

      <label className="cd-field">
        <span>Paste filters from a workspace URL</span>
        <div className="cd-paste">
          <input
            type="text"
            value={pasteValue}
            onChange={(e) => setPasteValue(e.target.value)}
            placeholder="/exceptions?status=open&severity=error"
          />
          <button
            type="button"
            className="small"
            onClick={() => {
              const parsed = parseFiltersFromUrl(pasteValue);
              if (Object.keys(parsed).length) onChange(parsed);
              setPasteValue("");
            }}
          >
            Apply
          </button>
        </div>
      </label>
    </div>
  );
}

/** Build a sensible default config for a freshly added widget of `type`. */
function defaultWidget(type: WidgetType, id: string): Widget {
  const base = { id, title: "", span: 1 as const };
  switch (type) {
    case "metric":
      return { ...base, title: "Metric", type, config: { params: { status: "open" }, warn_at: null, danger_at: null } };
    case "exceptions":
      return { ...base, title: "Recent exceptions", type, config: { params: { status: "open" }, limit: 5 } };
    case "checks":
      return { ...base, title: "Checks", type, config: { dataset_ids: [], only_failing: false } };
    case "sql":
      return { ...base, title: "SQL", span: 2, type, config: { connection_id: 0, sql: "SELECT 1 AS n", viz: { type: "number", x: null, y: null } } };
    case "note":
      return { ...base, title: "Note", type, config: { markdown: "" } };
  }
}

export { defaultWidget };

/** Per-type config modal. `initial` is the widget being edited (or a default for
 *  a new one). Calls onSave with the edited widget; the parent owns layout state
 *  and persistence. The existing Modal traps focus (a11y). saveError surfaces
 *  server validation (e.g. guard_sql 422) inline. */
export default function WidgetConfigModal({
  initial,
  onSave,
  onClose,
  saveError,
}: {
  initial: Widget;
  onSave: (w: Widget) => void;
  onClose: () => void;
  saveError?: string | null;
}) {
  const [draft, setDraft] = useState<Widget>(initial);
  const [showPreview, setShowPreview] = useState(false);

  const { data: datasets } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => api.get<Dataset[]>("/datasets"),
  });
  const { data: connections } = useQuery({
    queryKey: ["connections"],
    queryFn: () => api.get<Connection[]>("/connections"),
    enabled: draft.type === "sql",
  });

  const dsList = datasets ?? [];

  function patchConfig<T extends Widget>(updater: (w: T) => T) {
    setDraft((d) => updater(d as T));
  }

  return (
    <Modal
      title={`Configure ${draft.type} widget`}
      onClose={onClose}
      wide={draft.type === "sql" || draft.type === "note"}
      footer={
        <>
          {saveError && <span className="cd-modal-error">{saveError}</span>}
          <button className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" onClick={() => onSave(draft)}>
            Save widget
          </button>
        </>
      }
    >
      <label className="cd-field">
        <span>Title</span>
        <input
          type="text"
          value={draft.title}
          onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          placeholder="Widget title"
          maxLength={200}
        />
      </label>

      {draft.type === "metric" && (
        <>
          <FilterForm
            params={draft.config.params}
            datasets={dsList}
            onChange={(params) => patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, params } }))}
          />
          <div className="cd-field-row">
            <label className="cd-field">
              <span>Warn at ≥</span>
              <input
                type="number"
                value={draft.config.warn_at ?? ""}
                onChange={(e) =>
                  patchConfig<typeof draft>((d) => ({
                    ...d,
                    config: { ...d.config, warn_at: e.target.value === "" ? null : Number(e.target.value) },
                  }))
                }
                placeholder="never"
              />
            </label>
            <label className="cd-field">
              <span>Danger at ≥</span>
              <input
                type="number"
                value={draft.config.danger_at ?? ""}
                onChange={(e) =>
                  patchConfig<typeof draft>((d) => ({
                    ...d,
                    config: { ...d.config, danger_at: e.target.value === "" ? null : Number(e.target.value) },
                  }))
                }
                placeholder="never"
              />
            </label>
          </div>
        </>
      )}

      {draft.type === "exceptions" && (
        <>
          <FilterForm
            params={draft.config.params}
            datasets={dsList}
            onChange={(params) => patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, params } }))}
          />
          <label className="cd-field">
            <span>Rows to show (1–10)</span>
            <input
              type="number"
              min={1}
              max={10}
              value={draft.config.limit}
              onChange={(e) =>
                patchConfig<typeof draft>((d) => ({
                  ...d,
                  config: { ...d.config, limit: Math.max(1, Math.min(10, Number(e.target.value) || 1)) },
                }))
              }
            />
          </label>
        </>
      )}

      {draft.type === "checks" && (
        <>
          <div className="cd-field">
            <span>Datasets (up to 20)</span>
            <div className="cd-ds-multi">
              {dsList.map((d) => {
                const on = draft.config.dataset_ids.includes(d.id);
                return (
                  <label key={d.id} className="cd-check-row">
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() =>
                        patchConfig<typeof draft>((w) => {
                          const ids = on
                            ? w.config.dataset_ids.filter((x) => x !== d.id)
                            : [...w.config.dataset_ids, d.id].slice(0, 20);
                          return { ...w, config: { ...w.config, dataset_ids: ids } };
                        })
                      }
                    />
                    {d.table_name}
                  </label>
                );
              })}
              {dsList.length === 0 && <div className="empty" style={{ padding: 8 }}>No datasets yet.</div>}
            </div>
          </div>
          <label className="cd-check-row">
            <input
              type="checkbox"
              checked={draft.config.only_failing}
              onChange={(e) =>
                patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, only_failing: e.target.checked } }))
              }
            />
            Only show failing checks
          </label>
        </>
      )}

      {draft.type === "sql" && (
        <>
          <label className="cd-field">
            <span>Connection</span>
            <select
              value={draft.config.connection_id || ""}
              onChange={(e) =>
                patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, connection_id: Number(e.target.value) } }))
              }
            >
              <option value="">Select a connection…</option>
              {(connections ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.kind})
                </option>
              ))}
            </select>
          </label>
          <label className="cd-field">
            <span>SQL (read-only SELECT)</span>
            <textarea
              className="mono"
              rows={5}
              value={draft.config.sql}
              onChange={(e) => patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, sql: e.target.value } }))}
              spellCheck={false}
            />
          </label>
          <div className="cd-field-row">
            <label className="cd-field">
              <span>Chart</span>
              <select
                value={draft.config.viz.type}
                onChange={(e) =>
                  patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, viz: { ...d.config.viz, type: e.target.value as VizType } } }))
                }
              >
                {VIZ_TYPES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label className="cd-field">
              <span>X column</span>
              <input
                type="text"
                value={draft.config.viz.x ?? ""}
                onChange={(e) =>
                  patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, viz: { ...d.config.viz, x: e.target.value || null } } }))
                }
                placeholder="(optional)"
              />
            </label>
            <label className="cd-field">
              <span>Y column</span>
              <input
                type="text"
                value={draft.config.viz.y ?? ""}
                onChange={(e) =>
                  patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, viz: { ...d.config.viz, y: e.target.value || null } } }))
                }
                placeholder="(optional)"
              />
            </label>
          </div>
          <div className="cd-hint">Results are captured as a server-side snapshot when you Refresh the dashboard.</div>
        </>
      )}

      {draft.type === "note" && (
        <>
          <div className="cd-note-head">
            <button type="button" className="small ghost" onClick={() => setShowPreview((p) => !p)}>
              {showPreview ? "Edit" : "Preview"}
            </button>
          </div>
          {showPreview ? (
            <div className="cd-note-preview">
              <Markdown>{draft.config.markdown || "_Nothing to preview_"}</Markdown>
            </div>
          ) : (
            <textarea
              rows={8}
              value={draft.config.markdown}
              onChange={(e) => patchConfig<typeof draft>((d) => ({ ...d, config: { ...d.config, markdown: e.target.value } }))}
              placeholder="Markdown — runbook links, context… (no raw HTML)"
              maxLength={5000}
            />
          )}
        </>
      )}
    </Modal>
  );
}
