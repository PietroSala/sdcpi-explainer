#!/usr/bin/env python3
"""The loop tool against its mirror without loops, on processes without loops.

    run.py --loop LOOP --mirror MIRROR
    run.py --bin LOOP --mirror MIRROR --count 60 --seed 1 --bench DIR --jobs 2
    run.py --bin LOOP --mirror MIRROR --no-page --only flat-1-3
    python3 scripts/mirror-agreement/run.py --bin target/release/sdcpi --mirror ../sdcpi-parallel/target/release/sdcpi

`sdcpi-loop` is `sdcpi-parallel` with loops: on a process without loops the
two binaries must print the same thing, command by command. This suite
generates random loop-free processes with `scripts/loops/gen.py` (its knob
`--loops 0`), picks instance files of the benchmark folder across every
size, and runs both binaries on every one of them, comparing their outputs.

The binaries: `--loop` (or `--bin`, or `SDCPI_BIN`) is the loop tool, the
one under test; `--mirror` (or `SDCPI_MIRROR`) is the tool without loops,
by default `../sdcpi-parallel/target/release/sdcpi` beside the repository.
Everything generated goes to `--work`, by default a folder
`sdcpi-mirror-agreement` in the temporary directory of the system: the
texts and the instance files, the output of every command of both sides,
the JSON of every frontier, and `report.json`.

What is compared, per instance:

  parse          (generated texts) both binaries parse the text; the two
                 instance files are the same text. The file of the loop
                 tool is the instance every other command reads.
  info           every line but `loops` and `max iter`, which the loop
                 tool alone prints.
  bound          every printed line, with `--time 1` and without.
  to_text        the whole text.
  to_prism       the whole model, with `--time 1` and without.
  to_objective   the property of a budget, and of `--pareto true --time 1`.
  pareto         `--onephase 1 --time 1 --json` (the frontier over the time
                 and the impacts); `--onephase 1 --maximize <impact>`; and
                 `--onephase 1 --time 1 --maximize time`. The JSON reports
                 must agree on `status`, `maximized`, `components`, the two
                 bounds `upper` and `lower`, `tick` and `denominator`, and
                 on the set of points, each with its exact value and whether
                 it is certified (check `pareto-*`); the strategies behind
                 the points are compared apart (check `pareto-*-strategy`).
                 The timings, the counters and `continued`, which the loop
                 tool alone writes, are left out. On the benchmark the
                 search runs with `--time-limit` and the frontiers are
                 compared only when both report `complete`; otherwise the
                 comparison is skipped.
  determine      `--time 1 --arith exact --print-strategy 1` on three
                 budgets drawn from the frontier of the loop tool, when it
                 is complete on both sides: a point of the frontier, rounded
                 up at the ninth decimal; the same point lowered by a
                 billionth in one component; the same point raised by a
                 thousandth in every component. The two answers, the two
                 accepting vectors and the two strategies (the decision
                 lines) must be the same; the counters are left out. The
                 answer is also read against the frontier, on each side: a
                 complete frontier makes the point and the raised budget
                 feasible and the lowered one not (check `determine-*-vs-frontier`).

  to_svg         the whole drawing.

Then the page, unless `--no-page`: both binaries serve on the first two
free ports from `--port-base` (8200), each with a store of its own under
`--work`, and are asked the same questions: `/api/bounds` and `/api/svg`
on every generated text, `/api/frontier` with the time, the workers and
the limit of the run, plain and with one impact maximized, compared like
the JSON of `pareto` (the volatile fields left out, the steps of a
strategy in a fixed order, both answers `complete`), and `/api/bench` on
every benchmark instance whose path names its cell. The servers are
stopped at the end.

And once per run: the mirror refuses the loop syntax where the loop tool
accepts it, and both binaries refuse an option they do not know (a check
that both fail today: `--foo 1` passes in silence).

A command that exceeds its wall-clock cap, or a frontier cut at the time
limit, is a skip, not a failure. A run of both binaries on the same
instance is one job; `--jobs` runs that many instances at once, each with
`--workers` threads in the search.

The summary prints the checks made, passed, failed and skipped, the
instances and comparisons made, and the largest instances compared. Every
failure is printed with the instance, the command and the two outputs,
and the report has them in full. The exit code is 0 exactly when nothing
failed, 2 when the run cannot start.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
GEN = REPO / "scripts" / "loops" / "gen.py"

LOOP_TEXT = "(0.5 ((A, 1, {x: 1}) ^ (B, 2, {x: 3})))"

# ---------------------------------------------------------------------------
# running a binary
# ---------------------------------------------------------------------------


def run(binary: str, args: list[str], timeout: float, cwd: str | None = None) -> dict:
    """One command: its exit code, output and seconds; `rc` None on the cap."""
    t = time.time()
    try:
        r = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {"rc": r.returncode, "out": r.stdout, "err": r.stderr, "seconds": time.time() - t,
                "cmd": " ".join([binary, *args])}
    except subprocess.TimeoutExpired:
        return {"rc": None, "out": "", "err": f"wall-clock cap of {timeout}s exceeded",
                "seconds": time.time() - t, "cmd": " ".join([binary, *args])}


def head(text: str, lines: int = 40) -> str:
    rows = text.splitlines()
    if len(rows) <= lines:
        return text
    return "\n".join(rows[:lines]) + f"\n... ({len(rows) - lines} more lines)"


def diff_lines(a: str, b: str, limit: int = 12) -> str:
    """The first differing lines of two texts, side by side."""
    la, lb = a.splitlines(), b.splitlines()
    out = []
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<absent>"
        y = lb[i] if i < len(lb) else "<absent>"
        if x != y:
            out.append(f"  line {i + 1}:\n    loop:   {x}\n    mirror: {y}")
            if len(out) >= limit:
                out.append("  ...")
                break
    return "\n".join(out)


# ---------------------------------------------------------------------------
# the checks of one instance
# ---------------------------------------------------------------------------


class Checks:
    def __init__(self, instance: str, tasks: int, dims: int):
        self.instance = instance
        self.tasks = tasks
        self.dims = dims
        self.records: list[dict] = []

    def add(self, name: str, status: str, detail: str = "", loop: dict | None = None, mirror: dict | None = None):
        rec = {"instance": self.instance, "tasks": self.tasks, "dims": self.dims,
               "check": name, "status": status, "detail": detail}
        if loop is not None:
            rec["loop"] = {"cmd": loop["cmd"], "rc": loop["rc"], "out": loop["out"], "err": loop["err"],
                           "seconds": round(loop["seconds"], 3)}
        if mirror is not None:
            rec["mirror"] = {"cmd": mirror["cmd"], "rc": mirror["rc"], "out": mirror["out"], "err": mirror["err"],
                             "seconds": round(mirror["seconds"], 3)}
        self.records.append(rec)

    def same_text(self, name: str, a: dict, b: dict, normalize=None) -> bool:
        """Both ran, both succeeded, and the normalized outputs are equal."""
        if a["rc"] is None or b["rc"] is None:
            self.add(name, "skip", f"cap exceeded: loop rc {a['rc']}, mirror rc {b['rc']}", a, b)
            return False
        if a["rc"] != b["rc"]:
            self.add(name, "fail", f"exit codes differ: loop {a['rc']}, mirror {b['rc']}", a, b)
            return False
        if a["rc"] != 0:
            self.add(name, "fail", f"both failed with exit code {a['rc']}", a, b)
            return False
        x = normalize(a["out"]) if normalize else a["out"]
        y = normalize(b["out"]) if normalize else b["out"]
        if x == y:
            self.add(name, "pass")
            return True
        self.add(name, "fail", "outputs differ:\n" + diff_lines(x, y), a, b)
        return False


def info_lines(text: str) -> str:
    keep = []
    for line in text.splitlines():
        key = line.split("  ")[0].strip()
        if key in ("loops", "max iter"):
            continue
        keep.append(line)
    return "\n".join(keep)


def frontier_key(report: dict) -> dict:
    """What two frontiers must share: the fields of the search and the
    set of points with their exact values and certification."""
    points = sorted(
        (tuple(p["value"]), bool(p.get("certified", False))) for p in report.get("points", [])
    )
    return {
        "status": report.get("status"),
        "maximized": report.get("maximized"),
        "components": report.get("components"),
        "upper": report.get("upper"),
        "lower": report.get("lower"),
        "tick": report.get("tick"),
        "denominator": report.get("denominator"),
        "points": points,
    }


def strategies_key(report: dict) -> dict:
    return {tuple(p["value"]): p.get("strategy") for p in report.get("points", [])}


def describe_key(k: dict) -> str:
    pts = k["points"]
    body = f"status {k['status']}, maximized {k['maximized']}, components {k['components']}, " \
           f"upper {k['upper']}, lower {k['lower']}, tick {k['tick']}, D {k['denominator']}, {len(pts)} points"
    for v, c in pts[:8]:
        body += f"\n      {list(v)}{'' if c else ' (provisional)'}"
    if len(pts) > 8:
        body += f"\n      ... {len(pts) - 8} more"
    return body


def compare_frontiers(ch: Checks, name: str, a: dict, b: dict, json_a: Path, json_b: Path,
                      require_complete: bool) -> tuple[dict | None, dict | None]:
    """The two pareto runs: returns the two reports when both are usable."""
    if a["rc"] is None or b["rc"] is None:
        ch.add(name, "skip", f"cap exceeded: loop rc {a['rc']}, mirror rc {b['rc']}", a, b)
        return None, None
    if a["rc"] != 0 or b["rc"] != 0:
        ch.add(name, "fail", f"exit codes: loop {a['rc']}, mirror {b['rc']}", a, b)
        return None, None
    try:
        ra = json.loads(json_a.read_text())
        rb = json.loads(json_b.read_text())
    except (OSError, ValueError) as e:
        ch.add(name, "fail", f"JSON report unreadable: {e}", a, b)
        return None, None
    sa, sb = ra.get("status"), rb.get("status")
    if require_complete and not (sa == "complete" and sb == "complete"):
        ch.add(name, "skip", f"not complete on both sides: loop {sa!r} in {a['seconds']:.1f}s, "
                             f"mirror {sb!r} in {b['seconds']:.1f}s", a, b)
        return None, None
    ka, kb = frontier_key(ra), frontier_key(rb)
    if ka == kb:
        ch.add(name, "pass")
    else:
        ch.add(name, "fail", "frontiers differ:\n    loop:   " + describe_key(ka) + "\n    mirror: " + describe_key(kb), a, b)
        return ra, rb
    sta, stb = strategies_key(ra), strategies_key(rb)
    if sta == stb:
        ch.add(name + "-strategy", "pass")
    elif {v: sorted(map(json.dumps, d or [])) for v, d in sta.items()} == \
            {v: sorted(map(json.dumps, d or [])) for v, d in stb.items()}:
        ch.add(name + "-strategy", "pass", "note: the same decisions, listed in another order")
    else:
        rows = []
        for v in sta:
            if sta[v] != stb.get(v):
                rows.append(f"      point {list(v)}:\n        loop:   {sta[v]}\n        mirror: {stb.get(v)}")
        ch.add(name + "-strategy", "fail", "strategies behind the points differ:\n" + "\n".join(rows[:6]), a, b)
    return ra, rb


def decimal_ceil(x: Fraction, digits: int = 9) -> str:
    q = 10 ** digits
    n = -((-x * q) // 1)
    return fmt_scaled(int(n), digits)


def decimal_floor_below(x: Fraction, digits: int = 9) -> str:
    """A decimal strictly below x, the largest with `digits` decimals."""
    q = 10 ** digits
    n = (x * q) // 1
    if n == x * q:
        n -= 1
    return fmt_scaled(int(n), digits)


def fmt_scaled(n: int, digits: int) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    q = 10 ** digits
    return f"{sign}{n // q}.{n % q:0{digits}d}"


def determine_key(out: str) -> dict:
    answer = accepting = None
    decisions = []
    for line in out.splitlines():
        if line.startswith("answer"):
            answer = line.split(None, 1)[1].strip()
        elif line.startswith("accepting"):
            accepting = line.split(None, 1)[1].strip()
        elif line.startswith("  "):
            decisions.append(line.strip())
    return {"answer": answer, "accepting": accepting, "decisions": decisions}


def compare_instance(spec: dict) -> list[dict]:
    loop, mirror = spec["loop"], spec["mirror"]
    work = Path(spec["work"])
    work.mkdir(parents=True, exist_ok=True)
    name = spec["name"]
    ch = Checks(name, spec["tasks"], spec["dims"])
    cap = spec["cap"]
    workers = str(spec["workers"])
    k = spec["index"]

    # the instance file: for a text, parsed by both and compared
    if spec["kind"] == "gen":
        text = spec["text"]
        ya, yb = work / f"{name}.loop.yaml", work / f"{name}.mirror.yaml"
        a = run(loop, ["parse", "--file", text, "--out", str(ya)], cap)
        b = run(mirror, ["parse", "--file", text, "--out", str(yb)], cap)
        if a["rc"] != 0 or b["rc"] != 0:
            ch.add("parse", "fail", f"exit codes: loop {a['rc']}, mirror {b['rc']}", a, b)
            return ch.records
        ta, tb = ya.read_text(), yb.read_text()
        if ta == tb:
            ch.add("parse", "pass")
        else:
            ch.add("parse", "fail", "instance files differ:\n" + diff_lines(ta, tb), a, b)
        instance = str(ya)
    else:
        instance = spec["path"]

    def both(args: list[str], timeout: float = cap) -> tuple[dict, dict]:
        return run(loop, args, timeout, cwd=str(work)), run(mirror, args, timeout, cwd=str(work))

    # the cheap commands
    ch.same_text("info", *both(["info", instance]), normalize=info_lines)
    ch.same_text("bound", *both(["bound", instance]))
    ch.same_text("bound-time", *both(["bound", instance, "--time", "1"]))
    ch.same_text("to_text", *both(["to_text", instance]))
    ch.same_text("to_svg", *both(["to_svg", instance]))
    ch.same_text("to_prism", *both(["to_prism", instance]))
    ch.same_text("to_prism-time", *both(["to_prism", instance, "--time", "1"]))
    budget = ",".join(f"{(i + 3) / 2:.2f}" for i in range(spec["dims"]))
    ch.same_text("to_objective", *both(["to_objective", instance, "--B", budget]))
    ch.same_text("to_objective-pareto-time", *both(["to_objective", instance, "--pareto", "true", "--time", "1"]))

    # the frontier over the time and the impacts
    limit = ["--time-limit", str(spec["time_limit"])] if spec["time_limit"] else []
    plimit = cap + (spec["time_limit"] or 0)

    def pareto(tag: str, extra: list[str], require_complete: bool):
        ja, jb = work / f"{name}.{tag}.loop.json", work / f"{name}.{tag}.mirror.json"
        a = run(loop, ["pareto", instance, "--onephase", "1", "--workers", workers, "--print-strategy", "1",
                       "--json", str(ja), *limit, *extra], plimit, cwd=str(work))
        b = run(mirror, ["pareto", instance, "--onephase", "1", "--workers", workers, "--print-strategy", "1",
                         "--json", str(jb), *limit, *extra], plimit, cwd=str(work))
        return compare_frontiers(ch, "pareto-" + tag, a, b, ja, jb, require_complete), (a, b)

    bench = spec["kind"] == "bench"
    (ra, rb), (pa, pb) = pareto("time", ["--time", "1"], require_complete=True)
    both_complete = ra is not None and rb is not None and ra.get("status") == "complete" and rb.get("status") == "complete"

    # the directions: one impact maximized, then the time
    names = spec["impact_names"] or [str(i) for i in range(spec["dims"])]  # by name, or by index
    if not bench or both_complete:
        pareto("max-" + names[k % len(names)], ["--maximize", names[k % len(names)]], require_complete=bench)
        pareto("max-time", ["--time", "1", "--maximize", "time"], require_complete=bench)
    else:
        ch.add("pareto-max-impact", "skip", "the plain frontier was not complete on both sides")
        ch.add("pareto-max-time", "skip", "the plain frontier was not complete on both sides")

    # determine on three budgets drawn from the frontier
    if not both_complete or not ra.get("points"):
        for tag in ("at", "below", "above"):
            ch.add(f"determine-{tag}", "skip", "no complete frontier on both sides to draw a budget from")
        return ch.records
    points = sorted(ra["points"], key=lambda p: tuple(Fraction(v) for v in p["value"]))
    point = [Fraction(v) for v in points[k % len(points)]["value"]]
    dims = len(point)
    budgets = {"at": ([decimal_ceil(v) for v in point], "yes")}
    above = [decimal_ceil(v + Fraction(1, 1000)) for v in point]
    budgets["above"] = (above, "yes")
    # the lowered component: the first, from k, whose value is positive
    for j in range(dims):
        i = (k + j) % dims
        if point[i] > 0:
            below = [decimal_ceil(v) for v in point]
            below[i] = decimal_floor_below(point[i])
            budgets["below"] = (below, "no")
            break
    for tag, (vec, expected) in budgets.items():
        args = ["determine", instance, "--B", ",".join(vec), "--time", "1", "--arith", "exact",
                "--print-strategy", "1", "--workers", workers, "--timeout", str(spec["det_timeout"])]
        a, b = both(args, timeout=spec["det_timeout"] + cap)
        if a["rc"] is None or b["rc"] is None:
            ch.add(f"determine-{tag}", "skip", f"cap exceeded: loop rc {a['rc']}, mirror rc {b['rc']}", a, b)
            continue
        if a["rc"] != 0 or b["rc"] != 0:
            ch.add(f"determine-{tag}", "fail", f"exit codes: loop {a['rc']}, mirror {b['rc']}", a, b)
            continue
        ka, kb = determine_key(a["out"]), determine_key(b["out"])
        if ka["answer"] is None or kb["answer"] is None:
            ch.add(f"determine-{tag}", "skip", f"no answer within --timeout: loop {ka['answer']!r}, mirror {kb['answer']!r}", a, b)
            continue
        if ka["answer"] == kb["answer"] and ka["accepting"] == kb["accepting"]:
            ch.add(f"determine-{tag}", "pass")
        else:
            ch.add(f"determine-{tag}", "fail",
                   f"budget {vec}: loop answer {ka['answer']} accepting {ka['accepting']}, "
                   f"mirror answer {kb['answer']} accepting {kb['accepting']}", a, b)
            continue
        if ka["decisions"] == kb["decisions"]:
            ch.add(f"determine-{tag}-strategy", "pass")
        elif sorted(ka["decisions"]) == sorted(kb["decisions"]):
            ch.add(f"determine-{tag}-strategy", "pass", "note: the same decisions, listed in another order")
        else:
            ch.add(f"determine-{tag}-strategy", "fail",
                   f"budget {vec}: the strategies differ\n    loop:   {ka['decisions'][:6]}\n    mirror: {kb['decisions'][:6]}", a, b)
        # the answer against the frontier, on each side
        for side, kk, r in (("loop", ka, a), ("mirror", kb, b)):
            if kk["answer"] == expected:
                ch.add(f"determine-{tag}-vs-frontier-{side}", "pass")
            else:
                ch.add(f"determine-{tag}-vs-frontier-{side}", "fail",
                       f"budget {vec} from frontier point {[str(v) for v in point]}: expected {expected}, "
                       f"{side} answered {kk['answer']}", r if side == "loop" else None, r if side == "mirror" else None)
    return ch.records


# ---------------------------------------------------------------------------
# the instances
# ---------------------------------------------------------------------------

HEADER = re.compile(r"^(tasks|dimensions):\s*(\d+)", re.M)


def bench_header(path: Path) -> tuple[int, int]:
    """tasks and dimensions from the header of an instance file."""
    try:
        with open(path) as f:
            top = f.read(4000)
    except OSError:
        return 0, 0
    found = dict((m.group(1), int(m.group(2))) for m in HEADER.finditer(top))
    return found.get("tasks", 0), found.get("dimensions", 0)


def impact_names_of(path: Path) -> list[str]:
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("impact_names:"):
                    inner = line.split(":", 1)[1].strip().strip("[]")
                    return [x.strip() for x in inner.split(",") if x.strip()]
                if line.startswith("tree:"):
                    break
    except OSError:
        pass
    return []


def pick_bench(folder: Path, count: int, seed: int) -> list[Path]:
    """`count` instance files across the sizes: the files grouped by their
    number of tasks, the distinct sizes split into `count` slices of equal
    rank, one size drawn in every slice and one file drawn of that size."""
    files = sorted(folder.rglob("*.yaml"))
    if not files:
        return []
    by_size: dict[int, list[str]] = {}
    for f in files:
        by_size.setdefault(bench_header(f)[0], []).append(str(f))
    sizes = sorted(by_size)
    rng = random.Random(seed)
    picks = []
    n = len(sizes)
    count = min(count, len(files))
    for i in range(count):
        lo, hi = i * n // count, max(i * n // count + 1, (i + 1) * n // count)
        size = sizes[rng.randrange(lo, hi)]
        picks.append(Path(rng.choice(by_size[size])))
    return picks


def generate(count: int, seed: int, out: Path) -> list[Path]:
    if count <= 0:
        return []
    out.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, str(GEN), "--loops", "0", "--tasks", "3-9", "--impacts", "1-3",
                        "--choices", "1-3", "--count", str(count), "--seed", str(seed), "--out", str(out),
                        "--prefix", "flat"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"run.py: gen.py failed: {r.stderr}")
    return sorted(out.glob("flat-*.cpi"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))


def text_stats(path: Path) -> tuple[int, int, bool]:
    """tasks, impacts and whether a loop appears in a generated text."""
    text = path.read_text()
    m = re.search(r"tasks (\d+) loops (\d+) .* impacts (\d+)", text)
    tasks = int(m.group(1)) if m else 0
    impacts = int(m.group(3)) if m else 0
    body = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
    has_loop = re.search(r"\(\s*0?\.\d+\s", body) is not None or (m is not None and int(m.group(2)) > 0)
    return tasks, impacts, has_loop


def impact_names_of_text(path: Path) -> list[str]:
    names = []
    body = "\n".join(l for l in path.read_text().splitlines() if not l.startswith("#"))
    for m in re.finditer(r"\{([^}]*)\}", body):
        for item in m.group(1).split(","):
            if ":" in item:
                n = item.split(":")[0].strip()
                if n and n not in names:
                    names.append(n)
    return names



# ---------------------------------------------------------------------------
# the page: the two servers asked the same questions
# ---------------------------------------------------------------------------

import socket
import urllib.error
import urllib.parse
import urllib.request

PAGE_VOLATILE = {"seconds", "expanded", "apexes", "max_iter", "loops", "continued", "grammar"}
BENCH_PATH = re.compile(r"(\d+)-nested/(\d+)-independent/(\d+)-process_number/(\d+)-([a-z_]+)\.yaml$")


def free_ports(base: int, n: int) -> list[int]:
    found = []
    port = base
    while len(found) < n and port < base + 500:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            try:
                sk.bind(("127.0.0.1", port))
                found.append(port)
            except OSError:
                pass
        port += 1
    return found


def post(port: int, route: str, fields: dict, timeout: float) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return {"rc": resp.status, "out": body, "err": "", "seconds": time.time() - t,
                    "cmd": f"POST {route} {json.dumps(fields)[:300]}"}
    except urllib.error.HTTPError as e:
        return {"rc": e.code, "out": e.read().decode(errors="replace"), "err": str(e), "seconds": time.time() - t,
                "cmd": f"POST {route} {json.dumps(fields)[:300]}"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"rc": None, "out": "", "err": str(e), "seconds": time.time() - t,
                "cmd": f"POST {route} {json.dumps(fields)[:300]}"}


def page_normal(value):
    """A page answer without its volatile fields, the steps of a strategy
    in a fixed order."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in PAGE_VOLATILE:
                continue
            if k == "steps" and isinstance(v, list):
                out[k] = sorted(v, key=json.dumps)
            else:
                out[k] = page_normal(v)
        return out
    if isinstance(value, list):
        return [page_normal(v) for v in value]
    return value


