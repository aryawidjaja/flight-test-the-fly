"""Phase 3 (H4) analysis: merges results/shards/**/phase3_*.json from scripts/run_phase3.py.

  uv run python scripts/analyze_phase3.py [--shards results/shards]   # -> results/phase3_lesions.json,
                                                                       #    results/fig_phase3_departure.png
  uv run python scripts/analyze_phase3.py --app     # also copy the lesion recordings into app/runs/, add the
                                                    # scenario to app/runs/manifest.json, rebuild bundle.js
  uv run python scripts/analyze_phase3.py --selfcheck   # synthetic shards in the system temp dir

H4 (pre-registered): AUC of P(no departure) vs random-lesion fraction (normalized
trapezoid, stats.departure_auc), real minus the mean of the 3 shuffles, 95% percentile bootstrap over the 20 test
seeds with the seeds paired across wirings. Supported only if the CI lies entirely above 0.
Secondary: mean RMS r vs fraction (per-fraction and area differences, real minus shuffle mean, seed bootstrap).
Reported too: targeted lesions (all_HS, T4_only, T5_only, hubs05/10/20).
"""
import argparse, glob, json, os, shutil, subprocess, sys, tempfile
from datetime import datetime, timezone

import numpy as np
from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(__file__))
import stats  # noqa: E402
from run_phase3 import CONDS, REC_CONDS, REC_SEED, TEST_SEEDS, WIRINGS  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
RAND = [c for c in CONDS if CONDS[c][0] == 'random']
FRACS = [CONDS[c][1] for c in RAND]
TARGETED = [c for c in CONDS if c not in RAND]
SHUFS = WIRINGS[1:]
N_BOOT = 10000
COLORS = dict(real='#2a78d6', shuf00='#eb6834', shuf01='#1baf7a', shuf02='#eda100')  # dataviz slots 1-4, fixed
INK, INK2, SURF = '#0b0b0b', '#52514e', '#fcfcfb'


def load(shard_dir):
    """{(gain_set, wiring, cond): {'meta': ..., 'flights': {seed: flight}}}"""
    T = {}
    pat = lambda g: glob.glob(os.path.join(shard_dir, '**', f'phase3_{g}_*.json'), recursive=True)
    for p in sorted(pat('selected') + pat('explore')):
        d = json.load(open(p))
        m = d['meta']
        e = T.setdefault((m['gain_set'], m['wiring'], m['condition']), dict(meta=m, flights={}))
        for f in d['flights']:
            old = e['flights'].get(f['seed'])
            assert old is None or old['rms_r'] == f['rms_r'], f'conflicting duplicate flight {p} seed {f["seed"]}'
            e['flights'][f['seed']] = f
    return T


def wilson(k, n):
    ci = binomtest(int(k), int(n)).proportion_ci(0.95, 'wilson')
    return [float(ci.low), float(ci.high)]


def seed_boot(stat, n, n_boot=N_BOOT, seed=0, alpha=0.05):
    """Percentile bootstrap over seeds; same resampling loop as stats.departure_auc_ci (asserted in selfcheck)."""
    rng = np.random.default_rng(seed)
    b = np.array([stat(rng.integers(0, n, n)) for _ in range(n_boot)])
    lo, hi = np.quantile(b, [alpha / 2, 1 - alpha / 2])
    return dict(value=float(stat(np.arange(n))), lo=float(lo), hi=float(hi), n_seeds=n)


def h4_stat(dep):
    """dep: {wiring: bool (n_seeds, n_fracs)}. AUC(real) - mean AUC(shuffles), seed-paired bootstrap."""
    auc = lambda d, i: stats.departure_auc(FRACS, 1 - d[i].mean(0))
    return seed_boot(lambda i: auc(dep['real'], i) - np.mean([auc(dep[s], i) for s in SHUFS]), len(dep['real']))


def h4_verdict(dep, aucs, ci, real_is_bare):
    if real_is_bare:
        return 'not testable on this gain set: the real wiring\'s selected K is 0, so fly_real is the bare airframe'
    if all(a == 1.0 for a in aucs.values()) and not any(d.any() for d in dep.values()):
        return ('untestable / not supported: no flight departed at any random-lesion fraction in any wiring '
                '(every AUC = 1), so the departure curve cannot separate the wirings')
    return 'supported' if ci['lo'] > 0 else 'not supported (the 95% CI does not lie entirely above 0)'


