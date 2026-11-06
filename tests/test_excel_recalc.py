"""Recalculates the workbook in desktop Excel (COM) and compares with the Python maths.

Run explicitly: pytest -m excel
"""
import json
import subprocess
from pathlib import Path

import pytest

from helper import excel
from helper.stats import implied_price, quartiles
from tests.test_excel import COMMENTARY, PEERS, SOURCES, TARGET

SCRIPT = Path(__file__).parent / "excel_recalc.ps1"
pytestmark = pytest.mark.excel


def recalc(path: Path, addrs: list[tuple[str, str]]) -> dict:
    cells = json.dumps([{"sheet": s, "addr": a} for s, a in addrs])
    run = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), str(path), cells],
        capture_output=True, text=True, timeout=180,
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_excel_recalculation_matches_python(tmp_path):
    path = excel.build_workbook(TARGET, PEERS, COMMENTARY, SOURCES, tmp_path / "comps.xlsx")
    rows, n = excel.stats_rows(len(PEERS)), len(PEERS)
    wanted = [("Comps", f"{excel.COLUMN['ev_ebitda']}{excel.PEER_FIRST_ROW}"),
              ("Comps", f"{excel.COLUMN['ev_ebitda']}{excel.PEER_FIRST_ROW + 3}")]
    for key, _ in excel.MULTIPLES:
        wanted += [("Comps", f"{excel.COLUMN[key]}{rows[name]}") for name in ("mean", "median", "q1", "q3")]
    wanted += [("Football Field", f"{col}{r}") for r in range(5, 9) for col in "BCD"]
    got = recalc(path, wanted)

    assert got[f"Comps!{excel.COLUMN['ev_ebitda']}{excel.PEER_FIRST_ROW}"] == pytest.approx(10.5)
    assert got[f"Comps!{excel.COLUMN['ev_ebitda']}{excel.PEER_FIRST_ROW + 3}"] == "n/a"

    for i, (key, _) in enumerate(excel.MULTIPLES):
        values = [m for r in PEERS if (m := excel._python_multiple(r.profile, key)) is not None]
        q1, median, q3 = quartiles(values)
        assert got[f"Comps!{excel.COLUMN[key]}{rows['median']}"] == pytest.approx(median)
        for col, stat in zip("BCD", (q1, median, q3)):
            expected = implied_price(key, stat, TARGET)
            assert got[f"Football Field!{col}{5 + i}"] == pytest.approx(expected), (key, col)


def _random_case(seed: int):
    import random
    from helper.models import PeerRow, Profile
    rng = random.Random(seed)
    peers = [
        PeerRow(Profile(symbol=f"R{i}", name=f"R{i} Ltd", price=rng.uniform(50, 3000), market_cap_cr=rng.uniform(2000, 90000),
                        net_debt_cr=rng.uniform(-3000, 8000), revenue_cr=rng.uniform(1000, 40000),
                        ebitda_cr=rng.uniform(150, 9000), pe=rng.uniform(8, 70), pb=rng.uniform(1, 14), shares_out=1e9),
                75, "r", "m")
        for i in range(rng.randint(4, 9))
    ]
    target = Profile(symbol="TGT", name="T", price=rng.uniform(50, 3000), market_cap_cr=rng.uniform(5000, 60000),
                     net_debt_cr=rng.uniform(-1000, 5000), revenue_cr=rng.uniform(2000, 30000),
                     ebitda_cr=rng.uniform(300, 7000), pe=rng.uniform(10, 60), pb=rng.uniform(1.5, 12), shares_out=1e9)
    return target, peers


@pytest.mark.parametrize("seed", [11, 23, 37, 41, 58])
def test_random_median_value_and_upside_match_independent_python(tmp_path, seed):
    from statistics import median
    from helper.stats import trim_outliers
    target, peers = _random_case(seed)
    path = excel.build_workbook(target, peers, COMMENTARY, SOURCES, tmp_path / "r.xlsx")
    got = recalc(path, [("Football Field", f"{c}{r}") for r in range(5, 10) for c in "CG"])
    medians = []
    for i, (key, _) in enumerate(excel.MULTIPLES):
        values = [m for p in peers if (m := excel._python_multiple(p.profile, key)) is not None]
        kept, _ = trim_outliers(values)
        expected = implied_price(key, median(kept), target) if kept else None
        if expected is not None:
            medians.append(expected)
            assert got[f"Football Field!C{5 + i}"] == pytest.approx(expected), (seed, key)
            assert got[f"Football Field!G{5 + i}"] == pytest.approx(expected / target.price - 1), (seed, key)
        else:
            assert got[f"Football Field!G{5 + i}"] == "n/a"
    blended = median(medians)
    assert got["Football Field!C9"] == pytest.approx(blended)
    assert got["Football Field!G9"] == pytest.approx(blended / target.price - 1)
