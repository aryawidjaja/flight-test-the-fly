"""Phase 1 (H1, gate G1) analysis: reads results/shards/phase1_*.json (made by run_phase1.py) and writes
results/phase1_bode.json, results/fig_phase1_bode.png, results/fig_phase1_amp.png, results/phase1_summary.md.
Pre-registered rules: H1 and gate G1; coherence with nperseg = one drive cycle; 19 shuffles and the 1 Hz test
frequency; sign criterion cos(phase) < 0 at f <= 1 Hz; identified sign; exploratory 1 Hz amplitude sweep.

  uv run python scripts/analyze_phase1.py                    # real shards -> results/
  uv run python scripts/analyze_phase1.py --synthetic DIR    # fake shards in DIR/shards, outputs in DIR (never results/)
  uv run python scripts/analyze_phase1.py --selfcheck        # synthetic run + asserts (scratch dir)

Conventions: H = complex lock-in transfer of Delta = DNa02_R - DNa02_L (Hz) re the yaw rate r (deg/s), so gain is
Hz per deg/s and phase is deg of Delta re r. "Seed-averaged H" = mean of H_re and H_im over Poisson seeds.
"""
import argparse, glob, json, os, sys, tempfile
from datetime import datetime, timezone

import numpy as np
from scipy.stats import circstd

sys.path.insert(0, os.path.dirname(__file__))
import stats  # noqa: E402
from run_phase1 import AMP, FREQS, GROUPS, SEEDS, TWIRINGS, WIRINGS, n_cycles, _git  # noqa: E402  (constants only; no Brian2)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F_H1 = 1.0
G_SET, AMP_SET = (0.5, 1.0, 2.0, 4.0), (3.0, 10.0, 30.0, 100.0)
SCRATCH = os.path.join(tempfile.gettempdir(), 'fttf_phase1_synth')


# ---------------------------------------------------------------- loading
def load(shard_dir):
    """{(kind, wiring, seed, param): rows}, plus the file list."""
    files = sorted(glob.glob(os.path.join(shard_dir, 'phase1_*.json')))
    D = {}
    synthetic = False
    for p in files:
        d = json.load(open(p))
        m = d['meta']
        D[(m['kind'], m['wiring'], int(m['seed']), float(m['param']))] = d['rows']
        synthetic |= bool(m.get('synthetic'))
    return D, files, synthetic


def arrays(rows_by_seed):
    """rows_by_seed: list (per seed) of row lists -> dict of (n_seed, n_f) arrays, sorted by f."""
    out = {}
    for k in ('f', 'gain', 'phase_deg', 'H_re', 'H_im', 'coh', 'n_cycles'):
        out[k] = np.array([[r[k] for r in sorted(rows, key=lambda r: r['f'])] for rows in rows_by_seed], float)
    assert np.allclose(out['f'], out['f'][0]), 'seeds disagree on frequencies'
    out['rows'] = [sorted(rows, key=lambda r: r['f']) for rows in rows_by_seed]
    return out


def summarize(a):
    """Seed aggregation per frequency (pre-registered: complex mean H for gain/phase, mean coherence)."""
    H = a['H_re'].mean(0) + 1j * a['H_im'].mean(0)
    ph_seeds = a['phase_deg']
    return dict(f=a['f'][0].tolist(), n_seeds=len(a['f']),
                coh=a['coh'].mean(0).tolist(), coh_std=a['coh'].std(0, ddof=1).tolist(),
                gain=np.abs(H).tolist(), phase_deg=np.degrees(np.angle(H)).tolist(),
                H_re=H.real.tolist(), H_im=H.imag.tolist(),
                gain_seedmean=a['gain'].mean(0).tolist(), gain_std=a['gain'].std(0, ddof=1).tolist(),
                phase_circstd_deg=np.degrees(circstd(np.radians(ph_seeds), axis=0)).tolist(),
                per_seed=dict(gain=a['gain'].tolist(), phase_deg=ph_seeds.tolist(), coh=a['coh'].tolist(),
                              H_re=a['H_re'].tolist(), H_im=a['H_im'].tolist()))


def identified_sign(s):
    """Pre-registered identified sign: +1 if the seed-averaged Re(H) over the frequencies <= 0.5 Hz is < 0, else -1."""
    f, re_ = np.array(s['f']), np.array(s['H_re'])
    return 1 if re_[f <= 0.5].mean() < 0 else -1


