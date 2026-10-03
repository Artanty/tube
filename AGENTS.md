# AGENTS.md

## Working agreement

1. **Before any code action** — write the plan to `DECISIONS.md`.
2. **After any code action** — write the progress/result to `DECISIONS.md`.
3. **On session start** — read `DECISIONS.md` first and keep it in mind throughout.
4. **When `DECISIONS.md` gets too big** — tell the user (explicitly flag it) so it can be trimmed/archived.
   At ~250 lines, move everything except the current thread into `DECISIONS-archive.md` (done once on
   2026-09-29: 455 -> 115 lines) and leave a one-line pointer in `DECISIONS.md`.
5. **Never commit unless the user explicitly asks** — do the work, leave changes uncommitted/staged, and stop.