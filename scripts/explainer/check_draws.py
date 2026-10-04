#!/usr/bin/env python3
"""A deterministic policy is a function of the draws: the claim, checked.

    python3 scripts/explainer/check_draws.py [--count N] [--seed S] [--policies P]
                                             [--k 1,2,3] [--verbose]

The theory of the explainer (parallel-paper/semantics-time.tex of the paper
repository, section "A strategy as a function of the draws") rests on four
statements about the decision process of a BPMN+CPI process, the single-step
one of the paper with the clauses of the loops and the bound K on the
passes. This file implements that semantics exactly as the tex writes it,
five rules read by rank and witness, the two clauses of a loop, the counts,
and checks the statements on random processes and random deterministic
policies, every history of every policy enumerated:

  trail      two histories of the whole decision process, every action
             taken at every choice, neither a prefix of the other, part at
             a decision or at a draw, at the same node, with different
             entries; and two histories of one policy part at a draw;
  restart    a node that is running at a position and is started again at a
             later one has, in between, a draw at a loop above it that runs
             its region again; without loops no node starts twice;
  prefix     under a deterministic policy, two histories whose draws are
             comparable as sequences are comparable as histories;
  decides    under a deterministic policy, for every choice node, the
             histories at which the choice is due have pairwise distinct
             draws, so the decision is a function of the draws;
  assignment without loops every nature node is drawn at most once along
             every history, and the histories at which a choice is due have
             pairwise distinct partial assignments of the nature nodes;
  occurrences with loops or without, the histories at which a choice is due
             have pairwise distinct assignments indexed by the occurrences,
             the j-th draw at a node with what it gave: the reading the
             trees of the tool test;
  passes     with loops or without, a draw is indexed by the passes it is
             taken in: for every loop above its node, the outermost first,
             the pass of that loop that is running, and at a loop, last,
             the pass its region has just closed. No two draws of a history
             have the same node and the same passes, and the histories at
             which a choice is due have pairwise distinct assignments
             indexed so: the reading the trees of the tool test. The exit a
             loop is forced to at the bound is no draw and has no index;
  beyond     the decision functions of a policy of the process at K passes,
             read through the indices they know, decide the process at
             K + 1 passes: at every choice met outside the passes past K
             the assignment read is one the policy decided, the impact paid
             outside those passes is the impact of the policy at K, and
             the time is no less. A choice inside a pass past K is one the
             policy never meets, and what is read there is counted apart.
             The same is tried with the occurrences in place of the passes
             and counted apart, as an observation: there it fails, a pass
             past the bound moving the count of every later draw;
  reading    two sequences of draws that one policy meets at the same choice
             are told apart by the policy the proof builds, changed at one
             history, at which both are still met;
  recovered  the policy rebuilt from its decision functions alone, read on
             the draws, walks the same histories and has the same value, in
             the impact and in the time.

The processes hold loops inside loops, and the bound on the passes takes
the values of --k and, on a part of the processes with loops, no bound at
all, the histories being then cut at a length and the statements that speak
of whole runs left out. An instance whose histories pass --cap is skipped
and counted apart. No tool is called: the semantics here is independent of
the engine. The exit code is 0 exactly when nothing failed.
"""

import argparse
import random
import sys
from fractions import Fraction as F

TASK, SEQ, PAR, CHOICE, NATURE, LOOP = "task", "seq", "par", "choice", "nature", "loop"
IDLE, DONE = -1, -2
OUT = "out"
# the time is carried beside the impact in one rational, on a scale no sum of
# impacts reaches, so that one number compares both
TIME = F(10) ** 9


