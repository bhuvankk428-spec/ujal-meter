# SPEC: Urja Meter Ops API (Flock Energy take-home)

> A clean, documented, resilient REST service in front of the legacy "Urja Meter Ops" portal.
> Status: **plan**. Everything marked **[VERIFY]** is a hypothesis to confirm in DevTools, not a fact.

---

## 0. How to use this spec

- The take-home grades **problem solving, judgment, learning, communication** before code quality. The most valuable artifact is `PROTOCOL.md`, which must come from *your own* investigation. This spec tells you *what to look for* and *how to build around it*. It deliberately does not pretend to know the portal's internals.
- AI is allowed "for guidance, not to do the work". Use it to explain concepts and review code. Do the recon yourself, and be ready to explain every line in the follow-up call.
- Budget is **4-6 hours**. Section 12 gives time boxes and a **cut line**. A well-reasoned partial solution beats an exhausted complete one.

---

## 1. Goals and non-goals

### Goals
1. Programmatic, read-only access to meters, meter details, hierarchy, and consumption.
2. Hide the portal completely: no client ever touches it, sees HTML, or handles its auth.
3. Resilient: survives session expiry, slow responses, and portal errors without leaking them.
4. Documented to the point that a teammate can pick it up cold (README, PROTOCOL, OpenAPI).
5. Right-sized: production *habits* (typed models, timeouts, retries, tests, observability hooks) without production *sprawl* (no Kubernetes, no message bus).

### Non-goals (state these in the README as intentional omissions)
- Writing to the portal (read-only rule).
- Multi-tenant auth, per-user portal credentials, RBAC.
- Horizontal scaling of the session (single operator account, single process).
- Real-time streaming / webhooks.
- Attacking, fuzzing, or hammering the portal.

---

## 2. Stack decision

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | Fast to write, strong scraping ecosystem, matches your skills (FastAPI listed). |
| API framework | **FastAPI** | OpenAPI generated for free, Pydantic v2 validation, `/docs` built in. |
| HTTP client | **httpx.AsyncClient** | Async, cookie persistence, timeouts, `respx` for mocking. |
| HTML parser | **selectolax** (fallback: BeautifulSoup4 + lxml) | Fast; BS4 is fine if tables are small. Pick one, don't mix. |
| Models | **Pydantic v2** | One source of truth for validation + OpenAPI schema. |
| Local index (optional) | **SQLite** (stdlib `sqlite3` or SQLModel) | Zero infra, supports filters and bounding-box geo queries. |
| Cache | In-process **TTL + stale-while-revalidate + single-flight** (hand-rolled, ~80 lines) | Avoids a Redis dependency; see 8.3 on when to graduate. |
| Tests | **pytest + respx + pytest-asyncio** | Mock the portal with captured HTML fixtures. |
| Tooling | **ruff**, **mypy** (lenient), **pre-commit**, **uv** or pip | Cheap quality signal. |
| Packaging | **Dockerfile** + `docker compose` (optional) | "Clone and run" reproducibility. |
| CI | **GitHub Actions**: lint, type-check, test | Reviewers like green badges. |

If the portal turns out to expose a JSON API behind its frontend (see 3.3), the HTML parser becomes unnecessary. Don't install it until recon proves you need it.

---

## 3. Phase 1: Reconnaissance (your job, ~60-75 min)

This is the part being graded most. Treat it like an investigation and write findings *as you go* into `PROTOCOL.md`.

### 3.1 Setup
- Browser DevTools -> Network tab -> **Preserve log**, **Disable cache** on.
- Also try `curl -v` and `httpx` in a REPL so you can see what a non-browser client gets.
- Save representative raw responses to `tests/fixtures/` (HTML or JSON). They become your test data.

