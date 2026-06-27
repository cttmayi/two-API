# Config Configuration Wizard

Web-based configuration page built into two-API dashboard for editing all settings via a structured form, with save + hot-reload.

## Architecture

```
Browser ──GET /──→ FastAPI dashboard HTML
         ──GET /settings──→ Settings page (HTML + JS)
                │
                ├── GET  /api/config  → JSON (current config)
                └── POST /api/config  → JSON (new config)
                        │
                        ├── Validate with Pydantic
                        ├── Write YAML to ~/.two-api/config.yaml
                        └── Hot-reload:
                            ├── app.state.config = new_config
                            ├── app.state.router = ModelRouter(...)
                            └── init_cache(CacheConfig(...))
```

## API Layer

### GET /api/config

Returns the current config serialized as JSON. `api_key` values are masked (e.g., `sk-****`) to avoid exposing secrets in the browser.

### POST /api/config

Receives config JSON, validates with Pydantic `Config` model. On validation failure, returns `422` with error details. On success:
1. Serialize to YAML and write to `~/.two-api/config.yaml`
2. Update `app.state.config`
3. Rebuild router: `app.state.router = ModelRouter(new_config.models)`
4. Reinitialize cache: `init_cache(CacheConfig(...))` (clears old cache)
5. Logging config unchanged (no hot-reload for logging)
6. Return `{ "status": "ok" }`

API key handling:
- `GET /api/config` returns masked keys (`sk-****`, first 3 chars preserved)
- `POST /api/config`: if key is the masked value, preserve the original key from current config; if key is a new value, use it

## Frontend

### Navigation

Both `/` and `/settings` pages share a nav bar with "Dashboard" and "Settings" links. Clicking navigates via standard `<a>` links (full page load).

### Form Sections (all on one scrollable page)

**Server:**
- host: text input
- port: number input

**Models** (dynamic list, can add/remove entries):
Each model entry:
- `names`: editable list of strings + alias mappings (tag-like input, add/remove)
- `openai_base_url`: text input
- `anthropic_base_url`: text input
- `api_key`: text input (password maskable)
- `max_tokens`: number input, optional
- `responses_to_chat`: toggle switch

Add model button appends a new empty row. Remove button deletes a row.

**Alias** (key-value pairs, dynamic list):
- Each row: key input + value input
- Add/remove buttons

**Logging:**
- level: dropdown (DEBUG, INFO, WARNING, ERROR)
- output: dropdown (file, console)
- dir: text input

**Cache:**
- enabled: toggle switch
- ttl_seconds: number input
- max_entries: number input
- aliases: tag-like input list
- key_fields: tag-like input list

### Actions

- **Save**: collects all form data, POSTs to `/api/config`, shows success/error toast. On success, optionally redirects to dashboard.
- **Cancel**: resets form to last saved state (re-fetches GET /api/config).

### Validation (client-side, pre-submit)

- Port must be 1-65535
- At least one model entry required
- Each model entry must have at least one name and one base URL
- Alias keys cannot be empty
- Logging level must be valid

## Hot-Reload Behavior

- Router and cache are updated immediately for subsequent requests
- In-flight requests continue using old config (no disruption)
- Cache is cleared on config change (TTL/alias rules may have changed)
- Logging config changes require a restart (not hot-reloadable)

## Files Changed

| File | Change |
|------|--------|
| `src/main.py` | Add `/api/config` GET/POST endpoints, `/settings` page, navigation tabs on both pages |
| `pyproject.toml` | Add `pyyaml` if not already present (for YAML serialization in POST handler) |

Note: `src/config.py` already has `Config.model_dump()` for serialization; PyYAML is already a dependency for loading, `yaml.dump()` is available for writing.

## Testing

- `GET /api/config` returns valid JSON matching current config structure
- `POST /api/config` with valid data writes file and updates app state
- `POST /api/config` with invalid data returns 422
- API key masking round-trips correctly (masked key in POST preserves original)
- Navigation tabs appear on both pages
- Model add/remove rows work on client side
- Cache hot-reload clears old entries
