// Display metadata for check types — extend when adding a type to
// backend/app/core/check_types.py.

export const CHECK_TYPE_LABELS: Record<string, string> = {
  not_null: "Not null",
  unique: "Unique",
  accepted_values: "Accepted values",
  range: "Range",
  string_length: "String length",
  regex_match: "Pattern match",
  freshness: "Freshness",
  row_count_min: "Min row count",
  row_count_anomaly: "Row count anomaly",
  custom_sql: "Custom SQL",
  ml_outlier: "ML outlier",
  distribution_drift: "Distribution drift",
};

export function checkTypeLabel(key: string): string {
  return CHECK_TYPE_LABELS[key] ?? key;
}

export const RUN_STATUS_COLORS: Record<string, string> = {
  pass: "var(--ok)",
  warn: "var(--warn)",
  fail: "var(--danger)",
  error: "var(--danger-dark)",
};

export function originLabel(origin: string): string {
  return { llm: "AI", heuristic: "Auto", manual: "Manual", system: "System" }[origin] ?? origin;
}
