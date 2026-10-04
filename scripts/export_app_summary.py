"""Export every number the app's explainer ("guide") shows into one auditable file.

  uv run python scripts/export_app_summary.py            # -> app/data/summary.json
  uv run python scripts/export_app_summary.py --bundle   # also app/data/summary.js (window.APP_SUMMARY, for file://)
  uv run python scripts/export_app_summary.py --results DIR --out DIR   # read/write elsewhere (testing)

Reads whatever results exist. Closed-loop (phase2a/2b_metrics.json) and lesion (phase3_lesions.json) blocks are null
until those files appear. Model and plant constants are imported from the code (brain.py, plant.py, baselines.py),
never retyped. Every exported number has an entry in the parallel "_source" map: "<file>:<key path>" for a copied
value, "derived: ..." for one computed here. The self-check (__main__) re-reads the sources and asserts equality.
"""
import argparse, inspect, json, math, os, re, sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import baselines, brain, nulls, plant, run_phase1, run_phase2, run_phase3, stats  # noqa: E402  (brian2/jsbsim load lazily)

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = 'https://github.com/aryawidjaja/flight-test-the-fly'
DEG = 180 / math.pi
TEST_SEEDS = run_phase2.TEST


def _clean(x):
    """JSON-safe: numpy -> python, inf -> 'inf' / '-inf' (JSON has no Infinity), nan -> null."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        x = float(x)
        return None if math.isnan(x) else ('inf' if x == math.inf else '-inf' if x == -math.inf else x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


class Out:
    def __init__(self):
        self.d, self.src = {}, {}

    def put(self, path, value, src):
        node = self.d
        keys = path.split('.')
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = _clean(value)
        self.src[path] = src


def get(d, path):
    for k in path.split('.'):
        d = d[int(k)] if isinstance(d, list) else d[k]
    return d


def load(results, name):
    p = Path(results) / name
    return json.loads(p.read_text()) if p.exists() else None


def ci(c):
    """analyze_phase2 compare/h2b dict -> {mean, lo, hi} from its percentile CI (the pre-registered verdict CI)."""
    return dict(mean=c['mean'], lo=c['percentile']['lo'], hi=c['percentile']['hi'], n=c['n'])


def h4_code(v):
    return ('supported' if v == 'supported' else 'not_supported' if v.startswith('not supported')
            else 'untestable' if v.startswith('untestable') else 'not_testable' if v.startswith('not testable') else 'other')


def build(results):
    o = Out()
    R = lambda n: load(results, n)
    o.put('meta.created', datetime.now(timezone.utc).isoformat(), 'export time')
    o.put('meta.repo_url', REPO_URL, 'constant')
    o.put('meta.generator', 'scripts/export_app_summary.py', 'constant')
    from importlib.metadata import version
    o.put('meta.versions', {k: version(k) for k in ('brian2', 'jsbsim', 'numpy', 'scipy')}, 'importlib.metadata.version')
    o.put('meta.python', '.'.join(map(str, sys.version_info[:3])), 'sys.version_info')
    for k, fn in (('git_hash_closed_loop', 'phase2b_metrics.json'),):
        mm = R(fn)
        if mm:
            o.put(f'meta.{k}', mm['meta'].get('git_hash'), f'{fn}:meta.git_hash')
    o.put('meta.data_version', brain.DATA_VERSION, 'brain.py:DATA_VERSION')

    # ---------------- model constants, straight from the code
    S = brain.SHIU
    for k, name, scale in [('v_0', 'v_rest_mV', 1e3), ('v_rst', 'v_reset_mV', 1e3), ('v_th', 'v_th_mV', 1e3),
                           ('t_mbr', 'tau_m_ms', 1e3), ('tau', 'tau_s_ms', 1e3), ('t_rfc', 't_ref_ms', 1e3),
                           ('t_dly', 't_delay_ms', 1e3), ('w_syn', 'w_syn_mV', 1e3)]:
        o.put(f'model.{name}', round(S[k] * scale, 6), f'brain.py:SHIU.{k} (x{scale:g})')
    o.put('model.r_base_hz', brain.R_BASE, 'brain.py:R_BASE')
    o.put('model.g_hz_per_deg_s', brain.G_DEFAULT, 'brain.py:G_DEFAULT')
    o.put('model.bin_ms', brain.BIN * 1e3, 'brain.py:BIN')
    fc = inspect.signature(brain.FlyController).parameters
    o.put('model.washout_tau_s', fc['washout_tau'].default, 'brain.py:FlyController(washout_tau=)')
    o.put('model.warmup_s', fc['warmup_s'].default, 'brain.py:FlyController(warmup_s=)')
    o.put('model.ctrl_dt_ms', fc['ctrl_dt'].default * 1e3, 'brain.py:FlyController(ctrl_dt=)')
    o.put('model.sim_dt_ms', inspect.signature(brain.Brain).parameters['dt'].default * 1e3, 'brain.py:Brain(dt=)')
    o.put('model.n_swaps_per_edge', inspect.signature(nulls.shuffle).parameters['swaps_per_edge'].default,
          'nulls.py:shuffle(swaps_per_edge=)')
    o.put('model.roll_pid', plant.ROLL_PID, 'plant.py:ROLL_PID')
    o.put('model.jsbsim_hz', 1 / (plant.DT / plant.JSB_SUB), 'derived: 1 / (plant.py:DT / JSB_SUB)')
    o.put('model.n_boot', inspect.signature(stats.bootstrap_ci).parameters['n_boot'].default, 'stats.py:bootstrap_ci(n_boot=)')
    o.put('tuning.margin_protocol', dict(n_sines=len(run_phase2.MARGIN['freqs']) - 1, f_min_hz=min(run_phase2.MARGIN['freqs']),
                                         f_max_sine_hz=sorted(run_phase2.MARGIN['freqs'])[-2], f_nyquist_hz=max(run_phase2.MARGIN['freqs']),
                                         A=run_phase2.MARGIN['A'], settle_s=run_phase2.MARGIN['settle'],
                                         min_window_s=run_phase2.MARGIN['min_window']), 'run_phase2.py:MARGIN')
    o.put('tuning.K_base_n', run_phase2.K_BASE_N, 'run_phase2.py:K_BASE_N')
    o.put('protocol.bode_n_freqs', len(run_phase1.FREQS), 'derived: len(run_phase1.py:FREQS)')
    o.put('protocol.bode_amp_deg_s', run_phase1.AMP, 'run_phase1.py:AMP')
    o.put('model.damper_tau_s', inspect.signature(baselines.YawDamper).parameters['tau'].default,
          'baselines.py:YawDamper(tau=)')

    # ---------------- wiring
    pc = Path(results) / '00_pathway_check.txt'
    if pc.exists():
        mt = re.search(r'cells: T4/T5 (\d+) \| HS/VS/H2 (\d+) \| DNa02\+DNg02 (\d+)', pc.read_text())
        for k, v in zip(('n_t4t5_cells', 'n_hs_vs_h2_cells', 'n_dn_cells'), mt.groups()):
            o.put(f'wiring.{k}', int(v), '00_pathway_check.txt: "cells:" line')
    if brain.SUB_PATH.exists():
        sm = json.loads(str(np.load(brain.SUB_PATH)['meta']))
        o.put('wiring.threshold_synapses', sm['threshold'], 'data/sub/subcircuit_v783_t5.npz:meta.threshold')
        o.put('wiring.max_hops', sm['hops'], 'data/sub/subcircuit_v783_t5.npz:meta.hops')
        z = np.load(brain.SUB_PATH)
        ct = z['cell_type'].astype(str)
        src = np.array([bool(re.fullmatch(brain.SRC_RE, c)) for c in ct])
        o.put('wiring.n_sources', int(src.sum()), 'derived: count of T4/T5 cell types in data/sub/subcircuit_v783_t5.npz')
        o.put('wiring.n_lif', int((~src).sum()), 'derived: count of non-T4/T5 cells in data/sub/subcircuit_v783_t5.npz')
        for g, pat in (('HS', r'HS[ENS]'), ('VS', r'VS\d+'), ('H2', 'H2'), ('DNa02', 'DNa02'), ('DNg02', r'DNg02_[a-h]')):
            o.put(f'wiring.n_{g}', int(sum(bool(re.fullmatch(pat, c)) for c in ct)),
                  f'derived: count of cell_type ~ {pat} in data/sub/subcircuit_v783_t5.npz')
    sc = R('subcircuit_stats.json')
    if sc:
        for k in ('n_neurons', 'n_edges', 'n_sim_edges', 'n_edges_into_t4t5'):
            o.put(f'wiring.{k}', sc[k], f'subcircuit_stats.json:{k}')
        o.put('wiring.hs_frac_from_t4t5', sc['hs_input']['frac_from_t4t5'], 'subcircuit_stats.json:hs_input.frac_from_t4t5')
        o.put('wiring.net_weight_real', sc['net_lif_to_lif_signed_weight']['real'],
              'subcircuit_stats.json:net_lif_to_lif_signed_weight.real')
        o.put('wiring.net_weight_shuffle', sc['net_lif_to_lif_signed_weight']['shuffle_seed0_simulated_edges'],
              'subcircuit_stats.json:net_lif_to_lif_signed_weight.shuffle_seed0_simulated_edges')
        o.put('wiring.net_weight_superseded', sc['net_lif_to_lif_signed_weight']['superseded_shuffle_seed0_all_edges'],
              'subcircuit_stats.json:net_lif_to_lif_signed_weight.superseded_shuffle_seed0_all_edges')
        o.put('wiring.hs_frac_t4t5_from_a', sc['hs_input']['frac_of_t4t5_input_that_is_a'],
              'subcircuit_stats.json:hs_input.frac_of_t4t5_input_that_is_a')
        for k, v in sc.get('type_null', {}).items():   # coverage of the cell-type-preserving null
            o.put(f'wiring.type_null.{k}', v, f'subcircuit_stats.json:type_null.{k}')

    # ---------------- plant
    pd = R('plant_c172x_derivs.json')
    if pd:
        t, dv = pd['trim'], pd['derivs']
        o.put('plant.V_mps', t['vt_mps'], 'plant_c172x_derivs.json:trim.vt_mps')
        o.put('plant.V_kts', t['vt_kts'], 'plant_c172x_derivs.json:trim.vt_kts')
        o.put('plant.h_ft', t['h_sl_ft'], 'plant_c172x_derivs.json:trim.h_sl_ft')
        for k in ('weight_lbs', 'cg_x_in', 'cg_y_in', 'cg_z_in'):
            o.put(f'plant.{k}', t[k], f'plant_c172x_derivs.json:trim.{k}')
        o.put('plant.Yb_V', dv['Yb_V'], 'plant_c172x_derivs.json:derivs.Yb_V')
        o.put('plant.Nb', dv['Nb'], 'plant_c172x_derivs.json:derivs.Nb')
        o.put('plant.Nr', dv['Nr'], 'plant_c172x_derivs.json:derivs.Nr')
        o.put('plant.N_cmd', pd['lin2']['N_cmd'], 'plant_c172x_derivs.json:lin2.N_cmd')
        o.put('plant.Ycmd_V', pd['lin2']['Ycmd_V'], 'plant_c172x_derivs.json:lin2.Ycmd_V')
        o.put('plant.rudder_full_deg', pd['rudder_mapping']['norm_to_rad'] * DEG,
              'derived: plant_c172x_derivs.json:rudder_mapping.norm_to_rad in deg')
        for k, src in (('six_dof', 'full_13state'), ('two_state', 'two_state')):
            m = pd['dutch_roll'][src]
            o.put(f'plant.dutch_roll.{k}.wn', m['wn'], f'plant_c172x_derivs.json:dutch_roll.{src}.wn')
            o.put(f'plant.dutch_roll.{k}.zeta', m['zeta'], f'plant_c172x_derivs.json:dutch_roll.{src}.zeta')
            o.put(f'plant.dutch_roll.{k}.f_hz', m['wn'] / (2 * math.pi), f'derived: dutch_roll.{src}.wn / 2 pi')
        sig, L = plant.dryden_params('moderate', t['h_sl_ft'])
        o.put('plant.dryden.sigma_mps', sig, 'plant.py:dryden_params("moderate", trim.h_sl_ft)')
        o.put('plant.dryden.L_m', L, 'plant.py:dryden_params("moderate", trim.h_sl_ft)')
        o.put('plant.dryden.T_s', L / t['vt_mps'], 'derived: L / trim.vt_mps (plant.py:dryden_v T = L/V)')
    dep = plant.DEPARTURE
    o.put('plant.departure.phi_deg', dep['phi'] * DEG, 'plant.py:DEPARTURE.phi')
    o.put('plant.departure.beta_deg', dep['beta'] * DEG, 'plant.py:DEPARTURE.beta')
    o.put('plant.departure.r_deg_s', dep['r'] * DEG, 'plant.py:DEPARTURE.r')
    o.put('plant.departure.hold_s', dep['hold_s'], 'plant.py:DEPARTURE.hold_s')
    o.put('plant.dt_ms', plant.DT * 1e3, 'plant.py:DT')
    o.put('plant.flight_s', inspect.signature(plant.simulate).parameters['T'].default, 'plant.py:simulate(T=)')
    bl = R('baselines_yaw_damper.json')
    if bl:
        o.put('plant.bare_rms_r_train_deg_s', bl['lin2']['bare']['mean_rms_r'] * DEG,
              'derived: baselines_yaw_damper.json:lin2.bare.mean_rms_r in deg/s (train seeds)')

    # ---------------- tuning protocol (selection exists before any test result)
    o.put('tuning.margin_req.gm_db', baselines.MARGIN_REQ['gm_db'], 'baselines.py:MARGIN_REQ.gm_db')
    o.put('tuning.margin_req.pm_deg', baselines.MARGIN_REQ['pm_deg'], 'baselines.py:MARGIN_REQ.pm_deg')
    o.put('tuning.train_seeds', baselines.TRAIN_SEEDS, 'baselines.py:TRAIN_SEEDS')
    o.put('tuning.test_seeds', TEST_SEEDS, 'run_phase2.py:TEST')
    o.put('tuning.n_shuffles_closed_loop', len(run_phase2.WIRINGS) - 1, 'derived: len(run_phase2.py:WIRINGS) - 1')
    o.put('protocol.bode_seeds', list(run_phase1.SEEDS), 'run_phase1.py:SEEDS')
    o.put('protocol.bode_warmup_s', run_phase1.WARM, 'run_phase1.py:WARM')
    o.put('protocol.bode_min_cycles', run_phase1.n_cycles(0.1), 'run_phase1.py:n_cycles (minimum)')
    o.put('protocol.lesion_fractions', nulls.FRACTIONS, 'nulls.py:FRACTIONS')
    o.put('protocol.hub_fractions', nulls.HUB_FRACTIONS, 'nulls.py:HUB_FRACTIONS')
    o.put('protocol.lesion_n_shuffles', len(run_phase3.WIRINGS) - 1, 'derived: len(run_phase3.py:WIRINGS) - 1')
    o.put('protocol.lesion_plant', run_phase3.PLANT, 'run_phase3.py:PLANT')
    sel = R('phase2_selection.json')
    if sel:
        o.put('tuning.K_fly', sel['meta']['K_fly'], 'phase2_selection.json:meta.K_fly')
        o.put('tuning.K_damper', sel['meta']['K_damper'], 'phase2_selection.json:meta.K_damper')
        o.put('tuning.n_grid', len(sel['meta']['K_fly']), 'derived: len(phase2_selection.json:meta.K_fly)')
        for pl in ('lin2', 'jsbsim'):
            for c in ('fly_real', 'yaw_damper'):
                v = sel[pl][c]
                for k in ('selected_K', 'selected_gm_db', 'selected_pm_deg'):
                    o.put(f'tuning.selected.{pl}.{c}.{k[9:]}', v[k], f'phase2_selection.json:{pl}.{c}.{k}')
                o.put(f'tuning.selected.{pl}.{c}.unconstrained_K', v['unconstrained']['K'], f'phase2_selection.json:{pl}.{c}.unconstrained.K')
                o.put(f'tuning.selected.{pl}.{c}.margin_bound', v['unconstrained']['K'] != v['selected_K'],
                      f'derived: phase2_selection.json:{pl}.{c}.unconstrained.K != selected_K (margin rule changed the choice)')
        fly_loop(o, results, sel['lin2']['fly_real'])
    else:
        o.put('tuning.selected', None, 'phase2_selection.json missing')

    # ---------------- open loop (Bode)
    b = R('phase1_bode.json')
    if b:
        f = np.array(b['freqs'])
        W, T = b['wirings'], b['type_null_wirings']
        shuf = [w for w in W if w != 'real']
        real = W['real']
        o.put('bode.freqs', f, 'phase1_bode.json:freqs')
        o.put('bode.amp_deg_s', b['amp']['amplitudes_deg_s'][-1], 'phase1_bode.json:amp.amplitudes_deg_s[-1]')
        o.put('bode.n_seeds', real['n_seeds'], 'phase1_bode.json:wirings.real.n_seeds')
        o.put('bode.n_shuffles', len(shuf), 'derived: count of phase1_bode.json:wirings.shuf*')
        o.put('bode.n_type_shuffles', len(T), 'derived: count of phase1_bode.json:type_null_wirings.*')
        for k in ('gain', 'phase_deg', 'coh', 'coh_std', 'gain_std', 'phase_circstd_deg'):
            o.put(f'bode.real.{k}', real[k], f'phase1_bode.json:wirings.real.{k}')
        for tag, grp, src in (('shuffle', [W[w] for w in shuf], 'wirings.shuf*'),
                              ('type_shuffle', list(T.values()), 'type_null_wirings.*')):
            for k in ('gain', 'coh'):
                a = np.array([g[k] for g in grp])
                for st, fn in (('min', np.min), ('median', np.median), ('max', np.max)):
                    o.put(f'bode.{tag}.{k}_{st}', fn(a, 0), f'derived: {st} over phase1_bode.json:{src}.{k} per frequency')
        tph = np.array([np.degrees(np.unwrap(np.radians(t['phase_deg']))) for t in T.values()])
        tph -= 360 * np.floor(tph[:, :1] / 360)          # first point in [0, 360), like the real wiring's ~180 deg
        o.put('bode.type_shuffle.phase_median', np.median(tph, 0),
              'derived: median over type_null_wirings.* of the unwrapped phase_deg per frequency')
        o.put('bode.noise_q95', [p['q95'] for p in b['coherence_null']['per_f']], 'phase1_bode.json:coherence_null.per_f[*].q95')
        i1 = int(np.argmin(abs(f - 1.0)))
        g = np.array(real['gain'])
        o.put('bode.rel_db_vs_1hz', 20 * np.log10(g / g[i1]), 'derived: 20 log10(wirings.real.gain / gain at 1 Hz)')
        df = real['delay_fit']
        o.put('bode.delay_fit.tau_ms', df['tau_ms'], 'phase1_bode.json:wirings.real.delay_fit.tau_ms')
        o.put('bode.delay_fit.f_min_hz', min(df['f_used']), 'derived: min(wirings.real.delay_fit.f_used)')
        o.put('bode.delay_fit.n_points', df['n_points'], 'phase1_bode.json:wirings.real.delay_fit.n_points')
        o.put('bode.delay_fit.rms_resid_deg', df['rms_resid_deg'], 'phase1_bode.json:wirings.real.delay_fit.rms_resid_deg')
        if pd:
            fdr = pd['dutch_roll']['full_13state']['wn'] / (2 * math.pi)
            o.put('bode.delay_lag_at_dutch_roll_deg', 360 * fdr * df['tau_ms'] / 1e3,
                  'derived: 360 * (dutch_roll.full_13state.wn / 2 pi) * delay_fit.tau_ms')
            m = (f >= 0.25) & (f <= 0.45)   # measured frequencies bracketing the Dutch roll (0.262, 0.425 Hz)
            o.put('bode.measured_lag_near_dutch_roll_deg', float(np.max(180 - np.abs(np.array(real['phase_deg'])[m]))),
                  'derived: max(180 - |wirings.real.phase_deg|) at 0.25 <= f <= 0.45 Hz')
            o.put('bode.measured_lag_near_dutch_roll_f', f[m], 'phase1_bode.json:freqs (0.25-0.45 Hz)')
        le2, le1 = f <= 2.0, f <= 1.0
        o.put('bode.coh_range_le_2hz', [min(np.array(real['coh'])[le2]), max(np.array(real['coh'])[le2])],
              'derived: min/max wirings.real.coh at f <= 2 Hz')
        o.put('bode.phase_range_le_1hz', [min(np.abs(real['phase_deg'])[le1]), max(np.abs(real['phase_deg'])[le1])],
              'derived: min/max |wirings.real.phase_deg| at f <= 1 Hz')
        o.put('bode.flat_gain_range', [float(g[f <= 5].min()), float(g[f <= 5].max())],
              'derived: min/max wirings.real.gain at f <= 5 Hz')
        o.put('bode.H1_coh_threshold', 0.5, 'pre-registered H1 threshold (phase1_bode.json:H1.a.rule)')
        assert '0.5' in b['H1']['a']['rule']

        # 1 Hz test
        H, H2 = b['H1'], b['H1_secondary_type_null']
        o.put('h1.f_hz', f[i1], 'phase1_bode.json:freqs (1 Hz)')
        o.put('h1.verdict', H['verdict'], 'phase1_bode.json:H1.verdict')
        o.put('h1.a_pass', H['a']['pass'], 'phase1_bode.json:H1.a.pass')
        o.put('h1.b_pass', H['b']['pass'], 'phase1_bode.json:H1.b.pass')
        for tag, src in (('degree', 'H1.c'), ('type', 'H1_secondary_type_null.c')):
            c = get(b, src)
            for k in ('coherence', 'gain'):
                x = c[k]
                v = np.array(list(x['null'].values()))
                o.put(f'h1.{tag}.{k}.real', x['real'], f'phase1_bode.json:{src}.{k}.real')
                o.put(f'h1.{tag}.{k}.null', x['null'], f'phase1_bode.json:{src}.{k}.null')
                o.put(f'h1.{tag}.{k}.median', np.median(v), f'derived: median of phase1_bode.json:{src}.{k}.null')
                o.put(f'h1.{tag}.{k}.max', v.max(), f'derived: max of phase1_bode.json:{src}.{k}.null')
                for kk in ('rank', 'p', 'n_null'):
                    o.put(f'h1.{tag}.{k}.{kk}', x[kk], f'phase1_bode.json:{src}.{k}.{kk}')
        # identified signs
        neg = [w for w in shuf if W[w]['identified_sign'] == -1]
        coh1 = {w: H['c']['coherence']['null'][w] for w in shuf}
        coherent = [w for w in shuf if coh1[w] > 0.5]
        o.put('signs.degree_neg', len(neg), 'derived: count wirings.shuf*.identified_sign == -1')
        o.put('signs.degree_n', len(shuf), 'derived: count wirings.shuf*')
        o.put('signs.degree_coherent', len(coherent), 'derived: count H1.c.coherence.null > 0.5')
        o.put('signs.degree_coherent_neg', sum(W[w]['identified_sign'] == -1 for w in coherent),
              'derived: of those, identified_sign == -1')
        o.put('signs.type_stabilizing', sum(t['identified_sign'] == 1 for t in T.values()),
              'derived: count type_null_wirings.*.identified_sign == +1')
        rr = real['dna02_rates']
        o.put('resting.DNa02_L_hz', rr['DNa02_L_warm_hz'], 'phase1_bode.json:wirings.real.dna02_rates.DNa02_L_warm_hz')
        o.put('resting.DNa02_R_hz', rr['DNa02_R_warm_hz'], 'phase1_bode.json:wirings.real.dna02_rates.DNa02_R_warm_hz')
        # amplitude sweep (exploratory)
        A = b['amp']
        amps = A['amplitudes_deg_s']
        o.put('amplitude.amps_deg_s', amps, 'phase1_bode.json:amp.amplitudes_deg_s')
        for k in ('coh', 'gain'):
            o.put(f'amplitude.real.{k}', [A['wirings']['real'][str(int(a))][k] for a in amps],
                  f'phase1_bode.json:amp.wirings.real.<amp>.{k}')
            sv = np.array([[A['wirings'][w][str(int(a))][k] for a in amps] for w in shuf], float)
            o.put(f'amplitude.shuffle_max.{k}', np.nanmax(sv, 0), f'derived: nanmax over amp.wirings.shuf*.<amp>.{k}')
        o.put('amplitude.real_coh_rank1_all', bool(all(
            np.nan_to_num(np.array([A['wirings'][w][str(int(a))]['coh'] for w in shuf], float), nan=-1).max()
            < A['wirings']['real'][str(int(a))]['coh'] for a in amps)), 'derived: real coh > every shuffle at every amplitude')
        # g sensitivity
        gs = b['gsens']['by_g']
        o.put('gsens.g_values', [float(k) for k in gs], 'phase1_bode.json:gsens.by_g keys')
        o.put('gsens.min_coh_le_2hz', min(min(np.array(v['coh'])[np.array(v['f']) <= 2]) for v in gs.values()),
              'derived: min coherence at f <= 2 Hz over gsens.by_g.*')

    # ---------------- full-brain check
    fb = R('phase1_fullbrain_check.json')
    if fb:
        rows = fb['rows']
        fr = fb['meta']['freqs']
        mean = lambda m, f, k: float(np.mean([r[k] for r in rows if r['model'] == m and r['f'] == f]))
        sg, fg = [mean('subcircuit', x, 'gain') for x in fr], [mean('full_brain', x, 'gain') for x in fr]
        o.put('fullbrain.freqs', fr, 'phase1_fullbrain_check.json:meta.freqs')
        o.put('fullbrain.n_seeds', len(fb['meta']['seeds']), 'derived: len(phase1_fullbrain_check.json:meta.seeds)')
        o.put('fullbrain.sub_gain', sg, 'derived: seed mean of rows[model=subcircuit].gain')
        o.put('fullbrain.full_gain', fg, 'derived: seed mean of rows[model=full_brain].gain')
        o.put('fullbrain.gain_shortfall_pct', [100 * (1 - s / g) for s, g in zip(sg, fg)], 'derived: 100 (1 - sub/full)')
        o.put('fullbrain.phase_gap_deg', [abs(mean('subcircuit', x, 'phase_deg') - mean('full_brain', x, 'phase_deg')) for x in fr],
              'derived: |seed-mean phase sub - full|')

    # ---------------- closed loop (null until the files exist)
    for pl, fn, k2, k2b in (('lin2', 'phase2a_metrics.json', 'H2', 'H2b'), ('jsbsim', 'phase2b_metrics.json', 'H3_H2', 'H3_H2b')):
        m = R(fn)
        if not m or k2 not in m:
            o.put(f'closed_loop.{pl}', None, f'{fn} missing')
            continue
        C = m['controllers']
        for c in ('bare', 'yaw_damper', 'fly_real'):
            if c in C:
                o.put(f'closed_loop.{pl}.controllers.{c}.rms_r_deg_s', C[c]['mean_rms_r'] * DEG, f'derived: {fn}:controllers.{c}.mean_rms_r in deg/s')
                o.put(f'closed_loop.{pl}.controllers.{c}.rms_beta_deg', C[c]['mean_rms_beta'] * DEG, f'derived: {fn}:controllers.{c}.mean_rms_beta in deg')
                o.put(f'closed_loop.{pl}.controllers.{c}.n_departed', C[c]['n_departed'], f'{fn}:controllers.{c}.n_departed')
                o.put(f'closed_loop.{pl}.controllers.{c}.K', C[c]['K'], f'{fn}:controllers.{c}.K')
        if 'bare' in C:
            for c in ('yaw_damper', 'fly_real'):
                if c in C:
                    o.put(f'closed_loop.{pl}.pct_less_swing.{c}', 100 * (1 - C[c]['mean_rms_r'] / C['bare']['mean_rms_r']),
                          f'derived: 100 (1 - {fn}:controllers.{c}.mean_rms_r / controllers.bare.mean_rms_r)')
        h, hb = m[k2], m.get(k2b)
        o.put(f'closed_loop.{pl}.fly_minus_bare', {k: v * DEG if k != 'n' else v for k, v in ci(h).items()},
              f'derived: {fn}:{k2} mean and percentile CI, in deg/s')
        o.put(f'closed_loop.{pl}.fly_beats_bare', h['supported'], f'{fn}:{k2}.supported')
        if hb:
            o.put(f'closed_loop.{pl}.real_minus_median_shuffle', {k: v * DEG if k != 'n' else v for k, v in ci(hb).items()},
                  f'derived: {fn}:{k2b} mean and percentile CI, in deg/s')
            o.put(f'closed_loop.{pl}.real_beats_shuffles', hb['supported'], f'{fn}:{k2b}.supported')
            o.put(f'closed_loop.{pl}.rank_of_real', hb['rank_of_real'], f'{fn}:{k2b}.rank_of_real')
            o.put(f'closed_loop.{pl}.n_wirings', hb['n_null'] + 1, f'derived: {fn}:{k2b}.n_null + 1')
            o.put(f'closed_loop.{pl}.p', hb['p'], f'{fn}:{k2b}.p')
            o.put(f'closed_loop.{pl}.p_is_min', abs(hb['p'] - 1 / (hb['n_null'] + 1)) < 1e-12,
                  f'derived: {fn}:{k2b}.p == 1 / (n_null + 1), the smallest p the scrambles allow')
            o.put(f'closed_loop.{pl}.n_shuffles_zero_gain', sum(C[w]['K'] == 0 for w in hb['shuffles']),
                  f'derived: count of {fn}:controllers.<{k2b}.shuffles>.K == 0 (tuning kept the rudder still)')
            o.put(f'closed_loop.{pl}.shuffle_rms_r_deg_s', {w: v * DEG for w, v in hb['mean_rms_by_wiring'].items() if w != 'fly_real'},
                  f'derived: {fn}:{k2b}.mean_rms_by_wiring in deg/s')
        if 'yaw_damper_minus_fly_real' in m:
            o.put(f'closed_loop.{pl}.damper_minus_fly', {k: v * DEG if k != 'n' else v for k, v in ci(m['yaw_damper_minus_fly_real']).items()},
                  f'derived: {fn}:yaw_damper_minus_fly_real mean and percentile CI, in deg/s')

        closed_loop_extra(o, results, pl, fn, m)
    replays(o)

    # ---------------- lesions
    les = R('phase3_lesions.json')
    if not les or not les.get('gain_sets'):
        o.put('lesions', None, 'phase3_lesions.json missing')
    else:
        G = les['gain_sets'][0]
        fn = 'phase3_lesions.json:gain_sets[0]'
        h = G['H4']
        ws = list(h['auc'])
        o.put('lesions.exploratory', G['exploratory'], f'{fn}.exploratory')
        o.put('lesions.n_seeds', G['n_seeds'], f'{fn}.n_seeds')
        o.put('lesions.fractions', h['fractions'], f'{fn}.H4.fractions')
        o.put('lesions.wirings', ws, f'keys of {fn}.H4.auc')
        o.put('lesions.K', G['K'], f'{fn}.K')
        o.put('lesions.auc', h['auc'], f'{fn}.H4.auc')
        o.put('lesions.auc_diff', h['diff'], f'{fn}.H4.diff')
        o.put('lesions.auc_ci95', h['ci95'], f'{fn}.H4.ci95')
        o.put('lesions.any_departure', h['any_departure'], f'{fn}.H4.any_departure')
        o.put('lesions.verdict', h4_code(h['verdict']), f'derived: category of {fn}.H4.verdict')
        conds = [c for c in G['cells'] if c.startswith(ws[0] + '/random')]
        conds = sorted({c.split('/')[1] for c in conds}, key=lambda c: int(c[6:]))
        P = {w: [1 - G['cells'][f'{w}/{c}']['p_departure'] for c in conds] for w in ws}
        o.put('lesions.p_no_departure', P, f'derived: 1 - {fn}.cells.<w>/random*.p_departure')
        o.put('lesions.p_no_departure_ci', {w: [[1 - G['cells'][f'{w}/{c}']['p_departure_wilson95'][1],
                                                 1 - G['cells'][f'{w}/{c}']['p_departure_wilson95'][0]] for c in conds] for w in ws},
              f'derived: 1 - {fn}.cells.<w>/random*.p_departure_wilson95')
        o.put('lesions.rms_r_deg_s', {w: [G['cells'][f'{w}/{c}']['mean_rms_r'] * DEG for c in conds] for w in ws},
              f'derived: {fn}.cells.<w>/random*.mean_rms_r in deg/s')
        o.put('lesions.rms_r_ci_deg_s', {w: [[x * DEG for x in G['cells'][f'{w}/{c}']['mean_rms_r_ci95']] for c in conds] for w in ws},
              f'derived: {fn}.cells.<w>/random*.mean_rms_r_ci95 in deg/s')
        ref = les.get('reference_rms_r_phase2b', {})
        o.put('lesions.reference_deg_s', {k: v * DEG for k, v in ref.items()},
              'derived: phase3_lesions.json:reference_rms_r_phase2b in deg/s')
        tconds = list(G['targeted'])
        o.put('lesions.targeted_rms_r_deg_s', {c: {w: G['cells'][f'{w}/{c}']['mean_rms_r'] * DEG for w in ws} for c in tconds},
              f'derived: {fn}.cells.<w>/<targeted>.mean_rms_r in deg/s')
        o.put('lesions.targeted_rms_beta_deg', {c: {w: G['cells'][f'{w}/{c}']['mean_rms_beta'] * DEG for w in ws} for c in tconds},
              f'derived: {fn}.cells.<w>/<targeted>.mean_rms_beta in deg')
        if 'bare' in ref:
            o.put('lesions.targeted_equals_bare', {c: {w: abs(G['cells'][f'{w}/{c}']['mean_rms_r'] / ref['bare'] - 1) < 1e-9 for w in ws}
                                                   for c in tconds},
                  f'derived: {fn}.cells.<w>/<targeted>.mean_rms_r equal to reference_rms_r_phase2b.bare (rel. 1e-9)')
        o.put('lesions.n_flights', sum(c['n'] for c in G['cells'].values()), f'derived: sum of {fn}.cells.*.n')
        o.put('lesions.n_flights_lesioned', sum(c['n'] for c in G['cells'].values() if c['n_lesioned'] > 0),
              f'derived: sum of {fn}.cells.*.n where n_lesioned > 0')
        lesion_exceed(o, results, ref)
        o.put('lesions.n_departed_total', sum(c['n_departed'] for c in G['cells'].values()), f'derived: sum of {fn}.cells.*.n_departed')
        o.put('lesions.wilson_upper_max', max(c['p_departure_wilson95'][1] for c in G['cells'].values()),
              f'derived: max of {fn}.cells.*.p_departure_wilson95[1]')
        if 'bare' in ref:
            allc = {w: [G['cells'][k]['mean_rms_r'] for k in G['cells'] if k.startswith(w + '/')] for w in ws}
            o.put('lesions.max_rms_r_deg_s', {w: max(v) * DEG for w, v in allc.items()},
                  f'derived: max over all lesion conditions of {fn}.cells.<w>/*.mean_rms_r in deg/s')
            o.put('lesions.ever_worse_than_bare', {w: bool(max(v) > ref['bare'] * (1 + 1e-9)) for w, v in allc.items()},
                  f'derived: any {fn}.cells.<w>/*.mean_rms_r > reference_rms_r_phase2b.bare')
        sc = R('subcircuit_stats.json')
        if sc and 'hub_lesion_sets_real' in sc:
            o.put('lesions.hub_sets_real', sc['hub_lesion_sets_real'], 'subcircuit_stats.json:hub_lesion_sets_real')
        sr = G.get('secondary_rms_r')
        if sr:
            o.put('lesions.diff_per_fraction_deg_s', [{k: x[k] * DEG for k in ('mean', 'lo', 'hi')} for x in sr['diff_per_fraction']],
                  f'derived: {fn}.secondary_rms_r.diff_per_fraction (real minus shuffle mean) in deg/s')
            o.put('lesions.rms_increase_0_to_80_deg_s', {w: {k: v[k] * DEG for k in ('mean', 'lo', 'hi')}
                                                         for w, v in sr['increase_0_to_80'].items()},
                  f'derived: {fn}.secondary_rms_r.increase_0_to_80 in deg/s')
            o.put('lesions.rms_area_diff_deg_s', {k: sr['diff_area'][k] * DEG for k in ('mean', 'lo', 'hi')},
                  f'derived: {fn}.secondary_rms_r.diff_area (real minus shuffle mean) in deg/s')
        o.put('lesions.targeted', {c: dict(n_departed=v['n_departed'],
                                           real_minus_shuffles_deg_s={k: x * DEG for k, x in v['diff_rms_r_real_minus_shuffle_mean'].items()
                                                                       if k in ('mean', 'lo', 'hi')})
                                   for c, v in G['targeted'].items()}, f'derived: {fn}.targeted (rms in deg/s)')
    return o


SHUF_NAMES = [f'fly_shuffle_{k:02d}' for k in range(10)]
RELAY_SAT = 0.9   # a controller whose rudder is saturated in > 90 % of steps acts as a relay (bang-bang)


def closed_loop_extra(o, results, pl, fn, m):
    """Sideslip ranking, per-wiring table, per-seed paired differences and per-controller CIs for the paper."""
    C, p = m['controllers'], f'closed_loop.{pl}'
    ws = ['fly_real'] + SHUF_NAMES
    rb = {w: C[w]['mean_rms_beta'] * DEG for w in ws}
    o.put(f'{p}.rms_beta_deg_by_wiring', rb, f'derived: {fn}:controllers.<wiring>.mean_rms_beta in deg')
    o.put(f'{p}.rms_beta_rank_of_real', 1 + sum(rb[w] < rb['fly_real'] for w in SHUF_NAMES),
          f'derived: rank (1 = lowest) of fly_real among the 11 wirings by {fn}:controllers.*.mean_rms_beta')
    o.put(f'{p}.shuffle_rms_beta_range_deg', [min(rb[w] for w in SHUF_NAMES), max(rb[w] for w in SHUF_NAMES)],
          f'derived: min/max of {fn}:controllers.fly_shuffle_*.mean_rms_beta in deg')
    o.put(f'{p}.n_departed_all', sum(v['n_departed'] for v in C.values()), f'derived: sum of {fn}:controllers.*.n_departed')
    sat = {w: m['rudder_activity'][w]['mean_sat_frac'] for w in ['bare', 'yaw_damper'] + ws}
    o.put(f'{p}.sat_frac', sat, f'{fn}:rudder_activity.<controller>.mean_sat_frac')
    o.put(f'{p}.K_by_wiring', {w: C[w]['K'] for w in ws}, f'{fn}:controllers.<wiring>.K')
    o.put(f'{p}.relay_shuffles', [w for w in SHUF_NAMES if sat[w] > RELAY_SAT],
          f'derived: shuffles with {fn}:rudder_activity.*.mean_sat_frac > {RELAY_SAT}')
    deg = lambda d: {k: d[k] * DEG for k in ('mean', 'lo', 'hi')}
    for key, src in (('fly_minus_bare', 'H2' if pl == 'lin2' else 'H3_H2'), ('damper_minus_bare', 'yaw_damper_minus_bare'),
                     ('real_minus_median_shuffle', 'H2b' if pl == 'lin2' else 'H3_H2b')):
        d = m[src]
        o.put(f'{p}.per_seed.{key}', np.array(d.get('per_seed', d.get('per_seed_diff_vs_median'))) * DEG,
              f'derived: {fn}:{src}.per_seed(_diff_vs_median) in deg/s')
        o.put(f'{p}.paired.{key}', deg(d['percentile']), f'derived: {fn}:{src}.percentile in deg/s')
    for key, src in (('fly_minus_bare', 'rms_beta_fly_real_minus_bare'), ('damper_minus_bare', 'rms_beta_yaw_damper_minus_bare')):
        o.put(f'{p}.paired_beta.{key}', deg(m[src]['percentile']), f'derived: {fn}:{src}.percentile in deg')
    if 'bare' in C:
        o.put(f'{p}.damper_over_fly_reduction', (C['bare']['mean_rms_r'] - C['yaw_damper']['mean_rms_r'])
              / (C['bare']['mean_rms_r'] - C['fly_real']['mean_rms_r']),
              f'derived: {fn}: (bare - yaw_damper) / (bare - fly_real) of controllers.*.mean_rms_r')
    if 'margins_selected' in m:
        o.put(f'{p}.relay_gm_db', {w: m['margins_selected'][w]['gm_db'] for w in SHUF_NAMES if sat[w] > RELAY_SAT},
              f'{fn}:margins_selected.<relay shuffle>.gm_db (lin2 injection)')
    ss = m.get('secondary_sign', {})
    if ss.get('status') == 'ok':
        h = ss['H2b']
        o.put(f'{p}.signflip_real_minus_median', deg(h['percentile']), f'derived: {fn}:secondary_sign.H2b.percentile in deg/s')
        o.put(f'{p}.signflip_rank_of_real', h['rank_of_real'], f'{fn}:secondary_sign.H2b.rank_of_real')
    ex = m.get('exploratory_g4')
    if ex:
        o.put(f'{p}.g4.fly_minus_bare', deg(ex['fly_real_g4_minus_bare']['percentile']),
              f'derived: {fn}:exploratory_g4.fly_real_g4_minus_bare.percentile in deg/s')
        o.put(f'{p}.g4.rms_r_deg_s', C['fly_real_g4']['mean_rms_r'] * DEG, f'derived: {fn}:controllers.fly_real_g4.mean_rms_r in deg/s')
    # per-controller mean and 95 % percentile bootstrap CI over the 20 test seeds (same call as analyze_phase2's figure)
    for c in ('bare', 'yaw_damper', 'fly_real'):
        sp = Path(results) / 'shards' / f'phase2_test_{pl}_{c}.json'
        if not sp.exists():
            continue
        F = json.loads(sp.read_text())['flights']
        assert [f['seed'] for f in F] == TEST_SEEDS
        for k, unit in (('rms_r', 'rms_r_deg_s'), ('rms_beta', 'rms_beta_deg')):
            b = stats.bootstrap_ci(np.array([f[k] for f in F]) * DEG)
            o.put(f'{p}.ci.{c}.{unit}', dict(mean=b['mean'], lo=b['lo'], hi=b['hi']),
                  f'derived: stats.bootstrap_ci (percentile, seed 0) over shards/phase2_test_{pl}_{c}.json:flights[*].{k}')


def fly_loop(o, results, sel):
    """The selected real-wiring loop's measured L: peak |L| and, at the gain-margin phase crossing, |L| against the
    lock-in noise floor (run_phase2.measure_margins L_noise) at the two bracketing grid frequencies."""
    fn = f'shards/phase2_tune_lin2_fly_real_K{sel["K_index_selected"]}.json'
    sp = Path(results) / fn
    if not sp.exists():
        return
    row = json.loads(sp.read_text())['rows'][0]
    mg = row['margins']
    assert row['K'] == sel['selected_K'] and mg['gm_db'] == sel['selected_gm_db']
    f, La, nz = np.array(mg['freqs']), np.array(mg['L_abs']), np.array(mg['L_noise'])
    i = int(np.argmax(La))
    j = int(np.searchsorted(f, mg['f_gm_hz'])) - 1          # f[j] <= f_gm < f[j + 1]
    p = 'tuning.fly_loop'
    o.put(f'{p}.max_abs_L', La[i], f'{fn}:rows[0].margins.L_abs (max)')
    o.put(f'{p}.max_abs_L_f_hz', f[i], f'{fn}:rows[0].margins.freqs at max L_abs')
    o.put(f'{p}.max_abs_L_phase_deg', mg['L_phase_deg'][i], f'{fn}:rows[0].margins.L_phase_deg at max L_abs')
    o.put(f'{p}.gm_db', mg['gm_db'], f'{fn}:rows[0].margins.gm_db')
    o.put(f'{p}.f_gm_hz', mg['f_gm_hz'], f'{fn}:rows[0].margins.f_gm_hz')
    o.put(f'{p}.abs_L_at_gm', 10 ** (-mg['gm_db'] / 20), f'derived: 10^(-gm_db/20) from {fn}:rows[0].margins.gm_db')
    o.put(f'{p}.noise_at_gm', max(nz[j], nz[j + 1]), f'derived: max {fn}:rows[0].margins.L_noise at the grid points bracketing f_gm_hz')
    o.put(f'{p}.gm_bound_db', -20 * np.log10(10 ** (-mg['gm_db'] / 20) + max(nz[j], nz[j + 1])),
          'derived: -20 log10(abs_L_at_gm + noise_at_gm), the margin if the noise added in phase')
    o.put(f'{p}.noise_range', [nz.min(), nz.max()], f'derived: min/max {fn}:rows[0].margins.L_noise')


def lesion_exceed(o, results, ref):
    """Per-seed flights of the real wiring under random lesions that flew worse than the bare airframe on the same seed."""
    sh = Path(results) / 'shards'
    bp = sh / 'phase2_test_jsbsim_bare.json'
    if not bp.exists():
        return
    bare = {f['seed']: f['rms_r'] for f in json.loads(bp.read_text())['flights']}
    assert abs(np.mean(list(bare.values())) / ref['bare'] - 1) < 1e-12
    out = []
    for fr in nulls.FRACTIONS:
        cond = f'random{int(round(100 * fr)):02d}'
        for sp in sorted(sh.glob(f'phase3_selected_real_{cond}_c*.json')):
            for f in json.loads(sp.read_text())['flights']:
                if f['rms_r'] > bare[f['seed']] * (1 + 1e-9):
                    out.append(dict(fraction=fr, seed=f['seed'], rms_r_deg_s=f['rms_r'] * DEG, bare_deg_s=bare[f['seed']] * DEG))
    o.put('lesions.real_seeds_worse_than_bare', out,
          'derived: shards/phase3_selected_real_random*_c*.json:flights[*].rms_r > shards/phase2_test_jsbsim_bare.json same seed')


def replays(o, runs=ROOT / 'app' / 'runs', step=50):
    """Seed-100 replays in app/runs/manifest.json: rudder use, heading drift and offset from the bare airframe's path."""
    mf = runs / 'manifest.json'
    if not mf.exists():
        return
    for sc in json.loads(mf.read_text())['scenarios']:
        recs = {}
        for r in sc['runs']:
            if (runs / r['file']).exists():
                recs[r['file']] = json.loads((runs / r['file']).read_text())
        bare = next((f for f, d in recs.items() if d['meta']['controller'] == 'bare'), None)
        if not bare:
            continue
        P0 = np.array(recs[bare]['pos_ned_m'])
        u = P0[-1, :2] - P0[0, :2]; u /= np.linalg.norm(u)
        plant_name = recs[bare]['meta']['plant']
        for f, d in recs.items():
            P, src = np.array(d['pos_ned_m']), f'derived: app/runs/{f}'
            rel = P[:, :2] - P[0, :2]
            psi = np.unwrap(np.array(d['euler_rad'])[:, 2])
            rud = np.array(d['rudder'])
            k = f'replay.{plant_name}.{d["meta"]["controller"]}'
            o.put(f'{k}.file', f, 'app/runs/manifest.json')
            o.put(f'{k}.seed', d['meta']['seed'], f'{src}:meta.seed')
            o.put(f'{k}.mean_rudder_deg', rud.mean() * 16.0, f'{src}: mean(rudder) x 16 deg (plant.py rudder mapping)')
            o.put(f'{k}.sat_frac', np.mean(np.abs(rud) >= 1.0), f'{src}: fraction of steps with |rudder| >= 1')
            o.put(f'{k}.heading_change_deg', np.degrees(psi[-1] - psi[0]), f'{src}: unwrapped psi(end) - psi(start)')
            o.put(f'{k}.cross_track_m', u[0] * rel[-1, 1] - u[1] * rel[-1, 0],
                  f'{src}: final offset right (+) of the bare airframe\'s start-to-end line')
            o.put(f'{k}.mean_beta_deg', np.degrees(np.mean(d['beta'])), f'{src}: mean(beta)')
            o.put(f'{k}.rms_r_deg_s', np.degrees(np.sqrt(np.mean(np.square(d['r'])))), f'{src}: rms(r)')
            # ground track every `step` samples plus the final one, rotated so the bare path runs along +x
            # (along-track, cross-track right +)
            idx = list(range(0, len(rel), step))
            if idx[-1] != len(rel) - 1:
                idx.append(len(rel) - 1)
            tr = np.stack([rel[:, 0] * u[0] + rel[:, 1] * u[1], u[0] * rel[:, 1] - u[1] * rel[:, 0]], 1)[idx]
            o.put(f'{k}.track_m', np.round(tr, 1), f'{src}: pos_ned_m every {step} samples and the last, in the bare-path frame')


