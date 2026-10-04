"""Readout bottleneck evidence, from stored data only (no simulation) -> results/readout_stats.json.

(a) Seed-100 replays in app/runs/ that carry a fly brain: spikes of DNa02_L and DNa02_R over the 60 s flight, read
    from the raster (neurons mapped by type and side), and the number of 10 ms control steps with >= 1 DNa02_L spike.
(b) Phase 3 random lesions on the real wiring (gain set 'selected'): per fraction and test seed, whether the left /
    right DNa02 is in the lesion set (rebuilt exactly as run_phase3.lesion_idx, checked against each flight's stored
    lesion_sha1), and whether the flight is switched off (rms_rudder == 0) and identical to bare (rms_r equal to the
    Phase 2b bare test flight of that seed). Per fraction: counts, the binomial tail P(X >= observed left hits) for
    X ~ Binom(20, m/n) with m = round(f n) of n neurons silenced, and at 5 % the split of the lost benefit
    (benefit = RMS r(bare) - RMS r(fly), lost = intact benefit - lesioned benefit) between switched-off and other seeds.

  uv run python scripts/readout_stats.py
"""
import glob, json, os, sys
from datetime import datetime, timezone

import numpy as np
from scipy.stats import binom

sys.path.insert(0, os.path.dirname(__file__))
import analyze_phase3, run_phase3  # noqa: E402  (no Brian2 run: lesion sets and shard loading only)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARDS = os.path.join(ROOT, 'results', 'shards')
OUT = os.path.join(ROOT, 'results', 'readout_stats.json')
DT = 0.01


def replay_counts(path):
    d = json.load(open(path))
    idx = {(n['type'], n['side']): n['idx'] for n in d['raster']['neurons'] if n['type'] == 'DNa02'}
    assert set(idx) == {('DNa02', 'left'), ('DNa02', 'right')}, (path, idx)
    sp = np.array(d['raster']['spikes'], float).reshape(-1, 2)
    tL = sp[sp[:, 1] == idx[('DNa02', 'left')], 0]
    tR = sp[sp[:, 1] == idx[('DNa02', 'right')], 0]
    T = float(d['meta']['T'])
    winL = len(np.unique(np.floor(tL / DT + 1e-9).astype(int)))
    # cross-check against the stored per-step group rate (Hz over the step): steps with DNa02_L > 0
    stepsL = int(np.sum(np.asarray(d['groups']['DNa02_L']) > 0))
    return dict(controller=d['meta']['controller'], plant=d['meta'].get('plant'), seed=d['meta']['seed'],
                lesion=d['meta'].get('lesion'), K=d['meta'].get('K'), T_s=T,
                DNa02_L_spikes=len(tL), DNa02_R_spikes=len(tR), DNa02_L_mean_hz=len(tL) / T,
                DNa02_L_windows_with_spike=winL, n_windows=int(round(T / DT)),
                DNa02_L_steps_with_rate_gt0_from_groups=stepsL)


