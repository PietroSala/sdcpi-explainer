#!/usr/bin/env python3
"""The interval `continued` of a point, checked against its meaning.

    check.py --bin PATH                              the whole check
    check.py --bin PATH --count 40 --seed 3 --k 1,2,3 --jobs 2
    check.py --bin PATH --work DIR --time-limit 20 --verbose
    check.py --bin PATH --old-bin PATH               and the old interval, for information

Nothing of the tool is read but the frontier it writes. The semantics is
the transcription of `scripts/loops/check.py` (the phase and the count of
every node, the five ranks, the clauses), the bounds are its recursion of
LOOPS.md at an arbitrary state, truncated or unbounded, and the random
processes are those of `scripts/loops/gen.py`.

The meaning checked. A point is a strategy of the process truncated at K
passes, with its exact value, the time first. In the unbounded process the
exit at the bound is a draw: with probability p the region runs again, and
from that state on the run is no run of the truncated process, so that
whatever is decided from there on, what the run still pays lies between
the two bounds of the unbounded process at that state. Walk the truncated
process under the strategy; along a path let `stay` be the product of
`1 - p` over the forced exits met so far. A forced exit met after a path
of probability P, with stay S and cost C, is left by the unbounded run
with weight `P S p`, which then pays `C + X`, `X` between the bounds of
the whole unbounded process at the again-state, the state of the forced
exit with the region started again and every count as it is; the path goes
on with stay `S (1 - p)`. A complete run of probability P, stay S and cost
C weighs `P S C`. LO is the sum of the complete runs plus the sum over the
forced exits of `weight (C + L)`, HI the same with `U`, and `continued` is
`[LO - value, HI - value]`. An open history of probability P, stay S, cost
C and state s adds `P S (C + X at s)` to the two ends, against
`P (C + X_K at s)` in the two sums of the frontier.

The processes are hand-written ones (a loop followed by a choice, by a
nature node and a choice, a loop under a parallel node beside a choice, a
loop inside a loop, a loop whose region is a loop, two loops in sequence, a
choice inside a loop, a loop inside a choice, and the instances without
choices of `scripts/loops/check.py`), `--count` random ones with 3 to 7
tasks, 1 to 3 loops, 1 to 3 choices, 1 to 3 impacts, and `--count-free`
random ones without choices. For every process and every K of `--k`

    sdcpi pareto F.yaml --onephase 1 --time 1 --max-iter K --time-limit T
                        --print-strategy 1 --json out.json

A frontier that failed, that the time limit cut or that has more than
`--max-points` points is skipped and counted; at most `--sample` points of
a frontier are checked, a point whose witness has open histories is
skipped and counted. For every point checked:

  value       the truncated process walked under the strategy pays the
              value of the point, exactly.
  interval    LO and HI, with the bounds of the unbounded process at the
              again-states from the recursion of this side, are
              `value + continued.low` and `value + continued.up` exactly,
              in rationals, component by component; a point with no
              `continued` has LO = HI = value.
  sound-up    a behaviour follows the strategy until the first draw that
              runs a region again after its K-th pass and is free from
              there on. Its exact value in the process at K' = K + 1, and
              K + 2 when the walk is small enough, is at most HI in every
              component, for the behaviours that take at every later
              choice the low child, the high child, a child drawn by a
              seeded hash of the history since the draw, and, component by
              component, the dearest child (`worst`, the greatest value
              any behaviour reaches in that component) or the cheapest
              (`best`).
  sound-low   the same values are at least LO_K', the lower end computed
              with the lower bounds of the process truncated at K' at the
              again-states.
  low-order   LO_K' is at most LO, and LO_(K+1) at most LO_(K+2).
  no-choice   on a process without choices `value + continued.low` and
              `value + continued.up` enclose the expected cost of the
              unbounded process, in every component: the recursion where
              its two bounds agree, exactly, and the Markov chain of the
              unbounded process else, in floating point with a slack of
              1e-8; on the impacts the two ends are equal to it.

For a frontier accepted before it closed, an apex, `sdcpi determine F.yaml
--B <budget> --arith exact --time 1 --max-iter K --print-strategy 1
--print-size 1` is asked with the upper bound at the start and with
budgets between a point and that bound; an answer with open histories is
checked, the others are counted apart:

  apex-open      walking the partial strategy leaves as many open
                 histories as the tool says.
  apex-sum       the upper sum of the frontier, the cost of every closed
                 history plus, for an open one, the cost so far and the
                 upper bound of the truncated process at its state, is the
                 accepting sum printed, exactly.
  apex-interval  the `continued` printed is `[LO - lower sum, HI - upper
                 sum]` to its six decimals.
  apex-sound     three completions of the partial strategy, the missing
                 decisions drawn by a seeded hash of the history, each
                 extended to the behaviours of sound-up at K + 1, pay at
                 most HI, the accepting sum plus `continued.up`, and at
                 least LO_(K+1).

With `--old-bin` the same frontier is asked of the binary before the
change and the two intervals are compared point by point, for information:
on how many points they differ, on how many the new one contains the old
one, and how much wider it is on average. Nothing fails there.

Every check is counted. The exit code is 0 exactly when nothing failed, 1
when a check failed, 2 when the run cannot start.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction as F
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


lc = _load("loops_check", REPO / "scripts" / "loops" / "check.py")
gen = _load("loops_gen", REPO / "scripts" / "loops" / "gen.py")

sys.setrecursionlimit(200000)

# ---------------------------------------------------------------------------
# the processes
# ---------------------------------------------------------------------------

HAND = [
    ("loop-then-choice", "((0.5 (A, 1, {a: 1})) , ((X, 1, {a: 2}) ^ (Y, 2, {b: 1})))"),
    ("loop-nature-choice",
     "((0.5 (A, 2, {a: 1})) , (((B, 1, {b: 1}) ^[0.3] (C, 2, {a: 1})) , ((X, 1, {a: 2}) ^ (Y, 2, {b: 3}))))"),
    ("loop-under-parallel",
     "(((0.5 ((A1, 1, {}) ^[0.5] (A3, 3, {}))) , ((X, 1, {a: 1}) ^ (Y, 1, {b: 1}))) || "
     "((D, 2, {}) , ((E, 1, {}) ^[0.5] (F, 1, {}))))"),
    ("loop-beside-choice", "((0.5 (A, 1, {a: 1})) || ((D, 2, {}) , ((X, 1, {a: 3}) ^ (Y, 2, {b: 1}))))"),
    ("loop-in-loop", "(0.5 ((A, 1, {a: 1}) , ((0.2 (B, 1, {b: 1})) , ((X, 1, {a: 1}) ^ (Y, 1, {b: 1})))))"),
    ("loop-of-loop", "((0.5 (0.2 (A, 1, {a: 1}))) , ((X, 1, {a: 2}) ^ (Y, 1, {b: 1})))"),
    ("two-loops", "((0.2 (A, 1, {a: 1})) , ((0.8 (B, 2, {b: 1})) , ((X, 1, {a: 1}) ^ (Y, 3, {b: 1}))))"),
    ("choice-in-loop", "(0.5 ((A, 1, {a: 10}) ^ (B, 2, {a: 4})))"),
    ("loop-in-choice", "((0.5 (A, 1, {a: 2})) ^ ((B, 3, {b: 1}) , (C, 1, {a: 1})))"),
    ("choice-in-loop-beside", "((C, 2, {a: 1}) || (0.5 ((A, 1, {a: 2}) ^ (B, 1, {b: 2}))))"),
    ("two-loops-parallel-choice",
     "(((0.8 (A, 2, {a: 1})) || (0.2 (B, 3, {b: 1}))) , ((X, 1, {a: 1}) ^ (Y, 1, {b: 2})))"),
    ("choice-then-loop-beside-loop",
     "((((X, 1, {a: 1}) ^ (Y, 2, {b: 1})) , (0.5 (A, 1, {a: 1}))) || (0.5 ((B, 2, {b: 1}) ^ (C, 1, {a: 2}))))"),
]


def hand_free() -> list:
    """The hand-written instances of the loops harness that hold no choice."""
    return [("free-" + n[5:], t) for n, t in lc.HAND if " ^ " not in t]


def random_processes(count: int, seed: int, free: bool) -> list:
    out = []
    for index in range(count):
        rng = random.Random(f"gap:{seed}:{'free' if free else 'choice'}:{index}")
        n_tasks = rng.randint(3, 6 if free else 7)
        n_loops = rng.randint(1, 3)
        n_impacts = rng.randint(1, 3)
        n_choices = 0 if free else rng.randint(1, 3)
        tree = gen.generate(rng, n_tasks, n_loops, n_impacts, n_choices)
        out.append((f"{'free' if free else 'rand'}-{seed}-{index}", gen.text(tree)))
    return out


# ---------------------------------------------------------------------------
# vectors of rationals
# ---------------------------------------------------------------------------


def vadd(a, b):
    return [x + y for x, y in zip(a, b)]


def vscale(c, a):
    return [c * x for x in a]


def vle(a, b) -> bool:
    return all(x <= y for x, y in zip(a, b))


def shown(v) -> str:
    return "[" + ", ".join(str(x) for x in v) + "]"


# ---------------------------------------------------------------------------
# one step of the semantics, typed
# ---------------------------------------------------------------------------


class TooLarge(Exception):
    pass


class Open(Exception):
    pass


class Ctx:
    """The process with the time in front and the memories of one case."""

    def __init__(self, t):
        self.t = t
        self.k = t.k
        self.zero = [F(0)] * t.k
        self.steps = {}
        self.free = {}
        self.hashed = {}
        self.bound = {}
        self.budget = 0

    def spend(self, n=1):
        self.budget -= n
        if self.budget < 0:
            raise TooLarge()

    def step(self, state):
        """The clause due at the state, the bound on the passes left out:

        ("final",)
        ("choice", v, ((child, state), (child, state)))
        ("tau", cost, state)                       one successor, no draw
        ("nature", v, ((prob, child, state), (prob, child, state)))
        ("loop", v, p, passes, again, out)         the draw of the loop v
                                                   after `passes` passes
        """
        got = self.steps.get(state)
        if got is not None:
            return got
        t = self.t
        s, cnt = state
        rw = lc.rank_witness(t, s)
        if rw is None:
            got = ("final",)
        else:
            rank, v = rw
            n = t.nodes[v]
            exp = lc.expand(t, state, None, None)
            if rank == 1:
                got = ("choice", v, ((n.low, exp[2][0]), (n.high, exp[2][1])))
            elif rank == 4 and n.kind == "nature":
                (p0, _, _, s0), (p1, _, _, s1) = exp[1]
                got = ("nature", v, ((p0, n.low, s0), (p1, n.high, s1)))
            elif rank == 4:
                (p0, _, _, again), (_, _, _, out) = exp[1]
                got = ("loop", v, p0, cnt[n.low], again, out)
            else:
                (_, cost, _, nxt), = exp[1]
                got = ("tau", list(cost), nxt)
        self.steps[state] = got
        return got

    def bounds(self, state, K):
        """`(U, L)` of the whole process at the state, `K` passes or None."""
        key = (state, K)
        got = self.bound.get(key)
        if got is None:
            got = lc.bounds(self.t, state[0], state[1], self.t.root, K)
            self.bound[key] = got
        return got


def bit(*what) -> int:
    return hashlib.blake2b(repr(what).encode(), digest_size=2).digest()[0] & 1


def decide(strat, trail, st, fill):
    """The child the strategy starts at the choice `st` after the trail,
    None when it has no decision there and nothing fills it."""
    a = strat.get(trail)
    if a is None:
        if fill is None:
            return None
        a = st[2][bit("fill", fill, trail)][0]
    for child, nxt in st[2]:
        if child == a:
            return child, nxt
    raise ValueError(f"after {list(trail)} the strategy takes {a}, no child of the choice {st[1]}")


# ---------------------------------------------------------------------------
# the truncated process under the strategy: the value and the two ends
# ---------------------------------------------------------------------------


class Walk:
    def __init__(self, k):
        zero = [F(0)] * k
        self.paid = list(zero)      # sum of P C over the complete runs
        self.kept = list(zero)      # sum of P S C over the complete runs
        self.exits = {}             # again-state -> [weight, weight * C]
        self.opens = []             # (P, S, C, state)
        self.runs = 0
        self.forced = 0


def walk(ctx: Ctx, K: int, strat: dict) -> Walk:
    """The process truncated at K walked under the strategy, a history
    where it has no decision left open."""
    w = Walk(ctx.k)
    stack = [(lc.initial(ctx.t), (), F(1), F(1), ctx.zero)]
    while stack:
        state, trail, P, S, C = stack.pop()
        while True:
            ctx.spend()
            st = ctx.step(state)
            kind = st[0]
            if kind == "final":
                w.paid = vadd(w.paid, vscale(P, C))
                w.kept = vadd(w.kept, vscale(P * S, C))
                w.runs += 1
                break
            if kind == "tau":
                C = vadd(C, st[1])
                state = st[2]
            elif kind == "choice":
                got = decide(strat, trail, st, None)
                if got is None:
                    w.opens.append((P, S, C, state))
                    break
                trail = trail + (got[0],)
                state = got[1]
            elif kind == "nature":
                (p0, c0, s0), (p1, c1, s1) = st[2]
                stack.append((s1, trail + (c1,), P * p1, S, C))
                trail = trail + (c0,)
                P = P * p0
                state = s0
            else:
                _, v, p, passes, again, out = st
                if passes > K:
                    raise ValueError(f"the loop {v} made {passes} passes, past the bound {K}")
                if passes == K:
                    # the forced exit: no entry of the trail, one successor
                    weight = P * S * p
                    slot = w.exits.setdefault(again, [F(0), list(ctx.zero)])
                    slot[0] += weight
                    slot[1] = vadd(slot[1], vscale(weight, C))
                    w.forced += 1
                    S = S * (1 - p)
                    state = out
                else:
                    stack.append((out, trail + (v,), P * (1 - p), S, C))
                    trail = trail + (ctx.t.nodes[v].low,)
                    P = P * p
                    state = again
    return w


def ends(ctx: Ctx, w: Walk, K):
    """`(LO, HI)` of the walk with the bounds of the process at `K` passes,
    None the unbounded one, at the again-states and at the open states."""
    lo = list(w.kept)
    hi = list(w.kept)
    for again, (weight, wc) in w.exits.items():
        u, l = ctx.bounds(again, K)
        lo = vadd(lo, vadd(wc, vscale(weight, l)))
        hi = vadd(hi, vadd(wc, vscale(weight, u)))
    for P, S, C, state in w.opens:
        u, l = ctx.bounds(state, K)
        lo = vadd(lo, vscale(P * S, vadd(C, l)))
        hi = vadd(hi, vscale(P * S, vadd(C, u)))
    return lo, hi


def sums(ctx: Ctx, w: Walk, K):
    """The two sums of the frontier the walk stops at, lower and upper."""
    lo = list(w.paid)
    hi = list(w.paid)
    for P, S, C, state in w.opens:
        u, l = ctx.bounds(state, K)
        lo = vadd(lo, vscale(P, vadd(C, l)))
        hi = vadd(hi, vscale(P, vadd(C, u)))
    return lo, hi


# ---------------------------------------------------------------------------
# the behaviours past the bound, in the process at K' passes
# ---------------------------------------------------------------------------


def follow(ctx: Ctx, K: int, strat: dict, fill):
    """The process where a loop draws after its K-th pass too, walked under
    the strategy up to the first draw that runs a region again after its
    K-th pass: the cost of the runs that never meet one, weighted, and the
    departures `(probability, cost so far, state)`, the state being the
    one the draw leads to. No bound is read past K here, the departures
    being left as they start. `fill` completes a partial strategy."""
    base = list(ctx.zero)
    departures = []
    stack = [(lc.initial(ctx.t), (), F(1), ctx.zero)]
    while stack:
        state, trail, P, C = stack.pop()
        while True:
            ctx.spend()
            st = ctx.step(state)
            kind = st[0]
            if kind == "final":
                base = vadd(base, vscale(P, C))
                break
            if kind == "tau":
                C = vadd(C, st[1])
                state = st[2]
            elif kind == "choice":
                got = decide(strat, trail, st, fill)
                if got is None:
                    raise Open(f"no decision after {list(trail)} at the choice {st[1]}")
                trail = trail + (got[0],)
                state = got[1]
            elif kind == "nature":
                (p0, c0, s0), (p1, c1, s1) = st[2]
                stack.append((s1, trail + (c1,), P * p1, C))
                trail = trail + (c0,)
                P = P * p0
                state = s0
            else:
                _, v, p, passes, again, out = st
                if passes == K:
                    departures.append((P * p, C, again))
                    P = P * (1 - p)
                    state = out
                else:
                    stack.append((out, trail + (v,), P * (1 - p), C))
                    trail = trail + (ctx.t.nodes[v].low,)
                    P = P * p
                    state = again
    return base, departures


def free_value(ctx: Ctx, Kp: int, rule: str, state):
    """The expected cost still to pay from the state in the process at
    `Kp` passes, under a rule that reads the state alone: `low`, `high`,
    or, component by component, `worst` (the dearest child) and `best`."""
    key = (Kp, rule, state)
    got = ctx.free.get(key)
    if got is not None:
        return got
    ctx.spend()
    st = ctx.step(state)
    kind = st[0]
    if kind == "final":
        got = ctx.zero
    elif kind == "tau":
        got = vadd(st[1], free_value(ctx, Kp, rule, st[2]))
    elif kind == "choice":
        a = free_value(ctx, Kp, rule, st[2][0][1])
        b = free_value(ctx, Kp, rule, st[2][1][1])
        if rule == "low":
            got = a
        elif rule == "high":
            got = b
        elif rule == "worst":
            got = [max(x, y) for x, y in zip(a, b)]
        else:
            got = [min(x, y) for x, y in zip(a, b)]
    elif kind == "nature":
        (p0, _, s0), (p1, _, s1) = st[2]
        got = vadd(vscale(p0, free_value(ctx, Kp, rule, s0)), vscale(p1, free_value(ctx, Kp, rule, s1)))
    else:
        _, v, p, passes, again, out = st
        if passes >= Kp:
            got = free_value(ctx, Kp, rule, out)
        else:
            got = vadd(vscale(p, free_value(ctx, Kp, rule, again)), vscale(1 - p, free_value(ctx, Kp, rule, out)))
    ctx.free[key] = got
    return got


def hashed_value(ctx: Ctx, Kp: int, seed, start):
    """The same under the behaviour that takes, at every choice, the child
    a seeded hash of the history since `start` draws."""
    key = (Kp, seed, start)
    got = ctx.hashed.get(key)
    if got is not None:
        return got
    tag = hashlib.blake2b(repr(start).encode(), digest_size=8).hexdigest()
    total = list(ctx.zero)
    stack = [(start, (), F(1), ctx.zero)]
    while stack:
        state, trail, P, C = stack.pop()
        while True:
            ctx.spend()
            st = ctx.step(state)
            kind = st[0]
            if kind == "final":
                total = vadd(total, vscale(P, C))
                break
            if kind == "tau":
                C = vadd(C, st[1])
                state = st[2]
            elif kind == "choice":
                child, state = st[2][bit("free", seed, tag, trail)]
                trail = trail + (child,)
            elif kind == "nature":
                (p0, c0, s0), (p1, c1, s1) = st[2]
                stack.append((s1, trail + (c1,), P * p1, C))
                trail = trail + (c0,)
                P = P * p0
                state = s0
            else:
                _, v, p, passes, again, out = st
                if passes >= Kp:
                    state = out
                else:
                    stack.append((out, trail + (v,), P * (1 - p), C))
                    trail = trail + (ctx.t.nodes[v].low,)
                    P = P * p
                    state = again
    ctx.hashed[key] = total
    return total


RULES = ("low", "high", "hash", "worst", "best")


def behaviour(ctx: Ctx, Kp: int, rule: str, seed, base, departures):
    total = list(base)
    for P, C, state in departures:
        rest = hashed_value(ctx, Kp, seed, state) if rule == "hash" else free_value(ctx, Kp, rule, state)
        total = vadd(total, vscale(P, vadd(C, rest)))
    return total


# ---------------------------------------------------------------------------
# the binary
# ---------------------------------------------------------------------------


def run_tool(args: list, timeout: float):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timed out"


def frontier(binary, yaml: Path, K: int, out: Path, cfg):
    """The JSON of the frontier, or the reason there is none."""
    rc, _, err = run_tool([binary, "pareto", str(yaml), "--onephase", "1", "--time", "1", "--max-iter", str(K),
                           "--time-limit", str(cfg["time_limit"]), "--print-strategy", "1", "--json", str(out)],
                          cfg["time_limit"] + 60)
    if rc is None:
        return None, "the frontier timed out"
    if rc != 0:
        return None, f"pareto exit {rc}: {err.strip()[:200]}"
    try:
        return json.loads(out.read_text()), None
    except Exception as e:
        return None, f"the JSON does not read: {e}"


def ceil6(x: F) -> str:
    """The value rounded up at the sixth decimal, as a budget is written."""
    n = -((-x * 10**6).__floor__())
    sign = "-" if n < 0 else ""
    whole, part = divmod(abs(n), 10**6)
    return f"{sign}{whole}.{part:06d}"


VEC = re.compile(r"\[([^\]]*)\]")


def parse_determine(out: str) -> dict:
    d = {"decisions": {}}
    for line in out.splitlines():
        body = line.strip()
        if line.startswith("answer"):
            d["answer"] = body.split()[1]
        elif line.startswith("accepting"):
            d["accepting"] = [F(x.split("(")[0].strip()) for x in VEC.search(line).group(1).split(",")]
        elif line.startswith("continued"):
            a, b = VEC.findall(line)
            d["continued"] = ([float(x) for x in a.split(",")], [float(x) for x in b.split(",")])
        elif line.startswith("open won"):
            d["open"] = int(body.split()[2])
        elif body.startswith("after ["):
            m = re.match(r"after \[([^\]]*)\] take (\d+)", body)
            trail = tuple(int(x) for x in m.group(1).split(",") if x.strip())
            d["decisions"][trail] = int(m.group(2))
    return d


# ---------------------------------------------------------------------------
# the checks of one process at one K
# ---------------------------------------------------------------------------


class Result:
    def __init__(self, name, text, K):
        self.name, self.text, self.K = name, text, K
        self.counts = {}
        self.fails = []
        self.skips = {}
        self.info = {}
        self.seconds = 0.0

    def ok(self, check):
        self.counts[check] = self.counts.get(check, 0) + 1

    def fail(self, check, detail):
        self.counts[check] = self.counts.get(check, 0) + 1
        self.fails.append({"check": check, "detail": detail})

    def skip(self, why, n=1):
        self.skips[why] = self.skips.get(why, 0) + n

    def note(self, key, n=1):
        self.info[key] = self.info.get(key, 0) + n

    def pack(self):
        return {"name": self.name, "text": self.text, "K": self.K, "counts": self.counts, "fails": self.fails,
                "skips": self.skips, "info": self.info, "seconds": round(self.seconds, 2)}


def check_case(spec):
    name, text, K, cfg = spec
    res = Result(name, text, K)
    started = time.time()
    try:
        _check_case(res, name, text, K, cfg)
    except Exception as e:  # a crash of the harness is a failure, not a silence
        res.fail("harness", f"{type(e).__name__}: {e}")
    res.seconds = time.time() - started
    return res.pack()


def sound_checks(res: Result, ctx: Ctx, K: int, strat, fill, lo, hi, w: Walk, cfg, where: str, prefix: str, seed):
    """The behaviours past the bound against HI and LO_K'."""
    try:
        ctx.budget = cfg["cap"]
        base, departures = follow(ctx, K, strat, fill)
    except TooLarge:
        res.skip(f"{prefix}sound: the walk past the bound is too large")
        return
    previous = None
    for Kp in (K + 1, K + 2):
        lo_p, _ = ends(ctx, w, Kp)
        if prefix == "":
            if vle(lo_p, lo):
                res.ok("low-order")
            else:
                res.fail("low-order", f"{where}: LO at {Kp} passes {shown(lo_p)} is past LO {shown(lo)}")
            if previous is not None:
                if vle(previous, lo_p):
                    res.ok("low-order")
                else:
                    res.fail("low-order", f"{where}: LO at {Kp - 1} passes {shown(previous)} is past LO at {Kp} "
                             f"passes {shown(lo_p)}")
        previous = lo_p
        for rule in RULES:
            try:
                ctx.budget = cfg["cap"]
                total = behaviour(ctx, Kp, rule, seed, base, departures)
            except TooLarge:
                res.skip(f"{prefix}sound: a behaviour at K + {Kp - K} is too large")
                continue
            up_name, low_name = f"{prefix}sound-up" if prefix == "" else "apex-sound", \
                f"{prefix}sound-low" if prefix == "" else "apex-sound"
            if vle(total, hi):
                res.ok(up_name)
            else:
                res.fail(up_name, f"{where}: the behaviour `{rule}` pays {shown(total)} at {Kp} passes, past HI "
                         f"{shown(hi)}")
            if vle(lo_p, total):
                res.ok(low_name)
            else:
                res.fail(low_name, f"{where}: the behaviour `{rule}` pays {shown(total)} at {Kp} passes, below "
                         f"LO at {Kp} passes {shown(lo_p)}")
            if departures and total != base and prefix == "":
                res.note("behaviours that leave the bound")
        if prefix != "":
            break


