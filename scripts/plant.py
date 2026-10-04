"""The plant.

  (a) trim + linearize JSBSim c172x            -> results/plant_c172x_derivs.json
  (b) 2-state (beta, r) Dutch-roll plant, 'lin2'
  (c) Dryden lateral gust v_g (MIL-F-8785C)
  (d) simulate(controller, seed, T, plant='lin2'|'jsbsim', turbulence=...)   (controller.reset/step, lockstep)
  (e) JSBSim 6-DOF wrapper with seeded MIL-spec turbulence and a fixed wings-level aileron PID
  (f) loop_margins(): break the loop at the actuator, inject sines, lock-in -> L(jw), GM/PM

SIGN CONVENTIONS (defined here and nowhere else)
  r > 0     nose-right yaw rate (body z down).            JSBSim velocities/r-rad_sec.
  beta > 0  airflow from the right (nose left of the air-relative velocity). JSBSim aero/beta-rad.
  phi > 0   right wing down.  p > 0 right wing going down.
  cmd > 0   the controller's rudder_cmd: "command a nose-RIGHT yaw moment" (pre-registered fly readout,
            Delta > 0 -> nose-right). c172x has Cndr = -0.043 /rad on fcs/rudder-pos-rad (c172x.xml,
            Cndr), i.e. positive fcs/rudder-cmd-norm yaws the nose LEFT (standard TE-left-positive rudder,
            Etkin/Nelson sign). Therefore   fcs/rudder-cmd-norm = -cmd   and in the linear plant
            N_cmd = -N_drnorm > 0, so cmd > 0 always gives rdot > 0 (asserted in __main__).
  rudder    |fcs/rudder-cmd-norm| = 1 <-> 16 deg = 0.2793 rad of rudder (c172x <aerosurface_scale
            name="fcs/rudder-control"> range +-16 deg). c172x has NO rudder actuator element, so the 2-state
            plant has none either (consistency between 2a and 2b; no lag is cited, so none is invented).
  v_g > 0   lateral gust blowing toward the right wing (+y body / heading frame). The aircraft's air-relative
            lateral velocity is v - v_g, so beta_air = beta - v_g/V (beta = inertial sideslip v/V). This is the
            same as JSBSim (FGAuxiliary: vAeroUVW = vUVW - Tl2b*TotalWindNED). obs['beta'] and the recorded
            'beta' are ALWAYS the air-relative (aerodynamic) sideslip, in both plants.

LOCKSTEP TIMING. u_ctrl starts at 0. Each step k: u_plant = sat(u_ctrl + d(t_k)) is held over [t_k, t_k+dt]
(zero-order hold), the plant advances dt, then controller.step(obs(t_{k+1}), dt) returns the u_ctrl for the next
interval. So the loop transfer is L(z) = -C(z) * P(z), P(z) the ZOH-discretized plant.
"""
import json, subprocess, time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.linalg import expm, solve_continuous_lyapunov, cholesky

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'results'
DERIVS_JSON = RESULTS / 'plant_c172x_derivs.json'
FT, KT = 0.3048, 0.514444
H_FT, VT_KTS = 4000.0, 100.0          # trim: 4000 ft MSL (> 2000 ft -> MIL-F-8785C medium/high-altitude model), 100 KTAS
DT = 0.01                              # controller / plant step
JSB_SUB = 2                            # JSBSim runs at dt/2 = 200 Hz, an exact multiple of the 10 ms control step
DEG = np.pi / 180

# MIL-F-8785C Fig. 7 (sigma_u = sigma_v = sigma_w vs altitude, medium/high altitude), as tabulated in JSBSim
# FGWinds.cpp (commit 3b25f25, the build installed here). Rows = probability-of-exceedance index ("severity").
# Light/moderate/severe = 1e-2/1e-3/1e-5 = rows 3/4/6 (JSBSim FGWinds.h docs; MathWorks Dryden block docs).
_POE_ALT_FT = [500, 1750, 3750, 7500, 15000, 25000, 35000, 45000, 55000, 65000, 75000, 80000]
_POE_FPS = {3: [6.6, 6.9, 7.4, 6.7, 4.6, 2.7, 0.4, 0, 0, 0, 0, 0],
            4: [8.6, 9.6, 10.6, 10.1, 8.0, 6.6, 5.0, 4.2, 2.7, 0, 0, 0],
            6: [15.6, 17.6, 23.0, 23.6, 22.1, 20.0, 16.0, 15.1, 12.1, 7.9, 6.2, 5.1]}
