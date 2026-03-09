"""Add chapter versioning support

Revision ID: 025
Revises: 024
Create Date: 2026-03-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_UNIQUE = "uq_chapters_novel_chapter_number"
_NEW_UNIQUE = "uq_chapters_novel_chapter_version"
_NEW_INDEX = "ix_chapters_novel_chapter_version"


def _existing_unique_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    uniques = inspector.get_unique_constraints("chapters")
    return {
        str(item.get("name") or "").strip()
        for item in uniques
        if str(item.get("name") or "").strip()
    }


def _existing_index_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    indexes = inspector.get_indexes("chapters")
    return {
        str(item.get("name") or "").strip()
        for item in indexes
        if str(item.get("name") or "").strip()
    }


def _ensure_version_number_column() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {col["name"] for col in inspector.get_columns("chapters")}
    if "version_number" in existing_columns:
        return
    op.add_column(
        "chapters",
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
    )


def upgrade() -> None:
    _ensure_version_number_column()

    dialect = context.get_context().dialect.name
    unique_names = _existing_unique_names()
    index_names = _existing_index_names()

    if dialect == "sqlite":
        with op.batch_alter_table("chapters") as batch_op:
            if _OLD_UNIQUE in unique_names:
                batch_op.drop_constraint(_OLD_UNIQUE, type_="unique")
            if _NEW_UNIQUE not in unique_names:
                batch_op.create_unique_constraint(
                    _NEW_UNIQUE,
                    ["novel_id", "chapter_number", "version_number"],
                )
            if _NEW_INDEX not in index_names:
                batch_op.create_index(
                    _NEW_INDEX,
                    ["novel_id", "chapter_number", "version_number"],
                    unique=False,
                )
        return

    if _OLD_UNIQUE in unique_names:
        op.drop_constraint(_OLD_UNIQUE, "chapters", type_="unique")
    if _NEW_UNIQUE not in unique_names:
        op.create_unique_constraint(
            _NEW_UNIQUE,
            "chapters",
            ["novel_id", "chapter_number", "version_number"],
        )
    if _NEW_INDEX not in index_names:
        op.create_index(
            _NEW_INDEX,
            "chapters",
            ["novel_id", "chapter_number", "version_number"],
            unique=False,
        )


def downgrade() -> None:
    # Roll back to one row per chapter_number so old unique constraint can be restored.
    op.execute(
        """
        DELETE FROM chapters
        WHERE id NOT IN (
            SELECT MAX(id) FROM chapters GROUP BY novel_id, chapter_number
        )
        """
    )

    dialect = context.get_context().dialect.name
    unique_names = _existing_unique_names()
    index_names = _existing_index_names()

    if dialect == "sqlite":
        with op.batch_alter_table("chapters") as batch_op:
            if _NEW_INDEX in index_names:
                batch_op.drop_index(_NEW_INDEX)
            if _NEW_UNIQUE in unique_names:
                batch_op.drop_constraint(_NEW_UNIQUE, type_="unique")
            if _OLD_UNIQUE not in unique_names:
                batch_op.create_unique_constraint(
                    _OLD_UNIQUE,
                    ["novel_id", "chapter_number"],
                )
            batch_op.drop_column("version_number")
        return

    if _NEW_INDEX in index_names:
        op.drop_index(_NEW_INDEX, table_name="chapters")
    if _NEW_UNIQUE in unique_names:
        op.drop_constraint(_NEW_UNIQUE, "chapters", type_="unique")
    if _OLD_UNIQUE not in unique_names:
        op.create_unique_constraint(
            _OLD_UNIQUE,
            "chapters",
            ["novel_id", "chapter_number"],
        )
    op.drop_column("chapters", "version_number")
