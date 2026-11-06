"""Comps workbook: Comps, Football Field, Commentary, Sources and Assumptions.

Inputs are values (blue font); multiples and statistics are live formulas (black font).
Peer multiples that are statistical outliers stay visible but are excluded from the statistics.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from helper.models import Commentary, PeerRow, Profile, SourcesInfo
from helper.stats import trim_outliers

NA = "n/a"
MULTIPLES = (("ev_ebitda", "EV/EBITDA"), ("ev_sales", "EV/Sales"), ("pe", "P/E"), ("pb", "P/B"))
_COLUMNS = (
    ("symbol", "Symbol"), ("name", "Company"), ("similarity", "Similarity"), ("price", "Price (₹)"),
    ("mcap", "Market cap (₹ cr)"), ("net_debt", "Net debt (₹ cr)"), ("ev", "EV (₹ cr)"),
    ("revenue", "Revenue (₹ cr)"), ("ebitda", "EBITDA (₹ cr)"), ("eps", "EPS (₹, derived)"),
    ("bvps", "Book value/share (₹, derived)"), *MULTIPLES,
)
COLUMN = {key: chr(ord("A") + i) for i, (key, _) in enumerate(_COLUMNS)}
USED_COLUMN = {key: chr(ord("Q") + i) for i, (key, _) in enumerate(MULTIPLES)}  # statistics basis
HEADER_ROW = 5
PEER_FIRST_ROW = 6
_BLUE, _BLACK = Font(color="0000FF"), Font(color="000000")
_HEADER_FILL = PatternFill("solid", fgColor="1F2937")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_CRORE_TO_RUPEES = "10000000"
DISCLAIMER = "Illustration only, not investment advice. Data from Yahoo Finance may be incomplete or delayed."


def stats_rows(n_peers: int) -> dict[str, int]:
    last = PEER_FIRST_ROW + n_peers - 1
    return {"mean": last + 2, "median": last + 3, "q1": last + 4, "q3": last + 5}


def target_row(n_peers: int) -> int:
    return PEER_FIRST_ROW + n_peers + 6


def _num(value: float | None) -> float | str:
    return NA if value is None else value


def _ratio(num_col: str, den_col: str, row: int) -> str:
    n, d = f"{COLUMN[num_col]}{row}", f"{COLUMN[den_col]}{row}"
    return f'=IF(AND(ISNUMBER({n}),ISNUMBER({d})),IF({d}>0,{n}/{d},"{NA}"),"{NA}")'


def _formulas(row: int) -> dict[str, str]:
    mcap, debt = f"{COLUMN['mcap']}{row}", f"{COLUMN['net_debt']}{row}"
    return {
        "ev": f'=IF(AND(ISNUMBER({mcap}),ISNUMBER({debt})),{mcap}+{debt},"{NA}")',
        "ev_ebitda": _ratio("ev", "ebitda", row),
        "ev_sales": _ratio("ev", "revenue", row),
        "pe": _ratio("price", "eps", row),
        "pb": _ratio("price", "bvps", row),
    }


def _derived_per_share(price: float | None, ratio: float | None) -> float | None:
    return price / ratio if price is not None and ratio is not None and ratio > 0 else None


def _write_company_row(ws: Worksheet, row: int, p: Profile, similarity: int | str) -> None:
    inputs = {
        "symbol": p.symbol, "name": p.name or p.symbol, "similarity": similarity, "price": _num(p.price),
        "mcap": _num(p.market_cap_cr), "net_debt": _num(p.net_debt_cr), "revenue": _num(p.revenue_cr),
        "ebitda": _num(p.ebitda_cr), "eps": _num(_derived_per_share(p.price, p.pe)),
        "bvps": _num(_derived_per_share(p.price, p.pb)),
    }
    for key, value in inputs.items():
        c = ws[f"{COLUMN[key]}{row}"]
        c.value, c.font = value, _BLUE
        if key not in ("symbol", "name", "similarity"):
            c.number_format = "#,##0.00" if key in ("price", "eps", "bvps") else "#,##0"
    for key, formula in _formulas(row).items():
        c = ws[f"{COLUMN[key]}{row}"]
        c.value, c.font = formula, _BLACK
        c.number_format = '0.0"x"' if key != "ev" else "#,##0"


def _python_multiple(p: Profile, key: str) -> float | None:
    ev = None if p.market_cap_cr is None or p.net_debt_cr is None else p.market_cap_cr + p.net_debt_cr
    values = {
        "ev_ebitda": ev / p.ebitda_cr if ev is not None and p.ebitda_cr and p.ebitda_cr > 0 else None,
        "ev_sales": ev / p.revenue_cr if ev is not None and p.revenue_cr and p.revenue_cr > 0 else None,
        "pe": p.pe if p.pe and p.pe > 0 else None,
        "pb": p.pb if p.pb and p.pb > 0 else None,
    }
    return values[key]


def _trimmed(peers: list[PeerRow]) -> dict[str, dict[str, float]]:
    """{multiple: {symbol: value}} for peers whose multiple is an outlier."""
    result: dict[str, dict[str, float]] = {}
    for key, _ in MULTIPLES:
        available = [(r.profile.symbol, _python_multiple(r.profile, key)) for r in peers]
        available = [(s, v) for s, v in available if v is not None]
        _, removed = trim_outliers([v for _, v in available])
        result[key] = {available[i][0]: available[i][1] for i in removed}
    return result


def _write_comps(ws: Worksheet, target: Profile, peers: list[PeerRow]) -> dict[str, dict[str, float]]:
    ws["A1"], ws["A1"].font = f"Trading comps: {target.name or target.symbol}", Font(bold=True, size=14)
    ws["A2"] = ("Basis: trailing twelve months revenue and EBITDA (Yahoo), balance sheet as last reported; "
                "market cap and price are current. ₹ crore unless stated.")
    ws["A3"] = "Blue = input value, black = formula. Outlier multiples are marked 'trimmed' and excluded from the statistics."
    for key, title in _COLUMNS:
        c = ws[f"{COLUMN[key]}{HEADER_ROW}"]
        c.value, c.font, c.fill = title, _HEADER_FONT, _HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for key, title in MULTIPLES:
        c = ws[f"{USED_COLUMN[key]}{HEADER_ROW}"]
        c.value, c.font, c.fill = f"{title} used in statistics", _HEADER_FONT, _HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
    trimmed = _trimmed(peers)
    last = PEER_FIRST_ROW + len(peers) - 1
    for i, peer in enumerate(peers):
        row = PEER_FIRST_ROW + i
        _write_company_row(ws, row, peer.profile, peer.similarity)
        for key, _ in MULTIPLES:
            c = ws[f"{USED_COLUMN[key]}{row}"]
            c.value = "trimmed" if peer.profile.symbol in trimmed[key] else f"={COLUMN[key]}{row}"
            c.font, c.number_format = _BLACK, '0.0"x"'
    labels = {"mean": ("Mean", "AVERAGE({r})"), "median": ("Median", "MEDIAN({r})"),
              "q1": ("25th percentile", "QUARTILE({r},1)"), "q3": ("75th percentile", "QUARTILE({r},3)")}
    for name, row in stats_rows(len(peers)).items():
        label, template = labels[name]
        ws[f"B{row}"], ws[f"B{row}"].font = label, Font(bold=True)
        for key, _ in MULTIPLES:
            used = f"{USED_COLUMN[key]}{PEER_FIRST_ROW}:{USED_COLUMN[key]}{last}"
            usable = any(_python_multiple(r.profile, key) is not None and r.profile.symbol not in trimmed[key] for r in peers)
            c = ws[f"{COLUMN[key]}{row}"]
            c.value, c.font, c.number_format = ("=" + template.format(r=used)) if usable else NA, _BLACK, '0.0"x"'
    t_row = target_row(len(peers))
    ws[f"B{t_row - 1}"], ws[f"B{t_row - 1}"].font = "Target", Font(bold=True)
    _write_company_row(ws, t_row, target, "-")
    for col, width in zip("ABCDEFGHIJKLMNO", (11, 28, 10, 11, 14, 14, 14, 14, 14, 12, 14, 11, 11, 9, 9)):
        ws.column_dimensions[col].width = width
    for key, _ in MULTIPLES:
        ws.column_dimensions[USED_COLUMN[key]].width = 13
    ws.row_dimensions[HEADER_ROW].height = 45
    ws.freeze_panes = f"C{PEER_FIRST_ROW}"
    return trimmed


def _write_football_field(ws: Worksheet, target: Profile, n_peers: int) -> None:
    rows, t = stats_rows(n_peers), target_row(n_peers)
    ws["A1"], ws["A1"].font = "Football field: implied value per share (₹)", Font(bold=True, size=14)
    ws["A2"] = "Implied price from the 25th, 50th and 75th percentile of peer multiples. EV multiples are bridged with net debt and shares."
    for col, title in zip("ABCDEFG", ("Multiple", "Low (25th pct)", "Median", "High (75th pct)", "Range (high - low)", "Current price",
                                       "Upside / (downside) at median")):
        c = ws[f"{col}4"]
        c.value, c.font, c.fill = title, _HEADER_FONT, _HEADER_FILL
    inputs = {  # label, row, formula or value
        11: ("Current price (₹)", f"=Comps!{COLUMN['price']}{t}"),
        12: ("EBITDA (₹ cr)", f"=Comps!{COLUMN['ebitda']}{t}"),
        13: ("Revenue (₹ cr)", f"=Comps!{COLUMN['revenue']}{t}"),
        14: ("Net debt (₹ cr)", f"=Comps!{COLUMN['net_debt']}{t}"),
        15: ("Shares outstanding", _num(target.shares_out)),
        16: ("EPS (₹)", f"=Comps!{COLUMN['eps']}{t}"),
        17: ("Book value per share (₹)", f"=Comps!{COLUMN['bvps']}{t}"),
    }
    ws["A10"], ws["A10"].font = "Target inputs", Font(bold=True)
    for row, (label, value) in inputs.items():
        ws[f"A{row}"], ws[f"B{row}"] = label, value
        ws[f"B{row}"].font = _BLUE if row == 15 else _BLACK
        ws[f"B{row}"].number_format = "#,##0.00" if row in (11, 16, 17) else "#,##0"
    bridge = lambda s, metric: f'IFERROR(IF(({s}*{metric}-$B$14)>0,({s}*{metric}-$B$14)*{_CRORE_TO_RUPEES}/$B$15,"{NA}"),"{NA}")'
    per_share = lambda s, base: f'IFERROR(IF({base}>0,{base}*{s},"{NA}"),"{NA}")'
    implied = {
        "ev_ebitda": lambda s: bridge(s, "$B$12"), "ev_sales": lambda s: bridge(s, "$B$13"),
        "pe": lambda s: per_share(s, "$B$16"), "pb": lambda s: per_share(s, "$B$17"),
    }
    for i, (key, title) in enumerate(MULTIPLES):
        r = 5 + i
        ws[f"A{r}"] = title
        for col, stat in zip("BCD", ("q1", "median", "q3")):
            ws[f"{col}{r}"] = "=" + implied[key](f"Comps!{COLUMN[key]}{rows[stat]}")
        ws[f"E{r}"] = f'=IFERROR(D{r}-B{r},"{NA}")'
        ws[f"F{r}"] = "=$B$11"
        for col in "BCDEF":
            ws[f"{col}{r}"].number_format = "#,##0.00"
            ws[f"{col}{r}"].font = _BLACK
    ws["A9"], ws["A9"].font = "Median value (middle of the four multiples)", Font(bold=True)
    for col in "BCD":
        ws[f"{col}9"] = f'=IFERROR(MEDIAN({col}5:{col}8),"{NA}")'
    ws["E9"], ws["F9"] = f'=IFERROR(D9-B9,"{NA}")', "=$B$11"
    for col in "BCDEF":
        ws[f"{col}9"].number_format, ws[f"{col}9"].font = "#,##0.00", Font(bold=True)
    for r in range(5, 10):
        c = ws[f"G{r}"]
        c.value, c.number_format = f'=IFERROR(C{r}/$B$11-1,"{NA}")', "0.0%;[Red]-0.0%"
        c.font = Font(bold=(r == 9))
    chart = BarChart()
    chart.type, chart.grouping, chart.overlap = "bar", "stacked", 100
    price_text = "n/a" if target.price is None else f"{target.price:,.2f}"
    chart.title = f"Implied value per share (current price ₹ {price_text})"
    chart.add_data(Reference(ws, min_col=2, min_row=4, max_row=8), titles_from_data=True)
    chart.add_data(Reference(ws, min_col=5, min_row=4, max_row=8), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=5, max_row=8))
    chart.series[0].graphicalProperties.noFill = True
    chart.series[0].graphicalProperties.line.noFill = True
    chart.legend = None
    chart.x_axis.delete, chart.y_axis.delete = False, False
    chart.y_axis.title = "₹ per share"
    chart.height, chart.width = 8, 18
    ws.add_chart(chart, "H4")
    ws.column_dimensions["A"].width = 28
    for col in "BCDEF":
        ws.column_dimensions[col].width = 16
    ws.column_dimensions["G"].width = 22
    ws.row_dimensions[4].height = 32
    for col in "ABCDEFG":
        ws[f"{col}4"].alignment = Alignment(wrap_text=True, vertical="center")


def _write_commentary(ws: Worksheet, peers: list[PeerRow], commentary: Commentary) -> None:
    ws["A1"], ws["A1"].font = "Summary", Font(bold=True, size=14)
    ws["A2"] = commentary.summary
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A2:E2")
    ws.row_dimensions[2].height = 90
    for col, title in zip("ABCDE", ("Symbol", "Similarity", "Why a peer", "Mismatch", "Commentary")):
        c = ws[f"{col}4"]
        c.value, c.font, c.fill = title, _HEADER_FONT, _HEADER_FILL
    for i, peer in enumerate(peers):
        row = 5 + i
        values = (peer.profile.symbol, peer.similarity, peer.reason, peer.mismatch_note, commentary.per_peer.get(peer.profile.symbol, ""))
        for col, value in zip("ABCDE", values):
            ws[f"{col}{row}"] = value
            ws[f"{col}{row}"].alignment = Alignment(wrap_text=True, vertical="top")
    for col, width in zip("ABCDE", (12, 11, 45, 45, 55)):
        ws.column_dimensions[col].width = width


def _write_sources(ws: Worksheet, target: Profile, sources: SourcesInfo, trimmed: dict[str, dict[str, float]]) -> None:
    titles = dict(MULTIPLES)
    rows: list[tuple[str, str]] = [
        ("Data source", "Yahoo Finance (unofficial endpoints) and NSE public lists (Nifty Total Market, EQUITY_L)."),
        ("Data fetched on", sources.fetched_on.isoformat()),
        ("Fundamentals basis", "Revenue and EBITDA are trailing twelve months, balance sheet as last reported; market cap and price current; consolidated as Yahoo returns."),
        ("Units", "Rupees crore unless stated."),
        ("Derived inputs", "EPS = price / trailing P/E and book value per share = price / P/B (Yahoo provides the ratios)."),
        ("Enterprise value", "Market cap + net debt. Minority interest is ignored."),
        ("Football field", "Implied per-share price at the 25th, 50th and 75th percentile of peer multiples; EV multiples bridged with the target's net debt and shares outstanding."),
        ("Outlier rule", "Peer multiples outside 1.5x the interquartile range are trimmed from the statistics."),
    ]
    rows += [("Criteria used", line) for line in sources.criteria_lines]
    rows += [("Criterion dropped", note) for note in sources.dropped_criteria]
    rows += [("Excluded peer", f"{e.symbol}: {e.reason}") for e in sources.excluded]
    rows += [("Trimmed outlier", f"{symbol}: {titles[key]} {value:.1f}x trimmed from statistics")
             for key, found in trimmed.items() for symbol, value in found.items()]
    rows.append(("Disclaimer", DISCLAIMER))
    ws["A1"], ws["A1"].font = "Sources and assumptions", Font(bold=True, size=14)
    for i, (label, text) in enumerate(rows):
        ws[f"A{3 + i}"], ws[f"B{3 + i}"] = label, text
        ws[f"A{3 + i}"].font = Font(bold=True)
        ws[f"B{3 + i}"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110


def build_workbook(target: Profile, peers: list[PeerRow], commentary: Commentary, sources: SourcesInfo, out: Path) -> Path:
    wb = Workbook()
    comps = wb.active
    comps.title = "Comps"
    trimmed = _write_comps(comps, target, peers)
    _write_football_field(wb.create_sheet("Football Field"), target, len(peers))
    _write_commentary(wb.create_sheet("Commentary"), peers, commentary)
    _write_sources(wb.create_sheet("Sources and Assumptions"), target, sources, trimmed)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
