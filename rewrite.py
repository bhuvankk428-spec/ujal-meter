import re
from pathlib import Path

p = Path("DEMO_SCRIPT.txt")
text = p.read_text(encoding="utf-8")

new = {
"0:00-0:24": '''"Hi, I'm [Your Name]. This is my Urja Meter Ops API: a read-only REST API
and console in front of a legacy meter portal. Clients get typed JSON and never
see HTML, cookies, credentials, or messy data. I'll show the product, contract,
resilience, and trade-offs."''',
"0:24-0:52": '''"One constraint: the real portal URL and credentials were not available here.
I did not fake a successful integration. Instead I built a deterministic mock
portal with CSRF form login, cookie sessions, three expiry signals, a bulk CSV
export, server-rendered tables, and a JSON consumption endpoint. PROTOCOL.md
separates observed mock behavior from what still needs verification. Three
environment variables point the adapter at the real system."''',
"0:52-1:22": '''"Under the hood, this is an anti-corruption layer. FastAPI handles validation,
pagination, and problem+json errors. The service layer owns the bulk-first
snapshot, hierarchy builder, and stale-while-revalidate cache. The portal layer
owns one shared session, expiry detection, retries, a concurrency limit, and a
circuit breaker. Parsers return untouched strings. Normalizers are the only path
to a public model, and anything lossy becomes a quality flag."''',
"1:22-1:57": '''"Here is the console, talking only to the public API. The mock has twenty-four
unique meters: seventeen active, two faults, and fourteen flagged. Those flags
are deliberate: one duplicate id, one missing serial, three missing coordinates,
two unknown statuses. The goal is to surface the portal's condition, not hide it.
I can search or filter by status and zone. The cache chip shows HIT, MISS, or
STALE with the age, so consumers can judge freshness."''',
"1:57-2:32": '''"I'll open Sarjapur Road. The drawer combines the bulk record with detail-only
fields and a hierarchy path. The chart is the key quality view: this meter has
missing consumption points, shown as gaps, not zeros. The summary counts them.
The API returns the same data in typed JSON with source and quality flags.
Ambiguous dates or numbers would be flagged too."''',
"2:32-2:57": '''"The hierarchy tab shows the tree and the defects found while building it. This
dataset has an orphan, a cycle, a duplicate node, and a node with two parents.
Orphans attach under Unassigned, cycles are broken deterministically, the first
parent wins, and every decision is counted. The nested tree is for humans; the
flat node list is for clients."''',
"2:57-3:22": '''"Health separates liveness from readiness. Liveness never calls the portal.
Readiness checks the portal, session, breaker, and cache. Here everything is
ready and the breaker is closed. Cache refresh requires an API key, and the
server-side portal credentials never reach the browser."''',
"3:22-3:47": '''"FastAPI generates the contract at /docs, and the committed openapi.json is
checked in CI. I documented every endpoint, the admin API-key scheme, and the
problem+json errors. A missing meter returns 404 with a request id; parser
failures are 502, timeouts 504, portal failures 503."''',
"3:47-4:27": '''"Now session recovery. First, the error contract: an unknown meter returns
problem+json, not HTML. Next, I'll expire the mock session and request MTR-0009,
which is not detail-cached. The API detects expiry, re-authenticates once, and
retries the original request. The client still gets a 200 and never sees a
login. The mock stats show successful_logins increased by exactly one. The test
suite also fires ten concurrent requests at an expired session and asserts one
login, so this is proven under contention."''',
"4:27-4:50": '''"Finally, seventy-seven tests plus one opt-in live test cover parsing quirks,
cache semantics, hierarchy defects, retries, breaker behavior, API errors,
OpenAPI drift, and exactly-one re-authentication. I skipped SQLite, Redis, and
multi-tenant auth because the brief is read-only and single process; those
trade-offs are documented. The result is a runnable adapter with a real failure
model and honest boundaries. Thanks for watching."''',
}

for stamp, narration in new.items():
    pattern = re.compile(
        r"(\[" + re.escape(stamp) + r"\][^\n]*\n(?:.*\n)*?SAY:\n)(.*?)(\n\n)",
        re.S,
    )
    text, n = pattern.subn(lambda m: m.group(1) + narration + m.group(2), text, count=1)
    if n != 1:
        raise SystemExit(f"failed to update {stamp}: {n}")

p.write_text(text, encoding="utf-8")
print("updated narration")
