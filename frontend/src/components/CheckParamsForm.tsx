// Schema-driven param editor for checks (BF-7). Renders typed fields per check
// type from the registry schema exposed by GET /checks/types, with inline
// validation, a live "effective params" preview, and a raw-JSON escape hatch
// kept in sync with the fields. The single source of truth is the `params`
// object owned by the parent modal; this component is fully controlled.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { CheckTypeInfo } from "../api/types";

export type ParamSpec = CheckTypeInfo["params"][number];

// `tolerance` is a cross-cutting param the runner honours for every check type
// (allow up to N violations before failing) but it is not in any per-type
// schema. Surface it as an optional field on every check so analysts never have
// to reach for raw JSON to set it.
const TOLERANCE_SPEC: ParamSpec = {
  name: "tolerance",
  type: "number",
  required: false,
  default: null,
  description: "Allow up to N violations before the check fails",
};

export type ParamValues = Record<string, unknown>;

/** Param specs to render for a check type, including the universal tolerance field. */
export function paramSpecsFor(specs: ParamSpec[] | undefined): ParamSpec[] {
  const base = specs ?? [];
  return base.some((p) => p.name === "tolerance") ? base : [...base, TOLERANCE_SPEC];
}

function isEmpty(v: unknown): boolean {
  return v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);
}

// `number`-typed params are usually numeric, but some (notably range's min/max,
// which the registry documents as "numeric/date") legitimately hold date or
// datetime strings — the backend inlines them as SQL literals. Treat a value as
// acceptable for a number field when it is a real number, a numeric string, or a
// date-parseable string, so editing an existing date-bounded check never breaks.
function isNumberLike(v: unknown): boolean {
  if (typeof v === "number") return Number.isFinite(v);
  if (typeof v !== "string") return false;
  const s = v.trim();
  if (s === "") return false;
  if (Number.isFinite(Number(s))) return true;
  return !Number.isNaN(Date.parse(s)); // tolerate 2024-01-01, ISO timestamps, etc.
}

/** Per-field validation errors keyed by param name (empty object = valid). */
export function validateParams(specs: ParamSpec[], values: ParamValues): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const spec of paramSpecsFor(specs)) {
    const v = values[spec.name];
    if (spec.required && isEmpty(v)) {
      errors[spec.name] = "Required";
      continue;
    }
    if (isEmpty(v)) continue;
    if (spec.type === "number" && !isNumberLike(v)) {
      errors[spec.name] = "Must be a number or date";
    }
  }
  return errors;
}

// Drop empties so the submitted params object stays minimal and matches what the
// backend's validate_check would keep.
function compactParams(specs: ParamSpec[], values: ParamValues): ParamValues {
  const out: ParamValues = {};
  for (const spec of paramSpecsFor(specs)) {
    const v = values[spec.name];
    if (!isEmpty(v)) out[spec.name] = v;
  }
  // Preserve any extra keys the user set via raw JSON that aren't in the schema.
  for (const [k, v] of Object.entries(values)) {
    if (!(k in out) && !isEmpty(v)) out[k] = v;
  }
  return out;
}

function listToText(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ");
  return v == null ? "" : String(v);
}

function textToList(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function Field({
  spec,
  value,
  error,
  onChange,
}: {
  spec: ParamSpec;
  value: unknown;
  error?: string;
  onChange: (v: unknown) => void;
}) {
  const id = useId();
  const errId = `${id}-err`;
  const hintId = `${id}-hint`;
  const describedBy = [spec.description ? hintId : null, error ? errId : null].filter(Boolean).join(" ");
  const options = (spec as ParamSpec & { options?: unknown[] }).options;

  const label = (
    <>
      {spec.name}
      {spec.required && <span className="req"> *</span>}
    </>
  );

  let control: React.ReactNode;
  if (spec.type === "boolean") {
    // Default booleans to their schema default when unset so the control reflects
    // effective behaviour (e.g. accepted_values case_sensitive defaults to true).
    const checked = value === undefined ? Boolean(spec.default) : Boolean(value);
    return (
      <label className="field check-param-bool" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={checked}
          aria-describedby={describedBy || undefined}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span>
          {label}
          {spec.description && (
            <span id={hintId} className="field-hint" style={{ display: "block" }}>
              {spec.description}
            </span>
          )}
        </span>
      </label>
    );
  } else if (Array.isArray(options) && options.length > 0) {
    control = (
      <select
        id={id}
        value={value == null ? "" : String(value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      >
        {!spec.required && <option value="">— none —</option>}
        {options.map((o) => (
          <option key={String(o)} value={String(o)}>
            {String(o)}
          </option>
        ))}
      </select>
    );
  } else if (spec.type === "list") {
    control = (
      <textarea
        id={id}
        rows={2}
        value={listToText(value)}
        placeholder="comma or newline separated"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(e) => {
          const list = textToList(e.target.value);
          onChange(list.length ? list : undefined);
        }}
      />
    );
  } else if (spec.type === "sql") {
    control = (
      <textarea
        id={id}
        rows={4}
        value={value == null ? "" : String(value)}
        style={{ fontFamily: "var(--mono)", fontSize: 12 }}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      />
    );
  } else if (spec.type === "number") {
    // A native number input can't display a non-numeric string (e.g. a date
    // bound on a range check), so fall back to text when the value isn't numeric
    // — the value stays visible and editable, and numeric input still coerces.
    const numeric = value === undefined || value === null || value === "" || typeof value === "number";
    control = (
      <input
        id={id}
        type={numeric ? "number" : "text"}
        value={value === undefined || value === null ? "" : String(value)}
        placeholder={spec.default != null ? `default ${spec.default}` : undefined}
        inputMode={numeric ? "decimal" : undefined}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return onChange(undefined);
          const n = Number(raw);
          onChange(raw.trim() !== "" && Number.isFinite(n) ? n : raw);
        }}
      />
    );
  } else {
    // string and any unknown type fall back to a text input
    control = (
      <input
        id={id}
        type="text"
        value={value == null ? "" : String(value)}
        placeholder={spec.default != null ? `default ${spec.default}` : undefined}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      />
    );
  }

  return (
    <label className="field" htmlFor={id}>
      {label}
      {control}
      {spec.description && (
        <span id={hintId} className="field-hint">
          {spec.description}
        </span>
      )}
      {error && (
        <span id={errId} className="field-error" role="alert">
          {error}
        </span>
      )}
    </label>
  );
}

