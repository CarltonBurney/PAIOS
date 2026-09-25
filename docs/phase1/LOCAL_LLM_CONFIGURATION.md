# LOCAL_LLM_CONFIGURATION.md — as implemented

Canonical spec: Drive `04_SYSTEM_ARCHITECTURE/LOCAL_LLM_CONFIGURATION.md`. This
file records the configuration convention actually established.

## Config source

`apps/paios-command-center/config/providers.json`, resolved relative to the
host's content root and copied to build output. Phase 0 confirmed **no existing
convention to match** — no `appsettings.json`, no `.env`, no `config/` — so this
file establishes one, using the shape the spec suggested.

```json
{
  "autoDetect": {
    "ollama": {
      "enabled": true,
      "endpoint": "http://localhost:11434"
    }
  },
  "configured": []
}
```

A configured entry:

```json
{
  "providerId": "lmstudio-local",
  "providerType": "openai_compatible",
  "endpoint": "http://localhost:1234/v1",
  "apiKeyEnvVar": "LMSTUDIO_API_KEY"
}
```

## Rules — implemented as specified

| Rule | Implementation |
|---|---|
| Auto-detect Ollama on startup by default | `autoDetect.ollama.enabled` defaults true |
| Never fail startup if Ollama is absent | registered as `unavailable`; verified live |
| Store `apiKeyEnvVar`, never the key | asserted by `Api_key_is_never_written_to_the_config_file` |
| POST/DELETE persist to the config source | asserted by a reload through a second registry instance |
| Config changes need no redeploy | file is read per request; edits apply to the next call |

**Reload semantics:** the config file is read on each request rather than cached
at startup, so edits take effect immediately with no restart and no redeploy.
The cost is a small file read per API call, which is acceptable at this scale
and avoids a cache-invalidation path that nothing yet needs.

## Failure behaviour

- **Missing file** — the app starts on defaults and logs at Information. Verified
  by `Missing_config_file_does_not_fail_and_still_auto_detects_ollama`.
- **Malformed file** — the app starts on defaults and logs at Error rather than
  crashing. Verified by
  `Malformed_config_file_falls_back_to_defaults_instead_of_failing_startup`.
- **Unrecognized `providerType`** — logged as a warning, treated as `other`, and
  surfaced as `unknown` health. The provider is never silently dropped.

## Secrets

No key is ever written to `providers.json` — only the *name* of an environment
variable. The `Authorization` header is attached only when that variable is both
named and resolves non-empty. `.gitignore` already excludes `.env` and
`appsettings.Development.json`.

## Auto-detected vs configured

Auto-detected providers are **not** persisted, so a restart re-detects rather
than accumulating stale entries, and `DELETE` on one returns 404 with an
explanation pointing at the `enabled` flag. The reserved id `ollama-local`
cannot be claimed by a configured provider.
