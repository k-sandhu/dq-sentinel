"""Declarative definitions for the 7 built-in catalog datasets.

Pure data — no IO. Each :class:`CatalogEntry` fully describes a *mature, governed*
enterprise dataset: the backing-data generator, the tables to register, and for
each table the business knowledge, an enforceable data contract (column rules as
quality clauses), curated analyst checks, and reliability SLAs. The seeding engine
(:mod:`app.catalog.seed`) materializes all of this when a user connects the entry.

Accepted-value lists are imported from :mod:`app.catalog.generators` so the
contract's allowed domain stays in lock-step with the data the generator plants
(the generator deliberately injects a few out-of-domain values for checks to catch).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.catalog import generators as g

EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


@dataclass(frozen=True)
class KnowledgeBundle:
    """Maps onto ``TableKnowledge`` — the governance metadata a business fills in."""

    business_context: str
    known_issues: str
    importance: str  # low | medium | high | critical
    owner: str
    domain: str
    team: str
    freshness_sla_hours: int | None
    pii_columns: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class QualityClause:
    """A column/governance rule carried inside the data contract's ``quality[]``.

    NEVER ``check_type="freshness"`` — the monitor pack owns freshness so the two
    don't materialize duplicate checks on the same column.
    """

    id: str
    name: str
    check_type: str
    column: str | None
    params: dict[str, Any] = field(default_factory=dict)
    severity: str = "error"
    schedule_expr: str = "1440"
    rationale: str = ""


@dataclass(frozen=True)
class ExtraCheck:
    """An analyst-authored check created directly (origin=manual). Must NOT use a
    monitor-pack check_type (freshness/row_count_anomaly/schema_contract/
    distribution_drift) to avoid colliding with the auto monitors."""

    name: str
    check_type: str
    params: dict[str, Any] = field(default_factory=dict)
    column_name: str | None = None
    severity: str = "error"
    schedule_expr: str = "1440"
    rationale: str = ""


@dataclass(frozen=True)
class SlaSpec:
    name: str
    target_type: str = "check_success"  # freshness | volume | check_success
    objective: float = 0.99
    window: str = "rolling_30d"  # rolling_7d | rolling_30d


@dataclass(frozen=True)
class CatalogTable:
    table_name: str
    knowledge: KnowledgeBundle
    display_name: str = ""
    quality: tuple[QualityClause, ...] = ()
    extra_checks: tuple[ExtraCheck, ...] = ()
    slas: tuple[SlaSpec, ...] = ()
    consumers: tuple[str, ...] = ()
    contract_terms: str = ""


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    title: str
    description: str
    domain: str
    source_system: str  # also the Connection.name (unique) once connected
    engine: str  # sqlite | duckdb
    seed: int
    generate: Callable[..., dict[str, int]]
    tables: tuple[CatalogTable, ...]
    tags: tuple[str, ...] = ()


def _av(col: str, values, name: str, sev: str = "error") -> QualityClause:
    return QualityClause(
        id=f"accepted-{col}", name=name, check_type="accepted_values", column=col,
        params={"values": list(values)}, severity=sev,
        rationale=f"{col} must be one of the agreed domain values.",
    )


# --------------------------------------------------------------------------- #
# 1. Retail orders — Commerce                                                  #
# --------------------------------------------------------------------------- #

RETAIL = CatalogEntry(
    key="retail-commerce",
    title="Retail orders",
    description=(
        "The e-commerce order book: orders, customers, line items, and payments for an "
        "online retailer. System of record for revenue recognition and the busiest dataset "
        "in the estate."
    ),
    domain="Commerce",
    source_system="Retail — Commerce DB",
    engine="sqlite",
    seed=42,
    generate=g.generate_retail,
    tags=("orders", "revenue", "e-commerce"),
    tables=(
        CatalogTable(
            table_name="orders",
            display_name="Orders",
            knowledge=KnowledgeBundle(
                business_context=(
                    "Every customer order across web and store channels. The system of record "
                    "for revenue recognition and the source for daily revenue reporting."
                ),
                known_issues=(
                    "total_amount occasionally disagrees with the sum of line items; a few "
                    "far-future order_date values; status casing/domain drift from a legacy import."
                ),
                importance="critical",
                owner="Maria Gomez",
                domain="Commerce",
                team="Commerce Analytics",
                freshness_sla_hours=24,
                pii_columns=(),
                notes=(
                    "Joins to order_items and payments in the same source. "
                    "Revenue = SUM(total_amount) WHERE status != 'cancelled'."
                ),
            ),
            consumers=("Finance — Revenue", "Exec dashboard", "Marketing — Attribution"),
            contract_terms="Orders are immutable once shipped; corrections post as new rows.",
            quality=(
                _av("status", g._ORDER_STATUS, "Order status in agreed set"),
                QualityClause("nonneg-total", "Order total is non-negative", "range",
                              "total_amount", {"min": 0}, "error",
                              rationale="An order total must never be negative."),
                QualityClause("notnull-order-id", "order_id present", "not_null", "order_id"),
                QualityClause("unique-order-id", "order_id unique", "unique", "order_id"),
            ),
            extra_checks=(
                ExtraCheck(
                    name="orders: total_amount matches sum(line items)",
                    check_type="custom_sql",
                    params={"sql": (
                        "SELECT o.order_id, o.total_amount, SUM(i.line_total) AS items_total "
                        "FROM orders o JOIN order_items i ON i.order_id = o.order_id "
                        "GROUP BY o.order_id, o.total_amount "
                        "HAVING ABS(o.total_amount - SUM(i.line_total)) > 0.01"
                    )},
                    severity="error",
                    rationale="Order header total must reconcile to its line items.",
                ),
            ),
            slas=(SlaSpec("Orders check success", objective=0.99),),
        ),
        CatalogTable(
            table_name="customers",
            display_name="Customers",
            knowledge=KnowledgeBundle(
                business_context=(
                    "Customer master: identity, contact details, and loyalty tier. Feeds CRM "
                    "and marketing segmentation."
                ),
                known_issues=(
                    "~2% NULL emails; occasional malformed or duplicate emails; inconsistent "
                    "country casing; rare future signup dates."
                ),
                importance="high",
                owner="Maria Gomez",
                domain="Commerce",
                team="Commerce Analytics",
                freshness_sla_hours=None,
                pii_columns=("email", "full_name"),
                notes="email is the primary contact key for marketing; treat as PII.",
            ),
            consumers=("Marketing — CRM", "Support"),
            quality=(
                QualityClause("email-format", "email is well-formed", "regex_match", "email",
                              {"pattern": EMAIL_RE}, "warn",
                              rationale="Contact emails must be syntactically valid."),
                _av("loyalty_tier", ["bronze", "silver", "gold", "platinum"],
                    "Loyalty tier in agreed set", "warn"),
                QualityClause("notnull-name", "full_name present", "not_null", "full_name"),
            ),
            slas=(SlaSpec("Customers check success", objective=0.98),),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# 2. Payments ledger — Finance                                                 #
# --------------------------------------------------------------------------- #

PAYMENTS = CatalogEntry(
    key="finance-payments",
    title="Payments ledger",
    description=(
        "General-ledger transaction feed from the core banking system. Underpins "
        "reconciliation, regulatory reporting, and daily cash positioning."
    ),
    domain="Finance",
    source_system="Finance — Payments Ledger",
    engine="sqlite",
    seed=43,
    generate=g.generate_payments_ledger,
    tags=("ledger", "transactions", "reconciliation"),
    tables=(
        CatalogTable(
            table_name="transactions",
            display_name="Transactions",
            knowledge=KnowledgeBundle(
                business_context=(
                    "Booked general-ledger transactions from the core banking platform. The "
                    "basis for reconciliation, regulatory reporting, and cash positioning."
                ),
                known_issues=(
                    "Rare magnitude-typo amounts (100x); a few settled transactions with zero "
                    "value; value_date occasionally before posted_at; currency casing drift; "
                    "some orphan account_id references."
                ),
                importance="critical",
                owner="David Okafor",
                domain="Finance",
                team="Finance Data Engineering",
                freshness_sla_hours=6,
                pii_columns=("account_id", "counterparty"),
                notes=(
                    "posted_at is the booking timestamp; value_date is settlement. Sign "
                    "convention: debits and fees are negative amounts."
                ),
            ),
            consumers=("Regulatory reporting", "Treasury", "Reconciliation"),
            contract_terms="Ledger is append-only; reversals post as offsetting entries.",
            quality=(
                _av("txn_type", g._TXN_TYPES, "Transaction type in agreed set"),
                _av("currency", g._CURRENCIES, "Currency is a supported ISO code"),
                _av("status", g._TXN_STATUS, "Status in agreed set"),
                QualityClause("notnull-txn", "txn_id present", "not_null", "txn_id"),
                QualityClause("unique-txn", "txn_id unique", "unique", "txn_id"),
            ),
            extra_checks=(
                ExtraCheck(
                    name="transactions: amount outliers (IsolationForest)",
                    check_type="ml_outlier",
                    params={"columns": ["amount"], "contamination": 0.004},
                    severity="warn",
                    rationale="Catch magnitude-typo amounts the fixed bounds miss.",
                ),
                ExtraCheck(
                    name="transactions: value_date not before posted_at",
                    check_type="custom_sql",
                    params={"sql": (
                        "SELECT txn_id, posted_at, value_date FROM transactions "
                        "WHERE value_date < substr(posted_at, 1, 10)"
                    )},
                    severity="error",
                    rationale="Settlement date cannot precede the booking date.",
                ),
            ),
            slas=(SlaSpec("Ledger check success", objective=0.99),),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# 3. Employee directory — People (PII-rich)                                    #
# --------------------------------------------------------------------------- #

EMPLOYEES = CatalogEntry(
    key="people-hris",
    title="Employee directory",
    description=(
        "Authoritative employee master from the HRIS: identity, org structure, compensation, "
        "and lifecycle status. Heavily restricted PII."
    ),
    domain="People",
    source_system="People — HRIS",
    engine="sqlite",
    seed=44,
    generate=g.generate_employees,
    tags=("hr", "people", "pii"),
    tables=(
        CatalogTable(
            table_name="employees",
            display_name="Employees",
            knowledge=KnowledgeBundle(
                business_context=(
                    "The authoritative employee directory: identity, org structure, "
                    "compensation, and lifecycle status. Drives provisioning, payroll, and "
                    "headcount reporting."
                ),
                known_issues=(
                    "Some NULL or malformed work emails; rare future hire dates; a few orphan "
                    "manager_id references; occasional negative or 10x salary outliers; "
                    "department casing variants."
                ),
                importance="high",
                owner="Priya Nair",
                domain="People",
                team="People Analytics",
                freshness_sla_hours=24,
                pii_columns=("full_name", "work_email", "personal_email", "ssn", "phone",
                             "dob", "salary"),
                notes=(
                    "Highly sensitive: SSN, DOB, and salary are restricted. All PII columns "
                    "are redacted from LLM prompts."
                ),
            ),
            consumers=("Payroll", "IT provisioning", "Workforce planning"),
            contract_terms="Access restricted to People Analytics; comp fields are need-to-know.",
            quality=(
                QualityClause("email-format", "work_email is well-formed", "regex_match",
                              "work_email", {"pattern": EMAIL_RE}, "error",
                              rationale="Work email must be valid for provisioning."),
                _av("department", g._DEPARTMENTS, "Department in agreed org list"),
                _av("employment_status", g._EMP_STATUS, "Employment status in agreed set"),
                QualityClause("nonneg-salary", "salary is non-negative", "range", "salary",
                              {"min": 0}, "error", rationale="Salary cannot be negative."),
                QualityClause("notnull-emp", "employee_id present", "not_null", "employee_id"),
                QualityClause("unique-emp", "employee_id unique", "unique", "employee_id"),
            ),
            extra_checks=(
                ExtraCheck(
                    name="employees: manager_id resolves to an employee",
                    check_type="custom_sql",
                    params={"sql": (
                        "SELECT e.employee_id, e.manager_id FROM employees e "
                        "WHERE e.manager_id IS NOT NULL AND NOT EXISTS "
                        "(SELECT 1 FROM employees m WHERE m.employee_id = e.manager_id)"
                    )},
                    severity="warn",
                    rationale="Every manager reference must point to a real employee.",
                ),
            ),
            slas=(SlaSpec("Employees check success", objective=0.97),),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# 4. Web clickstream — Marketing (DuckDB analytics warehouse)                  #
# --------------------------------------------------------------------------- #

CLICKSTREAM = CatalogEntry(
    key="marketing-clickstream",
    title="Web clickstream",
    description=(
        "Raw web clickstream events from the marketing site and storefront, landed in the "
        "analytics warehouse. Powers funnel analysis, attribution, and growth dashboards."
    ),
    domain="Marketing",
    source_system="Marketing — Web Analytics",
    engine="duckdb",
    seed=45,
    generate=g.generate_web_events,
    tags=("clickstream", "events", "warehouse"),
    tables=(
        CatalogTable(
            table_name="web_events",
            display_name="Web events",
            knowledge=KnowledgeBundle(
                business_context=(
                    "Raw web clickstream events from the marketing site and storefront. Powers "
                    "funnel analysis, attribution models, and the growth dashboards."
                ),
                known_issues=(
                    "High anonymous-user rate (NULL user_id) by design; occasional bot-driven "
                    "volume spikes; rare future event timestamps; malformed page URLs; "
                    "occasional negative revenue or duration from client bugs."
                ),
                importance="medium",
                owner="Tom Becker",
                domain="Marketing",
                team="Growth Marketing",
                freshness_sla_hours=2,
                pii_columns=("user_id",),
                notes=(
                    "event_ts is client-reported; expect minor clock skew. Anonymous events "
                    "(NULL user_id) are expected, not a defect."
                ),
            ),
            consumers=("Attribution", "Growth dashboards", "Experimentation"),
            contract_terms="Events are immutable; late-arriving events allowed within 48h.",
            quality=(
                _av("event_type", g._EVENT_TYPES, "Event type in tracking plan"),
                _av("device", g._DEVICES, "Device in agreed set", "warn"),
                QualityClause("notnull-session", "session_id present", "not_null", "session_id",
                              severity="error",
                              rationale="Every event must belong to a session."),
                QualityClause("nonneg-duration", "duration_ms non-negative", "range",
                              "duration_ms", {"min": 0}, "warn",
                              rationale="Event duration cannot be negative."),
            ),
            slas=(SlaSpec("Clickstream check success", objective=0.95, window="rolling_7d"),),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# 5. Shipments — Supply Chain                                                  #
# --------------------------------------------------------------------------- #

SHIPMENTS = CatalogEntry(
    key="supplychain-logistics",
    title="Shipments",
    description=(
        "Outbound shipment tracking across carriers. Drives delivery SLAs, logistics cost "
        "reporting, and the delivery promises shown to customers."
    ),
    domain="Supply Chain",
    source_system="Supply Chain — Logistics",
    engine="sqlite",
    seed=46,
    generate=g.generate_shipments,
    tags=("logistics", "shipments", "delivery"),
    tables=(
        CatalogTable(
            table_name="shipments",
            display_name="Shipments",
            knowledge=KnowledgeBundle(
                business_context=(
                    "Outbound shipment tracking across carriers. Drives delivery SLAs, "
                    "logistics cost reporting, and customer delivery promises."
                ),
                known_issues=(
                    "A few delivered_at earlier than shipped_at; NULL or variant carrier "
                    "values; negative weights; cost outliers; status casing drift."
                ),
                importance="high",
                owner="Lena Andersson",
                domain="Supply Chain",
                team="Logistics Ops",
                freshness_sla_hours=12,
                pii_columns=(),
                notes="order_ref joins to the Commerce orders dataset (separate source).",
            ),
            consumers=("Customer delivery promise", "Logistics cost reporting"),
            quality=(
                _av("carrier", g._CARRIERS, "Carrier in approved list", "warn"),
                _av("status", g._SHIP_STATUS, "Shipment status in agreed set"),
                QualityClause("nonneg-weight", "weight_kg non-negative", "range", "weight_kg",
                              {"min": 0}, "error", rationale="Weight cannot be negative."),
                QualityClause("notnull-ship", "shipment_id present", "not_null", "shipment_id"),
                QualityClause("unique-ship", "shipment_id unique", "unique", "shipment_id"),
            ),
            extra_checks=(
                ExtraCheck(
                    name="shipments: delivered_at not before shipped_at",
                    check_type="custom_sql",
                    params={"sql": (
                        "SELECT shipment_id, shipped_at, delivered_at FROM shipments "
                        "WHERE delivered_at IS NOT NULL AND delivered_at < shipped_at"
                    )},
                    severity="error",
                    rationale="A shipment cannot be delivered before it ships.",
                ),
            ),
            slas=(SlaSpec("Shipments check success", objective=0.98),),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# 6. Clinical encounters — Healthcare (PII/PHI)                                #
# --------------------------------------------------------------------------- #

CLINICAL = CatalogEntry(
    key="healthcare-ehr",
    title="Clinical encounters",
    description=(
        "Patient encounter records from the EHR: admissions, discharges, diagnoses, and "
        "billing. HIPAA-governed; feeds clinical quality measures and revenue-cycle reporting."
    ),
    domain="Healthcare",
    source_system="Healthcare — EHR",
    engine="sqlite",
    seed=47,
    generate=g.generate_clinical_encounters,
    tags=("ehr", "healthcare", "phi"),
    tables=(
        CatalogTable(
            table_name="encounters",
            display_name="Encounters",
            knowledge=KnowledgeBundle(
                business_context=(
                    "Patient encounter records from the EHR: admissions, discharges, "
                    "diagnoses, and billing. Feeds clinical quality measures and "
                    "revenue-cycle reporting."
                ),
                known_issues=(
                    "Occasional discharge_at before admit_at; malformed MRNs; missing patient "
                    "links; invalid diagnosis codes; rare negative length_of_stay or "
                    "billed_amount."
                ),
                importance="critical",
                owner="Dr. Sara Haddad",
                domain="Healthcare",
                team="Clinical Data",
                freshness_sla_hours=24,
                pii_columns=("patient_name", "mrn", "dob", "ssn", "address"),
                notes=(
                    "PHI — strictly governed under HIPAA. MRN format is 'MRN' + 6 digits. "
                    "PII columns are redacted from LLM prompts."
                ),
            ),
            consumers=("Revenue cycle", "Clinical quality measures", "Population health"),
            contract_terms="PHI access is audited; minimum-necessary applies to all consumers.",
            quality=(
                _av("encounter_type", g._ENC_TYPES, "Encounter type in agreed set"),
                _av("status", g._ENC_STATUS, "Encounter status in agreed set"),
                QualityClause("mrn-format", "MRN well-formed", "regex_match", "mrn",
                              {"pattern": r"^MRN\d{6}$"}, "error",
                              rationale="Medical record number must be 'MRN' + 6 digits."),
                QualityClause("notnull-patient", "patient_id present", "not_null", "patient_id"),
                QualityClause("notnull-enc", "encounter_id present", "not_null", "encounter_id"),
                QualityClause("nonneg-los", "length_of_stay non-negative", "range",
                              "length_of_stay", {"min": 0}, "error",
                              rationale="Length of stay cannot be negative."),
            ),
            extra_checks=(
                ExtraCheck(
                    name="encounters: discharge_at not before admit_at",
                    check_type="custom_sql",
                    params={"sql": (
                        "SELECT encounter_id, admit_at, discharge_at FROM encounters "
                        "WHERE discharge_at IS NOT NULL AND discharge_at < admit_at"
                    )},
                    severity="error",
                    rationale="A patient cannot be discharged before admission.",
                ),
            ),
            slas=(SlaSpec("Encounters check success", objective=0.99),),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# 7. Product subscriptions — Product                                           #
# --------------------------------------------------------------------------- #

SUBSCRIPTIONS = CatalogEntry(
    key="product-subscriptions",
    title="Product subscriptions",
    description=(
        "Subscription lifecycle and billing state from the product/billing system. The basis "
        "for MRR, churn, and seat-utilization reporting."
    ),
    domain="Product",
    source_system="Product — Subscriptions",
    engine="sqlite",
    seed=48,
    generate=g.generate_product_subscriptions,
    tags=("saas", "subscriptions", "billing"),
    tables=(
        CatalogTable(
            table_name="subscriptions",
            display_name="Subscriptions",
            knowledge=KnowledgeBundle(
                business_context=(
                    "Subscription lifecycle and billing state from the product/billing "
                    "system. The basis for MRR, churn, and seat-utilization reporting."
                ),
                known_issues=(
                    "Rare canceled_at before started_at; plan/status casing drift; invalid "
                    "seat counts (<= 0); occasional negative MRR."
                ),
                importance="high",
                owner="Noah Park",
                domain="Product",
                team="Product Analytics",
                freshness_sla_hours=24,
                pii_columns=("account_id",),
                notes="mrr is normalized monthly recurring revenue; event_date is the snapshot date.",
            ),
            consumers=("Finance — ARR", "Growth — churn", "Customer Success"),
            quality=(
                _av("plan", g._PLANS, "Plan in agreed catalog"),
                _av("status", g._SUB_STATUS, "Subscription status in agreed set"),
                QualityClause("nonneg-seats", "seats positive", "range", "seats", {"min": 0},
                              "error", rationale="Seat count must be non-negative."),
                QualityClause("nonneg-mrr", "mrr non-negative", "range", "mrr", {"min": 0},
                              "warn", rationale="MRR cannot be negative."),
                QualityClause("notnull-sub", "subscription_id present", "not_null",
                              "subscription_id"),
                QualityClause("unique-sub", "subscription_id unique", "unique",
                              "subscription_id"),
            ),
            extra_checks=(
                ExtraCheck(
                    name="subscriptions: canceled_at not before started_at",
                    check_type="custom_sql",
                    params={"sql": (
                        "SELECT subscription_id, started_at, canceled_at FROM subscriptions "
                        "WHERE canceled_at IS NOT NULL AND canceled_at < started_at"
                    )},
                    severity="error",
                    rationale="A subscription cannot be canceled before it starts.",
                ),
            ),
            slas=(SlaSpec("Subscriptions check success", objective=0.97),),
        ),
    ),
)


CATALOG: tuple[CatalogEntry, ...] = (
    RETAIL,
    PAYMENTS,
    EMPLOYEES,
    CLICKSTREAM,
    SHIPMENTS,
    CLINICAL,
    SUBSCRIPTIONS,
)

CATALOG_BY_KEY: dict[str, CatalogEntry] = {e.key: e for e in CATALOG}


def entry_by_key(key: str) -> CatalogEntry | None:
    return CATALOG_BY_KEY.get(key)