### 3.2 Authentication questions
| Question | Why it matters |
|---|---|
| Is login a `POST`? To which path? Form-encoded or JSON? | Adapter login method. |
| Field names (`email` vs `username`)? Hidden fields / CSRF token in the form? | The portal's login page shows an "Email" and "Password" form, so the field name probably differs from the generic `username` in the reference snippet. **[VERIFY]** |
| What does a *successful* login return: 302 + `Set-Cookie`, or 200 + token? | How to detect success. A 200 on a failed login (re-rendered form) is a classic legacy trap. |
| Cookie attributes: name, `HttpOnly`, `Secure`, expiry, sliding vs absolute? | Session lifetime policy. |
| What does an **expired / missing** session look like on a data page: 401, 302 to `/login`, or a 200 with the login form? | **Most important detection rule** for auto-reauth. |
| Does logout exist? Does logging in again invalidate the old session? | Whether concurrent logins are safe (see 6.2). |
| Rate limiting, lockout on bad passwords? | Don't retry login in a tight loop. |

### 3.3 Data-path questions
- Open the meter list, one meter detail, consumption view, and any hierarchy view. For each, watch for:
  - **XHR/fetch calls to JSON endpoints** (`/api/...`). If they exist, prefer them over scraping HTML.
  - **Server-rendered HTML tables** and the selectors that identify them (ids, classes, `data-*` attrs).
  - **Pagination** (page/limit/offset/cursor), **sorting**, **filters**, **search**.
  - **Export / bulk / CSV / download** links. The brief hints "there may be more than one way to get data out", so a bulk path likely exists. **[VERIFY]**
  - **Date and period parameters** for consumption (range, granularity, timezone).
  - **Hidden ids** that differ from visible ids (internal id vs meter serial vs display label).
- Look at **JS bundle source** and the **page source** for endpoint strings the UI never calls but exists.
- Check `robots.txt`, `/openapi.json`, `/docs`, `/sitemap.xml`: legacy apps sometimes leave these up. Reading publicly served files is normal; do not probe for hidden or admin paths beyond what a logged-in user's UI links to.

### 3.4 Anomaly hunt (the "messy data" the brief warns about)
Build a checklist and record every hit in `PROTOCOL.md`:

- Missing / empty fields, `N/A`, `-`, `null`, `""` used inconsistently.
- Mixed date formats (`2026-03-01`, `01/03/2026`, `1 Mar 26`), mixed timezones, DST gaps.
- Numbers with thousand separators, units embedded in strings (`124.50 kWh`), commas as decimals.
- Duplicate meters, orphan nodes in the hierarchy, cycles, a node with two parents.
- Status vocabularies that drift (`Active`/`ACTIVE`/`active`/`1`).
- Pagination quirks (off-by-one, last page repeated, total count wrong).
- Inconsistent IDs (case, leading zeros, whitespace).
- Gaps or negative values or spikes in consumption series.
- Same entity returning different data from the list view vs the detail view.

### 3.5 Recon deliverable
`PROTOCOL.md` with the structure in section 10. If it is not in `PROTOCOL.md`, it did not happen.

---

## 4. Architecture

Follow an **anti-corruption layer** pattern: the legacy system's shapes never leak into your public API.

```
 Client / Web UI
       |  JSON (clean, versioned, documented)
       v
+------------------------------------------------------+
|  API layer (FastAPI routers)           app/api/      |
|   validation, pagination, error mapping, OpenAPI      |
+------------------------------------------------------+
|  Service layer                         app/services/ |
|   orchestration, cache (TTL+SWR+single-flight),      |
|   hierarchy builder, local index                      |
+------------------------------------------------------+
|  Domain models (Pydantic)              app/models/   |
|   Meter, MeterDetail, ConsumptionPoint, HierarchyNode |
+------------------------------------------------------+
|  Portal adapter                        app/portal/   |
|   session manager, login, reauth, retries, breaker,  |
|   fetchers, parsers (HTML/JSON -> raw dict)           |
|   normalizers (raw dict -> domain model)              |
+------------------------------------------------------+
       |  HTTPS, cookies  (single shared session)
       v
  Urja Meter Ops portal (legacy, read-only)
```

