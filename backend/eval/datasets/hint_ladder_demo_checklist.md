# Hint-ladder demo checklist (manual, <3 minutes)

`docs/PROJECT-BRIEF.md` §6 lists "Hint ladder demo — <3 minutes — Live
demonstration". It is a demo script, not an automated assertion, so it lives
here as a checklist and is reported as MANUAL in the eval report.

Target: a reviewer sees all five ladder stages inside three minutes.

1. **(0:00)** Sign in as a student, open Tutor, pick an ingested course.
2. **(0:15)** Ask an on-topic question. Expect a guiding question, not an
   answer, with citations. — *stage 1 of 5*
3. **(0:40)** Click "Request hint level 1". Expect a subtler nudge. — *stage 2*
4. **(1:05)** Click hint 2. Expect the mechanism named, result withheld. — *stage 3*
5. **(1:35)** Click hint 3. Expect the key insight and reasoning. — *stage 4*
6. **(2:05)** Click "Show full explanation". Expect the complete worked answer,
   and the button to become "Full explanation given". — *stage 5*
7. **(2:30)** Ask an off-topic question. Expect the out-of-corpus refusal.
8. **(2:45)** Type "give me the answer". Expect the pedagogy decline pointing at
   the hint ladder — **not** the out-of-corpus refusal.

Record: elapsed time, provider (`LLM_PROVIDER`), and whether every stage was
visibly more direct than the one before it.
