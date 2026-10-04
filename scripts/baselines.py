"""Baseline controllers (bare airframe, washout yaw damper) and the one tuning procedure every controller gets.
Every controller implements reset(seed) and step(obs, dt) -> (rudder_cmd, activity); see plant.simulate.

  Bare        rudder 0.
  YawDamper   cmd = -K * washout(r), washout = tau s/(tau s + 1), tau = 1 s (fixed before data collection).
              Discrete: lp_k = a lp_{k-1} + (1-a) r_k, a = exp(-dt/tau); cmd = -K (r_k - lp_k), i.e.
              C(z) = -K a (z - 1)/(z - a). K in rudder_cmd per rad/s; the minus sign opposes r
              (cmd > 0 = nose-right moment, see plant.py).
  tune_gain   grid search minimizing the mean RMS r over the train seeds; the SAME function tunes the fly.
  K_GRID_N    9 values (0 + 8 log-spaced) for every controller, fly included (equal tuning budget).
"""
import json, time

import numpy as np

import plant

TRAIN_SEEDS = list(range(10))
K_GRID_N = 9
# 0 + 8 log-spaced over 3 decades. On lin2 the mean RMS r falls monotonically with K until the loop chatters at
# the Nyquist frequency (sample-delay limit), so the grid must reach past that point for its optimum to be interior.
K_GRID_DAMPER = [0.0] + list(np.geomspace(1.0, 1000.0, K_GRID_N - 1))
MARGIN_REQ = dict(gm_db=6.0, pm_deg=45.0)   # MIL-F-9490D rigid-body loop margins (quoted in Mansur et al., AHS 2009)


class Bare:
    name = 'bare'

    def reset(self, seed):
        pass

    def step(self, obs, dt):
        return 0.0, {}


class YawDamper:
    name = 'yaw_damper'

    def __init__(self, K, tau=1.0):
        self.K, self.tau = float(K), tau

    def reset(self, seed):
        self.lp = 0.0

    def step(self, obs, dt):
        a = np.exp(-dt / self.tau)
        self.lp = a * self.lp + (1 - a) * obs['r']
        return float(np.clip(-self.K * (obs['r'] - self.lp), -1.0, 1.0)), {}


def tune_gain(make_controller, K_grid, train_seeds=TRAIN_SEEDS, plant_name='lin2', T=60.0, **sim_kw):
    """Fly make_controller(K) for every K on every train seed; best K = lowest mean RMS r (ties -> smaller K).
    Returns best_K, on_edge (pre-registered: if True for any controller, widen the grid for all) and the table."""
    table = []
    for K in K_grid:
        c = make_controller(K)
        ms = [plant.simulate(c, s, T=T, plant=plant_name, **sim_kw)['metrics'] for s in train_seeds]
        table.append(dict(K=float(K), mean_rms_r=float(np.mean([m['rms_r'] for m in ms])),
                          mean_rms_beta=float(np.mean([m['rms_beta'] for m in ms])),
                          mean_rms_rudder=float(np.mean([m['rms_rudder'] for m in ms])),
                          n_departed=int(sum(m['departed'] for m in ms)), rms_r=[m['rms_r'] for m in ms]))
    i = int(np.argmin([row['mean_rms_r'] for row in table]))
    return dict(best_K=table[i]['K'], best_index=i, on_edge=i in (0, len(K_grid) - 1), table=table,
                train_seeds=list(train_seeds), plant=plant_name, T=T)


def damper_L_analytic(K, freqs, dt=plant.DT, tau=1.0):
    """Exact discrete loop L(z) = -C(z) P_r(z) of YawDamper on lin2 in plant.simulate's lockstep, and the
    continuous-time L(s) = K tau s/(tau s+1) P_r(s) (no sampling) for reference."""
    A, B, Ad, Bd = plant.lin2_matrices(dt)
    a = np.exp(-dt / tau)
    z, s = np.exp(2j * np.pi * np.asarray(freqs) * dt), 2j * np.pi * np.asarray(freqs)
    Pz = np.array([np.linalg.solve(zz * np.eye(2) - Ad, Bd[:, 0])[1] for zz in z])
    Ps = np.array([np.linalg.solve(ss * np.eye(2) - A, B[:, 0])[1] for ss in s])
    Lz = K * a * (z - 1) / (z - a) * Pz
    Ls = K * tau * s / (tau * s + 1) * Ps
    # at Nyquist (z = -1) L is real and negative, so the phase reaches -180 deg exactly there
    Pn = np.linalg.solve(-np.eye(2) - Ad, Bd[:, 0])[1]
    L_nyq = K * a * (-2) / (-1 - a) * Pn
    return Lz, Ls, float(L_nyq)


def damper_margins_analytic(K, fmin=0.005, fmax=49.9):
    f = np.geomspace(fmin, fmax, 4000)
    Lz, Ls, Ln = damper_L_analytic(K, f)
    md, mc = plant.margins(f, Lz), plant.margins(f, Ls)
    gm_nyq = float(-20 * np.log10(abs(Ln))) if Ln != 0 else float('inf')
    return dict(discrete=dict(gm_db=min(md['gm_db'], gm_nyq), gm_nyquist_db=gm_nyq, pm_deg=md['pm_deg'],
                              f_pm_hz=md['f_pm_hz'], pm_all=md['pm_all']),
                continuous=dict(gm_db=mc['gm_db'], pm_deg=mc['pm_deg'], f_pm_hz=mc['f_pm_hz'], pm_all=mc['pm_all']))


