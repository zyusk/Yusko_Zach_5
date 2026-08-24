"""Manual review CLI -- fills in what the automated OCR pipeline (ocr.py)
honestly couldn't extract, rather than leaving 45/48 years as silent gaps
or trying to force more automation out of an approach already proven to
fail unpredictably on this table shape (see ocr.py's disabled positional
extractor and docs/ai_prompts.txt for exactly why that path was rejected).

Writes to a SEPARATE file, data/interim/manual_review.csv -- never touches
single_track_mileage_interim.csv. Keeping automated and manual results in
separate files (rather than merging now) is deliberate: Part C's panel
build is where they actually get combined, and every row's provenance
(auto-extracted vs. a human read it off the rasterized page) needs to
survive that merge, not get flattened away before it does.

Usage (verified working):
    uv run python src/saus_digitizer/review.py
    (or click Run on this file directly in your IDE -- same thing, see
    the sys.path note below)

NOT `python -m saus_digitizer.review` -- confirmed broken project-wide
as of this writing (docs/ai_prompts.txt has the full diagnosis): this
project's editable install doesn't get picked up by Python's site
module in this environment, so -m can't locate the package at all,
regardless of this file's own sys.path fix (which only runs once the
file is already executing -- too late for -m's own module lookup).
Real, open issue that will need solving before Part E's build.py, which
the brief requires to support the exact invocation
`python -m saus_digitizer.build --start 1900 --end 1950` -- not solved
here since build.py doesn't exist yet and this file was never a
brief-required entry point.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Makes `python review.py` (an IDE's "Run current file" button, no -m, no
# PYTHONPATH) work, not just `python -m saus_digitizer.review`. Confirmed
# live this is genuinely needed here, not just defensive boilerplate: even
# after a clean `uv sync`, this project's editable install
# (.venv/.../site-packages/saus_digitizer.pth, pointing at src/) does not
# get picked up by Python's site module in this environment -- checked
# directly with `site.addsitedir()` on the installed site-packages dir,
# which added nothing to sys.path. Root cause not fully chased down
# (a uv_build-specific editable-install quirk, not a missing dependency
# or a bad path), but this bootstrap sidesteps it entirely by adding
# src/ to sys.path directly, keyed off this file's own location rather
# than relying on the broken mechanism.
_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from saus_digitizer.ocr import PROJECT_ROOT, rasterize_page, resolve_path  # must follow the sys.path fix above

# Reuses ocr.py's PROJECT_ROOT (not a second, separately-computed one) so
# both modules agree on the same root regardless of invocation cwd --
# confirmed live this matters: a bare relative default here
# (Path("data/interim/...")) resolved against Positron's actual working
# directory, not Yusko_Zach_5/, producing exactly the "not found" error
# that prompted this fix.
INTERIM_PANEL_PATH = PROJECT_ROOT / "data" / "interim" / "single_track_mileage_interim.csv"
PAGE_MATCH_REPORT_PATH = PROJECT_ROOT / "outputs" / "table_page_matches.json"
MANUAL_REVIEW_PATH = PROJECT_ROOT / "data" / "interim" / "manual_review.csv"
MANUAL_REVIEW_FIELDNAMES = ["year", "value", "page_number", "verified_by", "notes"]

DEFAULT_REVIEWER = "Zach Yusko"

# System temp dir, not a project folder -- these PNGs are throwaway view
# copies, regenerated fresh from the source PDFs on every run, not data
# that needs to survive or be committed. Matches the brief's own
# "temp review folder" framing.
REVIEW_IMAGE_ROOT = Path(tempfile.gettempdir()) / "saus_digitizer_review"


def _load_pending_years(interim_path: Path) -> list[str]:
    """Years where the automated pipeline left value blank, in file order."""
    with interim_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [row["year"] for row in rows if not row["value"].strip()]


def _load_automated_row(year: str, interim_path: Path) -> dict | None:
    """Returns the automated pipeline's existing row for `year`, if any --
    used by --year force-review so a reviewer double-checking a KNOWN
    imprecise extraction (e.g. 1950's 226,704, disclosed as "Class I
    railways," a subset, not the true "All railways, road, first track"
    total on the same page) sees what automation found before consciously
    confirming or correcting it, rather than re-reading the page blind
    with no context that anything was already there."""
    if not interim_path.exists():
        return None
    with interim_path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row["year"] == year:
                return row if row["value"].strip() else None
    return None


def _load_already_reviewed_years(manual_path: Path) -> set[str]:
    """Years already answered (value OR 'unreadable') in a prior session --
    both count as answered and get skipped; only 'skip' responses (never
    written at all) leave a year eligible to be asked again."""
    if not manual_path.exists():
        return set()
    with manual_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {row["year"] for row in rows}


def _append_manual_row(row: dict, manual_path: Path) -> None:
    """Appends immediately, one row at a time -- same reliability lesson as
    the rest of this pipeline (fetch_saus.py's manifest, ocr.py's panel
    CSV): an interactive session that gets Ctrl+C'd or closed mid-review
    must not lose answers already given."""
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = manual_path.exists()
    with manual_path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANUAL_REVIEW_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _ordered_matches(entry: dict) -> list[dict]:
    """Candidate matches, strong-pattern ones first -- same priority order
    used by ocr.py's extractor, reused here (not re-derived differently)
    so the page shown/opened first is the one the automated pipeline
    itself would have tried first."""
    from saus_digitizer.ocr import STRONG_PATTERN_NAMES

    return sorted(
        entry["matches"],
        key=lambda m: 0 if STRONG_PATTERN_NAMES & set(m["matched_patterns"]) else 1,
    )


def _rasterize_candidates(year: str, entry: dict, out_dir: Path) -> list[tuple[int, Path]]:
    """Rasterizes every candidate page for this year (not just the
    top-priority one) -- the whole point of manual review is that a human
    can judge a page the automated heuristics rejected or ranked low (the
    1926 footnote catch and the state-table "United States" summary row
    both showed real cases where the "obviously right" page wasn't the
    first candidate). Ordered strong-pattern-matches first so the most
    likely page opens first without hiding the rest."""
    pdf_path = resolve_path(entry["pdf"])
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for match in _ordered_matches(entry):
        pno = match["page_number"]
        img = rasterize_page(pdf_path, pno)
        out_path = out_dir / f"{year}_p{pno}.png"
        img.save(out_path)
        results.append((pno, out_path))
    return results


def _open_images(paths: list[Path]) -> None:
    """`open` on macOS -- opening several images in one call groups them
    into a single Preview window with a thumbnail strip, which is exactly
    the right UX here (browse candidates, not N separate windows)."""
    if not paths:
        return
    try:
        subprocess.run(["open", *[str(p) for p in paths]], check=False)
    except FileNotFoundError:
        print(f"  (Couldn't auto-open -- 'open' not found. View manually: {paths[0].parent})")


def _parse_mileage_value(raw: str) -> float | None:
    """Parses a manually-entered value as a real number, not just a whole
    integer. Original bug: this used str.isdigit(), which rejects anything
    with a decimal point at all -- and these tables genuinely report
    decimals sometimes (confirmed real, e.g. 1910's explicit
    single/second/third-track table found earlier this session: "749.51",
    "88,711.38"), so a valid manual reading like "189294.66" was being
    rejected outright, not just formatted oddly. Strips thousands-
    separator commas before parsing. A negative result is rejected too --
    not because it fails to parse (it parses fine), but because mileage
    can never be negative in this domain; that's a real-world sanity
    check, not a parsing bug, so it's kept as a separate, explicit check
    rather than silently folded into the parse failure case."""
    cleaned = raw.replace(",", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def _format_mileage_value(value: float) -> str:
    """Whole-number values write as plain integers ("258362"), matching
    the automated pipeline's own CSV formatting -- only a genuine
    fractional reading gets the decimal point kept ("189294.66")."""
    if value == int(value):
        return str(int(value))
    return str(value)


def _prompt_value(year: str) -> str | None:
    """Returns the raw user response for the main prompt, or None if the
    user interrupted (Ctrl+C/Ctrl+D) -- treated the same as typing 'quit'."""
    try:
        return input(
            f"\n[{year}] Correct value, 'unreadable' (real gap, stop asking), "
            "'skip' (ask again later), or 'quit': "
        ).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None


def _prompt_optional(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None


BATCH_SIZE = 10


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _prompt_and_record_year(year: str, reviewer: str, manual_path: Path) -> str:
    """The interactive value/unreadable/skip/quit loop for one year --
    deliberately does NOT rasterize or open anything itself. That happens
    once per BATCH now (review_batch), not once per year -- these tables
    routinely show several years of trailing history on a single page
    (confirmed real: both 1949's and 1950's own tables reach back to
    1925), so re-rasterizing and re-opening a near-identical page for
    every single year in a 10-year batch was pure redundant work. Returns
    'answered', 'skipped', or 'quit'."""
    while True:
        raw = _prompt_value(year)
        if raw is None or raw.lower() == "quit":
            return "quit"
        lowered = raw.lower()

        if lowered == "skip":
            return "skipped"

        if lowered == "unreadable":
            notes = _prompt_optional("Notes (optional -- why it's unreadable): ")
            if notes is None:
                return "quit"
            _append_manual_row(
                {
                    "year": year,
                    "value": "",
                    "page_number": "",
                    "verified_by": reviewer,
                    "notes": notes or "unreadable",
                },
                manual_path,
            )
            print(f"  Recorded {year} as unreadable (real gap).")
            return "answered"

        parsed_value = _parse_mileage_value(raw)
        if parsed_value is None:
            print(
                f"  '{raw}' isn't a valid non-negative number, 'unreadable', 'skip', or 'quit' -- try again."
            )
            continue
        digits = _format_mileage_value(parsed_value)

        page_raw = _prompt_optional(f"[{year}] Which page number is this from? ")
        if page_raw is None:
            return "quit"
        if not page_raw.isdigit():
            print(f"  '{page_raw}' isn't a page number -- try again from the top.")
            continue

        notes = _prompt_optional("Notes (optional): ")
        if notes is None:
            return "quit"

        _append_manual_row(
            {
                "year": year,
                "value": digits,
                "page_number": page_raw,
                "verified_by": reviewer,
                "notes": notes,
            },
            manual_path,
        )
        print(f"  Recorded {year} = {digits} (p{page_raw}).")
        return "answered"


def review_batch(years: list[str], page_matches: dict, reviewer: str, manual_path: Path) -> str:
    """Rasterizes and opens ONE representative year's candidate pages for
    the whole batch, then prompts through every year in the batch against
    those same already-open images. The representative year is the LAST
    (most recent) one in the batch, not the first -- a later SAUS volume's
    own table is the one more likely to show trailing history reaching
    back to cover the earlier years in the batch too (the exact pattern
    confirmed real on both 1949 p553 and 1950 p509 this session: each
    volume's own table reached back to 1925). Returns 'quit' if the user
    quit partway through the batch, else 'done'.

    This does NOT guarantee every year in the batch is actually visible
    in the representative page(s) -- some volumes show fewer trailing
    years than others. If a given year's value genuinely isn't on the
    opened page, 'skip' or 'unreadable' are still there -- this batches
    the common case (it usually is visible) without blocking the
    uncommon one."""
    rep_year = years[-1]
    rep_entry = page_matches.get(rep_year)
    if rep_entry is None:
        print(
            f"\nWARNING: {rep_year} (this batch's representative year) has no entry in "
            "the page-match report -- skipping this whole batch."
        )
        return "done"

    print(f"\n{'#' * 70}")
    print(f"BATCH {years[0]}-{years[-1]} ({len(years)} years) -- showing {rep_year}'s candidate page(s)")
    print(f"{'#' * 70}")
    for match in _ordered_matches(rep_entry):
        patterns = ", ".join(match["matched_patterns"])
        print(f"  p{match['page_number']}: {patterns}")
    print(
        f"\n{rep_year}'s table likely shows trailing history covering the other years in "
        "this batch too -- rasterizing and opening once for the whole batch, not once per year."
    )

    image_dir = REVIEW_IMAGE_ROOT / rep_year
    rasterized = _rasterize_candidates(rep_year, rep_entry, image_dir)
    _open_images([path for _, path in rasterized])

    for year in years:
        outcome = _prompt_and_record_year(year, reviewer, manual_path)
        if outcome == "quit":
            return "quit"
        if outcome == "skipped":
            print(f"  Skipped {year} -- will ask again next run.")
    return "done"


def review_single_year(
    year: str,
    page_matches_path: Path = PAGE_MATCH_REPORT_PATH,
    interim_path: Path = INTERIM_PANEL_PATH,
    manual_path: Path = MANUAL_REVIEW_PATH,
    reviewer: str = DEFAULT_REVIEWER,
) -> None:
    """Force-reviews ONE specific year, regardless of whether it already
    has an automated value or a prior manual answer -- for exactly the
    case that prompted this: a year whose automated extraction is a
    KNOWN, disclosed imprecision, not a genuine gap (1950's 226,704 is
    "Class I railways," a subset -- the real "All railways, road, first
    track" figure sits on the same page, p509, but its numbers got
    scrambled by OCR's column linearization; full story in
    docs/ai_prompts.txt). Prints whatever automation already found first,
    so the reviewer is consciously confirming/correcting a specific known
    number, not re-reading the page blind.

    Still never touches single_track_mileage_interim.csv -- only appends
    to manual_review.csv, same as the normal batch flow. Calling this on
    a year already in manual_review.csv adds a SECOND row for it rather
    than overwriting the first; that's a real, open question for Part C's
    merge step to resolve (most-recent-row-wins, or flag the conflict),
    not something silently decided here.
    """
    with page_matches_path.open() as fh:
        page_matches = json.load(fh)
    entry = page_matches.get(year)
    if entry is None:
        raise RuntimeError(f"{year} has no entry in {page_matches_path}.")

    automated = _load_automated_row(year, interim_path)
    print(f"\n{'#' * 70}")
    print(f"FORCED RE-REVIEW: {year}")
    print(f"{'#' * 70}")
    if automated:
        print(
            f"Automated pipeline's current value: {automated['value']} "
            f"(page {automated['page_number']}, source: {automated['source_pattern']!r})"
        )
    else:
        print("(No automated value on file for this year.)")
    for match in _ordered_matches(entry):
        patterns = ", ".join(match["matched_patterns"])
        print(f"  p{match['page_number']}: {patterns}")

    image_dir = REVIEW_IMAGE_ROOT / year
    rasterized = _rasterize_candidates(year, entry, image_dir)
    _open_images([path for _, path in rasterized])

    _prompt_and_record_year(year, reviewer, manual_path)


def run_review(
    interim_path: Path = INTERIM_PANEL_PATH,
    page_matches_path: Path = PAGE_MATCH_REPORT_PATH,
    manual_path: Path = MANUAL_REVIEW_PATH,
    reviewer: str = DEFAULT_REVIEWER,
) -> None:
    if not interim_path.exists():
        raise RuntimeError(
            f"{interim_path} not found -- run "
            "`uv run python src/saus_digitizer/ocr.py --step extract` first."
        )
    if not page_matches_path.exists():
        raise RuntimeError(
            f"{page_matches_path} not found -- run "
            "`uv run python src/saus_digitizer/ocr.py --step detect` first."
        )

    pending_years = _load_pending_years(interim_path)
    already_reviewed = _load_already_reviewed_years(manual_path)
    with page_matches_path.open() as fh:
        page_matches = json.load(fh)

    todo = [year for year in pending_years if year not in already_reviewed]

    print(
        f"{len(pending_years)} years have no automated value; "
        f"{len(already_reviewed)} already reviewed; {len(todo)} left to review."
    )
    if not todo:
        print("Nothing to do.")
        return

    batches = _chunk(todo, BATCH_SIZE)
    print(f"Grouped into {len(batches)} batch(es) of up to {BATCH_SIZE} years each.")

    for batch in batches:
        outcome = review_batch(batch, page_matches, reviewer, manual_path)
        if outcome == "quit":
            print("\nStopping. Re-run this script to pick up where you left off.")
            return

    print(f"\nAll {len(todo)} pending years reviewed this session.")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manually review years the automated OCR pipeline (ocr.py) couldn't extract."
    )
    parser.add_argument("--interim", type=Path, default=INTERIM_PANEL_PATH)
    parser.add_argument("--page-matches", type=Path, default=PAGE_MATCH_REPORT_PATH)
    parser.add_argument("--manual-review", type=Path, default=MANUAL_REVIEW_PATH)
    parser.add_argument(
        "--reviewer", type=str, default=DEFAULT_REVIEWER, help="Name recorded in verified_by"
    )
    parser.add_argument(
        "--year",
        type=str,
        default=None,
        help="Force-review one specific year, even if it already has an automated value "
        "or a prior manual answer (e.g. a year with a known, disclosed imprecision like "
        "1950's 'Class I railways' subset value). Skips the normal pending-years batch flow.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    if args.year:
        review_single_year(args.year, args.page_matches, args.interim, args.manual_review, args.reviewer)
    else:
        run_review(args.interim, args.page_matches, args.manual_review, args.reviewer)


if __name__ == "__main__":
    main()
