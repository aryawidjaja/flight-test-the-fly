"""Phase 1 (H1): open-loop stepped-sine Bode of the FlyWire v783 eye-to-DNa02 subcircuit, real vs 19 shuffles.
Protocol fixed before the main experiments (see README, Protocol and amendments). One job = one (kind, wiring, seed, param):
  bode   wiring in real, shuf00..shuf18; seeds 0-4; g = 1; 13 frequencies (12 log 0.1-20 Hz + 1 Hz), 100 deg/s
  gsens  real; seeds 0-4; g in 0.5, 2, 4 (sensitivity check); same frequencies
  amp    all wirings; seeds 0-4; 1 Hz at 3, 10, 30 deg/s (exploratory; 100 deg/s is in 'bode')
Each frequency is its own run: 1 s at r = 0, then >= 10 whole cycles (and >= 2 s) of r = A sin(2 pi f t).
Output per job: results/shards/phase1_<kind>_<wiring>_s<seed>_<param>.json

  uv run python scripts/run_phase1.py --shard i --n-shards N     # cloud (GitHub Actions, sweep.yml)
  uv run python scripts/run_phase1.py --smoke                     # local self-check, ~30 s
"""
import argparse, json, os, sys, tempfile, time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import brain, nulls, stats  # noqa: E402

FS = 1 / brain.BIN                      # 200 Hz rate bins
WARM = 1.0
AMP = 100.0
N_SHUF, SEEDS = 19, range(5)
WIRINGS = ['real'] + [f'shuf{k:02d}' for k in range(N_SHUF)]
TWIRINGS = [f'tshuf{k:02d}' for k in range(N_SHUF)]   # secondary type-preserving null (swaps within (side, cell type))
FREQS = sorted({FS / round(FS / f) for f in [*np.geomspace(0.1, 20, 12), 1.0]})   # snapped to f = 200/n
GROUPS = ['DNa02_L', 'DNa02_R', 'HS_L', 'HS_R', 'H2_L', 'H2_R', 'DNg02_L', 'DNg02_R']


def jobs():
    J = [('bode', w, s, 1.0) for w in WIRINGS for s in SEEDS]
    J += [('gsens', 'real', s, g) for g in (0.5, 2.0, 4.0) for s in SEEDS]
    J += [('amp', w, s, a) for w in WIRINGS for s in SEEDS for a in (3.0, 10.0, 30.0)]
    J += [('bode', w, s, 1.0) for w in TWIRINGS for s in SEEDS]
    return J


def edges_for(wiring, sub):
    """'real' -> None (the subcircuit), 'shufNN' -> degree/sign-preserving shuffle of the SIMULATED edges,
    'tshufNN' -> the same, restricted to swaps within (side, cell type) of the post neuron (type-preserving null)."""
    if wiring == 'real':
        return None
    k = int(wiring[-2:])
    return nulls.shuffle(brain.sim_edges(sub), k, classes=brain.type_classes(sub) if wiring.startswith('tshuf') else None)


def n_cycles(f):
    return max(10, int(np.ceil(2.0 * f)))


def one_freq(edges, sub, f, A, seed, g, codegen):
    nc = n_cycles(f)
    T = WARM + nc / f
    o = brain.run_open_loop(edges, lambda t: np.where(t >= WARM, A * np.sin(2 * np.pi * f * (t - WARM)), 0.0), T, seed,
                            g=g, sub=sub, codegen=codegen)
    gr, k0 = o['group_rates'], int(round(WARM * FS))
    n = int(round(nc * FS / f))
    tc = (np.arange(n) + 0.5) / FS                          # bin centres, t = 0 at drive onset
    r = A * np.sin(2 * np.pi * f * tc)
    D = (gr['DNa02_R'] - gr['DNa02_L'])[k0:k0 + n]
    gain, ph = stats.transfer(r, D, f, FS, n_cycles=nc)
    coh = stats.coherence_at(r, D, f, FS)
    lk = {}
    for gname in GROUPS:
        if gname in gr:
            a, p = stats.transfer(r, gr[gname][k0:k0 + n], f, FS, n_cycles=nc)
            lk[gname] = dict(gain=a, phase_deg=p, mean_hz=float(gr[gname][k0:k0 + n].mean()),
                             warm_mean_hz=float(gr[gname][:k0].mean()))
    return dict(f=f, A=A, n_cycles=nc, T_s=T, gain=gain, phase_deg=ph, H_re=gain * np.cos(np.radians(ph)),
                H_im=gain * np.sin(np.radians(ph)), coh=coh['coh'], coh_floor_mean=coh['floor_mean'],
                n_segments=coh['n_segments'], groups=lk, T4T5_L_mean_hz=float(gr['T4T5_L'][k0:k0 + n].mean()))


