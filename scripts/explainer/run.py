#!/usr/bin/env python3
"""The explainer of `sdcpi` checked end to end, on its outputs alone.

    run.py --bin PATH                               the whole suite
    run.py --bin PATH --count 60 --seed 3 --k 1,2,3 --jobs 2
    run.py --bin PATH --work DIR --port 8637 --timeout 60

Nothing of the tool is trusted but what it writes. The semantics the checks
replay is the one of `check_draws.py`, an implementation independent of the
engine (identifiers in-order from 0 there, from 1 in the tool); the instance
file the tool writes is read by the reader of `scripts/loops/check.py`, and
the random processes are drawn by `scripts/loops/gen.py`.

The design checked. A draw is named by its node and its passes, a nested
index: for every loop above the node, the outermost first, the pass of that
loop that is running, counted from 1, and at a loop, last, the pass of its
own region that the draw follows, which is below the bound K, the exit
forced at the bound being no draw. A test is one statement on one draw,
"the draw (node, passes) gave x": it holds of a history exactly when the
history has a draw with that node, those passes and that value, and fails
in every other case, the other answer and the draw not taken. A test has
two branches, `yes` and `no`. The index of a draw is computed here by
`pass_index` of `check_draws.py`, on the state the draw is taken at.

The processes are hand-written ones covering every construct (a loop over a
choice, a choice whose child is a loop, a loop whose region is a loop,
natures in parallel followed by a choice, a choice under two nested loops,
a nature and a choice inside one loop, a choice beside a loop in parallel,
a loop inside a loop two and three deep, a loop inside a loop under a
parallel node beside a nature node, a choice that follows a loop beside a
draw of the other branch, the running example of the README) and `--count`
random ones, 3 to 6 tasks, 0 to 2 loops, 1 to 2 impacts. For every process
and every K of `--k` (the first K alone on a process without loops) the
frontier is asked with

    sdcpi pareto F.yaml --onephase 1 --time 1 --max-iter K --explain 1
                        --print-strategy 1 --json out.json --time-limit T

A frontier that failed or that has more than `--max-points` points is
skipped; a frontier the time limit cut has its points checked all the same
(`--cut 0` skips it), a point being a strategy with its exact value whether
or not the frontier is whole; at most `--sample` points of a frontier are
checked, a point whose strategy has more than `--max-decisions` decisions
is skipped.

What is checked, for every sampled point with its strategy and explanation:

  classify   every decision (trail, action) of the strategy is replayed on
             the simulator, its entries typed by walking the clauses (a
             decision at a choice, a draw at a nature node, a draw of a loop
             that runs it again or leaves it below the bound, nothing at a
             forced exit); every draw of the history gets its nested index,
             the tree of the choice node the trail ends at is evaluated on
             the draws (a test holds exactly when the history has a draw
             with that node, those passes and that value), and the leaf
             must be the action;
  trees      one tree per choice node decided and none for another node;
  names      in every test `passes` lists exactly the loops above the node,
             the outermost first, every pass is between 1 and K, at a loop
             `pass` is there and is below K, so that no test speaks of the
             exit forced at the bound, at a nature node there is no `pass`,
             `value` is a child of the nature node or again/out at a loop
             and never absent, and both branches are there;
  recovered  when the witness has no open history, the whole process is
             walked on the simulator at K deciding at every choice by the
             trees alone: every choice met has a tree, and the expected
             cost so obtained, the time and then the impacts, is the exact
             value of the point, in rationals;
  coverage   on the same walk, the decisions met are exactly the decisions
             of the strategy;
  minimal    for a tree claimed minimal with at most `--min-rows` rows and
             `--min-statements` statements (9 and 10), an exhaustive search
             independent of the tool over the two-way trees on the
             statements met, every (draw, value) pair with a value some row
             has (every pair of tests and depth a tree on a set of rows may
             have, no lexicographic shortcut), gives the fewest tests and,
             among the trees with the fewest tests, the least depth: both
             must equal `tests` and `depth`;
  shape      `tests` and `depth` are those of the tree carried, `histories`
             is the number of decisions at that choice, no statement is
             made twice along a path, both branches of a test are reached
             by a history, the nodes named are of the right kind;
  beyond     when the witness has no open history and the process has a
             loop, the process is walked on the simulator at K + 1 passes,
             deciding at every choice by evaluating the tree of that choice
             on the history so far; the evaluation is total, a test being
             true or false of any history. A choice node with no tree may
             be met only inside a pass past K (some loop above it has
             closed K passes of its region or more): its low child is
             taken there, and counted apart. Four checks:
               beyond-total   the walk never fails: every tree evaluates,
                              to a child of its choice, no choice without a
                              tree is met outside the passes past K, no two
                              draws of a history have one name;
               beyond-impact  the expected impact paid at the tasks closed
                              outside the passes past K is the exact impact
                              of the point, component by component;
               beyond-time    the expected time at K + 1 is at least the
                              time of the point;
               beyond-gap     the whole expected cost at K + 1, time and
                              impacts, is at most the value of the point
                              plus the upper end of its `continued`; with
                              no `continued` the two values are equal.
             beyond-gap holds of every process: `continued` bounds, from
             the first draw that runs a region again past the bound, the
             whole rest of the run by the bounds of the unbounded process,
             whatever is decided there. beyond-impact and beyond-time hold
             when no loop of the process sits under a parallel node. Under
             one the passes past the bound take time and move the draws of
             the other branch before or after a choice, so the tree may
             decide otherwise than the strategy outside those passes: the
             points where one of the two does not hold are counted apart
             and not failed;
  determine  `sdcpi explain F.yaml --B <the value, rounded up at the sixth
             decimal> --arith exact --time 1 --max-iter K --explain-json E
             --print-strategy 1` answers yes, and its trees classify its
             own strategy (the checks above, named `determine-*`);
  page       a server of the suite, `sdcpi serve --port P --saved
             WORK/store.json`: `POST /api/explain` with the process, time=1,
             max_iter=K and the decisions of the point gives the trees of
             the command line, up to the node objects, the readings of the
             impacts included;
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
  page-alive the server still answers after every malformed request;
  impacts-*  the readings of the impacts of every explained choice. The
             cost of a history at the state the choice is due at is what
             the fast forwards paid along it on the simulator, the time
             elapsed as the component 0 and then the impacts of the tasks
             closed. With the time in the cost (`--time 1`, always here) a
             choice has two readings, `in` on every component and `out` on
             the impacts alone, the time left out; a test on the impacts
             states "the component j is at most v" and holds of a history
             exactly when its cost on j is at most v, in rationals. A
             reading is separable when no two histories pay the same on
             its components and are decided differently. Six checks:
               impacts-shape      the readings are two, in that order, on
                                  those components; the names are those
                                  of the instance, the time first; every
                                  tree carries its tests and depth, reads
                                  the components it says, within the
                                  reading, never the time in `out`; every
                                  test names its component, says whether
                                  it is the time, relates "at most" (no
                                  component is maximized here) to an exact
                                  rational and its approximation, and both
                                  its branches are reached by a history; a
                                  reading that is not separable has a
                                  clash, no minimum and no set;
               impacts-separable  the reading is separable exactly when
                                  the simulator finds no two such
                                  histories, and the clash of one that is
                                  not is two decisions of the strategy at
                                  that choice that pay the same and take
                                  different children;
               impacts-classify   every tree of every reading, the minimum
                                  and the trees of the sets, comes to the
                                  child the strategy takes on the cost of
                                  every history;
               impacts-sets       the sets are the inclusion-minimal
                                  subsets of the components of the reading
                                  on which the histories are separable,
                                  found here over every subset, by size
                                  then by their indices, each read in full
                                  by its tree; the sets of `out` are those
                                  of `in` that leave out the time;
               impacts-minimal    for a tree claimed minimal with at most
                                  `--min-rows` histories distinct on its
                                  components and `--min-statements` cuts,
                                  the exhaustive search of `minimal` over
                                  the cuts, every component of the tree at
                                  every value a history pays on it but the
                                  greatest, gives its tests and depth: the
                                  minimum on the components of the
                                  reading, a set tree on its set;
               impacts-minimum    the minimum has no more tests than any
                                  set tree, as many when it reads exactly
                                  a minimal set, and reads a superset of
                                  one.

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
    ("loop-in-loop",
     "(0.5 ((0.5 ((A, 1, {x: 1}) ^[0.5] (B, 2, {y: 2}))) , ((C, 1, {x: 1}) ^ (D, 1, {y: 3}))))"),
    ("loop-in-loop-choice-inside",
     "((0.5 ((S, 1, {x: 1}) , (0.4 (((A, 1, {x: 2}) ^[0.5] (B, 2, {y: 1})) , ((C, 1, {x: 3}) ^ (D, 2, {y: 2})))))) , "
     "((E, 1, {x: 4}) ^ (G, 3, {y: 1})))"),
    ("loop-in-loop-in-loop",
     "(0.5 ((0.4 ((0.5 ((A, 1, {x: 1}) ^[0.5] (B, 2, {y: 2}))) , ((C, 1, {x: 2}) ^ (D, 2, {y: 1})))) , "
     "((E, 1, {x: 1}) ^ (G, 1, {y: 2}))))"),
    ("three-loops-then-choice",
     "((0.3 (0.4 ((0.5 ((A, 1, {x: 1}) ^[0.5] (B, 2, {y: 1}))) , (S, 1, {x: 1})))) , ((C, 1, {x: 5}) ^ (D, 3, {y: 2})))"),
    ("loop-in-loop-beside-nature",
     "(((0.5 ((0.5 ((A, 1, {x: 1}) ^[0.5] (B, 2, {y: 1}))) , (S, 1, {x: 1}))) || ((N, 2, {x: 1}) ^[0.4] (M, 3, {y: 2}))) , "
     "((C, 1, {x: 4}) ^ (D, 2, {y: 3})))"),
    ("loop-in-loop-with-choice-beside-nature",
     "((0.5 ((0.4 (((A, 1, {x: 1}) ^[0.5] (B, 2, {y: 1})) , ((C, 1, {x: 3}) ^ (D, 1, {y: 2})))) , (S, 1))) || "
     "(((N, 2, {x: 1}) ^[0.4] (M, 3, {y: 2})) , ((E, 1, {x: 2}) ^ (G, 2, {y: 2}))))"),
    ("choice-overtaken-by-a-draw",
     # a loop under a parallel node: at one pass the choice after A1 is due
     # before the draw of the other branch, and with a pass more after it;
     # the tree that reads that draw departs from the strategy past the bound
     "(((0.5 ((A1, 1, {x: 1}) ^[0.5] (A3, 3, {y: 1}))) , ((X, 1, {x: 1}) ^ (Y, 1, {y: 1}))) || "
     "((D, 2, {x: 1}) , ((E, 1, {x: 1}) ^[0.5] (F, 1, {y: 1}))))"),
    ("choice-after-loop-beside-draw",
     "(((0.5 (A, 1, {x: 1})) , ((B, 1, {x: 3}) ^ (C, 2, {y: 2}))) || ((D, 2, {x: 1}) , ((E, 1, {x: 2}) ^[0.5] (G, 1, {y: 3}))))"),
    ("loop-of-loop-of-nature-then-choice",
     "((0.5 (0.4 ((A, 1, {x: 2}) ^[0.5] (B, 2, {y: 1})))) , ((C, 1, {x: 3}) ^ (D, 2, {y: 2})))"),
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
        self.names = ["time"] + list(ct.names)  # the components as the tool names them
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


class SameName(Exception):
    """Two draws of one history with the same node and the same passes: the
    index would not name a draw."""


def draw_key(t, s, v):
    """The name of the draw taken at the state `s` at the node `v` of the
    simulator: the identifier of the tool and the passes."""
    return (v + 1, tuple(cd.pass_index(t, s, v)))


class Replayer:
    """The histories the trails write, replayed on the simulator and typed
    by the clauses. The state reached after every prefix is kept, with the
    draws so far and the cost paid so far, the decisions of a strategy
    sharing their prefixes. The draws of a history are a map from (node,
    passes) to what the draw gave, the node an identifier of the tool, the
    passes those of `pass_index` of the simulator at the state the draw is
    taken at. The cost is what the fast forwards paid, `Process.advance`
    at every rank-5 move: the time elapsed first, then the impacts of the
    tasks closed, a list of `proc.k` rationals."""

    def __init__(self, proc: Process, K: int):
        self.p, self.t, self.K = proc, proc.t, K
        self.memo = {(): (cd.initial(self.t), {}, [F(0)] * proc.k)}

    def consume(self, s, hist, cost, x):
        """Walks from `s` to the first decision or draw, paying the fast
        forwards on the way, and gives it the entry `x`, an identifier of
        the tool."""
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
                        return nxt, hist, cost
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
                key = draw_key(t, s, v)
                if key in hist:
                    raise SameName(f"two draws of one history at the node {key[0]} in the passes {list(key[1])}")
                hist = dict(hist)
                hist[key] = value
                return nxt, hist, cost
            nxt = mv[0][2]
            if r == 5:
                cost = [a + b for a, b in zip(cost, self.p.advance(s, nxt))]
            s = nxt
        raise BadTrail("the replay does not end")

    def after(self, trail):
        trail = tuple(trail)
        g = len(trail)
        while trail[:g] not in self.memo:
            g -= 1
        s, hist, cost = self.memo[trail[:g]]
        while g < len(trail):
            s, hist, cost = self.consume(s, hist, cost, trail[g])
            g += 1
            self.memo[trail[:g]] = (s, hist, cost)
        return s, hist, cost

    def due(self, trail):
        """The choice due after the trail, an identifier of the tool, the
        draws of the trail by their passes, and the cost paid up to the
        state the choice is due at."""
        s, hist, cost = self.after(trail)
        t = self.t
        for _ in range(100000):
            r, v, mv = cd.moves(t, s, self.K)
            if r is None:
                raise BadTrail("the history ends the run: no choice is due")
            if r == 1:
                return v + 1, hist, cost
            if r == 4 and len(mv) == 2:
                raise BadTrail(f"the trail ends at the draw of the node {v + 1}, not at a choice")
            nxt = mv[0][2]
            if r == 5:
                cost = [a + b for a, b in zip(cost, self.p.advance(s, nxt))]
            s = nxt
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


def test_key(node):
    """The draw a test speaks of, as the JSON writes it: its node and its
    passes, those of the loops above and, at a loop, its own last."""
    passes = [x["pass"] for x in node["passes"]]
    if "pass" in node:
        passes.append(node["pass"])
    return (node["draw"]["id"], tuple(passes))


def holds(node, hist):
    """Whether the statement of a test holds of a history: the history has
    a draw with that node, those passes and that value."""
    return hist.get(test_key(node)) == bvalue(node["value"])


def evaluate(node, hist):
    """The leaf a tree comes to on a history. Total on a tree that is well
    made; a tree that is not raises NoBranch."""
    try:
        while "take" not in node:
            node = node["yes"] if holds(node, hist) else node["no"]
        return node["take"]["id"]
    except (KeyError, TypeError) as err:
        raise NoBranch(f"a test of the tree does not read: {type(err).__name__} {err}")


def stats(node):
    """The tests and the depth of a tree."""
    if "take" in node:
        return 0, 0
    t1, d1 = stats(node["yes"])
    t2, d2 = stats(node["no"])
    return 1 + t1 + t2, 1 + max(d1, d2)


def tests_of(node, out):
    if "take" not in node:
        out.append(node)
        for side in ("yes", "no"):
            if side in node:
                tests_of(node[side], out)
    return out


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


def fewest(truth, labels):
    """The fewest tests of a two-way decision tree that separates the rows,
    and the least depth among the trees with the fewest tests, by
    exhaustion: for every set of rows, every pair (tests, depth) no other
    pair is below. `truth[r][p]` says whether the statement `p` holds of
    the row `r`."""
    ns = len(truth[0]) if truth else 0
    memo = {}

    def solve(rows):
        if rows in memo:
            return memo[rows]
        if len({labels[r] for r in rows}) <= 1:
            memo[rows] = {(0, 0)}
            return memo[rows]
        res = set()
        seen = set()
        for p in range(ns):
            yes = frozenset(r for r in rows if truth[r][p])
            if not yes or yes == rows or yes in seen:
                continue
            seen.add(yes)
            a, b = solve(yes), solve(rows - yes)
            res |= {(1 + x + z, 1 + max(y, w)) for (x, y) in a for (z, w) in b}
        memo[rows] = pareto_pairs(res)
        return memo[rows]
    got = solve(frozenset(range(len(truth))))
    return min(got) if got else None


# ---------------------------------------------------------------------------
# the trees on the impacts
# ---------------------------------------------------------------------------

READING_FIELDS = {"time", "available", "separable", "clash", "minimum", "sets", "sets_complete"}
IMPACT_TREE_FIELDS = {"components", "tests", "depth", "minimal", "tree"}
SETS_EXHAUSTIVE = 12   # the most components a reading lists every minimal set on


def is_node(x):
    """A node object of the JSON: an identifier with its name."""
    return (isinstance(x, dict) and "id" in x and set(x) <= {"id", "name", "label", "task"}
            and isinstance(x["id"], int) and not isinstance(x["id"], bool))


def rational(text):
    """The exact value a test on the impacts compares with, "a/b" or a
    decimal."""
    if not isinstance(text, str):
        raise ValueError(f"the value {text!r} is no string")
    return F(text)


def impact_holds(node, cost):
    """Whether a test on the impacts holds of a cost vector: the component
    is at most the value, or at least it on a maximized component."""
    v = rational(node["value"])
    x = cost[node["component"]]
    return x >= v if node["relation"] == "at least" else x <= v


def impact_evaluate(node, cost):
    """The leaf a tree on the impacts comes to on a cost vector."""
    try:
        while "take" not in node:
            node = node["yes"] if impact_holds(node, cost) else node["no"]
        return node["take"]["id"]
    except (KeyError, TypeError, IndexError, ValueError, ZeroDivisionError) as err:
        raise NoBranch(f"a test on the impacts does not read: {type(err).__name__} {err}")


def projection(cost, comps):
    """The cost on these components."""
    return tuple(cost[c] for c in comps)


def clash_on(rows, comps):
    """Two rows that pay the same on these components and take different
    children, or None when the decision is a function of the cost on
    them."""
    seen = {}
    for row in rows:
        other = seen.setdefault(projection(row[3], comps), row)
        if other[1] != row[1]:
            return other, row
    return None


def minimal_sets_on(rows, available):
    """The inclusion-minimal subsets of the available components on which
    the decision is a function of the cost, by size and then by their
    components, every subset tried."""
    avail = list(available)
    subsets = [tuple(c for i, c in enumerate(avail) if mask >> i & 1) for mask in range(1 << len(avail))]
    good = [s for s in subsets if clash_on(rows, s) is None]
    minimal = [s for s in good if not any(t != s and set(t) <= set(s) for t in good)]
    return sorted(minimal, key=lambda s: (len(s), s))


def distinct_rows(rows, comps):
    """One row per distinct cost on these components, the first met, as
    the tool keeps them."""
    seen = {}
    for row in rows:
        seen.setdefault(projection(row[3], comps), row)
    return list(seen.values())


def cut_statements(rows, comps):
    """What a test on the impacts may state: a component of `comps` is at
    most one of the values the rows pay on it, every value but the
    greatest."""
    out = []
    for c in comps:
        vals = sorted({row[3][c] for row in rows})
        out.extend((c, x) for x in vals[:-1])
    return out


def shown_cost(cost):
    return [str(x) for x in cost]


def check_impact_tree(proc, v, imp, r, label, t, rows, problems):
    """One tree on the impacts of a reading: its fields, its tests, the
    branches reached."""
    tag = f"choice {v}, the reading {r['time']}, the {label} tree"
    avail = r["available"]
    if not isinstance(t, dict) or set(t) != IMPACT_TREE_FIELDS:
        problems.append(f"{tag} has the fields {sorted(t) if isinstance(t, dict) else repr(t)}")
        return
    if not isinstance(t["minimal"], bool):
        problems.append(f"{tag}: minimal is {t['minimal']!r}")
    tests, depth = stats(t["tree"])
    if (tests, depth) != (t["tests"], t["depth"]):
        problems.append(f"{tag}: tests {t['tests']} depth {t['depth']} written, {tests} and {depth} in the tree")
    read = sorted({n.get("component") for n in tests_of(t["tree"], [])}, key=repr)
    if t["components"] != read:
        problems.append(f"{tag}: components {t['components']} written, the tests read {read}")

    def walk(node, here):
        if "take" in node:
            if not is_node(node["take"]) or node["take"]["id"] not in proc.children(v):
                problems.append(f"{tag}: a leaf takes {strip(node['take'])}, no child of the choice")
            return
        c = node.get("component")
        if not isinstance(c, int) or isinstance(c, bool) or c not in avail:
            problems.append(f"{tag}: a test reads the component {c!r}, not among the available {avail}")
            return
        where = f"{tag}: the test on the component {c}"
        if r["time"] == "out" and c == 0:
            problems.append(f"{where} reads the time, which the reading leaves out")
        if node.get("name") != imp["names"][c]:
            problems.append(f"{where} is named {node.get('name')!r}, the component {imp['names'][c]!r}")
        if node.get("time") is not (imp["time"] is True and c == 0):
            problems.append(f"{where} says time {node.get('time')!r}")
        if node.get("relation") != "at most":
            problems.append(f"{where} has the relation {node.get('relation')!r}, no component is maximized")
        try:
            value = rational(node.get("value"))
        except (ValueError, ZeroDivisionError) as err:
            problems.append(f"{where}: the value does not read: {err}")
            return
        approx = node.get("approx")
        if (isinstance(approx, bool) or not isinstance(approx, (int, float))
                or abs(approx - float(value)) > 1e-9 * max(1.0, abs(float(value)))):
            problems.append(f"{where} at most {node['value']} has the approx {approx!r}")
        for side in ("yes", "no"):
            if not isinstance(node.get(side), dict):
                problems.append(f"{where} at most {node['value']} has no branch {side}")
                return
        yes = [row for row in here if row[3][c] <= value]
        no = [row for row in here if row[3][c] > value]
        if here and not yes:
            problems.append(f"{where} at most {node['value']} holds of no history that reaches it")
        if here and not no:
            problems.append(f"{where} at most {node['value']} holds of every history that reaches it")
        walk(node["yes"], yes)
        walk(node["no"], no)
    walk(t["tree"], rows)


def check_impacts_shape(proc, e, rows, problems):
    """The readings of the impacts of one choice, as the JSON writes them.
    Returns the readings when they read, None otherwise."""
    v = e["choice"]["id"]
    where = f"choice {v}"
    imp = e.get("impacts")
    if not isinstance(imp, dict):
        problems.append(f"{where}: impacts is {type(imp).__name__}, no object")
        return None
    if set(imp) != {"time", "names", "readings"}:
        problems.append(f"{where}: impacts has the fields {sorted(imp)}")
    if imp.get("time") is not True:
        problems.append(f"{where}: impacts.time is {imp.get('time')!r}, the run has the time in")
    if imp.get("names") != proc.names:
        problems.append(f"{where}: the names {imp.get('names')} are not {proc.names}")
    k = proc.k
    want = [("in", list(range(k))), ("out", list(range(1, k)))] if imp.get("time") is True else [("none", list(range(k)))]
    readings = imp.get("readings")
    if not isinstance(readings, list) or not all(isinstance(r, dict) for r in readings):
        problems.append(f"{where}: readings is {readings!r}")
        return None
    heads = [(r.get("time"), r.get("available")) for r in readings]
    if heads != want:
        problems.append(f"{where}: the readings are {heads}, not {want}")
        return None
    for r in readings:
        tag = f"{where}, the reading {r['time']}"
        avail = r["available"]
        if set(r) != READING_FIELDS:
            problems.append(f"{tag} has the fields {sorted(r)}")
        if not isinstance(r.get("separable"), bool):
            problems.append(f"{tag}: separable is {r.get('separable')!r}")
            continue
        if r.get("sets_complete") is not True and len(avail) <= SETS_EXHAUSTIVE:
            problems.append(f"{tag}: sets_complete is {r.get('sets_complete')!r} on {len(avail)} components")
        if not isinstance(r.get("sets"), list):
            problems.append(f"{tag}: sets is {r.get('sets')!r}")
            continue
        if r["separable"]:
            if r.get("clash") is not None:
                problems.append(f"{tag} is separable and has the clash {strip(r['clash'])}")
            if not isinstance(r.get("minimum"), dict):
                problems.append(f"{tag} is separable and has the minimum {r.get('minimum')!r}")
            trees = [("minimum", r["minimum"])] if isinstance(r.get("minimum"), dict) else []
            trees += [(f"set {i + 1}", t) for i, t in enumerate(r["sets"])]
            for label, t in trees:
                check_impact_tree(proc, v, imp, r, label, t, rows, problems)
        else:
            if r.get("minimum") is not None or r["sets"]:
                problems.append(f"{tag} is not separable and carries a minimum or sets")
            c = r.get("clash")
            if not isinstance(c, dict) or set(c) != {"a", "b"}:
                problems.append(f"{tag}: the clash is {strip(c)}")
                continue
            for side in ("a", "b"):
                d = c[side]
                if (not isinstance(d, dict) or set(d) != {"after", "take"} or not isinstance(d["after"], list)
                        or not all(is_node(x) for x in d["after"]) or not is_node(d["take"])):
                    problems.append(f"{tag}: the side {side} of the clash is {strip(d)}")
    return None if problems else readings


def impact_checks(res, prefix, proc, e, rows, ctx, limits):
    """The readings of the impacts of one choice against its rows, each a
    decision with the cost paid up to the choice: shape, separable,
    classify, sets, minimal, minimum."""
    v = e["choice"]["id"]
    problems = []
    try:
        readings = check_impacts_shape(proc, e, rows, problems)
    except (KeyError, TypeError, AttributeError) as err:
        problems.append(f"choice {v}: the impacts do not read: {type(err).__name__} {err}")
        readings = None
    if problems or readings is None:
        res.fail(prefix + "impacts-shape", "; ".join(problems[:4]) or f"choice {v}: the impacts do not read", **ctx)
        return
    res.ok(prefix + "impacts-shape")
    decided = {(trail, action): cost for (_, action, trail, cost) in rows}
    listed_sets = {}
    for r in readings:
        avail = r["available"]
        tag = f"choice {v}, the reading {r['time']}"
        # separable: no two histories pay the same on the available
        # components and are decided differently
        clash = clash_on(rows, avail)
        if (clash is None) != r["separable"]:
            if clash is None:
                res.fail(prefix + "impacts-separable", f"{tag}: the tool says not separable, no two decisions pay the "
                         f"same on {avail} and take different children", **ctx)
            else:
                a, b = clash
                res.fail(prefix + "impacts-separable", f"{tag}: the tool says separable; after {list(a[2])} and after "
                         f"{list(b[2])} the cost is {shown_cost(a[3])} and {shown_cost(b[3])}, the same on {avail}, "
                         f"and the strategy takes {a[1]} and {b[1]}", **ctx)
        elif clash is not None:
            c = r["clash"]
            da = (tuple(x["id"] for x in c["a"]["after"]), c["a"]["take"]["id"])
            db = (tuple(x["id"] for x in c["b"]["after"]), c["b"]["take"]["id"])
            bad = None
            if da not in decided or db not in decided:
                bad = (f"{tag}: the clash names after {list(da[0])} take {da[1]} and after {list(db[0])} take {db[1]}, "
                       f"not both decisions of the strategy at the choice")
            elif da[1] == db[1]:
                bad = f"{tag}: the two decisions of the clash take the same child {da[1]}"
            elif projection(decided[da], avail) != projection(decided[db], avail):
                bad = (f"{tag}: the two decisions of the clash pay {shown_cost(decided[da])} and "
                       f"{shown_cost(decided[db])}, not the same on {avail}")
            if bad:
                res.fail(prefix + "impacts-separable", bad, **ctx)
            else:
                res.ok(prefix + "impacts-separable")
            res.note("readings not separable")
        else:
            res.ok(prefix + "impacts-separable")
        trees = [("minimum", r["minimum"], tuple(avail))] if r["minimum"] is not None else []
        trees += [(f"set {t['components']}", t, tuple(t["components"])) for t in r["sets"]]
        # classify: every tree comes to the child the strategy takes, on
        # the cost of every history
        for label, t, _ in trees:
            bad = None
            for (hist, action, trail, cost) in rows:
                try:
                    leaf = impact_evaluate(t["tree"], cost)
                except NoBranch as err:
                    bad = f"{tag}: the {label} tree after {list(trail)}: {err}"
                    break
                if leaf != action:
                    bad = (f"{tag}: after {list(trail)}, paying {shown_cost(cost)}, the strategy takes {action}, "
                           f"the {label} tree takes {leaf}")
                    break
            if bad:
                res.fail(prefix + "impacts-classify", bad, **ctx)
            else:
                res.ok(prefix + "impacts-classify")
        # sets: the minimal separable subsets by exhaustion, in order, each
        # read in full by its tree
        mine = minimal_sets_on(rows, avail)
        listed = [tuple(t["components"]) for t in r["sets"]]
        if listed != mine:
            res.fail(prefix + "impacts-sets", f"{tag}: the tool lists the sets {[list(s) for s in listed]} (the "
                     f"components each tree reads), the minimal separable sets are {[list(s) for s in mine]}", **ctx)
        else:
            res.ok(prefix + "impacts-sets")
        listed_sets[r["time"]] = listed
        # minimal: the fewest tests, then the least depth, by exhaustion
        # over the cuts, on the histories distinct on the components
        for label, t, comps in trees:
            if t["minimal"] is not True:
                res.note("impact trees not claimed minimal")
                continue
            rows_d = distinct_rows(rows, comps)
            statements = cut_statements(rows_d, comps)
            if len(rows_d) > limits[0] or len(statements) > limits[1]:
                res.skip(f"impacts-minimal: more than {limits[0]} rows or {limits[1]} statements")
                continue
            truth = [tuple(row[3][c] <= x for (c, x) in statements) for row in rows_d]
            best = fewest(truth, [row[1] for row in rows_d])
            if best is None:
                res.fail(prefix + "impacts-minimal", f"{tag}: no tree on the cuts of {list(comps)} separates the "
                         f"histories, the {label} tree claims to", **ctx)
            elif best != (t["tests"], t["depth"]):
                res.fail(prefix + "impacts-minimal", f"{tag}: the {label} tree says {t['tests']} tests and depth "
                         f"{t['depth']}, the fewest are {best[0]} tests, then depth {best[1]}, on {len(rows_d)} "
                         f"histories and the cuts {[(c, str(x)) for c, x in statements]}", **ctx)
            else:
                res.ok(prefix + "impacts-minimal")
                if best[0] > 0:
                    res.note("impact trees with a test, checked by exhaustion")
        # minimum: no set tree has fewer tests, the same count when the
        # minimum reads exactly a minimal set, and it reads a superset of one
        if r["minimum"] is not None:
            m = r["minimum"]
            if m["minimal"] is not True or any(t["minimal"] is not True for t in r["sets"]):
                res.skip("impacts-minimum: a tree not claimed minimal")
            else:
                bad = []
                for t in r["sets"]:
                    if m["tests"] > t["tests"]:
                        bad.append(f"the minimum has {m['tests']} tests, the tree on {t['components']} {t['tests']}")
                    elif m["components"] == t["components"] and m["tests"] != t["tests"]:
                        bad.append(f"the minimum reads exactly {t['components']} with {m['tests']} tests, the tree "
                                   f"on that set has {t['tests']}")
                if not any(set(s) <= set(m["components"]) for s in mine):
                    bad.append(f"the minimum reads {m['components']}, no superset of a minimal set of "
                               f"{[list(s) for s in mine]}")
                if bad:
                    res.fail(prefix + "impacts-minimum", f"{tag}: " + "; ".join(bad), **ctx)
                else:
                    res.ok(prefix + "impacts-minimum")
    # the sets of the reading without the time are those of the reading
    # with it that leave the time out
    if "in" in listed_sets and "out" in listed_sets:
        want = [s for s in listed_sets["in"] if 0 not in s]
        if listed_sets["out"] != want:
            res.fail(prefix + "impacts-sets", f"choice {v}: the sets of the reading out are "
                     f"{[list(s) for s in listed_sets['out']]}, the sets of the reading in without the time are "
                     f"{[list(s) for s in want]}", **ctx)
        else:
            res.ok(prefix + "impacts-sets")


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
    """The decisions by the choice they are taken at: (draws by passes,
    action, trail, cost paid up to the choice). An error is a decision that
    is no decision at a choice."""
    by_choice, errors = {}, []
    for trail, action in decisions:
        try:
            v, assign, cost = rep.due(trail)
        except BadTrail as e:
            errors.append(f"the decision after {list(trail)} take {action} does not replay: {e}")
            continue
        if action not in proc.children(v):
            errors.append(f"the decision after {list(trail)} takes {action}, no child of the choice {v}")
            continue
        by_choice.setdefault(v, []).append((assign, action, tuple(trail), cost))
    return by_choice, errors


def check_names(proc: Process, K, e, problems):
    """The names of the draws in the tests of one tree. Returns the tests
    read."""
    t = proc.t
    v = e["choice"]["id"]
    tests = tests_of(e["tree"], [])
    for node in tests:
        try:
            u = node["draw"]["id"]
            if not isinstance(u, int) or not (1 <= u <= proc.n) or proc.kind(u) not in (cd.NATURE, cd.LOOP):
                problems.append(f"choice {v}: a test at the node {u}, neither a nature node nor a loop")
                continue
            is_loop = proc.kind(u) == cd.LOOP
            where = f"choice {v}: the test at the node {u}"
            above = [l + 1 for l in cd.loops_above(t, u - 1)]
            if "passes" not in node or not isinstance(node["passes"], list):
                problems.append(f"{where} has no passes")
                continue
            named = [x["loop"]["id"] for x in node["passes"]]
            if named != above:
                problems.append(f"{where} names the loops {named}, the loops above it from the outermost are {above}")
            for x in node["passes"]:
                if not isinstance(x["pass"], int) or isinstance(x["pass"], bool) or not (1 <= x["pass"] <= K):
                    problems.append(f"{where} is in the pass {x['pass']} of the loop {x['loop']['id']}, outside 1 to {K}")
            if is_loop:
                if "pass" not in node:
                    problems.append(f"{where}, a loop, has no pass of its own")
                else:
                    j = node["pass"]
                    if not isinstance(j, int) or isinstance(j, bool) or j < 1:
                        problems.append(f"{where}, a loop, follows its pass {j}")
                    elif j >= K:
                        problems.append(f"{where}, a loop, follows its pass {j}: at the bound {K} the exit is forced and is no draw")
            elif "pass" in node:
                problems.append(f"{where}, a nature node, has a pass of its own, {node['pass']}")
            if "value" not in node or not isinstance(node["value"], dict) or "is" not in node["value"]:
                problems.append(f"{where} has no value")
            else:
                val = node["value"]
                if val["is"] == "child":
                    if is_loop or val.get("child", {}).get("id") not in proc.children(u):
                        problems.append(f"{where} has the value {strip(val)}, no child drawn there")
                elif val["is"] in ("again", "out"):
                    if not is_loop:
                        problems.append(f"{where}, a nature node, has the value {val['is']}")
                else:
                    problems.append(f"{where} has the value {val['is']}")
            for side in ("yes", "no"):
                if side not in node or not isinstance(node[side], dict):
                    problems.append(f"{where} has no branch {side}")
            if "branches" in node or "occurrence" in node:
                problems.append(f"{where} still carries the fields of the count by occurrences")
        except (KeyError, TypeError, AttributeError) as err:
            problems.append(f"choice {v}: a test does not read: {type(err).__name__} {err}")
    return len(tests)


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
    if not isinstance(e["minimal"], bool):
        problems.append(f"choice {v}: minimal is {e['minimal']}")

    def walk(node, here, path):
        if "take" in node:
            if node["take"]["id"] not in proc.children(v):
                problems.append(f"choice {v}: a leaf takes {node['take']['id']}, no child of the choice")
            elif any(r[1] != node["take"]["id"] for r in here):
                problems.append(f"choice {v}: a leaf takes {node['take']['id']} and a history that reaches it does not")
            return
        u = node["draw"]["id"]
        if not (1 <= u <= proc.n) or proc.kind(u) not in (cd.NATURE, cd.LOOP):
            problems.append(f"choice {v}: a test at the node {u}, neither a nature node nor a loop")
            return
        is_loop = proc.kind(u) == cd.LOOP
        if node["loop"] != is_loop:
            problems.append(f"choice {v}: the test at {u} says loop {node['loop']}")
        if [o["id"] for o in node["options"]] != proc.children(u):
            problems.append(f"choice {v}: the test at {u} has options {[o['id'] for o in node['options']]}")
        statement = (test_key(node), bvalue(node["value"]))
        if statement in path:
            problems.append(f"choice {v}: the statement {statement} is made twice along a path")
        yes = [r for r in here if holds(node, r[0])]
        no = [r for r in here if not holds(node, r[0])]
        if not yes:
            problems.append(f"choice {v}: the statement {statement} holds of no history that reaches it")
        if not no:
            problems.append(f"choice {v}: the statement {statement} holds of every history that reaches it")
        walk(node["yes"], yes, path | {statement})
        walk(node["no"], no, path | {statement})
    walk(e["tree"], rows, frozenset())


def analyse(res: Result, prefix, proc: Process, K, decisions, explanation, point, rep=None, limits=(9, 10)):
    """classify, trees, names, shape and minimal on one strategy and its
    trees, and the impacts-* checks on the readings of the impacts.
    Returns the rows by choice, or None when the strategy does not
    replay."""
    rep = rep or Replayer(proc, K)
    ctx = dict(point=point, trees=explanation, strategy=[[list(t), a] for t, a in decisions][:200])
    try:
        by_choice, errors = rows_of(rep, proc, decisions)
    except SameName as err:
        res.fail(prefix + "classify", f"on the simulator: {err}", **ctx)
        return None
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
    for v, e in sorted(trees.items()):
        problems = []
        n = check_names(proc, K, e, problems)
        if problems:
            res.fail(prefix + "names", "; ".join(problems[:4]), **ctx)
        else:
            res.ok(prefix + "names")
        if not prefix:
            res.note("tests whose names were read", n)
    for v, rows in sorted(by_choice.items()):
        e = trees.get(v)
        if e is None:
            continue
        bad = None
        for (hist, action, trail, _) in rows:
            try:
                leaf = evaluate(e["tree"], hist)
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
        try:
            check_shape(proc, e, rows, problems)
        except (KeyError, TypeError) as err:
            problems.append(f"choice {v}: the tree does not read: {type(err).__name__} {err}")
        if problems:
            res.fail(prefix + "shape", "; ".join(problems[:4]), **ctx)
        else:
            res.ok(prefix + "shape")
        # what a test may state: a draw, and a value it gave in some row
        statements = sorted({(key, val) for (hist, _, _, _) in rows for key, val in hist.items()})
        if e["minimal"] is not True:
            res.note("trees not claimed minimal")
        elif len(rows) > limits[0] or len(statements) > limits[1]:
            res.skip(f"minimal: more than {limits[0]} rows or {limits[1]} statements")
        else:
            truth = [tuple(hist.get(key) == val for (key, val) in statements) for (hist, _, _, _) in rows]
            best = fewest(truth, [a for (_, a, _, _) in rows])
            if best is None:
                res.fail(prefix + "minimal", f"choice {v}: no tree on the statements separates the rows", **ctx)
            elif best != (e["tests"], e["depth"]):
                res.fail(prefix + "minimal", f"choice {v}: the tool says {e['tests']} tests and depth {e['depth']}, "
                         f"the fewest are {best[0]} tests, then depth {best[1]}, on {len(rows)} rows and the "
                         f"statements {statements}", **ctx)
            else:
                res.ok(prefix + "minimal")
                if best[0] > 0:
                    res.note("minimal trees with a test, checked by exhaustion")
        impact_checks(res, prefix, proc, e, rows, ctx, limits)
    return by_choice


class Lost(Exception):
    pass


def recover(proc: Process, K, explanation, cap):
    """The whole process walked at K with the trees alone deciding. Returns
    the expected cost, the decisions met as (trail, action), and one final
    trail. Raises Lost when a choice has no tree or its tree does not
    evaluate."""
    t = proc.t
    trees = {e["choice"]["id"]: e["tree"] for e in explanation}
    total = [F(0)] * proc.k
    met = set()
    finals, a_final = 0, None
    stack = [(cd.initial(t), {}, F(1), [F(0)] * proc.k, ())]
    while stack:
        s, hist, prob, cost, trail = stack.pop()
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
                    a = evaluate(tree, hist)
                except NoBranch as err:
                    raise Lost(f"the choice {v + 1} is met after {list(trail)}: {err}")
                nxt = [m[2] for m in mv if m[0] == a - 1]
                if not nxt:
                    raise Lost(f"the tree of the choice {v + 1} takes {a}, no child of it")
                met.add((trail, a))
                trail = trail + (a,)
                s = nxt[0]
            elif r == 4 and len(mv) == 2:
                key = draw_key(t, s, v)
                if key in hist:
                    raise Lost(f"two draws of one history at the node {key[0]} in the passes {list(key[1])}, after {list(trail)}")
                for i, (_, pr, nxt, _) in enumerate(mv):
                    if t.kind[v] == cd.NATURE:
                        child = cd.entry(t, s, v, nxt)[1]
                        value, x = ("child", child + 1), child + 1
                    elif i == 0:
                        value, x = AGAIN, t.low[v] + 1
                    else:
                        value, x = OUT, v + 1
                    h2 = dict(hist)
                    h2[key] = value
                    stack.append((nxt, h2, prob * pr, list(cost), trail + (x,)))
                break
            elif r == 5:
                paid = proc.advance(s, mv[0][2])
                cost = [x + y for x, y in zip(cost, paid)]
                s = mv[0][2]
            else:
                s = mv[0][2]
    return total, met, a_final


def beyond(proc: Process, K, explanation, cap):
    """The process walked at K + 1 passes, every choice decided by the tree
    of that choice on the history so far. A pass past K is a pass of a
    region that has closed K passes or more; a node is inside one at a
    state when some loop above it has such a region there. Returns the
    whole expected cost, the expected impact paid at the tasks closed
    outside the passes past K, and what was met. Raises Lost when the walk
    fails."""
    t = proc.t
    above = [cd.loops_above(t, u) for u in range(t.n)]
    trees = {e["choice"]["id"]: e["tree"] for e in explanation}
    total = [F(0)] * proc.k
    kept = [F(0)] * (proc.k - 1)
    seen = {"finals": 0, "outside": 0, "inside": 0, "no tree inside": 0, "past": 0}
    stack = [(cd.initial(t), {}, F(1), [F(0)] * proc.k, [F(0)] * (proc.k - 1), ())]
    while stack:
        s, hist, prob, cost, mine, trail = stack.pop()
        while True:
            r, v, mv = cd.moves(t, s, K + 1)
            if r is None:
                seen["finals"] += 1
                if seen["finals"] > cap:
                    raise OverflowError
                for c in range(proc.k):
                    total[c] += prob * cost[c]
                for c in range(proc.k - 1):
                    kept[c] += prob * mine[c]
                break
            if r == 1:
                extra = any(s[1][t.low[l]] >= K for l in above[v])
                tree = trees.get(v + 1)
                if tree is None:
                    if not extra:
                        raise Lost(f"at {K + 1} passes the choice {v + 1} is met after {list(trail)}, outside the passes "
                                   f"past {K}, and has no tree")
                    seen["no tree inside"] += 1
                    a = t.low[v] + 1
                else:
                    try:
                        a = evaluate(tree, hist)
                    except NoBranch as err:
                        raise Lost(f"at {K + 1} passes the choice {v + 1} is met after {list(trail)}: {err}")
                seen["inside" if extra else "outside"] += 1
                nxt = [m[2] for m in mv if m[0] == a - 1]
                if not nxt:
                    raise Lost(f"at {K + 1} passes the tree of the choice {v + 1} takes {a}, no child of it")
                trail = trail + (a,)
                s = nxt[0]
            elif r == 4 and len(mv) == 2:
                key = draw_key(t, s, v)
                if key in hist:
                    raise Lost(f"at {K + 1} passes two draws of one history at the node {key[0]} in the passes "
                               f"{list(key[1])}, after {list(trail)}")
                if any(x > K for x in key[1]) or (t.kind[v] == cd.LOOP and key[1][-1] >= K):
                    seen["past"] += 1
                for i, (_, pr, nxt, _) in enumerate(mv):
                    if t.kind[v] == cd.NATURE:
                        child = cd.entry(t, s, v, nxt)[1]
                        value, x = ("child", child + 1), child + 1
                    elif i == 0:
                        value, x = AGAIN, t.low[v] + 1
                    else:
                        value, x = OUT, v + 1
                    h2 = dict(hist)
                    h2[key] = value
                    stack.append((nxt, h2, prob * pr, list(cost), list(mine), trail + (x,)))
                break
            elif r == 5:
                nxt = mv[0][2]
                paid = proc.advance(s, nxt)
                cost = [x + y for x, y in zip(cost, paid)]
                mine = list(mine)
                for u in t.tasks:
                    if s[0][u] >= 0 and nxt[0][u] == cd.DONE and all(s[1][t.low[l]] < K for l in above[u]):
                        for c, x in enumerate(proc.impact[u]):
                            mine[c] += x
                s = nxt
            else:
                s = mv[0][2]
    return total, kept, seen


def beyond_checks(res: Result, proc: Process, K, p, explanation, cap, ctx):
    """The four checks past the bound on one point."""
    value = [F(x) for x in p["value"]]
    try:
        total, kept, seen = beyond(proc, K, explanation, cap)
    except OverflowError:
        res.skip("beyond: more histories than the cap")
        return
    except Lost as e:
        res.fail("beyond-total", str(e), **ctx)
        return
    res.ok("beyond-total")
    res.note("beyond: choices decided outside the passes past the bound", seen["outside"])
    res.note("beyond: choices met inside a pass past the bound", seen["inside"])
    res.note("beyond: of which with no tree, the low child taken", seen["no tree inside"])
    res.note("beyond: draws past the bound", seen["past"])
    if seen["past"]:
        res.note("beyond: points whose walk takes a draw past the bound")
    shown = lambda xs: [str(x) for x in xs]  # noqa: E731
    # with a loop under a parallel node the explanation may depart from the
    # strategy past the bound, or the strategy have no continuation: what
    # does not hold there is counted apart
    under = cd.loop_under_par(proc.t)
    real_fail = res.fail

    def departs(name, detail, **kw):
        if under:
            res.note(f"beyond: a loop under a parallel node, {name} does not hold past the bound")
        else:
            real_fail(name, detail, **kw)
    if kept != value[1:]:
        departs("beyond-impact", f"at {K + 1} passes the impact paid outside the passes past {K} is {shown(kept)}, "
                f"the point pays {shown(value[1:])}", **ctx)
    else:
        res.ok("beyond-impact")
    if total[0] < value[0]:
        departs("beyond-time", f"at {K + 1} passes the expected time is {total[0]}, less than {value[0]} of the point", **ctx)
    else:
        res.ok("beyond-time")
    cont = p.get("continued")
    if cont is None:
        if total != value:
            res.fail("beyond-gap", f"the point has no continued and at {K + 1} passes the cost is {shown(total)}, "
                     f"the point says {shown(value)}", **ctx)
        else:
            res.ok("beyond-gap")
            res.note("beyond: points with no continued, equal at one pass more")
    else:
        up = [F(x) for x in cont["up"]]
        low = [F(x) for x in cont["low"]]
        if len(up) != len(value) or len(low) != len(value):
            res.fail("beyond-gap", f"continued has {len(low)} and {len(up)} components, the value {len(value)}", **ctx)
        elif any(total[c] > value[c] + up[c] for c in range(len(value))):
            res.fail("beyond-gap", f"at {K + 1} passes the cost is {shown(total)}, past the value {shown(value)} plus the "
                     f"upper end of continued {shown(up)}", **ctx)
        else:
            res.ok("beyond-gap")
            if any(total[c] < value[c] + low[c] for c in range(len(value))):
                res.note("beyond: points below the lower end of continued at one pass more (expected: one pass, not all)")


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
    try:
        by_choice, errors = rows_of(rep, proc, decisions)
    except SameName as err:
        return str(err)
    if errors:
        return errors[0]
    for v, rows in by_choice.items():
        seen = {}
        for (assign, action, trail, _) in rows:
            key = frozenset(assign.items())
            if seen.setdefault(key, action) != action:
                return f"two actions at the choice {v} after the same draws"
    return None


def page_checks(res: Result, proc: Process, K, port, points, rng, final_trail, n_mut, limits):
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
    v0, _, _ = rep.due(trail0)
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
                analyse(res, "page-mutation-", proc, K, ds, got["explanations"], label, rep, limits=limits)
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
    analyse(res, "determine-", proc, K, decisions, explanation, f"{label}, explain --B {budget}",
            limits=cfg["limits"])


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
        res.note(f"ran past the timeout: {name} K={K}")
        return
    if not report.exists():
        if "TooManyOutcomes" in out + errtext or "too many" in (out + errtext).lower():
            res.skip("frontier: failed, too many outcomes")
        else:
            res.fail("pareto", f"sdcpi pareto exits {code} and writes no report: {(out + errtext)[-400:]}")
        return
    data = json.loads(report.read_text())
    if data["status"] == "timeout" and cfg["cut"]:
        res.note("frontiers cut by the time limit, their points checked")
    elif data["status"] != "complete":
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
        all_tests = [n for e in explanation for n in tests_of(e["tree"], [])]
        if any(n.get("loop") is True for n in all_tests):
            res.note("points testing a loop draw")
        if any(len(n.get("passes", [])) >= 1 and n.get("loop") is False for n in all_tests):
            res.note("points testing a nature draw under a loop")
        if any(len(n.get("passes", [])) + (1 if "pass" in n else 0) >= 2 for n in all_tests):
            res.note("points testing a draw named by two passes or more")
        if any(len(n.get("passes", [])) + (1 if "pass" in n else 0) >= 3 for n in all_tests):
            res.note("points testing a draw named by three passes or more")
        ctx = dict(point=label, trees=explanation, strategy=p["strategy"][:200])
        by_choice = analyse(res, "", proc, K, decisions, explanation, label, limits=cfg["limits"])
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
        if proc.t.loops:
            beyond_checks(res, proc, K, p, explanation, cfg["cap"], ctx)
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
        page_checks(res, proc, K, cfg["port"], [(c[0], c[1], c[2]) for c in picks], rng, final_trail, cfg["mutations"],
                    cfg["limits"])


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
    ap.add_argument("--port", type=int, default=8637, help="the port of the server of the suite; 0 leaves the page out")
    ap.add_argument("--timeout", type=float, default=60, help="seconds a run of the tool may take (default 60)")
    ap.add_argument("--time-limit", type=float, default=20, help="the --time-limit of pareto (default 20)")
    ap.add_argument("--sample", type=int, default=40, help="points checked per frontier (default 40)")
    ap.add_argument("--max-points", type=int, default=300, help="a larger frontier is skipped (default 300)")
    ap.add_argument("--max-decisions", type=int, default=1500, help="a larger strategy is skipped (default 1500)")
    ap.add_argument("--cap", type=int, default=20000, help="histories of a walk by the trees (default 20000)")
    ap.add_argument("--determine", type=int, default=2, help="points per frontier given to explain (default 2)")
    ap.add_argument("--page-points", type=int, default=5, help="points per frontier given to the page (default 5)")
    ap.add_argument("--mutations", type=int, default=10, help="mutated decisions per frontier (default 10)")
    ap.add_argument("--cut", type=int, default=1, help="1 checks the points of a frontier the time limit cut (default 1)")
    ap.add_argument("--min-rows", type=int, default=9, help="rows of a tree checked minimal by exhaustion (default 9)")
    ap.add_argument("--min-statements", type=int, default=10, help="statements of such a tree (default 10)")
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
           "determine": args.determine, "page_points": args.page_points, "mutations": args.mutations, "seed": args.seed,
           "cut": args.cut, "limits": (args.min_rows, args.min_statements)}
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
