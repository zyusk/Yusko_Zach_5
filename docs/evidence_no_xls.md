# Dark-Table Proof

Required first checkpoint per the brief: *"Before you OCR, provide evidence that your
chosen table does not exist as CSV/XLS anywhere online."* This is that evidence, for all
five candidate tables the brief lists, not just the one chosen — "why this table and not
the other four" is a live interview question (Assignment 6 gate), not just planning
scratch.

## Table chosen

**Railroad mileage — single-track, state-level**, from the *Statistical Abstract of the
United States* (SAUS), FRASER title 66 (1878–1950 coverage).

## Method

For each candidate, searched the modern agency most likely to have already digitized it
(Treasury, USDA NASS, FRED/NBER Macrohistory, Census) plus a general web search for any
CSV/XLS at the *specific* granularity the SAUS table reports (state × single-track, not
just a national total). Live web search + fetch run directly in this Positron session on
2026-08-22, not reconstructed from memory. Where a page fetch was blocked (see FRED/FRASER
note below), that is stated explicitly rather than silently assumed — the underlying claim
is search-snippet-grounded, not page-verified, in those specific cases.

---

## (c) Gross and net public debt — DISQUALIFIED

- **Source found:** `fiscaldata.treasury.gov/datasets/historical-debt-outstanding/`
- **Finding:** Treasury's own Fiscal Data platform publishes annual debt outstanding
  **1789/1790 through the present**, directly downloadable as CSV (also JSON, XML, HTML,
  and API). This is a modern federal agency's own digitization of essentially the same
  series the SAUS table would report.
- **Verdict:** Fails the dark-table test outright — the CSV already exists at the source.
  Picking this table would fail Step 2 on day one.

## (b) Cotton production by state — DISQUALIFIED

- **Source found:** USDA NASS QuickStats, `quickstats.nass.usda.gov`, plus its public API
  (`catalog.data.gov/dataset/quick-stats-agricultural-database-api`).
- **Finding:** QuickStats holds state-level cotton acreage/yield/production data back into
  the early 1900s, filterable by commodity/state/year and exportable as CSV directly from
  the query tool, or programmatically via the API.
- **Verdict:** Same failure mode as debt — a modern agency already re-published this at
  state-level granularity as CSV.

## (d) Bank clearings, leading cities — partially dark, weak for Part D

- **Source found:** FRED / NBER Macrohistory, series `M12019USM191NNBR` — "Bank Clearings
  in Seven Cities Outside of New York City for United States." Confirmed via search
  snippet: monthly, not seasonally adjusted, **January 1875 – December 1914**, source
  Frickey's *Review of Economic Statistics* (1925). (Note: the earlier planning pass
  guessed the end date as 1919/1933 from a different snippet; the FRED series-page snippet
  pulled directly during this session says December 1914 — flagging the correction rather
  than carrying the wrong number forward.)
- **Finding:** This is an *aggregate* across seven cities, not the SAUS table's
  individual-city breakdown — the per-city table itself is genuinely dark. But the FRED
  comparison series stops in 1914, well short of the brief's required **1947–1950**
  validation window. Part D would have no overlapping years to compute a MAPE against.
- **Verdict:** Usable as a dark table, but disqualified on Part D grounds, not CSV
  availability grounds.

## (e) Telephone & telegraph messages sent — real prior-compilation risk

- **Source found:** U.S. Census Bureau, *Historical Statistics of the United States,
  Colonial Times to 1970* (Bicentennial Edition, 1975), Part 2, Chapter R
  ("Communications"), Series R 1–92, covering telephone/telegraph messages from 1866
  onward. Public PDF at `census.gov/library/publications/1975/compendia/hist_stats_colonial-1970.html`
  (chapter also available standalone, e.g.
  `www2.census.gov/library/publications/1960/compendia/hist_stats_colonial-1957/hist_stats_colonial-1957-chR.pdf`
  for the prior edition).
- **Finding:** Census already compiled a long-run telephone/telegraph messages-sent series
  **once before**, in their own historical-statistics compendium. It is not machine-readable
  CSV/XLS — it's a printed/scanned table in a PDF compendium — so it technically clears the
  brief's literal bar. But it means this table isn't virgin territory; a prior compilation
  exists and Seals/Altindag could plausibly know about it.
- **Verdict:** Not disqualified, but carries real risk if picked without disclosure.

## (a) Railroad mileage — single-track, state-level — CHOSEN

