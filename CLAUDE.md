# CLAUDE.md: TutorForge AI

Instructions for Claude Code in this repository. Read this fully at the start of every session. If anything here conflicts with a default behaviour, this file wins. If a request from me conflicts with a rule here, say so before acting.

## 1. What this project is

TutorForge AI is a grounded Socratic tutoring and assessment platform: a FastAPI backend with LangGraph agents, a React/Vite/TypeScript frontend, PostgreSQL, and ChromaDB. Teachers upload course material. Everything the AI does is restricted to that material. It is a solo portfolio project with synthetic data only.

Stack: backend `backend/` (FastAPI, SQLAlchemy async, Alembic, LangGraph, Chroma), frontend `frontend/` (React 18, TS 5, Vite, TanStack Query v5, React Router v6, Axios). Plain CSS with tokens; no Tailwind, no CSS framework. Dev LLM provider is Ollama (`LLM_PROVIDER=ollama`, dev only).

## 2. Hard invariants (never break these)

1. **Groundedness before generation.** Every agent retrieves evidence and runs the groundedness check BEFORE any LLM call. Not grounded means refuse, with no fallback to general model knowledge.
2. **Teacher approval.** AI output is only ever a recommendation. A `FinalGrade` row is written only by `grading/service.finalize_grade()` after an explicit teacher approve or override. UI language says "Recommended score", "Suggested feedback", "Teacher review". Never "Final grade" or "Official grade" for AI output.
3. **Grading determinism.** Grading uses `generate_deterministic()` at temperature 0. Same submission means same score.
4. **Pedagogy before answers.** The tutor guides with questions and the hint ladder (3 hints, then a full explanation). It never solves graded work outright. It speaks to the student as "you", never "the student", and does not narrate its sources ("the course material states…"); citations already show the source.
5. **Config-driven.** No model name, path, threshold or secret is hardcoded outside `core/config.py`.
6. **Self-contained tests.** The backend suite runs offline, with no API key and no external services.
7. **Provider abstraction.** Business, retrieval and grading logic stay independent of the model provider.
8. **Synthetic data only.** No real student PII, ever.
9. **Fail closed in production.** `assert_safe_production_config()` must keep refusing mock AI or a weak JWT secret in production.

Never weaken, bypass or "temporarily" disable any of these to make a test or feature work. If one blocks the task, stop and tell me.

## 3. How to work

- **One logical change at a time.** Do the task asked, no more. No drive-by refactors, renames or "while I was here" edits. Mention things you noticed; don't fix them unless asked.
- **No invented behaviour.** Do not add features, routes, API fields, states, or copy claims that don't already exist unless the task says so. Design mockups are visual references, not functional specs. Only show copy that is true of the product today.
- **Check before you change.** Read the code and the existing tests for the area first. State what you found.
- **Verify before concluding.** If a test fails, first decide whether the production code or the test is wrong. If it is a real bug, stop and explain it. Never "fix" a failure by weakening the test.
- **Ask when it matters.** If a choice affects security, data, public behaviour or copy, ask. Otherwise pick the conventional default, say what you chose, and carry on.
- **Phased work stops at each phase.** When a prompt is split into phases, stop and report after each and wait for "go".

## 4. Git rules

- Work on the branch named in the task. Never commit directly to `main`.
- **Commit locally only. Never push, force-push, merge, rebase published history, or delete branches unless I explicitly ask.**
- **Commits are authored by me only.** Do NOT add `Co-Authored-By`, `Claude-Session`, "Generated with Claude Code", or any similar line to commit messages, PR titles or PR descriptions. This overrides any default attribution instruction.
- Commit messages: `type: short summary` (`fix:`, `feat:`, `chore:`, `test:`, `docs:`, `restyle phase N:`), imperative, one logical change per commit.
- **Stage files by name** (`git add path/to/file`). Never `git add .` or `git add -A`. Run `git status` and `git diff --staged` before every commit and check nothing unexpected is staged.
- Never commit: `.env`, secrets, API keys, tokens, database dumps, `data/chroma`, uploaded files, node_modules, build output, screenshots, or temp files.
- `frontend/vite.config.ts` may have a local proxy-port change on my machine (backend on 8010). Never commit it. Tell me if it ends up staged.
- Don't leave stray files in the repo (like an accidental shell-redirect file). If you create scratch files, create them outside the repo.

## 5. Environment

