"""add bootstrap_jobs.quota_reservation_id

Revision ID: 023
Revises: 022
Create Date: 2026-03-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c.get("name") for c in inspector.get_columns("bootstrap_jobs")}
    if "quota_reservation_id" in columns:
        return

    with op.batch_alter_table("bootstrap_jobs") as batch_op:
        batch_op.add_column(sa.Column("quota_reservation_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_bootstrap_jobs_quota_reservation_id",
            "quota_reservations",
            ["quota_reservation_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c.get("name") for c in inspector.get_columns("bootstrap_jobs")}
    if "quota_reservation_id" not in columns:
        return

    with op.batch_alter_table("bootstrap_jobs") as batch_op:
        try:
            batch_op.drop_constraint("fk_bootstrap_jobs_quota_reservation_id", type_="foreignkey")
        except Exception:
            pass
        batch_op.drop_column("quota_reservation_id")
