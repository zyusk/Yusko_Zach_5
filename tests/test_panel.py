"""Part E.2.i and E.2.iii -- expected panel shape, and validation MAPE < 5%.

Both grouped here (rather than a third test file) because both are really
"is the final digitized panel any good" checks, just from two different
angles -- structural correctness of build_panel()'s own output, and
external numerical validation against a real FRED benchmark -- and the
brief's own reference folder structure lists only test_panel.py and
test_checksum.py under tests/ (Part E's "Parsimony counts" note taken at
its word rather than adding a third file for one extra assertion).

Both call the real pipeline functions (build_panel(), the validate.py
computation helpers) rather than re-reading already-written output files,
so these tests catch a real regression in the pipeline itself, not just
"did the last run happen to leave a good file lying around." Neither test
calls run_build()/run_validate() directly -- those have real file-writing
side effects (data/saus_railroad_mileage_1900_1950.csv,
outputs/figures/panel_vs_fred_validation.png, outputs/findings.md), and
outputs/findings.md in particular may already have Zach's own hand-written
reflection in it by the time this suite runs -- a test must never
overwrite that.
"""

import pytest

from saus_digitizer.build import ADMISSIBLE_RANGE, PANEL_FIELDNAMES, build_panel


def test_panel_shape():
    """(i) expected panel shape -- brief's Part E.2.i."""
    rows = build_panel(start_year=1900, end_year=1950)
    years = [r.year for r in rows]

    assert len(rows) == 49, f"expected 49 rows (1900-1948, manual-review-covered), got {len(rows)}"
    assert len(set(years)) == len(years), "duplicate year(s) in panel"
    assert min(years) == 1900
    assert max(years) == 1948
    assert 1949 not in years, "1949 is a real, documented gap -- see KNOWN_EXCLUDED_YEARS"
    assert 1950 not in years, "1950 is a real, documented gap -- see KNOWN_EXCLUDED_YEARS"
    # No silent internal gaps either -- every year 1900-1948 must be present.
    assert set(years) == set(range(1900, 1949))

    for r in rows:
        assert r.source in ("manual", "automated")
        assert isinstance(r.single_track_mileage, float)
        assert ADMISSIBLE_RANGE[0] <= r.single_track_mileage <= ADMISSIBLE_RANGE[1], (
            f"{r.year}: {r.single_track_mileage} outside admissible range {ADMISSIBLE_RANGE}"
        )

    # Schema written to data/saus_railroad_mileage_1900_1950.csv.
    assert PANEL_FIELDNAMES == ["year", "single_track_mileage", "source", "page_number", "notes"]


def test_panel_currently_all_manual():
    """A real, current fact about this panel worth pinning explicitly:
    manual_review.csv (independently OCR-verified against real pages
    this session) covers every year 1900-1948 on its own, so it wins
    build_panel()'s merge for all 49 rows -- automated contributes 0.
    This is expected to change only if manual_review.csv's own coverage
    ever shrinks, which would itself be worth noticing."""
    rows = build_panel(start_year=1900, end_year=1950)
    assert all(r.source == "manual" for r in rows)


def test_validation_mape_under_5_percent():
    """(iii) validation MAPE < 5% -- brief's Part E.2.iii.

    Real, live computation against FRED (not a fixture) -- skips rather
    than fails if the network/FRED is unreachable, since a connectivity
    problem in the test environment is not a pipeline bug. When it DOES
    run, this asserts the actual number: per the Step 8 checkpoint in
    this assignment's own prompt plan, the honest move if this ever comes
    in above 5% is to disclose it in findings.md, never to force the
    test to pass.
    """
    from saus_digitizer import validate

    rows = build_panel(start_year=1900, end_year=1950)
    panel = {r.year: r.single_track_mileage for r in rows}

    try:
        increases = validate.fetch_fred_increases()
    except Exception as exc:  # network/FRED unreachable -- not a pipeline bug
        pytest.skip(f"Could not reach FRED ({validate.FRED_SERIES_ID}): {exc}")

    implied = validate.cumulate_from_anchor(panel, increases, validate.HEADLINE_ANCHOR_YEAR)
    result = validate.compute_mape(panel, implied, validate.MAPE_YEARS)

    assert result["mape"] is not None, "no overlapping years produced a MAPE"
    assert result["mape"] < 5.0, (
        f"headline MAPE {result['mape']}% >= 5% -- per Step 8's own checkpoint, disclose "
        "this in findings.md, do not force this test to pass"
    )
