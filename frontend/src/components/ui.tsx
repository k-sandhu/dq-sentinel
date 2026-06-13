import type { ReactNode } from "react";

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
    rows: (
      <>
        <path d="M4 7h16M4 12h16M4 17h16" />
        <path d="M7 5v4M7 10v4M7 15v4" />
      </>
    ),
    star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9L12 3z" />,
    "star-filled": (
      <path
        d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9L12 3z"
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

type Tone = "ok" | "warn" | "danger" | "info" | "neutral" | "accent";

const STATUS_TONES: Record<string, Tone> = {
  pass: "ok",
  warn: "warn",
  fail: "danger",
  error: "danger",
  proposed: "accent",
  active: "ok",
  disabled: "neutral",
  archived: "neutral",
  open: "danger",
  acknowledged: "warn",
  expected: "info",
  resolved: "ok",
  muted: "neutral",
  running: "info",
  complete: "ok",
  failed: "danger",
  unknown: "neutral",
  review: "accent",
  approved: "ok",
  monitoring: "info",
  dismissed: "neutral",
};

export function StatusPill({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="pill tone-neutral">-</span>;
  const tone = STATUS_TONES[value] ?? "neutral";
  const className = `pill tone-${tone}`;
  return <span className={className}>{value}</span>;
}

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  const label = severity || "info";
  const tone = label === "error" ? "danger" : label === "warn" ? "warn" : "info";
  const className = `pill tone-${tone} pill-outline`;
  return <span className={className}>{label}</span>;
}

export function Pill({ value }: { value: string | null | undefined }) {
  return <StatusPill value={value} />;
}

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
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? " wide" : ""}`}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="ghost small" onClick={onClose} aria-label="Close">
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
