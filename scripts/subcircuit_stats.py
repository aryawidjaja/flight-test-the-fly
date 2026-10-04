"""Facts about the extracted subcircuit: HS input composition, edge totals, and the signed
weight of the simulated graph for the real wiring and for each null class.
uv run python scripts/subcircuit_stats.py  -> results/subcircuit_stats.json"""
import json, os, re, sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import brain, nulls  # noqa: E402

sub = brain.load_subcircuit()
ct, side = sub['cell_type'].astype(str), sub['side'].astype(str)
pre, post, w = sub['pre'], sub['post'], sub['w']
is_hs = np.array([bool(re.fullmatch(r'HS[ENS]', c)) for c in ct])
is_t45 = np.array([bool(re.fullmatch(brain.SRC_RE, c)) for c in ct])
is_ab = np.array([bool(re.fullmatch(r'T[45][ab]', c)) for c in ct])
is_a = np.array([bool(re.fullmatch(r'T[45]a', c)) for c in ct])
into_hs = is_hs[post]
syn = np.abs(w)
hs_all, hs_t45 = syn[into_hs].sum(), syn[into_hs & is_t45[pre]].sum()
hs_t45a = syn[into_hs & is_a[pre]].sum()


def net_lif(edges):  # signed weight of edges between simulated (LIF) cells
    p, q, ww = edges
    return int(ww[~is_t45[p] & ~is_t45[q]].sum())


E = brain.sim_edges(sub)
out = dict(
    meta=dict(data_version=brain.DATA_VERSION, subcircuit=brain.SUB_PATH.name, created=datetime.now(timezone.utc).isoformat()),
    n_neurons=int(len(ct)), n_edges=int(len(pre)), n_edges_into_t4t5=int(is_t45[post].sum()), n_sim_edges=int(len(E[0])),
    hs_input=dict(total_synapses=int(hs_all), from_t4t5=int(hs_t45), from_t4a_t5a=int(hs_t45a),
                  frac_from_t4t5=float(hs_t45 / hs_all), frac_of_t4t5_input_that_is_a=float(hs_t45a / hs_t45)),
    net_lif_to_lif_signed_weight=dict(
        real=net_lif(E),
        shuffle_seed0_simulated_edges=net_lif(nulls.shuffle(E, 0)),
        superseded_shuffle_seed0_all_edges=net_lif(nulls.shuffle(sub, 0))),
)
# Type-preserving null: how much of the simulated wiring it can actually move (one realization, seed 0)
cls = brain.type_classes(sub)
lif = ~is_t45
post_cls = cls[E[1]]
cls_size = np.bincount(cls[lif], minlength=cls.max() + 1)            # LIF members per (side, cell type) class
lif_classes = np.unique(cls[lif])
single = cls_size[post_cls] == 1
_, tst = nulls.shuffle(E, 0, classes=cls, return_stats=True)
_, dst = nulls.shuffle(E, 0, return_stats=True)
out['type_null'] = dict(n_lif_classes=int(len(lif_classes)), n_singleton_lif_classes=int(np.sum(cls_size[lif_classes] == 1)),
                        n_edges_into_singleton_class=int(single.sum()), frac_edges_into_singleton_class=float(single.mean()),
                        frac_unchanged_type_null_seed0=float(tst['frac_unchanged']),
                        frac_unchanged_degree_null_seed0=float(dst['frac_unchanged']),
                        frac_unchanged_chance_degree_null=float(dst['frac_unchanged_chance']))
# Composition of the Phase 3 hub lesion sets on the real wiring (betweenness on the simulated edges, ~65 s)
import run_phase3  # noqa: E402
hubs = run_phase3.hub_sets('real')
out['hub_lesion_sets_real'] = {c: dict(n=int(len(ix)), n_DNa02=int(np.sum(ct[ix] == 'DNa02')),
                                       n_HS=int(np.sum(is_hs[ix])), n_T4T5=int(np.sum(is_t45[ix]))) for c, ix in hubs.items()}
p = brain.ROOT / 'results' / 'subcircuit_stats.json'
p.write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
if __name__ == '__main__':
    # self-check: the corrected null keeps the simulated graph's signed LIF weight exactly; the superseded one did not
    assert out['net_lif_to_lif_signed_weight']['shuffle_seed0_simulated_edges'] == out['net_lif_to_lif_signed_weight']['real']
    assert out['n_sim_edges'] == out['n_edges'] - out['n_edges_into_t4t5']
