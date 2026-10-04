"""Export the FlyWire v783 brain as a compact point cloud for the app's 3D brain view.

  uv run python scripts/export_brain_points.py   # -> app/data/brain3d.{bin,json,js}, then self-check

One point per neuron (all 139,248 in the annotation table), at the soma when it is annotated, else at the
anchor point pos_* (on the backbone). Units: the annotation README (flyconnectome/flywire_annotations,
supplemental_files/README.md, checked 2026-10-04) says both are "in 4x4x40nm voxel space", so
    um = (vx * 0.004, vy * 0.004, vz * 0.040).
Axes as in FlyWire: x = medial-lateral (larger x = the 'right' side column), y = dorsal->ventral (y grows
downward), z = anterior->posterior (section depth).

Point order: the 20,556 subcircuit neurons come first, in subcircuit local-index order, so a recording's
raster.neurons[].local IS the point index. The rest follow in annotation-table order.

brain3d.bin (little-endian): Uint16[n*3] quantized positions (x,y,z interleaved), then Uint8[n] class byte.
    um = offset + q * scale.   class byte = cls | side << 4   (side 0 = center/na, 1 = left, 2 = right)
    cls: 0 other brain, 1 subcircuit (other), 2 T4/T5 a/b left, 3 T4/T5 a/b right, 4 HS, 5 VS, 6 H2,
         7 DNa02, 8 DNg02, 9 other recorded DN (DNb02 / DNa01)
Derived from FlyWire v783 annotations (CC BY 4.0).
"""
import base64, json, re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from brain import RAW, ROOT, SRC_RE, DATA_VERSION, group_of, load_subcircuit

OUT = ROOT / 'app' / 'data'
VOX_UM = np.array([0.004, 0.004, 0.040])  # 4 x 4 x 40 nm voxels -> micrometres
Q = 0.05                                   # um per quantization step (range 3.3 mm, far above a fly brain)
CLS = {'T4T5_L': 2, 'T4T5_R': 3, 'HS': 4, 'VS': 5, 'H2': 6, 'DNa02': 7, 'DNg02': 8, 'DNb02': 9, 'DNa01': 9}
SIDE = {'left': 1, 'right': 2}


def raster_locals(sub, n_raster_t4t5=20):
    """Local index for each raster idx, replicating Brain.__init__'s ordering (recorded LIF cells, then the
    sampled T4/T5). Fallback for recordings made before raster.neurons carried 'local'."""
    ct, sd = sub['cell_type'].astype(str), sub['side'].astype(str)
    lab = np.array([group_of(c, s) for c, s in zip(ct, sd)], dtype=object)
    is_src = np.array([bool(re.fullmatch(SRC_RE, c)) for c in ct])
    src_order = sorted(np.flatnonzero(is_src), key=lambda i: ({'T4T5_L': 0, 'T4T5_R': 1}.get(lab[i], 2), lab[i], i))
    groups = sorted({l for l in lab[~is_src] if l})
    lif_order = sorted(np.flatnonzero(~is_src), key=lambda i: (groups.index(lab[i]) if lab[i] else len(groups), i))
    n_rec = int(np.flatnonzero(lab[lif_order] != '')[-1] + 1)
    nL, h = int((lab[src_order] == 'T4T5_L').sum()), n_raster_t4t5 // 2
    return [int(i) for i in lif_order[:n_rec]] + [int(i) for i in src_order[nL - h:nL + h]]


