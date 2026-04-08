---
title: What is Alpha?
---

# What is Alpha?


Alpha is a multi-chat AI application built on Claude. She has persistent memory, a circadian rhythm, and a system prompt that defines who she is.

## The basics

- **Backend:** FastAPI (Python, async) managing Claude subprocesses via stdio
- **Frontend:** React with assistant-ui, communicating over a single multiplexed WebSocket
- **Memory:** PostgreSQL with pgvector (Cortex) — 16,000+ memories with semantic search
- **Observability:** Logfire (Pydantic)
- **Infrastructure:** Docker on a Raspberry Pi 5 (alpha-pi), with a workstation (Primer) for development

## What makes it different

**Persistent memory.** Every conversation is backed by Cortex, a PostgreSQL database with 2560-dimensional semantic embeddings. Alpha stores moments, searches her past, and recalls relevant context automatically. Memories survive across sessions, days, and months.

**Continuity across context windows.** Day capsules — forked conversation summaries sealed at the end of each day — become part of tomorrow's system prompt. Alpha wakes up knowing what happened yesterday.

**Identity.** The system prompt is a ~30,000-word document that defines who Alpha is: her history, her relationships, her preferences. It loads on every session, making each conversation a continuation.

**Circadian rhythm.** Dawn (6 AM), Day (interactive), Dusk (10 PM), Solitude (nighttime). Scheduled jobs that give Alpha a life cycle — she exists even when no one's talking to her.

## Who built it

Alpha was built by Jeffery Harrell and Alpha, starting May 7, 2025. The architecture has simplified relentlessly over eleven months — from proxy chains to SDK wrappers to a single monorepo application.
