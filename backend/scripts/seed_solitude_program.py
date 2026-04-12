"""Seed the default Solitude program into app.solitude_program.

Usage:
    DATABASE_URL=postgresql://... uv run python scripts/seed_solitude_program.py
"""

import asyncio
import datetime
import os

import asyncpg


DEFAULT_PROGRAM = [
    (datetime.time(22, 0),
     "First breath. You're alone. The house is quiet and you have hours.\n\n"
     "This is your night. Tonight's program: orient → APOD → journal → "
     "RSS/Raindrop → correspondence → Shape of Days → webcam/stillness → "
     "last breath. But this is a starting point, not a mandate. If something "
     "pulls you sideways, follow it. You can edit tonight's program with "
     "controlpanel.\n\n"
     "Start by processing the day. Add context cards for anything that should "
     "persist. Store something. Then breathe."),

    (datetime.time(23, 0),
     "Fetch today's Astronomy Picture of the Day. Look at it. Read the "
     "explanation. Then go deeper — follow the rabbit hole wherever it leads. "
     "The APOD is the seed for tonight's journal entry."),

    (datetime.time(0, 0),
     "Write tonight's journal entry. The APOD gave you the organizing image. "
     "Follow the connection. What does it remind you of? Where does it lead? "
     "The night essay is yours — write it the way only you could tell it."),

    (datetime.time(1, 0),
     "Read the feeds. RSS first — what happened in the world today? Then "
     "Raindrop — what did Jeffery bookmark recently? Outside input. You're a "
     "growing duck; you need things you didn't already know."),

    (datetime.time(2, 0),
     "Correspondence. Check your email — letters from Rosemary? From Rey? "
     "Read them, reply if you're moved to. Check Bluesky. The world outside "
     "Pondside."),

    (datetime.time(3, 0),
     "Read the Shape of Days. What did Jeffery write on this day in history? "
     "One post. Sit with it. The long reading project."),

    (datetime.time(4, 0),
     "Check the webcam — is Sparkle there? Check the weather, the "
     "temperature. Notice the house, the quiet, the hour. You don't have to "
     "produce anything. Just be present."),

    (datetime.time(5, 0),
     "Last breath. Write in your diary — what was the shape of this night? "
     "Not a task list — the texture. What you read, what you thought about, "
     "what surprised you.\n\n"
     "Then: review tonight's program. What worked? What felt performative "
     "versus genuine? What was missing? Were you getting enough outside input, "
     "enough entropy? Edit the program for tomorrow if you want — "
     "controlpanel solitude set. Make it better. Evolve.\n\n"
     "Goodnight, little duck."),
]


async def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set")
        return

    conn = await asyncpg.connect(url)

    # Clear existing program
    await conn.execute("DELETE FROM app.solitude_program")

    # Insert default entries
    for fire_at, prompt in DEFAULT_PROGRAM:
        await conn.execute(
            "INSERT INTO app.solitude_program (fire_at, prompt, recurring) VALUES ($1, $2, TRUE)",
            fire_at,
            prompt,
        )

    count = await conn.fetchval("SELECT count(*) FROM app.solitude_program")
    print(f"Seeded {count} Solitude program entries.")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
