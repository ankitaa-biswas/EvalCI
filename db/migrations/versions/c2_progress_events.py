"""Cycle 2 durable progress event history."""
from alembic import op
import sqlalchemy as sa

revision = "c2_progress_events"
down_revision = "c1_correct_reports"
branch_labels = None
depends_on = None


def upgrade():
    # Supports development databases previously initialized with create_all.
    if "run_events" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("run_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.Uuid(), sa.ForeignKey("eval_runs.id"), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_run_events_run_id", "run_events", ["run_id"])


def downgrade():
    op.drop_table("run_events")
