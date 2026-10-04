"""Phase 1 validation: subcircuit vs full v783 brain at 0.5, 2 and 8 Hz (real wiring, 100 deg/s, seeds 0-1).
Allowed because the full brain is only 3.05x slower per sim-s (results/brain_timing_probes.json). The full brain
is Shiu's whole connectome (all 15.1M rows, no synapse threshold), with the same T4/T5 Poisson sources and readout.
Needs data/raw (local only).   uv run python scripts/validate_fullbrain.py  -> results/phase1_fullbrain_check.json"""
import json, os, sys, time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import brain, run_phase1  # noqa: E402

FREQS, SEEDS = [0.5, 2.0, 8.0], [0, 1]


def full_brain():
    comp = pd.read_csv(brain.RAW / 'Completeness_783.csv', index_col=0)
    ann = pd.read_csv(brain.RAW / 'flywire_annotations.tsv', sep='\t', usecols=['root_id', 'cell_type', 'side'],
                      dtype={'root_id': 'int64'}, low_memory=False).set_index('root_id')
    con = pd.read_parquet(brain.RAW / 'Connectivity_783.parquet',
                          columns=['Presynaptic_Index', 'Postsynaptic_Index', 'Excitatory x Connectivity'])
    root = comp.index.values.astype(np.int64)
    return dict(cell_type=ann.cell_type.reindex(root).fillna('').to_numpy(dtype='U'),
                side=ann.side.reindex(root).fillna('na').to_numpy(dtype='U'),
                pre=con.Presynaptic_Index.values, post=con.Postsynaptic_Index.values,
                w=con['Excitatory x Connectivity'].values)


if __name__ == '__main__':
    subs = dict(subcircuit=brain.load_subcircuit(), full_brain=full_brain())
    rows = []
    for name, sub in subs.items():
        for seed in SEEDS:
            for f in FREQS:
                t = time.time()
                r = run_phase1.one_freq(None, sub, f, run_phase1.AMP, seed, 1.0, 'cython')
                rows.append(dict(model=name, seed=seed, wall_s=time.time() - t, **{k: r[k] for k in
                            ('f', 'gain', 'phase_deg', 'coh', 'n_cycles')},
                            DNa02_L_hz=r['groups']['DNa02_L']['mean_hz'], DNa02_R_hz=r['groups']['DNa02_R']['mean_hz']))
                print(rows[-1], flush=True)
    out = dict(meta=dict(data_version=brain.DATA_VERSION, seeds=SEEDS, freqs=FREQS, amp_deg_s=run_phase1.AMP,
                         codegen='cython', created=datetime.now(timezone.utc).isoformat(),
                         note='full brain = all Shiu v783 edges (no threshold); subcircuit = >=5 syn, <=4 hops'),
               rows=rows)
    p = brain.ROOT / 'results' / 'phase1_fullbrain_check.json'
    p.write_text(json.dumps(out, indent=1, default=float))
    # self-check: every condition ran, and the outputs are finite
    assert len(rows) == len(subs) * len(SEEDS) * len(FREQS)
    assert all(np.isfinite(r['gain']) and 0 <= r['coh'] <= 1 for r in rows)
    print('->', p)
