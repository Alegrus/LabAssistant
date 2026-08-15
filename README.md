# Lab Machine Assistant (working title)

A mobile-first web app for labs. A user scans a single QR code, opens a chat,
optionally sends a photo of a machine or an error, and gets step-by-step
instructions grounded **exclusively** in the lab's uploaded manuals (RAG — no
answers from model weights or the internet). Chats and access are logged so an
admin can review effectiveness and manage the documents.

## Architecture

The app runs **on your development machine**. The Mac mini provides the two heavy
backends — the database and the models. Nothing needs to be installed on the mini to
develop; you only need network access to it (same LAN, or the same tailnet from
anywhere).

```
  Dev machine (this repo)                 Mac mini
  ───────────────────────                 ────────
  FastAPI app  :8000               ┌────► Postgres 16 + pgvector  :55432
  embeddings (bge-base, MPS)  ─────┤      (chunks, vectors, chats, logs)
  reranker (cross-encoder, MPS)    └────► Ollama (OpenAI-compatible) :11434
  storage/documents/  (source PDFs)       (chat + vision models, kept warm)

  mini reachable as:  10.0.0.10 (LAN)  |  model-host.internal (tailnet)
```

Why this split: the embedding and reranker models are small and run fast locally on
Apple Silicon, while the LLM/VLM and the database are shared services worth centralising.
Swapping back to a hosted provider is a config change (`LLM_PROVIDER=openrouter`) — no
code edits.

## Prerequisites

- Python 3.14 + [`uv`](https://docs.astral.sh/uv/)
- Network access to the mini — same LAN, or the same tailnet from anywhere
  (never exposed to the public internet)
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
`model-host.internal` (its `100.x` IP can change, so prefer the name),
sleep is disabled, and both Postgres and
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
DATABASE_URL=postgresql+psycopg://dbuser:<password>@model-host.internal:55432/labassistant

LLM_PROVIDER=local
LOCAL_BASE_URL=http://model-host.internal:11434/v1
LOCAL_CHAT_MODEL=gemma4-12b-ctx16k:latest    # concise, fastest of those benchmarked
LOCAL_VISION_MODEL=qwen3-vl-ctx16k:latest    # reads screens/error codes
```

### Why the `-ctx16k` model variants

A model's memory footprint is dominated by its **context window, not its parameter
count**. Stock `qwen3-vl:8b` loads with a 256K context and claims **~44 GB**, which
evicted the chat model outright — only one model could stay resident. The same model
capped at a 16K context uses **7.8 GB**, so chat + vision coexist in ~16 GB and both stay
warm. 16K is ample for one photo plus the prompt.

If the variant is ever missing, recreate it (no CLI needed):

```bash
curl http://model-host.internal:11434/api/create -d '{
  "model":"qwen3-vl-ctx16k", "from":"qwen3-vl:8b", "parameters":{"num_ctx":16384}
}'
```

## 3. Launch

```bash
uv venv && uv pip install -r requirements.txt            # first run only
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- On this machine: <http://localhost:8000>
- From a phone on the same Wi-Fi: `http://<your-lan-ip>:8000` (`ipconfig getifaddr en0`)
  — this is what the QR code will eventually encode.
- From a phone anywhere: install Tailscale on it, sign in to **this machine's** tailnet
  (the mini is a *shared* node from another tailnet, so signing in as its owner will not
  reach the laptop), then `http://<this-machine-tailscale-ip>:8000` (`tailscale ip -4`).

The server binds `0.0.0.0`, so it is reachable from other devices; if a phone cannot
connect on the LAN, check the macOS firewall
(`/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate`).

### Photos from a phone

Camera uploads are normalised before they reach the vision model, so an iPhone works out
of the box:

- **HEIC → JPEG.** HEIC is the iPhone default and the inference server rejects it
  outright (`400 Bad Request`); it is transcoded via `pillow-heif`.
