"""The spiking fly brain (FlyWire v783 subcircuit, Shiu et al. 2024 LIF model, Brian2).

  uv run python scripts/brain.py --extract [--threshold 5] [--targets g1]   # build data/sub/subcircuit_v783_t5.npz
  uv run python scripts/brain.py --timing                                   # results/brain_timing.json
  uv run python scripts/brain.py --timing-probes                            # results/brain_timing_probes.json (batching, full brain)
  uv run python scripts/brain.py                                            # self-check (~1-2 min)

API
  extract_subcircuit(threshold=5, hops=4, targets='default') -> path, counts
  load_subcircuit(path=SUB_PATH) -> dict(root_id, cell_type, side, pre, post, w, meta)
  run_open_loop(edges, r_of_t, T_s, seed, g=1.0, lesion=None, sub=None, codegen='cython') -> dict(t_bins, group_rates, spikes, neurons, meta)
  FlyController(K, sign=+1, edges=None, g=1.0, lesion=None, codegen='cython', ctrl_dt=0.01, name='fly_real')
      controller interface: reset(seed), step(obs, dt) -> (rudder_cmd, activity); close() after the last step

edges = (pre, post, w): local indices into the subcircuit and signed synapse counts (Excitatory x Connectivity).
nulls.py shuffles return the same tuple. lesion = iterable of local indices whose OUTGOING edges are removed
(identical to Shiu's silence(), which sets those weights to 0).
"""
import argparse, json, re, time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw'
DATA_VERSION = 'flywire_v783'
SRC_RE = r'T[45][a-d]'
# Target set T. 'g1' is the first pre-listed fallback of gate G1 (not triggered): add DNa01 and DNb02 to the readout.
TARGET_RE = {'default': r'DNa02|DNg02_[a-h]', 'g1': r'DNa02|DNg02_[a-h]|DNa01|DNb02'}
SUB_PATH = ROOT / 'data' / 'sub' / 'subcircuit_v783_t5.npz'

# Eye input sign map, fixed before data collection and confirmed by the literature (Maisak 2013; Shinomiya 2019).
# r > 0 is nose-right yaw in deg/s. A cell's rate is r_base + g * max(0, s * r) with s from this dict.
# Nose-right: left eye sees front-to-back -> left T4a/T5a; right eye sees back-to-front -> right T4b/T5b.
YAW_SIGN = {('a', 'left'): +1, ('b', 'left'): -1, ('a', 'right'): -1, ('b', 'right'): +1}

# Shiu et al. 2024 parameters (vendor/shiu/model.py default_params), unchanged.
SHIU = dict(v_0=-52e-3, v_rst=-52e-3, v_th=-45e-3, t_mbr=20e-3, tau=5e-3, t_rfc=2.2e-3, t_dly=1.8e-3, w_syn=0.275e-3)
R_BASE = 10.0   # Hz
G_DEFAULT = 1.0  # Hz per deg/s: 100 deg/s -> +100 Hz
BIN = 5e-3       # s, fixed analysis choice


def group_of(cell_type, side):
    """Readout group label for one neuron, or '' for 'other'. Horizontal T4/T5 (a/b) are 'T4T5_L/R'."""
    s = {'left': 'L', 'right': 'R'}.get(side, 'C')
    if re.fullmatch(r'T[45][ab]', cell_type): return f'T4T5_{s}'
    if re.fullmatch(r'T[45][cd]', cell_type): return f'T4T5cd_{s}'
    for g, pat in [('HS', r'HS[ENS]'), ('VS', r'VS\d+'), ('H2', 'H2'), ('DNa02', 'DNa02'), ('DNg02', r'DNg02_[a-h]'),
                   ('DNa01', 'DNa01'), ('DNb02', 'DNb02')]:
        if re.fullmatch(pat, cell_type): return f'{g}_{s}'
    return ''