def _arr(T, gs, w, conds, key, seeds):
    return np.array([[T[(gs, w, c)]['flights'][s][key] for c in conds] for s in seeds], float)


def analyze_set(T, gs):
    keys = [(gs, w, c) for w in WIRINGS for c in CONDS]
    missing = [k for k in keys if k not in T]
    assert not missing, f'missing jobs: {missing[:5]} (+{len(missing) - 5})' if len(missing) > 5 else missing
    seeds = sorted(set.intersection(*[set(T[k]['flights']) for k in keys]))
    out = dict(gain_set=gs, exploratory=gs == 'explore', n_seeds=len(seeds), seeds=seeds,
               incomplete=seeds != TEST_SEEDS, K={w: T[(gs, w, 'random00')]['meta']['K'] for w in WIRINGS})
    out['k_zero_equals_bare'] = {w: out['K'][w] == 0 for w in WIRINGS}
    # pairing check: every brain-flown wiring got the same random lesion indices on each seed
    for c in RAND:
        for s in seeds:
            h = {T[(gs, w, c)]['flights'][s]['lesion_sha1'] for w in WIRINGS if out['K'][w] > 0}
            assert len(h) <= 1, f'random lesion not paired across wirings: {gs} {c} seed {s}'
    cells = {}
    for w in WIRINGS:
        for c in CONDS:
            F = [T[(gs, w, c)]['flights'][s] for s in seeds]
            k, r = sum(f['departed'] for f in F), np.array([f['rms_r'] for f in F])
            ci = stats.bootstrap_ci(r, seed=0)
            cells[f'{w}/{c}'] = dict(wiring=w, condition=c, n=len(F), n_departed=int(k), p_departure=k / len(F),
                                     p_departure_wilson95=wilson(k, len(F)), mean_rms_r=ci['mean'],
                                     mean_rms_r_ci95=[ci['lo'], ci['hi']],
                                     mean_rms_beta=float(np.mean([f['rms_beta'] for f in F])),
                                     n_lesioned=F[0]['n_lesioned'], n_edges_used=F[0]['n_edges_used'],
                                     n_input_saturated=int(sum(f['input_saturated'] for f in F)))
    out['cells'] = cells
    dep = {w: _arr(T, gs, w, RAND, 'departed', seeds).astype(bool) for w in WIRINGS}
    aucs = {w: stats.departure_auc(FRACS, 1 - dep[w].mean(0)) for w in WIRINGS}
    ci = h4_stat(dep)
    out['H4'] = dict(statistic='AUC of P(no departure) vs random-lesion fraction, real minus mean of shuf00-02',
                     fractions=FRACS, auc=aucs, diff=ci['value'], ci95=[ci['lo'], ci['hi']], n_boot=N_BOOT,
                     per_shuffle={s: stats.departure_auc_ci(FRACS, dep['real'], dep[s], n_boot=N_BOOT) for s in SHUFS},
                     any_departure=bool(any(d.any() for d in dep.values())),
                     verdict=h4_verdict(dep, aucs, ci, out['K']['real'] == 0))
    rms = {w: _arr(T, gs, w, RAND, 'rms_r', seeds) for w in WIRINGS}
    d = rms['real'] - np.mean([rms[s] for s in SHUFS], 0)      # (seeds, fractions), paired by seed
    area = lambda x: np.trapezoid(x, FRACS, axis=1) / (FRACS[-1] - FRACS[0])
    out['secondary_rms_r'] = dict(
        note='mean RMS r (rad/s) vs random-lesion fraction; diff = real minus mean of the 3 shuffles, per seed',
        fractions=FRACS, mean={w: rms[w].mean(0).tolist() for w in WIRINGS},
        diff_per_fraction=[stats.bootstrap_ci(d[:, j], seed=0) for j in range(len(FRACS))],
        diff_area=stats.bootstrap_ci(area(d), seed=0),
        increase_0_to_80={w: stats.bootstrap_ci(rms[w][:, -1] - rms[w][:, 0], seed=0) for w in WIRINGS})
    tg = {}
    for c in TARGETED:
        r = {w: _arr(T, gs, w, [c], 'rms_r', seeds)[:, 0] for w in WIRINGS}
        tg[c] = dict(diff_rms_r_real_minus_shuffle_mean=stats.bootstrap_ci(r['real'] - np.mean([r[s] for s in SHUFS], 0), seed=0),
                     n_departed={w: cells[f'{w}/{c}']['n_departed'] for w in WIRINGS},
                     note='hubs are by betweenness on each wiring itself' if c.startswith('hubs') else '')
    out['targeted'] = tg
    return out


