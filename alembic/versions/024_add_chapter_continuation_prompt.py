"""add chapters.continuation_prompt

Revision ID: 024
Revises: 023
Create Date: 2026-03-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c.get("name") for c in inspector.get_columns("chapters")}
    if "continuation_prompt" in columns:
        return

    with op.batch_alter_table("chapters") as batch_op:
        batch_op.add_column(
            sa.Column(
                "continuation_prompt",
                sa.Text(),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c.get("name") for c in inspector.get_columns("chapters")}
    if "continuation_prompt" not in columns:
        return

    with op.batch_alter_table("chapters") as batch_op:
        batch_op.drop_column("continuation_prompt")
