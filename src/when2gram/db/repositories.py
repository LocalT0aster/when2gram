from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from secrets import token_urlsafe

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import AvailabilityDay, Event, EventDay, InlineInvite, Response, User, utc_now


@dataclass(frozen=True)
class SubmissionResult:
    response_count: int
    target_reached: bool


@dataclass(frozen=True)
class TargetNotification:
    organizer_id: int
    event_title: str
    response_count: int


def event_ends_at(event: Event) -> datetime:
    """Return the server-local end timestamp for an event with loaded days."""
    if not event.days:
        raise ValueError("event has no days")
    return datetime.combine(event.days[-1].day, time.min) + timedelta(minutes=event.end_minute)


def event_is_expired(event: Event, *, now: datetime | None = None) -> bool:
    return event_ends_at(event) <= (datetime.now() if now is None else now)


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


async def update_event(
    session: AsyncSession,
    *,
    event_id: int,
    title: str | None = None,
    days: Sequence[date] | None = None,
    start_minute: int | None = None,
    end_minute: int | None = None,
    response_target: int | None = None,
    update_response_target: bool = False,
) -> Event:
    """Update an event, clearing replies when its schedule changes."""
    event = await session.scalar(
        select(Event).options(selectinload(Event.days)).where(Event.id == event_id)
    )
    if event is None:
        raise ValueError("event does not exist")

    if title is not None:
        title = title.strip()
        if not title:
            raise ValueError("event title cannot be empty")
        event.title = title

    if days is not None:
        unique_days = sorted(set(days))
        if not unique_days:
            raise ValueError("an event needs at least one day")
        if any(day < date.today() for day in unique_days):
            raise ValueError("event days cannot be in the past")
    else:
        unique_days = None
    days_changed = unique_days is not None and (
        [event_day.day for event_day in event.days] != unique_days
    )

    time_changed = False
    if start_minute is not None or end_minute is not None:
        next_start_minute = event.start_minute if start_minute is None else start_minute
        next_end_minute = event.end_minute if end_minute is None else end_minute
        if not 0 <= next_start_minute < next_end_minute <= 24 * 60:
            raise ValueError("event time range must be between 00:00 and 24:00")
        if next_start_minute % 60 or next_end_minute % 60:
            raise ValueError("event time range must use whole hours")
        time_changed = (event.start_minute, event.end_minute) != (
            next_start_minute,
            next_end_minute,
        )
        if time_changed:
            event.start_minute = next_start_minute
            event.end_minute = next_end_minute

    schedule_changed = days_changed or time_changed
    if schedule_changed:
        event_day_ids = select(EventDay.id).where(EventDay.event_id == event.id)
        await session.execute(
            delete(AvailabilityDay).where(AvailabilityDay.event_day_id.in_(event_day_ids))
        )
        await session.execute(delete(Response).where(Response.event_id == event.id))
        if days_changed:
            event.days.clear()
            await session.flush()
            event.days.extend(EventDay(day=day) for day in unique_days or [])
        event.target_notified_at = None

    if update_response_target:
        if response_target is not None and response_target < 1:
            raise ValueError("response target must be positive")
        if event.response_target != response_target:
            event.response_target = response_target
            event.target_notified_at = None

    await session.flush()
    return event


async def delete_event(session: AsyncSession, event_id: int) -> bool:
    event = await session.get(Event, event_id)
    if event is None:
        return False
    await session.delete(event)
    await session.flush()
    return True


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
    days: Sequence[date] | None = None,
) -> SubmissionResult:
    """Save one participant's day masks and mark their response submitted."""
    event = await session.get(Event, event_id)
    if event is None:
        raise ValueError("event does not exist")
    event_days = list(
        await session.scalars(
            select(EventDay).where(EventDay.event_id == event_id).order_by(EventDay.day)
        )
    )
    selected_days = event_days
    if days is not None:
        requested_days = set(days)
        if any(day < date.today() for day in requested_days):
            raise ValueError("cannot submit availability for passed days")
        selected_days = [event_day for event_day in event_days if event_day.day in requested_days]
        if len(selected_days) != len(requested_days):
            raise ValueError("availability days must belong to the event")
    if len(masks) != len(selected_days):
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

    for event_day, mask in zip(selected_days, masks, strict=True):
        availability = await session.get(AvailabilityDay, (event_day.id, user_id))
        if availability is None:
            session.add(
                AvailabilityDay(event_day_id=event_day.id, user_id=user_id, slot_mask=mask)
            )
        else:
            availability.slot_mask = mask

    await session.flush()
    response_count = await submitted_response_count(session, event_id)
    target_reached = False
    if event.response_target is not None and response_count >= event.response_target:
        result = await session.execute(
            update(Event)
            .where(Event.id == event_id, Event.target_notified_at.is_(None))
            .values(target_notified_at=utc_now())
        )
        target_reached = result.rowcount == 1
    return SubmissionResult(response_count=response_count, target_reached=target_reached)


