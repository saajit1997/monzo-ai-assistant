# Roadmap

> This is an independent portfolio project and is not affiliated with, endorsed by, or sponsored by Monzo Bank.

Each phase is scoped to be buildable over a weekend. See `docs/architecture.md` for how these fit together.

**MVP scope note (added after Phase 3):** Phases 4 (dbt over static content) and 5 (knowledge graph) are deferred, not required for the MVP. Neither is on the critical path to the actual MVP hypothesis — a working RAG assistant that also captures query analytics. Phase 4 as originally scoped was explicitly "a warm-up for Phase 8"; the real dbt/analytics payoff is modelling the live query stream in Phase 8, once one exists, not page-count stats over content that won't change much. The leanest path to a working, demonstrable MVP is **Phase 6 → 7 → 8**. Phases 4/5 can be revisited afterward as portfolio depth if there's time.

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
- **Table bug found and fixed**: plain `get_text()` flattened `<table>` elements (fee schedules, terms-and-conditions tables) into a bare sequence of cell values with no row/column label attached — a real correctness risk once Phase 6 chunks this text, since a chunk boundary could separate a fee amount from the plan/region it applies to. Tables are now rendered as explicit `"row — column: value"` text. Also fixed a related case on `/savings-isas`'s comparison tables (SVG-icon-only headers, row labels duplicated inside value cells for the mobile layout).
- **Known limitation, deliberately deferred to Phase 6**: `get_text(separator="\n")` fragments deeply-nested UI components (plan-card buttons, step-by-step instructions) into many tiny disconnected lines — e.g. `"Get"`, `"Tap"`, `"Learn more"` each on their own line, repeated per plan/step. Affects ~51/909 pages (>15% duplicate-line ratio), worse on pricing-plan and instructional pages (`/current-account/plans`, `/help/.../monzo-card-lost-damaged-stolen`). Unlike the table bug, this doesn't attach wrong labels to values — it's noise, not incorrect information — so it was left as-is rather than redesigning the whole line-break strategy. Revisit if Phase 6's chunking doesn't naturally absorb it (a chunk window spanning several lines should smooth over most of this) or if it visibly hurts retrieval quality.

## Phase 4 — dbt analytics layer *(deferred, not required for MVP)*

- **Objective**: Stand up a dbt project over the processed content to model page/category-level analytics (content volume by category, freshness, etc.) as a warm-up for the Phase 8 query-analytics dbt models.
- **Outputs**: `dbt/` project with staging + mart models.
- **Acceptance criteria**: `dbt run` + `dbt test` succeed against a local DuckDB (or similar) target.

## Phase 5 — Knowledge graph *(deferred, not required for MVP)*

- **Objective**: Extract entities (products, fees, features) and relationships from processed content into a graph.
- **Outputs**: Graph store populated under `src/monzo_ai/knowledge_graph/`.
- **Acceptance criteria**: Can answer simple structured queries (e.g. "which accounts have no monthly fee") directly from the graph.

## Phase 6 — Embeddings + hybrid retrieval

- **Objective**: Chunk and embed processed content; combine vector similarity with keyword/BM25 search.
- **Outputs**: Vector store populated under `src/monzo_ai/retrieval/`, artifacts in `data/processed/retrieval/` (committed — small and, unlike Phase 3's output, regenerable from `pages.parquet` alone, but committed anyway for reviewer convenience).
- **Acceptance criteria**: Given a sample query set, retrieval returns relevant chunks in the top-k with measured precision.
- **Approach**: chunks are packed from Phase 3's existing line-per-paragraph/line-per-table-row text up to ~220 words, splitting only at line boundaries (never mid-sentence, and — since Phase 3 already made every table row self-contained — never separating a value from its label). Embeddings are computed once with a local `sentence-transformers` model (`all-MiniLM-L6-v2`, no API key, no per-query cost, fully reproducible offline) into a FAISS flat index; BM25 (`rank_bm25`) supplies the keyword half. The two rankings are combined via Reciprocal Rank Fusion rather than a learned re-ranker, kept deliberately simple for the MVP.
- **Status**: done. Live build: 909 pages → 1,774 chunks, ~3MB index, ~30s to build locally. `scripts/evaluate_retrieval.py` runs a hand-written 14-query set (spanning help/product/business/security/legal) against the real index: **13/14 (93%) hit-rate@5**. The one miss ("how much does Monzo Max cost per month") pulled several on-topic-but-wrong pages (T&Cs, fee-information) instead of the actual plan-comparison page — a plausible limitation of a small local embedding model on a comparison-style query, not a code bug; noted rather than chased further for MVP scope.

## Phase 7 — RAG assistant

- **Objective**: Wire retrieval into an LLM-backed conversational assistant. (Originally also planned to draw on Phase 5's knowledge graph — deferred with Phase 5; retrieval-only is enough to prove the concept for the MVP.)
- **Outputs**: `app/streamlit_app.py` (interface choice made explicitly at this point — Streamlit over the originally-planned bare FastAPI endpoint, since the user already knows Streamlit and it gives a real chat UI in one file rather than an API plus a separate frontend); `src/monzo_ai/assistant/generate.py` (grounded prompt + Claude call); `src/monzo_ai/assistant/query_log.py` (SQLite logging, pulled forward from Phase 8 — see note below).
- **Acceptance criteria**: Answers a benchmark set of questions with grounded citations back to source URLs.
- **Model choice**: Claude Haiku 4.5 (`claude-haiku-4-5`) — explicitly chosen over Sonnet/Opus for cost, since these are grounded lookups over a handful of short retrieved chunks, not complex reasoning. Configurable in `config/sources.yaml` under `assistant:`.
- **Grounding approach**: the system prompt instructs the model to answer *only* from the retrieved context, cite source URLs, and — critically — respond with one fixed, exact marker sentence when the context doesn't contain the answer. Checking for that literal string (rather than trying to infer "did it actually answer" from free-form text) is what makes `had_answer` a reliable signal for Phase 8/9's content-gap analytics, not a guess.
- **Query logging pulled forward from Phase 8**: rather than building the chat app first and bolting on logging later, every exchange (question, retrieved chunks with rank/url/category/score, generated answer, model, latency, token counts, `had_answer`) is logged to `data/analytics/query_log.db` (SQLite, normalized: `queries` + `query_results` tables) from Phase 7's first commit. This is the project's stated differentiator per `docs/architecture.md` — the query stream itself is the analytics dataset — so it was built in from the start rather than retrofitted.
- **Note on cost**: this is the first phase that costs money per use (Claude API calls) and needs the user's own `ANTHROPIC_API_KEY`. Every prior phase (1–6) is free and runs fully offline/local.

## Phase 8 — Query/event analytics

- **Objective**: Log every query + retrieval outcome as a structured event; model it with dbt.
- **Outputs**: Event schema, dbt models under `dbt/`, populated `data/analytics/`.
- **Acceptance criteria**: Can answer "what are people asking about and where does retrieval fail" from the modelled data alone.
- **Status**: event capture is already done — Phase 7 built the logging (`data/analytics/query_log.db`) in from the start rather than retrofitting it. What's left for this phase specifically is the dbt modelling layer on top of the raw event table.

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
