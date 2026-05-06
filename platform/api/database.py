"""Database configuration and session management"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from api.config import settings

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.LOG_LEVEL == "DEBUG",
    future=True,
)

# Create async session maker
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Base class for models
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Initialize database tables"""
    # Import models so they register with Base.metadata
    from api.models import user, tenant, agent  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Also create billing tables (separate Base)
    try:
        from billing.models import Base as BillingBase
        async with engine.begin() as conn:
            await conn.run_sync(BillingBase.metadata.create_all)
    except Exception:
        pass  # billing module may not be available in all deployments


async def seed_admin():
    """Seed platform admin account from env vars if configured"""
    from api.config import settings
    from api.models.user import User

    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        return

    import logging
    logger = logging.getLogger(__name__)

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(select(User).where(User.email == settings.ADMIN_EMAIL))
        existing = result.scalar_one_or_none()

        if existing:
            # Ensure admin flag is set
            if not existing.is_platform_admin:
                existing.is_platform_admin = True
                await db.commit()
                logger.info(f"Promoted existing user {settings.ADMIN_EMAIL} to platform admin")
        else:
            # Create admin user
            from api.services.auth_svc import hash_password
            admin = User(
                email=settings.ADMIN_EMAIL,
                hashed_password=hash_password(settings.ADMIN_PASSWORD),
                display_name="Platform Admin",
                is_platform_admin=True,
            )
            db.add(admin)
            await db.commit()
            logger.info(f"Created platform admin: {settings.ADMIN_EMAIL}")
