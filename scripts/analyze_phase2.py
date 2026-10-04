"""Phase 2 analysis (H2, H2b, H3). Reads the test shards + results/phase2_selection.json and writes
results/phase2a_metrics.json (lin2), results/phase2b_metrics.json (jsbsim), results/fig_phase2a.png, fig_phase2b.png.

  uv run python scripts/analyze_phase2.py [--shards-dir results/shards_dl]     # analysis
  uv run python scripts/analyze_phase2.py --app [--shards-dir ...]             # seed-100 recordings -> app/runs + manifest
  uv run python scripts/analyze_phase2.py --selfcheck                          # synthetic data in the system temp dir only

Pre-registered statistics (H2/H2b/H3, fixed before any closed-loop data):
  H2/H3  per test seed d = RMS r(fly_real) - RMS r(bare); mean and 95 % bootstrap CI (percentile = verdict, BCa
         reported). Supported if the percentile CI lies entirely below 0.
  H2b    per test seed d = RMS r(fly_real) - median over the 10 shuffles; bootstrap CI as above. Also the rank of
         fly_real's mean RMS r among the 11 wirings (1 = lowest) and p = (1 + #{shuffles with mean <= real}) / 11,
         floor 1/11 = 0.091 (p < 0.05 is impossible with 10 shuffles).
Phase 3 hook: app_set_scenario(scenario) appends/replaces one manifest scenario and rebuilds app/runs/bundle.js.
"""
import argparse, glob, json, os, shutil, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import make_mock_runs, stats  # noqa: E402
from run_phase2 import REC_SEED, SELECTION, SHARDS, TEST, WIRINGS, _git, cname, select  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'results'
RUNS = ROOT / 'app' / 'runs'
OUT = {'lin2': ('phase2a_metrics.json', 'fig_phase2a.png', '2-state yaw model (lin2)'),
       'jsbsim': ('phase2b_metrics.json', 'fig_phase2b.png', 'JSBSim c172x 6-DOF')}
KEYS = ('rms_r', 'rms_beta', 'rms_rudder', 'max_abs_phi', 'sat_frac')
SHUFS = [cname(w, 1) for w in WIRINGS[1:]]
SHUFS_G4 = [cname(w, 1, 4.0) for w in WIRINGS[1:]]
DEG = 180 / np.pi


def load_tests(shard_dir, plant):
    out = {}
    for p in glob.glob(f'{shard_dir}/**/phase2_test_{plant}_*.json', recursive=True):
        d = json.loads(Path(p).read_text())
        fl = sorted(d['flights'], key=lambda x: x['seed'])
        assert [x['seed'] for x in fl] == TEST, (p, [x['seed'] for x in fl])
        m = d['meta']
        out[m['controller']] = dict(K=m['K'], sign=m['sign'], equals_bare=m['equals_bare'],
                                    departed=[bool(x['departed']) for x in fl],
                                    **{k: np.array([x.get(k, np.nan) for x in fl], float) for k in KEYS})
    return out


def ci(d):
    """Percentile (verdict) and BCa 95 % bootstrap CIs of the mean of paired per-seed differences."""
    d = np.asarray(d, float)
    out = dict(mean=float(d.mean()), n=len(d))
    for m in ('percentile', 'BCa'):
        if np.ptp(d) == 0:   # e.g. K = 0 selected: fly == bare on every seed; the bootstrap is degenerate
            out[m] = dict(mean=float(d[0]), lo=float(d[0]), hi=float(d[0]), n=len(d), method=m,
                          excludes_0=bool(d[0] != 0), degenerate=True)
        else:
            out[m] = stats.bootstrap_ci(d, method=m)
    return out


def compare(C, a, b, key='rms_r'):
    d = C[a][key] - C[b][key]
    return dict(a=a, b=b, metric=key, per_seed=d.tolist(), **ci(d))


