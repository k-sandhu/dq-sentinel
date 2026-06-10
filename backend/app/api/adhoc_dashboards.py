"""Ad-hoc investigation dashboards: generated (LLM or heuristic), persisted,
re-executed on open."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.connectors.sa import connector_for
from app.core.adhoc import execute_panels, heuristic_panels
from app.core.profiler import summarize_profile_for_llm
from app.db import get_db
from app.llm.client import llm_enabled
from app.models import utcnow
from app.security import get_current_user, require_role

log = logging.getLogger(__name__)
router = APIRouter(prefix="/adhoc-dashboards", tags=["adhoc-dashboards"])


def _meta(d: models.AdhocDashboard) -> schemas.AdhocDashboardMeta:
    out = schemas.AdhocDashboardMeta.model_validate(d)
    out.panel_count = len((d.spec or {}).get("panels", []))
    return out


@router.post("/generate", response_model=schemas.AdhocDashboardOut, status_code=201)
def generate(
    body: schemas.GenerateDashboardIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = db.get(models.Dataset, body.dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    profile_row = (
        db.query(models.Profile)
        .filter(models.Profile.dataset_id == ds.id)
        .order_by(models.Profile.id.desc())
        .first()
    )
    if profile_row is None:
        raise HTTPException(409, "Profile the dataset before generating a dashboard")
    profile = {
        "row_count": profile_row.row_count,
        "sampled_rows": profile_row.sampled_rows,
        "columns": profile_row.columns,
        "table_facts": profile_row.table_facts,
    }
    connector = connector_for(ds.connection)
    ref = connector.table_ref(ds.table_name, ds.schema_name)

    title = f"{ds.table_name} overview"
    origin = "heuristic"
    panels = None
    if llm_enabled():
        try:
            from app.llm.workbench import generate_dashboard_llm

            context = (
                f"Dialect: {connector.kind}\nTable: {ref}\n\n## Profile\n"
                + summarize_profile_for_llm(profile)
                + (f"\n\n## Focus question\n{body.focus}" if body.focus else "")
            )
            result = generate_dashboard_llm(context)
            title, panels, origin = result["title"], result["panels"], "llm"
        except Exception:  # noqa: BLE001 - heuristic fallback must always work
            log.exception("LLM dashboard generation failed; using heuristic panels")
    if panels is None:
        panels = heuristic_panels(connector, ref, profile)
        if body.focus:
            title = f"{ds.table_name}: {body.focus[:80]}"

    dash = models.AdhocDashboard(
        dataset_id=ds.id,
        title=title,
        focus=body.focus,
        origin=origin,
        spec={"panels": panels},
        created_by_id=user.id,
        last_refreshed_at=utcnow(),
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)

    out = schemas.AdhocDashboardOut(**_meta(dash).model_dump())
    out.panels = [schemas.PanelData(**p) for p in execute_panels(connector, panels)]
    return out


@router.get("", response_model=list[schemas.AdhocDashboardMeta])
def list_dashboards(
    dataset_id: int | None = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    q = db.query(models.AdhocDashboard)
    if dataset_id is not None:
        q = q.filter(models.AdhocDashboard.dataset_id == dataset_id)
    return [_meta(d) for d in q.order_by(models.AdhocDashboard.id.desc()).limit(50).all()]


@router.get("/{dashboard_id}", response_model=schemas.AdhocDashboardOut)
def open_dashboard(
    dashboard_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    dash = db.get(models.AdhocDashboard, dashboard_id)
    if dash is None:
        raise HTTPException(404, "Dashboard not found")
    ds = db.get(models.Dataset, dash.dataset_id)
    if ds is None:
        raise HTTPException(409, "Dashboard's dataset no longer exists")
    connector = connector_for(ds.connection)
    panels = (dash.spec or {}).get("panels", [])
    data = execute_panels(connector, panels)
    dash.last_refreshed_at = utcnow()
    db.commit()

    out = schemas.AdhocDashboardOut(**_meta(dash).model_dump())
    out.panels = [schemas.PanelData(**p) for p in data]
    return out


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(
    dashboard_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_role("editor")),
):
    dash = db.get(models.AdhocDashboard, dashboard_id)
    if dash is None:
        raise HTTPException(404, "Dashboard not found")
    db.delete(dash)
    db.commit()