def page_same(ch: Checks, name: str, a: dict, b: dict, require_complete: bool = False) -> None:
    if a["rc"] is None or b["rc"] is None:
        ch.add(name, "skip", f"no answer: loop {a['err'] or a['rc']}, mirror {b['err'] or b['rc']}", a, b)
        return
    if a["rc"] != b["rc"]:
        ch.add(name, "fail", f"HTTP status differs: loop {a['rc']}, mirror {b['rc']}", a, b)
        return
    try:
        ja, jb = json.loads(a["out"]), json.loads(b["out"])
    except ValueError as e:
        ch.add(name, "fail", f"answer is not JSON: {e}", a, b)
        return
    if require_complete and not (ja.get("status") == "complete" and jb.get("status") == "complete"):
        ch.add(name, "skip", f"not complete on both sides: loop {ja.get('status')!r}, mirror {jb.get('status')!r}", a, b)
        return
    na, nb = page_normal(ja), page_normal(jb)
    if na == nb:
        ch.add(name, "pass")
        return
    ta, tb = json.dumps(na, indent=0, sort_keys=True), json.dumps(nb, indent=0, sort_keys=True)
    ch.add(name, "fail", "page answers differ:\n" + diff_lines(ta, tb), a, b)


def serve(binary: str, port: int, store: Path, bench: Path | None, log: Path) -> subprocess.Popen:
    args = [binary, "serve", "--port", str(port), "--saved", str(store)]
    if bench:
        args += ["--bench", str(bench)]
    return subprocess.Popen(args, stdout=open(log, "w"), stderr=subprocess.STDOUT)