def delay_fit(s):
    """EXPLORATORY: phase(f) = phi0 - 360 f tau on the unwrapped seed-mean phase, f >= 2 Hz and coherence > 0.5.
    Unwrapping assumes |phase step| < 180 deg between adjacent used frequencies."""
    f, ph, c = np.array(s['f']), np.array(s['phase_deg']), np.array(s['coh'])
    m = (f >= 2.0) & (c > 0.5)
    out = dict(label='exploratory', n_points=int(m.sum()), f_used=f[m].tolist(), tau_ms=None, phi0_deg=None)
    if m.sum() >= 2:
        y = np.degrees(np.unwrap(np.radians(ph[m])))
        slope, phi0 = np.polyfit(f[m], y, 1)
        out.update(tau_ms=float(-slope / 360 * 1e3), phi0_deg=float(phi0),
                   rms_resid_deg=float(np.sqrt(np.mean((y - (phi0 + slope * f[m])) ** 2))))
    return out


def dn_rates(a):
    g = lambda name, k: np.array([[r['groups'][name][k] for r in rows] for rows in a['rows']])
    return {f'{n}_{w}_hz': float(g(n, k).mean()) for n in ('DNa02_L', 'DNa02_R')
            for w, k in (('warm', 'warm_mean_hz'), ('driven', 'mean_hz'))} | \
           {f'{n}_driven_hz_per_f': g(n, 'mean_hz').mean(0).tolist() for n in ('DNa02_L', 'DNa02_R')}


def at_f(s, key, f0, tol=1e-9):
    i = [k for k, f in enumerate(s['f']) if abs(f - f0) < tol]
    assert len(i) == 1, f'frequency {f0} not found in {s["f"]}'
    return s[key][i[0]]


# ---------------------------------------------------------------- analysis
def h1_block(B):
    real, shufs = B['real'], [w for w in B if w.startswith('shuf')]
    f = np.array(real['f'])
    coh, Hre = np.array(real['coh']), np.array(real['H_re'])
    a_f = f[f <= 2.0]
    a = dict(rule='real seed-mean coherence > 0.5 at every f <= 2 Hz', f=a_f.tolist(),
             coh=coh[f <= 2.0].tolist(), per_f_pass=(coh[f <= 2.0] > 0.5).tolist())
    a['pass'] = bool(all(a['per_f_pass']))
    b_m = f <= 1.0 + 1e-9
    cosph = Hre[b_m] / np.array(real['gain'])[b_m]
    b = dict(rule='cos(phase of seed-averaged H) < 0 at every f <= 1 Hz', f=f[b_m].tolist(),
             phase_deg=np.array(real['phase_deg'])[b_m].tolist(), cos_phase=cosph.tolist(),
             per_f_pass=(cosph < 0).tolist())
    b['pass'] = bool(all(b['per_f_pass']))
    c = {}
    for key, stat in (('coherence', 'coh'), ('gain', 'gain_seedmean')):
        rv, nv = at_f(real, stat, F_H1), [at_f(B[w], stat, F_H1) for w in shufs]
        t = stats.perm_test_real_vs_null(rv, nv, 'greater')
        c[key] = dict(statistic=f'mean over seeds of per-seed {key} at {F_H1} Hz', real=rv,
                      null=dict(zip(shufs, nv)), **t, alpha=0.05, **{'pass': bool(t['p'] <= 0.05)})
    failed = [k for k, ok in (('a_coherence_gt_0.5_le_2Hz', a['pass']), ('b_sign_opposes_rotation', b['pass']),
                              ('c_perm_coherence_1Hz', c['coherence']['pass']),
                              ('c_perm_gain_1Hz', c['gain']['pass'])) if not ok]
    return dict(a=a, b=b, c=c, failed=failed, n_shuffles=len(shufs),
                verdict='supported' if not failed else 'not supported (failed: ' + ', '.join(failed) + ')')


def null_floor(freqs, ncyc, n_mc=500):
    out = []
    for f, nc in zip(freqs, ncyc):
        r = stats.null_coherence(int(nc), spc=int(round(200 / f)), n_mc=n_mc, seed=0)
        out.append(dict(f=f, n_cycles=int(nc), n_segments=r['n_segments'], mean=r['mean'], q95=r['q95']))
    return out


