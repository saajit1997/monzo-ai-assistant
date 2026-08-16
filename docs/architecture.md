# Architecture

> This is an independent portfolio project and is not affiliated with, endorsed by, or sponsored by Monzo Bank.

## Long-term architecture

```text
Monzo Public Website
        │
        ▼
Data Ingestion            (sitemap discovery, page scraping)
        │
        ▼
Content Processing        (HTML → clean text/markdown, chunking)
        │
        ▼
dbt Transformation         (structured content + analytics models)
        │
        ▼
Knowledge Graph            (entities: products, fees, features, and their relationships)
        │
        ▼
Vector Retrieval           (embeddings + hybrid search over content chunks)
        │
        ▼
LLM / RAG                  (grounded generation over retrieved context + graph facts)
        │
        ▼
AI Assistant                (conversational interface)
        │
        ▼
Query Analytics             (every question asked is itself a data point)
        │
        ▼
Product Insights            (customer intent, content gaps, FAQ demand)
```

## Two paths through the same system

The architecture above is drawn as one pipeline, but it's deliberately built to serve two distinct consumers from the same underlying data and the same live query stream. That dual purpose is the central design idea of this project — not an incidental side effect.

### Operational AI path

```text
User → query → retrieval (knowledge graph / vector) → LLM → grounded answer
```

The user-facing job: answer a question about Monzo's products, fees, or features accurately, grounded in real ingested content, with the knowledge graph supplying structured facts (e.g. "which accounts support joint ownership") and the vector store supplying relevant prose (help articles, FAQs).

### Analytics path

```text
User queries → event logging → dbt → product analytics → customer intent / content-gap insights
```

The product/business-facing job: every question asked, every retrieval that succeeds or comes up empty, is a signal. Logged as structured events, transformed with dbt, this becomes a dataset that answers questions a support team or product manager would actually care about — what are people trying to find out, where does the content fail to answer them, which topics cluster together, how does intent shift over time.

### Why this matters

Most public RAG demos stop at "ask a question, get an answer." Treating the query stream itself as a first-class analytics dataset — with a dbt layer, defined grain, and downstream dashboards — is what turns this from a chatbot demo into a product analytics portfolio piece. It's the same reason a real company would build something like this: not just to answer customers, but to learn what they're asking.

## Phase 1–3 scope

Phases 1–3 (current) implement **Data Ingestion** and the start of **Content Processing**. Phase 1 produces a validated, categorised inventory of public Monzo URLs (`data/raw/monzo_urls.csv`); Phase 2 fetches the raw HTML for the MVP-flagged subset of that inventory (`data/raw/pages/`, tracked via `data/raw/pages_manifest.csv`); Phase 3 parses that raw HTML into clean, structured text (`data/processed/pages.parquet`). No embeddings, graph construction, retrieval, LLM calls, or analytics are implemented yet.

## Ingestion + processing pipeline

```text
robots.txt → sitemap discovery (recursive) → extract URLs → normalise → dedupe
→ filter irrelevant/excluded pages → categorise → mark MVP inclusion → save CSV   (Phase 1)

monzo_urls.csv → filter to include_in_mvp → per-URL robots.txt check
→ fetch raw HTML (retry on transient failure) → save to disk + manifest          (Phase 2)

pages_manifest.csv → join category from monzo_urls.csv → parse HTML with
BeautifulSoup → strip nav/header/footer/script/cookie-banner boilerplate
→ extract title/headings/body text → validate with pydantic → save Parquet     (Phase 3)
```

See `src/monzo_ai/ingestion/` and `src/monzo_ai/processing/` for the implementation:

- `ingestion/sitemap.py` — robots.txt + sitemap(-index) fetching and recursive resolution; also exposes `fetch_robots_parser()`, reused by Phase 2 for per-page robots.txt checks.
- `ingestion/filters.py` — URL normalisation, categorisation, and MVP-inclusion rules (all driven by `config/sources.yaml`).
- `ingestion/discover_urls.py` — Phase 1 orchestration, pydantic validation, CSV output.
- `ingestion/fetch_pages.py` — Phase 2 orchestration: fetch + persist raw HTML, manifest-based idempotent re-runs.
- `processing/clean_content.py` — Phase 3: `extract_page_content()` is a pure HTML-string-in/dict-out function (independently testable); `clean_pages()` orchestrates reading the manifest, extracting, and writing Parquet.
- `utils/http.py` — shared retry/backoff HTTP helper used by both `sitemap.py` and `fetch_pages.py`, so rate-limiting behaviour stays consistent across the ingestion pipeline.
