# TutorForge Design Brief (2026-10-07)

> Notes added when copying into the repo (2026-10-08):
> - The mockups are now in `design/mockups/`. This brief plus the app's real behaviour win over the mockups when they disagree (see `design/README.md`).
> - Where this brief says source chips "open a short excerpt", that popover is NOT part of the restyle (new feature, tracked as "Later"). Restyle the chips only.
> - Use two accent tokens: `--accent` for text, borders and marks; `--accent-fill` for solid backgrounds that carry white text. In the light theme both can equal the `--accent` value below. This keeps dark mode a simple second token set later.
> - Dark mode is out of scope for the restyle.

**Goal:** move the UI away from the generic "AI SaaS dashboard" look (purple/indigo gradients, glow, a pill badge on everything) toward a calm, academic, trustworthy look that matches what the product is: a grounded tutor plus a teacher-reviewed assessment tool.

## References that shaped this
- Khan Academy teacher side: plain white cards, thin borders, soft icon tiles, text tabs, no gradients (teacher pages).
- Brilliant: serif headings, quiet body text, one strong accent on the main action (student pages).
- Quizlet: calm dark theme, if dark mode is wanted later.
- Quiz-taking patterns (Refero): question on top, large outlined options, thin progress bar, nothing else.
- Linear: colour is rare; status is quiet text, not badges.

## Design tokens (light theme, source of truth)
| Token | Value | Use |
|---|---|---|
| `--bg` | `#f5f2ec` | page background |
| `--surface` | `#fffdf9` | cards, top bar, sidebar |
| `--surface-2` | `#ece7db` | secondary surface, student chat bubbles, neutral chips |
| `--input` | `#ffffff` | text inputs (slightly lifted from cards so fields read as fields) |
| `--line` | `#e3ddd0` | hairlines, table rules |
| `--line-strong` | `#cdc5b3` | input and button borders |
| `--ink` | `#1d1b16` | primary text (15.4:1 on bg) |
| `--muted` | `#6b665a` | secondary text (5.1:1 on bg) |
| `--tertiary` | `#6f695c` | placeholders, table headers, breadcrumbs (4.9:1 on bg, 5.4:1 on surface) |
| `--accent` | `#1d5c4a` | primary action, selected state (7.8:1 with white text) |
| `--accent-soft` | `#e4efe9` | selected option, success background |
| `--amber` / `--amber-soft` | `#8a5a0a` / `#fbf0dc` | needs attention, awaiting review (5.2:1) |
| `--red` / `--red-soft` | `#9c2f2a` / `#f8e4e1` | errors, destructive actions (7.2:1) |
| `--info` / `--info-soft` | `#4f6578` / `#e6ecf1` | informational notes and source/citation indicators only (6.0:1) |

**Contrast fix found in the mockups:** the first mockups used `#9a9486` for placeholders, table headers and breadcrumbs. That is only 2.7:1 on the page background and fails WCAG AA. Use `--tertiary` instead. Never use a lighter grey for text that carries meaning.

## Typography
- **Display text (page and course titles, wordmark, quiz question stems, large scores, empty-chat prompt):** a serif. Mockups used Lora; Lora is free and fine to ship. Weight 600.
- **Everything else (nav, buttons, tables, metadata, answers, grading evidence):** a humanist sans-serif. Mockups used Carlito only because it was installed in the sandbox. Ship **Source Sans 3** (similar feel, free) or equivalent. Avoid Inter as the default; it is the most common "generated app" font.
- Self-host fonts (for example `@fontsource/lora`, `@fontsource/source-sans-3`) with a system fallback stack. Do not depend on a CDN at runtime. Verify the real fonts load on every route.
- **Scale:** H1 36-40px serif; H2 26-28px serif; H3 19-20px (serif or bold sans); body 16-17px; small 14-15px; table header 13.5px uppercase with 0.06em tracking.

