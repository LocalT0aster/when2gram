from aiogram import Bot, F, Router
from aiogram.types import (
    ChosenInlineResult,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from when2gram.bot.keyboards.event_creation import format_selected_days
from when2gram.db.models import InlineInvite
from when2gram.db.repositories import (
    get_event_by_token,
    submitted_response_count,
    upsert_user,
)

router = Router(name=__name__)


@router.inline_query(F.query.startswith("event:"))
async def event_invitation_query(
    inline_query: InlineQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token = inline_query.query.removeprefix("event:").strip()
    async with session_factory() as session:
        event = await get_event_by_token(session, token)
        if event is None:
            await inline_query.answer([], cache_time=0, is_personal=True)
            return
        response_count = await submitted_response_count(session, event.id)

    bot_user = await bot.get_me()
    if not bot_user.username:
        await inline_query.answer([], cache_time=0, is_personal=True)
        return

    dates = format_selected_days([event_day.day for event_day in event.days])
    hours = f"{event.start_minute // 60:02d}:00 - {event.end_minute // 60:02d}:00"
    message_text = f"{event.title}\n{dates}\n{hours}\n\n{response_count} replied"
    deep_link = f"https://t.me/{bot_user.username}?start={event.token}"
    result = InlineQueryResultArticle(
        id=f"event:{event.token}",
        title=event.title,
        description=f"{dates}, {hours} - {response_count} replied",
        input_message_content=InputTextMessageContent(message_text=message_text),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Mark my availability", url=deep_link)]
            ]
        ),
    )
    await inline_query.answer([result], cache_time=0, is_personal=True)


@router.chosen_inline_result(F.result_id.startswith("event:"))
async def capture_inline_invitation(
    chosen_result: ChosenInlineResult,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if chosen_result.inline_message_id is None:
        return
    token = chosen_result.result_id.removeprefix("event:")
    async with session_factory() as session, session.begin():
        event = await get_event_by_token(session, token)
        if event is None:
            return
        await upsert_user(
            session,
            telegram_id=chosen_result.from_user.id,
            username=chosen_result.from_user.username,
            first_name=chosen_result.from_user.first_name,
        )
        existing = await session.scalar(
            select(InlineInvite).where(
                InlineInvite.inline_message_id == chosen_result.inline_message_id
            )
        )
        if existing is None:
            session.add(
                InlineInvite(
                    event_id=event.id,
                    inline_message_id=chosen_result.inline_message_id,
                    sent_by_user_id=chosen_result.from_user.id,
                )
            )
