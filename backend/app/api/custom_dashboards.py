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

Quota policy (per-tenant quotas — multi-tenancy track — will hook into these
caps): <=12 widgets, 200 rows per snapshot, the params allowlist.
"""

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
from app.security import ROLE_RANK, get_current_user, require_role

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


def _validate_for_role(layout: schemas.DashboardLayout, user: models.User) -> None:
    """sql widgets require editor (422 otherwise — checked at validation time, so
    the UI never offers the type to viewers and the API enforces it regardless)."""
    if not _is_editor(user) and any(w.type == "sql" for w in layout.widgets):
        raise HTTPException(422, "SQL widgets require the editor role")


def _validate_sql_widgets(db: Session, layout: schemas.DashboardLayout) -> None:
    """guard_sql() each sql widget at save time (#41 pattern) + connection must
    exist. Surface the guard message as a 422 so the builder can show it."""
    conn_cache: dict[int, bool] = {}
    for w in layout.widgets:
        if not isinstance(w, schemas.SqlWidget):
            continue
        cid = w.config.connection_id
        if cid not in conn_cache:
            conn_cache[cid] = db.get(models.Connection, cid) is not None
        if not conn_cache[cid]:
            raise HTTPException(422, f"Connection {cid} not found for widget '{w.title}'")
        try:
            guard_sql(w.config.sql)
        except SqlNotAllowed as exc:
            raise HTTPException(422, f"Widget '{w.title}': {exc}") from exc


def _out(db: Session, dash: models.CustomDashboard, user: models.User) -> schemas.CustomDashboardOut:
    out = schemas.CustomDashboardOut(**custom_dashboard_meta(db, dash).model_dump())
    out.layout = schemas.DashboardLayout.model_validate(dash.layout or {"version": 1, "widgets": []})
    out.can_edit = _can_edit(dash, user)
    return out


# ---- SQL snapshot runner ----------------------------------------------------
# TODO(#42): converge with core/dashboards.py once the scheduled-refresh helper
# lands. #42's dashboard-claim loop should ALSO claim custom dashboards that have
# sql widgets and call this same runner so scheduled and manual refresh share one
# guarded path. Until then this is the small local runner.
def _refresh_sql_widget(db: Session, cfg: schemas.SqlWidgetConfig) -> schemas.WidgetSnapshot:
    """Execute one sql widget through the SAME guarded path as the workbench
    (guard_sql + connector.run_select, row cap 200). Per-widget errors are
    captured in ``snapshot.error`` — they NEVER fail the enclosing request."""
    start = time.perf_counter()
    columns: list[str] = []
    rows: list[list] = []
    error: str | None = None
    try:
        conn = db.get(models.Connection, cfg.connection_id)
        if conn is None:
            raise ValueError(f"Connection {cfg.connection_id} no longer exists")
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
    _validate_sql_widgets(db, body.layout)
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
        _validate_for_role(body.layout, user)
        _validate_sql_widgets(db, body.layout)
        # Preserve existing server snapshots across a metadata/layout edit: the UI
        # round-trips snapshots back, but we never trust the client copy — re-attach
        # ours by widget id where the sql config is unchanged.
        prior = {
            w.get("id"): w.get("snapshot")
            for w in (dash.layout or {}).get("widgets", [])
            if w.get("type") == "sql" and w.get("snapshot")
        }
        new_layout = _strip_snapshots(body.layout)
        for w in new_layout["widgets"]:
            if w.get("type") == "sql" and w["id"] in prior:
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
    still refresh. Requires view access to the dashboard."""
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
        w["snapshot"] = _refresh_sql_widget(db, cfg).model_dump(mode="json")
    dash.layout = layout
    # mutating a nested JSON dict in place isn't always seen as dirty — flag it
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(dash, "layout")
    db.commit()
    db.refresh(dash)
    return _out(db, dash, user)
