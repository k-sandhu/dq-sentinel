import { Component, type ReactNode } from "react";
import type { Widget } from "../../api/types";
import { Icon } from "../ui";

/** Per-widget error boundary: one widget throwing must never blank the whole
 *  dashboard. Renders a compact in-frame message instead. */
class WidgetErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return <div className="error-box cd-widget-crash">This widget failed to render.</div>;
    }
    return this.props.children;
  }
}

/** A dashboard widget shell: a labelled <section>, a title bar, an optional
 *  "snapshot" badge, and (in edit mode) reorder / span / config / remove
 *  controls. The body is wrapped in an error boundary so one dead widget can't
 *  take down the page. */
export default function WidgetFrame({
  widget,
  editing,
  isFirst,
  isLast,
  badge,
  onMoveUp,
  onMoveDown,
  onToggleSpan,
  onConfigure,
  onRemove,
  children,
}: {
  widget: Widget;
  editing: boolean;
  isFirst: boolean;
  isLast: boolean;
  badge?: ReactNode;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onToggleSpan: () => void;
  onConfigure: () => void;
  onRemove: () => void;
  children: ReactNode;
}) {
  return (
    <section
      className={`card cd-widget cd-span-${widget.span}`}
      aria-label={widget.title}
    >
      <div className="cd-widget-head">
        <div className="cd-widget-title" title={widget.title}>
          {widget.title}
          {badge}
        </div>
        {editing && (
          <div className="cd-widget-controls">
            <button
              type="button"
              className="small icon-only ghost"
              onClick={onMoveUp}
              disabled={isFirst}
              aria-label={`Move ${widget.title} up`}
              title="Move up"
            >
              <Icon name="up" size={14} />
            </button>
            <button
              type="button"
              className="small icon-only ghost"
              onClick={onMoveDown}
              disabled={isLast}
              aria-label={`Move ${widget.title} down`}
              title="Move down"
            >
              <Icon name="down" size={14} />
            </button>
            <button
              type="button"
              className="small icon-only ghost"
              onClick={onToggleSpan}
              aria-label={`Toggle width of ${widget.title} (currently ${widget.span} column${widget.span === 2 ? "s" : ""})`}
              title={widget.span === 2 ? "Make narrow (1 column)" : "Make wide (2 columns)"}
            >
              <Icon name={widget.span === 2 ? "narrow" : "wide"} size={14} />
            </button>
            <button
              type="button"
              className="small icon-only ghost"
              onClick={onConfigure}
              aria-label={`Configure ${widget.title}`}
              title="Configure"
            >
              <Icon name="settings" size={14} />
            </button>
            <button
              type="button"
              className="small icon-only ghost cd-remove"
              onClick={onRemove}
              aria-label={`Remove ${widget.title}`}
              title="Remove"
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        )}
      </div>
      <div className="cd-widget-body">
        <WidgetErrorBoundary>{children}</WidgetErrorBoundary>
      </div>
    </section>
  );
}