class Tree:
    """The tree of a process, the identifiers in-order: the low subtree
    below a node and the high one above it, the region of a loop below it."""

    def __init__(self):
        self.kind, self.low, self.high, self.dur, self.prob, self.impact = [], [], [], [], [], []
        self.root = None

    def add(self, kind, low=None, high=None, dur=None, prob=None, impact=None):
        self.kind.append(kind); self.low.append(low); self.high.append(high)
        self.dur.append(dur); self.prob.append(prob); self.impact.append(impact)
        return len(self.kind) - 1

    def finish(self, root):
        """Renumber in-order and compute parents and subtrees."""
        order = []

        def walk(v):
            if self.kind[v] == TASK:
                order.append(v)
            elif self.kind[v] == LOOP:
                walk(self.low[v]); order.append(v)
            else:
                walk(self.low[v]); order.append(v); walk(self.high[v])
        walk(root)
        new = {old: i for i, old in enumerate(order)}
        t = Tree()
        for old in order:
            t.kind.append(self.kind[old]); t.dur.append(self.dur[old]); t.prob.append(self.prob[old])
            t.impact.append(self.impact[old])
            t.low.append(None if self.low[old] is None else new[self.low[old]])
            t.high.append(None if self.high[old] is None else new[self.high[old]])
        t.root = new[root]
        t.n = len(order)
        t.parent = [None] * t.n
        for v in range(t.n):
            for c in (t.low[v], t.high[v]):
                if c is not None:
                    t.parent[c] = v
        t.sub = [None] * t.n

        def subtree(v):
            s = {v}
            for c in (t.low[v], t.high[v]):
                if c is not None:
                    s |= subtree(c)
            t.sub[v] = frozenset(s)
            return s
        subtree(t.root)
        t.loops = [v for v in range(t.n) if t.kind[v] == LOOP]
        t.natures = [v for v in range(t.n) if t.kind[v] == NATURE]
        t.choices = [v for v in range(t.n) if t.kind[v] == CHOICE]
        t.tasks = [v for v in range(t.n) if t.kind[v] == TASK]
        return t

    def children(self, v):
        return [c for c in (self.low[v], self.high[v]) if c is not None]

    def text(self, v=None):
        v = self.root if v is None else v
        k = self.kind[v]
        if k == TASK:
            return f"(T{v},{self.dur[v]})"
        if k == LOOP:
            return f"({self.prob[v]} {self.text(self.low[v])})"
        op = {SEQ: ",", PAR: "||", CHOICE: "^", NATURE: f"^[{self.prob[v]}]"}[k]
        return f"({self.text(self.low[v])} {op} {self.text(self.high[v])})"


def random_tree(rng, tasks, loops_allowed):
    b = Tree()

    def gen(n):
        if n == 1:
            node = b.add(TASK, dur=rng.randint(1, 3), impact=F(rng.randint(0, 5)))
        else:
            left = rng.randint(1, n - 1)
            kind = rng.choice([SEQ, SEQ, PAR, CHOICE, CHOICE, NATURE, NATURE])
            lo, hi = gen(left), gen(n - left)
            prob = F(rng.randint(1, 9), 10) if kind == NATURE else None
            node = b.add(kind, lo, hi, prob=prob)
        while loops_allowed and rng.random() < 0.22:
            node = b.add(LOOP, node, prob=F(rng.randint(2, 7), 10))
        return node
    return b.finish(gen(tasks))


# ---------------------------------------------------------------------------
# the semantics of the tex: states, triggers, ranks, witnesses, expansions
# ---------------------------------------------------------------------------

def initial(t):
    ph = [IDLE] * t.n
    ph[t.root] = 0
    return (tuple(ph), tuple([0] * t.n))


def trigger(t, s, v, rule):
    ph = s[0]
    k = t.kind[v]
    kids = t.children(v)
    if rule == 1:
        return k == CHOICE and ph[v] >= 0 and all(ph[c] == IDLE for c in kids)
    if rule == 2:
        if k in (SEQ, PAR):
            return ph[v] >= 0 and all(ph[c] == IDLE for c in kids)
        return k == LOOP and ph[v] >= 0 and ph[t.low[v]] == IDLE
    if rule == 3:
        if k in (SEQ, CHOICE, NATURE):
            return any(ph[c] == DONE for c in kids)
        return k == PAR and all(ph[c] == DONE for c in kids)
    if rule == 4:
        if k == NATURE:
            return ph[v] >= 0 and all(ph[c] == IDLE for c in kids)
        return k == LOOP and ph[t.low[v]] == DONE
    if rule == 5:
        return k == TASK and 0 <= ph[v] < t.dur[v]
    raise ValueError(rule)


