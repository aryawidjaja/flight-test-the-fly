"""Phase 3 (H4): lesion to departure. Pre-specified before any lesion run
(see README, Protocol and amendments). Lesions are applied to a FIXED controller: each wiring keeps its own Phase 2b
(jsbsim) K with sign = +1, read from results/phase2_selection.json, and is never retuned after the lesion.

  wirings     real, shuf00, shuf01, shuf02 (nulls.shuffle(sub, k))
  conditions  random00..random80 (nulls.FRACTIONS; nested; draw seed = the flight's test seed; the same neuron
              indices for every wiring, because all wirings share the neuron set), all_HS, T4_only, T5_only,
              hubs05/hubs10/hubs20 (betweenness on THAT wiring, computed once per wiring in the shard's parent)
  flights     test seeds 100-119, T = 60 s, plant jsbsim, Dryden moderate. Departure is plant.simulate's
              pre-specified rule (|phi| > 60 deg, |beta| > 20 deg or |r| > 60 deg/s held > 1 s).
  gain sets   'selected' = each wiring's Phase 2b K. Pre-specified fallback: if real's selected K is 0, an extra 'explore' set
              at each wiring's smallest nonzero margin-admissible K is run and labelled exploratory.
              A wiring whose K is 0 flies the bare airframe (no brain is simulated); its flights say so.
One job = (gain set, wiring, condition, chunk of 10 seeds) -> results/shards/phase3_<set>_<wiring>_<cond>_c<i>.json
App recordings (real, seed 100; random00/40/80 and all_HS; 'explore' set when real's selected K is 0):
  results/shards/rec_lesion_<cond>_s100.json   (recording format, meta.lesion = {kind, fraction})

  uv run python scripts/run_phase3.py --plan                     # job count, CPU estimate, suggested n_shards
  uv run python scripts/run_phase3.py --shard i --n-shards N     # cloud (GitHub Actions, sweep.yml)
  uv run python scripts/run_phase3.py --smoke                    # local, < 2 min: 3 s flights, 40% random + all_HS
"""
import argparse, functools, hashlib, json, os, re, sys, tempfile, time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import baselines, brain, nulls, plant, run_phase1  # noqa: E402

SEL_PATH = os.path.join(os.path.dirname(__file__), '..', 'results', 'phase2_selection.json')
WIRINGS = ['real', 'shuf00', 'shuf01', 'shuf02']
TEST_SEEDS = list(range(100, 120))
CHUNK = 10
T_FLIGHT = 60.0
PLANT, TURB, SIGN, CODEGEN = 'jsbsim', 'moderate', +1, 'cython'
CONDS = ({f'random{round(100 * f):02d}': ('random', f) for f in nulls.FRACTIONS}
         | {k: (k, 0.0) for k in ('all_HS', 'T4_only', 'T5_only')}
         | {f'hubs{round(100 * f):02d}': ('hubs_topk', f) for f in nulls.HUB_FRACTIONS})
REC_CONDS = ('random00', 'random40', 'random80', 'all_HS')
REC_SEED = 100
WALL_PER_SIM_S = 0.9   # cython FlyController on the M4 (results/brain_timing.json: 0.887); lesions only make it faster


# ---------------------------------------------------------------- gains from Phase 2b
def _pick(d, *keys):
    for k in keys:
        if k in d:
            return d[k]
    raise KeyError(f'none of {keys} in {sorted(d)}')


