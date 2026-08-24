"""Part C -- Panel construction (and the Part E entry point).

Merges the two real interim sources into one standardized panel:

  - data/interim/manual_review.csv -- SOURCE OF TRUTH for any year it
    covers. Every row here was independently OCR-verified this session
    against the actual rasterized page (docs/ai_prompts.txt has the
    full trail), not just automated pattern-matching.
  - data/interim/single_track_mileage_interim.csv -- automated pipeline
    output, used ONLY for years NOT covered by manual review, AND only
    rows with a real value and an EMPTY excluded_reason. An automated
    row can have a populated `value` and still be unusable: 1949 and
    1950 both do (confirmed mislabeled -- the extractor grabbed a
    period-summary table's last column and assigned it to the volume's
    own cover year, but that column was actually 1-2 years earlier;
    see ocr.py's ExtractionResult docstring). excluded_reason, not an
    empty-value check, is what actually marks that.

Manual review currently covers 1900-1948 continuously (49 years,
including 1927/1944/1945 -- three years with no SAUS volume ever
published at all, filled in from OTHER volumes' own trailing-history
tables, something the per-year automated pipeline could never have
produced on its own). 1949 and 1950 are NOT in the output panel --
deliberately, not silently: both years' only automated values are
confirmed wrong, and no manual reading has been done for either yet.
This is disclosed in panel_summary.json's own "years_excluded" field,
not just left as an absent row with no explanation -- see findings.md
and validate.py for the fuller Part D methodological discussion.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

from saus_digitizer.ocr import PROJECT_ROOT

MANUAL_REVIEW_PATH = PROJECT_ROOT / "data" / "interim" / "manual_review.csv"
AUTOMATED_INTERIM_PATH = PROJECT_ROOT / "data" / "interim" / "single_track_mileage_interim.csv"
PANEL_OUTPUT_PATH = PROJECT_ROOT / "data" / "saus_railroad_mileage_1900_1950.csv"
PANEL_SUMMARY_PATH = PROJECT_ROOT / "outputs" / "panel_summary.json"
SUMMARY_STATS_PATH = PROJECT_ROOT / "outputs" / "summary_stats.csv"

PANEL_FIELDNAMES = ["year", "single_track_mileage", "source", "page_number", "notes"]

# Matches ocr.py's PLAUSIBLE_VALUE_RANGE -- the same bound this whole
# pipeline has used throughout to reject clearly-wrong OCR misreads, not
# a fresh statistical bound invented here.
ADMISSIBLE_RANGE = (140_000, 300_000)

KNOWN_EXCLUDED_YEARS = {
    1949: (
        "Automated value confirmed mislabeled (saus_1949.pdf Table 587 only reports "
        "through 1947 -- the extracted figure is real 1947 data, not 1949; see "
        "single_track_mileage_interim.csv's excluded_reason for the full diagnosis). "
        "No manual review has been done for this year yet."
    ),
    1950: (
        "Automated value confirmed mislabeled (saus_1950.pdf Table 591 only reports "
        "through 1948 -- the extracted figure is real 1948 data, a Class I subset at "
        "that; see single_track_mileage_interim.csv's excluded_reason for the full "
        "diagnosis). No manual review has been done for this year yet."
    ),
}


@dataclass
class PanelRow:
    year: int
    single_track_mileage: float
    source: str  # "manual" | "automated"
    page_number: str
    notes: str


def _load_manual_rows(path: Path) -> dict[int, PanelRow]:
    if not path.exists():
        return {}
    rows: dict[int, PanelRow] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            year = int(row["year"])
            notes = row["notes"].strip()
            verified_by = row["verified_by"].strip()
            combined_notes = f"Verified by {verified_by}. {notes}".strip()
            rows[year] = PanelRow(
                year=year,
                single_track_mileage=float(row["value"]),
                source="manual",
                page_number=row["page_number"],
                notes=combined_notes,
            )
    return rows


def _load_automated_rows(path: Path) -> dict[int, PanelRow]:
    """Only rows with a real value AND no excluded_reason -- see module
    docstring for why both checks are needed, not just "value present"."""
    if not path.exists():
        return {}
    rows: dict[int, PanelRow] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            value = row.get("value", "").strip()
            excluded = row.get("excluded_reason", "").strip()
            if not value or excluded:
                continue
            year = int(row["year"])
            source_pattern = row.get("source_pattern", "").strip()
            rows[year] = PanelRow(
                year=year,
                single_track_mileage=float(value),
                source="automated",
                page_number=row["page_number"],
                notes=f"Automated extraction. Source line: {source_pattern[:200]}",
            )
    return rows


def build_panel(
    manual_path: Path = MANUAL_REVIEW_PATH,
    automated_path: Path = AUTOMATED_INTERIM_PATH,
    start_year: int | None = None,
    end_year: int | None = None,
) -> list[PanelRow]:
    """Merges manual (authoritative) and automated (fallback-only)
    sources into one chronological panel. Manual review wins for any
    year it covers -- it's independently OCR-verified against the real
    page, not just pattern-matched -- automated fills in only years
    manual review hasn't reached, and only when that automated row is
    itself still valid (see _load_automated_rows). start_year/end_year,
    if given, filter the merged result to that inclusive range."""
    manual = _load_manual_rows(manual_path)
    automated = _load_automated_rows(automated_path)

    merged: dict[int, PanelRow] = dict(automated)  # automated first...
    merged.update(manual)  # ...manual overrides, per "source of truth"

    years = sorted(merged)
    if start_year is not None:
        years = [y for y in years if y >= start_year]
    if end_year is not None:
        years = [y for y in years if y <= end_year]
    return [merged[y] for y in years]


def _format_value(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def write_panel_csv(rows: list[PanelRow], output_path: Path = PANEL_OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PANEL_FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "year": r.year,
                    "single_track_mileage": _format_value(r.single_track_mileage),
                    "source": r.source,
                    "page_number": r.page_number,
                    "notes": r.notes,
                }
            )


def _git_commit_hash() -> str | None:
    """None (not a fabricated placeholder) when there's genuinely no git
    repository -- confirmed live this project has never had one
    initialized (`git status` fails all the way up the directory tree)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return None