def ref_rms(path):
    """bare / yaw_damper test-seed mean RMS r from Phase 2b, if the file exists (tolerant of its layout)."""
    if not os.path.exists(path):
        return {}
    d = json.load(open(path))
    d = next((d[k] for k in ('jsbsim', '2b', 'phase2b') if isinstance(d.get(k), dict)), d)

    def find(x, name):
        if isinstance(x, dict):
            if name in x and isinstance(x[name], dict):
                e = x[name].get('test', x[name])
                for k in ('mean_rms_r', 'rms_r'):
                    v = e.get(k) if isinstance(e, dict) else None
                    if v is not None:
                        return float(np.mean(v))
            for v in x.values():
                r = find(v, name)
                if r is not None:
                    return r
        return None
    return {n: v for n in ('bare', 'yaw_damper') if (v := find(d, n)) is not None}


def figure(res, refs, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    sets = res['gain_sets']
    plt.rcParams.update({'font.size': 9, 'axes.edgecolor': INK2, 'axes.labelcolor': INK, 'xtick.color': INK2,
                         'ytick.color': INK2, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, axs = plt.subplots(len(sets), 3, figsize=(13, 3.9 * len(sets)), squeeze=False, facecolor=SURF,
                            gridspec_kw=dict(width_ratios=[1, 1, 1.3]))
    x = 100 * np.array(FRACS)
    for row, R in zip(axs, sets):
        tag = ' (EXPLORATORY K)' if R['exploratory'] else ''
        for ax in row:
            ax.set_facecolor(SURF); ax.grid(axis='y', color='#e4e3df', lw=0.6); ax.set_axisbelow(True)
        for j, w in enumerate(WIRINGS):
            c = [R['cells'][f'{w}/{k}'] for k in RAND]
            p = 1 - np.array([q['p_departure'] for q in c])
            lo = 1 - np.array([q['p_departure_wilson95'][1] for q in c]); hi = 1 - np.array([q['p_departure_wilson95'][0] for q in c])
            off = (j - 1.5) * 0.6   # small x offset so overlapping CIs stay readable
            lab = f'{w} (K={R["K"][w]:.3g}{", = bare" if R["K"][w] == 0 else ""})'
            row[0].errorbar(x + off, p, [p - lo, hi - p], color=COLORS[w], lw=2 if w == 'real' else 1.4, marker='o',
                            ms=4, capsize=2, label=lab, zorder=3 if w == 'real' else 2)
            m = np.array([q['mean_rms_r'] for q in c]); ci = np.array([q['mean_rms_r_ci95'] for q in c])
            row[1].errorbar(x + off, np.degrees(m), [np.degrees(m - ci[:, 0]), np.degrees(ci[:, 1] - m)], color=COLORS[w],
                            lw=2 if w == 'real' else 1.4, marker='o', ms=4, capsize=2, label=w, zorder=3 if w == 'real' else 2)
            t = [R['cells'][f'{w}/{k}'] for k in TARGETED]
            xb = np.arange(len(TARGETED)) + (j - 1.5) * 0.2
            mb = np.degrees([q['mean_rms_r'] for q in t]); cb = np.degrees([q['mean_rms_r_ci95'] for q in t])
            row[2].bar(xb, mb, 0.18, color=COLORS[w], label=w, edgecolor=SURF, lw=1)
            row[2].errorbar(xb, mb, [mb - cb[:, 0], cb[:, 1] - mb], fmt='none', ecolor=INK2, lw=0.8, capsize=1.5)
            for xi, q, y in zip(xb, t, cb[:, 1]):
                if q['n_departed']:
                    row[2].text(xi, y, f"{q['n_departed']}/{q['n']}\ndep", ha='center', va='bottom', fontsize=6, color=INK)
        h = R['H4']
        row[0].set(xlabel='random lesion (% of subcircuit neurons)', ylabel='P(no departure), 95% Wilson CI', ylim=(-0.03, 1.05),
                   title=f'H4: ΔAUC real − shuffle mean = {h["diff"]:+.3f} [{h["ci95"][0]:+.3f}, {h["ci95"][1]:+.3f}]{tag}')
        row[0].title.set_fontsize(8.5)
        row[0].legend(fontsize=7, frameon=False, loc='lower left')
        for name, ls in (('bare', '--'), ('yaw_damper', ':')):
            if name in refs:
                for ax in row[1:]:
                    ax.axhline(np.degrees(refs[name]), color=INK2, ls=ls, lw=1)
                row[1].text(x[-1], np.degrees(refs[name]), f' {name} (2b)', color=INK2, fontsize=7, va='bottom', ha='right')
        row[1].set(xlabel='random lesion (% of subcircuit neurons)', ylabel='mean RMS yaw rate (deg/s), 95% CI',
                   title=f'Secondary: RMS r vs lesion{tag}')
        row[2].set_xticks(range(len(TARGETED)), [c.replace('_', ' ') for c in TARGETED], fontsize=7)
        row[2].set(ylabel='mean RMS yaw rate (deg/s), 95% CI', title=f'Targeted lesions{tag}')
        row[2].legend(fontsize=7, frameon=False, ncol=4, loc='upper left')
    fig.suptitle('Phase 3: lesion to departure, JSBSim c172x, Dryden moderate, test seeds 100-119 (FlyWire v783)',
                 color=INK, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURF)
    plt.close(fig)


def analyze(shard_dir, out_json, out_fig, ref_path):
    T = load(shard_dir)
    assert T, f'no phase3_*.json under {shard_dir}'
    gs_present = [g for g in ('selected', 'explore') if any(k[0] == g for k in T)]
    res = dict(meta=dict(phase=3, script='scripts/analyze_phase3.py', shard_dir=shard_dir, n_files_cells=len(T),
                         data_version='flywire_v783', plant='jsbsim', turbulence='dryden_moderate',
                         departure='|phi| > 60 deg, |beta| > 20 deg or |r| > 60 deg/s, held > 1 s (plant.simulate)',
                         git_hash=next(iter(T.values()))['meta'].get('git_hash'),
                         created=datetime.now(timezone.utc).isoformat()),
               gain_sets=[analyze_set(T, g) for g in gs_present])
    refs = ref_rms(ref_path)
    res['reference_rms_r_phase2b'] = refs
    primary = res['gain_sets'][0]
    res['H4_summary'] = dict(primary_gain_set=primary['gain_set'], verdict=primary['H4']['verdict'],
                             diff=primary['H4']['diff'], ci95=primary['H4']['ci95'],
                             exploratory=next(({'verdict': g['H4']['verdict'], 'diff': g['H4']['diff'], 'ci95': g['H4']['ci95']}
                                               for g in res['gain_sets'] if g['exploratory']), None))
    with open(out_json, 'w') as fh:
        json.dump(res, fh, indent=1, default=float)
    figure(res, refs, out_fig)
    return res


def app(shard_dir):
    runs = os.path.join(ROOT, 'app', 'runs')
    labels = dict(random00='0% silenced (intact)', random40='40% neurons silenced (random)', random80='80% neurons silenced (random)', all_HS='HS turn sensors silenced')
    entries = []
    for c in REC_CONDS:
        fn = f'rec_lesion_{c}_s{REC_SEED}.json'
        src = sorted(glob.glob(os.path.join(shard_dir, '**', fn), recursive=True))
        assert src, f'missing recording {fn}'
        shutil.copy(src[0], os.path.join(runs, fn))
        ex = json.load(open(src[0]))['meta'].get('exploratory')
        entries.append(dict(file=fn, label=labels[c] + (' (exploratory K)' if ex else '')))
    mp = os.path.join(runs, 'manifest.json')
    man = json.load(open(mp))
    sid = 'lesions_real_s100'
    man['scenarios'] = [s for s in man['scenarios'] if s['id'] != sid] + [
        dict(id=sid, label='Neuron loss: real wiring, same gust (JSBSim)', runs=entries)]
    with open(mp, 'w') as fh:
        json.dump(man, fh, indent=1)
    subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'make_mock_runs.py'), '--bundle-only'], check=True)