## Semantic colour rules
| Meaning | Treatment |
|---|---|
| Primary action | solid accent button |
| Correct / released / ready / published | accent, with a check icon and the word, never colour alone |
| Needs attention / awaiting review / check key | amber, with a warning icon and the word |
| Error / incorrect / destructive | red, with the word |
| Draft / neutral status | warm grey chip (`--surface-2`) |
| Informational / source reference | `--info`, used sparingly |
- **Never rely on colour alone.** Every state carries an icon or text label. This is a hard rule.
- Don't use accent green for everything. Selected navigation should be a quiet marker (ink text plus a thin accent bar), not a green fill; keep green for actions and positive states.

## Components
- **Buttons, three levels:** primary (solid accent), secondary (outlined), tertiary (text only). Destructive actions are red text or red outline, always behind a confirm step. In teacher tables, `Review` (the teacher has to do something) is secondary-outlined; `View` is tertiary text.
- **Cards:** `--surface`, 1px `--line`, radius 12px, no shadow. Tables are tables (rules between rows, no card per row).
- **Status:** small text chips only where state really matters (Ready, Published, Draft, Awaiting teacher review). Do not put a chip on every row.
- **Question options:** large outlined rows with a lettered tile; selected = accent border plus soft fill plus filled letter tile.
- **Citations:** small outlined chips with a document icon ("DAA Lab Guide, p. 12").
- **Tutor messages:** tutor text is plain on the page with a small uppercase "TUTOR" label and a tiny book mark (no humanoid avatar, no robot); student messages sit in a `--surface-2` bubble.
- **Interaction:** obvious hover and visible focus ring on everything clickable; subtle hover surface on table rows; micro-interactions functional only, and honour `prefers-reduced-motion`.

## Screen-by-screen changes
- **Student quiz-taking and the whole student assessment area** (worst offender in the frontend audit, currently unstyled): rebuild on the tokens. Show the question position ONCE ("Question N of M") plus a thin progress bar. Selected answer is unmistakable.
- **Student submissions:** pending submissions show "Awaiting teacher review" with the helper line and no score. Released ones show the score large, in the serif.
- **Student tutor:** course name and session title at the top, the "asks questions rather than giving answers, and only uses your course material" line, the docked input, and the hint control with its level. Citation chips restyled only.
- **Teacher course page:** tabs (Outline, Assessments, Students, Settings) so long outlines no longer bury Assessments and Students. Tables as in the mockup.
- **Teacher draft review:** correct option shaded and tagged "Marked correct"; numeric key shown in a monospace box. Keep the "Check every answer key before you publish" banner at its current prominence until per-question verification exists; it is the safety net for a known failure (the generator has produced wrong keys).
- **Mobile (375px):** replace the sidebar and top scroll bar with a bottom tab bar (Courses, Tutor, Quizzes, Progress) so Log out and Progress are reachable. Tables become stacked rows or cards. Tap targets at least 44px.

## Mockup features that do NOT exist in the app yet
Don't build these as part of the restyle:
- "Your answers are saved as you go" and "Save and exit" (the app currently saves on submit only).
- The question-map grid on the quiz page.
- Per-question "Verified / Needs review" state (would need a backend field).
- Clickable source chips that open a passage (popover is new UI).
- Suggested-question chips in the tutor (the product has none).
- Hint-level dots: confirm against the real hint behaviour before showing anything.
- Dark mode.
Only show copy that is true of the product.

## What to avoid
Gradients, glow, glassmorphism, heavy shadows, oversized rounded cards, cartoon avatars or robot mascots, confetti or heavy gamification, saturated colours, and a status badge on every row.

## Implementation plan
0. Inspect the existing frontend first: how styles are organised, existing shared components, the current theme (the app is dark today). Don't assume the structure.
1. Tokens as CSS variables and the font setup. No visual redesign yet.
2. Shared components (button levels, chip, card, input, table, tabs, modal, plus skeleton, empty state, inline error card and 404 layout).
3. Student area first, then My Submissions, then the tutor page.
4. Teacher pages.
5. Mobile navigation and responsive tables.
6. Polish: focus rings, keyboard states, reduced-motion, accessibility.
Behaviour must not change; existing tests stay green.

## Later (features, not styling)
- Per-question "verified" state plus a "N of M verified" summary and gating Publish on it.
- Autosave of in-progress quiz answers, then show "Answer saved".
- Passage-preview popover on source chips (with a roughly 200-character cap).
- Dark mode from the same tokens.
