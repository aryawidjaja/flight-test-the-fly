"""Mock flight recordings (replay format, see README) for building the viewer in app/ before real runs exist.

Every file written here has meta.mock = true and the viewer shows a MOCK DATA banner for it.
The dynamics are a made-up 2-state Dutch-roll toy with invented derivatives; NOTHING here is a result.

Usage:
  uv run python scripts/make_mock_runs.py              # write mock runs + manifest + bundle
  uv run python scripts/make_mock_runs.py --bundle-only  # rebuild app/runs/bundle.js from manifest.json
                                                         # (run this after exporting real runs)

app/runs/bundle.js is the same data as the JSON files, assigned to window.RUN_BUNDLE, so that opening
app/index.html straight from disk (file://, where fetch() is blocked) still works.
"""
import argparse
import json
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

RUNS = Path(__file__).resolve().parent.parent / "app" / "runs"
GROUPS = ["T4T5_L", "T4T5_R", "HS_L", "HS_R", "VS_L", "VS_R", "DNa02_L", "DNa02_R"]
TOP_KEYS = ["meta", "t", "pos_ned_m", "euler_rad", "r", "beta", "rudder", "gust", "groups", "raster", "metrics"]
MAX_BYTES = 5 * 1024 * 1024
D2R = np.pi / 180


def sig4(a):
    """Round to 4 significant digits (keeps files small; the real exporter should do the same)."""
    return [float(f"{x:.4g}") for x in np.asarray(a, float).ravel()]


def rows(a):
    a = np.asarray(a, float)
    return [sig4(r) for r in a]


def departed(r, beta, phi, dt):
    """Pre-specified departure: |phi|>60 deg, |beta|>20 deg or |r|>60 deg/s held continuously for > 1 s."""
    bad = (np.abs(phi) > 60 * D2R) | (np.abs(beta) > 20 * D2R) | (np.abs(r) > 60 * D2R)
    run = 0
    for b in bad:
        run = run + 1 if b else 0
        if run * dt > 1.0:
            return True
    return False


# Raster subset: 40 neurons. Types are real FlyWire cell-type names, but the mock spikes are not.
NEURONS = (
    [(t, s) for s in ("left", "right") for t in ("T4a", "T4a", "T4b", "T4b", "T5a", "T5a", "T5b", "T5b")]
    + [(t, s) for s in ("left", "right") for t in ("HSN", "HSE", "HSS")]
    + [(t, s) for s in ("left", "right") for t in ("H2",)]
    + [(t, s) for s in ("left", "right") for t in ("VS1", "VS2", "VS3", "VS4")]
    + [(t, s) for s in ("left", "right") for t in ("DNa02",)]
    + [(t, s) for s in ("left", "right") for t in ("DNg02", "DNg02", "DNg02")]
)