export default function CheckParamsForm({
  specs,
  params,
  onChange,
  errors,
}: {
  specs: ParamSpec[] | undefined;
  params: ParamValues;
  onChange: (next: ParamValues) => void;
  errors?: Record<string, string>;
}) {
  const allSpecs = useMemo(() => paramSpecsFor(specs), [specs]);
  const effective = useMemo(() => compactParams(allSpecs, params), [allSpecs, params]);
  const effectiveJson = useMemo(() => JSON.stringify(effective, null, 2), [effective]);

  const [advanced, setAdvanced] = useState(false);
  const [rawDraft, setRawDraft] = useState(effectiveJson);
  const [rawError, setRawError] = useState<string | null>(null);
  const advId = useId();

  // Keep the raw-JSON draft mirrored to the fields whenever the panel is closed,
  // or when the user isn't actively editing it (panel open + draft already
  // matches the canonical form). This is the "stays in sync" guarantee.
  const advancedRef = useRef(advanced);
  advancedRef.current = advanced;
  useEffect(() => {
    if (!advancedRef.current) {
      setRawDraft(effectiveJson);
      setRawError(null);
    }
  }, [effectiveJson]);

  const setField = (name: string, value: unknown) => {
    const next = { ...params };
    if (value === undefined) delete next[name];
    else next[name] = value;
    onChange(next);
  };

  const applyRaw = (text: string) => {
    setRawDraft(text);
    if (text.trim() === "") {
      setRawError(null);
      onChange({});
      return;
    }
    try {
      const parsed = JSON.parse(text);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        setRawError("Params must be a JSON object");
        return;
      }
      setRawError(null);
      onChange(parsed as ParamValues);
    } catch (e) {
      setRawError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };

  return (
    <div className="check-params">
      {allSpecs.map((spec) => (
        <Field
          key={spec.name}
          spec={spec}
          value={params[spec.name]}
          error={errors?.[spec.name]}
          onChange={(v) => setField(spec.name, v)}
        />
      ))}

      <div className="check-params-preview" aria-live="polite">
        <div className="check-params-preview-label">Effective params</div>
        <pre>{effectiveJson}</pre>
      </div>

      <div className="check-params-advanced">
        <button
          type="button"
          className="ghost small check-params-toggle"
          aria-expanded={advanced}
          aria-controls={advId}
          onClick={() => {
            if (!advanced) setRawDraft(effectiveJson);
            setAdvanced((a) => !a);
          }}
        >
          {advanced ? "▾" : "▸"} Advanced (raw JSON)
        </button>
        {advanced && (
          <div id={advId} className="field" style={{ marginTop: 8, marginBottom: 0 }}>
            <textarea
              rows={5}
              value={rawDraft}
              aria-label="Raw params JSON"
              aria-invalid={rawError ? true : undefined}
              aria-describedby={rawError ? `${advId}-err` : undefined}
              style={{ fontFamily: "var(--mono)", fontSize: 12 }}
              onChange={(e) => applyRaw(e.target.value)}
            />
            {rawError ? (
              <span id={`${advId}-err`} className="field-error" role="alert">
                {rawError}
              </span>
            ) : (
              <span className="field-hint">Edits here update the fields above.</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