def selfcheck(o, results):
    d, s = o.d, o.src
    b = load(results, 'phase1_bode.json')
    assert d['h1']['degree']['coherence']['real'] == b['H1']['c']['coherence']['real']
    assert d['h1']['degree']['gain']['real'] == b['H1']['c']['gain']['real']
    assert d['h1']['type']['coherence']['median'] == float(np.median(list(b['H1_secondary_type_null']['c']['coherence']['null'].values())))
    assert d['bode']['real']['gain'] == b['wirings']['real']['gain'] and d['bode']['freqs'] == b['freqs']
    assert abs(d['h1']['degree']['coherence']['median'] - 0.309) < 0.001 and abs(d['h1']['degree']['gain']['median'] - 0.0751) < 1e-4
    assert d['h1']['degree']['coherence']['rank'] == 1 and d['h1']['degree']['coherence']['p'] == 0.05
    assert abs(d['bode']['delay_fit']['tau_ms'] - 21.5758) < 1e-3
    assert d['signs'] == dict(degree_neg=12, degree_n=19, degree_coherent=7, degree_coherent_neg=5, type_stabilizing=19)
    sc = load(results, 'subcircuit_stats.json')
    assert d['wiring']['n_neurons'] == sc['n_neurons'] == 20556 and d['wiring']['n_sim_edges'] == sc['n_sim_edges']
    pd = load(results, 'plant_c172x_derivs.json')
    assert d['plant']['Nb'] == pd['derivs']['Nb'] and d['plant']['N_cmd'] == pd['lin2']['N_cmd']
    assert abs(d['plant']['dryden']['sigma_mps'] - 3.22) < 0.01 and abs(d['plant']['dryden']['L_m'] - 533.4) < 0.1
    assert abs(d['plant']['rudder_full_deg'] - 16) < 1e-9
    # constants match the brief (and therefore the code they were read from)
    m = d['model']
    assert (m['v_th_mV'], m['v_reset_mV'], m['tau_m_ms'], m['tau_s_ms'], m['t_ref_ms'], m['t_delay_ms'], m['w_syn_mV']) == \
        (-45, -52, 20, 5, 2.2, 1.8, 0.275), m
    assert m['washout_tau_s'] == m['damper_tau_s'] == 1.0 and m['r_base_hz'] == 10 and m['g_hz_per_deg_s'] == 1
    assert d['tuning']['margin_req'] == dict(gm_db=6.0, pm_deg=45.0)
    assert d['plant']['departure'] == dict(phi_deg=60.0, beta_deg=20.0, r_deg_s=60.0, hold_s=1.0) or \
        all(abs(d['plant']['departure'][k] - v) < 1e-9 for k, v in dict(phi_deg=60, beta_deg=20, r_deg_s=60, hold_s=1).items())
    fb = d['fullbrain']['gain_shortfall_pct']
    assert 15 < fb[0] < 17 and 15 < fb[1] < 17 and 35 < fb[2] < 37 and max(d['fullbrain']['phase_gap_deg']) < 4
    sel = load(results, 'phase2_selection.json')
    if sel:
        assert d['tuning']['selected']['lin2']['fly_real']['K'] == sel['lin2']['fly_real']['selected_K']
        assert d['tuning']['K_fly'] == sel['meta']['K_fly']
    for pl, fn, k in (('lin2', 'phase2a_metrics.json', 'H2'), ('jsbsim', 'phase2b_metrics.json', 'H3_H2')):
        m2 = load(results, fn)
        if m2 and k in m2:
            assert abs(d['closed_loop'][pl]['fly_minus_bare']['mean'] - m2[k]['mean'] * DEG) < 1e-12
            cl = d['closed_loop'][pl]
            assert abs(cl['pct_less_swing']['fly_real'] - 100 * (1 - cl['controllers']['fly_real']['rms_r_deg_s']
                                                                   / cl['controllers']['bare']['rms_r_deg_s'])) < 1e-9
            if 'n_shuffles_zero_gain' in cl:   # a zero-gain scramble flies exactly like the bare airframe
                assert cl['n_shuffles_zero_gain'] <= sum(abs(v - cl['controllers']['bare']['rms_r_deg_s']) < 1e-12
                                                         for v in cl['shuffle_rms_r_deg_s'].values())
        else:
            assert d['closed_loop'][pl] is None
    les = load(results, 'phase3_lesions.json')
    if les:
        assert d['lesions']['auc_diff'] == les['gain_sets'][0]['H4']['diff']
        L = d['lesions']
        assert L['rms_r_deg_s']['real'][0] == les['gain_sets'][0]['cells']['real/random00']['mean_rms_r'] * DEG
        assert L['n_flights'] == len(L['wirings']) * L['n_seeds'] * len(les['gain_sets'][0]['cells']) // len(L['wirings'])
        if 'reference_deg_s' in L and 'bare' in L['reference_deg_s']:
            assert abs(L['reference_deg_s']['bare'] - d['closed_loop']['jsbsim']['controllers']['bare']['rms_r_deg_s']) < 1e-9
        assert L['n_flights_lesioned'] == L['n_flights'] - len(L['wirings']) * L['n_seeds']   # all but the 0 % cells
        ex = L.get('real_seeds_worse_than_bare')
        assert ex is not None and all(e['rms_r_deg_s'] > e['bare_deg_s'] for e in ex) and not L['ever_worse_than_bare']['real']
    else:
        assert d['lesions'] is None
    tn = d['wiring'].get('type_null')
    if tn:
        assert tn == sc['type_null'] and 0.59 < tn['frac_unchanged_type_null_seed0'] < 0.60
        assert tn['n_singleton_lif_classes'] < tn['n_lif_classes'] and tn['frac_unchanged_degree_null_seed0'] < 0.05
    fl = d['tuning'].get('fly_loop')
    if fl:
        assert fl['gm_db'] == d['tuning']['selected']['lin2']['fly_real']['gm_db']
        assert fl['abs_L_at_gm'] < 2 * fl['noise_at_gm'] and fl['max_abs_L'] < 1 and fl['gm_bound_db'] < fl['gm_db']
    assert 0.83 < d['gsens']['min_coh_le_2hz'] < 0.84
    for pl in ('lin2', 'jsbsim'):
        cl = d['closed_loop'].get(pl)
        if cl and 'damper_over_fly_reduction' in cl:
            r = cl['pct_less_swing']['yaw_damper'] / cl['pct_less_swing']['fly_real']
            assert abs(cl['damper_over_fly_reduction'] - r) < 1e-9
    rg = (d['closed_loop'].get('jsbsim') or {}).get('relay_gm_db')
    if rg:
        assert set(rg) == set(d['closed_loop']['jsbsim']['relay_shuffles']) and all(g >= 6 for g in rg.values())
    for p, R in (d.get('replay') or {}).items():
        for k, r in R.items():   # the plotted track ends at the final sample, i.e. at the reported offset
            assert abs(r['track_m'][-1][1] - r['cross_track_m']) < 0.1, (p, k)
    # every leaf has a source, and the file is strict JSON
    def leaves(x, p=''):
        if isinstance(x, dict) and x:
            for k, v in x.items():
                yield from leaves(v, f'{p}.{k}' if p else k)
        else:
            yield p
    for p in leaves(d):
        assert any(p == k or p.startswith(k + '.') for k in s), f'no _source for {p}'
    json.dumps(d, allow_nan=False)
    assert TEST_SEEDS == run_phase3.TEST_SEEDS == list(range(100, 120))
    assert d['wiring'].get('n_t4t5_cells', 12246) == 12246 and d['protocol']['lesion_fractions'][-1] == 0.8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', default=str(ROOT / 'results'))
    ap.add_argument('--out', default=str(ROOT / 'app' / 'data'))
    ap.add_argument('--bundle', action='store_true', help='also write summary.js (window.APP_SUMMARY) for file://')
    a = ap.parse_args()
    o = build(a.results)
    selfcheck(o, a.results)
    doc = dict(o.d, _source=o.src)
    txt = json.dumps(doc, indent=1, allow_nan=False)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(txt)
    if a.bundle:
        (out / 'summary.js').write_text('// generated by scripts/export_app_summary.py --bundle; do not edit\n'
                                        f'window.APP_SUMMARY = {txt};\n')
    pending = [k for k in ('closed_loop.lin2', 'closed_loop.jsbsim', 'lesions') if get(o.d, k) is None]
    print(f'wrote {out / "summary.json"}{" + summary.js" if a.bundle else ""}; {len(o.src)} sourced keys; '
          f'pending: {pending or "none"}; self-check OK')


if __name__ == '__main__':
    main()
