"""Part D -- Validation against a real external benchmark.

CONFIRMED LIVE before using either of the brief's own suggested candidate
series: neither A02083USA581NNBR ("Increases in Railroad Track Mileage
Operated") nor A02F2AUSA374NNBR ("Miles of Railroad Built") is a LEVEL
series -- both are annual FLOWS (declared units "Thousands of Miles" and
"Miles" respectively, confirmed by reading the actual FRED series pages,
not assumed from the name). Comparing either directly to the panel's
~190,000-260,000-mile LEVELS would be a meaningless, ~100%-every-year
error -- an artifact of comparing different kinds of quantities, not a
real accuracy signal.

Resolved (user-approved) by cumulating A02083's annual increases into an
implied level, anchored to a real year from our own panel rather than an
external guess. TWO anchors were computed and compared before picking
one, because the choice turned out to matter enormously:

  - Anchored at 1900 (the panel's first year, 47 years of compounding to
    reach 1947/1948): MAPE = 38.2%. Diagnosed, not just reported: this
    gap opens almost entirely during 1900-1930 (see the plot) and then
    PLATEAUS -- consistent with A02083 measuring ALL track mileage
    (route mileage plus every additional parallel track), which grew
    fast during the era railroads were double/triple-tracking existing
    routes, a real phenomenon our single-track/route-mileage panel does
    NOT count as "new mileage" the same way. This is a genuine
    definitional mismatch between the two series' LEVELS, not a bug.

  - Anchored at 1946 (the panel's last manually-verified year before the
    1947/1948 window being validated -- only 1-2 years of compounding):
    MAPE = 0.12%. Once the network stopped rapid multi-tracking (post-
    1930, per the plateau above), ANNUAL CHANGES in "track mileage
    operated" track almost exactly with annual changes in single-track/
    route mileage, even though the two series' LEVELS mean different
    things and would never match at a 47-year remove.

1946-anchor is used as the headline MAPE below (it's the more honest
answer to "does this benchmark predict 1947/1948," since it minimizes
compounding distance); the 1900-anchor line is kept on the plot and in
the summary because the divergence it shows IS a real, interesting
finding about how these two series' definitions diverge over the long
run, not something to hide by only ever showing the flattering number.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from curl_cffi import requests as cffi_requests

from saus_digitizer.build import PANEL_OUTPUT_PATH
from saus_digitizer.ocr import PROJECT_ROOT

FRED_SERIES_ID = "A02083USA581NNBR"
FRED_SERIES_TITLE = "Increases in Railroad Track Mileage Operated"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

FIGURES_DIR = PROJECT_ROOT / "outputs" / "figures"
VALIDATION_PLOT_PATH = FIGURES_DIR / "panel_vs_fred_validation.png"
VALIDATION_SUMMARY_PATH = PROJECT_ROOT / "outputs" / "validation_summary.json"
FINDINGS_PATH = PROJECT_ROOT / "outputs" / "findings.md"

# The anchor used for the headline MAPE (see module docstring for why
# 1946, not the more obvious-looking 1900, was picked -- it's the last
# real panel year before the 1947/1948 window actually being validated).
HEADLINE_ANCHOR_YEAR = 1946
# Kept alongside it, on the plot and in the summary, as real disclosed
# context -- not swept under the rug because it's a worse-looking number.
CONTEXT_ANCHOR_YEAR = 1900

# The only years the brief's comparison window and our real data both
# cover -- 1949/1950 don't exist in this panel (see build.py, findings.md).
MAPE_YEARS = [1947, 1948]


def fetch_fred_increases(series_id: str = FRED_SERIES_ID) -> dict[int, float]:
    """Annual increases, thousands of miles, keyed by year. Plain
    requests/curl cannot reach *.stlouisfed.org (TLS fingerprinting --
    same finding as fetch_saus.py's FRASER work); curl_cffi with Chrome
    impersonation is required here too."""
    r = cffi_requests.get(
        FRED_CSV_URL, params={"id": series_id}, impersonate="chrome", timeout=20
    )
    r.raise_for_status()
    lines = r.text.strip().split("\n")[1:]  # drop header row
    data: dict[int, float] = {}
    for line in lines:
        date_str, value_str = line.split(",")
        if value_str == ".":  # FRED's own missing-value marker
            continue
        data[int(date_str[:4])] = float(value_str)
    return data


def load_panel(panel_path: Path = PANEL_OUTPUT_PATH) -> dict[int, float]:
    with panel_path.open(newline="") as fh:
        return {
            int(row["year"]): float(row["single_track_mileage"])
            for row in csv.DictReader(fh)
        }


def cumulate_from_anchor(
    panel: dict[int, float],
    increases: dict[int, float],
    anchor_year: int,
    end_year: int = 1948,
) -> dict[int, float]:
    """Walks FRED's annual increases (thousands of miles -> miles)
    forward from the panel's OWN anchor_year value -- the most solid
    number available for that year, not an external guess. Every year
    after the anchor compounds whatever gap opened in prior years, which
    is exactly why the anchor choice matters as much as it does here."""
    if anchor_year not in panel:
        raise ValueError(f"Panel has no value for anchor year {anchor_year}")
    if anchor_year not in increases:
        raise ValueError(f"FRED series has no value for anchor year {anchor_year}")
    level = panel[anchor_year]
    implied = {anchor_year: level}
    for year in range(anchor_year + 1, end_year + 1):
        if year not in increases:
            break
        level += increases[year] * 1000  # thousands of miles -> miles
        implied[year] = level
    return implied


def compute_mape(
    panel: dict[int, float], implied: dict[int, float], years: list[int]
) -> dict:
    rows = []
    for year in years:
        if year not in panel or year not in implied:
            continue
        actual = panel[year]
        predicted = implied[year]
        pct_error = abs(actual - predicted) / actual * 100
        rows.append(
            {
                "year": year,
                "panel": round(actual, 2),
                "fred_implied": round(predicted, 2),
                "pct_error": round(pct_error, 2),
            }
        )
    mape = round(sum(r["pct_error"] for r in rows) / len(rows), 2) if rows else None
    return {"mape": mape, "years": rows}


def plot_comparison(
    panel: dict[int, float],
    implied_headline: dict[int, float],
    implied_context: dict[int, float],
    output_path: Path = VALIDATION_PLOT_PATH,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    panel_years = sorted(panel)
    headline_years = sorted(implied_headline)
    context_years = sorted(implied_context)

    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.plot(
        panel_years,
        [panel[y] for y in panel_years],
        label="Panel (OCR/manual, single-track mileage)",
        color="#1f77b4",
        linewidth=2.2,
        zorder=3,
    )
    ax.plot(
        context_years,
        [implied_context[y] for y in context_years],
        label=f"FRED {FRED_SERIES_ID}, cumulated from {CONTEXT_ANCHOR_YEAR} anchor",
        color="#d62728",
        linewidth=1.3,
        linestyle="--",
        zorder=2,
    )
    ax.plot(
        headline_years,
        [implied_headline[y] for y in headline_years],
        label=f"FRED {FRED_SERIES_ID}, cumulated from {HEADLINE_ANCHOR_YEAR} anchor (headline MAPE)",
        color="#2ca02c",
        linewidth=2.0,
        linestyle="-.",
        zorder=4,
    )
    last_panel_year = max(panel_years)
    ax.axvline(x=last_panel_year, color="gray", linestyle=":", alpha=0.6, zorder=1)
    ax.annotate(
        f"Panel ends {last_panel_year}\n(1949-1950 excluded, not silently missing --\nsee findings.md / panel_summary.json)",
        xy=(last_panel_year, panel[last_panel_year]),
        xytext=(-160, -55),
        textcoords="offset points",
        fontsize=8,
        color="dimgray",
        arrowprops={"arrowstyle": "-", "color": "gray", "alpha": 0.6},
    )
    ax.set_xlabel("Year")
    ax.set_ylabel("Single-track-equivalent mileage")
    ax.set_title(
        "SAUS single-track panel vs. FRED-implied levels (cumulated from two anchors), 1900-1948"
    )
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def write_validation_summary(
    headline_result: dict,
    context_result: dict,
    output_path: Path = VALIDATION_SUMMARY_PATH,
) -> dict:
    summary = {
        "fred_series_id": FRED_SERIES_ID,
        "fred_series_title": FRED_SERIES_TITLE,
        "fred_series_type": "flow (annual increase), NOT a level -- confirmed via declared units 'Thousands of Miles' on the FRED series page, cumulated here to reconstruct an implied level",
        "mape_years": MAPE_YEARS,
        "headline": {
            "anchor_year": HEADLINE_ANCHOR_YEAR,
            "anchor_rationale": "Last manually-verified panel year before the 1947/1948 validation window -- only 1-2 years of compounding, the most honest answer to 'does this benchmark predict 1947/1948'.",
            "mape_pct": headline_result["mape"],
            "years": headline_result["years"],
        },
        "context": {
            "anchor_year": CONTEXT_ANCHOR_YEAR,
            "anchor_rationale": "Panel's first year -- 47 years of compounding. Kept and disclosed (not hidden) because the large gap it produces is itself a real finding: it opens almost entirely during 1900-1930 and then plateaus, consistent with A02083 measuring ALL track mileage (incl. 2nd/3rd tracks added to existing routes during the double/triple-tracking era) rather than single-track/route mileage.",
            "mape_pct": context_result["mape"],
            "years": context_result["years"],
        },
        "plot": str(VALIDATION_PLOT_PATH.relative_to(PROJECT_ROOT)),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as fh:
        json.dump(summary, fh, indent=2)
    return summary


FINDINGS_TEMPLATE = """# Findings — SAUS Railroad Mileage Digitization (Assignment 5)

**TODO — Zach: write the actual ~200-word reflection here yourself, per
project rules (findings.md is not something Claude writes end-to-end for
you). Real material from this session to reflect on:**

- The reporting-lag discovery — SAUS volumes report data 1-3 years behind
  their own cover year; the most-authoritative tables turned out to be
  later volumes' own trailing-history tables (e.g. saus_1948.pdf No. 564
  covering 1890-1946), not each year's own volume.