def gains(path=None):
    """{'selected': {wiring: K}, 'explore': {wiring: K} or None}. Tolerant reader; see the note posted to the
    run_phase2 author: section jsbsim|2b|phase2b, entry real|fly_real / shuf00|fly_shuffle_00, K|best_K|...,
    admissible_K|K_admissible|admissible."""
    d = json.load(open(path or SEL_PATH))
    sec = next((d[k] for k in ('jsbsim', '2b', 'phase2b') if isinstance(d.get(k), dict)), d)
    sel, adm = {}, {}
    for w in WIRINGS:
        e = _pick(sec, w, 'fly_real' if w == 'real' else f'fly_shuffle_{w[4:]}')
        if e.get('sign', SIGN) != SIGN:
            raise ValueError(f'{w}: Phase 3 uses the sign = +1 tuning, selection file has sign {e["sign"]}')
        sel[w] = float(_pick(e, 'K', 'best_K', 'selected_K', 'K_selected'))
        adm[w] = sorted(float(k) for k in _pick(e, 'admissible_K', 'K_admissible', 'admissible') if k > 0)
    explore = None
    if sel['real'] == 0:  # pre-specified fallback: explore at the smallest nonzero admissible K
        missing = [w for w in WIRINGS if not adm[w]]
        if missing:
            raise ValueError(f'the exploratory fallback needs a nonzero margin-admissible K for {missing}')
        explore = {w: adm[w][0] for w in WIRINGS}
    return dict(selected=sel, explore=explore)