def rank_witness(t, s):
    for rule in (1, 2, 3, 4, 5):
        for v in range(t.n):          # the identifiers in increasing order
            if trigger(t, s, v, rule):
                return rule, v
    return None, None


def moves(t, s, K):
    """The step at s: (rank, witness, [(action, probability, successor, cost)]).
    A final state has the absorption, returned as rank None."""
    ph, cnt = s
    if ph[t.root] == DONE:
        return None, None, []
    r, v = rank_witness(t, s)
    assert r is not None, "no trigger at a state that is not final"
    ph_l, cnt_l = list(ph), list(cnt)

    def state(pp, cc):
        return (tuple(pp), tuple(cc))

    if r == 1:
        out = []
        for c in t.children(v):
            p2 = list(ph); p2[c] = 0
            out.append((c, F(1), state(p2, cnt), F(0)))
        return r, v, out
    if r == 2:
        p2 = list(ph)
        if t.kind[v] == SEQ:
            p2[t.low[v]] = 0
        elif t.kind[v] == PAR:
            p2[t.low[v]] = 0; p2[t.high[v]] = 0
        else:
            p2[t.low[v]] = 0
        return r, v, [("tau", F(1), state(p2, cnt), F(0))]
    if r == 3:
        p2, c2 = list(ph), list(cnt)
        lo, hi = t.low[v], t.high[v]
        if t.kind[v] == SEQ and ph[lo] == DONE and ph[hi] == IDLE:
            p2[lo] = IDLE; p2[hi] = 0
        else:
            p2[lo] = IDLE; p2[hi] = IDLE; p2[v] = DONE; c2[v] += 1
        return r, v, [("tau", F(1), state(p2, c2), F(0))]
    if r == 4:
        if t.kind[v] == NATURE:
            out = []
            for c, pr in ((t.low[v], t.prob[v]), (t.high[v], 1 - t.prob[v])):
                p2 = list(ph); p2[c] = 0
                out.append(("tau", pr, state(p2, cnt), F(0)))
            return r, v, out
        R = t.low[v]
        m = cnt[R]
        p_exit, c_exit = list(ph), list(cnt)
        p_exit[R] = IDLE; p_exit[v] = DONE
        c_exit[v] += 1
        for u in t.sub[v]:
            if u != v:
                c_exit[u] = 0
        if K is None or m < K:
            p_again = list(ph); p_again[R] = 0
            return r, v, [("tau", t.prob[v], state(p_again, cnt), F(0)),
                          ("tau", 1 - t.prob[v], state(p_exit, c_exit), F(0))]
        return r, v, [("tau", F(1), state(p_exit, c_exit), F(0))]
    # the fast forward: the impact of the closers, and the advance as the time
    running = [u for u in t.tasks if ph[u] >= 0]
    step = min(t.dur[u] - ph[u] for u in running)
    closers = [u for u in running if t.dur[u] - ph[u] == step]
    p2, c2 = list(ph), list(cnt)
    cost = F(0)
    for u in range(t.n):
        if u in closers:
            p2[u] = DONE; c2[u] += 1; cost += t.impact[u]
        elif ph[u] >= 0:
            p2[u] = ph[u] + step
    return r, None, [("tau", F(1), state(p2, c2), cost + TIME * step)]


def entry(t, s, v, nxt):
    """The entry of a decision or a draw: the node it fires at and what it
    starts, the node written 0, or OUT at the exit of a loop."""
    for u in range(t.n):
        if nxt[0][u] == 0 and s[0][u] != 0:
            return (v, u)
    return (v, OUT)


class Failure(Exception):
    pass