- **EXIF rotation applied**, or portrait shots are read sideways.
- **Downscaled to `VISION_MAX_IMAGE_PX`** (1536) on the long edge — a 12 MP photo goes
  from ~186 KB to ~27 KB on the wire with no loss of on-screen legibility.

The schema is created automatically on startup, and in `development` the default
passwords are seeded:

| Role  | Password          | Notes                                   |
| ----- | ----------------- | --------------------------------------- |
| User  | `changeme-user`   | any display name; gets the chat UI       |
| Admin | `changeme-admin`  | Documents, Chats, Access Log, Security   |

Change both via **Admin → Security** before any real use.

On startup a background thread preloads everything the first query would otherwise wait
for — the local embedding + reranker models, and the remote chat + vision models on the
mini. Boot and `/health` stay instant; watch for:

```
[warmup] embeddings ready in 6.2s
[warmup] reranker ready in 4.9s
[warmup] llm 'gemma4-12b-ctx16k:latest' resident in 13.1s (keep_alive=2h)
[warmup] llm 'qwen3-vl-ctx16k:latest' resident in 0.0s (keep_alive=2h)
```

`LLM_KEEP_ALIVE` (default `2h`) keeps the models in the mini's memory between questions —
Ollama's own default is 5 minutes, which would make a sporadically-used assistant
cold-load again and again. Set `WARM_LLM_ON_STARTUP=false` to skip it. Warmup is
best-effort: if it fails, the first query is just slow.

## 4. Shutting down

**The app server** is the only piece you normally stop. If you launched it in a terminal,
`Ctrl+C`. If it is running in the background:

```bash
pkill -f "uvicorn app.main:app"
pgrep -fl "uvicorn app.main:app"     # no output = stopped
```

Nothing is lost — chats, manuals and logs all live in the database on the mini.

**Leave the database running.** It is a persistent service on the mini, not on this
machine; the whole point of the split is that it keeps serving while you are away.
(`alex` has no `docker` on the mini, so it isn't stoppable from here anyway.)

**The models** stay resident for `LLM_KEEP_ALIVE` and then release themselves. The mini is
shared, so to hand back the memory (~16 GB) immediately:

```bash
for m in gemma4-12b-ctx16k:latest qwen3-vl-ctx16k:latest; do
  curl -s http://model-host.internal:11434/api/generate \
    -d "{\"model\":\"$m\",\"prompt\":\"\",\"keep_alive\":0}" > /dev/null
done
curl -s http://model-host.internal:11434/api/ps   # confirm nothing is loaded
```

Starting the server again re-warms them.

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
| Hostname resolves but the port is refused, or Ollama returns a `308 redirect` | The `:55432` / `:11434` port was dropped when swapping the hostname into `.env`, so the URL fell back to 5432 / port 80. Both ports are required. |
| `No such file or directory` running `.venv/bin/uvicorn` or `pytest` | The project directory was moved; console-script shebangs still point at the old path. Recreate the venv, or rewrite line 1 of the scripts in `.venv/bin/`. (`.venv/bin/python -m uvicorn …` works regardless.) |
| First query takes ~15–20 s | A model cold-loaded. Startup warmup normally prevents this — check the `[warmup] llm …` lines appeared, and that `LLM_KEEP_ALIVE` hasn't expired. |
| ~10 s before the answer starts | Prompt prefill on local hardware, not a cold load. The status line ("Searching the manuals…") shows progress. Lower `RETRIEVAL_TOP_K` to trade completeness for speed. |
| Only one model stays loaded / the mini runs out of memory | A model is loading with a huge context window. See [Why the `-ctx16k` model variants](#why-the--ctx16k-model-variants); check actual usage with `curl …:11434/api/ps`. |
| Photo upload fails or the screen is read sideways | Should be handled (HEIC transcode + EXIF rotation). If it recurs, confirm `pillow-heif` is installed in the venv. |
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
