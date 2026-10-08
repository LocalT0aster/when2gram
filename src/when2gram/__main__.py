import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage

from when2gram.bot.routers import event_creation_router, inline_router, start_router
from when2gram.config import get_settings
from when2gram.db import claim_due_target_notifications, create_engine, create_session_factory


async def main() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required to run When2Gram")

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["session_factory"] = session_factory
    dispatcher.include_router(event_creation_router)
    dispatcher.include_router(inline_router)
    dispatcher.include_router(start_router)

    try:
        async with session_factory() as session, session.begin():
            overdue_notifications = await claim_due_target_notifications(session)
        for notification in overdue_notifications:
            try:
                await bot.send_message(
                    notification.organizer_id,
                    f"{notification.event_title} reached {notification.response_count} replies.",
                )
            except TelegramAPIError:
                logging.exception("Unable to send overdue response-target notification")
        await dispatcher.start_polling(bot)
    finally:
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
