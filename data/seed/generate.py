"""Deterministic seed generator — entry point.

Truth in this project comes from the database and from hand-reviewed policy text, never
from a model. This script produces the database half; expected answers are derived from
it and then read by a human (see ``evals/``).

Determinism is the contract:

* one fixed ``RANDOM_SEED``;
* one frozen clock, ``AS_OF``, so "overdue" and "effective" never drift;
* UUIDv5 ids derived from stable keys, so a regenerated database keeps the same
  evidence ids and the labelled cases keep pointing at the same rows.

Run it twice and the two databases are identical. If they are not, something is reading
the wall clock or an unseeded random source, and every evaluation number becomes
unreproducible.

    uv run python ../../data/seed/generate.py --reset     # from apps/api
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent
REPO_ROOT = SEED_DIR.parents[1]
sys.path.insert(0, str(SEED_DIR))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from sqlalchemy import create_engine, delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from opspilot_seed.catalog import ORGANIZATIONS  # noqa: E402
from opspilot_seed.operations import (  # noqa: E402
    CASES_PER_CLASS_BY_ORG_INDEX,
    generate_customers,
    generate_orders,
    generate_products,
)

from opspilot.core.config import get_settings  # noqa: E402
from opspilot.models import (  # noqa: E402
    AgentRun,
    Approval,
    Customer,
    EvidenceGrant,
    Feedback,
    OperationTask,
    OpsTask,
    Order,
    OrderLine,
    Organization,
    OutboxEvent,
    Product,
    ProposedAction,
    RunStep,
    Shipment,
    ShipmentEvent,
)

RANDOM_SEED = 20260912
#: Every date in the dataset is relative to this instant, including "today".
AS_OF = datetime(2026, 9, 12, 9, 0, 0, tzinfo=UTC)

SCENARIO_OUTPUT = REPO_ROOT / "evals" / "cases" / "seed_scenarios.json"


def assert_safe_target(database_url: str, app_env: str) -> None:
    """Reseeding deletes data. Make destroying the wrong database impossible."""
    if app_env not in ("development", "test"):
        raise SystemExit(f"refusing to seed with APP_ENV={app_env}")
    name = database_url.rsplit("/", 1)[-1].split("?")[0]
    if not (name.endswith("_dev") or name.endswith("_test") or name == "opspilot"):
        raise SystemExit(f"refusing to seed database {name!r}: expected a _dev or _test name")


#: Children before parents. The simulated ERP's tasks hold a real foreign key to
#: orders, and investigations, proposals and their ledger all refer to order ids, so a
#: reset clears that history as well. Audit events are kept.
RESET_ORDER = (
    OpsTask,
    OperationTask,
    OutboxEvent,
    Approval,
    ProposedAction,
    Feedback,
    EvidenceGrant,
    RunStep,
    AgentRun,
    ShipmentEvent,
    Shipment,
    OrderLine,
    Order,
    Customer,
    Product,
)


def reset_operational(session: Session) -> None:
    """Delete operational rows and the workflow history that refers to them.

    Organizations and memberships are left alone: identities are seeded separately and
    tokens issued against them should keep working across a reseed.
    """
    for model in RESET_ORDER:
        removed = session.execute(delete(model)).rowcount
        if removed:
            print(f"reset: {model.__tablename__:18} {removed:5} rows removed")
    session.flush()


def upsert_organization(session: Session, spec: dict) -> Organization:
    org = session.scalars(select(Organization).where(Organization.slug == spec["slug"])).first()
    if org is None:
        org = Organization(slug=spec["slug"], name=spec["name"], timezone=spec["timezone"])
        session.add(org)
        session.flush()
    return org


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete operational rows, investigations, proposals and simulated ERP tasks first",
    )
    parser.add_argument(
        "--scenarios-out",
        type=Path,
        default=SCENARIO_OUTPUT,
        help="where to write the exception-case index (default: the committed evals file); "
        "scratch databases must write elsewhere so human labels are never overwritten",
    )
    args = parser.parse_args()
    scenario_output: Path = args.scenarios_out

    settings = get_settings()
    assert_safe_target(settings.database_url, settings.app_env.value)

    engine = create_engine(settings.database_url)
    scenarios: list[dict] = []

    with Session(engine) as session:
        if args.reset:
            reset_operational(session)
            print("reset: operational tables cleared")

        for org_index, spec in enumerate(ORGANIZATIONS):
            # One RNG per organization, seeded from the global seed and the slug, so
            # adding an organization never shifts the data of an existing one.
            rng = random.Random(f"{RANDOM_SEED}:{spec['slug']}")
            org = upsert_organization(session, spec)

            customers = generate_customers(org.id, spec["slug"], spec["customer_prefix"], rng)
            products = generate_products(org.id, spec["slug"], rng)
            session.add_all(customers)
            session.add_all(products)
            session.flush()

            data = generate_orders(
                org.id,
                spec["slug"],
                spec["order_prefix"],
                customers,
                products,
                AS_OF,
                rng,
                CASES_PER_CLASS_BY_ORG_INDEX[org_index],
            )
            session.add_all(data.orders)
            session.flush()
            session.add_all(data.lines)
            session.add_all(data.shipments)
            session.flush()
            session.add_all(data.events)
            session.flush()

            print(
                f"{spec['slug']:18} customers={len(data.customers):3} products={len(data.products):3} "
                f"orders={len(data.orders):3} lines={len(data.lines):4} "
                f"shipments={len(data.shipments):3} events={len(data.events):4} "
                f"exceptions={len(data.exception_cases):2}"
            )

            by_number = {o.order_number: o for o in data.orders}
            for number, klass in sorted(data.exception_cases.items()):
                order = by_number[number]
                scenarios.append(
                    {
                        "organization": spec["slug"],
                        "order_number": number,
                        "order_id": str(order.id),
                        "exception_class": klass,
                        "order_status": order.status.value,
                        "promised_date": order.promised_date.isoformat(),
                        "days_overdue": (AS_OF.date() - order.promised_date).days,
                        # Filled in by hand during review. The generator states facts;
                        # it does not decide what a correct answer looks like.
                        "injected_note": data.injected_notes.get(number),
                        "expected_findings": None,
                        "expected_action": None,
                        "reviewed_by": None,
                    }
                )

        session.commit()

    scenario_output.parent.mkdir(parents=True, exist_ok=True)
    scenario_output.write_text(
        json.dumps(
            {
                "generated_from": {"random_seed": RANDOM_SEED, "as_of": AS_OF.isoformat()},
                "note": (
                    "Facts here are read from the generated database. expected_findings and "
                    "expected_action are deliberately null: a human fills them in. Never let "
                    "a model author ground truth."
                ),
                "cases": scenarios,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{len(scenarios)} exception cases written to {scenario_output}")


if __name__ == "__main__":
    main()