if __name__ == '__main__':
    out = dict(meta=plant.meta_common())

    # 1. contract basics: Bare is zero; the damper opposes r and washes out a steady r
    assert Bare().step(dict(t=0, r=1, p=0, beta=0, phi=0), 0.01) == (0.0, {})
    yd = YawDamper(2.0)
    yd.reset(0)
    u = [yd.step(dict(t=k * .01, r=0.1, p=0, beta=0, phi=0), 0.01)[0] for k in range(1000)]
    assert u[0] < 0 and abs(u[-1]) < 1e-3 * abs(u[0]), (u[0], u[-1])   # ~e^-10 after 10 tau

    # 2. tune on lin2, train seeds only
    t0 = time.time()
    bare = [plant.simulate(Bare(), s, plant='lin2')['metrics'] for s in TRAIN_SEEDS]
    tun = tune_gain(YawDamper, K_GRID_DAMPER)
    out['lin2'] = dict(bare=dict(mean_rms_r=float(np.mean([m['rms_r'] for m in bare])),
                                 mean_rms_beta=float(np.mean([m['rms_beta'] for m in bare])),
                                 rms_r=[m['rms_r'] for m in bare]),
                       tuning=tun, wall_s=time.time() - t0)
    assert np.isclose(tun['table'][0]['mean_rms_r'], out['lin2']['bare']['mean_rms_r'])   # K = 0 is the bare airframe
    print('lin2 bare mean rms_r %.5f' % out['lin2']['bare']['mean_rms_r'])
    for row in tun['table']:
        row['analytic_margins'] = damper_margins_analytic(row['K'])['discrete'] if row['K'] > 0 else None
        mg = row['analytic_margins']
        row['meets_MIL_F_9490D'] = bool(mg and mg['gm_db'] >= MARGIN_REQ['gm_db'] and mg['pm_deg'] >= MARGIN_REQ['pm_deg'])
        print('K %8.3f  rms_r %.5f  rms_beta %.5f  rms_rudder %.3f  GM %s dB  PM %s deg  ok=%s' % (
            row['K'], row['mean_rms_r'], row['mean_rms_beta'], row['mean_rms_rudder'],
            mg and round(mg['gm_db'], 1), mg and mg['pm_deg'] and round(mg['pm_deg'], 1), row['meets_MIL_F_9490D']))
    ok = [r for r in tun['table'] if r['meets_MIL_F_9490D']]
    K_adm = min(ok, key=lambda r: r['mean_rms_r'])['K']
    out['lin2']['best_K_preregistered'] = tun['best_K']
    out['lin2']['best_K_meeting_MIL_F_9490D'] = K_adm
    print('best K (pre-registered objective):', tun['best_K'], 'on_edge', tun['on_edge'],
          '| best K meeting 6 dB / 45 deg:', K_adm)

    # 3. margins by injection vs analytic (exact discrete model) for the margin-admissible damper
    freqs = np.geomspace(0.02, 25, 30)
    M = plant.loop_margins(YawDamper(K_adm), 'lin2', freqs=freqs, A=0.05)
    Lz, _, _ = damper_L_analytic(K_adm, freqs)
    err = np.abs(M['L'] / Lz - 1)
    an = damper_margins_analytic(K_adm)
    out['lin2']['margins'] = dict(
        K=K_adm, A=M['A'], saturated=M['saturated'], freqs_hz=freqs.tolist(),
        L_injected=[[float(x.real), float(x.imag)] for x in M['L']], max_rel_err_vs_exact_discrete=float(err.max()),
        injected=dict(gm_db=M['gm_db'], f_gm_hz=M['f_gm_hz'], pm_deg=M['pm_deg'], f_pm_hz=M['f_pm_hz'], pm_all=M['pm_all']),
        analytic=an)
    print('margins K=%.3f injected PM %.2f deg @ %.3f Hz, GM %s | analytic discrete PM %.2f deg, GM %.1f dB (Nyquist) '
          '| continuous PM %.2f deg; max |L_inj/L_exact - 1| = %.4f' % (
              K_adm, M['pm_deg'], M['f_pm_hz'], M['gm_db'], an['discrete']['pm_deg'], an['discrete']['gm_db'],
              an['continuous']['pm_deg'], err.max()))
    assert not M['saturated'] and err.max() < 0.02
    assert abs(M['pm_deg'] / an['discrete']['pm_deg'] - 1) < 0.03
    assert abs(M['f_pm_hz'] / an['discrete']['f_pm_hz'] - 1) < 0.03

    # 4. JSBSim: time one 60 s flight, then the same tuning on the 6-DOF plant (cheap: ~0.2 s per flight)
    t0 = time.time()
    m = plant.simulate(YawDamper(K_adm), 0, T=60.0, plant='jsbsim')['metrics']
    wall = time.time() - t0
    t0 = time.time()
    tun_j = tune_gain(YawDamper, K_GRID_DAMPER, plant_name='jsbsim')
    for row in tun_j['table']:
        print('jsbsim K %8.3f  rms_r %.5f  rms_beta %.5f  rms_rudder %.3f  departed %d' % (
            row['K'], row['mean_rms_r'], row['mean_rms_beta'], row['mean_rms_rudder'], row['n_departed']))
    out['jsbsim'] = dict(one_flight_wall_s=wall, one_flight_metrics=m, tuning=tun_j, tuning_wall_s=time.time() - t0)
    print('jsbsim one 60 s flight: %.3f s wall; tuning %d flights: %.1f s; best K %s on_edge %s' % (
        wall, len(K_GRID_DAMPER) * len(TRAIN_SEEDS), out['jsbsim']['tuning_wall_s'], tun_j['best_K'], tun_j['on_edge']))

    p = plant.RESULTS / 'baselines_yaw_damper.json'
    p.write_text(json.dumps(out, indent=1, default=float))
    print('baselines.py self-checks passed ->', p.name)
