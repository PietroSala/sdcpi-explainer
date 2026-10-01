#!/usr/bin/env python3
"""Storm against the engine on processes with loops.

    storm_check.py INSTANCE... --max-iter K            one bound, every instance
    storm_check.py INSTANCE... --max-iter 1 2 3        every bound in turn
    storm_check.py INSTANCE... --max-iter 3 --time 0   the impacts alone

For every instance and every bound K the model of `sdcpi to_prism --max-iter
K` and the frontier property of `sdcpi to_objective --pareto true` go to
Storm, with pure schedulers and exact arithmetic, and the frontier of `sdcpi
pareto --onephase 1` at the same K is computed beside. The two sets of
points are compared as exact rationals and one verdict is printed per run:

    equal           the two sets coincide
    storm-subset    every point of Storm is one of ours, and ours has more
    storm-dominated every point of Storm is one of ours or dominated by one
                    of ours, and at least one is dominated
    mismatch        a point of Storm is neither one of ours nor dominated by
                    one of ours, or an extreme differs
    unchecked       a tool failed or ran out of time, Storm as a rule: its
                    frontier under pure schedulers goes through a MILP and
                    a nested loop at K = 3 can take it past any budget

The engine ranges over the history-dependent policies and its points are
exact, so Storm, whose positional pure schedulers on the model with loops
read the pass under way and not the passes gone by (see the trail note of
src/to_prism.rs), may return a subset of the frontier or points the
frontier dominates, and never a point outside it: the first three verdicts
are the engine and the model agreeing, a mismatch is a defect in one of
the two, and an unchecked run says nothing. The exit code counts the
mismatches and the unchecked runs.

The time is the component zero by default, `--time 1`, as the paper has it;
`--time 0` compares the impacts alone. A component that is zero on every
task is dropped from the question put to Storm, which aborts on a reward
structure that is identically zero, and the two frontiers are compared on
the components kept, as the campaign of scripts/pareto-frontier does.

The points of Storm are read as scripts/pareto-frontier/run.py reads them,
in `run_storm`: the rationals of the `(...) approx.` lines, or the single
value of `Result (for initial states):` when one objective is left. That
script drives a campaign and is not importable without its benchmark, so
the few lines are repeated here and kept in step by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# the binary, overridable so that a build elsewhere can be checked
SDCPI = Path(os.environ.get("SDCPI_BIN") or REPO / "target" / "release" / "sdcpi")

Point = tuple[Fraction, ...]


class ToolError(Exception):
    """A tool failed or timed out; the message says which and how."""


def run(cmd: list[str], timeout: float) -> str:
    """Runs `cmd` and returns its two streams joined, or raises ToolError."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError(f"timeout after {timeout:g} s: {' '.join(cmd)}") from None
    except OSError as e:
        raise ToolError(f"{cmd[0]}: {e}") from None
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        raise ToolError(f"exit {r.returncode}: {' '.join(cmd)}\n  " + "\n  ".join(tail))
    return r.stdout + r.stderr


# -- the two frontiers --------------------------------------------------------


def storm_points(text: str) -> list[list[str]]:
    """The points of a Storm run as lists of rational literals: the lines
    `( a, b, ... ) approx. ( ... )` of a multi-objective answer, or the one
    value of a single objective. The same reading as `run_storm` of
    scripts/pareto-frontier/run.py."""
    points = [[x.strip() for x in m.group(1).split(",")]
              for m in re.finditer(r"\(([^()]*)\)\s*approx\.", text)]
    if not points:
        single = re.search(r"Result \(for initial states\): (\S+)", text)
        if single:
            points = [[single.group(1)]]
    return points


def objectives(instance: Path, common: list[str]) -> tuple[list[int], int]:
    """The components whose total impact is not zero, the time always kept,
    and the number of components: a component zero on every task is zero
    for every strategy and Storm aborts on a reward structure that is
    identically zero."""
    info = run([str(SDCPI), "info", str(instance)] + common, 120)
    line = next(l for l in info.splitlines() if l.startswith("total impact"))
    totals = [Fraction(x) for x in re.findall(r"-?\d+\.\d+", line)]
    time = "--time" in common
    return [j for j, t in enumerate(totals) if t != 0 or (time and j == 0)], len(totals)


