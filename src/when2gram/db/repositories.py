from collections.abc import Sequence
from datetime import date
from secrets import token_urlsafe

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event, EventDay, User


async def create_event(
    session: AsyncSession,
    *,
    organizer_id: int,
    organizer_username: str | None,
    organizer_first_name: str,
    title: str,
    days: Sequence[date],
    response_target: int | None,
) -> Event:
    """Create an event and its organizer record in the current transaction."""
    title = title.strip()
    unique_days = sorted(set(days))
    if not title:
        raise ValueError("event title cannot be empty")
    if not unique_days:
        raise ValueError("an event needs at least one day")
    if response_target is not None and response_target < 1:
        raise ValueError("response target must be positive")

    organizer = await session.get(User, organizer_id)
    if organizer is None:
        session.add(
            User(
                telegram_id=organizer_id,
                username=organizer_username,
                first_name=organizer_first_name,
            )
        )
    else:
        organizer.username = organizer_username
        organizer.first_name = organizer_first_name

    # Event has a scalar foreign key rather than an ORM relationship to User,
    # so ensure a newly created organizer exists before inserting the event.
    await session.flush()

    event = Event(
        token=token_urlsafe(12),
        organizer_id=organizer_id,
        title=title,
        response_target=response_target,
        days=[EventDay(day=day) for day in unique_days],
    )
    session.add(event)
    await session.flush()
    return event
