"""Build the one copy-paste Colab cell that runs a JobSpec: write the worker and the spec to /content, run, stream.

Why: WORKPLAN v2 F-03 and CONTROL.md "COMPUTE VIA CHROME WORKS": the coordinator types one cell into Nick's Colab
through the Chrome tools and reads the output back, so the cell must survive typing into the notebook editor:
every line starts at column 0 and no line ends with ':' (the editor re-indents after those), no triple quotes,
no f-strings, no backslashes; data travels as base64 (letters, digits, + / =), never as quoted JSON.

    text = build_cell(spec, worker_bytes)                 # fetch variant: about 1-2 KB
    text = build_cell(spec, worker_bytes, embed=True)     # worker inside the cell (bigger; Kaggle script, no fetch)

The fetch variant downloads tools/jobs/worker.py from raw.githubusercontent.com at the spec's pinned commit and
refuses to run unless its sha256 equals the hash written into the cell (the bytes `git show <commit>:` gives),
because the whole worker does not fit in about 6,000 characters. The worker that runs is therefore the one of
the pinned commit. `decode_cell` reverses either variant for the round-trip test.
"""
from __future__ import annotations

import base64
import hashlib
import re
import zlib

from tools.jobs.spec import JobSpec

WORKER_PATH = "tools/jobs/worker.py"
WORK_ROOT = "/content/rz_job"
CELL_LIMIT = 6000
_GITHUB = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?$")


def _b64z(data: bytes) -> str:
    return base64.b64encode(zlib.compress(data, 9)).decode("ascii")


def raw_worker_url(spec: JobSpec) -> str:
    m = _GITHUB.match(spec.repo_url)
    if not m:
        raise ValueError("the fetch cell needs a github.com repo_url; use embed=True for any other host")
    return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{spec.commit}/{WORKER_PATH}"


def build_cell(spec: JobSpec, worker: bytes, *, embed: bool = False, work_root: str = WORK_ROOT) -> str:
    """The cell text. `worker` must be the bytes of tools/jobs/worker.py at spec.commit."""
    spec.validate(frozenset(spec.data))            # data hashes are checked against manifests before this
    if not re.match(r"^/[A-Za-z0-9._/-]+$", work_root):
        raise ValueError("work_root must be a plain absolute path")
    head = [f"# RedZone job {spec.job_id} spec {spec.spec_hash()[:12]}: paste as ONE cell and run; run again to resume",
            "import base64,hashlib,pathlib,subprocess,sys,urllib.request,zlib",
            f"D=pathlib.Path('{work_root}/{spec.job_id}')",
            "D.mkdir(parents=True,exist_ok=True)"]
    if embed:
        get = [f"B=zlib.decompress(base64.b64decode('{_b64z(worker)}'))"]
    else:
        get = [f"B=urllib.request.urlopen('{raw_worker_url(spec)}',timeout=60).read()"]
    get += [f"assert hashlib.sha256(B).hexdigest()=='{hashlib.sha256(worker).hexdigest()}','worker.py differs from the cell'",
            "(D/'worker.py').write_bytes(B)",
            f"(D/'spec.json').write_bytes(zlib.decompress(base64.b64decode('{_b64z(spec.to_json().encode())}')))"]
    run = ["p=subprocess.Popen([sys.executable,'-u',str(D/'worker.py'),'--spec',str(D/'spec.json'),'--work',"
           "str(D/'work')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)",
           "for l in p.stdout: print(l,end='',flush=True)",
           "print('RZ-EXIT',p.wait())"]
    text = "\n".join(head + get + run) + "\n"
    problems = typing_problems(text)
    if problems:
        raise ValueError("cell text would not survive typing: " + "; ".join(problems))
    return text


def typing_problems(text: str) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if line[:1].isspace():
            out.append(f"line {i} is indented")
        if line.rstrip().endswith(":"):
            out.append(f"line {i} ends with ':'")
    for bad, why in (('"""', "triple quote"), ("'''", "triple quote"), ("\\", "backslash"), ('"', "double quote")):
        if bad in text:
            out.append(f"contains a {why}")
    if re.search(r"(?<![A-Za-z0-9_])[fF][rRbB]?'", text):
        out.append("contains an f-string")
    return out


def decode_cell(text: str) -> dict:
    """{'spec_json', 'worker_sha256', 'worker' (embedded bytes or None), 'url' (or None)} from a built cell."""
    spec = re.search(r"\(D/'spec\.json'\)\.write_bytes\(zlib\.decompress\(base64\.b64decode\('([A-Za-z0-9+/=]+)'\)\)\)",
                     text)
    want = re.search(r"hexdigest\(\)=='([0-9a-f]{64})'", text)
    emb = re.search(r"^B=zlib\.decompress\(base64\.b64decode\('([A-Za-z0-9+/=]+)'\)\)$", text, re.M)
    url = re.search(r"urlopen\('([^']+)'", text)
    if not (spec and want):
        raise ValueError("not a RedZone job cell")
    return {"spec_json": zlib.decompress(base64.b64decode(spec.group(1))).decode(),
            "worker_sha256": want.group(1),
            "worker": zlib.decompress(base64.b64decode(emb.group(1))) if emb else None,
            "url": url.group(1) if url else None}
