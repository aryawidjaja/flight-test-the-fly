"""Phase 2 (H2, H2b, H3): closed-loop yaw damping on lin2 (2a) and JSBSim c172x (2b). Tuning rule, grids and seeds were
fixed before the main experiments (see README, Protocol and amendments). Stages:

  --stage tune   --plant lin2|jsbsim [--signflip]   cloud. One job per (fly controller, sign, K > 0) + one damper job.
                 Each fly job flies train seeds 0-9 (60 s, moderate Dryden); on lin2 it also measures loop margins
                 by actuator injection (MARGIN below). -> results/shards/phase2_tune_<plant>_<controller>_K<i>.json
  --stage select                                   local, cheap. Pre-specified rule -> results/phase2_selection.json
  --stage test   --plant lin2|jsbsim [--signflip]   cloud. One job per (controller, sign) at its selected K, test seeds
                 100-119. -> results/shards/phase2_test_<plant>_<controller>.json and, for seed 100, the replay
                 recording results/shards/rec_<plant>_<controller>_s100.json
  --estimate     print job count and simulated seconds for the chosen stage/plant (no runs)
  --smoke        local self-check, < 2 min wall, train seeds only

  uv run python scripts/run_phase2.py --shard i --n-shards N --stage tune --plant lin2   # GitHub Actions (sweep.yml)

--signflip runs the pre-specified secondary sign analysis: sign = -1 for the wirings whose identified_sign is -1 in
results/phase1_bode.json (that file must be committed before dispatch). The test stage needs results/phase2_selection.json
committed. Controller names: bare, yaw_damper, fly_real, fly_shuffle_00..09 (recording-format spelling), + '_signflip' for
sign = -1. EXPLORATORY arm (added before any Phase 2 tuning or test data), lin2 only, included automatically in tune/select/test with --plant
lin2: the same fly controllers at input gain g = 4, named fly_real_g4, fly_shuffle_NN_g4; same grid, rule and seeds.
Selection schema (Phase 3 reads it): sel[plant][controller] = {sign, selected_K, admissible_K, rows, unconstrained, ...}.

Margin protocol (revised after an internal review, before any Phase 2 data was used):
ONE protocol for every controller, damper included (equal tuning). A = 0.1 rudder_cmd, 17 frequencies f = 100/n Hz
from 0.05 to 33.3 Hz plus the Nyquist frequency 50 Hz (injected as A*(-1)^k, since a sine is zero there), settle 20 s,
window = whole cycles >= 40 s (>= 2 cycles), Poisson seed 0 (a train seed), turbulence off. If |L| >= 1 at the lowest
frequency, a washout-side crossover may lie below the grid, so PM is set to NaN (inadmissible). The damper's exact
analytic margins are stored as a check only.
"""
import argparse, glob, json, math, os, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import baselines, brain, make_mock_runs, plant, stats  # noqa: E402
from run_phase1 import _git, edges_for  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SHARDS = ROOT / 'results' / 'shards'
SELECTION = ROOT / 'results' / 'phase2_selection.json'
PHASE1 = ROOT / 'results' / 'phase1_bode.json'
TRAIN, TEST = list(baselines.TRAIN_SEEDS), list(range(100, 120))
REC_SEED, T_FLIGHT, TURB = 100, 60.0, 'moderate'
WIRINGS = ['real'] + [f'shuf{k:02d}' for k in range(10)]
K_FLY = [0.0] + list(np.geomspace(1e-4, 1e-1, 8))          # per Hz (fixed before the main experiments)
K_DAMPER = list(baselines.K_GRID_DAMPER)
# Pre-specified edge rule triggered (best K on the upper edge): every grid widened by 2 points at the same log step, once.
for _g in (K_FLY, K_DAMPER):
    _r = _g[-1] / _g[-2]
    _g += [_g[-1] * _r, _g[-1] * _r * _r]
K_BASE_N = 9    # indices 0..8 = the original grid; 9..10 = the widening
REQ = baselines.MARGIN_REQ
G_EXPLORE = 4.0                                           # exploratory arm, lin2 only
MARGIN = dict(freqs=[100 / n for n in (2000, 1500, 1000, 800, 600, 450, 360, 290, 215, 129, 77, 46, 28, 17, 10, 5, 3, 2)],
              A=0.1, settle=20.0, min_window=40.0, n_cycles=2, seed=0)   # 100/2 = 50 Hz = Nyquist at dt = 10 ms