def storm_frontier(instance: Path, common: list[str], history: str, kept: list[int],
                   work: Path, tag: str, timeout: float) -> set[Point]:
    model = work / f"{tag}.prism"
    prop = work / f"{tag}.props"
    run([str(SDCPI), "to_prism", str(instance), "--encode-history", history,
         "--out", str(model)] + common, 120)
    full = run([str(SDCPI), "to_objective", str(instance), "--pareto", "true"] + common,
               120).strip()
    terms = full[len("multi("):-1].split(", ")
    prop.write_text("multi(" + ", ".join(t for j, t in enumerate(terms) if j in kept) + ")\n")
    text = run(["storm", "--prism", str(model), "--prop", str(prop), "--purescheds", "--exact"],
               timeout)
    (work / f"{tag}.storm.log").write_text(text)
    points = storm_points(text)
    if not points:
        raise ToolError(f"no point read from Storm, see {work / f'{tag}.storm.log'}")
    return {tuple(Fraction(x) for x in p) for p in points}


def our_frontier(instance: Path, common: list[str], kept: list[int], work: Path, tag: str,
                 timeout: float) -> set[Point]:
    out = work / f"{tag}.pareto.json"
    run([str(SDCPI), "pareto", str(instance), "--onephase", "1", "--json", str(out)] + common,
        timeout)
    report = json.loads(out.read_text())
    if report.get("status") != "complete":
        raise ToolError(f"the frontier of ours is {report.get('status')!r}, see {out}")
    points = {tuple(Fraction(p["value"][j]) for j in kept) for p in report["points"]}
    # the projection on the components kept may fold two points into one
    # that the other dominates, the components dropped being zero: keep
    # the undominated ones
    return {p for p in points if not any(q != p and dominates(q, p) for q in points)}


# -- the extremes ---------------------------------------------------------------


