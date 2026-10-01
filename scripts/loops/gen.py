#!/usr/bin/env python3
"""Random processes with loops, in the text grammar of `sdcpi parse`.

    gen.py --count 100 --seed 1 --out DIR
    gen.py --tasks 3-8 --loops 1-3 --impacts 1-3 --choices 0 --count 60 --seed 2 --out DIR

Every instance lands in one file, `DIR/<prefix>-<seed>-<index>.cpi`, holding
one process in the grammar at the head of `src/parse.rs`, so that
`sdcpi parse --file F --out F.yaml` turns it into an instance file. The
process is drawn from the seed and the index alone: the same seed and the
same parameters give the same files, whatever the count and whatever else
runs on the machine.

What is drawn. A full binary tree over the tasks, split at random; every
internal node is a sequence, a parallel composition, a nature node, or a
choice, the choices counted by `--choices` and placed at random among the
internal nodes; then `--loops` distinct subtrees, tasks and the root
included, are wrapped in a loop `(p R)`, the probability of running the
region again drawn from {0.2, 0.5, 0.8}, so that a loop wrapping a subtree
that holds another wrapped subtree nests them. A task carries a duration in
1..5 and one entry per impact, zero with probability one in four and 1..9
otherwise; the map of a task names its positive impacts alone, as the
grammar asks, and a task whose impacts are all zero is written with its
duration alone. Every impact name appears in at least one task, so the
instance file declares exactly `--impacts` dimensions. A nature node takes
its left branch with a probability of two decimals in (0, 1).

A parameter given as `a-b` is drawn uniformly in that range for every
instance; given as a single number it is fixed.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

IMPACT_NAMES = ["a", "b", "c", "d", "e", "f"]
LOOP_PROBS = ["0.2", "0.5", "0.8"]


# ---------------------------------------------------------------------------
# the tree: nested tuples
#   ("task", name, duration, {impact name: value})
#   (kind, prob, low, high)       kind in sequence, parallel, choice, nature
#   ("loop", prob, inner)
# ---------------------------------------------------------------------------


def random_shape(rng: random.Random, n_tasks: int):
    """A full binary tree with `n_tasks` leaves, the split drawn uniformly."""
    if n_tasks == 1:
        return ("leaf",)
    left = rng.randint(1, n_tasks - 1)
    return ("node", random_shape(rng, left), random_shape(rng, n_tasks - left))


def internal_count(shape) -> int:
    if shape[0] == "leaf":
        return 0
    return 1 + internal_count(shape[1]) + internal_count(shape[2])


def build(rng: random.Random, shape, kinds: list, tasks: list, names: list):
    """Fills the shape: the kinds of the internal nodes are popped from
    `kinds` in pre-order, the tasks numbered in order of appearance."""
    if shape[0] == "leaf":
        index = len(tasks) + 1
        impact = {}
        for name in names:
            if rng.random() < 0.25:
                continue
            impact[name] = rng.randint(1, 9)
        task = ("task", f"T{index}", rng.randint(1, 5), impact)
        tasks.append(task)
        return task
    kind = kinds.pop()
    prob = None
    if kind == "nature":
        prob = f"{rng.randint(1, 99) / 100:.2f}"
    low = build(rng, shape[1], kinds, tasks, names)
    high = build(rng, shape[2], kinds, tasks, names)
    return (kind, prob, low, high)


def subtrees(node, out: list, path: tuple):
    """Every subtree with its path from the root, pre-order."""
    out.append(path)
    if node[0] == "task":
        return
    if node[0] == "loop":
        subtrees(node[2], out, path + (2,))
        return
    subtrees(node[2], out, path + (2,))
    subtrees(node[3], out, path + (3,))


def wrap(node, path: tuple, prob: str):
    """The tree with the subtree at `path` wrapped in a loop."""
    if not path:
        return ("loop", prob, node)
    i = path[0]
    as_list = list(node)
    as_list[i] = wrap(node[i], path[1:], prob)
    return tuple(as_list)


def generate(rng: random.Random, n_tasks: int, n_loops: int, n_impacts: int, n_choices: int):
    names = IMPACT_NAMES[:n_impacts]
    shape = random_shape(rng, n_tasks)
    internals = internal_count(shape)
    n_choices = min(n_choices, internals)
    kinds = ["choice"] * n_choices
    for _ in range(internals - n_choices):
        kinds.append(rng.choices(["sequence", "parallel", "nature"], weights=[4, 3, 3])[0])
    rng.shuffle(kinds)
    tasks: list = []
    tree = build(rng, shape, kinds, tasks, names)
    # every impact name in at least one task: an absent name would not be
    # declared by the parser, and the instance would lose a dimension
    for name in names:
        if not any(name in t[3] for t in tasks):
            t = rng.choice(tasks)
            t[3][name] = rng.randint(1, 9)
    # the loops, over distinct subtrees; a path into a subtree wrapped
    # earlier is extended by the index of the region of the loop, since the
    # wrapped subtree moved one level down
    paths: list = []
    subtrees(tree, paths, ())
    chosen = rng.sample(paths, min(n_loops, len(paths)))
    # wrap the deeper paths first, so that the shallower ones stay valid
    chosen.sort(key=len, reverse=True)
    for path in chosen:
        tree = wrap(tree, path, rng.choice(LOOP_PROBS))
    return tree


# ---------------------------------------------------------------------------
# the text
# ---------------------------------------------------------------------------

OPS = {"sequence": ",", "parallel": "||", "choice": "^"}


def text(node) -> str:
    if node[0] == "task":
        _, name, duration, impact = node
        if not impact:
            return f"({name}, {duration})"
        inner = ", ".join(f"{k}: {v}" for k, v in impact.items())
        return f"({name}, {duration}, {{{inner}}})"
    if node[0] == "loop":
        return f"({node[1]} {text(node[2])})"
    kind, prob, low, high = node
    op = f"^[{prob}]" if kind == "nature" else OPS[kind]
    return f"({text(low)} {op} {text(high)})"


def count(node, kind: str) -> int:
    if node[0] == "task":
        return 1 if kind == "task" else 0
    if node[0] == "loop":
        return (1 if kind == "loop" else 0) + count(node[2], kind)
    return (1 if node[0] == kind else 0) + count(node[2], kind) + count(node[3], kind)


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def span(spec: str, name: str) -> tuple[int, int]:
    """`a-b` or `n`, as the two ends of a range."""
    try:
        if "-" in spec:
            a, b = spec.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(spec)
    except ValueError:
        sys.exit(f"gen.py: --{name} wants a number or a range a-b, found {spec!r}")
    if lo < 0 or hi < lo:
        sys.exit(f"gen.py: --{name} wants a range a-b with 0 <= a <= b, found {spec!r}")
    return lo, hi


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default="3-8", help="tasks per instance, a number or a range (default 3-8)")
    ap.add_argument("--loops", default="1-3", help="loops per instance (default 1-3)")
    ap.add_argument("--impacts", default="1-3", help="impact components (default 1-3)")
    ap.add_argument("--choices", default="0-2", help="choice nodes, capped by the internal nodes (default 0-2)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--out", default="instances", help="the folder of the .cpi files (default instances)")
    ap.add_argument("--prefix", default="loop")
    ap.add_argument("--print", action="store_true", help="print every process as it is written")
    args = ap.parse_args()

    tasks = span(args.tasks, "tasks")
    loops = span(args.loops, "loops")
    impacts = span(args.impacts, "impacts")
    choices = span(args.choices, "choices")
    if tasks[0] < 1:
        sys.exit("gen.py: a process has at least one task")
    if impacts[0] < 1 or impacts[1] > len(IMPACT_NAMES):
        sys.exit(f"gen.py: --impacts ranges over 1..{len(IMPACT_NAMES)}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for index in range(args.count):
        rng = random.Random(args.seed * 1_000_003 + index)
        n_tasks = rng.randint(*tasks)
        n_loops = rng.randint(*loops)
        n_impacts = rng.randint(*impacts)
        n_choices = rng.randint(*choices)
        tree = generate(rng, n_tasks, n_loops, n_impacts, n_choices)
        body = text(tree)
        name = f"{args.prefix}-{args.seed}-{index}"
        header = (
            f"# {name}: tasks {count(tree, 'task')} loops {count(tree, 'loop')} "
            f"choices {count(tree, 'choice')} natures {count(tree, 'nature')} "
            f"parallels {count(tree, 'parallel')} impacts {n_impacts}\n"
        )
        (out / f"{name}.cpi").write_text(header + body + "\n")
        if args.print:
            print(f"{name}  {body}")
    print(f"{args.count} instances in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
