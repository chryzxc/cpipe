---
name: my-optimization-audit
description: "Audit any codebase for performance issues and rank fixes."
version: 0.1.0
---

# Optimization Audit Skill

Read-only, evidence-first performance audit of a named repository, API, database, or frontend. Produces a prioritized findings report — severity, evidence, measured impact, smallest fix, verification — and routes fixes to the right specialist. It never edits code, mutates infrastructure, or approves its own recommendations.

## When to Use

- An optimization-audit task is assigned (Planner runs the read-only audit pass; Implementer runs fix-then-verify passes).
- The user asks to speed up, slim, or scale a codebase, endpoint, database, or UI.
- A release will add load (launch, migration, campaign) and needs a performance baseline first.

Don't use for: security verdicts (Security Reviewer), regression/CI/release evidence (Verifier), live incident response, or cloud cost estimation.

## Prerequisites

- Frozen target: exact repository/commit or running service URL, plus a stack inventory (language, framework, database, hosting).
- Dynamic checks need a runnable app or database. Without one, mark the report `static-only` and skip runtime findings.
- No credential changes, no infrastructure mutation, no load tests against production without explicit approval.

## How to Run

1. Inventory the stack with `search_files` and `read_file`: entry points, data layer, HTTP client, build config, dependency manifest. Record the stack map.
2. Capture a baseline before judging anything: page weight and load time (`terminal(command="lighthouse <url>")` or the platform profiler), response payload sizes, slow query log, bundle sizes. Unmeasured domains produce hypotheses, not findings.
3. Work every matching checklist domain below. For each item record `detected` / `clean` / `skipped` with evidence.
4. Classify findings with the severity rubric and write the report in the findings format.
5. Re-audit after fixes: rerun identical measurements and report only the delta.

## Audit Checklist

### Frontend rendering and runtime

- **Unnecessary re-renders** — components re-render on unrelated state changes. Detect: render-cause profiling plus memo/selector review. Fix: memoized selectors, stable callbacks, split state.
- **Debounce input handlers** — typing/search handlers fire requests or heavy work per keystroke; scroll/resize handlers run unthrottled. Detect: listeners on input/scroll/resize with network or write side effects. Fix: debounce inputs, throttle scroll/resize.
- **Loading skeletons** — deferred content pops in with no intermediate state. Detect: async/suspense boundaries without placeholders. Fix: skeletons matching final layout.
- **Paginate large lists** — unbounded queries or arrays rendered whole. Detect: missing LIMIT in queries, full-collection mapping. Fix: server-side pagination plus virtualized windows for long scroll surfaces.
- **Lazy loading** — everything mounts at startup. Detect: route/asset graphs with no dynamic imports. Fix: lazy routes, below-the-fold images, heavy components on demand.
- **Long main-thread tasks** — jank or blocking work. Detect: profiler tasks over 50ms. Fix: workers or chunked work; batch DOM reads/writes to avoid layout thrashing.
- **Memory leaks** — listeners, timers, observers retained after teardown. Detect: growing heap snapshots. Fix: cleanup on dispose.

### Bundle and assets

- **Split code into chunks** — one monolithic entry bundle. Detect: build output stats, single vendor chunk. Fix: route-level and vendor code splitting; confirm tree shaking.
- **Minify JS and CSS** — unminified production assets. Detect: bundle stats or file inspection. Fix: enable minification; ship source maps privately only.
- **Compress images** — oversized raster assets or stale formats. Detect: `terminal(command="du -sh <asset-dirs>")` and image inspection. Fix: resize to render size, WebP/AVIF with fallback, `srcset`, explicit dimensions.
- **Unused dependencies** — packages absent from the import graph. Detect: dependency graph tool (knip, depcheck, or platform equivalent). Fix: remove, rebuild, keep tests green.
- **Critical rendering path** — render-blocking CSS/JS and fonts. Detect: coverage profiling. Fix: inline critical CSS, `font-display: swap`, preload critical assets.

### Network and delivery

