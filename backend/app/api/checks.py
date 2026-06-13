import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.serialize import check_out, run_out
from app.connectors.sa import connector_for
from app.core.audit import audit
from app.core.check_types import CHECK_TYPES, validate_check
from app.core.generator import heuristic_proposals
from app.core.profiler import summarize_profile_for_llm
from app.core.runner import run_check
from app.db import get_db
from app.llm.client import llm_enabled
from app.models import utcnow
from app.security import get_current_user, require_role

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
        params = validate_check(body.check_type, body.column_name, body.params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    check = models.Check(
        dataset_id=ds.id,
        name=body.name or f"{ds.table_name}: {body.check_type}"
        + (f" on {body.column_name}" if body.column_name else ""),
        check_type=body.check_type,
        column_name=body.column_name,
        params=params,
        severity=body.severity,
        rationale=body.rationale,
        schedule_kind=body.schedule_kind,
        schedule_expr=body.schedule_expr,
        status=body.status,
        origin="manual",
        created_by_id=user.id,
        next_run_at=utcnow() if body.status == "active" else None,
    )
    db.add(check)
    db.flush()  # assign check.id for the audit row
    audit(
        db, user, "check.create", "check", check.id,
        check_type=check.check_type, column=check.column_name, status=check.status,
    )
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

    old_params = dict(check.params or {})
    old_status = check.status
    data = body.model_dump(exclude_unset=True)
    new_type_params = (
        ("params" in data and data["params"] is not None)
        or ("column_name" in data and data["column_name"] != check.column_name)
    )
    if "column_name" in data:
        check.column_name = data["column_name"]
    if "params" in data and data["params"] is not None:
        check.params = data["params"]
    if new_type_params:
        try:
            check.params = validate_check(check.check_type, check.column_name, check.params)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    for field in ("name", "severity", "rationale", "schedule_kind", "schedule_expr"):
        if field in data and data[field] is not None:
            setattr(check, field, data[field])
    if "status" in data and data["status"] is not None and data["status"] != check.status:
        check.status = data["status"]
        check.next_run_at = utcnow() if check.status == "active" else None
    detail: dict = {"fields": [f for f in data if data[f] is not None]}
    if check.params != old_params:
        detail["params_before"] = old_params
        detail["params_after"] = dict(check.params or {})
    if check.status != old_status:
        detail["status"] = {"before": old_status, "after": check.status}
    audit(db, user, "check.update", "check", check.id, **detail)
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
