"""Provenance of the stored results -> results/provenance.json.

(a) The simulation commit(s) each stage's shards record in meta.git_hash (these, not the analysis/export commit, are
    the code that produced the numbers).
(b) The GitHub Actions runner environment, parsed from the job logs (gh api .../actions/jobs/<id>/logs): runner
    image and version, the CPython uv installed, and the installed brian2 / jsbsim / numpy / scipy. Runs: the six
    named in the audit plus every other successful run whose head SHA is a shard SHA. Logs expire (GitHub keeps
    them ~90 days by default); whatever could not be fetched is recorded as unavailable with the run id.
(c) SHA-256 of uv.lock and of the subcircuit file, now and as committed at each simulation commit (local git).
(d) Whether each simulation commit is reachable from the public default branch.

  uv run python scripts/provenance.py      # needs an authenticated gh CLI for (b) and (d); (a) and (c) are local
"""
import glob, hashlib, json, os, re, subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARDS = os.path.join(ROOT, 'results', 'shards')
OUT = os.path.join(ROOT, 'results', 'provenance.json')
REPO = 'aryawidjaja/flight-test-the-fly'
STAGES = {'phase1': 'phase1_*', 'phase2_tune': 'phase2_tune_*', 'phase2_test': 'phase2_test_*', 'phase3': 'phase3_*'}
NAMED_RUNS = {37182520864: 'phase2_test', 37182516287: 'phase2_test', 37182969620: 'phase3',
              37145664704: 'phase2_tune', 37145659700: 'phase2_tune', 37141570260: 'phase1'}
PKGS = ('brian2', 'jsbsim', 'numpy', 'scipy')
FILES = ('uv.lock', 'data/sub/subcircuit_v783_t5.npz')


def shard_shas():
    out = {}
    for st, pat in STAGES.items():
        files = glob.glob(os.path.join(SHARDS, '**', pat + '.json'), recursive=True)
        c = Counter(json.load(open(p))['meta'].get('git_hash') for p in files)
        out[st] = dict(n_files=len(files), git_hash_counts=dict(c.most_common()))
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def sha256_at(commit, path):
    """SHA-256 of a file as committed in `commit` (local git objects), or None if unavailable."""
    r = subprocess.run(['git', 'show', f'{commit}:{path}'], capture_output=True, cwd=ROOT)
    return hashlib.sha256(r.stdout).hexdigest() if r.returncode == 0 else None


