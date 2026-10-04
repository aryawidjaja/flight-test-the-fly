"""Null wirings and lesion sets.

Edges are the tuple (pre, post, w) that scripts/brain.py uses: local neuron indices into the subcircuit and
signed synapse counts. A dict-like with keys pre/post/w (load_subcircuit() output or the raw npz) also works.
Every function returns that tuple and is deterministic for a given seed.

- shuffle(edges, seed): Maslov-Sneppen double-edge swaps of POST endpoints. pre and w never move, so
  each neuron's out-degree, its outgoing weight multiset and its sign (Dale's law) are kept by construction;
  swapping posts keeps every in-degree. Swaps that would make a self-loop or a duplicate pair are rejected.
- random_sparse(n_nodes, n_edges, weights, seed): Erdos-Renyi G(N, E) null with the same weight multiset.
  It does NOT keep degrees or Dale's law (that is the point of this weaker null).
- lesion_sets(n, kind, fraction, seed, meta, edges): neuron indices to silence for Phase 3.

Pre-registered lesion choices (fixed 2026-10-03, before any lesion run):
- 'random': sampled from ALL n subcircuit neurons, T4/T5 Poisson sources included (silencing a source = its
  spikes get zero outgoing weight). Nested across fractions for one seed: the set is the first round(f*n)
  entries of one seeded permutation, so the 10% set contains the 5% set, and so on. This pairs the fractions
  and lowers the variance of the departure curve.
- 'hubs_topk': k = round(fraction*n), i.e. the same counts as the random lesions; default fractions are
  .05/.10/.20 (HUB_FRACTIONS). Ranked by directed, unweighted betweenness on the wiring being lesioned
  (each shuffle gets its own hubs). Exact Brandes when n <= BC_SOURCES (expected for the subcircuit),
  otherwise Brandes with BC_SOURCES uniformly sampled sources (seed 0). Ties broken by total degree, then
  index. Total degree is NOT used as a proxy: on a proxy subcircuit its top-5% set overlaps betweenness's
  by only 58%.
- 'all_HS' (HSS/HSE/HSN), 'T4_only' (T4a-d), 'T5_only' (T5a-d): fraction is ignored; meta['cell_type'] needed.

Run: uv run python scripts/nulls.py   (self-check; also checks the real data/sub file if present)
"""
import hashlib
import re
import time

import numpy as np
import scipy.sparse as sp

FRACTIONS = (0.0, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8)
HUB_FRACTIONS = (0.05, 0.1, 0.2)
BC_SOURCES = 32768  # exact Brandes up to this many neurons (~65 s on a 20.6k-neuron, 284k-edge proxy, M4)
TYPE_PATTERNS = {'all_HS': r'HS[ENS]', 'T4_only': r'T4[a-d]', 'T5_only': r'T5[a-d]'}


def _edges(edges):
    if isinstance(edges, tuple):
        pre, post, w = edges
    else:
        pre, post, w = edges['pre'], edges['post'], edges['w']
    return np.asarray(pre, np.int64), np.asarray(post, np.int64), np.asarray(w)


def _check(pre, post):
    n = int(max(pre.max(), post.max())) + 1
    if np.any(pre == post):
        raise ValueError(f'{int(np.sum(pre == post))} self-loops in input; drop them first')
    if len(np.unique(pre * n + post)) != len(pre):
        raise ValueError('duplicate (pre, post) pairs in input; merge them first')
    return n