def h2b(C, real='fly_real', shufs=SHUFS, key='rms_r'):
    sh = [s for s in shufs if s in C]
    med = np.median(np.stack([C[s][key] for s in sh]), 0)
    d = C[real][key] - med
    means = {w: float(C[w][key].mean()) for w in [real] + sh}
    p = stats.perm_test_real_vs_null(means[real], [means[s] for s in sh], alternative='less')
    return dict(real=real, shuffles=sh, n_null=len(sh), per_seed_diff_vs_median=d.tolist(), **ci(d),
                mean_rms_by_wiring=means, rank_of_real=p['rank'], p=p['p'], p_floor=p['p_min'],
                note=f'rank 1 = lowest mean RMS r of {len(sh) + 1} wirings; p floor 1/{len(sh) + 1}')


def verdict(c):
    return bool(c['percentile']['hi'] < 0)


def analyze_plant(C, sel, plant):
    """All numbers for one plant. C: load_tests output; sel: selection dict (or None)."""
    S = (sel or {}).get(plant, {})
    summ = {k: dict(K=v['K'], sign=v['sign'], equals_bare=v['equals_bare'], n_departed=int(sum(v['departed'])),
                    **{f'mean_{m}': float(np.nanmean(v[m])) for m in KEYS}) for k, v in sorted(C.items())}
    res = dict(controllers=summ)
    if 'fly_real' in C and 'bare' in C:
        h = compare(C, 'fly_real', 'bare')
        res['H2' if plant == 'lin2' else 'H3_H2'] = dict(**h, supported=verdict(h),
                                                       rule='percentile 95 % CI of fly_real - bare (RMS r) entirely < 0')
        res['rms_beta_fly_real_minus_bare'] = compare(C, 'fly_real', 'bare', 'rms_beta')
    if 'fly_real' in C and any(s in C for s in SHUFS):
        h = h2b(C)
        res['H2b' if plant == 'lin2' else 'H3_H2b'] = dict(**h, supported=verdict(h),
                                                         rule='percentile 95 % CI of fly_real - median shuffle entirely < 0')
    if 'yaw_damper' in C and 'bare' in C:
        res['yaw_damper_minus_bare'] = compare(C, 'yaw_damper', 'bare')
        res['rms_beta_yaw_damper_minus_bare'] = compare(C, 'yaw_damper', 'bare', 'rms_beta')
    if 'yaw_damper' in C and 'fly_real' in C:
        res['yaw_damper_minus_fly_real'] = compare(C, 'yaw_damper', 'fly_real')
    res['rudder_activity'] = {k: dict(mean_rms_rudder=v['mean_rms_rudder'], mean_sat_frac=v['mean_sat_frac'])
                              for k, v in summ.items()}
    res['margins_selected'] = {k: dict(K=v.get('selected_K'), gm_db=v.get('selected_gm_db'), pm_deg=v.get('selected_pm_deg'),
                                       source='lin2 injection, same protocol for every controller'
                                              + (' (reused for 2b)' if plant == 'jsbsim' else ''),
                                       train_mean_rms_r=v.get('selected_mean_rms_r_train'))
                               for k, v in S.items()}
    if 'fly_real_g4' in C and 'bare' in C:   # exploratory g = 4 arm (added before any Phase 2 data), never a verdict
        x = dict(label='EXPLORATORY (added after the open-loop data, before any closed-loop data): fly controllers at input gain g = 4, lin2 only. '
                       'Not a pre-registered verdict; the primary H2/H2b use g = 1.',
                 fly_real_g4_minus_bare=compare(C, 'fly_real_g4', 'bare'),
                 rms_beta_fly_real_g4_minus_bare=compare(C, 'fly_real_g4', 'bare', 'rms_beta'))
        if any(s in C for s in SHUFS_G4):
            x['fly_real_g4_vs_median_shuffle_g4'] = h2b(C, 'fly_real_g4', SHUFS_G4)
        if 'yaw_damper' in C:
            x['yaw_damper_minus_fly_real_g4'] = compare(C, 'yaw_damper', 'fly_real_g4')
        if 'fly_real' in C:
            x['fly_real_g4_minus_fly_real_g1'] = compare(C, 'fly_real_g4', 'fly_real')
        for k in [k for k in x if isinstance(x[k], dict)]:
            x[k]['ci_excludes_0_below'] = verdict(x[k])
        res['exploratory_g4'] = x
    # secondary sign analysis (pre-registered): each wiring flown with its own open-loop identified sign
    ws = (sel or {}).get('meta', {}).get('signflip_wirings')
    if ws is None:
        res['secondary_sign'] = dict(status='not run: results/phase1_bode.json did not exist at selection time')
    elif not ws:
        res['secondary_sign'] = dict(status='no wiring has identified sign -1; secondary analysis = primary')
    else:
        idc = {w: cname(w, -1) if w in ws and cname(w, -1) in C else cname(w, 1) for w in WIRINGS}
        miss = [cname(w, -1) for w in ws if cname(w, -1) not in C]
        C2 = dict(C, **{f'id_{w}': C[c] for w, c in idc.items() if c in C})
        sec = dict(status='ok' if not miss else f'missing signflip test shards: {miss}', identified=idc)
        if 'id_real' in C2 and 'bare' in C:
            h = compare(C2, 'id_real', 'bare')
            sec['H2'] = dict(**h, supported=verdict(h))
            h = h2b(C2, 'id_real', [f'id_{w}' for w in WIRINGS[1:]])
            sec['H2b'] = dict(**h, supported=verdict(h))
        res['secondary_sign'] = sec
    return res


