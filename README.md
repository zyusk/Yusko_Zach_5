# SAUS Digitizer

Digitizes national single-track railroad mileage, 1900–1948, from Statistical
Abstract of the United States PDFs — a table never released as machine-readable
data (see `docs/evidence_no_xls.md`).

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync
```

This environment's editable install isn't picked up by Python's `site` module
after `uv sync` — fix (re-run after any fresh sync, since it lives inside the
regenerated `.venv/`):

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

Builds the panel (`data/saus_railroad_mileage_1900_1950.csv`), validates it
against a live FRED benchmark, and writes everything under `outputs/`. Tests:
`uv run pytest` (asserts panel shape, download checksums, validation MAPE).

## How to Reproduce

Setup, then Run (above) regenerate the full pipeline —
`fetch_saus.py` → `ocr.py --step detect|extract` → `review.py` → `build.py`
— from the source PDFs. The deliverable CSV lands at
`data/saus_railroad_mileage_1900_1950.csv` (49 rows, 1900–1948); a
500-row-capped copy is at `data_snippets/saus_railroad_mileage_snippet.csv`.
One fixed snapshot, not mechanically re-derivable: `data/interim/manual_review.csv`,
built from live human review (real judgment calls OCR alone couldn't make).
Full prompt-by-prompt trail: `docs/ai_prompts.txt`.

## Scraper options

`fetch_saus.py` (Part A): `--start`/`--end` — first/last year to fetch, inclusive (default 1900/1950). `--raw-dir`/`--manifest` — directory for downloaded PDFs / path for the download manifest CSV (default `data/raw`/`outputs/download_manifest.csv`). `--sleep` — seconds to wait between downloads, politeness delay (default 1.0). `--api-key` — FRASER API key (default: read `FRASER_API_KEY` from environment/`.env`). `--skip-reachability-check` — skip the fast preflight check and go straight to the full harvest. `-v`/`--verbose` — enable debug logging.
