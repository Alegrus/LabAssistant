# Project Roadmap

Status: v2 (build in progress) · Last updated 2026-06-30

A lab assistant: a user scans one QR code, opens a mobile chat, optionally sends a
photo of a machine or error, and gets step-by-step instructions grounded **only**
in the lab's uploaded manuals (RAG). Chats and access are logged so an admin can
review effectiveness and manage the document set.

Stack: **FastAPI + Jinja2/Tailwind**, **PostgreSQL + pgvector**, **OpenRouter**
for chat + vision, local sentence-transformers for embeddings (`bge-base-en-v1.5`,
768-dim) + cross-encoder reranker. Cloud-hosted MVP.

---

## Current status snapshot (2026-06-30)

| Phase | Status | Notes |
|-------|--------|-------|
| 0 — Foundations | 🟢 Local done, ⬜ not deployed | App, config, models, `docker compose` DB, `/health` all working locally; no cloud deploy yet. Alembic still deferred (using `init_db()`/`create_all`). |
| 1 — Auth & access logging | ✅ Done | Two-password login, signed sessions, A+F device identity, access log, admin change-passwords. 19 tests green. |
| 2 — Document ingestion & admin mgmt | ✅ Done | Batch upload → synchronous ingest → ready/failed; admin documents page; delete cascades chunks + file. |
| 3 — Core RAG chat | ✅ Done (non-streaming) | Chat/Message models, multi-chat UI, `rag.answer()` grounded replies, clickable page-deep-linked citations, "not found" fallback. Live LLM via user's OpenRouter key. Streaming deferred. |
| 4 — Image upload & vision | 🟡 Framework built, awaiting model | Full flow wired (upload → question-conditioned vision extraction → confirm/edit → grounded answer); provider-agnostic so local VLM drops in. Needs a reachable vision model to test end-to-end. |
| 5 — Admin review dashboard | 🟡 Partial | Access-log view exists (Phase 1). Full chat/transcript review + telemetry not built. |
| 6 — QR, hardening & pilot | ⬜ Not started | `scripts/generate_qr.py` is a stub. |

**RAG learning track (`rag_lab/` sandbox):** ✅ R0–R7 complete — the user built and
evaluated the retrieval pipeline from primitives (baseline hit@5 0.95 / MRR 0.81),
then read/validated the production `app/services/` equivalents.

**Immediate next step:** user verifies the live chat with their OpenRouter key, then
either add streaming (Phase 3 polish) or start Phase 4 (vision).

---

## Phase 0 — Foundations (½–1 week) · 🟢 local done, ⬜ not deployed
**Goal:** repo, config, and a deployable "hello world".
- Scaffold FastAPI app, settings via env (`.env`), structured logging.
- Provision Postgres + enable `pgvector`. Wire Alembic migrations.
- Health endpoint, base layout (Tailwind), deploy skeleton to chosen PaaS over HTTPS.
- **Exit:** app deploys and `/health` is green in the cloud.

## Phase 1 — Auth & access logging (R5, R10) (~1 week) · ✅ Done
**Goal:** the two-password security model and access tracking.
**Full build spec: [PHASE1_PLAN.md](PHASE1_PLAN.md).**
**Delivered:** `AppSettings` (hashed passwords, seeded + auto-seed in dev), signed
`itsdangerous` sessions, A+F identity (display name + `device_id` cookie,
find-or-create user), `access_log` with **per-login name snapshot** (fix: the log
row records the name-of-the-moment, not the mutable `User.display_name`), admin
change-passwords, `slowapi` login limit. Tests use an isolated `*_test` DB.
- Settings table holding hashed **user** and **admin** passwords (seeded, changeable).
- Login page → signed session cookie/JWT; role = user | admin.
- Admin "change passwords" screen (both).
- Access log: record login/session start (identity, ts, IP, UA).
- App-level rate limiting on login (`slowapi`) + edge/WAF rate limiting at deploy (**N2**, layered).
- **Exit:** user logs in with temp password; admin logs in separately; access events
  visible in DB; passwords changeable.

