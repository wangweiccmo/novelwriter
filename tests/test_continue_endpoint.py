"""
Tests for POST /novels/{novel_id}/continue.

Focus:
- WorldModel context injection is assembled and injected into the prompt
- context_chapters overrides settings.max_context_chapters

Network calls are avoided by mocking ai_client.generate.
"""

import math
import pytest
import json
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models import (
    Chapter,
    Continuation,
    Novel,
    WorldEntity,
    WorldRelationship,
    WorldSystem,
)


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def novel(db):
    n = Novel(title="逆天邪神", author="火星引力", file_path="/tmp/test.txt", total_chapters=2)
    db.add(n)
    db.commit()
    db.refresh(n)

    db.add_all(
        [
            Chapter(novel_id=n.id, chapter_number=1, title="第一章", content="云澈踏入宗门，心如止水。"),
            Chapter(novel_id=n.id, chapter_number=2, title="第二章", content="楚月仙在大殿中静坐，气息如渊。"),
        ]
    )
    db.commit()
    return n


@pytest.fixture
def world(db, novel):
    yunche = WorldEntity(
        novel_id=novel.id,
        name="云澈",
        entity_type="Character",
        description="主角",
        status="confirmed",
    )
    chuyuexian = WorldEntity(
        novel_id=novel.id,
        name="楚月仙",
        entity_type="Character",
        description="师父",
        status="confirmed",
    )
    db.add_all([yunche, chuyuexian])
    db.commit()
    db.refresh(yunche)
    db.refresh(chuyuexian)

    rel = WorldRelationship(
        novel_id=novel.id,
        source_id=yunche.id,
        target_id=chuyuexian.id,
        label="师徒",
        description="共同修炼",
        visibility="active",
        status="confirmed",
    )
    system = WorldSystem(
        novel_id=novel.id,
        name="修炼体系",
        display_type="list",
        description="玄气修炼等级",
        data={"items": [{"label": "真玄境", "visibility": "active"}]},
        constraints=["突破需要契机"],
        visibility="active",
        status="confirmed",
    )
    db.add_all([rel, system])
    db.commit()
    return {"yunche": yunche, "chuyuexian": chuyuexian}


@pytest.fixture
def client(db, monkeypatch):
    from app.api import novels

    test_app = FastAPI()
    test_app.include_router(novels.router)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    test_app.dependency_overrides[get_db] = override_get_db

    from app.core.auth import get_current_user_or_default, check_generation_quota
    from app.models import User

    fake_user = User(
        id=1, username="t", hashed_password="x", role="admin", is_active=True,
        generation_quota=999, feedback_submitted=False,
    )
    test_app.dependency_overrides[get_current_user_or_default] = lambda: fake_user
    test_app.dependency_overrides[check_generation_quota] = lambda: fake_user

    captured: dict[str, object] = {}

    async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        captured["max_tokens"] = max_tokens
        captured["kwargs"] = kwargs
        return "续写内容"

    async def fake_generate_stream(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs):
        captured["stream_prompt"] = prompt
        captured["stream_system_prompt"] = system_prompt
        captured["stream_max_tokens"] = max_tokens
        captured["stream_kwargs"] = kwargs
        # Two chunks to simulate incremental streaming.
        yield "续"
        yield "写"

    import app.core.generator as generator_mod

    monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)
    monkeypatch.setattr(generator_mod.ai_client, "generate_stream", fake_generate_stream)

    with TestClient(test_app) as c:
        yield c, captured
    test_app.dependency_overrides.clear()