def selfcheck(d=os.path.join(tempfile.gettempdir(), 'fttf_phase3_synth')):
    import run_phase3 as r3
    r3._selfcheck_lesions()          # nested random sets + same indices for every wiring (real subcircuit, no Brian2)
    shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    rng = np.random.default_rng(0)
    # known data: real departs only at 80% on seeds 100-109; each shuffle departs at 60% and 80% on every seed
    for w in WIRINGS:
        for c, (kind, f) in CONDS.items():
            for i in range(2):
                fl = []
                for s in TEST_SEEDS[i * 10:(i + 1) * 10]:
                    les = r3.lesion_idx(w, c, s) if kind != 'hubs_topk' else np.arange(round(f * 20556))
                    dep = (f >= 0.8 and s < 110) if w == 'real' else f >= 0.6
                    fl.append(dict(seed=s, rms_r=0.05 + 0.1 * f * (w != 'real') + 0.01 * rng.random(), rms_beta=0.01,
                                   max_abs_phi=0.1, departed=bool(dep), rms_rudder=0.1, input_saturated=False,
                                   controller_is_bare=False, n_lesioned=len(les), lesion_sha1=r3._sha(les), n_edges_used=1))
                json.dump(dict(meta=dict(gain_set='selected', wiring=w, condition=c, K=0.01, git_hash='synthetic'), flights=fl),
                          open(f'{d}/phase3_selected_{w}_{c}_c{i}.json', 'w'))
    res = analyze(d, f'{d}/phase3_lesions.json', f'{d}/fig_phase3_departure.png', f'{d}/none.json')
    h = res['gain_sets'][0]['H4']
    assert abs(h['auc']['real'] - 0.9375) < 1e-12 and abs(h['auc']['shuf00'] - 0.625) < 1e-12, h['auc']
    assert abs(h['diff'] - 0.3125) < 1e-12 and 0 < h['ci95'][0] <= 0.3125 <= h['ci95'][1], h
    assert h['verdict'] == 'supported' and res['gain_sets'][0]['n_seeds'] == 20
    # the paired bootstrap equals stats.departure_auc_ci when there is one reference wiring
    dep_r = np.zeros((20, 7), bool); dep_r[:10, -1] = True
    dep_s = np.zeros((20, 7), bool); dep_s[:, -2:] = True; dep_s[::3, -3] = True
    ref = stats.departure_auc_ci(FRACS, dep_r, dep_s, n_boot=N_BOOT)
    mine = seed_boot(lambda i: stats.departure_auc(FRACS, 1 - dep_r[i].mean(0)) - stats.departure_auc(FRACS, 1 - dep_s[i].mean(0)), 20)
    assert (mine['value'], mine['lo'], mine['hi']) == (ref['auc'], ref['lo'], ref['hi']), (mine, ref)
    # never departs -> untestable; the verdict says so plainly
    z = {w: np.zeros((20, 7), bool) for w in WIRINGS}
    za = {w: 1.0 for w in WIRINGS}
    assert h4_verdict(z, za, h4_stat(z), False).startswith('untestable') and h4_stat(z)['value'] == 0
    assert h4_verdict(z, za, h4_stat(z), True).startswith('not testable')
    # Wilson CI sanity
    lo, hi = wilson(0, 20)
    assert lo == 0 and 0.15 < hi < 0.17, hi
    # a broken pairing must be caught
    p = f'{d}/phase3_selected_shuf01_random40_c0.json'
    j = json.load(open(p)); j['flights'][0]['lesion_sha1'] = 'x'; json.dump(j, open(p, 'w'))
    try:
        analyze(d, f'{d}/x.json', f'{d}/x.png', f'{d}/none.json'); raise AssertionError('pairing break not caught')
    except AssertionError as e:
        assert 'not paired' in str(e), e
    print('analyze_phase3 selfcheck OK ->', d)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--shards', default=os.path.join(ROOT, 'results', 'shards'))
    ap.add_argument('--app', action='store_true')
    ap.add_argument('--selfcheck', action='store_true')
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck()
    else:
        res = analyze(a.shards, os.path.join(ROOT, 'results', 'phase3_lesions.json'),
                      os.path.join(ROOT, 'results', 'fig_phase3_departure.png'),
                      os.path.join(ROOT, 'results', 'phase2b_metrics.json'))
        print(json.dumps(res['H4_summary'], indent=1))
        if a.app:
            app(a.shards)
