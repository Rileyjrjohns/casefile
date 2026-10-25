"""Persist creation order independently of UUIDs, retries and worker clocks."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("created_order", sa.BigInteger(), nullable=True))
    # Legacy enqueue time was not retained. Prefer the first trace timestamp over
    # the mutable job schedule, with UUIDs as a deterministic tie-breaker.
    op.execute(
        sa.text("""
        WITH history AS (
            SELECT r.id, row_number() OVER (
                ORDER BY COALESCE((e.payload->>'at')::timestamptz, j.available_at), r.id
            ) AS position
            FROM runs r
            LEFT JOIN run_events e ON e.run_id = r.id AND e.sequence = 1
            LEFT JOIN run_jobs j ON j.run_id = r.id
        )
        UPDATE runs SET created_order = history.position FROM history WHERE runs.id = history.id
    """)
    )
    op.alter_column("runs", "created_order", nullable=False)
    op.alter_column("runs", "created_order", server_default=sa.Identity(always=True))
    op.execute(
        sa.text("""
        SELECT setval(pg_get_serial_sequence('runs', 'created_order'),
                      COALESCE(MAX(created_order), 0) + 1, false) FROM runs
    """)
    )
    op.create_index("ix_runs_case_created_order", "runs", ["case_id", "created_order"])


def downgrade() -> None:
    op.drop_index("ix_runs_case_created_order", table_name="runs")
    op.drop_column("runs", "created_order")
