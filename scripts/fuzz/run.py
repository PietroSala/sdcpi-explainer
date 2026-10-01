#!/usr/bin/env python3
"""Fuzz: random and mutated inputs against the sdcpi binary and its page.

    run.py --bin PATH                          the loop tool, page included
    run.py --bin PATH --no-loops               the mirror without loops
    run.py --bin PATH --count 40 --seed 7 --port 8137 --scratch DIR
    run.py --bin PATH --skip-page              the command line alone
    run.py --bin PATH --heavy                  the tests that allocate gigabytes

What the suite checks is that nothing the tool reads can make it panic,
hang, crash or answer out of contract; it does not measure what the tool
computes beyond a few cheap consistencies. Three families of inputs are
drawn from `--seed`, so that a run repeats exactly:

  (a) `--count` random valid texts from a grammar generator of this file,
      every construct in: tasks with and without a map, sequence commas and
      chains, parallel, choice, nature, loops (unless `--no-loops`),
      comments and odd spacing;
  (b) mutations of those texts: a character deleted, inserted or swapped, a
      parenthesis removed or doubled, a number replaced by 0, -1, 1.5, 1e9
      or twenty digits, a probability by 0, 1, 1.0, 1.5, -0.1, a duration
      by 0, 1000000, 2.5, a name by the empty string, a name with spaces,
      quotes, unicode, a keyword, 300 characters, a duplicate, the impact
      map by {}, {a: 0}, {a: -1}, {a: 1e-9}; plus a nesting 400 deep (and
      1999, the last the parser allows), a sequence of 1500 tasks and a
      parallel of 60 tasks;
  (c) instance files: the YAML `parse` writes for a valid text, altered in
      its kinds, its identifiers (duplicated, zero, negative, huge, missing,
      a child carrying the identifier of its parent), negative or ill-sized
      impacts, missing fields, `max_iter: 0`, probabilities out of range,
      header counts of zero or of fifty million nodes, an empty file,
      binary garbage, a megabyte of noise; with `--heavy` also a header
      declaring 2^32 - 1 nodes or 4e8 dimensions.

Every input goes to `parse` (a text) or to `info` (a file) with a timeout
of `--timeout` seconds (30). The contract: exit 0 with output, or a
non-zero exit with one line on standard error. The tool exits 2 on a
malformed input or argument, as its README says (nonzero), so 1 and 2 are
both clean refusals. A timeout, a signal, a "panicked", "overflow" or
"SIGSEGV" in the output, an empty standard error on a refusal, or more than
one line there, is a finding. Every input the tool accepts then goes
through `info`, `bound --time 1`, `bound --time 1 --maximize time`,
`pareto --onephase 1 --time 1 --time-limit 2 --workers 1`, `determine`
with the upper bound as the budget (which must answer yes) and with a
budget under the lower bound (which must answer no), `to_prism`, `to_svg`
and `to_text`, under the same rules, with these consistencies on top: the
two breaches `worst L-min` and `worst max-U` of `bound` stay at zero, the
exact optima lie in the box of the two bounds, every point of the frontier
lies in that box, and the text `to_text` writes parses back to an instance
with the same bounds.

Then, unless `--skip-page`, the suite starts its own page server on the
first free port from `--port` (8137), with a store under the scratch folder
and the benchmark folder `--bench` when it exists, and sends every text,
valid and mutated, to /api/svg, /api/bounds (time off and on, maximizing a
known name and an unknown one), /api/frontier with a limit of 2 seconds,
and /api/start, /api/snapshot, /api/stop; then the malformed bodies: empty,
fields missing, five megabytes, invalid percent-encoding, a byte that is
not UTF-8, a process field of a million characters and of two hundred
thousand, workers 0, -1 and 1000, a limit of 0, 1e9 and abc, max_iter 0,
500 and inf, a window of fifty components, a thousand names to maximize, a
lying Content-Length, unknown identifiers and unknown routes. Every answer
must be HTTP 200 with a JSON object holding a result or an "error" string;
a 400 or 404 with a one-line reason is accepted as a refusal for the bodies
the server cannot read at all (too large, not UTF-8, no such route) and
counted apart; a dropped connection, a 5xx, a 200 that is not JSON, or a
server that stops answering /api/hello is a finding, and so is a "panicked"
in the log of the server. The server is killed at the end.

Every input is written under `--scratch` (`inputs/`), the raw outputs of
every failing check beside it (`outputs/`), and the findings in
`findings.json`. The summary counts the checks made, passed, failed and
skipped; the exit code is 0 exactly when nothing failed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BENCH = Path.home() / "Documents/github/sdcpi-parallel/scripts/pareto-frontier/experiment/instances"
BAD_WORDS = ("panicked", "overflow", "SIGSEGV", "segmentation fault", "Segmentation fault")
EPS = 1e-6


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------


class Ledger:
    def __init__(self, scratch: Path, verbose: bool):
        self.checks = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.refusals_http = 0
        self.findings: list[dict] = []
        self.skip_reasons: dict[str, int] = {}
        self.scratch = scratch
        self.verbose = verbose
        self.outputs = scratch / "outputs"
        self.outputs.mkdir(parents=True, exist_ok=True)

    def ok(self, label: str):
        self.checks += 1
        self.passed += 1
        if self.verbose:
            print(f"  pass  {label}")

    def skip(self, label: str, why: str):
        self.checks += 1
        self.skipped += 1
        self.skip_reasons[why] = self.skip_reasons.get(why, 0) + 1
        if self.verbose:
            print(f"  skip  {label}: {why}")

    def fail(self, label: str, where: str, repro: str, detail: str, output: str = ""):
        self.checks += 1
        self.failed += 1
        n = len(self.findings) + 1
        out_path = self.outputs / f"finding-{n:03d}.txt"
        out_path.write_text(f"{label}\n{where}\n{repro}\n{detail}\n\n--- output ---\n{output}\n")
        self.findings.append(
            {"label": label, "where": where, "repro": repro, "detail": detail, "output": str(out_path)}
        )
        print(f"  FAIL  {label}: {detail}\n        {repro}")

    def check(self, label: str, cond: bool, where: str, repro: str, detail: str, output: str = ""):
        if cond:
            self.ok(label)
        else:
            self.fail(label, where, repro, detail, output)


# ---------------------------------------------------------------------------
# (a) the generator of valid texts
# ---------------------------------------------------------------------------

IMPACT_NAMES = ["a", "b", "c", "d", "e"]


class Gen:
    def __init__(self, rng: random.Random, loops: bool):
        self.rng = rng
        self.loops = loops
        self.next_task = 1
        self.names: list[str] = []

    def task(self) -> str:
        rng = self.rng
        name = f"T{self.next_task}"
        self.next_task += 1
        dur = rng.choice([1, 1, 2, 3, 5, 9, 12])
        items = []
        for n in self.names:
            if rng.random() < 0.3:
                continue
            v = rng.choice([1, 2, 3, 5, 7, 0.5, 2.25, 10, 0.1])
            items.append(f"{n}: {v}")
        sp = rng.choice(["", " ", "  "])
        if not items and rng.random() < 0.5:
            return f"({name},{sp}{dur})"
        if not items:
            return f"({name}, {dur}, {{}})"
        return f"({name}, {dur}, {{{', '.join(items)}}})"

    def prob(self) -> str:
        return self.rng.choice(["0.1", "0.25", "0.5", "0.75", "0.9", ".3", "0.33"])

    def wrap_loop(self, body: str) -> str:
        return f"({self.prob()} {body})"

    def process(self, n: int, depth: int = 0) -> str:
        rng = self.rng
        if n == 1:
            t = self.task()
            if self.loops and rng.random() < 0.15:
                return self.wrap_loop(t)
            return t
        kind = rng.choices(["seq", "chain", "par", "choice", "nature"], weights=[3, 2, 3, 3, 3])[0]
        if kind == "chain" and n >= 3:
            k = rng.randint(3, min(n, 4))
            sizes = [1] * k
            for _ in range(n - k):
                sizes[rng.randrange(k)] += 1
            parts = [self.process(s, depth + 1) for s in sizes]
            out = ", ".join(parts)
            if depth > 0 or rng.random() < 0.5:
                out = f"({out})"
        else:
            left = rng.randint(1, n - 1)
            a = self.process(left, depth + 1)
            b = self.process(n - left, depth + 1)
            if kind in ("seq", "chain"):
                op = ","
            elif kind == "par":
                op = "||"
            elif kind == "choice":
                op = "^"
            else:
                op = f"^[{self.prob()}]"
            nl = rng.choice([" ", " ", "\n  "])
            out = f"({a}{nl}{op} {b})"
        if self.loops and rng.random() < 0.25:
            out = self.wrap_loop(out)
        return out

    def text(self, n_tasks: int, n_impacts: int) -> str:
        self.next_task = 1
        self.names = IMPACT_NAMES[:n_impacts]
        body = self.process(n_tasks)
        # every impact name in at least one task, so the dimension is as drawn
        for n in self.names:
            if f"{n}:" not in body:
                if ", {}" in body:
                    body = body.replace(", {}", f", {{{n}: 1}}", 1)
                elif ", {" in body:
                    body = body.replace(", {", f", {{{n}: 1, ", 1)
        if ":" not in body:
            # at least one impact named somewhere, or the parser refuses the text
            body = re.sub(r"\((T1),\s*(\d+)\)", r"(\1, \2, {a: 1})", body, count=1)
        if self.rng.random() < 0.3:
            body = "# a generated process\n" + body + "  # end\n"
        return body


# ---------------------------------------------------------------------------
# (b) the mutations
# ---------------------------------------------------------------------------

NUMBER = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?")
TASK_HEAD = re.compile(r"\((\w+),\s*(\d+)")
NATURE = re.compile(r"\^\[([0-9.]+)\]")
LOOP = re.compile(r"\(([0-9.]+)\s")
MAP = re.compile(r"\{[^{}]*\}")


def mutations(text: str, rng: random.Random, loops: bool) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    n = len(text)

    def at(i: int) -> int:
        return max(0, min(n - 1, i))

    for k in range(3):
        i = at(rng.randrange(n))
        out.append((f"char-deleted-{k}", text[:i] + text[i + 1 :]))
        i = at(rng.randrange(n))
        c = rng.choice("()^|,{}:.0 ab#\n@[]\"'\\")
        out.append((f"char-inserted-{k}", text[:i] + c + text[i:]))
        i = at(rng.randrange(n - 1))
        out.append((f"char-swapped-{k}", text[:i] + text[i + 1] + text[i] + text[i + 2 :]))
    parens = [i for i, c in enumerate(text) if c in "()"]
    if parens:
        i = rng.choice(parens)
        out.append(("paren-removed", text[:i] + text[i + 1 :]))
        i = rng.choice(parens)
        out.append(("paren-doubled", text[:i] + text[i] + text[i:]))
    out.append(("parens-all-removed", text.replace("(", "").replace(")", "")))
    numbers = list(NUMBER.finditer(text))
    if numbers:
        for value in ["0", "-1", "1.5", "1e9", "99999999999999999999", "1e-400", "0x10", "1_000"]:
            m = rng.choice(numbers)
            out.append((f"number-{value}", text[: m.start()] + value + text[m.end() :]))
    probs = list(NATURE.finditer(text)) + (list(LOOP.finditer(text)) if loops else [])
    if probs:
        for value in ["0", "1", "1.0", "1.5", "-0.1", "0.0000000001", "0.99999999999999999999", "abc", ""]:
            m = rng.choice(probs)
            out.append((f"prob-{value or 'empty'}", text[: m.start(1)] + value + text[m.end(1) :]))
    heads = list(TASK_HEAD.finditer(text))
    if heads:
        for value in ["0", "1000000", "2.5", "127", "128", "-3", "", "1 1"]:
            m = rng.choice(heads)
            out.append((f"duration-{value or 'empty'}", text[: m.start(2)] + value + text[m.end(2) :]))
        names = [
            ("empty", ""),
            ("spaces", "a name"),
            ("quotes", '"quoted"'),
            ("unicode", "ünïcödé"),
            ("emoji", "😀"),
            ("keyword", "loop"),
            ("keyword-task", "task"),
            ("keyword-nature", "nature"),
            ("digits", "123"),
            ("300chars", "N" * 300),
            ("at", "@saved"),
            ("hash", "a#b"),
            ("nul", "a\x00b"),
        ]
        for label, value in names:
            m = rng.choice(heads)
            out.append((f"name-{label}", text[: m.start(1)] + value + text[m.end(1) :]))
        if len(heads) >= 2:
            a, b = rng.sample(heads, 2)
            out.append((f"name-duplicate", text[: a.start(1)] + b.group(1) + text[a.end(1) :]))
    maps = list(MAP.finditer(text))
    if maps:
        for value in ["{}", "{a: 0}", "{a: -1}", "{a: 1e-9}", "{a: 1, a: 2}", "{: 1}", "{a}", "{a: }", "{a: 1,}", "{{a: 1}}", "{a: 1 b: 2}", "{" + ", ".join(f"n{i}: 1" for i in range(200)) + "}"]:
            m = rng.choice(maps)
            out.append((f"map-{value[:12]}", text[: m.start()] + value + text[m.end() :]))
    out.append(("truncated-half", text[: n // 2]))
    out.append(("doubled", text + text))
    out.append(("op-appended", text + " ||"))
    out.append(("comma-appended", text + ","))
    out.append(("trailing-garbage", text + " ) ) )"))
    out.append(("leading-garbage", "^ " + text))
    out.append(("only-comment", "# " + text.replace("\n", " ")))
    out.append(("crlf", text.replace("\n", "\r\n") + "\r\n"))
    out.append(("tabs", text.replace(" ", "\t")))
    out.append(("loop-prob-only", "(0.5)"))
    out.append(("nested-loops-direct", "(0.5 (0.5 (0.5 (A, 1, {x: 1}))))"))
    out.append(("reference-unknown", "(@nothing || (A, 1, {x: 1}))"))
    out.append(("reference-quoted", '(@"a name" , (A, 1, {x: 1}))'))
    out.append(("reference-self", "@self"))
    return out


def stress_texts(loops: bool) -> list[tuple[str, str]]:
    out = []
    t = "(A, 1, {x: 1})"
    out.append(("nesting-400", "(" * 400 + t + ")" * 400))
    out.append(("nesting-1999", "(" * 1999 + t + ")" * 1999))
    out.append(("nesting-2001", "(" * 2001 + t + ")" * 2001))
    out.append(("nesting-100000", "(" * 100000 + t + ")" * 100000))
    out.append(("sequence-1500", ", ".join(f"(T{i}, 1, {{x: 1}})" for i in range(1500))))
    par = "(T0, 1, {x: 1})"
    for i in range(1, 60):
        par = f"({par} || (T{i}, 1, {{x: 1}}))"
    out.append(("parallel-60", par))
    ch = "(T0, 1, {x: 1})"
    for i in range(1, 40):
        ch = f"({ch} ^ (T{i}, 1, {{x: {i}}}))"
    out.append(("choice-chain-40", ch))
    if loops:
        lp = "(A, 1, {x: 1})"
        for _ in range(30):
            lp = f"(0.5 {lp})"
        out.append(("loops-nested-30", lp))
        out.append(("loop-over-sequence-200", "(0.9 " + ", ".join(f"(T{i}, 1, {{x: 1}})" for i in range(200)) + ")"))
    out.append(("empty", ""))
    out.append(("whitespace", "   \n\t  "))
    out.append(("one-paren", "("))
    out.append(("binary-noise", bytes(range(256)).decode("latin-1")))
    out.append(("long-line-1MB", "(A, 1, {x: 1})" + " " * 1_000_000))
    out.append(("deep-map-names", "(A, 1, {" + ", ".join(f"{'n' * 50}{i}: {i + 1}" for i in range(2000)) + "})"))
    return out


# ---------------------------------------------------------------------------
# (c) the mutated instance files
# ---------------------------------------------------------------------------


def yaml_mutations(y: str, loops: bool) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    lines = y.split("\n")

    def sub_first(pattern: str, repl: str, label: str):
        if re.search(pattern, y, flags=re.M):
            out.append((label, re.sub(pattern, repl, y, count=1, flags=re.M)))

    def drop_line(pattern: str, label: str):
        for i, ln in enumerate(lines):
            if re.search(pattern, ln):
                out.append((label, "\n".join(lines[:i] + lines[i + 1 :])))
                return

    for kind in ["sequence", "parallel", "choice", "nature", "loop"]:
        sub_first(rf"^(\s*- ){kind}:", r"\1bogus:", f"kind-{kind}-bogus")
    sub_first(r"^(\s*- )task: (\S+)", r"\1loop: \2", "kind-task-to-loop")
    sub_first(r"^(\s*- )task: (\S+)", r"\1sequence: \2", "kind-task-to-sequence")
    sub_first(r"^(\s*- )task:", r"\1task:", "kind-task-unnamed")
    sub_first(r"^(\s*- )(sequence|parallel|choice):", r"\1loop: 0.5", "kind-binary-to-loop")
    sub_first(r"^(\s*- )(sequence|parallel|choice):", r"\1task: X", "kind-binary-to-task")
    sub_first(r"^(\s*- )nature: [0-9.]+", r"\1sequence:", "kind-nature-to-sequence")
    sub_first(r"^(\s*- )loop: [0-9.]+", r"\1nature: 0.5", "kind-loop-to-nature")
    sub_first(r"^(\s*- )loop: [0-9.]+", r"\1sequence:", "kind-loop-to-sequence")
    # identifiers
    out.append(("id-all-one", re.sub(r"^(\s*id: )\d+", r"\g<1>1", y, flags=re.M)))
    sub_first(r"^(\s*id: )\d+", r"\g<1>0", "id-zero")
    sub_first(r"^(\s*id: )\d+", r"\g<1>-1", "id-negative")
    sub_first(r"^(\s*id: )\d+", r"\g<1>99999", "id-huge")
    sub_first(r"^(\s*id: )\d+", r"\g<1>4294967296", "id-2^32")
    sub_first(r"^(\s*id: )\d+", r"\g<1>abc", "id-abc")
    sub_first(r"^(\s*id: )\d+", r"\g<1>1.5", "id-fraction")
    drop_line(r"^\s*id: ", "id-missing")
    ids = re.findall(r"^\s*id: (\d+)", y, flags=re.M)
    if len(ids) >= 2:
        # the child carrying the identifier of its parent: a cycle of ids
        root = ids[0]
        i = y.index(f"id: {ids[1]}")
        out.append(("id-child-equals-parent", y[:i] + f"id: {root}" + y[i + len(f"id: {ids[1]}") :]))
        out.append(("id-reversed", re.sub(r"^(\s*id: )(\d+)", lambda m: m.group(1) + str(len(ids) + 1 - int(m.group(2))), y, flags=re.M)))
    # impacts
    sub_first(r"impact: \[(\d)", r"impact: [-\1", "impact-negative")
    sub_first(r"impact: \[", r"impact: [99, ", "impact-too-long")
    sub_first(r"impact: \[[^\]]*\]", r"impact: []", "impact-empty")
    sub_first(r"impact: \[[^\]]*\]", r"impact: abc", "impact-abc")
    sub_first(r"impact: \[[^\]]*\]", r"impact: [1e400]", "impact-1e400")
    sub_first(r"impact: \[[^\]]*\]", r"impact: [nan]", "impact-nan")
    sub_first(r"impact: \[[^\]]*\]", r"impact: [inf]", "impact-inf")
    sub_first(r"impact: \[[^\]]*\]", r"impact: [99999999999999999999999999]", "impact-huge")
    drop_line(r"^\s*impact: ", "impact-missing")
    # durations
    sub_first(r"duration: \d+", "duration: 0", "duration-zero")
    sub_first(r"duration: \d+", "duration: -2", "duration-negative")
    sub_first(r"duration: \d+", "duration: 128", "duration-128")
    sub_first(r"duration: \d+", "duration: 99999999999", "duration-huge")
    sub_first(r"duration: \d+", "duration: 1.5", "duration-fraction")
    drop_line(r"^\s*duration: ", "duration-missing")
    # branches and structure
    drop_line(r"^\s*low:", "low-missing")
    drop_line(r"^\s*high:", "high-missing")
    drop_line(r"^tree:", "tree-missing")
    drop_line(r"^dimensions:", "dimensions-missing")
    drop_line(r"^impact_names:", "impact-names-missing")
    out.append(("tree-empty", y[: y.index("tree:") + 5] + "\n"))
    out.append(("truncated", y[: len(y) // 2]))
    out.append(("doubled-tree", y + y[y.index("tree:") + 6 :]))
    out.append(("indent-flat", re.sub(r"^ +", "", y, flags=re.M)))
    out.append(("indent-tabs", y.replace("  ", "\t")))
    out.append(("crlf", y.replace("\n", "\r\n")))
    out.append(("bom", "﻿" + y))
    # max_iter
    out.append(("max-iter-0-header", y.replace("tree:", "max_iter: 0\ntree:", 1)))
    sub_first(r"^(\s*)(id: \d+)", r"\1\2\n\1max_iter: 0", "max-iter-0-node")
    sub_first(r"^(\s*- (?:loop|nature): )[0-9.]+", r"\g<1>0", "prob-zero")
    sub_first(r"^(\s*- (?:loop|nature): )[0-9.]+", r"\g<1>1", "prob-one")
    sub_first(r"^(\s*- (?:loop|nature): )[0-9.]+", r"\g<1>1.5", "prob-1.5")
    sub_first(r"^(\s*- (?:loop|nature): )[0-9.]+", r"\g<1>-0.1", "prob-negative")
    sub_first(r"^(\s*- (?:loop|nature): )[0-9.]+", r"\g<1>abc", "prob-abc")
    sub_first(r"^(\s*- (?:loop|nature): )[0-9.]+", r"\g<1>1e-400", "prob-1e-400")
    sub_first(r"^(\s*- (?:loop|nature): )[0-9.]+", r"\g<1>0.1e0", "prob-exponent")
    sub_first(r"^(\s*- (?:loop|nature): )[0-9.]+", r"\g<1>", "prob-empty")
    # header counts
    sub_first(r"^nodes: \d+", "nodes: 0", "nodes-zero")
    sub_first(r"^nodes: \d+", "nodes: 50000000", "nodes-50M")
    sub_first(r"^nodes: \d+", "nodes: -1", "nodes-negative")
    sub_first(r"^nodes: \d+", "nodes: 99999999999999999999", "nodes-20digits")
    sub_first(r"^tasks: \d+", "tasks: 0", "tasks-zero")
    sub_first(r"^tasks: \d+", "tasks: 4294967295", "tasks-2^32")
    sub_first(r"^dimensions: \d+", "dimensions: 0", "dimensions-zero")
    sub_first(r"^dimensions: \d+", "dimensions: 1000", "dimensions-1000")
    sub_first(r"^impact_names: \[[^\]]*\]", "impact_names: [time]", "impact-named-time")
    sub_first(r"^impact_names: \[[^\]]*\]", "impact_names: [a, a]", "impact-names-duplicate")
    sub_first(r"^impact_names: \[[^\]]*\]", "impact_names: []", "impact-names-empty")
    out.append(("header-unknown-key", "bogus: 1\n" + y))
    out.append(("file-empty", ""))
    out.append(("file-garbage", bytes(range(256)).decode("latin-1") * 4))
    out.append(("file-noise-1MB", "x" * 1_000_000))
    out.append(("file-one-line", y.replace("\n", " ")))
    return out


def yaml_heavy(y: str) -> list[tuple[str, str]]:
    out = []
    out.append(("heavy-nodes-2^32", re.sub(r"^nodes: \d+", "nodes: 4294967295", y, count=1, flags=re.M)))
    out.append(("heavy-dimensions-4e8", re.sub(r"^dimensions: \d+", "dimensions: 400000000", y, count=1, flags=re.M)))
    out.append(("heavy-dimensions-2^32", re.sub(r"^dimensions: \d+", "dimensions: 4294967295", y, count=1, flags=re.M)))
    return out


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


class Run:
    def __init__(self, argv: list[str], code: int | None, out: str, err: str, timed_out: bool, seconds: float):
        self.argv = argv
        self.code = code
        self.out = out
        self.err = err
        self.timed_out = timed_out
        self.seconds = seconds

    @property
    def text(self) -> str:
        return self.out + "\n" + self.err

    def cmd(self) -> str:
        return " ".join(shell_quote(a) for a in self.argv)


def shell_quote(a: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./=:,-]+", a):
        return a
    return "'" + a.replace("'", "'\\''") + "'"


def run_cli(argv: list[str], timeout: float, cwd: Path) -> Run:
    t = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, cwd=cwd)
        return Run(argv, r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), False, time.time() - t)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        err = (e.stderr or b"").decode("utf-8", "replace")
        return Run(argv, None, out, err, True, time.time() - t)


def judge(ledger: Ledger, label: str, run: Run, input_path: Path, out_file: Path | None = None) -> str:
    """Applies the contract; returns "accepted", "refused" or "failed"."""
    where = Path(run.argv[0]).name + " " + run.argv[1]
    repro = f"{run.cmd()}   (input {input_path})"
    if run.timed_out:
        ledger.fail(label, where, repro, f"timed out after {run.seconds:.0f}s", run.text)
        return "failed"
    if any(w in run.text for w in BAD_WORDS):
        ledger.fail(label, where, repro, f"the output carries a panic or an overflow (exit {run.code})", run.text)
        return "failed"
    if run.code is not None and run.code < 0:
        ledger.fail(label, where, repro, f"killed by signal {-run.code} ({signal.Signals(-run.code).name})", run.text)
        return "failed"
    if run.code == 0:
        has_output = bool(run.out.strip()) or (out_file is not None and out_file.exists() and out_file.stat().st_size > 0)
        if not has_output:
            ledger.fail(label, where, repro, "exit 0 with no output", run.text)
            return "failed"
        ledger.ok(label)
        return "accepted"
    if run.code in (1, 2):
        err_lines = [ln for ln in run.err.splitlines() if ln.strip()]
        if not err_lines:
            ledger.fail(label, where, repro, f"exit {run.code} with an empty standard error", run.text)
            return "failed"
        if len(err_lines) > 1:
            ledger.fail(label, where, repro, f"exit {run.code} with {len(err_lines)} lines on standard error", run.text)
            return "failed"
        ledger.ok(label)
        return "refused"
    ledger.fail(label, where, repro, f"exit code {run.code}, neither 0 nor a clean refusal", run.text)
    return "failed"


VEC = re.compile(r"\[([^\]]*)\]")


def vec_of(line: str) -> list[float] | None:
    m = VEC.search(line)
    if not m:
        return None
    try:
        return [float(x) for x in m.group(1).split(",") if x.strip()]
    except ValueError:
        return None


def exact_of(line: str):
    """The rationals of an `exact [a, b/c, ...]` line, as Fractions."""
    from fractions import Fraction
    m = VEC.search(line)
    if not m:
        return None
    try:
        return [Fraction(x.strip()) for x in m.group(1).split(",") if x.strip()]
    except (ValueError, ZeroDivisionError):
        return None


def rows(out: str) -> dict[str, str]:
    """`key   value` lines, the key up to the first run of two spaces or a bracket."""
    d: dict[str, str] = {}
    for ln in out.splitlines():
        # `worst L-min 0e0  over 1 choice states`: one space before the number
        m = re.match(r"^(worst L-min|worst max-U)\s+(\S.*)$", ln)
        if m:
            d.setdefault(m.group(1), m.group(2).strip())
            continue
        m = re.match(r"^\s*([A-Za-z][A-Za-z0-9 _-]*?)\s{2,}(.*)$", ln)
        if m:
            d.setdefault(m.group(1).strip(), m.group(2).strip())
    return d


class Cli:
    def __init__(self, args, ledger: Ledger, scratch: Path):
        self.bin = args.bin
        self.loops = not args.no_loops
        self.ledger = ledger
        self.timeout = args.timeout
        self.inputs = scratch / "inputs"
        self.inputs.mkdir(parents=True, exist_ok=True)
        self.work = scratch / "work"
        self.work.mkdir(parents=True, exist_ok=True)
        self.accepted_texts: list[tuple[str, str, Path]] = []  # label, text, yaml

    def save(self, name: str, content: str, suffix: str) -> Path:
        p = self.inputs / f"{name}{suffix}"
        p.write_bytes(content.encode("utf-8", "surrogateescape") if "\x00" not in content else content.encode("utf-8", "replace"))
        return p

    def parse_text(self, label: str, text: str, big: bool = False) -> Path | None:
        """`parse --file F --out F.yaml`; returns the yaml when accepted."""
        src = self.save(label, text, ".cpi")
        out = self.work / f"{label}.yaml"
        if out.exists():
            out.unlink()
        run = run_cli([self.bin, "parse", "--file", str(src), "--out", str(out)], self.timeout, self.work)
        verdict = judge(self.ledger, f"parse {label}", run, src, out)
        if verdict != "accepted":
            return None
        if not out.exists():
            self.ledger.fail(f"parse {label} wrote", "sdcpi parse", run.cmd(), "exit 0 but no --out file", run.text)
            return None
        return out

    def parse_inline(self, label: str, text: str) -> None:
        """The same text given inline on the command line, where the shell
        hands it over as one argument."""
        if "\x00" in text or len(text) > 100_000:
            return
        src = self.save(label + "-inline", text, ".cpi")
        run = run_cli([self.bin, "parse", text], self.timeout, self.work)
        judge(self.ledger, f"parse-inline {label}", run, src)

    def exercise(self, label: str, yaml: Path, big: bool = False) -> None:
        """Every command reading an accepted instance, with the consistencies."""
        L = self.ledger
        B = self.bin
        T = self.timeout
        r_info = run_cli([B, "info", str(yaml)], T, self.work)
        v = judge(L, f"info {label}", r_info, yaml)
        L.check(f"parse-readable {label}", v != "refused", "sdcpi parse | info", r_info.cmd(), "parse accepted the text and wrote this instance, which the reader refuses: " + r_info.err.strip()[:200], r_info.text)
        if v != "accepted":
            return

        r_bound = run_cli([B, "bound", str(yaml)], T, self.work)
        v = judge(L, f"bound {label}", r_bound, yaml)
        upper = lower = None
        if r_bound.timed_out:
            # the instance is beyond the tool at this size: one timeout is the
            # finding, the searches would only repeat it
            for c in ["bound-time", "bound-max-time", "bound-inf", "pareto", "determine", "optima"]:
                L.skip(f"{c} {label}", "bound timed out on this instance")
            for c in ["to_prism", "to_svg", "to_text"]:
                r = run_cli([B, c, str(yaml)], T, self.work)
                judge(L, f"{c} {label}", r, yaml)
            return
        if v == "accepted":
            d = rows(r_bound.out)
            upper, lower = vec_of(d.get("U at s0", "")), vec_of(d.get("L at s0", ""))
            self.bound_consistency(f"bound {label}", r_bound, yaml, d)

        r_bt = run_cli([B, "bound", str(yaml), "--time", "1"], T, self.work)
        v = judge(L, f"bound-time {label}", r_bt, yaml)
        if v == "accepted":
            d = rows(r_bt.out)
            self.bound_consistency(f"bound-time {label}", r_bt, yaml, d)
            u = vec_of(d.get("U at s0", ""))
            if upper is not None and u is not None:
                L.check(f"bound-time-width {label}", len(u) == len(upper) + 1, "sdcpi bound --time 1", r_bt.cmd(), f"with --time 1 the bound has {len(u)} components against {len(upper)} without", r_bt.text)

        r_bm = run_cli([B, "bound", str(yaml), "--time", "1", "--maximize", "time"], T, self.work)
        v = judge(L, f"bound-max-time {label}", r_bm, yaml)
        if v == "accepted":
            self.bound_consistency(f"bound-max-time {label}", r_bm, yaml, rows(r_bm.out), maximized=[0])

        if self.loops:
            r_bi = run_cli([B, "bound", str(yaml), "--max-iter", "inf", "--time", "1"], T, self.work)
            judge(L, f"bound-inf {label}", r_bi, yaml)
            for k in ["0", "128", "-1", "abc", "1e2", "inf"]:
                r_k = run_cli([B, "info", str(yaml), "--max-iter", k], T, self.work)
                judge(L, f"max-iter-{k} {label}", r_k, yaml)

        r_par = run_cli([B, "pareto", str(yaml), "--onephase", "1", "--time", "1", "--time-limit", "2", "--workers", "1"], T, self.work)
        v = judge(L, f"pareto {label}", r_par, yaml)
        if v == "accepted":
            self.pareto_consistency(f"pareto {label}", r_par, yaml)

        if upper is not None and lower is not None and len(upper) == len(lower) and upper:
            # the budget of the upper bound fits every strategy: the answer is yes
            b_up = ",".join(f"{x * (1 + EPS) + 1e-5:.9f}" for x in upper)
            r_det = run_cli([B, "determine", str(yaml), "--B", b_up, "--timeout", "10", "--print-size", "1"], T, self.work)
            v = judge(L, f"determine-upper {label}", r_det, yaml)
            if v == "accepted":
                ans = rows(r_det.out).get("answer", "")
                if ans == "timeout":
                    L.skip(f"determine-upper-yes {label}", "the search timed out")
                else:
                    L.check(f"determine-upper-yes {label}", ans == "yes", "sdcpi determine", r_det.cmd(), f"the budget is the upper bound at s0 and the answer is {ans!r}, not yes", r_det.text)
            # a budget under the lower bound in one component fits no strategy
            i = max(range(len(lower)), key=lambda j: lower[j])
            b_low = ",".join(f"{lower[j] * (1 - 1e-6) - 1e-3:.9f}" if j == i else f"{upper[j] * (1 + EPS) + 1e-5:.9f}" for j in range(len(lower)))
            r_det2 = run_cli([B, "determine", str(yaml), "--B", b_low, "--timeout", "10"], T, self.work)
            v = judge(L, f"determine-lower {label}", r_det2, yaml)
            if v == "accepted":
                ans = rows(r_det2.out).get("answer", "")
                if ans == "timeout":
                    L.skip(f"determine-lower-no {label}", "the search timed out")
                else:
                    L.check(f"determine-lower-no {label}", ans == "no", "sdcpi determine", r_det2.cmd(), f"the budget is under the lower bound at s0 in component {i} and the answer is {ans!r}, not no", r_det2.text)
            # the named form of the budget and the exact arithmetic
            names = self.impact_names(yaml)
            if names and len(names) == len(upper):
                named = "{" + ", ".join(f"{n}: {x * (1 + EPS) + 1e-5:.9f}" for n, x in zip(names, upper)) + "}"
                r_det3 = run_cli([B, "determine", str(yaml), "--B", named, "--arith", "exact", "--timeout", "10", "--print-strategy", "1"], T, self.work)
                judge(L, f"determine-named-exact {label}", r_det3, yaml)
            r_obj = run_cli([B, "to_objective", str(yaml), "--B", b_up], T, self.work)
            judge(L, f"to_objective {label}", r_obj, yaml)
        else:
            L.skip(f"determine {label}", "no bounds to build the budget from")

        r_prism = run_cli([B, "to_prism", str(yaml)], T, self.work)
        judge(L, f"to_prism {label}", r_prism, yaml)
        r_prism_t = run_cli([B, "to_prism", str(yaml), "--time", "1", "--encode-history", "false"], T, self.work)
        judge(L, f"to_prism-time {label}", r_prism_t, yaml)
        r_svg = run_cli([B, "to_svg", str(yaml)], T, self.work)
        v = judge(L, f"to_svg {label}", r_svg, yaml)
        if v == "accepted":
            L.check(f"to_svg-shape {label}", r_svg.out.lstrip().startswith("<svg") and r_svg.out.rstrip().endswith("</svg>"), "sdcpi to_svg", r_svg.cmd(), "the output is not one <svg> element", r_svg.out[:500])
        r_svg2 = run_cli([B, "to_svg", str(yaml), "--detail", "true", "--tooltips", "true", "--clocks", "true", "--names", "false", "--bars", "all"], T, self.work)
        judge(L, f"to_svg-options {label}", r_svg2, yaml)
        r_text = run_cli([B, "to_text", str(yaml)], T, self.work)
        v = judge(L, f"to_text {label}", r_text, yaml)
        if v == "accepted" and upper is not None:
            self.round_trip(label, r_text, yaml, upper, lower)
        r_opt = run_cli([B, "optima", str(yaml), "--max-states", "20000"], T, self.work)
        judge(L, f"optima {label}", r_opt, yaml)

    def impact_names(self, yaml: Path) -> list[str]:
        m = re.search(r"^impact_names: \[([^\]]*)\]", yaml.read_text(errors="replace"), flags=re.M)
        if not m:
            return []
        return [x.strip() for x in m.group(1).split(",") if x.strip()]

    def bound_consistency(self, label: str, run: Run, yaml: Path, d: dict[str, str], maximized: list[int] = ()):
        L = self.ledger
        for key in ("worst L-min", "worst max-U"):
            raw = d.get(key, "").split()[0] if d.get(key) else ""
            try:
                val = float(raw)
            except ValueError:
                L.skip(f"{label} {key}", f"no number in {d.get(key)!r}")
                continue
            L.check(f"{label} {key}", val <= 1e-9, "sdcpi bound", run.cmd(), f"{key} is {raw}: the bound is breached at a reachable choice state", run.text)
        u, l = vec_of(d.get("U at s0", "")), vec_of(d.get("L at s0", ""))
        mn, mx = vec_of(d.get("exact min", "")), vec_of(d.get("exact max", ""))
        if None in (u, l, mn, mx) or not (len(u) == len(l) == len(mn) == len(mx)):
            L.skip(f"{label} box", "bound did not print the four vectors")
            return
        ok = True
        for c in range(len(u)):
            # the shown orientation: L <= U in every component, a maximized
            # one reading the bounds of its negation the other way round
            lo, hi = min(l[c], u[c]), max(l[c], u[c])
            tol = 1e-6 * max(1.0, abs(hi))
            if not (lo - tol <= mn[c] <= mx[c] + tol <= hi + tol):
                ok = False
        L.check(f"{label} box", ok, "sdcpi bound", run.cmd(), f"the exact optima min={mn} max={mx} do not lie between L={l} and U={u}", run.text)

    def pareto_consistency(self, label: str, run: Run, yaml: Path):
        L = self.ledger
        d = rows(run.out)
        u, l = vec_of(d.get("U at s0", "")), vec_of(d.get("L at s0", ""))
        status = d.get("status", "")
        L.check(f"{label} status", status in ("complete", "cut at the limit", "timeout") or status.startswith("cut"), "sdcpi pareto", run.cmd(), f"status {status!r}", run.text)
        L.check(f"{label} limit", run.seconds < 20, "sdcpi pareto", run.cmd(), f"--time-limit 2 but the run took {run.seconds:.1f}s", run.text)
        points = [vec_of(ln) for ln in run.out.splitlines() if re.match(r"^point \d+ ", ln)]
        exact = [exact_of(ln) for ln in run.out.splitlines() if re.match(r"^\s+exact\s+\[", ln)]
        if u is None or l is None or not points:
            L.skip(f"{label} box", "no points or no bounds printed")
            return
        def tol(c):
            return 1e-6 * max(1.0, abs(u[c]), abs(l[c]))
        bad = [p for p in points if p is None or len(p) != len(u) or any(not (min(l[c], u[c]) - tol(c) <= p[c] <= max(l[c], u[c]) + tol(c)) for c in range(len(u)))]
        L.check(f"{label} box", not bad, "sdcpi pareto", run.cmd(), f"{len(bad)} points lie outside [L, U]: {bad[:3]} against L={l} U={u}", run.text)
        # a frontier is an antichain: no point dominates another, read on the
        # exact rationals when they are printed, on the six decimals otherwise
        vs = exact if len(exact) == len(points) and all(e is not None for e in exact) else points
        dom = False
        for p in vs:
            for q in vs:
                if p is not q and len(p) == len(q) and all(p[c] <= q[c] for c in range(len(p))) and any(p[c] < q[c] for c in range(len(p))):
                    dom = True
        L.check(f"{label} antichain", not dom, "sdcpi pareto", run.cmd(), "a point of the frontier dominates another (read on the exact rationals)", run.text)

    def round_trip(self, label: str, r_text: Run, yaml: Path, upper, lower):
        L = self.ledger
        src = self.save(f"{label}-roundtrip", r_text.out, ".cpi")
        out = self.work / f"{label}-roundtrip.yaml"
        r = run_cli([self.bin, "parse", "--file", str(src), "--out", str(out)], self.timeout, self.work)
        if judge(L, f"roundtrip-parse {label}", r, src, out) != "accepted":
            return
        rb = run_cli([self.bin, "bound", str(out)], self.timeout, self.work)
        if judge(L, f"roundtrip-bound {label}", rb, out) != "accepted":
            return
        d = rows(rb.out)
        u2, l2 = vec_of(d.get("U at s0", "")), vec_of(d.get("L at s0", ""))
        same = u2 is not None and l2 is not None and len(u2) == len(upper) and all(abs(a - b) < 1e-6 for a, b in zip(u2, upper)) and all(abs(a - b) < 1e-6 for a, b in zip(l2, lower))
        L.check(f"roundtrip-same {label}", same, "sdcpi to_text | parse | bound", f"{r_text.cmd()} ; {r.cmd()} ; {rb.cmd()}", f"the bounds differ after to_text and parse: U {upper} -> {u2}, L {lower} -> {l2}", r_text.out + "\n" + rb.out)

    def exercise_file(self, label: str, content: str, heavy: bool = False) -> None:
        p = self.save(label, content, ".yaml")
        timeout = 20 if heavy else self.timeout
        r = run_cli([self.bin, "info", str(p)], timeout, self.work)
        v = judge(self.ledger, f"info {label}", r, p)
        if v == "accepted":
            r2 = run_cli([self.bin, "bound", str(p), "--time", "1"], timeout, self.work)
            v2 = judge(self.ledger, f"bound {label}", r2, p)
            if v2 == "accepted":
                self.bound_consistency(f"bound {label}", r2, p, rows(r2.out))
            r3 = run_cli([self.bin, "to_text", str(p)], timeout, self.work)
            judge(self.ledger, f"to_text {label}", r3, p)
            r4 = run_cli([self.bin, "pareto", str(p), "--onephase", "1", "--time-limit", "2", "--workers", "1"], timeout, self.work)
            judge(self.ledger, f"pareto {label}", r4, p)

    def bad_arguments(self, yaml: Path) -> None:
        """Options out of range on a good instance."""
        B = self.bin
        cases = [
            ("no-args", []),
            ("unknown-command", ["frobnicate"]),
            ("info-missing-file", ["info", str(self.work / "does-not-exist.yaml")]),
            ("info-directory", ["info", str(self.work)]),
            ("determine-no-budget", ["determine", str(yaml)]),
            ("determine-B-empty", ["determine", str(yaml), "--B", ""]),
            ("determine-B-abc", ["determine", str(yaml), "--B", "abc"]),
            ("determine-B-negative", ["determine", str(yaml), "--B", "-1,-1,-1,-1,-1,-1"]),
            ("determine-B-nan", ["determine", str(yaml), "--B", "nan"]),
            ("determine-B-inf", ["determine", str(yaml), "--B", "inf"]),
            ("determine-B-1e400", ["determine", str(yaml), "--B", "1e400"]),
            ("determine-B-named-bad", ["determine", str(yaml), "--B", "{zz: 1}"]),
            ("determine-B-named-broken", ["determine", str(yaml), "--B", "{a: }"]),
            ("determine-B-too-many", ["determine", str(yaml), "--B", ",".join(["1"] * 50)]),
            ("determine-workers-0", ["determine", str(yaml), "--B", "1000,1000,1000,1000,1000", "--workers", "0", "--timeout", "5"]),
            ("determine-workers-neg", ["determine", str(yaml), "--B", "1000", "--workers", "-1"]),
            ("determine-ablation-bad", ["determine", str(yaml), "--B", "1000", "--ablation", "sometimes"]),
            ("determine-epsilon-neg", ["determine", str(yaml), "--B", "1000", "--epsilon", "-1", "--timeout", "5"]),
            ("determine-timeout-neg", ["determine", str(yaml), "--B", "1000", "--timeout", "-1"]),
            ("determine-timeout-0", ["determine", str(yaml), "--B", "1000", "--timeout", "0"]),
            ("determine-seed-huge", ["determine", str(yaml), "--B", "1000", "--seed", "99999999999999999999", "--timeout", "5"]),
            ("determine-B-file-missing", ["determine", str(yaml), "--B-file", str(self.work / "nope.yaml")]),
            ("pareto-window-bad", ["pareto", str(yaml), "--onephase", "1", "--window", "a..b", "--time-limit", "2"]),
            ("pareto-window-50", ["pareto", str(yaml), "--onephase", "1", "--window", ",".join(["0..1"] * 50), "--time-limit", "2"]),
            ("pareto-window-reversed", ["pareto", str(yaml), "--onephase", "1", "--window", "5..1", "--time-limit", "2"]),
            ("pareto-window-fraction", ["pareto", str(yaml), "--onephase", "1", "--window", "1/3..7/2", "--time-limit", "2"]),
            ("pareto-window-zero-denominator", ["pareto", str(yaml), "--onephase", "1", "--window", "1/0..2", "--time-limit", "2"]),
            ("pareto-components-bad", ["pareto", str(yaml), "--onephase", "1", "--components", "99", "--time-limit", "2"]),
            ("pareto-components-empty", ["pareto", str(yaml), "--onephase", "1", "--components", "", "--time-limit", "2"]),
            ("pareto-time-limit-0", ["pareto", str(yaml), "--onephase", "1", "--time-limit", "0"]),
            ("pareto-time-limit-neg", ["pareto", str(yaml), "--onephase", "1", "--time-limit", "-5"]),
            ("pareto-time-limit-abc", ["pareto", str(yaml), "--onephase", "1", "--time-limit", "abc"]),
            ("pareto-precision-0", ["pareto", str(yaml), "--precision", "0", "--time-limit", "2"]),
            ("pareto-precision-neg", ["pareto", str(yaml), "--precision", "-1", "--time-limit", "2"]),
            ("pareto-twophase", ["pareto", str(yaml), "--time-limit", "2", "--workers", "1"]),
            ("pareto-ablation-none", ["pareto", str(yaml), "--onephase", "1", "--ablation", "none", "--time-limit", "2"]),
            ("pareto-ablation-accept", ["pareto", str(yaml), "--onephase", "1", "--ablation", "accept", "--time-limit", "2"]),
            ("pareto-ablation-reject", ["pareto", str(yaml), "--onephase", "1", "--ablation", "reject", "--time-limit", "2"]),
            ("pareto-oracle-obdd", ["pareto", str(yaml), "--onephase", "1", "--oracle", "obdd", "--time-limit", "2"]),
            ("pareto-oracle-bad", ["pareto", str(yaml), "--onephase", "1", "--oracle", "crystal", "--time-limit", "2"]),
            ("pareto-json-dir", ["pareto", str(yaml), "--onephase", "1", "--json", str(self.work), "--time-limit", "2"]),
            ("pareto-anytime-dir", ["pareto", str(yaml), "--onephase", "1", "--anytime", str(self.work), "--time-limit", "2"]),
            ("pareto-workers-2", ["pareto", str(yaml), "--onephase", "1", "--workers", "2", "--time-limit", "2"]),
            ("pareto-maximize-all-time", ["pareto", str(yaml), "--onephase", "1", "--time", "1", "--maximize", "time,0,1,2,3,4", "--time-limit", "2"]),
            ("maximize-unknown", ["bound", str(yaml), "--maximize", "nothing"]),
            ("maximize-time-without-time", ["bound", str(yaml), "--maximize", "time"]),
            ("maximize-index-out", ["bound", str(yaml), "--maximize", "99"]),
            ("maximize-negative-index", ["bound", str(yaml), "--maximize", "-1"]),
            ("maximize-empty", ["bound", str(yaml), "--maximize", ""]),
            ("maximize-twice", ["bound", str(yaml), "--maximize", "0,0"]),
            ("maximize-1000", ["bound", str(yaml), "--maximize", ",".join(f"n{i}" for i in range(1000))]),
            ("time-abc", ["bound", str(yaml), "--time", "abc"]),
            ("time-2", ["bound", str(yaml), "--time", "2"]),
            ("time-0", ["bound", str(yaml), "--time", "0"]),
            ("flag-without-value", ["bound", str(yaml), "--time"]),
            ("unknown-flag", ["bound", str(yaml), "--frob", "1"]),
            ("to_prism-encode-bad", ["to_prism", str(yaml), "--encode-history", "maybe"]),
            ("to_prism-out-dir", ["to_prism", str(yaml), "--out", str(self.work)]),
            ("to_objective-pareto", ["to_objective", str(yaml), "--pareto", "true"]),
            ("to_objective-pareto-and-B", ["to_objective", str(yaml), "--pareto", "true", "--B", "1"]),
            ("to_svg-bars-bad", ["to_svg", str(yaml), "--bars", "zz"]),
            ("to_svg-detail-bad", ["to_svg", str(yaml), "--detail", "maybe"]),
            ("to_text-regenerate", ["to_text", str(yaml), "--regenerate", "shuffle", "--impacts", "2", "--seed", "1"]),
            ("to_text-regenerate-bad", ["to_text", str(yaml), "--regenerate", "zz"]),
            ("to_text-impacts-0", ["to_text", str(yaml), "--regenerate", "shuffle", "--impacts", "0"]),
            ("optima-max-states-0", ["optima", str(yaml), "--max-states", "0"]),
            ("optima-max-states-neg", ["optima", str(yaml), "--max-states", "-1"]),
            ("parse-file-missing", ["parse", "--file", str(self.work / "nope.cpi")]),
            ("parse-file-and-text", ["parse", "(A, 1, {x: 1})", "--file", str(self.work / "nope.cpi")]),
            ("parse-out-dir", ["parse", "(A, 1, {x: 1})", "--out", str(self.work)]),
            ("parse-saved-missing", ["parse", "(@x || (A, 1, {x: 1}))", "--saved", str(self.work / "nope.json")]),
            ("parse-no-text", ["parse"]),
            ("serve-port-bad", ["serve", "--port", "abc"]),
            ("serve-port-neg", ["serve", "--port", "-1"]),
            ("serve-port-huge", ["serve", "--port", "70000"]),
            ("test-unknown-suite", ["test", "no-such-suite-anywhere"]),
        ]
        if self.loops:
            cases += [
                ("max-iter-inf-pareto", ["pareto", str(yaml), "--onephase", "1", "--max-iter", "inf", "--time-limit", "2"]),
                ("max-iter-inf-determine", ["determine", str(yaml), "--B", "1000", "--max-iter", "inf"]),
                ("max-iter-inf-prism", ["to_prism", str(yaml), "--max-iter", "inf"]),
                # the exact pass of bound grows with the product of the counts, so
                # the largest bound is probed on the recursion alone
                ("max-iter-127", ["bound", str(yaml), "--max-iter", "127", "--no-exact", "1"]),
                ("max-iter-127-pareto", ["pareto", str(yaml), "--onephase", "1", "--max-iter", "127", "--time-limit", "2", "--time", "1"]),
                ("max-iter-huge", ["bound", str(yaml), "--max-iter", "99999999999999999999"]),
                ("max-iter-empty", ["bound", str(yaml), "--max-iter", ""]),
            ]
        for label, argv in cases:
            r = run_cli([B] + argv, self.timeout, self.work)
            if label == "no-args" or label == "unknown-command" or label.startswith("parse-no-text") or label == "test-unknown-suite":
                # the usage text is many lines by design; the rule is no panic and a nonzero exit
                where = "sdcpi " + " ".join(argv)
                bad = r.timed_out or any(w in r.text for w in BAD_WORDS) or (r.code is not None and r.code < 0)
                self.ledger.check(f"args {label}", not bad and (r.code == 0 or (r.code in (1, 2) and r.err.strip())), where, r.cmd(), f"exit {r.code}, timed out {r.timed_out}", r.text)
                continue
            judge(self.ledger, f"args {label}", r, yaml)


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class Page:
    def __init__(self, args, ledger: Ledger, scratch: Path):
        self.bin = args.bin
        self.ledger = ledger
        self.scratch = scratch
        self.port = args.port
        while not port_free(self.port):
            self.port += 1
        self.store = scratch / "fuzz-store.json"
        self.log = scratch / "server.log"
        self.proc: subprocess.Popen | None = None
        self.bench = Path(args.bench) if args.bench else DEFAULT_BENCH
        self.bodies = scratch / "inputs"
        self.n = 0
        self.restarts = 0

    def start(self) -> bool:
        argv = [self.bin, "serve", "--port", str(self.port), "--saved", str(self.store)]
        if self.bench.is_dir():
            argv += ["--bench", str(self.bench)]
        self.logf = open(self.log, "wb")
        self.proc = subprocess.Popen(argv, stdout=self.logf, stderr=subprocess.STDOUT, cwd=self.scratch)
        for _ in range(100):
            time.sleep(0.1)
            if self.proc.poll() is not None:
                return False
            try:
                st, body = self.get("/api/hello", timeout=5)
                if st == 200:
                    return True
            except OSError:
                pass
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        subprocess.run(["pkill", "-f", f"serve --port {self.port} "], capture_output=True)
        try:
            self.logf.close()
        except Exception:
            pass

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- raw HTTP ----------------------------------------------------------

    def get(self, path: str, timeout: float = 30) -> tuple[int, bytes]:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            c.request("GET", path)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def post(self, path: str, body: bytes, timeout: float = 60) -> tuple[int, bytes]:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            c.request("POST", path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def raw(self, request: bytes, timeout: float = 30) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
            s.sendall(request)
            chunks = []
            try:
                while True:
                    b = s.recv(65536)
                    if not b:
                        break
                    chunks.append(b)
            except socket.timeout:
                chunks.append(b"<<client timeout>>")
            return b"".join(chunks)

    @staticmethod
    def form(**fields) -> bytes:
        return urllib.parse.urlencode({k: v for k, v in fields.items() if v is not None}).encode()

    # -- the judgement -----------------------------------------------------

    def save_body(self, label: str, body: bytes) -> Path:
        self.n += 1
        p = self.bodies / f"page-{self.n:04d}-{re.sub(r'[^A-Za-z0-9_.-]', '_', label)[:60]}.body"
        p.write_bytes(body)
        return p

    def call(self, label: str, path: str, body: bytes, timeout: float = 60, strict: bool = True) -> dict | None:
        """One POST, judged. Returns the JSON object when there is one."""
        L = self.ledger
        saved = self.save_body(label, body)
        repro = f"curl -s -X POST --data-binary @{saved} http://127.0.0.1:{self.port}{path}"
        where = f"page {path}"
        try:
            st, raw = self.post(path, body, timeout=timeout)
        except (OSError, http.client.HTTPException) as e:
            if not strict and self.alive():
                # a body over the cap is refused before it is read whole, and
                # a client still sending it sees a reset: a refusal, noted
                L.refusals_http += 1
                L.ok(label)
            else:
                L.fail(label, where, repro, f"the connection failed: {e!r}", "")
            self.after(label)
            return None
        text = raw.decode("utf-8", "replace")
        if st == 200:
            try:
                obj = json.loads(text)
            except ValueError:
                L.fail(label, where, repro, "HTTP 200 but the body is not JSON", text[:2000])
                self.after(label)
                return None
            if not isinstance(obj, dict):
                L.fail(label, where, repro, f"HTTP 200 but the JSON is a {type(obj).__name__}, not an object", text[:2000])
                self.after(label)
                return None
            if "error" in obj and not isinstance(obj["error"], str):
                L.fail(label, where, repro, "the error field is not a string", text[:2000])
            else:
                L.ok(label)
            self.after(label)
            return obj
        if st in (400, 404) and not strict and text.strip():
            L.refusals_http += 1
            L.ok(label)
            self.after(label)
            return None
        L.fail(label, where, repro, f"HTTP {st}, not 200 with JSON", text[:2000])
        self.after(label)
        return None

    def after(self, label: str):
        """The server keeps answering after every request."""
        L = self.ledger
        try:
            st, raw = self.get("/api/hello", timeout=10)
            ok = st == 200 and isinstance(json.loads(raw.decode("utf-8", "replace")), dict)
        except Exception as e:
            ok = False
            raw = repr(e).encode()
        if not ok:
            if not self.alive():
                code = self.proc.returncode
                tail = self.log.read_text(errors="replace")[-1500:]
                L.fail(f"server died after {label}", "page server", f"the request of the check {label!r} (its body is the last page-*.body written under {self.bodies})", f"the server process exited with {code} ({signal.Signals(-code).name if code and code < 0 else 'exit'}) and answers nothing; its log ends with: {tail.strip().splitlines()[-3:] if tail.strip() else 'nothing'}", tail)
                # restart, so that the rest of the battery is measured too
                self.restarts += 1
                self.logf.close()
                self.log = self.log.with_name(f"server-{self.restarts}.log")
                if self.start():
                    print(f"        (server restarted on port {self.port}, log {self.log})")
                else:
                    print("        (the server could not be restarted)")
            else:
                L.fail(f"hello after {label}", "page /api/hello", f"curl http://127.0.0.1:{self.port}/api/hello", "the server does not answer /api/hello any more", raw.decode("utf-8", "replace")[:500])
        else:
            L.ok(f"hello after {label}")

    # -- the texts ----------------------------------------------------------

    def text(self, label: str, text: str, names: list[str], small: bool) -> None:
        if not self.alive():
            self.ledger.skip(f"page {label}", "the server is down")
            return
        f = self.form
        self.call(f"svg {label}", "/api/svg", f(process=text, name="", detail="1", tooltips="1", bars="1", clocks="1", names="1"))
        self.call(f"bounds {label}", "/api/bounds", f(process=text, name="", time="0", max_iter="2"))
        self.call(f"bounds-time {label}", "/api/bounds", f(process=text, name="", time="1", max_iter="2"))
        known = names[0] if names else "a"
        self.call(f"bounds-max-known {label}", "/api/bounds", f(process=text, name="", time="1", max_iter="2", maximize=f"time,{known}"))
        self.call(f"bounds-max-unknown {label}", "/api/bounds", f(process=text, name="", time="1", max_iter="2", maximize="nothing_of_the_kind"))
        obj = self.call(f"frontier {label}", "/api/frontier", f(process=text, name="", workers="1", ablation="both", limit="2", time="1", max_iter="2", window="", maximize=""), timeout=90)
        if obj is not None and "error" not in obj:
            self.frontier_shape(f"frontier-shape {label}", obj)
        obj = self.call(f"start {label}", "/api/start", f(process=text, name="", workers="1", ablation="both", limit="2", time="1", max_iter="2", window="", maximize=known))
        if obj is not None and "id" in obj:
            sid = str(obj["id"])
            self.call(f"snapshot {label}", "/api/snapshot", f(id=sid))
            self.call(f"stop {label}", "/api/stop", f(id=sid))
            time.sleep(0.05)
            done = self.call(f"snapshot-after-stop {label}", "/api/snapshot", f(id=sid))
            if done is not None:
                self.ledger.check(f"snapshot-after-stop-answered {label}", "error" in done or "status" in done, "page /api/snapshot", f"id={sid}", "the snapshot after the stop holds neither a status nor an error", json.dumps(done)[:500])

    def slow_bounds(self, label: str, text: str) -> None:
        """A text whose bounds the tool cannot compute in time goes to
        /api/bounds alone, with a short client timeout: the finding is the
        missing answer, and the thread it leaves behind takes no lock, so the
        rest of the page keeps answering. It never goes to /api/frontier or
        /api/start, whose lock would then hold every later search."""
        L = self.ledger
        body = self.form(process=text, time="1", max_iter="1")
        saved = self.save_body(f"bounds {label}", body)
        repro = f"curl -s -m 10 -X POST --data-binary @{saved} http://127.0.0.1:{self.port}/api/bounds"
        t = time.time()
        try:
            st, raw = self.post("/api/bounds", body, timeout=10)
            L.check(f"bounds {label}", st == 200, "page /api/bounds", repro, f"HTTP {st}", raw[:500].decode("utf-8", "replace"))
        except (OSError, http.client.HTTPException) as e:
            L.fail(f"bounds {label}", "page /api/bounds", repro, f"no answer within {time.time() - t:.0f}s: {e!r}", "")
        self.after(f"bounds {label}")

    def stop_cuts(self, tiny: str, slow: str) -> None:
        """A search started and stopped at once must not hold the next one:
        the frontier of a one-task process is asked right after the stop and
        must answer within a few seconds."""
        L = self.ledger
        f = self.form
        obj = self.call("stop-cuts start", "/api/start", f(process=slow, name="", workers="1", ablation="both", limit="2", time="1", max_iter="1"))
        if obj is None or "id" not in obj:
            L.skip("stop-cuts", "the slow search did not start")
            return
        sid = str(obj["id"])
        self.call("stop-cuts stop", "/api/stop", f(id=sid))
        body = f(process=tiny, name="", workers="1", ablation="both", limit="1", time="1", max_iter="1")
        saved = self.save_body("stop-cuts frontier", body)
        t = time.time()
        try:
            st, raw = self.post("/api/frontier", body, timeout=120)
            took = time.time() - t
            L.check("stop-cuts", st == 200 and took < 4, "page /api/start, /api/stop, /api/frontier", f"start {slow[:40]}... then stop then curl -X POST --data-binary @{saved} http://127.0.0.1:{self.port}/api/frontier", f"the frontier of a one-task process answered after {took:.1f}s: the stopped search still held the lock", raw[:300].decode("utf-8", "replace"))
        except (OSError, http.client.HTTPException) as e:
            L.fail("stop-cuts", "page /api/frontier", f"after a start and a stop of {slow[:40]}...", f"no answer within {time.time() - t:.0f}s: {e!r}")
        self.after("stop-cuts")

    def frontier_shape(self, label: str, obj: dict):
        L = self.ledger
        need = ["status", "points", "names"]
        missing = [k for k in need if k not in obj]
        if missing:
            L.fail(label, "page /api/frontier", "see the frontier call above", f"the answer lacks {missing}", json.dumps(obj)[:1000])
            return
        pts = obj["points"]
        ok = isinstance(pts, list) and all(isinstance(p, dict) and "value" in p and "approx" in p and "certified" in p for p in pts)
        if not ok:
            L.fail(label, "page /api/frontier", "see the frontier call above", "a point lacks value, approx or certified", json.dumps(obj)[:1000])
            return
        if "lower" not in obj or "upper" not in obj:
            L.ok(label)
            return
        lo, hi = obj["lower"], obj["upper"]
        bad = []
        for p in pts:
            a = p["approx"]
            if not (isinstance(a, list) and len(a) == len(lo) == len(hi)):
                bad.append(a)
                continue
            for c in range(len(a)):
                m = obj["maximized"][c] if isinstance(obj.get("maximized"), list) and c < len(obj["maximized"]) else False
                l_, h_ = (hi[c], lo[c]) if m else (lo[c], hi[c])
                if not (float(l_) - 1e-5 <= float(a[c]) <= float(h_) + 1e-5):
                    bad.append(a)
                    break
        L.check(label, not bad, "page /api/frontier", "see the frontier call above", f"{len(bad)} points outside the bounds [{lo}, {hi}]: {bad[:3]}", json.dumps(obj)[:2000])

    # -- the malformed bodies -----------------------------------------------

    def malformed(self, tiny: str) -> None:
        f = self.form
        L = self.ledger
        routes = ["/api/bench", "/api/save", "/api/load", "/api/delete", "/api/frontier", "/api/svg", "/api/bounds", "/api/start", "/api/snapshot", "/api/stop"]
        for r in routes:
            self.call(f"empty-body {r}", r, b"")
            self.call(f"no-equals {r}", r, b"process")
            self.call(f"only-ampersands {r}", r, b"&&&=&=&")
            self.call(f"unrelated-field {r}", r, b"zzz=1")
            self.call(f"json-body {r}", r, b'{"process": "(A, 1, {x: 1})"}')
        self.call("missing-process frontier", "/api/frontier", f(workers="1", limit="1"))
        self.call("missing-id snapshot", "/api/snapshot", f(process=tiny))
        self.call("id-abc snapshot", "/api/snapshot", f(id="abc"))
        self.call("id-neg snapshot", "/api/snapshot", f(id="-1"))
        self.call("id-huge snapshot", "/api/snapshot", f(id="99999999999999999999"))
        self.call("id-unknown stop", "/api/stop", f(id="999999"))
        self.call("5MB-text frontier", "/api/frontier", f(process="(A, 1, {x: 1})" + "#" + "x" * 5_000_000, limit="1"), strict=False, timeout=90)
        self.call("million-chars svg", "/api/svg", f(process="(" * 500_000 + "A, 1, {x: 1}" + ")" * 500_000), strict=False)
        self.call("200k-chars svg", "/api/svg", f(process="(A, 1, {x: 1})" + " " * 200_000))
        self.call("200k-parens svg", "/api/svg", f(process="(" * 100_000 + "(A, 1, {x: 1})" + ")" * 100_000), strict=False)
        self.call("200k-parens bounds", "/api/bounds", f(process="(" * 100_000 + "(A, 1, {x: 1})" + ")" * 100_000, time="1"), strict=False)
        self.call("40k-parens svg", "/api/svg", f(process="(" * 40_000 + "(A, 1, {x: 1})" + ")" * 40_000))
        self.call("40k-parens bounds", "/api/bounds", f(process="(" * 40_000 + "(A, 1, {x: 1})" + ")" * 40_000, time="1"))
        self.call("window-1e400 frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", window="1e400,2"))
        self.call("window-400digits frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", window="1" * 400 + ",2"))
        self.call("invalid-percent svg", "/api/svg", b"process=%zz%4&name=%&time=%2")
        self.call("percent-at-end svg", "/api/svg", b"process=(A, 1, {x: 1})%")
        self.call("percent-nul svg", "/api/svg", b"process=%00%00(A,%001,%00{x:%001})")
        self.call("non-utf8 svg", "/api/svg", b"process=\xff\xfe(A, 1, {x: 1})", strict=False)
        self.call("non-utf8-percent svg", "/api/svg", b"process=%ff%fe(A, 1, {x: 1})")
        self.call("overlong-percent svg", "/api/svg", b"process=%c0%80(A, 1, {x: 1})")
        self.call("surrogate-percent svg", "/api/svg", b"process=%ed%a0%80(A, 1, {x: 1})")
        self.call("plus-and-percent svg", "/api/svg", b"process=%28A%2C+1%2C+%7Bx%3A+1%7D%29")
        self.call("duplicate-fields bounds", "/api/bounds", f(process=tiny) + b"&process=garbage&time=1&time=abc")
        for w in ["0", "-1", "1000", "abc", "", "1.5", "99999999999999999999"]:
            self.call(f"workers-{w or 'empty'} frontier", "/api/frontier", f(process=tiny, workers=w, limit="1", time="1"), timeout=120)
        for lim in ["0", "1e9", "abc", "", "-5", "nan", "inf", "0.001"]:
            self.call(f"limit-{lim or 'empty'} frontier", "/api/frontier", f(process=tiny, workers="1", limit=lim, time="1"), timeout=120)
        for mi in ["0", "500", "inf", "-1", "abc", "", "1e2", "50", "51", "127", "128"]:
            self.call(f"max_iter-{mi or 'empty'} frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", max_iter=mi, time="1"))
            self.call(f"max_iter-{mi or 'empty'} bounds", "/api/bounds", f(process=tiny, max_iter=mi, time="1"))
        self.call("window-50 frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", window=";".join(["0,100"] * 50)))
        self.call("window-garbage frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", window="a,b;;,;1/0,2;--1,1e400"))
        self.call("window-reversed frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", window="100,0"))
        self.call("window-commas frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", window=",,,,,,"))
        self.call("window-long frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", window="1" * 100_000))
        self.call("maximize-1000 bounds", "/api/bounds", f(process=tiny, time="1", maximize=",".join(f"n{i}" for i in range(1000))))
        self.call("maximize-1000 frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", time="1", maximize=",".join(f"n{i}" for i in range(1000))))
        self.call("maximize-repeated frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", time="1", maximize="time,time,time,x,x"))
        self.call("maximize-commas frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", time="1", maximize=",,,,"))
        self.call("ablation-bad frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", ablation="sometimes"))
        self.call("ablation-none frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", ablation="none"))
        self.call("time-abc frontier", "/api/frontier", f(process=tiny, workers="1", limit="1", time="abc"))
        self.call("expand-garbage svg", "/api/svg", f(process=tiny, expand="a,b,c,;;99999999999,-1,abc"))
        self.call("name-long svg", "/api/svg", f(process=tiny, name="n" * 10_000))
        self.call("name-at svg", "/api/svg", f(process="(@x || " + tiny + ")", name="x"))
        self.call("name-self-reference bounds", "/api/bounds", f(process="(@self , " + tiny + ")", name="self"))
        # the store: save, load, delete, with odd names
        self.call("save-empty-name", "/api/save", f(name="", process=tiny))
        self.call("save-spaces-name", "/api/save", f(name="   ", process=tiny))
        self.call("save-long-name", "/api/save", f(name="n" * 5000, process=tiny))
        self.call("save-unicode-name", "/api/save", f(name="ünï 😀 \"q\" \\ /", process=tiny))
        self.call("save-slash-name", "/api/save", f(name="../../etc/passwd", process=tiny))
        self.call("save-newline-name", "/api/save", f(name="a\nb", process=tiny))
        self.call("save-ok", "/api/save", f(name="fuzz_one", process=tiny, overwrite="1"))
        self.call("save-again-no-overwrite", "/api/save", f(name="fuzz_one", process=tiny, overwrite="0"))
        self.call("save-invalid-process", "/api/save", f(name="fuzz_bad", process="((", overwrite="1"))
        self.call("save-refers-self", "/api/save", f(name="fuzz_self", process="(@fuzz_self , " + tiny + ")", overwrite="1"))
        self.call("save-refers-other", "/api/save", f(name="fuzz_two", process="(@fuzz_one || " + tiny + ")", overwrite="1"))
        self.call("bounds-via-reference", "/api/bounds", f(process="(@fuzz_two , @fuzz_one)", time="1"))
        self.call("svg-via-reference-expanded", "/api/svg", f(process="(@fuzz_two , @fuzz_one)", expand="1,2,3,4"))
        self.call("load-ok", "/api/load", f(name="fuzz_one"))
        self.call("load-unknown", "/api/load", f(name="no_such_process"))
        self.call("delete-referred", "/api/delete", f(name="fuzz_one"))
        self.call("delete-unknown", "/api/delete", f(name="no_such_process"))
        self.call("delete-ok", "/api/delete", f(name="fuzz_two"))
        self.call("bench-empty", "/api/bench", f(instance=""))
        self.call("bench-traversal", "/api/bench", f(instance="../../../../etc/passwd"))
        self.call("bench-garbage", "/api/bench", f(instance="zz", nested="x", independent="-1", number="1e9", mode="\x00"))
        self.call("bench-numbers", "/api/bench", f(nested="99999999999999999999", independent="-1", number="0"))
        try:
            st, raw = self.get("/api/saved")
            L.check("saved-list", st == 200 and isinstance(json.loads(raw), (dict, list)), "page /api/saved", f"curl http://127.0.0.1:{self.port}/api/saved", f"HTTP {st}", raw.decode("utf-8", "replace")[:500])
        except Exception as e:
            L.fail("saved-list", "page /api/saved", f"curl http://127.0.0.1:{self.port}/api/saved", repr(e))
        # raw requests the client library would not send
        raws = [
            ("raw-no-headers", b"POST /api/svg\r\n\r\nprocess=x"),
            ("raw-garbage", b"\xff\xfe\x00\x01 not http at all\r\n\r\n"),
            ("raw-empty-request", b"\r\n\r\n"),
            ("raw-get-with-body", b"GET /api/hello HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello"),
            ("raw-unknown-route", b"GET /nothing/here HTTP/1.1\r\n\r\n"),
            ("raw-unknown-method", b"DELETE /api/svg HTTP/1.1\r\n\r\n"),
            ("raw-head", b"HEAD / HTTP/1.1\r\n\r\n"),
            ("raw-huge-content-length", b"POST /api/svg HTTP/1.1\r\nContent-Length: 99999999999999999999\r\n\r\nprocess=x"),
            ("raw-negative-content-length", b"POST /api/svg HTTP/1.1\r\nContent-Length: -5\r\n\r\nprocess=x"),
            ("raw-content-length-over-cap", b"POST /api/svg HTTP/1.1\r\nContent-Length: 10000000\r\n\r\nprocess=x"),
            ("raw-long-header-line", b"POST /api/svg HTTP/1.1\r\nX-Junk: " + b"j" * 2_000_000 + b"\r\nContent-Length: 9\r\n\r\nprocess=x"),
            ("raw-many-headers", b"POST /api/svg HTTP/1.1\r\n" + b"X-A: 1\r\n" * 100_000 + b"Content-Length: 9\r\n\r\nprocess=x"),
            ("raw-path-long", b"GET /" + b"a" * 1_000_000 + b" HTTP/1.1\r\n\r\n"),
            ("raw-http-0.9", b"GET /\r\n"),
        ]
        for label, req in raws:
            saved = self.save_body(label, req)
            repro = f"python3 -c \"import socket;s=socket.create_connection(('127.0.0.1',{self.port}));s.sendall(open('{saved}','rb').read());print(s.recv(65536))\""
            try:
                ans = self.raw(req, timeout=30)
                ok = ans.startswith(b"HTTP/1.1 ") and b"<<client timeout>>" not in ans
                L.check(label, ok, "page raw request", repro, "no HTTP status line in the answer or the client timed out", ans[:500].decode("utf-8", "replace"))
            except OSError as e:
                L.fail(label, "page raw request", repro, f"connection failed: {e!r}")
            self.after(label)
        # a body shorter than its Content-Length: the server waits 15 s then refuses
        label = "raw-lying-content-length"
        req = b"POST /api/svg HTTP/1.1\r\nContent-Length: 100\r\n\r\nprocess=x"
        saved = self.save_body(label, req)
        t = time.time()
        try:
            ans = self.raw(req, timeout=25)
            L.check(label, ans.startswith(b"HTTP/1.1 ") and b"<<client timeout>>" not in ans, "page raw request", f"python3 -c ... {saved}", f"after {time.time() - t:.0f}s the answer is not an HTTP response", ans[:500].decode("utf-8", "replace"))
        except OSError as e:
            L.fail(label, "page raw request", f"{saved}", f"connection failed: {e!r}")
        self.after(label)
        # the half-open connection: a client that sends nothing at all
        label = "raw-silent-client"
        t = time.time()
        try:
            ans = self.raw(b"", timeout=25)
            L.check(label, b"<<client timeout>>" not in ans, "page raw request", "a connection that sends nothing", f"after {time.time() - t:.0f}s the server has not closed the idle connection", ans[:200].decode("utf-8", "replace"))
        except OSError as e:
            L.fail(label, "page raw request", "a connection that sends nothing", f"connection failed: {e!r}")
        self.after(label)
        # a burst of searches started and stopped at once
        ids = []
        for i in range(12):
            obj = self.call(f"burst-start-{i}", "/api/start", f(process=tiny, workers="1", limit="1", time="1"))
            if obj and "id" in obj:
                ids.append(str(obj["id"]))
        for sid in ids:
            self.call(f"burst-stop-{sid}", "/api/stop", f(id=sid))
        for sid in ids:
            self.call(f"burst-snapshot-{sid}", "/api/snapshot", f(id=sid))
        # the page itself
        try:
            st, raw = self.get("/")
            L.check("page-html", st == 200 and b"<!doctype html>" in raw.lower() and b"/api/" in raw, "page /", f"curl http://127.0.0.1:{self.port}/", f"HTTP {st}", raw[:300].decode("utf-8", "replace"))
        except Exception as e:
            L.fail("page-html", "page /", f"curl http://127.0.0.1:{self.port}/", repr(e))

    def log_scan(self):
        L = self.ledger
        text = ""
        for log in sorted(self.scratch.glob("server*.log")):
            try:
                text += log.read_text(errors="replace")
            except OSError:
                pass
        hits = [ln for ln in text.splitlines() if any(w in ln for w in BAD_WORDS)]
        L.check("server-log-clean", not hits, "page server log", str(self.scratch / "server*.log"), f"{len(hits)} lines of the server log carry a panic or an overflow", "\n".join(hits[:20]))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", required=True, help="the sdcpi binary")
    ap.add_argument("--count", type=int, default=20, help="random valid texts (default 20)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--port", type=int, default=8137, help="the first port tried for the page server (default 8137)")
    ap.add_argument("--no-loops", action="store_true", help="the mirror without loops: no loop is generated, --max-iter is not passed")
    ap.add_argument("--scratch", default=None, help="where inputs, outputs, the store and the log go (default: a fresh temp folder)")
    ap.add_argument("--bench", default=None, help="the benchmark folder for the server (default: the sdcpi-parallel experiment instances when present)")
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds per command (default 30)")
    ap.add_argument("--skip-page", action="store_true", help="the command line alone")
    ap.add_argument("--skip-cli", action="store_true", help="the page alone")
    ap.add_argument("--heavy", action="store_true", help="also the instance files that make the reader allocate gigabytes")
    ap.add_argument("--mutations-per-text", type=int, default=4, help="how many of the valid texts get the full mutation battery (default 4)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    if not Path(args.bin).is_file():
        sys.exit(f"run.py: no binary at {args.bin}")
    scratch = Path(args.scratch) if args.scratch else Path(tempfile.mkdtemp(prefix="sdcpi-fuzz-"))
    scratch.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(scratch, args.verbose)
    loops = not args.no_loops
    rng = random.Random(args.seed)
    gen = Gen(rng, loops)
    print(f"fuzz: bin {args.bin}\n      scratch {scratch}\n      seed {args.seed} count {args.count} loops {loops}")

    # (a) the valid texts
    valid: list[tuple[str, str]] = []
    for i in range(args.count):
        n_tasks = rng.randint(1, 7)
        n_imp = rng.randint(1, 3)
        valid.append((f"v{i:03d}", gen.text(n_tasks, n_imp)))
    tiny = "(A, 1, {x: 1})"
    valid.insert(0, ("tiny", tiny))
    valid.insert(1, ("hand-all", "((A, 2, {a: 1, b: 2.5}) , ((B, 1, {a: 3}) || ((C, 3, {b: 1}) ^ (D, 1, {a: 1, b: 1}))) , ((E, 2) ^[0.3] (F, 1, {})))"))
    if loops:
        valid.insert(2, ("hand-loops", "((0.5 (A, 1, {a: 2}) , (B, 2, {b: 1})) || (0.2 ((C, 1, {a: 1}) ^ (0.7 (D, 1, {b: 3})))))"))
        valid.insert(3, ("hand-loop-nature", "(0.8 ((A, 1, {a: 1}) ^[0.4] (B, 1, {a: 2})))"))

    # (b) the mutations and the stress texts
    mutated: list[tuple[str, str]] = []
    for label, text in valid[: args.mutations_per_text + 2]:
        for mlabel, mtext in mutations(text, rng, loops):
            mutated.append((f"{label}-{mlabel}", mtext))
    stress = stress_texts(loops)
    if not loops:
        mutated.append(("loop-syntax-in-mirror", "(0.5 " + tiny + ")"))
        mutated.append(("loop-syntax-nested-in-mirror", "((A, 1, {x: 1}) , (0.3 (B, 2, {x: 1})))"))

    cli = Cli(args, ledger, scratch)
    page = None
    accepted_yaml: dict[str, Path] = {}
    all_texts: list[tuple[str, str, bool]] = [(l, t, True) for l, t in valid] + [(l, t, False) for l, t in mutated] + [(l, t, False) for l, t in stress]

    if not args.skip_cli:
        print(f"\n[cli] {len(valid)} valid texts, {len(mutated)} mutations, {len(stress)} stress texts")
        for label, text, is_valid in all_texts:
            y = cli.parse_text(label, text)
            if is_valid and y is None:
                ledger.fail(f"valid-accepted {label}", "sdcpi parse", f"{args.bin} parse --file {scratch / 'inputs' / (label + '.cpi')}", "a text of the generator, valid by the grammar, was refused", "")
            if y is not None:
                accepted_yaml[label] = y
            if len(text) < 2000:
                cli.parse_inline(label, text)
        print(f"[cli] {len(accepted_yaml)} texts accepted by parse; exercising every command on them")
        for label, y in accepted_yaml.items():
            cli.exercise(label, y)
        # (c) the mutated instance files
        base_label = "hand-loops" if loops and "hand-loops" in accepted_yaml else ("hand-all" if "hand-all" in accepted_yaml else next(iter(accepted_yaml)))
        base_yaml = accepted_yaml[base_label].read_text()
        files = yaml_mutations(base_yaml, loops)
        if args.heavy:
            files += [(l, t) for l, t in yaml_heavy(base_yaml)]
        print(f"[cli] {len(files)} mutated instance files from {base_label}")
        for label, content in files:
            cli.exercise_file(f"yaml-{label}", content, heavy=label.startswith("heavy"))
        print("[cli] arguments out of range")
        cli.bad_arguments(accepted_yaml[base_label])

    if not args.skip_page:
        page = Page(args, ledger, scratch)
        print(f"\n[page] starting {Path(args.bin).name} serve --port {page.port} (log {page.log})")
        if not page.start():
            ledger.fail("server-start", "page", f"{args.bin} serve --port {page.port} --saved {page.store}", "the server did not answer /api/hello within 10 s", page.log.read_text(errors="replace")[-2000:] if page.log.exists() else "")
        else:
            try:
                for label, text, is_valid in all_texts:
                    if len(text) > 200_000:
                        continue  # over the body cap of the server; the malformed battery covers that
                    if label == "loops-nested-30":
                        page.slow_bounds(label, text)
                        continue
                    names = re.findall(r"\b([A-Za-z_]\w*)\s*:", text)[:1]
                    page.text(label, text, names, small=len(text) < 500)
                if loops:
                    slow = tiny
                    for _ in range(23):
                        slow = f"(0.5 {slow})"
                    page.stop_cuts(tiny, slow)
                print("[page] malformed bodies")
                page.malformed(tiny)
            finally:
                page.log_scan()
                page.stop()
                print(f"[page] server on port {page.port} stopped")

    (scratch / "findings.json").write_text(json.dumps(ledger.findings, indent=2))
    for why, n in sorted(ledger.skip_reasons.items(), key=lambda kv: -kv[1]):
        print(f"skipped {n:5d}  {why}")
    print(
        f"\nchecks {ledger.checks}  passed {ledger.passed}  failed {ledger.failed}  skipped {ledger.skipped}"
        f"  (http refusals accepted: {ledger.refusals_http}; findings in {scratch / 'findings.json'})"
    )
    return 0 if ledger.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
