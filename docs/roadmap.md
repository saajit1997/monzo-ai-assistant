# Roadmap

> This is an independent portfolio project and is not affiliated with, endorsed by, or sponsored by Monzo Bank.

Each phase is scoped to be buildable over a weekend. See `docs/architecture.md` for how these fit together.

## Phase 1 — URL discovery

- **Objective**: Discover every public URL on monzo.com via sitemap(s), respecting robots.txt, and produce a categorised, MVP-flagged inventory.
- **Outputs**: `data/raw/monzo_urls.csv`.
- **Acceptance criteria**: `python scripts/discover_monzo_urls.py` runs against the live site with zero manual URL entry; output is normalised, deduplicated, categorised, and MVP-flagged from config-driven rules; `pytest` passes with zero live network calls.
- **Status**: done. Live run against monzo.com: 2,512 URLs discovered, 943 flagged `include_in_mvp`.

## Phase 2 — Content ingestion

- **Objective**: Fetch the HTML for every `include_in_mvp=True` URL from Phase 1's inventory.
- **Outputs**: Raw HTML per URL under `data/raw/pages/` (not committed — regenerable), with a fetch manifest (`data/raw/pages_manifest.csv`: status code, fetched_at, content hash, error) for idempotent re-runs.
- **Acceptance criteria**: Reuses the same `httpx.Client` + rate-limiting pattern as Phase 1 (shared retry helper in `src/monzo_ai/utils/http.py`); additionally re-checks each URL against `robots.txt` via `RobotFileParser.can_fetch()` before fetching it (Phase 1 only reads sitemaps, never fetches individual pages, so this is the first point robots.txt Disallow rules matter for real page fetches); handles 4xx/5xx/timeouts without crashing the batch; re-running skips URLs with an already-successful manifest entry unless `--force` is passed.
- **Note**: raw HTML only — no boilerplate stripping or text extraction. That's Phase 3.
- **Status**: done. Live run against monzo.com: 943 targets, 909 fetched successfully, 34 failed (all clean HTTP 404s — a stale sitemap cluster under `/help/monzo-home-insurance/*` for a discontinued product, plus a handful of individually-stale help URLs; nothing on the pipeline side).

## Phase 3 — Content cleaning and modelling

- **Objective**: Turn raw HTML into clean, structured text (title, body, headings, published date where available) using `beautifulsoup4`.
- **Outputs**: `data/processed/pages.parquet` (committed — small and not regenerable from anything else in the repo, see README's data policy) with one row per page.
- **Acceptance criteria**: Boilerplate (nav, footer, cookie banners) stripped; content is chunkable; schema validated with pydantic.
- **Approach**: every Monzo page inspected has exactly one `<main>` tag holding the real content, with site nav/header/footer and the cookie-consent banner consistently living outside it — `<main>` (falling back to `<body>`, then the whole document) is used as the content root, with `<nav>/<header>/<footer>`/script/style and any cookie/consent-marked elements additionally stripped from inside it defensively. `og:title`/`og:description` are preferred over the plain `<title>`/meta description tags, which are more consistently populated on this site.
- **Status**: done. Live run: 909/909 fetched pages cleaned (0 failures after a bug fix — see below); median 213 words/page; 0 rows with detectable boilerplate leakage; `published_at` is null for all 909 pages (Monzo doesn't expose page dates in `<time>` tags or `article:*` meta on this site).
- **Bug found and fixed during the live run**: the boilerplate-stripper decomposed cookie/consent-marked elements while iterating a pre-computed `find_all(True)` list; on `/legal/*` pages with *nested* cookie-marked elements (a wrapper div plus an inner marked child), decomposing the outer element invalidated the inner one's `.attrs` before the loop reached it, crashing on `'NoneType' object has no attribute 'get'` for 750/909 pages. The two hand-written test fixtures didn't have nested markers, so this only surfaced against real data. Fixed with a `tag.decomposed` guard; added a regression test with a nested cookie-marker fixture.

## Phase 4 — dbt analytics layer

- **Objective**: Stand up a dbt project over the processed content to model page/category-level analytics (content volume by category, freshness, etc.) as a warm-up for the Phase 8 query-analytics dbt models.
- **Outputs**: `dbt/` project with staging + mart models.
- **Acceptance criteria**: `dbt run` + `dbt test` succeed against a local DuckDB (or similar) target.

## Phase 5 — Knowledge graph

- **Objective**: Extract entities (products, fees, features) and relationships from processed content into a graph.
- **Outputs**: Graph store populated under `src/monzo_ai/knowledge_graph/`.
- **Acceptance criteria**: Can answer simple structured queries (e.g. "which accounts have no monthly fee") directly from the graph.

## Phase 6 — Embeddings + hybrid retrieval

- **Objective**: Chunk and embed processed content; combine vector similarity with keyword/BM25 search.
- **Outputs**: Vector store populated under `src/monzo_ai/retrieval/`.
- **Acceptance criteria**: Given a sample query set, retrieval returns relevant chunks in the top-k with measured precision.

## Phase 7 — RAG assistant

- **Objective**: Wire retrieval + knowledge graph facts into an LLM-backed conversational assistant.
- **Outputs**: `app/` FastAPI service exposing a chat endpoint.
- **Acceptance criteria**: Answers a benchmark set of questions with grounded citations back to source URLs.

## Phase 8 — Query/event analytics

- **Objective**: Log every query + retrieval outcome as a structured event; model it with dbt.
- **Outputs**: Event schema, dbt models under `dbt/`, populated `data/analytics/`.
- **Acceptance criteria**: Can answer "what are people asking about and where does retrieval fail" from the modelled data alone.

## Phase 9 — Product analytics dashboard

- **Objective**: Surface Phase 8's models in a dashboard (customer intent themes, content-gap detection, query volume trends).
- **Outputs**: Dashboard (e.g. Streamlit or a BI tool) reading from `data/analytics/`.
- **Acceptance criteria**: Dashboard answers the acceptance question from Phase 8 visually, refreshable end-to-end.

## Phase 10 — Evaluation and observability

- **Objective**: Add retrieval/answer quality evaluation (groundedness, relevance) and basic operational observability (latency, error rates, cost).
- **Outputs**: Eval harness + a lightweight metrics/logging layer.
- **Acceptance criteria**: Regression in retrieval or answer quality is detectable from an automated eval run, not just manual spot-checks.

## Nice-to-haves, not required for any specific phase

- Linting/formatting/type-checking (`ruff`, `black`, `mypy`) — skipped in Phase 1 to keep the dependency footprint minimal; worth adding once the codebase is large enough that consistency starts to matter more than iteration speed.
- CI (GitHub Actions) running `pytest` on every push.
- Dockerising the Phase 7 `app/` service.
