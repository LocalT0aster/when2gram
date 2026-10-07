from collections.abc import Sequence
from datetime import date
from secrets import token_urlsafe

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import AvailabilityDay, Event, EventDay, Response, User, utc_now


async def create_event(
    session: AsyncSession,
    *,
    organizer_id: int,
    organizer_username: str | None,
    organizer_first_name: str,
    title: str,
    days: Sequence[date],
    start_minute: int,
    end_minute: int,
    response_target: int | None,
) -> Event:
    """Create an event and its organizer record in the current transaction."""
    title = title.strip()
    unique_days = sorted(set(days))
    if not title:
        raise ValueError("event title cannot be empty")
    if not unique_days:
        raise ValueError("an event needs at least one day")
    if not 0 <= start_minute < end_minute <= 24 * 60:
        raise ValueError("event time range must be between 00:00 and 24:00")
    if start_minute % 60 or end_minute % 60:
        raise ValueError("event time range must use whole hours")
    if response_target is not None and response_target < 1:
        raise ValueError("response target must be positive")

    await upsert_user(
        session,
        telegram_id=organizer_id,
        username=organizer_username,
        first_name=organizer_first_name,
        preferred_start_hour=start_minute // 60,
        preferred_end_hour=end_minute // 60,
    )

    event = Event(
        token=token_urlsafe(12),
        organizer_id=organizer_id,
        title=title,
        start_minute=start_minute,
        end_minute=end_minute,
        response_target=response_target,
        days=[EventDay(day=day) for day in unique_days],
    )
    session.add(event)
    await session.flush()
    return event


async def upsert_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None,
    first_name: str,
    preferred_start_hour: int | None = None,
    preferred_end_hour: int | None = None,
) -> User:
    """Create or refresh a Telegram user in the current transaction."""
    user = await session.get(User, telegram_id)
    if preferred_start_hour is not None or preferred_end_hour is not None:
        if preferred_start_hour is None or preferred_end_hour is None:
            raise ValueError("both preferred hours are required")
        if not 0 <= preferred_start_hour < preferred_end_hour <= 24:
            raise ValueError("preferred time range must be between 00:00 and 24:00")

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            preferred_start_hour=9 if preferred_start_hour is None else preferred_start_hour,
            preferred_end_hour=24 if preferred_end_hour is None else preferred_end_hour,
        )
        session.add(user)
    else:
        user.username = username
        user.first_name = first_name
        if preferred_start_hour is not None and preferred_end_hour is not None:
            user.preferred_start_hour = preferred_start_hour
            user.preferred_end_hour = preferred_end_hour

    # Event has a scalar foreign key rather than an ORM relationship to User,
    # so ensure a newly created user exists before dependent rows are inserted.
    await session.flush()
    return user


async def get_preferred_time_range(session: AsyncSession, user_id: int) -> tuple[int, int]:
    user = await session.get(User, user_id)
    if user is None:
        return (9, 24)
    return (user.preferred_start_hour, user.preferred_end_hour)


async def save_submitted_availability(
    session: AsyncSession,
    *,
    event_id: int,
    user_id: int,
    masks: Sequence[int],
) -> None:
    """Save one participant's day masks and mark their response submitted."""
    event = await session.get(Event, event_id)
    if event is None:
        raise ValueError("event does not exist")
    event_days = list(
        await session.scalars(
            select(EventDay).where(EventDay.event_id == event_id).order_by(EventDay.day)
        )
    )
    if len(masks) != len(event_days):
        raise ValueError("availability masks must match the event's days")
    if any(mask < 0 for mask in masks):
        raise ValueError("availability masks cannot be negative")
    slot_count = (event.end_minute - event.start_minute) // 15
    allowed_mask = (1 << slot_count) - 1
    if any(mask & ~allowed_mask for mask in masks):
        raise ValueError("availability masks must fit within the event time range")

    response = await session.get(Response, (event_id, user_id))
    if response is None:
        response = Response(event_id=event_id, user_id=user_id)
        session.add(response)
    response.submitted_at = utc_now()

    for event_day, mask in zip(event_days, masks, strict=True):
        availability = await session.get(AvailabilityDay, (event_day.id, user_id))
        if availability is None:
            session.add(
                AvailabilityDay(event_day_id=event_day.id, user_id=user_id, slot_mask=mask)
            )
        else:
            availability.slot_mask = mask

    await session.flush()


async def get_event_by_token(session: AsyncSession, token: str) -> Event | None:
    return await session.scalar(
        select(Event).options(selectinload(Event.days)).where(Event.token == token)
    )


async def submitted_response_count(session: AsyncSession, event_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Response)
            .where(Response.event_id == event_id, Response.submitted_at.is_not(None))
        )
        or 0
    )