# ---------------------------------------------------------------- figure
INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
COL = {'fly_real': '#2a78d6', 'yaw_damper': '#eb6834', 'bare': '#52514e', 'shuf': '#a3a29c'}


def _rec(shard_dir, plant, ctrl):
    p = sorted(glob.glob(f'{shard_dir}/**/rec_{plant}_{ctrl}_s{REC_SEED}.json', recursive=True))
    return json.loads(Path(p[0]).read_text()) if p else None


def figure(C, res, plant, path, shard_dir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 9, 'axes.edgecolor': INK2, 'axes.labelcolor': INK2, 'xtick.color': INK2,
                         'ytick.color': INK2, 'text.color': INK, 'axes.spines.top': False, 'axes.spines.right': False})
    fig = plt.figure(figsize=(13, 6.2), facecolor=SURF)
    gs = fig.add_gridspec(2, 2, width_ratios=[1.35, 1], hspace=0.45, wspace=0.42)
    ax = fig.add_subplot(gs[:, 0], facecolor=SURF)
    order = [c for c in ['bare', 'yaw_damper', 'fly_real'] + SHUFS if c in C]
    rng = np.random.default_rng(0)
    for i, c in enumerate(order):
        y = C[c]['rms_r'] * DEG
        col = COL.get(c, COL['shuf'])
        ax.scatter(i + rng.uniform(-0.18, 0.18, len(y)), y, s=14, color=col, alpha=0.7, lw=0, zorder=2)
        b = stats.bootstrap_ci(y) if np.ptp(y) > 0 else dict(mean=y[0], lo=y[0], hi=y[0])
        ax.errorbar(i + 0.32, b['mean'], yerr=[[b['mean'] - b['lo']], [b['hi'] - b['mean']]], fmt='o', ms=4,
                    color=INK, lw=1.5, capsize=0, zorder=3)
    lab = {'bare': 'Bare', 'yaw_damper': 'Damper', 'fly_real': 'Fly\nreal'}
    ax.set_xticks(range(len(order)), [lab.get(c, c[-2:]) + ('\nK=0' if C[c]['equals_bare'] else '') for c in order])
    ns = sum(c in SHUFS for c in order)
    if ns:
        ax.text(len(order) - ns / 2 - 0.5, -0.11, 'shuffled wirings', transform=ax.get_xaxis_transform(), ha='center',
                color=INK2)
    ax.set_ylabel('RMS yaw rate per test seed (deg/s)')
    ax.set_title(f'{OUT[plant][2]}: 20 held-out seeds (dots), mean with 95 % bootstrap CI (black)', loc='left',
                 fontsize=9.5, color=INK)
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.set_ylim(bottom=0)

    # paired differences (the actual tests)
    ax2 = fig.add_subplot(gs[0, 1], facecolor=SURF)
    rows = [(k, n) for k, n in [('H2' if plant == 'lin2' else 'H3_H2', 'Fly real − bare'),
                                ('H2b' if plant == 'lin2' else 'H3_H2b', 'Fly real − median shuffle'),
                                ('yaw_damper_minus_bare', 'Yaw damper − bare'),
                                ('yaw_damper_minus_fly_real', 'Yaw damper − fly real')] if k in res]
    for j, (k, n) in enumerate(rows[::-1]):
        c = res[k]['percentile']
        ax2.plot([c['lo'] * DEG, c['hi'] * DEG], [j, j], color=INK, lw=2, solid_capstyle='round')
        ax2.plot(c['mean'] * DEG, j, 'o', color=INK, ms=5)
    ax2.axvline(0, color=INK2, lw=0.8, ls='--')
    ax2.set_yticks(range(len(rows)), [n for _, n in rows[::-1]])
    ax2.set_xlabel('Paired difference in RMS yaw rate (deg/s), 95 % percentile CI')
    ax2.grid(axis='x', color=GRID, lw=0.8)
    ax2.set_axisbelow(True)

    # seed-100 time series
    ax3 = fig.add_subplot(gs[1, 1], facecolor=SURF)
    have = False
    for c, n in [('bare', 'Bare'), ('yaw_damper', 'Yaw damper'), ('fly_real', 'Fly real')]:
        r = _rec(shard_dir, plant, c)
        if r:
            ax3.plot(r['t'], np.asarray(r['r']) * DEG, color=COL[c], lw=1.2 if c != 'bare' else 1.0, label=n)
            have = True
    if have:
        ax3.legend(frameon=False, ncol=3, loc='upper left', fontsize=8)
        ax3.set_xlabel('Time (s)')
        ax3.set_ylabel('Yaw rate (deg/s)')
        ax3.grid(color=GRID, lw=0.8)
        ax3.set_axisbelow(True)
    else:
        ax3.text(0.5, 0.5, f'seed-{REC_SEED} recordings not found', ha='center', va='center', color=INK2,
                 transform=ax3.transAxes)
        ax3.set_axis_off()
    ax3.set_title(f'Test seed {REC_SEED}, same gust', loc='left', fontsize=9.5, color=INK)
    fig.savefig(path, dpi=150, bbox_inches='tight', facecolor=SURF)
    plt.close(fig)
    return path


