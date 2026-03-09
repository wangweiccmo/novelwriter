"""Tests for transaction-neutral event recording."""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import reload_settings
from app.core.events import record_event
from app.database import Base
from app.models import Chapter, Novel, User, UserEvent


@pytest.fixture()
def event_tracking_session(tmp_path):
    """DB session with ENABLE_EVENT_TRACKING enabled."""
    db_path = tmp_path / "events.db"

    orig_env = {}
    env_overrides = {
        "ENABLE_EVENT_TRACKING": "true",
        "JWT_SECRET_KEY": "test-secret-key",
    }
    for key, val in env_overrides.items():
        orig_env[key] = os.environ.get(key)
        os.environ[key] = val
    reload_settings()

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        for key, orig_val in orig_env.items():
            if orig_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig_val
        reload_settings()


def test_record_event_does_not_commit_caller_session(event_tracking_session):
    db = event_tracking_session

    user = User(username="u1", hashed_password="x")
    db.add(user)
    db.commit()
    db.refresh(user)

    # Add a novel but do NOT commit. If record_event() commits the caller session,
    # this novel will become persisted (bug).
    db.add(Novel(title="t", author="", file_path="f", owner_id=user.id))

    record_event(db, user.id, "signup")

    # Caller should still be able to rollback uncommitted work.
    db.rollback()

    # Verify the novel was not persisted.
    assert db.query(Novel).count() == 0


@pytest.fixture()
def event_tracking_client(event_tracking_session, monkeypatch):
    db = event_tracking_session
    from app.api import novels
    from app.core.auth import check_generation_quota, get_current_user_or_default

    user = User(username="u1", hashed_password="x", role="admin", is_active=True, generation_quota=100)
    db.add(user)
    db.commit()
    db.refresh(user)

    novel = Novel(title="事件测试", author="", file_path="f", owner_id=user.id, total_chapters=1)
    db.add(novel)
    db.commit()
    db.refresh(novel)
    db.add(Chapter(novel_id=novel.id, chapter_number=1, title="第一章", content="云澈看向远方。"))
    db.commit()

    app = FastAPI()
    app.include_router(novels.router)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[novels.get_db] = override_get_db
    app.dependency_overrides[get_current_user_or_default] = lambda: user
    app.dependency_overrides[check_generation_quota] = lambda: user

    with TestClient(app) as client:
        yield client, db, user, novel
    app.dependency_overrides.clear()


def test_continue_strict_retry_event_is_persisted(event_tracking_client, monkeypatch):
    client, db, user, novel = event_tracking_client
    import app.core.generator as generator_mod

    calls = {"n": 0}

    async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "他名为夜渊，站在殿前。"
        return "他立在殿前，沉默良久。"

    monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

    resp = client.post(
        f"/api/novels/{novel.id}/continue",
        json={"num_versions": 1, "context_chapters": 1, "strict_mode": True},
    )
    assert resp.status_code == 200

    evt = (
        db.query(UserEvent)
        .filter(UserEvent.user_id == user.id, UserEvent.event == "continue_strict_retry")
        .order_by(UserEvent.id.desc())
        .first()
    )
    assert evt is not None
    assert evt.novel_id == novel.id
    assert (evt.meta or {}).get("attempt") == 2


def test_continue_strict_fail_event_is_persisted(event_tracking_client, monkeypatch):
    client, db, user, novel = event_tracking_client
    import app.core.generator as generator_mod

    async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
        return "他名为夜渊，站在殿前。"

    monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

    resp = client.post(
        f"/api/novels/{novel.id}/continue",
        json={"num_versions": 1, "context_chapters": 1, "strict_mode": True},
    )
    assert resp.status_code == 422

    evt = (
        db.query(UserEvent)
        .filter(UserEvent.user_id == user.id, UserEvent.event == "continue_strict_fail")
        .order_by(UserEvent.id.desc())
        .first()
    )
    assert evt is not None
    assert evt.novel_id == novel.id
    assert (evt.meta or {}).get("attempt") == 2
    assert "夜渊" in ((evt.meta or {}).get("terms") or [])


def test_continue_lore_enabled_event_is_persisted(event_tracking_client, monkeypatch):
    client, db, user, novel = event_tracking_client
    import app.core.generator as generator_mod

    async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
        return "续写内容。"

    monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

    resp = client.post(
        f"/api/novels/{novel.id}/continue",
        json={"num_versions": 1, "context_chapters": 1, "use_lorebook": True},
    )
    assert resp.status_code == 200

    evt = (
        db.query(UserEvent)
        .filter(UserEvent.user_id == user.id, UserEvent.event == "continue_lore_enabled")
        .order_by(UserEvent.id.desc())
        .first()
    )
    assert evt is not None
    assert evt.novel_id == novel.id
    assert (evt.meta or {}).get("use_lorebook") is True