- **Add a CDN** — static assets served from origin only. Detect: asset URLs on the app domain with no cache headers. Fix: CDN in front of assets with fingerprinted URLs and immutable `Cache-Control`.
- **Compress API payloads** — uncompressed JSON/HTML responses. Detect: response headers lack `Content-Encoding`. Fix: Brotli/gzip at server or proxy.
- **Defer non-critical scripts** — synchronous third-party tags in head. Detect: blocking script order. Fix: `async`/`defer`, move tags off the critical path, load on interaction.
- **HTTP version and connections** — HTTP/1.1 with many parallel requests, no keep-alive. Detect: protocol column in the network trace. Fix: HTTP/2+, connection reuse, fewer round trips.
- **Over-fetching** — endpoints return fields the caller never reads. Detect: response size versus used fields. Fix: sparse fieldsets or field selection; stream large exports.

### API and backend

- **Cache API responses** — identical repeated calls hit origin every time. Detect: duplicate requests in traces, no client memoization. Fix: HTTP caching with ETags/Cache-Control for shareable responses, per-session memoization otherwise.
- **Server-side caching** — hot data recomputed per request (sessions, config, feature flags). Detect: per-request recomputation in traces. Fix: Redis/memcached layer with an explicit invalidation story.
- **Background slow work** — request waits on email, exports, reports. Detect: slow endpoints doing non-response work. Fix: queue plus worker, return accepted-with-status.
- **Rate limiting and backpressure** — unbounded fan-out or burst acceptance on expensive endpoints. Detect: missing limiter review. Fix: queue depth limits, rate limits, circuit breakers.

### Database

- **N+1 queries** — one query per item inside a loop or lazy relation. Detect: query logs showing per-row SELECTs; ORM lazy-loading defaults. Fix: batch with joins, IN loads, or DataLoader-style grouping.
- **Index the database** — frequent filters/sorts/joins scanning full tables. Detect: `EXPLAIN ANALYZE` showing sequential scans on hot queries. Fix: targeted indexes; check the write ratio first — never index write-heavy tables blindly.
- **Cache expensive queries** — repeated heavy aggregations. Detect: identical slow queries in the log. Fix: query cache or materialized views refreshed on schedule.
- **Connection pooling** — a connection per request or an unbounded pool. Detect: pool config review, connection errors under load. Fix: right-sized persistent pool with timeouts.
- **Read replicas** — read traffic saturating the primary. Detect: primary CPU/IO dominated by reads. Fix: route reads to replicas (infrastructure — Release Engineer).

### Infrastructure

- **Load balancer** — a single origin instance receives all traffic. Detect: deployment topology review. Fix: load balancer across instances with health checks (Release Engineer; approval required).
- **Performance observability** — no latency metrics to prove impact. Detect: missing APM or percentile dashboards. Fix: add latency/error/percentile metrics before optimizing so every fix is provable.

## Findings Format

Report one block per finding, ordered by severity:

```
PERF-001 · High · Cache API responses
Evidence: src/api/client.swift:88 — every poll calls /v1/stats uncached; 14 duplicate calls in 60s trace
Impact: ~840 req/h per active client; p95 +310ms
Smallest fix: memoize /v1/stats for 30s with revalidation
Effort: S · Owner: Implementer · Verify: trace shows cache hits and p95 delta
```

Severity rubric:

- **Critical** — hot-path defect that compounds with load (N+1 in a loop, unbounded list, pool exhaustion) or multi-second user-visible stalls.
- **High** — measurable waste on common paths (uncached repeated calls, uncompressed payloads, missing index on a hot query).
- **Medium** — cold-path or asset waste (unused deps, unminified assets, no lazy loading off the critical path).
- **Low** — hygiene (dead code, minor configuration drift).

Every finding carries evidence. A suspicion without reproduction is labeled `HYPOTHESIS` and never counts in the verdict.

## Pitfalls

- No cache without an invalidation strategy — state how entries expire, or the fix trades latency for staleness bugs.
- Indexes speed reads and tax writes; check the write ratio first.
- CDN, load balancer, replicas, and pool changes are infrastructure: route to Release Engineer and get approval.
- Micro-benchmarks lie; prefer real traces and percentiles over averages.
- Pagination and virtualization both fix large lists — pick by navigation pattern; do not stack both blindly.
- Compressing already-compressed formats (JPEG, PNG, zip) wastes CPU.

## Verification

- Every checklist domain is marked detected / clean / skipped (with reason) in the report.
- Each finding carries file:line or command-output evidence; the auditor changed nothing.
- Baseline numbers exist for every domain a fix targets; the re-audit reports like-for-like deltas.
- The report ends with a verdict: `CLEAN`, `FINDINGS_READY_FOR_FIXES`, or `BLOCKED` (missing access, unreadable state, or unrunnable dynamic checks).
