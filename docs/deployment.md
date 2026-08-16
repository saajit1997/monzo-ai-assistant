# Deployment

> This is an independent portfolio project and is not affiliated with, endorsed by, or sponsored by Monzo Bank.

Live at **https://huggingface.co/spaces/saajit1997/monzo-ai-assistant** — Docker SDK, Hugging Face Spaces, CPU basic hardware.

## Why Docker on HF Spaces (not Streamlit Community Cloud)

Streamlit Community Cloud was the original plan (free, official, GitHub-integrated), but its free tier caps apps at ~1GB RAM, which the `torch` + `transformers` + `sentence-transformers` + `faiss-cpu` stack risked exceeding. Hugging Face Spaces' CPU basic tier gives 16GB RAM instead.

**Platform-policy note (mid-2026):** Hugging Face now requires a **PRO account ($9/month)** to create Gradio or Docker Spaces — anything that actually runs compute. Only Static Spaces are free. The "New Space" SDK picker also no longer shows a dedicated Streamlit tile for new accounts under this policy — deployment here uses the **Docker SDK** with a hand-written `Dockerfile` instead, which also sidesteps HF's separate supported-`sdk_version` allowlist that the Streamlit SDK is subject to. If HF's pricing/SDK availability has changed since, re-verify before assuming either of these still holds.

## Redeploying (runbook)

The Space's `README.md` needs a Hugging Face config YAML frontmatter block (`sdk: docker`, `app_port: 8501`, etc.) that the GitHub-facing `README.md` deliberately does **not** carry (it renders as an ugly raw table on GitHub). It only gets added on the snapshot pushed to the Space. HF Hub also rejects plain-git binary blobs outright ("use Xet storage") — `data/processed/pages.parquet`, `data/processed/retrieval/chunks.parquet`, and `data/processed/retrieval/vector_index.faiss` have to go through the Xet-backed HTTP upload API instead of a normal `git push`. Both of these mean a full redeploy is a two-part process, every time:

**1. Push the code as a single-commit orphan snapshot** (from the repo root, on `main`, working tree clean):

```bash
git checkout --orphan hf-space-deploy
git rm --cached data/processed/pages.parquet data/processed/retrieval/chunks.parquet data/processed/retrieval/vector_index.faiss
# prepend the HF config frontmatter to README.md just for this snapshot (see below), then:
git add -A
git commit -m "Deploy snapshot for Hugging Face Space"
git push --force https://<hf-username>:<hf-write-token>@huggingface.co/spaces/saajit1997/monzo-ai-assistant hf-space-deploy:main

# restore local working tree
git checkout main
git branch -D hf-space-deploy
```

Frontmatter to prepend to `README.md` on the orphan branch only (not on `main`):

```yaml
---
title: Monzo AI Knowledge Assistant
emoji: 💬
colorFrom: red
colorTo: gray
sdk: docker
app_port: 8501
pinned: false
license: mit
---
```

**2. Re-upload the 3 binary files** via the Python API (this MUST be re-run after every step-1 push — the force-push of a new orphan snapshot orphans whatever binary-upload commit was sitting on top of the previous one):

```python
from huggingface_hub import HfApi
api = HfApi(token="<hf-write-token>")
api.upload_folder(
    folder_path="data/processed",
    path_in_repo="data/processed",
    repo_id="saajit1997/monzo-ai-assistant",
    repo_type="space",
)
api.restart_space(repo_id="saajit1997/monzo-ai-assistant")
```

A write-scoped HF token comes from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). Never save it into `.git/config` — pass it inline per command.

**Checking build/runtime status without opening a browser:**

```python
api.get_space_runtime(repo_id="saajit1997/monzo-ai-assistant").stage   # e.g. "RUNNING", "RUNNING_BUILDING"
list(api.fetch_space_logs(repo_id="saajit1997/monzo-ai-assistant", build=True))  # build log lines
```

## Bugs already fixed (don't reintroduce)

1. **`pip install torch` on Linux pulls the CUDA build by default.** A `requirements.txt` generated via `pip freeze` on Windows has a plain `torch==X` pin with no platform marker; on Linux this resolves to the GPU wheel and drags in ~2GB of `nvidia-*` CUDA/cuDNN/cuBLAS packages that are dead weight on CPU-only hardware, bloating/slowing the build for nothing. Fixed by adding `--extra-index-url https://download.pytorch.org/whl/cpu` as the first line of `requirements.txt`.
2. **Non-editable `pip install .` breaks path resolution.** `src/monzo_ai/ingestion/discover_urls.py` computes `REPO_ROOT = Path(__file__).resolve().parents[3]`, which every default config/data path derives from. A non-editable install physically copies the package into site-packages, so that walk-up lands under site-packages instead of the real project root — `load_config()` then can't find `config/sources.yaml`, which surfaces as a misleading "retrieval index not found" error (the app's broad `except FileNotFoundError` handler catches it). The `Dockerfile` installs the package with `pip install -e .` (editable) specifically to keep `__file__` pointing at the real source tree inside the image — don't change this to a plain `pip install .`.
3. **Container disk is ephemeral.** Anything written to local disk (including the SQLite query-log db) is wiped on every Space restart/rebuild. Fixed with a private HF Storage Bucket (`saajit1997/monzo-ai-assistant-analytics`) mounted read-write at `/data`, and an `ANALYTICS_DB_PATH=/data/query_log.db` Space variable that `app/streamlit_app.py` checks before falling back to the local relative-path config default. Verified durable by downloading the db directly from the bucket (independent of the running container) and reading real rows back with `sqlite3`.

## Operational config

- Session cap: `config/sources.yaml` → `assistant.max_queries_per_session` (currently 10) — bounds Claude API cost per visitor; no password gate.
- Secrets: `ANTHROPIC_API_KEY` set as a Space **secret**; `ANALYTICS_DB_PATH` set as a Space **variable** (not sensitive, just not meaningful locally).
- Local dev is unaffected by any of the above — `ANALYTICS_DB_PATH` is unset locally, so it falls back to `data/analytics/query_log.db`, and there's no HF-specific config needed to run `streamlit run app/streamlit_app.py` locally.
