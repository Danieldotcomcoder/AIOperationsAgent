"""Minimal identity seed: two organizations, four memberships.

Separate from the full dataset generator (``data/seed/generate.py``) on purpose — the
identity rows are what every other piece of work needs first, and they should not wait
on 300 orders being modelled.

Idempotent: re-running updates the existing rows rather than failing on the unique
constraint, so it is safe to run on every fresh checkout.

    uv run python ../../scripts/seed_dev_users.py     # from apps/api
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api" / "src"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from opspilot.core.config import get_settings  # noqa: E402
from opspilot.domain.enums import MembershipStatus, Role  # noqa: E402
from opspilot.models import Membership, Organization  # noqa: E402

ORGS = [
    ("northwind-supply", "Northwind Supply Co."),
    ("harbor-wholesale", "Harbor Wholesale Ltd."),
]

#: (org slug, email, display name, role). One analyst and one supervisor per org, so
#: the role boundary can be exercised from the very first authenticated request.
#: Domains are under example.com (IANA documentation use). Not .test/.invalid: those
#: are special-use names and email-validator rejects them, which EmailStr surfaces
#: as a 422 on the token route.
PEOPLE = [
    ("northwind-supply", "ana.analyst@northwind.example.com", "Ana Analyst", Role.analyst),
    ("northwind-supply", "sam.supervisor@northwind.example.com", "Sam Supervisor", Role.supervisor),
    ("harbor-wholesale", "hal.analyst@harbor.example.com", "Hal Analyst", Role.analyst),
    ("harbor-wholesale", "hana.supervisor@harbor.example.com", "Hana Supervisor", Role.supervisor),
]


def main() -> None:
    settings = get_settings()
    if not settings.app_env.is_local:
        raise SystemExit(f"refusing to seed identities with APP_ENV={settings.app_env}")

    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        orgs: dict[str, Organization] = {}
        for slug, name in ORGS:
            org = session.scalars(select(Organization).where(Organization.slug == slug)).first()
            if org is None:
                org = Organization(slug=slug, name=name)
                session.add(org)
                session.flush()
            orgs[slug] = org
            print(f"organization {slug:20} {org.id}")

        for slug, email, display_name, role in PEOPLE:
            org = orgs[slug]
            # The OIDC subject is opaque in production; locally it is derived from the
            # email so that reseeding produces stable, recognisable subjects.
            subject = f"dev|{email}"
            membership = session.scalars(
                select(Membership).where(
                    Membership.subject == subject, Membership.organization_id == org.id
                )
            ).first()
            if membership is None:
                membership = Membership(organization_id=org.id, subject=subject)
                session.add(membership)
            membership.email = email
            membership.display_name = display_name
            membership.role = role
            membership.status = MembershipStatus.active
            session.flush()
            print(f"  {role.value:11} {email:32} {membership.id}")

        session.commit()
    print("\nseeded. get a token with:")
    print(
        '  curl -X POST http://127.0.0.1:8000/auth/dev/token '
        '-H "Content-Type: application/json" '
        '-d \'{"email":"ana.analyst@northwind.example.com"}\''
    )


if __name__ == "__main__":
    main()