async def withdraw_submitted_availability(
    session: AsyncSession, *, event_id: int, user_id: int
) -> int:
    """Delete a submitted response and return the remaining response count."""
    event_day_ids = select(EventDay.id).where(EventDay.event_id == event_id)
    await session.execute(
        delete(AvailabilityDay).where(
            AvailabilityDay.user_id == user_id,
            AvailabilityDay.event_day_id.in_(event_day_ids),
        )
    )
    await session.execute(
        delete(Response).where(Response.event_id == event_id, Response.user_id == user_id)
    )
    await session.flush()
    return await submitted_response_count(session, event_id)


async def get_event_by_token(session: AsyncSession, token: str) -> Event | None:
    return await session.scalar(
        select(Event).options(selectinload(Event.days)).where(Event.token == token)
    )


async def get_event_by_id(session: AsyncSession, event_id: int) -> Event | None:
    return await session.scalar(
        select(Event).options(selectinload(Event.days)).where(Event.id == event_id)
    )


async def get_organized_events(
    session: AsyncSession, organizer_id: int, *, now: datetime | None = None
) -> list[Event]:
    events = list(
        await session.scalars(
            select(Event)
            .options(selectinload(Event.days))
            .where(Event.organizer_id == organizer_id)
        )
    )
    current_time = datetime.now() if now is None else now
    return sorted(
        (event for event in events if not event_is_expired(event, now=current_time)),
        key=event_ends_at,
    )


async def delete_expired_events(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    events = list(await session.scalars(select(Event).options(selectinload(Event.days))))
    current_time = datetime.now() if now is None else now
    expired_events = [event for event in events if event_is_expired(event, now=current_time)]
    for event in expired_events:
        await session.delete(event)
    await session.flush()
    return len(expired_events)


async def get_user_availability_masks(
    session: AsyncSession,
    *,
    event_id: int,
    user_id: int,
    days: Sequence[date] | None = None,
) -> list[int]:
    statement = (
        select(EventDay.id, AvailabilityDay.slot_mask)
        .outerjoin(
            AvailabilityDay,
            and_(
                AvailabilityDay.event_day_id == EventDay.id,
                AvailabilityDay.user_id == user_id,
            ),
        )
        .where(EventDay.event_id == event_id)
        .order_by(EventDay.day)
    )
    if days is not None:
        statement = statement.where(EventDay.day.in_(days))
    rows = await session.execute(statement)
    return [int(mask or 0) for _, mask in rows]


async def get_event_availability_masks(
    session: AsyncSession, event_id: int
) -> tuple[int, list[list[int]]]:
    event_days = list(
        await session.scalars(
            select(EventDay).where(EventDay.event_id == event_id).order_by(EventDay.day)
        )
    )
    masks_by_day = [[] for _ in event_days]
    day_index = {event_day.id: index for index, event_day in enumerate(event_days)}
    rows = await session.execute(
        select(AvailabilityDay.event_day_id, AvailabilityDay.slot_mask)
        .join(EventDay, AvailabilityDay.event_day_id == EventDay.id)
        .join(
            Response,
            and_(
                Response.event_id == EventDay.event_id,
                Response.user_id == AvailabilityDay.user_id,
                Response.submitted_at.is_not(None),
            ),
        )
        .where(EventDay.event_id == event_id)
    )
    for event_day_id, mask in rows:
        masks_by_day[day_index[event_day_id]].append(mask)
    return await submitted_response_count(session, event_id), masks_by_day


async def get_slot_respondents(
    session: AsyncSession, *, event_id: int, day: date, slot: int
) -> list[tuple[str | None, str, bool]]:
    """Return submitted respondents and whether each marked one event slot."""
    rows = await session.execute(
        select(User.username, User.first_name, AvailabilityDay.slot_mask)
        .join(Response, Response.user_id == User.telegram_id)
        .join(
            EventDay,
            and_(EventDay.event_id == Response.event_id, EventDay.day == day),
        )
        .outerjoin(
            AvailabilityDay,
            and_(
                AvailabilityDay.event_day_id == EventDay.id,
                AvailabilityDay.user_id == User.telegram_id,
            ),
        )
        .where(Response.event_id == event_id, Response.submitted_at.is_not(None))
        .order_by(User.first_name, User.telegram_id)
    )
    return [
        (username, first_name, bool(int(mask or 0) & (1 << slot)))
        for username, first_name, mask in rows
    ]


async def get_inline_invite_message_ids(session: AsyncSession, event_id: int) -> list[str]:
    return list(
        await session.scalars(
            select(InlineInvite.inline_message_id).where(InlineInvite.event_id == event_id)
        )
    )


async def claim_due_target_notifications(session: AsyncSession) -> list[TargetNotification]:
    events = list(
        await session.scalars(
            select(Event).where(
                Event.response_target.is_not(None), Event.target_notified_at.is_(None)
            )
        )
    )
    notifications = []
    for event in events:
        response_count = await submitted_response_count(session, event.id)
        if response_count < event.response_target:
            continue
        result = await session.execute(
            update(Event)
            .where(Event.id == event.id, Event.target_notified_at.is_(None))
            .values(target_notified_at=utc_now())
        )
        if result.rowcount == 1:
            notifications.append(
                TargetNotification(
                    organizer_id=event.organizer_id,
                    event_title=event.title,
                    response_count=response_count,
                )
            )
    return notifications


async def submitted_response_count(session: AsyncSession, event_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Response)
            .where(Response.event_id == event_id, Response.submitted_at.is_not(None))
        )
        or 0
    )