Key separation: **fetch** (HTTP) -> **parse** (HTML/JSON to raw dict) -> **normalize** (raw dict to typed model). Each step is independently testable; parsers and normalizers are pure functions tested against saved fixtures with no network.

### 4.1 Repo layout

```
flock-energy-api/
├── app/
│   ├── main.py                # app factory, lifespan (open/close client), middleware
│   ├── config.py              # pydantic-settings, env-driven
│   ├── api/
│   │   ├── deps.py            # shared dependencies
│   │   ├── errors.py          # exception handlers -> problem+json
│   │   ├── meters.py
│   │   ├── hierarchy.py
│   │   └── health.py
│   ├── models/                # public Pydantic schemas
│   ├── services/
│   │   ├── meter_service.py
│   │   ├── hierarchy_service.py
│   │   └── cache.py           # TTL + SWR + single-flight
│   ├── portal/
│   │   ├── session.py         # login, reauth, locking
│   │   ├── client.py          # low-level GET with retry/breaker
│   │   ├── parsers.py         # HTML/JSON -> raw dicts
│   │   ├── normalize.py       # raw dicts -> models, cleaning rules
│   │   └── exceptions.py
│   └── index/                 # optional: SQLite local query layer
├── tests/
│   ├── fixtures/              # captured portal responses (scrub secrets)
│   ├── unit/                  # parsers, normalizers, cache
│   ├── integration/           # API with respx-mocked portal
│   └── live/                  # opt-in (-m live) smoke tests, read-only
├── scripts/export_openapi.py
├── web/                       # optional modern client
├── openapi.json
├── PROTOCOL.md
├── README.md
├── REFLECTION.md
├── Dockerfile
├── .env.example
├── pyproject.toml
└── .github/workflows/ci.yml
```

---

## 5. Public API design