def jobs(G):
    J = []
    for gs in ('selected', 'explore'):
        if G.get(gs) is None:
            continue
        for w in WIRINGS:
            for c in CONDS:
                for i in range(len(TEST_SEEDS) // CHUNK):
                    J.append((gs, w, c, i))
    return J


def rec_gain_set(G):
    return 'explore' if G['explore'] is not None else 'selected'


# ---------------------------------------------------------------- lesions
@functools.lru_cache(maxsize=None)
def _sub():
    return brain.load_subcircuit()


@functools.lru_cache(maxsize=None)
def edges_for(wiring):
    s = _sub()  # one null definition for every phase (run_phase1.edges_for); hubs are ranked on simulated edges
    return brain.sim_edges(s) if wiring == 'real' else run_phase1.edges_for(wiring, s)


def hub_sets(wiring):
    """{cond: sorted indices} for the hubs conditions of one wiring (betweenness ~65 s, computed once)."""
    n = len(_sub()['cell_type'])
    return {c: nulls.lesion_sets(n, 'hubs_topk', f, meta=_sub(), edges=edges_for(wiring))
            for c, (k, f) in CONDS.items() if k == 'hubs_topk'}


def lesion_idx(wiring, cond, seed, hubs=None):
    kind, f = CONDS[cond]
    if kind == 'hubs_topk':
        return (hubs or hub_sets(wiring))[cond]
    # random: depends on seed only (paired across wirings); type kinds: on cell types only
    return nulls.lesion_sets(len(_sub()['cell_type']), kind, f, seed=seed, meta=_sub())


def n_edges_expected(lesion):
    """brain.Brain's edge mask, recomputed with numpy (smoke check that the lesion reaches the network)."""
    s = _sub()
    src = np.array([re.fullmatch(brain.SRC_RE, str(c)) is not None for c in s['cell_type']])
    m = ~src[s['post']] & (s['w'] != 0) & ~np.isin(s['pre'], lesion)
    return int(m.sum())


def _sha(idx):
    return hashlib.sha1(np.asarray(idx, np.int64).tobytes()).hexdigest()[:16]


# ---------------------------------------------------------------- flights
def to_recording(R, meta):
    """run_phase2.to_recording if it exists, else a minimal equivalent in the same recording format."""
    try:
        from run_phase2 import to_recording as tr
        rec = tr(R, meta)
    except ImportError:
        s4 = lambda a: [float(f'{x:.4g}') for x in np.asarray(a, float).ravel()]
        rows = lambda a: [s4(r) for r in np.asarray(a, float)]
        groups = ['T4T5_L', 'T4T5_R', 'HS_L', 'HS_R', 'VS_L', 'VS_R', 'DNa02_L', 'DNa02_R']
        rec = dict(meta=meta, t=[round(float(x), 3) for x in R['t']], pos_ned_m=np.round(R['pos_ned_m'], 2).tolist(),
                   euler_rad=rows(R['euler_rad']), r=s4(R['r']), beta=s4(R['beta']), rudder=s4(R['rudder']),
                   gust=rows(R['gust']), groups={g: s4(R['groups'][g]) for g in groups if g in R['groups']},
                   raster=dict(neurons=[dict(idx=n['idx'], type=n['type'], side=n['side']) for n in R['raster']['neurons']],
                               spikes=[[round(t, 4), int(i)] for t, i in R['raster']['spikes']]),
                   metrics={k: (bool(v) if k == 'departed' else float(f'{v:.4g}')) for k, v in R['metrics'].items()})
    rec['meta'] = {**rec['meta'], 'lesion': meta['lesion']}
    return rec


def fly(gs, wiring, K, cond, seed, lesion, T=T_FLIGHT, rec_path=None, exploratory=False):
    kind, f = CONDS[cond]
    name = 'fly_real' if wiring == 'real' else f'fly_shuffle_{wiring[4:]}'
    is_bare = K == 0
    c = baselines.Bare() if is_bare else brain.FlyController(
        K, SIGN, edges=None if wiring == 'real' else edges_for(wiring), lesion=lesion, sub=_sub(), codegen=CODEGEN,
        name=name, record_raster=rec_path is not None)
    t0 = time.time()
    try:
        R = plant.simulate(c, seed, T=T, plant=PLANT, turbulence=TURB)
    finally:
        if not is_bare:
            c.close()
    out = dict(seed=seed, **R['metrics'], max_abs_r=float(np.abs(R['r']).max()),
               max_abs_beta=float(np.abs(R['beta']).max()), input_saturated=R['meta']['input_saturated'],
               controller_is_bare=is_bare, n_lesioned=None if is_bare else len(lesion),
               lesion_sha1=None if is_bare else _sha(lesion),
               n_edges_used=None if is_bare else c.brain.n_edges_used, wall_s=round(time.time() - t0, 2))
    if rec_path:
        meta = dict(controller=name, aircraft='c172x', seed=seed, turbulence='dryden_' + TURB,
                    lesion=dict(kind='none' if cond == 'random00' else kind, fraction=f), dt=plant.DT,
                    data_version=brain.DATA_VERSION, created=datetime.now(timezone.utc).isoformat(), plant=PLANT,
                    K=K, sign=SIGN, gain_set=gs, exploratory=exploratory, n_lesioned=len(lesion), git_hash=_git(),
                    source='scripts/run_phase3.py')
        rec = to_recording(R, meta)
        os.makedirs(os.path.dirname(rec_path), exist_ok=True)
        with open(rec_path, 'w') as fh:
            json.dump(rec, fh, separators=(',', ':'))
        out['recording'] = os.path.basename(rec_path)
    return out


def _git():
    import subprocess
    try:
        return os.environ.get('GITHUB_SHA') or subprocess.run(
            ['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True, cwd=os.path.dirname(__file__)).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def job_path(job, out_dir):
    gs, w, c, i = job
    return f'{out_dir}/phase3_{gs}_{w}_{c}_c{i}.json'


def flight_specs(job, G, hubs, T, out_dir, seeds=None):
    gs, w, cond, i = job
    K = G[gs][w]
    seeds = seeds or TEST_SEEDS[i * CHUNK:(i + 1) * CHUNK]
    for s in seeds:
        rec = (w == 'real' and s == REC_SEED and cond in REC_CONDS and gs == rec_gain_set(G) and K > 0)
        les = lesion_idx(w, cond, s, hubs.get(w)) if K > 0 else np.array([], np.int64)  # K = 0: bare, no brain
        yield dict(gs=gs, wiring=w, K=K, cond=cond, seed=s, lesion=les, T=T,
                   rec_path=f'{out_dir}/rec_lesion_{cond}_s{s}.json' if rec else None, exploratory=gs == 'explore')


def write_job(job, G, flights, T, out_dir, sel_sha):
    gs, w, cond, i = job
    kind, f = CONDS[cond]
    out = dict(meta=dict(phase=3, gain_set=gs, exploratory=gs == 'explore', wiring=w, condition=cond, lesion_kind=kind,
                         fraction=f, K=G[gs][w], sign=SIGN, k_zero_equals_bare=G[gs][w] == 0,
                         shuffle_seed=None if w == 'real' else int(w[4:]), plant=PLANT, turbulence='dryden_' + TURB,
                         T_s=T, seeds=[fl['seed'] for fl in flights], codegen=CODEGEN, data_version=brain.DATA_VERSION,
                         subcircuit=brain.SUB_PATH.name, selection_sha1=sel_sha, git_hash=_git(),
                         wall_s=round(sum(fl['wall_s'] for fl in flights), 1), created=datetime.now(timezone.utc).isoformat()),
               flights=flights)
    os.makedirs(out_dir, exist_ok=True)
    p = job_path(job, out_dir)
    with open(p, 'w') as fh:
        json.dump(out, fh, indent=1, default=float)
    return p


def main(shard, n_shards, out_dir='results/shards', T=T_FLIGHT):
    from joblib import Parallel, delayed
    import run_phase3 as m  # workers must resolve functions by module name, not __main__ (loky cannot unpickle
    # __main__-level lru_cache wrappers such as _sub; found on the first cloud run)
    hub_sets_, fly_ = m.hub_sets, m.fly
    G = gains()
    sel_sha = hashlib.sha1(open(SEL_PATH, 'rb').read()).hexdigest()[:16]
    mine = jobs(G)[shard::n_shards]
    if not mine:
        return []
    nj = os.cpu_count() or 1
    need_hubs = sorted({w for gs, w, c, _ in mine if CONDS[c][0] == 'hubs_topk' and G[gs][w] > 0})
    hubs = dict(zip(need_hubs, Parallel(n_jobs=min(nj, max(1, len(need_hubs))))(delayed(hub_sets_)(w) for w in need_hubs)))
    specs = [(j, sp) for j in mine for sp in flight_specs(j, G, hubs, T, out_dir)]
    if any(sp['K'] > 0 for _, sp in specs):
        brain.run_open_loop(None, lambda t: 0 * t, 0.01, 0, codegen=CODEGEN)  # warm the cython cache before forking
    res = Parallel(n_jobs=min(nj, len(specs)))(delayed(fly_)(**sp) for _, sp in specs)
    paths = []
    for j in mine:
        paths.append(write_job(j, G, [r for (jj, _), r in zip(specs, res) if jj == j], T, out_dir, sel_sha))
    print('\n'.join(paths))
    return paths


def plan(G):
    J = jobs(G)
    brain_flights = sum(CHUNK for gs, w, c, i in J if G[gs][w] > 0)
    cpu_h = brain_flights * (T_FLIGHT + 1.0 + 3.0) * WALL_PER_SIM_S / 3600  # +1 s warm-up, ~3 s build
    hub_h = 65 * len({(w) for gs, w, c, i in J if CONDS[c][0] == 'hubs_topk'}) * 6 / 3600  # recomputed on ~6 shards each
    n_sh = 20
    per_shard_h = (cpu_h + hub_h) / n_sh / 4
    return dict(gains=G, n_jobs=len(J), n_flights=len(J) * CHUNK, n_brain_flights=brain_flights,
                cpu_h_m4=round(cpu_h + hub_h, 1), suggested_n_shards=n_sh,
                wall_h_per_shard_m4=round(per_shard_h, 2), wall_h_per_shard_runner_2x=round(2 * per_shard_h, 2))


def _selfcheck_lesions():
    """Cheap, no Brian2: nested random sets, paired across wirings, deterministic job list."""
    n = len(_sub()['cell_type'])
    for s in (100, 119):
        sets = [lesion_idx('real', f'random{round(100 * f):02d}', s) for f in nulls.FRACTIONS]
        assert all(np.isin(a, b).all() for a, b in zip(sets, sets[1:])), 'random lesions not nested'
        assert [len(x) for x in sets] == [round(f * n) for f in nulls.FRACTIONS]
        assert all(np.array_equal(lesion_idx(w, 'random40', s), sets[4]) for w in WIRINGS), 'not paired'
    assert not np.array_equal(lesion_idx('real', 'random40', 100), lesion_idx('real', 'random40', 101))
    hs = lesion_idx('real', 'all_HS', 100)
    assert len(hs) == 6 and all(str(_sub()['cell_type'][i]).startswith('HS') for i in hs)
    t4, t5 = lesion_idx('real', 'T4_only', 100), lesion_idx('real', 'T5_only', 100)
    assert len(t4) and len(t5) and not np.intersect1d(t4, t5).size
    G = dict(selected={w: 0.01 for w in WIRINGS}, explore=None)
    J = jobs(G)
    assert len(J) == len(set(J)) == 4 * 13 * 2 and len(jobs(dict(G, explore=G['selected']))) == 2 * len(J)
    assert sorted(sum((J[k::7] for k in range(7)), [])) == sorted(J)  # every job lands on exactly one shard


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--shard', type=int)
    ap.add_argument('--n-shards', type=int)
    ap.add_argument('--smoke', action='store_true')
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--T', type=float, default=T_FLIGHT, help='flight length; < 60 s only for local plumbing tests')
    ap.add_argument('--out-dir', default='results/shards')
    a = ap.parse_args()
    _selfcheck_lesions()
    if a.plan:
        try:
            print(json.dumps(plan(gains()), indent=1))
        except FileNotFoundError:  # no selection yet: the two cases the fallback allows (placeholder Ks, not results)
            k = {w: 0.01 for w in WIRINGS}
            print('no phase2_selection.json; if every K > 0:', json.dumps(plan(dict(selected=k, explore=None))))
            print('if real K = 0 (shuffles K > 0), + explore set:',
                  json.dumps(plan(dict(selected=dict(k, real=0.0), explore=k))))
    elif a.smoke:  # 3 s flights on the real wiring, seed 100: 40% random and all_HS, both recorded
        out = os.path.join(tempfile.gettempdir(), 'fttf_phase3_smoke')
        try:
            G = gains()
        except FileNotFoundError:
            print('no results/phase2_selection.json yet: smoke uses K = 0.01 (placeholder, not a result)')
            G = dict(selected={w: 0.01 for w in WIRINGS}, explore=None)
        if G['selected']['real'] == 0:
            G = dict(selected=G['explore'], explore=None)
        K = G['selected']['real']
        t0 = time.time()
        for cond in ('random40', 'all_HS'):
            les = lesion_idx('real', cond, REC_SEED)
            r = fly('selected', 'real', K, cond, REC_SEED, les, T=3.0, rec_path=f'{out}/rec_lesion_{cond}_s100.json')
            p = write_job(('selected', 'real', cond, 0), G, [r], 3.0, out, 'smoke')
            print(cond, {k: r[k] for k in ('n_lesioned', 'n_edges_used', 'rms_r', 'departed', 'wall_s')})
            full = n_edges_expected(np.array([], int))
            assert r['n_edges_used'] == n_edges_expected(les) < full, (r['n_edges_used'], full)
            rec = json.load(open(f'{out}/{r["recording"]}'))
            import make_mock_runs
            make_mock_runs.validate(rec)
            kind = CONDS[cond][0]
            assert rec['meta']['lesion'] == dict(kind=kind, fraction=CONDS[cond][1]) and len(rec['t']) == 300
            assert len(rec['raster']['spikes']) > 0 and json.load(open(p))['flights'][0]['seed'] == REC_SEED
        print(f'smoke OK in {time.time() - t0:.0f} s; full network uses {full} edges ->', out)
    else:
        assert 0 <= a.shard < a.n_shards
        main(a.shard, a.n_shards, out_dir=a.out_dir, T=a.T)
