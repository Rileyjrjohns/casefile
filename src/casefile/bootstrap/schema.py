"""Merge feature-owned tables; cross-feature foreign keys belong to composition."""

from sqlalchemy import ForeignKeyConstraint, MetaData

from casefile.features.case_intake.adapters.outbound.sql import IntakeBase
from casefile.features.evaluation.adapters.outbound.sql import EvaluationBase
from casefile.features.investigation.adapters.outbound.agent.journal_sql import ExecutionEventRow
from casefile.features.review.adapters.outbound.sql import ReviewBase


def database_metadata() -> MetaData:
    metadata = MetaData()
    for owned in (
        IntakeBase.metadata,
        ExecutionEventRow.metadata,
        ReviewBase.metadata,
        EvaluationBase.metadata,
    ):
        for table in owned.tables.values():
            table.to_metadata(metadata)
    metadata.tables["runs"].append_constraint(
        ForeignKeyConstraint(["case_id"], ["cases.id"], name="fk_runs_case")
    )
    metadata.tables["reviews"].append_constraint(
        ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_reviews_run")
    )
    metadata.tables["reference_examples"].append_constraint(
        ForeignKeyConstraint(["review_id"], ["reviews.id"], name="fk_reference_review")
    )
    return metadata