def wait_hello(port: int, seconds: float = 15.0) -> bool:
    t = time.time()
    while time.time() - t < seconds:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def page_checks(specs: list[dict], loop: str, mirror: str, work: Path, bench: Path | None,
                port_base: int, workers: int, time_limit: float) -> list[dict]:
    ch = Checks("(page)", 0, 0)
    ports = free_ports(port_base, 2)
    if len(ports) < 2:
        ch.add("page-servers", "skip", f"no two free ports from {port_base}")
        return ch.records
    pa, pb = ports
    procs = [serve(loop, pa, work / "store-loop.json", bench, work / "serve-loop.log"),
             serve(mirror, pb, work / "store-mirror.json", bench, work / "serve-mirror.log")]
    records: list[dict] = []
    try:
        if not (wait_hello(pa) and wait_hello(pb)):
            ch.add("page-servers", "fail", f"a server did not answer /api/hello on {pa} or {pb}")
            return ch.records
        ch.add("page-servers", "pass")
        records.extend(ch.records)
        for spec in specs:
            c = Checks(spec["name"], spec["tasks"], spec["dims"])
            if spec["kind"] == "gen":
                text = "\n".join(l for l in Path(spec["text"]).read_text().splitlines() if not l.startswith("#")).strip()
                page_same(c, "page-bounds", post(pa, "/api/bounds", {"process": text, "time": "1"}, 30),
                          post(pb, "/api/bounds", {"process": text, "time": "1"}, 30))
                page_same(c, "page-svg", post(pa, "/api/svg", {"process": text}, 30),
                          post(pb, "/api/svg", {"process": text}, 30))
                form = {"process": text, "time": "1", "workers": str(workers), "limit": str(time_limit)}
                page_same(c, "page-frontier", post(pa, "/api/frontier", form, time_limit + 60),
                          post(pb, "/api/frontier", form, time_limit + 60), require_complete=True)
                names = spec["impact_names"] or []
                if names:
                    form = {**form, "maximize": names[spec["index"] % len(names)]}
                    page_same(c, "page-frontier-max", post(pa, "/api/frontier", form, time_limit + 60),
                              post(pb, "/api/frontier", form, time_limit + 60), require_complete=True)
            else:
                m = BENCH_PATH.search(spec["path"])
                if not m or bench is None:
                    continue
                form = {"nested": m.group(1), "independent": m.group(2), "process": m.group(3),
                        "impacts": m.group(4), "mode": m.group(5)}
                page_same(c, "page-bench", post(pa, "/api/bench", form, 30), post(pb, "/api/bench", form, 30))
            records.extend(c.records)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
    return records


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def once_checks(loop: str, mirror: str, work: Path) -> list[dict]:
    ch = Checks("(once)", 0, 0)
    a = run(loop, ["parse", LOOP_TEXT], 30)
    b = run(mirror, ["parse", LOOP_TEXT], 30)
    if a["rc"] == 0:
        ch.add("loop-accepts-loop-syntax", "pass")
    else:
        ch.add("loop-accepts-loop-syntax", "fail", f"the loop tool refused {LOOP_TEXT}", a, None)
    if b["rc"] not in (0, None):
        ch.add("mirror-refuses-loop-syntax", "pass")
    else:
        ch.add("mirror-refuses-loop-syntax", "fail", f"the mirror accepted {LOOP_TEXT} with rc {b['rc']}", None, b)
    tiny = work / "tiny.yaml"
    run(loop, ["parse", "((A, 1, {x: 1}) ^ (B, 2, {x: 3}))", "--out", str(tiny)], 30)
    for side, binary in (("loop", loop), ("mirror", mirror)):
        r = run(binary, ["info", str(tiny), "--foo", "1"], 30)
        if r["rc"] not in (0, None):
            ch.add(f"unknown-option-refused-{side}", "pass")
        else:
            ch.add(f"unknown-option-refused-{side}", "fail",
                   f"`info F --foo 1` exits {r['rc']} and prints the info: an unknown option passes in silence",
                   r if side == "loop" else None, r if side == "mirror" else None)
    r = run(mirror, ["info", str(tiny), "--max-iter", "2"], 30)
    if r["rc"] not in (0, None):
        ch.add("mirror-refuses-max-iter", "pass")
    else:
        ch.add("mirror-refuses-max-iter", "fail",
               f"the mirror, which has no loops, takes `--max-iter 2` with rc {r['rc']}", None, r)
    return ch.records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", "--bin", dest="loop", default=os.environ.get("SDCPI_BIN"),
                    help="the loop tool, the binary under test (or SDCPI_BIN)")
    ap.add_argument("--mirror", default=os.environ.get("SDCPI_MIRROR"),
                    help="the tool without loops (or SDCPI_MIRROR; default ../sdcpi-parallel/target/release/sdcpi)")
    ap.add_argument("--count", type=int, default=60, help="random loop-free texts from gen.py (default 60)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bench", type=Path, default=None, help="the folder of the benchmark instance files")
    ap.add_argument("--bench-count", type=int, default=40, help="instance files picked across the sizes (default 40)")
    ap.add_argument("--jobs", type=int, default=2, help="instances compared at once (default 2)")
    ap.add_argument("--workers", type=int, default=2, help="--workers of every search (default 2)")
    ap.add_argument("--time-limit", type=float, default=10.0,
                    help="--time-limit of pareto on the benchmark instances (default 10)")
    ap.add_argument("--det-timeout", type=float, default=30.0, help="--timeout of determine (default 30)")
    ap.add_argument("--cap", type=float, default=60.0, help="wall-clock seconds for a plain command (default 60)")
    ap.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()) / "sdcpi-mirror-agreement")
    ap.add_argument("--only", nargs="+", default=None, help="only the instances whose name holds one of these")
    ap.add_argument("--no-page", action="store_true", help="leave out the page: the two servers asked the same questions")
    ap.add_argument("--port-base", type=int, default=8200, help="the two servers take the first free ports from here (default 8200)")
    args = ap.parse_args()

    loop = args.loop
    mirror = args.mirror or str(REPO.parent / "sdcpi-parallel" / "target" / "release" / "sdcpi")
    if not loop or not Path(loop).is_file():
        print(f"run.py: the loop binary is not there: {loop!r} (give --loop or SDCPI_BIN)", file=sys.stderr)
        return 2
    if not Path(mirror).is_file():
        print(f"run.py: the mirror binary is not there: {mirror!r} (give --mirror or SDCPI_MIRROR)", file=sys.stderr)
        return 2
    if not GEN.is_file():
        print(f"run.py: gen.py is not there: {GEN}", file=sys.stderr)
        return 2
    work = args.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    specs: list[dict] = []
    base = {"loop": str(Path(loop).resolve()), "mirror": str(Path(mirror).resolve()), "workers": args.workers,
            "cap": args.cap, "det_timeout": args.det_timeout}
    for i, text in enumerate(generate(args.count, args.seed, work / "gen")):
        tasks, impacts, has_loop = text_stats(text)
        if has_loop:
            print(f"run.py: {text.name} holds a loop, left out", file=sys.stderr)
            continue
        specs.append({**base, "kind": "gen", "name": text.stem, "text": str(text), "tasks": tasks, "dims": impacts,
                      "impact_names": impact_names_of_text(text), "index": i, "time_limit": None,
                      "work": str(work / "gen" / text.stem)})
    if args.bench:
        if not args.bench.is_dir():
            print(f"run.py: --bench is not a folder: {args.bench}", file=sys.stderr)
            return 2
        for i, path in enumerate(pick_bench(args.bench.resolve(), args.bench_count, args.seed)):
            tasks, dims = bench_header(path)
            rel = str(path.relative_to(args.bench.resolve())).replace("/", "_").removesuffix(".yaml")
            specs.append({**base, "kind": "bench", "name": "bench-" + rel, "path": str(path), "tasks": tasks,
                          "dims": dims, "impact_names": impact_names_of(path), "index": i,
                          "time_limit": args.time_limit, "work": str(work / "bench" / rel)})
    if args.only:
        specs = [s for s in specs if any(o in s["name"] for o in args.only)]

    print(f"mirror-agreement: loop {loop}\n                  mirror {mirror}")
    print(f"{sum(s['kind'] == 'gen' for s in specs)} generated texts (seed {args.seed}), "
          f"{sum(s['kind'] == 'bench' for s in specs)} benchmark instances, {args.jobs} jobs, "
          f"{args.workers} workers, time limit {args.time_limit}s on the benchmark, work in {work}")
    t0 = time.time()
    records = once_checks(base["loop"], base["mirror"], work)
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for spec, recs in zip(specs, pool.map(compare_instance, specs)):
            records.extend(recs)
            fails = sum(r["status"] == "fail" for r in recs)
            skips = sum(r["status"] == "skip" for r in recs)
            print(f"  {spec['name']:<60} tasks {spec['tasks']:>3} dims {spec['dims']:>2}  "
                  f"{len(recs):>3} checks  {fails} failed  {skips} skipped", flush=True)

    if not args.no_page:
        page = page_checks(specs, base["loop"], base["mirror"], work, args.bench.resolve() if args.bench else None,
                           args.port_base, args.workers, args.time_limit)
        records.extend(page)
        print(f"  (page) {len(page)} checks  {sum(r['status'] == 'fail' for r in page)} failed  "
              f"{sum(r['status'] == 'skip' for r in page)} skipped", flush=True)

    # the summary
    passed = sum(r["status"] == "pass" for r in records)
    failed = sum(r["status"] == "fail" for r in records)
    skipped = sum(r["status"] == "skip" for r in records)
    compared = [r for r in records if r["status"] != "skip" and r["instance"] not in ("(once)", "(page)")]
    by_check: dict[str, list[int]] = {}
    for r in records:
        if r["instance"] in ("(once)", "(page)"):
            continue
        key = re.sub(r"^pareto-max-(?!time$).*", "pareto-max-impact", r["check"])
        key = re.sub(r"-vs-frontier-(loop|mirror)$", "-vs-frontier", key)
        by_check.setdefault(key, [0, 0, 0])
        by_check[key][("pass", "fail", "skip").index(r["status"])] += 1
    largest_all = max((r["tasks"] for r in compared), default=0)
    largest_frontier = max((r["tasks"] for r in compared if r["check"] == "pareto-time"), default=0)
    largest_det = max((r["tasks"] for r in compared if r["check"].startswith("determine-") and "vs-frontier" not in r["check"]), default=0)

    print()
    print("per check            passed  failed  skipped")
    for key in sorted(by_check):
        p, f, s = by_check[key]
        print(f"  {key:<32} {p:>5} {f:>7} {s:>8}")
    print()
    print(f"instances {len(specs)} ({sum(s['kind'] == 'gen' for s in specs)} generated, "
          f"{sum(s['kind'] == 'bench' for s in specs)} benchmark), comparisons made {len(compared)}, "
          f"{time.time() - t0:.0f}s")
    print(f"largest instance compared: {largest_all} tasks on any command, {largest_frontier} on the frontier, "
          f"{largest_det} on determine")
    notes = [r for r in records if r["status"] == "pass" and r["detail"].startswith("note:")]
    print(f"checks {len(records)}  passed {passed}  failed {failed}  skipped {skipped}"
          + (f"  (notes {len(notes)}: the same decisions listed in another order)" if notes else ""))

    failures = [r for r in records if r["status"] == "fail"]
    if failures:
        print(f"\n{len(failures)} failures:")
        for r in failures:
            print(f"\n- {r['instance']}  [{r['check']}]  {r['detail']}")
            for side in ("loop", "mirror"):
                if side in r:
                    print(f"    {side}: {r[side]['cmd']}  (rc {r[side]['rc']}, {r[side]['seconds']}s)")
                    if r[side]["err"].strip():
                        print("      stderr: " + head(r[side]["err"], 5))

    report = {"loop": loop, "mirror": mirror, "seed": args.seed, "count": args.count, "bench": str(args.bench),
              "instances": [{k: v for k, v in s.items() if k not in ("loop", "mirror")} for s in specs],
              "checks": len(records), "passed": passed, "failed": failed, "skipped": skipped,
              "per_check": by_check, "largest_tasks": {"any": largest_all, "frontier": largest_frontier, "determine": largest_det},
              "records": records}
    (work / "report.json").write_text(json.dumps(report, indent=1))
    print(f"report: {work / 'report.json'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
