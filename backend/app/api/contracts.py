"""Data contract API (#105): CRUD, activation, conformance, and ODCS YAML."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.audit import audit
from app.core.contracts import (
    apply_contract,
    archive_contract_checks,
    conformance,
    contract_out,
    default_contract_spec,
    from_odcs_yaml,
    normalize_spec,
    serialize_checks,
    snapshot_version,
    spec_diff,
    to_odcs_yaml,
)
from app.db import get_db
from app.security import get_current_user, require_role

router = APIRouter(prefix="/datasets/{dataset_id}", tags=["contracts"])


def _get_dataset(db: Session, dataset_id: int) -> models.Dataset:
    ds = db.get(models.Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    return ds


def _get_contract(db: Session, dataset_id: int, contract_id: int) -> models.DataContract:
    contract = db.get(models.DataContract, contract_id)
    if contract is None or contract.dataset_id != dataset_id:
        raise HTTPException(404, "Data contract not found")
    return contract


def _latest_contract(db: Session, dataset_id: int) -> models.DataContract:
    contract = (
        db.query(models.DataContract)
        .filter(models.DataContract.dataset_id == dataset_id, models.DataContract.status == "active")
        .order_by(models.DataContract.id.desc())
        .first()
    )
    if contract is None:
        contract = (
            db.query(models.DataContract)
            .filter(models.DataContract.dataset_id == dataset_id)
            .order_by(models.DataContract.id.desc())
            .first()
        )
    if contract is None:
        raise HTTPException(404, "Dataset has no data contract yet")
    return contract


def _out(db: Session, contract: models.DataContract) -> dict:
    data = contract_out(contract)
    data["version_count"] = (
        db.query(models.DataContractVersion)
        .filter(models.DataContractVersion.contract_id == contract.id)
        .count()
    )
    return data


@router.get("/contracts", response_model=list[schemas.DataContractOut])
def list_contracts(
    dataset_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    contracts = (
        db.query(models.DataContract)
        .filter(models.DataContract.dataset_id == dataset_id)
        .order_by(models.DataContract.id.desc())
        .all()
    )
    return [_out(db, c) for c in contracts]


@router.get("/contract", response_model=schemas.DataContractOut)
def get_latest_contract(
    dataset_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    return _out(db, _latest_contract(db, dataset_id))


@router.get("/contract/conformance", response_model=schemas.DataContractConformanceOut)
def latest_contract_conformance(
    dataset_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    return conformance(db, _latest_contract(db, dataset_id))


@router.get("/contract/export", response_model=schemas.DataContractExportOut)
def export_latest_contract(
    dataset_id: int,
    format: str = "odcs",
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    if format != "odcs":
        raise HTTPException(422, "Only format=odcs is supported")
    return {"format": "odcs", "yaml": to_odcs_yaml(_latest_contract(db, dataset_id))}


@router.get("/contract/{contract_id}", response_model=schemas.DataContractOut)
def get_contract(
    dataset_id: int,
    contract_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    return _out(db, _get_contract(db, dataset_id, contract_id))


@router.post("/contract", response_model=schemas.DataContractOut, status_code=201)
def create_contract(
    dataset_id: int,
    body: schemas.DataContractCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = _get_dataset(db, dataset_id)
    spec = normalize_spec(body.spec) if body.spec is not None else default_contract_spec(db, ds)
    contract = models.DataContract(
        dataset_id=ds.id,
        name=body.name or f"{ds.table_name} contract",
        version=body.version,
        status="draft",
        spec=spec,
        created_by_id=user.id,
    )
    db.add(contract)
    db.flush()
    snapshot_version(db, contract, user)
    if body.status == "active":
        try:
            apply_contract(db, contract, user)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif body.status == "deprecated":
        contract.status = "deprecated"
    audit(db, user, "contract.create", "contract", contract.id, dataset_id=ds.id, status=contract.status)
    db.commit()
    db.refresh(contract)
    return _out(db, contract)


@router.patch("/contract/{contract_id}", response_model=schemas.DataContractOut)
def update_contract(
    dataset_id: int,
    contract_id: int,
    body: schemas.DataContractUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    _get_dataset(db, dataset_id)
    contract = _get_contract(db, dataset_id, contract_id)
    data = body.model_dump(exclude_unset=True)
    changed = False
    if "name" in data and data["name"] is not None:
        contract.name = data["name"]
        changed = True
    if "version" in data and data["version"] is not None:
        contract.version = data["version"]
        changed = True
    if "spec" in data and data["spec"] is not None:
        contract.spec = normalize_spec(data["spec"])
        changed = True
    if "status" in data and data["status"] is not None:
        if data["status"] == "active":
            try:
                apply_contract(db, contract, user)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        else:
            archive_contract_checks(db, contract)
            contract.status = data["status"]
        changed = True
    if changed and data.get("status") != "active":
        snapshot_version(db, contract, user)
    audit(db, user, "contract.update", "contract", contract.id, fields=list(data))
    db.commit()
    db.refresh(contract)
    return _out(db, contract)


@router.delete("/contract/{contract_id}", status_code=204)
def delete_contract(
    dataset_id: int,
    contract_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    _get_dataset(db, dataset_id)
    contract = _get_contract(db, dataset_id, contract_id)
    archived = archive_contract_checks(db, contract)
    audit(
        db,
        user,
        "contract.delete",
        "contract",
        contract.id,
        dataset_id=dataset_id,
        archived_checks=len(archived),
    )
    db.delete(contract)
    db.commit()


@router.post("/contract/{contract_id}/activate", response_model=schemas.DataContractApplyOut)
def activate_contract(
    dataset_id: int,
    contract_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = _get_dataset(db, dataset_id)
    contract = _get_contract(db, dataset_id, contract_id)
    try:
        created, updated, schema_pinned = apply_contract(db, contract, user)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(
        db,
        user,
        "contract.activate",
        "contract",
        contract.id,
        dataset_id=dataset_id,
        created_checks=len(created),
        updated_checks=len(updated),
    )
    db.commit()
    db.refresh(contract)
    return {
        "contract": _out(db, contract),
        "created_checks": serialize_checks(created, ds.table_name),
        "updated_checks": serialize_checks(updated, ds.table_name),
        "schema_pinned": schema_pinned,
    }


@router.get("/contract/{contract_id}/conformance", response_model=schemas.DataContractConformanceOut)
def contract_conformance(
    dataset_id: int,
    contract_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    return conformance(db, _get_contract(db, dataset_id, contract_id))


@router.get("/contract/{contract_id}/versions", response_model=list[schemas.DataContractVersionOut])
def contract_versions(
    dataset_id: int,
    contract_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    _get_contract(db, dataset_id, contract_id)
    return (
        db.query(models.DataContractVersion)
        .filter(models.DataContractVersion.contract_id == contract_id)
        .order_by(models.DataContractVersion.id.desc())
        .all()
    )


@router.get("/contract/{contract_id}/versions/{from_version_id}/diff", response_model=schemas.DataContractDiffOut)
def contract_version_diff(
    dataset_id: int,
    contract_id: int,
    from_version_id: int,
    to_version_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    _get_contract(db, dataset_id, contract_id)
    before = db.get(models.DataContractVersion, from_version_id)
    after = db.get(models.DataContractVersion, to_version_id)
    if (
        before is None
        or after is None
        or before.contract_id != contract_id
        or after.contract_id != contract_id
    ):
        raise HTTPException(404, "Contract version not found")
    diff = spec_diff(before.spec or {}, after.spec or {})
    return {"contract_id": contract_id, "from_version_id": from_version_id, "to_version_id": to_version_id, **diff}


@router.post("/contract/import", response_model=schemas.DataContractOut, status_code=201)
def import_contract(
    dataset_id: int,
    body: schemas.DataContractImportIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("editor")),
):
    ds = _get_dataset(db, dataset_id)
    try:
        name, version, spec = from_odcs_yaml(body.yaml)
    except Exception as exc:  # noqa: BLE001 - return a clean parser error
        raise HTTPException(422, f"Could not parse ODCS YAML: {exc}") from exc
    contract = models.DataContract(
        dataset_id=ds.id,
        name=name,
        version=version,
        status="draft",
        spec=spec,
        created_by_id=user.id,
    )
    db.add(contract)
    db.flush()
    snapshot_version(db, contract, user)
    if body.activate:
        try:
            apply_contract(db, contract, user)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    audit(db, user, "contract.import", "contract", contract.id, dataset_id=dataset_id)
    db.commit()
    db.refresh(contract)
    return _out(db, contract)


@router.get("/contract/{contract_id}/export", response_model=schemas.DataContractExportOut)
def export_contract(
    dataset_id: int,
    contract_id: int,
    format: str = "odcs",
    db: Session = Depends(get_db),
    _: models.User = Depends(get_current_user),
):
    _get_dataset(db, dataset_id)
    if format != "odcs":
        raise HTTPException(422, "Only format=odcs is supported")
    return {"format": "odcs", "yaml": to_odcs_yaml(_get_contract(db, dataset_id, contract_id))}
