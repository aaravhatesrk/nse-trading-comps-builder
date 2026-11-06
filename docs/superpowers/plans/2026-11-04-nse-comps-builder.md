# NSE Trading Comps Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An n8n form workflow that screens NSE peers for a target company, has Groq score business similarity, asks the user to approve the shortlist, and emails an Excel comps file with live formulas and a football field.

**Architecture:** A local Python (Flask) helper owns all data fetching, filtering, maths, state and Excel building. n8n owns the form, the Groq calls, the approval wait and Gmail. n8n talks to the helper over HTTP on localhost. The LLM only writes similarity scores and prose; Python computes every number.

**Tech Stack:** Python 3.11, Flask, requests, openpyxl, pytest; n8n (local); Groq `openai/gpt-oss-120b`; Gmail OAuth.

**Spec:** `DESIGN.md`

## Global Constraints

- Free only: no paid APIs. Groq free tier model must be `openai/gpt-oss-120b`.
- Python computes all numbers. Missing data is `None`/"n/a", never zero, and is excluded from medians.
- Ticker input is a plain NSE symbol (`MARICO`, no `.NS`). Target outside the Nifty Total Market list is refused with a clear message. Banks and NBFCs are refused with a message.
- Auto defaults: same NSE industry; market cap 0.5x to 3x of target; EBITDA margin target plus or minus 10 points; ROCE (computed) target plus or minus 10 points; net debt/EBITDA max 3x with net cash allowed; minimum listing age 3 years; revenue, 3-year CAGR, liquidity and promoter/insider holding are off by default. Always on: positive EBITDA only, outlier trimming.
- Similarity weights: product 30, customer_channel 20, business_model 20, value_drivers 10, operating_model 10, geography_positioning 10. Scores 0 to 3 or null; null weight is shared across the rest. Product score 0 always excludes. Keep a candidate if weighted total is 65 or higher (provisional).
- Multiples: EV/EBITDA, P/E, EV/Sales, P/B. Football field shows implied per-share price (25th to 75th percentile) against current price; EV multiples bridged with net debt and shares outstanding; minorities ignored and noted.
- Basis: last reported fiscal year fundamentals, current market cap and price, labelled on the sheet. Units: rupees crore.
- Excel: inputs as values with source and date, multiples and statistics as live formulas, blue inputs and black formulas; sheets Comps, Football Field, Commentary, Sources and Assumptions. Sources sheet carries the Yahoo disclaimer (illustration only, not investment advice).
- Approval: helper-served checkbox page linked from the email; requests expire after 24 hours. Try an n8n Wait/form resume first; fall back to helper-served page if it fails on localhost.
- Immutable data objects (frozen dataclasses). No secrets in the repo.

## Review Focus

