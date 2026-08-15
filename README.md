# Lab Machine Assistant (working title)

A mobile-first web app for labs. A user scans a single QR code, opens a chat,
optionally sends a photo of a machine or an error, and gets step-by-step
instructions grounded **exclusively** in the lab's uploaded manuals (RAG — no
answers from model weights or the internet). Chats and access are logged so an
admin can review effectiveness and manage the documents.

## Architecture

The app runs **on your development machine**. The Mac mini on the LAN provides the two
heavy backends — the database and the models. Nothing needs to be installed on the mini
to develop; you just need to be on the same network.

```
  Dev machine (this repo)                    Mac mini  10.0.0.10
  ─────────────────────────                  ──────────────────────────
  FastAPI app  :8000                  ┌────► Postgres 16 + pgvector  :55432
  embeddings (bge-base, MPS)  ────────┤      (chunks, vectors, chats, logs)
  reranker (cross-encoder, MPS)       └────► Ollama (OpenAI-compatible) :11434
  storage/documents/  (source PDFs)          (chat + vision models)
```

Why this split: the embedding and reranker models are small and run fast locally on
Apple Silicon, while the LLM/VLM and the database are shared services worth centralising.
Swapping back to a hosted provider is a config change (`LLM_PROVIDER=openrouter`) — no
code edits.

## Prerequisites

- Python 3.14 + [`uv`](https://docs.astral.sh/uv/)
- On the same LAN as the mini (both services are LAN-only, no internet exposure)
- **No Docker required.** Postgres lives on the mini.

## 1. Connect to the mini

Both services are plain TCP — there is nothing to log into. Verify both backends in one
step before launching:

```bash
uv run python scripts/check_backends.py
```

It checks DNS, TCP, the live service, pgvector, the indexed document count, and that the
chat + vision models named in `.env` are actually installed — with round-trip times and a
fix hint for anything that fails.

Optional shell access (only needed to manage models, not to develop):

```bash
ssh user@10.0.0.10
ollama list          # what's installed
ollama pull <model>  # add a model
```

## Working away from the home network

The mini stays put and keeps serving the database and models; you connect to it over
[Tailscale](https://tailscale.com), a private WireGuard network. Both machines join the
same tailnet and get stable `100.x` addresses that work from anywhere — **nothing is
exposed to the public internet**.

The mini is already set up: Tailscale is installed, it is online as
`model-host.internal` (`100.64.0.10`), sleep is disabled, and both Postgres and
Ollama already listen on the tailnet interface.

To finish, on **this** machine:

1. Install Tailscale (`brew install --cask tailscale`, or the Mac App Store build) and
   sign in to the **same tailnet** as the mini.
2. Confirm the mini appears: `tailscale status` should list `mac-mini`.
3. Point `.env` at the tailnet name instead of the LAN IP:

   ```ini
   DATABASE_URL=postgresql+psycopg://dbuser:<password>@model-host.internal:55432/labassistant
   LOCAL_BASE_URL=http://model-host.internal:11434/v1
   ```

4. `uv run python scripts/check_backends.py`

Use the tailnet name **permanently** — at home Tailscale routes it straight over the LAN,
so there is no config to switch when you leave or come back.

Expect a little more latency away from home: each turn makes a few database round-trips
plus the model call, so a remote connection typically adds well under a second overall —
generation speed on the mini stays the dominant cost.

> **Do not port-forward these ports on the router.** Ollama has **no authentication
> whatsoever** — anyone who finds an exposed instance can use it — and an internet-facing
> Postgres invites constant credential attacks. Tailscale avoids both: traffic is
> encrypted and only devices you have authorised can connect. If the mini is ever
> unreachable, fix the tailnet rather than opening a port.

## 2. Configure

`.env` is **gitignored** — it holds the DB password and any API keys, so it is never
committed. Copy the template and fill it in:

```bash
cp .env.example .env
```

The values that matter for the mini setup:

```ini
DATABASE_URL=postgresql+psycopg://dbuser:<password>@10.0.0.10:55432/labassistant

LLM_PROVIDER=local
LOCAL_BASE_URL=http://10.0.0.10:11434/v1
LOCAL_CHAT_MODEL=gemma4-12b-ctx16k:latest   # large context, concise answers
LOCAL_VISION_MODEL=qwen3-vl:8b              # reads screens/error codes
```

## 3. Launch

```bash
uv venv && uv pip install -r requirements.txt            # first run only
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- On this machine: <http://localhost:8000>
- From a phone on the same Wi-Fi: `http://<your-lan-ip>:8000` (`ipconfig getifaddr en0`)
  — this is what the QR code will eventually encode.

The schema is created automatically on startup, and in `development` the default
passwords are seeded:

| Role  | Password          | Notes                                   |
| ----- | ----------------- | --------------------------------------- |
| User  | `changeme-user`   | any display name; gets the chat UI       |
| Admin | `changeme-admin`  | Documents, Chats, Access Log, Security   |

Change both via **Admin → Security** before any real use.

On startup the embedding + reranker models are preloaded in a background thread so the
first query isn't slow — look for `[warmup] ... ready` in the log.

## Tests

```bash
uv run python -m pytest
```

Tests run against a separate `<db>_test` database on the mini (created automatically),
so your development data is never touched. No Docker needed.

## Troubleshooting

Start with `uv run python scripts/check_backends.py` — it identifies which backend is
failing and why.

| Symptom | Cause / fix |
| --- | --- |
| DB/Ollama connection refused or times out | Not on the LAN and not on the tailnet. See [Working away from the home network](#working-away-from-the-home-network). |
| Tailnet hostname won't resolve | Tailscale isn't running or isn't signed in **on this machine**; check `tailscale status`. |
| First query takes ~15–20 s | The mini cold-loads the model into memory. Subsequent queries are much faster; it stays warm for a few minutes. |
| ~9 s before the answer starts | Prompt prefill on local hardware. The status line ("Searching the manuals…") shows progress. Lower `RETRIEVAL_TOP_K` to trade completeness for speed. |
| "temporarily unavailable" banner | The model errored or timed out. The question is preserved — press Send again. The reason is logged in **Admin → Access Log**. |
| Answers are all "I couldn't find that…" | Working as designed: the answer must come from an uploaded manual. Upload one covering the topic (Admin → Documents). |
| Citation links 404 | `storage/documents/` is missing — the DB stores relative paths to the source PDFs. |

## Docs

- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — scope, acceptance criteria, risks
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased build plan and status
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — stack, data model, RAG flow

## Status

Phases 1–3 complete (auth + access logging, document ingestion, grounded cited chat with
streaming). Phase 4 (photo → vision → confirm → grounded answer) is built and wired to a
local vision model. Remaining: feedback telemetry, QR generation, deployment to an
always-on host.