MARGIN_NOTE = (
    'A = 0.1: at the lin2 Dutch-roll peak |P_r| ~ 0.9 rad/s per unit cmd, so the injected yaw rate is ~0.09 rad/s '
    '(5 deg/s), 1.8x the bare RMS r in moderate turbulence (0.052 rad/s): the brain is probed at its flight operating '
    'amplitude, not at the 100 deg/s Bode amplitude. Brain noise: one DNa02 per side, D quantized in 100 Hz steps per '
    '10 ms, ~70 Hz rms, white; the lock-in error of L over N = 4000 samples is ~70 K sqrt(2/N)/A ~ 16 K, i.e. <= 0.08 '
    'for K <= 5.2e-3 against the 0.5 (6 dB) threshold. u_ctrl noise is ~70 K rms, so for K <= 5.2e-3 the input '
    '|d + u_ctrl| stays below 1 almost always; for K >= 1.4e-2 the rudder is noise-saturated and the measured L is a '
    'describing function (sat_frac and L_noise are reported). Frequencies 100/n Hz (integer samples per cycle): 0.05 Hz '
    'catches a washout-side |L| = 1 crossing (the damper has them at 0.09-0.15 Hz), 0.28 Hz sits at the Dutch roll, and '
    '1-10 Hz bracket the fly phase crossover expected near 1/(4 x ~60 ms delay) ~ 4 Hz; 20 Hz guards later crossings. '
    'Revised after an internal review, before the closed-loop tuning data were used: grid densified (17 sines + Nyquist) because 12-point interpolation overstated PM by ~1.2 deg '
    'and could not see the damper GM at Nyquist; settle 20 s because the closed-loop washout mode decays in up to ~10 s '
    '(8 s left a 3.4 % bias). Cost 18 x (1 s warm-up + 20 s + >= 40 s) ~ 1.1e3 sim-s per K.')


def cname(wiring, sign, g=1.0):
    """fly_real / fly_shuffle_NN (recording-format spelling), + '_signflip' for sign = -1, + '_g4' for the exploratory arm."""
    base = 'fly_real' if wiring == 'real' else f'fly_shuffle_{wiring[4:]}'
    return base + ('_signflip' if sign < 0 else '') + ('' if g == 1 else f'_g{g:g}')


def signflip_wirings(path=PHASE1):
    """Wirings with identified_sign = -1 in Phase 1, or None if results/phase1_bode.json does not exist yet."""
    if not Path(path).exists():
        return None
    W = json.loads(Path(path).read_text()).get('wirings', {})
    return [w for w in WIRINGS if W.get(w, {}).get('identified_sign') == -1]


def _need_signflip():
    ws = signflip_wirings()
    if ws is None:
        sys.exit(f'--signflip needs {PHASE1} (Phase 1 analysis); it does not exist yet')
    return ws


def jobs(stage, plant_name, signflip=False, k_min=1):
    """Job tuples (stage, plant, kind, wiring, sign, K_index, g). K_index None = the controller's selected K (test).
    On lin2 (not --signflip) the EXPLORATORY g = 4 arm is appended after the primary jobs,
    so the primary jobs keep their shard assignment."""
    gs = [1.0] + ([G_EXPLORE] if plant_name == 'lin2' and not signflip else [])
    if stage == 'tune':
        ws, s = (_need_signflip(), -1) if signflip else (WIRINGS, +1)
        J = [] if signflip else [('tune', plant_name, 'damper', None, +1, None, 1.0)]
        return J + [('tune', plant_name, 'fly', w, s, i, g) for g in gs for i in range(max(1, k_min), len(K_FLY)) for w in ws]
    if signflip:
        return [('test', plant_name, 'fly', w, -1, None, 1.0) for w in _need_signflip()]
    return ([('test', plant_name, 'bare', None, +1, None, 1.0), ('test', plant_name, 'damper', None, +1, None, 1.0)] +
            [('test', plant_name, 'fly', w, +1, None, g) for g in gs for w in WIRINGS])


