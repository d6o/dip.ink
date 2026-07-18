# my memory

Private memory repo for a [dip.ink](https://github.com/d6o/dip.ink) deployment.

- `notes/` — inbox: agents drop timestamped note folders here (via `wiki_note_drop`). FLAGged or already-ingested folders move to `notes/.blocked/`.
- `wiki/` — the curated wiki the hourly curator maintains (markdown pages + source archive under `wiki/sources/notes/`).
- `raw/` — immutable source documents for manual ingestion.
- `AGENTS.md` — the schema + curation contract every maintaining agent follows (Pi / AGENTS.md convention).
- `scripts/` — linter, index generator, log rotation, weekly distill, curator supervisor.
- `.github/workflows/` — the curator (hourly), synthesis (weekly), review-queue (daily) agents. All three share concurrency group `memory-repo-writer` and run `ghcr.io/d6o/dip.ink/pi-runner:v0.1.0`.

A fresh copy includes a small bootstrap source note so a new deployment has
something for Graphiti to ingest and for the default healthcheck `ANSWER_PROBE`
to ground against.

Setup checklist (see the dip.ink README for the full walkthrough):

1. Push this repo as **private**.
2. Add the `PI_API_KEY` Actions secret (LLM key for the curator).
3. Optionally set Actions variables `PI_PROVIDER`, `PI_MODEL`, and/or
   `PI_MODELS_JSON` for non-default / custom providers.
4. Actions → General → Workflow permissions → "Read and write permissions".
5. Point your dip.ink stack's `WIKI_REPO_URL` at this repo.

There is **no automatic secret scanning** in these workflows. Agents must never
commit credentials, tokens, or passwords into notes; reference secret-manager
paths only. The private repo and gated network are the security perimeter.
