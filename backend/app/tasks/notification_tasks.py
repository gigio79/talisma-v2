import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.user import User
from app.services import notification_service, push_service
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _make_session_maker():
    """Create a fresh engine+session for the Celery worker event loop."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _generate_all() -> int:
    """Generate due-date alerts for every user, pushing new ones."""
    engine, session_maker = _make_session_maker()
    try:
        total = 0

        async with session_maker() as session:
            result = await session.execute(select(User))
            users = list(result.scalars().all())

        for user in users:
            try:
                async with session_maker() as session:
                    created = await notification_service.create_due_date_alerts(session, user)
                    if created:
                        pushed = await push_service.send_notifications_push(session, created)
                        logger.info(
                            "Generated %d due-date alerts for user %s (%d pushed)",
                            len(created), user.id, pushed,
                        )
                        total += len(created)
            except Exception:
                logger.exception("Failed to generate due-date alerts for user %s", user.id)

    finally:
        await engine.dispose()
    return total


@celery_app.task(name="app.tasks.notification_tasks.generate_due_date_alerts")
def generate_due_date_alerts() -> dict:
    """Celery task: generate due-date alerts for all users (idempotent)."""
    total = asyncio.run(_generate_all())
    logger.info("Due-date alert generation complete: %d alerts created", total)
    return {"generated": total}