# ---------------------------------------------------------------- extraction
def extract_subcircuit(threshold=5, hops=4, targets='default', out=None):
    import pandas as pd, scipy.sparse as sp
    comp = pd.read_csv(RAW / 'Completeness_783.csv', index_col=0)
    ann = pd.read_csv(RAW / 'flywire_annotations.tsv', sep='\t', usecols=['root_id', 'cell_type', 'side'],
                      dtype={'root_id': 'int64'}, low_memory=False).set_index('root_id')
    con = pd.read_parquet(RAW / 'Connectivity_783.parquet',
                          columns=['Presynaptic_Index', 'Postsynaptic_Index', 'Connectivity', 'Excitatory x Connectivity'])
    con = con[con.Connectivity >= threshold]
    N = len(comp)
    root = comp.index.values.astype(np.int64)
    ctype = ann.cell_type.reindex(root).fillna('').to_numpy(dtype='U')
    side = ann.side.reindex(root).fillna('na').to_numpy(dtype='U')
    pre, post = con.Presynaptic_Index.values, con.Postsynaptic_Index.values
    A = sp.csr_matrix((np.ones(len(pre), np.float32), (pre, post)), shape=(N, N))

    def dist(seeds, M):  # BFS hop distance from a seed set, capped at `hops`; M maps frontier -> next
        d = np.full(N, np.inf); d[seeds] = 0; f = seeds.astype(np.float32)
        for k in range(1, hops + 1):
            nxt = (M @ f > 0) & np.isinf(d)
            d[nxt] = k; f = nxt.astype(np.float32)
        return d

    S = np.array([bool(re.fullmatch(SRC_RE, c)) for c in ctype])
    T = np.array([bool(re.fullmatch(TARGET_RE[targets], c)) for c in ctype])
    keep = dist(S, A.T.tocsr()) + dist(T, A) <= hops
    loc = np.full(N, -1); loc[keep] = np.arange(keep.sum())
    e = keep[pre] & keep[post]
    sub = dict(root_id=root[keep], cell_type=ctype[keep], side=side[keep],
               pre=loc[pre[e]].astype(np.int32), post=loc[post[e]].astype(np.int32),
               w=con['Excitatory x Connectivity'].values[e].astype(np.int32))
    meta = dict(threshold=threshold, hops=hops, targets=TARGET_RE[targets], source=SRC_RE, data_version=DATA_VERSION,
                created=datetime.now(timezone.utc).isoformat(), n_neurons=int(keep.sum()), n_edges=int(e.sum()))
    out = Path(out or ROOT / 'data' / 'sub' / f'subcircuit_v783_t{threshold}{"_g1" if targets == "g1" else ""}.npz')
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, meta=json.dumps(meta), **sub)
    counts = describe(sub)
    print(f'subcircuit: {meta["n_neurons"]} neurons, {meta["n_edges"]} edges (threshold {threshold}, <= {hops} hops) -> {out}')
    print(json.dumps(counts, indent=1))
    return out, counts


def describe(sub):
    """Neuron and edge counts by group (T4/T5 per subtype and side, HS/VS/H2, DNs, other)."""
    lab = np.array([f'{c}_{s}' if re.fullmatch(SRC_RE, c) else group_of(c, s) or 'other'
                    for c, s in zip(sub['cell_type'], sub['side'])])
    u, n = np.unique(lab, return_counts=True)
    return dict(neurons={k: int(v) for k, v in zip(u, n)},
                edges_out_of={k: int((lab[sub['pre']] == k).sum()) for k in u},
                edges_into={k: int((lab[sub['post']] == k).sum()) for k in u},
                total_neurons=int(len(lab)), total_edges=int(len(sub['pre'])))


def sim_edges(sub):
    """The edges the network actually simulates: everything except edges INTO T4/T5 (Poisson sources take no
    input). Nulls must be built from these, or the shuffle moves the dropped inputs onto simulated cells
    (an earlier null built that way gave shuffles ~4x the real net inhibition and was superseded)."""
    src = np.array([bool(re.fullmatch(SRC_RE, c)) for c in sub['cell_type']])
    m = ~src[sub['post']]
    return sub['pre'][m], sub['post'][m], sub['w'][m]


def type_classes(sub):
    """Per-neuron (side, cell type) label for the type-preserving null (unannotated cells share a type '')."""
    return np.unique(np.char.add(np.char.add(sub['side'].astype(str), '|'), sub['cell_type'].astype(str)),
                     return_inverse=True)[1]


def load_subcircuit(path=SUB_PATH):
    z = np.load(path)
    return dict(root_id=z['root_id'], cell_type=z['cell_type'], side=z['side'], pre=z['pre'], post=z['post'],
                w=z['w'], meta=json.loads(str(z['meta'])))