## Phase 2 — Document ingestion & admin management (R6) (~1.5 weeks) · ✅ Done
**Goal:** admin can upload/delete manuals and they become searchable.
**Delivered:** admin documents page with **batch upload** (streamed to disk one file
at a time, so peak RAM ≈ largest single file). Per-file 100 MB cap, 500 MB batch cap
(a disk + request-time guard, not a memory one), soft 15 MB warning + per-file
confirmation. Optional display name (on-disk name stays random for safety). Synchronous
ingest → per-file `ready`/`failed` + error. Delete removes row + chunks (cascade) + file.
- Upload endpoint (PDF first). Store original in `storage/documents/`.
- Ingestion pipeline: parse (text + page numbers) → chunk → embed → upsert to pgvector.
- **Synchronous** ingestion inside the upload request; record per-document status
  (`ready`/`failed`) + ingestion logs; enforce a max file size (**N4**).
- Delete: remove file + chunks + embeddings.
- Admin documents page: list, status, upload, delete.
- **Exit:** admin uploads a manual, sees it go "ready," deletes it cleanly.

## Phase 3 — Core RAG chat (R1, R2, R8, R9) (~2 weeks) · ✅ Done (non-streaming)
**Goal:** grounded, cited, multi-chat conversation.
**Delivered:** `Chat` + `Message` models (assistant turns store citations as JSON),
chat hub + per-chat UI, `rag.answer()` per turn (hybrid retrieval → rerank →
grounding prompt → OpenRouter → citations), "not found" fallback (no LLM call when
retrieval is empty), citations rendered as links to the source PDF deep-linked to the
page (`/documents/{id}/file#page=N`, served inline). **Deferred:** token streaming
(`llm.stream_chat` exists; UI wiring is a polish pass).
- Retrieval: embed query → top-k chunks → assemble context.
- Strict grounding prompt (no parametric/internet knowledge; "not found" fallback) (**N5**).
- LLM call via OpenRouter service; stream response.
- Citations: map cited chunks → source docs; render clickable links that
  open/download the document, deep-linking to page where possible (**N1, R9**).
- New chat / chat list per user session (R8); persist messages.
- **Exit:** user asks a question, gets an answer grounded in a real manual with a
  working citation link; can start a second chat.

## Phase 4 — Image upload & vision (R3) (~1 week) · 🟡 Framework built, awaiting model
**Goal:** photo-driven queries from a phone, answered from the manuals.

**Delivered (framework):** `services/vision.py` (question-conditioned extraction via the
provider-agnostic `llm` client); `rag.answer(image_context=...)` folds observations into
retrieval + grounding; chat route handles photo upload → vision → **pending confirm/edit**
→ answer, with `/confirm`, `/cancel`, and an owner/admin-gated image route; `Message` gains
`image_path` / `image_observations` / `pending`; transcript + admin views render the photo
and the "vision read". LLM/vision failures (incl. model-unreachable) degrade to the retry
banner. **Remaining:** point `LLM_PROVIDER=local` at a running VLM and test end-to-end;
HEIC→JPEG conversion; bulk image delete (**N3**).

**Design (refined):** the vision model does **perception, not answers** — it reads the
screen; the manuals explain what it means. This preserves grounding (R2) and is also
forced by architecture: retrieval is text-based, so the image *must* be turned into text
before we can search. The turn takes the photo **and the user's question together**:

