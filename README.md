# Lab Machine Assistant

A mobile-first RAG assistant for shared lab equipment. A user scans a QR code, opens a
chat, optionally photographs a machine or an error on screen, and gets step-by-step
instructions grounded **exclusively** in that lab's own uploaded manuals — never from
model weights or the internet. Admins upload documents and review every conversation.

Built to run entirely on self-hosted models, so equipment manuals and photos of lab
screens never leave the network.

> **Status:** working prototype. The core loop (upload manuals → ask → grounded, cited
> answer, with photos) is complete and used against a real manual set. See
> [Roadmap](docs/ROADMAP.md) for what's done and what's next.

---

## Why it's built this way

The hard requirement is **no invented answers**. In a lab, a plausible-but-wrong
instruction is worse than "I don't know", so several design choices fall out of that:

- **Retrieval-only grounding.** The model receives only the retrieved passages and is
  instructed to answer from them alone. If retrieval returns nothing, the LLM is never
  called — an empty knowledge base cannot be answered from parametric memory.
- **Citations are verified, not generated.** The model cites sources by number; those
  numbers are mapped back to real documents server-side. It cannot cite a document that
  wasn't retrieved.
- **Vision is perception, not answers.** A photo is read by a vision model to extract
  *observable facts* (button states, error text), which the user confirms; the answer
  still comes from the manuals. This keeps the grounding guarantee intact even for
  photo-driven questions.
- **"Not found" is a first-class outcome**, logged as telemetry so an admin can see which
  manuals are missing.

## How it works

```
question ──► condense (resolve follow-ups)
                │
                ▼
        hybrid retrieval  ──  dense (pgvector) + sparse (tsvector)
                │              fused with Reciprocal Rank Fusion
                ▼
        cross-encoder rerank
                │
                ▼
        context expansion  ──  stitch neighbouring chunks so multi-step
                │              procedures aren't cut off mid-list
                ▼
        grounded prompt ──► LLM (streamed) ──► answer + verified citations
```

**Retrieval** is hybrid because neither half is sufficient alone: dense vectors catch
paraphrase ("reset the spinner" ≈ "restart centrifuge") but miss exact tokens like error
codes, while full-text search does the opposite. RRF fuses the two ranked lists without
needing to tune a weight between incomparable score scales.

**Context expansion** exists because a chunk boundary can truncate a procedure. Retrieving
"steps 1–4" alone produced answers that stopped mid-task; pulling adjacent chunks from the
same document returns the whole sequence.

## Stack

