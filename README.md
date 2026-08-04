# Lab Machine Assistant (working title)

A mobile-first web app for labs. A user scans a single QR code, opens a chat,
optionally sends a photo of a machine or an error, and gets step-by-step
instructions grounded **exclusively** in the lab's uploaded manuals (RAG — no
answers from model weights or the internet). Chats and access are logged so an
admin can review effectiveness and manage the documents.

## Docs
- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — scope, acceptance criteria, risks
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased build plan
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — stack, data model, RAG flow, file tree

## Stack
FastAPI + Jinja2/HTMX/Tailwind · PostgreSQL + pgvector · OpenRouter (chat + vision) ·
local sentence-transformers embeddings. Cloud-hosted MVP.

## Quick start (target)
```bash
cp .env.example .env          # fill in OPENROUTER_API_KEY, DB url, secrets
docker compose up -d db       # Postgres + pgvector
pip install -r requirements.txt
alembic upgrade head
python scripts/seed.py        # seed initial user + admin passwords
uvicorn app.main:app --reload
```

## Status
Pre-implementation. Scaffold + planning docs only.
