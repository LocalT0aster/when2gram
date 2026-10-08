from datetime import date

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from when2gram.bot.keyboards.availability import availability_keyboard
from when2gram.bot.keyboards.event_creation import (
    date_picker_keyboard,
    event_preview_keyboard,
    format_selected_days,
    response_target_keyboard,
    time_range_keyboard,
)
from when2gram.bot.routers.inline import refresh_inline_invitations
from when2gram.db.models import Event
from when2gram.db.repositories import (
    create_event,
    get_event_availability_masks,
    get_event_by_token,
    get_preferred_time_range,
    get_user_availability_masks,
    save_submitted_availability,
    upsert_user,
)
from when2gram.domain.availability import (
    SLOT_MINUTES,
    SLOTS_PER_DAY,
    aggregate_counts,
    is_selected,
    slot_label,
)

router = Router(name=__name__)
GRID_WIDTH_DELIMITER = "=" * 60


class NewEvent(StatesGroup):
    title = State()
    dates = State()
    time_range = State()
    target = State()
    custom_target = State()


class OrganizerAvailability(StatesGroup):
    selecting = State()


class EventResponses(StatesGroup):
    viewing = State()


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


@router.message(CommandStart(deep_link=True))
async def join_event(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if message.from_user is None or not command.args:
        return
    async with session_factory() as session, session.begin():
        event = await get_event_by_token(session, command.args)
        if event is None:
            await message.answer("That invitation is no longer available.")
            return
        await upsert_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
    await _start_event_availability(
        message,
        state,
        event,
        session_factory,
        replace=False,
        is_organizer=message.from_user.id == event.organizer_id,
    )


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
async def finish_dates(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    selected_days = _read_selected_days(await state.get_data())
    if not selected_days or callback.from_user is None:
        await callback.answer("Select at least one date", show_alert=True)
        return

    async with session_factory() as session:
        start_hour, end_hour = await get_preferred_time_range(session, callback.from_user.id)
    await state.set_state(NewEvent.time_range)
    await state.update_data(
        start_hour=start_hour,
        end_hour=end_hour,
        pending_start_hour=None,
    )
    if callback.message is not None:
        await _show_time_range(callback.message, state)
    await callback.answer()


def _time_range_prompt(start_hour: int, end_hour: int, pending_start_hour: int | None) -> str:
    prompt = (
        "What times might work?\n\n"
        f"Event hours: {start_hour:02d}:00 - {end_hour:02d}:00\n"
        "Tap a start hour, then an end hour. Hours use the 24-hour clock."
    )
    if pending_start_hour is not None:
        return f"{prompt}\n\nStart {pending_start_hour:02d}:00 selected. Now tap the end hour."
    return prompt


async def _show_time_range(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    start_hour = int(data["start_hour"])
    end_hour = int(data["end_hour"])
    pending_start_hour = data.get("pending_start_hour")
    if not isinstance(pending_start_hour, int):
        pending_start_hour = None
    await message.edit_text(
        _time_range_prompt(start_hour, end_hour, pending_start_hour),
        reply_markup=time_range_keyboard(
            start_hour,
            end_hour,
            pending_start_hour=pending_start_hour,
        ),
    )


@router.callback_query(NewEvent.time_range, F.data == "new:time:reset")
async def reset_time_range_selection(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.update_data(pending_start_hour=None)
    await _show_time_range(callback.message, state)
    await callback.answer("Selection reset")


@router.callback_query(NewEvent.time_range, F.data.regexp(r"^new:time:\d+$"))
async def select_time_range_hour(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    try:
        hour = int(callback.data.rsplit(":", maxsplit=1)[1])
    except ValueError:
        await callback.answer("Invalid hour", show_alert=True)
        return
    if not 0 <= hour <= 24:
        await callback.answer("Choose an hour from 00 to 24", show_alert=True)
        return

    data = await state.get_data()
    pending_start_hour = data.get("pending_start_hour")
    if not isinstance(pending_start_hour, int):
        if hour == 24:
            await callback.answer("24:00 can only be the end hour", show_alert=True)
            return
        await state.update_data(pending_start_hour=hour)
        await _show_time_range(callback.message, state)
        await callback.answer("Start hour selected")
        return
    if hour <= pending_start_hour:
        await callback.answer("The end hour must be after the start hour", show_alert=True)
        return

    await state.update_data(
        start_hour=pending_start_hour,
        end_hour=hour,
        pending_start_hour=None,
    )
    await _show_time_range(callback.message, state)
    await callback.answer()


@router.callback_query(NewEvent.time_range, F.data == "new:time:done")
async def finish_time_range(callback: CallbackQuery, state: FSMContext) -> None:
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

    event = await _persist_event(message, state, session_factory, target)
    await message.answer(f"Notification target set to {target} replies.")
    await _start_event_availability(
        message,
        state,
        event,
        session_factory,
        replace=False,
        is_organizer=True,
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
    event = await _persist_event(callback, state, session_factory, target)
    await _start_event_availability(
        callback.message,
        state,
        event,
        session_factory,
        replace=True,
        is_organizer=True,
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
    start_hour = data.get("start_hour")
    end_hour = data.get("end_hour")
    if not isinstance(start_hour, int) or not isinstance(end_hour, int):
        raise RuntimeError("event creation requires a time range")
    async with session_factory() as session, session.begin():
        event = await create_event(
            session,
            organizer_id=user.id,
            organizer_username=user.username,
            organizer_first_name=user.first_name,
            title=str(data["title"]),
            days=selected_days,
            start_minute=start_hour * 60,
            end_minute=end_hour * 60,
            response_target=target,
        )
    return event


async def _start_event_availability(
    message: Message,
    state: FSMContext,
    event: Event,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    replace: bool,
    is_organizer: bool,
) -> None:
    selected_days = _read_selected_days(await state.get_data())
    if not selected_days:
        selected_days = [event_day.day for event_day in event.days]
    user_id = message.chat.id
    async with session_factory() as session:
        masks = await get_user_availability_masks(session, event_id=event.id, user_id=user_id)
    await state.set_state(OrganizerAvailability.selecting)
    await state.set_data(
        {
            "event_id": event.id,
            "event_token": event.token,
            "organizer_id": event.organizer_id,
            "title": event.title,
            "days": [day.isoformat() for day in selected_days],
            "masks": masks,
            "day_index": 0,
            "range_start_day_index": None,
            "range_start_slot": None,
            "response_target": event.response_target,
            "availability_start_minute": event.start_minute,
            "availability_end_minute": event.end_minute,
            "time_page_start": 0,
            "is_organizer": is_organizer,
        }
    )
    if replace:
        await _show_organizer_availability(message, state)
    else:
        data = await state.get_data()
        await message.answer(
            _availability_prompt(
                _read_selected_days(data), 0, None, event.start_minute
            ),
            reply_markup=_organizer_availability_keyboard(data),
        )


def _read_masks(data: dict[str, object]) -> list[int]:
    masks = data.get("masks", [])
    if not isinstance(masks, list) or not all(
        isinstance(mask, int) and mask >= 0 for mask in masks
    ):
        return []
    return masks


def _day_index(data: dict[str, object], days: list[date]) -> int:
    index = data.get("day_index", 0)
    if not isinstance(index, int) or not 0 <= index < len(days):
        return 0
    return index


def _availability_prompt(
    days: list[date], index: int, range_start: tuple[int, int] | None, start_minute: int
) -> str:
    prompt = (
        f"{GRID_WIDTH_DELIMITER}\n"
        "What times might work for you?\n\n"
        f"{format_selected_days([days[index]])} ({index + 1}/{len(days)})\n"
        "Tap a start time, then an end time."
    )
    if range_start is not None:
        range_day_index, range_slot = range_start
        return (
            f"{prompt}\n\nFrom {format_selected_days([days[range_day_index]])} at "
            f"{slot_label(range_slot, start_minute=start_minute)} selected. "
            "Browse to the end day and time."
        )
    return prompt


def _range_start(data: dict[str, object]) -> tuple[int, int] | None:
    day_index = data.get("range_start_day_index")
    slot = data.get("range_start_slot")
    if isinstance(day_index, int) and isinstance(slot, int):
        return day_index, slot
    return None


def _organizer_availability_keyboard(data: dict[str, object]):
    days = _read_selected_days(data)
    masks = _read_masks(data)
    index = _day_index(data, days)
    range_start = _range_start(data)
    start_minute = data.get("availability_start_minute")
    end_minute = data.get("availability_end_minute")
    page_start = data.get("time_page_start")
    if not isinstance(start_minute, int) or not isinstance(end_minute, int):
        raise RuntimeError("availability requires an event time range")
    if not isinstance(page_start, int):
        page_start = 0
    return availability_keyboard(
        [0] * SLOTS_PER_DAY,
        respondent_count=1,
        selected_mask=masks[index],
        callback_prefix="availability",
        range_start=range_start[1] if range_start is not None and range_start[0] == index else None,
        day_label=f"{index + 1}/{len(days)}",
        can_go_previous=index > 0,
        can_go_next=index < len(days) - 1,
        start_minute=start_minute,
        end_minute=end_minute,
        page_start=page_start,
    )


async def _show_organizer_availability(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    days = _read_selected_days(data)
    index = _day_index(data, days)
    range_start = _range_start(data)
    start_minute = data.get("availability_start_minute")
    if not isinstance(start_minute, int):
        raise RuntimeError("availability requires an event time range")
    await message.edit_text(
        _availability_prompt(days, index, range_start, start_minute),
        reply_markup=_organizer_availability_keyboard(data),
    )


@router.callback_query(OrganizerAvailability.selecting, F.data.startswith("availability:slot:"))
async def toggle_organizer_slot(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    try:
        slot = int(callback.data.rsplit(":", maxsplit=1)[1])
    except ValueError:
        await callback.answer("Invalid slot", show_alert=True)
        return

    data = await state.get_data()
    days = _read_selected_days(data)
    masks = _read_masks(data)
    index = _day_index(data, days)
    start_minute = data.get("availability_start_minute")
    end_minute = data.get("availability_end_minute")
    if not isinstance(start_minute, int) or not isinstance(end_minute, int):
        await callback.answer("Availability expired. Open the invitation again.", show_alert=True)
        return
    slot_count = (end_minute - start_minute) // SLOT_MINUTES
    if not 0 <= slot < slot_count:
        await callback.answer("That time is outside the event window", show_alert=True)
        return
    range_start = _range_start(data)
    if range_start is None:
        await state.update_data(range_start_day_index=index, range_start_slot=slot)
        await _show_organizer_availability(callback.message, state)
        await callback.answer("Start selected")
        return

    start_day_index, start_slot = range_start
    _toggle_cross_day_range(masks, start_day_index, start_slot, index, slot, slot_count)
    await state.update_data(masks=masks, range_start_day_index=None, range_start_slot=None)
    await _show_organizer_availability(callback.message, state)
    await callback.answer()


def _toggle_cross_day_range(
    masks: list[int],
    start_day_index: int,
    start_slot: int,
    end_day_index: int,
    end_slot: int,
    slot_count: int,
) -> None:
    should_clear = is_selected(masks[start_day_index], start_slot)
    if (end_day_index, end_slot) < (start_day_index, start_slot):
        start_day_index, start_slot, end_day_index, end_slot = (
            end_day_index,
            end_slot,
            start_day_index,
            start_slot,
        )
    first_slot, last_slot = sorted((start_slot, end_slot))
    range_mask = ((1 << (last_slot - first_slot + 1)) - 1) << first_slot
    for day_index in range(start_day_index, end_day_index + 1):
        masks[day_index] = (
            masks[day_index] & ~range_mask if should_clear else masks[day_index] | range_mask
        )


async def _get_owned_event(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> Event | None:
    if callback.from_user is None or callback.data is None:
        return None
    token = callback.data.rsplit(":", maxsplit=1)[1]
    async with session_factory() as session:
        event = await get_event_by_token(session, token)
    if event is None or event.organizer_id != callback.from_user.id:
        return None
    return event


@router.callback_query(F.data.startswith("event:availability:"))
async def edit_owner_availability(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    event = await _get_owned_event(callback, session_factory)
    if event is None:
        await callback.answer("Only the organizer can edit this event", show_alert=True)
        return
    await _start_event_availability(
        callback.message,
        state,
        event,
        session_factory,
        replace=True,
        is_organizer=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("event:responses:"))
async def view_event_responses(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    event = await _get_owned_event(callback, session_factory)
    if event is None:
        await callback.answer("Only the organizer can view responses", show_alert=True)
        return
    async with session_factory() as session:
        respondent_count, masks_by_day = await get_event_availability_masks(session, event.id)
    await state.set_state(EventResponses.viewing)
    await state.set_data(
        {
            "event_token": event.token,
            "title": event.title,
            "days": [event_day.day.isoformat() for event_day in event.days],
            "response_target": event.response_target,
            "availability_start_minute": event.start_minute,
            "availability_end_minute": event.end_minute,
            "respondent_count": respondent_count,
            "masks_by_day": masks_by_day,
            "day_index": 0,
            "time_page_start": 0,
        }
    )
    await _show_event_responses(callback.message, state)
    await callback.answer()


def _read_masks_by_day(data: dict[str, object]) -> list[list[int]]:
    masks_by_day = data.get("masks_by_day")
    if not isinstance(masks_by_day, list) or not all(
        isinstance(masks, list) and all(isinstance(mask, int) and mask >= 0 for mask in masks)
        for masks in masks_by_day
    ):
        return []
    return masks_by_day


async def _show_event_responses(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    days = _read_selected_days(data)
    masks_by_day = _read_masks_by_day(data)
    index = _day_index(data, days)
    start_minute = data.get("availability_start_minute")
    end_minute = data.get("availability_end_minute")
    respondent_count = data.get("respondent_count")
    page_start = data.get("time_page_start")
    if (
        len(days) != len(masks_by_day)
        or not isinstance(start_minute, int)
        or not isinstance(end_minute, int)
        or not isinstance(respondent_count, int)
        or not isinstance(page_start, int)
    ):
        raise RuntimeError("event response view expired")
    await message.edit_text(
        f"{GRID_WIDTH_DELIMITER}\n"
        f"Availability responses: {respondent_count}\n\n"
        f"{format_selected_days([days[index]])} ({index + 1}/{len(days)})",
        reply_markup=availability_keyboard(
            aggregate_counts(masks_by_day[index]),
            respondent_count=respondent_count,
            callback_prefix="responses",
            day_label=f"{index + 1}/{len(days)}",
            can_go_previous=index > 0,
            can_go_next=index < len(days) - 1,
            start_minute=start_minute,
            end_minute=end_minute,
            page_start=page_start,
            read_only=True,
            back_callback="responses:back",
        ),
    )


@router.callback_query(
    EventResponses.viewing,
    F.data.in_({"responses:previous", "responses:next"}),
)
async def change_response_day(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    data = await state.get_data()
    days = _read_selected_days(data)
    index = _day_index(data, days)
    direction = -1 if callback.data.endswith("previous") else 1
    await state.update_data(day_index=max(0, min(index + direction, len(days) - 1)))
    await _show_event_responses(callback.message, state)
    await callback.answer()


@router.callback_query(
    EventResponses.viewing,
    F.data.in_({"responses:earlier", "responses:later"}),
)
async def change_response_time_page(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    data = await state.get_data()
    start_minute = data.get("availability_start_minute")
    end_minute = data.get("availability_end_minute")
    page_start = data.get("time_page_start")
    if (
        not isinstance(start_minute, int)
        or not isinstance(end_minute, int)
        or not isinstance(page_start, int)
    ):
        await callback.answer("Response view expired", show_alert=True)
        return
    slot_count = (end_minute - start_minute) // SLOT_MINUTES
    next_page_start = page_start - 60 if callback.data.endswith("earlier") else page_start + 60
    await state.update_data(
        time_page_start=max(0, min(next_page_start, ((slot_count - 1) // 60) * 60))
    )
    await _show_event_responses(callback.message, state)
    await callback.answer()


@router.callback_query(EventResponses.viewing, F.data == "responses:back")
async def return_to_event_preview(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    title = str(data.get("title", "Event"))
    token = str(data.get("event_token", ""))
    days = _read_selected_days(data)
    target = data.get("response_target")
    start_minute = data.get("availability_start_minute")
    end_minute = data.get("availability_end_minute")
    await state.clear()
    await callback.message.edit_text(
        _event_preview_text(
            title,
            days,
            target if isinstance(target, int) else None,
            start_minute // 60 if isinstance(start_minute, int) else 9,
            end_minute // 60 if isinstance(end_minute, int) else 24,
        ),
        reply_markup=event_preview_keyboard(token),
    )
    await callback.answer()


@router.callback_query(
    OrganizerAvailability.selecting,
    F.data.in_({"availability:earlier", "availability:later"}),
)
async def change_availability_time_page(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    data = await state.get_data()
    start_minute = data.get("availability_start_minute")
    end_minute = data.get("availability_end_minute")
    page_start = data.get("time_page_start", 0)
    if (
        not isinstance(start_minute, int)
        or not isinstance(end_minute, int)
        or not isinstance(page_start, int)
    ):
        await callback.answer("Availability expired. Open the invitation again.", show_alert=True)
        return
    slot_count = (end_minute - start_minute) // SLOT_MINUTES
    next_page_start = page_start - 60 if callback.data.endswith("earlier") else page_start + 60
    await state.update_data(
        time_page_start=max(0, min(next_page_start, ((slot_count - 1) // 60) * 60)),
    )
    await _show_organizer_availability(callback.message, state)
    await callback.answer()


@router.callback_query(OrganizerAvailability.selecting, F.data == "availability:clear")
async def clear_organizer_availability(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    days = _read_selected_days(data)
    masks = _read_masks(data)
    masks[_day_index(data, days)] = 0
    await state.update_data(masks=masks, range_start_day_index=None, range_start_slot=None)
    await _show_organizer_availability(callback.message, state)
    await callback.answer("Cleared")


@router.callback_query(
    OrganizerAvailability.selecting,
    F.data.in_({"availability:previous", "availability:next"}),
)
async def change_availability_day(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    data = await state.get_data()
    days = _read_selected_days(data)
    index = _day_index(data, days)
    direction = -1 if callback.data.endswith("previous") else 1
    await state.update_data(day_index=max(0, min(index + direction, len(days) - 1)))
    await _show_organizer_availability(callback.message, state)
    await callback.answer()


@router.callback_query(OrganizerAvailability.selecting, F.data == "availability:done")
async def submit_organizer_availability(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return
    data = await state.get_data()
    days = _read_selected_days(data)
    masks = _read_masks(data)
    event_id = data.get("event_id")
    if not isinstance(event_id, int) or len(days) != len(masks):
        await callback.answer("Availability expired. Use /new to start again.", show_alert=True)
        return

    async with session_factory() as session, session.begin():
        result = await save_submitted_availability(
            session,
            event_id=event_id,
            user_id=callback.from_user.id,
            masks=masks,
        )
    await refresh_inline_invitations(bot, session_factory, event_id)
    title = str(data["title"])
    token = str(data["event_token"])
    target = data.get("response_target")
    start_minute = data.get("availability_start_minute")
    end_minute = data.get("availability_end_minute")
    is_organizer = data.get("is_organizer") is True
    organizer_id = data.get("organizer_id")
    await state.clear()
    if result.target_reached and isinstance(organizer_id, int):
        await bot.send_message(
            organizer_id,
            f"{title} reached {result.response_count} replies.",
        )
    if is_organizer:
        await callback.message.edit_text(
            _event_preview_text(
                title,
                days,
                target if isinstance(target, int) else None,
                start_minute // 60 if isinstance(start_minute, int) else 9,
                end_minute // 60 if isinstance(end_minute, int) else 24,
            ),
            reply_markup=event_preview_keyboard(token),
        )
    else:
        await callback.message.edit_text("Availability saved. Thanks for replying.")
    await callback.answer("Availability saved")


def _event_preview_text(
    title: str,
    days: list[date],
    target: int | None,
    start_hour: int,
    end_hour: int,
) -> str:
    notification = (
        "No organizer notification" if target is None else f"Notify after {target} replies"
    )
    return (
        f"Event created\n\n{title}\n{format_selected_days(days)}\n"
        f"{start_hour:02d}:00 - {end_hour:02d}:00\n{notification}"
        "\n\nShare it when ready."
    )
