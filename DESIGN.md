# NSE Trading Comps Builder: Design

Date: 2026-11-04. Status: draft for approval. Nothing is built yet.

## Intent and why

Aarav wants a finance n8n workflow he can show on LinkedIn as a portfolio project aimed at investment banking (IB) credibility. It must be reasonably useful, run on free data and free APIs only, and be more substantial than a news summariser. The chosen task is the trading comparables ("comps") analysis, the most common IB analyst task, for Indian listed companies (NSE).

Success: an IB reviewer opens the Excel file, clicks into cells, sees live formulas, sensible peers with stated reasons, and a football field chart. The workflow and helper are open-sourced on GitHub and demoed in a LinkedIn post.

## Goals

- Input: target NSE ticker and user criteria via an n8n form. Output: emailed Excel comps file.
- Criteria-based peer screening, each criterion either "Auto" (defaults below) or a user-entered range.
- AI (Groq free tier) judges business similarity and gives a one-line reason per peer.
- Human approval step by email before the Excel is built; the user can drop peers.
- Excel with live formulas, football field chart, AI commentary sheet, sources and assumptions sheet.
- Open-source repo: README, scrubbed workflow JSON, helper script, sample output (Marico).

## Non-goals

- No paid APIs or keys beyond free Groq and Gmail OAuth.
- US or other markets. India (NSE) only.
- Precedent transactions, DCF, merger models.
- Revenue mix and capital intensity filters (data too patchy for free sources).
- Hosting or a public service. It runs on Aarav's PC.

## Anti-goals (failure even if it technically runs)

- Numbers hard-pasted as values instead of formulas.
- Silent wrong data: missing fields shown as zero. Missing must show as "n/a" and be excluded from medians.
- LLM computing any number. Python computes all numbers; the LLM writes prose and similarity judgements only.
- Credentials or personal data in the GitHub repo.

## Constraints

- Free only. Local PC. n8n on localhost (no public tunnel assumed).
- n8n Code node has no fs or network access, so file building is done by a local Python helper called over HTTP.
- Groq model must be `openai/gpt-oss-120b` (my call, based on earlier project notes).
- The n8n MCP failed to connect on 2026-11-04 (ECONNREFUSED); it must be reconnected before workflow build.
- Wait/approval step: needs checking that a form or webhook resume URL works on localhost without a tunnel. This is an open technical risk. Fallback: approval link that points to a local helper endpoint.

## Recon results (2026-11-04, read-only tests)

- Yahoo Finance quoteSummary (cookie + crumb flow) worked for MARICO.NS and EMAMILTD.NS: market cap, EBITDA, total debt, total cash, revenue, revenue growth, EBITDA margin, EV, trailing P/E, sector, industry, 4 years of annual income statements. Emami's ROE was missing, so expect gaps.
- NSE archive CSVs are free and keyless: Nifty Total Market list (about 750 companies, with an industry tag, 20 coarse buckets) and EQUITY_L.csv (listing dates for 2,600+ symbols).
- Yahoo endpoints are unofficial and may change. Mitigation: isolate all Yahoo calls in the helper, cache results per day, fail loudly.
- Not yet tested: balance sheet modules (needed for ROCE), P/B and average volume fields, insider holding field, rate limits across 50 to 150 tickers.

## Decisions made by Aarav

Screening criteria (each editable on the form, "Auto" uses these defaults):

| Criterion | Auto default |
|---|---|
| Industry | Same NSE industry as target, then AI business-overlap check |
| Market cap | 0.5x to 3x of target |
| Revenue | Off, optional |
| EBITDA margin | Target plus or minus 10 percentage points |
| 3-year revenue CAGR | Off, optional |
| ROCE (computed from EBIT and balance sheet) | Target plus or minus 10 points |
| Net debt / EBITDA | Max 3x, net cash allowed |
| Liquidity (avg daily traded value) | Off, optional |
| Promoter holding (Yahoo insider % as labelled proxy) | Off, optional |
| Minimum listing age | 3 years |
| Always on | Positive EBITDA only; outlier trimming |

Other decisions:
- Manual include and exclude ticker list on the form.
- Multiples: EV/EBITDA, P/E, EV/Sales, P/B.
- Output: Excel with live formulas, football field, AI summary, delivered by Gmail attachment.
- Architecture: n8n plus local Python helper (openpyxl).
- Approval step: yes, by email, before the Excel is built.
- Showcase: open-source on GitHub plus a LinkedIn post.

## Flow (my proposal)

1. Form trigger: ticker, email, criteria (Auto or ranges), include/exclude list.
2. Helper: load target data; load NSE universe; filter by industry and listing age; fetch Yahoo data for candidates (cached); apply numeric filters; drop negative EBITDA.
3. LLM (Groq): rate each remaining candidate's business similarity to the target (score and one-line reason); drop low scores. Output must use a fixed JSON schema with every field required.
4. Email approval: shortlist with reasons and key metrics; user unticks and approves.
5. Helper: apply approval; trim outliers; build the workbook.
6. LLM: write the commentary on how the target compares to peers (prose only, numbers supplied by Python).
7. Gmail: send the Excel with a short summary.

## Excel workbook (my proposal)

- Sheet "Comps": one row per peer, inputs as values (with source and date), multiples as live formulas, median, mean, 25th and 75th percentile as formulas, target row separated.
- Sheet "Football Field": implied target value ranges (25th to 75th percentile) for each multiple, native Excel chart.
- Sheet "Commentary": AI summary and per-peer rationale.
- Sheet "Sources and Assumptions": data source, fetch date, criteria used, excluded peers and reasons.
- Colour convention: blue inputs, black formulas. Units in rupees crore.

