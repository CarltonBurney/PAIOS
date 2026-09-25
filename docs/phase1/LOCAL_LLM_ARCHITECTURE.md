# LOCAL_LLM_ARCHITECTURE.md — as implemented

**Specification ownership sits outside this repository.** The canonical design
lives in Drive (`04_SYSTEM_ARCHITECTURE/LOCAL_LLM_ARCHITECTURE.md`). This file
records what was actually built against that contract, and where repository
reality forced a departure. Where the two disagree, this file describes the code
and the canonical spec describes the intent.

## Normalized schema — implemented as specified

`Providers/ProviderModels.cs`

| Spec type | Implemented as | Notes |
|---|---|---|
| `ProviderType` | `enum ProviderType` | wire values `ollama`, `openai_compatible`, `other` |
| `HealthState` | `enum HealthState` | wire values `healthy`, `degraded`, `unavailable`, `unknown` |
| `ModelProvider` | `record ModelProvider` | plus `autoDetected` (see departures) |
| `ModelRecord` | `record ModelRecord` | all spec fields present |

Wire values are asserted by `SerializationTests`, because the workspace CSS keys
class names off these exact strings.

Two additions beyond the spec:

- **`ModelProvider.AutoDetected`** — distinguishes a provider discovered at
  startup from one a user registered. Needed because auto-detected providers are
  not persisted and therefore cannot be deleted; without the flag the UI could
  not explain why `DELETE` refuses.
- **`ModelListResult`** — carries the health of the *listing attempt*. The spec
  returns `ModelRecord[]`, which collapses "healthy provider, zero models" and
  "could not read models" into the same empty array. The spec requires those two
  be distinguishable, so the result type carries status and error alongside the
  records.

## Adapter interface — implemented as specified

`Providers/IModelProviderAdapter.cs`. Implementations never throw for network or
protocol failure; every failure is a `HealthState`.

### Ollama adapter (`Providers/OllamaAdapter.cs`)

- Health: `GET {endpoint}/api/tags`. 200 + parses → `healthy`; connection
  refused or timeout → `unavailable`; unexpected shape or non-2xx → `degraded`.
- Models: same endpoint, `models[]` → `ModelRecord[]`, carrying `size`.
- Default endpoint `http://localhost:11434`, overridable via config.
- Trailing slashes normalized (asserted by test).

### OpenAI-compatible adapter (`Providers/OpenAiCompatibleAdapter.cs`)

- Health: `GET {endpoint}/v1/models`. Same state mapping.
- Models: `data[]` → `ModelRecord[]`.
- Endpoints accepted with or without the `/v1` suffix — a URL copied from LM
  Studio works unmodified (four cases asserted by test).
- **No `Authorization` header is sent unless** the config names an env var *and*
  that variable resolves to a non-empty value. Asserted by three tests, because
  local servers commonly reject unexpected auth headers.

## Backend endpoints — implemented as specified

`Program.cs`

```
GET    /api/providers              -> ModelProvider[]   (health-checked on read)
GET    /api/providers/{id}/models  -> { providerId, status, errorMessage, models[] }
GET    /api/providers/{id}/health  -> ModelProvider     (forces a re-check)
POST   /api/providers              -> 201 + ModelProvider | 400 + { error }
DELETE /api/providers/{id}         -> 204 | 404 + { error }
```

Unknown provider id → `404`. A provider that cannot be read still returns `200`
with its status and error attached, so the workspace renders the failure instead
of breaking.

## Failure handling — implemented as specified

| Spec rule | Implementation |
|---|---|
| Failed provider still listed with status + error | `ProviderRegistry.GetProvidersAsync` never filters |
| Empty model list on healthy provider is valid | returns `healthy` + `[]`, `errorMessage` null |
| Malformed body → `degraded`, no uncaught throw | `TryParse*` returns the parse detail |
| Frontend renders all four states distinctly | four `.state-*` CSS classes |

Probes run under a 5-second `HttpClient` timeout, so a half-started server
surfaces as `unavailable` rather than hanging the request.

## UI consumption rule — implemented as specified

`wwwroot/index.html` calls `GET /api/providers` and
`GET /api/providers/{id}/models` only. It contains no hard-coded model names,
sample data, or provider lists. With nothing configured it renders an explicit
empty state naming how to add a provider. Model inventories are fetched in
parallel and one failing provider cannot prevent the others from rendering.

## Departures forced by repository reality

Recorded per the Phase 0 directive; these change the verb, not the design.

1. **"Add to existing API, do not create a parallel service layer."** There was
   no service layer — `Program.cs` was 20 lines of inline lambdas with no DI
   beyond `AddHealthChecks()`. The provider layer **establishes** the first one.
   Nothing parallel was created: the endpoints live in the same app, same host.
2. **"Pick whichever config source matches the existing repo convention."** No
   convention existed. `config/providers.json` establishes one, matching the
   spec's suggested shape exactly.
3. **First external dependency.** The project had zero `PackageReference`
   entries. It still does — `HttpClient` and `System.Text.Json` are in-framework.
   The only new package references are in the test project.

## Test matrix — all cases covered

The spec's minimum matrix, each verified twice: as a unit test, and live against
a real socket.

| Case | Unit test | Live verification |
|---|---|---|
| Ollama running, has models | ✅ | stub server |
| Ollama not running | ✅ | real absent service on :11434 |
| OpenAI-compat reachable | ✅ | real server on :1234 |
| OpenAI-compat unreachable | ✅ | connection refused |
| Malformed JSON → degraded | ✅ | real server on :1236 |
| Healthy, zero models | ✅ | real server on :1235 |

Suite: **45 tests, 0 failures** (`dotnet test Paios.sln`).
