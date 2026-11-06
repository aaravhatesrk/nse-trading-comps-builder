# NSE Trading Comps Builder

Type an NSE ticker into a form. Get back an Excel trading-comps workbook by email.

The workflow screens the Nifty Total Market universe for companies in the same industry, asks an AI to score how similar each business is, lets you approve the shortlist, then emails a workbook with live formulas, a football-field chart, short AI commentary and a sources sheet. It runs on free data and free APIs, on your own machine.

A sample output for Marico is in [`samples/marico_comps.xlsx`](samples/marico_comps.xlsx).

## How it works

1. **Form** (n8n): ticker, your email, and optional ranges. Leave a range blank for Auto.
2. **Screen** (Python helper): same NSE industry, listed at least 3 years, then numeric filters. Auto means market cap 0.5x to 5x of the target, EBITDA margin within 10 points, net debt / EBITDA at most 3x, positive EBITDA.
3. **Score** (Groq, free tier): the AI scores each candidate 0 to 3 on six criteria (product, customer and channel, business model, value drivers, operating model, geography and positioning) with a one-line evidence note each. **Python does all the arithmetic.** The AI only supplies the scores and the prose.
4. **Shortlist**: 70 and above is pre-ticked, 60 to 69 is shown unticked as "needs your review", below 60 is dropped. A product score of 0 always drops a peer. In Auto mode, companies from 0.15x to 0.5x of the target's size are also scored, but always need your review.
5. **Approve**: you get an email with a link to a page where you tick the peers you want.
6. **Workbook**: outlier multiples are trimmed (1.5x the interquartile range), then the workbook is built and emailed from your own Gmail.

The workbook has four sheets: Comps (live formulas), Football Field (chart), Commentary, and Sources and Assumptions (criteria used, excluded peers and why, disclaimer).

## What you need

- Python 3.11 or newer
- A self-hosted n8n running on the same machine (the helper listens on `127.0.0.1:5057`, so n8n Cloud cannot reach it)
- A free [Groq](https://console.groq.com) API key
- A Gmail account connected to n8n with OAuth (see n8n's Gmail credential docs)

## Setup

```bash
git clone <this repo>
cd nse-comps-builder

python -m venv .venv
# Windows:  .venv\Scripts\activate        macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

python -m helper.server        # leave this running; listens on http://127.0.0.1:5057
```

Then in n8n:

1. **Import** `workflow/comps-builder.json` (Workflows, then Import from file).
2. **Create credentials**: a *Groq* credential with your API key, and a *Gmail OAuth2* credential.
3. **Attach them**: open the two Groq nodes (`Score Similarity With Groq`, `Write Commentary With Groq`) and the five Gmail nodes (the ones starting `Email ...`) and pick your credentials. n8n shows missing credentials in red.
4. **Publish** the workflow. The approval step waits for your click, and that only works on a published workflow.
5. Open the `Comps Request Form` node and copy its **Production URL**.

## Run it

1. Open the form URL. Enter a ticker (for example `MARICO`) and your email. Leave the ranges blank.
2. Wait. Each candidate takes about 20 seconds to score, because the free Groq tier allows 8,000 tokens per minute. A typical run takes a few minutes; a big industry can take 10 to 15.
3. Open the approval email, tick the peers you want, and press the button.
4. The workbook arrives in your inbox.

Ranges are optional: market cap and revenue are in rupees crore, EBITDA margin and ROCE are in percent. `Include` and `Exclude` take comma-separated tickers. Use `Include` to force a peer that the filters removed.

## Known limits

- **Local only.** The form and the approval link are `localhost` addresses. The whole flow works on the machine running n8n and the helper, and nowhere else.
- **Yahoo Finance data is unofficial.** The helper uses Yahoo's unofficial endpoints. They can change or break without notice, some fields are missing for some companies, and figures may be delayed. A missing field makes a company fail a filter that needs it (the reason is listed on the Sources sheet).
- **Banks and NBFCs are refused.** EV/EBITDA is not meaningful for them. A target with missing or negative EBITDA is refused too.
- **AI scores vary between runs.** Even at temperature 0, scores for the same pair can move by 10 to 25 points for some peers. That is why 60 to 69 goes to you for review instead of being decided automatically.
- **Auto filters can remove real peers.** For Marico, Emami (too small) and Colgate-Palmolive (EBITDA margin too high) never reach scoring. Type them in `Include` if you want them.
- **Groq free-tier limits.** About 200,000 tokens per day. A run of 40 candidates uses roughly 80,000.
- **Approval links expire after 24 hours.**
- **Universe.** Peers come from the Nifty Total Market index list, matched by NSE industry. Companies outside it are not considered.

## Tests

```bash
pytest
```

Runs the unit tests (no network). Tests marked `live` (real network) and `excel` (needs desktop Excel on Windows) are skipped by default.

`calibration/` holds the scripts and results used to check the scoring against hand-picked reference lists for Marico (FMCG) and Infosys (IT services). See `DESIGN.md` for the full design and every decision.

## Disclaimer

Data comes from Yahoo Finance and public NSE lists. It is for illustration only, may be incomplete or delayed, and is **not investment advice**. Check any figure against company filings before relying on it. This project is not affiliated with Yahoo, NSE, Groq or Google.

## License

MIT, see [LICENSE](LICENSE).
