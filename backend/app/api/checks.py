import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import check_out, check_version_out, run_out
from app.connectors.sa import connector_for
from app.core import check_authoring, incidents
from app.core.audit import audit
from app.core.check_types import CHECK_TYPES
from app.core.generator import heuristic_proposals
from app.core.profiler import summarize_profile_for_llm
from app.core.runner import run_check
from app.db import get_db
from app.llm.client import llm_enabled
from app.security import (
    assert_connection_role,
    assert_dataset_visible,
    get_current_user,
    require_role,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/checks", tags=["checks"])


@router.get("/types", response_model=list[schemas.CheckTypeInfo])
def check_types(_: models.User = Depends(get_current_user)):
    return [
        schemas.CheckTypeInfo(
            key=ct.key,
            label=ct.label,
            description=ct.description,
            needs_column=ct.needs_column,
            params=ct.params,
        )
        for ct in CHECK_TYPES.values()
    ]


@router.get("", response_model=list[schemas.CheckOut])
def list_checks(
    dataset_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    query = db.query(models.Check).filter(models.Check.status != "archived")
    if dataset_id is not None:
        query = query.filter(models.Check.dataset_id == dataset_id)
    if status:
        query = query.filter(models.Check.status == status)
    if q:
        needle = f"%{q.lower()}%"
        query = query.filter(
            func.lower(models.Check.name).like(needle)
            | func.lower(func.coalesce(models.Check.column_name, "")).like(needle)
        )
    return [check_out(c) for c in query.order_by(models.Check.dataset_id, models.Check.id).all()]


@router.post("", response_model=schemas.CheckOut, status_code=201)
def create_check(
    body: schemas.CheckIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = db.get(models.Dataset, body.dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    try:
        check = check_authoring.create_check(
            db, user, ds,
            name=body.name,
            check_type=body.check_type,
            column_name=body.column_name,
            params=body.params,
            severity=body.severity,
            rationale=body.rationale,
            schedule_kind=body.schedule_kind,
            schedule_expr=body.schedule_expr,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    db.refresh(check)
    return check_out(check)


@router.patch("/{check_id}", response_model=schemas.CheckOut)
def update_check(
    check_id: int,
    body: schemas.CheckUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    check = db.get(models.Check, check_id)
    if check is None:
        raise HTTPException(404, "Check not found")
    try:
        check_authoring.apply_update(db, user, check, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Disabling/archiving a check via PATCH must also silence its open incident (#A6),
    # same as the DELETE/archive path.
    if check.status in ("disabled", "archived"):
        incidents.resolve_incident_for_retired_check(db, check, reason=f"check {check.status}")
    db.commit()
    db.refresh(check)
    return check_out(check)


@router.post("/{check_id}/run", response_model=schemas.RunOut)
def run_now(
    check_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    check = db.get(models.Check, check_id)
    if check is None:
        raise HTTPException(404, "Check not found")
    audit(db, user, "check.run_manual", "check", check.id, check_type=check.check_type)
    run = run_check(db, check, triggered_by="manual")  # commits the audit row in the same tx
    return run_out(db, run)


@router.get("/{check_id}/versions", response_model=list[schemas.CheckVersionOut])
def list_check_versions(
    check_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    check = db.get(models.Check, check_id)
    if check is None:
        raise HTTPException(404, "Not found")
    # Scope through the check's dataset connection (#159): 404 (not 403) if the
    # user can't see that connection, so check existence isn't leaked across the
    # data boundary. Read access = viewer-or-above on the owning connection.
    assert_dataset_visible(db, user, check.dataset_id)
    versions = (
        db.query(models.CheckVersion)
        .filter(models.CheckVersion.check_id == check_id)
        .order_by(models.CheckVersion.version.desc())
        .all()
    )
    current = versions[0].version if versions else None
    return [check_version_out(db, v, is_current=v.version == current) for v in versions]


@router.post("/{check_id}/restore", response_model=schemas.CheckOut)
def restore_check_version(
    check_id: int,
    body: schemas.CheckRestoreIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    check = db.get(models.Check, check_id)
    if check is None:
        raise HTTPException(404, "Not found")
    ds = db.get(models.Dataset, check.dataset_id)
    if ds is None:
        raise HTTPException(404, "Not found")
    # Restoring mutates the check, so require effective EDITOR on the owning
    # connection (#159) — a global editor with only a viewer grant there is
    # blocked. 404 for an invisible connection, 403 for visible-but-not-editor.
    assert_connection_role(db, user, ds.connection_id, "editor")
    try:
        check_authoring.restore_version(db, user, check, body.version)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:  # a restored definition that no longer validates
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    db.refresh(check)
    return check_out(check)


@router.delete("/{check_id}", status_code=204)
def archive_check(
    check_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    check = db.get(models.Check, check_id)
    if check is None:
        raise HTTPException(404, "Check not found")
    check.status = "archived"
    check.next_run_at = None
    # Retiring a check must silence its open incident too, else escalations keep
    # paging for a check that will never run (and never recover) again (#A6).
    incidents.resolve_incident_for_retired_check(db, check, reason="check archived")
    audit(db, user, "check.archive", "check", check.id, check_type=check.check_type)
    db.commit()


@router.post("/generate", response_model=schemas.GenerateChecksOut)
def generate_checks(
    body: schemas.GenerateChecksIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = db.get(models.Dataset, body.dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    profile = (
        db.query(models.Profile)
        .filter(models.Profile.dataset_id == ds.id)
        .order_by(models.Profile.id.desc())
        .first()
    )
    if profile is None:
        raise HTTPException(409, "Profile the dataset before generating checks")

    profile_dict = {
        "row_count": profile.row_count,
        "sampled_rows": profile.sampled_rows,
        "columns": profile.columns,
        "table_facts": profile.table_facts,
    }
    knowledge = ds.knowledge
    knowledge_dict = (
        {
            "business_context": knowledge.business_context,
            "known_issues": knowledge.known_issues,
            "importance": knowledge.importance,
            "owner": knowledge.owner,
            "domain": knowledge.domain,
            "team": knowledge.team,
            "freshness_sla_hours": knowledge.freshness_sla_hours,
            "pii_columns": knowledge.pii_columns,
            "notes": knowledge.notes,
        }
        if knowledge
        else None
    )

    existing = (
        db.query(models.Check)
        .filter(models.Check.dataset_id == ds.id, models.Check.status != "archived")
        .all()
    )
    existing_keys = {(c.check_type, c.column_name or "") for c in existing}
    existing_descriptions = [
        f"{c.check_type} on {c.column_name or '(table)'} [{c.status}]" for c in existing
    ]

    mode = "heuristic"
    explored = False
    proposals: list[dict] = []

    if body.use_llm and llm_enabled():
        try:
            exploration = ds.exploration
            if body.explore:
                from app.llm.explorer import explore_dataset

                connector = connector_for(ds.connection)
                exploration = explore_dataset(
                    connector,
                    connector.table_ref(ds.table_name, ds.schema_name),
                    summarize_profile_for_llm(
                        profile_dict, (knowledge_dict or {}).get("pii_columns")
                    ),
                    knowledge_dict,
                )
                ds.exploration = {
                    "insights": exploration["insights"],
                    "queries_run": exploration["queries_run"],
                }
                db.commit()
                explored = True

            from app.llm.check_gen import generate_checks_llm

            proposals = generate_checks_llm(
                table_name=ds.table_name,
                profile_summary=summarize_profile_for_llm(
                    profile_dict, (knowledge_dict or {}).get("pii_columns")
                ),
                knowledge=knowledge_dict,
                exploration=ds.exploration,
                existing_checks=existing_descriptions,
                valid_columns={c["name"] for c in profile.columns},
            )
            mode = "llm"
        except Exception:  # noqa: BLE001 - LLM failure must not block generation
            log.exception("LLM check generation failed; falling back to heuristics")
            proposals = []

    if not proposals:
        proposals = heuristic_proposals(profile_dict, knowledge_dict)
        mode = "heuristic"

    created: list[models.Check] = []
    skipped = 0
    for p in proposals:
        key = (p["check_type"], p.get("column_name") or "")
        if key in existing_keys:
            skipped += 1
            continue
        existing_keys.add(key)
        check = models.Check(
            dataset_id=ds.id,
            name=p.get("name")
            or f"{ds.table_name}: {p['check_type']}"
            + (f" on {p['column_name']}" if p.get("column_name") else ""),
            check_type=p["check_type"],
            column_name=p.get("column_name"),
            params=p.get("params") or {},
            severity=p.get("severity", "warn"),
            rationale=p.get("rationale", ""),
            schedule_kind=p.get("schedule_kind", "interval"),
            schedule_expr=p.get("schedule_expr", "1440"),
            status="proposed",
            origin=mode,
            created_by_id=user.id,
        )
        db.add(check)
        check_authoring.snapshot_version(db, check, user, "generated")
        created.append(check)
    db.commit()
    for c in created:
        db.refresh(c)
    return schemas.GenerateChecksOut(
        created=len(created),
        skipped_duplicates=skipped,
        mode=mode,
        explored=explored,
        checks=[check_out(c, ds.table_name) for c in created],
    )
