# VQI interface

Run from the repository root:

```powershell
.venv/Scripts/python.exe -m streamlit run app/main.py
```

The entry experience is a portfolio case study, with a shared ivory, charcoal,
forest-green and terracotta design system. All visuals and fonts are local; no
external asset request is required.

## Portfolio

- **The case study:** product thesis, working-demo entry points and recorded evaluation evidence.
- **Product strategy:** user job, prioritization, trade-offs, proposed pilot measures and next experiments.
- **Agent architecture:** pipeline responsibilities, uncertainty states and an interactive read-only tool explorer.

## Working prototype

The six-step flow covers the sourcing brief, supplier evidence, review queue,
clarifications, bid comparison and award decision. Ask the AI analyst exposes its
tool calls; Evaluation lab distinguishes saved benchmarks from current usage.

The tool explorer on Agent architecture executes fixed examples through the real
`src.analyst.Store` methods. It does not instantiate an AI provider. Ask remains a
live provider-backed feature and requires a configured API key.

## Files

- `design.py` and `theme.css`: shared presentation primitives and responsive styles.
- `overview.py`: portfolio landing experience; metrics come from the saved snapshot.
- `case_study.py`: product strategy, architecture and API-free tool inspection.
- `story.py`: guided sourcing workflow.
- `main.py`: navigation and the working procurement views.

## Data and validation

The UI reads the pipeline `procurement.db` by default. `PROCUREMENT_DB` selects
another database. Frozen truth is never a valid UI source. Missing datasets and
snapshots produce explicit empty states; benchmark numbers are not invented.

```powershell
.venv/Scripts/python.exe -m pytest tests app -q
```

Portfolio integration tests cover navigation, missing data, real read-only tool
results and refusal to use the frozen reference. The existing suite covers the
procurement and award logic. Browser QA is still needed to confirm the final
visual layout on desktop and mobile.
