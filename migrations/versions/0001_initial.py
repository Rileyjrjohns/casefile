"""Cases, runs, leased jobs, reviews and correction references."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("alert_id", sa.Text(), nullable=False, unique=True),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_runs_case"),
        sa.CheckConstraint("revision > 0", name="ck_runs_revision"),
        sa.CheckConstraint(
            "state IN ('PENDING','GATHERING','ANALYZING','AWAITING_REVIEW','CLOSED','FAILED')",
            name="ck_runs_state",
        ),
        sa.CheckConstraint(
            "payload->>'state' = state AND payload->>'id' = id::text "
            "AND payload->>'case_id' = case_id::text",
            name="ck_runs_payload",
        ),
    )
    op.create_index("ix_runs_case_id", "runs", ["case_id"])
    op.create_index(
        "uq_runs_active_case",
        "runs",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('CLOSED','FAILED')"),
    )
    op.create_table(
        "run_jobs",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="READY"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_jobs_run"),
        sa.CheckConstraint("state IN ('READY','RUNNING','DONE')", name="ck_jobs_state"),
        sa.CheckConstraint("attempt >= 0", name="ck_jobs_attempt"),
        sa.CheckConstraint(
            "(state = 'RUNNING') = (token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_jobs_lease",
        ),
    )
    op.create_index("ix_jobs_claim", "run_jobs", ["state", "available_at"])
    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_reviews_run"),
    )
    op.create_table(
        "reference_examples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("conclusion_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("review_id", "conclusion_id", name="uq_reference_source"),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], name="fk_reference_review"),
    )


def downgrade() -> None:
    op.drop_table("reference_examples")
    op.drop_table("reviews")
    op.drop_table("run_jobs")
    op.drop_table("runs")
    op.drop_table("cases")
