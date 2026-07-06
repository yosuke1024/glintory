"""signals_fts

Revision ID: da4fadf39e75
Revises: 9de005508393
Create Date: 2026-07-06 19:10:55.179654

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "da4fadf39e75"
down_revision: str | Sequence[str] | None = "9de005508393"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create FTS5 Virtual Table using external content from 'signals'
    op.execute("""
        CREATE VIRTUAL TABLE signals_fts USING fts5(
            title,
            excerpt,
            author,
            content='signals',
            content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        );
    """)

    # Create Insert Trigger
    op.execute("""
        CREATE TRIGGER signals_fts_after_insert
        AFTER INSERT ON signals
        BEGIN
            INSERT INTO signals_fts(
                rowid,
                title,
                excerpt,
                author
            )
            VALUES (
                new.rowid,
                new.title,
                new.excerpt,
                COALESCE(new.author, '')
            );
        END;
    """)

    # Create Delete Trigger
    op.execute("""
        CREATE TRIGGER signals_fts_after_delete
        AFTER DELETE ON signals
        BEGIN
            INSERT INTO signals_fts(
                signals_fts,
                rowid,
                title,
                excerpt,
                author
            )
            VALUES (
                'delete',
                old.rowid,
                old.title,
                old.excerpt,
                COALESCE(old.author, '')
            );
        END;
    """)

    # Create Update Trigger
    op.execute("""
        CREATE TRIGGER signals_fts_after_update
        AFTER UPDATE OF title, excerpt, author ON signals
        BEGIN
            INSERT INTO signals_fts(
                signals_fts,
                rowid,
                title,
                excerpt,
                author
            )
            VALUES (
                'delete',
                old.rowid,
                old.title,
                old.excerpt,
                COALESCE(old.author, '')
            );

            INSERT INTO signals_fts(
                rowid,
                title,
                excerpt,
                author
            )
            VALUES (
                new.rowid,
                new.title,
                new.excerpt,
                COALESCE(new.author, '')
            );
        END;
    """)

    # Backfill existing signals if any
    op.execute("INSERT INTO signals_fts(signals_fts) VALUES ('rebuild');")


def downgrade() -> None:
    """Downgrade schema."""
    # Drop elements in order
    op.execute("DROP TRIGGER IF EXISTS signals_fts_after_update;")
    op.execute("DROP TRIGGER IF EXISTS signals_fts_after_delete;")
    op.execute("DROP TRIGGER IF EXISTS signals_fts_after_insert;")
    op.execute("DROP TABLE IF EXISTS signals_fts;")