## Left to the builder

- Exact outlier rule (suggest 1.5x IQR trim before computing statistics).
- LLM prompt wording and similarity cut-off.
- Caching strategy and rate limiting for Yahoo calls.
- Helper endpoints and how state is held between the screen and approval steps.
- Football field chart implementation in openpyxl.
- Test plan: Marico vs Emami and a second sector (e.g. a bank or IT company) to see how the multiples behave. P/B and EV/EBITDA are not meaningful for banks, so decide whether to handle or refuse financials.

## Builder-check resolutions (my calls, please review)

- Approval step: try an n8n Wait/form resume first. If that fails on localhost without a tunnel, fall back to two workflows: screen, then an approval page served by the local helper (checkbox list, since email cannot hold checkboxes), then build. The email carries a link to that page (the user is on the same PC). Unapproved requests expire after 24 hours and are discarded.
- Missing data: a peer missing a value for any active criterion is excluded and listed on the Sources sheet as "excluded: missing data". Missing values never appear as zero. If a default criterion (for example ROCE) cannot be fetched for most candidates, drop it for that run and say so in the email and on the Sources sheet.
- Football field: implied per-share price for each multiple at the 25th to 75th percentile, shown against the current price. EV multiples are bridged using the target's net debt and Yahoo's shares outstanding; minority interest is ignored and noted as a limitation.
- Basis: last reported fiscal year for fundamentals, current market cap and price, labelled on the sheet. Consolidated figures as Yahoo returns them.
- Acceptance check: for MARICO, the shortlist should include most of Dabur, Godrej Consumer, Emami and Colgate-Palmolive India, and exclude unrelated industries.
- Form: ticker entered as plain NSE symbol (MARICO, no .NS); each range is min and max in rupees crore or percent; a target outside the Nifty Total Market list is refused with a clear message. Banks and NBFCs are refused with a message (EV/EBITDA is not meaningful for them).

## AI similarity scoring (Aarav asked to define the criteria)

- The AI scores each candidate against a rubric, 0 to 3 per criterion, or "unknown" if the business summary does not say (unknown is not zero; its weight is shared across the others). Every score needs a one-line evidence quote. Python computes the weighted total; the AI never does.
- Criteria and starting weights (my guess, to be tuned): product/category overlap 30, customer and channel 20, business model and revenue mix 20, value drivers and cyclicality 10, operating model 10, geography and positioning 10.
- Product overlap score of 0 always excludes the candidate, however well the others match.
- For a company that matches on most criteria but differs on one, the AI must name the mismatch and say whether it likely affects the multiple.
- Input text: Yahoo business summary (field availability to be confirmed before the build).
- Cut-off (Aarav's decision, 2026-11-04): a candidate is kept if its weighted total is 65 or higher, and the product gate (product score 0 is always excluded) still applies. No secondary tier for now.
- This was calibrated on one target and one blind run (Groq openai/gpt-oss-120b, temperature 0; results in calibration/marico_scores_run1.html). Marico reference list agreed with Aarav: core DABUR, GODREJCP, EMAMILTD, BAJAJCON; borderline HINDUNILVR, COLPAL, JYOTHYLAB, TATACONSUM; out NESTLEIND, BRITANNIA. At 65 all core names are in and both "out" names are out, but the margin is thin (Britannia 63 vs Dabur and Emami 67).
- Known weakness: the generic criteria (customer/channel, business model, geography) score high for almost any Indian packaged-goods company and lift outsiders. The 65 cut-off is provisional. The builder must re-check it with a stability re-run and a second-sector target (for example IT services), and the weights may change if it fails there. Numeric filters (for example the market-cap range) run before the AI scoring and were not part of this test.
- Marico reference list is the acceptance test for this step.

## Sharing on GitHub (Aarav asked)

- The repo ships the workflow JSON (no credentials), the Python helper, and a README. Each user imports the workflow into their own n8n, connects their own Gmail OAuth and their own free Groq key, and runs the helper locally.
- The form runs at the user's own localhost n8n address, so it is not reachable from other machines.
- The result email is sent from the user's own Gmail to the address typed in the form. It never uses Aarav's account.
- A public hosted demo is out of scope.

## Decision on sample data (Aarav)

- The GitHub repo includes a sample Marico comps Excel, with a README and Sources-sheet disclaimer: data from Yahoo Finance, illustration only, not investment advice.

## Task 8 spike result (2026-11-04)

Wait-node resume works on localhost with no tunnel. A published Webhook-triggered workflow reached a Wait node (resume: webhook, 24h limit) and went `waiting`; the signed `$execution.resumeUrl` (http://localhost:5678/webhook-waiting/<id>?signature=...) resumed it to `success` when POSTed. So the approval page posts the choice to the request's `resume_url`; the polling fallback (`GET /approvals/<id>/status`) stays as a safety net. Limitation: both the n8n form and the approval link are `localhost` URLs, so the whole flow works on the machine running n8n and the helper. Anyone cloning the repo runs both locally.

## Auto defaults change (2026-11-04, user decision after first live MARICO run)
Real MARICO run with the original Auto defaults returned zero peers. At the user's direction: Auto market cap band is now 0.5x to 5x of the target (was 0.5x to 3x) and ROCE is no longer an Auto criterion (still available as an optional form range). EBITDA margin stays +/-10 points and net debt/EBITDA stays at most 3x.