# ---------------------------------------------------------------- network
class Brain:
    """One Brian2 network for a subcircuit + edge list. T4/T5 are Poisson sources (pre-registered), the rest is
    Shiu's LIF. The yaw input is a shared variable `r_deg` (closed loop) or a TimedArray (open loop)."""

    def __init__(self, sub, edges=None, g=G_DEFAULT, r_base=R_BASE, lesion=None, r_timed=None, dt=1e-4,
                 codegen='numpy', n_raster_t4t5=20):
        import brian2 as b2
        b2.prefs.codegen.target = codegen
        b2.defaultclock.dt = dt * b2.second
        self.b2, self.sub, self.dt = b2, sub, dt
        ct, sd = sub['cell_type'], sub['side']
        n = len(ct)
        lab = np.array([group_of(str(c), str(s)) for c, s in zip(ct, sd)], dtype=object)
        is_src = np.array([bool(re.fullmatch(SRC_RE, c)) for c in ct])
        s_yaw = np.array([YAW_SIGN.get((c[2:], s), 0) if src else 0 for c, s, src in zip(ct, sd, is_src)], float)

        # Order neurons so every readout group is contiguous. Sources: T4T5_L block then T4T5_R block, so the
        # recorded T4/T5 sample is the last k/2 of L plus first k/2 of R (one contiguous slice, one monitor).
        src_order = sorted(np.flatnonzero(is_src), key=lambda i: ({'T4T5_L': 0, 'T4T5_R': 1}.get(lab[i], 2), lab[i], i))
        groups = sorted({l for l in lab[~is_src] if l})
        lif_order = sorted(np.flatnonzero(~is_src), key=lambda i: (groups.index(lab[i]) if lab[i] else len(groups), i))
        self.n_src, self.n_lif = len(src_order), len(lif_order)
        pos = np.empty(n, int); pos[src_order] = np.arange(self.n_src); pos[lif_order] = np.arange(self.n_lif)
        self.pos, self.is_src = pos, is_src

        ns = dict(r_base=r_base * b2.Hz, g_in=g * b2.Hz, **{k: v * (b2.second if k.startswith('t') else b2.volt)
                                                               for k, v in SHIU.items()})
        if r_timed is not None:
            ns['r_ta'] = r_timed
            src_eqs = 'rate = r_base + g_in * clip(s * r_ta(t), 0, 1e9) : Hz\ns : 1 (constant)'
        else:
            src_eqs = 'rate = r_base + g_in * clip(s * r_deg, 0, 1e9) : Hz\ns : 1 (constant)\nr_deg : 1 (shared)'
        # silenced: lesioned cells never spike (rand() stays the left operand, so the RNG stream is unchanged)
        self.src = b2.NeuronGroup(self.n_src, src_eqs + '\nsilenced : 1 (constant)',
                                  threshold='rand() < rate * dt and silenced == 0', namespace=ns)
        self.src.s = s_yaw[src_order]
        les = np.zeros(n, bool)
        if lesion is not None and len(lesion):
            les[np.fromiter(lesion, int)] = True
        self.src.silenced = les[src_order].astype(float)
        self.lif = b2.NeuronGroup(self.n_lif, '''dv/dt = (v_0 - v + g) / t_mbr : volt (unless refractory)
                                                dg/dt = -g / tau : volt (unless refractory)
                                                rfc : second
                                                silenced : 1 (constant)''',
                                  method='linear', threshold='v > v_th and silenced == 0', reset='v = v_rst; w = 0; g = 0 * mV',
                                  refractory='rfc', namespace=ns)
        self.lif.v = SHIU['v_0'] * b2.volt; self.lif.g = 0 * b2.volt; self.lif.rfc = SHIU['t_rfc'] * b2.second
        self.lif.silenced = les[lif_order].astype(float)

        pre, post, w = (np.asarray(a) for a in (edges if edges is not None else (sub['pre'], sub['post'], sub['w'])))
        m = ~is_src[post]  # Poisson sources take no synaptic input (their LIF dynamics are replaced)
        if lesion is not None and len(lesion):
            m &= ~np.isin(pre, np.fromiter(lesion, int))  # == Shiu silence(): outgoing weights to 0
        m &= w != 0
        self.n_edges_used, self.n_edges_into_src = int(m.sum()), int(is_src[post].sum())
        objs = [self.src, self.lif]
        for pg, flag in [(self.src, True), (self.lif, False)]:
            k = m & (is_src[pre] == flag)
            syn = b2.Synapses(pg, self.lif, 'w : volt', on_pre='g += w', delay=SHIU['t_dly'] * b2.second)
            syn.connect(i=pos[pre[k]], j=pos[post[k]])
            syn.w = w[k] * SHIU['w_syn'] * b2.volt
            objs.append(syn)

        # Readout. Group slices in local LIF / source index space.
        lif_lab, src_lab = lab[lif_order], lab[src_order]
        self.slices = {}
        for G, L in [('lif', lif_lab), ('src', src_lab)]:
            for gname in sorted({l for l in L if l}):
                idx = np.flatnonzero(L == gname)
                assert idx[-1] + 1 - idx[0] == len(idx), gname  # contiguous
                self.slices[gname] = (G, int(idx[0]), int(idx[-1] + 1))
        n_rec = int(np.flatnonzero(lif_lab != '')[-1] + 1) if (lif_lab != '').any() else 0
        self.lif_mon = b2.SpikeMonitor(self.lif[:n_rec])  # all HS/VS/H2/DN cells (+ any extra labelled groups)
        nL = (src_lab == 'T4T5_L').sum(); h = n_raster_t4t5 // 2
        self.src_lo = nL - h
        self.src_mon = b2.SpikeMonitor(self.src[self.src_lo:nL + h])
        self.src_rate = {gn: b2.PopulationRateMonitor(self.src[a:b]) for gn, (G, a, b) in self.slices.items()
                         if G == 'src' and gn.startswith('T4T5_')}
        objs += [self.lif_mon, self.src_mon, *self.src_rate.values()]
        self.net = b2.Network(*objs)
        # Raster neuron table (recording format 'raster.neurons'): recorded LIF cells then sampled T4/T5 cells.
        lif_ids = np.array(lif_order)[:n_rec]; src_ids = np.array(src_order)[self.src_lo:nL + h]
        self.raster_ids = np.concatenate([lif_ids, src_ids])
        self.neurons = [dict(idx=k, local=int(i), type=str(ct[i]), side=str(sd[i])) for k, i in enumerate(self.raster_ids)]
        self.n_rec_lif = n_rec

    def run(self, T_s):
        self.net.run(T_s * self.b2.second, namespace={})

    def spikes(self, t0=0.0):
        """Recorded spikes as array of (t_s, raster_idx), time-sorted, t >= t0."""
        ti, ii = np.asarray(self.lif_mon.t_), np.asarray(self.lif_mon.i)
        ts, is_ = np.asarray(self.src_mon.t_), np.asarray(self.src_mon.i) + self.n_rec_lif
        t, i = np.concatenate([ti, ts]), np.concatenate([ii, is_])
        k = t >= t0; o = np.argsort(t[k], kind='stable')
        return np.stack([t[k][o], i[k][o]], 1)

    def group_rates(self, t0, t1, bin_s=BIN):
        """Mean per-cell rate (Hz) of each readout group in bins over [t0, t1)."""
        edges = np.arange(t0, t1 + 1e-9, bin_s)
        e = edges - self.dt / 2  # samples sit on exact multiples of dt; keep them off the bin edges
        out = {}
        ti, ii = np.asarray(self.lif_mon.t_), np.asarray(self.lif_mon.i)
        for gn, (G, a, b) in self.slices.items():
            if G == 'lif':
                k = (ii >= a) & (ii < b)
                out[gn] = np.histogram(ti[k], e)[0] / ((b - a) * bin_s)
            elif gn in self.src_rate:  # PopulationRateMonitor: per-dt population rate, average into bins
                m = self.src_rate[gn]
                out[gn] = np.histogram(m.t_, e, weights=m.rate_)[0] / np.maximum(np.histogram(m.t_, e)[0], 1)
        return edges[:-1], out


