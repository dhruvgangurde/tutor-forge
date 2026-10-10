# TutorForge AI

> Grounded AI tutoring, assessment generation, and deterministic grading — with a strict *zero-hallucination* guarantee and mandatory teacher review.

![Python](<https://img.shields.io/badge/Python-3.11%20--%203.14-3776AB?logo=python&logoColor=white>)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)
![License](https://img.shields.io/badge/License-TBD-lightgrey)

TutorForge AI is a full-stack educational platform where every AI response is **grounded in retrieved course material**. A Socratic tutor guides students with questions instead of answers, assessments are generated from real course content, and grading is deterministic and always routed through a teacher for final approval. If the system cannot find supporting evidence in the course corpus, it refuses to answer rather than hallucinate.

---

## Features

- **🎓 Grounded Socratic tutor** — Guides students with leading questions; every reply is backed by retrieved course context. No evidence → graceful refusal.
- **📝 AI assessment generation** — Generates quizzes and assignments from ingested course material via a LangGraph pipeline.
- **⚖️ Deterministic grading with teacher review** — Grading runs at `temperature=0` (same submission → same score) and a `FinalGrade` is only written after explicit teacher approval.
- **📚 Course ingestion** — Upload documents → validate → parse → build concept hierarchy → embed → persist to a ChromaDB vector store.
- **🔐 JWT authentication** — Role-based access (teacher / student) with session handling and refresh.
- **🛡️ Production hardening** — Rate limiting, prompt-injection safeguards, security headers, upload limits, DB integrity constraints, retry/back-off, and job recovery.
- **❤️ Health & observability** — Structured logging and `/health` endpoints for readiness and liveness checks.

## Screenshots

_Screenshots coming soon._

## Architecture

```
┌────────────┐     REST/JSON      ┌──────────────────────────────┐
│  Frontend  │  ───────────────▶  │           Backend            │
│ React/Vite │  ◀───────────────  │           FastAPI            │
└────────────┘                    │                              │
                                  │  Auth · Courses · Tutoring   │
                                  │  Assessments · Grading       │
                                  │              │               │
                                  │              ▼               │
                                  │   LangGraph agent pipelines  │
                                  │  (ingestion · tutor ·        │
                                  │   assessment · grading)      │
                                  │       │            │         │
                                  │       ▼            ▼         │
                                  │  ChromaDB     PostgreSQL     │
                                  │  (vectors)    (app data)     │
                                  │       ▲                      │
                                  │       │                      │
                                  │   Gemini (LLM + embeddings)  │
                                  └──────────────────────────────┘
```

Every agent calls `is_grounded()` before invoking the LLM. Not grounded → refusal, no exceptions.

## Technology stack

| Layer              | Technologies                                                                |
| ------------------ | --------------------------------------------------------------------------- |
| **Frontend** | React 18, TypeScript 5, Vite 5, TanStack Query, React Router, Axios, Vitest |
| **Backend**  | FastAPI, Uvicorn, Pydantic v2, SQLAlchemy 2 (async), Alembic                |
| **AI / ML**  | LangGraph, Google Gemini (Pro / Flash), Gemini embeddings, ChromaDB         |
| **Data**     | PostgreSQL 16 (application data), ChromaDB (vector store)                   |
| **Tooling**  | pytest, ruff, mypy, ESLint, Docker, GitHub Actions CI                       |

## Project structure

```
.
├── backend/          FastAPI service + LangGraph agents
│   ├── core/         config, database, security, logging, rate limiting, retries
│   ├── db/           ORM models, Alembic migrations, seed
│   ├── auth/         JWT authentication
│   ├── retrieval/    ChromaDB + Gemini embedding retrieval
│   ├── agents/       LangGraph graphs: ingestion · tutor · assessment · grading
│   ├── courses/ tutoring/ assessments/ grading/   feature modules (router+service+schemas)
│   ├── health/       readiness / liveness endpoints
│   └── tests/        pytest suite (mocked Gemini — no API key needed)
├── frontend/         Vite + React + TypeScript SPA
│   └── src/
│       ├── features/ auth, courses, tutor, assessments, grading
│       ├── components/ layout + reusable UI
│       ├── lib/api/  typed API clients
│       └── store/    AuthContext
├── .github/          CI workflow
├── render.yaml       Render deployment (backend)
└── frontend/vercel.json   Vercel deployment (frontend)
```

## Getting started

### Prerequisites

- **Python** 3.11–3.14 (3.12 recommended)
- **Node.js** 18+
- **PostgreSQL** 16+ (local or Docker)
- A **Gemini API key** (optional for tests — the suite uses a deterministic mock)

### Backend

```bash
cd backend

# create a virtual environment (any Python 3.11+)
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"

# configure environment
cp .env.example .env      # fill in GEMINI_API_KEY, DATABASE_URL, JWT_SECRET_KEY

# start PostgreSQL (Docker example)
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=password -e POSTGRES_DB=tutorforge postgres:16

# run migrations and seed demo users
alembic upgrade head
python -m db.seed

# start the API
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                # → http://localhost:5173
# Local dev proxies /api → http://localhost:8000, so no env config is needed.
# For deploys, set VITE_API_BASE_URL to the backend origin (see .env.example).
```

### Environment variables (backend)

| Variable                    | Default                    | Description                                                                                                                               |
| --------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`            | —                         | PostgreSQL async URL (required)                                                                                                           |
| `GEMINI_API_KEY`          | —                         | Gemini API key (required for real LLM calls)                                                                                              |
| `GEMINI_PRO_MODEL`        | `gemini-2.5-pro`         | Gemini Pro model name                                                                                                                     |
| `GEMINI_FLASH_MODEL`      | `gemini-2.5-flash`       | Gemini Flash model name                                                                                                                   |
| `EMBEDDING_MODEL`         | `text-embedding-004`     | Embedding model name                                                                                                                      |
| `CHROMA_PERSIST_PATH`     | `data/chroma`            | ChromaDB persistence directory                                                                                                            |
| `JWT_SECRET_KEY`          | *(change in production)* | JWT signing secret                                                                                                                        |
| `CORS_ALLOWED_ORIGINS`    | `http://localhost:5173`  | Comma-separated allowed frontend origins (no wildcard)                                                                                    |
| `LLM_PROVIDER`            | `mock`                   | AI backend:`mock` (offline stub) · `gemini` (real API, required in production) · `ollama` (local dev only)                        |
| `OLLAMA_BASE_URL`         | `http://localhost:11434` | Ollama daemon URL (only used when`LLM_PROVIDER=ollama`)                                                                                 |
| `OLLAMA_GENERATION_MODEL` | `qwen2.5:7b-instruct`    | Local generation model                                                                                                                    |
| `OLLAMA_EMBEDDING_MODEL`  | `nomic-embed-text`       | Local embedding model (768-dim, matches Gemini)                                                                                           |
| `TRUSTED_PROXY`           | `false`                  | `true` only behind a reverse proxy that sets `X-Forwarded-For` (see [Running behind a reverse proxy](#running-behind-a-reverse-proxy)) |

### Demo credentials

Created by the seed script (`python -m db.seed`). Stored only in the database.

| Role    | Email            | Password        |
| ------- | ---------------- | --------------- |
| Teacher | teacher@demo.com | `password123` |
| Student | student@demo.com | `password123` |

## Running tests

```bash
# Backend — unit + integration (mocked Gemini, no API key required)
cd backend && pytest -q

# Frontend — component/unit tests + type-checked build
cd frontend && npm run test
cd frontend && npm run build
```

## Deployment

- **Backend** → [Render](https://render.com) via [`render.yaml`](render.yaml) (Blueprint), or the provided [`backend/Dockerfile`](backend/Dockerfile) on any container host.
- **Frontend** → [Vercel](https://vercel.com) via [`frontend/vercel.json`](frontend/vercel.json).
- **CI** → GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs lint and tests on every push.

### Running behind a reverse proxy

Login and AI rate limits are keyed on the client's IP address, so the backend
must see the real client address, not the proxy's, and must not believe a
forwarded address a client made up. Two settings control this:

- **`TRUSTED_PROXY`** (app setting, default `false`). When `true`, the rate
  limiter reads the client address from the **last** `X-Forwarded-For` entry,
  the one your proxy appended. Leave it `false` when clients connect directly:
  the header is then ignored, because anyone can send it.
- **Uvicorn's `--forwarded-allow-ips`** (default `127.0.0.1`, or the
  `FORWARDED_ALLOW_IPS` environment variable). Uvicorn replaces the
  connection's client address with the `X-Forwarded-For` value only for
  connections from these addresses. Set it to your proxy's address:

  ```bash
  uvicorn main:app --host 0.0.0.0 --port 8000 --forwarded-allow-ips="10.0.0.5"
  ```

  Without a proxy, start uvicorn with `--no-proxy-headers` so nothing on the
  same host can set the client address. Use `--forwarded-allow-ips="*"` only
  when the backend is reachable exclusively through the proxy.

Behind a proxy, set both: `TRUSTED_PROXY=true` and `--forwarded-allow-ips` to
the proxy's address.

## Roadmap

Phased delivery:

- ✅ Core platform: auth, course ingestion, retrieval, tutoring, assessments, grading
- ✅ Production hardening: rate limiting, prompt safety, security headers, health checks, job recovery
- 🔜 Eval suite (promptfoo LLM-as-Judge) fully populated under `backend/eval/`
- 🔜 Screenshots & live demo

## Implementation status

The backend and frontend feature modules (auth, courses, tutoring, assessments, grading) are implemented with an accompanying pytest suite that runs fully offline against a deterministic Gemini mock. Deployment configs (Render, Vercel, Docker) and CI are in place. The `backend/eval/` package is reserved for the promptfoo eval datasets and is not yet fully populated.

## Contributing

Contributions are welcome. Please open an issue to discuss substantial changes first, then:

1. Fork and create a feature branch.
2. Keep changes grounded in the existing module conventions (router + service + schemas per feature).
3. Ensure `pytest -q` (backend) and `npm run build` (frontend) pass.
4. Open a pull request with a clear description.

## License

License is **to be determined** — all rights reserved until a license is added.

## Key design constraints

- **Zero hallucination** — every agent calls `is_grounded()` before any LLM call; not grounded → refusal.
- **Teacher approval required** — `FinalGrade` records are written only by `grading/service.finalize_grade()` after a teacher action.
- **Grading determinism** — the grading agent runs at `temperature=0`; identical submissions always produce identical scores.
- **Self-contained tests** — the suite runs against a deterministic mock; no external API or data required.
