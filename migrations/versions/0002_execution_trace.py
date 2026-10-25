"""Append-only call traces committed with execution checkpoints."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_events",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_run_events_run"),
        sa.CheckConstraint("sequence > 0", name="ck_run_events_sequence"),
    )


def downgrade() -> None:
    op.drop_table("run_events")