def run_open_loop(edges, r_of_t, T_s, seed, g=G_DEFAULT, lesion=None, sub=None, codegen='cython', dt=1e-4,
                  r_base=R_BASE):
    """r_of_t: callable t_s(array) -> deg/s, or an array sampled at `dt`. Returns 5 ms group rates and spikes."""
    import brian2 as b2
    sub = sub or load_subcircuit()
    t = np.arange(int(round(T_s / dt)) + 1) * dt
    r = r_of_t(t) if callable(r_of_t) else np.asarray(r_of_t, float)
    ta = b2.TimedArray(r, dt=dt * b2.second)
    b2.seed(seed)
    brain = Brain(sub, edges, g=g, r_base=r_base, lesion=lesion, r_timed=ta, dt=dt, codegen=codegen)
    brain.run(T_s)
    tb, rates = brain.group_rates(0.0, T_s)
    return dict(t_bins=tb, group_rates=rates, spikes=brain.spikes(), neurons=brain.neurons,
                meta=dict(seed=seed, g=g, r_base=r_base, T_s=T_s, dt=dt, lesion_n=len(lesion or []),
                          n_edges=brain.n_edges_used, data_version=DATA_VERSION, codegen=codegen))


class FlyController:
    """Fly-brain controller (reset/step interface, see plant.simulate). Lockstep: step(obs, dt) sets the eye input
    from obs['r'] (rad/s -> deg/s), the brain runs dt, and the readout is taken over that dt.
    rudder = clip(sign*K*(D - b), -1, 1), D = DNa02_R - DNa02_L mean rate (Hz) over the step, b = mean D over a 1 s no-rotation warm-up at reset.
    sign=+1 (pre-registered, biology): D > 0 commands a NOSE-RIGHT yaw moment; the plant must map
    rudder_cmd > 0 to a nose-right moment.

    Speed design (results/brain_timing.json): repeated net.run(10 ms) costs 44-103 ms of Brian2 overhead per call,
    so the brain runs as ONE net.run in a worker thread. A NetworkOperation at every control boundary hands the
    window's activity to step() and blocks until step() hands back the next obs. The caller sees plain step().

    Readout conditioning (fixed before data collection): cmd = clip(sign*K*washout(D - b), -1, 1) with the SAME
    1 s washout tau s/(tau s + 1) as baselines.YawDamper, so the two differ only in sensor + processing."""

    def __init__(self, K, sign=+1, edges=None, g=G_DEFAULT, lesion=None, sub=None, codegen='cython', dt=1e-4,
                 ctrl_dt=0.01, name='fly_real', record_raster=True, warmup_s=1.0, washout_tau=1.0):
        self.name, self.K, self.sign, self.ctrl_dt, self.warmup_s = name, float(K), sign, ctrl_dt, warmup_s
        self.washout_tau = washout_tau
        self.sub = sub or load_subcircuit()
        self.kw = dict(edges=edges, g=g, lesion=lesion, dt=dt, codegen=codegen)
        self.record_raster = record_raster
        self.brain = self._thread = None

    def reset(self, seed):
        import brian2 as b2, queue, threading
        self.close()
        b2.seed(seed)
        br = self.brain = Brain(self.sub, **self.kw)  # rebuilt per flight: fresh state, deterministic per seed
        br.src.r_deg = 0.0
        br.run(self.warmup_s)
        _, r = br.group_rates(0.0, self.warmup_s, self.warmup_s)
        self.b = float(r['DNa02_R'][0] - r['DNa02_L'][0])
        V = lambda mon, v: mon.variables[v].get_value  # live view of a monitor's dynamic array (no copy)
        self._get = dict(lt=V(br.lif_mon, 't'), li=V(br.lif_mon, 'i'), st=V(br.src_mon, 't'), si=V(br.src_mon, 'i'),
                         **{g: V(m, 'rate') for g, m in br.src_rate.items()})
        self._n = dict(l=len(self._get['lt']()), s=len(self._get['st']()), **{g: len(self._get[g]()) for g in br.src_rate})
        self._obs, self._act = queue.Queue(1), queue.Queue(1)
        self._first = True
        self._lp = 0.0  # washout low-pass state
        self._prev = None  # last completed window (causal readout)
        self.raster_neurons = br.neurons

        def boundary():  # runs inside net.run at every ctrl_dt boundary (worker thread)
            if not self._first:
                self._act.put(self._window())
            self._first = False
            obs = self._obs.get()
            if obs is None:
                br.net.stop(); return
            br.src.r_deg = float(np.degrees(obs['r']))
        br.net.add(b2.NetworkOperation(boundary, dt=self.ctrl_dt * b2.second, when='start'))

        def worker():
            try:
                br.run(1e6)  # ends via net.stop() from close()
            except BaseException as e:  # surface errors in step() instead of hanging
                self._act.put(e)
        self._thread = threading.Thread(target=worker, daemon=True); self._thread.start()

    def _window(self):
        br, G, n, dt = self.brain, self._get, self._n, self.ctrl_dt
        lt, li = G['lt'](), G['li']()
        new_i = li[n['l']:]
        act = {g: float(np.count_nonzero((new_i >= a) & (new_i < b)) / ((b - a) * dt))
               for g, (kind, a, b) in br.slices.items() if kind == 'lif'}
        for g in br.src_rate:
            rr = G[g](); act[g] = float(np.mean(rr[n[g]:])) if len(rr) > n[g] else 0.0; n[g] = len(rr)
        if self.record_raster:  # activity['_spikes']: (t_s in plant time = brain time - warm-up, raster idx)
            st, si = G['st'](), G['si']()
            act['_spikes'] = sorted([(float(t) - self.warmup_s, int(i)) for t, i in zip(lt[n['l']:], new_i)] +
                                    [(float(t) - self.warmup_s, int(i) + br.n_rec_lif)
                                     for t, i in zip(st[n['s']:], si[n['s']:])])
        n['l'], n['s'] = len(lt), len(G['st']())
        return act

    def step(self, obs, dt):
        assert self._thread is not None, 'call reset(seed) first'
        assert abs(dt - self.ctrl_dt) < 1e-12, f'brain built for ctrl_dt={self.ctrl_dt}, got {dt}'
        self._obs.put(obs)
        act_new = self._act.get(timeout=600)
        if isinstance(act_new, BaseException):
            raise act_new
        # Causal readout: the command held over the next interval may use only spikes that
        # already happened, i.e. the window that ended at this obs. act_new (the window just simulated with this
        # obs as input) is used at the NEXT step. Adds one control step (10 ms) of transport delay.
        act, self._prev = self._prev, act_new
        if act is None:
            return 0.0, {k: (0.0 if k != '_spikes' else []) for k in act_new}
        x = act['DNa02_R'] - act['DNa02_L'] - self.b
        a = np.exp(-dt / self.washout_tau)  # same discrete washout as baselines.YawDamper
        self._lp = a * self._lp + (1 - a) * x
        return float(np.clip(self.sign * self.K * (x - self._lp), -1, 1)), act

    def close(self):
        """Stop the brain thread (called by reset; call it after the last step of a flight)."""
        if self._thread is not None and self._thread.is_alive():
            self._obs.put(None)  # the worker is always parked on _obs.get() between steps
            self._thread.join(timeout=60)
        self._thread = None


