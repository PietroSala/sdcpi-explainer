#!/usr/bin/env python3
"""The explainer of `sdcpi` checked end to end, on its outputs alone.

    run.py --bin PATH                               the whole suite
    run.py --bin PATH --count 60 --seed 3 --k 1,2,3 --jobs 2
    run.py --bin PATH --work DIR --port 8437 --timeout 60

Nothing of the tool is trusted but what it writes. The semantics the checks
replay is the one of `check_draws.py`, an implementation independent of the
engine (identifiers in-order from 0 there, from 1 in the tool); the instance
file the tool writes is read by the reader of `scripts/loops/check.py`, and
the random processes are drawn by `scripts/loops/gen.py`.

The processes are hand-written ones covering every construct (a loop over a
choice, a choice whose child is a loop, a loop whose region is a loop,
natures in parallel followed by a choice, a choice under two nested loops,
a nature and a choice inside one loop, a choice beside a loop in parallel,
the running example of the README) and `--count` random ones, 3 to 6 tasks,
0 to 2 loops, 1 to 2 impacts. For every process and every K of `--k` (the
first K alone on a process without loops) the frontier is asked with

    sdcpi pareto F.yaml --onephase 1 --time 1 --max-iter K --explain 1
                        --print-strategy 1 --json out.json --time-limit T

A frontier that is cut, that failed, or that has more than `--max-points`
points is skipped; at most `--sample` points of a frontier are checked, a
point whose strategy has more than `--max-decisions` decisions is skipped.

What is checked, for every sampled point with its strategy and explanation:

  classify   every decision (trail, action) of the strategy is replayed on
             the simulator, its entries typed by walking the clauses (a
             decision at a choice, a draw at a nature node, a draw of a loop
             that runs it again or leaves it below the bound, nothing at a
             forced exit); the assignment by occurrences of its draws is
             computed, the tree of the choice node the trail ends at is
             evaluated on it (a test on (node, occurrence) follows the
             branch whose value matches, absent when the draw was not
             taken), and the leaf must be the action;
  trees      one tree per choice node decided and none for another node;
  recovered  when the witness has no open history, the whole process is
             walked on the simulator deciding at every choice by the trees
             alone: every choice met has a tree and a matching branch, and
             the expected cost so obtained, the time and then the impacts,
             is the exact value of the point, in rationals;
  coverage   on the same walk, the decisions met are exactly the decisions
             of the strategy;
  minimal    for a tree claimed minimal with at most 9 rows and 8 features,
             an exhaustive search independent of the tool (every pair of
             tests and depth a tree on a set of rows may have, no
             lexicographic shortcut) gives the fewest tests and, among the
             trees with the fewest tests, the least depth: both must equal
             `tests` and `depth`;
  shape      `tests` and `depth` are those of the tree carried, `histories`
             is the number of decisions at that choice, no test repeats a
             feature along a path, every test has two branches at least,
             every branch value is one met by a row that reaches it, the
             nodes named are of the right kind;
  determine  `sdcpi explain F.yaml --B <the value, rounded up at the sixth
             decimal> --arith exact --time 1 --max-iter K --explain-json E
             --print-strategy 1` answers yes, and its trees classify its
             own strategy (the checks above, named `determine-*`);
  page       a server of the suite, `sdcpi serve --port P --saved
             WORK/store.json`: `POST /api/explain` with the process, time=1,
             max_iter=K and the decisions of the point gives the trees of
             the command line, up to the node objects;
  page-malformed
             decisions with no `>`, an identifier out of range, an action
             that is no child of the choice the trail ends at, a trail that
             is no history, a trail that ends the run, two actions after
             one history: the answer is {"error": ...};
  page-mutation
             the decisions of a point changed at random, one entry replaced,
             dropped or added: the simulator says whether they still are
             decisions at choices, the server must answer an error exactly
             when they are not, and otherwise trees that classify them;
  page-alive the server still answers after every malformed request.

The server is started on `--port`, refused when something already answers
there, and killed at the end. Every file lands under `--work`, by default a
fresh temporary folder; the failures are written in full to
`WORK/failures.json`. The exit code is 0 exactly when nothing failed, 1 when
a check failed, 2 when the run could not start.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction as F
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts" / "loops"))

import check_draws as cd  # noqa: E402  the independent simulator
import check as loops_check  # noqa: E402  the reader of the instance file
import gen as loops_gen  # noqa: E402  the random processes

ABSENT = ("absent",)
AGAIN = ("again",)
OUT = ("out",)

HAND = [
    ("loop-over-choice", "(0.5 ((A, 1, {x: 5}) ^ (B, 3, {x: 1})))"),
    ("choice-of-loop", "(((0.5 (A, 1, {x: 2})) ^ (B, 2, {x: 3})) , ((C, 1, {x: 4}) ^ (D, 3, {x: 1})))"),
    ("loop-of-loop", "(0.5 (0.4 ((A, 1, {x: 4}) ^ (B, 2, {x: 1}))))"),
    ("natures-in-parallel-then-choice",
     "((((A, 1, {x: 1}) ^[0.3] (B, 2, {x: 2})) || ((C, 1, {y: 1}) ^[0.6] (D, 3, {y: 2}))) , ((E, 1, {x: 5}) ^ (F, 4, {y: 3})))"),
    ("choice-under-two-loops",
     "(0.5 ((S, 1, {x: 1}) , (0.3 (((A, 1, {x: 3}) ^ (B, 2, {y: 2})) , ((N, 1) ^[0.5] (M, 2, {x: 1}))))))"),
    ("nature-and-choice-in-loop", "(0.6 (((A, 1, {x: 1}) ^[0.5] (B, 2, {y: 1})) , ((C, 1, {x: 3}) ^ (D, 3, {y: 2}))))"),
    ("choice-beside-loop", "(((0.5 ((A, 1, {x: 1}) ^[0.4] (B, 2))) || ((C, 2, {x: 2}) ^ (D, 1, {y: 3}))) , ((E, 1, {x: 2}) ^ (G, 2, {y: 1})))"),
    ("loop-then-choice", "((0.5 ((A, 1, {x: 1}) ^[0.5] (B, 3, {y: 2}))) , ((C, 1, {x: 6}) ^ (D, 4, {y: 1})))"),
    ("readme-example",
     "( (Intake, 1, {euro: 10, hours: 2}) , ( (Swap, 2, {euro: 50, hours: 4}) ^ ( (Mend, 3, {euro: 20, hours: 9}) , "
     "( (Redo, 2, {euro: 40, hours: 6}) ^[0.3] (Pass, 1, {hours: 1}) ) ) ) , "
     "( (Check, 1, {euro: 6, hours: 1}) || (File, 1, {euro: 4, hours: 1}) ) , "
     "( (Courier, 1, {euro: 26, hours: 1}) ^ (Counter, 2, {euro: 2, hours: 2}) ) )"),
]


# ---------------------------------------------------------------------------
# the process on the simulator
# ---------------------------------------------------------------------------

KINDS = {"task": cd.TASK, "sequence": cd.SEQ, "parallel": cd.PAR, "choice": cd.CHOICE, "nature": cd.NATURE,
         "loop": cd.LOOP}


class Process:
    """The tree of the instance file on the simulator: the node `i` of the
    simulator is the node `i + 1` of the tool. The impacts are kept here as
    vectors, the simulator carrying none."""

    def __init__(self, yaml: Path):
        ct = loops_check.read_instance(yaml)
        order = []

        def inorder(v):
            n = ct.nodes[v]
            if n.low:
                inorder(n.low)
            order.append(v)
            if n.high:
                inorder(n.high)
        inorder(ct.root)
        if order != list(range(1, ct.n + 1)):
            raise ValueError(f"the identifiers of the instance are not in-order: {order}")
        b = cd.Tree()

        def build(v):
            n = ct.nodes[v]
            if n.kind == "task":
                return b.add(cd.TASK, dur=n.duration, impact=F(0))
            if n.kind == "loop":
                return b.add(cd.LOOP, build(n.low), prob=n.prob)
            lo, hi = build(n.low), build(n.high)
            return b.add(KINDS[n.kind], lo, hi, prob=n.prob if n.kind == "nature" else None)
        self.t = b.finish(build(ct.root))
        if self.t.n != ct.n:
            raise ValueError("the simulator tree has another size")
        for i in range(ct.n):
            if self.t.kind[i] != KINDS[ct.nodes[i + 1].kind]:
                raise ValueError(f"node {i + 1} is not the same on the simulator")
        self.k = ct.k + 1                       # the time in front
        self.n = ct.n
        self.impact = [None] * ct.n
        for i in range(ct.n):
            n = ct.nodes[i + 1]
            if n.kind == "task":
                imp = list(n.impact) if n.impact is not None else []
                self.impact[i] = imp + [F(0)] * (ct.k - len(imp))

    def children(self, tool_id):
        return [c + 1 for c in self.t.children(tool_id - 1)]

    def kind(self, tool_id):
        return self.t.kind[tool_id - 1]

    def advance(self, s, nxt):
        """What a fast forward from `s` to `nxt` pays: the time, then the
        impacts of the tasks it closes."""
        ph = s[0]
        running = [u for u in self.t.tasks if ph[u] >= 0]
        step = min(self.t.dur[u] - ph[u] for u in running)
        cost = [F(step)] + [F(0)] * (self.k - 1)
        for u in running:
            if nxt[0][u] == cd.DONE:
                for c, x in enumerate(self.impact[u]):
                    cost[c + 1] += x
        return cost


class BadTrail(Exception):
    pass


class Replayer:
    """The histories the trails write, replayed on the simulator and typed
    by the clauses. The state reached after every prefix is kept, the
    decisions of a strategy sharing their prefixes."""

    def __init__(self, proc: Process, K: int):
        self.p, self.t, self.K = proc, proc.t, K
        self.memo = {(): (cd.initial(self.t), {}, {})}

    def consume(self, s, assign, occ, x):
        """Walks from `s` to the first decision or draw and gives it the
        entry `x`, an identifier of the tool."""
        t = self.t
        for _ in range(100000):
            r, v, mv = cd.moves(t, s, self.K)
            if r is None:
                raise BadTrail("the trail goes on past the end of the run")
            if not 1 <= x <= t.n:
                raise BadTrail(f"{x} is no node of the process")
            y = x - 1
            if r == 1:
                for (a, _, nxt, _) in mv:
                    if a == y:
                        return nxt, assign, occ
                raise BadTrail(f"{x} is no child of the choice {v + 1}")
            if r == 4 and len(mv) == 2:
                if t.kind[v] == cd.NATURE:
                    if y not in t.children(v):
                        raise BadTrail(f"{x} is no child of the nature node {v + 1}")
                    nxt = [m[2] for m in mv if cd.entry(t, s, v, m[2]) == (v, y)][0]
                    value = ("child", x)
                elif y == t.low[v]:
                    nxt, value = mv[0][2], AGAIN
                elif y == v:
                    nxt, value = mv[1][2], OUT
                else:
                    raise BadTrail(f"{x} is neither the region nor the exit of the loop {v + 1}")
                occ = dict(occ)
                occ[v] = occ.get(v, 0) + 1
                assign = dict(assign)
                assign[(v + 1, occ[v])] = value
                return nxt, assign, occ
            s = mv[0][2]
        raise BadTrail("the replay does not end")

    def after(self, trail):
        trail = tuple(trail)
        g = len(trail)
        while trail[:g] not in self.memo:
            g -= 1
        s, assign, occ = self.memo[trail[:g]]
        while g < len(trail):
            s, assign, occ = self.consume(s, assign, occ, trail[g])
            g += 1
            self.memo[trail[:g]] = (s, assign, occ)
        return s, assign

    def due(self, trail):
        """The choice due after the trail, an identifier of the tool, and
        the assignment by occurrences of the draws of the trail."""
        s, assign = self.after(trail)
        t = self.t
        for _ in range(100000):
            r, v, mv = cd.moves(t, s, self.K)
            if r is None:
                raise BadTrail("the history ends the run: no choice is due")
            if r == 1:
                return v + 1, assign
            if r == 4 and len(mv) == 2:
                raise BadTrail(f"the trail ends at the draw of the node {v + 1}, not at a choice")
            s = mv[0][2]
        raise BadTrail("the replay does not end")


# ---------------------------------------------------------------------------
# the trees of the JSON
# ---------------------------------------------------------------------------

def bvalue(value):
    if value["is"] == "child":
        return ("child", value["child"]["id"])
    return (value["is"],)


class NoBranch(Exception):
    pass


def evaluate(node, assign):
    while "take" not in node:
        key = (node["draw"]["id"], node["occurrence"])
        val = assign.get(key, ABSENT)
        for b in node["branches"]:
            if bvalue(b["value"]) == val:
                node = b["tree"]
                break
        else:
            raise NoBranch(f"no branch for the value {val} of the draw {key[1]} at the node {key[0]}")
    return node["take"]["id"]


def stats(node):
    """The tests and the depth of a tree."""
    if "take" in node:
        return 0, 0
    tests, depth = 1, 0
    for b in node["branches"]:
        t, d = stats(b["tree"])
        tests += t
        depth = max(depth, d)
    return tests, depth + 1


def strip(x):
    """A JSON value with every node object replaced by its identifier."""
    if isinstance(x, dict):
        if "id" in x and set(x) <= {"id", "name", "label", "task"}:
            return x["id"]
        return {k: strip(v) for k, v in x.items()}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x


def pareto_pairs(pairs):
    out = set()
    for (a, b) in pairs:
        if not any((c <= a and d <= b) and (c, d) != (a, b) for (c, d) in pairs):
            out.add((a, b))
    return out


def fewest(values, labels):
    """The fewest tests of a decision tree that separates the rows, and the
    least depth among the trees with the fewest tests, by exhaustion: for
    every set of rows, every pair (tests, depth) no other pair is below."""
    nf = len(values[0]) if values else 0
    memo = {}

    def solve(rows):
        if rows in memo:
            return memo[rows]
        if len({labels[r] for r in rows}) <= 1:
            memo[rows] = {(0, 0)}
            return memo[rows]
        res = set()
        for f in range(nf):
            parts = {}
            for r in rows:
                parts.setdefault(values[r][f], []).append(r)
            if len(parts) < 2:
                continue
            combos = {(1, 0)}
            for part in parts.values():
                sub = solve(frozenset(part))
                combos = pareto_pairs({(a + c, max(b, d)) for (a, b) in combos for (c, d) in sub})
                if not combos:
                    break
            res |= {(a, b + 1) for (a, b) in combos}
        memo[rows] = pareto_pairs(res)
        return memo[rows]
    got = solve(frozenset(range(len(values))))
    return min(got) if got else None


# ---------------------------------------------------------------------------
# a result: checks made, failed, skipped
# ---------------------------------------------------------------------------

class Result:
    def __init__(self, name, text, K):
        self.name, self.text, self.K = name, text, K
        self.made, self.failed, self.skipped = {}, {}, {}
        self.fails = []
        self.info = {}

    def ok(self, check, n=1):
        self.made[check] = self.made.get(check, 0) + n

    def fail(self, check, detail, point=None, trees=None, strategy=None):
        self.made[check] = self.made.get(check, 0) + 1
        self.failed[check] = self.failed.get(check, 0) + 1
        self.fails.append({"check": check, "process": self.text, "name": self.name, "K": self.K, "point": point,
                           "detail": detail, "trees": trees, "strategy": strategy})

    def skip(self, reason, n=1):
        self.skipped[reason] = self.skipped.get(reason, 0) + n

    def note(self, key, n=1):
        self.info[key] = self.info.get(key, 0) + n

    def pack(self):
        return {"name": self.name, "K": self.K, "made": self.made, "failed": self.failed, "skipped": self.skipped,
                "fails": self.fails, "info": self.info}


# ---------------------------------------------------------------------------
# the checks on one strategy and its explanation
# ---------------------------------------------------------------------------

def rows_of(rep: Replayer, proc: Process, decisions):
    """The decisions by the choice they are taken at: (assignment, action,
    trail). An error is a decision that is no decision at a choice."""
    by_choice, errors = {}, []
    for trail, action in decisions:
        try:
            v, assign = rep.due(trail)
        except BadTrail as e:
            errors.append(f"the decision after {list(trail)} take {action} does not replay: {e}")
            continue
        if action not in proc.children(v):
            errors.append(f"the decision after {list(trail)} takes {action}, no child of the choice {v}")
            continue
        by_choice.setdefault(v, []).append((assign, action, tuple(trail)))
    return by_choice, errors


def check_shape(proc: Process, e, rows, problems):
    v = e["choice"]["id"]
    if not (1 <= v <= proc.n) or proc.kind(v) != cd.CHOICE:
        problems.append(f"the node {v} of a tree is no choice node")
        return
    if [o["id"] for o in e["options"]] != proc.children(v):
        problems.append(f"choice {v}: the options {[o['id'] for o in e['options']]} are not its children {proc.children(v)}")
    tests, depth = stats(e["tree"])
    if (tests, depth) != (e["tests"], e["depth"]):
        problems.append(f"choice {v}: tests {e['tests']} depth {e['depth']} written, {tests} and {depth} in the tree")
    if e["histories"] != len(rows):
        problems.append(f"choice {v}: histories {e['histories']}, {len(rows)} decisions at that choice")

    def walk(node, here, path):
        if "take" in node:
            if node["take"]["id"] not in proc.children(v):
                problems.append(f"choice {v}: a leaf takes {node['take']['id']}, no child of the choice")
            return
        u, j = node["draw"]["id"], node["occurrence"]
        if not (1 <= u <= proc.n) or proc.kind(u) not in (cd.NATURE, cd.LOOP):
            problems.append(f"choice {v}: a test at the node {u}, neither a nature node nor a loop")
            return
        is_loop = proc.kind(u) == cd.LOOP
        if node["loop"] != is_loop:
            problems.append(f"choice {v}: the test at {u} says loop {node['loop']}")
        if [o["id"] for o in node["options"]] != proc.children(u):
            problems.append(f"choice {v}: the test at {u} has options {[o['id'] for o in node['options']]}")
        if not isinstance(j, int) or j < 1:
            problems.append(f"choice {v}: occurrence {j}")
        if (u, j) in path:
            problems.append(f"choice {v}: the draw {j} at the node {u} is tested twice along a path")
        if len(node["branches"]) < 2:
            problems.append(f"choice {v}: the test of the draw {j} at {u} has {len(node['branches'])} branch")
        seen = set()
        for b in node["branches"]:
            val = bvalue(b["value"])
            if val in seen:
                problems.append(f"choice {v}: the value {val} twice at the test ({u}, {j})")
            seen.add(val)
            if val[0] == "child" and (is_loop or val[1] not in proc.children(u)):
                problems.append(f"choice {v}: the value {val} at the test ({u}, {j}) is no child drawn there")
            if val[0] in ("again", "out") and not is_loop:
                problems.append(f"choice {v}: the value {val} at a nature node {u}")
            if val[0] not in ("child", "again", "out", "absent"):
                problems.append(f"choice {v}: the value {val} is unknown")
            part = [r for r in here if r[0].get((u, j), ABSENT) == val]
            if not part:
                problems.append(f"choice {v}: the branch {val} of the test ({u}, {j}) is met by no history that reaches it")
            walk(b["tree"], part, path | {(u, j)})
    walk(e["tree"], rows, frozenset())


def analyse(res: Result, prefix, proc: Process, K, decisions, explanation, point, rep=None):
    """classify, trees, shape and minimal on one strategy and its trees.
    Returns the rows by choice, or None when the strategy does not replay."""
    rep = rep or Replayer(proc, K)
    ctx = dict(point=point, trees=explanation, strategy=[[list(t), a] for t, a in decisions][:200])
    by_choice, errors = rows_of(rep, proc, decisions)
    for err in errors[:3]:
        res.fail(prefix + "classify", err, **ctx)
    if errors:
        return None
    trees = {}
    dup = False
    for e in explanation:
        v = e["choice"]["id"]
        if v in trees:
            dup = True
        trees[v] = e
    if dup or set(trees) != set(by_choice):
        res.fail(prefix + "trees", f"trees for the choices {sorted(e['choice']['id'] for e in explanation)}, "
                 f"decisions at the choices {sorted(by_choice)}", **ctx)
    else:
        res.ok(prefix + "trees")
    for v, rows in sorted(by_choice.items()):
        e = trees.get(v)
        if e is None:
            continue
        bad = None
        for (assign, action, trail) in rows:
            try:
                leaf = evaluate(e["tree"], assign)
                if leaf != action:
                    bad = f"choice {v}: after {list(trail)} the strategy takes {action}, the tree takes {leaf}"
            except NoBranch as err:
                bad = f"choice {v}: after {list(trail)}: {err}"
            if bad:
                break
        if bad:
            res.fail(prefix + "classify", bad, **ctx)
        res.ok(prefix + "classify", len(rows) - (1 if bad else 0))
        problems = []
        check_shape(proc, e, rows, problems)
        if problems:
            res.fail(prefix + "shape", "; ".join(problems[:4]), **ctx)
        else:
            res.ok(prefix + "shape")
        features = sorted({f for (assign, _, _) in rows for f in assign})
        if not e["minimal"]:
            res.note("trees not claimed minimal")
        elif len(rows) > 9 or len(features) > 8:
            res.skip("minimal: more than 9 rows or 8 features")
        else:
            values = [tuple(assign.get(f, ABSENT) for f in features) for (assign, _, _) in rows]
            best = fewest(values, [a for (_, a, _) in rows])
            if best is None:
                res.fail(prefix + "minimal", f"choice {v}: no tree on the draws separates the rows", **ctx)
            elif best != (e["tests"], e["depth"]):
                res.fail(prefix + "minimal", f"choice {v}: the tool says {e['tests']} tests and depth {e['depth']}, "
                         f"the fewest are {best[0]} tests, then depth {best[1]}, on {len(rows)} rows and the "
                         f"features {features}", **ctx)
            else:
                res.ok(prefix + "minimal")
                if best[0] > 0:
                    res.note("minimal trees with a test")
    return by_choice


class Lost(Exception):
    pass


def recover(proc: Process, K, explanation, cap):
    """The whole process walked with the trees alone deciding. Returns the
    expected cost, the decisions met as (trail, action), and one final
    trail. Raises Lost when a choice has no tree or no branch."""
    t = proc.t
    trees = {e["choice"]["id"]: e["tree"] for e in explanation}
    total = [F(0)] * proc.k
    met = set()
    finals, a_final = 0, None
    stack = [(cd.initial(t), {}, {}, F(1), [F(0)] * proc.k, ())]
    while stack:
        s, assign, occ, prob, cost, trail = stack.pop()
        while True:
            r, v, mv = cd.moves(t, s, K)
            if r is None:
                finals += 1
                if finals > cap:
                    raise OverflowError
                for c in range(proc.k):
                    total[c] += prob * cost[c]
                a_final = trail
                break
            if r == 1:
                tree = trees.get(v + 1)
                if tree is None:
                    raise Lost(f"the choice {v + 1} is met after {list(trail)} and has no tree")
                try:
                    a = evaluate(tree, assign)
                except NoBranch as err:
                    raise Lost(f"the choice {v + 1} is met after {list(trail)}: {err}")
                nxt = [m[2] for m in mv if m[0] == a - 1]
                if not nxt:
                    raise Lost(f"the tree of the choice {v + 1} takes {a}, no child of it")
                met.add((trail, a))
                trail = trail + (a,)
                s = nxt[0]
            elif r == 4 and len(mv) == 2:
                j = occ.get(v, 0) + 1
                for i, (_, pr, nxt, _) in enumerate(mv):
                    if t.kind[v] == cd.NATURE:
                        child = cd.entry(t, s, v, nxt)[1]
                        value, x = ("child", child + 1), child + 1
                    elif i == 0:
                        value, x = AGAIN, t.low[v] + 1
                    else:
                        value, x = OUT, v + 1
                    o2 = dict(occ)
                    o2[v] = j
                    a2 = dict(assign)
                    a2[(v + 1, j)] = value
                    stack.append((nxt, a2, o2, prob * pr, list(cost), trail + (x,)))
                break
            elif r == 5:
                paid = proc.advance(s, mv[0][2])
                cost = [x + y for x, y in zip(cost, paid)]
                s = mv[0][2]
            else:
                s = mv[0][2]
    return total, met, a_final


# ---------------------------------------------------------------------------
# the tool and the page
# ---------------------------------------------------------------------------

def run_tool(args, timeout):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timeout"


def post(port, path, fields, timeout=60):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def alive(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def encode(decisions):
    return ";".join(".".join(str(x) for x in trail) + ">" + str(a) for trail, a in decisions)


def ask_page(port, text, K, decisions_text):
    """The answer of /api/explain, or a string saying what went wrong."""
    try:
        return post(port, "/api/explain", {"process": text, "time": "1", "max_iter": str(K), "decisions": decisions_text})
    except Exception as e:  # a dropped connection, a body that is no JSON
        return f"{type(e).__name__}: {e}"


def valid(rep: Replayer, proc: Process, decisions):
    """None when the decisions are decisions at choices and a function of
    the draws, as the simulator reads them; the reason otherwise."""
    by_choice, errors = rows_of(rep, proc, decisions)
    if errors:
        return errors[0]
    for v, rows in by_choice.items():
        seen = {}
        for (assign, action, trail) in rows:
            key = frozenset(assign.items())
            if seen.setdefault(key, action) != action:
                return f"two actions at the choice {v} after the same draws"
    return None


def page_checks(res: Result, proc: Process, K, port, points, rng, final_trail, n_mut):
    """The page on the points given: the trees of the command line, the
    malformed decisions, the mutations."""
    text = res.text
    rep = Replayer(proc, K)
    for (label, decisions, explanation) in points:
        ctx = dict(point=label, trees=explanation, strategy=[[list(t), a] for t, a in decisions][:200])
        got = ask_page(port, text, K, encode(decisions))
        if not isinstance(got, dict) or "explanations" not in got:
            res.fail("page", f"/api/explain answers {str(got)[:300]}", **ctx)
        elif strip(got["explanations"]) != strip(explanation):
            res.fail("page", f"the trees of the page differ from those of the command line: {json.dumps(strip(got['explanations']))[:1500]}", **ctx)
        else:
            res.ok("page")
    if not points:
        return
    label, decisions, explanation = points[0]
    decisions = [(tuple(t), a) for t, a in decisions]
    if not decisions:
        return
    n = proc.n

    def expect_error(kind, body, why):
        got = ask_page(port, text, K, body)
        if isinstance(got, dict) and "error" in got:
            res.ok("page-malformed")
        else:
            res.fail("page-malformed", f"{kind}: the decisions {body[:300]!r} ({why}) answer {str(got)[:300]}", point=label)

    trail0, action0 = max(decisions, key=lambda d: len(d[0]))
    dots = ".".join(str(x) for x in trail0)
    expect_error("no >", dots or "1", "no > at all")
    expect_error("no >", encode(decisions).replace(">", ":"), "every > replaced")
    expect_error("no >", encode(decisions) + ";" + str(action0), "one decision without >")
    expect_error("range", f"{dots}>{n + 1}", "the action past the last node")
    expect_error("range", f"{dots}>0", "the action 0")
    expect_error("range", f"{n + 1}>{action0}", "an entry past the last node")
    expect_error("range", f"0>{action0}", "an entry 0")
    expect_error("range", f"{dots}>99999999999999999999", "an action that overflows")
    expect_error("range", f"{dots}>-1", "a negative action")
    expect_error("range", f"{dots}>x", "an action that is no number")
    expect_error("range", f"a.b>{action0}", "entries that are no numbers")
    expect_error("range", f"{dots}>{action0}>{action0}", "two > in one decision")
    v0, _ = rep.due(trail0)
    expect_error("not a child", f"{dots}>{v0}", "the action is the choice itself")
    strangers = [x for x in range(1, n + 1) if x not in proc.children(v0)]
    x = rng.choice(strangers)
    expect_error("not a child", f"{dots}>{x}", "the action is another node")
    expect_error("not a child", encode([d for d in decisions if d[0] != trail0] + [(trail0, x)]),
                 "one action of the strategy replaced by another node")
    # a trail that is no history: found on the simulator
    sent = 0
    candidates = []
    for (trail, a) in decisions:
        for x in range(1, n + 1):
            candidates.append((trail + (x,), a))
            for i in range(len(trail)):
                candidates.append((trail[:i] + (x,) + trail[i + 1:], a))
                candidates.append((trail[:i] + (x,) + trail[i:], a))
    rng.shuffle(candidates)
    for (trail, a) in candidates:
        if sent >= 4:
            break
        try:
            rep.due(trail)
        except BadTrail as e:
            expect_error("no history", encode([(trail, a)]), str(e))
            sent += 1
    if final_trail is not None:
        for c in proc.children(v0):
            expect_error("ends the run", encode([(final_trail, c)]), "the history ends the run")
    other = [c for c in proc.children(v0) if c != action0][0]
    expect_error("two actions", encode(decisions + [(trail0, other)]), "two actions after one history")
    # the decisions changed at random: the simulator says what they are
    for m in range(n_mut):
        ds = list(decisions)
        i = rng.randrange(len(ds))
        trail, a = ds[i]
        how = rng.choice(["replace", "drop", "insert", "action", "extend", "cut"])
        if how == "replace" and trail:
            j = rng.randrange(len(trail))
            trail = trail[:j] + (rng.randint(1, n),) + trail[j + 1:]
        elif how == "drop" and trail:
            j = rng.randrange(len(trail))
            trail = trail[:j] + trail[j + 1:]
        elif how == "insert":
            j = rng.randint(0, len(trail))
            trail = trail[:j] + (rng.randint(1, n),) + trail[j:]
        elif how == "extend":
            trail = trail + (rng.randint(1, n),)
        elif how == "cut" and trail:
            trail = trail[:rng.randrange(len(trail))]
        else:
            a = rng.randint(1, n)
        ds = [(trail, a)] if m % 2 == 0 else ds[:i] + [(trail, a)] + ds[i + 1:]
        why = valid(rep, proc, ds)
        got = ask_page(port, text, K, encode(ds))
        shown = encode(ds)[:300]
        if why is not None:
            if isinstance(got, dict) and "error" in got:
                res.ok("page-mutation")
            else:
                res.fail("page-mutation", f"the decisions {shown!r} are refused by the simulator ({why}) and the page answers {str(got)[:300]}", point=label)
        else:
            if not isinstance(got, dict) or "explanations" not in got:
                res.fail("page-mutation", f"the decisions {shown!r} are decisions at choices on the simulator and the page answers {str(got)[:300]}", point=label)
            else:
                res.ok("page-mutation")
                res.note("mutations still valid")
                analyse(res, "page-mutation-", proc, K, ds, got["explanations"], label, rep)
    if alive(port):
        res.ok("page-alive")
    else:
        res.fail("page-alive", "the server no longer answers /api/hello after the malformed decisions", point=label)


def ceil6(v: F) -> str:
    q = math.ceil(v * 10 ** 6)
    sign = "-" if q < 0 else ""
    q = abs(q)
    return f"{sign}{q // 10 ** 6}.{q % 10 ** 6:06d}"


def determine_check(res: Result, proc: Process, K, cfg, yaml: Path, value, label, tag):
    out_json = yaml.with_name(f"{yaml.stem}-k{K}-explain-{tag}.json")
    if out_json.exists():
        out_json.unlink()
    budget = ",".join(ceil6(F(x)) for x in value)
    args = [cfg["bin"], "explain", str(yaml), "--B", budget, "--arith", "exact", "--time", "1", "--max-iter", str(K),
            "--explain-json", str(out_json), "--print-strategy", "1", "--timeout", str(cfg["timeout"])]
    code, out, errtext = run_tool(args, cfg["timeout"] + 30)
    m = re.search(r"^answer\s+(\S+)", out, re.M)
    answer = m.group(1) if m else None
    if code is None or answer == "timeout":
        res.skip("determine: timed out")
        return
    if code != 0 or answer != "yes":
        res.fail("determine", f"explain --B {budget} exits {code} and answers {answer}: {(out + errtext)[-400:]}", point=label)
        return
    decisions = [(tuple(int(x) for x in re.findall(r"\d+", h)), int(a))
                 for h, a in re.findall(r"^\s+after \[(.*?)\] take (\d+)\s*$", out, re.M)]
    m = re.search(r"^decisions\s+(\d+)", out, re.M)
    if m is None or int(m.group(1)) != len(decisions):
        res.fail("determine", f"the strategy printed does not read: {out[-400:]}", point=label)
        return
    if "explanation    unavailable" in out or not out_json.exists():
        res.fail("determine", f"explain --B {budget} answers yes and gives no explanation: {out[-400:]}", point=label,
                 strategy=[[list(t), a] for t, a in decisions][:200])
        return
    try:
        explanation = json.loads(out_json.read_text())
    except ValueError as e:
        res.fail("determine", f"the file of --explain-json is no JSON: {e}", point=label)
        return
    res.ok("determine")
    analyse(res, "determine-", proc, K, decisions, explanation, f"{label}, explain --B {budget}")


def check_case(spec):
    name, text, K, cfg = spec
    res = Result(name, text, K)
    try:
        _check_case(res, name, text, K, cfg)
    except Exception:
        res.fail("harness", traceback.format_exc()[-1500:])
    return res.pack()


def _check_case(res: Result, name, text, K, cfg):
    work = Path(cfg["work"]) / "cases"
    work.mkdir(parents=True, exist_ok=True)
    yaml = work / f"{name}.yaml"
    code, out, errtext = run_tool([cfg["bin"], "parse", text, "--out", str(yaml)], cfg["timeout"])
    if code != 0:
        res.fail("parse", f"sdcpi parse exits {code}: {(out + errtext)[-300:]}")
        return
    proc = Process(yaml)
    report = work / f"{name}-k{K}.json"
    if report.exists():
        report.unlink()
    args = [cfg["bin"], "pareto", str(yaml), "--onephase", "1", "--time", "1", "--max-iter", str(K), "--explain", "1",
            "--print-strategy", "1", "--json", str(report), "--time-limit", str(cfg["time_limit"])]
    code, out, errtext = run_tool(args, cfg["timeout"] + cfg["time_limit"] + 30)
    if code is None:
        res.skip("frontier: the tool ran past the timeout")
        return
    if not report.exists():
        if "TooManyOutcomes" in out + errtext or "too many" in (out + errtext).lower():
            res.skip("frontier: failed, too many outcomes")
        else:
            res.fail("pareto", f"sdcpi pareto exits {code} and writes no report: {(out + errtext)[-400:]}")
        return
    data = json.loads(report.read_text())
    if data["status"] != "complete":
        res.skip(f"frontier: {data['status']}")
        return
    points = data["points"]
    if len(points) > cfg["max_points"]:
        res.skip(f"frontier: more than {cfg['max_points']} points")
        return
    rng = random.Random(f"{cfg['seed']}/{name}/{K}")
    usable = []
    for i, p in enumerate(points):
        label = f"point {i + 1} {p['value']}"
        if p.get("decisions") is None or "strategy" not in p:
            res.skip("point: no witness")
            continue
        if "explanation_error" in p:
            res.fail("explained", f"the tool does not explain its own strategy: {p['explanation_error']}", point=label,
                     strategy=p["strategy"][:200])
            continue
        if "explanation" not in p:
            res.fail("explained", "the point carries no explanation", point=label)
            continue
        usable.append((i, p, label))
    res.note("frontiers checked")
    res.note("points of the frontiers", len(points))
    if len(usable) > cfg["sample"]:
        # the point with the most tests always, the rest drawn
        top = max(usable, key=lambda u: sum(e["tests"] for e in u[1]["explanation"]))
        rest = [u for u in usable if u is not top]
        usable = [top] + rng.sample(rest, cfg["sample"] - 1)
        usable.sort(key=lambda u: u[0])
    checked = []
    final_trail = None
    for (i, p, label) in usable:
        decisions = [(tuple(d[0]), d[1]) for d in p["strategy"]]
        explanation = p["explanation"]
        if len(decisions) != p["decisions"]:
            res.fail("shape", f"decisions {p['decisions']} written, {len(decisions)} in the strategy", point=label)
        if len(decisions) > cfg["max_decisions"]:
            res.skip(f"point: more than {cfg['max_decisions']} decisions")
            continue
        res.note("points checked")
        if any(e["tests"] > 0 for e in explanation):
            res.note("points with a test")
        if any("draw" in json.dumps(e["tree"]) and '"loop": true' in json.dumps(e["tree"]) for e in explanation):
            res.note("points testing a loop draw")
        if any('"absent"' in json.dumps(e["tree"]) for e in explanation):
            res.note("points with an absent branch")
        ctx = dict(point=label, trees=explanation, strategy=p["strategy"][:200])
        by_choice = analyse(res, "", proc, K, decisions, explanation, label)
        checked.append((label, decisions, explanation, p))
        if by_choice is None:
            continue
        if p["open"] != 0:
            res.note("points with open histories (not recovered)")
            continue
        try:
            total, met, a_final = recover(proc, K, explanation, cfg["cap"])
        except OverflowError:
            res.skip("recovered: more histories than the cap")
            continue
        except Lost as e:
            res.fail("recovered", str(e), **ctx)
            continue
        final_trail = final_trail if final_trail is not None else a_final
        value = [F(x) for x in p["value"]]
        if total != value:
            res.fail("recovered", f"the trees alone give the expected cost {[str(x) for x in total]}, the point says {p['value']}", **ctx)
        else:
            res.ok("recovered")
        if met != set(decisions):
            extra = sorted(set(decisions) - met)[:3]
            missing = sorted(met - set(decisions))[:3]
            res.fail("coverage", f"the walk by the trees meets {len(met)} decisions, the strategy has {len(set(decisions))}: "
                     f"in the strategy and not met {extra}, met and not in the strategy {missing}", **ctx)
        else:
            res.ok("coverage")
    # determine: the point with the most tests and one more
    if checked:
        order = sorted(checked, key=lambda c: -sum(e["tests"] for e in c[2]))
        picks = [order[0]] + ([rng.choice(order[1:])] if len(order) > 1 else [])
        for tag, (label, decisions, explanation, p) in enumerate(picks[:cfg["determine"]]):
            determine_check(res, proc, K, cfg, yaml, p["value"], label, tag)
    # the page
    if cfg["port"] and checked:
        order = sorted(checked, key=lambda c: -sum(e["tests"] for e in c[2]))
        picks = order[:1] + rng.sample(order[1:], min(len(order) - 1, cfg["page_points"] - 1))
        page_checks(res, proc, K, cfg["port"], [(c[0], c[1], c[2]) for c in picks], rng, final_trail, cfg["mutations"])


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def random_processes(count, seed):
    out = []
    for index in range(count):
        rng = random.Random(seed * 1_000_003 + index)
        n_tasks = rng.randint(3, 6)
        n_loops = rng.randint(0, 2)
        n_impacts = rng.randint(1, 2)
        n_choices = rng.randint(1, 3)
        tree = loops_gen.generate(rng, n_tasks, n_loops, n_impacts, n_choices)
        out.append((f"random-{seed}-{index}", loops_gen.text(tree), n_loops > 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default=str(REPO / "target" / "release" / "sdcpi"))
    ap.add_argument("--count", type=int, default=60, help="random processes (default 60)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--k", default="1,2,3", help="the bounds on the passes (default 1,2,3)")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--work", default=None, help="the scratch folder (default a fresh temporary one)")
    ap.add_argument("--port", type=int, default=8437, help="the port of the server of the suite; 0 leaves the page out")
    ap.add_argument("--timeout", type=float, default=60, help="seconds a run of the tool may take (default 60)")
    ap.add_argument("--time-limit", type=float, default=20, help="the --time-limit of pareto (default 20)")
    ap.add_argument("--sample", type=int, default=40, help="points checked per frontier (default 40)")
    ap.add_argument("--max-points", type=int, default=300, help="a larger frontier is skipped (default 300)")
    ap.add_argument("--max-decisions", type=int, default=1500, help="a larger strategy is skipped (default 1500)")
    ap.add_argument("--cap", type=int, default=20000, help="histories of a walk by the trees (default 20000)")
    ap.add_argument("--determine", type=int, default=2, help="points per frontier given to explain (default 2)")
    ap.add_argument("--page-points", type=int, default=5, help="points per frontier given to the page (default 5)")
    ap.add_argument("--mutations", type=int, default=10, help="mutated decisions per frontier (default 10)")
    ap.add_argument("--show", type=int, default=12, help="failures printed in full (default 12)")
    args = ap.parse_args()

    if not os.access(args.bin, os.X_OK):
        print(f"run.py: {args.bin} is no executable", file=sys.stderr)
        return 2
    try:
        ks = [int(x) for x in args.k.split(",") if x.strip()]
    except ValueError:
        ks = []
    if not ks or any(k < 1 for k in ks):
        print("run.py: --k wants positive numbers, 1,2,3", file=sys.stderr)
        return 2
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="sdcpi-explainer-"))
    work.mkdir(parents=True, exist_ok=True)

    server = None
    if args.port:
        if alive(args.port):
            print(f"run.py: something already answers on port {args.port}; the suite uses a server of its own", file=sys.stderr)
            return 2
        log = open(work / "server.log", "w")
        server = subprocess.Popen([args.bin, "serve", "--port", str(args.port), "--saved", str(work / "store.json")],
                                  stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + 15
        while time.time() < deadline and not alive(args.port):
            if server.poll() is not None:
                break
            time.sleep(0.2)
        if server.poll() is not None or not alive(args.port):
            print(f"run.py: the server did not start on port {args.port}; see {work / 'server.log'}", file=sys.stderr)
            if server.poll() is None:
                server.kill()
            return 2

    cfg = {"bin": args.bin, "work": str(work), "port": args.port, "timeout": args.timeout, "time_limit": args.time_limit,
           "sample": args.sample, "max_points": args.max_points, "max_decisions": args.max_decisions, "cap": args.cap,
           "determine": args.determine, "page_points": args.page_points, "mutations": args.mutations, "seed": args.seed}
    specs = []
    for (name, text) in HAND:
        for K in ks:
            if K != ks[0] and "(0." not in text:
                continue
            specs.append((name, text, K, cfg))
    for (name, text, loops) in random_processes(args.count, args.seed):
        for K in (ks if loops else ks[:1]):
            specs.append((name, text, K, cfg))

    started = time.time()
    results = []
    server_died = False
    try:
        with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            for r in pool.map(check_case, specs):
                results.append(r)
        if server is not None and (server.poll() is not None or not alive(args.port)):
            server_died = True
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    made, failed, skipped, info, fails = {}, {}, {}, {}, []
    for r in results:
        for src, dst in ((r["made"], made), (r["failed"], failed), (r["skipped"], skipped), (r["info"], info)):
            for k, v in src.items():
                dst[k] = dst.get(k, 0) + v
        fails.extend(r["fails"])
    if server_died:
        made["page-alive"] = made.get("page-alive", 0) + 1
        failed["page-alive"] = failed.get("page-alive", 0) + 1
        fails.append({"check": "page-alive", "process": "", "name": "", "K": None, "point": None,
                      "detail": "the server of the suite was dead at the end of the run", "trees": None, "strategy": None})
    (work / "failures.json").write_text(json.dumps(fails, indent=1))

    n_random = args.count
    print(f"{len(HAND)} hand-written processes and {n_random} random ones (seed {args.seed}), K in {ks}: "
          f"{len(specs)} frontiers asked, {time.time() - started:.1f} s, work in {work}")
    print("what was met: " + ", ".join(f"{k} {v}" for k, v in sorted(info.items())))
    print("checks made and failed:")
    for k in sorted(made):
        print(f"  {k:28s} {made[k]:8d} made {failed.get(k, 0):6d} failed")
    print("skipped:")
    for k in sorted(skipped):
        print(f"  {skipped[k]:6d}  {k}")
    if not skipped:
        print("       0")
    for f in fails[:args.show]:
        print(f"FAIL [{f['check']}] {f['name']} K={f['K']} {f['point'] or ''}")
        print(f"     {f['detail']}")
        print(f"     process: {f['process']}")
        if f.get("strategy") is not None:
            print(f"     strategy: {json.dumps(f['strategy'])[:1200]}")
        if f.get("trees") is not None:
            print(f"     trees: {json.dumps(strip(f['trees']))[:2500]}")
    if len(fails) > args.show:
        print(f"... {len(fails) - args.show} more failures in {work / 'failures.json'}")
    total, bad, skip = sum(made.values()), sum(failed.values()), sum(skipped.values())
    print(f"checks {total}, failed {bad}, skipped {skip}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