def analyse(shard_dir, out_dir, n_mc=500):
    D, files, synthetic = load(shard_dir)
    if not any(k[0] == 'bode' for k in D):
        sys.exit(f'No Phase 1 bode shards in {shard_dir}. Run run_phase1.py (GitHub Actions sweep) and download '
                 f'the artifacts there first.')
    B, missing = {}, []
    for w in WIRINGS:
        seeds = [s for s in SEEDS if ('bode', w, s, 1.0) in D]
        missing += [f'bode {w} s{s}' for s in SEEDS if s not in seeds]
        if seeds:
            a = arrays([D[('bode', w, s, 1.0)] for s in seeds])
            B[w] = summarize(a) | dict(seeds=seeds, identified_sign=identified_sign(summarize(a)),
                                       dna02_rates=dn_rates(a))
            B[w]['delay_fit'] = delay_fit(B[w])
    assert 'real' in B, 'no real-wiring bode shards'
    real = B['real']
    # secondary null: type-preserving shuffles (swaps only within the post's (side, cell type))
    T = {}
    for w in TWIRINGS:
        seeds = [s for s in SEEDS if ('bode', w, s, 1.0) in D]
        missing += [f'bode {w} s{s}' for s in SEEDS if s not in seeds]
        if seeds:
            a = arrays([D[('bode', w, s, 1.0)] for s in seeds])
            T[w] = summarize(a) | dict(seeds=seeds, identified_sign=identified_sign(summarize(a)), dna02_rates=dn_rates(a))
    h1_type = None
    if T:
        h1_type = dict(label='SECONDARY (not the pre-registered H1 null): type-preserving null', c={})
        for key, stat in (('coherence', 'coh'), ('gain', 'gain_seedmean')):
            rv, nv = at_f(real, stat, F_H1), [at_f(T[w], stat, F_H1) for w in T]
            t = stats.perm_test_real_vs_null(rv, nv, 'greater')
            h1_type['c'][key] = dict(real=rv, null=dict(zip(T, nv)), **t, alpha=0.05, **{'pass': bool(t['p'] <= 0.05)})

    gsens = {}
    for g in G_SET:
        if g == 1.0:
            gsens['1'] = {k: real[k] for k in ('f', 'gain', 'phase_deg', 'coh', 'n_seeds')}
            continue
        seeds = [s for s in SEEDS if ('gsens', 'real', s, g) in D]
        missing += [f'gsens g={g:g} s{s}' for s in SEEDS if s not in seeds]
        if seeds:
            sm = summarize(arrays([D[('gsens', 'real', s, g)] for s in seeds]))
            gsens[f'{g:g}'] = {k: sm[k] for k in ('f', 'gain', 'phase_deg', 'coh', 'n_seeds')}

    amp = dict(label='exploratory', f=F_H1, amplitudes_deg_s=list(AMP_SET),
               note='gain = seed mean of per-seed lock-in |H| (the H1 statistic); it is biased upward by noise '
                    'when coherence is low', wirings={})
    for w in B:
        row = {}
        for A in AMP_SET:
            if A == AMP:
                row['100'] = dict(gain=at_f(B[w], 'gain_seedmean', F_H1), coh=at_f(B[w], 'coh', F_H1),
                                  n_seeds=B[w]['n_seeds'])
                continue
            seeds = [s for s in SEEDS if ('amp', w, s, A) in D]
            missing += [f'amp {w} A={A:g} s{s}' for s in SEEDS if s not in seeds]
            if seeds:
                rr = [D[('amp', w, s, A)][0] for s in seeds]
                row[f'{A:g}'] = dict(gain=float(np.mean([r['gain'] for r in rr])),
                                     coh=float(np.mean([r['coh'] for r in rr])), n_seeds=len(seeds))
        amp['wirings'][w] = row
    shufs = [w for w in B if w != 'real']
    amp['shuffle_summary'] = {}  # NaN coherence = a silent DNa02 pair (0/0); counted, and excluded from the median
    for A in AMP_SET:
        k = f'{A:g}'
        for m in ('gain', 'coh'):
            v = np.array([amp['wirings'][w][k][m] for w in shufs if k in amp['wirings'][w]], float)
            if len(v) and k in amp['wirings']['real']:
                ok = v[np.isfinite(v)]
                amp['shuffle_summary'][f'{k}_{m}'] = dict(min=float(ok.min()), median=float(np.median(ok)), max=float(ok.max()),
                                                          n=int(len(v)), n_nan=int(len(v) - len(ok)),
                                                          real_rank=1 + int(np.sum(ok > amp['wirings']['real'][k][m])))

    coh_r = np.array(real['coh'])
    out = dict(
        meta=dict(phase=1, data_version='flywire_v783', created=datetime.now(timezone.utc).isoformat(),
                  git_hash=os.environ.get('GITHUB_SHA') or _git(), script='scripts/analyze_phase1.py',
                  shard_dir=os.path.relpath(os.path.abspath(shard_dir), ROOT),  # relative: no local paths in outputs
                  input_files=[os.path.basename(p) for p in files],
                  n_seeds_per_wiring={w: B[w]['n_seeds'] for w in B}, expected_wirings=WIRINGS,
                  expected_seeds=list(SEEDS), missing=missing, data_complete=not missing and len(B) == len(WIRINGS),
                  synthetic=synthetic,
                  conventions='H = lock-in transfer of Delta = DNa02_R - DNa02_L (Hz) re yaw rate r (deg/s); '
                              'gain Hz per deg/s; phase deg of Delta re r; seed-averaged H = mean(H_re) + j mean(H_im)'),
        freqs=real['f'],
        wirings=B,
        H1=h1_block(B),
        H1_secondary_type_null=h1_type,
        type_null_wirings=T,
        identified_sign_note='identified_sign is the pre-registered rule and is applied regardless of '
                             'coherence; a sign is only meaningful where 1 Hz coherence is well above the noise floor',
        G1=dict(rule='real seed-mean coherence < 0.3 at every frequency -> G1 fails, try the pre-listed alternatives',
                coh_below_0p3_everywhere=bool(np.all(coh_r < 0.3)), max_coh=float(coh_r.max()),
                f_at_max=real['f'][int(coh_r.argmax())]),
        coherence_null=dict(method='stats.null_coherence: sine vs white noise, nperseg = 1 cycle, cycles as run '
                                   '(run_phase1.n_cycles)', n_mc=n_mc,
                            per_f=null_floor(real['f'], [n_cycles(f) for f in real['f']], n_mc)),
        gsens=dict(wiring='real', note='g = 1 is the bode set', by_g=gsens),
        amp=amp,
    )
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'phase1_bode.json'), 'w') as fh:
        json.dump(out, fh, indent=1, default=float)
    fig_bode(out, os.path.join(out_dir, 'fig_phase1_bode.png'))
    fig_amp(out, os.path.join(out_dir, 'fig_phase1_amp.png'))
    write_summary(out, os.path.join(out_dir, 'phase1_summary.md'))
    return out