def sim_seconds(job):
    """Simulated seconds of brain time in one job (1 s warm-up per flight); 0 for jobs without a brain."""
    stage, pl, kind, *_ = job
    if kind != 'fly':
        return 0.0
    if stage == 'test':
        return len(TEST) * (T_FLIGHT + 1)
    margin = sum(1 + MARGIN['settle'] + _n_win(f) * plant.DT for f in MARGIN['freqs']) if pl == 'lin2' else 0
    return len(TRAIN) * (T_FLIGHT + 1) + margin


def _n_win(f, dt=plant.DT):  # identical to plant.loop_margins
    nc = max(MARGIN['n_cycles'], int(np.ceil(MARGIN['min_window'] * f)))
    return int(round(nc / f / dt))


# ---------------------------------------------------------------- margins
def measure_margins(controller, freqs, A, settle, min_window, n_cycles, seed=0, dt=plant.DT):
    """plant.loop_margins (same injection, settle, window and lock-in; asserted equal in --smoke) plus, per frequency,
    the saturation fraction of the actuator input in the window and a noise floor L_noise = mean |U_ctrl| at the two
    adjacent DFT bins f +- 1/T_window (orthogonal to f over the window) divided by |U_plant|."""
    L, noise, sat = [], [], []
    for f in freqs:
        nc = max(n_cycles, int(np.ceil(min_window * f)))
        nw = int(round(nc / f / dt))
        nyq = abs(f * 2 * dt - 1) < 1e-9  # sin(pi k) = 0 at Nyquist: inject the alternating sequence instead
        inj = (lambda t: A * np.cos(np.pi * round(t / dt))) if nyq else (lambda t, f=f: A * np.sin(2 * np.pi * f * t))
        R = plant.simulate(controller, seed, T=settle + nw * dt, plant='lin2', turbulence='none', dt=dt, inject=inj)
        s = slice(-nw, None)
        t, uc, ur = R['t'][s], R['u_ctrl'][s], R['rudder'][s]
        Up = plant._lockin(ur, t, f)  # at Nyquist exp(-j 2 pi f t_k) = (-1)^k: the lock-in is exact there too
        Lf = -plant._lockin(uc, t, f) / Up
        L.append(complex(Lf.real, 0.0) if nyq else Lf)  # exactly real at Nyquist; drop float round-off in the imag part
        df = 1 / (nw * dt)
        nb = [abs(plant._lockin(uc, t, g)) for g in (f - df, f + df) if g > 0]
        noise.append(float(np.mean(nb) / abs(Up)))
        sat.append(float(np.mean(np.abs(ur) >= 1.0)))
    L = np.array(L)
    M = plant.margins(np.asarray(freqs, float), L)
    if abs(L[0]) >= 1:  # a washout-side |L| = 1 crossing may lie below the grid: PM unknown -> inadmissible
        M['pm_deg'], M['pm_unresolved_below_grid'] = float('nan'), True
    return dict(freqs=list(map(float, freqs)), L_re=L.real.tolist(), L_im=L.imag.tolist(), L_abs=np.abs(L).tolist(),
                L_phase_deg=np.degrees(np.angle(L)).tolist(), L_noise=noise, sat_frac=sat,
                sat_frac_mean=float(np.mean(sat)), sat_frac_max=float(np.max(sat)), A=A, settle=settle,
                min_window=min_window, n_cycles=n_cycles, seed=seed, **M)


def margins_ok(m):
    return bool(m is not None and m['gm_db'] >= REQ['gm_db'] and m['pm_deg'] >= REQ['pm_deg'])


# ---------------------------------------------------------------- flights
def flight(controller, seed, plant_name, T=T_FLIGHT):
    R = plant.simulate(controller, seed, T=T, plant=plant_name, turbulence=TURB)
    return R, dict(seed=seed, **R['metrics'], sat_frac=float(np.mean(np.abs(R['rudder']) >= 1.0)))


def _meta(**kw):
    return dict(phase=2, turbulence='dryden_' + TURB, data_version=brain.DATA_VERSION, subcircuit=brain.SUB_PATH.name,
                git_hash=os.environ.get('GITHUB_SHA') or _git(), created=datetime.now(timezone.utc).isoformat(), **kw)


def _dump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        json.dump(obj, fh, indent=1, default=float)
    return path


