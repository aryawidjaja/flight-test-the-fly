"""Statistics for Phases 1-3. Coherence settings and seed splits were fixed before data collection.

- lockin / transfer: gain and phase at the drive frequency by projection onto sin/cos over whole cycles.
  Phase convention: x = A*sin(2*pi*f*t + phase), t = 0 at the first sample passed in. Degrees.
- coherence_at: scipy.signal.coherence, nperseg = one drive cycle, 50% overlap (Hann), read at bin 1.
- bootstrap_ci: CI of the mean paired difference (H2: fly - bare per test seed), percentile or BCa.
- perm_test_real_vs_null: one-sided empirical p = (1 + #{null >= real}) / (1 + n_null) (H1, H2b).
- departure_auc / departure_auc_ci: normalized area under the no-departure curve (H4), seed bootstrap.

Tip for Phase 1: choose drive frequencies with fs/f an integer (fs = 200 Hz for 5 ms bins), so a cycle is a
whole number of samples, lock-in has no leakage and coherence bin 1 sits exactly on f.
"""
import numpy as np
from scipy import signal, stats


def _n_window(n_avail, f, fs, n_cycles):
    spc = fs / f
    if n_cycles is None:
        n_cycles = int(np.floor(n_avail / spc + 1e-9))
    n = int(round(n_cycles * spc))
    if n_cycles < 1 or n > n_avail:
        raise ValueError(f'need {n_cycles} cycles = {n} samples, have {n_avail}')
    return n


def lockin(x, u_ref_freq, fs, n_cycles=None):
    """Amplitude and phase (deg) of x at u_ref_freq, over the first n_cycles whole cycles (default: as many
    as fit). Trim the warm-up before calling. Mean is removed first."""
    x = np.asarray(x, float)
    n = _n_window(len(x), u_ref_freq, fs, n_cycles)
    x = x[:n] - x[:n].mean()
    wt = 2 * np.pi * u_ref_freq * np.arange(n) / fs
    i, q = 2 / n * np.sum(x * np.sin(wt)), 2 / n * np.sum(x * np.cos(wt))  # not @: Accelerate BLAS emits spurious warnings
    return float(np.hypot(i, q)), float(np.degrees(np.arctan2(q, i)))


def wrap_deg(p):
    """Wrap to (-180, 180]."""
    return 180.0 - np.mod(180.0 - np.asarray(p, float), 360.0)


def transfer(x_in, x_out, f, fs, n_cycles=None):
    """Gain (out amplitude / in amplitude) and phase of out relative to in, deg in (-180, 180]."""
    a_in, p_in = lockin(x_in, f, fs, n_cycles)
    a_out, p_out = lockin(x_out, f, fs, n_cycles)
    return a_out / a_in, float(wrap_deg(p_out - p_in))


def n_segments(n_samples, nperseg):
    step = nperseg - nperseg // 2
    return 1 + (n_samples - nperseg) // step