# ---------------------------------------------------------------- figures
BLUE, ORANGE, GREY, INK, INK2 = '#2a78d6', '#eb6834', '#8a8984', '#0b0b0b', '#52514e'
GREEN = '#2e9e6a'
TITLE = (r'$\it{Drosophila\ melanogaster}$ FlyWire v783 eye→DNa02 subcircuit (Shiu LIF model), '
         '\nstepped sines 100°/s, 5 seeds')


def _style():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 10, 'axes.edgecolor': INK2, 'axes.labelcolor': INK, 'xtick.color': INK2,
                         'ytick.color': INK2, 'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.grid': True, 'grid.color': '#e4e3df', 'grid.linewidth': 0.6, 'legend.frameon': False,
                         'figure.facecolor': 'white', 'axes.facecolor': 'white', 'lines.linewidth': 2})
    return plt


def _unwrap0(ph):
    """Unwrap a phase curve (deg) over frequency, first point in [0, 360)."""
    ph = np.degrees(np.unwrap(np.radians(np.asarray(ph, float))))
    return ph - 360 * np.floor(ph[0] / 360)


def fig_bode(out, path):
    plt = _style()
    W = out['wirings']
    real, shufs = W['real'], [W[w] for w in W if w != 'real']
    f = np.array(real['f'])
    fig, ax = plt.subplots(3, 1, figsize=(7.5, 10), sharex=True, constrained_layout=True)
    # shuffles: grey min-max band + median
    if shufs:
        G = np.array([s['gain'] for s in shufs])
        P = np.array([_unwrap0(s['phase_deg']) for s in shufs])
        C = np.array([s['coh'] for s in shufs])
        for a, M in ((ax[0], G), (ax[2], C)):
            a.fill_between(f, M.min(0), M.max(0), color=GREY, alpha=0.22, lw=0,
                           label=f'{len(shufs)} degree-preserving shuffles (min–max)')
            a.plot(f, np.median(M, 0), color=GREY, lw=1.5, label='shuffle median')
        # phase only means something where the signal is above noise: each shuffle's wrapped phase where coh > 0.5
        Pw, ok = np.mod(np.array([s['phase_deg'] for s in shufs]), 360), C > 0.5
        ax[1].scatter(np.broadcast_to(f, Pw.shape)[ok], Pw[ok], s=9, color=GREY, alpha=0.6, lw=0,
                      label='shuffles, phase where coherence > 0.5')
    T = out.get('type_null_wirings') or {}
    if T:
        for a_, key in ((ax[0], 'gain'), (ax[2], 'coh')):
            M = np.array([v[key] for v in T.values()])
            a_.fill_between(f, np.nanmin(M, 0), np.nanmax(M, 0), color=GREEN, alpha=0.18, lw=0,
                            label=f'{len(T)} type-preserving shuffles (min–max, secondary null)')
    # real wiring: line + markers, +-1 SD across seeds
    g, gs = np.array(real['gain']), np.array(real['gain_std'])
    ph, phs = _unwrap0(real['phase_deg']), np.array(real['phase_circstd_deg'])
    c, cs = np.array(real['coh']), np.array(real['coh_std'])
    lab = 'real wiring (seed-averaged H; band ±1 SD over seeds)'
    for a, y, s in ((ax[0], g, gs), (ax[1], ph, phs), (ax[2], c, cs)):
        lo = np.maximum(y - s, y * 0.05) if a is ax[0] else y - s
        a.fill_between(f, lo, y + s, color=BLUE, alpha=0.18, lw=0)
        a.plot(f, y, color=BLUE, marker='o', ms=5, label=lab, zorder=5)
    # first-order yaw damper reference C(s) = -K tau s / (tau s + 1), tau = 1 s, K = real |H| at 1 Hz
    fd = np.geomspace(f.min(), f.max(), 200)
    wt = 2 * np.pi * fd * 1.0
    K = at_f(real, 'gain', F_H1)
    DAMP = (r'yaw damper $-K\tau s/(\tau s+1)$, $\tau$ = 1 s; phase = 180° + washout lead'
            '\n(shape reference, scaled: K = real gain at 1 Hz)')
    ax[0].plot(fd, K * wt / np.sqrt(1 + wt ** 2), color=ORANGE, ls='--', lw=1.6,
               label=DAMP)
    ax[1].plot(fd, 180 + 90 - np.degrees(np.arctan(wt)), color=ORANGE, ls='--', lw=1.6,
               label=DAMP)
    nf = out['coherence_null']['per_f']
    ax[2].plot([r['f'] for r in nf], [r['q95'] for r in nf], color=INK2, ls='--', lw=1.2,
               label='noise floor (null 95th percentile)')
    for y, t in ((0.5, 'H1 threshold 0.5'), (0.3, 'G1 threshold 0.3')):
        ax[2].axhline(y, color=INK2, lw=0.8, ls=':')
        ax[2].text(f.max(), y, t, ha='right', va='bottom', fontsize=8, color=INK2)
    ax[0].set_yscale('log')
    ax[0].set_ylabel('gain |H|\n(Hz of ΔDNa02 per deg/s)')
    ax[1].set_ylabel('phase of ΔDNa02 re yaw rate (deg)')
    ax[1].axhline(180, color=INK2, lw=0.8, ls=':')
    ax[1].text(f.min(), 180, ' 180° = opposes rotation', va='bottom', fontsize=8, color=INK2)
    lo, hi = 0, 360  # phases wrapped to [0, 360); the real wiring stays inside (180 -> ~36 deg)
    ax[1].set_ylim(lo, hi)
    ax[1].set_yticks(np.arange(lo, hi + 1, 90))
    ax[2].set_ylim(0, 1.02)
    ax[2].set_ylabel('magnitude-squared coherence')
    ax[2].set_xscale('log')
    ax[2].set_xlabel('drive frequency (Hz)')
    ax[2].set_xticks([0.1, 0.2, 0.5, 1, 2, 5, 10, 20])
    ax[2].set_xticklabels(['0.1', '0.2', '0.5', '1', '2', '5', '10', '20'])
    hl = {}
    for a in ax:
        for h_, l_ in zip(*a.get_legend_handles_labels()):
            hl.setdefault(l_, h_)
    fig.legend(hl.values(), hl.keys(), loc='outside lower center', ncol=2, fontsize=7.5)
    syn = '  [SYNTHETIC TEST DATA]' if out['meta']['synthetic'] else ''
    fig.suptitle(TITLE + syn, fontsize=10, color=INK)
    fig.savefig(path, dpi=300)
    plt.close(fig)