class TestContinueEndpoint:
    def test_injects_world_context_and_returns_debug(self, client, db, novel, world):
        c, captured = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "context_chapters": 2},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["continuations"]) == 1
        assert data["continuations"][0]["chapter_number"] == 3

        debug = data["debug"]
        assert debug["context_chapters"] == 2
        assert "修炼体系" in debug["injected_systems"]
        assert "云澈" in debug["injected_entities"]
        assert "楚月仙" in debug["injected_entities"]
        assert any("师徒" in r for r in debug["injected_relationships"])

        prompt_used = str(captured.get("prompt") or "")
        assert "<world_knowledge>" in prompt_used
        assert "修炼体系" in prompt_used
        assert "云澈" in prompt_used
        assert "楚月仙" in prompt_used

        # Also persisted to DB for traceability.
        cont = db.query(Continuation).filter(Continuation.novel_id == novel.id).one()
        assert "<world_knowledge>" in cont.prompt_used

    def test_context_chapters_override_affects_relevance(self, client, novel, world):
        c, captured = client

        # Only use the last chapter. It mentions 楚月仙 but not 云澈.
        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"context_chapters": 1},
        )
        assert resp.status_code == 200
        data = resp.json()

        debug = data["debug"]
        assert debug["context_chapters"] == 1
        assert debug["injected_entities"] == ["楚月仙"]
        assert debug["injected_relationships"] == []

        prompt_used = str(captured.get("prompt") or "")
        assert "楚月仙" in prompt_used
        assert "云澈" not in debug["injected_entities"]

    def test_target_chars_use_single_call_with_estimated_budget_and_trim(self, client, novel, monkeypatch):
        c, _ = client

        import app.core.generator as generator_mod

        calls: list[int] = []
        prompts: list[str] = []
        system_prompts: list[str] = []

        async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
            calls.append(max_tokens)
            prompts.append(prompt)
            system_prompts.append(system_prompt)
            return "甲" * 3990 + "。" + "乙" * 300 + "。"

        monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "context_chapters": 2, "target_chars": 4000},
        )
        assert resp.status_code == 200

        content = resp.json()["continuations"][0]["content"]
        assert len(content) <= 4000
        assert content.endswith("。")
        assert len(calls) == 1
        settings = generator_mod.get_settings()
        expected_max_tokens = math.ceil(4000 * settings.continuation_chars_to_tokens_ratio)
        expected_max_tokens = math.ceil(expected_max_tokens * (1 + settings.continuation_token_buffer_ratio))
        expected_max_tokens = min(settings.max_continuation_tokens, max(100, expected_max_tokens))
        expected_prompt_target = max(
            4000,
            math.ceil(4000 * settings.continuation_prompt_target_overrun_ratio),
        )
        assert calls[0] == expected_max_tokens
        assert "<length_control>" not in prompts[0]
        # Length guidance is in system prompt only
        assert f"约{expected_prompt_target}字" in system_prompts[0]
        assert "【长度纪律】" in system_prompts[0]
        # Style anchor and recent chapters at the end of user prompt
        assert "无缝衔接原文风格" in prompts[0]
        assert prompts[0].rstrip().endswith("请续写第3章：")

    def test_custom_target_chars_supported_in_custom_mode(self, client, novel, monkeypatch):
        c, _ = client

        import app.core.generator as generator_mod

        calls: list[int] = []

        async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
            calls.append(max_tokens)
            return "甲" * 2600 + "。"

        monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={
                "num_versions": 1,
                "length_mode": "custom",
                "target_chars": 2500,
                "context_chapters": 2,
            },
        )
        assert resp.status_code == 200
        content = resp.json()["continuations"][0]["content"]
        assert len(content) <= 2500

        settings = generator_mod.get_settings()
        expected_max_tokens = math.ceil(2500 * settings.continuation_chars_to_tokens_ratio)
        expected_max_tokens = math.ceil(expected_max_tokens * (1 + settings.continuation_token_buffer_ratio))
        expected_max_tokens = min(settings.max_continuation_tokens, max(100, expected_max_tokens))
        assert calls[0] == expected_max_tokens

    def test_custom_target_chars_without_length_mode_remains_compatible(self, client, novel, monkeypatch):
        c, _ = client

        import app.core.generator as generator_mod

        async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
            return "甲" * 2400 + "。"

        monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "target_chars": 2200, "context_chapters": 2},
        )
        assert resp.status_code == 200
        content = resp.json()["continuations"][0]["content"]
        assert len(content) <= 2200

    def test_preset_mode_rejects_non_preset_target_chars(self, client, novel):
        c, _ = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "length_mode": "preset", "target_chars": 2500},
        )
        assert resp.status_code == 422

    def test_custom_mode_requires_target_chars(self, client, novel):
        c, _ = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "length_mode": "custom"},
        )
        assert resp.status_code == 422

    def test_user_prompt_is_part_of_relevance_signal(self, client, novel, world):
        c, captured = client

        # context_chapters=1 would normally exclude 云澈, but the user instruction mentions him.
        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"context_chapters": 1, "prompt": "请继续写云澈的内心戏"},
        )
        assert resp.status_code == 200
        data = resp.json()

        debug = data["debug"]
        assert "楚月仙" in debug["injected_entities"]
        assert "云澈" in debug["injected_entities"]

        prompt_used = str(captured.get("prompt") or "")
        assert "<world_knowledge>" in prompt_used
        assert "云澈" in prompt_used

    def test_context_chapters_above_cap_falls_back_to_five(self, client, db, novel):
        c, _captured = client

        for idx in range(3, 8):
            db.add(
                Chapter(
                    novel_id=novel.id,
                    chapter_number=idx,
                    title=f"第{idx}章",
                    content=f"这是第 {idx} 章内容。",
                )
            )
        db.commit()

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"context_chapters": 99},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["debug"]["context_chapters"] == 5

    def test_num_versions_above_default_cap_rejected(self, client, novel):
        c, _ = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 5, "context_chapters": 2},
        )
        assert resp.status_code == 422

    def test_num_versions_respects_settings_cap(self, client, novel, monkeypatch):
        c, _ = client

        monkeypatch.setattr("app.schemas.get_settings", lambda: SimpleNamespace(max_continue_versions=2))
        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 3, "context_chapters": 2},
        )
        assert resp.status_code == 422

    def test_use_lorebook_true_injects_supplementary_lore_and_debug(self, client, novel, monkeypatch):
        c, captured = client

        import app.core.generator as generator_mod

        class FakeLoreManager:
            def get_injection_context(self, chapter_text: str, max_tokens: int):
                return "秘史设定", ["a", "b"], 321

        monkeypatch.setattr(generator_mod.cache_manager, "get_lore", lambda novel_id: FakeLoreManager())

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "context_chapters": 2, "use_lorebook": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        prompt_used = str(captured.get("prompt") or "")
        assert "<supplementary_lore>" in prompt_used
        assert "秘史设定" in prompt_used
        assert body["debug"]["lore_hits"] == 2
        assert body["debug"]["lore_tokens_used"] == 321

    def test_use_lorebook_false_skips_supplementary_lore_and_debug(self, client, novel, monkeypatch):
        c, captured = client

        import app.core.generator as generator_mod

        class FakeLoreManager:
            def get_injection_context(self, chapter_text: str, max_tokens: int):
                return "秘史设定", ["a", "b"], 321

        monkeypatch.setattr(generator_mod.cache_manager, "get_lore", lambda novel_id: FakeLoreManager())

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "context_chapters": 2, "use_lorebook": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        prompt_used = str(captured.get("prompt") or "")
        assert "<supplementary_lore>" not in prompt_used
        assert body["debug"]["lore_hits"] == 0
        assert body["debug"]["lore_tokens_used"] == 0

    def test_strict_mode_retries_once_and_returns_repaired_continuation(self, client, db, novel, monkeypatch):
        c, _ = client

        import app.core.generator as generator_mod

        call_count = {"n": 0}

        async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "他名为夜渊，站在殿前。"
            return "他立在殿前，沉默良久。"

        monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "context_chapters": 2, "strict_mode": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["continuations"][0]["content"] == "他立在殿前，沉默良久。"
        assert call_count["n"] == 2
        assert db.query(Continuation).filter(Continuation.novel_id == novel.id).count() == 1

    def test_strict_mode_returns_422_and_rolls_back_persisted_outputs_when_retry_still_fails(
        self, client, db, novel, monkeypatch
    ):
        c, _ = client

        import app.core.generator as generator_mod

        async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
            return "他名为夜渊，站在殿前。"

        monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "context_chapters": 2, "strict_mode": True},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "postcheck_strict_failed"
        assert "夜渊" in detail["terms"]
        assert db.query(Continuation).filter(Continuation.novel_id == novel.id).count() == 0