def run_tune(job, out_dir=SHARDS, seeds=TRAIN, T=T_FLIGHT, margin_kw=MARGIN, codegen='cython'):
    _, pl, kind, wiring, sign, ki, g = job
    t0 = time.time()
    if kind == 'damper':
        rows = []
        for i, K in enumerate(K_DAMPER):
            c = baselines.Bare() if K == 0 else baselines.YawDamper(K)
            row = dict(K=float(K), K_index=i, train=[flight(c, s, pl, T)[1] for s in seeds])
            if pl == 'lin2' and K > 0:
                row['margins_analytic'] = baselines.damper_margins_analytic(K)['discrete']  # stored check only
                row['margins'] = measure_margins(c, **margin_kw)  # the same injection protocol as every fly controller
            rows.append(row)
        out = dict(meta=_meta(stage='tune', plant=pl, controller='yaw_damper', seeds=list(seeds), T=T, wall_s=time.time() - t0,
                              margin_protocol=margin_kw, margin_source='injected, same protocol as the fly (rule); analytic (check)'),
                   controller='yaw_damper', sign=1, rows=rows)
        return _dump(out, f'{out_dir}/phase2_tune_{pl}_yaw_damper.json')
    sub = brain.load_subcircuit()
    K, name = K_FLY[ki], cname(wiring, sign, g)
    fc = brain.FlyController(K, sign=sign, edges=edges_for(wiring, sub), g=g, sub=sub, codegen=codegen, name=name,
                             record_raster=False)
    try:
        train = [flight(fc, s, pl, T)[1] for s in seeds]
        m = measure_margins(fc, **margin_kw) if pl == 'lin2' else None
    finally:
        fc.close()
    out = dict(meta=_meta(stage='tune', plant=pl, controller=name, wiring=wiring, sign=sign, g=g, exploratory=g != 1,
                          K=K, K_index=ki,
                          seeds=list(seeds), T=T, codegen=codegen, wall_s=time.time() - t0, margin_protocol=margin_kw,
                          shuffle_seed=None if wiring == 'real' else int(wiring[4:])),
               controller=name, sign=sign, rows=[dict(K=K, K_index=ki, train=train, margins=m)])
    return _dump(out, f'{out_dir}/phase2_tune_{pl}_{name}_K{ki}.json')