def lesion_table(T):
    sub = run_phase3._sub()
    ct, side = np.asarray(sub['cell_type']).astype(str), np.asarray(sub['side']).astype(str)
    n = len(ct)
    dnL, dnR = (np.flatnonzero((ct == 'DNa02') & (side == s)) for s in ('left', 'right'))
    assert len(dnL) == 1 and len(dnR) == 1, (dnL, dnR)
    dnL, dnR = int(dnL[0]), int(dnR[0])
    bare_f = json.load(open(os.path.join(SHARDS, 'phase2_test_jsbsim_bare.json')))['flights']
    bare = {f['seed']: f['rms_r'] for f in bare_f}
    seeds = run_phase3.TEST_SEEDS
    rows, per = {}, {}
    for c in analyze_phase3.RAND:
        f = run_phase3.CONDS[c][1]
        cell = T[('selected', 'real', c)]
        m = int(round(f * n))
        flights = []
        for s in seeds:
            fl = cell['flights'][s]
            les = run_phase3.lesion_idx('real', c, s)
            assert run_phase3._sha(les) == fl['lesion_sha1'] and len(les) == m, (c, s)   # exact reconstruction
            flights.append(dict(seed=s, left_silenced=bool(dnL in les), right_silenced=bool(dnR in les),
                                rms_rudder=fl['rms_rudder'], rudder_off=fl['rms_rudder'] == 0,
                                rms_r=fl['rms_r'], equals_bare=fl['rms_r'] == bare[s]))
        per[c] = flights
        kL = sum(x['left_silenced'] for x in flights)
        rows[c] = dict(fraction=f, n_silenced=m, n_neurons=n, p_hit=m / n, n_seeds=len(seeds),
                       left_silenced=kL, right_silenced=sum(x['right_silenced'] for x in flights),
                       both_silenced=sum(x['left_silenced'] and x['right_silenced'] for x in flights),
                       expected_left_hits=len(seeds) * m / n,
                       p_tail_left_ge_observed=float(binom.sf(kL - 1, len(seeds), m / n)) if kL else 1.0,
                       rudder_off=sum(x['rudder_off'] for x in flights),
                       equals_bare=sum(x['equals_bare'] for x in flights),
                       rudder_off_without_left_silenced=sum(x['rudder_off'] and not x['left_silenced'] for x in flights),
                       left_silenced_but_rudder_on=sum(x['left_silenced'] and not x['rudder_off'] for x in flights),
                       seeds_rudder_off=[x['seed'] for x in flights if x['rudder_off']],
                       seeds_left_silenced=[x['seed'] for x in flights if x['left_silenced']])
    # 5 % benefit decomposition (benefit vs bare, paired by seed)
    b = np.array([bare[s] for s in seeds])
    r0 = np.array([x['rms_r'] for x in per['random00']])
    r5 = np.array([x['rms_r'] for x in per['random05']])
    off = np.array([x['rudder_off'] for x in per['random05']])
    ben0, lost = b - r0, r5 - r0
    dec = dict(note='benefit = RMS r(bare) - RMS r(fly) per seed (rad/s); lost = benefit(0%) - benefit(5%) = '
                    'RMS r(5%) - RMS r(0%); switched off = rms_rudder == 0 at 5%',
               mean_benefit_intact=float(ben0.mean()), mean_benefit_5pct=float((b - r5).mean()),
               fraction_benefit_lost=float(lost.sum() / ben0.sum()),
               n_switched_off=int(off.sum()),
               share_of_loss_from_switched_off=float(lost[off].sum() / lost.sum()),
               fraction_benefit_lost_switched_off_seeds=float(lost[off].sum() / ben0[off].sum()),
               fraction_benefit_lost_other_seeds=float(lost[~off].sum() / ben0[~off].sum()),
               n_other=int((~off).sum()))
    return dict(DNa02_local_idx=dict(left=dnL, right=dnR), by_fraction=rows, decomposition_5pct=dec,
                per_seed=per)


def main(out=OUT):
    reps = []
    for p in sorted(glob.glob(os.path.join(ROOT, 'app', 'runs', '*.json'))):
        d = json.load(open(p)) if not p.endswith(('manifest.json',)) else None
        if d and d.get('meta', {}).get('seed') == 100 and d.get('raster', {}).get('neurons'):
            reps.append(dict(file=os.path.relpath(p, ROOT), **replay_counts(p)))
    T = analyze_phase3.load(SHARDS)
    res = dict(meta=dict(script='scripts/readout_stats.py', data_version='flywire_v783',
                         created=datetime.now(timezone.utc).isoformat(),
                         sources=['app/runs/*_s100.json (raster)', 'results/shards/phase3_selected_real_random*_c*.json',
                                  'results/shards/phase2_test_jsbsim_bare.json', 'data/sub/subcircuit_v783_t5.npz',
                                  'run_phase3.lesion_idx / nulls.lesion_sets (rebuilt, checked against lesion_sha1)']),
               replays_seed100=reps, phase3_random_real=lesion_table(T))
    with open(out, 'w') as fh:
        json.dump(res, fh, indent=1)
    return res


if __name__ == '__main__':
    r = main()
    reps = r['replays_seed100']
    assert reps and all(x['DNa02_L_windows_with_spike'] == x['DNa02_L_steps_with_rate_gt0_from_groups'] for x in reps)
    real = [x for x in reps if x['controller'] == 'fly_real']
    assert real and all(x['DNa02_R_spikes'] == 0 for x in real), [(x['file'], x['DNa02_R_spikes']) for x in real]
    L = r['phase3_random_real']['by_fraction']
    assert L['random00']['left_silenced'] == 0 and L['random00']['equals_bare'] == 0
    assert all(L[c]['equals_bare'] <= L[c]['rudder_off'] for c in L)
    lefts = [L[c]['left_silenced'] for c in L]
    assert lefts == sorted(lefts)            # nested lesion sets: a hit at f stays a hit at every larger f
    for x in reps:
        print(f"{x['file']}: DNa02_L {x['DNa02_L_spikes']} spikes ({x['DNa02_L_windows_with_spike']}/{x['n_windows']} "
              f"windows), DNa02_R {x['DNa02_R_spikes']}")
    for c, v in L.items():
        print(f"{c}: left {v['left_silenced']}, right {v['right_silenced']}, both {v['both_silenced']}, "
              f"rudder off {v['rudder_off']}, = bare {v['equals_bare']}, P(X>=left) {v['p_tail_left_ge_observed']:.2g}")
    print(json.dumps(r['phase3_random_real']['decomposition_5pct'], indent=1))
    print('readout_stats self-check OK ->', OUT)