| Layer | Choice |
| --- | --- |
| Web | FastAPI + Jinja2 + Tailwind, server-rendered, progressive enhancement |
| Database | PostgreSQL + [pgvector](https://github.com/pgvector/pgvector) |
| Embeddings | `bge-base-en-v1.5` (local, sentence-transformers) |
| Reranker | `bge-reranker-base` cross-encoder (local) |
| LLM / vision | Any OpenAI-compatible endpoint — self-hosted (Ollama/LM Studio/MLX) or hosted (OpenRouter) |
| Ingestion | PyMuPDF → token-aware chunking → embeddings |

Streaming replies use newline-delimited JSON over `fetch`, with the plain form POST kept
as a no-JS fallback.

## Architecture

The app and the two heavy backends can live on one machine or be split across a network —
the split is configuration, not code.

```
  Application                            Inference / data host
  ───────────                            ─────────────────────
  FastAPI app                     ┌────► PostgreSQL + pgvector
  embeddings + reranker (local)  ─┤      (chunks, vectors, chats, logs)
  document storage                └────► OpenAI-compatible LLM + vision API
```

Splitting them lets a modest laptop run the app while a larger machine hosts the models
and database. All LLM access goes through one small client module, so switching between a
self-hosted model and a hosted provider is a single environment variable.

## Quick start

**Prerequisites:** Python 3.14, [`uv`](https://docs.astral.sh/uv/), a reachable
PostgreSQL 16+ with the `vector` extension available, and an OpenAI-compatible model
endpoint (e.g. [Ollama](https://ollama.com)).

```bash
uv venv && uv pip install -r requirements.txt
cp .env.example .env          # fill in DATABASE_URL and your model endpoint
uv run python scripts/check_backends.py    # verifies DB, pgvector, models
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>. The schema is created on first start, and in
`APP_ENV=development` the default passwords below are seeded automatically.

| Role | Default password | Access |
| --- | --- | --- |
| User | `changeme-user` | chat, photo upload |
| Admin | `changeme-admin` | documents, chat review, access log, password change |

Then sign in as admin, upload a few PDFs under **Documents**, and ask a question.

> ⚠️ **Before exposing this anywhere real:** set a strong `SECRET_KEY` (it signs session
> cookies), change both passwords via **Admin → Security**, and set `APP_ENV=production`
> — which disables default-password seeding and refuses to start on a default
> `SECRET_KEY`.

### Self-hosted models

Point the app at any OpenAI-compatible server:

```ini
LLM_PROVIDER=local
LOCAL_BASE_URL=http://<your-model-host>:11434/v1
LOCAL_CHAT_MODEL=<a chat model>
LOCAL_VISION_MODEL=<a vision-capable model>
```

**Sizing note, learned the hard way:** a model's memory footprint is dominated by its
**context window, not its parameter count**. An 8B vision model at a 256K context claimed
~44 GB and evicted the chat model entirely; capped at 16K it used 7.8 GB, letting chat and
vision stay resident together in ~16 GB. If only one model stays loaded, check the context
size before blaming the model size. With Ollama you can cap it:

```bash
curl http://<your-model-host>:11434/api/create -d '{
  "model":"<name>-ctx16k", "from":"<base model>", "parameters":{"num_ctx":16384}
}'
```

Both models are preloaded at startup and held in memory (`LLM_KEEP_ALIVE`, default `2h`),
because otherwise the first question — and separately the first photo — each pay a 15–20 s
cold load, and most servers unload after ~5 minutes idle.

### Working across a network

If the model/database host is remote, use a private overlay network such as
[Tailscale](https://tailscale.com) or WireGuard rather than forwarding ports.

> **Do not port-forward these services.** Ollama has **no authentication whatsoever**, and
> an internet-facing Postgres invites constant credential attacks. An overlay network
> encrypts traffic and only admits devices you have authorised.

## Photos from a phone

Camera uploads are normalised before reaching the vision model:

- **HEIC → JPEG** — the iPhone default, which inference servers reject outright.
- **EXIF rotation applied** — otherwise portrait photos are read sideways.
- **Downscaled** to `VISION_MAX_IMAGE_PX` (1536) on the long edge — a 12 MP photo drops
  from ~186 KB to ~27 KB with no loss of on-screen legibility.

The extracted interpretation is shown to the user for confirmation before it drives an
answer, so a misread is caught rather than silently producing a wrong instruction.

## Tests

```bash
uv run python -m pytest
```

Tests create and use a separate `<database>_test` database, so development data is never
touched. They cover session/auth security, the access-log audit trail, and the retrieval
context-expansion logic.

## Shutting down

Stop the app with `Ctrl+C` (or `pkill -f "uvicorn app.main:app"` if backgrounded). Nothing
is lost — chats, documents and logs live in the database. If the model host is shared, you
can release model memory immediately instead of waiting for `LLM_KEEP_ALIVE`:

```bash
curl -s http://<your-model-host>:11434/api/generate \
  -d '{"model":"<model>","prompt":"","keep_alive":0}' > /dev/null
```

## Troubleshooting

Run `uv run python scripts/check_backends.py` first — it checks DNS, TCP, the live
service, pgvector, indexed document counts, and whether the configured models exist, with
timings and a fix hint for each failure.

| Symptom | Cause / fix |
| --- | --- |
| Connection refused / times out | Wrong host or port in `.env`, or no network path to the host. Note the port is required — a URL without it silently falls back to 5432 / port 80. |
| First query takes 15–20 s | A model cold-loaded. Check the `[warmup] llm …` lines appeared at startup and `LLM_KEEP_ALIVE` hasn't expired. |
| ~10 s before the answer starts | Prompt prefill on local hardware, not a cold load. Lower `RETRIEVAL_TOP_K` to trade answer completeness for speed. |
| Only one model stays loaded | Context window too large — see the sizing note above; inspect with `/api/ps`. |
| Answers are all "I couldn't find that…" | Working as designed — the answer must come from an uploaded manual. Upload one covering the topic. |
| Citation links 404 | The document storage directory is missing; the database stores relative paths to the source files. |
| `No such file or directory` running `.venv/bin/…` | The project directory moved; console-script shebangs hold absolute paths. Recreate the venv. |

## Documentation

- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — scope, acceptance criteria, risks
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — data model and RAG flow
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased build plan and current status
- [docs/RAG_LEARNING_PLAN.md](docs/RAG_LEARNING_PLAN.md) — the from-scratch retrieval
  exercises in `rag_lab/`, built and evaluated before the production pipeline

## Privacy

Uploaded manuals and user-submitted photos are **not** part of this repository — the
storage directories are gitignored and ship empty. With a self-hosted model endpoint, no
document text or image ever leaves your network.

A pre-commit hook guards this. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

It scans staged changes and refuses commits containing API keys or hard-coded
credentials, private LAN/Tailscale addresses or tailnet hostnames, personal email
addresses, or any lab document or user upload. Deliberate exceptions:
`git commit --no-verify`.

## License

[MIT](LICENSE)