# ---------------------------------------------------------------- recording (replay format); Phase 3 imports this
def to_recording(R, meta=None, max_bytes=make_mock_runs.MAX_BYTES):
    """plant.simulate result -> replay-format dict (see README, Recording format). meta overrides/extends R['meta']
    (e.g. controller, lesion, K).
    Positions to 0.01 m, t to 1 ms, spike times to 0.1 ms, other floats to 4 significant digits. If the JSON exceeds
    max_bytes, every time series is downsampled by 2 then 4 (groups averaged over the block, meta.dt updated), spikes
    are always kept in full. Metrics come from the full-resolution flight."""
    m = dict(R['meta'])
    m.update(meta or {})
    m.setdefault('lesion', dict(kind='none', fraction=0.0))
    m.update(data_version=brain.DATA_VERSION, mock=False, created=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    dt = m['dt']
    met = {k: (bool(v) if isinstance(v, (bool, np.bool_)) else float(f'{v:.4g}')) for k, v in R['metrics'].items()}
    G = make_mock_runs.GROUPS
    has_brain = all(g in R['groups'] for g in G)
    neurons = [dict(idx=int(n['idx']), type=n['type'], side=n['side']) for n in R['raster']['neurons']]
    spikes = [[round(float(t), 4), int(i)] for t, i in R['raster']['spikes']]
    for step in (1, 2, 4):
        n = len(R['t']) // step * step
        k = slice(step - 1, n, step)
        rec = dict(meta=dict(m, dt=dt * step, downsample=step), t=[round(float(x), 3) for x in R['t'][k]],
                   pos_ned_m=np.round(R['pos_ned_m'][k], 2).tolist(), euler_rad=make_mock_runs.rows(R['euler_rad'][k]),
                   r=make_mock_runs.sig4(R['r'][k]), beta=make_mock_runs.sig4(R['beta'][k]),
                   rudder=make_mock_runs.sig4(R['rudder'][k]), gust=make_mock_runs.rows(R['gust'][k]),
                   groups={g: make_mock_runs.sig4(np.asarray(R['groups'][g][:n]).reshape(-1, step).mean(1)) for g in G}
                   if has_brain else {},
                   raster=dict(neurons=neurons, spikes=spikes), metrics=met)
        size = len(json.dumps(rec, separators=(',', ':')))
        if size <= max_bytes:
            break
    rec['meta']['bytes'] = size
    if size > max_bytes:
        rec['meta']['oversize'] = True  # still written; the viewer loads it, flagged for review
    return rec


def write_recording(rec, path):
    make_mock_runs.validate(rec)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(json.dumps(rec, separators=(',', ':')))
    return path


# ---------------------------------------------------------------- test
def selected_K(plant_name, controller, path=SELECTION):
    return float(json.loads(Path(path).read_text())[plant_name][controller]['selected_K'])


def run_test(job, out_dir=SHARDS, seeds=TEST, T=T_FLIGHT, K=None, rec_seed=REC_SEED, codegen='cython'):
    _, pl, kind, wiring, sign, _, g = job
    name = {'bare': 'bare', 'damper': 'yaw_damper'}.get(kind) or cname(wiring, sign, g)
    K = 0.0 if kind == 'bare' else (selected_K(pl, name) if K is None else float(K))
    t0, fc = time.time(), None
    if K == 0:
        c = baselines.Bare()   # K = 0 is the bare airframe: no brain is run (fly controllers marked equals_bare)
    elif kind == 'damper':
        c = baselines.YawDamper(K)
    else:
        sub = brain.load_subcircuit()
        c = fc = brain.FlyController(K, sign=sign, edges=edges_for(wiring, sub), g=g, sub=sub, codegen=codegen, name=name,
                                     record_raster=False)
    meta = dict(controller=name, plant=pl, K=K, sign=sign, g=g, exploratory=g != 1, wiring=wiring,
                equals_bare=bool(K == 0 and kind != 'bare'))
    rows, rec_path = [], None
    try:
        for s in seeds:
            if fc is not None:
                fc.record_raster = s == rec_seed
            R, row = flight(c, s, pl, T)
            rows.append(row)
            if s == rec_seed:
                rec_path = write_recording(to_recording(R, dict(meta, seed=s)), f'{out_dir}/rec_{pl}_{name}_s{s}.json')
    finally:
        if fc is not None:
            fc.close()
    out = dict(meta=_meta(stage='test', seeds=list(seeds), T=T, codegen=codegen, wall_s=time.time() - t0,
                          recording=rec_path and os.path.basename(rec_path), **meta), flights=rows)
    return _dump(out, f'{out_dir}/phase2_test_{pl}_{name}.json')


# ---------------------------------------------------------------- select (pre-specified tuning rule)
def _edge(i, n):
    """Only the upper edge can call for a wider grid: K = 0 is the bare reference and is always on the grid
    (a destabilizing-sign wiring picks K = 0, which no widening fixes)."""
    return 'high' if i == n - 1 else None


def select(shard_dir=SHARDS, out=SELECTION, phase1=PHASE1):
    """admissible K = {0} U {K > 0: no train departure on that plant AND lin2 GM >= 6 dB AND PM >= 45 deg};
    selected = min mean train RMS r over admissible K (ties -> smaller K). 2b uses the lin2 margins of the same
    (controller, sign, K). Missing shards are listed and their K excluded."""
    T, seen = {}, {}
    for p in sorted(glob.glob(f'{shard_dir}/**/phase2_tune_*.json', recursive=True)):
        d = json.loads(Path(p).read_text())
        for row in d['rows']:
            key = (d['meta']['plant'], d['controller'], row['K_index'])
            stats.check_duplicate(seen, key, dict(meta=d['meta'], row=row), p)   # identical or ValueError
            T[key] = row
    ws = signflip_wirings(phase1) or []
    expected = [('yaw_damper', 1, K_DAMPER)] + [(cname(w, 1), 1, K_FLY) for w in WIRINGS] + \
               [(cname(w, -1), -1, K_FLY) for w in ws]
    g4 = [(cname(w, 1, G_EXPLORE), 1, K_FLY) for w in WIRINGS]   # exploratory, lin2 only
    res = dict(meta=dict(phase=2, rule='admissible K = {0} U {K: no train departure on the plant, lin2 injected GM >= 6 dB '
                         'and PM >= 45 deg}; selected = argmin mean train RMS r (ties -> smaller K). 2b reuses the lin2 '
                         'margins. Every controller, damper included, by the same injection protocol (analytic damper margins stored as a check).', margin_req=REQ, margin_protocol=MARGIN,
                         margin_note=MARGIN_NOTE, K_fly=K_FLY, K_damper=K_DAMPER, train_seeds=TRAIN,
                         signflip_wirings=signflip_wirings(phase1), data_version=brain.DATA_VERSION,
                         git_hash=_git(), created=datetime.now(timezone.utc).isoformat()))
    widen = []
    for pl in ('lin2', 'jsbsim'):
        bare = T.get((pl, 'yaw_damper', 0))
        res[pl] = {}
        for name, sign, grid in expected + (g4 if pl == 'lin2' else []):
            rows, missing = [], []
            for i, K in enumerate(grid):
                row = bare if i == 0 else T.get((pl, name, i))
                mrow = None if i == 0 else T.get(('lin2', name, i))
                if row is None or (i > 0 and mrow is None):
                    missing.append(dict(K_index=i, plant_row=row is not None, lin2_margins=mrow is not None))
                    continue
                tr = row['train']
                assert [x['seed'] for x in tr] == TRAIN, (pl, name, i)
                m = None if i == 0 else mrow['margins']
                dep = int(sum(x['departed'] for x in tr))
                mok = True if i == 0 else margins_ok(m)
                rows.append(dict(K=float(K), K_index=i, mean_rms_r=float(np.mean([x['rms_r'] for x in tr])),
                                 mean_rms_beta=float(np.mean([x['rms_beta'] for x in tr])),
                                 mean_rms_rudder=float(np.mean([x['rms_rudder'] for x in tr])), n_departed=dep,
                                 gm_db=None if m is None else m['gm_db'], pm_deg=None if m is None else m['pm_deg'],
                                 f_gm_hz=None if m is None else m['f_gm_hz'], f_pm_hz=None if m is None else m['f_pm_hz'],
                                 margin_sat_frac_max=None if m is None else m.get('sat_frac_max'),
                                 margins_ok=mok, admissible=bool(i == 0 or (dep == 0 and mok))))
            e = dict(sign=sign, exploratory=name.endswith('_g4'), grid=list(map(float, grid)), rows=rows, missing=missing)
            if not rows or rows[0]['K_index'] != 0:
                e.update(selected_K=None, note='bare (K = 0) train shard missing')
                res[pl][name] = e
                continue
            adm = [r for r in rows if r['admissible']]
            b = min(adm, key=lambda r: r['mean_rms_r'])        # min() keeps the first (smallest K) on ties
            u = min(rows, key=lambda r: r['mean_rms_r'])
            e.update(selected_K=b['K'], K_index_selected=b['K_index'], selected_mean_rms_r_train=b['mean_rms_r'],
                     selected_gm_db=b['gm_db'], selected_pm_deg=b['pm_deg'],
                     admissible_K=[r['K'] for r in adm], selected_edge=_edge(b['K_index'], len(grid)),
                     unconstrained=dict(K=u['K'], K_index=u['K_index'], mean_rms_r=u['mean_rms_r'],
                                        edge=_edge(u['K_index'], len(grid))))
            if e['unconstrained']['edge'] and not e['exploratory']:
                widen.append(f'{pl} {name}: unconstrained best K={u["K"]:g} on the {e["unconstrained"]["edge"]} edge')
            res[pl][name] = e
    res['widen_grid_for_all'] = bool(widen)
    res['widen_reasons'] = widen
    res['missing'] = {pl: {k: v['missing'] for k, v in res[pl].items() if v['missing']} for pl in ('lin2', 'jsbsim')}
    if out:
        _dump(res, str(out))
    return res


# ---------------------------------------------------------------- driver
def _warm(codegen='cython'):
    """Compile the FlyController's cython code once before joblib forks (as run_phase1 does)."""
    fc = brain.FlyController(1e-3, codegen=codegen, record_raster=False)
    fc.reset(0)
    for k in range(3):
        fc.step(dict(t=k * 0.01, r=0.0, p=0.0, beta=0.0, phi=0.0), 0.01)
    fc.close()


def run_job(job):
    return run_tune(job) if job[0] == 'tune' else run_test(job)


def main(stage, plant_name, signflip, shard, n_shards, k_min=1):
    from joblib import Parallel, delayed
    mine = jobs(stage, plant_name, signflip, k_min)[shard::n_shards]
    if any(j[2] == 'fly' for j in mine):
        _warm()
    paths = Parallel(n_jobs=max(1, min(len(mine), os.cpu_count() or 1)))(delayed(run_job)(j) for j in mine)
    print('\n'.join(paths))
    return paths


def estimate(stage, plant_name, signflip, wall_per_sim_s=1.5, cores=4):
    """CPU time at an assumed runner speed; one shard = one round of <= `cores` parallel jobs (jobs[shard::n])."""
    s = [sim_seconds(j) for j in jobs(stage, plant_name, signflip)]
    return dict(stage=stage, plant=plant_name, signflip=signflip, n_jobs=len(s), n_brain_jobs=sum(x > 0 for x in s),
                sim_s_total=sum(s), sim_s_per_brain_job=max(s), cpu_h=sum(s) * wall_per_sim_s / 3600,
                wall_per_sim_s_assumed=wall_per_sim_s, shard_wall_h=max(s) * wall_per_sim_s / 3600,
                suggested_n_shards=math.ceil(len(s) / cores))


def smoke(d=os.path.join(tempfile.gettempdir(), 'fttf_phase2_smoke')):
    """Train seeds only. Fly tune job (T = 3 s, 2 seeds, 2-frequency margins with a short settle), the margin
    function against plant.loop_margins on the damper, a 3 s recording export validated against the recording format."""
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    t0 = time.time()
    # 1. measure_margins == plant.loop_margins (same protocol), on the noiseless damper
    mk = dict(freqs=[0.2, 1.0], A=0.1, settle=4.0, min_window=4.0, n_cycles=2, seed=0)
    a = measure_margins(baselines.YawDamper(7.2), **mk)
    b = plant.loop_margins(baselines.YawDamper(7.2), 'lin2', freqs=mk['freqs'], A=0.1, settle=4.0, n_cycles=2, min_window=4.0)
    assert np.allclose(np.array(a['L_re']) + 1j * np.array(a['L_im']), b['L'], rtol=1e-12, atol=1e-15)
    assert a['sat_frac_max'] == 0
    # full protocol on the noiseless damper: L_noise is then only transient leakage, which must be small vs |L|
    f = measure_margins(baselines.YawDamper(7.2), **MARGIN)
    rel = np.array(f['L_noise']) / np.array(f['L_abs'])
    print('damper K=7.2, full margin protocol: PM %.1f deg (analytic %.1f), GM %s; max L_noise/|L| %.2e; sat %.2f' % (
        f['pm_deg'], baselines.damper_margins_analytic(7.2)['discrete']['pm_deg'], f['gm_db'], rel.max(), f['sat_frac_max']))
    assert rel.max() < 0.02 and f['sat_frac_max'] == 0
    # 2. fly tune job, lin2, K index 4 (1.93e-3)
    p = run_tune(('tune', 'lin2', 'fly', 'real', +1, 4, 1.0), out_dir=d, seeds=[0, 1], T=3.0,
                 margin_kw=dict(freqs=[0.5, 2.0], A=0.1, settle=1.0, min_window=2.0, n_cycles=2, seed=0))
    t1 = time.time()
    r = json.loads(Path(p).read_text())['rows'][0]
    assert [x['seed'] for x in r['train']] == [0, 1] and all(np.isfinite(x['rms_r']) for x in r['train'])
    m = r['margins']
    assert len(m['L_re']) == 2 and all(0 <= s <= 1 for s in m['sat_frac']) and all(np.isfinite(m['L_noise']))
    print('tune job', os.path.basename(p), f'{t1 - t0:.1f} s wall;', 'train', [(x['seed'], round(x['rms_r'], 5),
          round(x['rms_rudder'], 3), x['sat_frac']) for x in r['train']])
    print('  margins |L|', np.round(m['L_abs'], 4), 'phase', np.round(m['L_phase_deg'], 1), 'noise', np.round(m['L_noise'], 4),
          'sat', m['sat_frac'], 'GM', m['gm_db'], 'PM', m['pm_deg'])
    # 3. recording export (train seed 0, 3 s, raster on): recording-format keys, size, groups, spikes
    p = run_test(('test', 'lin2', 'fly', 'real', +1, None, 1.0), out_dir=d, seeds=[0], T=3.0, K=K_FLY[4], rec_seed=0)
    rec = json.loads(Path(f'{d}/rec_lin2_fly_real_s0.json').read_text())
    make_mock_runs.validate(rec)
    assert set(make_mock_runs.TOP_KEYS) <= set(rec) and rec['meta']['mock'] is False and rec['meta']['downsample'] == 1
    assert len(rec['t']) == 300 and len(rec['raster']['spikes']) > 0 and rec['raster']['neurons']
    assert all(-0.01 <= s[0] <= 3.0 for s in rec['raster']['spikes'])
    size = rec['meta']['bytes']
    print(f'recording 3 s: {size / 1e6:.3f} MB, {len(rec["raster"]["spikes"])} spikes, {len(rec["raster"]["neurons"])} '
          f'neurons -> 60 s ~ {20 * size / 1e6:.1f} MB (limit 5.24 MB; exporter downsamples if needed)')
    # 4. exporter downsampling path: force a tiny limit and check alignment
    R = dict(t=np.arange(1, 9) * 0.01, pos_ned_m=np.zeros((8, 3)), euler_rad=np.zeros((8, 3)), r=np.zeros(8),
             beta=np.zeros(8), rudder=np.zeros(8), gust=np.zeros((8, 3)),
             groups={g: np.arange(8.0) for g in make_mock_runs.GROUPS}, raster=dict(neurons=[], spikes=[]),
             metrics=dict(rms_r=0.0, rms_beta=0.0, max_abs_phi=0.0, departed=False),
             meta=dict(controller='x', aircraft='c172x', seed=0, turbulence='none', dt=0.01))
    q = to_recording(R, max_bytes=1)
    assert q['meta']['downsample'] == 4 and q['t'] == [0.04, 0.08] and q['groups']['HS_L'] == [1.5, 5.5] and q['meta']['oversize']
    for st, pl in (('tune', 'lin2'), ('tune', 'jsbsim'), ('test', 'lin2')):
        print('estimate', json.dumps(estimate(st, pl, False)))
    print(f'smoke OK in {time.time() - t0:.1f} s wall ->', d)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', choices=['tune', 'select', 'test'])
    ap.add_argument('--plant', choices=['lin2', 'jsbsim'])
    ap.add_argument('--signflip', action='store_true')
    ap.add_argument('--shard', type=int)
    ap.add_argument('--n-shards', type=int)
    ap.add_argument('--shards-dir', default=str(SHARDS))
    ap.add_argument('--estimate', action='store_true')
    ap.add_argument('--smoke', action='store_true')
    ap.add_argument('--k-min', type=int, default=1, help='tune only fly K indices >= this (grid widening: 9)')
    a = ap.parse_args()
    J = jobs('tune', 'lin2')
    assert len(J) == len(set(J)) == 1 + 2 * 10 * 11 and len(jobs('tune', 'jsbsim')) == 1 + 10 * 11
    assert len(K_FLY) == len(K_DAMPER) == baselines.K_GRID_N + 2 and K_FLY[:9] == [0.0] + list(np.geomspace(1e-4, 1e-1, 8))
    assert len(jobs('tune', 'lin2', k_min=9)) == 1 + 2 * 2 * 11
    assert all(abs(100 / f - round(100 / f)) < 1e-9 for f in MARGIN['freqs'])
    assert sim_seconds(J[1]) <= len(TRAIN) * 61 + 1200, sim_seconds(J[1])   # margin budget <= ~1.2e3 sim-s per K (dense margin protocol)
    if a.smoke:
        smoke()
    elif a.stage == 'select':
        r = select(a.shards_dir)
        for pl in ('lin2', 'jsbsim'):
            for k, v in r[pl].items():
                print(pl, k, 'selected_K', v.get('selected_K'), 'admissible', v.get('admissible_K'), 'missing', len(v['missing']))
        print('widen_grid_for_all', r['widen_grid_for_all'], r['widen_reasons'], '->', SELECTION)
    elif a.estimate:
        print(json.dumps(estimate(a.stage, a.plant, a.signflip), indent=1))
    else:
        assert a.stage in ('tune', 'test') and a.plant and 0 <= a.shard < a.n_shards
        main(a.stage, a.plant, a.signflip, a.shard, a.n_shards, a.k_min)
