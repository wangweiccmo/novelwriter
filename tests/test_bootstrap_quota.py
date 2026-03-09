import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.core import bootstrap as bootstrap_mod
from app.database import Base
from app.models import BootstrapJob, Chapter, Novel, QuotaReservation, User
from app.schemas import BootstrapTriggerRequest


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


@pytest.fixture(scope="function")
def hosted_settings(_force_selfhost_settings):  # ensure conftest bootstrap runs first
    import app.config as config_mod
    from app.config import Settings

    prev = config_mod._settings_instance
    config_mod._settings_instance = Settings(deploy_mode="hosted", _env_file=None)
    try:
        yield
    finally:
        config_mod._settings_instance = prev


@pytest.fixture
def world_api(monkeypatch):
    from app.api import world

    async def _noop_bootstrap_job(*args, **kwargs):
        return None

    def _drop_background_task(coro):
        coro.close()

        class _DoneTask:
            def done(self):
                return True

        return _DoneTask()

    monkeypatch.setattr(world, "run_bootstrap_job", _noop_bootstrap_job)
    monkeypatch.setattr(world.asyncio, "create_task", _drop_background_task)
    return world


@pytest.fixture
def hosted_user(db, hosted_settings):
    user = User(
        username="hosted_user",
        hashed_password="x",
        role="admin",
        is_active=True,
        generation_quota=3,
        feedback_submitted=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def novel_with_text(db, hosted_user):
    novel = Novel(
        title="Bootstrap Quota",
        author="Tester",
        file_path="/tmp/bootstrap_quota.txt",
        total_chapters=1,
        owner_id=hosted_user.id,
    )
    db.add(novel)
    db.commit()
    db.refresh(novel)
    db.add(
        Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="One",
            content=("Alice met Bob in the city. " * 80).strip(),
        )
    )
    db.commit()
    return novel


class _FailingAIClient:
    async def generate_structured(self, **kwargs):
        raise RuntimeError("LLM unavailable")


class _SuccessfulAIClient:
    async def generate_structured(self, **kwargs):
        response_model = kwargs["response_model"]
        return response_model.model_validate(
            {
                "entities": [
                    {"name": "Alice", "entity_type": "Character", "aliases": []},
                    {"name": "Bob", "entity_type": "Character", "aliases": []},
                ],
                "relationships": [
                    {"source_name": "Alice", "target_name": "Bob", "label": "ally"},
                ],
            }
        )


@pytest.mark.asyncio
async def test_bootstrap_failure_refunds_reserved_quota(world_api, db, hosted_user, novel_with_text):
    before = hosted_user.generation_quota
    trigger = await world_api.trigger_bootstrap(
        novel_id=novel_with_text.id,
        body=BootstrapTriggerRequest(mode="initial"),
        db=db,
        current_user=hosted_user,
    )

    db.refresh(hosted_user)
    assert hosted_user.generation_quota == before - 1

    await bootstrap_mod.run_bootstrap_job(
        trigger.job_id,
        session_factory=TestingSessionLocal,
        client=_FailingAIClient(),
        user_id=hosted_user.id,
    )

    db.expire_all()
    job = db.query(BootstrapJob).filter(BootstrapJob.id == trigger.job_id).first()
    reservation = db.query(QuotaReservation).filter(QuotaReservation.id == job.quota_reservation_id).first()
    db.refresh(hosted_user)

    assert job is not None
    assert job.status == "failed"
    assert reservation is not None
    assert reservation.charged_count == 0
    assert reservation.released_at is not None
    assert hosted_user.generation_quota == before


@pytest.mark.asyncio
async def test_bootstrap_retrigger_does_not_double_charge(world_api, db, hosted_user, novel_with_text):
    baseline = hosted_user.generation_quota

    first = await world_api.trigger_bootstrap(
        novel_id=novel_with_text.id,
        body=BootstrapTriggerRequest(mode="initial"),
        db=db,
        current_user=hosted_user,
    )
    first_job = db.query(BootstrapJob).filter(BootstrapJob.id == first.job_id).first()
    assert first_job is not None
    first_reservation_id = int(first_job.quota_reservation_id)

    db.refresh(hosted_user)
    assert hosted_user.generation_quota == baseline - 1

    # Simulate that the previous attempt is no longer running before retrigger.
    first_job.status = "failed"
    db.commit()

    second = await world_api.trigger_bootstrap(
        novel_id=novel_with_text.id,
        body=BootstrapTriggerRequest(mode="initial"),
        db=db,
        current_user=hosted_user,
    )
    second_job = db.query(BootstrapJob).filter(BootstrapJob.id == second.job_id).first()
    assert second_job is not None
    second_reservation_id = int(second_job.quota_reservation_id)

    db.refresh(hosted_user)
    first_reservation = db.query(QuotaReservation).filter(QuotaReservation.id == first_reservation_id).first()
    second_reservation = db.query(QuotaReservation).filter(QuotaReservation.id == second_reservation_id).first()
    assert first_reservation is not None
    assert second_reservation is not None
    assert first_reservation.released_at is not None
    assert second_reservation.released_at is None
    # One old reservation refunded, one new reservation active: net one unit reserved.
    assert hosted_user.generation_quota == baseline - 1

    await bootstrap_mod.run_bootstrap_job(
        second.job_id,
        session_factory=TestingSessionLocal,
        client=_SuccessfulAIClient(),
        user_id=hosted_user.id,
    )

    db.expire_all()
    db.refresh(hosted_user)
    first_reservation = db.query(QuotaReservation).filter(QuotaReservation.id == first_reservation_id).first()
    second_reservation = db.query(QuotaReservation).filter(QuotaReservation.id == second_reservation_id).first()
    assert first_reservation is not None
    assert second_reservation is not None
    assert first_reservation.charged_count == 0
    assert first_reservation.released_at is not None
    assert second_reservation.charged_count == 1
    assert second_reservation.released_at is not None
    # Exactly one successful bootstrap has been charged.
    assert hosted_user.generation_quota == baseline - 1