def extremes(instance: Path, common: list[str], kept: list[int], work: Path, tag: str,
             timeout: float) -> list[str]:
    """The least and the greatest expected value of every component kept,
    Storm on the plain model against `sdcpi optima`: a single component
    has a positional optimal policy, so the plain model answers it and the
    trail is not needed. Returns the discrepancies, as lines."""
    model = work / f"{tag}.plain.prism"
    prop = work / f"{tag}.extremes.props"
    run([str(SDCPI), "to_prism", str(instance), "--encode-history", "false",
         "--out", str(model)] + common, 120)
    full = run([str(SDCPI), "to_objective", str(instance), "--pareto", "true"] + common,
               120).strip()
    final = re.search(r"\[ F ([^\]]*)\]", full).group(1).strip()
    # one call per property: Storm reads a property file as one formula
    # and refuses several, one per line
    found: dict[tuple[int, str], Fraction] = {}
    log = []
    for j in kept:
        for what in ("min", "max"):
            prop.write_text(f'R{{"impact{j}"}}{what}=? [ F {final} ]\n')
            text = run(["storm", "--prism", str(model), "--prop", str(prop), "--exact"], timeout)
            log.append(text)
            m = re.search(r"Result \(for initial states\): (\S+)", text)
            if not m:
                (work / f"{tag}.extremes.log").write_text("\n".join(log))
                raise ToolError(f"no result from Storm for the {what} of component {j}, "
                                f"see {work / f'{tag}.extremes.log'}")
            found[(j, what)] = Fraction(m.group(1))
    (work / f"{tag}.extremes.log").write_text("\n".join(log))
    opt = run([str(SDCPI), "optima", str(instance)] + common, timeout)
    ours: dict[int, tuple[float, float]] = {}
    for m in re.finditer(r"component (\d+)\s+min (\S+)\s+max (\S+)", opt):
        ours[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
    out = []
    for j in kept:
        for pos, what in enumerate(("min", "max")):
            storm = found[(j, what)]
            mine = ours[j][pos]
            # the optima of ours are floating point, Storm's are exact
            if abs(float(storm) - mine) > 1e-9 * max(1.0, abs(mine)):
                out.append(f"{what} of component {j}: storm {storm} ({float(storm)}), ours {mine}")
    return out


# -- the comparison -------------------------------------------------------------


def dominates(a: Point, b: Point) -> bool:
    """Whether `a` is at most `b` in every component and below it in one."""
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def verdict(storm: set[Point], ours: set[Point]) -> tuple[str, list[Point]]:
    """The verdict, and the points of Storm that are not ours."""
    foreign = sorted(storm - ours)
    if not foreign:
        return ("equal" if storm == ours else "storm-subset"), []
    if all(any(dominates(q, p) for q in ours) for p in foreign):
        return "storm-dominated", foreign
    return "mismatch", [p for p in foreign if not any(dominates(q, p) for q in ours)]


def fmt(p: Point) -> str:
    return "(" + ", ".join(str(x) for x in p) + ")"


def say(line: str) -> None:
    """One line, written at once: a run of Storm may take a while and the
    verdicts before it are worth reading through a pipe."""
    print(line, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[1:]))
    ap.add_argument("instances", nargs="+", type=Path, help="instance files, YAML")
    ap.add_argument("--max-iter", type=int, nargs="+", default=[3], metavar="K",
                    help="the bound on the passes of a loop, one or more (default 3)")
    ap.add_argument("--time", type=int, choices=[0, 1], default=1,
                    help="1, the default, the time as the component zero; 0 the impacts alone")
    ap.add_argument("--history", choices=["true", "false"], default="true",
                    help="--encode-history of to_prism (default true)")
    ap.add_argument("--extremes", type=int, choices=[0, 1], default=1,
                    help="1, the default, also checks the least and the greatest expected "
                         "value of every component, Storm on the plain model against "
                         "sdcpi optima; 0 compares the frontiers alone")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="seconds per Storm run and per run of ours (default 600)")
    ap.add_argument("--work", type=Path, default=None,
                    help="where the models, the properties, the logs and the reports go "
                         "(default a temporary folder, removed at the end)")
    a = ap.parse_args()

    if not SDCPI.exists():
        print(f"{SDCPI} is missing: cargo build --release first, or set SDCPI_BIN",
              file=sys.stderr)
        return 2
    for k in a.max_iter:
        if k < 1:
            ap.error("--max-iter wants positive integers")

    tmp = None
    if a.work is None:
        tmp = tempfile.TemporaryDirectory(prefix="storm-check-")
        work = Path(tmp.name)
    else:
        work = a.work
        work.mkdir(parents=True, exist_ok=True)

    mismatches = 0
    for instance in a.instances:
        for k in a.max_iter:
            common = ["--max-iter", str(k)] + (["--time", "1"] if a.time else [])
            tag = f"{instance.stem}.k{k}" + (".time" if a.time else "")
            head = f"{instance.name:28} K={k:<3} time={a.time} "
            try:
                kept, every = objectives(instance, common)
                if not kept:
                    say(head + "equal           0 / 0 points, every impact zero")
                    continue
                storm = storm_frontier(instance, common, a.history, kept, work, tag, a.timeout)
                ours = our_frontier(instance, common, kept, work, tag, a.timeout)
                wrong = extremes(instance, common, kept, work, tag, a.timeout) if a.extremes else []
            except ToolError as e:
                mismatches += 1
                say(head + f"unchecked       {e}")
                continue
            word, foreign = verdict(storm, ours)
            if wrong:
                word = "mismatch"
            say(head + f"{word:15} storm {len(storm):>3} / ours {len(ours):>3} points"
                + (f", components {kept} of {every}" if len(kept) != every else "")
                + (", extremes agree" if a.extremes and not wrong else ""))
            if word == "mismatch":
                mismatches += 1
                for p in foreign:
                    say(f"{'':44}storm point {fmt(p)} is not ours and nothing of ours dominates it")
                missing = sorted(ours - storm)
                if missing and len(missing) <= 12:
                    for p in missing:
                        say(f"{'':44}ours {fmt(p)} is not in the answer of Storm")
                for line in wrong:
                    say(f"{'':44}{line}")
            elif word == "storm-dominated":
                for p in foreign:
                    q = next(q for q in sorted(ours) if dominates(q, p))
                    say(f"{'':44}storm point {fmt(p)} is dominated by ours {fmt(q)}")
    if tmp is not None:
        tmp.cleanup()
    return mismatches


if __name__ == "__main__":
    sys.exit(main())