# ---------------------------------------------------------------- driver
def analyze(shard_dir=SHARDS, sel_path=SELECTION, out_dir=RESULTS, plants=('lin2', 'jsbsim')):
    sel = json.loads(Path(sel_path).read_text()) if Path(sel_path).exists() else None
    done = {}
    for pl in plants:
        C = load_tests(shard_dir, pl)
        if not C:
            print(f'{pl}: no test shards in {shard_dir}')
            continue
        res = analyze_plant(C, sel, pl)
        jn, fn, title = OUT[pl]
        res['meta'] = dict(phase=2, plant=pl, title=title, test_seeds=TEST, n_controllers=len(C),
                           shard_dir=os.path.relpath(os.path.abspath(shard_dir), ROOT), selection=os.path.relpath(os.path.abspath(sel_path), ROOT), data_version='flywire_v783',
                           widen_grid_for_all=(sel or {}).get('widen_grid_for_all'), git_hash=_git(),
                           created=datetime.now(timezone.utc).isoformat())
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / jn).write_text(json.dumps(res, indent=1, default=float))
        figure(C, res, pl, Path(out_dir) / fn, shard_dir)
        done[pl] = res
        print(pl, '->', Path(out_dir) / jn, Path(out_dir) / fn)
    return done


# ---------------------------------------------------------------- app
def app_set_scenario(sc, runs_dir=RUNS, drop_mock=True):
    """Phase 3 hook: put one scenario (dict id/label/runs[{file,label}]) into app/runs/manifest.json, replacing any
    with the same id; with drop_mock, scenarios that only reference mock_*.json files are removed. Rebuilds bundle.js
    (= make_mock_runs.py --bundle-only)."""
    mp = Path(runs_dir) / 'manifest.json'
    man = json.loads(mp.read_text())
    keep = [s for s in man['scenarios'] if s['id'] != sc['id'] and
            not (drop_mock and all(r['file'].startswith('mock_') for r in s['runs']))]
    man['scenarios'] = keep + [sc]
    mp.write_text(json.dumps(man, indent=1))
    return make_mock_runs.write_bundle(Path(runs_dir))


