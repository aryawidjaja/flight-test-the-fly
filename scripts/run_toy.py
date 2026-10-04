"""Toy sharded sweep: tests the GitHub Actions plumbing (.github/workflows/sweep.yml) end to end.
Each seed shuffles the committed subcircuit (data/sub, no data/raw needed) and records the mixing stats.
Run: uv run python scripts/run_toy.py --shard 0 --n-shards 2   -> results/shards/toy_0.json
Sharding rule for every run_*.py: job i takes items[i::N]; output is deterministic per seed."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import nulls  # noqa: E402

SUB = 'data/sub/subcircuit_v783_t5.npz'
SEEDS = list(range(10))


def git_hash():
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get('GITHUB_SHA')


def main(shard, n_shards, out_dir='results/shards'):
    edges = np.load(SUB) if os.path.exists(SUB) else nulls._synthetic()
    rows = []
    for seed in SEEDS[shard::n_shards]:
        _, st = nulls.shuffle(edges, seed, return_stats=True)
        rows.append(dict(seed=seed, frac_unchanged=st['frac_unchanged'], swaps=st['swaps'], seconds=st['seconds']))
    os.makedirs(out_dir, exist_ok=True)
    path = f'{out_dir}/toy_{shard}.json'
    meta = dict(script='run_toy.py', shard=shard, n_shards=n_shards, seeds=SEEDS[shard::n_shards],
                git_hash=git_hash(), data_version='flywire_v783', edges=SUB if os.path.exists(SUB) else 'synthetic',
                created=datetime.now(timezone.utc).isoformat())
    with open(path, 'w') as f:
        json.dump(dict(meta=meta, rows=rows), f, indent=1)
    return path


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--shard', type=int, required=True)
    ap.add_argument('--n-shards', type=int, required=True)
    a = ap.parse_args()
    assert 0 <= a.shard < a.n_shards
    p = main(a.shard, a.n_shards)
    d = json.load(open(p))
    assert d['meta']['seeds'] == SEEDS[a.shard::a.n_shards] and len(d['rows']) == len(d['meta']['seeds'])
    assert all(r['frac_unchanged'] < 0.1 for r in d['rows'])
    print('wrote', p, [round(r['frac_unchanged'], 4) for r in d['rows']])
