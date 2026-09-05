from collections.abc import Generator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def install_append_only_guards(engine: Engine) -> None:
    """Install SQLite guards for audit, review, and catalog history tables."""

    if engine.dialect.name != "sqlite":
        return
    statements = []
    for table_name in (
        "audit_events",
        "citizen_chat_audit_events",
        "review_decisions",
        "published_replies",
        "department_catalog_versions",
        "department_catalog_entries",
        "catalog_import_events",
    ):
        for operation in ("UPDATE", "DELETE"):
            trigger_name = f"prevent_{table_name}_{operation.lower()}"
            statements.append(
                f"""
                CREATE TRIGGER IF NOT EXISTS {trigger_name}
                BEFORE {operation} ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, '{table_name} is append-only');
                END
                """
            )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
