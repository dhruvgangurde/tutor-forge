# PROJECT-CONTEXT.md — TutorForge AI

> **Purpose of this file:** a complete, self-contained briefing for an AI assistant or developer
> starting with **zero prior knowledge** of this project. Nothing here assumes earlier conversation.
> Everything below was verified against the actual code in the repository, not from memory.
>
> **Snapshot date:** 2026-08-15 · **Repo root:** `c:\Users\Dhruvv\Desktop\AI_Tutor` · **Branch:** `main` (clean)

---

## 1. What this project is (one paragraph)

**TutorForge AI** is a full-stack educational AI platform. Teachers upload course material (PDF / PPTX /
notes); the system parses it, builds a concept hierarchy, embeds it into a vector store, and then
provides three AI capabilities that are **all restricted to that uploaded corpus**:

1. a **Socratic tutor** that answers students with *guiding questions and a 3-level hint ladder* rather than direct answers,
2. **AI assessment generation** (MCQ / short-answer / numeric, tagged by Bloom level), and
3. **deterministic rubric-based grading** of student submissions, which produces a *recommendation* that a teacher must explicitly approve or override before it becomes a grade.

The defining constraint is **zero hallucination**: every agent must retrieve supporting course
evidence and pass a groundedness check *before* any LLM call. If evidence is insufficient, the system
**refuses to answer** rather than falling back on the model's general knowledge.

