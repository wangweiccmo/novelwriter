from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool


def test_init_db_backfills_legacy_chapters_continuation_prompt(
    tmp_path: Path,
    monkeypatch,
):
    from app import database

    db_path = tmp_path / "legacy.db"
    legacy_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    with legacy_engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE novels (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    author VARCHAR(255),
                    file_path VARCHAR(512) NOT NULL,
                    total_chapters INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE chapters (
                    id INTEGER PRIMARY KEY,
                    novel_id INTEGER NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    title VARCHAR(255),
                    content TEXT NOT NULL,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO novels (id, title, author, file_path, total_chapters)
                VALUES (1, 'N', '', 'n.txt', 1)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO chapters (id, novel_id, chapter_number, title, content)
                VALUES (1, 1, 1, 'C1', 'body')
                """
            )
        )

    monkeypatch.setattr(database, "engine", legacy_engine)
    monkeypatch.setattr(database, "_is_sqlite", True)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(db_auto_create=False),
    )

    database.init_db()

    chapter_columns = {col["name"] for col in inspect(legacy_engine).get_columns("chapters")}
    assert "continuation_prompt" in chapter_columns

    with legacy_engine.begin() as conn:
        prompt_value = conn.execute(
            text("SELECT continuation_prompt FROM chapters WHERE id = 1")
        ).scalar_one()
    assert prompt_value == ""

