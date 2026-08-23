# Development Environment

One reproducible compose stack rather than independently installed services, so
the environment matches the registry / workflow / audit architecture instead of
drifting per machine.

```
Windows
├── Docker Desktop / WSL 2
│   ├── postgres    durable operational data (registries, workflows, audit)
│   ├── redis       session / cache tier
│   ├── n8n         workflow engine, official image
│   └── paios       the control plane package
└── VS Code + Git
```

## Prerequisites

Install in this order, then reboot if Docker Desktop asks:

| # | Component | Notes |
|---|---|---|
| 1 | Docker Desktop | Enable **WSL 2 integration** during setup. |
| 2 | Python | **3.12 or 3.13** — see the version note below. |
| 3 | Node.js | Provides `node` and `npm`. |
| 4 | VS Code | Install the Claude Code extension after the CLI. |
| 5 | Git | System install, for local development. |
| 6 | Azure CLI | Not yet needed. Install at the tenant/deployment phase. |

### Python version

`pyproject.toml` declares `requires-python = ">=3.12"`. **Prefer 3.12 or
3.13 over 3.14.**

PAIOS itself has *zero runtime dependencies* — the governance core is pure
Python and will run on 3.14 without complaint. The risk is downstream: the
database and Azure libraries this stack will need (`psycopg`, `asyncpg`,
`azure-identity`, `openai`) ship compiled wheels that lag new interpreter
releases, and a missing wheel means falling back to building from source with a
C toolchain on Windows. That is an avoidable afternoon.

The container image pins `python:3.12-slim` for exactly this reason, so the
compose stack is unaffected by whichever interpreter is installed on the host.

### Do not install

- **n8n from the source archive.** Use the official image, which the compose
  file already pins. Building n8n from source is not a supported deployment
  path.
- **Duplicate `PAIOS-main*.zip` / `files*.zip` archives.** Inspect before
  extracting — a second project root that looks like this repository but is not
  tracked by Git is the most confusing possible thing to have on disk. The
  authoritative source is the Git clone.

## First run

```powershell
git clone https://github.com/CarltonBurney/PAIOS
cd PAIOS
copy .env.example .env
```

Then fill in the two required secrets in `.env`:

```
POSTGRES_PASSWORD=<choose one>
N8N_ENCRYPTION_KEY=<choose one>
```

Both are **required and deliberately have no default** — `docker compose up`
fails immediately with a named message rather than silently starting with a
guessable password. `N8N_ENCRYPTION_KEY` encrypts stored n8n credentials; if it
changes, previously saved credentials become unreadable, so set it once and keep
it. `.env` is gitignored and must stay that way.

```powershell
docker compose up -d
docker compose ps
```

n8n is then at <http://localhost:5678>. Postgres, Redis, and n8n bind to
`127.0.0.1` only, so nothing is exposed to the local network.

## Verify

Host tooling:

```powershell
docker --version
docker compose version
python --version
node --version
npm --version
git --version
```

The stack itself:

```powershell
# All four services up, postgres and redis healthy
docker compose ps

# Both databases exist
docker compose exec postgres psql -U paios -c "\l"

# Redis responds
docker compose exec redis redis-cli ping

# The control plane suite passes inside the container
docker compose run --rm paios pytest -q
```

## Working in the stack

```powershell
# Run the tests
docker compose run --rm paios pytest -q

# Lint
docker compose run --rm paios ruff check .

# A shell in the PAIOS container
docker compose exec paios bash

# Follow logs
docker compose logs -f n8n

# Stop, keeping data
docker compose down

# Stop and destroy all data, including n8n workflows
docker compose down -v
```

`src/`, `tests/`, and `policies/` are bind-mounted, so edits on the host take
effect without a rebuild. Rebuild only when `pyproject.toml` changes:

```powershell
docker compose build paios
```

## What this stack does not yet include

- **No PAIOS API service.** PAIOS is currently a library with no HTTP surface,
  so the `paios` container runs `sleep infinity` and exists to be exec'd into
  and to run the suite. When a FastAPI service lands, it gets a `command` and a
  port here.
- **No database schema.** Postgres runs and holds n8n's data, but PAIOS itself
  still keeps registries and audit records in memory. Persisting them is a
  separate slice.
- **No Azure connection.** The default provider is the deterministic mock, so
  the whole stack runs with no cloud account. Set `PAIOS_PROVIDER=foundry` plus
  the Foundry endpoint and deployment in `.env` once the tenant is ready — and
  note that `DefaultAzureCredential` inside a container will not pick up a host
  `az login` session without extra wiring.

## Troubleshooting

**`required variable POSTGRES_PASSWORD is missing a value`** — `.env` is absent
or the value is blank. This is the fail-fast guard working.

**n8n restarts repeatedly on first boot** — it is waiting for its database. The
init script creates the `n8n` database only when the Postgres data volume is
first created. If the volume predates this file, create it once by hand:

```powershell
docker compose exec postgres psql -U paios -c "CREATE DATABASE n8n;"
docker compose restart n8n
```

**Port already in use** — override `POSTGRES_PORT`, `REDIS_PORT`, or `N8N_PORT`
in `.env`.

**Docker Desktop cannot start** — confirm WSL 2 is enabled and the distribution
is running (`wsl -l -v`).
