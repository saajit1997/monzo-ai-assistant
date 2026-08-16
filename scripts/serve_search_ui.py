#!/usr/bin/env python
"""Local test UI for Phase 6's retrieval index -- NOT the Phase 7 assistant.

Serves a small HTML page (app/search_ui.html) plus a JSON search API, both
on localhost only, so you can interactively try queries against the real
hybrid (FAISS + BM25) index and see exactly what chunks retrieval returns.
No LLM call happens here and nothing is generated -- this shows the context
Phase 7 would eventually hand to an LLM, not an answer.

Stdlib-only deliberately: this is a throwaway testing tool, not a
commitment to Phase 7's eventual app architecture (CLI vs FastAPI is still
an open decision), so it doesn't add a web framework dependency to try it.

Usage:
    python scripts/serve_search_ui.py [--port 8765]
    Then open http://127.0.0.1:8765 in a browser.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from monzo_ai.ingestion.discover_urls import DEFAULT_CONFIG_PATH, load_config
from monzo_ai.retrieval.index import RetrievalIndex, embed_texts, load_index
from monzo_ai.retrieval.search import search
from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = REPO_ROOT / "app" / "search_ui.html"


def _make_handler(index: RetrievalIndex) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            logger.info("%s %s", self.address_string(), format % args)

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            if self.path in ("/", "/index.html"):
                body = HTML_PATH.read_text(encoding="utf-8").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802 - stdlib method name
            if self.path != "/api/search":
                self.send_error(404)
                return

            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                query = (payload.get("query") or "").strip()
                top_k = int(payload.get("top_k", 5))
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"error": "Invalid request body"})
                return

            if not query:
                self._send_json(400, {"error": "Query is empty"})
                return

            try:
                results = search(index, query, top_k=top_k)
            except Exception as exc:  # noqa: BLE001 - surface it to the UI rather than crashing the server
                logger.error("Search failed for %r: %s", query, exc)
                self._send_json(500, {"error": "Search failed on the server"})
                return

            rows = [
                {
                    "url": row["url"],
                    "title": row["title"],
                    "category": row["category"],
                    "text": row["text"],
                    "score": round(float(row["score"]), 4),
                }
                for _, row in results.iterrows()
            ]
            self._send_json(200, {"results": rows})

    return Handler


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a local test UI for the Phase 6 retrieval index.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--index-dir", type=Path, default=None)
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser tab.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    index_dir = args.index_dir or Path(config.get("retrieval", {}).get("index_dir", "data/processed/retrieval"))

    if not (index_dir / "vector_index.faiss").exists():
        logger.error("Retrieval index not found at %s — run scripts/build_retrieval_index.py first.", index_dir)
        return 1

    logger.info("Loading retrieval index from %s", index_dir)
    index = load_index(index_dir)

    logger.info("Warming up embedding model %s", index.model_name)
    embed_texts(["warmup"], model_name=index.model_name)  # pay the first-load cost now, not on the first query

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _make_handler(index))
    url = f"http://127.0.0.1:{args.port}"
    print(f"\nServing retrieval test UI at {url}  (Ctrl+C to stop)\n")

    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