def fig_amp(out, path):
    plt = _style()
    A = out['amp']
    amps = np.array(A['amplitudes_deg_s'])
    W = A['wirings']
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.8), constrained_layout=True)
    for a, m, yl in ((ax[0], 'gain', 'gain at 1 Hz (Hz of ΔDNa02 per deg/s)'), (ax[1], 'coh', 'coherence at 1 Hz')):
        S = np.array([[W[w].get(f'{x:g}', {}).get(m, np.nan) for x in amps] for w in W if w != 'real'])
        if len(S):
            a.fill_between(amps, np.nanmin(S, 0), np.nanmax(S, 0), color=GREY, alpha=0.22, lw=0,
                           label=f'{len(S)} shuffles (min–max)')
            a.plot(amps, np.nanmedian(S, 0), color=GREY, lw=1.5, label='shuffle median')
        a.plot(amps, [W['real'].get(f'{x:g}', {}).get(m, np.nan) for x in amps], color=BLUE, marker='o', ms=5,
               label='real wiring', zorder=5)
        a.set_xscale('log')
        a.set_xticks(amps)
        a.set_xticklabels([f'{x:g}' for x in amps])
        a.set_xlabel('yaw-rate amplitude (deg/s)')
        a.set_ylabel(yl)
        a.legend(fontsize=8)
    ax[0].set_yscale('log')
    ax[1].set_ylim(0, 1.02)
    syn = ' [SYNTHETIC TEST DATA]' if out['meta']['synthetic'] else ''
    fig.suptitle('Exploratory: 1 Hz amplitude sweep, FlyWire v783 eye→DNa02 subcircuit, 5 seeds' + syn,
                 fontsize=9.5, color=INK)
    fig.savefig(path, dpi=300)
    plt.close(fig)


