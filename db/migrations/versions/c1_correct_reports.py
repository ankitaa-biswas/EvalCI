"""Cycle 1 evaluation accounting and canonical reports.

The previous initial revision was empty. Create required evaluation tables on
fresh databases and add fields to existing installations without deleting data.
Legacy reports have NULL report payloads and cannot pass the new gate.
"""
from alembic import op
import sqlalchemy as sa

revision = "c1_correct_reports"
down_revision = "f072ddcd1e53"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    uid = sa.Uuid()
    if "eval_runs" not in tables:
        op.create_table("eval_runs",
            sa.Column("id", uid, primary_key=True),
            sa.Column("commit_sha", sa.String(), nullable=False),
            sa.Column("branch", sa.String(), nullable=False),
            sa.Column("triggered_by", sa.String()),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("overall_severity", sa.Float()), sa.Column("overall_score", sa.Float()),
            sa.Column("passed", sa.Boolean()), sa.Column("error_message", sa.Text()),
            sa.Column("celery_task_id", sa.String()))
    columns = {c["name"] for c in sa.inspect(bind).get_columns("eval_runs")}
    for col in [sa.Column("provenance", sa.JSON()), sa.Column("completion", sa.JSON()),
                sa.Column("evaluation_errors", sa.JSON()), sa.Column("baseline_run_id", uid),
                sa.Column("baseline_bootstrap", sa.Boolean(), nullable=False, server_default=sa.false())]:
        if col.name not in columns:
            op.add_column("eval_runs", col)
    if "eval_runs" in tables and bind.dialect.name == "postgresql":
        types = {c["name"]: c["type"] for c in sa.inspect(bind).get_columns("eval_runs")}
        for name in ("created_at", "completed_at"):
            if not types[name].timezone:
                op.alter_column("eval_runs", name, type_=sa.DateTime(timezone=True),
                                postgresql_using=f"{name} AT TIME ZONE 'UTC'")
    if "question_scores" not in tables:
        op.create_table("question_scores", sa.Column("id", uid, primary_key=True),
            sa.Column("run_id", uid, sa.ForeignKey("eval_runs.id"), nullable=False),
            *[sa.Column(k, sa.String(), nullable=False) for k in ("question_id", "category")],
            sa.Column("question_text", sa.Text(), nullable=False), sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("contexts", sa.JSON()), sa.Column("ground_truth", sa.Text()),
            *[sa.Column(k, sa.Float(), nullable=False) for k in ("correctness", "grounding", "hallucination_risk", "context_recall", "context_precision")],
            sa.Column("from_cache", sa.Boolean(), nullable=False))
    if "category_scores" not in tables:
        op.create_table("category_scores", sa.Column("id", uid, primary_key=True),
            sa.Column("run_id", uid, sa.ForeignKey("eval_runs.id"), nullable=False),
            sa.Column("category", sa.String(), nullable=False), sa.Column("question_count", sa.Integer(), nullable=False),
            *[sa.Column(k, sa.Float(), nullable=False) for k in ("avg_correctness", "avg_grounding", "avg_hallucination_risk", "avg_context_recall", "avg_context_precision")],
            sa.Column("passed", sa.Boolean(), nullable=False))
    if "fingerprint_reports" not in tables:
        op.create_table("fingerprint_reports", sa.Column("id", uid, primary_key=True),
            sa.Column("run_id", uid, sa.ForeignKey("eval_runs.id"), nullable=False),
            sa.Column("baseline_run_id", uid, sa.ForeignKey("eval_runs.id")),
            sa.Column("overall_regressed", sa.Boolean(), nullable=False),
            sa.Column("top_failing_component", sa.String()), sa.Column("summary", sa.Text()),
            sa.Column("regressions", sa.JSON()), sa.Column("action_items", sa.JSON()), sa.Column("report", sa.JSON()))
    else:
        columns = {c["name"] for c in sa.inspect(bind).get_columns("fingerprint_reports")}
        if "report" not in columns:
            op.add_column("fingerprint_reports", sa.Column("report", sa.JSON()))
        with op.batch_alter_table("fingerprint_reports") as batch:
            batch.alter_column("baseline_run_id", existing_type=uid, nullable=True)


def downgrade():
    # Preserve all evaluation data; rollback the application only after backup.
    raise RuntimeError("This data-preserving migration requires an explicit reviewed rollback.")
