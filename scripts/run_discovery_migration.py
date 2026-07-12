#!/usr/bin/env python3
import os
import sqlite3
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Setup path so it can import glintory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from glintory.domain.models import Opportunity
from glintory.services.opportunity_rebuild_service import OpportunityRebuildService


def main():
    db_url = os.environ.get(
        "GLINTORY_DATABASE_URL", "sqlite:///.state/public-glintory.sqlite3"
    )
    print(f"Connecting to database: {db_url}")

    if not db_url.startswith("sqlite:///"):
        print("Only SQLite databases are supported.")
        sys.exit(1)

    db_path = db_url[10:]
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # 1. Create table state_metadata using sqlite3 directly before SQLAlchemy engine start
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS state_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()

        # Check existing marker
        cursor.execute(
            "SELECT value FROM state_metadata WHERE key = ?",
            ("discovery_model_version",),
        )
        row = cursor.fetchone()
        marker = row[0] if row else None
    finally:
        conn.close()

    print(f"Current discovery_model_version marker: {marker}")

    # 2. Setup SQLAlchemy Session
    engine = create_engine(db_url)
    session_class = sessionmaker(bind=engine)
    session = session_class()

    try:
        # Check if rebuild is required based on DB opportunities
        # If any opportunity has gate_version != 'v3' or cluster_version != 'v2', we must rebuild
        needs_migration = False
        if marker != "clustering-v2-gate-v3":
            needs_migration = True
        else:
            # Dual check: are there any records that are NOT migrated?
            unmigrated_opps_count = (
                session.query(Opportunity)
                .filter(
                    (Opportunity.gate_version != "v3")
                    | (Opportunity.cluster_version != "v2")
                )
                .count()
            )
            if unmigrated_opps_count > 0:
                print(
                    f"Found {unmigrated_opps_count} unmigrated opportunities. Re-triggering migration."
                )
                needs_migration = True

        if not needs_migration:
            print("Discovery model migration already up-to-date. Skipping.")
            return

        print("Triggering full Discovery Model Migration (clustering-v2-gate-v3)...")

        # Instantiate rebuild service
        service = OpportunityRebuildService(session)

        # Execute non-destructive rebuild with signal reclassification
        result = service.rebuild_v2(
            from_version="v2",
            to_version="v2",
            reclassify_signals=True,
            cluster_version="v2",
            gate_version="v3",
        )

        print("Rebuild success:")
        print(f"  Source Opportunities: {result['source_opportunities']}")
        print(f"  Source Signals: {result['source_signals']}")
        print(f"  Created v2 Opportunities: {result['created_v2_opportunities']}")
        print(f"  Updated v2 Opportunities: {result['updated_v2_opportunities']}")
        print(
            f"  Gate Passed: {result['gate_passed']}, Rejected: {result['gate_rejected']}"
        )

        # 3. Save migration marker in state_metadata
        # We use raw sql session execution to ensure it is committed in the same transaction
        session.execute(
            text(
                "INSERT OR REPLACE INTO state_metadata (key, value) VALUES (:key, :value)"
            ),
            {"key": "discovery_model_version", "value": "clustering-v2-gate-v3"},
        )

        session.commit()
        print("Discovery model migration completed and marker saved successfully.")

    except Exception as e:
        session.rollback()
        print(f"ERROR: Discovery model migration failed: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