def coherence_at(x, y, f, fs):
    """Magnitude-squared coherence at the drive bin. Returns dict(coh, f_bin, nperseg, n_segments,
    floor_mean = 1/n_segments, the independent-segment null mean; see null_coherence for the exact null
    with overlapping segments)."""
    nperseg = int(round(fs / f))
    if len(x) < 5 * nperseg:
        raise ValueError(f'coherence needs >= 5 drive cycles ({5 * nperseg} samples), got {len(x)}')
    fr, c = signal.coherence(x, y, fs=fs, nperseg=nperseg, noverlap=nperseg // 2)
    ns = n_segments(len(x), nperseg)
    return {'coh': float(c[1]), 'f_bin': float(fr[1]), 'nperseg': nperseg, 'n_segments': ns, 'floor_mean': 1 / ns}


def null_coherence(n_cycles, spc=200, n_mc=2000, seed=0, q=(0.5, 0.95, 0.99)):
    """Monte Carlo null of coherence_at: a pure sine vs independent white noise, n_cycles whole cycles of spc
    samples. Returns mean, quantiles and P(coh > 0.3), P(coh > 0.5)."""
    rng = np.random.default_rng(seed)
    n = n_cycles * spc
    x = np.sin(2 * np.pi * np.arange(n) / spc)
    c = np.array([coherence_at(x, rng.standard_normal(n), 1.0, spc)['coh'] for _ in range(n_mc)])
    out = {'n_segments': n_segments(n, spc), 'mean': float(c.mean()), 'p_gt_0.3': float((c > 0.3).mean()),
           'p_gt_0.5': float((c > 0.5).mean())}
    out.update({f'q{int(100 * k)}': float(np.quantile(c, k)) for k in q})
    return out


def bootstrap_ci(diff_per_seed, n_boot=10000, alpha=0.05, seed=0, method='percentile'):
    """CI of the mean of paired per-seed differences. method: 'percentile' or 'BCa'."""
    d = np.asarray(diff_per_seed, float)
    r = stats.bootstrap((d,), np.mean, n_resamples=n_boot, confidence_level=1 - alpha, method=method,
                        rng=np.random.default_rng(seed))
    lo, hi = r.confidence_interval
    return {'mean': float(d.mean()), 'lo': float(lo), 'hi': float(hi), 'n': len(d), 'method': method,
            'excludes_0': bool(lo > 0 or hi < 0)}


def perm_test_real_vs_null(real_value, null_values, alternative='greater'):
    """One-sided empirical p of the real wiring against the null wirings, counting ties against the real one.
    rank 1 = the real wiring is the most extreme. p_min = 1/(n_null+1): with 10 shuffles p_min = 1/11 = 0.091,
    so p < 0.05 is impossible; n_null >= 19 is needed for p_min = 0.05."""
    v = np.asarray(null_values, float)
    if alternative == 'greater':
        k, rank = np.sum(v >= real_value), 1 + np.sum(v > real_value)
    elif alternative == 'less':
        k, rank = np.sum(v <= real_value), 1 + np.sum(v < real_value)
    else:
        raise ValueError(alternative)
    return {'p': float((1 + k) / (1 + len(v))), 'rank': int(rank), 'n_null': len(v), 'p_min': 1 / (1 + len(v))}


def departure_auc(fractions, p_no_departure):
    """Trapezoid area under P(no departure) vs lesion fraction, divided by the fraction span (1 = never departs)."""
    f, p = np.asarray(fractions, float), np.asarray(p_no_departure, float)
    return float(np.trapezoid(p, f) / (f[-1] - f[0]))


def departure_auc_ci(fractions, departed, departed_ref=None, n_boot=10000, alpha=0.05, seed=0):
    """departed: bool (n_seeds, n_fractions). Bootstraps test seeds. With departed_ref (same seeds, e.g. a
    shuffle), the statistic is AUC(departed) - AUC(departed_ref), resampled with the seeds paired."""
    d = np.asarray(departed, bool)
    ref = None if departed_ref is None else np.asarray(departed_ref, bool)
    stat = lambda i: departure_auc(fractions, 1 - d[i].mean(0)) - (0 if ref is None else departure_auc(fractions, 1 - ref[i].mean(0)))
    rng = np.random.default_rng(seed)
    boot = np.array([stat(rng.integers(0, len(d), len(d))) for _ in range(n_boot)])
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    return {'auc': stat(np.arange(len(d))), 'lo': float(lo), 'hi': float(hi), 'n_seeds': len(d)}


if __name__ == '__main__':
    fs, f = 200.0, 1.3  # non-integer samples per cycle on purpose
    t = np.arange(int(8 * fs / f)) / fs
    a, p = lockin(3.0 * np.sin(2 * np.pi * f * t + np.radians(40)) + 0.5, f, fs)
    assert abs(a - 3) < 0.03 and abs(p - 40) < 0.4, (a, p)
    g, ph = transfer(np.sin(2 * np.pi * f * t), 2 * np.sin(2 * np.pi * f * t - np.radians(170)), f, fs)
    assert abs(g - 2) < 0.02 and abs(ph + 170) < 0.4, (g, ph)
    g, ph = transfer(np.sin(2 * np.pi * f * t), np.sin(2 * np.pi * f * t + np.radians(190)), f, fs)
    assert abs(ph + 170) < 0.4, ph  # wrapped into (-180, 180]
    assert wrap_deg(180) == 180 and wrap_deg(-180) == 180 and wrap_deg(540) == 180 and wrap_deg(-90) == -90

    rng = np.random.default_rng(0)
    n = 5 * 200
    x = np.sin(2 * np.pi * np.arange(n) / 200)
    c = coherence_at(x, -2 * x + 0.1 * rng.standard_normal(n), 1.0, 200)
    assert c['coh'] > 0.99 and c['n_segments'] == 9 and c['f_bin'] == 1.0, c
    assert coherence_at(x, rng.standard_normal(n), 1.0, 200)['coh'] < 0.6
    try:
        coherence_at(x[:4 * 200], x[:4 * 200], 1.0, 200)
        raise AssertionError('should have raised')
    except ValueError:
        pass
    nc = null_coherence(5, n_mc=500)
    assert 0.05 < nc['mean'] < 0.4, nc
    print('coherence null, 5 cycles:', nc)

    b = bootstrap_ci(rng.normal(-0.3, 0.2, 20), seed=1)
    bb = bootstrap_ci(rng.normal(-0.3, 0.2, 20), seed=1, method='BCa')
    assert b['lo'] < -0.3 < b['hi'] and b['excludes_0'] and bb['lo'] < -0.3 < bb['hi'], (b, bb)
    cover = np.mean([(lambda r: r['lo'] < 1 < r['hi'])(bootstrap_ci(rng.normal(1, 1, 20), 2000, seed=k)) for k in range(200)])
    assert 0.88 < cover <= 1.0, cover  # percentile CI with n=20 under-covers slightly
    print('bootstrap coverage of nominal 95% CI, n=20:', cover)

    r = perm_test_real_vs_null(5.0, [1, 2, 3, 4, 5, 6, 0, 0, 0, 0])
    assert r['p'] == 3 / 11 and r['rank'] == 2 and abs(r['p_min'] - 1 / 11) < 1e-12, r
    assert perm_test_real_vs_null(9, np.arange(19))['p'] == 11 / 20
    assert perm_test_real_vs_null(99, np.arange(19))['p'] == 0.05
    assert perm_test_real_vs_null(-1, np.arange(10), 'less')['p'] == 1 / 11

    fr = [0, .05, .1, .2, .4, .6, .8]
    assert abs(departure_auc(fr, np.ones(7)) - 1) < 1e-12 and abs(departure_auc([0, 1], [1, 0]) - 0.5) < 1e-12
    dep = rng.random((20, 7)) < np.array(fr) * 0.8
    ci = departure_auc_ci(fr, dep, n_boot=2000)
    assert ci['lo'] <= ci['auc'] <= ci['hi'] and departure_auc_ci(fr, dep, dep, n_boot=200)['hi'] == 0
    print('stats.py self-check OK')