def enumerate_policy(t, K, rng, cap, policy=None, max_len=None):
    """Every history of one random deterministic policy, from the initial
    state. The policy is drawn lazily, an action per history at which a
    choice is due, on top of the table given. With max_len the histories are
    cut at that length, for the process with no bound on the passes.
    Returns the list of the histories as records, and the table."""
    records = []
    policy = dict(policy or {})

    def walk(states, trail, kinds, pos, cost, prob):
        if len(records) > cap:
            raise OverflowError
        s = states[-1]
        r, v, mv = moves(t, s, K)
        # pos: for every entry of the trail, the position of the state it was taken at
        rec = {"states": tuple(states), "trail": tuple(trail), "kinds": tuple(kinds), "pos": tuple(pos),
               "rank": r, "witness": v, "prob": prob, "cost": cost, "cut": False}
        records.append(rec)
        if r is None:
            return
        if max_len is not None and len(states) > max_len:
            rec["cut"] = True
            return
        if r == 1:
            key = tuple(states)
            if key not in policy:
                policy[key] = rng.choice([m[0] for m in mv])
            a = policy[key]
            (_, pr, nxt, c) = [m for m in mv if m[0] == a][0]
            walk(states + [nxt], trail + [entry(t, s, v, nxt)], kinds + ["dec"], pos + [len(states) - 1], cost + c, prob * pr)
        elif r == 4 and len(mv) == 2:
            for (_, pr, nxt, c) in mv:
                walk(states + [nxt], trail + [entry(t, s, v, nxt)], kinds + ["drw"], pos + [len(states) - 1], cost + c, prob * pr)
        else:
            (_, pr, nxt, c) = mv[0]
            walk(states + [nxt], trail, kinds, pos, cost + c, prob * pr)

    walk([initial(t)], [], [], [], F(0), F(1))
    return records, policy


def enumerate_all(t, K, cap, max_len=None):
    """Every maximal history of the decision process itself, every action
    taken at every choice and every draw followed: the states of each."""
    out = []

    def walk(states):
        if len(out) > cap:
            raise OverflowError
        r, v, mv = moves(t, states[-1], K)
        if r is None or (max_len is not None and len(states) > max_len):
            out.append(tuple(states)); return
        for (_, _, nxt, _) in mv:
            walk(states + [nxt])
    walk([initial(t)])
    return out


def loops_above(t, v):
    """The loops strictly above a node, the outermost first."""
    out = []
    u = t.parent[v]
    while u is not None:
        if t.kind[u] == LOOP:
            out.append(u)
        u = t.parent[u]
    return out[::-1]


def pass_index(t, s, v):
    """The passes a draw at v is taken in, at the state s: for every loop
    above v, the outermost first, the pass of its region that is running,
    counted from 1, the count of the region being the passes it has closed;
    and at a loop, last, the pass its region has just closed."""
    cnt = s[1]
    idx = [cnt[t.low[l]] + 1 for l in loops_above(t, v)]
    if t.kind[v] == LOOP:
        idx.append(cnt[t.low[v]])
    return tuple(idx)


def by_passes(t, rec):
    """The draws of a history, each with its node and its passes."""
    return [((e[0], pass_index(t, rec["states"][i], e[0])), e[1])
            for e, k, i in zip(rec["trail"], rec["kinds"], rec["pos"]) if k == "drw"]


def by_occurrences(rec):
    """The draws of a history, each with its node and its occurrence."""
    occ, out = {}, []
    for (u, x) in draws(rec):
        occ[u] = occ.get(u, 0) + 1
        out.append(((u, occ[u]), x))
    return out


def is_prefix(a, b):
    return len(a) <= len(b) and b[:len(a)] == a


def draws(rec):
    return tuple(e for e, k in zip(rec["trail"], rec["kinds"]) if k == "drw")


