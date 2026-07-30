"""Custom dashboards: user-composed, cross-dataset "my morning screen" (issue #67).

The user-composed sibling of the per-dataset ad-hoc dashboards
(``api/adhoc_dashboards.py``). An analyst hand-picks widgets, optionally shares
the board with the team, and can set it as their landing page. The authoritative
widget JSON contract lives in ``schemas.py`` (``Widget`` union) and is mirrored
in ``frontend/src/api/types.ts``.

RBAC semantics (document, don't reinvent — epic standard #3):
  * **Live widgets** (metric/exceptions/checks) resolve client-side through the
    EXISTING read endpoints (``GET /exceptions``, ``GET /checks``) with the
    stored params, executed as the *viewing* user. A shared (team) dashboard is
    therefore shared *configuration*, NOT shared data authority: each viewer
    sees only what their role/grants allow. A widget's count is the same number
    the triage queue shows for the same filters — never a parallel counting path.
  * **SQL snapshots** are the one exception: server-executed, persisted results
    captured with the *refresher's* authority (same posture as ad-hoc boards).
    They always carry ``refreshed_at`` (UTC) so the UI labels freshness honestly.
    Because that authority is baked into the stored rows, the snapshot is gated
    twice against the per-connection grant model (#72): **writing** it requires
    editor ON the widget's connection (checked per widget, so one ungranted
    widget can't block the rest), and **reading** a board redacts the rows of any
    widget whose connection the viewer can't see. A shared board must never
    become a side channel for data from a source you were not granted.

Quota policy (per-tenant quotas — multi-tenancy track — will hook into these
caps): <=12 widgets, 200 rows per snapshot, the params allowlist.
"""