def run_job(job, codegen='cython', freqs=None, out_dir='results/shards'):
    kind, wiring, seed, param = job
    sub = brain.load_subcircuit()
    edges = edges_for(wiring, sub)
    t0 = time.time()
    if kind == 'amp':
        rows = [one_freq(edges, sub, 1.0, param, seed, 1.0, codegen)]
    else:
        g = param
        rows = [one_freq(edges, sub, f, AMP, seed, g, codegen) for f in (freqs or FREQS)]
    out = dict(meta=dict(phase=1, kind=kind, wiring=wiring, seed=seed, param=param, g=param if kind != 'amp' else 1.0,
                         amp_deg_s=param if kind == 'amp' else AMP, shuffle_seed=None if wiring == 'real' else int(wiring[-2:]),
                         null=None if wiring == 'real' else ('type_preserving' if wiring.startswith('tshuf') else 'degree_preserving'),
                         codegen=codegen, warmup_s=WARM, data_version=brain.DATA_VERSION,
                         subcircuit=str(brain.SUB_PATH.name), git_hash=os.environ.get('GITHUB_SHA') or _git(),
                         wall_s=time.time() - t0, created=datetime.now(timezone.utc).isoformat()),
               rows=rows)
    os.makedirs(out_dir, exist_ok=True)
    path = f'{out_dir}/phase1_{kind}_{wiring}_s{seed}_{param:g}.json'
    with open(path, 'w') as fh:
        json.dump(out, fh, indent=1, default=float)
    return path


def _git():
    import subprocess
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True,
                              cwd=os.path.dirname(__file__)).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main(shard, n_shards):
    from joblib import Parallel, delayed
    mine = jobs()[shard::n_shards]
    brain.run_open_loop(None, lambda t: 0 * t, 0.01, 0, codegen='cython')  # warm the cython cache before forking
    paths = Parallel(n_jobs=min(len(mine), os.cpu_count() or 1))(delayed(run_job)(j) for j in mine)
    print('\n'.join(paths))
    return paths


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--shard', type=int)
    ap.add_argument('--n-shards', type=int)
    ap.add_argument('--smoke', action='store_true')
    a = ap.parse_args()
    J = jobs()
    assert len(J) == len(set(J)) == 100 + 15 + 300 + 95
    assert len(FREQS) == 13 and 1.0 in FREQS and all(abs(FS / f - round(FS / f)) < 1e-9 for f in FREQS)
    if a.smoke:  # one real job at 1 and 20 Hz (seed 0), local; checks the file and the 1 Hz sign/coherence
        p = run_job(('bode', 'real', 0, 1.0), freqs=[1.0, 20.0], out_dir=os.path.join(tempfile.gettempdir(), 'fttf_phase1_smoke'))
        d = json.load(open(p))
        r1 = d['rows'][0]
        print(json.dumps({k: r1[k] for k in ('f', 'gain', 'phase_deg', 'coh', 'n_segments')}))
        assert r1['n_segments'] >= 19 and 0 <= r1['coh'] <= 1 and np.isfinite(r1['gain'])
        assert abs(r1['T4T5_L_mean_hz'] - (10 + 100 / np.pi)) < 5   # mean of 10 + max(0, +/-100 sin) over a/b blocks
        print('smoke OK', p)
    else:
        assert 0 <= a.shard < a.n_shards
        main(a.shard, a.n_shards)