class TestContinueStreamEndpoint:
    def test_stream_yields_ndjson_events_and_includes_variant_content(self, client, novel):
        c, captured = client

        headers = {
            "x-llm-base-url": "https://user.example.com/v1",
            "x-llm-api-key": "user-key",
            "x-llm-model": "user-model",
        }
        resp = c.post(
            f"/api/novels/{novel.id}/continue/stream",
            json={"num_versions": 2, "context_chapters": 2},
            headers=headers,
        )
        assert resp.status_code == 200

        lines = [ln for ln in resp.text.splitlines() if ln.strip()]
        events = [json.loads(ln) for ln in lines]

        assert events[0]["type"] == "start"
        assert events[0]["total_variants"] == 2

        token_text = "".join(e["content"] for e in events if e["type"] == "token" and e["variant"] == 0)
        assert token_text == "续写"

        done0 = next(e for e in events if e["type"] == "variant_done" and e["variant"] == 0)
        done1 = next(e for e in events if e["type"] == "variant_done" and e["variant"] == 1)
        assert done0["content"] == "续写"
        assert done1["content"] == "续写内容"

        done = next(e for e in events if e["type"] == "done")
        assert done["continuation_ids"] == [done0["continuation_id"], done1["continuation_id"]]

        # BYOK headers are passed through to both streaming and non-streaming generation calls.
        assert captured.get("stream_kwargs", {}).get("base_url") == "https://user.example.com/v1"
        assert captured.get("stream_kwargs", {}).get("api_key") == "user-key"
        assert captured.get("stream_kwargs", {}).get("model") == "user-model"

        assert captured.get("kwargs", {}).get("base_url") == "https://user.example.com/v1"
        assert captured.get("kwargs", {}).get("api_key") == "user-key"
        assert captured.get("kwargs", {}).get("model") == "user-model"

    def test_stream_supports_four_variants(self, client, novel):
        c, _ = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue/stream",
            json={"num_versions": 4, "context_chapters": 2},
        )
        assert resp.status_code == 200

        events = [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]
        assert events[0]["type"] == "start"
        assert events[0]["total_variants"] == 4
        done_events = [e for e in events if e["type"] == "variant_done"]
        assert [e["variant"] for e in done_events] == [0, 1, 2, 3]

    def test_stream_strict_mode_emits_postcheck_strict_failed_error_event(self, client, db, novel, monkeypatch):
        c, _ = client

        import app.core.generator as generator_mod

        async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
            return "他名为夜渊，站在殿前。"

        monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

        resp = c.post(
            f"/api/novels/{novel.id}/continue/stream",
            json={"num_versions": 1, "context_chapters": 2, "strict_mode": True},
        )
        assert resp.status_code == 200
        events = [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]
        assert events[0]["type"] == "start"
        err = next(e for e in events if e["type"] == "error")
        assert err["code"] == "postcheck_strict_failed"
        assert db.query(Continuation).filter(Continuation.novel_id == novel.id).count() == 0

    def test_stream_strict_mode_retries_once_and_returns_variant_done_without_tokens(
        self, client, novel, monkeypatch
    ):
        c, _ = client

        import app.core.generator as generator_mod

        calls = {"n": 0}

        async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                return "他名为夜渊，站在殿前。"
            return "他立在殿前，沉默良久。"

        monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

        resp = c.post(
            f"/api/novels/{novel.id}/continue/stream",
            json={"num_versions": 1, "context_chapters": 2, "strict_mode": True},
        )
        assert resp.status_code == 200

        events = [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]
        assert events[0]["type"] == "start"
        assert not any(e["type"] == "token" for e in events)
        variant_done = next(e for e in events if e["type"] == "variant_done")
        assert variant_done["content"] == "他立在殿前，沉默良久。"
        assert calls["n"] == 2

    def test_get_continuations_preserves_requested_order(self, client, novel):
        c, _ = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue/stream",
            json={"num_versions": 2, "context_chapters": 2},
        )
        assert resp.status_code == 200
        events = [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]
        done0 = next(e for e in events if e["type"] == "variant_done" and e["variant"] == 0)
        done1 = next(e for e in events if e["type"] == "variant_done" and e["variant"] == 1)

        resp2 = c.get(f"/api/novels/{novel.id}/continuations?ids={done1['continuation_id']},{done0['continuation_id']}")
        assert resp2.status_code == 200
        data = resp2.json()
        assert [row["id"] for row in data] == [done1["continuation_id"], done0["continuation_id"]]

    def test_stream_strips_thinking_blocks_from_non_stream_variants(self, client, novel, monkeypatch):
        c, _ = client

        import app.core.generator as generator_mod

        async def fake_generate(prompt: str, system_prompt: str = "", max_tokens: int = 0, **kwargs) -> str:
            return "<think>Step-by-step reasoning...</think>\n续写内容"

        monkeypatch.setattr(generator_mod.ai_client, "generate", fake_generate)

        resp = c.post(
            f"/api/novels/{novel.id}/continue/stream",
            json={"num_versions": 2, "context_chapters": 2},
        )
        assert resp.status_code == 200

        events = [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]
        done1 = next(e for e in events if e["type"] == "variant_done" and e["variant"] == 1)
        assert done1["content"] == "续写内容"