def shuffle(edges, seed, swaps_per_edge=10, return_stats=False, classes=None):
    """Degree- and sign-preserving shuffle -> (pre, post, w); pre and w equal the input, only post changes.
    Stops after swaps_per_edge * E successful swaps. return_stats=True -> ((pre, post, w), stats).
    classes: optional per-neuron labels; a swap is allowed only between edges whose posts share a label, so each
    pre neuron keeps its number of edges into every class (e.g. (side, cell type): a type-preserving null)."""
    pre, post0, w = _edges(edges)
    post = post0.copy()
    n, E = _check(pre, post), len(pre)
    rng = np.random.default_rng(seed)
    target, done, tried, t0 = swaps_per_edge * E, 0, 0, time.time()
    keys = np.sort(pre * n + post)
    ce = None if classes is None else np.asarray(classes)[post0]  # an edge's post class never changes
    stall = 0
    while done < target and stall < 50:
        # one batch = a random perfect matching of the edges, so no edge is in two proposals; with classes, the
        # random order is stably grouped by class so pairs fall within a class (boundary pairs get rejected)
        perm = rng.permutation(E)
        if ce is not None:
            perm = perm[np.argsort(ce[perm], kind='stable')]
        perm = perm[: 2 * min(E // 2, target - done)]
        a, b = perm[0::2], perm[1::2]
        new_a, new_b = pre[a] * n + post[b], pre[b] * n + post[a]
        ok = (pre[a] != post[b]) & (pre[b] != post[a]) & (post[a] != post[b])
        if ce is not None:
            ok &= ce[a] == ce[b]
        # reject if a new pair already exists (checked against the pre-batch set: conservative but exact)
        for k in (new_a, new_b):
            i = np.minimum(np.searchsorted(keys, k), E - 1)
            ok &= keys[i] != k
        # reject if two accepted proposals in this batch would create the same pair
        cand = np.concatenate([new_a[ok], new_b[ok]])
        u, inv, cnt = np.unique(cand, return_inverse=True, return_counts=True)
        clash = cnt[inv] > 1
        m = ok.sum()
        ok[np.flatnonzero(ok)[clash[:m] | clash[m:]]] = False
        post[a[ok]], post[b[ok]] = post[b[ok]], post[a[ok]].copy()
        keys = np.sort(pre * n + post)
        done += int(ok.sum())
        tried += len(a)
        stall = stall + 1 if not ok.any() else 0
    out = (pre.copy(), post, w.copy())
    if return_stats:
        kout, kin = np.bincount(pre, minlength=n), np.bincount(post, minlength=n)
        # chance level: expected overlap of a fully mixed configuration-model graph, P(i->j) ~ kout_i kin_j / E
        chance = float(np.minimum(1, kout[pre] * kin[post0] / E).mean())
        return out, {'n_edges': E, 'swaps': done, 'proposed': tried, 'accept_rate': done / max(tried, 1),
                     'frac_unchanged': float(np.isin(pre * n + post0, keys).mean()), 'frac_unchanged_chance': chance,
                     'seconds': round(time.time() - t0, 2)}
    return out


def random_sparse(n_nodes, n_edges, weights, seed):
    """G(N, E): E distinct directed non-self pairs drawn uniformly, weights randomly permuted onto them."""
    rng = np.random.default_rng(seed)
    k = rng.choice(n_nodes * (n_nodes - 1), size=n_edges, replace=False)
    pre, j = k // (n_nodes - 1), k % (n_nodes - 1)
    post = j + (j >= pre)  # skip the diagonal
    return pre, post, rng.permutation(np.asarray(weights))


def betweenness(pre, post, n, n_sources=BC_SOURCES, seed=0, batch=64):
    """Directed unweighted Brandes betweenness, batched over sources with sparse matmuls. Exact when
    n <= n_sources, else estimated from n_sources sampled sources and rescaled by n / n_sources."""
    A = sp.csr_matrix((np.ones(len(pre)), (pre, post)), shape=(n, n))
    At = A.T.tocsr()
    src = np.arange(n) if n <= n_sources else np.sort(np.random.default_rng(seed).choice(n, n_sources, False))
    bc = np.zeros(n)
    for s in np.array_split(src, max(1, int(np.ceil(len(src) / batch)))):
        K, cols = len(s), np.arange(len(s))
        dist = np.full((n, K), -1, np.int32)
        sigma = np.zeros((n, K))
        dist[s, cols], sigma[s, cols] = 0, 1.0
        d = 0
        while True:  # BFS by levels, counting shortest paths
            nxt = At @ np.where(dist == d, sigma, 0.0)
            new = (nxt > 0) & (dist == -1)
            if not new.any():
                break
            dist[new], sigma[new] = d + 1, nxt[new]
            d += 1
        delta = np.zeros((n, K))
        for lev in range(d, 0, -1):  # dependency accumulation, deepest level first
            coef = np.where(dist == lev, (1 + delta) / np.where(sigma > 0, sigma, 1), 0.0)
            back = A @ coef
            m = dist == lev - 1
            delta[m] += sigma[m] * back[m]
        delta[s, cols] = 0
        bc += delta.sum(1)
    return bc * (n / len(src))


_BC_CACHE = {}


def _hub_order(edges, n):
    pre, post, _ = _edges(edges)
    key = hashlib.sha1(pre.tobytes() + post.tobytes() + str(n).encode()).hexdigest()
    if key not in _BC_CACHE:  # ponytail: in-process cache; save betweenness() output to disk if reruns are slow
        bc = betweenness(pre, post, n)
        deg = np.bincount(pre, minlength=n) + np.bincount(post, minlength=n)
        _BC_CACHE[key] = np.lexsort((np.arange(n), -deg, -bc))
    return _BC_CACHE[key]


def lesion_sets(n, kind, fraction=0.0, seed=0, meta=None, edges=None):
    """Sorted int array of local neuron indices to silence. meta: the subcircuit dict (needs 'cell_type' for the
    type kinds). 'hubs_topk' ranks THE WIRING BEING LESIONED: pass edges=(pre, post, w) of a shuffle; default
    is meta's own (real) edges."""
    if kind == 'random':
        idx = np.random.default_rng(seed).permutation(n)[: int(round(fraction * n))]
    elif kind == 'hubs_topk':
        idx = _hub_order(meta if edges is None else edges, n)[: int(round(fraction * n))]
    elif kind in TYPE_PATTERNS:
        ct = np.asarray(meta['cell_type']).astype(str)
        idx = np.flatnonzero([re.fullmatch(TYPE_PATTERNS[kind], c) is not None for c in ct])
    else:
        raise ValueError(f'unknown lesion kind {kind!r}')
    return np.sort(idx).astype(np.int64)


def _synthetic(n=2000, E=40000, seed=1):
    """Heavy-tailed directed graph with per-pre signs, as a stand-in for the subcircuit."""
    rng = np.random.default_rng(seed)
    p_out, p_in = rng.pareto(1.5, n) + 1, rng.pareto(1.5, n) + 1
    pre = rng.choice(n, 3 * E, p=p_out / p_out.sum())
    post = rng.choice(n, 3 * E, p=p_in / p_in.sum())
    k = np.unique(pre[pre != post] * n + post[pre != post])
    k = rng.permutation(k)[:E]
    pre, post = k // n, k % n
    sign = np.where(rng.random(n) < 0.6, 1, -1)
    return pre, post, sign[pre] * rng.integers(5, 60, len(pre))


def _assert_valid_shuffle(g, s):
    (gp, gq, gw), (sp_, sq, sw) = _edges(g), _edges(s)
    n = int(max(gp.max(), gq.max())) + 1
    for a, b in ((gp, sp_), (gq, sq)):
        assert np.array_equal(np.bincount(a, minlength=n), np.bincount(b, minlength=n)), 'degree changed'
    assert np.array_equal(gp, sp_) and np.array_equal(gw, sw)  # weights stay with their pre: multiset + Dale kept
    assert not np.any(sp_ == sq), 'self-loop'
    assert len(np.unique(sp_ * n + sq)) == len(sp_), 'duplicate pair'


if __name__ == '__main__':
    import os
    g = _synthetic()
    s, st = shuffle(g, 0, return_stats=True)
    _assert_valid_shuffle(g, s)
    print('synthetic', st)
    assert st['frac_unchanged'] < 2 * st['frac_unchanged_chance'] + 0.01, 'shuffle is not mixing'
    assert np.array_equal(shuffle(g, 0)[1], s[1]) and not np.array_equal(shuffle(g, 1)[1], s[1])
    sg = np.zeros(2000); sg[s[0]] = np.sign(s[2])
    assert np.all(np.sign(s[2]) == sg[s[0]])  # one sign per pre neuron (Dale) after shuffling

    rp, rq, rw = random_sparse(2000, len(g[2]), g[2], 0)
    assert len(np.unique(rp * 2000 + rq)) == len(rw) and not np.any(rp == rq) and rq.max() < 2000
    assert np.array_equal(np.sort(rw), np.sort(g[2]))

    # Brandes on graphs with known answers: directed path 0..4 has bc(i) = i*(4-i); diamond splits 0.5/0.5
    assert np.allclose(betweenness(np.arange(4), np.arange(1, 5), 5), [0, 3, 4, 3, 0])
    assert np.allclose(betweenness(np.array([0, 0, 1, 2]), np.array([1, 2, 3, 3]), 4), [0, .5, .5, 0])
    gs = _synthetic(600, 6000, 2)  # the sampled estimate ranks hubs like the exact one
    ex, est = betweenness(gs[0], gs[1], 600), betweenness(gs[0], gs[1], 600, n_sources=200)
    top = lambda x: set(np.argsort(-x)[:30])
    assert len(top(ex) & top(est)) >= 20, len(top(ex) & top(est))

    ls = [lesion_sets(2000, 'random', f, 3) for f in FRACTIONS]
    assert all(np.isin(a, b).all() for a, b in zip(ls, ls[1:])) and len(ls[0]) == 0 and len(ls[-1]) == 1600
    assert not np.array_equal(ls[2], lesion_sets(2000, 'random', 0.1, 4))
    meta = dict(pre=g[0], post=g[1], w=g[2], cell_type=np.array(['T4a', 'T5b', 'HSS', 'HSE', 'T4d', 'VS1'] + ['x'] * 1994))
    assert list(lesion_sets(2000, 'all_HS', meta=meta)) == [2, 3]
    assert list(lesion_sets(2000, 'T4_only', meta=meta)) == [0, 4] and list(lesion_sets(2000, 'T5_only', meta=meta)) == [1]
    h = lesion_sets(2000, 'hubs_topk', 0.05, meta=meta)
    assert len(h) == 100 and np.isin(lesion_sets(2000, 'hubs_topk', 0.02, meta=meta), h).all()
    assert not np.array_equal(h, lesion_sets(2000, 'hubs_topk', 0.05, meta=meta, edges=s))  # shuffle has own hubs

    sub = 'data/sub/subcircuit_v783_t5.npz'
    if os.path.exists(sub):  # the subcircuit written by brain.py --extract
        z = np.load(sub)
        zp, zq, zw = _edges(z)
        pos, neg = np.bincount(zp, zw > 0), np.bincount(zp, zw < 0)
        print('real subcircuit: Dale violations (pre with mixed signs):', int(((pos > 0) & (neg > 0)).sum()),
              '| zero weights:', int((zw == 0).sum()))
        sr, st = shuffle(z, 0, return_stats=True)
        _assert_valid_shuffle(z, sr)
        print('real subcircuit shuffle seed 0:', st)
        assert st['frac_unchanged'] < 2 * st['frac_unchanged_chance'] + 0.01
    print('nulls.py self-check OK')