- **National aggregates that exist (and are not a substitute):**
  - FRED `A02083USA581NNBR`, "Increases in Railroad Track Mileage Operated for United
    States" — annual, ICC-compiled, **1877–1963** (1877–90 and 1917–63 calendar years,
    1891–1916 years ending June 30). Confirmed via FRED series-page search snippet.
  - FRED `A02F2AUSA374NNBR`, "Miles of Railroad Built for United States" — annual,
    NBER-sourced. Date range is **unresolved between sources**: one snippet says
    1830–1952, the original planning pass (from a different snippet) said 1877–90. This
    needs a direct look at the FRED page before Part D relies on it — see Open Items below.
  - Neither series is broken out by state or by single-track vs. total track. They are
    single national numbers per year.
- **Direct check of the printed compendium itself (page-verified, not snippet-grounded):**
  Fetched and read the Census Bureau's *Historical Statistics of the United States,
  1789–1945* (1949 edition), Chapter K ("Transportation"), which contains the Bureau's own
  prior railroad-mileage compilation:
  - `Series K 1–17` ("Railroads Before 1890 — Mileage, Equipment, and Passenger and Freight
    Service: 1830 to 1890") — national totals only, columns for "Road operated," "Road
    owned," and "All track."
  - `Series K 28–42` ("Railroads — Mileage, Equipment, and Passenger Service; Operating
    Steam Railways: 1890 to 1945") — national totals only, columns for "Road operated
    (rail line)," "Main track," "Other main track," "Yard tracks and sidings."
  - Neither table has a state column, and neither uses "single track" as a reported
    category — the closest analog is "main track" vs. "other main track," a different cut
    than the SAUS annual volumes' "single track" / "second, third, and other tracks" /
    "total all tracks" breakdown described in the Prompt Plan. This directly confirms, from
    the primary compendium itself rather than a search snippet, that even Census's own
    historical-statistics team never compiled a state × single-track version of this table.
  - Source file: `www2.census.gov/library/publications/1949/compendia/hist_stats_1789-1945/hist_stats_1789-1945-chK.pdf`
    (this PDF *does* carry an OCR text layer already, imperfect in places — e.g.
    "l05-118" — which is itself a useful sanity check for what OCR noise on this class of
    document typically looks like, ahead of building the pipeline in Part B).
- **General web search** (`railroad mileage single track by state historical statistics
  CSV dataset`) turned up only modern-era sources at the wrong granularity or wrong
  scope for a 1900–1950 state panel: BTS (Bureau of Transportation Statistics) Class I
  system mileage from 1960 forward, FRA operational/crossing data (current, not
  historical), and Statista's modern Class I freight-mileage chart. Nothing at
  state × single-track granularity for the 1900–1950 window.
- **Verdict:** No state-level, single-track-specific digitization found anywhere checked.
  This is a real dark table at the chosen granularity, and it has the added benefit that
  the FRED national-aggregate series (once its exact range is confirmed) gives a genuine,
  ready-made 1947–1950 benchmark for Part D — unlike bank clearings (benchmark ends 1914)
  or telegraph (already compiled once, not a clean "first digitization" story).

---

## Open items — resolve before treating this as final

- **FRED/FRASER pages return HTTP 403 to automated fetches from this session**,
  reproduced independently in this live Positron pass (not just the earlier planning
  session) — confirmed on `fred.stlouisfed.org/series/...`, the `fredgraph.csv` direct-download
  endpoint, and `alfred.stlouisfed.org`. This is a real access restriction on FRED's side
  for automated tools, not a one-off fluke — the fetch/OCR pipeline (Part A) will need to
  hit FRASER's documented REST/OAI-PMH API (per its own user-doc PDF) rather than scraping
  HTML pages, and the exact FRED series metadata below should be confirmed via a manual
  browser visit, not assumed from search snippets.
- **Resolve which FRED series is the right Part D benchmark** — `A02083USA581NNBR`
  ("Increases in...Track Mileage Operated," 1877–1963, confirmed range) vs.
  `A02F2AUSA374NNBR` ("Miles of Railroad Built," range unresolved between conflicting
  snippets — one says 1830–1952, another says 1877–90). Confirm the real range and the
  real definitional match to the SAUS table's single-track column before committing to it
  in Part D.
- **Confirm the SAUS annual volumes' actual "single track" caption wording and column
  layout directly** (not just via the 1949 decennial compendium checked above, which uses
  different category names) — needed for the Part B page-detection regex.