# ---------------------------------------------------------------- timing
def timing(T_s=1.0, codegens=('numpy', 'cython')):
    import brian2 as b2
    sub = load_subcircuit()
    res = dict(meta=dict(seed=0, data_version=DATA_VERSION, created=datetime.now(timezone.utc).isoformat(),
                         subcircuit=str(SUB_PATH.relative_to(ROOT)), n_neurons=len(sub['cell_type']),
                         n_edges=len(sub['pre']), dt_s=1e-4, machine='Apple M4 (dev Mac)'))
    for cg in codegens:
        b2.seed(0)
        t = time.time(); br = Brain(sub, codegen=cg); br.run(0.01); t_build = time.time() - t  # incl. compile
        t = time.time(); br.run(T_s); t_run = (time.time() - t) / T_s
        br.src.r_deg = 0.0
        t = time.time()
        for _ in range(50): br.run(0.01)
        t_step = (time.time() - t) / 50
        res[cg] = dict(build_plus_first_run_s=round(t_build, 2), wall_s_per_sim_s=round(t_run, 3),
                       wall_s_per_10ms_step_repeated_run=round(t_step, 4),
                       per_call_overhead_s=round(t_step - 0.01 * t_run, 4))
        # Single net.run with the per-10ms work in a network_operation (inverted control), for comparison.
        b2.seed(0); br2 = Brain(sub, codegen=cg); br2.run(0.01)
        calls = []
        op = b2.NetworkOperation(lambda: (calls.append(1), setattr(br2.src, 'r_deg', 0.0)), dt=10 * b2.ms)
        br2.net.add(op)
        t = time.time(); br2.run(0.5); res[cg]['wall_s_per_10ms_step_network_operation'] = round((time.time() - t) / 50, 4)
        # The shipped FlyController (threaded single net.run), 3 s of 10 ms steps with a sinusoidal r.
        fc = FlyController(K=0.01, sub=sub, codegen=cg); fc.reset(0)
        t = time.time()
        for k in range(300): fc.step({'t': k * 0.01, 'r': np.radians(100 * np.sin(2 * np.pi * k * 0.01))}, 0.01)
        res[cg]['flycontroller_wall_s_per_sim_s'] = round((time.time() - t) / 3.0, 3); fc.close()
        print(cg, res[cg], flush=True)
    # Raw 1 Hz smoke observation (not the Phase 1 analysis): 1 s warm-up at r=0 then 5 cycles at 100 deg/s.
    from scipy.signal import coherence
    f, T0 = 1.0, 1.0
    o = run_open_loop(None, lambda t: np.where(t >= T0, 100 * np.sin(2 * np.pi * f * (t - T0)), 0.0), T0 + 5, seed=0,
                      sub=sub, codegen=codegens[-1])
    tb, gr = o['t_bins'], o['group_rates']; k = tb >= T0
    D, r = (gr['DNa02_R'] - gr['DNa02_L'])[k], 100 * np.sin(2 * np.pi * f * (tb[k] + BIN / 2 - T0))
    z = 2 * np.mean(D * np.exp(-2j * np.pi * f * (tb[k] + BIN / 2 - T0)))  # lock-in vs sin -> phase of D re r
    fc, C = coherence(r, D, fs=1 / BIN, nperseg=int(round(1 / (f * BIN))))
    res['smoke_1hz_seed0'] = dict(DNa02_L_mean_hz=float(gr['DNa02_L'][k].mean()), DNa02_R_mean_hz=float(gr['DNa02_R'][k].mean()),
                                  baseline_delta_hz=float((gr['DNa02_R'] - gr['DNa02_L'])[~k].mean()),
                                  lockin_amp_hz=float(abs(z)), lockin_phase_deg_vs_r=float(np.degrees(np.angle(z * 1j))),
                                  coherence_at_1hz=float(C[np.argmin(abs(fc - f))]))
    print(res['smoke_1hz_seed0'])
    return res