It is a **portfolio-grade / solo-developer project** (scoped in the brief as "a single intern over
4–6 weeks"), built to demonstrate production-quality agentic AI engineering — not a commercial product
with real users. All data is synthetic; no real student PII is ever used.

**Owner / sole developer:** Dhruv (`dhruvgangurde13@gmail.com`).

---

## 2. Current status at a glance

| Aspect | State |
|---|---|
| Backend | Feature-complete for all 5 modules (auth, courses, tutoring, assessments, grading) |
| Frontend | Feature-complete SPA for teacher + student flows |
| Tests | Backend pytest suite runs fully **offline** against a deterministic Gemini mock (~153 test functions in 21 files; the hardening log records **272 passed** with parametrization). Frontend has minimal Vitest coverage (1 spec file). |
| Production hardening | Milestone 1 complete, Milestone 2 mostly complete, Milestone 3 not started (details in §11) |
| Deployment | Config exists (Render + Vercel + Docker) but **no live deployment is known to be running** |
| Git history | **One squashed commit**: `ab60d6e Initial release: TutorForge AI` — earlier history was rewritten for public release |
| Eval suite | **Not built.** `backend/eval/` contains only an empty `__init__.py` |
| Langfuse observability | Client is wired in, but keys default to empty strings |

---

## 3. Repository layout

```
AI_Tutor/
├── backend/                      FastAPI service + LangGraph agents (Python)
│   ├── main.py                   App factory, lifespan, Gemini client wrappers, middleware, routers
│   ├── core/                     Cross-cutting infrastructure (see §7)
│   ├── db/                       SQLAlchemy models, Alembic migrations, seed script
│   ├── auth/                     JWT auth (router + service + schemas)
│   ├── courses/                  Course upload & structure (router + service + schemas)
│   ├── tutoring/                 Socratic tutor endpoints (router + service + schemas)
│   ├── assessments/              Assessment generation, publish, submit (router + service + schemas)
│   ├── grading/                  Grading trigger, queue, approve/override (router + service + schemas)
│   ├── retrieval/                RetrievalService — ChromaDB + Gemini embeddings; the grounding engine
│   ├── agents/                   LangGraph pipelines: ingestion, tutor, assessment, grading
│   │   └── <agent>/{graph,nodes,state,prompts}.py
│   ├── health/                   /health and /health/ready probes
│   ├── tests/                    pytest suite (21 files)
│   ├── eval/                     RESERVED for promptfoo eval suite — currently EMPTY
│   ├── diagnose_retrieval.py     Ad-hoc retrieval debugging script
│   ├── diagnose_stale_data.py    Ad-hoc script from a past mock-data bug
│   ├── pyproject.toml            Deps with upper bounds + pytest/ruff config
│   ├── requirements.lock.txt     Exact pins for the Docker image
│   ├── Dockerfile                Backend container
│   └── .env / .env.example       Local config (real .env is gitignored)
│
├── frontend/                     Vite + React 18 + TypeScript 5 SPA
│   └── src/
│       ├── App.tsx               All routes + role gating
│       ├── features/             auth · courses · tutor · assessments · grading
│       ├── components/           layout (AppShell, Sidebar) + ui primitives
│       ├── lib/api/              Typed axios clients per domain + shared client/errors/types
│       ├── lib/                  queryClient, queryKeys, constants, statusVariant
│       ├── store/AuthContext.tsx Auth state (localStorage-backed)
│       └── pages/                NotFoundPage, NotAuthorizedPage
│
├── docs/
│   ├── PROJECT-BRIEF.md          The original product/architecture spec (the "north star" document)
│   ├── ROADMAP.md                Phase plan (4A–4D, Phase 5) — partly stale, see §12
│   ├── SETUP_WINDOWS.md          Windows/Python 3.12 setup walkthrough
│   ├── DATABASE_SETUP.md         PostgreSQL troubleshooting guide
│   └── screenshots/              Empty (.gitkeep only)
│
├── guide-docs/                   GUIDE-AI-SDP workflow artifacts (gitignored — local only)
│   ├── guide-state.md            Stage/milestone tracker
│   ├── audit.md                  Append-only log of every user prompt + AI response
│   ├── discover/                 requirements, user-stories, application-design, plans
│   ├── build/                    phase code plans
│   └── phase-4d-*.md             Phase 4D plans
│
├── .project-guide-rule-details/  GUIDE-AI-SDP rule files (gitignored — local only)
├── .claude/settings.json         Claude Code permission allowlist (gitignored)
├── CLAUDE.md                     GUIDE-AI-SDP workflow instructions (gitignored — see §13)
├── .github/workflows/ci.yml      GitHub Actions CI
├── render.yaml                   Render blueprint for backend
├── README.md                     Public-facing README
└── "s -ExecutionPolicy RemoteSigned) ; (& c:Users...Activate.ps1)"   ← junk file, see §12
```

**Important:** `.gitignore` excludes `CLAUDE.md`, `.claude/`, `guide-docs/`, and
`.project-guide-rule-details/`. The **published repo contains only the application** — all AI-workflow
scaffolding is local-only by design.

---

## 4. Architecture

```
┌────────────┐    REST/JSON     ┌────────────────────────────────────┐
│  Frontend  │ ───────────────▶ │             Backend                │
│ React/Vite │ ◀─────────────── │             FastAPI                │
│ (Vercel)   │                  │             (Render)               │
└────────────┘                  │                                    │
                                │  auth · courses · tutoring         │
                                │  assessments · grading · health    │
                                │                │                   │
                                │                ▼                   │
                                │   LangGraph agent pipelines        │
                                │  (ingestion · tutor ·              │
                                │   assessment · grading)            │
                                │        │              │            │
                                │        ▼              ▼            │
                                │   ChromaDB       PostgreSQL 16     │
                                │   (vectors,      (application      │
                                │    on disk)       data)            │
                                │        ▲                           │
                                │        │                           │
                                │  Gemini (LLM + embeddings)         │
                                │  Langfuse (traces)                 │
                                └────────────────────────────────────┘
```

### Startup sequence (`backend/main.py` → `lifespan`)
1. `configure_logging()` — structured, request-id-aware logging.
2. `assert_safe_production_config()` — **fails closed** before the app is built if `ENVIRONMENT=production` and either `USE_MOCK_GEMINI=true` or the JWT secret is the default/shorter than 32 chars.
3. ChromaDB `PersistentClient` opened at `CHROMA_PERSIST_PATH` — existing course collections reload automatically, no re-ingestion after restart.
4. Gemini Pro + Flash clients created (**real or mock**, based on `USE_MOCK_GEMINI`).
5. Langfuse client created.
6. `RetrievalService` singleton created (Chroma + Gemini Pro for embeddings).
7. `recover_stale_jobs()` reaps jobs orphaned by a previous crash (non-fatal on error).
8. All singletons stored on `app.state`, consumed by `Depends()` factories in `core/dependencies.py`.

Middleware order (outermost first): **CORS → SecurityHeaders → RateLimit → RequestId → routes**.
CORS is deliberately the outer layer so a 429 still carries CORS headers and the browser can read it.

---

## 5. The four LangGraph agent pipelines

All graphs are built by `build_*_graph(...)` factory functions that take injected dependencies
(retrieval service, Gemini client, DB session, Langfuse) via `functools.partial`.
DB sessions passed into background-task graphs are **request-scoped and must not be shared**.

### Ingestion (`agents/ingestion/`)
```
validate → parse → build_hierarchy → create_course_collection → chunk_and_embed → persist_to_db
```
Any node can set `status="failed"`; conditional edges route straight to `END`.
`create_course_collection` runs *before* `chunk_and_embed` so the Chroma collection always exists.

### Tutor (`agents/tutor/`)
```
retrieve_context → check_groundedness ──grounded──▶ generate_guiding_question → emit_pedagogy_trace
                                     └─not grounded─▶ refuse (NO LLM CALL)
```
Built with `mode="chat"` or `mode="hint"`; in hint mode `advance_hint_node` replaces the question
node — it increments `hint_level` (capped at **3**) and regenerates a more direct guiding question
through the same groundedness gate. `emit_pedagogy_trace` writes a Langfuse trace.

### Assessment (`agents/assessment/`)
```
retrieve_concepts → check_groundedness ──▶ generate_questions ──▶ persist_assessment → END
                                       └─▶ refuse → END
```
Also routes to `END` when generation yields **0 questions** (avoids persisting an empty assessment).
Runs as a FastAPI **BackgroundTask**; assessment status moves `generating → draft` (or `failed`).

### Grading (`agents/grading/`)
```
load_submission → retrieve_evidence → grade_responses → persist_recommendation → END
```
Grading calls Gemini Pro via `generate_deterministic()` (**temperature = 0**, enforced in code).
Partial grades are still persisted if some responses fail. Scores are clamped defensively.

---

## 6. Data model — 16 SQLAlchemy tables (`backend/db/models.py`)

Defined in FK-dependency order. All PKs are UUIDs.

| # | Table | Notes |
|---|---|---|
| 1 | `users` | `role` = `"teacher"` \| `"student"`; unique email; bcrypt hash |
| 2 | `courses` | `status` = `pending` \| `ingesting` \| `ready` \| `failed`; owned by a teacher |
| 3 | `chapters` | ordered, cascade-deleted with course |
| 4 | `concepts` | name, description, keywords (JSON-in-text), difficulty, order |
| 5 | `concept_prerequisites` | self-referential join, unique (concept, prerequisite) |
| 6 | `ingestion_jobs` | `pending` \| `running` \| `complete` \| `failed` + `error_message` |
| 7 | `tutoring_sessions` | per student + course; carries `current_hint_level` |
| 8 | `tutoring_messages` | role `student`\|`tutor`, `citations` JSON, `hint_level`, `is_refusal` flag |
| 9 | `assessments` | `generating` \| `draft` \| `published` \| `failed` (**DB CHECK constraint**), `config` JSON, `generation_error`, `published_at` |
| 10 | `questions` | type `mcq` \| `short_answer` \| `numeric`; `options`/`answer_key` JSON; `bloom_level`; `max_points` |
| 11 | `rubric_criteria` | per question, description + max_points |
| 12 | `submissions` | `pending_grading` \| `graded` \| `rejected`; **UNIQUE (assessment_id, student_id)** |
| 13 | `submission_responses` | `answer_text` or `answer_choice` ("A"–"D") |
| 14 | `grade_recommendations` | `pending_review` \| `approved` \| `overridden` \| `rejected`; `recommended_score`, `rationale` JSON, `evidence_citations` JSON; **unique per submission** |
| 15 | `final_grades` | **only written by `grading/service.finalize_grade()`** — the sole path to a released grade; `action` = `approved`\|`overridden` |
| 16 | `grade_audit_records` | AI score vs. teacher score + action + reason, one per final grade |

**Migrations:** `db/migrations/versions/0001_initial.py`, `0002_integrity_constraints.py`
(the latter added the submission unique constraint with de-dup, plus 15 FK indexes).
JSON-ish fields are stored as `Text`, not JSONB — migrating to JSONB is tracked as open work (F29).

---

## 7. `backend/core/` — cross-cutting infrastructure

| File | Responsibility |
|---|---|
| `config.py` | Single pydantic-settings `Settings` singleton. **No model name, path, or secret is hardcoded anywhere else.** Also holds `assert_safe_production_config()`. |
| `database.py` | Async SQLAlchemy engine + `AsyncSessionFactory` + `Base` |
| `dependencies.py` | `Depends()` factories: `get_db_session`, `get_retrieval_service`, `get_langfuse_client`, `get_gemini_pro`, `get_gemini_flash` |
| `security.py` | `hash_password`, `verify_password` (bcrypt/passlib), `create_access_token`, `create_refresh_token`, `decode_token` |
| `token_store.py` | **In-memory** revoked-refresh-token (`jti`) set, auto-pruning. Single-process; revocations lost on restart. |
| `exceptions.py` | `DomainError` hierarchy + `register_exception_handlers` |
| `logging.py` | `configure_logging()` + `RequestIdMiddleware` (correlation id on every log line) |
| `ratelimit.py` | Fixed-window `RateLimitMiddleware`; IP-keyed for auth, user-keyed for AI endpoints |
| `security_headers.py` | `SecurityHeadersMiddleware` on every response |
| `retry.py` | `call_with_retry` — bounded retry + exponential backoff + jitter for Gemini calls |
| `job_recovery.py` | `recover_stale_jobs()` — startup reaper marking orphaned in-flight jobs failed |
| `prompt_safety.py` | `wrap_untrusted()` — wraps untrusted document text / student answers in `<<UNTRUSTED …>>` markers, strips forged markers, prepends a security notice |
| `mock_gemini.py` | Deterministic mock Gemini Pro/Flash: topic-aware canned hierarchies + a **bag-of-words feature-hashing embedding** (768-dim, stopword-filtered) so similar text has positive cosine similarity |

**Mock embeddings caveat (important):** the mock's similarity scale is far lower than real embeddings,
so there are **two groundedness thresholds** in config — `groundedness_threshold = 0.65` (real Gemini)
and `groundedness_threshold_mock = 0.20` (mock ceiling ≈ 0.42, off-topic noise ≈ 0.0–0.1).

---

## 8. API surface

All routes below are relative to the backend origin. Roles enforced via dependencies; ownership
checks (`_require_teacher_owns_submission`, `_require_student_owns_session`) run per request.

**Health** — `GET /health` (liveness), `GET /health/ready` (readiness: DB + Chroma)

**Auth** (`/auth`)
- `POST /auth/login` → token pair
- `POST /auth/register` → new **student** account (201)
- `POST /auth/refresh` → rotate refresh token, issue new pair
- `POST /auth/logout` → revoke a refresh token
- `GET /auth/me` → current identity

**Courses** (`/courses`) — teacher-oriented
- `POST /courses/upload` (multipart; triggers ingestion background job)
- `GET /courses` (owned), `GET /courses/available` (student-visible)
- `GET /courses/{course_id}`, `GET /courses/{course_id}/structure`

**Tutoring** (`/tutor`) — student
- `POST /tutor/sessions`
- `POST /tutor/sessions/{session_id}/chat`
- `POST /tutor/sessions/{session_id}/hint`
- `GET  /tutor/sessions/{session_id}/messages`
- `GET  /tutor/sessions`

**Assessments** (`/assessments`)
- `POST  /assessments/generate` (teacher, 202 + background task)
- `GET   /assessments/course/{course_id}` (teacher)
- `GET   /assessments/{assessment_id}` (teacher draft preview)
- `PATCH /assessments/{assessment_id}/publish` (teacher)
- `POST  /assessments/{assessment_id}/submit` (student; 409 on duplicate)
- `GET   /assessments/published` (student)
- `GET   /assessments/my-submissions` (student)
- `GET   /assessments/submissions/{submission_id}` (student)

**Grading** (`/grading`) — teacher only
- `POST /grading/{submission_id}/grade` (trigger AI grading)
- `GET  /grading/queue`
- `GET  /grading/{submission_id}`
- `POST /grading/{submission_id}/approve`
- `POST /grading/{submission_id}/override`

---

## 9. Frontend

React 18 + TypeScript 5 + Vite 5, TanStack Query v5, React Router v6, Axios. No CSS framework —
plain CSS + CSS modules.

**Routing & role gating** (`src/App.tsx`): public `/login`, `/signup`; everything else nested inside
`ProtectedRoute` → `ErrorBoundary` → `AppShell`.
- Teacher-only: `/courses`, `/courses/:courseId`, `/grading`
- Student-only: `/assessments`, `/assessments/:assessmentId/take`, `/assessments/submissions`, `/assessments/submissions/:submissionId`, `/tutor`, `/tutor/:sessionId`
- Wrong role → `/not-authorized`; unknown authenticated path → `NotFoundPage` (sidebar intact)

**API base URL:** in local dev the app calls `/api`, which Vite proxies to `http://localhost:8000`
(no env var needed). For deploys, set `VITE_API_BASE_URL` to the backend origin.

**Auth storage:** `localStorage` keys `tf_access_token` and `tf_user`. An axios interceptor injects
the bearer token and clears storage on 401. **The frontend does not currently call `/auth/refresh`** —
the backend refresh/rotation flow exists but is unused by the UI (see §12).

Scripts: `npm run dev` · `npm run build` (`tsc && vite build`) · `npm run test` (vitest) · `npm run lint`

---

## 10. Configuration, running, and testing

### Backend environment variables (all read through `settings.*`)
| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:password@localhost:5432/tutorforge` | async driver required |
| `GEMINI_API_KEY` | `""` | required for real LLM calls |
| `GEMINI_PRO_MODEL` | `gemini-2.5-pro` | tutor, rubric, grading |
| `GEMINI_FLASH_MODEL` | `gemini-2.5-flash` | assessment generation |
| `EMBEDDING_MODEL` | `text-embedding-004` | |
| `CHROMA_PERSIST_PATH` | `data/chroma` | relative to `backend/` |
| `JWT_SECRET_KEY` | `change-me-in-production` | prod boot guard rejects default / <32 chars |
| `JWT_EXPIRE_MINUTES` / `JWT_REFRESH_EXPIRE_MINUTES` | `60` / `10080` (7 d) | |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | comma-separated, **no wildcard** |
| `USE_MOCK_GEMINI` | `false` | mock clients, zero API quota |
| `ENVIRONMENT` | `development` | `production` activates fail-closed guards |
| `LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY` / `_HOST` | `""` / `""` / cloud.langfuse.com | |
| `GEMINI_MAX_ATTEMPTS` / `_RETRY_BASE_DELAY` / `_RETRY_MAX_DELAY` / `_TIMEOUT_SECONDS` | `4` / `0.5` / `8.0` / `30.0` | |
| `JOB_STALE_AFTER_SECONDS` | `1800` | orphaned-job reaper threshold |
| `RATE_LIMIT_*` | enabled; auth 10/60 s, AI 20/60 s | |
| `MAX_UPLOAD_BYTES` / `MAX_UPLOAD_FILES` | 50 MB / 20 | oversized ⇒ 413 |
| `GROUNDEDNESS_THRESHOLD` / `_MOCK` | `0.65` / `0.20` | see §7 |

### Local run (this machine is **Windows 11 + PowerShell**)
```powershell
cd backend
.venv\Scripts\activate            # venv already exists at backend/.venv (Python 3.14 currently)
pip install -e ".[dev]"
copy .env.example .env            # then fill in values
alembic upgrade head
python -m db.seed                 # creates demo users
uvicorn main:app --reload --port 8000

cd ..\frontend
npm install
npm run dev                       # http://localhost:5173
```

**Seeded demo credentials** (created by `python -m db.seed`, stored only in the DB):
`teacher@demo.com` / `password123` and `student@demo.com` / `password123`.

### Tests
```powershell
cd backend;  pytest -q                    # runs fully offline against the Gemini mock
cd frontend; npm run test; npm run build
```
On Windows, prefix with `PYTHONIOENCODING=utf-8` when console encoding errors appear — this is the
pattern used throughout the project's tooling history.

### Deployment config (present, not confirmed live)
- **Backend → Render** via `render.yaml`: Docker service from `backend/`, `preDeployCommand: alembic upgrade head`, health check `/health`, managed Postgres 16, a **1 GB persistent disk** mounted at `/app/data` for Chroma, `JWT_SECRET_KEY` auto-generated by Render, secrets marked `sync:false`.
- **Frontend → Vercel** via `frontend/vercel.json`.
- **CI** — `.github/workflows/ci.yml`: backend ruff (**informational, `continue-on-error`**) + pytest; frontend `npm run build` + vitest. Runs on push to `main` and all PRs.

---

## 11. The hard invariants (do not break these)

These are architectural constraints, not preferences. They come from `docs/PROJECT-BRIEF.md` and are
enforced in code:

1. **Groundedness gate** — every agent retrieves evidence and calls `is_grounded()` *before* any LLM
   call. Not grounded ⇒ refuse. No fallback to general model knowledge. Target: **zero out-of-corpus leakage**.
2. **Teacher approval required** — a `FinalGrade` row is written **only** by
   `grading/service.finalize_grade()`, after an explicit teacher approve/override. AI output is always
   a *recommendation*. User-facing language must say "Recommended Score" / "Suggested Feedback" /
   "Instructor Review Required" — never "Final Grade" / "Official Grade".
3. **Grading determinism** — grading runs at `temperature=0` via `generate_deterministic()`. Identical
   submissions must produce identical scores. Target grading variance: **0**.
4. **Pedagogy before answers** — the tutor guides with questions and a 3-level hint ladder before
   revealing explanations; it refuses to solve graded assignments outright.
5. **Config-driven** — no model name, path, or secret hardcoded outside `core/config.py`.
6. **Self-contained tests** — the suite must keep running with no API key and no external services.
7. **Provider abstraction** — business/retrieval/grading logic stays independent of the model provider.
8. **Synthetic data only** — no real student PII, ever.
9. **Fail closed in production** — `assert_safe_production_config()` refuses to boot a production
   deploy on mock AI or a weak JWT secret.

Module convention: each feature is **router + service + schemas**; agents are **graph + nodes + state (+ prompts)**.

---

## 12. Project history and how it got here

Three distinct eras, which explains why some documents disagree with the code:

**Era 1 — GUIDE-AI-SDP greenfield build (2026-06-21 → ~2026-07-09).**
The project was built through a formal staged workflow (see §13): Workspace Detection → Requirements
Analysis → User Stories → Workflow Planning → Application Design, then per-unit BUILD stages across
7 planned units (U1 Auth, U2 Course Ingestion, U3 Retrieval, U4 Socratic Tutor, U5 Assessment Gen,
U6 Grading+Review, U7 Frontend). Artifacts live in `guide-docs/discover/`.
Frontend phases 4A (foundation), 4B (teacher course mgmt), 4C (student tutoring) were completed and
frozen; **Phase 4D (student assessment taking + teacher grading UI) was planned then implemented** —
those pages exist in the code today.

**Era 2 — Production hardening (2026-07-19 onward).**
A production audit produced **37 findings (F1–F37: 4 critical, 12 high, 14 medium, 7 low)**. These were
worked through as a dependency-ordered, lightweight PR-by-PR flow (not the full stage machinery, at
the developer's direction). Findings are referenced throughout the codebase by their **F-number in
code comments** (e.g. "F5" next to rate limiting, "F23" next to prompt safety) — that's what those
markers mean.

- **Milestone 1 — launch blockers (COMPLETE):** F14 dep pinning + lockfile · F8 handler logging ·
  F30 request-id logging · F13 test-DI harness · F27 DomainError · F9 health endpoints ·
  F1 Docker/Render · F2 prod API route · F3 mock-off guard · F6 JWT-secret guard ·
  F10 Gemini retry/timeout · F4-min job reaper · F5 rate limiting · F7 submission unique + F20 FK indexes
- **Milestone 2 — hardening (mostly complete):** F18 security headers + F15 upload caps ·
  F17 password policy + F31 constant-time login · F23 prompt-injection isolation ·
  F11 ingestion tests · F12 frontend test tooling + F32 wrong-role UX · F37 CI pipeline.
  Then **F19 refresh-token rotation + revocation** landed (`core/token_store.py`, `/auth/refresh`,
  `/auth/logout`, `tests/test_auth_session.py`) — this is **newer than what `guide-state.md` records**.
  Still open in this milestone: **F25/F26 cookie-based token storage** (frontend still uses
  localStorage) and **F16/F4-full** (externalized job queue + vector store).
- **Milestone 3 — technical debt (NOT STARTED):** F22 boundary-aware chunking (+F35) ·
  F29 JSONB migration · F24/F34/F36 perf + hygiene cleanup.

**Era 3 — public release.** Git history was squashed to a single commit
(`ab60d6e Initial release: TutorForge AI`), the README was rewritten as a public-facing document, and
`.gitignore` was extended to exclude all AI-workflow scaffolding.

---

## 13. Known gaps, stale docs, and gotchas

Things a newcomer would otherwise trip over:

- **`docs/ROADMAP.md` is stale.** It lists Phase 4D as "⏳ Ready" — it has since been implemented. It
  also references a `CURRENT_STATUS.md` that **does not exist** anywhere in the repo.
- **`docs/PROJECT-BRIEF.md` names "Gemini 3.1 Pro/Flash"**, but the code defaults to
  `gemini-2.5-pro` / `gemini-2.5-flash`. The brief is aspirational; config is authoritative.
- **Brief components 6 and 7 were never built.** There is **no Learning Analytics Agent** (no SM-2
  spaced repetition, no mastery map, no review queue) and **no Evaluation & Safety Agent**.
  `backend/eval/` is an empty package — none of the promised eval suites (groundedness probe,
  paraphrase-bias, grading-consistency, promptfoo LLM-as-Judge) exist. Every acceptance-criteria
  target in the brief (≥80 % teacher acceptance, 0 leakage, ≤5 % paraphrase delta) is therefore
  **unmeasured**.
- **`guide-docs/guide-state.md` under-reports progress** — it still shows DISCOVER "Units Generation"
  unchecked and PR-19–22 pending even though refresh/revocation shipped. Trust the code first.
- **Refresh tokens are backend-only.** The frontend never calls `/auth/refresh`; when the 60-minute
  access token expires the user is simply logged out on the next 401.
- **Single-process state.** The rate limiter, the revoked-token store, and the job reaper all keep
  state in process memory, and Chroma is a local on-disk store. **The backend cannot be horizontally
  scaled as-is** — that's exactly what F16/F4-full would address.
- **Background jobs are FastAPI `BackgroundTask`s**, not a durable queue. A crash mid-generation
  leaves a job that the startup reaper later marks `failed`.
- **Chroma collections are pinned to cosine distance at creation.** `retrieve()` computes
  `confidence = 1.0 - distance`, which is only valid for cosine. Collections created before
  `hnsw:space: "cosine"` was set must be **deleted and re-ingested** — it cannot be changed retroactively.
- **`bcrypt` is pinned to `>=4.0,<4.1`** because passlib 1.7.4 breaks against bcrypt 4.1+ (removed
  `__about__`) and 5.x (hard-raises on the 72-byte limit). Don't bump it casually.
- **Ruff lint debt (~44 pre-existing issues).** CI lint is intentionally non-blocking until a
  dedicated cleanup pass; then it should be flipped to blocking.
- **Known dead code:** `GRADING_SYSTEM_INSTRUCTION` is imported but never passed to the model.
- **A junk file is tracked at the repo root**, named
  `s -ExecutionPolicy RemoteSigned) ; (& c:UsersDhruvvDesktopAI_Tutorbackend.venvScriptsActivate.ps1)`.
  It's an accidental shell-redirect artifact containing a dump of the pre-squash `git log`. It should
  be deleted — note it also happens to be the only surviving record of the old commit history.
- **`docs/screenshots/` is empty** and the README's screenshot section is a placeholder.
- **License is undetermined** — "all rights reserved" until one is chosen.
- **The local venv is `backend/.venv` running Python 3.14**, while `pyproject.toml` allows
  `>=3.11,<3.15`, CI runs 3.12, and the docs recommend 3.12.

---

## 14. Working conventions on this project

- **Platform:** Windows 11, PowerShell is the primary shell (Git Bash also available). Paths are
  Windows-style; `&&` chaining does **not** work in Windows PowerShell 5.1 — use `;` / `if ($?)`.
- **Tests accompany code.** The standing instruction during the build was "make test files for each
  code file." Every hardening PR shipped with tests and reported a running suite total.
- **One change at a time.** During hardening the developer explicitly required "implement ONLY ONE
  planned PR at a time… STOP. Wait for approval before continuing." Expect approval gates between
  units of work.
- **Verify before concluding.** When a test failure was suspected to be test-only, the standing
  instruction was to first verify the production code is actually correct and to stop and explain if
  a real bug was found. (This exact check once correctly identified a bad mock rather than a bug.)
- **`guide-docs/audit.md` is append-only.** It must be edited by appending, never rewritten wholesale.

### The GUIDE-AI-SDP workflow (`CLAUDE.md`)
The repo root carries a `CLAUDE.md` (gitignored, local-only) defining **GUIDE-AI-SDP**, a mandatory
staged software-development workflow for AI assistants, with rule files in
`.project-guide-rule-details/`. In summary it requires: a welcome message and rule-file loading before
any action; a **DISCOVER phase** (workspace detection → reverse engineering → requirements → user
stories → workflow planning → application design → units generation); a **BUILD phase** (per-unit
functional design → NFR requirements → NFR design → infrastructure design → code generation, then
build & test); a placeholder **DEPLOY phase**; explicit written user approval at every stage gate;
no application code before Workflow Planning *and* a Code Generation plan are approved; and complete
audit logging of every raw user input to `guide-docs/audit.md`.

**In practice this was followed strictly for the original build and then deliberately relaxed** to a
lightweight PR-by-PR flow during hardening, with the audit trail backfilled retroactively. A new
session should be aware the file exists and will be loaded automatically in Claude Code — but note it
is **not present in the published repo**, so a fresh Claude Desktop chat won't have it unless it's
pasted in.

---

## 15. Quick orientation for a new session

If you're picking this up cold, read in this order:
1. This file.
2. `README.md` — the public framing.
3. `backend/main.py` — how everything is wired.
4. `backend/db/models.py` — the domain in 16 tables.
5. `backend/retrieval/service.py` + any `agents/*/graph.py` — where the grounding guarantee lives.
6. `frontend/src/App.tsx` — the whole UI surface in one file.

**The highest-value unfinished work**, in rough priority order:
1. Build the eval suite in `backend/eval/` — it is the project's biggest credibility gap, since every
   headline claim (zero leakage, zero grading variance, ≤5 % paraphrase delta) is currently unverified.
2. Finish the session story (F25/F26): use the existing refresh flow from the frontend and move tokens
   out of `localStorage`.
3. Externalize job queue + vector store (F16/F4-full) so the backend can scale beyond one process.
4. Clean the ruff debt and flip CI lint to blocking.
5. Delete the junk root file, refresh `docs/ROADMAP.md`, and add screenshots.