def check_instance(t, K, rng, counts, fails, cap, label, max_len=None):
    try:
        records, policy = enumerate_policy(t, K, rng, cap, max_len=max_len)
    except (OverflowError, RecursionError):
        counts["skipped"] = counts.get("skipped", 0) + 1
        return
    whole = max_len is None        # the runs are whole: the statements on them apply

    def fail(name, detail):
        fails.append((name, label, detail))

    def count(name, n=1):
        counts[name] = counts.get(name, 0) + n

    # the measure of the maximal histories is one
    if whole:
        total = sum(r["prob"] for r in records if r["rank"] is None)
        count("measure")
        if total != 1:
            fail("measure", f"the final histories have probability {total}")

    # trail, on the whole process: two histories part at a decision or at a
    # draw, at the same node, with different entries
    try:
        everything = enumerate_all(t, K, 400, max_len=max_len)
    except (OverflowError, RecursionError):
        everything = []
    pairs = [(a, b) for a in range(len(everything)) for b in range(a + 1, len(everything))]
    if len(pairs) > 1500:
        pairs = rng.sample(pairs, 1500)
    for a, b in pairs:
        ha, hb = everything[a], everything[b]
        g = 0
        while g < min(len(ha), len(hb)) and ha[g] == hb[g]:
            g += 1
        if g == min(len(ha), len(hb)):
            continue                      # one a prefix of the other, a cut history
        s = ha[g - 1]
        r, v, mv = moves(t, s, K)
        count("trail")
        if not (r == 1 or (r == 4 and len(mv) == 2)):
            fail("trail", f"two histories of the process part at rank {r} with {len(mv)} successors")
            continue
        ea, eb = entry(t, s, v, ha[g]), entry(t, s, v, hb[g])
        if ea[0] != eb[0] or ea[1] == eb[1]:
            fail("trail", f"entries {ea} and {eb} at the parting")

    # trail, on one policy: its histories part at a draw
    finals = [r for r in records if r["rank"] is None or r["cut"]]
    for a in range(len(finals)):
        for b in range(a + 1, len(finals)):
            ha, hb = finals[a]["states"], finals[b]["states"]
            g = 0
            while g < min(len(ha), len(hb)) and ha[g] == hb[g]:
                g += 1
            # g is the first position where the states differ: the step at g - 1 parts them
            if g >= min(len(ha), len(hb)):
                continue
            s = ha[g - 1]
            r, v, mv = moves(t, s, K)
            count("trail")
            if not (r == 4 and len(mv) == 2):
                fail("trail", f"two histories of one policy part at rank {r}, not at a draw")
                continue
            ea, eb = entry(t, s, v, ha[g]), entry(t, s, v, hb[g])
            if ea[0] != eb[0] or ea[1] == eb[1]:
                fail("trail", f"entries {ea} and {eb} at the parting")

    # restart: a node running at i and started at l > i has a loop draw in between
    for rec in finals:
        st = rec["states"]
        trail_pos = []          # (position, entry, kind) of the decisions and draws
        j = 0
        for i in range(len(st) - 1):
            r, v, mv = moves(t, st[i], K)
            if r == 1 or (r == 4 and len(mv) == 2):
                trail_pos.append((i, rec["trail"][j], rec["kinds"][j])); j += 1
        last_running = {}
        for l in range(len(st) - 1):
            for w in range(t.n):
                if st[l][0][w] >= 0:
                    last_running[w] = l
            for w in range(t.n):
                if st[l + 1][0][w] == 0 and st[l][0][w] != 0:
                    # w is started by the step at l: was it running before?
                    prev = [i for i in range(l) if st[i][0][w] >= 0]
                    if not prev:
                        continue
                    i = max(prev)
                    count("restart")
                    ok = any(i < q <= l and kind == "drw" and t.kind[e[0]] == LOOP and e[1] == t.low[e[0]]
                             and w in t.sub[t.low[e[0]]] for (q, e, kind) in trail_pos)
                    if not ok:
                        fail("restart", f"node {w} running at {i} starts at {l} with no loop draw between")
                    if not t.loops:
                        fail("restart", f"node {w} starts twice in a process without loops")

    # prefix: histories with comparable draws are comparable
    by_draws = {}
    for rec in records:
        by_draws.setdefault(draws(rec), []).append(rec)
    keys = list(by_draws)
    for d, group in by_draws.items():
        group.sort(key=lambda r: len(r["states"]))
        for a, b in zip(group, group[1:]):
            count("prefix")
            if not is_prefix(a["states"], b["states"]):
                fail("prefix", f"two histories with the draws {d} are not comparable")
    for x in keys:
        for y in keys:
            if x != y and is_prefix(x, y):
                a = by_draws[x][-1]            # the longest history with the shorter draws
                b = by_draws[y][0]             # the shortest with the longer ones
                count("prefix")
                if not is_prefix(a["states"], b["states"]):
                    fail("prefix", f"draws {x} a prefix of {y}, histories not comparable")

    # without loops every nature node is drawn at most once along every history
    if not t.loops:
        for rec in records:
            nodes = [e[0] for e in draws(rec)]
            count("assignment")
            if len(set(nodes)) != len(nodes):
                fail("assignment", f"a nature node drawn twice without loops: {draws(rec)}")

    # passes: no two draws of a history have the same node and the same
    # passes, no draw is taken at the bound, no pass is past it
    for rec in finals:
        idx = [k for k, _ in by_passes(t, rec)]
        count("passes")
        if len(set(idx)) != len(idx):
            fail("passes", f"two draws of one history with the same node and passes: {idx}")
        if K is not None and any(t.kind[u] == LOOP and q[-1] >= K for (u, q) in idx):
            fail("passes", f"a draw of a loop at the bound {K}: {idx}")
        if K is not None and any(x > K for (u, q) in idx for x in q):
            fail("passes", f"a pass past the bound {K}: {idx}")

    # reading: two sequences of draws one policy meets at a choice are told
    # apart by the policy of the proof, changed at one history
    for v in t.choices:
        due = [r for r in records if r["rank"] == 1 and r["witness"] == v and r["states"] in policy]
        if len(due) < 2:
            continue
        ra, rb = rng.sample(due, 2)
        if is_prefix(rb["states"], ra["states"]):
            ra, rb = rb, ra                        # rb is not a proper prefix of ra
        table = dict(policy)
        if policy[ra["states"]] == policy[rb["states"]]:
            table[rb["states"]] = [c for c in t.children(v) if c != policy[rb["states"]]][0]
        try:
            rec2, pol2 = enumerate_policy(t, K, rng, cap, policy=table, max_len=max_len)
        except (OverflowError, RecursionError):
            continue
        count("reading")
        met = {r["states"]: r for r in rec2 if r["rank"] == 1 and r["witness"] == v}
        if ra["states"] not in met or rb["states"] not in met:
            fail("reading", f"the changed policy no longer meets choice {v} at both histories")
        elif pol2[ra["states"]] == pol2[rb["states"]]:
            fail("reading", f"the changed policy decides alike after {draws(ra)} and {draws(rb)}")
        elif draws(met[ra["states"]]) == draws(met[rb["states"]]):
            fail("reading", f"two histories at choice {v} with the same draws")

    # decides: at a choice node, the histories at which it is due have distinct draws
    for v in t.choices:
        due = [r for r in records if r["rank"] == 1 and r["witness"] == v]
        seen = {}
        for rec in due:
            d = draws(rec)
            count("decides")
            if d in seen:
                fail("decides", f"choice {v} due at two histories with the same draws {d}")
            seen[d] = rec
        # the assignment indexed by the occurrences: the j-th draw at a node
        # with what it gave, loops or not
        seen_o = {}
        for rec in due:
            occ, key = {}, []
            for (u, x) in draws(rec):
                occ[u] = occ.get(u, 0) + 1
                key.append(((u, occ[u]), x))
            key = frozenset(key)
            count("occurrences")
            if key in seen_o:
                fail("occurrences", f"choice {v} due at two histories with the same assignment by occurrences {sorted(key, key=str)}")
            seen_o[key] = rec
        # the assignment indexed by the passes
        seen_p = {}
        for rec in due:
            key = frozenset(by_passes(t, rec))
            count("passes")
            if key in seen_p:
                fail("passes", f"choice {v} due at two histories with the same assignment by passes {sorted(key, key=str)}")
            seen_p[key] = rec
        if not t.loops:
            # the partial assignment of the nature nodes
            seen_a = {}
            for rec in due:
                d = draws(rec)
                nodes = [e[0] for e in d]
                count("assignment")
                if len(set(nodes)) != len(nodes):
                    fail("assignment", f"a nature node drawn twice without loops: {d}")
                key = frozenset(d)
                if key in seen_a:
                    fail("assignment", f"choice {v} due at two histories with the same assignment {sorted(key)}")
                seen_a[key] = rec

    # recovered: the decision functions alone rebuild the policy
    if not whole:
        return
    f = {}
    for rec in records:
        if rec["rank"] == 1:
            f[(rec["witness"], draws(rec))] = policy[rec["states"]]

    def replay(states, drw, cost, prob, acc):
        s = states[-1]
        r, v, mv = moves(t, s, K)
        if r is None:
            acc.append((tuple(states), prob, cost)); return
        if r == 1:
            a = f.get((v, tuple(drw)))
            if a is None:
                raise Failure(f"no decision recorded for choice {v} after the draws {tuple(drw)}")
            (_, pr, nxt, c) = [m for m in mv if m[0] == a][0]
            replay(states + [nxt], drw, cost + c, prob * pr, acc)
        elif r == 4 and len(mv) == 2:
            for (_, pr, nxt, c) in mv:
                replay(states + [nxt], drw + [entry(t, s, v, nxt)], cost + c, prob * pr, acc)
        else:
            (_, pr, nxt, c) = mv[0]
            replay(states + [nxt], drw, cost + c, prob * pr, acc)

    count("recovered")
    try:
        acc = []
        replay([initial(t)], [], F(0), F(1), acc)
        mine = sorted((r["states"], r["prob"], r["cost"]) for r in records if r["rank"] is None)
        if sorted(acc) != mine:
            fail("recovered", "the policy rebuilt from its decision functions walks other histories")
        elif sum(p * c for _, p, c in acc) != sum(r["prob"] * r["cost"] for r in records if r["rank"] is None):
            fail("recovered", "the rebuilt policy has another value")
    except Failure as e:
        fail("recovered", str(e))

    # beyond: the decision functions at K passes, read through the indices
    # they know, decide the process at K + 1 passes
    if K is None or not t.loops:
        return
    time_K = sum(r["prob"] * (r["cost"] // TIME) for r in records if r["rank"] is None)
    impact_K = sum(r["prob"] * (r["cost"] % TIME) for r in records if r["rank"] is None)
    above = {u: loops_above(t, u) for u in range(t.n)}

    def beyond(nested):
        """The policy at K + 1 that reads, at a choice, the draws at the
        indices the choice knows at K, and takes what the policy took after
        them. Returns the choices met outside the passes past K, those among
        them whose reading the policy never decided, the choices inside a
        pass past K and those among them left undecided, the impact paid
        outside the passes past K, and the time."""
        G, known = {}, {}
        for rec in records:
            if rec["rank"] == 1:
                a = by_passes(t, rec) if nested else by_occurrences(rec)
                G[(rec["witness"], frozenset(a))] = policy[rec["states"]]
                known.setdefault(rec["witness"], set()).update(k for k, _ in a)
        out = {"met": 0, "missing": 0, "inside": 0, "undecided": 0, "kept": F(0), "time": F(0), "nodes": 0}

        def walk(s, drw, occ, prob, kept, time):
            out["nodes"] += 1
            if out["nodes"] > 40 * cap:
                raise OverflowError
            r, v, mv = moves(t, s, K + 1)
            if r is None:
                out["kept"] += prob * kept; out["time"] += prob * time
                return
            if r == 1:
                # a choice in a pass past K is one the policy at K never
                # meets: whatever is taken there is paid by the gap
                extra = any(s[1][t.low[l]] >= K for l in above[v])
                out["inside" if extra else "met"] += 1
                a = G.get((v, frozenset(e for e in drw if e[0] in known.get(v, ()))))
                if a is None:
                    out["undecided" if extra else "missing"] += 1
                    a = t.low[v]
                (_, pr, nxt, c) = [m for m in mv if m[0] == a][0]
                walk(nxt, drw, occ, prob * pr, kept, time)
            elif r == 4 and len(mv) == 2:
                o2 = dict(occ); o2[v] = o2.get(v, 0) + 1
                idx = pass_index(t, s, v) if nested else o2[v]
                for (_, pr, nxt, c) in mv:
                    walk(nxt, drw + [((v, idx), entry(t, s, v, nxt)[1])], o2, prob * pr, kept, time)
            elif r == 5:
                (_, pr, nxt, c) = mv[0]
                # the tasks this step closes, outside every pass past K
                closers = [u for u in t.tasks if s[0][u] >= 0 and nxt[0][u] == DONE]
                paid = sum(t.impact[u] for u in closers if all(s[1][t.low[l]] < K for l in above[u]))
                walk(nxt, drw, occ, prob * pr, kept + paid, time + c // TIME)
            else:
                (_, pr, nxt, c) = mv[0]
                walk(nxt, drw, occ, prob * pr, kept, time)

        walk(initial(t), [], {}, F(1), F(0), F(0))
        return out

    try:
        b = beyond(True)
        count("beyond")
        counts["observed: choices inside a pass past the bound"] = counts.get("observed: choices inside a pass past the bound", 0) + b["inside"]
        counts["observed: of which no decision is read"] = counts.get("observed: of which no decision is read", 0) + b["undecided"]
        if b["missing"]:
            fail("beyond", f"{b['missing']} of {b['met']} choices outside the passes past K read draws the policy never decided after")
        elif b["kept"] != impact_K:
            fail("beyond", f"the impact outside the passes past K is {b['kept']}, the policy at K pays {impact_K}")
        elif b["time"] < time_K:
            fail("beyond", f"the time at K + 1 is {b['time']}, less than {time_K} at K")
        # the same with the occurrences: an observation, counted apart
        nested_loops = any(above[l] for l in t.loops)
        o = beyond(False)
        counts["observed: occurrences beyond"] = counts.get("observed: occurrences beyond", 0) + 1
        if o["missing"] or o["kept"] != impact_K:
            name = "observed: occurrences misread beyond, loops inside loops" if nested_loops else "observed: occurrences misread beyond, no loop inside a loop"
            counts[name] = counts.get(name, 0) + 1
    except (OverflowError, RecursionError):
        counts["beyond skipped for size"] = counts.get("beyond skipped for size", 0) + 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--policies", type=int, default=4)
    ap.add_argument("--k", default="1,2,3")
    ap.add_argument("--cap", type=int, default=6000, help="histories of one policy past which an instance is skipped")
    ap.add_argument("--cut", type=int, default=40, help="length at which a history of the unbounded process is cut")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    sys.setrecursionlimit(20000)
    rng = random.Random(args.seed)
    ks = [int(x) for x in args.k.split(",")]
    counts, fails = {}, []
    with_loops = without = 0
    unbounded = [0]
    for n in range(args.count):
        loops_allowed = n % 2 == 0
        t = random_tree(rng, rng.randint(2, 6), loops_allowed)
        if t.loops:
            with_loops += 1
        else:
            without += 1
        for K in (ks if t.loops else [1]):
            for p in range(args.policies):
                check_instance(t, K, rng, counts, fails, args.cap, f"#{n} K={K} policy {p}: {t.text()}")
        if t.loops and n % 4 == 0:
            # no bound on the passes: the histories cut at a length
            for p in range(2):
                check_instance(t, None, rng, counts, fails, args.cap, f"#{n} K=inf policy {p}: {t.text()}", max_len=args.cut)
                unbounded[0] += 1
        if args.verbose:
            print(n, t.text())
    skipped = counts.pop("skipped", 0)
    nested = "loops inside loops among them"
    print(f"{args.count} processes, {with_loops} with loops ({nested}) and {without} without, {args.policies} policies each, K in {ks}, "
          f"and {unbounded[0]} runs with no bound, cut at length {args.cut}")
    print("checks made:  ", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"skipped for size: {skipped} policy runs")
    for name, label, detail in fails[:12]:
        print(f"  FAIL [{name}] {detail}\n       {label}")
    print(f"failures: {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