# ---------------------------------------------------------------- summary
def write_summary(out, path):
    h, r = out['H1'], out['wirings']['real']
    fmt = lambda v: ', '.join(f'{x:.3g}' for x in v)
    L = [f'# Phase 1 summary (auto-generated by scripts/analyze_phase1.py, {out["meta"]["created"][:19]}Z)', '']
    if out['meta']['synthetic']:
        L += ['**SYNTHETIC TEST DATA. NOT A RESULT.**', '']
    L += [f'Data: {out["meta"]["data_version"]}; {len(out["meta"]["input_files"])} shard files; '
          f'data complete: {out["meta"]["data_complete"]} (missing: {len(out["meta"]["missing"])}).',
          f'Source: results/phase1_bode.json', '',
          f'## H1 verdict: **{h["verdict"]}**', '',
          f'- (a) real coherence > 0.5 at f ≤ 2 Hz: {"PASS" if h["a"]["pass"] else "FAIL"}. '
          f'f = {fmt(h["a"]["f"])} Hz; coh = {fmt(h["a"]["coh"])}',
          f'- (b) cos(phase of seed-averaged H) < 0 at f ≤ 1 Hz: {"PASS" if h["b"]["pass"] else "FAIL"}. '
          f'phase = {fmt(h["b"]["phase_deg"])} deg']
    for k, v in h['c'].items():
        L.append(f'- (c) 1 Hz {k}: real {v["real"]:.3g} vs {v["n_null"]} shuffles (median '
                 f'{np.median(list(v["null"].values())):.3g}); rank {v["rank"]}/{v["n_null"] + 1}; '
                 f'p = {v["p"]:.3g} (one-sided, α = 0.05): {"PASS" if v["pass"] else "FAIL"}')
    ht = out.get('H1_secondary_type_null')
    if ht:
        L += ['', '## Secondary: real vs the type-preserving null (swaps only within the post cell\'s side and type)', '']
        for k, v in ht['c'].items():
            L.append(f'- 1 Hz {k}: real {v["real"]:.3g} vs {v["n_null"]} type-preserving shuffles (median '
                     f'{np.nanmedian(list(v["null"].values())):.3g}, max {np.nanmax(list(v["null"].values())):.3g}); '
                     f'rank {v["rank"]}/{v["n_null"] + 1}; p = {v["p"]:.3g}')
    g1 = out['G1']
    L += ['', f'## G1: real coherence < 0.3 at every frequency: **{g1["coh_below_0p3_everywhere"]}** '
              f'(max {g1["max_coh"]:.3g} at {g1["f_at_max"]:.3g} Hz)', '',
          '## Real wiring per frequency', '', '| f (Hz) | gain (Hz per deg/s) | phase (deg) | coherence | '
          'coh SD | null q95 |', '|---|---|---|---|---|---|']
    for i, f in enumerate(r['f']):
        L.append(f'| {f:.3g} | {r["gain"][i]:.3g} | {r["phase_deg"][i]:.1f} | {r["coh"][i]:.3f} | '
                 f'{r["coh_std"][i]:.3f} | {out["coherence_null"]["per_f"][i]["q95"]:.3f} |')
    d = r['delay_fit']
    L += ['', f'Identified sign (pre-registered rule): real {r["identified_sign"]:+d}; shuffles: ' +
          ', '.join(f'{w} {v["identified_sign"]:+d} (1 Hz coh {at_f(v, "coh", F_H1):.2f})'
                    for w, v in out['wirings'].items() if w != 'real'),
          f'Exploratory pure-delay fit (f ≥ 2 Hz, coh > 0.5, {d["n_points"]} points): tau = '
          + (f'{d["tau_ms"]:.1f} ms' if d['tau_ms'] is not None else 'n/a (< 2 points)'),
          'DNa02 mean rates (Hz): ' + ', '.join(f'{k} {v:.3g}' for k, v in r['dna02_rates'].items()
                                                if not k.endswith('per_f'))]
    L += ['', '## g sensitivity (real wiring): coherence at each f', '',
          '| g | ' + ' | '.join(f'{f:.3g}' for f in r['f']) + ' |', '|---' * (len(r['f']) + 1) + '|']
    for g, v in out['gsens']['by_g'].items():
        L.append(f'| {g} | ' + ' | '.join(f'{x:.2f}' for x in v['coh']) + ' |')
    L += ['', '## Amplitude sweep at 1 Hz (exploratory)', '', '| A (deg/s) | real gain | shuffle gain median '
          '[min, max] | real coh | shuffle coh median [min, max] |', '|---|---|---|---|---|']
    for A in out['amp']['amplitudes_deg_s']:
        k, rw, ss = f'{A:g}', out['amp']['wirings']['real'], out['amp']['shuffle_summary']
        if k in rw and f'{k}_gain' in ss:
            sg, sc = ss[f'{k}_gain'], ss[f'{k}_coh']
            silent = '; %d silent (NaN)' % sc['n_nan'] if sc.get('n_nan') else ''
            L.append(f'| {k} | {rw[k]["gain"]:.3g} | {sg["median"]:.3g} [{sg["min"]:.3g}, {sg["max"]:.3g}] | '
                     f'{rw[k]["coh"]:.3f} | {sc["median"]:.3f} [{sc["min"]:.3f}, {sc["max"]:.3f}]{silent} |')
    open(path, 'w').write('\n'.join(L) + '\n')


