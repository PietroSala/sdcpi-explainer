#!/usr/bin/env python3
"""Storm against the engine under every direction of the frontier.

    run.py --bin target/release/sdcpi                    the loop tool, default run
    run.py --bin PATH --count 30 --seed 7 --jobs 2       more instances, another draw
    run.py --bin PATH --no-loops                         a build without loops
    run.py --bin PATH --max-iter 1 2 3 --budgets 10      other bounds, fewer budgets
    sdcpi test storm-directions --bin PATH [options]     the same through the tool

The suite draws `--count` small random processes (2 to 5 tasks, 1 or 2
impacts, natures and choices, and in the loop tool at most one loop) with
the generator of scripts/loops/gen.py, turns each into an instance with
`sdcpi parse`, and confronts the binary with Storm for every bound `K` of
`--max-iter` (1 and 2 by default; none with `--no-loops`, whose binary
has no such option and rejects the loop syntax, which is checked too) and
for every subset of maximized components among: none, each impact alone,
the time alone, the time and every impact. The time is always in, as the
component zero (`--time 1`), so `impact0` of the model is the time. Every
value is read in the shown orientation, the one the commands print: a
maximized component reads as the instance writes it, its frontier takes
the greatest values there, and a budget on it is a floor.

What is checked, per instance, per `K`, per subset:

  enclosure        `bound` reports `worst L-min` and `worst max-U` at most
                   1e-9: the two bounds enclose the exact pass at every
                   reachable choice state, under the directions too.
  property-shape   `to_objective --pareto true` emits one term per
                   component, `max=?` exactly on the maximized ones and
                   `min=?` elsewhere, all on the final state of the root.
  single-objective for every component `c`, Storm on the plain model
                   (`to_prism --encode-history false`, exact arithmetic)
                   with `R{"impactc"}min=?` and `max=?` equals the `exact
                   min` and `exact max` of `bound` to 1e-6.
  frontier         Storm on the history model with the frontier property
                   (`--purescheds --exact`) against `pareto --onephase 1`:
                   the two sets of exact points are compared in the directed
                   sense, a point dominating another when it is at least as
                   good in every component, below on a minimized one and
                   above on a maximized one, and better in one. The verdicts
                   are those of scripts/loops/storm_check.py: `equal`,
                   `storm-subset` and `storm-dominated` pass, since Storm
                   ranges over positional pure schedulers, which equal our
                   history-dependent strategies through the trail of the
                   history encoding but may still return a subset in a
                   multi-objective query, and keep no past of a loop beyond
                   the pass under way (LOOPS.md, Limits); `mismatch`, a
                   point of Storm that is neither ours nor dominated by one
                   of ours, fails.
  frontier-antichain  no point of our frontier dominates another.
  frontier-extremes   the least value of a minimized component over our
                   frontier, and the greatest of a maximized one, is the
                   exact optimum `bound` reports, to 1e-6.
  frontier-names   `pareto` names the maximized components back.
  determine-vs-frontier  for `--budgets` random budgets (20 by default)
                   drawn per component between the two bounds and a quarter
                   of their width beyond them on both sides, `determine
                   --arith exact` answers yes exactly when a point of our
                   complete frontier fits the budget, a floor on a maximized
                   component.
  achievability    on the same budgets, the property of `to_objective --B`
                   (`<=` on a minimized component, `>=` on a maximized one)
                   on the history model with `--purescheds --exact` agrees
                   with `determine`: Storm `true` and ours `no` always fails,
                   Storm `false` and ours `yes` fails on an instance without
                   loops, or with loops at `K = 1`, where the two classes of
                   strategies coincide; with loops at `K >= 2` it is Storm
                   ranging over a subclass and is reported as `storm-weaker`,
                   a pass with a note.

A run of Storm, or of the binary, that exceeds `--timeout` seconds (60 by
default) is a skipped check, as is a frontier of ours that is not complete
or a run Storm refuses; a nonzero exit of the binary is a failure. The
summary at the end counts the checks made, passed, failed and skipped, with
one line per kind, then the findings, each with the process, the command
lines and both outputs; `--work DIR` keeps everything generated (the
instances, the models, the properties, the logs and `report.json`), by
default in a temporary folder removed at the end. The exit code is 0
exactly when nothing failed, 2 when the run cannot start.
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
sys.path.insert(0, str(HERE.parent / "loops"))
import gen  # noqa: E402  (scripts/loops/gen.py: the random processes)

Point = tuple[Fraction, ...]
FLOAT = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


class ToolError(Exception):
    """A tool failed; the message says which and how."""


class Timeout(Exception):
    """A tool ran out of time."""


def run(cmd: list[str], timeout: float, log: Path | None = None) -> str:
    """Runs `cmd` and returns its two streams joined; raises Timeout past
    `timeout` seconds and ToolError on a nonzero exit."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Timeout(f"timeout after {timeout:g} s: {' '.join(cmd)}") from None
    except OSError as e:
        raise ToolError(f"{cmd[0]}: {e}") from None
    text = r.stdout + r.stderr
    if log is not None:
        log.write_text("$ " + " ".join(cmd) + "\n" + text)
    if r.returncode != 0:
        tail = text.strip().splitlines()[-4:]
        raise ToolError(f"exit {r.returncode}: {' '.join(cmd)}\n  " + "\n  ".join(tail))
    return text


