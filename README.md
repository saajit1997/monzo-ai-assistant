# Monzo AI Knowledge Assistant

> **Disclaimer:** This is an independent portfolio project and is not affiliated with, endorsed by, or sponsored by Monzo Bank.

## Problem

Banking websites spread customer information across product pages, FAQs, help docs, and blog posts, with no single place to ask a question and get a grounded answer. Customers manually hunt across pages to piece together things like "does this account support joint ownership" or "what happens if I lose my card abroad."

## MVP hypothesis

A grounded conversational interface over a bank's public content could make that information easier to discover — and, as a byproduct of every question asked, generate structured analytics about what customers actually want to know (product-intent signals, content gaps) that a support or product team could act on.

## Long-term architecture

```text
Monzo Public Website → Data Ingestion → Content Processing → dbt Transformation
    → Knowledge Graph → Vector Retrieval → LLM/RAG → AI Assistant
    → Query Analytics → Product Insights
```

Full detail, including the deliberate split between the operational (answer-a-question) path and the analytics (learn-from-questions) path, is in [`docs/architecture.md`](docs/architecture.md).

## Current phase

**Phase 7 — RAG assistant (next).** Phases 1–3 built the content pipeline: sitemap → URL inventory (`data/raw/monzo_urls.csv`) → raw HTML (`data/raw/pages/`, manifest at `data/raw/pages_manifest.csv`) → clean structured text (`data/processed/pages.parquet`). Phase 6 chunks that text (~2 pages → 1 chunk, ~220 words each) and builds a hybrid retrieval index — local sentence-transformers embeddings in FAISS, fused with BM25 keyword search via Reciprocal Rank Fusion — under `data/processed/retrieval/`. A hand-written 14-query acceptance eval hits the correct page in the top-5 for 13/14 (93%). Phases 4 (dbt over static content) and 5 (knowledge graph) are deferred — see the MVP scope note in `docs/roadmap.md`.

Retrieval works; there's no LLM-backed chat yet — `scripts/query_monzo_assistant.py` shows retrieved chunks, not a generated answer.

## Future phases

| Phase | Focus |
|---|---|
| 1 | URL discovery |
| 2 | Content ingestion |
| 3 | Content cleaning and modelling |
| 4 | dbt analytics layer *(deferred, not required for MVP)* |
| 5 | Knowledge graph *(deferred, not required for MVP)* |
| 6 | Embeddings + hybrid retrieval |
| 7 | RAG assistant *(current)* |
| 8 | Query/event analytics |
| 9 | Product analytics dashboard |
| 10 | Evaluation and observability |

See [`docs/roadmap.md`](docs/roadmap.md) for objectives/outputs/acceptance criteria per phase.

## Data policy

- `data/raw/monzo_urls.csv` **is committed to git.** It's a small (~1–2k row), useful artifact that documents Phase 1's output for reviewers — nothing sensitive, just public URLs and metadata derived from them.
- `data/raw/pages_manifest.csv` **is committed to git.** Same reasoning as above — one small, useful row per fetched URL (status code, content hash, timestamp), not the content itself.
- `data/raw/pages/*.html` (the actual scraped HTML bodies) is **not committed** — regenerable by re-running `scripts/fetch_monzo_pages.py`, and bulky enough (~1k files, ~100MB) that it doesn't belong in git history.
- `data/processed/pages.parquet` **is committed to git.** It's small (~1MB) and, unlike the raw HTML, isn't regenerable from anything else in the repo — it's built from the gitignored `data/raw/pages/*.html`, so without committing it a reviewer would have to run the full live crawl themselves just to see Phase 3's output.
- `data/processed/retrieval/` (Phase 6's FAISS index + chunk table) **is committed to git.** Small (~3MB) and, unlike Phase 3's output, *is* regenerable from something already committed (`pages.parquet`) — committed anyway so reviewers can query the assistant's retrieval step immediately instead of waiting ~30s for a local embedding build.
- The rest of `data/processed/` and all of `data/analytics/` are **not committed** (`.gitignore`'d, `.gitkeep` aside). Future phases may generate much larger derived content there that doesn't belong in git history.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

(Equivalently: `pip install -e .` — `pyproject.toml` is the source of truth for dependencies; `requirements.txt` is generated from it for reviewers who'd rather not do an editable install.)

## Running Phase 1

```bash
python scripts/discover_monzo_urls.py
pytest
```

Optional flags on the discovery script:

- `--limit N` — cap the number of URLs processed, for fast local iteration.
- `--output PATH` — override the output CSV path.

## Running Phase 2

```bash
python scripts/fetch_monzo_pages.py
```

Fetches raw HTML for every `include_in_mvp=True` URL from `data/raw/monzo_urls.csv` into `data/raw/pages/`, writing a fetch manifest to `data/raw/pages_manifest.csv`. Re-running skips URLs that were already fetched successfully — pass `--force` to re-fetch everything anyway.

Optional flags:

- `--limit N` — cap the number of URLs fetched, for fast local iteration.
- `--force` — re-fetch URLs even if already successfully cached.
- `--input / --output-dir / --manifest PATH` — override the default paths.

## Running Phase 3

```bash
python scripts/clean_monzo_content.py
```

Reads Phase 2's fetch manifest, parses each successfully-fetched HTML file with BeautifulSoup, strips nav/header/footer/cookie-banner boilerplate, and writes one row per page (title, headings, body text, word count, published date if available) to `data/processed/pages.parquet`.

Optional flags:

- `--limit N` — cap the number of pages cleaned, for fast local iteration.
- `--manifest / --urls / --output PATH` — override the default paths.

## Running Phase 6

```bash
python scripts/build_retrieval_index.py
python scripts/query_monzo_assistant.py "what's the fee for withdrawing cash abroad"
python scripts/evaluate_retrieval.py
```

`build_retrieval_index.py` chunks `data/processed/pages.parquet` (~220 words/chunk, packed along existing line boundaries so a table row is never split from its own label), embeds every chunk locally with `sentence-transformers` (`all-MiniLM-L6-v2`, no API key), and saves a FAISS index + chunk table to `data/processed/retrieval/`.

`query_monzo_assistant.py` runs a hybrid search (vector similarity + BM25 keyword, fused with Reciprocal Rank Fusion) and prints the top-k retrieved chunks — no LLM call, just what a RAG assistant would see as context.

`evaluate_retrieval.py` runs a hand-written 14-question query set with expected-relevant pages against the built index and reports hit-rate@5 (currently 13/14, 93%).

## Responsible crawling

This project only reads publicly listed sitemap URLs and respects `robots.txt` (`Sitemap:` directives, `Disallow` rules, `Crawl-delay`). It sends an identifying `User-Agent`, rate-limits requests, and never touches customer/personal data, authentication, or non-public endpoints.
