# SAUS Digitizer

Digitizes national single-track railroad mileage, 1900–1948, from Statistical
Abstract of the United States PDFs — a table never released as machine-readable
data (see `docs/evidence_no_xls.md`).

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/): `uv sync`.

If the editable install isn't on Python's path after sync (`.venv/`
regenerates each time), add:

```
cat > .venv/lib/python3.12/site-packages/sitecustomize.py << 'EOF'
import sys
from pathlib import Path
_src = Path(__file__).resolve().parents[4] / "src"
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))
EOF
```

## Run

```
python -m saus_digitizer.build --start 1900 --end 1950
```

Builds the panel, validates against a live FRED benchmark, writes everything
under `outputs/`. Tests: `uv run pytest`.

## Reproduce

Setup, then Run, regenerate the pipeline — `fetch_saus.py` →
`ocr.py --step detect|extract` → `review.py` → `build.py` — from source PDFs.
Deliverable: `data/saus_railroad_mileage_1900_1950.csv` (49 rows, 1900–1948);
capped copy at `data_snippets/saus_railroad_mileage_snippet.csv`. One fixed,
non-derivable snapshot: `data/interim/manual_review.csv` (live human review).
Full prompt trail: `docs/ai_prompts.txt`.

## Scraper options (`fetch_saus.py`, Part A)

`--start`/`--end` — year range (default 1900/1950). `--raw-dir`/`--manifest`
— PDF dir / manifest path (default `data/raw`, `outputs/download_manifest.csv`).
`--sleep` — delay between downloads (default 1.0s). `--api-key` — FRASER key
(default: `FRASER_API_KEY` env/`.env`). `--skip-reachability-check` — skip
preflight. `-v`/`--verbose` — debug logging.