import copy
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import custom_dashboard_meta
from app.connectors.sa import connector_for
from app.connectors.safety import SqlNotAllowed, guard_sql
from app.core.profiler import jsonable
from app.db import get_db
from app.models import utcnow
from app.schemas import SNAPSHOT_ROW_CAP
from app.security import (
    ROLE_RANK,
    assert_connection_role,
    connection_role,
    get_current_user,
    require_role,
    visible_connection_ids,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboards/custom", tags=["custom-dashboards"])


# ---- helpers ----------------------------------------------------------------
def _is_editor(user: models.User) -> bool:
    return ROLE_RANK.get(user.role, -1) >= ROLE_RANK["editor"]


def _is_admin(user: models.User) -> bool:
    return ROLE_RANK.get(user.role, -1) >= ROLE_RANK["admin"]


def _can_view(dash: models.CustomDashboard, user: models.User) -> bool:
    return dash.visibility == "team" or dash.owner_id == user.id or _is_admin(user)


def _can_edit(dash: models.CustomDashboard, user: models.User) -> bool:
    return dash.owner_id == user.id or _is_admin(user)


def _get_viewable(db: Session, dashboard_id: int, user: models.User) -> models.CustomDashboard:
    """Fetch a dashboard the user may view, else 404. We 404 (not 403) for
    private dashboards you don't own so existence isn't leaked."""
    dash = db.get(models.CustomDashboard, dashboard_id)
    if dash is None or not _can_view(dash, user):
        raise HTTPException(404, "Dashboard not found")
    return dash


def _strip_snapshots(layout: schemas.DashboardLayout) -> dict:
    """Serialize a validated layout for storage, dropping any client-sent
    ``snapshot`` on sql widgets — snapshots are server-owned (written only by
    /refresh). Keeps an existing server snapshot only when merged in explicitly."""
    data = layout.model_dump(mode="json")
    for w in data.get("widgets", []):
        if w.get("type") == "sql":
            w.pop("snapshot", None)
    return data


def _sql_widget_configs(widgets: list[dict]) -> dict[str, tuple]:
    """Map widget id -> (sql, connection_id) for the sql widgets in a stored layout."""
    out: dict[str, tuple] = {}
    for w in widgets:
        if w.get("type") == "sql":
            cfg = w.get("config") or {}
            out[w.get("id")] = (cfg.get("sql"), cfg.get("connection_id"))
    return out


def _validate_for_role(
    layout: schemas.DashboardLayout,
    user: models.User,
    existing_sql: dict[str, tuple] | None = None,
) -> None:
    """Adding or changing a sql widget requires editor. A viewer who owns a board with
    pre-existing sql widgets (e.g. a duplicated team board, #A10) may still move/keep/
    remove them — only NEW or MODIFIED sql widgets are gated, so editing the layout
    isn't blocked outright by widgets they didn't author."""
    if _is_editor(user):
        return
    existing_sql = existing_sql or {}
    for w in layout.widgets:
        if w.type != "sql":
            continue
        if existing_sql.get(w.id) != (w.config.sql, w.config.connection_id):
            raise HTTPException(422, "Adding or editing SQL widgets requires the editor role")


def _validate_sql_widgets(
    db: Session,
    layout: schemas.DashboardLayout,
    user: models.User,
    existing_sql: dict[str, tuple] | None = None,
) -> None:
    """guard_sql() each sql widget at save time (#41 pattern); a NEW or MODIFIED
    widget must additionally point at a connection the author may run SQL on (#72).

    Only new/modified widgets are connection-checked, matching ``_validate_for_role``:
    a widget inherited unchanged from a duplicated team board (#A10) can be moved or
    removed but can never execute — ``/refresh`` re-checks every widget against the
    refresher's grants — so blocking the whole save would destroy layout editing for
    no security gain.

    A connection that does not exist and one the author cannot see produce the SAME
    422, so a save can't be used to probe which connection ids exist.
    """
    existing_sql = existing_sql or {}
    for w in layout.widgets:
        if not isinstance(w, schemas.SqlWidget):
            continue
        cid = w.config.connection_id
        if existing_sql.get(w.id) != (w.config.sql, cid):
            role = connection_role(db, user, cid)
            if role is None:  # missing OR invisible — deliberately indistinguishable
                raise HTTPException(422, f"Connection {cid} not found for widget '{w.title}'")
            if ROLE_RANK.get(role, -1) < ROLE_RANK["editor"]:
                raise HTTPException(
                    422,
                    f"Widget '{w.title}': editor access on connection {cid} is required",
                )
        try:
            guard_sql(w.config.sql)
        except SqlNotAllowed as exc:
            raise HTTPException(422, f"Widget '{w.title}': {exc}") from exc


def _readable_layout(db: Session, dash: models.CustomDashboard, user: models.User) -> dict:
    """The stored layout with the snapshot ROWS of any sql widget on a connection
    the caller can't see blanked out (#72).

    A snapshot holds source rows captured under the *refresher's* grants; serving
    them to a viewer of a shared board would hand out data from a connection they
    were never granted. The widget itself stays in the layout with an honest reason
    in ``snapshot.error``, so the board keeps its shape instead of silently losing
    a tile. Deep-copied: this must never write back to the stored JSON.
    """
    layout = copy.deepcopy(dash.layout or {"version": 1, "widgets": []})
    vis = visible_connection_ids(db, user)  # None -> unrestricted (admin / zero-grant)
    if vis is None:
        return layout
    for w in layout.get("widgets", []):
        if w.get("type") != "sql" or not w.get("snapshot"):
            continue
        if (w.get("config") or {}).get("connection_id") in vis:
            continue
        w["snapshot"] = {
            **w["snapshot"],
            "columns": [],
            "rows": [],
            "error": "Hidden: you don't have access to this widget's connection",
        }
    return layout


def _out(db: Session, dash: models.CustomDashboard, user: models.User) -> schemas.CustomDashboardOut:
    out = schemas.CustomDashboardOut(**custom_dashboard_meta(db, dash).model_dump())
    out.layout = schemas.DashboardLayout.model_validate(_readable_layout(db, dash, user))
    out.can_edit = _can_edit(dash, user)
    return out


# ---- SQL snapshot runner ----------------------------------------------------
NOT_ALLOWED_SNAPSHOT_ERROR = "Not allowed: this widget needs editor access on its connection"


# TODO(#42): converge with core/dashboards.py once the scheduled-refresh helper
# lands. #42's dashboard-claim loop should ALSO claim custom dashboards that have
# sql widgets and call this same runner so scheduled and manual refresh share one
# guarded path. Until then this is the small local runner.
def _refresh_sql_widget(
    db: Session, user: models.User, cfg: schemas.SqlWidgetConfig
) -> schemas.WidgetSnapshot | None:
    """Execute one sql widget through the SAME guarded path as the workbench
    (guard_sql + connector.run_select, row cap 200) and under the SAME authority:
    editor ON that widget's connection, exactly like POST /query/run (#72). A
    stored widget must not become a way to point server-side execution at a source
    the refresher was never granted — the board's global editor gate says nothing
    about *which* connections this user may reach.

    Returns ``None`` when the refresher may not run SQL on this widget's connection
    (missing, invisible and under-roled are deliberately indistinguishable, so a
    snapshot can't become an oracle for connection ids). The caller then leaves the
    stored snapshot alone. Everything else is captured in ``snapshot.error`` — a
    per-widget failure NEVER fails the enclosing request."""
    try:
        assert_connection_role(db, user, cfg.connection_id, "editor")
    except HTTPException:
        return None
    start = time.perf_counter()
    columns: list[str] = []
    rows: list[list] = []
    error: str | None = None
    try:
        conn = db.get(models.Connection, cfg.connection_id)  # exists: the gate 404s otherwise
        connector = connector_for(conn)
        res = connector.run_select(cfg.sql, limit=SNAPSHOT_ROW_CAP)
        columns = res.columns
        rows = [[jsonable(v) for v in row] for row in res.rows]
    except Exception as exc:  # noqa: BLE001 - per-widget failure isolation
        error = f"{type(exc).__name__}: {exc}"
    return schemas.WidgetSnapshot(
        columns=columns,
        rows=rows,
        refreshed_at=utcnow(),
        error=error,
        elapsed_ms=int((time.perf_counter() - start) * 1000),
    )


# ---- endpoints --------------------------------------------------------------
@router.get("", response_model=list[schemas.CustomDashboardMeta])
def list_dashboards(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Own dashboards + every ``visibility="team"`` one. Any authenticated user.
    Returns metas (no layout) so the list page stays cheap."""
    rows = (
        db.query(models.CustomDashboard)
        .filter(
            or_(
                models.CustomDashboard.owner_id == user.id,
                models.CustomDashboard.visibility == "team",
            )
        )
        .order_by(models.CustomDashboard.updated_at.desc(), models.CustomDashboard.id.desc())
        .all()
    )
    owners = {u.id: u for u in db.query(models.User).all()}
    return [custom_dashboard_meta(db, d, owners.get(d.owner_id)) for d in rows]


@router.get("/{dashboard_id}", response_model=schemas.CustomDashboardOut)
def get_dashboard(
    dashboard_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    dash = _get_viewable(db, dashboard_id, user)
    return _out(db, dash, user)


@router.post("", response_model=schemas.CustomDashboardOut, status_code=201)
def create_dashboard(
    body: schemas.CustomDashboardCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Any authenticated user (viewers curate read-only dashboards too), BUT a
    layout containing a ``sql`` widget requires editor (422)."""
    _validate_for_role(body.layout, user)
    _validate_sql_widgets(db, body.layout, user)
    dash = models.CustomDashboard(
        name=body.name,
        description=body.description,
        owner_id=user.id,
        visibility=body.visibility,
        layout=_strip_snapshots(body.layout),
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return _out(db, dash, user)


@router.patch("/{dashboard_id}", response_model=schemas.CustomDashboardOut)
def update_dashboard(
    dashboard_id: int,
    body: schemas.CustomDashboardUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Owner or admin only. Full-layout replace (no widget-level PATCH). Client-
    sent ``snapshot`` fields are stripped (server-owned). ``owner_id`` reassignment
    is admin-only (offboarding: an admin reassigns an inactive user's dashboards).

    TODO(#30): emit a ``dashboard.share`` / visibility-change audit event here
    once the audit log lands — sharing scope is an auditable action.
    """
    dash = db.get(models.CustomDashboard, dashboard_id)
    if dash is None:
        raise HTTPException(404, "Dashboard not found")
    if not _can_edit(dash, user):
        # Owner/admin gate. For a private board you can't even see, prefer 404.
        raise HTTPException(404 if not _can_view(dash, user) else 403, "Not allowed to edit this dashboard")

    if body.layout is not None:
        existing_widgets = (dash.layout or {}).get("widgets", [])
        existing_sql = _sql_widget_configs(existing_widgets)
        _validate_for_role(body.layout, user, existing_sql)
        _validate_sql_widgets(db, body.layout, user, existing_sql)
        # Preserve existing server snapshots across a metadata/layout edit: the UI
        # round-trips snapshots back, but we never trust the client copy — re-attach
        # ours by widget id ONLY where the sql config is unchanged. Re-attaching by id
        # alone would keep a stale result under a changed query, mislabeling data as
        # something it isn't until the next refresh (#A9).
        prior = {
            w.get("id"): w.get("snapshot")
            for w in existing_widgets
            if w.get("type") == "sql" and w.get("snapshot")
        }
        new_layout = _strip_snapshots(body.layout)
        for w in new_layout["widgets"]:
            if w.get("type") == "sql" and w["id"] in prior:
                cfg = w.get("config") or {}
                if existing_sql.get(w["id"]) == (cfg.get("sql"), cfg.get("connection_id")):
                    w["snapshot"] = prior[w["id"]]
        dash.layout = new_layout
    if body.name is not None:
        dash.name = body.name
    if body.description is not None:
        dash.description = body.description
    if body.visibility is not None:
        dash.visibility = body.visibility
    if body.owner_id is not None:
        if not _is_admin(user):
            raise HTTPException(403, "Only an admin can reassign dashboard ownership")
        if db.get(models.User, body.owner_id) is None:
            raise HTTPException(422, "New owner not found")
        dash.owner_id = body.owner_id

    db.commit()
    db.refresh(dash)
    return _out(db, dash, user)


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(
    dashboard_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Owner or admin."""
    dash = db.get(models.CustomDashboard, dashboard_id)
    if dash is None:
        raise HTTPException(404, "Dashboard not found")
    if not _can_edit(dash, user):
        raise HTTPException(404 if not _can_view(dash, user) else 403, "Not allowed to delete this dashboard")
    db.delete(dash)
    db.commit()


@router.post("/{dashboard_id}/duplicate", response_model=schemas.CustomDashboardOut, status_code=201)
def duplicate_dashboard(
    dashboard_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Any user who can view it. The copy becomes ``private``, owned by the caller,
    with all snapshots cleared. This is how team templates propagate."""
    src = _get_viewable(db, dashboard_id, user)
    layout = schemas.DashboardLayout.model_validate(src.layout or {"version": 1, "widgets": []})
    # A viewer may duplicate a board containing sql widgets (they own the copy and
    # can't refresh it), so don't role-gate the type here — only refresh is gated.
    dash = models.CustomDashboard(
        name=f"{src.name} (copy)"[:255],
        description=src.description,
        owner_id=user.id,
        visibility="private",
        layout=_strip_snapshots(layout),  # snapshots cleared
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return _out(db, dash, user)


@router.post("/{dashboard_id}/refresh", response_model=schemas.CustomDashboardOut)
def refresh_dashboard(
    dashboard_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    """Editor. Execute each ``sql`` widget through the guarded path and stamp its
    snapshot (rows + ``refreshed_at``, row cap 200). A broken query lands in that
    widget's ``snapshot.error`` and never fails the request; the other widgets
    still refresh. Requires view access to the dashboard.

    Widgets on connections this refresher may not run SQL on are SKIPPED, not
    errored-over: overwriting a snapshot an authorized editor captured would
    destroy shared analyst state on a team board (#72)."""
    dash = _get_viewable(db, dashboard_id, user)
    layout = dict(dash.layout or {"version": 1, "widgets": []})
    widgets = layout.get("widgets", [])
    for w in widgets:
        if w.get("type") != "sql":
            continue
        try:
            cfg = schemas.SqlWidgetConfig.model_validate(w.get("config") or {})
        except Exception as exc:  # noqa: BLE001 - a malformed stored widget shouldn't 500
            w["snapshot"] = schemas.WidgetSnapshot(
                refreshed_at=utcnow(), error=f"Invalid widget config: {exc}"
            ).model_dump(mode="json")
            continue
        snap = _refresh_sql_widget(db, user, cfg)
        if snap is None:  # not authorized on this widget's connection
            if not w.get("snapshot"):  # nothing to preserve -> label it honestly
                w["snapshot"] = schemas.WidgetSnapshot(
                    refreshed_at=utcnow(), error=NOT_ALLOWED_SNAPSHOT_ERROR
                ).model_dump(mode="json")
            continue
        w["snapshot"] = snap.model_dump(mode="json")
    dash.layout = layout
    # mutating a nested JSON dict in place isn't always seen as dirty — flag it
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(dash, "layout")
    db.commit()
    db.refresh(dash)
    return _out(db, dash, user)
