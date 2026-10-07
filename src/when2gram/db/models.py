from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(255))
    preferred_start_hour: Mapped[int] = mapped_column(Integer, default=9)
    preferred_end_hour: Mapped[int] = mapped_column(Integer, default=24)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    organizer_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    start_minute: Mapped[int] = mapped_column(Integer, default=9 * 60)
    end_minute: Mapped[int] = mapped_column(Integer, default=24 * 60)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=15)
    response_target: Mapped[int | None] = mapped_column(Integer)
    target_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    days: Mapped[list[EventDay]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventDay.day",
    )


class EventDay(Base):
    __tablename__ = "event_days"
    __table_args__ = (UniqueConstraint("event_id", "day", name="uq_event_days_event_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)

    event: Mapped[Event] = relationship(back_populates="days")


class Response(Base):
    __tablename__ = "responses"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        primary_key=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class AvailabilityDay(Base):
    __tablename__ = "availability_days"

    event_day_id: Mapped[int] = mapped_column(
        ForeignKey("event_days.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        primary_key=True,
    )
    slot_mask: Mapped[int] = mapped_column(BigInteger, default=0)


class InlineInvite(Base):
    __tablename__ = "inline_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    inline_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    sent_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
