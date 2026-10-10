#!/usr/bin/env python3
"""The ablation study of the campaign with loops, by the bound on the passes.

    analysis_loops.py                          into experiment-loops/analysis
    analysis_loops.py --out DIR --results DIR
    analysis_loops.py --all-instances          every record, not only the
                                               instances run at every bound

The campaign with loops (run.py --loops --max-iters ...) writes one folder
per instance and bound, `<n>-<i>-<k>-<d>-<mode>-K<bound>`, with the sixteen
records of the frontier in one phase, `onephase-<ablation>-w<workers>`. This
script reads them with the readers of analysis.py, which it imports and does
not change, and adds the bound as an axis: a cell is an ablation, a number of
workers and a bound, and its rates are the ones of the study without loops,
the total found over the total of the time the tool reports for itself, the
budget capping a run the budget cut.

The bounds of one instance run one after the other in the campaign, so at
any moment the last instance may hold some bounds and not the others. By
default only the instances run at every bound with all their sixteen records
are read, so that the columns of the bounds rest on the same instances; the
switch --all-instances reads every record.

Written into the output folder:
  * `table-loops-apexes.tex` and `table-loops-points.tex`: rows an ablation
    and a number of workers, columns the bounds, the apexes and the points a
    second;
  * `table-loops-ablation-K<bound>.tex`: for every bound the table of the
    study without loops, runs, cut runs, the rates, the yield and the
    candidate points;
  * `apexes-per-second-<ablation>-K<bound>.dat` and the same for the points,
    against the workers, for pgfplots;
  * `analysis-loops.json`: every number of the tables, the coverage by
    diagonal and the instances read.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis as base  # noqa: E402  the readers and the cells of the study without loops

KEY_RE = re.compile(r"^(?P<n>\d+)-(?P<i>\d+)-(?P<k>\d+)-(?P<d>\d+)-(?P<mode>[a-z_]+)-K(?P<bound>\d+)$")


def parse_key(name: str) -> dict | None:
    """The axes of a folder of the campaign with loops."""
    m = KEY_RE.match(name)
    if not m:
        return None
    g = m.groupdict()
    axes = {k: int(g[k]) for k in ("n", "i", "k", "d", "bound")}
    axes["mode"] = g["mode"]
    axes["diagonal"] = axes["n"] + axes["i"]
    return axes


def scan(results: Path, all_instances: bool, max_diagonal: int | None):
    """The records by instance and bound, then the cells by ablation,
    workers and bound, over the instances read."""
    by_instance: dict[tuple, dict[int, dict[str, dict]]] = collections.defaultdict(dict)
    unreadable = 0
    for folder in os.scandir(results):
        if not folder.is_dir():
            continue
        axes = parse_key(folder.name)
        if axes is None or (max_diagonal is not None and axes["diagonal"] > max_diagonal):
            continue
        recs, bad = base.records_of(folder)
        unreadable += bad
        recs = {c: r for c, r in recs.items() if c != base.STORM_CONF}
        inst = (axes["n"], axes["i"], axes["k"], axes["d"], axes["mode"])
        by_instance[inst][axes["bound"]] = recs
    bounds = sorted({b for v in by_instance.values() for b in v})
    confs = sorted({c for v in by_instance.values() for recs in v.values() for c in recs})
    full = {inst for inst, per in by_instance.items()
            if set(per) == set(bounds) and all(len(per[b]) == len(confs) for b in bounds)}
    taken = set(by_instance) if all_instances else full
    cells: dict[tuple[str, int, int], base.Cell] = collections.defaultdict(base.Cell)
    coverage = collections.defaultdict(lambda: collections.Counter())
    for inst in taken:
        diagonal = inst[0] + inst[1]
        coverage[diagonal]["instances"] += 1
        for b, recs in by_instance[inst].items():
            for conf, rec in recs.items():
                parsed = base.parse_conf(conf)
                if parsed is None:
                    continue
                ablation, workers = parsed
                cells[(ablation, workers, b)].add(rec)
                coverage[diagonal]["runs"] += 1
                coverage[diagonal]["truncated"] += rec.get("status") == base.TRUNCATED
    started = collections.Counter(i[0] + i[1] for i in by_instance)
    return {
        "cells": cells, "bounds": bounds, "confs": confs, "taken": taken,
        "instances_seen": len(by_instance), "instances_full": len(full),
        "coverage": coverage, "started": started, "unreadable": unreadable,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--results", type=Path, default=HERE / "experiment-loops" / "results")
    p.add_argument("--out", type=Path, default=HERE / "experiment-loops" / "analysis")
    p.add_argument("--all-instances", action="store_true",
                   help="read every record, not only the instances run at every bound")
    p.add_argument("--max-diagonal", type=int, default=None)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    s = scan(a.results, a.all_instances, a.max_diagonal)
    cells, bounds = s["cells"], s["bounds"]
    ablations = sorted({base.parse_conf(c)[0] for c in s["confs"] if base.parse_conf(c)})
    workers = sorted({base.parse_conf(c)[1] for c in s["confs"] if base.parse_conf(c)})
    order = [x for x in ("both", "accept", "reject", "none") if x in ablations] + \
            [x for x in ablations if x not in ("both", "accept", "reject", "none")]

    summary = {(ab, w, b): base.cell_summary(cells[(ab, w, b)])
               for ab in order for w in workers for b in bounds if (ab, w, b) in cells}
    diagonals = sorted(s["coverage"])
    which = "every record" if a.all_instances else "the instances run at every bound"
    scope = (f"{len(s['taken'])} instances ({which}), diagonals "
             f"{', '.join(str(d) for d in diagonals)}")

    # the two cross tables: rows ablation and workers, columns the bounds
    for what, field in (("apexes", "apexes_per_second"), ("points", "points_per_second")):
        rows, rules = [], set()
        for ab in order:
            for w in workers:
                rows.append([f"\\texttt{{{ab}}}", str(w)] +
                            [base.si(summary.get((ab, w, b), {}).get(field), 1) for b in bounds])
            rules.add(len(rows) - 1)
        cut = [sum(summary.get((ab, w, b), {}).get("truncated", 0) for ab in order for w in workers)
               for b in bounds]
        runs = [sum(summary.get((ab, w, b), {}).get("runs", 0) for ab in order for w in workers)
                for b in bounds]
        rows.append(["runs", ""] + [base.si(r) for r in runs])
        rows.append(["cut", ""] + [base.si(c) for c in cut])
        base.write_table(
            a.out / f"table-loops-{what}.tex",
            caption=(f"The {what} a second of the frontier in one phase on the benchmark with "
                     f"loops, by ablation, number of workers and bound $K$ on the passes of a "
                     f"loop, over {scope}. A rate is the total found over the total of the time "
                     "the tool reports for itself; a run the budget cut counts with the budget "
                     "as its time and its points as candidates. The last two rows count the "
                     "runs and the cut runs of each bound."),
            label=f"tab:loops-{what}",
            spec="ll" + "r" * len(bounds),
            header=["ablation", "$w$"] + [f"$K={b}$" for b in bounds],
            rows=rows, rules={r for r in rules})

    # one table of the study without loops per bound
    for b in bounds:
        rows, rules = [], set()
        for ab in order:
            for w in workers:
                d = summary.get((ab, w, b))
                if d is None:
                    continue
                rows.append([f"\\texttt{{{ab}}}", str(w), base.si(d["runs"]), base.si(d["truncated"]),
                             base.si(d["apexes_per_second"], 1), base.si(d["points_per_second"], 1),
                             base.si(d["points_per_second_complete"], 1),
                             base.si(100 * d["yield"], 1) if d["yield"] is not None else "--",
                             base.si(d["candidate_points"])])
            rules.add(len(rows) - 1)
        base.write_table(
            a.out / f"table-loops-ablation-K{b}.tex",
            caption=(f"The two early tests, switched on and off, on the benchmark with loops at "
                     f"the bound $K={b}$ on the passes, over {scope}. The columns are those of "
                     "the study without loops."),
            label=f"tab:loops-ablation-K{b}",
            spec="llrrrrrrr",
            header=["ablation", "$w$", "runs", "cut", "apexes/s", "points/s",
                    "points/s compl.", "yield (\\%)", "cand.\\ points"],
            rows=rows, rules={r for r in rules if r != len(rows) - 1})

    # the data files of the throughput, against the workers
    for what in ("points", "apexes"):
        for ab in order:
            for b in bounds:
                rows = []
                for w in workers:
                    d = summary.get((ab, w, b))
                    if d is not None:
                        rows.append([w, d[f"{what}_per_second"], d[f"{what}_per_second_complete"],
                                     d["runs"], d["truncated"]])
                base.write_dat(a.out / f"{what}-per-second-{ab}-K{b}.dat",
                               ["workers", "per_second", "per_second_complete_runs", "runs", "truncated"],
                               rows, f"{what} a second of the ablation {ab} at the bound K={b}, {scope}")

    out = {
        "generated_from": str(a.results),
        "scope": scope,
        "instances_seen": s["instances_seen"],
        "instances_run_at_every_bound": s["instances_full"],
        "instances_read": len(s["taken"]),
        "instances_started_by_diagonal": dict(sorted(s["started"].items())),
        "coverage_by_diagonal": {d: dict(c) for d, c in sorted(s["coverage"].items())},
        "bounds": bounds, "ablations": order, "workers": workers,
        "cells": {f"{ab}-w{w}-K{b}": v for (ab, w, b), v in summary.items()},
        "unreadable_records": s["unreadable"],
    }
    (a.out / "analysis-loops.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(f"{scope}; {sum(v['runs'] for v in summary.values())} runs; into {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
