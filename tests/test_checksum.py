"""Part E.2.ii -- checksum match.

What a SHA-256 match here actually proves: the PDF sitting in data/raw/ is
byte-for-byte the same file fetch_saus.py originally downloaded and hashed
into outputs/download_manifest.csv -- i.e. no download corruption, no file
silently swapped/truncated/re-saved since. What it does NOT prove: that
anything printed on the scanned page is correct, or that the OCR pipeline
read it right -- that's a completely separate concern (ocr.py's own
cross-validation against manual_review.csv, see docs/ai_prompts.txt),
never conflated with this one.
"""

import csv

import pytest

from saus_digitizer.fetch_saus import MANIFEST_PATH, PROJECT_ROOT, sha256_of_file


def _load_manifest_rows() -> list[dict]:
    if not MANIFEST_PATH.exists():
        pytest.skip(f"{MANIFEST_PATH} not present -- run fetch_saus.py first.")
    with MANIFEST_PATH.open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_manifest_has_expected_columns():
    rows = _load_manifest_rows()
    assert rows, "manifest is empty"
    assert set(rows[0].keys()) == {"year", "title", "url", "sha256", "path"}


def test_manifest_sha256_matches_real_files():
    """The actual integrity check: recompute every manifest row's PDF
    hash live and compare against what fetch_saus.py recorded at
    download time. Collects every mismatch/missing file before
    asserting, so a real failure shows every bad row at once, not just
    the first one found."""
    rows = _load_manifest_rows()

    missing: list[str] = []
    mismatched: list[str] = []
    for row in rows:
        pdf_path = PROJECT_ROOT / row["path"]
        if not pdf_path.exists():
            missing.append(row["path"])
            continue
        real_hash = sha256_of_file(pdf_path)
        if real_hash != row["sha256"]:
            mismatched.append(f"{row['path']}: manifest={row['sha256']} real={real_hash}")

    if missing and len(missing) == len(rows):
        pytest.skip(
            f"None of the {len(rows)} manifest PDFs are present locally (raw PDFs are "
            "gitignored / excluded from the submission zip, not fetched in this "
            "environment) -- nothing to checksum against."
        )

    assert not missing, f"{len(missing)} manifest PDF(s) missing on disk: {missing[:5]}"
    assert not mismatched, f"{len(mismatched)} manifest checksum mismatch(es): {mismatched}"


def test_manifest_year_range_matches_brief():
    """Not the whole 1900-1950 span will always have a real download --
    1927 and 1944-1945 are real, documented gaps (no SAUS volume was ever
    published those years, see README.md), not a bug -- but every row
    that DOES exist must fall inside the brief's own 1900-1950 window."""
    rows = _load_manifest_rows()
    years = {int(r["year"]) for r in rows}
    assert years, "no years in manifest"
    assert min(years) >= 1900
    assert max(years) <= 1950
    known_gap_years = {1927, 1944, 1945}
    assert years | known_gap_years == set(range(1900, 1951)), (
        f"unexpected gap(s) beyond the documented 1927/1944/1945: "
        f"{set(range(1900, 1951)) - years - known_gap_years}"
    )
