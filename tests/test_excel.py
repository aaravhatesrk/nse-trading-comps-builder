from datetime import date

import pytest
from openpyxl import load_workbook

from helper import excel
from helper.models import Commentary, Exclusion, PeerRow, Profile, SourcesInfo

BLUE = "0000FF"


def make_peer(symbol, price, mcap, nd, rev, ebitda, pe, pb, sim=75) -> PeerRow:
    return PeerRow(
        Profile(symbol=symbol, name=f"{symbol} Ltd", price=price, market_cap_cr=mcap, net_debt_cr=nd,
                revenue_cr=rev, ebitda_cr=ebitda, pe=pe, pb=pb, shares_out=1e9),
        similarity=sim, reason=f"{symbol} sells similar products", mismatch_note="smaller scale",
    )


TARGET = Profile(
    symbol="TGT", name="Target Ltd", price=100.0, market_cap_cr=10000.0, net_debt_cr=500.0, revenue_cr=5000.0,
    ebitda_cr=1000.0, pe=25.0, pb=10.0, shares_out=1e9, fiscal_year_end="2026-03-31",
)
PEERS = [
    make_peer("P1", 200, 20000, 1000, 8000, 2000, 20, 5),
    make_peer("P2", 50, 6000, -500, 3000, 600, 10, 2.5),
    make_peer("P3", 300, 30000, 2000, 10000, 2500, 30, 6),
    make_peer("P4", 80, 8000, 0, 4000, None, 15, 4),
]
COMMENTARY = Commentary("Target trades at a premium to peers.", {"P1": "Closest peer.", "P2": "Smaller.", "P3": "Larger.", "P4": "No EBITDA."})
SOURCES = SourcesInfo(
    fetched_on=date(2026, 11, 4),
    criteria_lines=("market cap 5,000 to 30,000 cr",),
    excluded=(Exclusion("BADCO", "negative EBITDA"),),
    dropped_criteria=("roce filter dropped: data missing for 5 of 7 candidates",),
)


@pytest.fixture
def wb(tmp_path):
    path = excel.build_workbook(TARGET, PEERS, COMMENTARY, SOURCES, tmp_path / "comps.xlsx")
    return load_workbook(path)


def cell(ws, key, row):
    return ws[f"{excel.COLUMN[key]}{row}"]


def test_all_four_sheets_exist(wb):
    assert wb.sheetnames == ["Comps", "Football Field", "Commentary", "Sources and Assumptions"]


def test_multiples_are_live_formulas(wb):
    ws, row = wb["Comps"], excel.PEER_FIRST_ROW
    ev_ebitda = cell(ws, "ev_ebitda", row).value
    assert ev_ebitda.startswith("=")
    assert f"{excel.COLUMN['ev']}{row}" in ev_ebitda and f"{excel.COLUMN['ebitda']}{row}" in ev_ebitda
    for key in ("ev_sales", "pe", "pb"):
        assert str(cell(ws, key, row).value).startswith("=")


def test_enterprise_value_is_formula_of_market_cap_and_net_debt(wb):
    ws, row = wb["Comps"], excel.PEER_FIRST_ROW
    ev = cell(ws, "ev", row).value
    assert ev.startswith("=") and f"{excel.COLUMN['mcap']}{row}" in ev and f"{excel.COLUMN['net_debt']}{row}" in ev


def test_statistics_rows_are_formulas(wb):
    ws = wb["Comps"]
    rows = excel.stats_rows(len(PEERS))
    assert cell(ws, "ev_ebitda", rows["mean"]).value.startswith("=AVERAGE(")
    assert cell(ws, "ev_ebitda", rows["median"]).value.startswith("=MEDIAN(")
    assert cell(ws, "ev_ebitda", rows["q1"]).value.startswith("=QUARTILE(")
    assert cell(ws, "ev_ebitda", rows["q3"]).value.endswith(",3)")


def test_inputs_are_blue_and_formulas_are_black(wb):
    ws, row = wb["Comps"], excel.PEER_FIRST_ROW
    assert cell(ws, "mcap", row).font.color.rgb.endswith(BLUE)
    assert cell(ws, "ebitda", row).font.color.rgb.endswith(BLUE)
    assert not cell(ws, "ev_ebitda", row).font.color.rgb.endswith(BLUE)
    assert not cell(ws, "ev", row).font.color.rgb.endswith(BLUE)


def test_missing_value_is_na_text_not_zero(wb):
    ws = wb["Comps"]
    p4_row = excel.PEER_FIRST_ROW + 3
    assert cell(ws, "ebitda", p4_row).value == "n/a"


def test_target_row_sits_below_statistics(wb):
    ws = wb["Comps"]
    target_row = excel.target_row(len(PEERS))
    assert ws[f"A{target_row}"].value == "TGT"
    assert target_row > excel.stats_rows(len(PEERS))["q3"]


def test_football_field_has_chart_and_formulas(wb):
    ws = wb["Football Field"]
    assert len(ws._charts) == 1
    assert str(ws["B5"].value).startswith("=") and "Comps" in str(ws["B5"].value)


def test_commentary_sheet_has_summary_and_per_peer_text(wb):
    texts = [str(c.value) for row in wb["Commentary"].iter_rows() for c in row if c.value]
    assert any("premium to peers" in t for t in texts)
    assert any("Closest peer." in t for t in texts)
    assert any("sells similar products" in t for t in texts)


def test_sources_sheet_has_disclaimer_exclusions_and_basis(wb):
    texts = " | ".join(str(c.value) for row in wb["Sources and Assumptions"].iter_rows() for c in row if c.value)
    assert "Yahoo Finance" in texts and "not investment advice" in texts
    assert "BADCO" in texts and "negative EBITDA" in texts
    assert "roce filter dropped" in texts
    assert "trailing twelve months" in texts


def test_trimmed_outlier_is_excluded_from_statistics_and_listed(tmp_path):
    peers = [make_peer(s, 100, 1000, 0, 500, 100, 20, 3) for s in ("A", "B", "C", "D")]
    # EV/EBITDA 10x for A-D, 80x for the outlier E
    peers.append(make_peer("E", 100, 8000, 0, 500, 100, 20, 3))
    path = excel.build_workbook(TARGET, peers, Commentary("s", {}), SOURCES, tmp_path / "t.xlsx")
    wb = load_workbook(path)
    ws = wb["Comps"]
    used_col = excel.USED_COLUMN["ev_ebitda"]
    assert ws[f"{used_col}{excel.PEER_FIRST_ROW + 4}"].value == "trimmed"
    assert str(ws[f"{used_col}{excel.PEER_FIRST_ROW}"].value).startswith("=")
    texts = " | ".join(str(c.value) for row in wb["Sources and Assumptions"].iter_rows() for c in row if c.value)
    assert "E: EV/EBITDA" in texts and "trimmed" in texts


def test_basis_label_says_trailing_twelve_months_not_fiscal_year(wb):
    text = " ".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value)
    assert "trailing twelve months" in text and "fiscal year ended" not in text