1. Ticker typed as `marico`, ` MARICO `, or `MARICO.NS`: normalized to `MARICO`; anything not in the universe is refused, not a crash.
2. Yahoo returns missing fields, HTTP 429, or an expired crumb mid-run: retry then `None`; the peer is excluded with "missing data", never scored as zero.
3. Fewer than 3 peers survive screening or approval (plan's call): email explains which filters removed peers instead of sending a near-empty comps.
4. Groq returns invalid JSON, a score outside 0 to 3, or missing criteria: validate, retry once, then exclude that peer with a reason.
5. User unticks every peer, submits twice, or opens an expired approval link: clear message and no duplicate email.

---

## File Structure

```
helper/models.py      frozen dataclasses: Profile, Range, Criteria, Exclusion, ScreenResult, PeerRow
helper/universe.py    NSE CSV loading (Nifty Total Market + EQUITY_L listing dates)
helper/yahoo.py       crumb session, fetch_profile, daily file cache
helper/metrics.py     ROCE, 3-year CAGR, net debt/EBITDA from statements
helper/screen.py      normalize_ticker, auto_criteria, apply_filters, support checks
helper/scoring.py     validate_llm_output, weighted_total, decide
helper/stats.py       trim_outliers, quartiles, implied_price
helper/excel.py       build_workbook
helper/store.py       approval request store with 24h expiry
helper/server.py      Flask endpoints
workflow/comps-builder.json   exported n8n workflow (scrubbed)
calibration/          calibrate.py, marico_reference.json, results
tests/                one test file per helper module
README.md, requirements.txt, LICENSE
```

### Task 1: Scaffold and data layer

**Files:** Create `requirements.txt`, `helper/models.py`, `helper/universe.py`, `helper/yahoo.py`, `tests/test_universe.py`, `tests/test_yahoo.py`, `tests/fixtures/` (recorded Yahoo JSON for MARICO, EMAMILTD; a trimmed NSE CSV). Run `git init`, create a venv, install requirements.

**Interfaces:**
- Produces: `Profile` (symbol, name, nse_industry, summary, market_cap_cr, revenue_cr, ebitda_cr, ebitda_margin, net_debt_cr, ev_cr, pe, pb, roce, revenue_cagr_3y, avg_daily_value_cr, insider_pct, listing_date, shares_out, price, fiscal_year_end; every numeric field `float | None`).
- `load_universe(cache_dir: Path) -> list[UniverseRow]` (symbol, name, industry, listing_date).
- `fetch_profile(symbol: str, cache_dir: Path) -> Profile` (daily cache; retries 429 with backoff, three attempts).

- [ ] **Step 1: Write failing tests** `test_loads_industry_and_listing_date` (fixture CSV: MARICO has industry "Fast Moving Consumer Goods" and a non-null listing date), `test_fetch_profile_maps_fields_to_crore` (fixture: MARICO market cap 1151189778432 becomes 115118.98 cr), `test_missing_field_is_none_not_zero` (EMAMILTD ROE-style missing value yields `None`), `test_second_call_uses_cache` (network function called once).
- [ ] **Step 2: Run** `pytest tests/test_universe.py tests/test_yahoo.py -v`. Expect FAIL (modules missing).
- [ ] **Step 3: Implement** the three modules. Yahoo flow: GET `fc.yahoo.com` for cookie, GET `getcrumb`, then `quoteSummary` with modules `price,summaryDetail,defaultKeyStatistics,financialData,assetProfile,incomeStatementHistory,balanceSheetHistory`. Convert rupees to crore (divide by 1e7).
- [ ] **Step 4: Run tests.** Expect PASS. Run one live smoke test marked `@pytest.mark.live` for MARICO; expect a non-null market cap and non-empty summary.
- [ ] **Step 5: Commit** `feat(data): add universe loader and Yahoo profile fetch`.

### Task 2: Metrics from statements

**Files:** Create `helper/metrics.py`, `tests/test_metrics.py`.

**Interfaces:** `roce(ebit: float | None, total_assets: float | None, current_liabilities: float | None) -> float | None`; `revenue_cagr(revenues_newest_first: list[float | None], years: int = 3) -> float | None`; `net_debt_to_ebitda(debt, cash, ebitda) -> float | None`. `fetch_profile` (Task 1) calls these to fill `roce`, `revenue_cagr_3y`.

- [ ] **Step 1: Spike (throwaway)**: confirm the balance sheet and income statement module fields exist for MARICO, EMAMILTD and DABUR (EBIT, total assets, current liabilities, 4 years of revenue). Record which are missing in `calibration/data_notes.md`. If ROCE inputs are mostly unavailable, stop and escalate to Aarav before continuing (spec says drop the criterion with a notice).
- [ ] **Step 2: Write failing tests** `test_roce_basic` (EBIT 100, assets 1000, current liabilities 200 gives 0.125), `test_roce_none_when_input_missing`, `test_cagr_three_years` (revenue 100, 110, 121, 133.1 newest first reversed correctly gives 0.10), `test_cagr_none_when_fewer_than_four_values`, `test_net_debt_negative_when_net_cash`, `test_zero_or_negative_ebitda_returns_none`.
- [ ] **Step 3: Run, expect FAIL. Implement. Run, expect PASS.** Wire into `fetch_profile`; re-run Task 1 tests.
- [ ] **Step 4: Commit** `feat(metrics): ROCE, CAGR and leverage helpers`.

### Task 3: Criteria and screening

**Files:** Create `helper/screen.py`, `tests/test_screen.py`.

**Interfaces:**
- Consumes: `Profile`, `UniverseRow`, `Criteria`, `Range`, `Exclusion`.
- Produces: `normalize_ticker(raw: str) -> str`; `check_target_supported(row: UniverseRow | None, profile: Profile) -> None` (raises `UnsupportedTarget(message)` for not-in-universe, banks/NBFCs by Financial Services industry, or negative EBITDA target); `auto_criteria(target: Profile, overrides: Mapping[str, Any]) -> Criteria`; `apply_filters(target, candidates: list[Profile], criteria: Criteria) -> ScreenResult` with `kept` and `excluded` (every exclusion has a reason string, including "missing data: roce").

- [ ] **Step 1: Write failing tests**: `test_normalize_ticker` (`" marico.ns "` gives `MARICO`), `test_auto_market_cap_band` (target 100000 cr gives 50000 to 300000), `test_override_replaces_auto_range`, `test_negative_ebitda_peer_excluded_with_reason`, `test_recent_ipo_excluded_by_listing_age`, `test_missing_active_criterion_excludes_with_missing_data_reason`, `test_net_cash_peer_passes_leverage_cap`, `test_include_list_forces_peer_and_exclude_list_removes`, `test_bank_target_refused`.
- [ ] **Step 2: Run, expect FAIL. Implement. Run, expect PASS.** Filters run in the order industry, listing age, positive EBITDA, then numeric ranges.
- [ ] **Step 3: Commit** `feat(screen): criteria defaults and peer filters`.

### Task 4: Similarity scoring maths

**Files:** Create `helper/scoring.py`, `tests/test_scoring.py`.

**Interfaces:** `WEIGHTS: dict[str, int]`, `CUTOFF = 65`; `validate_llm_output(raw: Mapping) -> dict[str, int | None]` (raises `BadLlmOutput` for missing criterion or score outside 0 to 3); `weighted_total(scores: Mapping[str, int | None]) -> int | None`; `decide(scores) -> Decision(total, kept, reason)`.

- [ ] **Step 1: Write failing tests** `test_all_threes_is_100`, `test_null_weight_is_shared` (product 3, all others null gives 100), `test_all_null_returns_none`, `test_product_zero_excluded_despite_high_total`, `test_cutoff_inclusive` (total exactly 65 kept; 64 not), `test_validate_rejects_score_four`, `test_validate_rejects_missing_criterion`, `test_known_marico_scores_reproduce_run1` (use JYOTHYLAB-like scores from calibration run to confirm total 83).
- [ ] **Step 2: Run, expect FAIL. Implement. Run, expect PASS.**
- [ ] **Step 3: Commit** `feat(scoring): weighted similarity total and cutoff`.

### Task 5: Statistics and valuation

**Files:** Create `helper/stats.py`, `tests/test_stats.py`.

**Interfaces:** `trim_outliers(values: Sequence[float]) -> tuple[list[float], list[int]]` (1.5x IQR; returns kept values and removed indexes); `quartiles(values) -> tuple[float, float, float]`; `implied_price(multiple: str, peer_multiple: float, target: Profile) -> float | None` for `ev_ebitda`, `ev_sales`, `pe`, `pb`; EV bridge uses `target.net_debt_cr` and `target.shares_out`; returns `None` when an input is missing or target EBITDA is not positive.

- [ ] **Step 1: Write failing tests**: `test_outlier_removed` (values 10, 11, 12, 11, 60 removes the 60), `test_no_outliers_unchanged`, `test_quartiles_match_numpy_linear`, `test_implied_price_pe` (EPS-based: multiple 20, target price 100 at PE 25 gives 80), `test_implied_price_ev_ebitda_bridges_net_debt` (hand-computed example), `test_implied_price_none_when_shares_missing`.
- [ ] **Step 2: Run, expect FAIL. Implement. Run, expect PASS.**
- [ ] **Step 3: Commit** `feat(stats): outlier trimming and implied price`.

### Task 6: Excel builder

**Files:** Create `helper/excel.py`, `tests/test_excel.py`.

**Interfaces:** Consumes `Profile`, `stats`. `build_workbook(target: Profile, peers: list[PeerRow], commentary: Commentary, sources: SourcesInfo, out: Path) -> Path`. `PeerRow` = Profile plus similarity total, reason, mismatch note, trimmed flag. Sheets: Comps, Football Field, Commentary, Sources and Assumptions.

- [ ] **Step 1: Write failing tests**: `test_multiples_are_formulas` (EV/EBITDA cell string starts with `=`), `test_median_mean_quartiles_are_formulas`, `test_inputs_are_blue_formulas_black`, `test_missing_value_cell_is_na_and_excluded_from_median`, `test_football_field_chart_present`, `test_trimmed_outlier_listed_on_sources_sheet`, `test_disclaimer_text_on_sources_sheet`, `test_all_four_sheets_exist`.
- [ ] **Step 2: Run, expect FAIL. Implement.** Median and quartile formulas reference only non-trimmed rows. Football field: stacked bar (low as invisible series, range as visible) of implied price; current price as a marker line.
- [ ] **Step 3: Run tests, expect PASS.** Then open the generated file in Excel and confirm formulas recalculate and the chart renders; record in the commit message. If LibreOffice is installed, add a recalculation test comparing a median to `stats.quartiles`.
- [ ] **Step 4: Commit** `feat(excel): comps workbook with live formulas and football field`.

### Task 7: Helper server and approval store

**Files:** Create `helper/store.py`, `helper/server.py`, `tests/test_store.py`, `tests/test_server.py`.

**Interfaces (HTTP, JSON):**
- `POST /screen` body `{ticker, criteria_overrides, include, exclude}` returns `{target, candidates: [{symbol, name, summary}], excluded: [{symbol, reason}], criteria_used}` or `{error}` (422) for unsupported targets.
- `POST /rank` body `{target, scored: [{symbol, scores, evidence, mismatch_note}]}` returns `{shortlist, rejected}` using `scoring.decide`; invalid LLM output goes to `rejected` with reason.
- `POST /approvals` body `{email, shortlist, context}` returns `{id, url}`; `GET /approve/<id>` renders checkboxes; `POST /approve/<id>` records choices and calls the n8n resume URL stored in the request; expired or already-submitted ids return a clear page.
- `POST /build` body `{approval_id, commentary}` returns `{file_path}`.

`store.py`: `create(payload) -> str`, `get(id) -> Request | None`, `submit(id, chosen: list[str]) -> bool` (false if expired after 24 hours or already submitted).

- [ ] **Step 1: Write failing tests**: store: `test_request_expires_after_24h` (injectable clock), `test_double_submit_rejected`; server: `test_screen_unknown_ticker_422_with_message`, `test_rank_applies_cutoff_and_product_gate`, `test_rank_rejects_malformed_llm_entry`, `test_approve_page_lists_peers`, `test_approve_with_zero_peers_rejected`.
- [ ] **Step 2: Run, expect FAIL. Implement. Run, expect PASS.**
- [ ] **Step 3: Commit** `feat(server): screen, rank, approval and build endpoints`.

### Task 8: n8n workflow

**Files:** Create `workflow/comps-builder.json` (exported); n8n workflow built through the n8n MCP.

**Prerequisite:** n8n MCP reachable (it failed with ECONNREFUSED on 2026-11-04). Helper running on `localhost`. Groq and Gmail credentials created in n8n by Aarav.

**Flow:** Form Trigger (ticker, email, per-criterion Auto/min/max, include, exclude) → HTTP `/screen` → IF error, email the message → Loop over candidates → Groq (rubric prompt from `calibration/calibrate.py`, JSON output, temperature 0) → HTTP `/rank` → IF fewer than 3 shortlisted, email explanation → HTTP `/approvals` → email with approval link → Wait for resume → Groq commentary (numbers passed in, prose only) → HTTP `/build` → Gmail with attachment. Required structured-output fields all required with placeholder defaults (your n8n notes: optional fields break Groq tool-calling). Space adjacent closing braces in expressions. Call `publish_workflow` after every update.

- [ ] **Step 1: Spike the Wait/resume on localhost.** Build a two-node test (Wait → Gmail). If the resume URL does not work without a tunnel, switch to the fallback in Global Constraints and record the decision in `DESIGN.md`.
- [ ] **Step 2: Build the workflow** node by node; run it with the form for MARICO and Aarav's own email.
- [ ] **Step 3: Verify end to end**: the email arrives, the approval page loads from the email link, approving builds the file, the final email attaches an Excel that opens with live formulas and a football field. Test the exact link a recipient would click.
- [ ] **Step 4: Verify the failure paths**: unknown ticker, a bank, too few peers, expired link, double submit. Each ends in a clear email or page.
- [ ] **Step 5: Commit** the exported JSON: `feat(workflow): n8n comps builder`.

### Task 9: Calibration re-checks

**Files:** Create `calibration/calibrate.py` (generalises the spike: target, reference file, runs, writes HTML), `calibration/marico_reference.json`, `calibration/second_target.json`.

- [ ] **Step 1: Stability**: run the scoring for MARICO twice more with identical settings. Report each reference name's score spread. Pass if no reference name crosses 65 between runs; otherwise report to Aarav before changing anything.
- [ ] **Step 2: Second sector**: Aarav and Claude agree a core, borderline and out reference list for a non-FMCG target (for example an IT services company) before running. Run blind; report where the list lands.
- [ ] **Step 3: Acceptance check** on the full workflow output for MARICO: shortlist includes most of DABUR, GODREJCP, EMAMILTD, COLPAL (design acceptance list) after numeric filters, and excludes unrelated industries. Report honestly if it does not.
- [ ] **Step 4: Decide with Aarav** whether to change the cut-off or weights based on steps 1 to 3; record the decision in `DESIGN.md`. Delete `groq.key` from the scratchpad.
- [ ] **Step 5: Commit** `test(calibration): stability and second-sector runs`.

### Task 10: Open-source packaging

**Files:** Create `README.md`, `LICENSE`, `samples/marico_comps.xlsx`; scrub `workflow/comps-builder.json`.

- [ ] **Step 1: Scrub**: remove credential ids, email addresses, webhook URLs and any key from the workflow JSON; grep the repo for `gsk_`, `@gmail`, `.key`. Expect no hits.
- [ ] **Step 2: README**: setup steps (Python venv, `pip install -r requirements.txt`, start helper, import workflow, connect own Gmail and Groq, run the form), how emails are sent from the user's own Gmail, the form being local-only, the Yahoo disclaimer, known limits (unofficial endpoints, missing fields, banks refused).
- [ ] **Step 3: Fresh-machine test**: clone into a new folder, follow the README only, run MARICO. Fix the README wherever a step fails.
- [ ] **Step 4: Commit** `docs: README, license and sample output`.

---

## Self-Review

- Spec coverage: form and Auto defaults (Tasks 3, 8), universe and Yahoo data (1, 2), AI scoring and cut-off (4, 8, 9), outlier trimming and multiples (5), Excel sheets and conventions (6), approval and expiry (7, 8), failure messages (3, 7, 8), Gmail delivery (8), GitHub packaging and disclaimer (10), calibration (9).
- Types: `Profile`, `Criteria`, `PeerRow`, `Decision` are defined once in Tasks 1, 3, 4, 6 and referenced by those names later.
- Open items carried from the spec: n8n MCP reconnect, Wait/resume on localhost, ROCE input availability (Task 2 spike), cut-off validity (Task 9).
