# Architecture

Status: Draft v1 · Last updated 2026-06-21

## Stack
- **Backend/UI:** FastAPI + Jinja2 templates, HTMX for partial updates, Tailwind for
  mobile-first styling. Server-rendered keeps JS minimal and mobile-robust.
- **DB:** PostgreSQL with the `pgvector` extension — one store for relational data
  *and* embeddings (simplest for a cloud MVP).
- **LLM:** OpenRouter (chat + vision), wrapped in `app/services/llm.py` so the model
  is configurable and swappable.
- **Embeddings:** local `sentence-transformers` (e.g. `bge-small-en` / `all-MiniLM`)
  — free, private, no extra API. Behind `app/services/embeddings.py`.
- **Storage:** original documents and user image uploads on disk (`storage/`), or
  object storage (S3-compatible) in production.

## RAG request flow (text)
```
user query
  → embed query (local model)
  → vector search in pgvector (top-k chunks, filtered to "ready" docs)
  → assemble context + strict-grounding system prompt + chat history
  → OpenRouter chat completion (streamed)
  → response + citations (chunk → document map)
  → render answer with clickable document links; persist messages
```

## RAG request flow (image)
```
user uploads photo (mobile camera)
  → store + normalize (HEIC→JPEG, resize)
  → vision model (via OpenRouter): describe machine / read error text
  → use observations (+ any text) as the retrieval query → same path as above
```
Grounding rule: vision may *interpret the picture*, but the instructions/solution
text must come from retrieved manual chunks (see REQUIREMENTS R2 nuance).

## Data model (core tables)
- **settings** — singleton-ish: `user_password_hash`, `admin_password_hash`, config.
- **users / sessions** — lightweight identity (issued or self-entered) + session token.
- **access_log** — `identity`, `timestamp`, `ip`, `user_agent`, `event`.
- **documents** — `id`, `filename`, `path`, `status`, `uploaded_by`, `created_at`.
- **chunks** — `id`, `document_id`, `page`, `text`, `embedding vector`.
- **chats** — `id`, `user_id`, `title`, `created_at`.
- **messages** — `id`, `chat_id`, `role`, `content`, `image_path?`, `created_at`.
- **citations** — `message_id`, `document_id`, `page` (drives R9 links + N1 integrity).
- **feedback** — `message_id`, `rating` (N6); plus `not_found` flag for N5.

## Auth model (R10)
Two hashed passwords in `settings`. Login issues a signed cookie/JWT carrying the
role (`user` | `admin`). Admin endpoints require the admin role. Both passwords are
changeable from the admin UI. Shared user password is rotatable (mark it temporary).

## Citation → document serving (R9)
`GET /documents/{id}/view` streams the original file with correct content-type;
links may append `#page=N` for PDFs to deep-link. Only documents present in the
turn's retrieval set are linkable (prevents fabricated citations).

## Project file structure
```
FHProject/
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── docker-compose.yml          # local Postgres+pgvector + app
├── Dockerfile
├── docs/
│   ├── ROADMAP.md
│   ├── REQUIREMENTS.md
│   └── ARCHITECTURE.md
├── app/
│   ├── main.py                 # FastAPI app, router mounting, startup
│   ├── config.py               # env-driven settings
│   ├── database.py             # engine/session, pgvector setup
│   ├── dependencies.py         # auth/session dependencies
│   ├── models/                 # SQLAlchemy models
│   │   ├── user.py  chat.py  message.py  document.py  access_log.py
│   ├── schemas/                # Pydantic request/response models
│   ├── core/
│   │   └── security.py         # hashing, token signing, password change
│   ├── api/routes/
│   │   ├── auth.py  chat.py  documents.py  admin.py  health.py
│   ├── services/
│   │   ├── llm.py              # OpenRouter chat client (abstracted)
│   │   ├── vision.py           # image → observations
│   │   ├── embeddings.py       # local embedding model
│   │   ├── vectorstore.py      # pgvector queries
│   │   ├── ingestion.py        # parse → chunk → embed → index
│   │   └── rag.py              # retrieve + assemble prompt + cite
│   ├── templates/              # base, login, chat, admin/*
│   └── static/                 # css/js/img
├── storage/
│   ├── documents/              # original manuals (citation source)
│   └── uploads/                # user image uploads
├── scripts/
│   ├── generate_qr.py          # build the single global QR
│   └── seed.py                 # seed initial passwords/config
├── alembic/                    # migrations
└── tests/                      # auth / rag / chat / ingestion
```

## Key external dependencies
`fastapi`, `uvicorn`, `jinja2`, `sqlalchemy`, `alembic`, `psycopg[binary]`,
`pgvector`, `pydantic-settings`, `passlib[bcrypt]`, `python-jose` (or
`itsdangerous`), `httpx` (OpenRouter), `sentence-transformers`, `pypdf` /
`pymupdf` (PDF parse + page anchors), `pillow` + `pillow-heif` (image normalize),
`qrcode`, `slowapi` (rate limiting), `python-multipart` (uploads).