def gh(*args):
    r = subprocess.run(['gh', *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def parse_log(text):
    """Environment facts from one job log; None for anything not found."""
    text = re.sub(r'^\ufeff?\S+Z ', '', text, flags=re.M)   # drop the per-line timestamps
    g = lambda pat: (m.group(1) if (m := re.search(pat, text)) else None)
    img = re.search(r'##\[group\]Runner Image\n.*?Image: (\S+)\n.*?Version: (\S+)', text, re.S)
    return dict(image=img and img.group(1), image_version=img and img.group(2), cpython=g(r'Using CPython (\S+)'),
                runner_version=g(r"Current runner version: '([^']+)'"),
                **{p: g(rf'\+ {p}==(\S+)') for p in PKGS})


def run_env(run_id):
    v = gh('run', 'view', str(run_id), '--repo', REPO, '--json', 'headSha,conclusion,createdAt,jobs')
    if v is None:
        return dict(run_id=run_id, available=False, reason='gh run view failed')
    v = json.loads(v)
    jobs = [j for j in v['jobs'] if j['name'].startswith('run')]
    logs = list(ThreadPoolExecutor(8).map(lambda j: gh('api', f'repos/{REPO}/actions/jobs/{j["databaseId"]}/logs'), jobs))
    parsed = [parse_log(t) for t in logs if t]
    keys = ('image', 'image_version', 'cpython', 'runner_version') + PKGS
    return dict(run_id=run_id, url=f'https://github.com/{REPO}/actions/runs/{run_id}', head_sha=v['headSha'],
                conclusion=v['conclusion'], created=v['createdAt'], n_run_jobs=len(jobs), n_logs_fetched=len(parsed),
                available=bool(parsed),
                values={k: dict(Counter(p[k] for p in parsed)) for k in keys})


def reachable_from_default(sha):
    r = subprocess.run(['gh', 'api', f'repos/{REPO}/compare/main...{sha}'], capture_output=True, text=True)
    if r.returncode == 0:
        c = json.loads(r.stdout)
        return dict(on_main=c['status'] in ('identical', 'behind'), status=c['status'])
    return dict(on_main=False, status=(r.stdout or r.stderr).strip()[:200])


def main(out=OUT):
    shas = shard_shas()
    sim = sorted({s for st in shas.values() for s in st['git_hash_counts'] if s})
    listed = gh('run', 'list', '--repo', REPO, '-L', '300', '--json', 'databaseId,headSha,conclusion')
    extra = sorted(r['databaseId'] for r in json.loads(listed or '[]')
                   if r['headSha'] in sim and r['conclusion'] == 'success' and r['databaseId'] not in NAMED_RUNS)
    runs = [dict(run_env(i), stage=NAMED_RUNS.get(i), named_in_audit=i in NAMED_RUNS) for i in list(NAMED_RUNS) + extra]
    agg = {}
    for r in runs:
        for k, c in r.get('values', {}).items():
            for val, n in c.items():
                agg.setdefault(k, Counter())[val] += n
    res = dict(
        meta=dict(script='scripts/provenance.py', created=datetime.now(timezone.utc).isoformat(), repo=REPO,
                  data_version='flywire_v783'),
        simulation_commits_by_stage=shas,
        runner_environment=dict(runs=runs, all_jobs={k: dict(v) for k, v in agg.items()},
                                note='counts are per Actions job ("run (i)" jobs only); values parsed from the job logs'),
        file_sha256={f: sha256(os.path.join(ROOT, f)) for f in FILES},
        file_sha256_at_simulation_commits={s: {f: sha256_at(s, f) for f in FILES} for s in sim},
        public_reachability={s: reachable_from_default(s) for s in sim},
        archive_note='The simulation commits above are not on any public branch: the public history of '
                     f'{REPO} was restarted, so they share no ancestor with main (GitHub keeps them only as '
                     'unreferenced objects, which can be garbage-collected). They are archived privately in the '
                     "owner's local repository / history bundle, because they contain internal notes the owner keeps "
                     'private. Cite them by full SHA; do not present the analysis/export commit as the simulation commit.')
    with open(out, 'w') as fh:
        json.dump(res, fh, indent=1)
    return res


if __name__ == '__main__':
    log = ("2026-10-04T06:20:43Z Current runner version: '2.337.0'\n##[group]Runner Image\nImage: ubuntu-24.04\n"
           "Version: 20260927.320.1\n Using CPython 3.11.17\n + brian2==2.9.0\n + numpy==2.2.6\n")
    p = parse_log(log.replace('\n', '\n2026-10-04T06:20:43.1Z '))
    assert (p['image'], p['image_version'], p['cpython'], p['brian2'], p['numpy'], p['jsbsim']) == \
           ('ubuntu-24.04', '20260927.320.1', '3.11.17', '2.9.0', '2.2.6', None), p
    r = main()
    S = r['simulation_commits_by_stage']
    assert all(len(h) == 40 for st in S.values() for h in st['git_hash_counts'] if h)
    assert all(st['n_files'] == sum(st['git_hash_counts'].values()) for st in S.values())
    for s, h in r['file_sha256_at_simulation_commits'].items():   # same lockfile and subcircuit as the runs used
        assert all(h[f] in (None, r['file_sha256'][f]) for f in FILES), (s, h)
    for k, v in S.items():
        print(k, v)
    for k, v in r['runner_environment']['all_jobs'].items():
        print(k, v)
    print(r['file_sha256'])
    print({s[:7]: v['on_main'] for s, v in r['public_reachability'].items()})
    print('provenance self-check OK ->', OUT)
