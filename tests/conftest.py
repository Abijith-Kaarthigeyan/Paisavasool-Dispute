import asyncio
from datetime import datetime
from uuid import UUID, uuid4
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from main import app
from src.core.security.dependencies import get_current_user
from src.data.clients.postgres_client import get_async_db, Base
from src.data.models.postgres.user_mapping import RoleMapping, UserMapping
from src.schemas.auth import RoleName, TokenPayload

# In-memory SQLite async engine for isolated, fast test executions
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Strip schema names from metadata dynamically to support SQLite execution
for table in Base.metadata.tables.values():
    table.schema = None


@pytest.fixture(scope="session", autouse=True)
def event_loop():
    """Create an instance of the default event loop for the test session."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Initializes the database tables before tests run."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id CHAR(36) PRIMARY KEY,
                    invoice_number VARCHAR(100) NOT NULL,
                    customer_id CHAR(36) NOT NULL,
                    invoice_date VARCHAR(50),
                    due_date VARCHAR(50),
                    currency VARCHAR(10),
                    subtotal_amount DECIMAL,
                    tax_amount DECIMAL,
                    total_amount DECIMAL,
                    outstanding_amount DECIMAL,
                    status VARCHAR(50),
                    batch_id CHAR(36),
                    is_deleted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)
        )
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


class LockedAsyncSessionProxy:
    def __init__(self, session: AsyncSession):
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_lock", asyncio.Lock())

    def __getattr__(self, name):
        attr = getattr(self._session, name)
        if name in ("execute", "flush", "commit", "rollback", "refresh", "get", "scalar", "scalars", "delete", "merge"):
            async def async_wrapper(*args, **kwargs):
                async with self._lock:
                    return await attr(*args, **kwargs)
            return async_wrapper
        return attr

    def __setattr__(self, name, value):
        setattr(self._session, name, value)


@pytest.fixture
async def db_session() -> AsyncSession:
    """Provides an isolated transactional database session for a single test case."""
    async with TestSessionLocal() as session, session.begin():
        # Mock commit as a flush so we rollback changes after every test
        async def mock_commit():
            await session.flush()

        session.commit = mock_commit
        proxy = LockedAsyncSessionProxy(session)
        yield proxy
        await session.rollback()


@pytest.fixture(autouse=True)
def override_db_dependency(db_session: AsyncSession):
    """Overrides the FastAPI DB dependency to inject our isolated session."""
    async def _override_db():
        yield db_session

    app.dependency_overrides[get_async_db] = _override_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_auth_service():
    """Mocks calls to the auth service selectively for routing tests."""
    from unittest.mock import MagicMock, patch
    import httpx
    from src.core.config.settings import settings

    original_get = httpx.AsyncClient.get

    mock_me_response = MagicMock()
    mock_me_response.status_code = 200
    mock_me_response.json.return_value = {
        "id": "00000000-0000-0000-0000-000000000101",
        "email": "manager@paisavasool.com",
        "role": {"role_name": "FINANCE_MANAGER"},
        "is_active": True,
    }

    async def mock_get_impl(self, url, *args, **kwargs):
        if str(url).startswith(settings.AUTH_SERVICE_URL):
            return mock_me_response
        return await original_get(self, url, *args, **kwargs)

    with patch("httpx.AsyncClient.get", new=mock_get_impl):
        yield


@pytest.fixture
async def seed_users(db_session: AsyncSession):
    """Seeds RoleMapping and UserMapping records required for testing workload and manager mappings."""
    # Seed roles
    role_assoc = await db_session.scalar(select(RoleMapping).where(RoleMapping.role_name == "FINANCE_ASSOCIATE"))
    if not role_assoc:
        role_assoc = RoleMapping(
            id=uuid4(),
            role_name="FINANCE_ASSOCIATE",
            description="Finance Associate",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db_session.add(role_assoc)

    role_mgr = await db_session.scalar(select(RoleMapping).where(RoleMapping.role_name == "FINANCE_MANAGER"))
    if not role_mgr:
        role_mgr = RoleMapping(
            id=uuid4(),
            role_name="FINANCE_MANAGER",
            description="Finance Manager",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db_session.add(role_mgr)

    await db_session.flush()

    # Seed manager
    manager_id = UUID("00000000-0000-0000-0000-000000000101")
    manager = await db_session.get(UserMapping, manager_id)
    if not manager:
        manager = UserMapping(
            id=manager_id,
            email="manager@paisavasool.com",
            first_name="Jane",
            last_name="Manager",
            password_hash="mock",
            role_id=role_mgr.id,
            manager_id=None,
            is_active=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db_session.add(manager)

    # Seed associate
    associate_id = UUID("00000000-0000-0000-0000-000000000102")
    associate = await db_session.get(UserMapping, associate_id)
    if not associate:
        associate = UserMapping(
            id=associate_id,
            email="associate@paisavasool.com",
            first_name="John",
            last_name="Associate",
            password_hash="mock",
            role_id=role_assoc.id,
            manager_id=manager_id,
            is_active=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db_session.add(associate)

    await db_session.flush()
    return {"manager": manager, "associate": associate}


@pytest.fixture
def mock_client():
    """Provides a test client configured with ASGI transport."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