def app_update(shard_dir=SHARDS, sel_path=SELECTION, runs_dir=RUNS):
    """Seed-100 scenarios: fly_real, yaw_damper, best and median shuffle (chosen by TRAIN mean RMS r at the selected
    K, never by test results), bare; one per plant present."""
    sel = json.loads(Path(sel_path).read_text())
    for pl, title in (('lin2', 'Simple yaw model'), ('jsbsim', 'JSBSim Cessna 172')):
        S = sel[pl]
        sh = sorted((S[s]['selected_mean_rms_r_train'], s) for s in SHUFS if S.get(s, {}).get('selected_K') is not None)
        if not sh or not _rec(shard_dir, pl, 'fly_real'):
            print(f'{pl}: recordings or selection missing; scenario skipped')
            continue
        best, med = sh[0][1], sh[(len(sh) - 1) // 2][1]
        K = lambda c: S[c]['selected_K']
        spec = [('fly_real', 'Fly brain (real wiring)'),
                ('yaw_damper', 'Classical yaw damper'),
                (best, 'Fly brain, scrambled wiring (lowest training yaw rate)'),
                (med, 'Fly brain, scrambled wiring (typical)'),
                ('bare', 'No controller')]
        runs = []
        for c, label in spec:
            r = _rec(shard_dir, pl, c)
            assert r is not None and r['meta'].get('mock') is False, (pl, c)
            make_mock_runs.validate(r)
            fn = f'{pl}_{c}_s{REC_SEED}.json'
            Path(runs_dir, fn).write_text(json.dumps(r, separators=(',', ':')))
            if c != 'bare' and K(c) == 0:
                label += ' (gain 0: flies like no controller)'
            runs.append(dict(file=fn, label=label))
        app_set_scenario(dict(id=f'phase2_{pl}_s{REC_SEED}', label=f'{title}, same gust', runs=runs),
                         runs_dir)
        print(pl, 'scenario written:', [r['file'] for r in runs])


# ---------------------------------------------------------------- self-check (synthetic, scratch dir only)
def selfcheck(d=os.path.join(tempfile.gettempdir(), 'fttf_phase2_synth')):
    import run_phase2 as r2
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(f'{d}/shards')
    rng = np.random.default_rng(1)
    noise = 0.002 * rng.random(10)
    ok_m = dict(gm_db=20.0, pm_deg=80.0, f_gm_hz=4.0, f_pm_hz=None, sat_frac_max=0.0)

    def tr(mean, dep=()):
        return [dict(seed=s, rms_r=mean + noise[s] - noise.mean(), rms_beta=0.01, rms_rudder=0.1, max_abs_phi=0.1,
                     departed=s in dep, sat_frac=0.0) for s in r2.TRAIN]

    for pl in ('lin2', 'jsbsim'):
        rows = [dict(K=K, K_index=i, train=tr(0.05 - 0.003 * i),
                     margins=None if i == 0 or pl == 'jsbsim' else dict(ok_m, gm_db=20.0 if i <= 3 else 3.0))
                for i, K in enumerate(r2.K_DAMPER)]
        json.dump(dict(meta=dict(plant=pl), controller='yaw_damper', rows=rows), open(f'{d}/shards/t_{pl}_damper.json', 'w'))
        for w, g in [(w, g) for w in r2.WIRINGS for g in ((1.0, 4.0) if pl == 'lin2' else (1.0,))]:
            c = cname(w, 1, g)
            for i in range(1, len(r2.K_FLY)):
                mean = 0.05 + (0.003 * i if w == 'shuf01' else -0.004 * i)   # shuf01: feedback only hurts
                if g == 4: mean = 0.05 - 0.001 * i                            # g4 arm: K index 6 by margins as well
                m = dict(ok_m)
                if i == 7: m['gm_db'] = 5.0                                   # GM < 6 dB -> excluded
                if i >= 8: m['pm_deg'] = 30.0                                 # PM < 45 deg -> excluded (incl. widened K)
                if w == 'shuf00': m['gm_db'] = 2.0                            # nothing admissible -> K = 0
                dep = (3,) if (pl == 'jsbsim' and i == 6) else ()             # 2b departure at K index 6
                if pl == 'jsbsim' and w == 'shuf09' and i == 5:
                    continue                                                  # a missing shard
                json.dump(dict(meta=dict(plant=pl), controller=c, rows=[dict(K=r2.K_FLY[i], K_index=i, train=tr(mean, dep),
                          margins=m if pl == 'lin2' else None)]), open(f'{d}/shards/t_{pl}_{c}_{i}.json', 'w'))
    # select reads files named phase2_tune_*.json
    for p in glob.glob(f'{d}/shards/t_*.json'):
        os.rename(p, p.replace('/t_', '/phase2_tune_'))
    S = select(f'{d}/shards', f'{d}/sel.json', phase1=f'{d}/no_phase1.json')
    L, J = S['lin2'], S['jsbsim']
    assert L['fly_real']['K_index_selected'] == 6 and J['fly_real']['K_index_selected'] == 5, (L['fly_real'], J['fly_real'])
    assert L['fly_real']['unconstrained']['K_index'] == len(r2.K_FLY) - 1 and L['fly_real']['unconstrained']['edge'] == 'high'
    assert L['fly_shuffle_00']['selected_K'] == 0 and J['fly_shuffle_00']['selected_K'] == 0          # K = 0 fallback
    # K = 0 best is NOT a grid edge (K = 0 is the bare reference): only the upper edge can call for widening
    assert L['fly_shuffle_01']['selected_K'] == 0 and L['fly_shuffle_01']['unconstrained']['edge'] is None
    assert L['yaw_damper']['K_index_selected'] == 3 and J['yaw_damper']['K_index_selected'] == 3
    assert J['fly_shuffle_09']['K_index_selected'] == 4 and [m['K_index'] for m in J['fly_shuffle_09']['missing']] == [5]
    assert S['widen_grid_for_all'] and 0.0 in L['fly_real']['admissible_K'] and r2.K_FLY[7] not in L['fly_real']['admissible_K']
    print('select rule OK: real lin2 K idx 6, jsbsim idx 5 (departure), shuf00 K=0 (margins), missing shard excluded')

    # test shards with known answers
    bare = 0.05 + 0.01 * rng.random(20)
    v = rng.standard_normal(20)
    off = {cname(w, 1): -0.001 * k for k, w in enumerate(WIRINGS[1:])}
    off['fly_shuffle_09'] = -0.02                                         # one shuffle beats real -> rank 2, p = 2/11
    series = dict(bare=bare, yaw_damper=bare - 0.02 + 0.001 * v, fly_real=bare - 0.01 + 0.002 * v,
                  **{c: bare + o for c, o in off.items()}, fly_real_g4=bare - 0.03 + 0.002 * v,
                  **{c + '_g4': bare - 0.001 for c in off})
    for c, y in series.items():
        json.dump(dict(meta=dict(controller=c, K=0.0 if c == 'bare' else 1e-3, sign=1, equals_bare=False),
                       flights=[dict(seed=s, rms_r=float(x), rms_beta=0.01, rms_rudder=0.1, max_abs_phi=0.1,
                                     departed=False, sat_frac=0.0) for s, x in zip(TEST[::-1], y[::-1])]),
                  open(f'{d}/shards/phase2_test_lin2_{c}.json', 'w'))
        json.dump(dict(t=[0.01, 0.02, 0.03], r=[0.0, float(y[0]), 0.0]), open(f'{d}/shards/rec_lin2_{c}_s100.json', 'w'))
    R = analyze(f'{d}/shards', f'{d}/sel.json', d, plants=('lin2',))['lin2']
    h = R['H2']
    assert np.isclose(h['mean'], np.mean(-0.01 + 0.002 * v)) and h['supported'] and h['BCa']['hi'] < 0
    assert np.allclose(h['per_seed'], -0.01 + 0.002 * v)
    b = R['H2b']
    assert np.allclose(b['per_seed_diff_vs_median'], -0.01 + 0.002 * v + 0.0045), b['per_seed_diff_vs_median'][:3]
    assert b['rank_of_real'] == 2 and np.isclose(b['p'], 2 / 11) and np.isclose(b['p_floor'], 1 / 11) and b['n_null'] == 10
    assert np.isclose(R['yaw_damper_minus_fly_real']['mean'], np.mean(-0.01 - 0.001 * v))
    assert R['secondary_sign']['status'].startswith('not run')
    assert L['fly_real_g4']['exploratory'] and L['fly_real_g4']['K_index_selected'] == 6 and 'fly_real_g4' not in J
    x = R['exploratory_g4']
    assert np.isclose(x['fly_real_g4_minus_bare']['mean'], np.mean(-0.03 + 0.002 * v)) and x['fly_real_g4_vs_median_shuffle_g4']['rank_of_real'] == 1
    assert np.isclose(x['fly_real_g4_vs_median_shuffle_g4']['p'], 1 / 11) and np.isclose(x['fly_real_g4_minus_fly_real_g1']['mean'], -0.02)
    assert 'fly_real_g4' not in [c for c in R['H2b']['mean_rms_by_wiring']]        # never mixed into the primary
    # degenerate case: fly == bare on every seed (K = 0 selected)
    dg = ci(np.zeros(20))
    assert dg['percentile']['degenerate'] and not dg['BCa']['excludes_0']
    assert Path(d, 'fig_phase2a.png').stat().st_size > 10000
    print('H2/H2b OK: mean', round(h['mean'], 5), 'CI', round(h['percentile']['lo'], 5), round(h['percentile']['hi'], 5),
          '| H2b rank', b['rank_of_real'], 'p', round(b['p'], 3))

    # app hook on a scratch copy of the manifest (never app/runs)
    rd = Path(d, 'runs')
    rd.mkdir()
    shutil.copy(RUNS / 'manifest.json', rd / 'manifest.json')
    for f in {r['file'] for s in json.loads((rd / 'manifest.json').read_text())['scenarios'] for r in s['runs']}:
        (rd / f).write_text('{}')
    (rd / 'a.json').write_text('{}')
    app_set_scenario(dict(id='x', label='X', runs=[dict(file='a.json', label='A')]), rd)
    app_set_scenario(dict(id='x', label='X2', runs=[dict(file='a.json', label='A')]), rd)
    man = json.loads((rd / 'manifest.json').read_text())
    labels = [s['label'] for s in man['scenarios']]
    assert labels.count('X2') == 1 and 'X' not in labels and labels[-1] == 'X2'          # same id replaced, not duplicated
    assert not any(all(r['file'].startswith('mock_') for r in s['runs']) for s in man['scenarios'])  # mock-only dropped
    assert 'window.RUN_BUNDLE=' in (rd / 'bundle.js').read_text()
    print('app hook OK (mock scenarios dropped, same-id scenario replaced, bundle rebuilt)')
    print('selfcheck OK ->', d)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--shards-dir', default=str(SHARDS))
    ap.add_argument('--app', action='store_true')
    ap.add_argument('--selfcheck', action='store_true')
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck()
    elif a.app:
        app_update(a.shards_dir)
    else:
        analyze(a.shards_dir)