def timing_probes(r_deg=50.0, batches=(1, 4, 16)):
    """(1) B block-diagonal copies of the subcircuit in one network (copies k>0 unlabelled; timing only).
    (2) The full v783 brain (all Shiu edges, no threshold) in the same Brain class. Both cython, constant r_deg."""
    import brian2 as b2, pandas as pd
    sub = load_subcircuit(); n = len(sub['cell_type'])
    src = np.array([bool(re.fullmatch(SRC_RE, c)) for c in sub['cell_type']])
    res = dict(meta=dict(seed=0, r_deg=r_deg, codegen='cython', data_version=DATA_VERSION,
                         created=datetime.now(timezone.utc).isoformat(), machine='Apple M4 (dev Mac)'), batch={})

    def clock(s):
        b2.seed(0); t = time.time(); br = Brain(s, codegen='cython'); br.run(0.01); tb = time.time() - t
        br.src.r_deg = r_deg; t = time.time(); br.run(1.0)
        return round(tb, 2), round(time.time() - t, 3), br.n_edges_used
    for B in batches:
        bs = dict(cell_type=np.concatenate([sub['cell_type']] + [np.where(src, sub['cell_type'], 'x')] * (B - 1)),
                  side=np.tile(sub['side'], B), w=np.tile(sub['w'], B),
                  pre=np.concatenate([sub['pre'] + k * n for k in range(B)]),
                  post=np.concatenate([sub['post'] + k * n for k in range(B)]))
        _, w, _ = clock(bs)
        res['batch'][B] = dict(wall_s_per_sim_s=w, wall_s_per_copy_sim_s=round(w / B, 3)); print(B, res['batch'][B], flush=True)
    comp = pd.read_csv(RAW / 'Completeness_783.csv', index_col=0)
    ann = pd.read_csv(RAW / 'flywire_annotations.tsv', sep='\t', usecols=['root_id', 'cell_type', 'side'],
                      dtype={'root_id': 'int64'}, low_memory=False).set_index('root_id')
    con = pd.read_parquet(RAW / 'Connectivity_783.parquet', columns=['Presynaptic_Index', 'Postsynaptic_Index',
                                                                     'Excitatory x Connectivity'])
    root = comp.index.values.astype(np.int64)
    full = dict(cell_type=ann.cell_type.reindex(root).fillna('').to_numpy(dtype='U'),
                side=ann.side.reindex(root).fillna('na').to_numpy(dtype='U'), pre=con.Presynaptic_Index.values,
                post=con.Postsynaptic_Index.values, w=con['Excitatory x Connectivity'].values)
    tb, w, ne = clock(full)
    res['full_brain'] = dict(n_neurons=len(root), n_edges_used=ne, build_s=tb, wall_s_per_sim_s=w,
                             ratio_vs_subcircuit=round(w / res['batch'][batches[0]]['wall_s_per_sim_s'], 2))
    print(res['full_brain'])
    return res


