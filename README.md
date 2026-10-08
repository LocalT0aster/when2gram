# When2Gram

Telegram-native, When2Meet-style group scheduling without a separate web app.

The project is intentionally Telegram-first: organizers create an event in the bot's private chat, share it through inline mode, and participants fill availability using an inline-keyboard timetable. Shared invitations are designed to update with the current response count and best-overlap preview.

## MVP scope

- organizer-defined 00:00–24:00 event windows at 15-minute resolution
- paged 15×4 availability grid with row/column headers and styled count buttons
- When2Meet-like event creation across multiple dates
- inline invitations with live response count and a deep link to the bot's PM
- organizer-defined response threshold notification
- SQLite persistence
- `uv` for dependency management
- single-container `compose.yml` deployment

The current implementation includes the domain model, SQLite schema/migration, an event-relative availability representation of up to 96 quarter-hour slots per day, a `/grid` command for validating the dense Telegram keyboard UX, and a private-chat `/new` wizard for creating events. Organizers choose an hourly event window, record their own availability before sharing, can edit it later from the preview, and can view aggregate participant responses.

## Architecture

```text
Telegram
   |
   v
aiogram routers  ---> keyboard / inline adapters
   |
   v
application/domain logic
   |
   v
SQLAlchemy 2 async
   |
   v
SQLite (WAL, foreign keys, 5s busy timeout)
```

Each participant/day availability is represented as an event-relative decimal bit mask: bit 0 is the event's start time, with up to 96 quarter-hour slots. Decimal storage avoids SQLite's 64-bit integer limit while keeping constant-time toggles and aggregation.

## Local development

Requirements: Python 3.14+ and `uv`.

```bash
cp .env.example .env
# set BOT_TOKEN in .env
uv sync
uv run alembic upgrade head
uv run python -m when2gram
```

Try `/grid` in the bot's private chat to exercise the timetable prototype, or `/new` to create an event. The event wizard supports multi-date selection, an hourly event window from 00:00–24:00, and an optional organizer notification threshold. The share button opens inline mode with an invitation result; enable inline mode first in BotFather with `/setinline` and enable `/setinlinefeedback` so sent invitations can refresh their reply count.

Run checks:

```bash
uv run ruff check .
uv run pytest
```

## Docker Compose

```bash
cp .env.example .env
# set BOT_TOKEN

docker compose up -d --build
```

The SQLite database is persisted in the named `when2gram-data` volume at `/data/when2gram.db`. Long polling is used, so no inbound port, reverse proxy, or TLS setup is required.

## Planned implementation order

1. Validate the availability grid and event-creation wizard on Telegram mobile and desktop clients.
2. Add a participant availability summary and best-overlap display.
3. Refresh shared invitations after submissions.
4. Add exactly-once response-threshold notifications.
5. Add integration tests around Telegram callbacks and SQLite concurrency.
