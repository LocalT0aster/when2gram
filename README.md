# When2Gram

Telegram-native, When2Meet-style group scheduling without a separate web app.

The project is intentionally Telegram-first: organizers create an event in the bot's private chat, share it through inline mode, and participants fill availability using an inline-keyboard timetable. Shared invitations are designed to update with the current response count and best-overlap preview.

## MVP scope

- 09:00–24:00 availability window at 15-minute resolution (60 slots/day)
- 15×4 time grid with row/column headers and styled count buttons
- When2Meet-like event creation across multiple dates
- inline invitations with live response count and a deep link to the bot's PM
- organizer-defined response threshold notification
- SQLite persistence
- `uv` for dependency management
- single-container `compose.yml` deployment

The current implementation includes the domain model, SQLite schema/migration, the 60-bit/day availability representation, a `/grid` command for validating the dense Telegram keyboard UX, and a private-chat `/new` wizard for creating events. Organizers choose an hourly event window, defaulting to their most recently used range of 09:00–24:00, before sharing an inline invitation.

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

Each participant/day availability is represented as a 60-bit integer: bit 0 is 09:00 and bit 59 is 23:45. This keeps persistence and aggregation small while still allowing constant-time toggles.

## Local development

Requirements: Python 3.14+ and `uv`.

```bash
cp .env.example .env
# set BOT_TOKEN in .env
uv sync
uv run alembic upgrade head
uv run python -m when2gram
```

Try `/grid` in the bot's private chat to exercise the timetable prototype, or `/new` to create an event. The event wizard supports multi-date selection, an hourly event window from 00:00–24:00, and an optional organizer notification threshold. The share button opens inline mode with an invitation result; enable inline mode first in BotFather with `/setinline` for the bot.

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