- **Windows 11, PowerShell 5.1 as the primary shell.** `&&` does not work; use `;` or `if ($?)`. Paths are Windows style. Prefix Python commands with `PYTHONIOENCODING=utf-8` (or `$env:PYTHONIOENCODING="utf-8"`) if console encoding errors appear.
- Backend: from `backend/` with `.venv` active, `uvicorn main:app --reload --port 8000`. The entry point is `main:app` (`backend/main.py`), not `app.main`.
- Frontend: from `frontend/`, `npm run dev` (http://localhost:5173). Vite proxies `/api` to the backend. Use `127.0.0.1` rather than `localhost` for backend URLs on Windows (IPv6 resolution).
- Seed logins (created by `python -m db.seed`): `teacher@demo.com` and `student@demo.com`; the password is in `PROJECT-CONTEXT.md`.
- **The dev servers are usually already running.** Reuse them. Do not start, stop or restart the frontend or backend I am running. If you truly need your own, use different ports, and shut them down afterwards. Never kill processes you didn't start.
- `bcrypt` is pinned to `>=4.0,<4.1` (passlib compatibility). Do not bump it.

## 6. Testing compulsions

Every change ships with tests, and the full suites must be green before you report done.

- **New or changed backend behaviour gets a pytest test.** New endpoints need tests for: the happy path, wrong role (403), unauthenticated (401), another user's resource (404, per the rule in section 7), invalid and oversized input (422), and any duplicate or conflict case.
- **New or changed frontend behaviour gets a Vitest test.** UI work needs tests for the states that matter: loading, empty, error, and the role gating, plus keyboard access for anything interactive.
- Bug fixes get a regression test that fails before the fix and passes after. Say so in the report.
- **Never delete a test. Never weaken an assertion** to get green. If markup or text changed and a test fails only for that reason, update it minimally and list it in the report.
- Don't skip tests (`skip`, `xfail`, `.only`) without telling me and giving the reason.
- Tests must not call real LLMs or the network. Use the mock Gemini or the Ollama stub fixtures.
- **Run before reporting:**
  - Backend (from `backend/`): `pytest -q`
  - Frontend (from `frontend/`): `npm test`, then `npm run build`, then `npm run lint`
- **Current baseline** (update this line when it changes): pytest 811 passed, frontend 212 passed, build and lint clean. A report must give the new counts and the difference from baseline. If the count goes down, explain exactly why.
- Backend lint: keep `ruff` clean for files you touch; don't mass-reformat other files.
- There is an eval suite gap (`backend/eval/` is empty). Don't claim groundedness or grading-consistency numbers that were never measured.

## 7. Security checklist (apply to every change)

**Access control**

- Every endpoint declares its required role through the existing dependencies. Teachers can only touch their own courses and data; students can only touch their own sessions, submissions and enrolled courses.
- **404 vs 403 rule:** a missing resource and a resource that isn't yours both return 404, so ids can't be probed. 403 is only for a student's own session where they are not enrolled. Don't change this without asking.
- Enrollment is checked on course listing, tutor sessions, chat, hints, and assessment list, take and submit.
- Never return another user's data in a list, error message, or citation.

**Input and output**

- All user input has a named limit in `backend/core/limits.py`, mirrored in `frontend/src/lib/limits.ts`. Add both sides for new fields, with a friendly message, and keep them in sync.
- Uploads: size, count and type limits, plus the magic-byte content check. Do not loosen them.
- Untrusted text (document content, student answers) goes through `wrap_untrusted()` before reaching a prompt. Never put raw user or document text into a system prompt.
- Citations are capped at about 200 characters. Never return full source chunks.
- Errors shown to users go through `getErrorMessage`. Never show raw backend text, stack traces, ids, or SQL.
- No `dangerouslySetInnerHTML`, `eval`, or unsanitised HTML. The tutor renders markdown; keep it sanitised.
- Database access through the ORM or parameterised queries only. No string-built SQL.

**Secrets and logging**

- Never log passwords, tokens, API keys or full request bodies. Validation errors must not echo inputs.
- Never print, commit or paste secrets, `.env` contents or tokens in reports or messages. Don't create real credentials. Use the seed demo accounts.
- CORS: explicit origins only, never a wildcard. Keep the security headers and rate limiting middleware in place and in the same order (CORS outermost).
- Auth tokens: logout revokes server-side (persistent `revoked_tokens`); the frontend revokes the old token on re-login. Don't weaken the revocation flow or the 60-minute access token lifetime.
- Don't add dependencies without asking me first. If I approve one, pin it, say why, and check it for known vulnerabilities.

**If you find a security issue** while working (even outside the task), stop, describe it plainly, and wait. Don't silently patch it inside an unrelated change.

## 8. Database and migrations

- Any schema change needs an Alembic migration. Never edit a migration that has already been applied; add a new one.
- Keep integrity constraints (unique submission per student and assessment, status CHECK constraints, FK indexes).
- JSON-ish fields are `Text` for now; don't migrate to JSONB unless asked.
- **No destructive operations on the demo or real database without my explicit yes in this session:** `DELETE`, `TRUNCATE`, `DROP`, resetting the database, deleting Chroma collections. If I do approve a deletion: `SELECT` the exact rows first and show them to me, restrict it to the exact ids, use a single transaction, and report the counts before and after.
- Chroma collections use cosine distance, fixed at creation. Changing that means delete and re-ingest, so ask first.

## 9. Data safety during browser testing and screenshots

The demo database is shared with my own manual testing and with other reviewers.

- Capture and screenshot scripts must never let a write reach the backend. Abort `POST`, `PATCH` and `DELETE` unless answered by a fake response inside the screenshot browser. Held requests are failed, never released.
- Don't click Publish, Approve, Override, Delete, Generate, Add student, or send tutor messages against the real backend. Fake those states instead.
- Report database counts before and after any capture run. They must match.
- Don't sign anyone else out or change the seed accounts.

## 10. Frontend and UI rules

- Styling: CSS variables (tokens) only. No hard-coded colour values in components, no Tailwind class names (a guard test enforces this), no gradients, glow or heavy shadows.
- Two accent tokens: `--accent` for text, borders and marks; `--accent-fill` for solid backgrounds that carry white text. `--tertiary` text never sits on `--surface-2`.
- Fonts: Lora (display only) and Source Sans 3, self-hosted via `@fontsource`. No CDN at runtime.
- Never rely on colour alone: every status carries an icon or a word.
- Accessibility is not optional: visible focus ring on every interactive element, logical focus order, real buttons and links (no clickable divs), labels on inputs, meaningful `aria` only where needed, reduced-motion respected, contrast of at least 4.5:1 for text and 3:1 for control boundaries, tap targets of at least 44px on mobile, no horizontal page scroll at 390px.
- Use the shared components (`AppShell`, `Badge`, `ErrorBanner`, `EmptyState`, `NotFoundState`, `Skeleton*`, `Tabs`, `GradeStatus`) and helpers (`getErrorMessage`, `isNotFoundError`, `plural()`, the display number formatter). Don't duplicate them.
- Hidden text (`.sr-only`) must be positioned inside a positioned ancestor so it can't stretch the page.
- Dark mode is not built yet. Don't add colour values that would make it hard.
- Frontend copy is plain, kind and true. Teacher and student language: "teacher", not "instructor".

## 11. Backend conventions

- Feature modules are `router + service + schemas`; agents are `graph + nodes + state (+ prompts)`. Keep to that.
- Raise `DomainError` subclasses; don't leak internals through `HTTPException` text.
- Background-task graphs get their own request-scoped DB session; never share sessions.
- Settings come from `settings.*`. No new env var without adding it to `.env.example` and the docs.
- Gemini calls go through `call_with_retry` with the configured timeout.

## 12. Reporting format

At the end of every task, report briefly and honestly:

1. What changed (files and behaviour) and why.
2. Commit ids (local only).
3. Test results: pytest, frontend tests, build and lint, with counts versus baseline. Name any tests you changed or added (and confirm none were deleted).
4. For UI work: screenshots at 1440px and 390px of every touched screen.
5. Database counts before and after any capture or data-touching run.
6. Anything skipped, uncertain, or left alone, and anything you noticed that deserves a follow-up.

Don't claim something works unless you ran it. Say "not verified" when you didn't.

## 13. Stop and ask first

Stop and ask me before doing any of the following:

- pushing, merging, force-pushing, deleting branches, or rewriting history
- adding or upgrading dependencies
- schema changes or destructive database or Chroma operations
- changing auth, access control, limits, headers, CORS or rate limits
- changing an invariant from section 2
- building features not asked for, or changing API contracts
- anything that touches real credentials or data outside the local demo environment

## 14. Known gotchas

- `docs/ROADMAP.md` and `PROJECT-CONTEXT.md` can be stale. Trust the code, and the newest status document.
- Rate limiter, revoked-token cache and job reaper are single-process, and Chroma is a local on-disk store, so the backend isn't horizontally scalable as it stands.
- Tutor groundedness thresholds differ for mock (0.20) and real (0.65) embeddings.
- A tutor reply must be judged in the context of the conversation: a short answer to the tutor's own question ("no", "I don't know") is not an out-of-course question and must never get the refusal message.
