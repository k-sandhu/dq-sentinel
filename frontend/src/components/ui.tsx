import { Fragment, type ReactNode } from "react";
import { Link } from "react-router";

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    home: <path d="M3 9.5 12 3l9 6.5V21h-6v-7h-6v7H3z" />,
    db: (
      <>
        <ellipse cx="12" cy="5" rx="8" ry="3" />
        <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
      </>
    ),
    table: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 10h18M9 10v10M15 10v10" />
      </>
    ),
    shield: <path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6l8-3z" />,
    play: <path d="M7 4.5v15l13-7.5L7 4.5z" />,
    alert: (
      <>
        <path d="M12 3 2.5 20h19L12 3z" />
        <path d="M12 10v4M12 17.2v.3" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19 12a7 7 0 0 0-.2-1.6l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2.7-1.6L13.4 2h-2.8l-.4 2.9a7 7 0 0 0-2.7 1.6l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .5.1 1.1.2 1.6l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2.7 1.6l.4 2.9h2.8l.4-2.9a7 7 0 0 0 2.7-1.6l2.3 1 2-3.4-2-1.5c.1-.5.2-1 .2-1.6z" />
      </>
    ),
    bolt: <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" />,
    search: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-4-4" />
      </>
    ),
    check: <path d="m4 12.5 5 5L20 6.5" />,
    book: <path d="M4 5a2 2 0 0 1 2-2h14v18H6a2 2 0 0 0-2 2V5zM20 17H6a2 2 0 0 0-2 2" />,
    x: <path d="M5 5l14 14M19 5 5 19" />,
    plus: <path d="M12 5v14M5 12h14" />,
    refresh: <path d="M20 11A8 8 0 1 0 18.9 15M20 4v7h-7" />,
    moon: <path d="M20 14.5A8 8 0 1 1 9.5 4 6.5 6.5 0 0 0 20 14.5z" />,
    sun: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </>
    ),
    graph: (
      <>
        <circle cx="5" cy="12" r="2.6" />
        <circle cx="18.5" cy="5.5" r="2.6" />
        <circle cx="18.5" cy="18.5" r="2.6" />
        <path d="M7.4 10.9 16.1 6.6M7.4 13.1l8.7 4.3" />
      </>
    ),
    copy: (
      <>
        <rect x="9" y="9" width="11" height="11" rx="2" />
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
      </>
    ),
    chat: (
      <>
        <path d="M21 12a8 8 0 0 1-8 8H4l1.7-3.4A8 8 0 1 1 21 12z" />
        <path d="M8.5 11h.01M12 11h.01M15.5 11h.01" />
      </>
    ),
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </>
    ),
    up: <path d="M6 15l6-6 6 6" />,
    down: <path d="M6 9l6 6 6-6" />,
    wide: (
      <>
        <rect x="3" y="6" width="18" height="12" rx="1.5" />
        <path d="M8 6v12M16 6v12" />
      </>
    ),
    narrow: (
      <>
        <rect x="3" y="6" width="18" height="12" rx="1.5" />
        <path d="M12 6v12" />
      </>
    ),
    rows: <path d="M4 6h16M4 12h16M4 18h16" />,
    // Personalization (#59): outline + filled star for favorites toggles.
    star: <path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8L12 3.6z" />,
    "star-filled": (
      <path
        d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8L12 3.6z"
        fill="currentColor"
      />
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {paths[name] ?? <circle cx="12" cy="12" r="8" />}
    </svg>
  );
}

export function Breadcrumbs({ items }: { items: { label: string; to?: string }[] }) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      {items.map((item, i) => (
        <Fragment key={i}>
          {i > 0 && <span className="crumb-sep" aria-hidden="true">/</span>}
          {item.to ? (
            <Link to={item.to} className="crumb">
              {item.label}
            </Link>
          ) : (
            <span className="crumb current" aria-current="page">
              {item.label}
            </span>
          )}
        </Fragment>
      ))}
    </nav>
  );
}

/** Semantic status tone. Backgrounds/foregrounds live in styles.css (.pill.tone-*),
 *  defined once for light and once for dark — so a status's meaning is centralized
 *  here instead of scattered across ~20 per-status CSS selectors. */
type Tone = "ok" | "warn" | "danger" | "info" | "neutral" | "accent";

/** Maps every status string used across the app to one of six tones. Unknown
 *  values fall back to "neutral" (rather than rendering unstyled). */
const STATUS_TONES: Record<string, Tone> = {
  // run + check last_status
  pass: "ok", warn: "warn", fail: "danger", error: "danger",
  // check lifecycle
  proposed: "accent", active: "ok", disabled: "neutral", archived: "neutral",
  // exception lifecycle
  open: "danger", acknowledged: "warn", expected: "info", resolved: "ok", muted: "neutral",
  // misc statuses used across pages
  running: "info", complete: "ok", failed: "danger", unknown: "neutral",
  review: "accent", approved: "ok", monitoring: "info", dismissed: "neutral",
};

/** Status chip with centralized tone mapping. Always renders the value as a text
 *  label (never color-only) so it stays legible for color-vision-deficient users. */
export function StatusPill({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="pill tone-neutral">—</span>;
  const tone = STATUS_TONES[value] ?? "neutral";
  return <span className={`pill tone-${tone}`}>{value}</span>;
}

/** Outlined severity chip (info/warn/error). Keeps the word, not just a color. */
export function SeverityBadge({ severity }: { severity: string }) {
  const tone = severity === "error" ? "danger" : severity === "warn" ? "warn" : "info";
  return <span className={`pill tone-${tone} pill-outline`}>{severity}</span>;
}

/** @deprecated Use {@link StatusPill}. Kept as an alias while call sites migrate. */
export const Pill = StatusPill;

export function SeverityDot({ severity }: { severity: string }) {
  return (
    <span title={severity}>
      <span className={`sev ${severity}`} />
      {severity}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="center">
      <span className="spinner" />
      {label ?? "Loading…"}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <div className="error-box">{message}</div>;
}

export function EmptyState({ title, hint, children }: { title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="big">{title}</div>
      {hint && <div>{hint}</div>}
      {children && <div style={{ marginTop: 14 }}>{children}</div>}
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
  dirty,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  /** When true, closing via the backdrop or ✕ asks before discarding edits. */
  dirty?: boolean;
}) {
  const requestClose = () => {
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    onClose();
  };
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && requestClose()}>
      <div className={`modal${wide ? " wide" : ""}`}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="ghost small" onClick={requestClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "ok" | "danger";
}) {
  return (
    <div className="card stat-card">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}