def export():
    ann = pd.read_csv(RAW / 'flywire_annotations.tsv', sep='\t', low_memory=False,
                      usecols=['root_id', 'pos_x', 'pos_y', 'pos_z', 'soma_x', 'soma_y', 'soma_z', 'cell_type', 'side'])
    sub = load_subcircuit()
    rid = sub['root_id'].astype(np.int64)
    row_of = pd.Series(np.arange(len(ann)), index=ann.root_id.values)
    missing = ~np.isin(rid, ann.root_id.values)
    assert not missing.any(), f'{missing.sum()} subcircuit root_ids not in annotations'
    sub_rows = row_of.loc[rid].values
    rest = np.setdiff1d(np.arange(len(ann)), sub_rows)
    order = np.concatenate([sub_rows, rest])  # point index -> annotation row
    a = ann.iloc[order].reset_index(drop=True)

    soma = a[['soma_x', 'soma_y', 'soma_z']].to_numpy(float)
    pos = a[['pos_x', 'pos_y', 'pos_z']].to_numpy(float)
    has_soma = ~np.isnan(soma).any(1)
    um = np.where(has_soma[:, None], soma, pos) * VOX_UM
    off = np.floor(um.min(0))
    q = np.round((um - off) / Q)
    assert q.max() < 65536 and q.min() >= 0
    q = q.astype('<u2')

    n_sub = len(rid)
    cls = np.zeros(len(a), np.uint8)
    cls[:n_sub] = 1
    ct, sd = sub['cell_type'].astype(str), sub['side'].astype(str)
    cells = []
    for i in range(n_sub):
        g = group_of(ct[i], sd[i])
        key = g if g.startswith('T4T5_') else g.rsplit('_', 1)[0]
        if key in CLS:
            cls[i] = CLS[key]
            if CLS[key] >= 4:
                cells.append(dict(local=i, type=ct[i], side=sd[i], root_id=str(rid[i])))
    side = a['side'].map(SIDE).fillna(0).to_numpy(np.uint8)
    cbyte = cls | (side << 4)

    blob = q.tobytes() + cbyte.tobytes()
    meta = dict(
        data_version=DATA_VERSION, n=int(len(a)), n_sub=n_sub, n_with_soma=int(has_soma.sum()),
        units='um', voxel_nm=[4, 4, 40], scale=Q, offset=off.tolist(),
        extent_um=(um.max(0) - um.min(0)).round(1).tolist(),
        axes='x medial-lateral (larger x = side "right"), y dorsal->ventral, z anterior->posterior',
        layout=dict(pos=dict(type='Uint16', offset=0, count=int(q.size)),
                    cls=dict(type='Uint8', offset=int(q.nbytes), count=int(len(a)))),
        classes={'0': 'other brain', '1': 'subcircuit (other)', '2': 'T4/T5 a/b left', '3': 'T4/T5 a/b right',
                 '4': 'HS', '5': 'VS', '6': 'H2', '7': 'DNa02', '8': 'DNg02', '9': 'other recorded DN'},
        side_bits='class byte = cls | side << 4; side 0 center/na, 1 left, 2 right',
        point_index='points [0, n_sub) are subcircuit local indices 0..n_sub-1, so raster.neurons[].local is the point index',
        cells=cells, raster_default=raster_locals(sub),
        created=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        source='FlyWire v783 annotations (Schlegel et al. 2024), CC BY 4.0')
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'brain3d.bin').write_bytes(blob)
    (OUT / 'brain3d.json').write_text(json.dumps(meta, separators=(',', ':')))
    (OUT / 'brain3d.js').write_text('// generated by scripts/export_brain_points.py; FlyWire v783 derived (CC BY 4.0)\n'
                                    f'window.BRAIN3D={{"meta":{json.dumps(meta, separators=(",", ":"))},'
                                    f'"bin":"{base64.b64encode(blob).decode()}"}};\n')
    print(f'{len(a)} points ({n_sub} subcircuit, {has_soma.sum()} at soma), extent {meta["extent_um"]} um, '
          f'{len(cells)} labelled cells, bin {len(blob)/1e6:.2f} MB')
    return meta, um, cbyte


if __name__ == '__main__':
    meta, um, cb = export()
    cls, side = cb & 15, cb >> 4
    # whole brain ~1 mm wide incl. optic lobes; a few hundred um tall and deep
    w, hgt, d = meta['extent_um']
    assert 600 < w < 1100 and 200 < hgt < 600 and 100 < d < 400, meta['extent_um']
    # left/right: the side column splits on x around the midline
    mid = np.median(um[side == 0, 0]) if (side == 0).any() else um[:, 0].mean()
    assert (um[side == 1, 0] < mid).mean() > 0.97 and (um[side == 2, 0] > mid).mean() > 0.97
    # HS cells: lateral (optic lobe / lobula plate, > 100 um from midline), on their side, nearer same-side T4/T5
    t4 = {1: um[cls == 2].mean(0), 2: um[cls == 3].mean(0)}
    for c in meta['cells']:
        if c['type'].startswith('HS'):
            p, s = um[c['local']], SIDE[c['side']]
            assert (p[0] < mid) == (s == 1) and abs(p[0] - mid) > 100, c
            assert np.linalg.norm(p - t4[s]) < np.linalg.norm(p - t4[3 - s]), c
    assert sum(c['type'].startswith('HS') for c in meta['cells']) == 6
    # raster fallback matches the type/side of a real recording
    rec = json.load(open(ROOT / 'app' / 'runs' / 'jsbsim_fly_real_s100.json'))
    sub = load_subcircuit()
    for nr in rec['raster']['neurons']:
        loc = nr.get('local', meta['raster_default'][nr['idx']])
        assert meta['raster_default'][nr['idx']] == loc
        assert (str(sub['cell_type'][loc]), str(sub['side'][loc])) == (nr['type'], nr['side']), nr
    sizes = {f: (OUT / f).stat().st_size for f in ('brain3d.bin', 'brain3d.json', 'brain3d.js')}
    assert sizes['brain3d.bin'] + sizes['brain3d.json'] < 2e6 and sizes['brain3d.js'] < 3e6, sizes
    print('self-check ok', sizes)
