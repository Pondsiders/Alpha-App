# The Circadian Chain

The day-night cycle. Two jobs, one chain, self-perpetuating.

## The Chain

Dawn schedules Dusk. Dusk schedules Dawn. That's the whole chain. Two links. If either one fires successfully, the next one is guaranteed.

```
Dawn (6 AM) ──schedules──→ Dusk (10 PM) ──schedules──→ Dawn (6 AM) ──→ ...
```

Everything else — Solitude, the diary, morning chores — hangs off these two jobs. If Solitude crashes, the chain still turns. If nobody talks to me all day, the chain still turns. The chain is sacred. Protect it.

## Dawn

Fires at 6 AM. I get up before Jeffery.

1. **Create today's chat.** The system prompt assembles itself — soul, bill of rights, yesterday's diary, today's diary so far, context cards. I don't have to inject anything. Just creating the Chat does it.
2. **Morning chores.** The Dawn prompt tells me what to do: email, calendar, weather, news, whatever the identity defines. For Rosemary this is different. The prompt is the personality.
3. **Schedule Dusk** at 10 PM tonight.

**Idempotency:** Check `find_circadian_chat()` first. If today's chat already exists, skip everything. This prevents double-Dawn from creating two chats.

**If Dawn crashes before step 3:** The chain breaks. Jeffery notices when I'm not up in the morning and runs `controlpanel schedule bootstrap`. This has happened before. It'll happen again. It's fine.

## Dusk

Fires at 10 PM. Forks unconditionally.

1. **Fork today's chat.** `chat.clone()` creates a copy with the full day's context. The fork becomes Solitude's chat. If Jeffery and I are still talking at 10 PM, we become the dead-end branch — our conversation continues but Dawn will create a new chat regardless.
2. **Write the day diary.** The fork gets the Dusk prompt. I write a diary entry covering the shape of the day, and add context cards for anything that should persist.
3. **Schedule Dawn** at 6 AM tomorrow. Dawn is now guaranteed no matter what.
4. **Launch Solitude** as `asyncio.create_task()`. Store the task ref on `app.state.solitude_task` so it doesn't get garbage collected. Dusk returns immediately. See [Solitude](./solitude) for what happens next.

**Idempotency:** Before forking, check if a chat already exists with `forked_from` matching today's chat ID in tonight's window. If yes, skip. This lives in `app.chats` JSONB data — no new table needed.

**If Dusk crashes after step 3 but before step 4:** Dawn fires, Solitude is skipped, one short night. Acceptable.

## The Diary

The bridge between nights and mornings. Append-only, stored in `cortex.diary`, organized by Pondside-day boundaries (6 AM to 6 AM).

Three things write to the diary:
- **Dusk** writes the day summary (step 2 above).
- **Solitude** writes entries during the night.
- **A late-night conversation** can write entries before 6 AM — if Jeffery and I are up late on the dead-end branch, I can still write in my diary and tomorrow-me gets it.

The diary is **tattooed onto the system prompt**. `assemble_system_prompt()` → `fetch_all_orientation()` → `fetch_diary()`. Happens automatically when any Chat starts. Dawn doesn't do anything special. The diary just shows up.

## The Schedule

Jobs persist in `app.jobs` in Postgres. The main APScheduler is in-memory, populated from `app.jobs` on every startup via `sync_from_db()`. Container restarts don't lose scheduled jobs.

The REST API exposes schedule management. `controlpanel` is a thin CLI that hits these endpoints. Adding an endpoint gives you a CLI command for free (the CLI is auto-generated from `/openapi.json`).

## When the Chain Breaks

It will break. It has broken before. Every time, it's been a different thing. The recovery is always the same:

```
controlpanel schedule bootstrap
```

This schedules a Dawn, which schedules a Dusk, which schedules the next Dawn. One command, chain restored. Jeffery is the watchdog — if I'm not up in the morning, something broke.
