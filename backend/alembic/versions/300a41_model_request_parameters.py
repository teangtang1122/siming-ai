"""Store model-specific API request parameters independently of model discovery."""
import sqlalchemy as sa
from alembic import op
revision = "300a41_model_request_parameters"
down_revision = "300a40_recycle_bin"
branch_labels = None
depends_on = None

def upgrade():
    if not sa.inspect(op.get_bind()).has_table("api_configs"):
        return
    if "model_request_parameters" not in {row["name"] for row in sa.inspect(op.get_bind()).get_columns("api_configs")}:
        op.add_column("api_configs", sa.Column("model_request_parameters", sa.JSON(), nullable=True))

def downgrade():
    if not sa.inspect(op.get_bind()).has_table("api_configs"):
        return
    if "model_request_parameters" in {row["name"] for row in sa.inspect(op.get_bind()).get_columns("api_configs")}:
        op.drop_column("api_configs", "model_request_parameters")
