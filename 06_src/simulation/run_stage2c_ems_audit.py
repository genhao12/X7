"""Stage2c hourly EMS bottleneck audit runner (extended PV domain 350-500 kWp)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "06_src") not in sys.path:
    sys.path.insert(0, str(ROOT / "06_src"))

from simulation.run_stage2b_ems_audit import run_audit


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=ROOT / "02_data_processed" / "tradestreet_pipeline" / "64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv")
    parser.add_argument("--result-root", type=Path, default=ROOT / "07_results" / "rectification_v2_stage2c")
    args = parser.parse_args(argv)
    run_audit(args.config, args.data, args.result_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