def _check_case(res: Result, name, text, K, cfg):
    binary = cfg["bin"]
    work = Path(cfg["work"])
    yaml = work / f"{name}.yaml"
    rc, _, err = run_tool([binary, "parse", text, "--out", str(yaml)], 60)
    if rc != 0:
        res.fail("parse", f"exit {rc}: {err.strip()}")
        return
    plain = lc.read_instance(yaml)
    t = plain.with_time()
    ctx = Ctx(t)
    k = t.k
    j, why = frontier(binary, yaml, K, work / f"{name}-K{K}.json", cfg)
    if j is None:
        res.skip(f"frontier: {why.split(':')[0]}")
        return
    if j.get("status") != "complete":
        res.skip("frontier: cut by the time limit")
        return
    points = j.get("points", [])
    if len(points) > cfg["max_points"]:
        res.skip("frontier: too many points")
        return
    res.note("frontiers")
    rng = random.Random(f"{cfg['seed']}:{name}:{K}")
    order = list(range(len(points)))
    if len(order) > cfg["sample"]:
        order = sorted(rng.sample(order, cfg["sample"]))
        res.note("points left out of the sample", len(points) - len(order))
    walks = {}
    for i in order:
        p = points[i]
        value = [F(x) for x in p["value"]]
        where = f"K={K} point {i + 1} {shown(value)}"
        if p.get("open", 0) != 0:
            res.skip("point: the witness has open histories")
            continue
        if "strategy" not in p:
            res.skip("point: no strategy in the JSON")
            continue
        strat = {tuple(trail): action for trail, action in p["strategy"]}
        try:
            ctx.budget = cfg["cap"]
            w = walk(ctx, K, strat)
        except TooLarge:
            res.skip("point: the walk is too large")
            continue
        if w.opens:
            res.fail("value", f"{where}: the witness is said closed and the walk meets {len(w.opens)} choices with "
                     f"no decision")
            continue
        walks[i] = w
        res.note("points")
        # (1) the value
        if w.paid == value:
            res.ok("value")
        else:
            res.fail("value", f"{where}: the walk pays {shown(w.paid)}")
        # (2) the interval
        lo, hi = ends(ctx, w, None)
        cont = p.get("continued")
        low = [F(x) for x in cont["low"]] if cont else [F(0)] * k
        up = [F(x) for x in cont["up"]] if cont else [F(0)] * k
        if cont:
            res.note("points with continued")
            if any(x < 0 for x in low):
                res.note("points with a negative component of continued.low")
        if (cont is None) != (not w.exits):
            res.note("points where continued is there and no forced exit is met, or the converse")
        if lo == vadd(value, low) and hi == vadd(value, up):
            res.ok("interval")
        else:
            res.fail("interval", f"{where}: LO {shown(lo)} HI {shown(hi)} from {w.forced} forced exits over "
                     f"{w.runs} runs; the tool says value + low {shown(vadd(value, low))}, value + up "
                     f"{shown(vadd(value, up))} (continued low {shown(low)} up {shown(up)})")
        if not vle(lo, hi):
            res.fail("interval", f"{where}: LO {shown(lo)} is past HI {shown(hi)}")
        # (3) and (4) the behaviours past the bound
        sound_checks(res, ctx, K, strat, None, lo, hi, w, cfg, where, "", f"{cfg['seed']}:{name}:{K}:{i}")

    # (5) a process without choices: the unbounded value
    if not plain.choices and points:
        no_choice(res, ctx, K, points[0], cfg)

    # (6) the interval of the binary before the change, for information
    if cfg["old_bin"]:
        old_interval(res, ctx, K, yaml, work / f"{name}-K{K}-old.json", points, order, cfg)

    # (7) the frontiers accepted before they closed
    if cfg["apex"] > 0 and plain.choices:
        apex_checks(res, ctx, K, yaml, points, order, cfg, rng)