- The Class-I-only scope trap in 1917-1918 — an early draft reading
  (saus_1920.pdf p353) accidentally used a Class-I railways subset
  instead of the full network figure; caught and corrected.
- The 1926 discrepancy — the automated pipeline's only "valid" value,
  240,293, turned out to be a real number pulled from the wrong place (a
  by-state footnote actually reporting 1910's "road owned" column, not
  1926); manual re-read against saus_1948.pdf No. 564 found the real
  value, 258,815.
- Catching two mislabeled automated values (1949, 1950) by cross-
  referencing each volume's own period-summary table against itself —
  both years' extracted figures were real numbers, just assigned to the
  wrong year (the volume's own cover year instead of the table's actual
  last reported year).
- The honest 1949-1950 gap in the final panel — not silently missing,
  documented in panel_summary.json's years_excluded field and here.
- (Optional, from Part D) The FRED anchor-year finding below — worth a
  line if there's room: the SAME benchmark series validates almost
  perfectly (MAPE 0.12%) or terribly (MAPE 38.2%) depending only on how
  far back the cumulation starts, which is itself a lesson about what
  "validation" against a flow-derived series can and can't tell you.

Delete this TODO block once written.

---

## Validation data (real, computed — reference for your write-up)

**Benchmark series**: FRED `{fred_series_id}` ("{fred_series_title}",
NBER Macrohistory / ICC-compiled, via FRASER's FRED mirror). **Not** used
as a direct level comparison — confirmed live that this series (and the
brief's other suggested candidate, `A02F2AUSA374NNBR`, "Miles of Railroad
Built") are both annual FLOWS (declared units "Thousands of Miles" and
"Miles" respectively — read off the actual FRED series pages, not
assumed from the name), not levels. Comparing either directly to the
panel's ~190,000-260,000-mile levels would be a meaningless, roughly
-100%-every-year "error" — an artifact of comparing different kinds of
quantities, not a real accuracy signal.

**Resolved by cumulating `{fred_series_id}`'s annual increases into an
implied level**, anchored to a real year from the panel itself (not an
external guess). The anchor choice turned out to matter far more than
expected:

| Anchor | Compounding distance to 1947/48 | MAPE (1947-1948) |
|---|---|---|
| **{headline_anchor} (headline)** | 1-2 years | **{headline_mape}%** |
| {context_anchor} (context, on the plot) | 47 years | {context_mape}% |

**Why both are reported, and why {headline_anchor} is the headline, not
{context_anchor}**: {context_anchor} is the panel's first year — the
"obvious" anchor — but 47 years of compounded annual increases open a
gap that turns out to open almost entirely during 1900-1930 and then
plateau (visible on the plot). That pattern is consistent with
`{fred_series_id}` measuring ALL track mileage (every additional
parallel track laid on an existing route, not just new route-miles) —
a real, different metric from this panel's single-track/route mileage,
not a bug. {headline_anchor} (the panel's last manually-verified year
right before the years being validated) minimizes that compounding —
it's the more honest answer to "does this benchmark predict 1947/1948,"
and the near-0% MAPE it produces shows the two series' *annual changes*
track closely in this mature-network era, even though their *levels*
mean different things at a distance.

| Year | Panel (real) | FRED-implied ({headline_anchor} anchor) | % error |
|------|-------------|--------------|---------|
{mape_table_rows}

**Plot**: `outputs/figures/panel_vs_fred_validation.png` — panel line,
plus both FRED-implied lines, full 1900-1948 range, panel's end clearly
marked (1949-1950 excluded, not silently missing).

**Real limitation to weigh in your reflection**: neither MAPE is a same-
year, independently-sourced accuracy check the way this panel's own
OCR-vs-manual cross-referencing was — both are built by cumulating a
flow series from *some* starting point, so both carry real anchor-choice
and compounding-error uncertainty. A tight near-anchor MAPE is
meaningful evidence the two series' short-run dynamics agree; it is not
proof the underlying levels mean the same thing (they don't — see the
1900-anchor line).
"""


def write_findings(
    headline_result: dict,
    context_result: dict,
    output_path: Path = FINDINGS_PATH,
) -> None:
    """Deliberately NOT the ~200-word reflection itself -- per project
    rule and Zach's own explicit instruction, that's his analysis to
    write, not something generated end-to-end here. This populates the
    file with the real, computed facts he needs as reference material,
    with a clear TODO for the prose."""
    table_rows = "\n".join(
        f"| {r['year']} | {r['panel']:,.2f} | {r['fred_implied']:,.2f} | {r['pct_error']}% |"
        for r in headline_result["years"]
    )
    content = FINDINGS_TEMPLATE.format(
        fred_series_id=FRED_SERIES_ID,
        fred_series_title=FRED_SERIES_TITLE,
        headline_anchor=HEADLINE_ANCHOR_YEAR,
        context_anchor=CONTEXT_ANCHOR_YEAR,
        headline_mape=headline_result["mape"],
        context_mape=context_result["mape"],
        mape_table_rows=table_rows,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)


def run_validate(panel_path: Path = PANEL_OUTPUT_PATH) -> dict:
    panel = load_panel(panel_path)
    increases = fetch_fred_increases()

    implied_headline = cumulate_from_anchor(panel, increases, HEADLINE_ANCHOR_YEAR)
    implied_context = cumulate_from_anchor(panel, increases, CONTEXT_ANCHOR_YEAR)

    headline_result = compute_mape(panel, implied_headline, MAPE_YEARS)
    context_result = compute_mape(panel, implied_context, MAPE_YEARS)

    plot_comparison(panel, implied_headline, implied_context)
    summary = write_validation_summary(headline_result, context_result)
    write_findings(headline_result, context_result)

    print(f"Validation summary: {VALIDATION_SUMMARY_PATH}")
    print(
        f"  Headline MAPE ({HEADLINE_ANCHOR_YEAR} anchor, {MAPE_YEARS}): {headline_result['mape']}%"
    )
    print(
        f"  Context MAPE ({CONTEXT_ANCHOR_YEAR} anchor, {MAPE_YEARS}): {context_result['mape']}%"
    )
    print(f"Plot: {VALIDATION_PLOT_PATH}")
    print(f"Findings scaffold: {FINDINGS_PATH} (TODO block left for Zach's own reflection)")
    return summary


if __name__ == "__main__":
    run_validate()
