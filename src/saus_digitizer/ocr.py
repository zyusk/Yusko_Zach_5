"""Part B -- Page Detection, OCR & Table Extraction.

MAJOR FINDING that changes this module's whole premise from what Step 1's
scaffold (and the brief) assumed: these FRASER-hosted PDFs are NOT
image-only with no text layer. Checked directly, on real downloaded
1900-1950 volumes (docs/ai_prompts.txt has the full trail) -- every
sampled page has substantial extractable text via fitz.Page.get_text(),
meaning FRASER has already run OCR and embedded a text layer on these
scans. This does NOT make Part B.2's "rasterize + pytesseract"
requirement go away -- that's still required by the brief's own learning
objectives and rubric ("Apply open-source OCR...", "B -- OCR accuracy")
regardless of a pre-existing text layer of unknown quality, and that
existing layer is visibly noisy (see finding 2 below) -- not something
to silently substitute for doing the assignment's actual OCR step. But
it means detect_table_pages() below can search the existing text layer
directly instead of rasterizing+OCR-ing every page just to find which
one to OCR properly next.

Note from the prompt plan, still correct: camelot/tabula-py won't work
here even though a text layer exists -- these are OCR text layers with
no underlying vector table/line structure for a lattice/stream parser to
find, not a normal digitally-typeset PDF. Part B.2-3 is still a
rasterize -> pytesseract -> parse-the-OCR-output pipeline.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd
import pytesseract
from PIL import Image

logger = logging.getLogger("saus_digitizer.ocr")

# Anchored to this file's own location (src/saus_digitizer/ocr.py ->
# parents[2] == Yusko_Zach_5/), NOT left as bare relative paths like
# Path("data/raw"). Confirmed live this distinction matters: every path
# in this module worked throughout development only because Bash-tool
# testing always `cd`'d into Yusko_Zach_5/ first -- Positron's actual Run
# button (via IPython's %run) does not share that cwd, and a bare
# relative default silently resolves against whatever directory the
# session happens to be in, not the project root. Same fix already
# applied once for fetch_saus.py's .env loading; generalized here to
# every path default in this module.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RAW_DIR = PROJECT_ROOT / "data" / "raw"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
PAGE_MATCH_REPORT_PATH = PROJECT_ROOT / "outputs" / "table_page_matches.json"
INTERIM_PANEL_PATH = PROJECT_ROOT / "data" / "interim" / "single_track_mileage_interim.csv"
RASTER_DPI = 300


def resolve_path(raw: str | Path) -> Path:
    """Resolves a path that may have been READ BACK from data (e.g. the
    "pdf" field in table_page_matches.json, written by a possibly-earlier
    run) against PROJECT_ROOT if it's relative, rather than against
    whatever the current process's cwd happens to be. A relative path
    stored in a JSON/CSV file means nothing stable on its own -- it must
    be resolved the same way every time, regardless of where the
    consuming script is later invoked from."""
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path

# Confirmed by directly reading real 1900-1950 volumes in this session
# (docs/ai_prompts.txt has the search trail) -- NOT the brief's one
# example ("Mileage of Road," "Railway Mileage," "Railroads -- Mileage
# Operated"), which doesn't literally match any real caption found.
# Deliberately broad and overlapping rather than one pattern per decade
# -- caption wording drifts within a single volume too (the main mileage
# table, a rail-mail-service mileage table, and an electric-railway
# mileage table all share vocabulary), so detect_table_pages() returns
# every match for human review rather than silently picking "the" page.
DEFAULT_CAPTION_PATTERNS: list[str] = [
    "MILEAGE OF RAILROADS",  # 1900: "No. 107.--MILEAGE of RAILROADS...by States"
    "MILEAGE OF ROAD",  # variant seen across early SAUS volumes
    "RAILWAY MILEAGE OWNED",  # 1930: "No. 433.--RAILWAY MILEAGE OWNED: By States"
    "RAILWAY MILEAGE",  # broad net -- catches "...OWNED", "...OPERATED", etc.
    "RAILROAD MILEAGE",
    "RAILROADS MILEAGE",
    "LENGTH OF SINGLE",  # 1910: "Length of single, second, third, and fourth tracks"
    "SINGLE, SECOND",  # same table, alternate phrasing/OCR line-break point
    "SINGLE TRACK",  # generic explicit anchor
    "FIRST TRACK",  # 1950: terminology shifted from "single" to "first" track
    "CLASSES OF TRACK",  # 1930 table-of-contents alternate wording
    "MILEAGE OPERATED",
    "TRACK OPERATED",
    "TRACK MILEAGE",
]


@dataclass
class TablePageMatch:
    page_number: int  # 0-indexed, matches fitz's page numbering directly
    matched_patterns: list[str]
    snippet: str  # first ~200 chars of the page's text, collapsed to one line, for a human glance


def _squash(text: str) -> str:
    """Strip ALL whitespace and uppercase.

    This, not a `\\s+`-tolerant regex, is what actually handles a real,
    repeated OCR artifact found while researching this: old SAUS
    letter-spaced small-caps table headers get OCR'd as individual
    space-separated characters. Real example, 1930 volume page 409:
        "R A I L W A Y   M I L E A G E   O W N E D :   B y   S t a t e s"
    `re.search(r"railway\\s+mileage", text)` does NOT match that -- there
    is a space between every letter, not just between words. Stripping
    all whitespace from both the page text and each candidate phrase
    before a substring check collapses normal spacing and this
    letter-spacing artifact to the same form and matches both
    identically. (A pairwise letter-squashing normalizer was tried
    first and rejected -- once every letter is single-spaced, real word
    boundaries are genuinely ambiguous to recover; substring matching on
    fully-squashed text sidesteps that ambiguity instead of guessing at it.)
    """
    return re.sub(r"\s+", "", text).upper()


def detect_table_pages(
    pdf_path: Path,
    caption_patterns: list[str] | None = None,
) -> list[TablePageMatch]:
    """Return every page whose OCR'd text matches any caption pattern.

    Returns match evidence (which pattern(s) hit, a text snippet) per
    page, not a bare list[int] -- a deliberate change from Step 1's
    original scaffold guess at this signature. A caption "match" on a
    500-1000-page, 50-year-old OCR'd volume needs a human glance before
    Part B.2 spends time rasterizing/re-OCR-ing the wrong page; a bare
    page-number list would hide exactly the kind of unverified result
    this whole project is built to avoid trusting blindly. Get the plain
    page numbers with `[m.page_number for m in detect_table_pages(...)]`.

    caption_patterns defaults to DEFAULT_CAPTION_PATTERNS (confirmed
    against real volumes -- see module docstring) but can be overridden
    or extended by the caller, e.g. after visually confirming a new
    variant in a specific year that isn't in the default list yet. Don't
    treat the default list as exhaustive across all 48 years -- it
    covers every variant confirmed so far, not necessarily every variant
    that exists.
    """
    patterns = caption_patterns if caption_patterns is not None else DEFAULT_CAPTION_PATTERNS
    squashed_patterns = [(p, _squash(p)) for p in patterns]

    doc = fitz.open(pdf_path)
    matches: list[TablePageMatch] = []
    try:
        for page_number in range(len(doc)):
            text = doc[page_number].get_text()
            if not text.strip():
                continue  # a handful of pages (plates, blank leaves) have no text layer at all
            squashed = _squash(text)
            hit_patterns = [p for p, sqp in squashed_patterns if sqp in squashed]
            if hit_patterns:
                snippet = " ".join(text.split())[:200]
                matches.append(
                    TablePageMatch(page_number=page_number, matched_patterns=hit_patterns, snippet=snippet)
                )
    finally:
        doc.close()
    return matches


def _write_report(results: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as fh:
        json.dump(results, fh, indent=2)


def build_page_match_report(
    raw_dir: Path = RAW_DIR,
    output_path: Path = PAGE_MATCH_REPORT_PATH,
    caption_patterns: list[str] | None = None,
) -> dict:
    """Run detect_table_pages() against every saus_<year>.pdf in raw_dir and
    write one entry per year to output_path as JSON: {year: {pdf,
    candidate_pages, matches: [{page, matched_patterns, snippet}]}}.

    Written after every single year, not just at the end -- the same
    reliability lesson learned the hard way in fetch_saus.py (a run that
    crashed partway through 48 large downloads had silently thrown away
    the completed work because the manifest was only written once, at
    the end). A 48-volume, 500-1000+-page-each text scan is a real
    multi-minute run with the same risk profile; this makes it safe to
    interrupt and inspect partial results in outputs/table_page_matches.json
    at any point while it's running, not just after it finishes.
    """
    pdf_paths = sorted(raw_dir.glob("saus_*.pdf"))
    if not pdf_paths:
        raise RuntimeError(f"No saus_*.pdf files found in {raw_dir} -- run fetch_saus.py first.")

    results: dict[str, dict] = {}
    for pdf_path in pdf_paths:
        year = pdf_path.stem.replace("saus_", "")
        matches = detect_table_pages(pdf_path, caption_patterns)
        results[year] = {
            "pdf": str(pdf_path),
            "candidate_pages": [m.page_number for m in matches],
            "matches": [asdict(m) for m in matches],
        }
        _write_report(results, output_path)
        logger.info("%s: %d candidate pages -> %s", year, len(matches), [m.page_number for m in matches])

    no_hits = [year for year, r in results.items() if not r["candidate_pages"]]
    if no_hits:
        logger.warning(
            "%d/%d years had ZERO candidate pages -- caption patterns don't cover them yet: %s",
            len(no_hits), len(results), sorted(no_hits, key=int),
        )
    else:
        logger.info("All %d years had at least one candidate page.", len(results))

    return results


# --- Part B.2-3: rasterize + OCR + extract the national single-track value ---
#
# Real, confirmed findings from building this against actual 1910/1930/1950
# pages (docs/ai_prompts.txt has the full trail):
#
# 1. LABEL AND VALUE OFTEN LAND ON DIFFERENT OCR LINES. Real example, 1950
#    p518: the row label "Average miles of line (first track) op-" and its
#    numbers "erated ____ ... 259,646 | 257,098 | ... | 287, 824" are two
#    separate lines in pytesseract's plain-text output (word-wrap). A
#    same-line-only regex would miss this table entirely.
#
# 2. "SINGLE TRACK" CAN BE A FALSE POSITIVE FOR THE WRONG INDUSTRY. Real
#    example, 1950 p529: "Electric railway--miles of single track" is a
#    transit-industry table (streetcars/trolleys), not steam railroads --
#    the phrase matches but the table is measuring something else entirely.
#    Rejected via WRONG_INDUSTRY_MARKERS before any value is trusted from it.
#
# 3. EVEN THE CLEANEST TABLE FOUND STILL HAD A REAL OCR DIGIT MISREAD. The
#    1948 value on that same 1950 p518 table reads as 287,824 in the OCR
#    output; the true value is very likely 237,824 (2/3 digit confusion --
#    exactly the classic misread the Prompt Plan's Step 5 checkpoint warned
#    about), visible only because it breaks the smooth declining trend of
#    the surrounding period values. This is NOT corrected here -- ocr_raw_text
#    is preserved in full specifically so a human (or Part D's FRED
#    cross-check) can catch it, rather than this function silently "fixing"
#    a number it can't actually verify.
#
# 4. DENSE MULTI-DECADE HISTORICAL GRIDS (e.g. 1910 p260's "1832 to 1909"
#    table, three Year|Value|Increase blocks repeated side by side on one
#    page) do NOT reliably parse with this line-window approach -- confirmed
#    by testing against it directly. Those years are expected to come back
#    with value=None, not a silently wrong cell from the wrong sub-block --
#    real, honest gaps for manual review, not swept under the rug.

# Tables using these words near a track-mileage phrase are measuring the
# wrong industry (transit/streetcars), not steam railroads -- see finding 2.
WRONG_INDUSTRY_MARKERS = ["ELECTRIC RAILWAY", "TRANSIT", "TROLLEY", "STREET RAILWAY", "SUBWAY"]

# A label LINE containing one of these is a real, confirmed false-positive
# trap even though the phrase we're searching for also matches on it:
# "Mileage operated by" the Railway Express Agency / express companies
# (found live TWICE, in different wordings -- 1930 p436: "Mileage
# operated by express companies"; 1940 p470 and 1941 p521: "Mileage
# operated by Railway Express Agency, Inc." -- an initial guard for just
# "EXPRESS COMPAN" missed the second wording entirely and let two wrong
# values through a full run before this was caught. Express Agency
# mileage is especially dangerous here, not just wrong: its figures
# (282,369 in 1939; 285,454 in 1940) land RIGHT inside
# PLAUSIBLE_VALUE_RANGE and look completely reasonable, because the
# Agency operated over most of the same rail network -- a range check
# alone cannot catch this, only recognizing the entity is not the
# railroads themselves.), a table-of-contents entry (found live, 1930
# p9 -- contents lines list a table's *title*, not its data, and a
# trailing page number on that line can look exactly like a plausible
# mileage value if not excluded here).
WRONG_CONTEXT_MARKERS = ["EXPRESS", "PULLMAN", "CONTENTS", "TABLE PAGE"]

# Rough plausible range for total continental-US steam-railroad
# single-track-equivalent mileage across 1900-1950. Real confirmed values
# seen this session run ~193,000 (1900) to ~259,000 (1920s-40s peak);
# generous margin on both sides specifically to reject the kind of clearly-
# wrong OCR/context misreads found live while testing (10,310; 71,948;
# 9,760,337), not to be a tight scientific bound.
PLAUSIBLE_VALUE_RANGE = (140_000, 300_000)

# A table whose text contains many of these is a by-state breakdown, not
# the national total the user explicitly scoped this extraction to.
US_STATE_NAMES = [
    "ALABAMA", "ARIZONA", "ARKANSAS", "CALIFORNIA", "COLORADO", "CONNECTICUT",
    "DELAWARE", "FLORIDA", "GEORGIA", "IDAHO", "ILLINOIS", "INDIANA", "IOWA",
    "KANSAS", "KENTUCKY", "LOUISIANA", "MAINE", "MARYLAND", "MASSACHUSETTS",
    "MICHIGAN", "MINNESOTA", "MISSISSIPPI", "MISSOURI", "MONTANA", "NEBRASKA",
    "NEVADA", "NEW HAMPSHIRE", "NEW JERSEY", "NEW MEXICO", "NEW YORK",
    "NORTH CAROLINA", "NORTH DAKOTA", "OHIO", "OKLAHOMA", "OREGON",
    "PENNSYLVANIA", "RHODE ISLAND", "SOUTH CAROLINA", "SOUTH DAKOTA",
    "TENNESSEE", "TEXAS", "UTAH", "VERMONT", "VIRGINIA", "WASHINGTON",
    "WEST VIRGINIA", "WISCONSIN", "WYOMING",
]

# Names from DEFAULT_CAPTION_PATTERNS that are explicit track-type labels --
# used to OCR the most-promising candidate pages first (see
# extract_single_track_value()) without needing to OCR a low-value candidate
# just to learn it was low-value; detect_table_pages() already told us that
# via matched_patterns, no extra OCR call needed to know it.
STRONG_PATTERN_NAMES = {"SINGLE TRACK", "LENGTH OF SINGLE", "SINGLE, SECOND", "FIRST TRACK", "CLASSES OF TRACK"}

STRONG_VALUE_LABELS = [
    re.compile(r"single track", re.IGNORECASE),
    re.compile(r"first track", re.IGNORECASE),
    re.compile(r"first\s{0,3}main\s{0,3}track", re.IGNORECASE),
]
WEAK_VALUE_LABELS = [
    re.compile(r"miles? of (road|line)\D{0,20}operated", re.IGNORECASE),
    # Broadened from a literal "mileage operated" after finding live that
    # the real 1930 table reads "MILEAGE OWNED AND OPERATED" -- a rigid
    # exact-phrase match skipped that page entirely and fell through to a
    # worse candidate. \D{0,15} tolerates "owned and", "operated and", etc.
    # between the two anchor words without matching arbitrary unrelated text.
    re.compile(r"mileage\D{0,15}(owned|operated)", re.IGNORECASE),
    re.compile(r"road\D{0,15}operated", re.IGNORECASE),
    re.compile(r"track\D{0,15}operated", re.IGNORECASE),
]

NUMBER_RE = re.compile(r"\d[\d,.\s]{2,10}\d|\d{4,7}")


def rasterize_page(pdf_path: Path, page_number: int, dpi: int = RASTER_DPI) -> Image.Image:
    """Render one PDF page to a PIL Image via PyMuPDF -- no poppler/
    pdf2image dependency, which matters here since this environment has
    neither poppler nor a package manager to install it with."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number]
        zoom = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.open(io.BytesIO(pix.tobytes("png")))
    finally:
        doc.close()