def no_choice(res: Result, ctx: Ctx, K, p, cfg):
    t = ctx.t
    k = t.k
    value = [F(x) for x in p["value"]]
    cont = p.get("continued")
    low = vadd(value, [F(x) for x in cont["low"]] if cont else [F(0)] * k)
    up = vadd(value, [F(x) for x in cont["up"]] if cont else [F(0)] * k)
    u_inf, l_inf = lc.bounds_at_start(t, None)
    chain = None
    if u_inf != l_inf:
        try:
            chain, _, converged = lc.unbounded_chain(t, cfg["chain_cap"])
            if not converged:
                chain = None
        except lc.TooManyStates:
            chain = None
        if chain is None:
            res.skip("no-choice: the chain of the unbounded process is too large or did not converge")
    for c in range(k):
        tag = f"K={K} component {c} ({t.names[c]})"
        if u_inf[c] == l_inf[c]:
            v = u_inf[c]
            if low[c] <= v <= up[c]:
                res.ok("no-choice")
            else:
                res.fail("no-choice", f"{tag}: [{low[c]}, {up[c]}] misses the unbounded value {v}")
            if c > 0:
                if low[c] == v and up[c] == v:
                    res.ok("no-choice")
                else:
                    res.fail("no-choice", f"{tag}: the ends {low[c]} and {up[c]} are not the unbounded value {v}")
        elif chain is not None:
            v = chain[c]
            if float(low[c]) - 1e-8 <= v <= float(up[c]) + 1e-8:
                res.ok("no-choice")
            else:
                res.fail("no-choice", f"{tag}: [{float(low[c]):.10g}, {float(up[c]):.10g}] misses the unbounded "
                         f"value {v:.10g} of the chain")