1. **Question-conditioned extraction.** Send image + the user's question to the vision
   model, prompted to report only the concrete on-screen facts relevant to the question
   (control/button states, active selections, status readouts, error text — verbatim),
   *not* to explain causes. (E.g. "why is Load grayed out?" → extracts "Load disabled;
   Active Tube 4799_001; acquisition in progress; Next Tube available.")
2. **Confirm** the interpretation with the user (safety gate around the one parametric
   step) before it drives an answer.
3. **Retrieve** using question + extracted facts as the query.
4. **Grounded answer** from the manuals, with the extracted facts as extra context, cited.

So the extracted observations feed **both retrieval and the grounding prompt**. Manuals
here are ~95% text-rich, so no OCR is needed for retrieval; OCR of the few image-only
pages is an optional later add.

**Provider-ready:** `llm.py` is provider-agnostic (OpenAI-compatible); `LLM_PROVIDER`
switches between OpenRouter and a local server (Ollama/LM Studio/MLX) with no code change.
Vision is a strong candidate for local on the 64 GB Mac mini — the task is light
(question-guided OCR + short description), so a 7B–32B VLM (e.g. `qwen2.5-vl`) fits with
room to spare and keeps photos on-prem.
- Mobile camera/file input; server stores upload (`storage/uploads/`), converts HEIC.
- Multimodal message = text + `image_url` parts (already supported in `llm.py`).
- Show the attached image in the transcript.
- Images retained indefinitely; admin delete control, single + bulk (**N3**).
- **Exit:** on a phone, user photographs a machine/error and gets a grounded, cited answer.

## Phase 5 — Admin review dashboard (R7) (~1 week) · 🟡 Partial
**Goal:** review effectiveness and usage.
**Done:** access-log view (with the name-snapshot fix). **Remaining:** browse chats
grouped by user/device, view transcripts + citations, "not found"/👎 telemetry.
- Browse all chats grouped by user/session; view transcript + images + citations (R7).
- Access log view with date filters (R5).
- "Not found" + 👍/👎 telemetry surfaced to guide which manuals to add (**N5, N6**).
- **Exit:** admin can audit any conversation and see where the bot failed to answer.

## Phase 6 — QR, hardening & pilot (R4) (~1 week) · ⬜ Not started
**Goal:** ship to a real lab.
- Generate the single global QR (`scripts/generate_qr.py`) → printable card.
- Security pass: HTTPS, secret hygiene, file-type/size validation, rate limits, headers.
- Cost/latency tuning (context caps, top-k, model choice).
- Lightweight load test; backup/restore of DB + documents.
- **Exit:** QR printed and posted in the lab; pilot users onboarded.

## Phase 7 — Post-MVP / stretch
- **Per-machine QR codes** (scoped retrieval) — strong UX upgrade.
- Stronger auth (per-visit codes / magic links / SSO) replacing shared password.
- Self-hosted model + on-prem deploy if confidentiality requires it.
- Multi-lab tenancy, roles, analytics.
- Answer evaluation harness (does the bot cite the right manual?).
- Multi-format ingestion (DOCX, scanned PDFs via OCR), table/diagram extraction.

---

## Starting point — retrieval-first (no LLM) — ✅ done
We deliberately built the **retrieval half of RAG first, with no LLM**: load → chunk
→ embed → store → retrieve. This was both the lowest-risk core and the learning
vehicle (see **[RAG_LEARNING_PLAN.md](RAG_LEARNING_PLAN.md)**, R0–R7, now complete).
The LLM generation step was added afterwards (Phase 3) and, as predicted, was small.

## Build decisions & gotchas (learned during the build)
- **No Alembic yet:** schema is created by `init_db()`/`create_all`, which does NOT
  alter existing tables. Adding a column to a live table needs a manual `ALTER`
  (done twice: access-log `display_name`; watch for this until Alembic lands).
- **Tests use a separate `labassistant_test` DB** (`tests/conftest.py` redirects
  `DATABASE_URL` before app import) so `pytest` never touches dev data. The login
  rate limiter is disabled in the auth fixture (its state leaked across tests → flaky 429s).
- **Dev auto-seed:** on startup in `development`, missing `AppSettings` is seeded with
  the default passwords (never in other envs — avoids default creds in prod).
- **Dev DB lifecycle:** Postgres runs via `docker compose` with `restart: unless-stopped`;
  data lives in the `pgdata` volume (survives restarts; only `down -v` wipes it).
- **bcrypt, not passlib** (passlib 1.7.4 is incompatible with bcrypt 5).
- **Starlette `TemplateResponse(request, name, ctx)`** signature required (request first).
- **Embedding model is fixed to 768-dim** (`bge-base`); changing it requires matching
  `EMBEDDING_DIM` + the `Vector(768)` column + re-ingesting everything.

## Suggested build order (critical path)
`Phase 0 → 1 → 2 → 3` is the spine (auth → documents → grounded chat). Phases 4–6
can partially overlap once Phase 3 lands. Don't start Phase 4 (vision) before
Phase 3 retrieval works — image observations are useless without retrieval.

## Rough timeline
~8–9 weeks for a solid MVP (one developer). Aggressive prototype: collapse Phases
1, 3, and a thin admin view to demo grounded chat + citations in ~2–3 weeks, then
backfill ingestion robustness, vision, and the dashboard.

## Effectiveness loop (the product's real purpose)
Log every "not found" and every 👎. Phase 5 surfaces these so the admin knows which
manual to upload or which chunking to fix — closing the loop the use case describes.
