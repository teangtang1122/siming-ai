"""Retire free-model activation jobs without touching saved models or stories."""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "300a39_retire_free_onboarding"
down_revision = "300a38_candidate_envelope"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    tables = sa.inspect(connection).get_table_names()
    metadata = sa.MetaData()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    message = "快速开始已改为 API 接入，旧免费模型准备任务已停止"
    next_action = "打开快速开始，获取并配置 API Key，完成连接测试"
    if "operation_runs" in tables:
        operations = sa.Table("operation_runs", metadata, autoload_with=connection)
        old_jobs = operations.c.source_kind == "opencode_activation"
        # Preserve completed/failed results and events, but retire all controls
        # that formerly resumed a removed worker.
        connection.execute(operations.update().where(old_jobs).values(
            can_pause=False, can_cancel=False, can_retry=False,
            resume_url="/getting-started", next_action=next_action,
        ))
        connection.execute(operations.update().where(old_jobs).where(
            operations.c.status.in_([
                "queued", "running", "waiting_user", "paused", "interrupted", "blocked",
            ])
        ).values(
            status="cancelled", health_status="completed", phase="retired",
            current_message=message, attention_json=None,
            completed_at=now, updated_at=now,
        ))
    if "opencode_activation_jobs" in tables:
        # This table is retained only as upgrade history; new databases no
        # longer create it and no runtime code reads or resumes its jobs.
        jobs = sa.Table("opencode_activation_jobs", metadata, autoload_with=connection)
        connection.execute(jobs.update().where(
            jobs.c.status.in_(["pending", "running", "auth_required"])
        ).values(
            status="cancelled", phase="retired", message=message,
            next_action=next_action, completed_at=now, updated_at=now,
        ))


def downgrade():
    # Restoring an older binary must not restart an obsolete activation job.
    pass
