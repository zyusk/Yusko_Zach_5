# Findings — SAUS Railroad Mileage Digitization (Assignment 5)


> This project's real lesson wasn't that OCR misreads digits -- I expected
> that. It's that a perfectly legible number can still be wrong for the
> table it's supposedly from. My automated pipeline once returned 240,293
> for 1926 with total confidence; the number was real, just pulled from a
> by-state footnote reporting 1910's data, not 1926's. Cross-referencing
> against saus_1948.pdf's own retrospective table (No. 564) caught it --
> the corrected value, 258,815, actually fits the surrounding trend where
> 240,293 didn't.
>
> The 1917-1918 Class-I scope trap taught me the same lesson differently:
> an early reading used a Class-I-railways-only subset instead of the
> full network figure, and both numbers looked entirely plausible on
> their own. I only caught it by finding a second, independent table
> covering the same years.
>
> I also learned SAUS volumes report data 1-3 years behind their own
> cover year -- the most reliable source for 1930's mileage wasn't the
> 1930 volume, it was a later volume's own retrospective table.
>
> 1949 and 1950 stayed out of my final panel. Both years' only automated
> extraction turned out to be real numbers mislabeled with the wrong
> year -- rather than guess which was right, I left them out and
> documented exactly why.




---

## Validation data (real, computed — reference for your write-up)

**Benchmark series**: FRED `A02083USA581NNBR` ("Increases in Railroad Track Mileage Operated",
NBER Macrohistory / ICC-compiled, via FRASER's FRED mirror). **Not** used
as a direct level comparison — confirmed live that this series (and the
brief's other suggested candidate, `A02F2AUSA374NNBR`, "Miles of Railroad
Built") are both annual FLOWS (declared units "Thousands of Miles" and
"Miles" respectively — read off the actual FRED series pages, not
assumed from the name), not levels. Comparing either directly to the
panel's ~190,000-260,000-mile levels would be a meaningless, roughly
-100%-every-year "error" — an artifact of comparing different kinds of
quantities, not a real accuracy signal.

**Resolved by cumulating `A02083USA581NNBR`'s annual increases into an
implied level**, anchored to a real year from the panel itself (not an
external guess). The anchor choice turned out to matter far more than
expected:

| Anchor | Compounding distance to 1947/48 | MAPE (1947-1948) |
|---|---|---|
| **1946 (headline)** | 1-2 years | **0.12%** |
| 1900 (context, on the plot) | 47 years | 38.19% |

**Why both are reported, and why 1946 is the headline, not
1900**: 1900 is the panel's first year — the
"obvious" anchor — but 47 years of compounded annual increases open a
gap that turns out to open almost entirely during 1900-1930 and then
plateau (visible on the plot). That pattern is consistent with
`A02083USA581NNBR` measuring ALL track mileage (every additional
parallel track laid on an existing route, not just new route-miles) —
a real, different metric from this panel's single-track/route mileage,
not a bug. 1946 (the panel's last manually-verified year
right before the years being validated) minimizes that compounding —
it's the more honest answer to "does this benchmark predict 1947/1948,"
and the near-0% MAPE it produces shows the two series' *annual changes*
track closely in this mature-network era, even though their *levels*
mean different things at a distance.

| Year | Panel (real) | FRED-implied (1946 anchor) | % error |
|------|-------------|--------------|---------|
| 1947 | 238,209.00 | 238,369.00 | 0.07% |
| 1948 | 237,756.00 | 238,169.00 | 0.17% |

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



