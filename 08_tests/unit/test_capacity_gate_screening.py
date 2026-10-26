import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "06_src"))

from simulation.capacity_gate import CapacityCandidate, energy_screen, enumerate_domain


def test_main_and_extended_ids_never_mix():
    main = enumerate_domain(
        "main",
        {"PV_kWp": [90], "BAT_kWh": [90], "PEMFC_kW": [30], "PEMEL_kW": [25], "H2_kg": [10]},
    )
    ext = enumerate_domain(
        "extended_pv",
        {"PV_kWp": [300], "BAT_kWh": [90], "PEMFC_kW": [30], "PEMEL_kW": [25], "H2_kg": [10]},
    )
    assert main[0].candidate_id.startswith("main__")
    assert ext[0].candidate_id.startswith("extended_pv__")


def test_energy_screen_only_rejects_below_99_percent_load():
    candidate = CapacityCandidate("main__x", "main", 150, 90, 30, 25, 10)
    reject = energy_screen(candidate, {2016: 98.9}, {2016: 100.0}, min_fraction=0.99)
    keep = energy_screen(candidate, {2016: 99.0}, {2016: 100.0}, min_fraction=0.99)
    assert reject.passed is False
    assert keep.passed is True