# -- the records ----------------------------------------------------------------


class Check:
    """One check: its kind, where it ran, how it went, and the evidence."""

    def __init__(self, kind: str, where: str, status: str, detail: str = "",
                 repro: str = ""):
        self.kind = kind
        self.where = where
        self.status = status  # pass | fail | skip
        self.detail = detail
        self.repro = repro

    def as_dict(self) -> dict:
        return {"kind": self.kind, "where": self.where, "status": self.status,
                "detail": self.detail, "repro": self.repro}


# -- reading the tools ----------------------------------------------------------


def vector(line: str) -> list[Fraction]:
    """The numbers between the brackets of a `key [a, b, ...]` line."""
    inner = line[line.index("[") + 1:line.rindex("]")]
    return [Fraction(x.strip()) for x in inner.split(",") if x.strip()]


def read_bound(text: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        if line.startswith("U at s0"):
            out["U"] = vector(line)
        elif line.startswith("L at s0"):
            out["L"] = vector(line)
        elif line.startswith("exact min"):
            out["min"] = vector(line)
        elif line.startswith("exact max"):
            out["max"] = vector(line)
        elif line.startswith("worst L-min"):
            out["worst_L"] = Fraction(re.search(FLOAT, line[len("worst L-min"):]).group(0))
        elif line.startswith("worst max-U"):
            out["worst_U"] = Fraction(re.search(FLOAT, line[len("worst max-U"):]).group(0))
    for key in ("U", "L", "min", "max", "worst_L", "worst_U"):
        if key not in out:
            raise ToolError(f"bound printed no `{key}` line")
    return out


def storm_points(text: str) -> list[list[str]]:
    """The points of a Storm run as lists of rational literals, read as
    scripts/loops/storm_check.py reads them."""
    points = [[x.strip() for x in m.group(1).split(",")]
              for m in re.finditer(r"\(([^()]*)\)\s*approx\.", text)]
    if not points:
        single = re.search(r"Result \(for initial states\): (\S+)", text)
        if single:
            points = [[single.group(1)]]
    return points


def storm_single(text: str) -> Fraction:
    m = re.search(r"Result \(for initial states\): (\S+)", text)
    if not m:
        raise ToolError("no result from Storm")
    return Fraction(m.group(1))


def storm_bool(text: str) -> bool:
    m = re.search(r"Result \(for initial states\): (true|false)", text)
    if not m:
        raise ToolError("no true/false from Storm")
    return m.group(1) == "true"


# -- the directed order ---------------------------------------------------------


def at_least_as_good(a: Point, b: Point, maximized: list[bool]) -> bool:
    return all((x >= y) if m else (x <= y) for x, y, m in zip(a, b, maximized))


def dominates(a: Point, b: Point, maximized: list[bool]) -> bool:
    return a != b and at_least_as_good(a, b, maximized)


def verdict(storm: set[Point], ours: set[Point], maximized: list[bool]) -> tuple[str, list[Point]]:
    foreign = sorted(storm - ours)
    if not foreign:
        return ("equal" if storm == ours else "storm-subset"), []
    if all(any(dominates(q, p, maximized) for q in ours) for p in foreign):
        return "storm-dominated", foreign
    return "mismatch", [p for p in foreign if not any(dominates(q, p, maximized) for q in ours)]


def fmt(p) -> str:
    return "(" + ", ".join(str(x) for x in p) + ")"


def close(a: Fraction, b: Fraction, tol: Fraction = Fraction(1, 10**6)) -> bool:
    return abs(a - b) <= tol * max(Fraction(1), abs(b))


# -- one unit of work: an instance at one K ------------------------------------


def unit(job: dict) -> list[Check]:
    """Every check of one instance at one bound `K`, over every subset of
    maximized components. Runs in a worker process."""
    sdcpi = job["bin"]
    storm = job["storm"]
    timeout = job["timeout"]
    work = Path(job["work"])
    name = job["name"]
    k = job["k"]
    checks: list[Check] = []
    text = Path(job["cpi"]).read_text()
    process = next(l for l in text.splitlines() if not l.startswith("#")).strip()
    has_loop = re.search(r"\(\s*0\.\d+\s+\(", process) is not None

    def repro(cmds: list[list[str]], outputs: list[str] = ()) -> str:
        lines = [f"process: {process}"]
        lines += ["$ " + " ".join(c) for c in cmds]
        lines += list(outputs)
        return "\n".join(lines)

    instance = work / f"{name}.yaml"
    kopt = ["--max-iter", str(k)] if k is not None else []
    tag_k = f"{name}" + (f".k{k}" if k is not None else "")
    where_k = f"{name} K={k if k is not None else '-'}"
    try:
        run([sdcpi, "parse", "--file", job["cpi"], "--out", str(instance)], timeout)
    except (ToolError, Timeout) as e:
        checks.append(Check("parse", where_k, "fail", str(e), repro([[sdcpi, "parse", "--file", job["cpi"]]])))
        return checks
    yaml = instance.read_text()
    m = re.search(r"impact_names:\s*\[([^\]]*)\]", yaml)
    impacts = [x.strip() for x in m.group(1).split(",") if x.strip()]
    names = ["time"] + impacts
    root = int(re.search(r"^\s*- \w+:.*\n\s*id: (\d+)", yaml, re.M).group(1))

    # the components whose total is not zero: a reward structure that is
    # identically zero aborts Storm, and the generator never writes one
    try:
        info = run([sdcpi, "info", str(instance), "--time", "1"] + kopt, timeout)
        totals = vector(next(l for l in info.splitlines() if l.startswith("total impact")))
        kept = [j for j, t in enumerate(totals) if t != 0 or j == 0]
    except (ToolError, Timeout) as e:
        checks.append(Check("info", where_k, "fail", str(e), repro([[sdcpi, "info", str(instance)]])))
        return checks

    subsets: list[list[str]] = [[]] + [[n] for n in impacts] + [["time"], ["time"] + impacts]
    seen = set()
    for subset in subsets:
        key = tuple(subset)
        if key in seen:
            continue
        seen.add(key)
        maximized = [n in subset for n in names]
        mopt = ["--maximize", ",".join(subset)] if subset else []
        common = ["--time", "1"] + kopt + mopt
        tag = tag_k + ("." + "-".join(subset) if subset else ".min")
        where = where_k + f" max={','.join(subset) or '-'}"
        rng = random.Random(f"{job['seed']}:{name}:{k}:{','.join(subset)}")

        # -- bound: the enclosure and the exact optima -------------------------
        cmd_bound = [sdcpi, "bound", str(instance)] + common
        try:
            btext = run(cmd_bound, timeout, work / f"{tag}.bound.log")
            b = read_bound(btext)
        except Timeout as e:
            checks.append(Check("enclosure", where, "skip", str(e)))
            continue
        except ToolError as e:
            checks.append(Check("enclosure", where, "fail", str(e), repro([cmd_bound])))
            continue
        if b["worst_L"] <= Fraction(1, 10**9) and b["worst_U"] <= Fraction(1, 10**9):
            checks.append(Check("enclosure", where, "pass"))
        else:
            checks.append(Check("enclosure", where, "fail",
                                f"worst L-min {float(b['worst_L'])}, worst max-U {float(b['worst_U'])}",
                                repro([cmd_bound], [btext])))

        # -- the shape of the frontier property --------------------------------
        cmd_obj = [sdcpi, "to_objective", str(instance), "--pareto", "true"] + common
        try:
            prop_text = run(cmd_obj, timeout).strip()
        except Timeout as e:
            checks.append(Check("property-shape", where, "skip", str(e)))
            continue
        except ToolError as e:
            checks.append(Check("property-shape", where, "fail", str(e), repro([cmd_obj])))
            continue
        terms = prop_text[len("multi("):-1].split(", ") if prop_text.startswith("multi(") else []
        wanted = [f'R{{"impact{j}"}}{"max" if maximized[j] else "min"}=? [ F n{root}=-2 ]'
                  for j in range(len(names))]
        if terms == wanted:
            checks.append(Check("property-shape", where, "pass"))
        else:
            checks.append(Check("property-shape", where, "fail",
                                f"expected {wanted}, found {terms}", repro([cmd_obj], [prop_text])))
        final = f"n{root}=-2"

        # -- single objectives on the plain model ------------------------------
        plain = work / f"{tag}.plain.prism"
        cmd_plain = [sdcpi, "to_prism", str(instance), "--encode-history", "false",
                     "--out", str(plain)] + common
        try:
            run(cmd_plain, timeout)
        except Timeout as e:
            checks.append(Check("single-objective", where, "skip", str(e)))
            plain = None
        except ToolError as e:
            checks.append(Check("single-objective", where, "fail", str(e), repro([cmd_plain])))
            plain = None
        if plain is not None:
            for j in kept:
                for what, key in (("min", "min"), ("max", "max")):
                    prop = work / f"{tag}.single{j}{what}.props"
                    prop.write_text(f'R{{"impact{j}"}}{what}=? [ F {final} ]\n')
                    cmd_storm = [storm, "--prism", str(plain), "--prop", str(prop), "--exact"]
                    w = where + f" c={names[j]} {what}"
                    try:
                        stext = run(cmd_storm, timeout, work / f"{tag}.single{j}{what}.log")
                        value = storm_single(stext)
                    except Timeout as e:
                        checks.append(Check("single-objective", w, "skip", str(e)))
                        continue
                    except ToolError as e:
                        checks.append(Check("single-objective", w, "skip", f"storm: {e}"))
                        continue
                    ours = b[key][j]
                    if close(value, ours):
                        checks.append(Check("single-objective", w, "pass"))
                    else:
                        checks.append(Check("single-objective", w, "fail",
                                            f"storm {value} ({float(value)}), bound exact {what} {float(ours)}",
                                            repro([cmd_bound, cmd_plain, cmd_storm],
                                                  [btext, prop.read_text(), stext])))

        # -- the frontier on the history model ---------------------------------
        model = work / f"{tag}.prism"
        prop = work / f"{tag}.props"
        cmd_model = [sdcpi, "to_prism", str(instance), "--out", str(model)] + common
        prop.write_text("multi(" + ", ".join(t for j, t in enumerate(terms) if j in kept) + ")\n")
        cmd_storm = [storm, "--prism", str(model), "--prop", str(prop), "--purescheds", "--exact"]
        out = work / f"{tag}.pareto.json"
        cmd_pareto = [sdcpi, "pareto", str(instance), "--onephase", "1", "--json", str(out)] + common
        ours_points: set[Point] | None = None
        ours_full: set[Point] | None = None
        try:
            run(cmd_model, timeout)
            ptext = run(cmd_pareto, timeout, work / f"{tag}.pareto.log")
            report = json.loads(out.read_text())
        except Timeout as e:
            checks.append(Check("frontier", where, "skip", str(e)))
            report = None
        except ToolError as e:
            checks.append(Check("frontier", where, "fail", str(e), repro([cmd_model, cmd_pareto])))
            report = None
        if report is not None:
            if report.get("status") != "complete":
                checks.append(Check("frontier", where, "skip",
                                    f"our frontier is {report.get('status')!r}"))
            else:
                ours_full = {tuple(Fraction(x) for x in p["value"]) for p in report["points"]}
                proj = {tuple(p[j] for j in kept) for p in ours_full}
                mk = [maximized[j] for j in kept]
                ours_points = {p for p in proj if not any(dominates(q, p, mk) for q in proj)}
                # the names
                if sorted(report.get("maximized", [])) == sorted(subset):
                    checks.append(Check("frontier-names", where, "pass"))
                else:
                    checks.append(Check("frontier-names", where, "fail",
                                        f"pareto names {report.get('maximized')}, asked {subset}",
                                        repro([cmd_pareto], [ptext])))
                # the antichain
                bad = [(p, q) for p in ours_full for q in ours_full if dominates(p, q, maximized)]
                if not bad:
                    checks.append(Check("frontier-antichain", where, "pass"))
                else:
                    p, q = bad[0]
                    checks.append(Check("frontier-antichain", where, "fail",
                                        f"point {fmt(p)} dominates point {fmt(q)}",
                                        repro([cmd_pareto], [ptext])))
                # the extremes
                wrong = []
                for j in range(len(names)):
                    col = [p[j] for p in ours_full]
                    if maximized[j]:
                        got, want, what = max(col), b["max"][j], "max"
                    else:
                        got, want, what = min(col), b["min"][j], "min"
                    if not close(got, want):
                        wrong.append(f"{what} of {names[j]} over the frontier {float(got)}, "
                                     f"bound exact {what} {float(want)}")
                if not wrong:
                    checks.append(Check("frontier-extremes", where, "pass"))
                else:
                    checks.append(Check("frontier-extremes", where, "fail", "; ".join(wrong),
                                        repro([cmd_bound, cmd_pareto], [btext, ptext])))
                # Storm
                try:
                    stext = run(cmd_storm, timeout, work / f"{tag}.storm.log")
                    pts = storm_points(stext)
                    if not pts:
                        raise ToolError("no point read from Storm")
                    storm_set = {tuple(Fraction(x) for x in p) for p in pts}
                except Timeout as e:
                    checks.append(Check("frontier", where, "skip", str(e)))
                    storm_set = None
                except ToolError as e:
                    checks.append(Check("frontier", where, "skip", f"storm: {e}"))
                    storm_set = None
                if storm_set is not None:
                    word, foreign = verdict(storm_set, ours_points, mk)
                    if word == "mismatch":
                        lines = [f"storm point {fmt(p)} is not ours and nothing of ours dominates it"
                                 for p in foreign]
                        lines += [f"ours {fmt(p)} is not in the answer of Storm"
                                  for p in sorted(ours_points - storm_set)]
                        checks.append(Check("frontier", where, "fail",
                                            f"{word}: storm {len(storm_set)} / ours {len(ours_points)} points; "
                                            + "; ".join(lines),
                                            repro([cmd_model, cmd_pareto, cmd_storm],
                                                  [prop.read_text(), ptext, stext])))
                    else:
                        checks.append(Check("frontier", where, "pass",
                                            f"{word}: storm {len(storm_set)} / ours {len(ours_points)} points"))

        # -- achievability on random budgets -----------------------------------
        for i in range(job["budgets"]):
            budget = []
            for j in range(len(names)):
                lo, hi = b["L"][j], b["U"][j]
                w = hi - lo if hi > lo else Fraction(1)
                v = float(lo - w / 4) + rng.random() * float(hi - lo + w / 2)
                budget.append(f"{v:.2f}")
            bstr = ",".join(budget)
            bvec = tuple(Fraction(x) for x in budget)
            w = where + f" B={bstr}"
            cmd_det = [sdcpi, "determine", str(instance), "--B", bstr, "--arith", "exact"] + common
            try:
                dtext = run(cmd_det, timeout, work / f"{tag}.det{i}.log")
                m = re.search(r"^answer\s+(yes|no)", dtext, re.M)
                if not m:
                    raise ToolError("determine printed no answer")
                answer = m.group(1) == "yes"
            except Timeout as e:
                checks.append(Check("achievability", w, "skip", str(e)))
                continue
            except ToolError as e:
                checks.append(Check("achievability", w, "fail", str(e), repro([cmd_det])))
                continue
            # against our own frontier
            if ours_full is not None:
                fits = any(at_least_as_good(p, bvec, maximized) for p in ours_full)
                if fits == answer:
                    checks.append(Check("determine-vs-frontier", w, "pass"))
                else:
                    checks.append(Check("determine-vs-frontier", w, "fail",
                                        f"determine says {'yes' if answer else 'no'}, the frontier "
                                        f"{'has' if fits else 'has no'} point within the budget",
                                        repro([cmd_pareto, cmd_det], [ptext, dtext])))
            # against Storm
            aprop = work / f"{tag}.ach{i}.props"
            cmd_aobj = [sdcpi, "to_objective", str(instance), "--B", bstr, "--out", str(aprop)] + common
            cmd_ast = [storm, "--prism", str(model), "--prop", str(aprop), "--purescheds", "--exact"]
            try:
                run(cmd_aobj, timeout)
                atext = aprop.read_text().strip()
                aterms = atext[len("multi("):-1].split(", ")
                awanted = [f'R{{"impact{j}"}}{">=" if maximized[j] else "<="}{budget[j]} [ F {final} ]'
                           for j in range(len(names))]
                if aterms != awanted:
                    checks.append(Check("property-shape", w, "fail",
                                        f"expected {awanted}, found {aterms}", repro([cmd_aobj], [atext])))
                    continue
                stext = run(cmd_ast, timeout, work / f"{tag}.ach{i}.log")
                storm_says = storm_bool(stext)
            except Timeout as e:
                checks.append(Check("achievability", w, "skip", str(e)))
                continue
            except ToolError as e:
                checks.append(Check("achievability", w, "skip", f"storm: {e}"))
                continue
            if storm_says == answer:
                checks.append(Check("achievability", w, "pass"))
            elif storm_says and not answer:
                checks.append(Check("achievability", w, "fail",
                                    "storm true, determine no",
                                    repro([cmd_det, cmd_aobj, cmd_ast], [dtext, atext, stext])))
            elif has_loop and k is not None and k >= 2:
                checks.append(Check("achievability", w, "pass",
                                    "storm-weaker: storm false, determine yes, a loop at K >= 2"))
            else:
                checks.append(Check("achievability", w, "fail",
                                    "storm false, determine yes, no loop past one pass",
                                    repro([cmd_det, cmd_aobj, cmd_ast], [dtext, atext, stext])))
    return checks


# -- the suite ------------------------------------------------------------------


def generate(args, out: Path) -> list[Path]:
    """The random processes, drawn as scripts/loops/gen.py draws them."""
    files = []
    for index in range(args.count):
        rng = random.Random(args.seed * 1_000_003 + index)
        n_tasks = rng.randint(2, 5)
        n_impacts = rng.randint(1, 2)
        n_loops = 0 if args.no_loops else rng.randint(0, 1)
        n_choices = rng.randint(0, 2)
        tree = gen.generate(rng, n_tasks, n_loops, n_impacts, n_choices)
        body = gen.text(tree)
        name = f"dir-{args.seed}-{index}"
        header = (f"# {name}: tasks {gen.count(tree, 'task')} loops {gen.count(tree, 'loop')} "
                  f"choices {gen.count(tree, 'choice')} natures {gen.count(tree, 'nature')} "
                  f"parallels {gen.count(tree, 'parallel')} impacts {n_impacts}\n")
        path = out / f"{name}.cpi"
        path.write_text(header + body + "\n")
        files.append(path)
    return files


def say(line: str) -> None:
    print(line, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[1:]))
    ap.add_argument("--bin", default=os.environ.get("SDCPI_BIN") or str(REPO / "target" / "release" / "sdcpi"),
                    help="the sdcpi binary (default SDCPI_BIN or target/release/sdcpi)")
    ap.add_argument("--count", type=int, default=12, help="instances to draw (default 12)")
    ap.add_argument("--seed", type=int, default=1, help="seed of the draw (default 1)")
    ap.add_argument("--storm", default="storm", help="the Storm binary (default storm on the PATH)")
    ap.add_argument("--jobs", type=int, default=2, help="instances checked at once (default 2)")
    ap.add_argument("--no-loops", action="store_true",
                    help="a binary without loops: no loop is drawn, no --max-iter is passed, "
                         "and the loop syntax must be rejected")
    ap.add_argument("--max-iter", type=int, nargs="+", default=[1, 2], metavar="K",
                    help="the bounds on the passes of a loop (default 1 2)")
    ap.add_argument("--budgets", type=int, default=20, help="random budgets per subset (default 20)")
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="seconds per run of Storm and of the binary (default 60)")
    ap.add_argument("--work", type=Path, default=None,
                    help="where everything generated goes (default a temporary folder)")
    args = ap.parse_args()

    if not Path(args.bin).exists():
        print(f"{args.bin} is missing: cargo build --release first, or give --bin", file=sys.stderr)
        return 2
    if shutil.which(args.storm) is None and not Path(args.storm).exists():
        print(f"{args.storm} is not on the PATH: give --storm", file=sys.stderr)
        return 2
    if args.jobs < 1 or args.count < 1 or args.budgets < 0:
        ap.error("--jobs and --count want positive integers, --budgets a non-negative one")
    if any(k < 1 for k in args.max_iter):
        ap.error("--max-iter wants positive integers")

    tmp = None
    if args.work is None:
        tmp = tempfile.TemporaryDirectory(prefix="storm-directions-")
        work = Path(tmp.name)
    else:
        work = args.work
        work.mkdir(parents=True, exist_ok=True)

    started = time.time()
    checks: list[Check] = []

    # a build without loops refuses the loop syntax
    if args.no_loops:
        cmd = [args.bin, "parse", "(0.5 ((A, 1, {x: 1}) ^ (B, 2, {x: 2})))"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            checks.append(Check("rejects-loops", "parse", "pass"))
        else:
            checks.append(Check("rejects-loops", "parse", "fail",
                                "a loop was parsed by a build without loops",
                                "$ " + " ".join(cmd) + "\n" + r.stdout))

    files = generate(args, work)
    ks = [None] if args.no_loops else args.max_iter
    jobs = [{"bin": args.bin, "storm": args.storm, "timeout": args.timeout, "work": str(work),
             "name": f.stem, "cpi": str(f), "k": k, "budgets": args.budgets, "seed": args.seed}
            for f in files for k in ks]
    say(f"{len(files)} instances, {len(jobs)} runs, work in {work}")
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for result in pool.map(unit, jobs):
            checks.extend(result)
            for c in result:
                if c.status != "pass" or c.kind in ("frontier",):
                    say(f"  {c.status:4} {c.kind:22} {c.where}  {c.detail}".rstrip())

    # -- the summary ------------------------------------------------------------
    kinds = sorted({c.kind for c in checks})
    passed = sum(c.status == "pass" for c in checks)
    failed = sum(c.status == "fail" for c in checks)
    skipped = sum(c.status == "skip" for c in checks)
    say("")
    say(f"{'check':22} {'made':>6} {'passed':>6} {'failed':>6} {'skipped':>7}")
    for kind in kinds:
        of = [c for c in checks if c.kind == kind]
        say(f"{kind:22} {len(of):>6} {sum(c.status == 'pass' for c in of):>6} "
            f"{sum(c.status == 'fail' for c in of):>6} {sum(c.status == 'skip' for c in of):>7}")
    words = {}
    for c in checks:
        if c.kind == "frontier" and c.status == "pass":
            words[c.detail.split(":")[0]] = words.get(c.detail.split(":")[0], 0) + 1
        if c.kind == "achievability" and c.detail.startswith("storm-weaker"):
            words["storm-weaker"] = words.get("storm-weaker", 0) + 1
    if words:
        say("verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(words.items())))
    say(f"checks {len(checks)} passed {passed} failed {failed} skipped {skipped} "
        f"in {time.time() - started:.0f} s")

    findings = [c for c in checks if c.status == "fail"]
    if findings:
        say("")
        say(f"{len(findings)} findings:")
        for c in findings:
            say(f"--- {c.kind} at {c.where}: {c.detail}")
            if c.repro:
                say("    " + c.repro.replace("\n", "\n    "))
    (work / "report.json").write_text(json.dumps({
        "bin": args.bin, "seed": args.seed, "count": args.count, "no_loops": args.no_loops,
        "max_iter": ks, "checks": len(checks), "passed": passed, "failed": failed,
        "skipped": skipped, "records": [c.as_dict() for c in checks]}, indent=1))
    if tmp is not None:
        tmp.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
