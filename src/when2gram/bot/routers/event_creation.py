from datetime import date

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from when2gram.bot.keyboards.event_creation import (
    date_picker_keyboard,
    event_preview_keyboard,
    format_selected_days,
    response_target_keyboard,
)
from when2gram.db.models import Event
from when2gram.db.repositories import create_event

router = Router(name=__name__)


class NewEvent(StatesGroup):
    title = State()
    dates = State()
    target = State()
    custom_target = State()


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _shift_month(month: date, amount: int) -> date:
    month_number = month.month - 1 + amount
    return date(month.year + month_number // 12, month_number % 12 + 1, 1)


def _read_selected_days(data: dict[str, object]) -> list[date]:
    days = data.get("days", [])
    if not isinstance(days, list) or not all(isinstance(day, str) for day in days):
        return []
    return sorted(date.fromisoformat(day) for day in days)


async def _show_date_picker(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    month = date.fromisoformat(str(data["visible_month"]))
    selected_days = _read_selected_days(data)
    await message.edit_text(
        "Select one or more dates, then tap Continue.\n"
        f"Selected: {format_selected_days(selected_days) or 'none'}",
        reply_markup=date_picker_keyboard(month, selected_days),
    )


@router.message(Command("new"))
async def begin_new_event(message: Message, state: FSMContext) -> None:
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("Create events in a private chat with me using /new.")
        return

    await state.set_state(NewEvent.title)
    await message.answer("What should this event be called?")


@router.message(NewEvent.title)
async def receive_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Send a title for the event.")
        return
    if len(title) > 255:
        await message.answer("The title must be 255 characters or fewer.")
        return

    month = _month_start(date.today())
    await state.set_state(NewEvent.dates)
    await state.set_data({"title": title, "days": [], "visible_month": month.isoformat()})
    await message.answer(
        "Select one or more dates, then tap Continue.",
        reply_markup=date_picker_keyboard(month, []),
    )


@router.callback_query(NewEvent.dates, F.data.startswith("new:month:"))
async def change_month(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return

    try:
        amount = int(callback.data.rsplit(":", maxsplit=1)[1])
    except ValueError:
        await callback.answer("Invalid month", show_alert=True)
        return

    data = await state.get_data()
    month = _shift_month(date.fromisoformat(str(data["visible_month"])), amount)
    await state.update_data(visible_month=month.isoformat())
    await _show_date_picker(callback.message, state)
    await callback.answer()


@router.callback_query(NewEvent.dates, F.data.startswith("new:date:"))
async def toggle_event_day(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return

    try:
        value = callback.data.rsplit(":", maxsplit=1)[1]
        selected_day = date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except (TypeError, ValueError):
        await callback.answer("Invalid date", show_alert=True)
        return
    if selected_day < date.today():
        await callback.answer("Choose a future date", show_alert=True)
        return

    data = await state.get_data()
    selected_days = set(_read_selected_days(data))
    if selected_day in selected_days:
        selected_days.remove(selected_day)
    else:
        selected_days.add(selected_day)
    await state.update_data(days=[day.isoformat() for day in sorted(selected_days)])
    await _show_date_picker(callback.message, state)
    await callback.answer()


@router.callback_query(NewEvent.dates, F.data == "new:dates:done")
async def finish_dates(callback: CallbackQuery, state: FSMContext) -> None:
    selected_days = _read_selected_days(await state.get_data())
    if not selected_days:
        await callback.answer("Select at least one date", show_alert=True)
        return

    await state.set_state(NewEvent.target)
    if callback.message is not None:
        await callback.message.edit_text(
            "Notify you after how many submitted replies?",
            reply_markup=response_target_keyboard(),
        )
    await callback.answer()


@router.callback_query(NewEvent.target, F.data == "new:target:custom")
async def request_custom_target(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewEvent.custom_target)
    if callback.message is not None:
        await callback.message.edit_text(
            "Send the number of submitted replies that should notify you."
        )
    await callback.answer()


@router.callback_query(NewEvent.target, F.data.startswith("new:target:"))
async def select_response_target(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if callback.data is None:
        await callback.answer()
        return

    value = callback.data.rsplit(":", maxsplit=1)[1]
    try:
        target = None if value == "none" else int(value)
    except ValueError:
        await callback.answer("Invalid notification target", show_alert=True)
        return
    if target is not None and target < 1:
        await callback.answer("Invalid notification target", show_alert=True)
        return
    await _create_and_preview(callback, state, session_factory, target)


@router.message(NewEvent.custom_target)
async def receive_custom_target(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        target = int((message.text or "").strip())
    except ValueError:
        await message.answer("Send a whole number, such as 12.")
        return
    if target < 1:
        await message.answer("The notification target must be at least 1.")
        return

    selected_days = _read_selected_days(await state.get_data())
    event = await _persist_event(message, state, session_factory, target)
    await message.answer(
        _event_preview_text(event.title, selected_days, target),
        reply_markup=event_preview_keyboard(event.token),
    )


async def _create_and_preview(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    target: int | None,
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    selected_days = _read_selected_days(await state.get_data())
    event = await _persist_event(callback, state, session_factory, target)
    await callback.message.edit_text(
        _event_preview_text(event.title, selected_days, target),
        reply_markup=event_preview_keyboard(event.token),
    )
    await callback.answer("Event created")


async def _persist_event(
    source: Message | CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    target: int | None,
) -> Event:
    user = source.from_user
    if user is None:
        raise RuntimeError("event creation requires a Telegram user")

    data = await state.get_data()
    selected_days = _read_selected_days(data)
    async with session_factory() as session, session.begin():
        event = await create_event(
            session,
            organizer_id=user.id,
            organizer_username=user.username,
            organizer_first_name=user.first_name,
            title=str(data["title"]),
            days=selected_days,
            response_target=target,
        )
    await state.clear()
    return event


def _event_preview_text(title: str, days: list[date], target: int | None) -> str:
    notification = (
        "No organizer notification" if target is None else f"Notify after {target} replies"
    )
    return (
        f"Event created\n\n{title}\n{format_selected_days(days)}\n{notification}"
        "\n\nShare it when ready."
    )
