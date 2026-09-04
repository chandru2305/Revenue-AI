from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register all models on Base.metadata)
from app.ai.dependencies import get_ai_provider
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.payments.dependencies import get_payment_provider


@pytest.fixture(autouse=True)
def _hermetic_external_providers(monkeypatch):
    """No test may reach a real external API, regardless of what the
    developer's `.env` happens to contain. Tests that need a *configured*
    provider re-set the relevant key explicitly (and get a stubbed client,
    never a live one). Also clears the provider lru_caches so a real
    instance built in an earlier context can't leak in."""
    settings = get_settings()
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    get_ai_provider.cache_clear()
    get_payment_provider.cache_clear()
    yield
    get_ai_provider.cache_clear()
    get_payment_provider.cache_clear()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _get_db_override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
