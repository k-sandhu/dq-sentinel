import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { api, ApiError } from "../api/client";
import type {
  CustomDashboard,
  DashboardLayout,
  Visibility,
  Widget,
  WidgetType,
} from "../api/types";
import ChecksWidget from "../components/dashboards/ChecksWidget";
import ExceptionsWidget from "../components/dashboards/ExceptionsWidget";
import MetricWidget from "../components/dashboards/MetricWidget";
import NoteWidget from "../components/dashboards/NoteWidget";
import SqlWidget from "../components/dashboards/SqlWidget";
import WidgetConfigModal, { defaultWidget } from "../components/dashboards/WidgetConfigModal";
import WidgetFrame from "../components/dashboards/WidgetFrame";
import WidgetGrid from "../components/dashboards/WidgetGrid";
import { useAuth } from "../auth";
import { EmptyState, ErrorBox, Icon, Modal, Spinner } from "../components/ui";
import { timeAgo } from "../lib/format";
import { clearLanding, getLanding, setLanding } from "../lib/prefs";

const WIDGET_TYPES: { type: WidgetType; label: string; desc: string }[] = [
  { type: "metric", label: "Metric", desc: "A single count from the exceptions queue, with tone thresholds." },
  { type: "exceptions", label: "Exceptions list", desc: "The most recent matching exceptions, linking into triage." },
  { type: "checks", label: "Checks", desc: "Active checks for chosen datasets; optionally only failing." },
  { type: "sql", label: "SQL snapshot", desc: "A read-only query rendered as a chart, refreshed on demand." },
  { type: "note", label: "Note", desc: "Markdown — runbook links, context, reminders." },
];

