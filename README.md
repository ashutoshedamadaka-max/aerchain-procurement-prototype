# Aerchain procurement prototype

A Streamlit sourcing workspace for corrugated packaging bids. It compares inconsistent vendor documents, shows source evidence and uncertainty, evaluates questionnaire gates, and explains award scenarios.

This source repository includes the code and synthetic-data generators. The local vendor documents, databases and evaluation outputs are not published here. This is a prototype, not a production procurement approval system. Extraction, normalization and recommendations can contain errors; inspect their evidence and review flags.

## Run locally

Python 3.11 or newer is recommended. From the repository root:

```sh
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/main.py
```

The app reads a local `procurement.db`, which is not included in this public repository. Without data, pages show empty or unavailable states. Set `PROCUREMENT_DB` to use another pipeline database. Do not point the app at `dataset/truth/truth.sqlite`.

## API configuration

Browsing the recorded bids does not require an API key. Ask and live extraction require a provider key and incur API charges. Configure `OPENAI_API_KEY` and `ANALYST_PROVIDER=openai` in your environment or in a local `.env` file. Anthropic is also supported through `ANTHROPIC_API_KEY` and `ANALYST_PROVIDER=anthropic`.

Do not commit `.env`, API keys, or `.streamlit/secrets.toml`. They are excluded by `.gitignore`. On Streamlit Community Cloud, put credentials in the app's Secrets settings, not in GitHub. Restrict access to the Ask page or enforce a usage budget before exposing your API-backed demo publicly.

## Pages

- **RFx:** sourcing scope, line specifications and supplier questionnaire evidence.
- **Event:** recorded submissions and review queue.
- **Comparison:** bid grid, source anchors, clarification questions and award-note export.
- **Ask:** answers grounded in database tools and the award engine, with a visible tool trace.
- **Evals:** saved evaluation results, not a live re-run of tests.

## Project layout

- `app/`: Streamlit interface and app tests.
- `src/`: extraction, conversions, normalization, award evaluation and analyst tools.
- `scripts/`: dataset/document generation, grading and reporting utilities.
- `tests/`: deterministic checks; live analyst tests are opt-in.
- `dataset/artifacts/` (local only): synthetic RFx, vendor responses and certificates.
- `dataset/truth/` (local only): frozen synthetic ground truth used for generation and scoring, never as the analyst's source.
- `dataset/eval/eval_snapshot.json` (local only): saved results displayed by the Evals page.

## Tests

```sh
pip install -r requirements-dev.txt
python -m pytest tests app
```

Live tests are skipped by default. Setting `RUN_LIVE_ANALYST=1` enables tests that use API credit. Saved evaluation snapshots can lag the current database.

## Streamlit Community Cloud

Deploy this GitHub repository with `app/main.py` as the entry point. A populated demo additionally needs an approved data package containing `procurement.db`, `dataset/artifacts/` and, for Evals, `dataset/eval/eval_snapshot.json`. These are deliberately excluded from this public source repository. Configure provider credentials in Secrets. Repository publication alone does not deploy a live website.

## Prototype limitations

Use recorded results as evidence to review, not as guarantees. Unknown freight and absent supplier values remain explicit. Per-kg conversions depend on declared assumptions. Gate evidence can come from attachments or a buyer vendor-master record; those are distinct sources. Verify quote validity and commercial conditions before relying on an award recommendation.

## Generate synthetic inputs in a fresh checkout

These commands create demonstration inputs locally; they do not populate the extracted pipeline database. Run them in a fresh checkout, not over an existing frozen dataset:

```sh
python scripts/generate_dataset.py
python scripts/generate_vendor_docs.py
python scripts/export_rfx_artifact.py
python scripts/export_vendor_master.py
python scripts/generate_attachments.py
```

Inspect the extraction and reporting entry points before running them: live extraction uses API credit. Never substitute the truth database for the pipeline database merely to populate the UI.

## Vercel container deployment

`Dockerfile.vercel` packages the existing Streamlit server using Vercel's Container Images beta. In the import screen, use the container detection (or Other rather than the Python function preset), keep the repository root, and leave custom build/output commands unset. The HTTP server listens on port 80.

Only source files are copied into this container. The current image therefore has no populated procurement database or vendor documents. A populated demo needs a separately approved data package and deployment configuration. API credentials must be configured as Vercel environment variables; local `.env` files are excluded from both upload and build context.

Container deployment requires Vercel account access to the beta. Streamlit sessions and local SQLite edits are instance-local and are not durable across scaling/redeploys. Validate interactive pages on the deployed URL before using it for a demo. This configuration has not been built locally because Docker is not installed in the development environment.
