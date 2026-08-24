# How to Reproduce

This repo publishes `data/saus_railroad_mileage_1900_1950.csv` — U.S. national
single-track railroad mileage, 1900–1948, digitized from the *Statistical
Abstract of the United States* (FRASER title 66). This series has never been
released as machine-readable CSV/XLS anywhere else; see `docs/evidence_no_xls.md`
for the dark-table proof.

## Use the CSV directly

`data/saus_railroad_mileage_1900_1950.csv` — columns: `year`,
`single_track_mileage`, `source`, `page_number`, `notes`.

## Regenerate it yourself

```
uv sync
python -m saus_digitizer.build --start 1900 --end 1950
```

Full pipeline (`fetch_saus.py` → `ocr.py` → `review.py` → `build.py`) is in
`README.md`; the complete development/prompt trail is in `docs/ai_prompts.txt`.
