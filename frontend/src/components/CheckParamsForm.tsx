import { useEffect, useMemo, useState } from "react";
import type { CheckTypeInfo } from "../api/types";

type ParamField = CheckTypeInfo["params"][number];
type DraftValue = string | boolean;

function draftValue(field: ParamField, params: Record<string, unknown>): DraftValue {
  const value = params[field.name] ?? field.default;
  if (field.type === "boolean") return Boolean(value);
  if (field.type === "list") return Array.isArray(value) ? value.join(", ") : value == null ? "" : String(value);
  return value == null ? "" : String(value);
}

function initialDraft(typeInfo: CheckTypeInfo, params: Record<string, unknown>) {
  const draft: Record<string, DraftValue> = {};
  for (const field of typeInfo.params) draft[field.name] = draftValue(field, params);
  draft.tolerance = params.tolerance == null ? "" : String(params.tolerance);
  return draft;
}

function parseList(value: string): string[] {
  const trimmed = value.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("[")) {
    const parsed = JSON.parse(trimmed);
    if (!Array.isArray(parsed)) throw new Error("List params must be arrays");
    return parsed.map((item) => String(item));
  }
  return trimmed
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildParams(typeInfo: CheckTypeInfo, draft: Record<string, DraftValue>) {
  const params: Record<string, unknown> = {};
  for (const field of typeInfo.params) {
    const value = draft[field.name];
    if (field.type === "boolean") {
      params[field.name] = Boolean(value);
      continue;
    }

    const text = typeof value === "string" ? value.trim() : "";
    if (!text) {
      if (field.required) throw new Error(`${field.name} is required`);
      continue;
    }

    if (field.type === "number") {
      const n = Number(text);
      if (!Number.isFinite(n)) throw new Error(`${field.name} must be a number`);
      params[field.name] = n;
    } else if (field.type === "list") {
      const values = parseList(text);
      if (field.required && values.length === 0) throw new Error(`${field.name} needs at least one value`);
      if (values.length) params[field.name] = values;
    } else {
      params[field.name] = text;
    }
  }

  const tolerance = typeof draft.tolerance === "string" ? draft.tolerance.trim() : "";
  if (tolerance) {
    const n = Number(tolerance);
    if (!Number.isFinite(n) || n < 0) throw new Error("tolerance must be zero or greater");
    params.tolerance = n;
  }
  return params;
}

export default function CheckParamsForm({
  typeInfo,
  params,
  onChange,
}: {
  typeInfo: CheckTypeInfo;
  params: Record<string, unknown>;
  onChange: (params: Record<string, unknown>, error: string | null) => void;
}) {
  const [advanced, setAdvanced] = useState(false);
  const [draft, setDraft] = useState<Record<string, DraftValue>>(() => initialDraft(typeInfo, params));
  const [raw, setRaw] = useState(() => JSON.stringify(params ?? {}, null, 2));

  useEffect(() => {
    setDraft(initialDraft(typeInfo, params));
    setRaw(JSON.stringify(params ?? {}, null, 2));
    setAdvanced(false);
  }, [typeInfo.key]);

  const parsed = useMemo(() => {
    try {
      const next = advanced ? JSON.parse(raw || "{}") : buildParams(typeInfo, draft);
      if (!next || Array.isArray(next) || typeof next !== "object") throw new Error("Params must be a JSON object");
      return { params: next as Record<string, unknown>, error: null };
    } catch (err) {
      return { params: null, error: err instanceof Error ? err.message : String(err) };
    }
  }, [advanced, draft, raw, typeInfo]);

  useEffect(() => {
    onChange(parsed.params ?? {}, parsed.error);
  }, [parsed, onChange]);

  const fields = typeInfo.params;

  return (
    <div className="params-form">
      <div className="params-head">
        <div>
          <strong>Parameters</strong>
          <div className="field-hint">Configure this check without hand-editing JSON.</div>
        </div>
        <label className="switch-row">
          <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} />
          Advanced JSON
        </label>
      </div>

      {advanced ? (
        <label className="field">
          Raw params
          <textarea
            rows={7}
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            style={{ fontFamily: "var(--mono)", fontSize: 12 }}
          />
        </label>
      ) : (
        <>
          {!fields.length && <div className="info-box">This check type has no required parameters.</div>}
          {fields.map((field) => {
            const value = draft[field.name];
            if (field.type === "boolean") {
              return (
                <label key={field.name} className="switch-row param-switch">
                  <input
                    type="checkbox"
                    checked={Boolean(value)}
                    onChange={(e) => setDraft((prev) => ({ ...prev, [field.name]: e.target.checked }))}
                  />
                  <span>
                    {field.name}
                    {field.description && <span className="field-hint">{field.description}</span>}
                  </span>
                </label>
              );
            }
            return (
              <label key={field.name} className="field">
                {field.name} {field.required && <span className="req">*</span>}
                {field.type === "sql" ? (
                  <textarea
                    rows={5}
                    value={String(value ?? "")}
                    onChange={(e) => setDraft((prev) => ({ ...prev, [field.name]: e.target.value }))}
                    style={{ fontFamily: "var(--mono)", fontSize: 12 }}
                  />
                ) : (
                  <input
                    type={field.type === "number" ? "number" : "text"}
                    value={String(value ?? "")}
                    onChange={(e) => setDraft((prev) => ({ ...prev, [field.name]: e.target.value }))}
                    placeholder={field.type === "list" ? "Comma or newline separated values" : undefined}
                  />
                )}
                {field.description && <div className="field-hint">{field.description}</div>}
              </label>
            );
          })}
          <label className="field">
            Tolerance
            <input
              type="number"
              min="0"
              value={String(draft.tolerance ?? "")}
              onChange={(e) => setDraft((prev) => ({ ...prev, tolerance: e.target.value }))}
              placeholder="0"
            />
            <div className="field-hint">Allowed violations before the run fails.</div>
          </label>
        </>
      )}

      {parsed.error && <div className="error-box">{parsed.error}</div>}
      <div className="params-preview">
        <div className="label">Effective params</div>
        <pre className="result">{JSON.stringify(parsed.params ?? {}, null, 2)}</pre>
      </div>
    </div>
  );
}