def simulate(controller, seed, lesion=0.0, T=60.0, dt=0.01):
    """Toy lateral dynamics (invented numbers): beta, r Dutch roll + roll with a wings leveller."""
    rng_gust = np.random.default_rng(seed)  # same gust for every controller on this seed
    rng = np.random.default_rng([seed, zlib.crc32(controller.encode()), int(lesion * 100)])
    n = int(round(T / dt))
    t = np.arange(n) * dt
    V = 51.4  # m/s, ~100 kt
    Yb, Nb, Nr, Nd, Lb, Lp, Lr = -0.6, 4.0, -0.5, 3.0, -8.0, -5.0, 1.5  # MOCK derivatives

    # Dryden-like coloured noise (first-order), moderate-ish sigma; mock only.
    def coloured(sigma, tau):
        w = rng_gust.standard_normal(n)
        out = np.zeros(n)
        a = np.exp(-dt / tau)
        for k in range(1, n):
            out[k] = a * out[k - 1] + sigma * np.sqrt(1 - a * a) * w[k]
        return out

    gu, gv, gw = coloured(1.0, 4.0), coloured(2.0, 1.5), coloured(1.0, 1.0)

    beta = r = phi = p = psi = 0.0
    x = y = 0.0
    wash = 0.0
    delay = int(0.05 / dt)  # 50 ms sensory-motor delay in the mock fly
    rbuf = [0.0] * (delay + 1)
    K = {"fly_real": 0.012, "fly_shuffle_03": 0.005}.get(controller, 0.012) * (1 - lesion)
    noise = {"fly_real": 3.0, "fly_shuffle_03": 6.0}.get(controller, 3.0) * (1 + 3 * lesion)
    brain = controller.startswith("fly")

    out = {k: np.zeros(n) for k in ("r", "beta", "rudder", "phi", "psi", "theta", "x", "y", "z")}
    rates = {g: np.zeros(n) for g in GROUPS}
    sub = {k: np.zeros(n) for k in ("T4a_L", "T4b_L", "T4a_R", "T4b_R")}
    for k in range(n):
        rbuf = rbuf[1:] + [r]
        rd = rbuf[0]
        cmd = 0.0
        if controller == "yaw_damper":
            wash += dt / 1.0 * (r - wash)  # washout tau = 1 s
            cmd = -0.8 * (r - wash)
        if brain:
            rdeg = rd / D2R
            g = 1.0 * (1 - lesion)  # +100 Hz at 100 deg/s
            t4a_l = 10 + g * max(0, rdeg) + rng.normal(0, 2)
            t4b_l = 10 + g * max(0, -rdeg) + rng.normal(0, 2)
            t4a_r, t4b_r = 10 + g * max(0, -rdeg) + rng.normal(0, 2), 10 + g * max(0, rdeg) + rng.normal(0, 2)
            hs_l = 15 + 1.5 * (t4a_l - t4b_l) * (1 - lesion) + rng.normal(0, noise)
            hs_r = 15 + 1.5 * (t4a_r - t4b_r) * (1 - lesion) + rng.normal(0, noise)
            vs_l = 12 + 0.3 * p / D2R + rng.normal(0, noise)
            vs_r = 12 - 0.3 * p / D2R + rng.normal(0, noise)
            dn_l = 20 + 0.8 * (hs_l - 15) + rng.normal(0, noise)
            dn_r = 20 + 0.8 * (hs_r - 15) + rng.normal(0, noise)
            vals = [(t4a_l + t4b_l) / 2, (t4a_r + t4b_r) / 2, hs_l, hs_r, vs_l, vs_r, dn_l, dn_r]
            for gname, v in zip(GROUPS, vals):
                rates[gname][k] = max(0.0, v)
            for kk, v in zip(sub, (t4a_l, t4b_l, t4a_r, t4b_r)):
                sub[kk][k] = max(0.0, v)
            cmd = K * (rates["DNa02_R"][k] - rates["DNa02_L"][k])  # bias b = 0 by construction here
        cmd = float(np.clip(cmd, -1, 1))

        ba = beta - gv[k] / V
        dbeta = Yb * ba - r + 0.05 * cmd
        dr = Nb * ba + Nr * r + Nd * cmd
        dp = Lb * ba + Lp * p + Lr * r - (2.0 * phi + 1.5 * p)
        beta += dbeta * dt
        r += dr * dt
        p += dp * dt
        phi += p * dt
        psi += r * dt
        chi = psi + beta
        x += (V + gu[k]) * np.cos(chi) * dt
        y += (V + gu[k]) * np.sin(chi) * dt
        for name, v in (("r", r), ("beta", beta), ("rudder", cmd), ("phi", phi), ("psi", psi),
                        ("theta", 0.03 + 0.005 * gw[k]), ("x", x), ("y", y), ("z", -305 - 0.3 * gw[k])):
            out[name][k] = v

    rec = {
        "meta": {"controller": controller, "aircraft": "c172x", "seed": seed, "turbulence": "dryden_moderate",
                 "lesion": {"kind": "random" if lesion else "none", "fraction": lesion}, "dt": dt,
                 "data_version": "flywire_v783", "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "mock": True, "note": "MOCK: synthetic toy dynamics for viewer development. Not a result."},
        "t": [round(v, 3) for v in t],
        "pos_ned_m": np.round(np.c_[out["x"], out["y"], out["z"]], 2).tolist(),  # cm, not sig figs: 4 s.f. of 3 km is a 1 m staircase
        "euler_rad": rows(np.c_[out["phi"], out["theta"], out["psi"]]),
        "r": sig4(out["r"]), "beta": sig4(out["beta"]), "rudder": sig4(out["rudder"]),
        "gust": rows(np.c_[gu, gv, gw]),
        "groups": {g: sig4(rates[g]) for g in GROUPS} if brain else {},
        "raster": {"neurons": [], "spikes": []},
        "metrics": {"rms_r": float(f"{np.sqrt(np.mean(out['r']**2)):.4g}"),
                    "rms_beta": float(f"{np.sqrt(np.mean(out['beta']**2)):.4g}"),
                    "max_abs_phi": float(f"{np.max(np.abs(out['phi'])):.4g}"),
                    "departed": departed(out["r"], out["beta"], out["phi"], dt)},
    }
    if brain:
        spikes = []
        for idx, (typ, side) in enumerate(NEURONS):
            s = "L" if side == "left" else "R"
            if typ[:2] in ("T4", "T5"):
                rate = sub[f"T4{typ[2]}_{s}"]
            elif typ.startswith("HS") or typ == "H2":
                rate = rates[f"HS_{s}"]
            elif typ.startswith("VS"):
                rate = rates[f"VS_{s}"]
            else:
                rate = rates[f"DNa02_{s}"] * (0.6 if typ == "DNg02" else 1.0)
            rate = rate * rng.uniform(0.6, 1.4)
            if lesion and rng.random() < lesion:
                rate = rate * 0  # silenced neuron in the lesion mock
            cnt = rng.poisson(np.clip(rate, 0, None) * dt)
            for k in np.nonzero(cnt)[0]:
                for _ in range(cnt[k]):
                    spikes.append([round(float(t[k] + rng.uniform(0, dt)), 4), idx])
        spikes.sort()
        rec["raster"] = {"neurons": [{"idx": i, "type": ty, "side": sd} for i, (ty, sd) in enumerate(NEURONS)],
                         "spikes": spikes}
    return rec


def validate(rec):
    assert list(rec) == TOP_KEYS or set(TOP_KEYS) <= set(rec), set(TOP_KEYS) - set(rec)
    n = len(rec["t"])
    assert n > 1 and all(b > a for a, b in zip(rec["t"], rec["t"][1:])), "t must increase"
    for k in ("r", "beta", "rudder"):
        assert len(rec[k]) == n, k
    for k in ("pos_ned_m", "euler_rad", "gust"):
        assert len(rec[k]) == n and all(len(v) == 3 for v in rec[k]), k
    assert all(-1 <= u <= 1 for u in rec["rudder"])
    if rec["groups"]:
        assert set(rec["groups"]) == set(GROUPS), rec["groups"].keys()
        assert all(len(v) == n for v in rec["groups"].values())
        nn = len(rec["raster"]["neurons"])
        assert all(0 <= i < nn for _, i in rec["raster"]["spikes"])
    for k in ("controller", "aircraft", "seed", "turbulence", "lesion", "dt", "data_version", "created"):
        assert k in rec["meta"], k
    assert set(rec["metrics"]) >= {"rms_r", "rms_beta", "max_abs_phi", "departed"}


def write_bundle(runs_dir=RUNS):
    man = json.loads((runs_dir / "manifest.json").read_text())
    files = sorted({r["file"] for s in man["scenarios"] for r in s["runs"]})
    runs = {f: json.loads((runs_dir / f).read_text()) for f in files}
    body = json.dumps({"manifest": man, "runs": runs}, separators=(",", ":"))
    (runs_dir / "bundle.js").write_text("// generated by scripts/make_mock_runs.py --bundle-only; do not edit\n"
                                        f"window.RUN_BUNDLE={body};\n")
    return runs_dir / "bundle.js"


def main():
    RUNS.mkdir(parents=True, exist_ok=True)
    seed = 100
    specs = [  # (file, controller, lesion, label)
        ("mock_fly_real_s100.json", "fly_real", 0.0, "Fly brain, real wiring (MOCK)"),
        ("mock_yaw_damper_s100.json", "yaw_damper", 0.0, "Classical yaw damper (MOCK)"),
        ("mock_fly_shuffle_03_s100.json", "fly_shuffle_03", 0.0, "Fly brain, shuffled wiring #03 (MOCK)"),
        ("mock_bare_s100.json", "bare", 0.0, "Bare airframe (MOCK)"),
        ("mock_fly_real_lesion80_s100.json", "fly_real", 0.8, "Fly brain, 80% random lesion (MOCK)"),
    ]
    for f, c, les, _ in specs:
        rec = simulate(c, seed, les)
        validate(rec)
        (RUNS / f).write_text(json.dumps(rec, separators=(",", ":")))
    lab = {f: l for f, _, _, l in specs}
    manifest = {"version": 1, "scenarios": [
        {"id": "same_gust_seed_100", "label": "Same gust, test seed 100 (MOCK)",
         "runs": [{"file": f, "label": lab[f]} for f in
                  ("mock_fly_real_s100.json", "mock_yaw_damper_s100.json",
                   "mock_fly_shuffle_03_s100.json", "mock_bare_s100.json")]},
        {"id": "lesion", "label": "Lesions, seed 100 (MOCK)",
         "runs": [{"file": "mock_fly_real_s100.json", "label": "0% lesion (MOCK)"},
                  {"file": "mock_fly_real_lesion80_s100.json", "label": lab["mock_fly_real_lesion80_s100.json"]}]},
    ]}
    (RUNS / "manifest.json").write_text(json.dumps(manifest, indent=1))
    write_bundle()
    return specs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle-only", action="store_true")
    if ap.parse_args().bundle_only:
        print("wrote", write_bundle())
    else:
        specs = main()
        # Self-check: every manifest file exists, is <= 5 MB, validates against the recording format; bundle matches.
        man = json.loads((RUNS / "manifest.json").read_text())
        files = {r["file"] for s in man["scenarios"] for r in s["runs"]}
        assert files == {s[0] for s in specs}
        for f in files:
            size = (RUNS / f).stat().st_size
            rec = json.loads((RUNS / f).read_text())
            validate(rec)
            assert rec["meta"]["mock"] is True
            assert size <= MAX_BYTES, (f, size)
            nsp = len(rec["raster"]["spikes"])
            assert (nsp == 0) == (not rec["groups"]), f
            print(f"{f}: {size/1e6:.2f} MB, {len(rec['t'])} samples, {nsp} spikes, metrics {rec['metrics']}")
        b = (RUNS / "bundle.js").read_text()
        assert b.count("window.RUN_BUNDLE=") == 1 and all(f in b for f in files)
        print("self-check OK")
