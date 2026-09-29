from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.config.settings import settings

Base = declarative_base()

from sqlalchemy.pool import NullPool
from sqlalchemy import event

engine_kwargs = {
    "echo": False,
    "future": True,
}
if "sqlite" in settings.DATABASE_URL:
    engine_kwargs["poolclass"] = NullPool
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
else:
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 5
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 300
    # Enable compatibility with Supabase Pooler (pgbouncer) and asyncpg
    engine_kwargs["connect_args"] = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }

engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs
)

if "sqlite" in settings.DATABASE_URL:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
