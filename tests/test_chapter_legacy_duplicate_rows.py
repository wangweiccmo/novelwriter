from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.api import novels
from app.core.auth import get_current_user_or_default
from app.database import get_db
from app.models import User


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _create_legacy_tables_and_seed() -> None:
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS chapters"))
        conn.execute(sa.text("DROP TABLE IF EXISTS novels"))
        conn.execute(
            sa.text(
                """
                CREATE TABLE novels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title VARCHAR(255) NOT NULL,
                    author VARCHAR(255) DEFAULT '',
                    file_path VARCHAR(512) NOT NULL,
                    total_chapters INTEGER DEFAULT 0,
                    window_index BLOB NULL,
                    owner_id INTEGER NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE chapters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    novel_id INTEGER NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    title VARCHAR(255) DEFAULT '',
                    content TEXT NOT NULL,
                    continuation_prompt TEXT NOT NULL DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO novels (id, title, author, file_path, total_chapters)
                VALUES (1, 'Legacy Novel', 'Tester', '/tmp/legacy.txt', 3)
                """
            )
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO chapters (id, novel_id, chapter_number, title, content, continuation_prompt)
                VALUES
                    (11, 1, 1, 'Chapter 1', 'chapter-1-content', 'chapter-1-prompt'),
                    (21, 1, 2, 'Chapter 2 old', 'chapter-2-old-content', 'chapter-2-old-prompt'),
                    (22, 1, 2, 'Chapter 2 new', 'chapter-2-new-content', 'chapter-2-new-prompt')
                """
            )
        )


@pytest.fixture(scope="function")
def db():
    _create_legacy_tables_and_seed()
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        with engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS chapters"))
            conn.execute(sa.text("DROP TABLE IF EXISTS novels"))


@pytest.fixture(scope="function")
def client(db):
    app = FastAPI()
    app.include_router(novels.router)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    fake_user = User(
        id=1,
        username="tester",
        hashed_password="x",
        role="admin",
        is_active=True,
        generation_quota=999,
    )
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_or_default] = lambda: fake_user

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_get_chapter_prefers_latest_duplicate_row(client):
    resp = client.get("/api/novels/1/chapters/2")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["id"] == 22
    assert payload["content"] == "chapter-2-new-content"
    assert payload["continuation_prompt"] == "chapter-2-new-prompt"


def test_get_chapters_deduplicates_legacy_rows(client):
    resp = client.get("/api/novels/1/chapters")
    assert resp.status_code == 200
    payload = resp.json()

    assert [item["chapter_number"] for item in payload] == [1, 2]
    chapter_2 = next(item for item in payload if item["chapter_number"] == 2)
    assert chapter_2["id"] == 22
    assert chapter_2["content"] == "chapter-2-new-content"


def test_update_chapter_updates_all_duplicate_rows(client, db):
    resp = client.put(
        "/api/novels/1/chapters/2",
        json={"content": "chapter-2-updated", "continuation_prompt": "prompt-updated"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["id"] == 22
    assert payload["content"] == "chapter-2-updated"
    assert payload["continuation_prompt"] == "prompt-updated"

    rows = db.execute(
        sa.text(
            """
            SELECT id, content, continuation_prompt
            FROM chapters
            WHERE novel_id = 1 AND chapter_number = 2
            ORDER BY id
            """
        )
    ).fetchall()
    assert len(rows) == 2
    assert all(row.content == "chapter-2-updated" for row in rows)
    assert all(row.continuation_prompt == "prompt-updated" for row in rows)


def test_delete_chapter_removes_all_duplicate_rows_and_recomputes_total(client, db):
    resp = client.delete("/api/novels/1/chapters/2")
    assert resp.status_code == 204

    chapter_count = db.execute(
        sa.text(
            """
            SELECT COUNT(*) AS c
            FROM chapters
            WHERE novel_id = 1 AND chapter_number = 2
            """
        )
    ).scalar_one()
    assert chapter_count == 0

    total_chapters = db.execute(
        sa.text(
            """
            SELECT total_chapters
            FROM novels
            WHERE id = 1
            """
        )
    ).scalar_one()
    assert total_chapters == 1