def ocr_page_to_grid(pdf_path: Path, page_number: int, dpi: int = RASTER_DPI) -> str:
    """Rasterize one page and run pytesseract, returning its OCR'd plain
    text. Named to match Step 1's original scaffold signature/intent --
    "grid" here is plain text, not a structured cell grid; see
    extract_single_track_value()'s docstring (finding 1 and 4 above) for
    why a fully general table-cell-position parser isn't what got built,
    and what that trade-off costs on the densest historical tables.
    """
    img = rasterize_page(pdf_path, page_number, dpi)
    return pytesseract.image_to_string(img)


def _clean_number(raw: str) -> int | None:
    """Parse an OCR'd number token, tolerant of stray whitespace after
    commas ("128, 320") and stray periods some printings use. Rejects
    anything reducing to fewer than 4 digits -- too short to plausibly be
    a national mileage figure, more likely a page/table/footnote number."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 4:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _is_state_table(text: str) -> bool:
    upper = text.upper()
    return sum(1 for state in US_STATE_NAMES if state in upper) >= 6


def _is_wrong_industry(text: str) -> bool:
    upper = text.upper()
    return any(marker in upper for marker in WRONG_INDUSTRY_MARKERS)


def _is_contents_page(text: str) -> bool:
    """A table-of-contents / per-chapter index page lists table TITLES
    with trailing page numbers, not table data -- confirmed live as a
    real trap twice: 1930 p9 (a contents entry's own trailing page number
    can look exactly like a plausible mileage figure), and 1900 p11,
    which produced a real wrong extraction (189,937 from page-number-ish
    noise) before this was caught. p11 is worse than p9 in one way: its
    own literal "CONTENTS. XI" header sits ~700 characters in, past
    leftover wrapped text from a preceding subsection's index -- an
    earlier version of this check only looked at the first 200
    characters and missed it entirely. Searches the WHOLE page text for
    "CONTENTS" now, not just the top, plus a structural fallback (many
    numbered "NNN. Description" entries -- an index/contents shape) for
    continuation pages that might not carry the word "CONTENTS" at all.
    """
    if "CONTENTS" in text.upper():
        return True
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    numbered_entries = sum(1 for line in lines if re.match(r"^[.|]?\s*\d{1,3}\.\s+[A-Z]", line))
    return numbered_entries >= 4


def _numbers_in_range(line: str) -> list[int]:
    return [
        v
        for tok in NUMBER_RE.findall(line)
        if (v := _clean_number(tok)) is not None and PLAUSIBLE_VALUE_RANGE[0] <= v <= PLAUSIBLE_VALUE_RANGE[1]
    ]


def _is_wrong_context(line: str) -> bool:
    upper = line.upper()
    return any(marker in upper for marker in WRONG_CONTEXT_MARKERS)


# A year token on a line, standalone-ish (not part of a longer number) --
# used to catch the real trap below: a dense table listing one row per year
# ("June 30, 1890 ... 163,597 | ...", "1895 ... 180,657 | ...") where the
# label is a column HEADER appearing once above ALL those rows. Confirmed
# live: 1946 p510's table runs "1890 to 1944," and the first data row
# ("June 30, 1890") landed immediately after the header, producing a
# plausible-looking but flatly wrong value (199,875 -- the 1890 figure,
# not 1944's) that PLAUSIBLE_VALUE_RANGE alone could never catch, because
# an 1890s mileage figure and a 1940s one both fall in the same broad range.
# NOT \b1[89]\d{2}\b -- \b treats underscore as a "word" character, so it
# fails to match real OCR dot-leader artifacts like "1890__._...." (found
# live: this exact string is why the first version of this fix still
# didn't catch the 1946 bug -- the trailing underscores swallowed the
# boundary). (?!\d)/(?<!\d) only care about digits, so they don't have
# that blind spot.
YEAR_TOKEN_RE = re.compile(r"(?<!\d)(1[89]\d{2})(?!\d)")


def _line_declares_a_different_year(line: str, target_year: int, tolerance: int = 2) -> bool:
    """True if `line` opens with (or prominently contains near its start) a
    year token more than `tolerance` years from target_year -- the specific
    signature of a per-year-row table's first data row, not a genuine
    same-row label/value wrap. Checked before trusting a same-line or
    next-line number, not just a bare range check."""
    match = YEAR_TOKEN_RE.search(line[:25])  # near the start of the line only
    if not match:
        return False
    return abs(int(match.group(1)) - target_year) > tolerance


def _find_value_near_label(
    text: str, label_patterns: list[re.Pattern], target_year: int
) -> tuple[int, str] | None:
    """Search text line-by-line for any label pattern; on a hit, return the
    last plausible-range number found, preferring the SAME line as the
    match and only falling through to the next line if the same line has
    no plausible number (handles the real label/value line-wrap from
    finding 1 -- 1950 p518's label and numbers land on adjacent lines).

    Skips a matched line outright if it looks like the wrong context (an
    "Express Companies" note, a table-of-contents entry -- both real,
    confirmed false-positive traps, see WRONG_CONTEXT_MARKERS) even though
    the phrase itself matched -- a matched label doesn't guarantee it's
    actually about the table we want. Also skips a candidate line/next-line
    if it opens with a year token far from target_year (see
    _line_declares_a_different_year -- the real 1946 bug: grabbing 1890's
    row from a table headed "1890 to 1944" because the header line and
    1890's row happened to be adjacent). Requires the number to fall in
    PLAUSIBLE_VALUE_RANGE -- a matched label with no plausible-range number
    nearby is treated as no match, not accepted anyway (this is what
    rejects the wrong-page misreads found live: 10,310; 71,948; 9,760,337).

    Deliberately does NOT search more than one line past the label match
    (rejected a wider 3-line window after finding live that it pulls
    numbers from unrelated trailing text, e.g. a footnote after the real
    row) -- narrower on purpose, at the cost of missing tables where the
    value legitimately sits further from its label than this.
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if any(pat.search(line) for pat in label_patterns):
            if _is_wrong_context(line):
                continue
            if not _line_declares_a_different_year(line, target_year):
                same_line = _numbers_in_range(line)
                if same_line:
                    return same_line[-1], line.strip()
            if i + 1 < len(lines) and not _is_wrong_context(lines[i + 1]):
                if _line_declares_a_different_year(lines[i + 1], target_year):
                    continue  # real 1946 trap: next line is a different year's row, not a wrap
                next_line = _numbers_in_range(lines[i + 1])
                if next_line:
                    return next_line[-1], f"{line.strip()} / {lines[i + 1].strip()}"
    return None


# --- Positional fallback for "repeated Year|Owned|Operated block" tables ---
# DISABLED -- not called from extract_single_track_value() below. Kept in
# the module, unused but documented, as real evidence of an approach that
# was built carefully, tested, and then independently proven unreliable --
# see extract_single_track_value()'s docstring and docs/ai_prompts.txt for
# exactly how (2 of 17 raw results confirmed pulled from the wrong decade
# entirely, via a rare page with legible ground-truth year labels; no way
# to check the other 15 the same way, so none of its output is trustworthy,
# not just the two caught). Read the reasoning below for why it seemed
# sound going in -- it did solve a real, correctly-diagnosed problem
# (illegible year labels); it just couldn't be made reliable in practice.
#
# Real finding that motivated this: the single most common recurring table
# blocking extraction across the 1920s-1930s is "No. NNN.--RAILWAY MILEAGE
# OWNED AND OPERATED" (confirmed present verbatim in 1930 p410 and 1935
# p379, almost certainly in most nearby years too) -- 3 side-by-side
# "Year | Owned | Operated" blocks, oldest-to-newest left-to-right, each
# block itself ascending top-to-bottom. Checked directly via bounding
# boxes (not assumed): this table's YEAR column is NOT legible to
# Tesseract at any confidence threshold on either page tested -- a hard
# OCR-quality ceiling, not a parsing bug, so no text-based approach can
# ever find "the row for 1935" by reading a year label here.
#
# What IS reliably true, checked directly: the block layout itself, and
# that each block's data ends cleanly (confirmed on both pages -- the last
# real data row is followed by ordinary footnote/prose text, not more
# rows). So instead of reading a year, this infers position: the
# bottom-most row of the RIGHTMOST block is the most recent year that
# block covers.
#
# REAL, DISCLOSED LIMITATION: "most recent year in the table" is not
# verified to be exactly target_year. SAUS tables commonly lag the
# volume's own cover year by 1-2 years (confirmed on 1930's and 1950's
# own tables elsewhere in this module), so this may be target_year,
# target_year-1, or target_year-2. This is recorded plainly in the
# returned source_pattern label, not silently presented as an exact-year
# match -- a real trade-off for coverage, not a hidden one.

BLOCK_TABLE_VALUE_COLUMNS = ["Operated", "Operated.", "Operated,"]
# A y-gap this many pixels or larger after the last real data row is
# treated as "the table ended, this is unrelated content below it."
# Calibrated against two real pages with different scan quality: 1935
# p379's rows are evenly spaced (~24px); 1930 p410's has real OCR
# dropout -- some rows never got recognized at all, producing gaps up to
# ~51px WITHIN the same real table. An earlier 40px threshold broke
# there prematurely, cutting off before the table's real last row. Both
# pages' actual table-end transition (into footnote/next-table content)
# is much larger (~320px on 1930 p410) -- 100px comfortably spans
# realistic dropout on both while stopping well short of that.
BLOCK_TABLE_ROW_GAP_PX = 100


def _ocr_words(pdf_path: Path, page_number: int, min_conf: float = 20.0) -> pd.DataFrame:
    """Word-level OCR with bounding boxes (left/top/width/height), for
    positional extraction -- plain image_to_string() throws this position
    information away, which is exactly what the block-table extractor
    below needs back."""
    img = rasterize_page(pdf_path, page_number)
    df = pytesseract.image_to_data(img, output_type=pytesseract.Output.DATAFRAME)
    df = df[df["conf"].astype(float) > min_conf].copy()
    df["text"] = df["text"].astype(str).str.strip()
    return df[df["text"] != ""]


def _extract_from_owned_operated_block_table(
    pdf_path: Path, page_number: int
) -> tuple[int, str] | None:
    """See the module-level comment above for the full reasoning. Returns
    (value, label) where label plainly states this is a positional,
    approximate-year read -- or None if this page doesn't actually have
    the expected repeated-block header shape at all (most pages won't;
    this is a narrow, targeted fallback, not a general table parser)."""
    words = _ocr_words(pdf_path, page_number)
    if words.empty:
        return None

    headers = words[words["text"].isin(BLOCK_TABLE_VALUE_COLUMNS)]
    if headers.empty:
        return None  # not this table shape -- nothing to do here

    # The rightmost "Operated" header = the most-recent-years block.
    header_row = headers.loc[headers["top"].idxmin()] if len(headers) == 1 else headers.sort_values("left").iloc[-1]
    col_left = float(header_row["left"])
    header_top = float(header_row["top"])
    col_width_guess = 130  # observed real header/number widths on both test pages, ~90-115px

    strip = words[
        (words["left"] >= col_left - 20)
        & (words["left"] <= col_left + col_width_guess)
        & (words["top"] > header_top + 15)
    ].sort_values("top")
    if strip.empty:
        return None

    # Group into rows by y-proximity; stop at the first big gap (table end).
    rows: list[list[pd.Series]] = []
    current_row: list[pd.Series] = []
    last_top: float | None = None
    for _, word in strip.iterrows():
        top = float(word["top"])
        if last_top is not None and top - last_top > BLOCK_TABLE_ROW_GAP_PX:
            break  # real table-end signature, confirmed on both test pages
        if last_top is not None and top - last_top > 10:
            rows.append(current_row)
            current_row = []
        current_row.append(word)
        last_top = top
    if current_row:
        rows.append(current_row)
    if not rows:
        return None

    # Walk backward from the true last row, not just rows[-1] blindly --
    # found live (1930 p410) that the actual bottom-most row-group is
    # sometimes a stray OCR fragment (a single 3-digit token, "425", 26px
    # below the real last data row "258, 362") rather than real table
    # data. Only the row itself is skipped when it has no plausible
    # number in it -- capped at a few rows back so this can't wander into
    # a genuinely different, older row if several stray fragments stack up.
    joined = candidates = None
    for row in reversed(rows[-4:]):
        row_sorted = sorted(row, key=lambda w: float(w["left"]))
        row_joined = " ".join(str(w["text"]) for w in row_sorted)
        row_candidates = _numbers_in_range(row_joined)
        if row_candidates:
            joined, candidates = row_joined, row_candidates
            break
    if not candidates:
        return None

    label = (
        f'Positional read, bottom row of rightmost "Operated" block on p{page_number} '
        f"-- approximate year, not verified exact (raw: {joined!r})"
    )
    return candidates[-1], label



# --- Known trailing-history tables: real, targeted automation -------------
#
# Manual review this session (docs/ai_prompts.txt has the full trail) found
# that just 3 specific pages, in 3 different volumes, carry dense
# "trailing-history" tables that together cover nearly the entire 1900-1948
# range -- a far richer automation target than the original per-year search
# (which, searching each year's OWN volume separately, only ever produced 1
# usable value across all 48 years). These 3 pages are exactly the ones
# manual review actually used as its source, per manual_review.csv's own
# notes.
#
# Every function below re-derives its numbers from real OCR'd pixels on
# every run -- nothing here returns a value it did not just read off the
# page. What IS fixed, and disclosed as such in each docstring, is
# structural knowledge about these 3 SPECIFIC named tables' layouts
# (which column holds the target figure; for one table, which row
# corresponds to which year, since that table's own year column doesn't
# OCR at all -- a real, confirmed Tesseract ceiling, not a parsing bug).
# That structural knowledge was established by cross-referencing EVERY
# extractable row against manual_review.csv's independently-verified
# values, not by guessing or by looking up the values themselves -- see
# the validation counts in each function's docstring and the full
# cross-check trail in docs/ai_prompts.txt.
KNOWN_TRAILING_TABLES: list[tuple[str, int, str]] = [
    # (pdf filename under RAW_DIR, 0-indexed page number, table "kind")
    # Priority order matters: earlier entries win on any year where two
    # tables overlap (see extract_from_known_trailing_tables()) -- this
    # ordering exactly reproduces which source manual review actually
    # preferred for each overlap year, but via a general "first known
    # table to cover a year wins" rule, not a per-year lookup.
    ("saus_1918.pdf", 333, "track_length"),  # No. 215, 1895-1916
    ("saus_1948.pdf", 528, "owned_operated_rows"),  # No. 564, 1890-1946
    ("saus_1950.pdf", 509, "owned_operated_columns"),  # No. 591, 1925-1948
]

# Matches a caption's own declared "XXXX TO YYYY" / "XXXX-YYYY" span (e.g.
# "1895 TO 1916") -- used to derive how many sequential data rows a table
# like No. 215 should have FROM THE TABLE'S OWN PRINTED CAPTION, not a
# hardcoded assumption about that specific volume. Deliberately NOT
# anchored on a literal "TO"/"-" separator -- checked live, OCR renders
# the word "TO" here as "Tro" (a spurious inserted letter), so a
# separator-specific pattern missed it entirely; any 1-15 non-digit
# characters between two plausible-year tokens is enough, since this is
# only ever searched right after a specific table's own confirmed
# caption anchor, not blindly across a whole page.
CAPTION_YEAR_RANGE_RE = re.compile(r"(1[89]\d{2})\D{1,15}(1[89]\d{2})")

# A single comma-grouped number token ("252, 105", "192,556.03") -- stricter
# and comma-group-AWARE, unlike the module's general NUMBER_RE (built for
# finding one value per line, not splitting a dense row into columns).
# Reusing NUMBER_RE here was tried first and rejected: its whitespace-
# tolerant character class can't tell "252, 105" (one 6-digit number,
# comma-space formatted) apart from "252, 105 263, 547" (two separate
# numbers with a column gap) -- confirmed live, it merged adjacent columns
# into single bogus multi-digit blobs on these exact tables.
NUMBER_TOKEN_RE = re.compile(r"\d{1,3}(?:,\s?\d{3})+(?:\.\d{1,2})?")


def _parse_caption_year_range(text: str) -> tuple[int, int] | None:
    """Finds a table's own declared "XXXX TO YYYY" span near the top of
    its page text. Searches only the first 600 characters -- the caption
    is always near the top; searching the whole page risks matching an
    unrelated year-range mentioned in a footnote or a different table
    further down."""
    match = CAPTION_YEAR_RANGE_RE.search(text[:600])
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _row_numbers(line: str) -> list[int]:
    """Every comma-grouped number on one line, left to right, as ints
    (decimals truncated -- these dense grids only need whole-mile
    comparisons against PLAUSIBLE_VALUE_RANGE and against
    manual_review.csv, which itself stores some rows to 2 decimals; exact
    fractional-mile matching is not attempted here). Truncates by
    splitting off the ".XX" suffix BEFORE stripping non-digit characters
    -- re.sub(r"\\D", "", tok) alone on a token like "216,978.61" would
    strip the decimal point along with the commas and concatenate the
    cents digits onto the whole number (21,697,861 -- wrong by two orders
    of magnitude, confirmed live, this pushed every decimal-bearing row
    straight out of PLAUSIBLE_VALUE_RANGE and silently dropped it)."""
    result = []
    for tok in NUMBER_TOKEN_RE.findall(line):
        whole_part = tok.split(".")[0]
        digits = re.sub(r"\D", "", whole_part)
        if digits:
            result.append(int(digits))
    return result


def _extract_from_track_length_table(pdf_path: Path, page_number: int) -> dict[int, tuple[int, str]]:
    """Table shape: 'LENGTH OF SINGLE, SECOND, THIRD [, FOURTH] TRACKS...'
    (SAUS Table No. 215 on saus_1918.pdf p333) -- one row per year,
    strictly sequential, no gaps. The YEAR column itself does not OCR at
    all on this table (confirmed -- not one digit of it appears in
    pytesseract's output on this page, at any confidence threshold), but
    the table's own caption states its declared range ("1895 TO 1916"),
    and every row's FIRST comma-grouped number is the single-track
    mileage -- confirmed against every one of the 17 years (1900-1916)
    manual_review.csv independently verified from this exact table: 6
    exact matches, 6 more within 0.5% (real, minor OCR digit noise), 3
    real single-digit misreads (1908, 1913, 1916 -- each one specific
    misread digit, e.g. "280,494" OCR'd where the true value is
    "230,494"; NOT silently accepted -- see docs/ai_prompts.txt for the
    full row-by-row comparison and exactly which digit is wrong in each),
    and 2 honest gaps (1904, 1915 -- both rejected by their own
    corrupted first column, not guessed). Real OCR noise on this
    table's small, tightly-packed decimal print is measurably worse than
    the cleaner No. 564 table below, and that's disclosed here rather
    than only in the aggregate validation numbers.

    Row detection is two-pass, specifically to avoid a real desync risk
    found while building this: the "Third track" and "Fourth track"
    columns are usually under 1,000 miles, so they never get a comma
    group and NUMBER_TOKEN_RE (comma-group-only, by design -- see its own
    docstring) never matches them. Requiring >= 6 matched tokens per row
    (all 6 real columns) to accept a row therefore rejected many
    perfectly good rows over a missing THIRD/FOURTH-column token, not a
    problem with the row's own first column -- and because rows are
    otherwise sequential with no separator, silently skipping one still-
    good row desyncs every year after it by one. Pass 1 counts row-SHAPED
    lines loosely (>= 3 numeric tokens of any kind, comma-grouped or not)
    to establish real row positions without requiring every column to
    have matched; pass 2 reads nums[0] from each of those same lines
    using the strict comma-aware tokenizer, or leaves that one year's
    value as None (an honest, position-safe per-row gap, not a shift)
    if the first column specifically failed to parse -- a real, confirmed
    case: 1904's row has severe OCR corruption in its own first two
    columns and comes back as a real gap, not a silently wrong value.

    A table with MORE data rows than its declared range accounts for is
    real too -- p333 has exactly this, one extra row appended after the
    normal 1895-1916 run (a footnoted calendar-year alternate for 1916,
    distinct from the normal fiscal-year-ended-June-30 figure the
    sequential row already produced). That extra row is not invented a
    new year past the declared range; it's treated as a second, alternate
    candidate for the table's own LAST declared year, and the label
    records BOTH values so a real disagreement stays visible rather than
    being silently overwritten in either direction.

    Scoped to start AFTER this table's own caption line, not the top of
    the page -- a real, confirmed trap: p333 carries a DIFFERENT table
    (No. 214, "1837 TO 1916") immediately above No. 215, and naively
    parsing "the first XXXX TO YYYY span on the page" grabs No. 214's
    range and starts consuming No. 214's own data rows instead. Anchored
    on the literal table number "No. 215" -- checked live, this table's
    actual descriptive caption text OCRs far too badly to match on
    ("Length of Single, Second..." comes back "Lenera or Snare,
    Seconn..."), but the bracketed table number itself reads cleanly.
    """
    text = ocr_page_to_grid(pdf_path, page_number)
    lines = text.split("\n")
    caption_idx = next(
        (i for i, line in enumerate(lines) if re.search(r"No\.?\s*215\b", line)),
        None,
    )
    if caption_idx is None:
        return {}
    scoped_text = "\n".join(lines[caption_idx:])
    year_range = _parse_caption_year_range(scoped_text)
    if year_range is None:
        return {}
    start_year, end_year = year_range
    expected_rows = end_year - start_year + 1

    # Pass 1: loose row-shape detection, any >= 3 numeric tokens (plain
    # NUMBER_RE, not the comma-group-only NUMBER_TOKEN_RE) -- establishes
    # real row POSITIONS even for a row whose first column is corrupted.
    row_lines = [
        line for line in lines[caption_idx:]
        if len([tok for tok in NUMBER_RE.findall(line) if _clean_number(tok) is not None]) >= 3
    ]
    if len(row_lines) not in (expected_rows, expected_rows + 1):
        logger.warning(
            "track-length table on p%d: expected %d (or %d+1 with an extra) row-shaped "
            "lines for declared range %d-%d, found %d -- not applying (real row count "
            "mismatch, not silently guessing at alignment).",
            page_number, expected_rows, expected_rows, start_year, end_year, len(row_lines),
        )
        return {}

    # Pass 2: strict per-row extraction, position already confirmed above.
    results: dict[int, tuple[int, str]] = {}
    for i, line in enumerate(row_lines[:expected_rows]):
        nums = _row_numbers(line)
        year = start_year + i
        if nums and PLAUSIBLE_VALUE_RANGE[0] <= nums[0] <= PLAUSIBLE_VALUE_RANGE[1]:
            results[year] = (
                nums[0],
                f"Known track-length table (No. 215-shape), page {page_number}, row {i} of "
                f"declared range {start_year}-{end_year}: {nums}",
            )
        # else: real per-row gap (e.g. 1904's corrupted first column) --
        # position for every OTHER row stays correct either way, since
        # row_lines' own count/order was already confirmed in pass 1.

    if len(row_lines) > expected_rows:
        extra_line = row_lines[expected_rows]
        extra_nums = _row_numbers(extra_line)
        if extra_nums and PLAUSIBLE_VALUE_RANGE[0] <= extra_nums[0] <= PLAUSIBLE_VALUE_RANGE[1]:
            prior = results.get(end_year)
            results[end_year] = (
                extra_nums[0],
                f"Known track-length table (No. 215-shape), page {page_number}: table has "
                f"1 extra row beyond its declared range for {end_year} (a real, confirmed "
                f"case -- a footnoted alternate figure, e.g. calendar- vs fiscal-year). "
                f"Using the extra row ({extra_nums[0]}); the normal sequential row's own "
                f"reading was {prior[0] if prior else 'not found'} -- both are real "
                "candidates, disclosed here rather than one silently overwriting the other.",
            )
    return results


def _extract_from_owned_operated_rows_table(pdf_path: Path, page_number: int) -> dict[int, tuple[int, str]]:
    """Table shape: 'STEAM RAILWAYS -- MILEAGE OWNED AND MILEAGE
    OPERATED' with years as ROWS (SAUS Table No. 564 on saus_1948.pdf
    p528) -- 'Road, first track' is column index 3 (0-indexed) of the
    'Reporting railways' block. The YEAR column on this specific table
    does not OCR at all (confirmed, same real Tesseract ceiling as the
    track-length table above), and unlike that table this one's row
    cadence is NOT uniformly sequential -- it opens with five
    every-5-years rows (1890, 1895, 1900, 1905, 1910), then switches to
    annual from 1914 through 1946. That schedule is NOT stated anywhere
    in the table's own caption/footnotes (checked); it is a real,
    confirmed structural fact about this ONE SPECIFIC named table,
    established here by cross-referencing every one of its 36 rows that
    manual_review.csv could independently verify (1900, 1905, 1910, and
    every year 1917-1946) against this function's own OCR read: 36/36
    exact matches, including the 1926 row -- which resolved a real
    discrepancy this session (an earlier, DIFFERENT extraction had
    misread 1926 as 240,293, actually 1910's figure; this table's own
    1926 row reads 258,815, matching manual_review.csv's independently
    re-read value exactly). See docs/ai_prompts.txt for the full
    row-by-row comparison.

    This schedule is scoped to THIS table specifically, not applied
    blindly: it only fires if the caption matches (checked first) AND
    the real row count found equals the expected 38 -- if either check
    fails (e.g. this code is ever pointed at a differently-shaped
    reprint of the same title), it returns {} rather than silently
    misapplying a schedule that doesn't actually match what's on the
    page. The row-to-year SCHEDULE is fixed; the mileage VALUES
    themselves are still freshly OCR'd from this page's real pixels on
    every run, never looked up.
    """
    text = ocr_page_to_grid(pdf_path, page_number)
    # Tolerant, not an exact-phrase match: OCR renders this specific
    # caption very noisily (confirmed live -- "Steam Railways" comes back
    # "Sream Rariways", "Mileage" comes back "MiILEaGE") -- three
    # substrings anywhere in the caption area is enough to confirm this
    # is really the No. 564 table without requiring an exact reading of a
    # caption OCR gets this garbled on.
    squashed_caption = _squash(text[:800])
    if not ("564" in squashed_caption and "MILEAG" in squashed_caption and "OPERATED" in squashed_caption):
        return {}

    data_rows: list[list[int]] = []
    for line in text.split("\n"):
        nums = _row_numbers(line)
        if len(nums) >= 5 and PLAUSIBLE_VALUE_RANGE[0] <= nums[3] <= PLAUSIBLE_VALUE_RANGE[1]:
            data_rows.append(nums)

    # The real, confirmed schedule for THIS table (see docstring) --
    # sparse every-5-years, then annual. Only applied if the real row
    # count matches exactly what this schedule expects.
    schedule = [1890, 1895, 1900, 1905, 1910] + list(range(1914, 1947))
    if len(data_rows) != len(schedule):
        logger.warning(
            "owned_operated_rows table on p%d: expected %d rows for the known 1890-1946 "
            "schedule, found %d -- not applying (real row count mismatch, not silently "
            "guessing a shifted schedule).",
            page_number, len(schedule), len(data_rows),
        )
        return {}

    results: dict[int, tuple[int, str]] = {}
    for year, nums in zip(schedule, data_rows):
        results[year] = (
            nums[3],
            f"Known owned-operated table (No. 564-shape), page {page_number}, "
            f"'Road, first track' column, row for {year}: {nums}",
        )
    return results


def _extract_from_owned_operated_columns_table(pdf_path: Path, page_number: int) -> dict[int, tuple[int, str]]:
    """Table shape: the SAME 'STEAM RAILWAYS -- MILEAGE OWNED AND MILEAGE
    OPERATED' title, but TRANSPOSED -- years as COLUMN headers, metrics as
    ROW labels (SAUS Table No. 591 on saus_1950.pdf p509, covering 7
    benchmark years: 1925, 1930, 1935, 1940, 1945, 1947, 1948). Unlike the
    two tables above, this one's year headers DO OCR correctly (confirmed
    -- 'ITEM 1925 1930 1935 1940 1945 1947 1948' reads cleanly), so this
    function locates columns by real year tokens rather than a fixed
    schedule.

    The real difficulty here isn't the years, it's the ROW: this table
    prints FOUR different 'Road ... first track' variants stacked closely
    together -- 'Road owned, first track', 'All railways, road, first
    track', the plain 'Road, first track' (the actual target -- the
    unqualified national figure), and 'Class I railways, road, first
    track' (a SUBSET, real but wrong-scope -- confirmed as the exact trap
    an earlier draft reading of this same page fell into, using 226,704
    for 1948 instead of the real 237,756; see manual_review.csv's own
    1947/1948 notes). Picking the wrong one of these four rows would be a
    real, silent wrong-answer risk, not a cosmetic issue.

    Disambiguated positionally, via word-level bounding boxes (not plain
    OCR text order, which was checked and found to interleave this page's
    label column and number columns in a scrambled, unreliable order):
    the target row is the one whose LEFT-column label words include
    "road" and "first" together, WITHOUT "owned", "all", "railways," or
    "class" also present on that same row band -- i.e. the unqualified
    row, by the same rule a human applies reading these labels, not a
    lookup of which row happens to hold the known-correct value. Confirmed
    against both of this table's genuinely new years (1947, 1948 -- the
    other 5 columns duplicate years the rows-shape table above already
    covers): both match manual_review.csv exactly (238,209 and 237,756).
    """
    words = _ocr_words(pdf_path, page_number)
    if words.empty:
        return {}

    year_words = words[words["text"].str.fullmatch(r"19[0-5]\d")]
    if year_words.empty:
        return {}
    header_top = float(year_words["top"].median())
    header_row = year_words[abs(year_words["top"] - header_top) < 15]
    year_columns = sorted(
        (int(row["text"]), float(row["left"])) for _, row in header_row.iterrows()
    )
    if not year_columns:
        return {}

    # Find every row-label token cluster that says "road" + "first"
    # without a disqualifying qualifier nearby (see docstring).
    label_words = words[words["left"] < 600].copy()
    label_words["lower"] = label_words["text"].str.lower()
    target_row_top: float | None = None
    for candidate_top in sorted(label_words["top"].unique()):
        band = label_words[abs(label_words["top"] - candidate_top) < 12]
        lowers = set(band["lower"])
        has_road = any("road" in w for w in lowers)
        has_first = any("first" in w for w in lowers)
        disqualified = any(
            any(bad in w for w in lowers) for bad in ["owned", "all", "railways", "class"]
        )
        if has_road and has_first and not disqualified:
            target_row_top = candidate_top
            break
    if target_row_top is None:
        return {}

    # Real values for this row live at roughly the same y as the label
    # (checked live -- within ~15px), possibly split into fragments by
    # OCR at the comma ("258," + "681"); assign every numeric fragment on
    # this row band to its nearest year column by x-position, then
    # concatenate each column's fragments left-to-right.
    row_band = words[abs(words["top"] - target_row_top) < 15]
    numeric_tokens = row_band[row_band["text"].str.match(r"^[\d,.]+$")]
    per_column: dict[int, list[tuple[float, str]]] = {year: [] for year, _ in year_columns}
    for _, tok in numeric_tokens.iterrows():
        left = float(tok["left"])
        nearest_year = min(year_columns, key=lambda yc: abs(yc[1] - left))[0]
        per_column[nearest_year].append((left, str(tok["text"])))

    results: dict[int, tuple[int, str]] = {}
    for year, frags in per_column.items():
        if not frags:
            continue
        frags.sort()
        joined = "".join(text for _, text in frags)
        value = _clean_number(joined)
        if value is not None and PLAUSIBLE_VALUE_RANGE[0] <= value <= PLAUSIBLE_VALUE_RANGE[1]:
            results[year] = (
                value,
                f"Known owned-operated table (No. 591-shape), page {page_number}, "
                f"unqualified 'Road, first track' row, {year} column (fragments: {frags}).",
            )
    return results


def extract_from_known_trailing_tables(raw_dir: Path = RAW_DIR) -> dict[int, tuple[int, str, int, str]]:
    """Runs all 3 targeted parsers above against their fixed, known pages
    -- not gated by any year's own per-year candidate search, since these
    3 pages live in different volumes than most of the years they cover.

    Returns {year: (value, source_pattern, page_number, ocr_raw_text)} --
    ocr_raw_text is that page's FULL OCR'd text (matching what the
    per-year extract_single_track_value() path stores there), not just
    the one matched row, so a human or Part D's cross-check has the same
    amount of context to verify a value against either way.

    Where two tables both cover the same year (a real, confirmed overlap:
    p333 and the rows-shape table both reach 1900/1905/1910),
    KNOWN_TRAILING_TABLES' own list order decides priority -- first table
    to produce a value for a year wins, later tables only fill years
    still missing. This reproduces exactly which source manual review
    actually preferred for every overlap year, but via that one general
    ordering rule, not a per-year lookup table.
    """
    parsers = {
        "track_length": _extract_from_track_length_table,
        "owned_operated_rows": _extract_from_owned_operated_rows_table,
        "owned_operated_columns": _extract_from_owned_operated_columns_table,
    }
    merged: dict[int, tuple[int, str, int, str]] = {}
    for filename, page_number, kind in KNOWN_TRAILING_TABLES:
        pdf_path = raw_dir / filename
        if not pdf_path.exists():
            logger.warning("Known trailing table %s not found at %s -- skipping.", filename, pdf_path)
            continue
        table_results = parsers[kind](pdf_path, page_number)
        if not table_results:
            continue
        page_text = ocr_page_to_grid(pdf_path, page_number)
        for year, (value, label) in table_results.items():
            if year not in merged:
                merged[year] = (value, label, page_number, page_text)
    return merged


def extract_single_track_value(
    pdf_path: Path,
    candidate_matches: list[dict],
    target_year: int,
) -> tuple[int | None, int | None, str | None, str]:
    """Given one year's candidate pages (the "matches" list from
    table_page_matches.json), OCR them -- in priority order, not file
    order, and stopping at the first one that actually yields a value --
    until a real national single-track-equivalent figure is found.

    Returns (value, page_number, source_pattern, ocr_raw_text).
    source_pattern is the literal matched line/label text the value came
    from (e.g. "Single track." or "Average miles of line (first track) op-"),
    so a reviewer can see immediately whether a row used the explicit term
    or a best-available proxy -- per the project decision, a proxy is
    allowed, but it must always be labeled, never silently blended under
    one meaning with the real thing.

    Rejects any candidate that looks like a by-state breakdown or the
    wrong industry (electric/transit -- findings above) before trusting
    anything from it. Does NOT blindly OCR every candidate unconditionally
    -- candidates whose matched_patterns already include a strong,
    explicit track-type label (from detect_table_pages(), no extra OCR
    needed to know this) are tried first, since they're far more likely to
    be the real table; weaker generic-label candidates are only OCR'd if
    none of the strong ones panned out. This still means every candidate
    gets checked against the real page content before being trusted or
    discarded -- it does not just take candidate_matches[0].
    """
    if not candidate_matches:
        return None, None, None, ""

    ordered = sorted(
        candidate_matches,
        key=lambda m: 0 if STRONG_PATTERN_NAMES & set(m["matched_patterns"]) else 1,
    )

    survivors: list[tuple[int, str]] = []  # (page_number, ocr_text), in priority order
    for match in ordered:
        pno = match["page_number"]
        text = ocr_page_to_grid(pdf_path, pno)
        if _is_state_table(text) or _is_wrong_industry(text) or _is_contents_page(text):
            continue
        survivors.append((pno, text))

        found = _find_value_near_label(text, STRONG_VALUE_LABELS, target_year) or _find_value_near_label(
            text, WEAK_VALUE_LABELS, target_year
        )
        if found:
            value, label_text = found
            return value, pno, label_text, text

    # NOT calling _extract_from_owned_operated_block_table() here --
    # DISABLED, not just unused. It raised raw coverage from 3/48 to
    # 17/48, but got independently proven wrong on 2 of those 17 (1941,
    # 1942) via a rare page (1942 p504) that happened to have fully
    # legible year labels, letting its output be checked against real
    # ground truth for once. Both were confirmed pulled from the wrong
    # decade entirely (1941's "value" was actually 1907's; 1942's was
    # actually 1920's) -- not "off by a year or two" as the function's
    # own approximate-year caveat allowed for, but a different block
    # entirely. Root cause: its "rightmost detected header = most recent
    # block" assumption silently breaks when a block's header simply
    # fails to OCR -- the code then treats an earlier block as if it
    # were the last one, with nothing to signal the mistake. Given 2 of
    # 17 were provably wrong and there's no ground truth to check the
    # other 15 against, none of its output can be trusted, not just the
    # two caught -- decided (with the user) to disable it rather than
    # ship results some fraction of which are confidently, silently
    # wrong. The function and its helpers are left in the module,
    # unused but documented, rather than deleted -- see
    # docs/ai_prompts.txt for the full investigation; it's real, honest
    # evidence of an approach that was tried carefully and rejected for
    # a specific, provable reason, which is itself worth keeping.

    # Nothing usable anywhere -- return the first non-rejected candidate's
    # raw text (or candidate_matches[0]'s, if every candidate was rejected)
    # so the year is still inspectable, with value=None rather than a guess.
    if survivors:
        first_pno, first_text = survivors[0]
        return None, first_pno, None, first_text
    fallback = candidate_matches[0]
    fallback_pno = fallback["page_number"]
    fallback_text = ocr_page_to_grid(pdf_path, fallback_pno)
    return None, fallback_pno, None, fallback_text


@dataclass
class ExtractionResult:
    year: int
    value: int | None
    page_number: int | None
    source_pattern: str | None
    ocr_raw_text: str
    excluded_reason: str = ""
    # Added after finding live (not hypothetically) that 1949 and 1950's
    # automated values were mislabeled: _find_value_near_label()'s "last
    # number in the line" heuristic assumes the last column of a
    # period-summary table (e.g. "ITEM 1925 1930 ... 1947") corresponds
    # to the SAUS volume's own cover year, but never actually checks the
    # header row's real last column against target_year -- confirmed on
    # saus_1949.pdf Table 587 and saus_1950.pdf Table 591, both of which
    # list years ending 2-3 years before the volume's own cover year
    # (1947 and 1948 respectively, not 1949/1950). Blank/empty here means
    # "no known issue"; non-empty means the row is NOT usable as extracted
    # even though `value` is populated -- see build_single_track_panel()'s
    # docstring and docs/ai_prompts.txt for the specific finding. This is
    # a manual, one-off correction on the CSV, not a fix to the
    # extraction logic itself -- re-running --step extract would
    # reproduce the same mislabeled values and lose this annotation; the
    # underlying "last column != target_year" gap in
    # _find_value_near_label() is real and still open.


def _write_panel_csv(results: list[ExtractionResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "year",
                "value",
                "page_number",
                "source_pattern",
                "ocr_raw_text",
                "excluded_reason",
            ],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))


def build_single_track_panel(
    page_matches_path: Path = PAGE_MATCH_REPORT_PATH,
    output_path: Path = INTERIM_PANEL_PATH,
    raw_dir: Path = RAW_DIR,
) -> list[ExtractionResult]:
    """Load table_page_matches.json, PREFER the 3 known trailing-history
    tables (extract_from_known_trailing_tables()) for any year they
    cover, fall back to the original per-year candidate search
    (extract_single_track_value()) for any year they don't, and write one
    row per year to data/interim/ as CSV: year, value, page_number,
    source_pattern, ocr_raw_text, excluded_reason.

    The known-trailing-table path is preferred, not just used when the
    per-year path fails, because it's the far better-validated source --
    cross-referenced against manual_review.csv row by row before this was
    wired in (docs/ai_prompts.txt has the full comparison): 39/49 exact
    matches, 6 more within 0.5% (real, minor OCR digit noise), only 3
    real single-digit misreads, and 1 honest gap -- a categorically
    different reliability level from the per-year path's own 1/48 real
    yield. Manual review still wins over BOTH of these in build.py's
    Part C merge either way (this module never claims to replace that);
    this only changes which AUTOMATED value gets recorded.

    The known-trailing tables also produce a handful of years the
    original 1900-1950 fetch never targeted at all (1890, 1895-1899 --
    real bonus coverage from the same 3 pages, see docs/ai_prompts.txt).
    These are appended after the main per-year loop, clearly out of the
    assignment's own 1900-1950 scope but not discarded -- build.py's own
    --start/--end filtering already excludes them from the final panel
    without any special-casing needed here.

    Written after every single year, not just at the end -- the same
    incremental-write lesson as fetch_saus.py's manifest and
    build_page_match_report()'s JSON: a 48-year OCR run (each year
    potentially OCR-ing several candidate pages) is real, slow,
    multi-minute-per-year work, not something to risk losing to a crash
    near the end.
    """
    with page_matches_path.open() as fh:
        page_matches = json.load(fh)

    known_results = extract_from_known_trailing_tables(raw_dir)

    results: list[ExtractionResult] = []
    covered_years = set()
    for year_str in sorted(page_matches, key=int):
        year = int(year_str)
        covered_years.add(year)
        if year in known_results:
            value, source_pattern, page_number, ocr_raw_text = known_results[year]
        else:
            entry = page_matches[year_str]
            pdf_path = resolve_path(entry["pdf"])
            value, page_number, source_pattern, ocr_raw_text = extract_single_track_value(
                pdf_path, entry["matches"], year
            )
        results.append(
            ExtractionResult(
                year=year,
                value=value,
                page_number=page_number,
                source_pattern=source_pattern,
                ocr_raw_text=ocr_raw_text,
            )
        )
        _write_panel_csv(results, output_path)
        logger.info("%s: value=%s page=%s label=%r", year_str, value, page_number, source_pattern)

    # Bonus years the known trailing-tables found that AREN'T a key in
    # page_matches.json at all -- either genuinely before the original
    # 1900-1950 fetch range (1890, 1895-1899), or a real gap year within
    # that range with no volume ever published (1927, 1944-1945 -- see
    # fetch_saus.py's README section) so build_page_match_report() never
    # created an entry for it. Appended here, not silently dropped.
    for year in sorted(set(known_results) - covered_years):
        value, source_pattern, page_number, ocr_raw_text = known_results[year]
        reason = "before the 1900-1950 fetch range" if year < 1900 else "no SAUS volume published for this year"
        results.append(
            ExtractionResult(
                year=year,
                value=value,
                page_number=page_number,
                source_pattern=f"[Bonus year, {reason}] {source_pattern}",
                ocr_raw_text=ocr_raw_text,
            )
        )
        _write_panel_csv(results, output_path)
        logger.info("%s (bonus -- %s): value=%s page=%s", year, reason, value, page_number)

    missing = [r.year for r in results if r.value is None]
    if missing:
        logger.warning(
            "%d/%d years had NO extractable value -- real gaps for manual review, not silently filled: %s",
            len(missing), len(results), missing,
        )
    else:
        logger.info("All %d years produced a value.", len(results))
    return results


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SAUS railroad-mileage page detection and OCR extraction.")
    parser.add_argument(
        "--step",
        choices=["detect", "extract"],
        default="detect",
        help="'detect': find candidate table pages (Part B.1). "
        "'extract': OCR candidates and extract the national single-track value (Part B.2-3).",
    )
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR, help="Directory of saus_<year>.pdf files")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: outputs/table_page_matches.json for detect, "
        "data/interim/single_track_mileage_interim.csv for extract)",
    )
    parser.add_argument(
        "--page-matches",
        type=Path,
        default=PAGE_MATCH_REPORT_PATH,
        help="Input JSON for --step extract (default: outputs/table_page_matches.json)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s"
    )
    if args.step == "detect":
        build_page_match_report(args.raw_dir, args.output or PAGE_MATCH_REPORT_PATH)
    else:
        build_single_track_panel(args.page_matches, args.output or INTERIM_PANEL_PATH)


if __name__ == "__main__":
    main()
