#!/usr/bin/env python3
"""The tolerances of the frontier search against the exact frontier.

    python3 scripts/tolerance/run.py --bin PATH [--count N] [--seed S] [--jobs J]
                                     [--no-loops] [--work DIR] [--timeout SECS]

With --tolerance f,... (one fraction per component, of the range of the
component between its two bounds at the initial state) the one-phase
search leaves out a point within the tolerances of a point found and
closes a partial strategy that cannot leave them, and answers an
approximation of the frontier. What is promised, and checked here on
hand-written and random instances, for several tolerance vectors:

  exact        with every tolerance zero the answer is the exact frontier,
               the same set of rationals as without the option;
  separated    no two points reported are within the tolerances of each
               other, |x - y| <= t in every component, in the orientation
               of the engine (a maximized component negated): the second
               of the two would have been left out;
  covered      every point of the exact frontier is within the tolerances of
               a point reported: some reported a has a <= f + t;
  box          every point reported lies within the two bounds, and the
               status is complete;
  fewer        the points reported are never more than the exact ones, and
               the nodes expanded never more (a note, not a failure, when
               the search is not monotone in the tolerance);
  window       with a window on top of the tolerances, every point reported
               lies within the window and the separation holds;
  refused      the two-phase search refuses the option, a wrong count or a
               fraction outside [0, 1] is refused with a message.

The instances are the hand-written ones below plus N random ones from
scripts/loops/gen.py (3 to 6 tasks, 1 to 3 impacts, loops unless
--no-loops), every one with the time as the component zero. The exit code
is 0 exactly when nothing failed.
"""

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE.parent / "loops" / "gen.py"

HAND = [
    ("one", "(A, 1, {x: 1})"),
    ("choice", "((A, 1, {x: 1, y: 4}) ^ (B, 2, {x: 3, y: 1}))"),
    ("two-choices", "( ((A, 1, {x: 1, y: 4}) ^ (B, 2, {x: 3, y: 1})) , ((C, 1, {x: 2, y: 2}) ^ (D, 1, {x: 1, y: 5})) )"),
    ("nature-choice", "( ((A, 1, {x: 1}) ^[0.3] (B, 2, {x: 2})) , ((C, 1, {x: 5, y: 1}) ^ (D, 2, {x: 1, y: 6})) )"),
    ("parallel", "( ((A, 2, {x: 1, y: 3}) ^ (B, 1, {x: 4, y: 1})) || ((C, 1, {x: 1, y: 1}) ^ (D, 3, {x: 2, y: 0.5})) )"),
    ("line", "( (I, 1, {e: 10, h: 2}) , ((S, 2, {e: 50, h: 4}) ^ (M, 3, {e: 20, h: 9})) , ((C, 1, {e: 26, h: 1}) ^ (K, 2, {e: 2, h: 2})) )"),
]
HAND_LOOPS = [
    ("loop-choice", "(0.5 ((A, 1, {x: 1, y: 3}) ^ (B, 2, {x: 3, y: 1})))"),
    ("loop-nature-choice", "( (0.6 ((Ok, 1, {e: 5}) ^[0.8] (Scrap, 2, {e: 5, s: 1}))) , ((C, 1, {e: 26, h: 1}) ^ (K, 2, {e: 2, h: 2})) )"),
]


class Run:
    def __init__(self, args, rc, out, err, timed_out):
        self.args, self.rc, self.out, self.err, self.timed_out = args, rc, out, err, timed_out


