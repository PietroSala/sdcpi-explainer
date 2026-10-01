#!/usr/bin/env python3
"""The engine with loops against exact backward induction.

    check.py DIR                      every .cpi file of DIR (see gen.py)
    check.py a.cpi b.cpi              the files named
    check.py --text '(0.5 ((A, 1, {euro: 10}) ^ (B, 2, {euro: 4})))'
    check.py --hand                   the hand-written instances alone
    check.py DIR --jobs 8 --out report.json

The binary is `SDCPI_BIN` or `--sdcpi`, by default `target/release/sdcpi`
at the root of the repository. Everything generated goes to `--work`, by
default `work/` inside the folder of the instances: the instance file of
every process, the JSON of every frontier, and the report.

What is checked, for every instance and every `K` in `--k` (1..4):

  enclosure      `sdcpi bound F --max-iter K`, with and without `--time 1`:
                 the lines `worst L-min` and `worst max-U`, the worst breach
                 of the two bounds over every reachable choice state as the
                 engine measures it against its own exact pass, at most 1e-9.
  optima         the `exact min` and `exact max` of the engine against the
                 exact pass of this file, which walks the semantics of
                 LOOPS.md with the phase and the count of every node and
                 never the quotient of the engine, in rational arithmetic;
                 the engine prints six decimals, so the slack is 1e-6.
  bounds         `U at s0` and `L at s0` against the recursion of LOOPS.md
                 transcribed here, to the six printed decimals from `bound`
                 and exactly from the rationals of the frontier.
  status         `sdcpi pareto F --onephase 1 --max-iter K --time 1`
                 reports `complete`; every point of the frontier lies in
                 the box of the two bounds; the least value of every
                 component over the frontier is the exact minimum; on an
                 instance without choices the frontier is one point, whose
                 value is the exact expected cost and whose `continued` is
                 the expected gap of the forced exits, both exactly.

And the gap test, on the instances without choices, for `K` in `--gap-k`
(1..5): the value of the one point plus `continued.low`, and plus
`continued.up`, enclose the expected cost of the unbounded process, which
is `U at s0` of `sdcpi bound F --max-iter inf` for every component where
`U` and `L` agree, that is every impact and the time of a process without
parallel nodes; the time of a process with a parallel node is the
solution of the Markov chain of the unbounded process, solved here in
floating point to 1e-12 and compared with a slack of 1e-8. The value grows
with `K` and its distance to the unbounded value shrinks. The time of
`continued.low` is zero exactly when every loop sits under a parallel
node. The gap of a forced exit is what LOOPS.md states: `p / (1 - p)`
passes, each worth the charge of the region in the unbounded process, the
loops inside the region unbounded too, scaled by the probability of the
path; the lower bound of the time is zero under a parallel node.

One reading of LOOPS.md is settled here: the exit of a loop resets the
counts of the nodes below it and counts as a closing of the loop node for
the loop above, so that a loop whose region is a loop is bounded too; the
engine keeps the count of a region on the loop node above it, which reads
the same.

The exit code is 0 when no check fails, 1 otherwise, 2 when the run
cannot start. A run that exceeds `--timeout` seconds is reported apart,
as is an exact pass that exceeds `--cap` states; neither is a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

IDLE = -1
DONE = -2

# ---------------------------------------------------------------------------
# the tree, read from the instance file the engine reads
# ---------------------------------------------------------------------------


class Node:
    __slots__ = ("id", "kind", "low", "high", "duration", "prob", "impact", "name")

    def __init__(self, id, kind, low, high, duration, prob, impact, name):
        self.id = id
        self.kind = kind
        self.low = low
        self.high = high
        self.duration = duration
        self.prob = prob
        self.impact = impact
        self.name = name


class Tree:
    def __init__(self, nodes, root, names, k, time=False):
        self.nodes = nodes  # index 0 a filler
        self.root = root
        self.n = len(nodes) - 1
        self.names = names
        self.k = k
        self.time = time
        self.tasks = [v for v in range(1, self.n + 1) if nodes[v].kind == "task"]
        self.loops = [v for v in range(1, self.n + 1) if nodes[v].kind == "loop"]
        self.choices = [v for v in range(1, self.n + 1) if nodes[v].kind == "choice"]
        self.parallels = [v for v in range(1, self.n + 1) if nodes[v].kind == "parallel"]
        self.parent = [0] * (self.n + 1)
        for v in range(1, self.n + 1):
            n = nodes[v]
            if n.low:
                self.parent[n.low] = v
            if n.high:
                self.parent[n.high] = v
        self._subtree = {}

    def with_time(self) -> "Tree":
        """The same process with the time as the component zero of the cost."""
        if self.time:
            return self
        nodes = [None]
        for v in range(1, self.n + 1):
            n = self.nodes[v]
            impact = None if n.impact is None else [Fraction(0)] + list(n.impact)
            nodes.append(Node(n.id, n.kind, n.low, n.high, n.duration, n.prob, impact, n.name))
        return Tree(nodes, self.root, ["time"] + list(self.names), self.k + 1, time=True)

    def subtree(self, v) -> list:
        """The identifiers of the subtree of `v`, `v` included."""
        if v not in self._subtree:
            out = []
            stack = [v]
            while stack:
                u = stack.pop()
                out.append(u)
                n = self.nodes[u]
                if n.low:
                    stack.append(n.low)
                if n.high:
                    stack.append(n.high)
            self._subtree[v] = out
        return self._subtree[v]

    def under_parallel(self, v) -> bool:
        """A parallel node on the path from the root to `v`, `v` excluded."""
        u = self.parent[v]
        while u:
            if self.nodes[u].kind == "parallel":
                return True
            u = self.parent[u]
        return False


def read_instance(path: Path) -> Tree:
    lines = path.read_text().splitlines()
    header = {}
    i = 0
    while i < len(lines):
        body = lines[i].strip()
        i += 1
        if not body or body.startswith("#"):
            continue
        if body == "tree:":
            break
        key, _, value = body.partition(":")
        header[key.strip()] = value.strip()
    names = [n.strip() for n in header.get("impact_names", "").strip("[]").split(",") if n.strip()]
    k = int(header["dimensions"])
    count = int(header["nodes"])
    nodes = [None] * (count + 1)

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def node() -> int:
        nonlocal i
        while i < len(lines) and not lines[i].strip():
            i += 1
        line = lines[i]
        ind = indent(line)
        body = line.strip()
        if not body.startswith("- "):
            raise ValueError(f"{path}: line {i + 1}: a node opens on `- `")
        kind, _, value = body[2:].partition(":")
        kind = kind.strip()
        value = value.strip()
        i += 1
        field_indent = ind + 2
        nid = None
        duration = 0
        impact = None
        low = high = 0
        while i < len(lines):
            if not lines[i].strip():
                i += 1
                continue
            if indent(lines[i]) < field_indent:
                break
            key, _, val = lines[i].strip().partition(":")
            key = key.strip()
            val = val.strip()
            if key == "id":
                nid = int(val)
                i += 1
            elif key == "duration":
                duration = int(val)
                i += 1
            elif key == "impact":
                impact = [Fraction(x.strip()) for x in val.strip("[]").split(",") if x.strip()]
                i += 1
            elif key == "low":
                i += 1
                low = node()
            elif key == "high":
                i += 1
                high = node()
            else:
                raise ValueError(f"{path}: line {i + 1}: unknown key {key!r}")
        prob = Fraction(value) if kind in ("nature", "loop") else None
        name = value if kind == "task" else ""
        nodes[nid] = Node(nid, kind, low, high, duration, prob, impact, name)
        return nid

    root = node()
    if any(n is None for n in nodes[1:]):
        raise ValueError(f"{path}: the tree does not use every identifier")
    return Tree(nodes, root, names, k)


# ---------------------------------------------------------------------------
# the semantics: phases and counts, the five ranks, the clauses
# ---------------------------------------------------------------------------


def rank_witness(t: Tree, s):
    """The rank and the witness of a state, `None` at the final state: the
    least rank that holds somewhere, at the holder of least identifier."""
    best = [0] * 6
    nodes = t.nodes
    for v in range(1, t.n + 1):
        n = nodes[v]
        sv = s[v]
        kind = n.kind
        if kind == "task":
            if 0 <= sv < n.duration:
                r = 5
            else:
                continue
        elif kind == "loop":
            lo = s[n.low]
            if lo == DONE:
                r = 4
            elif sv >= 0 and lo == IDLE:
                r = 2
            else:
                continue
        else:
            lo = s[n.low]
            hi = s[n.high]
            if kind == "parallel":
                if lo == DONE and hi == DONE:
                    r = 3
                elif sv >= 0 and lo == IDLE and hi == IDLE:
                    r = 2
                else:
                    continue
            else:
                if lo == DONE or hi == DONE:
                    r = 3
                elif sv >= 0 and lo == IDLE and hi == IDLE:
                    r = {"sequence": 2, "choice": 1, "nature": 4}[kind]
                else:
                    continue
        if best[r] == 0:
            best[r] = v
            if r == 1:
                return (1, v)
    for r in range(2, 6):
        if best[r]:
            return (r, best[r])
    return None


def initial(t: Tree):
    s = [IDLE] * (t.n + 1)
    s[t.root] = 0
    return (tuple(s), tuple([0] * (t.n + 1)))


def expand(t: Tree, state, K, charges):
    """The successors of a state.

    ("final",)
    ("choice", v, [state_low, state_high])
    ("move", [(prob, cost, gap, state), ...])   gap None or 2k values

    `K` is the bound on the passes, `None` for the unbounded process, whose
    counts are not kept: the state then carries `None` as its counts. The
    `charges` map a loop node to the 2k values its forced exit adds to the
    gap, the lower then the upper bound; `None` keeps no gap.
    """
    s, cnt = state
    k = t.k
    rw = rank_witness(t, s)
    if rw is None:
        return ("final",)
    rank, v = rw
    n = t.nodes[v]
    zero = (Fraction(0),) * k
    counted = cnt is not None
    if rank == 1:
        lo = list(s)
        lo[n.low] = 0
        hi = list(s)
        hi[n.high] = 0
        return ("choice", v, [(tuple(lo), cnt), (tuple(hi), cnt)])
    if rank == 2:
        s2 = list(s)
        s2[n.low] = 0
        if n.kind == "parallel":
            s2[n.high] = 0
        return ("move", [(Fraction(1), zero, None, (tuple(s2), cnt))])
    if rank == 3:
        s2 = list(s)
        c2 = list(cnt) if counted else None
        if n.kind == "sequence" and s[n.low] == DONE and s[n.high] == IDLE:
            s2[n.low] = IDLE
            s2[n.high] = 0
        else:
            s2[n.low] = IDLE
            s2[n.high] = IDLE
            s2[v] = DONE
            if counted:
                c2[v] += 1
        return ("move", [(Fraction(1), zero, None, (tuple(s2), tuple(c2) if counted else None))])
    if rank == 4 and n.kind == "nature":
        lo = list(s)
        lo[n.low] = 0
        hi = list(s)
        hi[n.high] = 0
        p = n.prob
        return ("move", [(p, zero, None, (tuple(lo), cnt)), (1 - p, zero, None, (tuple(hi), cnt))])
    if rank == 4:
        # the draw of a loop: the region closed m times in this activation
        r = n.low
        exit_s = list(s)
        exit_s[v] = DONE
        exit_s[r] = IDLE
        exit_c = None
        if counted:
            exit_c = list(cnt)
            for u in t.subtree(v):
                exit_c[u] = 0
            # the exit closes the loop node for the loop above it
            exit_c[v] = cnt[v] + 1
            exit_c = tuple(exit_c)
        exit_state = (tuple(exit_s), exit_c)
        m = cnt[r] if counted else 0
        if K is None or m < K:
            again = list(s)
            again[r] = 0
            p = n.prob
            return ("move", [(p, zero, None, (tuple(again), cnt)), (1 - p, zero, None, exit_state)])
        gap = charges[v] if charges is not None else None
        return ("move", [(Fraction(1), zero, gap, exit_state)])
    # rank 5: the fast forward, the only clause that pays
    running = [(u, t.nodes[u].duration - s[u]) for u in t.tasks if s[u] >= 0]
    step = min(left for _, left in running)
    s2 = list(s)
    c2 = list(cnt) if counted else None
    cost = [Fraction(0)] * k
    for u, left in running:
        if left == step:
            s2[u] = DONE
            if counted:
                c2[u] += 1
            imp = t.nodes[u].impact
            for c in range(k):
                cost[c] += imp[c]
        else:
            s2[u] = s[u] + step
    if t.time:
        cost[0] += step
    return ("move", [(Fraction(1), tuple(cost), None, (tuple(s2), tuple(c2) if counted else None))])


class TooManyStates(Exception):
    pass


def exact_pass(t: Tree, K: int, charges, cap: int):
    """The least and the greatest expected cost of every component over the
    strategies of the truncated process, from the initial state, and, when
    the process has no choice, the expected gap of its forced exits, `2k`
    values. Backward induction over the graph of the states, which the
    bound on the passes makes finite and acyclic."""
    k = t.k
    start = initial(t)
    expansions = {}

    def succs(state):
        exp = expand(t, state, K, charges)
        expansions[state] = exp
        if exp[0] == "final":
            return []
        if exp[0] == "choice":
            return list(exp[2])
        return [item[3] for item in exp[1]]

    frames = [(start, iter(succs(start)))]
    seen = {start}
    post = []
    while frames:
        state, it = frames[-1]
        nxt = next(it, None)
        if nxt is None:
            frames.pop()
            post.append(state)
        elif nxt not in seen:
            seen.add(nxt)
            if len(seen) > cap:
                raise TooManyStates(len(seen))
            frames.append((nxt, iter(succs(nxt))))

    keep_gap = charges is not None and not t.choices
    zeros = [Fraction(0)] * k
    gzeros = [Fraction(0)] * (2 * k)
    value = {}
    for state in post:
        exp = expansions[state]
        if exp[0] == "final":
            value[state] = (zeros, zeros, gzeros)
            continue
        if exp[0] == "choice":
            a = value[exp[2][0]]
            b = value[exp[2][1]]
            lo = [min(x, y) for x, y in zip(a[0], b[0])]
            hi = [max(x, y) for x, y in zip(a[1], b[1])]
            value[state] = (lo, hi, gzeros)
            continue
        lo = [Fraction(0)] * k
        hi = [Fraction(0)] * k
        gap = [Fraction(0)] * (2 * k) if keep_gap else gzeros
        for prob, cost, step_gap, nxt in exp[1]:
            nlo, nhi, ngap = value[nxt]
            for c in range(k):
                lo[c] += prob * (cost[c] + nlo[c])
                hi[c] += prob * (cost[c] + nhi[c])
            if keep_gap:
                for c in range(2 * k):
                    gap[c] += prob * ngap[c]
                if step_gap is not None:
                    for c in range(2 * k):
                        gap[c] += prob * step_gap[c]
        value[state] = (lo, hi, gap)
    lo, hi, gap = value[start]
    return lo, hi, (gap if keep_gap else None), len(seen)


# ---------------------------------------------------------------------------
# the two bounds of LOOPS.md, from a state
# ---------------------------------------------------------------------------


def geometric(p: Fraction, further) -> Fraction:
    """`p + ... + p^further`, or `p / (1 - p)` when further is None."""
    if further is None:
        return p / (1 - p)
    total = Fraction(0)
    power = p
    for _ in range(further):
        total += power
        power *= p
    return total


def bounds(t: Tree, s, cnt, v, K):
    """`(U, L)` at the node `v` from the state, `K` the bound on the passes
    or `None`; the counts are those of the semantics of this file."""
    k = t.k
    zero = [Fraction(0)] * k
    if s[v] == DONE:
        return zero, zero
    n = t.nodes[v]
    kind = n.kind
    if kind == "task":
        i = list(n.impact)
        if t.time:
            i[0] = Fraction(n.duration - (s[v] if s[v] >= 0 else 0))
        return i, list(i)
    if kind == "sequence":
        if s[n.high] != IDLE:
            return bounds(t, s, cnt, n.high, K)
        ua, la = bounds(t, s, cnt, n.low, K)
        ub, lb = bounds(t, s, cnt, n.high, K)
        return [x + y for x, y in zip(ua, ub)], [x + y for x, y in zip(la, lb)]
    if kind == "parallel":
        ua, la = bounds(t, s, cnt, n.low, K)
        ub, lb = bounds(t, s, cnt, n.high, K)
        u = [x + y for x, y in zip(ua, ub)]
        l = [x + y for x, y in zip(la, lb)]
        if t.time:
            l[0] = max(la[0], lb[0])
        return u, l
    if kind == "loop":
        r = n.low
        if s[r] == DONE:
            u, l = zero, list(zero)
            m = cnt[r]
        elif s[r] >= 0:
            u, l = bounds(t, s, cnt, r, K)
            m = cnt[r] + 1
        else:
            u, l = bounds(t, s, cnt, r, K)
            m = 1
        further = None if K is None else max(K - m, 0)
        g = geometric(n.prob, further)
        pu, pl = pass_bounds(t, v, K)
        return [x + g * y for x, y in zip(u, pu)], [x + g * y for x, y in zip(l, pl)]
    lo = s[n.low]
    hi = s[n.high]
    if lo >= 0:
        return bounds(t, s, cnt, n.low, K)
    if hi >= 0:
        return bounds(t, s, cnt, n.high, K)
    if lo == IDLE and hi == IDLE:
        ua, la = bounds(t, s, cnt, n.low, K)
        ub, lb = bounds(t, s, cnt, n.high, K)
        if kind == "choice":
            return [max(x, y) for x, y in zip(ua, ub)], [min(x, y) for x, y in zip(la, lb)]
        p = n.prob
        return [p * x + (1 - p) * y for x, y in zip(ua, ub)], [p * x + (1 - p) * y for x, y in zip(la, lb)]
    return zero, list(zero)


def pass_bounds(t: Tree, v, K):
    """`(U, L)` of the region of the loop `v` from its idle state."""
    idle = tuple([IDLE] * (t.n + 1))
    cnt = tuple([0] * (t.n + 1))
    return bounds(t, idle, cnt, t.nodes[v].low, K)


def bounds_at_start(t: Tree, K):
    s, cnt = initial(t)
    return bounds(t, s, cnt, t.root, K)


def continuation(t: Tree, v):
    """What the unbounded process would still pay at a forced exit of the
    loop `v`: the lower then the upper bound, `2k` values, `p / (1 - p)`
    passes each worth the charge of the region from idle with its loops
    unbounded, the time of the lower bound zero under a parallel node."""
    g = geometric(t.nodes[v].prob, None)
    pu, pl = pass_bounds(t, v, None)
    low = [g * x for x in pl]
    up = [g * x for x in pu]
    if t.time and t.under_parallel(v):
        low[0] = Fraction(0)
    return low + up


def charges_of(t: Tree):
    """Per loop node, the `2k` values its forced exit adds to the gap."""
    return {v: continuation(t, v) for v in t.loops}


# ---------------------------------------------------------------------------
# the unbounded process without choices: its Markov chain, in floating point
# ---------------------------------------------------------------------------


def unbounded_chain(t: Tree, cap: int, tol: float = 1e-13, sweeps: int = 50000):
    """The expected cost of the unbounded process from its initial state,
    every component, by Gauss-Seidel over the reachable states; the process
    has no choice. Returns the vector, the number of states and whether the
    iteration converged."""
    s0 = initial(t)[0]
    start = (s0, None)
    index = {start: 0}
    order = [start]
    trans = []
    i = 0
    while i < len(order):
        state = order[i]
        exp = expand(t, state, None, None)
        if exp[0] == "final":
            trans.append([])
        elif exp[0] == "choice":
            raise ValueError("the process has a choice")
        else:
            row = []
            for prob, cost, _, nxt in exp[1]:
                if nxt not in index:
                    index[nxt] = len(order)
                    order.append(nxt)
                    if len(order) > cap:
                        raise TooManyStates(len(order))
                row.append((float(prob), [float(c) for c in cost], index[nxt]))
            trans.append(row)
        i += 1
    k = t.k
    V = [[0.0] * k for _ in order]
    converged = False
    for _ in range(sweeps):
        delta = 0.0
        scale = 1.0
        for j in range(len(order) - 1, -1, -1):
            row = trans[j]
            if not row:
                continue
            new = [0.0] * k
            for prob, cost, nxt in row:
                target = V[nxt]
                for c in range(k):
                    new[c] += prob * (cost[c] + target[c])
            old = V[j]
            for c in range(k):
                d = abs(new[c] - old[c])
                if d > delta:
                    delta = d
                if abs(new[c]) > scale:
                    scale = abs(new[c])
            V[j] = new
        if delta <= tol * scale:
            converged = True
            break
    return V[0], len(order), converged


# ---------------------------------------------------------------------------
# the binary
# ---------------------------------------------------------------------------


class Run:
    def __init__(self, args, rc, out, err, seconds, timed_out):
        self.args = args
        self.rc = rc
        self.out = out
        self.err = err
        self.seconds = seconds
        self.timed_out = timed_out


def run(sdcpi: str, args: list, timeout: float) -> Run:
    cmd = [sdcpi] + args
    started = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return Run(args, p.returncode, p.stdout, p.stderr, time.time() - started, False)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return Run(args, None, out, err, time.time() - started, True)


VEC = re.compile(r"\[([^\]]*)\]")


def vector(line: str) -> list:
    m = VEC.search(line)
    return [float(x) for x in m.group(1).split(",") if x.strip()]


def parse_bound(out: str) -> dict:
    d = {}
    for line in out.splitlines():
        if line.startswith("U at s0"):
            d["U"] = vector(line)
        elif line.startswith("L at s0"):
            d["L"] = vector(line)
        elif line.startswith("exact min"):
            d["exact_min"] = vector(line)
        elif line.startswith("exact max"):
            d["exact_max"] = vector(line)
        elif line.startswith("exact "):
            d["exact_unavailable"] = line.strip()
        elif line.startswith("worst L-min"):
            m = re.match(r"worst L-min\s+(\S+)\s+over\s+(\d+)", line)
            d["worst_low"] = float(m.group(1))
            d["states"] = int(m.group(2))
        elif line.startswith("worst max-U"):
            d["worst_high"] = float(line.split()[2])
        elif line.startswith("enclosure"):
            d["enclosure_unavailable"] = line.strip()
    return d


def rationals(items) -> list:
    return [Fraction(x) for x in items]


# ---------------------------------------------------------------------------
# the checks of one instance
# ---------------------------------------------------------------------------


def close(a, b, tol=1e-6) -> bool:
    return abs(a - b) <= tol + 1e-9 * max(abs(a), abs(b))


def fmt(v) -> str:
    return "[" + ", ".join(str(x) for x in v) + "]"


def fmtf(v) -> str:
    return "[" + ", ".join(f"{float(x):.9g}" for x in v) + "]"


class Report:
    def __init__(self, name: str, text: str):
        self.name = name
        self.text = text
        self.failures = []  # (check, detail)
        self.skipped = []  # (check, detail)
        self.runs = 0
        self.seconds = 0.0
        self.counts = {}
        self.notes = []
        # the bounds on the passes whose frontier the engine did not deliver
        self.no_frontier = set()

    def count(self, check: str):
        self.counts[check] = self.counts.get(check, 0) + 1

    def fail(self, check: str, detail: str):
        self.failures.append((check, detail))

    def skip(self, check: str, detail: str):
        self.skipped.append((check, detail))

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "text": self.text,
            "failures": [{"check": c, "detail": d} for c, d in self.failures],
            "skipped": [{"check": c, "detail": d} for c, d in self.skipped],
            "runs": self.runs,
            "seconds": round(self.seconds, 3),
            "counts": self.counts,
            "notes": self.notes,
        }


def check_instance(spec: tuple) -> dict:
    name, text, cfg = spec
    rep = Report(name, text)
    started = time.time()
    try:
        _check_instance(name, text, cfg, rep)
    except Exception as e:  # a crash of the harness is reported, not hidden
        rep.fail("harness", f"{type(e).__name__}: {e}")
    rep.seconds = time.time() - started
    return rep.as_dict()


def _check_instance(name: str, text: str, cfg: dict, rep: Report):
    sdcpi = cfg["sdcpi"]
    work = Path(cfg["work"])
    timeout = cfg["timeout"]
    cap = cfg["cap"]
    yaml = work / f"{name}.yaml"
    r = run(sdcpi, ["parse", text, "--out", str(yaml)], timeout)
    rep.runs += 1
    if r.rc != 0:
        rep.fail("parse", f"exit {r.rc}: {r.err.strip()}")
        return
    plain = read_instance(yaml)
    timed = plain.with_time()
    k = timed.k
    choice_free = not plain.choices

    for K in cfg["ks"]:
        # the exact pass of this file, the time in, the counts in full
        charges = charges_of(timed)
        mine = None
        try:
            lo, hi, gap, states = exact_pass(timed, K, charges, cap)
            mine = (lo, hi, gap)
            rep.notes.append(f"K={K}: {states} states in the exact pass")
        except TooManyStates as e:
            rep.skip("optima", f"K={K}: the exact pass passed {cap} states ({e})")
        my_u, my_l = bounds_at_start(timed, K)

        for with_time in (False, True):
            args = ["bound", str(yaml), "--max-iter", str(K)] + (["--time", "1"] if with_time else [])
            r = run(sdcpi, args, timeout)
            rep.runs += 1
            tag = f"K={K}{' time' if with_time else ''}"
            if r.timed_out:
                rep.skip("enclosure", f"{tag}: bound timed out after {timeout}s")
                continue
            if r.rc != 0:
                rep.fail("enclosure", f"{tag}: bound exit {r.rc}: {r.err.strip()}")
                continue
            b = parse_bound(r.out)
            off = 0 if with_time else 1  # the engine's components against mine
            # enclosure, as the engine measures it
            rep.count("enclosure")
            if "worst_low" not in b or "worst_high" not in b:
                rep.skip("enclosure", f"{tag}: {b.get('enclosure_unavailable', 'no worst lines')}")
            elif b["worst_low"] > 1e-9 or b["worst_high"] > 1e-9:
                rep.fail("enclosure", f"{tag}: worst L-min {b['worst_low']:e} worst max-U {b['worst_high']:e}")
            # the bounds at the start, six decimals
            rep.count("bounds")
            eu = b.get("U", [])
            el = b.get("L", [])
            if len(eu) != k - off or len(el) != k - off:
                rep.fail("bounds", f"{tag}: the engine prints {len(eu)} components, {k - off} expected")
            else:
                for c in range(k - off):
                    if not close(eu[c], float(my_u[c + off])) or not close(el[c], float(my_l[c + off])):
                        rep.fail("bounds", f"{tag}: engine U {fmtf(eu)} L {fmtf(el)}, mine U {fmtf(my_u[off:])} L {fmtf(my_l[off:])}")
                        break
            # the optima, six decimals
            if mine is None:
                continue
            rep.count("optima")
            if "exact_min" not in b:
                rep.skip("optima", f"{tag}: {b.get('exact_unavailable', 'no exact lines')}")
            else:
                lo, hi, _ = mine
                emin = b["exact_min"]
                emax = b["exact_max"]
                bad = False
                for c in range(k - off):
                    if not close(emin[c], float(lo[c + off])) or not close(emax[c], float(hi[c + off])):
                        bad = True
                if bad:
                    rep.fail("optima", f"{tag}: engine min {fmtf(emin)} max {fmtf(emax)}, mine min {fmtf(lo[off:])} max {fmtf(hi[off:])}")
                # the bounds enclose the exact optimum at the start, exactly
                for c in range(k):
                    if my_l[c] > lo[c] or hi[c] > my_u[c]:
                        rep.fail("bounds", f"{tag}: at s0 the recursion gives U {fmt(my_u)} L {fmt(my_l)} around min {fmt(lo)} max {fmt(hi)}")
                        break

        # the frontier, the time in
        out = work / f"{name}-K{K}.json"
        r = run(sdcpi, ["pareto", str(yaml), "--onephase", "1", "--max-iter", str(K), "--time", "1", "--json", str(out)], timeout)
        rep.runs += 1
        tag = f"K={K}"
        rep.count("status")
        if r.timed_out:
            rep.skip("status", f"{tag}: pareto timed out after {timeout}s")
            rep.no_frontier.add(K)
            continue
        if r.rc != 0:
            rep.fail("status", f"{tag}: pareto exit {r.rc}: {r.err.strip()}")
            rep.no_frontier.add(K)
            continue
        try:
            j = json.loads(out.read_text())
        except Exception as e:
            rep.fail("status", f"{tag}: the JSON does not read: {e}")
            continue
        if j.get("status") != "complete":
            rep.fail("status", f"{tag}: status {j.get('status')!r} {j.get('reason', '')}")
            continue
        ju = rationals(j["upper"])
        jl = rationals(j["lower"])
        rep.count("bounds")
        if ju != list(my_u) or jl != list(my_l):
            rep.fail("bounds", f"{tag}: frontier U {fmt(ju)} L {fmt(jl)}, mine U {fmt(my_u)} L {fmt(my_l)}")
        points = j.get("points", [])
        values = [rationals(p["value"]) for p in points]
        rep.count("status")
        if not points:
            rep.fail("status", f"{tag}: the frontier is empty")
            continue
        for val in values:
            if any(val[c] < jl[c] or val[c] > ju[c] for c in range(k)):
                rep.fail("status", f"{tag}: the point {fmt(val)} leaves the box U {fmt(ju)} L {fmt(jl)}")
                break
        if mine is not None:
            lo, hi, gap = mine
            least = [min(val[c] for val in values) for c in range(k)]
            if least != list(lo):
                rep.fail("status", f"{tag}: the least values over the frontier are {fmt(least)}, the exact minima {fmt(lo)}")
        if choice_free:
            if len(points) != 1:
                rep.fail("status", f"{tag}: {len(points)} points on an instance without choices")
            elif mine is not None:
                lo, hi, gap = mine
                if values[0] != list(lo):
                    rep.fail("status", f"{tag}: the one point is {fmt(values[0])}, the exact value {fmt(lo)}")
                cont = points[0].get("continued")
                engine_gap = gap
                if cont is None:
                    got = [Fraction(0)] * (2 * k)
                else:
                    got = rationals(cont["low"]) + rationals(cont["up"])
                if got != engine_gap:
                    rep.fail("status", f"{tag}: continued {fmt(got)}, the expected gap of the forced exits {fmt(engine_gap)}")

    if choice_free:
        gap_test(name, text, cfg, rep, plain, timed, yaml)


def gap_test(name, text, cfg, rep: Report, plain: Tree, timed: Tree, yaml: Path):
    sdcpi = cfg["sdcpi"]
    work = Path(cfg["work"])
    timeout = cfg["timeout"]
    cap = cfg["cap"]
    k = timed.k
    # the unbounded value: the recursion where it is a value, the chain else
    u_inf, l_inf = bounds_at_start(timed, None)
    exact = [u_inf[c] == l_inf[c] for c in range(k)]
    v_inf = [u_inf[c] if exact[c] else None for c in range(k)]
    chain = None
    if not all(exact):
        try:
            chain, states, converged = unbounded_chain(timed, cap)
            rep.notes.append(f"unbounded chain: {states} states, {'converged' if converged else 'NOT converged'}")
            if not converged:
                rep.skip("gap-enclosure", "the chain of the unbounded process did not converge")
                chain = None
        except TooManyStates as e:
            rep.skip("gap-enclosure", f"the chain of the unbounded process passed {cap} states ({e})")
        except ValueError as e:
            rep.fail("harness", str(e))
    if chain is not None:
        # the chain agrees with the recursion where the recursion is exact
        rep.count("chain")
        for c in range(k):
            if exact[c] and not close(chain[c], float(u_inf[c]), 1e-8):
                rep.fail("chain", f"component {c}: the chain gives {chain[c]:.12g}, the recursion {float(u_inf[c]):.12g}")
        for c in range(k):
            if not exact[c]:
                v_inf[c] = chain[c]
    # the engine's bounds of the unbounded process
    r = run(sdcpi, ["bound", str(yaml), "--max-iter", "inf", "--no-exact", "1", "--time", "1"], timeout)
    rep.runs += 1
    rep.count("inf-bounds")
    b = parse_bound(r.out)
    if "U" not in b or "L" not in b:
        rep.fail("inf-bounds", f"bound --max-iter inf printed no bounds: exit {r.rc} {r.err.strip()}")
    else:
        if r.rc != 0:
            rep.notes.append(f"bound --max-iter inf exits {r.rc} after printing the bounds: {r.err.strip()}")
        for c in range(k):
            if not close(b["U"][c], float(u_inf[c])) or not close(b["L"][c], float(l_inf[c])):
                rep.fail("inf-bounds", f"engine U {fmtf(b['U'])} L {fmtf(b['L'])}, mine U {fmtf(u_inf)} L {fmtf(l_inf)}")
                break

    all_under = all(timed.under_parallel(v) for v in timed.loops)
    series = []
    for K in cfg["gap_ks"]:
        out = work / f"{name}-K{K}.json"
        if K in rep.no_frontier:
            rep.skip("gap-enclosure", f"K={K}: no frontier")
            continue
        if not out.exists():
            r = run(sdcpi, ["pareto", str(yaml), "--onephase", "1", "--max-iter", str(K), "--time", "1", "--json", str(out)], timeout)
            rep.runs += 1
            if r.timed_out or r.rc != 0:
                rep.skip("gap-enclosure", f"K={K}: pareto {'timed out after ' + str(timeout) + 's' if r.timed_out else 'exit ' + str(r.rc) + ': ' + r.err.strip()}")
                continue
        j = json.loads(out.read_text())
        if j.get("status") != "complete" or len(j.get("points", [])) != 1:
            rep.skip("gap-enclosure", f"K={K}: status {j.get('status')!r}, {len(j.get('points', []))} points")
            continue
        p = j["points"][0]
        val = rationals(p["value"])
        cont = p.get("continued")
        low = rationals(cont["low"]) if cont else [Fraction(0)] * k
        up = rationals(cont["up"]) if cont else [Fraction(0)] * k
        rep.count("gap-enclosure")
        for c in range(k):
            lo_end = val[c] + low[c]
            up_end = val[c] + up[c]
            if v_inf[c] is None:
                continue
            if exact[c]:
                ok = lo_end <= v_inf[c] <= up_end
            else:
                ok = float(lo_end) - 1e-8 <= v_inf[c] <= float(up_end) + 1e-8
            if not ok:
                rep.fail(
                    "gap-enclosure",
                    f"K={K} component {c} ({timed.names[c]}): value {val[c]} + [{low[c]}, {up[c]}] = "
                    f"[{float(lo_end):.9g}, {float(up_end):.9g}] misses the unbounded value {float(v_inf[c]):.9g}",
                )
        # the time of the lower end is zero exactly when every loop is under a parallel node
        rep.count("gap-time-zero")
        if (low[0] == 0) != all_under:
            rep.fail("gap-time-zero", f"K={K}: continued.low time {low[0]}, loops under a parallel node: {[timed.under_parallel(v) for v in timed.loops]}")
        series.append((K, val))
    # the value grows with K and approaches the unbounded value
    if len(series) >= 2:
        rep.count("gap-convergence")
        for (K1, v1), (K2, v2) in zip(series, series[1:]):
            for c in range(k):
                if v1[c] > v2[c]:
                    rep.fail("gap-convergence", f"component {c}: value(K={K1}) {v1[c]} > value(K={K2}) {v2[c]}")
        for c in range(k):
            if v_inf[c] is None:
                continue
            if exact[c]:
                dist = [v_inf[c] - v for _, v in [(K, val[c]) for K, val in series]]
                if any(d < 0 for d in dist):
                    rep.fail("gap-convergence", f"component {c}: a truncated value exceeds the unbounded one {v_inf[c]}: {fmt(dist)}")
                if dist[0] > 0 and not dist[-1] < dist[0]:
                    rep.fail("gap-convergence", f"component {c}: the distance to the unbounded value does not shrink: {fmt(dist)}")
            else:
                dist = [v_inf[c] - float(val[c]) for _, val in series]
                if any(d < -1e-8 for d in dist):
                    rep.fail("gap-convergence", f"component {c}: a truncated value exceeds the unbounded one {v_inf[c]:.9g}: {fmtf(dist)}")
                if dist[0] > 1e-8 and not dist[-1] < dist[0]:
                    rep.fail("gap-convergence", f"component {c}: the distance to the unbounded value does not shrink: {fmtf(dist)}")
        rep.notes.append("values by K: " + "; ".join(f"K={K} {fmtf(val)}" for K, val in series) + f"; unbounded {fmtf([v if v is not None else float('nan') for v in v_inf])}")


# ---------------------------------------------------------------------------
# the hand-written instances
# ---------------------------------------------------------------------------

HAND = [
    ("hand-worked", "(0.5 ( (A, 1, {euro: 10}) ^ (B, 2, {euro: 4}) ))"),
    ("hand-under-parallel", "( (A, 3, {x: 2}) || (0.5 (B, 1, {x: 1})) )"),
    ("hand-in-sequence", "( (A, 3, {x: 2}) , (0.5 (B, 1, {x: 1})) )"),
    ("hand-nested", "(0.5 ( (A, 1, {x: 1}) , (0.5 (B, 1, {x: 1})) ))"),
    ("hand-parallel-inside", "(0.5 ( (A, 1, {x: 1}) || (B, 2, {x: 1}) ))"),
    ("hand-loop-of-loop", "(0.5 (0.2 (A, 1, {x: 1})))"),
    ("hand-two-loops-parallel", "( (0.8 (A, 2, {x: 1, y: 2})) || (0.2 (B, 3, {y: 1})) )"),
    ("hand-nature-in-loop", "(0.8 ( (A, 1, {x: 3}) ^[0.25] (B, 2, {x: 1, y: 4}) ))"),
    ("hand-choice-under-parallel", "( (C, 2, {x: 1}) || (0.5 ( (A, 1, {x: 2}) ^ (B, 1, {y: 2}) )) )"),
    ("hand-loop-then-loop", "( (0.2 (A, 1, {x: 1})) , (0.8 ( (B, 1, {y: 1}) || (C, 2, {x: 1}) )) )"),
]


def worked_example(cfg: dict) -> dict:
    """The worked example of the task, with its stated numbers at K=3."""
    name, text = HAND[0]
    rep = Report(name + "-numbers", text)
    started = time.time()
    try:
        sdcpi = cfg["sdcpi"]
        work = Path(cfg["work"])
        yaml = work / f"{name}.yaml"
        run(sdcpi, ["parse", text, "--out", str(yaml)], cfg["timeout"])
        r = run(sdcpi, ["bound", str(yaml), "--max-iter", "3", "--time", "1"], cfg["timeout"])
        rep.runs += 1
        b = parse_bound(r.out)
        rep.count("worked")
        if b.get("U") != [3.5, 17.5] or b.get("L") != [1.75, 7.0]:
            rep.fail("worked", f"U {b.get('U')} L {b.get('L')}, expected U [3.5, 17.5] L [1.75, 7]")
        out = work / f"{name}-K3-worked.json"
        r = run(sdcpi, ["pareto", str(yaml), "--onephase", "1", "--max-iter", "3", "--time", "1", "--json", str(out)], cfg["timeout"])
        rep.runs += 1
        j = json.loads(out.read_text())
        pts = j.get("points", [])
        if len(pts) != 8:
            rep.fail("worked", f"{len(pts)} points, 8 expected")
        for p in pts:
            c = p.get("continued", {})
            if rationals(c.get("low", [])) != [Fraction(1, 4), Fraction(1)] or rationals(c.get("up", [])) != [Fraction(1, 2), Fraction(5, 2)]:
                rep.fail("worked", f"continued {c}, expected low [1/4, 1] up [1/2, 5/2]")
                break
    except Exception as e:
        rep.fail("harness", f"{type(e).__name__}: {e}")
    rep.seconds = time.time() - started
    return rep.as_dict()


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def span(spec: str) -> list:
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


def read_cpi(path: Path) -> str:
    """The process of a .cpi file, its comment lines dropped."""
    lines = []
    for line in path.read_text().splitlines():
        body = line.split("#", 1)[0].strip()
        if body:
            lines.append(body)
    return " ".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="folders of .cpi files, or .cpi files")
    ap.add_argument("--text", action="append", default=[], help="a process in the grammar, checked as an instance")
    ap.add_argument("--hand", action="store_true", help="the hand-written instances (always run when no path is given)")
    ap.add_argument("--no-hand", action="store_true", help="skip the hand-written instances")
    ap.add_argument("--sdcpi", default=os.environ.get("SDCPI_BIN") or str(REPO / "target" / "release" / "sdcpi"))
    ap.add_argument("--work", default=None, help="where the instance files, the JSON and the report go")
    ap.add_argument("--k", default="1-4", help="the bounds on the passes checked (default 1-4)")
    ap.add_argument("--gap-k", default="1-5", help="the bounds on the passes of the gap test (default 1-5)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per call of the binary")
    ap.add_argument("--cap", type=int, default=400_000, help="states an exact pass may build")
    ap.add_argument("--out", default=None, help="the report as JSON (default work/report.json)")
    ap.add_argument("--limit", type=int, default=None, help="the first N instances alone")
    ap.add_argument("--verbose", action="store_true", help="print every instance as it completes")
    args = ap.parse_args()

    sdcpi = args.sdcpi
    if not Path(sdcpi).exists():
        print(f"check.py: no binary at {sdcpi}; build it or set SDCPI_BIN", file=sys.stderr)
        return 2
    specs = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            for f in sorted(path.glob("*.cpi")):
                specs.append((f.stem, read_cpi(f)))
        elif path.exists():
            specs.append((path.stem, read_cpi(path)))
        else:
            print(f"check.py: {p} does not exist", file=sys.stderr)
            return 2
    for i, text in enumerate(args.text):
        specs.append((f"text-{i}", text))
    if args.limit is not None:
        specs = specs[: args.limit]
    hand = args.hand or (not args.paths and not args.text)
    if hand and not args.no_hand:
        specs = list(HAND) + specs
    if not specs:
        print("check.py: nothing to check", file=sys.stderr)
        return 2
    work = Path(args.work) if args.work else (Path(args.paths[0]) if args.paths and Path(args.paths[0]).is_dir() else Path(".")) / "work"
    work.mkdir(parents=True, exist_ok=True)
    cfg = {
        "sdcpi": sdcpi,
        "work": str(work),
        "ks": span(args.k),
        "gap_ks": span(args.gap_k),
        "timeout": args.timeout,
        "cap": args.cap,
    }
    print(f"{len(specs)} instances, K in {cfg['ks']}, gap K in {cfg['gap_ks']}, {args.jobs} jobs, binary {sdcpi}")
    started = time.time()
    results = []
    jobs = [(name, text, cfg) for name, text in specs]
    if hand and not args.no_hand:
        results.append(worked_example(cfg))
    if args.jobs > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for res in pool.map(check_instance, jobs):
                results.append(res)
                if args.verbose:
                    print(f"  {res['name']}: {len(res['failures'])} failures, {res['runs']} runs, {res['seconds']}s")
    else:
        for job in jobs:
            res = check_instance(job)
            results.append(res)
            if args.verbose:
                print(f"  {res['name']}: {len(res['failures'])} failures, {res['runs']} runs, {res['seconds']}s")
    elapsed = time.time() - started

    # the summary
    counts = {}
    failures = {}
    skipped = {}
    for res in results:
        for check, n in res["counts"].items():
            counts[check] = counts.get(check, 0) + n
        for f in res["failures"]:
            failures[f["check"]] = failures.get(f["check"], 0) + 1
        for s in res["skipped"]:
            skipped[s["check"]] = skipped.get(s["check"], 0) + 1
    runs = sum(r["runs"] for r in results)
    failing = [r for r in results if r["failures"]]
    print(f"\n{len(results)} instances, {runs} calls of the binary, {elapsed:.1f}s")
    print("checks made:   " + ", ".join(f"{c} {n}" for c, n in sorted(counts.items())))
    print("checks failed: " + (", ".join(f"{c} {n}" for c, n in sorted(failures.items())) or "none"))
    if skipped:
        print("skipped:       " + ", ".join(f"{c} {n}" for c, n in sorted(skipped.items())))
    print(f"instances with a failure: {len(failing)} of {len(results)}")
    for res in failing:
        print(f"\n{res['name']}  {res['text']}")
        for f in res["failures"]:
            print(f"    {f['check']}: {f['detail']}")
    if any(r["skipped"] for r in results):
        print("\nskipped:")
        for res in results:
            for s in res["skipped"]:
                print(f"    {res['name']}: {s['check']}: {s['detail']}")
    out = Path(args.out) if args.out else work / "report.json"
    out.write_text(json.dumps({
        "binary": sdcpi,
        "instances": len(results),
        "runs": runs,
        "seconds": round(elapsed, 1),
        "counts": counts,
        "failures": failures,
        "skipped": skipped,
        "results": results,
    }, indent=1))
    print(f"\nreport: {out}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
