#!/usr/bin/env python3
"""Every command of sdcpi against every other command, on one instance at a time.

    python3 scripts/cli-consistency/run.py --bin PATH
    python3 scripts/cli-consistency/run.py --bin PATH --count 40 --seed 2 --jobs 2
    python3 scripts/cli-consistency/run.py --bin PATH --no-loops      # the mirror without loops

The binary is `--bin` or `SDCPI_BIN`, by default `target/release/sdcpi` at
the root of the repository. Everything generated goes to `--work`, by default
a fresh temporary folder whose path is printed: the text and the instance
file of every process, every JSON read back, every SVG, every anytime log.

The instances are twenty hand-written processes covering every construct
(one task, sequence, parallel, choice, nature, loop, each nested inside each
other, equal impacts, zero-impact tasks, comments) plus `--count` random ones
drawn with `scripts/loops/gen.py` from `--seed`, small: three to six tasks,
one to three impacts, up to two choices, up to two loops. With `--no-loops`
the hand instances with loops are left out, no loop is generated, and the
checks that only the loop tool knows are skipped, so the same suite runs on
the mirror that rejects the loop syntax.

What is checked, every check over every instance, every value read in the
shown orientation, every frontier as a set of exact rationals:

  round-trip     `parse` writes F.yaml, `to_text` writes it back as a text,
                 `parse` of that text gives the same file modulo whitespace;
                 `info` agrees with the file: tasks, dimensions, loops, the
                 total impact, and `max duration` against the longest task;
                 `to_svg` with every combination of --detail, --tooltips,
                 --clocks, --names, and --bars all or by name, is well-formed
                 XML; `to_prism` and `to_objective` exit 0.
  determinism    `pareto --onephase 1 --time 1` with --workers 1 and 2 and
                 with every --ablation reports `complete` and the same set of
                 points; the two-phase `pareto` (no --onephase) gives the
                 same set.
  determine      with --arith exact, for every frontier point p (at most
                 eight): `determine --B p` answers yes with an accepting
                 vector at or under p; the budget p with one component
                 lowered by one millionth answers no, for every component;
                 with --maximize on the last impact the same on the directed
                 frontier, the maximized component raised instead.
  bounds         `bound --time 1` prints `worst L-min` and `worst max-U` at
                 most 1e-9; its U and L at s0 agree with the `upper` and
                 `lower` of pareto to six decimals; every frontier point lies
                 within [L, U]; the least value of every component over the
                 frontier is `exact min`, and with that component maximized
                 the greatest is `exact max`, to six decimals.
  windows        a --window with an upper end alone, on one component at a
                 value of the frontier, gives the frontier intersected with
                 the window, as sets; with a lower end alone the intersection
                 is contained in the windowed frontier and every windowed
                 point lies within the window and within [L, U]; the end
                 written as a fraction a/b gives the same set and the same
                 printed window as the decimal.
  json           the --json report carries maximized, upper, lower and the
                 points with value, approx (within 1e-6 of value) and
                 certified, every point certified on a complete frontier;
                 the --anytime file is JSON lines whose first line carries
                 start and maximized, whose apexes cover every point of the
                 frontier, and whose last lines say end with the status and
                 minimal with the count of points.
  time-limit     once per run: among generated instances large enough (ten
                 to fourteen tasks, with loops at --max-iter 3; without loops
                 up to thirty-two tasks, the search being too fast below),
                 the first one that `--time-limit 1` cuts ends within four
                 seconds of wall clock, prints `timeout, frontier partial`,
                 and its counts of final and provisional points agree with
                 the JSON and with the anytime file.
  loops          (loop tool only) on every frontier point the continued gap
                 is non-negative in every minimized component and its lower
                 end at most its upper end; on the choice-free instances
                 with loops, over --max-iter 1, 2, 3, the one point grows,
                 its upper gap shrinks or stays, and value plus gap encloses
                 the unbounded expected value wherever `bound --max-iter inf`
                 pins it (U equal to L); `bound --max-iter inf` is at or
                 beyond the bounded bounds; a loop written with probability 0
                 or 1 is refused with a message; `pareto --max-iter inf` is
                 refused.
  exit           every malformed invocation exits nonzero with a message on
                 standard error and no panic, the option mistyped and the
                 window of the wrong arity included; every run of the suite
                 is read for `panicked`.

The exit code is 0 when no check fails, 1 otherwise, 2 when the run cannot
start. A command that exceeds `--timeout` seconds on one of these small
instances is a failure (a hang), not a skip. The failures are listed at the
end with the command that showed them, so that each can be rerun by hand.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

EPS = Fraction(1, 10**6)

# ---------------------------------------------------------------------------
# the hand-written instances: (name, text, has_loop, choice_free)
# ---------------------------------------------------------------------------

HAND = [
    ("one-task", "(A, 1, {x: 1})", False),
    ("one-task-two-impacts", "(Alpha_1, 4, {cost: 2.5, time_h: 0.5})", False),
    ("sequence", "(A, 1, {x: 1}), (B, 2, {x: 2, y: 1})", False),
    ("parallel", "((A, 2, {x: 1}) || (B, 3, {y: 2}))", False),
    ("choice", "((A, 1, {x: 1, y: 2}) ^ (B, 1, {x: 2, y: 1}))", False),
    ("nature", "((A, 1, {x: 1}) ^[0.25] (B, 2, {x: 3}))", False),
    ("equal-impacts", "((A, 1, {x: 2}) ^ (B, 3, {x: 2}))", False),
    ("chain-zero-task", "(A, 1, {x: 1}), (B, 1, {y: 1}), (C, 1, {x: 1, y: 1}), (D, 2)  # a comment", False),
    ("sequence-in-parallel", "(((A, 1, {x: 1}) , (B, 1, {y: 1})) || ((C, 2, {x: 2}) , (D, 1, {y: 2})))", False),
    ("parallel-in-sequence", "(((A, 1, {x: 1}) || (B, 2, {x: 2})) , ((C, 1, {y: 1}) || (D, 1, {y: 3})))", False),
    ("choice-in-nature", "(((A, 1, {x: 1, y: 3}) ^ (B, 1, {x: 3, y: 1})) ^[0.5] (C, 2, {x: 2, y: 2}))", False),
    ("nature-in-choice", "(((A, 1, {x: 1, y: 3}) ^[0.3] (B, 1, {x: 3, y: 1})) ^ (C, 2, {x: 2, y: 2}))", False),
    ("choices-in-parallel", "(((A, 1, {x: 1, y: 2}) ^ (B, 2, {x: 2, y: 1})) || ((C, 1, {x: 1, z: 1}) ^ (D, 1, {y: 1, z: 2})))", False),
    ("natures-in-parallel", "(((A, 1, {x: 1}) ^[0.6] (B, 2, {x: 4})) || ((C, 1, {y: 2}) ^[0.4] (D, 3, {y: 1})))", False),
    ("choice-in-choice-nature-in-sequence",
     "((((A, 1, {x: 1, y: 4}) ^ (B, 1, {x: 2, y: 3})) ^ ((C, 1, {x: 3, y: 2}) ^[0.5] (D, 1, {x: 4, y: 1}))) , (E, 1, {x: 1}))", False),
    ("everything-nested",
     "# three impacts, every construct\n(A, 1, {x: 1}) , ((((B, 2, {y: 2}) || (C, 1, {x: 2})) ^ ((D, 1, {y: 1}) ^[0.7] (E, 2, {x: 1, y: 1}))) , (F, 1, {z: 1}))", False),
    ("loop-task", "(0.5 (A, 1, {x: 2}))", True),
    ("loop-sequence-in-sequence", "((S, 1, {x: 1}) , (0.3 (A, 1, {x: 1}) , (B, 2, {y: 1})))", True),
    ("loop-choice-in-parallel", "((0.2 ((A, 1, {x: 1, y: 2}) ^ (B, 1, {x: 2, y: 1}))) || (C, 2, {x: 1}))", True),
    ("loop-in-loop", "(0.5 ((A, 1, {x: 1}) , (0.5 (B, 1, {y: 1}))))", True),
    ("loop-nature-in-choice", "(((0.4 ((A, 1, {x: 1}) ^[0.5] (B, 2, {x: 3}))) ^ (C, 1, {x: 2})) , (D, 1, {y: 1}))", True),
    ("loop-parallel-in-nature", "((0.5 (((A, 1, {x: 1, y: 3}) ^ (B, 1, {x: 3, y: 1})) || (C, 1, {z: 1}))) ^[0.5] (D, 2, {x: 2}))", True),
    ("loops-nature-parallel-choice-free", "((0.5 ((A, 2, {x: 3, y: 1}) ^[0.4] (B, 1, {x: 1, y: 4}))) || (0.2 (C, 3, {y: 2})))", True),
]

# ---------------------------------------------------------------------------
# running the binary
# ---------------------------------------------------------------------------


class Run:
    __slots__ = ("args", "rc", "out", "err", "seconds", "timed_out")

    def __init__(self, args, rc, out, err, seconds, timed_out):
        self.args = args
        self.rc = rc
        self.out = out
        self.err = err
        self.seconds = seconds
        self.timed_out = timed_out

    def cmd(self) -> str:
        return " ".join(shlex.quote(a) for a in self.args)


class Runner:
    def __init__(self, sdcpi: str, timeout: float, report: "Report"):
        self.sdcpi = sdcpi
        self.timeout = timeout
        self.report = report

    def __call__(self, args: list, timeout: float | None = None, cwd: str | None = None) -> Run:
        cmd = [self.sdcpi] + [str(a) for a in args]
        t = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or self.timeout, cwd=cwd)
            r = Run(cmd, p.returncode, p.stdout, p.stderr, time.time() - t, False)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            r = Run(cmd, None, out, err, time.time() - t, True)
        self.report.count("panic")
        if "panicked" in r.err or "panicked" in r.out:
            self.report.fail("panic", f"{r.cmd()}\n    {r.err.strip()[:300]}")
        return r


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


class Report:
    def __init__(self):
        self.lock = threading.Lock()
        self.checks: dict = {}
        self.failures: list = []
        self.skips: list = []
        self.exit_codes: dict = {}

    def _slot(self, name):
        return self.checks.setdefault(name, {"checks": 0, "failed": 0, "skipped": 0})

    def count(self, name):
        with self.lock:
            self._slot(name)["checks"] += 1

    def fail(self, name, message):
        with self.lock:
            self._slot(name)["failed"] += 1
            self.failures.append((name, message))

    def skip(self, name, message):
        with self.lock:
            s = self._slot(name)
            s["skipped"] += 1
            self.skips.append((name, message))

    def check(self, name, ok: bool, message: str) -> bool:
        """One check counted; a failure recorded with its message."""
        self.count(name)
        if not ok:
            self.fail(name, message)
        return ok

    def totals(self):
        checks = sum(s["checks"] for s in self.checks.values())
        failed = sum(s["failed"] for s in self.checks.values())
        skipped = sum(s["skipped"] for s in self.checks.values())
        return checks, checks - failed, failed, skipped


# ---------------------------------------------------------------------------
# reading the outputs
# ---------------------------------------------------------------------------

VEC = re.compile(r"\[([^\]]*)\]")


def line_after(out: str, prefix: str) -> str | None:
    """The rest of the first line starting with `prefix`, stripped."""
    for line in out.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def floats(text: str) -> list:
    m = VEC.search(text)
    if not m:
        return []
    return [float(x) for x in m.group(1).split(",") if x.strip()]


def rationals(text: str) -> list:
    """`[17/5 (3.4), 5 (5), 13/5 (2.6)]` or `[3, 5, 1]` as Fractions."""
    m = VEC.search(text)
    if not m:
        return []
    out = []
    for part in m.group(1).split(","):
        tok = part.strip().split()[0]
        out.append(Fraction(tok))
    return out


def parse_bound(out: str) -> dict:
    d = {}
    for key, pre in (("U", "U at s0"), ("L", "L at s0"), ("min", "exact min"), ("max", "exact max")):
        rest = line_after(out, pre)
        if rest is not None and rest.startswith("["):
            d[key] = floats(rest)
    rest = line_after(out, "worst L-min")
    if rest is not None:
        d["worst_low"] = float(rest.split()[0])
    rest = line_after(out, "worst max-U")
    if rest is not None:
        d["worst_high"] = float(rest.split()[0])
    rest = line_after(out, "exact ")
    if rest is not None and rest.startswith("unavailable"):
        d["exact_unavailable"] = rest
    return d


def read_json(path: Path):
    with open(path) as f:
        return json.load(f)


def frontier(j) -> set:
    return {tuple(Fraction(v) for v in p["value"]) for p in j.get("points", [])}


def decimal(fr: Fraction) -> str | None:
    """The exact decimal of a terminating rational, else None."""
    d = fr.denominator
    n2 = n5 = 0
    while d % 2 == 0:
        d //= 2
        n2 += 1
    while d % 5 == 0:
        d //= 5
        n5 += 1
    if d != 1:
        return None
    e = max(n2, n5)
    num = fr.numerator * (10**e // fr.denominator)
    sign = "-" if num < 0 else ""
    s = str(abs(num)).rjust(e + 1, "0")
    return sign + (s[:-e] + "." + s[-e:] if e else s)


def fmt(v) -> str:
    return "[" + ", ".join(str(x) for x in v) + "]"


def dominated(p, q, maxed: list) -> bool:
    """`q` dominates `p` in the directed sense: at least as good everywhere,
    better somewhere."""
    if p == q:
        return False
    for a, b, m in zip(p, q, maxed):
        if (b < a) if m else (b > a):
            return False
    return True


# ---------------------------------------------------------------------------
# the instance file
# ---------------------------------------------------------------------------


class Instance:
    def __init__(self, name, text, has_loop, work: Path):
        self.name = name
        self.text = text
        self.has_loop = has_loop
        self.dir = work / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cpi = self.dir / f"{name}.cpi"
        self.cpi.write_text(text + "\n")
        self.yaml = self.dir / f"{name}.yaml"
        self.names: list = []
        self.file: dict = {}

    def path(self, stem: str) -> Path:
        return self.dir / stem


def read_file_facts(path: Path) -> dict:
    """What the instance file holds, read by hand: counts and vectors."""
    text = path.read_text()
    facts = {"tasks": 0, "loops": 0, "choices": 0, "natures": 0, "durations": [], "impacts": []}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("impact_names:"):
            inner = s.split(":", 1)[1].strip().strip("[]")
            facts["names"] = [n.strip() for n in inner.split(",") if n.strip()]
        elif s.startswith("dimensions:"):
            facts["dimensions"] = int(s.split(":", 1)[1])
        elif s.startswith("- task:") or s.startswith("task:"):
            facts["tasks"] += 1
        elif s.startswith("- loop:") or s.startswith("loop:"):
            facts["loops"] += 1
        elif s.startswith("- choice:") or s.startswith("choice:"):
            facts["choices"] += 1
        elif s.startswith("- nature:") or s.startswith("nature:"):
            facts["natures"] += 1
        elif s.startswith("duration:"):
            facts["durations"].append(int(s.split(":", 1)[1]))
        elif s.startswith("impact:"):
            facts["impacts"].append([Fraction(x.strip()) for x in s.split(":", 1)[1].strip().strip("[]").split(",")])
    return facts


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


# ---------------------------------------------------------------------------
# the checks of one instance
# ---------------------------------------------------------------------------


class Suite:
    def __init__(self, run: Runner, rep: Report, loops: bool, workers_max: int = 2):
        self.run = run
        self.rep = rep
        self.loops = loops
        self.workers_max = workers_max

    # -- helpers ------------------------------------------------------------

    def pareto(self, inst: Instance, stem: str, extra: list, onephase=True, time=True, timeout=None):
        """One pareto run with its JSON read back: (Run, json or None)."""
        js = inst.path(stem + ".json")
        args = ["pareto", inst.yaml, "--json", js] + (["--onephase", "1"] if onephase else []) + (["--time", "1"] if time else []) + extra
        r = self.run(args, timeout=timeout)
        j = None
        if r.rc == 0 and js.exists():
            try:
                j = read_json(js)
            except Exception as e:
                self.rep.fail("json", f"{r.cmd()}\n    the JSON does not read: {e}")
        return r, j

    def complete(self, name: str, r: Run, j, what: str) -> bool:
        if r.timed_out:
            self.rep.fail(name, f"{what}: no answer within {self.run.timeout}s (a hang)\n    {r.cmd()}")
            return False
        if r.rc != 0 or j is None:
            self.rep.fail(name, f"{what}: exit {r.rc}: {r.err.strip()[:200]}\n    {r.cmd()}")
            return False
        if j.get("status") != "complete":
            self.rep.fail(name, f"{what}: status {j.get('status')!r}\n    {r.cmd()}")
            return False
        return True

    # -- 1 round trip --------------------------------------------------------

    def check_round_trip(self, inst: Instance) -> bool:
        rep = self.rep
        r = self.run(["parse", "--file", inst.cpi, "--out", inst.yaml])
        if not rep.check("round-trip", r.rc == 0 and inst.yaml.exists(), f"{inst.name}: parse exit {r.rc}: {r.err.strip()}\n    {r.cmd()}"):
            return False
        facts = read_file_facts(inst.yaml)
        inst.file = facts
        inst.names = facts.get("names", [])
        # to_text and back
        txt = inst.path("roundtrip.txt")
        r = self.run(["to_text", inst.yaml, "--out", txt])
        if rep.check("round-trip", r.rc == 0 and txt.exists(), f"{inst.name}: to_text exit {r.rc}: {r.err.strip()}\n    {r.cmd()}"):
            y2 = inst.path("roundtrip.yaml")
            r2 = self.run(["parse", "--file", txt, "--out", y2])
            if rep.check("round-trip", r2.rc == 0, f"{inst.name}: parse of the to_text output exit {r2.rc}: {r2.err.strip()}\n    {r2.cmd()}\n    text: {txt.read_text().strip()}"):
                rep.check("round-trip", squash(inst.yaml.read_text()) == squash(y2.read_text()),
                          f"{inst.name}: parse . to_text . parse differs from parse\n    {r2.cmd()}\n    {inst.yaml} vs {y2}")
        # info against the file
        r = self.run(["info", inst.yaml])
        if rep.check("info", r.rc == 0, f"{inst.name}: info exit {r.rc}: {r.err.strip()}\n    {r.cmd()}"):
            tasks = line_after(r.out, "tasks")
            rep.check("info", tasks is not None and int(tasks) == facts["tasks"], f"{inst.name}: info tasks {tasks}, the file holds {facts['tasks']}\n    {r.cmd()}")
            dims = line_after(r.out, "dimensions")
            rep.check("info", dims is not None and int(dims) == facts["dimensions"], f"{inst.name}: info dimensions {dims}, the file says {facts['dimensions']}\n    {r.cmd()}")
            if self.loops:
                loops = line_after(r.out, "loops")
                rep.check("info", loops is not None and int(loops) == facts["loops"], f"{inst.name}: info loops {loops}, the file holds {facts['loops']}\n    {r.cmd()}")
            md = line_after(r.out, "max duration")
            longest = max(facts["durations"]) if facts["durations"] else 0
            rep.check("info", md is not None and int(md) == longest, f"{inst.name}: info prints `max duration {md}`, the longest task of the file lasts {longest}\n    {r.cmd()}")
            total = floats(line_after(r.out, "total impact") or "")
            want = [sum(v[c] for v in facts["impacts"]) for c in range(facts["dimensions"])]
            rep.check("info", len(total) == len(want) and all(abs(a - float(b)) <= 1e-6 for a, b in zip(total, want)),
                      f"{inst.name}: info total impact {total}, the sum of the tasks {fmt(want)}\n    {r.cmd()}")
        # every SVG combination
        bars = ["all"] + ([inst.names[0]] if inst.names else [])
        combos = [(d, t, c, n, b) for d in ("true", "false") for t in ("true", "false") for c in ("true", "false") for n in ("true", "false") for b in bars]
        for d, t, c, n, b in combos:
            svg = inst.path(f"svg-{d}-{t}-{c}-{n}-{b}.svg")
            r = self.run(["to_svg", inst.yaml, "--detail", d, "--tooltips", t, "--clocks", c, "--names", n, "--bars", b, "--out", svg])
            ok = r.rc == 0 and svg.exists()
            why = f"exit {r.rc}: {r.err.strip()[:200]}"
            if ok:
                try:
                    root = ET.parse(svg).getroot()
                    ok = root.tag.endswith("svg")
                    why = f"the root element is {root.tag}"
                except ET.ParseError as e:
                    ok = False
                    why = f"not XML: {e}"
            rep.check("svg", ok, f"{inst.name}: to_svg {why}\n    {r.cmd()}")
        # the model checker encodings exist and exit 0
        r = self.run(["to_prism", inst.yaml, "--out", inst.path("model.prism")])
        rep.check("round-trip", r.rc == 0 and "mdp" in inst.path("model.prism").read_text() if r.rc == 0 else False,
                  f"{inst.name}: to_prism exit {r.rc}: {r.err.strip()[:200]}\n    {r.cmd()}")
        budget = ",".join("1" for _ in range(facts["dimensions"]))
        r = self.run(["to_objective", inst.yaml, "--B", budget, "--out", inst.path("objective.props")])
        rep.check("round-trip", r.rc == 0 and "multi(" in inst.path("objective.props").read_text() if r.rc == 0 else False,
                  f"{inst.name}: to_objective exit {r.rc}: {r.err.strip()[:200]}\n    {r.cmd()}")
        return True

    # -- 2 determinism -------------------------------------------------------

    def check_determinism(self, inst: Instance):
        """The reference frontier (json) or None."""
        rep = self.rep
        ref_r, ref = self.pareto(inst, "ref", ["--workers", "1", "--ablation", "both"])
        if not self.complete("determinism", ref_r, ref, inst.name):
            return None
        ref_set = frontier(ref)
        rep.check("determinism", len(ref_set) > 0, f"{inst.name}: the frontier is empty\n    {ref_r.cmd()}")
        for w in range(1, self.workers_max + 1):
            for a in ("both", "accept", "reject", "none"):
                if w == 1 and a == "both":
                    continue
                r, j = self.pareto(inst, f"w{w}-{a}", ["--workers", str(w), "--ablation", a])
                if self.complete("determinism", r, j, f"{inst.name} workers {w} ablation {a}"):
                    rep.check("determinism", frontier(j) == ref_set,
                              f"{inst.name}: workers {w} ablation {a} gives {sorted(fmt(p) for p in frontier(j))}, the reference {sorted(fmt(p) for p in ref_set)}\n    {r.cmd()}\n    reference: {ref_r.cmd()}")
        r, j = self.pareto(inst, "twophase", [], onephase=False)
        if self.complete("determinism", r, j, f"{inst.name} two phases"):
            rep.check("determinism", frontier(j) == ref_set,
                      f"{inst.name}: the two-phase frontier is {sorted(fmt(p) for p in frontier(j))}, the one-phase {sorted(fmt(p) for p in ref_set)}\n    {r.cmd()}")
        return ref

    # -- 3 determine against pareto -------------------------------------------

    def check_determine(self, inst: Instance, ref, maxed_name: str | None, cap: int = 8):
        rep = self.rep
        points = sorted(frontier(ref))[:cap]
        k = len(ref["components"])
        maxed = [False] * k
        extra = ["--time", "1", "--arith", "exact"]
        if maxed_name is not None:
            names = ["time"] + inst.names
            maxed[names.index(maxed_name)] = True
            extra += ["--maximize", maxed_name]
        tag = f"{inst.name}" + (f" maximize {maxed_name}" if maxed_name else "")
        for p in points:
            ds = [decimal(v) for v in p]
            if any(d is None for d in ds):
                rep.skip("determine", f"{tag}: the point {fmt(p)} has no finite decimal, --B takes decimals alone")
                continue
            r = self.run(["determine", inst.yaml, "--B", ",".join(ds)] + extra)
            if r.timed_out:
                rep.fail("determine", f"{tag}: no answer within {self.run.timeout}s (a hang)\n    {r.cmd()}")
                continue
            answer = line_after(r.out, "answer")
            if rep.check("determine", r.rc == 0 and answer == "yes", f"{tag}: determine at the frontier point {fmt(p)} answers {answer!r} (exit {r.rc}) {r.err.strip()[:100]}\n    {r.cmd()}"):
                acc = rationals(line_after(r.out, "accepting") or "")
                ok = len(acc) == k and all((a >= b) if m else (a <= b) for a, b, m in zip(acc, p, maxed))
                rep.check("determine", ok, f"{tag}: the accepting vector {fmt(acc)} does not fit the budget {fmt(p)}\n    {r.cmd()}")
            for c in range(k):
                q = list(p)
                q[c] = p[c] + EPS if maxed[c] else p[c] - EPS
                qs = [decimal(v) for v in q]
                r = self.run(["determine", inst.yaml, "--B", ",".join(qs)] + extra)
                if r.timed_out:
                    rep.fail("determine", f"{tag}: no answer within {self.run.timeout}s (a hang)\n    {r.cmd()}")
                    continue
                answer = line_after(r.out, "answer")
                rep.check("determine", r.rc == 0 and answer == "no",
                          f"{tag}: the budget {fmt(q)}, the frontier point {fmt(p)} tightened in component {c}, answers {answer!r} (exit {r.rc}): a strategy there would dominate the point {r.err.strip()[:100]}\n    {r.cmd()}")

    # -- 4 bounds -------------------------------------------------------------

    def check_bounds(self, inst: Instance, ref, maxed_name: str | None, ref_max):
        rep = self.rep
        r = self.run(["bound", inst.yaml, "--time", "1"])
        if not rep.check("bounds", r.rc == 0, f"{inst.name}: bound exit {r.rc}: {r.err.strip()}\n    {r.cmd()}"):
            return
        b = parse_bound(r.out)
        k = len(ref["components"])
        if "worst_low" in b and "worst_high" in b:
            rep.check("bounds", b["worst_low"] <= 1e-9 and b["worst_high"] <= 1e-9,
                      f"{inst.name}: worst L-min {b['worst_low']} worst max-U {b['worst_high']}\n    {r.cmd()}")
        else:
            rep.skip("bounds", f"{inst.name}: no worst lines: {b.get('exact_unavailable', '?')}")
        U = [Fraction(v) for v in ref["upper"]]
        L = [Fraction(v) for v in ref["lower"]]
        rep.check("bounds", len(b.get("U", [])) == k and all(abs(float(u) - x) <= 1e-6 for u, x in zip(U, b["U"])) and all(abs(float(l) - x) <= 1e-6 for l, x in zip(L, b.get("L", []))),
                  f"{inst.name}: bound prints U {b.get('U')} L {b.get('L')}, pareto reports upper {fmt(U)} lower {fmt(L)}\n    {r.cmd()}")
        pts = frontier(ref)
        for p in pts:
            rep.check("bounds", all(L[c] - EPS <= p[c] <= U[c] + EPS for c in range(k)),
                      f"{inst.name}: the point {fmt(p)} leaves the box L {fmt(L)} U {fmt(U)}")
        if "min" in b:
            least = [min(p[c] for p in pts) for c in range(k)]
            rep.check("bounds", all(abs(float(least[c]) - b["min"][c]) <= 1e-6 for c in range(k)),
                      f"{inst.name}: the least values over the frontier are {fmt(least)}, bound prints exact min {b['min']}\n    {r.cmd()}")
        else:
            rep.skip("bounds", f"{inst.name}: no exact min line")
        if maxed_name is not None and ref_max is not None:
            rm = self.run(["bound", inst.yaml, "--time", "1", "--maximize", maxed_name])
            bm = parse_bound(rm.out)
            names = ["time"] + inst.names
            c = names.index(maxed_name)
            if rep.check("bounds", rm.rc == 0 and "max" in bm, f"{inst.name}: bound --maximize {maxed_name} exit {rm.rc}, no exact max: {rm.err.strip()[:100]}\n    {rm.cmd()}"):
                greatest = max(p[c] for p in frontier(ref_max))
                rep.check("bounds", abs(float(greatest) - bm["max"][c]) <= 1e-6,
                          f"{inst.name}: with {maxed_name} maximized the greatest value over the frontier is {greatest}, bound prints exact max {bm['max']}\n    {rm.cmd()}")
                rep.check("bounds", all(abs(x - y) <= 1e-6 for x, y in zip(bm.get("max", []), b.get("max", []))) and all(abs(x - y) <= 1e-6 for x, y in zip(bm.get("min", []), b.get("min", []))),
                          f"{inst.name}: bound with and without --maximize disagree on the exact optima: {bm.get('min')} {bm.get('max')} vs {b.get('min')} {b.get('max')}\n    {rm.cmd()}")

    # -- 5 windows --------------------------------------------------------------

    def check_windows(self, inst: Instance, ref):
        rep = self.rep
        pts = frontier(ref)
        k = len(ref["components"])
        if len(pts) < 2:
            rep.skip("windows", f"{inst.name}: one point, no window to cut")
            return
        U = [Fraction(v) for v in ref["upper"]]
        L = [Fraction(v) for v in ref["lower"]]
        c = max(range(k), key=lambda i: len({p[i] for p in pts}))
        vals = sorted({p[c] for p in pts})
        cut = vals[len(vals) // 2]
        forms = [f"{cut.numerator}/{cut.denominator}"]
        if decimal(cut) is not None:
            forms.append(decimal(cut))
        # upper end alone: equality with the intersection
        got = []
        for i, f in enumerate(forms):
            ranges = [".."] * k
            ranges[c] = f"..{f}"
            r, j = self.pareto(inst, f"window-hi-{i}", ["--window", ",".join(ranges)])
            if self.complete("windows", r, j, f"{inst.name} window {ranges}"):
                want = {p for p in pts if p[c] <= cut}
                rep.check("windows", frontier(j) == want,
                          f"{inst.name}: the window {ranges} gives {sorted(fmt(p) for p in frontier(j))}, the frontier within it is {sorted(fmt(p) for p in want)}\n    {r.cmd()}")
                got.append((frontier(j), line_after(r.out, "window")))
        if len(got) == 2:
            rep.check("windows", got[0] == got[1], f"{inst.name}: the fraction {forms[0]} and the decimal {forms[1]} give different answers: {got[0]} vs {got[1]}")
        # lower end alone: containment
        ranges = [".."] * k
        ranges[c] = f"{forms[0]}.."
        r, j = self.pareto(inst, "window-lo", ["--window", ",".join(ranges)])
        if self.complete("windows", r, j, f"{inst.name} window {ranges}"):
            inside = {p for p in pts if p[c] >= cut}
            w = frontier(j)
            rep.check("windows", inside <= w,
                      f"{inst.name}: the window {ranges} misses the frontier points {sorted(fmt(p) for p in inside - w)}; it gives {sorted(fmt(p) for p in w)}\n    {r.cmd()}")
            rep.check("windows", all(p[c] >= cut and all(L[i] <= p[i] <= U[i] for i in range(k)) for p in w),
                      f"{inst.name}: the window {ranges} gives a point outside it: {sorted(fmt(p) for p in w if not (p[c] >= cut))}\n    {r.cmd()}")
            rep.check("windows", not any(dominated(p, q, [False] * k) for p in w for q in w),
                      f"{inst.name}: the windowed frontier {sorted(fmt(p) for p in w)} is not an antichain\n    {r.cmd()}")

    # -- 6 json and anytime -------------------------------------------------------

    def check_json(self, inst: Instance, ref, ref_max, maxed_name):
        rep = self.rep
        for j, tag, maxed in ((ref, inst.name, []), (ref_max, f"{inst.name} maximize {maxed_name}", [maxed_name] if maxed_name else [])):
            if j is None:
                continue
            for key in ("maximized", "upper", "lower", "points", "status", "components"):
                rep.check("json", key in j, f"{tag}: the JSON lacks {key!r}")
            rep.check("json", j.get("maximized") == maxed, f"{tag}: the JSON says maximized {j.get('maximized')}, asked {maxed}")
            k = len(j.get("components", []))
            for p in j.get("points", []):
                ok = all(key in p for key in ("value", "approx", "certified"))
                rep.check("json", ok, f"{tag}: a point lacks value, approx or certified: {p}")
                if ok:
                    rep.check("json", len(p["value"]) == k and len(p["approx"]) == k and all(abs(float(Fraction(v)) - a) <= 1e-6 for v, a in zip(p["value"], p["approx"])),
                              f"{tag}: approx {p['approx']} is not the value {p['value']} to 1e-6")
                    rep.check("json", p["certified"] is True, f"{tag}: the point {p['value']} is not certified on a complete frontier")
            rep.check("json", j.get("certified_points") == len(j.get("points", [])), f"{tag}: certified_points {j.get('certified_points')} against {len(j.get('points', []))} points")
        # the anytime file
        anyf = inst.path("anytime.jsonl")
        if anyf.exists():
            anyf.unlink()
        r, j = self.pareto(inst, "anytime", ["--anytime", anyf])
        if not self.complete("anytime", r, j, f"{inst.name} anytime"):
            return
        if not rep.check("anytime", anyf.exists(), f"{inst.name}: no anytime file written\n    {r.cmd()}"):
            return
        lines = []
        for n, line in enumerate(anyf.read_text().splitlines(), 1):
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError as e:
                rep.fail("anytime", f"{inst.name}: line {n} of the anytime file is not JSON: {e}\n    {r.cmd()}")
                return
        rep.count("anytime")
        if not lines or not lines[0].get("start") or "maximized" not in lines[0]:
            rep.fail("anytime", f"{inst.name}: the first line does not carry start and maximized: {lines[:1]}\n    {r.cmd()}")
            return
        apexes = {tuple(Fraction(v) for v in l["v"]) for l in lines if "v" in l}
        pts = frontier(j)
        rep.check("anytime", pts <= apexes, f"{inst.name}: the frontier points {sorted(fmt(p) for p in pts - apexes)} were never recorded as apexes\n    {r.cmd()}")
        rep.check("anytime", frontier(ref) == pts, f"{inst.name}: the anytime run gives a different frontier\n    {r.cmd()}")
        ends = [l for l in lines if l.get("end")]
        rep.check("anytime", len(ends) == 1 and ends[0].get("status") == j.get("status"), f"{inst.name}: the end line says {ends}, the JSON status {j.get('status')}\n    {r.cmd()}")
        last = lines[-1]
        rep.check("anytime", last.get("minimal") and last.get("points") == len(pts), f"{inst.name}: the last line {last}, the frontier holds {len(pts)} points\n    {r.cmd()}")
        ts = [l["t"] for l in lines if "t" in l]
        rep.check("anytime", ts == sorted(ts), f"{inst.name}: the times of the anytime lines are not increasing\n    {r.cmd()}")

    # -- 8 loops ---------------------------------------------------------------

    def check_loops_gap(self, inst: Instance, ref):
        rep = self.rep
        if not inst.has_loop:
            return
        k = len(ref["components"])
        for p in ref.get("points", []):
            cont = p.get("continued")
            if not rep.check("loops", cont is not None and "low" in cont and "up" in cont, f"{inst.name}: the point {p['value']} carries no continued gap"):
                continue
            low = [Fraction(v) for v in cont["low"]]
            up = [Fraction(v) for v in cont["up"]]
            rep.check("loops", all(0 <= low[c] <= up[c] for c in range(k)),
                      f"{inst.name}: the point {p['value']} has the continued gap low {fmt(low)} up {fmt(up)}: negative, or low above up")

    def check_loops_k(self, inst: Instance):
        """On a choice-free instance with loops: K = 1, 2, 3 and inf."""
        rep = self.rep
        if not inst.has_loop or inst.file.get("choices", 0) > 0:
            return
        series = []
        for K in (1, 2, 3):
            r, j = self.pareto(inst, f"k{K}", ["--max-iter", str(K)])
            if not self.complete("loops-k", r, j, f"{inst.name} max-iter {K}"):
                return
            pts = j.get("points", [])
            if not rep.check("loops-k", len(pts) == 1, f"{inst.name}: {len(pts)} points on an instance without choices at --max-iter {K}\n    {r.cmd()}"):
                return
            p = pts[0]
            series.append((K, [Fraction(v) for v in p["value"]], [Fraction(v) for v in p["continued"]["low"]], [Fraction(v) for v in p["continued"]["up"]],
                           [Fraction(v) for v in j["lower"]], [Fraction(v) for v in j["upper"]], r.cmd()))
        k = len(series[0][1])
        for (K1, v1, l1, u1, _, _, c1), (K2, v2, l2, u2, _, _, c2) in zip(series, series[1:]):
            rep.check("loops-k", all(v2[c] >= v1[c] for c in range(k)), f"{inst.name}: the value falls from K={K1} {fmt(v1)} to K={K2} {fmt(v2)}\n    {c1}\n    {c2}")
            rep.check("loops-k", all(u2[c] <= u1[c] for c in range(k)), f"{inst.name}: the upper gap grows from K={K1} {fmt(u1)} to K={K2} {fmt(u2)}\n    {c1}\n    {c2}")
            rep.check("loops-k", all(l2[c] <= l1[c] for c in range(k)), f"{inst.name}: the lower gap grows from K={K1} {fmt(l1)} to K={K2} {fmt(l2)}\n    {c1}\n    {c2}")
        r = self.run(["bound", inst.yaml, "--time", "1", "--max-iter", "inf"])
        if not rep.check("loops-k", r.rc == 0, f"{inst.name}: bound --max-iter inf exit {r.rc}: {r.err.strip()}\n    {r.cmd()}"):
            return
        b = parse_bound(r.out)
        if not rep.check("loops-k", "U" in b and "L" in b, f"{inst.name}: bound --max-iter inf prints no bounds\n    {r.cmd()}"):
            return
        for K, v, lo, up, L, U, cmd in series:
            rep.check("loops-k", all(b["U"][c] >= float(U[c]) - 1e-6 and b["L"][c] >= float(L[c]) - 1e-6 for c in range(k)),
                      f"{inst.name}: bound --max-iter inf U {b['U']} L {b['L']} lies below the bounds at K={K}: U {fmt(U)} L {fmt(L)}\n    {r.cmd()}")
            for c in range(k):
                if abs(b["U"][c] - b["L"][c]) <= 1e-9:
                    rep.check("loops-k", float(v[c] + lo[c]) - 1e-6 <= b["U"][c] <= float(v[c] + up[c]) + 1e-6,
                              f"{inst.name}: at K={K} component {c}: value {v[c]} + gap [{lo[c]}, {up[c]}] does not enclose the unbounded value {b['U'][c]}\n    {cmd}\n    {r.cmd()}")
        r = self.run(["pareto", inst.yaml, "--onephase", "1", "--time", "1", "--max-iter", "inf"])
        rep.check("loops-k", r.rc != 0, f"{inst.name}: pareto --max-iter inf exits {r.rc}, the search of the unbounded process is refused by the documentation\n    {r.cmd()}")

    # -- the whole of one instance ------------------------------------------------

    def instance(self, inst: Instance):
        if not self.check_round_trip(inst):
            return
        ref = self.check_determinism(inst)
        if ref is None:
            return
        maxed_name = inst.names[-1] if inst.names else None
        ref_max = None
        if maxed_name is not None:
            r, ref_max = self.pareto(inst, "max", ["--maximize", maxed_name])
            if not self.complete("determinism", r, ref_max, f"{inst.name} maximize {maxed_name}"):
                ref_max = None
            else:
                pts = frontier(ref_max)
                self.rep.check("determinism", not any(dominated(p, q, [False] * (len(inst.names)) + [True]) for p in pts for q in pts),
                               f"{inst.name}: the frontier with {maxed_name} maximized is not an antichain in the directed sense\n    {r.cmd()}")
        self.check_determine(inst, ref, None)
        if ref_max is not None:
            self.check_determine(inst, ref_max, maxed_name, cap=6)
        self.check_bounds(inst, ref, maxed_name, ref_max)
        self.check_windows(inst, ref)
        self.check_json(inst, ref, ref_max, maxed_name)
        if self.loops:
            self.check_loops_gap(inst, ref)
            self.check_loops_k(inst)

    # -- 7 the time limit, once per run ---------------------------------------------

    def check_time_limit(self, gen, work: Path, seed: int):
        rep = self.rep
        plans = []
        if self.loops:
            plans.append(dict(tasks=(10, 14), loops=(2, 3), impacts=2, choices=(3, 6), extra=["--max-iter", "3"]))
        else:
            plans.append(dict(tasks=(10, 14), loops=(0, 0), impacts=2, choices=(4, 7), extra=[]))
            plans.append(dict(tasks=(24, 32), loops=(0, 0), impacts=2, choices=(10, 16), extra=[]))
        tried = 0
        for plan in plans:
            for index in range(12):
                rng = random.Random(seed * 7_919 + index + 100 * len(plan["extra"]))
                tree = gen.generate(rng, rng.randint(*plan["tasks"]), rng.randint(*plan["loops"]), plan["impacts"], rng.randint(*plan["choices"]))
                inst = Instance(f"limit-{plan['tasks'][0]}-{index}", gen.text(tree), plan["loops"][1] > 0, work)
                r = self.run(["parse", "--file", inst.cpi, "--out", inst.yaml])
                if r.rc != 0:
                    continue
                tried += 1
                anyf = inst.path("anytime.jsonl")
                t = time.time()
                r, j = self.pareto(inst, "limit", ["--workers", str(self.workers_max), "--time-limit", "1", "--anytime", anyf] + plan["extra"], timeout=30)
                wall = time.time() - t
                if r.timed_out:
                    rep.check("time-limit", False, f"{inst.name}: --time-limit 1 did not end within 30 s (a hang)\n    {r.cmd()}")
                    return
                if r.rc != 0 or j is None:
                    rep.check("time-limit", False, f"{inst.name}: exit {r.rc}: {r.err.strip()[:200]}\n    {r.cmd()}")
                    return
                if j.get("status") == "complete":
                    continue
                rep.check("time-limit", wall <= 4.0, f"{inst.name}: --time-limit 1 ended after {wall:.2f} s of wall clock\n    {r.cmd()}")
                rep.check("time-limit", j.get("status") == "timeout" and (line_after(r.out, "status") or "").startswith("timeout"),
                          f"{inst.name}: the cut reports status {j.get('status')!r} / {line_after(r.out, 'status')!r}\n    {r.cmd()}")
                pts = j.get("points", [])
                final = sum(1 for p in pts if p.get("certified"))
                m = re.search(r"certified\s+(\d+) of (\d+) points are final, (\d+) open lower bounds", r.out)
                rep.check("time-limit", m is not None and int(m.group(1)) == final == j.get("certified_points") and int(m.group(2)) == len(pts) and int(m.group(3)) == j.get("open_bounds"),
                          f"{inst.name}: the cut line {m.group(0) if m else None!r} against the JSON: {final} certified of {len(pts)} points, certified_points {j.get('certified_points')}, open_bounds {j.get('open_bounds')}\n    {r.cmd()}")
                printed = line_after(r.out, "points")
                rep.check("time-limit", printed is not None and int(printed.split()[0]) == len(pts), f"{inst.name}: stdout says points {printed}, the JSON holds {len(pts)}\n    {r.cmd()}")
                for p in pts:
                    rep.check("time-limit", len(p["value"]) == len(p["approx"]) and all(abs(float(Fraction(v)) - a) <= 1e-6 for v, a in zip(p["value"], p["approx"])),
                              f"{inst.name}: approx {p['approx']} is not the value {p['value']}")
                if anyf.exists():
                    lines = [json.loads(l) for l in anyf.read_text().splitlines()]
                    ends = [l for l in lines if l.get("end")]
                    last = lines[-1] if lines else {}
                    rep.check("time-limit", len(ends) == 1 and ends[0].get("status") == "timeout" and last.get("minimal") and last.get("points") == len(pts),
                              f"{inst.name}: the anytime file ends {ends} {last}, the JSON holds {len(pts)} points\n    {r.cmd()}")
                    apexes = {tuple(Fraction(v) for v in l["v"]) for l in lines if "v" in l}
                    rep.check("time-limit", frontier(j) <= apexes, f"{inst.name}: points of the cut frontier were never recorded as apexes\n    {r.cmd()}")
                return
        rep.skip("time-limit", f"no generated instance out of {tried} was cut by --time-limit 1; the search finished them all")

    # -- 9 exit codes -------------------------------------------------------------------

    def check_exit_codes(self, work: Path):
        rep = self.rep
        d = work / "exit"
        d.mkdir(exist_ok=True)
        good = d / "good.yaml"
        self.run(["parse", "((A, 2, {x: 3, y: 1}) ^ ((B, 1, {x: 1, y: 4}) ^[0.3] (C, 3, {y: 2}))) , (D, 1, {x: 2})", "--out", good])
        garbage = d / "garbage.yaml"
        garbage.write_text("garbage\n")
        looped = d / "looped.yaml"
        if self.loops:
            self.run(["parse", "((A, 2, {x: 3, y: 1}) ^ (0.5 (B, 1, {x: 1, y: 4})))", "--out", looped])
        cases = [
            ("a task of duration 0", ["parse", "(A, 0, {x: 1})"]),
            ("an impact written 0", ["parse", "(A, 1, {x: 0})"]),
            ("a negative impact", ["parse", "(A, 1, {x: -1})"]),
            ("a nature probability above 1", ["parse", "((A, 1) ^[1.5] (B, 1))"]),
            ("a nature probability 0", ["parse", "((A, 1) ^[0] (B, 1))"]),
            ("an unclosed parenthesis", ["parse", "((A, 1) ^ (B, 1)"]),
            ("an empty process", ["parse", ""]),
            ("a process naming no impact", ["parse", "((A, 3) ^ (B, 1))"]),
            ("a quoted task name, which the grammar has not", ["parse", '("A B", 1, {x: 1})']),
            ("a missing file", ["info", d / "missing.yaml"]),
            ("a file that is not an instance", ["info", garbage]),
            ("determine without a budget", ["determine", good]),
            ("a budget of the wrong length", ["determine", good, "--B", "1,2,3"]),
            ("a budget naming an unknown impact", ["determine", good, "--B", "{x: 1, zz: 2}"]),
            ("a budget written as a fraction", ["determine", good, "--B", "17/5,1"]),
            ("--maximize naming an unknown impact", ["pareto", good, "--onephase", "1", "--maximize", "zz"]),
            ("--components outside the range", ["pareto", good, "--onephase", "1", "--components", "9"]),
            ("--window with a division by zero", ["pareto", good, "--onephase", "1", "--window", "1/0..,.."]),
            ("--window without the dots", ["pareto", good, "--onephase", "1", "--window", "1,2"]),
            ("--window with three ranges on two components", ["pareto", good, "--onephase", "1", "--window", "..1,..,.."]),
            ("--window with one range on two components", ["pareto", good, "--onephase", "1", "--window", "..100"]),
            ("--time-limit negative", ["pareto", good, "--onephase", "1", "--time-limit", "-1"]),
            ("--time-limit not a number", ["pareto", good, "--onephase", "1", "--time-limit", "soon"]),
            ("--workers not a number", ["pareto", good, "--onephase", "1", "--workers", "many"]),
            ("--ablation unknown", ["pareto", good, "--onephase", "1", "--ablation", "sometimes"]),
            ("--arith unknown", ["determine", good, "--B", "5,5", "--arith", "float"]),
            ("an option mistyped (--time-limt)", ["pareto", good, "--onephase", "1", "--time-limt", "1"]),
            ("an option unknown (--frobnicate)", ["pareto", good, "--onephase", "1", "--frobnicate", "1"]),
            ("--to_svg with --detail neither true nor false", ["to_svg", good, "--detail", "maybe"]),
            ("--to_svg --bars naming an unknown impact", ["to_svg", good, "--bars", "zz"]),
            ("to_prism with --encode-history neither true nor false", ["to_prism", good, "--encode-history", "maybe"]),
            ("an unknown command", ["frobnicate"]),
            ("no arguments", []),
            ("a loop written in the mirror without loops" if not self.loops else "a loop with probability 0", ["parse", "(0 (A, 1, {x: 1}))"]),
        ]
        if self.loops:
            cases += [
                ("a loop with probability 1", ["parse", "(1 (A, 1, {x: 1}))"]),
                ("a loop with probability above 1", ["parse", "(1.5 (A, 1, {x: 1}))"]),
                ("--max-iter 0", ["pareto", good, "--onephase", "1", "--max-iter", "0"]),
                ("--max-iter above 127", ["pareto", good, "--onephase", "1", "--max-iter", "200"]),
                ("--max-iter not a number", ["pareto", good, "--onephase", "1", "--max-iter", "many"]),
                ("pareto --max-iter inf on a process with a loop", ["pareto", looped, "--onephase", "1", "--max-iter", "inf"]),
                ("determine --max-iter inf on a process with a loop", ["determine", looped, "--B", "5,5", "--max-iter", "inf"]),
                ("to_prism --max-iter inf on a process with a loop", ["to_prism", looped, "--max-iter", "inf"]),
            ]
        else:
            cases += [
                ("--max-iter in the mirror without loops", ["pareto", good, "--onephase", "1", "--max-iter", "2"]),
            ]
        for what, args in cases:
            r = self.run(args)
            if r.timed_out:
                rep.check("exit", False, f"{what}: no answer within {self.run.timeout}s (a hang)\n    {r.cmd()}")
                continue
            msg = r.err.strip()
            rep.check("exit", r.rc not in (0, None) and msg != "" and "panicked" not in msg,
                      f"{what}: exit {r.rc}, stderr {msg[:160]!r}\n    {r.cmd()}")
            if r.rc not in (0, None):
                with rep.lock:
                    rep.exit_codes[r.rc] = rep.exit_codes.get(r.rc, 0) + 1
        # a working command exits 0 and a budget by name reorders
        r = self.run(["determine", good, "--B", "{y: 5, x: 5}", "--arith", "exact"])
        rep.check("exit", r.rc == 0 and line_after(r.out, "answer") == "yes" and rationals(line_after(r.out, "B exact") or "") == [Fraction(5), Fraction(5)],
                  f"a budget by name: exit {r.rc} answer {line_after(r.out, 'answer')} B exact {line_after(r.out, 'B exact')}\n    {r.cmd()}")


# ---------------------------------------------------------------------------
# the instances
# ---------------------------------------------------------------------------


def load_gen():
    spec = importlib.util.spec_from_file_location("gen", HERE.parent / "loops" / "gen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def generated(gen, count: int, seed: int, loops: bool, work: Path) -> list:
    out = []
    for index in range(count):
        rng = random.Random(seed * 1_000_003 + index)
        n_tasks = rng.randint(3, 6)
        n_loops = rng.randint(0, 2) if loops else 0
        n_impacts = rng.randint(1, 3)
        n_choices = rng.randint(0, 2)
        tree = gen.generate(rng, n_tasks, n_loops, n_impacts, n_choices)
        has_loop = gen.count(tree, "loop") > 0
        out.append(Instance(f"gen-{seed}-{index}", gen.text(tree), has_loop, work))
    # a few choice-free instances with loops, for the series over K
    if loops:
        for index in range(4):
            rng = random.Random(seed * 1_000_003 + 10_000 + index)
            tree = gen.generate(rng, rng.randint(2, 4), rng.randint(1, 2), rng.randint(1, 2), 0)
            out.append(Instance(f"gen-loop-free-{seed}-{index}", gen.text(tree), True, work))
    return out


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default=os.environ.get("SDCPI_BIN", str(REPO / "target" / "release" / "sdcpi")), help="the sdcpi binary")
    ap.add_argument("--count", type=int, default=20, help="random instances (default 20)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-loops", action="store_true", help="skip every loop: for the mirror that rejects the loop syntax")
    ap.add_argument("--jobs", type=int, default=2, help="instances checked at once (default 2)")
    ap.add_argument("--work", default=None, help="the folder of everything generated (default: a fresh temporary folder)")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds a command may take on a small instance before it is a hang (default 60)")
    ap.add_argument("--only", default=None, help="run the instances whose name contains this alone")
    args = ap.parse_args()

    sdcpi = args.bin
    if not (os.path.isfile(sdcpi) and os.access(sdcpi, os.X_OK)):
        print(f"run.py: no executable at {sdcpi}; pass --bin", file=sys.stderr)
        return 2
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="sdcpi-cli-consistency-"))
    work.mkdir(parents=True, exist_ok=True)
    gen = load_gen()
    loops = not args.no_loops

    rep = Report()
    run = Runner(sdcpi, args.timeout, rep)
    suite = Suite(run, rep, loops)

    instances = [Instance(name, text, has_loop, work) for name, text, has_loop in HAND if loops or not has_loop]
    instances += generated(gen, args.count, args.seed, loops, work)
    if args.only:
        instances = [i for i in instances if args.only in i.name]
    print(f"sdcpi {sdcpi}\nwork {work}\n{len(instances)} instances, loops {'on' if loops else 'off'}, jobs {args.jobs}")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        list(pool.map(suite.instance, instances))
    suite.check_time_limit(gen, work, args.seed)
    suite.check_exit_codes(work)
    elapsed = time.time() - t0

    checks, passed, failed, skipped = rep.totals()
    print()
    print(f"{'check':14} {'made':>6} {'passed':>6} {'failed':>6} {'skipped':>7}")
    for name in sorted(rep.checks):
        s = rep.checks[name]
        print(f"{name:14} {s['checks']:6} {s['checks'] - s['failed']:6} {s['failed']:6} {s['skipped']:7}")
    print(f"{'total':14} {checks:6} {passed:6} {failed:6} {skipped:7}   in {elapsed:.1f}s")
    if rep.skips:
        print("\nskipped:")
        for name, msg in rep.skips[:20]:
            print(f"  [{name}] {msg}")
        if len(rep.skips) > 20:
            print(f"  ... {len(rep.skips) - 20} more")
    if rep.failures:
        print("\nfailures:")
        for name, msg in rep.failures:
            print(f"  [{name}] {msg}")
    if rep.exit_codes:
        print(f"\nexit codes of the refused invocations: {rep.exit_codes}")
    print(f"\nchecks {checks} passed {passed} failed {failed} skipped {skipped}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
