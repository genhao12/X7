import sys, time, json
sys.path.insert(0, r'd:\matlab\C\06_src')
import numpy as np
from ems.ems_stage2b import Stage2BEMSConfig, EMSState, dispatch_stage2b
from components.annual_input_contract import load_evaluation_years, scale_pv_available
from simulation.run_stage3_full_analysis import build_c1_config
from simulation.run_double_layer_propagation import precompute_inner, efc_k_weighted, sobol_sample_params, PARAM_DEFS

admissions = json.loads(open(r'07_results/rectification_v2_stage2c/search/verified_admissions.json', encoding='utf-8').read())
rep = admissions['representatives'][0]
loaded = load_evaluation_years(r'02_data_processed/tradestreet_pipeline/64_TradeStreet_2016_2018_synthetic_reconstructed_PV_building_load_evaluation_years.csv')
frame = loaded[2017]
cfg_ems = Stage2BEMSConfig(pv_kwp=float(rep['pv_kwp']), bat_kwh=float(rep['bat_kwh']), pemfc_kw=float(rep['pemfc_kw']), pemel_kw=float(rep['pemel_kw']), h2_kg=float(rep['h2_kg']))
pv_scaled = scale_pv_available(frame['pv_raw_kw'].to_numpy(dtype=float), nominal_kw=float(rep['pv_kwp']))
load = frame['load_kw'].to_numpy(dtype=float)
c1 = build_c1_config(rep)

t0 = time.perf_counter()
cmds = dispatch_stage2b(load, pv_scaled, cfg_ems, strategy='battery_first', initial_state=EMSState(soc=0.5, h2_kg=cfg_ems.h2_kg*0.5)).commands
t1 = time.perf_counter()
inner = precompute_inner(cmds, 4000, 42, c1)
t2 = time.perf_counter()
outer = sobol_sample_params(256, seed=20260803)
m = np.zeros((256, 4000))
for i in range(256):
    params = {n: outer[n][i] for n in PARAM_DEFS}
    m[i] = inner['bat_cal'] + params['BAT_EFC_A_D'] * efc_k_weighted(inner['bat_hists'], params['BAT_EFC_K'])
t3 = time.perf_counter()
print(f'compact: dispatch={t1-t0:.2f}s | precompute_inner(4000)={t2-t1:.2f}s | outer-fill(256x4000 bat)={t3-t2:.2f}s', flush=True)
print(f'  outer-fill scales ~linearly: 1000~{0.25*(t3-t2):.2f}s, 2000~{0.5*(t3-t2):.2f}s, 4000~{t3-t2:.2f}s', flush=True)
total_one_cfg = (t1-t0) + (t2-t1) + 0.25*(t3-t2) + 0.5*(t3-t2) + (t3-t2)
print(f'  est. one-config total (3 checkpoints): {total_one_cfg:.1f}s', flush=True)
print(f'  est. 5 configs total: {5*total_one_cfg:.0f}s = {5*total_one_cfg/60:.1f} min', flush=True)
