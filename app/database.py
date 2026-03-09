import os
import logging
from pathlib import Path
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR = Path(os.getenv("SCNGS_DATA_DIR", DEFAULT_DATA_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = f"sqlite:///{DATA_DIR}/novels.db"

_is_sqlite = DATABASE_URL.startswith("sqlite")

if _is_sqlite:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
else:
    engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

_SQLITE_COMPAT_COLUMNS: dict[str, dict[str, str]] = {
    "chapters": {
        # 024 migration introduced this field. Old selfhost DBs created via create_all()
        # may miss it and fail ORM SELECTs/INSERTs.
        "continuation_prompt": "TEXT NOT NULL DEFAULT ''",
        # 025 migration introduced chapter multi-versioning. Old local DBs may miss it.
        "version_number": "INTEGER NOT NULL DEFAULT 1",
    }
}

_CHAPTERS_VERSIONED_UNIQUE = ("novel_id", "chapter_number", "version_number")
_CHAPTERS_LEGACY_UNIQUE = ("novel_id", "chapter_number")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_sqlite_schema_compatibility() -> None:
    """Backfill known SQLite columns that older local DBs may be missing."""
    if not _is_sqlite:
        return

    logger = logging.getLogger(__name__)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    for table_name, required_columns in _SQLITE_COMPAT_COLUMNS.items():
        if table_name not in table_names:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column_name, ddl in required_columns.items():
            if column_name in existing_columns:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ADD COLUMN {column_name} {ddl}"
                        )
                    )
                logger.warning(
                    "Added missing SQLite schema column %s.%s",
                    table_name,
                    column_name,
                )
            except Exception as exc:  # pragma: no cover - defensive duplicate/race guard
                if "duplicate column name" in str(exc).lower():
                    continue
                raise

    _ensure_sqlite_chapter_versioning_constraints(logger=logger)


def _ensure_sqlite_chapter_versioning_constraints(*, logger: logging.Logger) -> None:
    """Repair legacy chapter uniqueness so multi-version writes work on old SQLite DBs."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "chapters" not in table_names:
        return

    unique_constraints = inspector.get_unique_constraints("chapters")
    unique_cols = {
        tuple(str(col).strip() for col in (item.get("column_names") or []) if col)
        for item in unique_constraints
    }

    has_versioned_unique = _CHAPTERS_VERSIONED_UNIQUE in unique_cols
    has_legacy_unique = _CHAPTERS_LEGACY_UNIQUE in unique_cols

    indexes = inspector.get_indexes("chapters")
    index_names = {str(item.get("name") or "").strip() for item in indexes if item.get("name")}
    has_versioned_index = "ix_chapters_novel_chapter_version" in index_names

    if has_versioned_unique and has_versioned_index:
        return

    # If the table does not expose the expected versioned unique constraint, rebuild it
    # into the current canonical schema. This fixes legacy DBs that still enforce
    # (novel_id, chapter_number) uniqueness and causes 409 on version append.
    if not has_versioned_unique:
        logger.warning(
            "Repairing legacy SQLite chapter uniqueness constraints for multi-version support"
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE chapters__compat_new (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        novel_id INTEGER NOT NULL,
                        chapter_number INTEGER NOT NULL,
                        version_number INTEGER NOT NULL DEFAULT 1,
                        title VARCHAR(255),
                        content TEXT NOT NULL,
                        continuation_prompt TEXT NOT NULL DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_chapters_novel_chapter_version
                            UNIQUE (novel_id, chapter_number, version_number),
                        FOREIGN KEY(novel_id) REFERENCES novels (id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO chapters__compat_new (
                        id,
                        novel_id,
                        chapter_number,
                        version_number,
                        title,
                        content,
                        continuation_prompt,
                        created_at,
                        updated_at
                    )
                    SELECT
                        id,
                        novel_id,
                        chapter_number,
                        ROW_NUMBER() OVER (
                            PARTITION BY novel_id, chapter_number
                            ORDER BY COALESCE(version_number, 1), id
                        ) AS version_number,
                        title,
                        content,
                        COALESCE(continuation_prompt, '') AS continuation_prompt,
                        created_at,
                        updated_at
                    FROM chapters
                    ORDER BY id
                    """
                )
            )
            conn.execute(text("DROP TABLE chapters"))
            conn.execute(text("ALTER TABLE chapters__compat_new RENAME TO chapters"))

    # Always ensure the composite index exists for current query paths.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chapters_novel_chapter_version
                ON chapters (novel_id, chapter_number, version_number)
                """
            )
        )


def init_db():
    from app.config import get_settings

    settings = get_settings()
    if not settings.db_auto_create:
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            if "novels" in tables:
                _ensure_sqlite_schema_compatibility()
                return
            logging.getLogger(__name__).warning(
                "Database missing core tables; creating schema via metadata.create_all(). "
                "Consider running Alembic migrations or enabling DB_AUTO_CREATE."
            )
        except Exception:
            return

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_schema_compatibility()
