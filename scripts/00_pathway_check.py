"""Phase 0 evidence: does the FlyWire v783 wiring link the eye's motion detectors to the steering DNs?
Run: uv run --python 3.12 --with pandas --with pyarrow python scripts/00_pathway_check.py
Needs data/raw/flywire_annotations.tsv and data/raw/Connectivity_783.parquet (download links in README.md)."""
import collections
import pandas as pd

ann = pd.read_csv('data/raw/flywire_annotations.tsv', sep='\t', usecols=['root_id', 'cell_type', 'side'],
                  dtype={'root_id': 'int64'}, low_memory=False)
con = pd.read_parquet('data/raw/Connectivity_783.parquet')
con = con[con.Connectivity >= 5]  # ponytail: >=5-synapse threshold; rerun at 1 and 10 to check sensitivity

ids = lambda pat: set(ann.root_id[ann.cell_type.fillna('').str.fullmatch(pat)])
src, lptc, dn = ids(r'T[45][a-d]'), ids(r'HS[ENS]|VS\d|H2'), ids(r'DNa02|DNg02_[a-h]')
adj = collections.defaultdict(list)
for a, b in zip(con.Presynaptic_ID.values, con.Postsynaptic_ID.values):
    adj[a].append(b)

def hops(S, T, maxd=6):
    seen, q, d = {s: 0 for s in S}, list(S), 0
    while q and d < maxd:
        d += 1
        nq = [v for u in q for v in adj[u] if v not in seen]
        for v in nq:
            seen.setdefault(v, d)
        q = list(dict.fromkeys(nq))
    return collections.Counter(seen.get(t) for t in T)

print('cells: T4/T5', len(src), '| HS/VS/H2', len(lptc), '| DNa02+DNg02', len(dn))
print('T4/T5 -> HS/VS/H2 min hops:', hops(src, lptc))
print('HS/VS/H2 -> DNa02/DNg02 min hops:', hops(lptc, dn))
print('T4/T5 -> DNa02/DNg02 min hops:', hops(src, dn))
name = dict(zip(ann.root_id, ann.cell_type.astype(str) + '_' + ann.side.astype(str)))
d = con[con.Presynaptic_ID.isin(lptc) & con.Postsynaptic_ID.isin(dn)].sort_values('Connectivity', ascending=False)
print('direct HS/VS/H2 -> DN edges (>=5 syn):')
for _, r in d.iterrows():
    print(' ', name[r.Presynaptic_ID], '->', name[r.Postsynaptic_ID], r.Connectivity, 'syn', 'exc' if r.Excitatory > 0 else 'inh')

if __name__ == '__main__':
    # self-check: the verified 2026-10-03 result must still hold
    assert hops(src, lptc)[1] == len(lptc), 'every HS/VS/H2 should be 1 hop from T4/T5'
    assert len(d) >= 5, 'expected >=5 direct HS->DNa02 edges'