def _sine(f, A=100.0):
    return lambda t: A * np.sin(2 * np.pi * f * t)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--extract', action='store_true')
    ap.add_argument('--threshold', type=int, default=5)
    ap.add_argument('--targets', default='default', choices=list(TARGET_RE))
    ap.add_argument('--timing', action='store_true')
    ap.add_argument('--timing-probes', action='store_true')
    a = ap.parse_args()
    if a.extract:
        extract_subcircuit(a.threshold, targets=a.targets)
    elif a.timing:
        out = ROOT / 'results' / 'brain_timing.json'
        out.write_text(json.dumps(timing(), indent=1)); print('->', out)
    elif a.timing_probes:
        out = ROOT / 'results' / 'brain_timing_probes.json'
        out.write_text(json.dumps(timing_probes(), indent=1)); print('->', out)
    else:  # self-check
        import pandas as pd
        sub = load_subcircuit()
        assert sub['meta']['data_version'] == DATA_VERSION
        # 1. every HS/VS cell with a direct (>=threshold) edge to DNa02 is in the subcircuit
        con = pd.read_parquet(RAW / 'Connectivity_783.parquet')
        con = con[con.Connectivity >= sub['meta']['threshold']]
        ann = pd.read_csv(RAW / 'flywire_annotations.tsv', sep='\t', usecols=['root_id', 'cell_type'], low_memory=False)
        ct = dict(zip(ann.root_id, ann.cell_type.fillna('')))
        d = con[con.Presynaptic_ID.map(lambda x: bool(re.fullmatch(r'HS[ENS]|VS\d+', ct.get(x, ''))))
                & con.Postsynaptic_ID.map(lambda x: ct.get(x) == 'DNa02')]
        assert len(d) and set(d.Presynaptic_ID) <= set(sub['root_id']), 'HS/VS->DNa02 cells missing'
        assert {'DNa02'} <= set(sub['cell_type'])
        # 2. input sign map: left T4a > left T4b under r > 0, reversed under r < 0
        b0 = Brain(sub, r_timed=None)
        si = {(c, sd): b0.src.s[b0.pos[i]] for i, (c, sd) in enumerate(zip(sub['cell_type'], sub['side'])) if b0.is_src[i]}
        rate = lambda c, sd, r: R_BASE + G_DEFAULT * max(0.0, si[(c, sd)] * r)
        assert rate('T4a', 'left', 100) == 110 and rate('T4b', 'left', 100) == 10 and rate('T4b', 'right', 100) == 110
        assert rate('T4b', 'left', -100) == 110 and rate('T5c', 'left', 100) == 10
        # 3. 2 s open loop, 1 Hz, 100 deg/s: DNa02 fires; determinism per seed
        t = time.time()
        o1 = run_open_loop(None, _sine(1.0), 2.0, seed=1, sub=sub, codegen='numpy')
        print(f'open loop 2 s: {time.time() - t:.1f} s wall')
        o2 = run_open_loop(None, _sine(1.0), 2.0, seed=1, sub=sub, codegen='numpy')
        gr = o1['group_rates']
        print({k: round(float(v.mean()), 2) for k, v in gr.items()})
        assert gr['DNa02_L'].mean() + gr['DNa02_R'].mean() > 0, 'DNa02 silent'
        assert np.array_equal(o1['spikes'], o2['spikes']), 'not deterministic'
        # Poisson source measured rates follow the commanded drive (horizontal block mean ~ r_base + g*|r|/2)
        assert 20 < gr['T4T5_L'].mean() < 60, gr['T4T5_L'].mean()
        # 4. FlyController lockstep (threaded): interface shapes, determinism, response to rotation
        def fly(seed, n=30):
            fc = FlyController(K=0.01, sub=sub, codegen='numpy'); fc.reset(seed)
            out = [fc.step({'t': k * 0.01, 'r': np.radians(100.0), 'p': 0.0, 'beta': 0.0, 'phi': 0.0}, 0.01)
                   for k in range(n)]
            fc.close(); assert fc._thread is None
            return out
        t = time.time(); f1, f2 = fly(3), fly(3)
        print(f'closed loop 2 x 0.3 s: {time.time() - t:.1f} s wall')
        u, act = f1[0]
        assert -1 <= u <= 1 and {'DNa02_L', 'DNa02_R', 'HS_L', 'T4T5_L'} <= set(act) and isinstance(act['_spikes'], list)
        assert [x[0] for x in f1] == [x[0] for x in f2] and f1[-1][1]['_spikes'] == f2[-1][1]['_spikes'], 'closed loop not deterministic'
        # r = +100 deg/s drives left T4a/T5a + right T4b/T5b: each horizontal block's mean ~ 10 + 100/2 = 60 Hz
        assert 40 < np.mean([a['T4T5_L'] for _, a in f1]) < 80
        print('self-check OK')