Base path `/api/v1`. JSON only. Snake_case fields. ISO-8601 UTC timestamps with explicit offset. Numbers are real numbers with explicit unit fields.

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/meters` | List meters | `limit`, `cursor` or `page`, `status`, `q`; returns `{items, next_cursor, total?}` |
| GET | `/meters/{id}` | Meter detail | 404 if unknown; includes hierarchy path |
| GET | `/meters/{id}/consumption` | Consumption series | `from`, `to`, `granularity` (if portal supports it); returns points + unit + summary |
| GET | `/hierarchy` | Network tree | `?root=`, `?depth=`; flags data-quality issues |
| GET | `/health` | Liveness | Process is up |
| GET | `/ready` | Readiness | Portal reachable + session valid |
| POST | `/api/v1/auth/login` | **Skip.** Session is managed internally | Document this decision (see 6.1) |

Extras if time allows:
- `GET /meters/search?lat=&lon=&radius_km=` (needs coordinates in portal data **[VERIFY]**)
- `GET /meters/{id}/anomalies`, `GET /stats/summary`
- `POST /admin/cache/refresh` (protected by an API key)

### 5.1 Conventions
- **Errors:** RFC 9457 `application/problem+json` (`type`, `title`, `status`, `detail`, `instance`) so clients see one consistent shape.
- **Status mapping:**
  - 404: unknown meter
  - 422: bad query params (FastAPI default)
  - 502: portal returned garbage or unexpected HTML (parser failure)
  - 503 + `Retry-After`: portal down or circuit open
  - 504: portal timeout
- **Staleness is explicit:** responses served from cache carry `X-Data-Age` (seconds) and optionally `X-Cache: HIT|STALE|MISS`. Consumers can decide whether it is fresh enough.
- **Versioning:** URL prefix `/v1`. Never change field meaning within a version.
- **Pagination:** one style across the API. Cursor if the portal supports it, otherwise page/limit with a documented ceiling.
- **Data quality is surfaced, not hidden:** where cleaning is lossy (e.g. a date had to be guessed), include a `quality_flags: ["date_ambiguous"]` field rather than silently "fixing" it.

---

## 6. Portal adapter design (the core)

### 6.1 Session management
- **One shared `httpx.AsyncClient`** created in FastAPI `lifespan`, closed on shutdown. Connection pooling plus cookie jar come for free.
- **Login lazily and on demand.** Do *not* expose login in the public API; the operator credential is a server-side secret. Document this as a trade-off (simpler, safer for a single-tenant wrapper; not suitable for per-user portal identities).
- Credentials come from env (`URJA_BASE_URL`, `URJA_USERNAME`, `URJA_PASSWORD`) via `pydantic-settings`; `.env.example` committed, `.env` git-ignored. Wrap the password in `SecretStr` and never log it. The credentials in the brief are a shared test login; still keep them out of the repo's committed files and git history.

### 6.2 Auto re-authentication (the interesting bit)
Same pattern production SDKs use: a typical session-based SDK does automatic 401 re-authentication with internal locking to prevent license exhaustion. For this portal:

1. Wrap every portal call in `request()`.
2. Detect "session lost" using the rule you found in 3.2 (401 / redirect to `/login` / login form in a 200 body). Encapsulate it in one function `is_session_expired(response)`.
3. On expiry: acquire an `asyncio.Lock`, **re-check** whether another task already re-logged in (compare a session generation counter), log in once, release, retry the original request **once**.
4. Never loop: one reauth per request, then surface `PortalAuthError`.
5. If login itself fails (bad creds / lockout), do not retry blindly; trip the breaker and return 503 with a clear message.

Why the lock matters: without it, ten concurrent requests hitting an expired session each trigger a login, which is wasteful and, on some legacy apps, invalidates the previous session and causes a thundering herd.

### 6.3 Resilience
Use the four standard proxy patterns together (SWR cache, request coalescing, circuit breaker, retry) because no single mechanism covers latency, load, and failure at once:

| Pattern | Behaviour here |
|---|---|
| **Timeouts** | Explicit connect/read/total (e.g. 3s / 10s / 15s). Never rely on defaults. |
| **Retry with backoff + jitter** | Only for idempotent GETs, on connect errors / 502 / 503 / 504, max 2-3 attempts. Never retry on 4xx. |
| **Circuit breaker** | After N consecutive failures, open for ~15-30s and fail fast with 503; allow one probe request, close on success. |
| **Single-flight / coalescing** | Concurrent requests for the same key share one upstream call. |
| **Concurrency limit** | `asyncio.Semaphore` (e.g. 4-5) to avoid overloading an ageing portal. This is a courtesy to the portal, not a nicety. |
| **Graceful degradation** | If the portal is down and a stale cache entry exists, serve it with `X-Cache: STALE`. |

### 6.4 Parsing and normalization rules
- Parsers return **plain dicts** (string values, untouched). Normalizers convert and validate. Never mix the two.
- Select by **stable anchors** (ids, `data-*`, header text) rather than position (`td[3]`). Match table columns by **header name**, not index, so a reordered column does not silently corrupt data.
- **Fail loudly on structural change:** if an expected table or header is missing, raise `ParseError` (-> 502 with a clear detail) instead of returning empty lists. Silent empties are worse than errors.
- Normalization rules go in one documented table (and a unit test per row):
  - whitespace/NBSP collapse; trim
  - `"124.50"`, `"1,24,500"`, `"124.50 kWh"` -> `float` + `unit`
  - date formats -> ISO-8601 UTC, with a configured portal timezone (**assumption to document**: likely Asia/Kolkata)
  - status vocabulary -> enum (`ACTIVE`, `INACTIVE`, `FAULT`, `UNKNOWN`) + raw value kept in `status_raw`
  - ids -> canonical form (strip, case rule), keep `portal_id` if it differs
  - empty-ish (`""`, `"-"`, `"N/A"`) -> `null`
- Keep a `source` block on responses where useful (e.g. `fetched_at`) for traceability.

### 6.5 Bulk path
If recon finds a CSV/export/bulk endpoint, prefer it for the **list + index build** (one request instead of N detail pages), and use per-meter pages only for fields the bulk file lacks. Document the reasoning in `PROTOCOL.md`. Be polite: fetch once at startup / on refresh, not per API call.

---

## 7. Hierarchy (optional extension, high signal)

- Build the tree from parent references (or path strings) discovered in recon.
- Handle messy data deliberately and report it:
  - **orphans** (parent id not found) -> attach under a synthetic `__unassigned__` root
  - **cycles** -> detect with visited-set, break at the back-edge, flag
  - **multiple parents / duplicates** -> pick a deterministic rule, flag the rest
  - **inconsistent level naming** -> normalize, keep raw
- Response: nested `children` for small trees plus a flat `nodes` array with `parent_id` for large ones; include `data_quality: {orphans: n, cycles: n, duplicates: n}`.
- Test with a hand-built fixture containing one of each defect.

---

## 8. Optional extensions (pick by value per hour)

### 8.1 Local index / query layer
- At startup (and on refresh), ingest the full meter list into **SQLite**, with indexes on status, hierarchy parent, and (if coordinates exist) lat/lon.
- Enables: multi-attribute filtering the portal cannot do, and "meters near this point" via **bounding-box prefilter + haversine** (no PostGIS needed).
- **Scale note to write in the README:** this is fine to roughly 10^5 meters in one process. Beyond that: move to Postgres (+ PostGIS or H3 cells for proximity), incremental sync instead of full reloads, and a background worker.

### 8.2 Freshness / caching policy
Give each data class a deliberate TTL and say *why*:

| Data | Nature | Suggested policy |
|---|---|---|
| Meter list / hierarchy | Changes rarely | fresh 10-15 min, stale-OK up to 1 h |
| Meter details | Slow-changing | fresh ~5 min |
| Consumption (past, closed periods) | Immutable-ish | long TTL (hours) |
| Consumption (current period) | Still accruing | short TTL (1-2 min) |

Mechanics: **two lifespans per entry**: a fresh window served as-is, and a longer stale window served while a background refresh runs. Add **TTL jitter** to avoid synchronized expiry, and a manual refresh endpoint. State clearly how you decide "stale": time-based only, because the portal gives no change signal (check for `ETag` / `Last-Modified` in recon; if present, use conditional GETs).

### 8.3 When to use Redis instead
In-process cache is correct for a single instance. If you run >1 replica, cache coherence and a shared session become real problems; move cache to Redis and put the portal session behind a single "gateway" worker. Mention this, don't build it.

### 8.4 Modern web client (best "product sense" lever)
Small **Vite + React + TypeScript + Tailwind** app (you already know this stack) calling only your API:
- **Meters table** (search, status filter, sort) with virtualization if large
- **Meter page**: consumption line/area chart (Recharts), daily/monthly toggle, min/max/avg, spike markers
- **Hierarchy explorer**: collapsible tree with counts and data-quality badges
- **Map** (Leaflet + OpenStreetMap tiles) *only if coordinates exist*
- **Health/anomaly panel**: meters with zero consumption, spikes, stale readings, status = fault
- Keep it ~1 evening. Deploy static build on Vercel pointing at a locally run API, or just document `npm run dev`. Do not let this eat core time (see cut line).

### 8.5 Observability (cheap, impressive)
- Structured JSON logs (`structlog` or stdlib), request-id middleware, log portal latency and cache outcome per request.
- `/metrics` (Prometheus) is optional; a `/ready` that reports `{portal: ok, session_age_s, breaker: closed, cache_entries}` is enough.

---

## 9. Testing strategy

| Layer | What | How |
|---|---|---|
| Unit | parsers, normalizers, cache (TTL/SWR/single-flight), hierarchy builder | Pure functions + saved fixtures; table-driven cases for messy values |
| Adapter | login, expiry detection, **reauth-once-then-retry**, breaker, retry | `respx` scripted sequences (e.g. 200, then expired-session, then login, then 200) |
| Concurrency | 10 parallel requests on expired session -> exactly **one** login | `asyncio.gather` + call counter |
| API | contract tests per endpoint, error shapes (problem+json) | FastAPI `TestClient` + mocked portal |
| Schema | exported `openapi.json` equals generated one | CI step: regenerate and `git diff --exit-code` |
| Live (opt-in) | read-only smoke: login, list 1 page, fetch 1 meter | `pytest -m live`, never in CI |

Priority per the brief: tests rank *below* judgment and communication. Aim for a **small, meaningful** suite (reauth, parsing quirks, hierarchy defects) rather than coverage theatre. Scrub any cookies or tokens from fixtures before committing.

---

## 10. Documentation deliverables

### 10.1 `PROTOCOL.md` (most valuable)
1. **Overview**: what the portal is, what data exists
2. **Auth flow**: login request/response, fields, cookies, expiry behaviour (with a sequence diagram)
3. **Endpoint catalogue**: table of URL, method, params, response type, notes
4. **Data model**: fields per entity, types, units, id relationships
5. **Pagination / filtering / sorting** behaviour
6. **Bulk/export paths** (if any) and when to prefer them
7. **Quirks and anomalies**: every item from 3.4 with a real example
8. **Rate/limits observed**, performance (typical latency)
9. **Open questions**: things you could not determine; be honest

### 10.2 `README.md`
Zero-context reader test. Include: what it is, architecture diagram, **quick start** (3-5 commands), env vars, sample `curl` requests *with real sample responses*, link to `/docs`, `openapi.json`, `PROTOCOL.md`, REFLECTION; **Assumptions**, **Design decisions & trade-offs**, **Intentionally skipped**, **With more time**; test instructions.

### 10.3 `openapi.json`
Generated via `scripts/export_openapi.py` (`json.dump(app.openapi())`), committed, and CI-verified. Add `summary`, `description`, `examples`, and response models for the error shape so Swagger/Scalar renders well. Serve `/docs`; optionally Scalar at `/reference`.

### 10.4 `REFLECTION.md` (answer all five, a paragraph each)
Assumptions, hardest part and how you got unstuck, what you'd do with another day, **your real mistake**, and how you'd criticise your own submission. Keep a running `notes.md` during the build and log mistakes as they happen; they are your best material and far more credible than something invented at the end.

---

## 11. Production-readiness checklist

**Security**
- [ ] No secrets in repo or git history; `.env.example` only
- [ ] `SecretStr` for passwords; redact cookies/Authorization in logs
- [ ] TLS verification on (never `verify=False`)
- [ ] If any admin/refresh endpoint exists, protect with an API key header
- [ ] CORS locked to known origins (needed if the web client is separate)
- [ ] Docker image runs as non-root; pinned dependency versions

**Reliability**
- [ ] Explicit timeouts everywhere
- [ ] Bounded retries with jitter; no retry storms
- [ ] Circuit breaker + concurrency limit toward the portal
- [ ] Single-flight on cache misses and reauth
- [ ] Graceful shutdown closes the HTTP client
- [ ] Parser raises on structural change; no silent empties

**Operability**
- [ ] `/health` and `/ready`
- [ ] Structured logs with request id, portal latency, cache result
- [ ] Config entirely via env; sane defaults
- [ ] One-command run (`docker compose up` or `uvicorn`)

**Quality**
- [ ] ruff + mypy clean, pre-commit hooks
- [ ] CI green (lint, type, tests, OpenAPI drift check)
- [ ] Conventional commits, small meaningful history (reviewers read it)
- [ ] README tested by following it on a clean clone

**Courtesy to the portal**
- [ ] Read-only GETs (plus the login POST) only
- [ ] Low concurrency, caching, no scraping loops on every request
- [ ] No probing beyond what a normal logged-in user's UI exposes

---

## 12. Execution plan and cut line (target 5-6 h)

| # | Block | Time | Output | Required? |
|---|---|---|---|---|
| 1 | Recon + `PROTOCOL.md` draft + fixtures | 75 min | Understanding, fixtures | **Yes** |
| 2 | Scaffold repo, config, Docker, CI skeleton | 25 min | Runnable empty app | **Yes** |
| 3 | Portal adapter: session, login, reauth, parsers, normalizers | 75 min | Fixture-tested adapter | **Yes** |
| 4 | API endpoints + models + error handling | 45 min | 4 core endpoints | **Yes** |
| 5 | Cache (SWR + single-flight) + breaker/limits | 35 min | Resilient adapter | High value |
| 6 | Tests (reauth, parsers, concurrency, API) | 40 min | Green CI | **Yes** (small) |
| 7 | OpenAPI export + `/docs`, README, REFLECTION | 45 min | Docs complete | **Yes** |
| | **Cut line: stop adding features here** | ~5.5 h | | |
| 8 | Hierarchy endpoint | 30-45 min | Tree + quality flags | Best extension |
| 9 | Local index / geo query | 45 min | SQLite layer | Optional |
| 10 | Web client | 2-3 h | Charts/map/tree | Only if ahead |

**If over budget:** stop at the cut line, and write a precise "what I'd do next" section. The brief says that earns real credit.

**Order of operations tip:** write README and PROTOCOL *incrementally* during blocks 1-4. Writing them last under fatigue is the most common reason submissions read poorly.

---

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Portal's expiry signal is subtle (200 + login HTML) | Centralize `is_session_expired`; unit test with a captured expired-session fixture |
| HTML structure drifts or is inconsistent between pages | Header-based column matching; strict `ParseError`; fixtures per page type |
| Slow / flaky portal | Timeouts, retries, breaker, SWR cache, concurrency cap |
| Consumption timezone/DST ambiguity | Assume and document a timezone; emit UTC + `quality_flags` |
| Over-engineering | Use the cut line; every extra feature must have a README sentence justifying it |
| Accidentally violating the read-only rule | Adapter exposes only `get()` plus `login()`; no generic `post()` |
| AI-authored code you cannot explain | Write the adapter and parsers yourself; use AI for review and explanations; keep `notes.md` of what you learned |
| Credentials leaking into git | `.gitignore`, `.env.example`, pre-commit secret scan (e.g. gitleaks) |

---

## 14. Definition of done

- [ ] `git clone` -> follow README -> API runs -> sample `curl` works against the live portal
- [ ] All four core endpoints return clean, typed JSON with documented errors
- [ ] Expired session recovers transparently (proven by a test)
- [ ] `PROTOCOL.md` explains auth, endpoints, data model, and at least 5 concrete quirks
- [ ] `openapi.json` matches the running app (CI-verified) and `/docs` renders
- [ ] README covers assumptions, trade-offs, omissions, improvements
- [ ] `REFLECTION.md` answers all five questions honestly, including a real mistake
- [ ] You can explain, unaided, why every major design choice was made

---

## 15. Reference patterns (what this plan borrows from)

- **Session-managed SDKs for legacy systems:** async client, automatic re-auth on 401 with internal locking (e.g. SAP Business One Service Layer SDK `b1sl-python` on PyPI).
- **Resilient API proxy patterns:** SWR cache, request coalescing, circuit breaker, retry (e.g. "4 Patterns for a Resilient API Proxy", agiledrop.com).
- **Python cache libraries with TTL + SWR + single-flight:** `kmcache`, `yokedcache`, `functools2` on PyPI. Study the semantics; you need ~80 lines, not a dependency.
- **Browser-to-httpx session handoff / why session reuse beats re-automating login:** invisible_playwright wiki. Relevant only if the portal ever needs a real browser (unlikely; avoid).
- **Anti-corruption layer** (Domain-Driven Design) and the **strangler/facade** approach for putting an API in front of a legacy system.
- **RFC 9457** (problem details for HTTP APIs) for the error format.