Design inputs for the restyle. Priority order when sources disagree:
1. The real app's current behaviour and copy (never change behaviour).
2. DESIGN-BRIEF.md (tokens, type, components).
3. STATES-AND-CHAT.md (empty, loading, error, 404, chat states).
4. The mockups (visual reference only).

Known mockup differences from the real app (follow the app):
- Grading Approve button: the app's labels are "Approve recommended score (4 / 6)" and "Override and finalize (3 / 6)". Ignore "Approve 4 / 6" and "Approve 4.5 / 6" in the mockups.
- The tutor mockup shows a source popover. It is NOT being built in this restyle; restyle the citation chips only.
- fix-teacher-courses-failed.png has a grey explanatory note under the cards. Ignore it; it is not UI.
- The quiz mockup (3-student-quiz.png) shows "2 of 6" and "QUESTION 2". Show the question position once.
- Mockups use stand-in fonts (Lora, Carlito). Ship Lora and Source Sans 3.
- mockups-dark/ is for the later dark-mode task only. Do not build dark mode in the restyle.

mockups/ contents
- 1-login ... 8-mobile-submissions: the main light screens (student and teacher, desktop; the last is mobile).
- fix-*: screens showing fixes already built (failed course card, not-found page, grading evidence percentage).
- light-1 ... light-4: tutor empty chat, tutor thinking state, loading skeleton, teacher empty and error states.