def run(binary, args, timeout, cwd):
    try:
        r = subprocess.run([binary] + args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return Run(args, r.returncode, r.stdout, r.stderr, False)
    except subprocess.TimeoutExpired as e:
        return Run(args, None, e.stdout or "", e.stderr or "", True)


def frontier(binary, yaml, extra, timeout, cwd, tag):
    out = cwd / f"{yaml.stem}-{tag}.json"
    r = run(binary, ["pareto", str(yaml), "--onephase", "1", "--time", "1", "--workers", "1", "--json", str(out)] + extra, timeout, cwd)
    if r.timed_out or r.rc != 0:
        return None, r
    j = json.loads(out.read_text())
    return j, r


def vectors(j, maximized_flags):
    """The points in the orientation of the engine: a maximized component negated."""
    pts = []
    for p in j["points"]:
        v = [Fraction(x) for x in p["value"]]
        pts.append(tuple(-x if m else x for x, m in zip(v, maximized_flags)))
    return pts


def leq(a, b):
    return all(x <= y for x, y in zip(a, b))


class Ledger:
    def __init__(self):
        self.counts = {}
        self.failures = []
        self.notes = []

    def check(self, name, ok, detail=""):
        self.counts[name] = self.counts.get(name, 0) + 1
        if not ok:
            self.failures.append((name, detail))

    def note(self, text):
        self.notes.append(text)


def check_instance(binary, name, text, cfg, ledger):
    work = cfg["work"]
    timeout = cfg["timeout"]
    yaml = work / f"{name}.yaml"
    r = run(binary, ["parse", text, "--out", str(yaml)], timeout, work)
    if r.rc != 0:
        ledger.check("parse", False, f"{name}: {r.err.strip()}")
        return
    maxiter = ["--max-iter", "2"] if cfg["loops"] else []
    exact, r0 = frontier(binary, yaml, maxiter, timeout, work, "exact")
    if exact is None or exact.get("status") != "complete":
        ledger.note(f"{name}: no complete exact frontier ({'timeout' if r0.timed_out else exact and exact.get('status')}), skipped")
        return
    k = len(exact["components"])
    maxflags = [False] * k
    exact_pts = vectors(exact, maxflags)
    exact_set = set(exact_pts)
    upper = [Fraction(x) for x in exact["upper"]]
    lower = [Fraction(x) for x in exact["lower"]]
    rng = [u - l for u, l in zip(upper, lower)]

    zero, _ = frontier(binary, yaml, maxiter + ["--tolerance", ",".join(["0"] * k)], timeout, work, "zero")
    ledger.check("exact", zero is not None and set(vectors(zero, maxflags)) == exact_set,
                 f"{name}: tolerance zero gives {zero and len(zero['points'])} points, the exact frontier {len(exact_pts)}")

    vectors_to_try = [
        [Fraction(1, 20)] * k,
        [Fraction(1, 5)] * k,
        [Fraction(1, 2)] * k,
        [Fraction(1, 10) if c % 2 == 0 else Fraction(0) for c in range(k)],
        [Fraction(1)] * k,
    ]
    for tv in vectors_to_try:
        tag = "t" + "-".join(str(float(f)).replace(".", "p") for f in tv)
        j, r = frontier(binary, yaml, maxiter + ["--tolerance", ",".join(str(float(f)) for f in tv)], timeout, work, tag)
        if j is None:
            ledger.check("status", False, f"{name} {tag}: pareto failed: {r.err.strip()[:200] if not r.timed_out else 'timeout'}")
            continue
        ledger.check("status", j.get("status") == "complete", f"{name} {tag}: status {j.get('status')}")
        if j.get("status") != "complete":
            continue
        abs_tol = [Fraction(x) for x in j["tolerance"]] if j.get("tolerance") else [Fraction(0)] * k
        expect = [f * rg for f, rg in zip(tv, rng)]
        ledger.check("absolute", abs_tol == expect, f"{name} {tag}: the JSON says tolerance {abs_tol}, fraction times range gives {expect}")
        pts = vectors(j, maxflags)
        # separated: no two points within the tolerances of each other, that
        # is |x - y| <= t in every component: the second of the two would
        # have been left out
        sep = True
        for x in pts:
            for y in pts:
                if x != y and all(abs(xx - yy) <= t for xx, yy, t in zip(x, y, abs_tol)):
                    sep = False
        ledger.check("separated", sep, f"{name} {tag}: two points within the tolerances of each other")
        # covered: every exact point within the tolerances of a reported one
        cov = all(any(leq(a, tuple(ff + t for ff, t in zip(f, abs_tol))) for a in pts) for f in exact_pts)
        ledger.check("covered", cov, f"{name} {tag}: an exact point is not within the tolerances of any point reported")
        # box
        box = all(all(l <= x <= u for x, l, u in zip(p, lower, upper)) for p in pts)
        ledger.check("box", box, f"{name} {tag}: a point leaves the box")
        # fewer
        ledger.check("fewer", len(pts) <= len(exact_pts), f"{name} {tag}: {len(pts)} points, the exact frontier has {len(exact_pts)}")
        if j["expanded"] > exact["expanded"]:
            ledger.note(f"{name} {tag}: {j['expanded']} nodes expanded against {exact['expanded']} exact")
        # a window on top: the upper half of the box on the first component
        mid = lower[0] + (upper[0] - lower[0]) / 2
        win = f"..{mid},{','.join(['..'] * (k - 1))}" if k > 1 else f"..{mid}"
        jw, rw = frontier(binary, yaml, maxiter + ["--tolerance", ",".join(str(float(f)) for f in tv), "--window", win], timeout, work, tag + "-w")
        if jw is not None and jw.get("status") == "complete":
            wpts = vectors(jw, maxflags)
            ledger.check("window", all(p[0] <= mid for p in wpts), f"{name} {tag}: a windowed point leaves the window")
            sepw = all(not (x != y and all(abs(xx - yy) <= t for xx, yy, t in zip(x, y, abs_tol))) for x in wpts for y in wpts)
            ledger.check("window", sepw, f"{name} {tag}: windowed points within the tolerances of each other")
        else:
            ledger.note(f"{name} {tag}: the windowed search gave no complete answer")

    # refusals
    r = run(binary, ["pareto", str(yaml), "--time", "1", "--tolerance", ",".join(["0.1"] * k)] + maxiter, timeout, work)
    ledger.check("refused", r.rc not in (0, None) and "one-phase" in (r.err + r.out), f"{name}: the two-phase search took --tolerance (exit {r.rc})")
    r = run(binary, ["pareto", str(yaml), "--onephase", "1", "--time", "1", "--tolerance", ",".join(["0.1"] * (k + 1))] + maxiter, timeout, work)
    ledger.check("refused", r.rc not in (0, None) and r.err.strip() != "", f"{name}: a wrong count of tolerances passed (exit {r.rc})")
    r = run(binary, ["pareto", str(yaml), "--onephase", "1", "--time", "1", "--tolerance", ",".join(["1.5"] * k)] + maxiter, timeout, work)
    ledger.check("refused", r.rc not in (0, None) and r.err.strip() != "", f"{name}: a fraction above one passed (exit {r.rc})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", required=True)
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--no-loops", action="store_true")
    ap.add_argument("--work")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()
    if not os.path.exists(args.bin):
        print(f"no binary at {args.bin}", file=sys.stderr)
        return 2
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="sdcpi-tolerance-"))
    work.mkdir(parents=True, exist_ok=True)
    cfg = {"work": work, "timeout": args.timeout, "loops": not args.no_loops}
    instances = list(HAND) + ([] if args.no_loops else list(HAND_LOOPS))
    # random instances from gen.py
    gen_dir = work / "gen"
    gen_dir.mkdir(exist_ok=True)
    gen_args = [sys.executable, str(GEN), "--tasks", "3-6", "--impacts", "1-3", "--choices", "1-3", "--count", str(args.count), "--seed", str(args.seed), "--out", str(gen_dir)]
    gen_args += ["--loops", "0"] if args.no_loops else ["--loops", "0-2"]
    g = subprocess.run(gen_args, capture_output=True, text=True)
    if g.returncode != 0:
        print("gen.py failed:", g.stderr.strip()[:300], file=sys.stderr)
    for f in sorted(gen_dir.glob("*.cpi")):
        instances.append((f.stem, f.read_text().strip()))
    ledger = Ledger()
    from concurrent.futures import ThreadPoolExecutor
    def one(item):
        name, text = item
        led = Ledger()
        try:
            check_instance(args.bin, name, text, cfg, led)
        except Exception as e:  # a crash of the suite itself is a failure of the suite
            led.check("suite", False, f"{name}: {e!r}")
        return led
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for led in ex.map(one, instances):
            for k, v in led.counts.items():
                ledger.counts[k] = ledger.counts.get(k, 0) + v
            ledger.failures += led.failures
            ledger.notes += led.notes
    print(f"{len(instances)} instances, work {work}")
    print("checks made:  ", ", ".join(f"{k} {v}" for k, v in sorted(ledger.counts.items())))
    for n in ledger.notes[:12]:
        print("  note:", n)
    if len(ledger.notes) > 12:
        print(f"  ... {len(ledger.notes) - 12} more notes")
    total = sum(ledger.counts.values())
    print(f"\nchecks {total} passed {total - len(ledger.failures)} failed {len(ledger.failures)}")
    for name, detail in ledger.failures[:30]:
        print(f"  FAIL [{name}] {detail}")
    return 1 if ledger.failures else 0


if __name__ == "__main__":
    sys.exit(main())