class TestTemperaturePassthrough:
    def test_temperature_passed_to_generate(self, client, novel):
        c, captured = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "context_chapters": 2, "temperature": 1.5},
        )
        assert resp.status_code == 200
        assert captured["kwargs"]["temperature"] == 1.5

    def test_temperature_passed_to_stream(self, client, novel):
        c, captured = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue/stream",
            json={"num_versions": 1, "context_chapters": 2, "temperature": 0.3},
        )
        assert resp.status_code == 200
        assert captured["stream_kwargs"]["temperature"] == 0.3

    def test_temperature_omitted_uses_default(self, client, novel):
        c, captured = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "context_chapters": 2},
        )
        assert resp.status_code == 200
        # temperature should NOT appear in kwargs when not provided
        assert "temperature" not in captured["kwargs"]

    def test_temperature_validation_rejects_out_of_range(self, client, novel):
        c, _ = client

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "temperature": -0.1},
        )
        assert resp.status_code == 422

        resp = c.post(
            f"/api/novels/{novel.id}/continue",
            json={"num_versions": 1, "temperature": 2.1},
        )
        assert resp.status_code == 422


class TestLorebookConfigResolution:
    def test_resolve_use_lorebook_honors_request_override(self, monkeypatch):
        from app.api import novels
        from app.schemas import ContinueRequest

        monkeypatch.setattr(
            novels,
            "get_settings",
            lambda: SimpleNamespace(continuation_use_lorebook_default=False),
        )

        assert novels._resolve_use_lorebook(ContinueRequest(num_versions=1, use_lorebook=True)) is True
        assert novels._resolve_use_lorebook(ContinueRequest(num_versions=1, use_lorebook=False)) is False

    def test_resolve_use_lorebook_follows_server_default_when_request_not_set(self, monkeypatch):
        from app.api import novels
        from app.schemas import ContinueRequest

        monkeypatch.setattr(
            novels,
            "get_settings",
            lambda: SimpleNamespace(continuation_use_lorebook_default=True),
        )
        assert novels._resolve_use_lorebook(ContinueRequest(num_versions=1)) is True