function genId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `w_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** One dashboard: view mode by default; owner/admin can enter builder mode to
 *  reorder / configure / add / remove widgets, edit metadata, share, duplicate,
 *  delete, or set it as their landing page. Edit state is local and saved as a
 *  full-layout PATCH; a dirty guard warns before losing unsaved changes. */
export default function CustomDashboardPage() {
  const { id } = useParams();
  const dashboardId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const { data: dash, isLoading, error } = useQuery({
    queryKey: ["custom-dashboard", dashboardId],
    queryFn: () => api.get<CustomDashboard>(`/dashboards/custom/${dashboardId}`),
    enabled: Number.isFinite(dashboardId),
  });

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<DashboardLayout | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [picking, setPicking] = useState(false);
  const [configuring, setConfiguring] = useState<{ widget: Widget; isNew: boolean } | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [isLanding, setIsLanding] = useState(() => getLanding() === `/dashboards/${dashboardId}`);

  // Enter builder when ?edit=1 and the user may edit (e.g. straight after create).
  useEffect(() => {
    if (dash?.can_edit && searchParams.get("edit") === "1") {
      beginEdit();
      const next = new URLSearchParams(searchParams);
      next.delete("edit");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dash]);

  const dirty = useMemo(() => {
    if (!editing || !dash || !draft) return false;
    return (
      JSON.stringify(draft) !== JSON.stringify(dash.layout) ||
      name !== dash.name ||
      description !== dash.description ||
      visibility !== dash.visibility
    );
  }, [editing, draft, dash, name, description, visibility]);

  // Warn on tab close / reload with unsaved changes.
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  function beginEdit() {
    if (!dash) return;
    setDraft(JSON.parse(JSON.stringify(dash.layout)) as DashboardLayout);
    setName(dash.name);
    setDescription(dash.description);
    setVisibility(dash.visibility);
    setSaveError(null);
    setEditing(true);
  }

  function cancelEdit() {
    if (dirty && !window.confirm("Discard unsaved changes to this dashboard?")) return;
    setEditing(false);
    setDraft(null);
  }

  const save = useMutation({
    mutationFn: () =>
      api.patch<CustomDashboard>(`/dashboards/custom/${dashboardId}`, {
        name,
        description,
        visibility,
        layout: draft,
      }),
    onSuccess: (updated) => {
      qc.setQueryData(["custom-dashboard", dashboardId], updated);
      qc.invalidateQueries({ queryKey: ["custom-dashboards"] });
      setEditing(false);
      setDraft(null);
      setSaveError(null);
    },
    onError: (err) => setSaveError(err instanceof ApiError ? err.message : String(err)),
  });

  const duplicate = useMutation({
    mutationFn: () => api.post<CustomDashboard>(`/dashboards/custom/${dashboardId}/duplicate`),
    onSuccess: (copy) => {
      qc.invalidateQueries({ queryKey: ["custom-dashboards"] });
      navigate(`/dashboards/${copy.id}?edit=1`);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.del<void>(`/dashboards/custom/${dashboardId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["custom-dashboards"] });
      // if this was the landing page, clear that pref so we don't loop into a 404
      if (getLanding() === `/dashboards/${dashboardId}`) clearLanding();
      navigate("/dashboards");
    },
  });

  if (error) {
    return (
      <div className="page">
        <ErrorBox error={error} />
        <button className="ghost" onClick={() => navigate("/dashboards")}>
          ← All dashboards
        </button>
      </div>
    );
  }
  if (isLoading || !dash) return <Spinner label="Loading dashboard…" />;

  // In edit mode we render `draft`; in view mode the server layout.
  const layout = editing && draft ? draft : dash.layout;
  const widgets = layout.widgets;
  const canRefresh = user?.role === "editor" || user?.role === "admin";

  function mutateWidgets(fn: (ws: Widget[]) => Widget[]) {
    setDraft((d) => (d ? { ...d, widgets: fn(d.widgets) } : d));
  }
  function move(index: number, delta: number) {
    mutateWidgets((ws) => {
      const next = [...ws];
      const j = index + delta;
      if (j < 0 || j >= next.length) return ws;
      [next[index], next[j]] = [next[j], next[index]];
      return next;
    });
  }
  function toggleSpan(index: number) {
    mutateWidgets((ws) => ws.map((w, i) => (i === index ? { ...w, span: w.span === 2 ? 1 : 2 } : w)));
  }
  function removeWidget(index: number) {
    mutateWidgets((ws) => ws.filter((_, i) => i !== index));
  }
  function addWidget(type: WidgetType) {
    setPicking(false);
    setConfiguring({ widget: defaultWidget(type, genId()), isNew: true });
  }
  function saveWidget(w: Widget) {
    if (!configuring) return;
    if (configuring.isNew) mutateWidgets((ws) => [...ws, w]);
    else mutateWidgets((ws) => ws.map((x) => (x.id === w.id ? w : x)));
    setConfiguring(null);
  }

  function renderWidgetBody(w: Widget) {
    switch (w.type) {
      case "metric":
        return <MetricWidget widget={w} />;
      case "exceptions":
        return <ExceptionsWidget widget={w} />;
      case "checks":
        return <ChecksWidget widget={w} />;
      case "sql":
        return <SqlWidget widget={w} dashboardId={dashboardId} canRefresh={canRefresh && !editing} />;
      case "note":
        return <NoteWidget widget={w} />;
    }
  }

  function toggleLanding() {
    if (isLanding) {
      clearLanding();
      setIsLanding(false);
    } else {
      setLanding(`/dashboards/${dashboardId}`);
      setIsLanding(true);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="cd-breadcrumb">
            <button className="link-btn" onClick={() => navigate("/dashboards")}>
              Dashboards
            </button>{" "}
            / {dash.name}
          </div>
          {editing ? (
            <input
              className="cd-name-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-label="Dashboard name"
              maxLength={255}
            />
          ) : (
            <h1>{dash.name}</h1>
          )}
          <div className="sub">
            {editing ? (
              <input
                className="cd-desc-input"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Description (optional)"
                aria-label="Dashboard description"
              />
            ) : (
              <>
                {dash.description && <span>{dash.description} · </span>}
                <span title={dash.owner_active ? undefined : "Owner is inactive"}>
                  {dash.owner_name}
                  {!dash.owner_active && " (inactive)"}
                </span>{" "}
                · <span className={`pill ${dash.visibility === "team" ? "ok" : "unknown"}`}>{dash.visibility}</span>
              </>
            )}
          </div>
        </div>
        <div className="header-actions cd-actions">
          {!editing && (
            <>
              <button className="ghost" onClick={() => duplicate.mutate()} disabled={duplicate.isPending} title="Make a private copy">
                <Icon name="copy" size={14} /> Duplicate
              </button>
              <button className="ghost" onClick={toggleLanding} title="Open this dashboard when DQ Sentinel starts">
                <Icon name="home" size={14} /> {isLanding ? "Landing page ✓" : "Set as my landing page"}
              </button>
              {dash.can_edit && (
                <button className="primary" onClick={beginEdit}>
                  <Icon name="settings" size={14} /> Edit
                </button>
              )}
            </>
          )}
          {editing && (
            <>
              <button className="ghost" onClick={cancelEdit}>
                Cancel
              </button>
              <button className="primary" onClick={() => save.mutate()} disabled={save.isPending || !dirty}>
                {save.isPending ? "Saving…" : "Save"}
              </button>
            </>
          )}
        </div>
      </div>

      {duplicate.isError && <ErrorBox error={duplicate.error} />}
      {remove.isError && <ErrorBox error={remove.error} />}
      {saveError && <div className="error-box">{saveError}</div>}

      {editing && (
        <div className="cd-builder-bar">
          <button className="primary small" onClick={() => setPicking(true)} disabled={widgets.length >= 12}>
            <Icon name="plus" size={13} /> Add widget
          </button>
          <span className="cd-builder-count">{widgets.length} / 12 widgets</span>
          <label className="cd-vis-toggle">
            <span>Visibility</span>
            <select value={visibility} onChange={(e) => setVisibility(e.target.value as Visibility)}>
              <option value="private">Private — only you</option>
              <option value="team">Team — anyone can view</option>
            </select>
            <span className="cd-hint">{visibility === "team" ? "Team: anyone can view; only you can edit." : "Private: only you can see this."}</span>
          </label>
          <div className="spacer" />
          <button className="ghost small cd-danger-btn" onClick={() => setConfirmDelete(true)}>
            <Icon name="x" size={13} /> Delete dashboard
          </button>
        </div>
      )}

      {widgets.length === 0 ? (
        <EmptyState
          title="This dashboard is empty"
          hint={dash.can_edit ? "Add widgets to build your screen." : "The owner hasn't added any widgets yet."}
        >
          {dash.can_edit && !editing && (
            <button className="primary" onClick={beginEdit}>
              <Icon name="settings" size={14} /> Edit
            </button>
          )}
          {editing && (
            <button className="primary" onClick={() => setPicking(true)}>
              <Icon name="plus" size={14} /> Add widget
            </button>
          )}
        </EmptyState>
      ) : (
        <WidgetGrid>
          {widgets.map((w, i) => (
            <WidgetFrame
              key={w.id}
              widget={w}
              editing={editing}
              isFirst={i === 0}
              isLast={i === widgets.length - 1}
              badge={w.type === "sql" ? <span className="cd-snap-badge" title="Server-side snapshot">snapshot</span> : undefined}
              onMoveUp={() => move(i, -1)}
              onMoveDown={() => move(i, 1)}
              onToggleSpan={() => toggleSpan(i)}
              onConfigure={() => setConfiguring({ widget: w, isNew: false })}
              onRemove={() => removeWidget(i)}
            >
              {renderWidgetBody(w)}
            </WidgetFrame>
          ))}
        </WidgetGrid>
      )}

      {picking && (
        <Modal title="Add a widget" onClose={() => setPicking(false)}>
          <div className="cd-type-picker">
            {WIDGET_TYPES.map((t) => {
              const disabled = t.type === "sql" && !canRefresh;
              return (
                <button
                  type="button"
                  key={t.type}
                  className="cd-type-card"
                  disabled={disabled}
                  onClick={() => addWidget(t.type)}
                  title={disabled ? "SQL widgets require the editor role" : undefined}
                >
                  <span className="cd-type-name">{t.label}</span>
                  <span className="cd-type-desc">{disabled ? "Requires editor role" : t.desc}</span>
                </button>
              );
            })}
          </div>
        </Modal>
      )}

      {configuring && (
        <WidgetConfigModal
          initial={configuring.widget}
          saveError={saveError}
          onSave={saveWidget}
          onClose={() => setConfiguring(null)}
        />
      )}

      {confirmDelete && (
        <Modal
          title="Delete dashboard?"
          onClose={() => setConfirmDelete(false)}
          footer={
            <>
              <button className="ghost" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
              <button className="primary cd-danger-btn" onClick={() => remove.mutate()} disabled={remove.isPending}>
                {remove.isPending ? "Deleting…" : "Delete"}
              </button>
            </>
          }
        >
          <p>
            Permanently delete <strong>{dash.name}</strong>? This cannot be undone.
            {dash.visibility === "team" && " Team members will lose access to it."}
          </p>
        </Modal>
      )}

      {!editing && <div className="cd-foot-note">Updated {timeAgo(dash.updated_at)}</div>}
    </div>
  );
}