# ---------------------------------------------------------------- synthetic data + self-check
SYN_G0, SYN_TAU, SYN_SIGMA = 0.7, 0.040, 30.0       # real: |H| = 0.7, phase = 180 - 360 f tau, noise SD 30 Hz
SYN_HOT = 'shuf07'                                  # a shuffle with higher gain but low coherence at 1 Hz


def syn_truth(w):
    """True (gain, phase_deg at f, noise SD) per synthetic wiring."""
    if w == 'real':
        return lambda f: (SYN_G0, 180 - 360 * f * SYN_TAU), SYN_SIGMA
    k = int(w[-2:]) + (100 if w.startswith('tshuf') else 0)
    rng = np.random.default_rng(1000 + k)
    g, ph0 = rng.uniform(0.1, 0.4) * SYN_G0, (180.0 if k % 3 else 0.0) + rng.uniform(-40, 40)
    if w == SYN_HOT:
        return lambda f: (1.3 * SYN_G0, 180.0), 400.0
    return lambda f: (g, ph0), SYN_SIGMA


def syn_row(w, f, A, seed, gscale=1.0):
    truth, sig = syn_truth(w)
    G, ph = truth(f)
    G *= gscale
    nc = n_cycles(f)
    fs = 200.0
    n = int(round(nc * fs / f))
    rng = np.random.default_rng([(WIRINGS + TWIRINGS).index(w), seed, int(f * 1e4), int(A), int(gscale * 10)])
    tc = (np.arange(n) + 0.5) / fs
    r = A * np.sin(2 * np.pi * f * tc)
    D = G * A * np.sin(2 * np.pi * f * tc + np.radians(ph)) + sig * rng.standard_normal(n)
    gain, phd = stats.transfer(r, D, f, fs, n_cycles=nc)
    coh = stats.coherence_at(r, D, f, fs)
    grp = {k: dict(gain=0.3, phase_deg=0.0, mean_hz=20.0 + 5 * ('_R' in k), warm_mean_hz=1.0 + ('_R' in k))
           for k in GROUPS}
    return dict(f=f, A=A, n_cycles=nc, T_s=1 + nc / f, gain=gain, phase_deg=phd,
                H_re=gain * np.cos(np.radians(phd)), H_im=gain * np.sin(np.radians(phd)), coh=coh['coh'],
                coh_floor_mean=coh['floor_mean'], n_segments=coh['n_segments'], groups=grp, T4T5_L_mean_hz=41.8)


def make_synthetic(shard_dir):
    os.makedirs(shard_dir, exist_ok=True)
    for p in glob.glob(os.path.join(shard_dir, 'phase1_*.json')):
        os.remove(p)
    jobs = [('bode', w, s, 1.0) for w in WIRINGS for s in SEEDS]
    jobs += [('gsens', 'real', s, g) for g in (0.5, 2.0, 4.0) for s in SEEDS]
    jobs += [('amp', w, s, a) for w in WIRINGS for s in SEEDS for a in (3.0, 10.0, 30.0)]
    jobs += [('bode', w, s, 1.0) for w in TWIRINGS for s in SEEDS]
    for kind, w, s, p in jobs:
        if kind == 'amp':
            rows = [syn_row(w, 1.0, p, s)]
        else:
            rows = [syn_row(w, f, AMP, s, gscale=np.sqrt(p)) for f in FREQS]
        meta = dict(phase=1, kind=kind, wiring=w, seed=s, param=p, g=p if kind != 'amp' else 1.0,
                    amp_deg_s=p if kind == 'amp' else AMP, shuffle_seed=None if w == 'real' else int(w[-2:]),
                    codegen='synthetic', warmup_s=1.0, data_version='flywire_v783', synthetic=True,
                    subcircuit='SYNTHETIC', git_hash=None, wall_s=0.0, created=datetime.now(timezone.utc).isoformat())
        with open(os.path.join(shard_dir, f'phase1_{kind}_{w}_s{s}_{p:g}.json'), 'w') as fh:
            json.dump(dict(meta=meta, rows=rows), fh, default=float)
    return len(jobs)