def write_panel_summary(
    rows: list[PanelRow],
    start_year: int,
    end_year: int,
    output_path: Path = PANEL_SUMMARY_PATH,
) -> dict:
    row_count = len(rows)
    nominal_year_count = end_year - start_year + 1
    na_share = round(1 - (row_count / nominal_year_count), 4) if nominal_year_count else None

    manual_count = sum(1 for r in rows if r.source == "manual")
    automated_count = sum(1 for r in rows if r.source == "automated")

    present_years = {r.year for r in rows}
    years_excluded = {
        str(y): reason
        for y, reason in KNOWN_EXCLUDED_YEARS.items()
        if start_year <= y <= end_year and y not in present_years
    }

    summary = {
        "row_count": row_count,
        "nominal_range": f"{start_year}-{end_year}",
        "nominal_year_count": nominal_year_count,
        "na_share": na_share,
        "manual_rows": manual_count,
        "automated_rows": automated_count,
        "years_excluded": years_excluded,
        "git_commit": _git_commit_hash(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as fh:
        json.dump(summary, fh, indent=2)
    return summary


def write_summary_stats(rows: list[PanelRow], output_path: Path = SUMMARY_STATS_PATH) -> None:
    values = [r.single_track_mileage for r in rows]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["field", "value"])
        writer.writerow(["variable", "single_track_mileage"])
        writer.writerow(
            [
                "definition",
                "Best-available annual national single-track-equivalent steam railroad "
                "mileage for the continental United States, in route-miles. Where an "
                "explicit 'Single track' column exists in the source SAUS/ICC table, that "
                "figure is used; otherwise the historically-conventional equivalent "
                "('first track', 'road operated', 'main track') is used and labeled as such "
                "in that row's notes -- per the SAUS tables' own footnote convention ('the "
                "term \"mileage\" signifies single-track mileage'), these are the same "
                "underlying measurement concept under different era-specific names, not "
                "interchangeable metrics being silently conflated.",
            ]
        )
        writer.writerow(["n", len(values)])
        if values:
            writer.writerow(["min", _format_value(min(values))])
            writer.writerow(["max", _format_value(max(values))])
            writer.writerow(["mean", round(statistics.mean(values), 2)])
            writer.writerow(["median", round(statistics.median(values), 2)])
        writer.writerow(["admissible_range_low", ADMISSIBLE_RANGE[0]])
        writer.writerow(["admissible_range_high", ADMISSIBLE_RANGE[1]])
        range_note = (
            f"Real values across this panel run {_format_value(min(values))}-"
            f"{_format_value(max(values))}; {ADMISSIBLE_RANGE[0]:,}-{ADMISSIBLE_RANGE[1]:,} "
            "is the wider bound used throughout this pipeline's own extraction/validation "
            "code (ocr.py's PLAUSIBLE_VALUE_RANGE) to reject clearly-wrong OCR misreads, "
            "not a tight statistical bound on this specific panel."
            if values
            else "No values in panel."
        )
        writer.writerow(["admissible_range_rationale", range_note])


DATA_SNIPPETS_DIR = PROJECT_ROOT / "data_snippets"
SNIPPET_OUTPUT_PATH = DATA_SNIPPETS_DIR / "saus_railroad_mileage_snippet.csv"
SNIPPET_MAX_ROWS = 500
SNIPPET_RANDOM_SEED = 123


def write_data_snippet(rows: list[PanelRow], output_path: Path = SNIPPET_OUTPUT_PATH) -> int:
    """Brief's Part 2 submission rule: data_snippets/ gets AT MOST 500
    rows from the final panel -- np.random.seed(123) + .sample(500), or
    a contiguous slice, either is allowed.

    Real, disclosed fact about this specific panel: it only has 49 rows
    (1900-1948; 1949-1950 are a documented gap, not silently missing --
    see KNOWN_EXCLUDED_YEARS). A "snippet" of "at most 500 rows" is
    therefore, unavoidably, the ENTIRE panel here -- not a genuine
    subsample. Given that, a random .sample() would only shuffle row
    ORDER without actually subsampling anything, which is worse for a
    chronological time series than it looks (a reader opening this file
    would see years out of order for no real reason). Uses the
    contiguous-slice option instead, in the panel's own natural year
    order -- the brief explicitly allows this as an alternative to
    .sample(), and it's the more useful choice specifically because
    there's no real subsampling decision to make on a panel this small.
    np.random.seed(123) is still set, matching the brief's literal
    instruction, even though nothing here ends up depending on it.
    """
    import numpy as np
    import pandas as pd

    np.random.seed(SNIPPET_RANDOM_SEED)  # per the brief -- see docstring for why it's a no-op here

    df = pd.DataFrame(
        {
            "year": [r.year for r in rows],
            "single_track_mileage": [r.single_track_mileage for r in rows],
            "source": [r.source for r in rows],
            "page_number": [r.page_number for r in rows],
            "notes": [r.notes for r in rows],
        }
    ).sort_values("year")

    snippet = df.iloc[:SNIPPET_MAX_ROWS]  # contiguous slice -- a no-op cap here (49 < 500)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    snippet.to_csv(output_path, index=False)
    return len(snippet)


def run_build(
    start_year: int = 1900, end_year: int = 1950, skip_validate: bool = False
) -> list[PanelRow]:
    rows = build_panel(start_year=start_year, end_year=end_year)
    write_panel_csv(rows)
    summary = write_panel_summary(rows, start_year, end_year)
    write_summary_stats(rows)

    print(f"Panel written: {PANEL_OUTPUT_PATH} ({summary['row_count']} rows)")
    print(f"  Manual: {summary['manual_rows']}, Automated: {summary['automated_rows']}")
    if summary["years_excluded"]:
        print(f"  Excluded (documented, not silent): {', '.join(summary['years_excluded'])}")
    print(f"  NA-share vs. nominal {summary['nominal_range']}: {summary['na_share']}")
    print(f"Summary: {PANEL_SUMMARY_PATH}")
    print(f"Stats: {SUMMARY_STATS_PATH}")

    snippet_rows = write_data_snippet(rows)
    print(f"Snippet: {SNIPPET_OUTPUT_PATH} ({snippet_rows} rows)")

    if not skip_validate:
        # Part D -- lives in validate.py, imported here (not inlined) so
        # build.py stays panel-construction-only. Needs network access
        # (fetches a live FRED series); --skip-validate exists for the
        # rare case that's unavailable and the panel still needs
        # rebuilding on its own.
        from saus_digitizer.validate import run_validate

        run_validate()

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the SAUS long-run panel.")
    parser.add_argument("--start", type=int, default=1900)
    parser.add_argument("--end", type=int, default=1950)
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip Part D (fetches a live FRED series -- needs network access).",
    )
    args = parser.parse_args()
    run_build(args.start, args.end, skip_validate=args.skip_validate)


if __name__ == "__main__":
    main()