SEVERITY = {'none': 0, 'light': 3, 'moderate': 4, 'severe': 6}
W20_FPS = {'none': 0.0, 'light': 25.0, 'moderate': 50.0, 'severe': 75.0}   # only used by JSBSim below 2000 ft
DEPARTURE = dict(phi=60 * DEG, beta=20 * DEG, r=60 * DEG, hold_s=1.0)        # pre-registered departure rule (H4)

# Wings-level hold for 2b, identical for every run: da = da_trim - KP*phi - KD*p - KI*int(phi).
# Positive fcs/aileron-cmd-norm gives +L (right roll) in c172x (Clda > 0 on the effective aileron), hence minus.
ROLL_PID = dict(KP=0.6, KD=0.08, KI=0.1)


def meta_common():
    try:
        git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        git = ''
    import jsbsim
    return dict(aircraft='c172x', jsbsim_version=jsbsim.__version__, git_hash=git or 'no-git-repo',
                data_version='flywire_v783 (not used by the plant)', created=datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------- (a) JSBSim trim + linearization
def trimmed_fdm(dt=DT / JSB_SUB):
    import jsbsim
    f = jsbsim.FGFDMExec(None)
    f.set_debug_level(0)
    f.load_model('c172x')
    f.set_dt(dt)
    for k, v in {'ic/h-sl-ft': H_FT, 'ic/vt-kts': VT_KTS, 'ic/gamma-deg': 0.0, 'ic/psi-true-deg': 0.0}.items():
        f[k] = v
    f['propulsion/set-running'] = -1   # all engines running (a cold engine makes the trim fail: "udot not trimmable")
    f.run_ic()
    f.do_trim(1)                       # 1 = full longitudinal + lateral trim
    return f


def linearize(write=True):
    """JSBSim's own FGLinearization (finite differences about the trim point), cross-checked against the
    c172x.xml coefficients. Units: per-second dimensional; rudder in fcs/rudder-cmd-norm units (JSBSim sign)."""
    import jsbsim
    f = trimmed_fdm()
    lin = jsbsim.FGLinearization(f)
    A, B = np.array(lin.system_matrix), np.array(lin.input_matrix)
    xi = {n: i for i, n in enumerate(lin.x_names)}
    ui = {n: i for i, n in enumerate(lin.u_names)}
    b, p, r, ph = xi['Beta'], xi['P'], xi['R'], xi['Phi']
    dr, da = ui['DrCmd'], ui['DaCmd']
    V = f['velocities/vt-fps']
    d = dict(Yb_V=A[b, b], Yp_V=A[b, p], Yr_V_minus_1=A[b, r], g_cos_theta_V=A[b, ph], Ydr_V=B[b, dr], Yda_V=B[b, da],
             Nb=A[r, b], Np=A[r, p], Nr=A[r, r], Ndr=B[r, dr], Nda=B[r, da],
             Lb=A[p, b], Lp=A[p, p], Lr=A[p, r], Ldr=B[p, dr], Lda=B[p, da])
    d = {k: float(v) for k, v in d.items()}
    d['Yb_fps2'] = d['Yb_V'] * V
    d['Ydr_fps2'] = d['Ydr_V'] * V

    def modes(eigs):
        cpx = [e for e in eigs if e.imag > 1e-6]
        return [dict(re=float(e.real), im=float(e.imag), wn=float(abs(e)), zeta=float(-e.real / abs(e))) for e in cpx]
    lat = [b, p, r, ph]
    eig_lat = np.linalg.eigvals(A[np.ix_(lat, lat)])
    eig_full = np.linalg.eigvals(A)
    dr_lat = modes(eig_lat)                                    # the lateral 4x4 has exactly one complex pair
    assert len(dr_lat) == 1, eig_lat
    dr_full = min(modes(eig_full), key=lambda m: abs(m['wn'] - dr_lat[0]['wn']))
    A2 = np.array([[d['Yb_V'], -1.0], [d['Nb'], d['Nr']]])
    dr_2 = modes(np.linalg.eigvals(A2))[0]

    # independent cross-check from c172x.xml: Cnb = 0.0227/0.349, Cndr = -0.043, CYb = -0.108/0.349, 16 deg rudder
    qS, bw = f['aero/qbar-psf'] * f['metrics/Sw-sqft'], f['metrics/bw-ft']
    Izz, m = f['inertia/izz-slugs_ft2'], f['inertia/mass-slugs']
    xml = dict(Nb=qS * bw * (0.0227 / 0.349) / Izz, Ndr=qS * bw * (-0.043) * 16 * DEG / Izz,
               # + the drag (= thrust at trim) rotated into body y by beta: -D*beta (Etkin & Reid: Y_v includes -D)
               Yb_V=(qS * (-0.108 / 0.349) - f['propulsion/engine/thrust-lbs']) / (m * V))
    out = dict(
        meta=dict(**meta_common(), method='jsbsim.FGLinearization (JSBSim built-in numerical linearization) about '
                  'do_trim(1); cross-checked against c172x.xml aero coefficients (xml_check)'),
        trim=dict(h_sl_ft=H_FT, vt_kts=VT_KTS, vt_fps=V, vt_mps=V * FT, gamma_deg=0.0, psi_deg=0.0,
                  alpha_deg=f['aero/alpha-deg'], theta_deg=f['attitude/theta-deg'], qbar_psf=f['aero/qbar-psf'],
                  rho_slug_ft3=f['atmosphere/rho-slugs_ft3'], throttle=f['fcs/throttle-cmd-norm'],
                  pitch_trim=f['fcs/pitch-trim-cmd-norm'], aileron_trim=f['fcs/aileron-cmd-norm'],
                  rudder_trim=f['fcs/rudder-cmd-norm'], mass_slug=m, Ixx=f['inertia/ixx-slugs_ft2'], Izz=Izz,
                  Ixz=f['inertia/ixz-slugs_ft2'], flaps=0),
        derivs=d,
        units=('per second, dimensional. Y*_V = (dY/dx)/(m V). N*, L* = angular accel per unit. *dr, *da per unit '
               'fcs/rudder-cmd-norm / fcs/aileron-cmd-norm in the JSBSim sign (positive rudder-cmd-norm -> nose-left).'),
        rudder_mapping=dict(norm_to_rad=16 * DEG, note='rudder-cmd-norm = 1 <-> 16 deg (c172x fcs/rudder-control range), '
                            'no actuator. Controller cmd = -rudder-cmd-norm, so N_cmd = -Ndr > 0, Y_cmd/V = -Ydr_V.'),
        lin2=dict(A=A2.tolist(), N_cmd=-d['Ndr'], Ycmd_V=-d['Ydr_V'], note='states (beta, r); beta_dot uses -r '
                  '(the pre-registered textbook form) instead of Yr_V_minus_1 (difference < 1%)'),
        dutch_roll=dict(full_13state=dr_full, lateral_4state=dr_lat[0], two_state=dr_2),
        modes_full=[[float(e.real), float(e.imag)] for e in eig_full],
        xml_check=dict(xml={k: float(v) for k, v in xml.items()},
                       rel_err={k: float(abs(d[k] / xml[k] - 1)) for k in xml}),
        literature=[
            dict(source='M. Peet, MMAE 441 Lecture 12 (IIT), "C172: V0 = 132 kt, 5000 ft", '
                        'https://control.asu.edu/Classes/MMAE441/Aircraft/441Lecture12.pdf (slides 7, 19)',
                 wn=3.4, zeta=0.2, eig='-0.686 +- 3.306j', Nb=10.119, Nr=-1.2597, Yb_V=-0.1473,
                 caveat='lecture-slide data; its own roll-mode slide gives Lp inconsistent with its matrix'),
            dict(source='MIL-F-8785C para 3.3.1.1 Table VI, Level 1, Category B (as reproduced in AFWAL-TR-81-3109): zeta >= 0.08, '
                        'zeta*wn >= 0.15 rad/s, wn >= 0.4 rad/s', wn_min=0.4, zeta_min=0.08, zeta_wn_min=0.15)],
    )
    if write:
        DERIVS_JSON.write_text(json.dumps(out, indent=1))
    return out


def load_derivs():
    if not DERIVS_JSON.exists():
        linearize()
    return json.loads(DERIVS_JSON.read_text())


# ---------------------------------------------------------------- (c) Dryden lateral gust
def dryden_params(turbulence, h_ft=H_FT):
    """sigma_v [m/s], L_v [m] for MIL-F-8785C medium/high altitude (h > 2000 ft): L_u = L_v = L_w = 1750 ft
    (MIL-F-8785C 3.7.2.1; identical to MIL-HDBK-1797's L_u = 2L_v = 1750 ft with its 2L_v form),
    sigma_v = sigma_u from Fig. 7 at the POE of the severity."""
    if SEVERITY[turbulence] == 0:
        return 0.0, 1750 * FT
    assert h_ft > 2000, 'only the medium/high-altitude Dryden model is implemented'
    return float(np.interp(h_ft, _POE_ALT_FT, _POE_FPS[SEVERITY[turbulence]])) * FT, 1750 * FT


def dryden_v(seed, n, dt, sigma, L, V):
    """n samples of the Dryden lateral gust, H_v(s) = sigma*sqrt(T) (1 + sqrt(3) T s)/(1 + T s)^2, T = L/V,
    driven by unit-intensity continuous white noise (so var = sigma^2 exactly; MIL-F-8785C form). Exact
    discretization: Ad = expm(A dt), process noise covariance by Van Loan, started from the stationary covariance."""
    if sigma == 0:
        return np.zeros(n)
    T = L / V
    A = np.array([[0.0, 1.0], [-1 / T**2, -2 / T]])
    Bw = np.array([[0.0], [1.0]])
    C = sigma * np.sqrt(T) / T**2 * np.array([1.0, np.sqrt(3) * T])
    M = expm(np.block([[-A, Bw @ Bw.T], [np.zeros((2, 2)), A.T]]) * dt)
    Ad = M[2:, 2:].T
    Qd = Ad @ M[:2, 2:]
    P = solve_continuous_lyapunov(A, -Bw @ Bw.T)
    rng = np.random.default_rng(seed)
    x = cholesky(P, lower=True) @ rng.standard_normal(2)
    Lq = cholesky(Qd, lower=True)
    w = rng.standard_normal((n, 2))
    out = np.empty(n)
    for k in range(n):
        out[k] = C @ x
        x = Ad @ x + Lq @ w[k]
    return out


# ---------------------------------------------------------------- (b) 2-state plant + (e) JSBSim, same 3-method API
def lin2_matrices(dt=DT, d=None):
    """Continuous (A, B) and ZOH-exact (Ad, Bd) of x = [beta_inertial, r], u = [cmd, v_g/V]:
       beta_dot = Yb_V*(beta - v_g/V) - r + Ycmd_V*cmd ;  r_dot = Nb*(beta - v_g/V) + Nr*r + N_cmd*cmd."""
    d = d or load_derivs()['derivs']
    A = np.array([[d['Yb_V'], -1.0], [d['Nb'], d['Nr']]])
    B = np.array([[-d['Ydr_V'], -d['Yb_V']], [-d['Ndr'], -d['Nb']]])
    E = expm(np.block([[A, B], [np.zeros((2, 4))]]) * dt)
    return A, B, E[:2, :2], E[:2, 2:]


class _Lin2Run:
    def __init__(self, seed, n, dt, turbulence):
        D = load_derivs()
        self.V = D['trim']['vt_mps']
        _, _, self.Ad, self.Bd = lin2_matrices(dt, D['derivs'])
        sig, L = dryden_params(turbulence)
        self.vg = dryden_v(seed, n + 1, dt, sig, L, self.V)
        self.x, self.k, self.dt = np.zeros(2), 0, dt
        self.psi, self.pos = 0.0, np.array([0.0, 0.0, -H_FT * FT])

    def step(self, u):
        r0 = self.x[1]
        self.x = self.Ad @ self.x + self.Bd @ np.array([u, self.vg[self.k] / self.V])
        self.k += 1
        self.psi += 0.5 * (r0 + self.x[1]) * self.dt
        c, s, bv = np.cos(self.psi), np.sin(self.psi), self.V * self.x[0]
        self.pos[:2] += self.dt * np.array([self.V * c - bv * s, self.V * s + bv * c])

    def obs(self):
        return dict(r=float(self.x[1]), p=0.0, beta=float(self.x[0] - self.vg[self.k] / self.V), phi=0.0)

    def rec(self):
        return self.pos.copy(), (0.0, 0.0, self.psi), (0.0, float(self.vg[self.k]), 0.0)


class _JSBSimRun:
    def __init__(self, seed, n, dt, turbulence):
        f = self.f = trimmed_fdm(dt / JSB_SUB)
        f['fcs/yaw-trim-cmd-norm'] = f['fcs/rudder-cmd-norm']   # keep the trim rudder in the trim channel
        f['fcs/rudder-cmd-norm'] = 0.0
        self.da0, self.iphi, self.dt = f['fcs/aileron-cmd-norm'], 0.0, dt
        # FGWinds gets its own RNG (FGWinds::SetRandomSeed). JSBSim 1.3.1 uses std::default_random_engine (minstd_rand0,
        # an LCG with c = 0), which maps seed 0 to the same state as seed 1 (seeds 0 and 1 gave the
        # identical flight). Offset by 1 so every project seed is a distinct stream. The engine and std::normal_distribution
        # are implementation-defined, so a given seed may differ between libc++ (macOS) and libstdc++ (Linux runners);
        # all reported JSBSim runs are made on the Linux runners.
        f['atmosphere/randomseed'] = int(seed) + 1
        f['atmosphere/turb-type'] = 3            # ttMilspec: first-order Markov approximation (Yeager 1998 eqs 30-35), NOT the
        # second-order Dryden v filter of lin2: v correlation time 875 ft/V = 5.2 s vs 1750 ft/V; same sigma and DC level,
        # ~4/3 the lin2 PSD near the Dutch roll; also adds u, w, p, q, r gusts; starts from zero
        f['atmosphere/turbulence/milspec/windspeed_at_20ft_AGL-fps'] = W20_FPS[turbulence]
        f['atmosphere/turbulence/milspec/severity'] = SEVERITY[turbulence]
        self.pos = np.array([0.0, 0.0, -f['position/h-sl-ft'] * FT])

    def step(self, u):
        f = self.f
        f['fcs/rudder-cmd-norm'] = -u                           # the one sign flip (see module docstring)
        phi, p = f['attitude/phi-rad'], f['velocities/p-rad_sec']
        self.iphi += phi * self.dt
        g = ROLL_PID
        f['fcs/aileron-cmd-norm'] = float(np.clip(self.da0 - g['KP'] * phi - g['KD'] * p - g['KI'] * self.iphi, -1, 1))
        for _ in range(JSB_SUB):
            f.run()
        self.pos += self.dt * FT * np.array([f['velocities/v-north-fps'], f['velocities/v-east-fps'], 0.0])
        self.pos[2] = -f['position/h-sl-ft'] * FT

    def obs(self):
        f = self.f
        return dict(r=f['velocities/r-rad_sec'], p=f['velocities/p-rad_sec'], beta=f['aero/beta-rad'],
                    phi=f['attitude/phi-rad'])

    def rec(self):
        f = self.f
        psi = f['attitude/psi-rad']
        n, e, dn = (f['atmosphere/turb-north-fps'] * FT, f['atmosphere/turb-east-fps'] * FT,
                    f['atmosphere/turb-down-fps'] * FT)
        gust = (np.cos(psi) * n + np.sin(psi) * e, -np.sin(psi) * n + np.cos(psi) * e, dn)   # heading frame
        return self.pos.copy(), (f['attitude/phi-rad'], f['attitude/theta-rad'], psi), gust


# ---------------------------------------------------------------- (d) lockstep simulation + metrics
def departed(phi, beta, r, dt, lim=DEPARTURE):
    """True if ANY one of |phi|>60deg, |beta|>20deg, |r|>60deg/s holds continuously for > 1 s
    (each sample stands for one dt interval, so a run of n samples lasts n*dt)."""
    for x, thr in ((phi, lim['phi']), (beta, lim['beta']), (r, lim['r'])):
        run = 0
        for over in np.abs(np.asarray(x)) > thr:
            run = run + 1 if over else 0
            if run * dt > lim['hold_s'] + 1e-9:
                return True
    return False


def simulate(controller, seed, T=60.0, plant='lin2', turbulence='moderate', dt=DT, inject=None):
    """Fly `controller` (reset(seed), step(obs, dt) -> (rudder_cmd, activity)) in lockstep.
    T = 60 s default for both plants: the gust correlation time
    L_v/V = 533 m / 51.4 m/s = 10.4 s, so 60 s spans ~6 correlation times and ~20 Dutch-roll periods.
    inject: optional d(t) added at the actuator (u_plant = sat(u_ctrl + d)); used by loop_margins().
    Returns numpy arrays named as in the recording format (+ 'u_ctrl', 'p', 'phi') and 'metrics'."""
    n = int(round(T / dt))
    sim = {'lin2': _Lin2Run, 'jsbsim': _JSBSimRun}[plant](seed, n, dt, turbulence)
    controller.reset(seed)
    R = {k: np.zeros(n) for k in ('t', 'r', 'beta', 'p', 'phi', 'rudder', 'u_ctrl')}
    R.update(pos_ned_m=np.zeros((n, 3)), euler_rad=np.zeros((n, 3)), gust=np.zeros((n, 3)))
    groups, spikes, u_ctrl, sat = {}, [], 0.0, False
    for k in range(n):
        u = u_ctrl + (inject(k * dt) if inject else 0.0)
        sat |= abs(u) > 1
        u = min(1.0, max(-1.0, u))
        sim.step(u)
        o = sim.obs()
        o['t'] = (k + 1) * dt
        R['t'][k], R['rudder'][k], R['u_ctrl'][k] = o['t'], u, u_ctrl
        for key in ('r', 'beta', 'p', 'phi'):
            R[key][k] = o[key]
        R['pos_ned_m'][k], R['euler_rad'][k], R['gust'][k] = sim.rec()
        u_ctrl, act = controller.step(o, dt)
        u_ctrl = float(u_ctrl)
        for g, v in act.items():
            if g == '_spikes':
                spikes.extend(v)
            else:
                groups.setdefault(g, []).append(v)
    R['groups'] = {g: np.asarray(v) for g, v in groups.items()}
    R['raster'] = dict(neurons=getattr(controller, 'raster_neurons', []), spikes=spikes)
    R['metrics'] = dict(rms_r=float(np.sqrt(np.mean(R['r']**2))), rms_beta=float(np.sqrt(np.mean(R['beta']**2))),
                        max_abs_phi=float(np.max(np.abs(R['phi']))), departed=departed(R['phi'], R['beta'], R['r'], dt),
                        rms_rudder=float(np.sqrt(np.mean(R['rudder']**2))))
    R['meta'] = dict(controller=getattr(controller, 'name', type(controller).__name__), aircraft='c172x', seed=seed,
                     plant=plant, turbulence=('dryden_' + turbulence) if turbulence != 'none' else 'none', dt=dt, T=T,
                     input_saturated=bool(sat))
    return R


# ---------------------------------------------------------------- (f) loop margins by injection at the actuator
def _lockin(x, t, f):
    """Complex amplitude of x at f Hz (x ~ Re(X e^{j 2 pi f t})); mean removed; integer cycles expected."""
    x = np.asarray(x) - np.mean(x)
    return 2 * np.mean(x * np.exp(-2j * np.pi * f * np.asarray(t)))


def margins(freqs, L):
    """GM (at each -180 deg crossing of the unwrapped phase) and PM (at each |L| = 1 crossing), interpolated
    linearly in log-frequency; the worst one of each is reported (inf, f=None if there is no crossing).
    PM = angular distance of L from -1 at the crossing, 180 - |phase wrapped to [-180, 180)|: the usual
    180 + phase for lagging loops, and still meaningful at a washout's low-frequency (phase-lead) crossing.
    Margins say nothing about stability by themselves; the closed-loop simulation does."""
    lf, lm = np.log(freqs), np.log(np.abs(L))
    ph = np.unwrap(np.angle(L))
    pm, gm = [], []
    for i in range(len(freqs) - 1):
        if lm[i] == 0 or lm[i] * lm[i + 1] < 0:
            x = lm[i] / (lm[i] - lm[i + 1])
            pc = ph[i] + x * (ph[i + 1] - ph[i])
            wrapped = (np.degrees(pc) + 180) % 360 - 180            # phase in [-180, 180)
            pm.append((float(180 - abs(wrapped)), float(np.exp(lf[i] + x * (lf[i + 1] - lf[i])))))
        a, b = (ph[i] + np.pi) / (2 * np.pi), (ph[i + 1] + np.pi) / (2 * np.pi)   # -180 deg + k*360 <-> integers
        if np.floor(a) != np.floor(b):
            lvl = max(np.floor(a), np.floor(b))
            x = (lvl - a) / (b - a)
            gm.append((float(-20 * (lm[i] + x * (lm[i + 1] - lm[i])) / np.log(10)),
                       float(np.exp(lf[i] + x * (lf[i + 1] - lf[i])))))
    # a sample lying exactly on -180 deg (e.g. the Nyquist point, where a discrete L is real and negative) is itself a
    # phase crossing; the bracketing test above cannot see it when it is the last point of the grid
    for i in range(len(freqs)):
        if abs(abs((np.degrees(ph[i]) + 180) % 360 - 180) - 180) < 1e-6 and np.real(L[i]) < 0:
            gm.append((float(-20 * lm[i] / np.log(10)), float(freqs[i])))
    pmin = min(pm) if pm else (float('inf'), None)
    gmin = min(gm) if gm else (float('inf'), None)
    return dict(gm_db=gmin[0], f_gm_hz=gmin[1], pm_deg=pmin[0], f_pm_hz=pmin[1], pm_all=pm, gm_all=gm)


def loop_margins(controller, plant='lin2', freqs=None, seed=0, A=0.05, settle=20.0, n_cycles=5, min_window=4.0,
                 dt=DT):
    """Loop broken at the actuator (fixed before data collection): u_plant = d + u_ctrl, d = A sin(2 pi f t),
    turbulence off.
    L(jw) = -U_ctrl/U_plant by lock-in over an integer number of cycles (>= n_cycles and >= min_window s)
    after `settle` s. A in rudder_cmd units: small enough to stay unsaturated, large enough above brain noise.
    settle = 20 s: with 5 s the washout yaw damper's slow closed-loop transient biased L by up to 3.6 %; with
    20 s the error vs the exact discrete model is < 0.3 % at every frequency (baselines.py self-check)."""
    freqs = np.geomspace(0.1, 20, 16) if freqs is None else np.asarray(freqs, float)
    L, sat = [], False
    for f in freqs:
        nc = max(n_cycles, int(np.ceil(min_window * f)))
        nw = int(round(nc / f / dt))
        R = simulate(controller, seed, T=settle + nw * dt, plant=plant, turbulence='none', dt=dt,
                     inject=lambda t, f=f: A * np.sin(2 * np.pi * f * t))
        sat |= R['meta']['input_saturated']
        s = slice(-nw, None)
        L.append(-_lockin(R['u_ctrl'][s], R['t'][s], f) / _lockin(R['rudder'][s], R['t'][s], f))
    L = np.array(L)
    return dict(freqs=freqs, L=L, saturated=bool(sat), A=A, seed=seed, plant=plant, **margins(freqs, L))


# ---------------------------------------------------------------- self-checks
if __name__ == '__main__':
    out = linearize()
    D, d = out, out['derivs']
    chk = {}

    # 1. derivatives agree with the c172x.xml coefficients (JSBSim linearization vs hand calculation)
    print('xml cross-check rel err', out['xml_check']['rel_err'])
    assert all(e < 0.05 for e in out['xml_check']['rel_err'].values())
    print('Dutch roll', {k: (round(v['wn'], 3), round(v['zeta'], 3)) for k, v in out['dutch_roll'].items()})

    # 2. bare-plant eigenvalues of lin2 match the derivatives' analytic wn^2 = Nb + Yb_V*Nr, 2 zeta wn = -(Yb_V+Nr)
    A, B, Ad, Bd = lin2_matrices()
    e = np.linalg.eigvals(A)
    wn, z = abs(e[0]), -e[0].real / abs(e[0])
    assert np.isclose(wn**2, d['Nb'] + d['Yb_V'] * d['Nr']) and np.isclose(2 * z * wn, -(d['Yb_V'] + d['Nr']))
    assert np.allclose(np.sort_complex(np.log(np.linalg.eigvals(Ad)) / DT), np.sort_complex(e))
    chk['lin2_dutch_roll'] = dict(wn=wn, zeta=z)

    # 3. positive cmd -> positive rdot (lin2, and JSBSim 6-DOF)
    assert B[1, 0] > 0 and (Ad @ [0, 0] + Bd @ [0.1, 0])[1] > 0

    class Const:
        name = 'const'
        def __init__(s, u): s.u = u
        def reset(s, seed): pass
        def step(s, obs, dt): return s.u, {}
    r_pos = simulate(Const(0.2), 0, T=0.3, plant='jsbsim', turbulence='none')['r']
    r_neg = simulate(Const(-0.2), 0, T=0.3, plant='jsbsim', turbulence='none')['r']
    r_0 = simulate(Const(0.0), 0, T=0.3, plant='jsbsim', turbulence='none')['r']
    assert r_pos[-1] > r_0[-1] + 1e-3 > r_neg[-1] + 2e-3, (r_pos[-1], r_0[-1], r_neg[-1])
    chk['sign_jsbsim_r_after_0p3s'] = dict(cmd_p02=r_pos[-1], cmd_0=r_0[-1], cmd_m02=r_neg[-1])

    # 4. Dryden: long-run sample variance = sigma_v^2, and the dt-scaling is right (dt = 10 ms and 2 ms)
    sig, L = dryden_params('moderate')
    V = D['trim']['vt_mps']
    for ddt in (0.01, 0.002):
        v = np.concatenate([dryden_v(s, int(3000 / ddt), ddt, sig, L, V) for s in range(4)])  # 4 x 3000 s ~ 1150 T_v
        chk[f'dryden_std_ratio_dt{ddt}'] = float(v.std() / sig)
        assert abs(v.std() / sig - 1) < 0.05, v.std() / sig
    a, b = dryden_v(3, 500, DT, sig, L, V), dryden_v(3, 500, DT, sig, L, V)
    assert np.array_equal(a, b) and not np.array_equal(a, dryden_v(4, 500, DT, sig, L, V))
    chk['dryden'] = dict(sigma_v_mps=sig, sigma_v_fps=sig / FT, L_v_m=L, L_v_ft=L / FT, T_v_s=L / V)

    # 5. departure detector: 1.1 s over threshold fires, 0.9 s does not (each channel)
    z0 = np.zeros(500)
    for key in ('phi', 'beta', 'r'):
        for hold, want in ((1.1, True), (0.9, False)):
            x = z0.copy()
            x[100:100 + int(round(hold / DT))] = DEPARTURE[key] * 1.01
            args = dict(phi=z0, beta=z0, r=z0)
            args[key] = x
            assert departed(dt=DT, **args) == want, (key, hold)

    # 6. lin2 closed loop: injected margins of u = -K r match the exact discrete analytic L(z) = K * P_r(z)
    class RateFB:
        name = 'rate_fb'
        def reset(s, seed): pass
        def step(s, obs, dt): return -3.0 * obs['r'], {}
    M = loop_margins(RateFB(), 'lin2', freqs=np.geomspace(0.1, 30, 14), A=0.05)
    z = np.exp(2j * np.pi * M['freqs'] * DT)
    La = np.array([3.0 * (np.linalg.solve(zz * np.eye(2) - Ad, Bd[:, 0]))[1] for zz in z])
    assert np.max(np.abs(M['L'] / La - 1)) < 0.02, np.abs(M['L'] / La - 1)
    assert not M['saturated']

    # 7. JSBSim: seeded turbulence is deterministic; trim is sufficient for 60 s with the wings-level hold alone
    t0 = time.time()
    R1 = simulate(Const(0.0), 7, T=60.0, plant='jsbsim', turbulence='moderate')
    wall = time.time() - t0
    R2 = simulate(Const(0.0), 7, T=60.0, plant='jsbsim', turbulence='moderate')
    R3 = simulate(Const(0.0), 8, T=60.0, plant='jsbsim', turbulence='moderate')
    assert np.array_equal(R1['gust'], R2['gust']) and np.array_equal(R1['r'], R2['r'])
    assert np.abs(R1['gust'] - R3['gust']).max() > 1.0
    g0, g1 = (simulate(Const(0.0), s, T=5.0, plant='jsbsim', turbulence='moderate')['gust'] for s in (0, 1))
    assert np.abs(g0 - g1).max() > 0.1, 'JSBSim seeds 0 and 1 must be different turbulence streams'
    dz = R1['pos_ned_m'][:, 2] + H_FT * FT
    chk['jsbsim_bare_seed7_moderate_60s'] = dict(
        wall_s=wall, metrics=R1['metrics'], gust_std_mps=R1['gust'].std(0).tolist(),
        max_abs_dalt_m=float(np.abs(dz).max()), max_abs_phi_deg=float(np.degrees(np.abs(R1['phi']).max())))
    print('jsbsim 60 s flight wall', round(wall, 3), 's;', chk['jsbsim_bare_seed7_moderate_60s'])
    assert not R1['metrics']['departed']

    # 8. is "elevator/throttle at trim" sufficient for 60 s? Compare with a pitch-attitude hold (de = 1.0*dtheta + 0.2*q)
    rows = []
    for seed in (0, 100):
        for kth, kq in ((0.0, 0.0), (1.0, 0.2)):
            s = _JSBSimRun(seed, 6000, DT, 'moderate')
            th0, vt, h, r = s.f['attitude/theta-rad'], [], [], []
            for _ in range(6000):
                s.f['fcs/elevator-cmd-norm'] = kth * (s.f['attitude/theta-rad'] - th0) + kq * s.f['velocities/q-rad_sec']
                s.step(0.0)
                vt.append(s.f['velocities/vtrue-kts']), h.append(s.f['position/h-sl-ft']), r.append(s.f['velocities/r-rad_sec'])
            rows.append(dict(seed=seed, theta_hold=[kth, kq], vt_kts_min_max_std=[min(vt), max(vt), float(np.std(vt))],
                             h_ft_min_max=[min(h), max(h)], rms_r=float(np.sqrt(np.mean(np.square(r))))))
    chk['trim_sufficiency_60s_moderate'] = rows
    assert all(abs(a['rms_r'] / b['rms_r'] - 1) < 0.1 for a, b in zip(rows[::2], rows[1::2]))  # pitch hold irrelevant to yaw

    for k, v in list(chk.items()):
        chk[k] = json.loads(json.dumps(v, default=float))
    (RESULTS / 'plant_selfcheck.json').write_text(json.dumps(dict(meta=meta_common(), checks=chk), indent=1))
    print('plant.py self-checks passed ->', DERIVS_JSON.name, 'plant_selfcheck.json')
