# my memory

Private memory repo for a [dip.ink](https://github.com/d6o/dip.ink) deployment.

- `notes/` — inbox: agents drop timestamped note folders here (via `wiki_note_drop`).
- `wiki/` — the curated wiki the hourly curator maintains. Browse it in Obsidian.
- `raw/` — immutable source documents for manual ingestion.
- `AGENTS.md` — the schema + curation contract every maintaining agent follows.
- `scripts/` — linter, index generator, log rotation, weekly distill, curator supervisor.
- `.github/workflows/` — the curator (hourly), synthesis (weekly), review-queue (daily) agents.

Setup checklist (see the dip.ink README for the full walkthrough):

1. Push this repo as **private**.
2. Add the `PI_API_KEY` Actions secret (LLM key for the curator).
3. Actions → General → Workflow permissions → "Read and write permissions".
4. Point your dip.ink stack's `WIKI_REPO_URL` at this repo.
