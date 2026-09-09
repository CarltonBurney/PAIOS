# Command Center API

Status API and host for the PAIOS Command Center dashboard. It serves the dashboard
and the four endpoints that dashboard calls from one origin on `127.0.0.1:8000`.

The app is dependency-free at runtime — collectors use `urllib`, the server uses
`http.server` — so it runs from a checkout with nothing installed.

## Run it

```bash
cp projects.example.json projects.json   # then edit it
python3 -m paios_command_center --poll-on-start
```

Open http://127.0.0.1:8000.

Useful flags: `--projects`, `--state`, `--static`, `--host`, `--port`. It binds to
loopback by default and has no authentication, so do not publish it on `0.0.0.0`.

Set `GITHUB_TOKEN` before starting to raise the GitHub rate limit from 60 requests
an hour to 5,000, and to read private repositories.

## Endpoints

| Endpoint | Method | Returns |
|---|---|---|
| `/api/status` | GET | The assembled status document. Reads stored state only — no network, so the dashboard's 20-second refresh is free. |
| `/api/poll` | POST | Refreshes every project from its sources, then returns `{status, errors}`. |
| `/api/log` | GET | Poll history, newest first, capped at 50 entries. |
| `/api/manual/{id}` | POST | Saves the edit-modal fields for one project. |
| `/health` | GET | `{"status": "ok"}`. |

## Where the numbers come from

**GitHub** is polled per repository for open issues and last push time. GitHub's
`open_issues_count` counts pull requests as issues, so open PRs are counted and
subtracted — a board that reported every open PR as backlog would overstate it.

**Perplexity** publishes no API for listing sessions inside a project, so there is
nothing honest to poll. The collector reports unavailable, the card falls back to
the last recorded snapshot, and the dashboard says so rather than showing a
fabricated zero. `collectors.collect_perplexity` is where a real source would go.

**Manual fields** — status, phase, next action, percent complete, notes — are typed
by a person in the edit modal and are never overwritten by a collector.

State lives in `data/state.json` (git-ignored): manual edits, the last good snapshot
per source, and the poll log. Writes are atomic, so an interrupted save cannot
truncate it.

## Registry

`projects.json` decides which cards exist and in what order. Each entry sets the
name, emoji, category, accent `color` (one of `primary`, `gold`, `blue`, `purple`,
`success`, `warning`, `neutral`), the Perplexity project URL, and the `repos` to
poll in `owner/name` form. An invalid entry stops startup rather than rendering an
empty dashboard that looks like a working one.

## Tests

```bash
python3 -m pytest        # from this directory
ruff check .
```

## Relationship to `apps/paios-command-center`

That app is the .NET 8 shell scaffold serving `/health` and `/api/workspaces` on
port 8080. This one is a separate Python service with its own dashboard and its own
endpoints; the two do not talk to each other. How the shell and the governance
control plane should ultimately connect is still an open decision — see
`docs/REPOSITORY_STATUS.md` if it has landed.