def selfcheck(out_dir=SCRATCH):
    assert not os.path.abspath(out_dir).startswith(os.path.join(ROOT, 'results')), 'synthetic data must not land in results/'
    n = make_synthetic(os.path.join(out_dir, 'shards'))
    assert n == 100 + 15 + 300 + 95
    o = analyse(os.path.join(out_dir, 'shards'), out_dir, n_mc=200)
    W, real, f = o['wirings'], o['wirings']['real'], np.array(o['freqs'])
    # shapes
    assert len(W) == 20 and o['meta']['data_complete'] and not o['meta']['missing'] and o['meta']['synthetic']
    assert len(f) == 13 and all(np.array(W[w]['per_seed']['coh']).shape == (5, 13) for w in W)
    assert len(o['coherence_null']['per_f']) == 13 and set(o['gsens']['by_g']) == {'0.5', '1', '2', '4'}
    assert all(set(o['amp']['wirings'][w]) == {'3', '10', '30', '100'} for w in W)
    # known gain / phase recovered
    true_ph = stats.wrap_deg(180 - 360 * f * SYN_TAU)
    assert np.allclose(real['gain'], SYN_G0, rtol=0.05), real['gain']
    ht = o['H1_secondary_type_null']
    assert ht and ht['c']['coherence']['n_null'] == 19 and len(o['type_null_wirings']) == 19
    assert all(w.startswith('shuf') for w in o['H1']['c']['gain']['null'])  # primary H1 never mixes in the type null
    assert np.all(np.abs(stats.wrap_deg(np.array(real['phase_deg']) - true_ph)) < 3), real['phase_deg']
    d = real['delay_fit']
    assert d['n_points'] == 5 and abs(d['tau_ms'] - SYN_TAU * 1e3) < 2, d
    assert np.allclose(o['gsens']['by_g']['4']['gain'], 2 * SYN_G0, rtol=0.05)
    # permutation p and rank, recomputed by hand
    h = o['H1']
    for key, stat in (('coherence', 'coh'), ('gain', 'gain_seedmean')):
        rv = at_f(real, stat, 1.0)
        nv = np.array([at_f(W[w], stat, 1.0) for w in W if w != 'real'])
        assert h['c'][key]['p'] == (1 + np.sum(nv >= rv)) / 20 and h['c'][key]['rank'] == 1 + np.sum(nv > rv)
    assert h['c']['coherence']['p'] == 0.05 and h['c']['coherence']['rank'] == 1, h['c']['coherence']
    assert h['c']['gain']['p'] == 0.10 and h['c']['gain']['rank'] == 2, h['c']['gain']   # SYN_HOT beats real
    assert h['a']['pass'] and h['b']['pass'] and h['failed'] == ['c_perm_gain_1Hz']
    assert h['verdict'] == 'not supported (failed: c_perm_gain_1Hz)'
    assert not o['G1']['coh_below_0p3_everywhere']
    # sign rule: Re(H) < 0 at low f -> +1
    assert real['identified_sign'] == 1
    for w in W:
        if w != 'real':
            expect = 1 if np.cos(np.radians(syn_truth(w)[0](0.1)[1])) < 0 else -1
            assert W[w]['identified_sign'] == expect, (w, W[w]['identified_sign'])
    assert {W[w]['identified_sign'] for w in W} == {1, -1}
    # identified_sign on a hand-made case: mean Re(H) over f <= 0.5 is (-1 + 3)/2 > 0 -> -1
    assert identified_sign(dict(f=[0.1, 0.5, 1.0], H_re=[-1, 3, -9])) == -1
    print(f'self-check OK: synthetic outputs in {out_dir} (H1 synthetic verdict: {h["verdict"]})')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--synthetic', metavar='DIR', help='generate fake shards in DIR/shards and analyse into DIR')
    ap.add_argument('--selfcheck', action='store_true')
    ap.add_argument('--shards', default=os.path.join(ROOT, 'results', 'shards'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'results'))
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck()
    elif a.synthetic:
        assert not os.path.abspath(a.synthetic).startswith(os.path.join(ROOT, 'results'))
        make_synthetic(os.path.join(a.synthetic, 'shards'))
        analyse(os.path.join(a.synthetic, 'shards'), a.synthetic)
    else:
        if not glob.glob(os.path.join(a.shards, 'phase1_*.json')):
            sys.exit(f'No phase1_*.json shards in {a.shards} yet. Download the GitHub Actions artifacts '
                     f'(gh run download) into that directory, then rerun.')
        o = analyse(a.shards, a.out)
        print(f'H1: {o["H1"]["verdict"]}; G1 coherence < 0.3 everywhere: {o["G1"]["coh_below_0p3_everywhere"]}; '
              f'data complete: {o["meta"]["data_complete"]}')
