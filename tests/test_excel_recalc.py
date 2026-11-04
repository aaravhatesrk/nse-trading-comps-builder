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