def old_interval(res: Result, ctx: Ctx, K, yaml, out, points, order, cfg):
    j, why = frontier(cfg["old_bin"], yaml, K, out, cfg)
    if j is None or j.get("status") != "complete":
        res.note("old: no frontier from the old binary")
        return
    old = {}
    for q in j.get("points", []):
        key = (tuple(q["value"]), tuple(sorted((tuple(a), b) for a, b in q.get("strategy", []))))
        old[key] = q
    k = ctx.k
    for i in order:
        p = points[i]
        key = (tuple(p["value"]), tuple(sorted((tuple(a), b) for a, b in p.get("strategy", []))))
        q = old.get(key)
        if q is None:
            res.note("old: points with no point of the same value and strategy")
            continue
        res.note("old: points compared")
        zero = {"low": ["0"] * k, "up": ["0"] * k}
        a, b = p.get("continued") or zero, q.get("continued") or zero
        nl, nu = [F(x) for x in a["low"]], [F(x) for x in a["up"]]
        ol, ou = [F(x) for x in b["low"]], [F(x) for x in b["up"]]
        if (nl, nu) == (ol, ou):
            continue
        res.note("old: points where the interval differs")
        if vle(nl, ol) and vle(ou, nu):
            res.note("old: points where the new interval contains the old one")
        else:
            res.note("old: points where the new interval does not contain the old one")
        for c in range(k):
            res.info["old: width new"] = res.info.get("old: width new", 0.0) + float(nu[c] - nl[c])
            res.info["old: width old"] = res.info.get("old: width old", 0.0) + float(ou[c] - ol[c])
        res.note("old: components compared", k)


def apex_checks(res: Result, ctx: Ctx, K, yaml, points, order, cfg, rng):
    t = ctx.t
    k = t.k
    u0, _ = lc.bounds_at_start(t, K)
    budgets = [list(u0)]
    picks = [i for i in order]
    rng.shuffle(picks)
    for i, lam in zip(picks, (F(1, 2), F(1, 5), F(3, 4), F(1, 10))):
        value = [F(x) for x in points[i]["value"]]
        budgets.append([v + lam * (u - v) for v, u in zip(value, u0)])
    budgets = budgets[: cfg["apex"]]
    for b, budget in enumerate(budgets):
        text = ",".join(ceil6(x) for x in budget)
        rc, out, err = run_tool([cfg["bin"], "determine", str(yaml), "--B", text, "--arith", "exact", "--time", "1",
                                 "--max-iter", str(K), "--print-strategy", "1", "--print-size", "1", "--timeout",
                                 str(cfg["time_limit"])], cfg["time_limit"] + 60)
        d = parse_determine(out)
        if rc is None or d.get("answer") != "yes" or "accepting" not in d or "open" not in d:
            res.skip("apex: determine gave no accepted frontier to read")
            continue
        if d["open"] == 0:
            res.note("apex: accepted frontiers that are closed, not checked here")
            continue
        where = f"K={K} budget [{text}]"
        strat = d["decisions"]
        try:
            ctx.budget = cfg["cap"]
            w = walk(ctx, K, strat)
        except TooLarge:
            res.skip("apex: the walk is too large")
            continue
        res.note("apex: frontiers with open histories")
        if len(w.opens) == d["open"]:
            res.ok("apex-open")
        else:
            res.fail("apex-open", f"{where}: the walk leaves {len(w.opens)} histories open, the tool says {d['open']}")
        s_lo, s_hi = sums(ctx, w, K)
        if s_hi == d["accepting"]:
            res.ok("apex-sum")
        else:
            res.fail("apex-sum", f"{where}: the upper sum is {shown(s_hi)}, the accepting sum printed "
                     f"{shown(d['accepting'])}")
        lo, hi = ends(ctx, w, None)
        mine_low = [float(x - y) for x, y in zip(lo, s_lo)]
        mine_up = [float(x - y) for x, y in zip(hi, s_hi)]
        tool_low, tool_up = d.get("continued", ([0.0] * k, [0.0] * k))
        if all(abs(x - y) <= 1.5e-6 for x, y in zip(mine_low + mine_up, tool_low + tool_up)):
            res.ok("apex-interval")
        else:
            res.fail("apex-interval", f"{where}: LO - lower sum {mine_low}, HI - upper sum {mine_up}; the tool "
                     f"prints continued {tool_low} to {tool_up} ({len(w.opens)} open histories, {w.forced} forced "
                     f"exits)")
        for n in range(3):
            fill = f"{cfg['seed']}:{b}:{n}"
            try:
                sound_checks(res, ctx, K, strat, fill, lo, hi, w, cfg, f"{where} completion {n}", "apex-", fill)
            except Open as e:
                res.fail("apex-sound", f"{where}: {e}")


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default=os.environ.get("SDCPI_BIN") or str(REPO / "target" / "release" / "sdcpi"))
    ap.add_argument("--old-bin", default=None, help="the binary before the change, for the comparison of the intervals")
    ap.add_argument("--count", type=int, default=40, help="random processes with choices")
    ap.add_argument("--count-free", type=int, default=None, help="random processes without choices (default count / 4)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--k", default="1,2,3", help="the bounds on the passes")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--work", default=None, help="where the instance files, the frontiers and the report go")
    ap.add_argument("--time-limit", type=float, default=20.0, help="seconds per frontier")
    ap.add_argument("--sample", type=int, default=30, help="points checked per frontier")
    ap.add_argument("--max-points", type=int, default=400, help="a frontier with more points is skipped")
    ap.add_argument("--cap", type=int, default=400_000, help="steps one walk may take")
    ap.add_argument("--chain-cap", type=int, default=200_000, help="states of the chain of the unbounded process")
    ap.add_argument("--apex", type=int, default=3, help="budgets asked of determine per frontier, 0 for none")
    ap.add_argument("--no-hand", action="store_true", help="the random processes alone")
    ap.add_argument("--text", action="append", default=[], help="a process in the grammar, checked as well")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not Path(args.bin).exists():
        print(f"check.py: no binary at {args.bin}; give --bin or set SDCPI_BIN", file=sys.stderr)
        return 2
    if args.old_bin and not Path(args.old_bin).exists():
        print(f"check.py: no binary at {args.old_bin}", file=sys.stderr)
        return 2
    try:
        ks = [int(x) for x in args.k.split(",") if x.strip()]
    except ValueError:
        print(f"check.py: --k wants numbers separated by commas, found {args.k!r}", file=sys.stderr)
        return 2
    work = Path(args.work) if args.work else Path.cwd() / "gap-work"
    work.mkdir(parents=True, exist_ok=True)
    cfg = {"bin": args.bin, "old_bin": args.old_bin, "work": str(work), "time_limit": args.time_limit,
           "sample": args.sample, "max_points": args.max_points, "cap": args.cap, "chain_cap": args.chain_cap,
           "apex": args.apex, "seed": args.seed}
    free_count = args.count_free if args.count_free is not None else args.count // 4
    processes = [] if args.no_hand else list(HAND) + hand_free()
    processes += [(f"text-{i}", x) for i, x in enumerate(args.text)]
    processes += random_processes(args.count, args.seed, False) + random_processes(free_count, args.seed, True)
    specs = [(name, text, K, cfg) for name, text in processes for K in ks]
    print(f"{len(processes)} processes, K in {ks}, {len(specs)} frontiers, {args.jobs} jobs, binary {args.bin}")
    started = time.time()
    results = []

    def take(r):
        results.append(r)
        if args.verbose:
            made = sum(r["counts"].values())
            print(f"  {r['name']} K={r['K']}: {made} checks, {len(r['fails'])} failed, "
                  f"{sum(r['skips'].values())} skipped, {r['seconds']}s", flush=True)

    if args.jobs > 1 and len(specs) > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for r in pool.map(check_case, specs):
                take(r)
    else:
        for spec in specs:
            take(check_case(spec))
    elapsed = time.time() - started

    counts, failed, skips, info = {}, {}, {}, {}
    for r in results:
        for c, n in r["counts"].items():
            counts[c] = counts.get(c, 0) + n
        for f in r["fails"]:
            failed[f["check"]] = failed.get(f["check"], 0) + 1
        for s, n in r["skips"].items():
            skips[s] = skips.get(s, 0) + n
        for s, n in r["info"].items():
            info[s] = info.get(s, 0) + n
    total = sum(counts.values())
    bad = sum(failed.values())
    print(f"\n{len(results)} frontiers asked, {elapsed:.1f}s")
    print(f"checks made:   {total}  (" + ", ".join(f"{c} {n}" for c, n in sorted(counts.items())) + ")")
    print(f"checks failed: {bad}" + ("  (" + ", ".join(f"{c} {n}" for c, n in sorted(failed.items())) + ")" if bad else ""))
    print(f"skipped:       {sum(skips.values())}")
    for s, n in sorted(skips.items()):
        print(f"    {n:6d}  {s}")
    print("for information:")
    for s, n in sorted(info.items()):
        if s.startswith("old: width"):
            continue
        print(f"    {n:6d}  {s}")
    if info.get("old: components compared"):
        n = info["old: components compared"]
        new_w, old_w = info.get("old: width new", 0.0), info.get("old: width old", 0.0)
        print(f"    where the two intervals differ the new one is {new_w / n:.6g} wide per component on average, "
              f"the old one {old_w / n:.6g}" + (f", a ratio of {new_w / old_w:.4g}" if old_w else ""))
    shown_fails = 0
    for r in results:
        if not r["fails"]:
            continue
        print(f"\n{r['name']}  K={r['K']}  {r['text']}")
        for f in r["fails"]:
            shown_fails += 1
            if shown_fails <= 60:
                print(f"    {f['check']}: {f['detail']}")
    if shown_fails > 60:
        print(f"\n({shown_fails - 60} more failures in the report)")
    report = work / "report.json"
    report.write_text(json.dumps({"binary": args.bin, "seed": args.seed, "k": ks, "checks": total, "failed": bad,
                                  "counts": counts, "failures": failed, "skips": skips, "info": info,
                                  "seconds": round(elapsed, 1), "results": results}, indent=1))
    print(f"\nreport: {report}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
