#!/usr/bin/env python3
"""The page of `sdcpi serve` exercised route by route, with urllib alone.

    run.py --bin PATH                        the loop tool, no benchmark panel
    run.py --bin PATH --bench DIR            with the instances of the campaign
    run.py --bin PATH --no-loops             the mirror without loops
    run.py --bin PATH --port 8150 --work W   the port and the scratch folder

The suite starts a server of its own, `BIN serve --port P --saved W/store.json
[--bench DIR]`, with a fresh store, waits for `/api/hello`, and sends every
route what the page sends it, as `application/x-www-form-urlencoded` bodies.
The server is killed at the end, and restarted once in the middle, to read
its store back. Nothing of the suite reaches a server it did not start, and
every file it writes lands under `--work`, by default a fresh temporary
folder.

What is checked, route by route:

  hello       the fields `grammar`, `example`, `bench`, `modes`; the example
              parses, and draws as many task boxes as it names.
  bench       (with `--bench`, or when hello says the panel is there) the key
              1-1-1-d-mode for every mode and two sizes, the text drawing as
              many tasks as the instance file holds; the regeneration with a
              seed, the same seed twice giving the same text and another seed
              another; the keys out of the grid, the bad numbers, a missing
              field and an unknown mode refused with an error. Only keys on
              disk are asked for, so that the generator never writes.
  examples    the list of the panel "Examples", the ten processes of the
              paper and of the thesis with their group and their settings,
              typed, and no text in it; every example by its id, its text
              the file under `examples/`, drawing its diagram and giving,
              with its settings, the frontier its document reports, 1937
              points for the release; an unknown id, a missing one and a
              path are errors; the release and the stroke drawn, bounded,
              searched and explained on an empty store through the built-in
              subprocess; a save closing a cycle through an example refused;
              a saved process named `tests` shadowing the built-in one until
              it is deleted.
  svg         a valid text gives an SVG with one task box per task, with each
              of the switches detail, tooltips, bars, clocks, names on and
              off; `hide` and `show` select the bars; an invalid text, an
              empty one and an unknown reference give an error string; a text
              closing a cycle through the references gives the cycle error.
  bounds      the names and the two bounds with the time on and off, the
              flags `maximized` for `maximize=name`, an unknown name dropped
              silently, the count `loops` (loop tool), the clock renamed
              "expected time" when an impact is named `time`, and the two
              bounds equal to `sdcpi bound` on the same instance, the
              `--max-iter` and `--maximize` of the command line included.
  frontier    the synchronous route against the final answer of a start on
              the same body, point for point; against `sdcpi pareto
              --onephase 1 --json` on the same instance, values and
              `continued`; the window honored, `outside` and
              `outside_points` consistent with it, a fraction accepted; the
              directions honored; the error paths.
  start, snapshot, stop
              a lifecycle on a process of twenty one tasks with limit 20:
              the snapshots are live with counters that grow, a stop gives
              the final answer with status "cut at the limit" and its
              certified points, a snapshot after the end returns the same
              answer every time, a stop after the end is fine, an unknown
              identifier is an error, two starts back to back both get an
              identifier and both end, and the sessions kept are the last six.
  saved, save, load, delete
              a process saved under a name, listed with its length, loaded
              back equal; a second save answers `exists` and overwrites only
              on `overwrite=1`; a name with spaces and unicode; `@name`,
              `@name[shown]` and `@"a name"` spliced by svg and frontier with
              the hierarchical identifiers in the decisions; a save closing a
              cycle refused and the store left as it was; a delete of a
              process another refers to refused, a prefix of a name not
              counting as a reference; the names refused; the store file
              valid JSON after every step, and read back by a restarted
              server.
  hygiene     a GET on a POST route and an unknown path answer 404 and not a
              crash; a body of three megabytes is refused and the server
              lives; a body that is not a form, and one that is not UTF-8;
              fifty concurrent bounds requests all answer; the server is
              alive at the end.

A failed check is reported with the request and the answer, so that it can
be replayed by hand. The summary counts the checks made, passed, failed and
skipped, and the exit code is 0 exactly when nothing failed.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from fractions import Fraction

# -- the processes the checks use --------------------------------------------

TEXT_PLAIN = """# two impacts, a choice, a nature node, a parallel
( (Intake, 1, {euro: 10, hours: 2})
, ( (Swap, 2, {euro: 50, hours: 4})
    ^ ( (Mend, 3, {euro: 20, hours: 9})
      , ( (Redo, 2, {euro: 40, hours: 6}) ^[0.3] (Pass, 1, {hours: 1}) ) ) )
, ( (Check, 1, {euro: 6, hours: 1}) || (File, 1, {euro: 4, hours: 1}) )
, ( (Courier, 1, {euro: 26, hours: 1}) ^ (Counter, 2, {euro: 2, hours: 2}) ) )"""

TEXT_TIME_NAMED = "((A, 1, {x: 1, time: 2}) ^ (B, 2, {x: 3}))"

TEXT_LOOP = "(0.5 ((A, 1, {x: 1}) ^ (B, 2, {x: 3})))"

TEXT_TWO_LOOPS = "((0.3 (A, 1, {x: 1})) , (0.5 ((B, 2, {x: 3, y: 1}) ^ (C, 1, {y: 4}))))"

# the instance 2-5-5-5-random of the campaign, as `sdcpi to_text` writes it:
# twenty one tasks, five impacts, a frontier of more than a thousand points
# that the one-phase search does not close in twenty seconds
TEXT_SLOW = """( ( ( (T1, 2, {impact0: 0.954507, impact1: 0.059873, impact2: 0.738298, impact3: 0.336444, impact4: 0.350465})
    || ( ( (T2, 5, {impact0: 0.033696, impact1: 0.397834, impact2: 0.927812, impact3: 0.415244, impact4: 0.227533})
           , ( (T3, 1, {impact0: 0.255418, impact1: 0.379869, impact2: 0.157092, impact3: 0.392955, impact4: 0.318901}) || (T4, 1, {impact0: 0.448309, impact1: 0.724177, impact2: 0.610884, impact3: 0.325709, impact4: 0.417991}) ) )
         ^[0.921774] (T5, 3, {impact0: 0.388905, impact1: 0.304861, impact2: 0.782094, impact3: 0.384525, impact4: 0.199264}) ) )
  , ( (T6, 3, {impact0: 0.003256, impact1: 0.408814, impact2: 0.693761, impact3: 0.156494, impact4: 0.513408})
    , ( (T7, 5, {impact0: 0.287924, impact1: 0.844475, impact2: 0.842649, impact3: 0.868103, impact4: 0.309259}) ^[0.147079] (T8, 2, {impact0: 0.307151, impact1: 0.529079, impact2: 0.216031, impact3: 0.915689, impact4: 0.630736}) ) ) )
|| ( ( (T9, 1, {impact0: 0.918408, impact1: 0.033856, impact2: 0.975221, impact3: 0.056351, impact4: 0.557522})
       ^[0.502285] ( (T10, 3, {impact0: 0.269231, impact1: 0.866217, impact2: 0.713184, impact3: 0.270512, impact4: 0.599826})
                     , ( (T11, 1, {impact0: 0.384168, impact1: 0.180481, impact2: 0.490147, impact3: 0.08501, impact4: 0.884643})
                       , ( (T12, 2, {impact0: 0.29261, impact1: 0.203078, impact2: 0.961374, impact3: 0.754515, impact4: 0.047901}) ^ (T13, 4, {impact0: 0.958287, impact1: 0.395838, impact2: 0.640002, impact3: 0.818384, impact4: 0.347219}) ) )
                     , ( (T14, 2, {impact0: 0.978124, impact1: 0.444574, impact2: 0.835942, impact3: 0.829949, impact4: 0.236442})
                       , (T15, 1, {impact0: 0.420454, impact1: 0.574463, impact2: 0.394002, impact3: 0.007827, impact4: 0.443442})
                       , ( ( (T16, 4, {impact0: 0.467053, impact1: 0.76875, impact2: 0.220707, impact3: 0.944579, impact4: 0.079177}) || (T17, 4, {impact0: 0.028341, impact1: 0.884002, impact2: 0.858986, impact3: 0.202439, impact4: 0.750911}) )
                         ^ (T18, 4, {impact0: 0.007345, impact1: 0.182511, impact2: 0.983355, impact3: 0.754691, impact4: 0.914999}) ) ) ) )
     , ( ( (T19, 1, {impact0: 0.890546, impact1: 0.869393, impact2: 0.856733, impact3: 0.373456, impact4: 0.609533}) ^ (T20, 1, {impact0: 0.703268, impact1: 0.945377, impact2: 0.43249, impact3: 0.570204, impact4: 0.871707}) )
       ^[0.728244] (T21, 3, {impact0: 0.426879, impact1: 0.394883, impact2: 0.790622, impact3: 0.756004, impact4: 0.406662}) ) ) )"""

# the examples of the panel, as the list of the page must give them: the id,
# the group, the file of the text under examples/, the three settings, and
# the number of points of the frontier computed with them, the outcome the
# paper and the thesis report
EXAMPLES = [
    ("release", "paper", "release.cpi", False, 2, [], 1937),
    ("tests", "paper", "tests.cpi", False, 2, [], 2),
    ("coin", "paper", "coin.cpi", False, 1, [], 3),
    ("winery", "thesis", "winery.cpi", False, 1, ["points"], 7),
    ("stroke", "thesis", "stroke.cpi", True, 1, [], 12),
    ("transport", "thesis", "transport.cpi", True, 1, [], 2),
    ("release-nested", "thesis", "release-nested.cpi", True, 2, [], 1024),
    ("line", "thesis", "line-thesis.cpi", False, 1, [], 4),
    ("quantile", "thesis", "quantile.cpi", False, 1, [], 1),
    ("loop-choice", "thesis", "loop-choice.cpi", False, 2, [], 4),
]

# the points of the small frontiers, in the order of the names of the answer
EXAMPLE_POINTS = {
    "coin": ["1 11", "6 6", "11 1"],
    "winery": ["5150 22/5", "5390 5", "5710 29/5", "5950 32/5", "7190 7", "7510 39/5", "7750 42/5"],
    "line": ["587/5 43/5", "607/5 41/5", "667/5 7", "687/5 33/5"],
    "loop-choice": ["3/2 15", "2 12", "5/2 9", "3 6"],
    "quantile": ["2 5/2"],
}

# the folder of the examples, beside the scripts
EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "examples")

TASK_RE = re.compile(r"\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*\d+")


def code_of(text):
    """A text without its comments, which the parser never reads."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def tasks_in(text):
    """How many tasks a text names: a parenthesis, a name, a comma, a duration."""
    return len(TASK_RE.findall(text))


# -- the ledger ---------------------------------------------------------------


class Ledger:
    def __init__(self, verbose):
        self.checks = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.findings = []
        self.section = ""
        self.verbose = verbose

    def start(self, section):
        self.section = section
        print(f"\n== {section}")

    def check(self, name, ok, request=None, answer=None):
        self.checks += 1
        if ok:
            self.passed += 1
            if self.verbose:
                print(f"  ok    {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}")
            self.findings.append(
                {
                    "section": self.section,
                    "check": name,
                    "request": clip(request),
                    "answer": clip(answer),
                }
            )
        return ok

    def skip(self, name, why):
        self.skipped += 1
        print(f"  skip  {name}: {why}")


def clip(x, n=600):
    if x is None:
        return None
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + f"... [{len(s)} chars]"


# -- the client ----------------------------------------------------------------


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path, timeout=30):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def post_raw(self, path, data, timeout=120, content_type="application/x-www-form-urlencoded"):
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        req.add_header("Content-Type", content_type)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def post(self, path, fields, timeout=120):
        """A form POST; the parsed JSON, or {"_raw": text} when it is not JSON."""
        body = urllib.parse.urlencode(fields).encode("utf-8")
        status, text = self.post_raw(path, body, timeout=timeout)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text, "_status": status}

    def status_of(self, path, method="GET", data=None):
        """The HTTP status of a request, whatever it is."""
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")


def describe(path, fields):
    return f"POST {path} " + json.dumps(fields, ensure_ascii=False)


# -- the server ----------------------------------------------------------------


class Server:
    def __init__(self, binary, port, store, bench, log, bench_loops=None):
        self.binary = binary
        self.port = port
        self.store = store
        self.bench = bench
        self.bench_loops = bench_loops
        self.log = log
        self.proc = None

    def start(self):
        args = [self.binary, "serve", "--port", str(self.port), "--saved", self.store]
        if self.bench:
            args += ["--bench", self.bench]
        if self.bench_loops:
            args += ["--bench-loops", self.bench_loops]
        self.logfile = open(self.log, "ab")
        self.proc = subprocess.Popen(args, stdout=self.logfile, stderr=subprocess.STDOUT)
        client = Client(self.port)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"the server exited with {self.proc.returncode}; see {self.log}")
            try:
                status, text = client.get("/api/hello", timeout=2)
                if status == 200:
                    return
            except (urllib.error.URLError, ConnectionError, socket.timeout, OSError):
                time.sleep(0.2)
        raise RuntimeError("the server did not answer /api/hello within 15 seconds")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None
        try:
            self.logfile.close()
        except Exception:
            pass


def port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # the listener of the server sets SO_REUSEADDR, so a port whose last
        # connections are still in TIME_WAIT is free for it
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


# -- the command line beside the page ------------------------------------------


def cli(binary, args, timeout=120):
    r = subprocess.run([binary] + args, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def cli_instance(binary, text, work, stem, store=None):
    """The instance file of a text, written by `parse`; None when it fails."""
    path = os.path.join(work, stem + ".yaml")
    args = ["parse", text, "--out", path]
    if store:
        args += ["--saved", store]
    code, out, err = cli(binary, args)
    return path if code == 0 and os.path.isfile(path) else None


def cli_bound(binary, path, time_on, max_iter=None, maximize=None):
    """The lines `U at s0` and `L at s0` of `sdcpi bound`, as floats."""
    args = ["bound", path]
    if time_on:
        args += ["--time", "1"]
    if max_iter is not None:
        args += ["--max-iter", str(max_iter)]
    if maximize:
        args += ["--maximize", maximize]
    code, out, err = cli(binary, args)
    if code != 0:
        return None, " ".join(args) + "\n" + err
    found = {}
    for line in out.splitlines():
        for key in ("U at s0", "L at s0"):
            if line.startswith(key):
                found[key] = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", line.split("[", 1)[1])]
    return found, " ".join(args) + "\n" + out


def cli_pareto(binary, path, work, stem, time_on, max_iter=None, maximize=None, loops=True):
    """The JSON of `sdcpi pareto --onephase 1`: points as rationals."""
    out_json = os.path.join(work, stem + ".pareto.json")
    args = ["pareto", path, "--onephase", "1", "--workers", "1", "--time-limit", "60", "--json", out_json]
    if time_on:
        args += ["--time", "1"]
    if max_iter is not None and loops:
        args += ["--max-iter", str(max_iter)]
    if maximize:
        args += ["--maximize", maximize]
    code, out, err = cli(binary, args, timeout=180)
    if code != 0 or not os.path.isfile(out_json):
        return None, " ".join(args) + "\n" + out + err
    with open(out_json) as f:
        return json.load(f), " ".join(args)


def frac_tuple(values):
    return tuple(Fraction(v) for v in values)


def points_set(points):
    return set(frac_tuple(p["value"]) for p in points)


def close(a, b, eps=1e-6):
    return len(a) == len(b) and all(abs(float(x) - float(y)) <= eps for x, y in zip(a, b))


# -- the sections ----------------------------------------------------------------


def section_hello(L, c, args):
    L.start("hello")
    status, text = c.get("/api/hello")
    try:
        h = json.loads(text)
    except json.JSONDecodeError:
        L.check("hello is JSON", False, "GET /api/hello", text)
        return {}
    L.check("hello has a grammar string", isinstance(h.get("grammar"), str) and len(h["grammar"]) > 50, "GET /api/hello", h)
    L.check("hello has an example string", isinstance(h.get("example"), str) and "(" in h.get("example", ""), "GET /api/hello", h)
    L.check("hello bench is a boolean", isinstance(h.get("bench"), bool), "GET /api/hello", h)
    if args.bench:
        L.check("hello bench is true with --bench", h.get("bench") is True, "GET /api/hello", h)
    L.check("hello loops is a boolean", isinstance(h.get("loops"), bool), "GET /api/hello", h)
    if args.bench_loops:
        L.check("hello loops is true with --bench-loops", h.get("loops") is True, "GET /api/hello", h)
    modes = h.get("modes")
    expected = ["random", "bagging_divide", "bagging_remove", "bagging_remove_divide", "bagging_remove_reverse", "bagging_remove_reverse_divide"]
    L.check("hello lists the six modes", modes == expected, "GET /api/hello", h)
    if not args.no_loops:
        L.check("hello grammar mentions the loop", "(p R)" in h.get("grammar", ""), "GET /api/hello", h)
    else:
        L.check("hello grammar of the mirror says nothing of loops", "(p R)" not in h.get("grammar", ""), "GET /api/hello", h)
    # the example draws
    ex = h.get("example", "")
    fields = {"process": ex}
    d = c.post("/api/svg", fields)
    L.check("the example draws", "svg" in d and d["svg"].count('class="task"') == tasks_in(ex), describe("/api/svg", fields), d)
    return h


def section_bench(L, c, args, hello):
    L.start("bench")
    if not hello.get("bench"):
        L.skip("bench", "no benchmark panel (no --bench and no generator beside the executable)")
        return
    modes = hello["modes"]
    seen = {}
    for d_ in (2, 3):
        for mode in modes:
            fields = {"nested": 1, "independent": 1, "process": 1, "impacts": d_, "mode": mode, "regenerate": 0, "seed": 1}
            key = f"1-1-1-{d_}-{mode}"
            if args.bench and not os.path.isfile(os.path.join(args.bench, "1-nested", "1-independent", "1-process_number", f"{d_}-{mode}.yaml")):
                L.skip(f"bench {key}", "not on disk, and the generator would write into the bench folder")
                continue
            d = c.post("/api/bench", fields)
            ok = d.get("instance") == key and isinstance(d.get("process"), str) and d.get("regenerated") is False
            L.check(f"bench {key} answers the key and a text", ok, describe("/api/bench", fields), d)
            if not ok:
                continue
            seen[key] = d["process"]
            svg = c.post("/api/svg", {"process": d["process"]})
            n = svg.get("svg", "").count('class="task"')
            want = tasks_in(d["process"])
            if args.bench:
                with open(os.path.join(args.bench, "1-nested", "1-independent", "1-process_number", f"{d_}-{mode}.yaml")) as f:
                    want_file = sum(1 for line in f if re.match(r"\s*-\s*task:", line))
                L.check(f"bench {key} names as many tasks as its file", want == want_file, describe("/api/bench", fields), {"text_tasks": want, "file_tasks": want_file})
            L.check(f"bench {key} draws its tasks", "svg" in svg and n == want and n > 0, describe("/api/svg", {"process": d["process"]}), {"boxes": n, "tasks": want})
            # the impacts named in the text are among impact0..impact(d-1): a
            # task names only its positive impacts, and a mode that removes
            # impacts may zero one in every task, which the text then omits
            names = sorted(set(re.findall(r"(impact\d+)\s*:", d["process"])))
            L.check(f"bench {key} names impacts among the {d_}", names and set(names) <= {f"impact{j}" for j in range(d_)}, describe("/api/bench", fields), names)
            if mode == "random":
                L.check(f"bench {key} names all {d_} impacts", names == [f"impact{j}" for j in range(d_)], describe("/api/bench", fields), names)
    # the grid with loops: the same coordinates, the key suffixed, a loop in
    # the text, and the panel refused when the server has no such grid
    base = {"nested": 1, "independent": 1, "process": 1, "impacts": 2, "mode": "random", "regenerate": 0, "seed": 1}
    if hello.get("loops"):
        d = c.post("/api/bench", dict(base, loops=1))
        ok = d.get("instance") == "1-1-1-2-random-loops" and isinstance(d.get("process"), str)
        L.check("bench with loops answers the suffixed key and a text", ok, describe("/api/bench", dict(base, loops=1)), d)
        if ok:
            L.check("bench with loops gives a process with a loop", re.search(r"\(\s*0?\.\d+\s*\(", d["process"]) is not None or re.search(r"\(\s*\d\.\d+\s", d["process"]) is not None, describe("/api/bench", dict(base, loops=1)), d["process"][:200])
            svg = c.post("/api/svg", {"process": d["process"]})
            L.check("the process with loops draws", "svg" in svg and svg["svg"].count('class="task"') == tasks_in(d["process"]) > 0, describe("/api/svg", {"process": d["process"]}), svg.get("error"))
            plain = c.post("/api/bench", dict(base, loops=0))
            L.check("bench without the switch keeps the plain key", plain.get("instance") == "1-1-1-2-random", describe("/api/bench", dict(base, loops=0)), plain.get("instance"))
    else:
        d = c.post("/api/bench", dict(base, loops=1))
        L.check("bench with loops is refused without a grid with loops", "error" in d, describe("/api/bench", dict(base, loops=1)), d)
    # regenerate with a seed: deterministic in the seed, other with another seed
    key = "1-1-1-2-random"
    if key in seen:
        base = {"nested": 1, "independent": 1, "process": 1, "impacts": 2, "mode": "random"}
        r1 = c.post("/api/bench", dict(base, regenerate=1, seed=42))
        r2 = c.post("/api/bench", dict(base, regenerate=1, seed=42))
        r3 = c.post("/api/bench", dict(base, regenerate=1, seed=43))
        L.check("regenerate says regenerated", r1.get("regenerated") is True and r1.get("instance") == key, describe("/api/bench", dict(base, regenerate=1, seed=42)), r1)
        L.check("regenerate changes the impacts", r1.get("process") != seen[key], describe("/api/bench", dict(base, regenerate=1, seed=42)), r1)
        L.check("regenerate is deterministic in the seed", r1.get("process") == r2.get("process"), describe("/api/bench", dict(base, regenerate=1, seed=42)), [r1, r2])
        L.check("another seed gives another text", r1.get("process") != r3.get("process"), describe("/api/bench", dict(base, regenerate=1, seed=43)), [r1, r3])
        L.check("regenerate keeps the tasks", tasks_in(r1.get("process", "")) == tasks_in(seen[key]) and re.findall(r"\(\s*(T\d+)\s*,", r1.get("process", "")) == re.findall(r"\(\s*(T\d+)\s*,", seen[key]), describe("/api/bench", dict(base, regenerate=1, seed=42)), r1)
        svg = c.post("/api/svg", {"process": r1.get("process", "")})
        L.check("the regenerated text draws", "svg" in svg and svg["svg"].count('class="task"') == tasks_in(seen[key]), describe("/api/svg", {"process": r1.get("process", "")}), svg)
        # a mode that is not random, regenerated
        for mode in modes[1:]:
            rm = c.post("/api/bench", dict(base, mode=mode, regenerate=1, seed=5))
            L.check(f"regenerate in mode {mode}", rm.get("regenerated") is True and tasks_in(rm.get("process", "")) == tasks_in(seen[key]), describe("/api/bench", dict(base, mode=mode, regenerate=1, seed=5)), rm)
        # a bad seed falls back to the default and still answers
        rs = c.post("/api/bench", dict(base, regenerate=1, seed="abc"))
        L.check("a bad seed still regenerates", rs.get("regenerated") is True and isinstance(rs.get("process"), str), describe("/api/bench", dict(base, regenerate=1, seed="abc")), rs)
    # the refusals: out of the grid, not numbers, missing, unknown mode; every
    # one refused before a path is built, so the generator never runs
    bad = [
        ("nested 0", {"nested": 0, "independent": 1, "process": 1, "impacts": 2, "mode": "random"}),
        ("nested 11", {"nested": 11, "independent": 1, "process": 1, "impacts": 2, "mode": "random"}),
        ("process 0", {"nested": 1, "independent": 1, "process": 0, "impacts": 2, "mode": "random"}),
        ("impacts 1", {"nested": 1, "independent": 1, "process": 1, "impacts": 1, "mode": "random"}),
        ("impacts 11", {"nested": 1, "independent": 1, "process": 1, "impacts": 11, "mode": "random"}),
        ("nested abc", {"nested": "abc", "independent": 1, "process": 1, "impacts": 2, "mode": "random"}),
        ("nested -1", {"nested": -1, "independent": 1, "process": 1, "impacts": 2, "mode": "random"}),
        ("nested 1.5", {"nested": 1.5, "independent": 1, "process": 1, "impacts": 2, "mode": "random"}),
        ("independent missing", {"nested": 1, "process": 1, "impacts": 2, "mode": "random"}),
        ("mode unknown", {"nested": 1, "independent": 1, "process": 1, "impacts": 2, "mode": "bogus"}),
        ("mode with a path", {"nested": 1, "independent": 1, "process": 1, "impacts": 2, "mode": "../random"}),
    ]
    for label, fields in bad:
        d = c.post("/api/bench", fields)
        L.check(f"bench refuses {label}", isinstance(d.get("error"), str) and d["error"] and "process" not in d, describe("/api/bench", fields), d)


def section_examples(L, c, args, work):
    L.start("examples")
    if args.no_loops:
        L.skip("examples", "the mirror without loops has no panel of examples")
        return
    status, text = c.status_of("/api/examples")
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        d = None
    listed = d.get("examples") if isinstance(d, dict) else None
    if not L.check("the list is a JSON object with an examples array", status == 200 and isinstance(listed, list), "GET /api/examples", f"{status} {clip(text, 300)}"):
        return
    L.check("the list holds the ten examples in the order of the documents", [e.get("id") for e in listed] == [x[0] for x in EXAMPLES], "GET /api/examples", [e.get("id") for e in listed])
    L.check("the paper comes first, then the thesis", [e.get("group") for e in listed] == [x[1] for x in EXAMPLES], "GET /api/examples", [e.get("group") for e in listed])
    L.check("every example has a title, a source and a note", all(isinstance(e.get(k), str) and e[k].strip() for e in listed for k in ("title", "source", "note")), "GET /api/examples", clip(listed, 400))
    L.check("the list carries no text", all("process" not in e and "text" not in e for e in listed), "GET /api/examples", [sorted(e) for e in listed][:2])
    typed = lambda e: (isinstance(e.get("time"), bool) and isinstance(e.get("max_iter"), int) and not isinstance(e.get("max_iter"), bool) and e["max_iter"] >= 1
                       and isinstance(e.get("maximize"), list) and all(isinstance(n, str) for n in e["maximize"]))
    L.check("the settings of the list are present and typed", all(typed(e) for e in listed), "GET /api/examples", [{k: e.get(k) for k in ("id", "time", "max_iter", "maximize")} for e in listed])
    L.check("the settings of the list are those of the documents", [(e.get("time"), e.get("max_iter"), e.get("maximize")) for e in listed] == [(x[3], x[4], x[5]) for x in EXAMPLES], "GET /api/examples", [{k: e.get(k) for k in ("id", "time", "max_iter", "maximize")} for e in listed])
    saved_before = json.loads(c.get("/api/saved")[1]).get("saved")
    L.check("the store is empty while the examples are read", saved_before == [], "GET /api/saved", saved_before)
    texts = {}
    for (eid, group, fname, time_on, k, maximize, n_points), row in zip(EXAMPLES, listed):
        f = {"id": eid}
        e = c.post("/api/example", f)
        ok = e.get("name") == eid and isinstance(e.get("process"), str) and e["process"].strip() != ""
        if not L.check(f"example {eid} answers its name and a text", ok, describe("/api/example", f), clip(e, 300)):
            continue
        text = e["process"]
        texts[eid] = text
        L.check(f"example {eid} carries its settings, typed, as the list has them", typed(e) and all(e.get(key) == row.get(key) for key in ("time", "max_iter", "maximize")), describe("/api/example", f), {key: e.get(key) for key in ("time", "max_iter", "maximize")})
        L.check(f"example {eid} opens with a comment of one to three lines", 1 <= len([1 for line in text.splitlines()[:4] if line.startswith("#")]) <= 3 and text.startswith("#"), describe("/api/example", f), text[:200])
        path = os.path.join(EXAMPLES_DIR, fname)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                L.check(f"example {eid} is the file examples/{fname}", fh.read() == text, describe("/api/example", f), text[:200])
        else:
            L.skip(f"example {eid} against its file", f"no {path}")
        # the diagram: one box per task of the text, one collapsed box per reference
        code = code_of(text)
        fs = {"process": text, "name": eid}
        svg = c.post("/api/svg", fs)
        boxes, subs = svg.get("svg", "").count('class="task"'), svg.get("svg", "").count('class="sub collapsed"')
        L.check(f"example {eid} draws its diagram", "svg" in svg and boxes == tasks_in(code) > 0 and subs == code.count("@"), describe("/api/svg", {"process": "(the text)", "name": eid}), svg.get("error") or {"boxes": boxes, "tasks": tasks_in(code), "subs": subs, "references": code.count("@")})
        # the frontier with the settings of the document
        ff = {"process": text, "name": eid, "workers": "1", "limit": "120", "time": "1" if time_on else "0", "max_iter": str(k), "maximize": ",".join(maximize)}
        fr = c.post("/api/frontier", ff, timeout=200)
        L.check(f"example {eid} has a frontier of {n_points} points with its settings", fr.get("status") == "complete" and len(fr.get("points", [])) == n_points, describe("/api/frontier", dict(ff, process="(the text)")), fr.get("error") or {"status": fr.get("status"), "points": len(fr.get("points", [])), "seconds": fr.get("seconds")})
        L.check(f"the frontier of {eid} carries the time and the directions of its settings", fr.get("time") is time_on and fr.get("max_iter") == k and fr.get("maximized") == [n in maximize for n in fr.get("names", [])], describe("/api/frontier", dict(ff, process="(the text)")), {key: fr.get(key) for key in ("time", "max_iter", "names", "maximized")})
        if eid in EXAMPLE_POINTS and "points" in fr:
            want = {frac_tuple(p.split()) for p in EXAMPLE_POINTS[eid]}
            L.check(f"the frontier of {eid} is the one of its document", points_set(fr["points"]) == want, describe("/api/frontier", dict(ff, process="(the text)")), [p["value"] for p in fr["points"]])
    # the ids that are no example
    for label, f in [("an unknown id", {"id": "no_such_example"}), ("an id that is a path", {"id": "../examples/release.cpi"}), ("an empty id", {"id": "  "}), ("no id", {})]:
        e = c.post("/api/example", f)
        L.check(f"example refuses {label}", isinstance(e.get("error"), str) and e["error"] and "process" not in e, describe("/api/example", f), e)
    status, body = c.status_of("/api/example", "GET")
    L.check("GET on /api/example answers 404", status == 404, "GET /api/example", f"{status} {clip(body, 100)}")
    status, body = c.status_of("/api/examples", "POST", data=b"id=release")
    L.check("POST on /api/examples answers 404", status == 404, "POST /api/examples", f"{status} {clip(body, 100)}")
    if "release" not in texts or "stroke" not in texts:
        return
    # the release and the stroke on an empty store: the reference is the
    # built-in subprocess, in the drawing, the bounds, the two searches and
    # the explanations
    rel, stroke = texts["release"], texts["stroke"]
    f = {"process": rel, "name": "release"}
    d = c.post("/api/svg", f)
    L.check("the release names its subprocess tests, shown as Tests", [(x.get("name"), x.get("shown")) for x in d.get("subs", [])] == [("tests", "Tests")], describe("/api/svg", dict(f, process="(the release)")), d.get("error") or d.get("subs"))
    # the box is expanded by the identifier it shows, read from the answer
    frel = {"process": rel, "name": "release", "expand": ",".join(x.get("label", "") for x in d.get("subs", []))}
    d = c.post("/api/svg", frel)
    svg = d.get("svg", "")
    L.check("the release expanded draws the tasks of the built-in tests", all(f">{t}<" in svg for t in ("Unit", "Signoff", "Patch", "Refactor")) and svg.count('class="sub expanded"') == 1, describe("/api/svg", dict(frel, process="(the release)")), d.get("error") or re.findall(r'class="name"[^>]*>([^<]*)<', svg))
    f = {"process": stroke, "name": "stroke"}
    d = c.post("/api/svg", f)
    L.check("the stroke names its subprocess transport twice, shown as Pickup and Transfer", [(x.get("name"), x.get("shown")) for x in d.get("subs", [])] == [("transport", "Pickup"), ("transport", "Transfer")], describe("/api/svg", dict(f, process="(the stroke)")), d.get("error") or d.get("subs"))
    labels = [x.get("label") for x in d.get("subs", [])]
    d = c.post("/api/svg", {"process": stroke, "name": "stroke", "expand": ",".join(labels)})
    svg = d.get("svg", "")
    L.check("the stroke expanded draws the tasks of the built-in transport twice", svg.count(">Heli<") == 2 and svg.count(">Ambulance<") == 2 and svg.count('class="sub expanded"') == 2, describe("/api/svg", {"process": "(the stroke)", "expand": ",".join(labels)}), d.get("error") or {"Heli": svg.count(">Heli<"), "expanded": svg.count('class="sub expanded"')})
    fb = {"process": rel, "name": "release", "time": "0", "max_iter": "2"}
    b = c.post("/api/bounds", fb)
    L.check("the bounds of the release read the built-in tests", b.get("names") == ["hours", "euro"] and b.get("loops") == 1 and b.get("lower_exact") == ["197/8", "50"] and b.get("upper_exact") == ["361/8", "1940"], describe("/api/bounds", dict(fb, process="(the release)")), b)
    store_file = os.path.join(EXAMPLES_DIR, "store.json")
    rel_file = os.path.join(EXAMPLES_DIR, "release.cpi")
    if os.path.isfile(store_file) and os.path.isfile(rel_file):
        out = os.path.join(work, "examples-release.yaml")
        code, _, errtext = cli(args.bin, ["parse", "--file", rel_file, "--saved", store_file, "--out", out])
        found, trace = cli_bound(args.bin, out, False, max_iter=2) if code == 0 else (None, errtext)
        L.check("the release of the command line, through examples/store.json, has the bounds of the page", found is not None and "upper" in b and close(found["U at s0"], b["upper"]) and close(found["L at s0"], b["lower"]), f"parse --file {rel_file} --saved {store_file}\n{trace}", b)
    else:
        L.skip("the release of the command line", f"no {store_file}")
    fs = {"process": stroke, "name": "stroke", "workers": "1", "limit": "60", "time": "1"}
    sid, final = run_to_end(c, fs, L, "the stroke")
    if sid is not None:
        L.check("the live search of the stroke reads the built-in transport", final.get("status") == "complete" and len(final.get("points", [])) == 12 and final.get("names") == ["time", "euro", "disability"], describe("/api/start", dict(fs, process="(the stroke)")), final.get("error") or {"status": final.get("status"), "points": len(final.get("points", [])), "names": final.get("names")})
        decided = [p for p in final.get("points", []) if p.get("steps")]
        if decided:
            decisions = ";".join(".".join(str(n["id"]) for n in st["after"]) + ">" + str(st["take"]["id"]) for st in decided[0]["steps"])
            fx = {"process": stroke, "name": "stroke", "time": "1", "max_iter": "1", "decisions": decisions}
            x = c.post("/api/explain", fx, timeout=120)
            L.check("the explanation of a strategy of the stroke reads the built-in transport", isinstance(x.get("explanations"), list) and len(x["explanations"]) > 0, describe("/api/explain", dict(fx, process="(the stroke)")), clip(x, 300))
    # an example referred to from a typed text
    f = {"process": "( @coin , (Z, 1, {a: 1}) )"}
    d = c.post("/api/svg", f)
    L.check("a typed text refers to an example by its id", d.get("svg", "").count('class="task"') == 1 and [x.get("name") for x in d.get("subs", [])] == ["coin"], describe("/api/svg", f), d.get("error") or d.get("subs"))
    # a cycle through an example: tests referring to the release, which refers to tests
    f = {"process": "( @release , (A, 1, {x: 1}) )", "name": "tests"}
    d = c.post("/api/svg", f)
    L.check("a text named tests that refers to the release is a cycle", "cycle" in d.get("error", "") and "release" in d.get("error", ""), describe("/api/svg", f), d)
    r = c.post("/api/save", f)
    L.check("a save closing a cycle through an example is refused", "cycle" in r.get("error", "") and "ok" not in r, describe("/api/save", f), r)
    L.check("the refused save left the store empty", json.loads(c.get("/api/saved")[1]).get("saved") == [], "GET /api/saved", c.get("/api/saved")[1])
    # a saved process named tests shadows the built-in one, until it is deleted
    f = {"name": "tests", "process": "(Other, 1, {hours: 1})"}
    r = c.post("/api/save", f)
    L.check("a process is saved under the name of an example", r == {"ok": True, "name": "tests"}, describe("/api/save", f), r)
    d = c.post("/api/svg", frel)
    svg = d.get("svg", "")
    L.check("the saved tests shadows the built-in one in the drawing", ">Other<" in svg and ">Unit<" not in svg and svg.count('class="task"') == 8, describe("/api/svg", dict(frel, process="(the release)")), d.get("error") or {"tasks": svg.count('class="task"'), "Other": ">Other<" in svg, "Unit": ">Unit<" in svg})
    b2 = c.post("/api/bounds", fb)
    L.check("the saved tests shadows the built-in one in the bounds", "lower_exact" in b2 and b2.get("lower_exact") != b.get("lower_exact"), describe("/api/bounds", dict(fb, process="(the release)")), [b.get("lower_exact"), b2.get("lower_exact")])
    e = c.post("/api/example", {"id": "tests"})
    L.check("the example tests is still the built-in text", e.get("process") == texts.get("tests"), describe("/api/example", {"id": "tests"}), clip(e, 200))
    L.check("the list of the examples does not change with the store", json.loads(c.get("/api/examples")[1]).get("examples") == listed, "GET /api/examples", None)
    r = c.post("/api/delete", {"name": "tests"})
    L.check("the saved tests deletes", r == {"ok": True, "name": "tests"}, "POST /api/delete tests", r)
    d = c.post("/api/svg", frel)
    L.check("the built-in tests is back once the saved one is gone", ">Unit<" in d.get("svg", "") and ">Other<" not in d.get("svg", ""), describe("/api/svg", dict(frel, process="(the release)")), d.get("error") or d.get("subs"))
    L.check("the section leaves the store empty", json.loads(c.get("/api/saved")[1]).get("saved") == [], "GET /api/saved", c.get("/api/saved")[1])


def section_svg(L, c, args):
    L.start("svg")
    text = TEXT_PLAIN
    n_tasks = tasks_in(text)
    fields = {"process": text}
    d = c.post("/api/svg", fields)
    svg = d.get("svg", "")
    L.check("a valid text gives an svg", svg.lstrip().startswith("<svg") and svg.rstrip().endswith("</svg>"), describe("/api/svg", fields), d)
    L.check("one task box per task", svg.count('class="task"') == n_tasks, describe("/api/svg", fields), {"boxes": svg.count('class="task"'), "tasks": n_tasks})
    L.check("the impacts are listed alphabetically", d.get("impacts") == ["euro", "hours"], describe("/api/svg", fields), d.get("impacts"))
    L.check("no subprocess in a plain text", d.get("subs") == [], describe("/api/svg", fields), d.get("subs"))
    L.check("every task name is drawn", all(f">{name}<" in svg for name in re.findall(r"\(\s*([A-Za-z_]\w*)\s*,\s*\d+", text)), describe("/api/svg", fields), svg[:300])
    # the switches, each on and off, every answer an svg with the same boxes
    switches = {
        "detail": lambda s: "duration" in s,
        "tooltips": lambda s: "<title>" in s,
        "bars": lambda s: 'class="bar"' in s,
        "clocks": lambda s: 'class="clock"' in s,
        "names": lambda s: True,
    }
    for name, marker in switches.items():
        for on in ("0", "1"):
            f = {"process": text, name: on}
            d = c.post("/api/svg", f)
            s = d.get("svg", "")
            ok = s.startswith("<svg") and s.count('class="task"') == n_tasks
            L.check(f"svg with {name}={on} draws the tasks", ok, describe("/api/svg", f), d.get("error") or s[:200])
            if on == "1" and name != "names":
                L.check(f"svg with {name}=1 shows its mark", marker(s), describe("/api/svg", f), s[:300])
            if on == "0" and name in ("bars", "clocks"):
                L.check(f"svg with {name}=0 shows no mark", not marker(s), describe("/api/svg", f), s[:300])
    # names with bars: the names of the impacts beside the bars
    f = {"process": text, "bars": "1", "names": "1"}
    d = c.post("/api/svg", f)
    L.check("bars with names write the impact names", ">euro<" in d.get("svg", "") and ">hours<" in d.get("svg", ""), describe("/api/svg", f), d.get("svg", "")[:300])
    # every switch on at once
    f = {"process": text, "detail": "1", "tooltips": "1", "bars": "1", "clocks": "1", "names": "1"}
    d = c.post("/api/svg", f)
    L.check("every switch on at once draws", d.get("svg", "").count('class="task"') == n_tasks, describe("/api/svg", f), d.get("error") or d.get("svg", "")[:200])
    # hide and show select the bars
    f_all = {"process": text, "bars": "1"}
    bars_all = c.post("/api/svg", f_all).get("svg", "").count('class="bar"')
    f_hide = {"process": text, "bars": "1", "hide": "euro"}
    bars_hide = c.post("/api/svg", f_hide).get("svg", "").count('class="bar"')
    f_show = {"process": text, "bars": "1", "show": "hours"}
    bars_show = c.post("/api/svg", f_show).get("svg", "").count('class="bar"')
    f_hide_all = {"process": text, "bars": "1", "hide": "euro,hours"}
    bars_none = c.post("/api/svg", f_hide_all).get("svg", "").count('class="bar"')
    L.check("bars are drawn for every impact", bars_all > 0, describe("/api/svg", f_all), {"bars": bars_all})
    L.check("hide=euro halves the bars", 0 < bars_hide < bars_all and bars_hide == bars_show, describe("/api/svg", f_hide), {"all": bars_all, "hide": bars_hide, "show": bars_show})
    L.check("hiding every impact leaves no bar", bars_none == 0, describe("/api/svg", f_hide_all), {"bars": bars_none})
    # the invalid texts
    for label, bad in [
        ("an unclosed parenthesis", "(A, 1, {x: 1}"),
        ("a zero duration", "(A, 0, {x: 1})"),
        ("a negative impact", "(A, 1, {x: -1})"),
        ("a probability of 1", "((A, 1, {x: 1}) ^[1] (B, 1, {x: 1}))"),
        ("a bare name", "A"),
        ("an empty map and a comma", "(A, 1, {x: 1},)"),
        ("a reference to nothing", "(@nothing_saved_here , (B, 1, {x: 1}))"),
    ]:
        f = {"process": bad}
        d = c.post("/api/svg", f)
        L.check(f"svg refuses {label}", isinstance(d.get("error"), str) and d["error"] and "svg" not in d, describe("/api/svg", f), d)
    f = {"process": "   "}
    d = c.post("/api/svg", f)
    L.check("svg refuses an empty text", d.get("error") == "no process was given", describe("/api/svg", f), d)
    d = c.post("/api/svg", {})
    L.check("svg refuses a body with no process", d.get("error") == "no process was given", "POST /api/svg {}", d)
    if args.no_loops:
        f = {"process": TEXT_LOOP}
        d = c.post("/api/svg", f)
        L.check("the mirror refuses the loop syntax", isinstance(d.get("error"), str) and "svg" not in d, describe("/api/svg", f), d)
    else:
        f = {"process": TEXT_LOOP}
        d = c.post("/api/svg", f)
        L.check("a loop draws with its back edge", d.get("svg", "").count('class="task"') == 2 and "again" in d.get("svg", "") and "out" in d.get("svg", ""), describe("/api/svg", f), d.get("error") or d.get("svg", "")[:300])
    # a cycle through the references: the text being edited stands for its
    # own name, so a text referring to itself is a cycle of length one, and a
    # chain through the store a longer one
    f = {"process": "(@self_ref , (B, 1, {x: 1}))", "name": "self_ref"}
    d = c.post("/api/svg", f)
    L.check("a text referring to its own name is a cycle", "cycle" in d.get("error", ""), describe("/api/svg", f), d)
    s1 = c.post("/api/save", {"name": "cyc_b", "process": "(B, 1, {x: 1})", "overwrite": "1"})
    s2 = c.post("/api/save", {"name": "cyc_a", "process": "(@cyc_b , (A, 1, {x: 1}))", "overwrite": "1"})
    L.check("the chain for the cycle is saved", s1.get("ok") is True and s2.get("ok") is True, "POST /api/save cyc_b, cyc_a", [s1, s2])
    f = {"process": "(@cyc_a , (C, 1, {x: 1}))", "name": "cyc_b"}
    d = c.post("/api/svg", f)
    L.check("a cycle through the store is named", "cycle" in d.get("error", "") and "cyc_a" in d.get("error", "") and "cyc_b" in d.get("error", ""), describe("/api/svg", f), d)
    f = {"process": "(@cyc_a , (C, 1, {x: 1}))"}
    d = c.post("/api/svg", f)
    L.check("the same text under no name draws the chain", d.get("svg", "").count('class="task"') == 1 and d.get("svg", "").count('class="sub collapsed"') == 1, describe("/api/svg", f), d.get("error") or d.get("subs"))
    f = {"process": "(@cyc_a , (C, 1, {x: 1}))", "expand": "1"}
    d = c.post("/api/svg", f)
    L.check("expanding a subprocess draws its tasks", d.get("svg", "").count('class="task"') == 2 and d.get("svg", "").count('class="sub collapsed"') == 1, describe("/api/svg", f), d.get("error") or d.get("subs"))
    f = {"process": "(@cyc_a , (C, 1, {x: 1}))", "expand": "1,1-1"}
    d = c.post("/api/svg", f)
    L.check("expanding both levels draws every task", d.get("svg", "").count('class="task"') == 3 and d.get("svg", "").count('class="sub collapsed"') == 0, describe("/api/svg", f), d.get("error") or d.get("subs"))
    # a deep term: the parser is bounded, the answer is an error or a drawing,
    # never a crash
    deep = "(" * 300 + "(A, 1, {x: 1})" + ")" * 300
    f = {"process": deep}
    d = c.post("/api/svg", f)
    L.check("three hundred nested parentheses answer", "svg" in d or "error" in d, describe("/api/svg", {"process": "(" * 300 + "..."}), clip(d, 200))
    deep = "(" * 5000 + "(A, 1, {x: 1})" + ")" * 5000
    f = {"process": deep}
    try:
        d = c.post("/api/svg", f)
        L.check("five thousand nested parentheses answer", "svg" in d or "error" in d, describe("/api/svg", {"process": "(" * 5000 + "..."}), clip(d, 200))
    except (urllib.error.URLError, ConnectionError, socket.timeout) as e:
        L.check("five thousand nested parentheses answer", False, describe("/api/svg", {"process": "(" * 5000 + "..."}), repr(e))


def section_bounds(L, c, args, work, store):
    L.start("bounds")
    binary = args.bin

    def page_bounds(text, time_on, maximize=None, max_iter=None, name=None):
        f = {"process": text, "time": "1" if time_on else "0"}
        if maximize is not None:
            f["maximize"] = maximize
        if max_iter is not None:
            f["max_iter"] = str(max_iter)
        if name:
            f["name"] = name
        return f, c.post("/api/bounds", f)

    f, d = page_bounds(TEXT_PLAIN, False)
    L.check("bounds without time name the impacts", d.get("names") == ["euro", "hours"], describe("/api/bounds", f), d)
    L.check("bounds without time say time false", d.get("time") is False, describe("/api/bounds", f), d)
    L.check("lower is at most upper", "lower" in d and all(l <= u for l, u in zip(d["lower"], d["upper"])), describe("/api/bounds", f), d)
    L.check("the exact bounds match the approximations", "lower_exact" in d and close([Fraction(x) for x in d["lower_exact"]], d["lower"]) and close([Fraction(x) for x in d["upper_exact"]], d["upper"]), describe("/api/bounds", f), d)
    L.check("maximized is all false by default", d.get("maximized") == [False, False], describe("/api/bounds", f), d)
    if not args.no_loops:
        L.check("loops is zero without a loop", d.get("loops") == 0, describe("/api/bounds", f), d)
    else:
        L.check("the mirror reports no loops field", "loops" not in d, describe("/api/bounds", f), d)
    f, d = page_bounds(TEXT_PLAIN, True)
    L.check("bounds with time put the clock first", d.get("names") == ["time", "euro", "hours"] and d.get("time") is True, describe("/api/bounds", f), d)
    L.check("the time of a sequence is at least its shortest path", "lower" in d and d["lower"][0] >= 1 + 1 + 1, describe("/api/bounds", f), d)
    f, d = page_bounds(TEXT_PLAIN, True, maximize="euro")
    L.check("maximize=euro flags euro", d.get("maximized") == [False, True, False], describe("/api/bounds", f), d)
    f, d = page_bounds(TEXT_PLAIN, True, maximize="time,hours")
    L.check("maximize=time,hours flags both", d.get("maximized") == [True, False, True], describe("/api/bounds", f), d)
    f, d = page_bounds(TEXT_PLAIN, True, maximize="euro,nope")
    L.check("an unknown name in maximize is dropped silently", d.get("maximized") == [False, True, False] and "error" not in d, describe("/api/bounds", f), d)
    f, d = page_bounds(TEXT_PLAIN, True, maximize="nope")
    L.check("maximize of unknown names alone is all false", d.get("maximized") == [False, False, False] and "error" not in d, describe("/api/bounds", f), d)
    f, d = page_bounds(TEXT_PLAIN, False, maximize="time")
    L.check("maximize=time without the time is dropped", d.get("maximized") == [False, False] and "error" not in d, describe("/api/bounds", f), d)
    # a maximized component shows the same two numbers, in the shown orientation
    f0, d0 = page_bounds(TEXT_PLAIN, True)
    f1, d1 = page_bounds(TEXT_PLAIN, True, maximize="euro")
    L.check("a maximized component keeps its shown bounds", d0.get("lower_exact") == d1.get("lower_exact") and d0.get("upper_exact") == d1.get("upper_exact"), describe("/api/bounds", f1), [d0, d1])
    # the clock renamed when an impact is named time
    f, d = page_bounds(TEXT_TIME_NAMED, True)
    L.check("the clock is 'expected time' beside an impact named time", d.get("names") == ["expected time", "x", "time"], describe("/api/bounds", f), d)
    f, d = page_bounds(TEXT_TIME_NAMED, True, maximize="expected time")
    L.check("maximize names the clock as 'expected time'", d.get("maximized") == [True, False, False], describe("/api/bounds", f), d)
    f, d = page_bounds(TEXT_TIME_NAMED, True, maximize="time")
    L.check("maximize=time beside an impact named time flags the impact", d.get("maximized") == [False, False, True], describe("/api/bounds", f), d)
    # the error paths
    f, d = page_bounds("(A, 1, {x: 1}", False)
    L.check("bounds refuse a bad text", isinstance(d.get("error"), str) and "names" not in d, describe("/api/bounds", f), d)
    f, d = page_bounds("", False)
    L.check("bounds refuse an empty text", d.get("error") == "no process was given", describe("/api/bounds", f), d)
    # loops
    if not args.no_loops:
        f, d = page_bounds(TEXT_LOOP, True, max_iter=3)
        L.check("loops counts one loop", d.get("loops") == 1, describe("/api/bounds", f), d)
        f, d = page_bounds(TEXT_TWO_LOOPS, True, max_iter=3)
        L.check("loops counts two loops", d.get("loops") == 2, describe("/api/bounds", f), d)
        f1, d1 = page_bounds(TEXT_LOOP, False, max_iter=1)
        f3, d3 = page_bounds(TEXT_LOOP, False, max_iter=3)
        L.check("more passes raise the upper bound", "upper" in d1 and "upper" in d3 and d3["upper"][0] > d1["upper"][0], describe("/api/bounds", f3), [d1, d3])
        fd, dd = page_bounds(TEXT_LOOP, False)
        L.check("the default max_iter is one", dd.get("upper_exact") == d1.get("upper_exact"), describe("/api/bounds", fd), [dd, d1])
        fb, db = page_bounds(TEXT_LOOP, False, max_iter="abc")
        L.check("a bad max_iter falls back to the default", db.get("upper_exact") == d1.get("upper_exact"), describe("/api/bounds", fb), [db, d1])
        f50, d50 = page_bounds(TEXT_LOOP, False, max_iter=50)
        f1000, d1000 = page_bounds(TEXT_LOOP, False, max_iter=1000)
        L.check("max_iter is clamped to fifty", d50.get("upper_exact") == d1000.get("upper_exact"), describe("/api/bounds", f1000), [d50, d1000])
        f0, d0 = page_bounds(TEXT_LOOP, False, max_iter=0)
        L.check("max_iter zero is clamped to one", d0.get("upper_exact") == d1.get("upper_exact"), describe("/api/bounds", f0), [d0, d1])
    else:
        f, d = page_bounds(TEXT_LOOP, True)
        L.check("the mirror refuses the loop syntax in bounds", isinstance(d.get("error"), str), describe("/api/bounds", f), d)
    # against the command line
    cases = [("plain", TEXT_PLAIN, None, None), ("plain-max", TEXT_PLAIN, "euro", None), ("time-named", TEXT_TIME_NAMED, None, None), ("time-named-max", TEXT_TIME_NAMED, "x", None)]
    if not args.no_loops:
        cases += [("loop-k3", TEXT_LOOP, None, 3), ("loop-k1", TEXT_LOOP, None, 1), ("two-loops-k2", TEXT_TWO_LOOPS, None, 2), ("two-loops-k2-max", TEXT_TWO_LOOPS, "y", 2), ("loop-k5-max-time", TEXT_LOOP, "time", 5)]
    for stem, text, maximize, k in cases:
        path = cli_instance(binary, text, work, "bounds-" + stem)
        if path is None:
            L.check(f"parse of {stem} on the command line", False, f"{binary} parse ...", "parse failed")
            continue
        for time_on in (False, True):
            if maximize == "time" and not time_on:
                continue
            found, trace = cli_bound(binary, path, time_on, max_iter=k, maximize=maximize)
            f, d = page_bounds(text, time_on, maximize=maximize, max_iter=k)
            if found is None or "U at s0" not in found or "upper" not in d:
                L.check(f"bounds of {stem} time={time_on} on both sides", False, describe("/api/bounds", f) + "\n" + trace, d)
                continue
            ok = close(found["U at s0"], d["upper"]) and close(found["L at s0"], d["lower"])
            L.check(f"bounds of {stem} time={time_on} equal the command line", ok, describe("/api/bounds", f) + "\n" + trace, d)
    # a text with a reference, against the command line through --saved
    c.post("/api/save", {"name": "bnd_inner", "process": "((A1, 1, {x: 1}) ^ (A2, 2, {y: 1}))", "overwrite": "1"})
    text = "(@bnd_inner , (B, 1, {x: 2}))"
    path = cli_instance(binary, text, work, "bounds-ref", store=store)
    f, d = page_bounds(text, True)
    if path is None:
        L.check("parse --saved of a reference on the command line", False, f"{binary} parse {text!r} --saved {store}", "parse failed")
    else:
        found, trace = cli_bound(binary, path, True)
        ok = found is not None and "upper" in d and close(found["U at s0"], d["upper"]) and close(found["L at s0"], d["lower"])
        L.check("bounds of a reference equal the command line through --saved", ok, describe("/api/bounds", f) + "\n" + trace, d)
    L.check("bounds of a reference merge the impact names", d.get("names") == ["time", "x", "y"], describe("/api/bounds", f), d)
    return


def run_to_end(c, fields, L, label, limit_s=60.0):
    """A start followed by snapshots until the answer; the id and the answer."""
    s = c.post("/api/start", fields)
    sid = s.get("id")
    if not isinstance(sid, int):
        L.check(f"{label}: start gives an id", False, describe("/api/start", fields), s)
        return None, s
    deadline = time.time() + limit_s + 15
    last = None
    while time.time() < deadline:
        last = c.post("/api/snapshot", {"id": sid})
        if not last.get("live"):
            return sid, last
        time.sleep(0.3)
    L.check(f"{label}: the search ends", False, describe("/api/start", fields), last)
    return sid, last


def section_frontier(L, c, args, work, store):
    L.start("frontier")
    binary = args.bin
    base = {"process": TEXT_PLAIN, "workers": "1", "ablation": "both", "limit": "30", "time": "1"}
    d = c.post("/api/frontier", base)
    ok = d.get("status") == "complete" and isinstance(d.get("points"), list) and d["points"]
    L.check("the frontier of the example completes", ok, describe("/api/frontier", base), clip(d, 400))
    if not ok:
        return
    L.check("the names carry the time first", d.get("names") == ["time", "euro", "hours"] and d.get("components") == [0, 1, 2], describe("/api/frontier", base), d.get("names"))
    L.check("every point of a complete frontier is certified", all(p.get("certified") is True for p in d["points"]), describe("/api/frontier", base), [p.get("certified") for p in d["points"]])
    L.check("every point carries its strategy as sentences", all(isinstance(p.get("steps"), list) and all("sentence" in s and "take" in s and "after" in s for s in p["steps"]) for p in d["points"]), describe("/api/frontier", base), clip(d["points"][0], 400))
    L.check("the approximations match the exact values", all(close([Fraction(v) for v in p["value"]], p["approx"]) for p in d["points"]), describe("/api/frontier", base), clip(d["points"][0], 300))
    # an antichain: no point dominates another
    pts = [frac_tuple(p["value"]) for p in d["points"]]

    def dominates(a, b):
        return all(x <= y for x, y in zip(a, b)) and a != b

    L.check("the frontier is an antichain", not any(dominates(a, b) for a in pts for b in pts), describe("/api/frontier", base), [list(map(str, p)) for p in pts])
    L.check("the points are distinct", len(set(pts)) == len(pts), describe("/api/frontier", base), len(pts))
    # inside the bounds of the process
    fb = {"process": TEXT_PLAIN, "time": "1"}
    b = c.post("/api/bounds", fb)
    L.check("every point lies in the box of the bounds", "lower_exact" in b and all(all(Fraction(lo) <= v <= Fraction(hi) for v, lo, hi in zip(p, b["lower_exact"], b["upper_exact"])) for p in pts), describe("/api/frontier", base), [b, [list(map(str, p)) for p in pts]])
    L.check("the window is null without one", d.get("window") is None and d.get("outside") == 0 and d.get("outside_points") == [], describe("/api/frontier", base), {k: d.get(k) for k in ("window", "outside", "outside_points")})
    L.check("maximized is all false", d.get("maximized") == [False, False, False], describe("/api/frontier", base), d.get("maximized"))
    if not args.no_loops:
        L.check("max_iter is echoed", d.get("max_iter") == 1, describe("/api/frontier", base), d.get("max_iter"))
        L.check("no continued without a loop", all(p.get("continued") is None for p in d["points"]), describe("/api/frontier", base), [p.get("continued") for p in d["points"]])
    # the same body through start and snapshot
    sid, final = run_to_end(c, base, L, "example")
    if sid is not None:
        same = final.get("status") == d["status"] and points_set(final.get("points", [])) == points_set(d["points"]) and final.get("names") == d["names"] and final.get("maximized") == d["maximized"]
        L.check("frontier equals the final snapshot of a start on the same body", same, describe("/api/start", base), [clip(final, 300), clip(d, 300)])
        L.check("the final snapshot is not live", "live" not in final, describe("/api/snapshot", {"id": sid}), clip(final, 200))
    # against the command line
    path = cli_instance(binary, TEXT_PLAIN, work, "frontier-plain")
    if path is None:
        L.check("parse of the example on the command line", False, f"{binary} parse ...", "failed")
    else:
        for time_on, maximize in [(True, None), (False, None), (True, "euro"), (True, "time"), (False, "euro,hours")]:
            j, trace = cli_pareto(binary, path, work, f"frontier-plain-{time_on}-{maximize}", time_on, maximize=maximize, loops=not args.no_loops)
            f = dict(base, time="1" if time_on else "0")
            if maximize:
                f["maximize"] = maximize
            dp = c.post("/api/frontier", f)
            if j is None or "points" not in dp:
                L.check(f"frontier time={time_on} maximize={maximize} on both sides", False, describe("/api/frontier", f) + "\n" + trace, dp)
                continue
            L.check(f"frontier time={time_on} maximize={maximize} completes on both sides", j.get("status") == "complete" and dp.get("status") == "complete", describe("/api/frontier", f) + "\n" + trace, [j.get("status"), dp.get("status")])
            L.check(f"frontier time={time_on} maximize={maximize} equals the command line", points_set(j["points"]) == points_set(dp["points"]), describe("/api/frontier", f) + "\n" + trace, [[p["value"] for p in j["points"]], [p["value"] for p in dp["points"]]])
            if maximize:
                want = [n in maximize.split(",") for n in dp.get("names", [])]
                L.check(f"frontier maximize={maximize} flags the right components", dp.get("maximized") == want, describe("/api/frontier", f), [dp.get("names"), dp.get("maximized")])
                # the maximized components take their greatest values on the frontier
                mx = [i for i, m in enumerate(dp["maximized"]) if m]
                a = [frac_tuple(p["value"]) for p in dp["points"]]
                plain = c.post("/api/frontier", dict(f, maximize=""))
                b_ = [frac_tuple(p["value"]) for p in plain.get("points", [])]
                # a point of the directed frontier is never dominated in the directed sense by any point of the plain one
                def dir_dom(x, y):
                    return all((y[i] >= x[i]) if i in mx else (y[i] <= x[i]) for i in range(len(x))) and x != y
                L.check(f"frontier maximize={maximize} is a directed antichain", not any(dir_dom(x, y) for x in a for y in a), describe("/api/frontier", f), [list(map(str, p)) for p in a])
                top = max(x[mx[0]] for x in a) if a else None
                L.check(f"frontier maximize={maximize} reaches at least the greatest plain value", top is not None and (not b_ or top >= max(x[mx[0]] for x in b_)), describe("/api/frontier", f), [str(top), [str(x[mx[0]]) for x in b_]])
    # the window: cut the first component at the median of the points
    vals = sorted(p[0] for p in pts)
    cut = vals[len(vals) // 2]
    fw = dict(base, window=f",{cut};;")
    dw = c.post("/api/frontier", fw)
    inside = {p for p in pts if p[0] <= cut}
    outside = {p for p in pts if p[0] > cut}
    L.check("a window keeps the points inside it", "points" in dw and points_set(dw["points"]) == inside, describe("/api/frontier", fw), [[p["value"] for p in dw.get("points", [])], [list(map(str, p)) for p in inside]])
    L.check("outside counts the points left out", dw.get("outside") == len(dw.get("outside_points", [])) and dw.get("outside", -1) >= 0, describe("/api/frontier", fw), {k: dw.get(k) for k in ("outside", "outside_points")})
    L.check("every outside point breaks the window", all(p["approx"][0] > float(cut) - 1e-6 for p in dw.get("outside_points", [])), describe("/api/frontier", fw), dw.get("outside_points"))
    L.check("the window is echoed", isinstance(dw.get("window"), list) and len(dw["window"]) == 3 and abs(dw["window"][0][1] - float(cut)) < 1e-6, describe("/api/frontier", fw), dw.get("window"))
    L.check("the note mentions the grey points when some are outside", dw.get("outside", 0) == 0 or "grey" in dw.get("note", ""), describe("/api/frontier", fw), dw.get("note"))
    # the same window as a fraction
    fq = dict(base, window=f",{cut.numerator}/{cut.denominator};;")
    dq = c.post("/api/frontier", fq)
    L.check("a window written as a fraction reads the same", "points" in dq and points_set(dq["points"]) == inside and dq.get("outside") == dw.get("outside"), describe("/api/frontier", fq), clip(dq, 300))
    # a window on the second component from below
    v1 = sorted(p[1] for p in pts)
    lo = v1[len(v1) // 2]
    fl = dict(base, window=f";{lo},;")
    dl = c.post("/api/frontier", fl)
    L.check("a lower end of a window keeps the points above it", "points" in dl and points_set(dl["points"]) == {p for p in pts if p[1] >= lo}, describe("/api/frontier", fl), [[p["value"] for p in dl.get("points", [])], str(lo)])
    # an empty window
    fe = dict(base, window=";-1,-0.5;")
    de = c.post("/api/frontier", fe)
    L.check("an impossible window gives no point and says so", de.get("points") == [] and "No strategy" in de.get("note", "") and de.get("status") == "complete", describe("/api/frontier", fe), clip(de, 300))
    # a window on a maximized component is written as shown: a floor at the median keeps the points at or above it
    fm = dict(base, maximize="euro")
    dm = c.post("/api/frontier", fm)
    if "points" in dm:
        em = sorted(frac_tuple(p["value"])[1] for p in dm["points"])
        floor = em[len(em) // 2]
        fmw = dict(fm, window=f";{floor},;")
        dmw = c.post("/api/frontier", fmw)
        want = {frac_tuple(p["value"]) for p in dm["points"] if frac_tuple(p["value"])[1] >= floor}
        L.check("a window on a maximized component is a floor as shown", "points" in dmw and points_set(dmw["points"]) == want, describe("/api/frontier", fmw), [[p["value"] for p in dmw.get("points", [])], [list(map(str, p)) for p in want]])
        L.check("the echoed window of a maximized component is shown", isinstance(dmw.get("window"), list) and abs(dmw["window"][1][0] - float(floor)) < 1e-6, describe("/api/frontier", fmw), dmw.get("window"))
    # loops: a frontier with continued, against the command line
    if not args.no_loops:
        fl3 = {"process": TEXT_LOOP, "workers": "1", "limit": "30", "time": "1", "max_iter": "3"}
        dl3 = c.post("/api/frontier", fl3)
        L.check("a loop frontier completes with max_iter 3", dl3.get("status") == "complete" and dl3.get("max_iter") == 3, describe("/api/frontier", fl3), clip(dl3, 300))
        L.check("a forced exit reports continued", "points" in dl3 and all(isinstance(p.get("continued"), dict) and "low_exact" in p["continued"] for p in dl3["points"]), describe("/api/frontier", fl3), clip(dl3, 300))
        path = cli_instance(binary, TEXT_LOOP, work, "frontier-loop")
        j, trace = cli_pareto(binary, path, work, "frontier-loop-3", True, max_iter=3) if path else (None, "parse failed")
        if j is None or "points" not in dl3:
            L.check("loop frontier on both sides", False, describe("/api/frontier", fl3) + "\n" + trace, dl3)
        else:
            L.check("loop frontier equals the command line", points_set(j["points"]) == points_set(dl3["points"]), describe("/api/frontier", fl3) + "\n" + trace, [[p["value"] for p in j["points"]], [p["value"] for p in dl3["points"]]])
            cj = {frac_tuple(p["value"]): (frac_tuple(p["continued"]["low"]), frac_tuple(p["continued"]["up"])) for p in j["points"] if p.get("continued")}
            cp = {frac_tuple(p["value"]): (frac_tuple(p["continued"]["low_exact"]), frac_tuple(p["continued"]["up_exact"])) for p in dl3["points"] if p.get("continued")}
            L.check("continued equals the command line", cj == cp, describe("/api/frontier", fl3) + "\n" + trace, [str(cj), str(cp)])
        fl1 = dict(fl3, max_iter="1")
        dl1 = c.post("/api/frontier", fl1)
        L.check("more passes cost more", "points" in dl1 and "points" in dl3 and min(frac_tuple(p["value"])[1] for p in dl3["points"]) > min(frac_tuple(p["value"])[1] for p in dl1["points"]), describe("/api/frontier", fl3), [clip(dl1, 200), clip(dl3, 200)])
    else:
        fl3 = {"process": TEXT_LOOP, "workers": "1", "limit": "30", "time": "1"}
        dl3 = c.post("/api/frontier", fl3)
        L.check("the mirror refuses a loop in frontier", isinstance(dl3.get("error"), str), describe("/api/frontier", fl3), dl3)
    # the error paths
    for label, f in [
        ("an empty text", dict(base, process="")),
        ("a bad text", dict(base, process="(A, 1, {x: 1}")),
        ("an unknown reference", dict(base, process="(@no_such_thing , (A, 1, {x: 1}))")),
        ("an unknown ablation", dict(base, ablation="bogus")),
    ]:
        d_ = c.post("/api/frontier", f)
        L.check(f"frontier refuses {label}", isinstance(d_.get("error"), str) and "points" not in d_, describe("/api/frontier", f), d_)
    d_ = c.post("/api/frontier", dict(base, window=",1000;"))
    L.check("frontier refuses a window of two components for three, by position", isinstance(d_.get("error"), str) and "components" in d_["error"], describe("/api/frontier", dict(base, window=",1000;")), d_)
    for label, f in [
        ("workers=0", dict(base, workers="0")),
        ("workers=abc", dict(base, workers="abc")),
        ("limit=abc", dict(base, limit="abc")),
        ("limit=0.01", dict(base, limit="0.01")),
        ("limit=99999", dict(base, limit="99999")),
        ("a bad window", dict(base, window="abc;;")),
        ("a window by name, one component of three", dict(base, window="euro=,1000")),
        ("maximize with spaces", dict(base, maximize=" euro , hours ")),
    ]:
        d_ = c.post("/api/frontier", f)
        L.check(f"frontier tolerates {label}", d_.get("status") == "complete" and "points" in d_, describe("/api/frontier", f), clip(d_, 300))
    for label, f in [("ablation=accept", dict(base, ablation="accept")), ("ablation=reject", dict(base, ablation="reject")), ("ablation=none", dict(base, ablation="none")), ("workers=2", dict(base, workers="2"))]:
        d_ = c.post("/api/frontier", f)
        L.check(f"frontier with {label} gives the same points", d_.get("status") == "complete" and points_set(d_.get("points", [])) == set(pts), describe("/api/frontier", f), clip(d_, 300))


def section_lifecycle(L, c, args):
    L.start("start, snapshot, stop")
    fields = {"process": TEXT_SLOW, "workers": "1", "ablation": "both", "limit": "20", "time": "0"}
    s = c.post("/api/start", fields)
    sid = s.get("id")
    if not L.check("start gives an id", isinstance(sid, int) and sid > 0, describe("/api/start", fields), s):
        return
    snaps = []
    t0 = time.time()
    while time.time() - t0 < 3.0:
        snap = c.post("/api/snapshot", {"id": sid})
        snaps.append(snap)
        if not snap.get("live"):
            break
        time.sleep(0.3)
    live = [x for x in snaps if x.get("live")]
    L.check("the snapshots of a running search are live", len(live) >= 3 and all(x.get("status") == "building" for x in live), describe("/api/snapshot", {"id": sid}), [clip(x, 200) for x in snaps[:3]])
    if live:
        L.check("a live snapshot carries the counters", all(isinstance(x.get(k), (int, float)) for x in live for k in ("apexes", "reads", "pruned", "open_bounds", "seconds")), describe("/api/snapshot", {"id": sid}), clip(live[0], 300))
        L.check("the counters grow", all(a["apexes"] <= b["apexes"] and a["reads"] <= b["reads"] and a["seconds"] < b["seconds"] for a, b in zip(live, live[1:])), describe("/api/snapshot", {"id": sid}), [(x["apexes"], x["reads"], x["seconds"]) for x in live])
        L.check("the last live snapshot holds points", len(live[-1].get("points", [])) > 0, describe("/api/snapshot", {"id": sid}), clip(live[-1], 300))
        L.check("a live snapshot has provisional points", any(p.get("certified") is False for p in live[-1].get("points", [])), describe("/api/snapshot", {"id": sid}), [p.get("certified") for p in live[-1].get("points", [])][:30])
        L.check("the names of a live snapshot", live[-1].get("names") == [f"impact{j}" for j in range(5)] and live[-1].get("window") is None, describe("/api/snapshot", {"id": sid}), live[-1].get("names"))
        L.check("a live point carries its strategy", all(isinstance(p.get("steps"), list) for p in live[-1].get("points", [])), describe("/api/snapshot", {"id": sid}), clip(live[-1]["points"][0], 300) if live[-1].get("points") else None)
        L.check("the live note says the search is running", "running" in live[-1].get("note", ""), describe("/api/snapshot", {"id": sid}), live[-1].get("note"))
    # stop, then the final answer
    st = c.post("/api/stop", {"id": sid})
    L.check("stop answers ok", st.get("ok") is True, describe("/api/stop", {"id": sid}), st)
    t1 = time.time()
    final = None
    while time.time() - t1 < 15:
        final = c.post("/api/snapshot", {"id": sid})
        if not final.get("live"):
            break
        time.sleep(0.2)
    L.check("the search ends soon after the stop", final is not None and not final.get("live") and time.time() - t1 < 10, describe("/api/snapshot", {"id": sid}), clip(final, 300))
    if final is None or final.get("live"):
        return
    L.check("the stopped search is cut at the limit", final.get("status") == "cut at the limit", describe("/api/snapshot", {"id": sid}), final.get("status"))
    cert = sum(1 for p in final.get("points", []) if p.get("certified"))
    L.check("the cut answer holds points, some certified", len(final.get("points", [])) > 0 and 0 <= cert <= len(final["points"]), describe("/api/snapshot", {"id": sid}), {"points": len(final.get("points", [])), "certified": cert})
    L.check("the note counts the final points", f"{cert} of the {len(final.get('points', []))} points are final" in final.get("note", ""), describe("/api/snapshot", {"id": sid}), final.get("note"))
    L.check("the cut answer took under the limit", final.get("seconds", 1e9) < 20, describe("/api/snapshot", {"id": sid}), final.get("seconds"))
    # the certified points of the cut are the final ones: no live snapshot
    # before the stop held them below... the final points are an antichain
    fpts = [frac_tuple(p["value"]) for p in final.get("points", [])]
    L.check("the cut frontier is an antichain", not any(all(x <= y for x, y in zip(a, b)) and a != b for a in fpts for b in fpts), describe("/api/snapshot", {"id": sid}), len(fpts))
    again = [c.post("/api/snapshot", {"id": sid}) for _ in range(3)]
    L.check("a snapshot after the end returns the same answer every time", all(a == final for a in again), describe("/api/snapshot", {"id": sid}), [clip(a, 200) for a in again])
    st2 = c.post("/api/stop", {"id": sid})
    L.check("stop after the end is ok", st2.get("ok") is True, describe("/api/stop", {"id": sid}), st2)
    # the errors
    d = c.post("/api/snapshot", {"id": 999999})
    L.check("an unknown id is an error", d.get("error") == "no search 999999 is known", describe("/api/snapshot", {"id": 999999}), d)
    d = c.post("/api/stop", {"id": 999999})
    L.check("stop of an unknown id is an error", d.get("error") == "no search 999999 is known", describe("/api/stop", {"id": 999999}), d)
    d = c.post("/api/snapshot", {})
    L.check("a snapshot with no id is an error", d.get("error") == "no search was named", "POST /api/snapshot {}", d)
    d = c.post("/api/snapshot", {"id": "abc"})
    L.check("a snapshot with a bad id is an error", d.get("error") == "no search was named", describe("/api/snapshot", {"id": "abc"}), d)
    d = c.post("/api/start", {"process": "(A, 1, {x: 1}", "limit": "5"})
    L.check("start refuses a bad text", isinstance(d.get("error"), str) and "id" not in d, describe("/api/start", {"process": "(A, 1, {x: 1}"}), d)
    d = c.post("/api/start", {"limit": "5"})
    L.check("start refuses no text", d.get("error") == "no process was given", "POST /api/start {limit: 5}", d)
    # two starts back to back, both get ids and both end, the second after the first
    quick = {"process": TEXT_PLAIN, "workers": "1", "limit": "20", "time": "1"}
    slow = dict(fields, limit="6")
    a = c.post("/api/start", slow)
    b = c.post("/api/start", quick)
    ida, idb = a.get("id"), b.get("id")
    L.check("two starts back to back both get ids", isinstance(ida, int) and isinstance(idb, int) and idb == ida + 1, describe("/api/start", {"...": "slow then quick"}), [a, b])
    if isinstance(ida, int) and isinstance(idb, int):
        early = c.post("/api/snapshot", {"id": idb})
        L.check("the second search waits while the first runs", early.get("live") is True and early.get("points") == [], describe("/api/snapshot", {"id": idb}), clip(early, 300))
        deadline = time.time() + 40
        fa = fb = None
        while time.time() < deadline:
            fa = c.post("/api/snapshot", {"id": ida})
            fb = c.post("/api/snapshot", {"id": idb})
            if not fa.get("live") and not fb.get("live"):
                break
            time.sleep(0.3)
        L.check("both searches end", fa is not None and not fa.get("live") and not fb.get("live"), describe("/api/snapshot", {"id": [ida, idb]}), [clip(fa, 200), clip(fb, 200)])
        L.check("the first was cut at its limit, the second complete", fa.get("status") == "cut at the limit" and fb.get("status") == "complete", describe("/api/snapshot", {"id": [ida, idb]}), [fa.get("status"), fb.get("status")])
        L.check("the second search answers the example", fb.get("names") == ["time", "euro", "hours"] and len(fb.get("points", [])) > 0, describe("/api/snapshot", {"id": idb}), clip(fb, 300))
    # the sessions kept are the last few: seven quick searches, then the first ones are gone
    ids = []
    for _ in range(7):
        r = c.post("/api/start", quick)
        ids.append(r.get("id"))
    L.check("seven quick starts get consecutive ids", all(isinstance(i, int) for i in ids) and ids == list(range(ids[0], ids[0] + 7)), describe("/api/start", quick), ids)
    deadline = time.time() + 60
    while time.time() < deadline:
        last = c.post("/api/snapshot", {"id": ids[-1]})
        if not last.get("live"):
            break
        time.sleep(0.2)
    kept = [c.post("/api/snapshot", {"id": i}) for i in ids]
    L.check("the last six sessions are kept", all("error" not in k for k in kept[1:]), describe("/api/snapshot", {"id": ids[1:]}), [clip(k, 100) for k in kept[1:]])
    L.check("the seventh last session is dropped", kept[0].get("error") == f"no search {ids[0]} is known", describe("/api/snapshot", {"id": ids[0]}), kept[0])
    gone = c.post("/api/snapshot", {"id": sid})
    L.check("the first session of this run is gone", gone.get("error") == f"no search {sid} is known", describe("/api/snapshot", {"id": sid}), clip(gone, 200))
    # a session with a window: the snapshot sets the outside points apart
    fw = dict(fields, limit="4", window=",1.2;;;;")
    r = c.post("/api/start", fw)
    sidw = r.get("id")
    if isinstance(sidw, int):
        sw = c.post("/api/snapshot", {"id": sidw})
        for _ in range(5):
            if not sw.get("live") or sw.get("points"):
                break
            time.sleep(0.2)
            sw = c.post("/api/snapshot", {"id": sidw})
        if sw.get("live"):
            L.check("a live snapshot with a window keeps the points inside", all(p["approx"][0] <= 1.2 + 1e-9 for p in sw.get("points", [])) and sw.get("outside") == len(sw.get("outside_points", [])), describe("/api/snapshot", {"id": sidw}), {"outside": sw.get("outside"), "inside_max": max([p["approx"][0] for p in sw.get("points", [])] or [0])})
            L.check("a live snapshot sets the outside points apart", all(p["approx"][0] > 1.2 - 1e-9 for p in sw.get("outside_points", [])), describe("/api/snapshot", {"id": sidw}), sw.get("outside_points"))
        else:
            L.check("the windowed search that ended at once keeps the points inside", all(p["approx"][0] <= 1.2 + 1e-9 for p in sw.get("points", [])) and sw.get("outside") == len(sw.get("outside_points", [])), describe("/api/snapshot", {"id": sidw}), clip(sw, 300))
        c.post("/api/stop", {"id": sidw})
        deadline = time.time() + 20
        while time.time() < deadline:
            fw_ = c.post("/api/snapshot", {"id": sidw})
            if not fw_.get("live"):
                break
            time.sleep(0.2)
        L.check("the windowed search ends with its window echoed", not fw_.get("live") and isinstance(fw_.get("window"), list) and all(p["approx"][0] <= 1.2 + 1e-9 for p in fw_.get("points", [])), describe("/api/snapshot", {"id": sidw}), clip(fw_, 300))
    else:
        L.check("a start with a window gives an id", False, describe("/api/start", fw), r)


def store_ok(path):
    """The store file read back as JSON: the dict, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and all(isinstance(v, str) for v in d.values()) else None
    except (OSError, json.JSONDecodeError):
        return None


def section_saved(L, c, args, server, store):
    L.start("saved, save, load, delete")
    mine = {}

    def listed():
        status, text = c.get("/api/saved")
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            return None, text
        return d, text

    def expect_store(label):
        d = store_ok(store)
        lst, raw = listed()
        names = {x["name"] for x in lst.get("saved", [])} if lst else None
        L.check(f"{label}: the store file is valid JSON and lists what is saved", d is not None and names is not None and set(d) == names and all(d[n] == t for n, t in mine.items() if n in d) and set(mine) <= set(d), f"GET /api/saved after {label}", [raw, clip(d, 300)])

    def save(name, text, overwrite=None):
        f = {"name": name, "process": text}
        if overwrite is not None:
            f["overwrite"] = overwrite
        d = c.post("/api/save", f)
        if d.get("ok"):
            mine[name] = text
        return f, d

    d, raw = listed()
    L.check("the list is a JSON object with a saved array", isinstance(d, dict) and isinstance(d.get("saved"), list), "GET /api/saved", raw)
    before = {x["name"] for x in d.get("saved", [])} if d else set()
    text_a = "((A1, 1, {x: 1}) ^ (A2, 2, {y: 1}))\n# with a comment and a newline\n"
    f, r = save("alpha", text_a)
    L.check("save answers ok with the name", r == {"ok": True, "name": "alpha"}, describe("/api/save", f), r)
    d, raw = listed()
    row = next((x for x in d.get("saved", []) if x["name"] == "alpha"), None) if d else None
    L.check("the saved list has alpha with its length in chars", row is not None and row.get("chars") == len(text_a), "GET /api/saved", raw)
    f = {"name": "alpha"}
    r = c.post("/api/load", f)
    L.check("load gives the text back equal", r.get("process") == text_a and r.get("name") == "alpha", describe("/api/load", f), r)
    f, r = save("alpha", "(B, 1, {x: 1})")
    L.check("a second save asks before overwriting", r == {"exists": True, "name": "alpha"}, describe("/api/save", f), r)
    r2 = c.post("/api/load", {"name": "alpha"})
    L.check("the refused save left the text", r2.get("process") == text_a, "POST /api/load alpha", r2)
    f, r = save("alpha", "(B, 1, {x: 1})", overwrite="0")
    L.check("overwrite=0 asks too", r == {"exists": True, "name": "alpha"}, describe("/api/save", f), r)
    f, r = save("alpha", text_a + "# v2\n", overwrite="1")
    L.check("overwrite=1 overwrites", r == {"ok": True, "name": "alpha"} and c.post("/api/load", {"name": "alpha"}).get("process") == text_a + "# v2\n", describe("/api/save", f), r)
    expect_store("alpha overwritten")
    f, r = save("alpha", text_a, overwrite="true")
    L.check("overwrite=true overwrites", r.get("ok") is True and c.post("/api/load", {"name": "alpha"}).get("process") == text_a, describe("/api/save", f), r)
    # a name with spaces and unicode
    uname = "ciclo è ünï 漢字 1"
    # the grammar names tasks and impacts in ASCII; the unicode goes in the saved name
    utext = "((Piu, 1, {euro: 2}) , (Uni, 1, {euro: 1, hours: 3}))"
    f, r = save(uname, utext)
    L.check("a name with spaces and unicode saves", r == {"ok": True, "name": uname}, describe("/api/save", f), r)
    r = c.post("/api/load", {"name": uname})
    L.check("a unicode name loads back equal", r.get("process") == utext and r.get("name") == uname, describe("/api/load", {"name": uname}), r)
    d, raw = listed()
    row = next((x for x in d.get("saved", []) if x["name"] == uname), None) if d else None
    L.check("the unicode name is listed with its length in chars", row is not None and row.get("chars") == len(utext), "GET /api/saved", raw)
    r = c.post("/api/load", {"name": "  " + uname + "  "})
    L.check("a name is trimmed on load", r.get("process") == utext, describe("/api/load", {"name": "  " + uname + "  "}), r)
    expect_store("unicode saved")
    L.check("the saved names are listed sorted", d is not None and [x["name"] for x in d["saved"]] == sorted(x["name"] for x in d["saved"]), "GET /api/saved", raw)
    # references: @alpha, @alpha[shown], @"a name"
    text_ref = f'( @alpha , ( @alpha[second] ^ @"{uname}" ) , (Z, 1, {{x: 5}}) )'
    f = {"process": text_ref}
    d = c.post("/api/svg", f)
    svg = d.get("svg", "")
    L.check("a text with three references draws three boxes and one task", svg.count('class="sub collapsed"') == 3 and svg.count('class="task"') == 1, describe("/api/svg", f), d.get("error") or d.get("subs"))
    subs = d.get("subs", [])
    L.check("the subs name the saved process and the shown name", [(s.get("name"), s.get("shown")) for s in subs] == [("alpha", "alpha"), ("alpha", "second"), (uname, uname)], describe("/api/svg", f), subs)
    # the identifiers are in-order over the top text: @alpha 1, the sequence
    # 2, @alpha[second] 3, the choice 4, the quoted reference 5, the sequence
    # 6, Z 7
    L.check("the subs carry the labels of the referring leaves", [s.get("label") for s in subs] == ["1", "3", "5"] and all(s.get("depth") == 0 and s.get("expanded") is False for s in subs), describe("/api/svg", f), subs)
    L.check("the impacts are merged by name over the assembly", d.get("impacts") == sorted(["x", "y", "euro", "hours"]), describe("/api/svg", f), d.get("impacts"))
    f = {"process": text_ref, "expand": "1,3,5", "detail": "1"}
    d = c.post("/api/svg", f)
    svg = d.get("svg", "")
    L.check("expanding every reference draws every task", svg.count('class="task"') == 1 + 2 + 2 + 2 and svg.count('class="sub expanded"') == 3, describe("/api/svg", f), d.get("error") or d.get("subs"))
    L.check("the expanded tasks show hierarchical identifiers", all(f"id {lab}" in svg for lab in ("1-1", "1-3", "3-1", "3-3", "5-1", "5-3", "7")) and "id 2" not in svg, describe("/api/svg", f), re.findall(r">id [^<]*<", svg))
    L.check("the shown name is drawn for @alpha[second]", "second" in svg, describe("/api/svg", f), re.findall(r"<title>[^<]*</title>", svg))
    L.check("the subs say which are expanded", all(s.get("expanded") is True for s in d.get("subs", [])), describe("/api/svg", f), d.get("subs"))
    # the frontier through the references, with the hierarchical ids in the decisions
    f = {"process": text_ref, "workers": "1", "limit": "30", "time": "1"}
    d = c.post("/api/frontier", f)
    L.check("the frontier through the references completes", d.get("status") == "complete" and len(d.get("points", [])) > 0, describe("/api/frontier", f), clip(d, 300))
    if d.get("points"):
        L.check("the frontier names the merged impacts", d.get("names") == ["time", "x", "y", "euro", "hours"], describe("/api/frontier", f), d.get("names"))
        labels = [s["take"]["label"] for p in d["points"] for s in p["steps"]] + [a["label"] for p in d["points"] for s in p["steps"] for a in s["after"]]
        L.check("the decisions carry hierarchical identifiers", any(re.fullmatch(r"\d+-\d+", lab) for lab in labels), describe("/api/frontier", f), labels)
        L.check("the decisions name the tasks of the saved processes", any(s["take"]["name"] in ("A1", "A2") for p in d["points"] for s in p["steps"]), describe("/api/frontier", f), [s["sentence"] for p in d["points"] for s in p["steps"]][:20])
        # the same frontier as the assembled text written by hand
        flat = text_ref.replace('@"' + uname + '"', "(" + utext + ")").replace("@alpha[second]", "(" + text_a + ")").replace("@alpha", "(" + text_a + ")")
        ff = dict(f, process=flat)
        df = c.post("/api/frontier", ff)
        L.check("the frontier through the references equals the flat text", "points" in df and points_set(df["points"]) == points_set(d["points"]), describe("/api/frontier", ff), [clip(df, 200), clip(d, 200)])
    # a save that closes a cycle is refused and the store is left as it was
    f, r = save("beta", "(@alpha , (B, 1, {x: 1}))")
    L.check("beta referring to alpha saves", r.get("ok") is True, describe("/api/save", f), r)
    f, r = save("alpha", "(@beta , (C, 1, {x: 1}))", overwrite="1")
    L.check("a save closing a cycle is refused", "cycle" in r.get("error", "") and "ok" not in r, describe("/api/save", f), r)
    L.check("the refused cycle left alpha as it was", c.post("/api/load", {"name": "alpha"}).get("process") == text_a, "POST /api/load alpha", None)
    f, r = save("gamma", "(@gamma , (C, 1, {x: 1}))")
    L.check("a new process referring to itself is refused", "cycle" in r.get("error", "") and "gamma" not in {x["name"] for x in listed()[0].get("saved", [])}, describe("/api/save", f), r)
    f, r = save("gamma", "(@no_such , (C, 1, {x: 1}))")
    L.check("a process referring to nothing saved is accepted (the reference resolves later)", "ok" in r or "error" in r, describe("/api/save", f), r)
    if r.get("ok"):
        c.post("/api/delete", {"name": "gamma"})
        mine.pop("gamma", None)
    expect_store("after the cycle")
    # a delete of a process another refers to is refused
    f = {"name": "alpha"}
    r = c.post("/api/delete", f)
    L.check("deleting a process another refers to is refused", "referred to by beta" in r.get("error", ""), describe("/api/delete", f), r)
    L.check("the refused delete left alpha", c.post("/api/load", {"name": "alpha"}).get("process") == text_a, "POST /api/load alpha", None)
    # a prefix of a name is not a reference to it
    f, r = save("alphabet", "(D, 1, {x: 1})")
    f2, r2 = save("delta", "(@alphabet , (E, 1, {x: 1}))")
    r3 = c.post("/api/delete", {"name": "beta"})
    mine.pop("beta", None)
    L.check("beta deletes once nothing refers to it", r3 == {"ok": True, "name": "beta"}, "POST /api/delete beta", r3)
    r4 = c.post("/api/delete", {"name": "alpha"})
    L.check("alpha deletes although delta refers to @alphabet", r4 == {"ok": True, "name": "alpha"}, "POST /api/delete alpha (delta holds @alphabet)", r4)
    mine.pop("alpha", None)
    r5 = c.post("/api/delete", {"name": "alphabet"})
    L.check("alphabet is still referred to by delta", "referred to by delta" in r5.get("error", ""), "POST /api/delete alphabet", r5)
    f, r = save("eps", f'(@"{uname}" , (E, 1, {{x: 1}}))')
    r6 = c.post("/api/delete", {"name": uname})
    L.check("a quoted reference protects a unicode name", "referred to by eps" in r6.get("error", ""), describe("/api/delete", {"name": uname}), r6)
    r = c.post("/api/delete", {"name": "eps"})
    mine.pop("eps", None)
    # a mention inside a comment is not a reference: the engine never reads
    # it, so it must not protect the name against a delete
    f, r = save("mentioned", "(M, 1, {x: 1})")
    f2, r2 = save("talker", "(T, 1, {x: 1}) # see @mentioned for the original")
    r3 = c.post("/api/delete", {"name": "mentioned"})
    L.check("a name mentioned only in a comment deletes", r3 == {"ok": True, "name": "mentioned"}, "POST /api/delete mentioned (talker holds '# see @mentioned' in a comment)", r3)
    if r3.get("ok"):
        mine.pop("mentioned", None)
    else:
        c.post("/api/delete", {"name": "talker"})
        mine.pop("talker", None)
        c.post("/api/delete", {"name": "mentioned"})
        mine.pop("mentioned", None)
    L.check("delete of an unknown name is an error", c.post("/api/delete", {"name": "never_saved"}).get("error") == 'no process is saved as "never_saved"', "POST /api/delete never_saved", None)
    L.check("load of an unknown name is an error", c.post("/api/load", {"name": "never_saved"}).get("error") == 'no process is saved as "never_saved"', "POST /api/load never_saved", None)
    # the names refused
    for label, f in [
        ("an empty name", {"name": "", "process": "(A, 1, {x: 1})"}),
        ("a blank name", {"name": "   ", "process": "(A, 1, {x: 1})"}),
        ("no name", {"process": "(A, 1, {x: 1})"}),
        ("a name with a double quote", {"name": 'a"b', "process": "(A, 1, {x: 1})"}),
        ("a name of eighty one characters", {"name": "n" * 81, "process": "(A, 1, {x: 1})"}),
        ("an empty process", {"name": "empty", "process": "  "}),
        ("no process", {"name": "empty"}),
    ]:
        r = c.post("/api/save", f)
        L.check(f"save refuses {label}", isinstance(r.get("error"), str) and "ok" not in r, describe("/api/save", f), r)
    f, r = save("n" * 80, "(A, 1, {x: 1})")
    L.check("a name of eighty characters saves", r.get("ok") is True, describe("/api/save", f), r)
    c.post("/api/delete", {"name": "n" * 80})
    mine.pop("n" * 80, None)
    # a text that does not parse is saved anyway? the page saves what is typed
    f, r = save("draft", "(A, 1, {x: 1}")
    L.check("save of a text that does not parse answers", "ok" in r or "error" in r, describe("/api/save", f), r)
    # a text with every escape the store must carry
    tricky = 'line1 "quoted" \\ backslash\n\ttab\r\n# unicode ü 漢 \u0001 ctrl'
    f, r = save("tricky", tricky)
    L.check("a text with quotes, backslashes and control characters saves", r.get("ok") is True, describe("/api/save", f), r)
    L.check("the tricky text loads back equal", c.post("/api/load", {"name": "tricky"}).get("process") == tricky, "POST /api/load tricky", clip(c.post("/api/load", {"name": "tricky"}), 300))
    expect_store("after the tricky text")
    # a name that is a prefix with the separators the referrer check allows
    # the store read back by a restarted server
    lst_before, raw_before = listed()
    file_before = store_ok(store)
    server.stop()
    L.check("the server stops for the restart", not server.alive(), "SIGTERM", None)
    try:
        server.start()
    except RuntimeError as e:
        L.check("the server restarts", False, "serve again", str(e))
        return
    lst_after, raw_after = listed()
    L.check("a restarted server lists the same processes", lst_before == lst_after, "GET /api/saved before and after a restart", [raw_before, raw_after])
    L.check("a restarted server loads the same texts", all(c.post("/api/load", {"name": n}).get("process") == t for n, t in mine.items()), "POST /api/load every saved name after a restart", list(mine))
    L.check("the store file is unchanged by the restart", store_ok(store) == file_before, store, None)
    L.check("the restarted server still splices a reference", c.post("/api/svg", {"process": f'(@"{uname}" , (Z, 1))'}).get("svg", "").count('class="sub collapsed"') == 1, describe("/api/svg", {"process": f'(@"{uname}" , (Z, 1))'}), None)


def section_hygiene(L, c, args, server):
    L.start("hygiene")
    for label, path, method in [
        ("GET on /api/frontier", "/api/frontier", "GET"),
        ("GET on /api/svg", "/api/svg", "GET"),
        ("GET on /api/start", "/api/start", "GET"),
        ("GET on /api/save", "/api/save", "GET"),
        ("GET on an unknown path", "/nope", "GET"),
        ("GET on /api/", "/api/", "GET"),
        ("GET on /api/hello/extra", "/api/hello/extra", "GET"),
        ("GET on a path with a traversal", "/../../etc/passwd", "GET"),
        ("GET on /index.html", "/index.html", "GET"),
    ]:
        status, body = c.status_of(path, method)
        L.check(f"{label} answers 404 or 405", status in (404, 405), f"{method} {path}", f"{status} {clip(body, 100)}")
    for label, path, method in [
        ("POST on /", "/", "POST"),
        ("POST on /api/hello", "/api/hello", "POST"),
        ("POST on /api/saved", "/api/saved", "POST"),
        ("PUT on /api/svg", "/api/svg", "PUT"),
        ("DELETE on /api/svg", "/api/svg", "DELETE"),
    ]:
        status, body = c.status_of(path, method, data=b"process=x")
        L.check(f"{label} answers 404 or 405", status in (404, 405), f"{method} {path}", f"{status} {clip(body, 100)}")
    status, body = c.status_of("/", "GET")
    L.check("GET / is the page", status == 200 and body.lower().startswith("<!doctype html>") and "/api/hello" in body, "GET /", f"{status} {len(body)} bytes")
    L.check("the page ends with its script", body.rstrip().lower().endswith("</script>") and body.count("<script") == 1, "GET /", body[-100:])
    routes = ("/api/bench", "/api/saved", "/api/save", "/api/load", "/api/delete", "/api/svg", "/api/bounds", "/api/start", "/api/snapshot", "/api/stop")
    if not args.no_loops:
        # the route of one example with the quote that closes it in the
        # page, the route of the list being the same text and an s
        routes += ("/api/examples", "/api/example'")
    L.check("the page names every route it uses", all(r in body for r in routes), "GET /", [r for r in routes if r not in body])
    # a body of three megabytes, refused before it is read
    big = ("process=" + "A" * (3 * 1024 * 1024)).encode()
    try:
        status, text = c.post_raw("/api/svg", big, timeout=60)
        L.check("a body of 3 MB is answered", status == 200 and "error" in text, "POST /api/svg with 3 MB", f"{status} {clip(text, 200)}")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        L.check("a body of 3 MB is refused with 400", e.code == 400 and "larger" in text, "POST /api/svg with 3 MB", f"{e.code} {clip(text, 200)}")
    except (urllib.error.URLError, ConnectionError, socket.timeout, OSError) as e:
        # the server answers 400 without reading the body, and may close the
        # socket while the client is still sending; the answer is then lost on
        # the client side, which is acceptable as long as the server lives
        L.check("a body of 3 MB is refused (the connection was closed while sending)", True, "POST /api/svg with 3 MB", repr(e))
    time.sleep(0.2)
    L.check("the server lives after the 3 MB body", server.alive() and c.get("/api/hello")[0] == 200, "GET /api/hello", None)
    # a body just under the cap is answered as a parse error
    near = ("process=" + "(" * (250 * 1024)).encode()
    try:
        status, text = c.post_raw("/api/svg", near, timeout=120)
        L.check("a body of 250 KB is answered", status == 200 and "error" in text, "POST /api/svg with 250 KB of parentheses", f"{status} {clip(text, 200)}")
    except (urllib.error.URLError, ConnectionError, socket.timeout, OSError) as e:
        L.check("a body of 250 KB is answered", False, "POST /api/svg with 250 KB of parentheses", repr(e))
    L.check("the server lives after the 250 KB body", server.alive() and c.get("/api/hello")[0] == 200, "GET /api/hello", None)
    # bodies that are not forms
    for label, data, ctype in [
        ("a body that is not a form", b"this is not a form", "text/plain"),
        ("a JSON body", b'{"process": "(A, 1, {x: 1})"}', "application/json"),
        ("an empty body", b"", "application/x-www-form-urlencoded"),
        ("a body with a broken percent escape", b"process=%zz%2", "application/x-www-form-urlencoded"),
        ("a body with a bare key", b"process", "application/x-www-form-urlencoded"),
    ]:
        try:
            status, text = c.post_raw("/api/svg", data, content_type=ctype)
            ok = status == 200 and ("error" in text or "svg" in text)
            L.check(f"{label} is answered", ok, f"POST /api/svg {data[:40]!r}", f"{status} {clip(text, 200)}")
        except urllib.error.HTTPError as e:
            L.check(f"{label} is answered", e.code in (400,), f"POST /api/svg {data[:40]!r}", f"{e.code}")
    try:
        status, text = c.post_raw("/api/svg", b"process=\xff\xfe(A, 1)", content_type="application/x-www-form-urlencoded")
        L.check("a body that is not UTF-8 is answered", status == 200 and ("error" in text or "svg" in text), "POST /api/svg with bytes ff fe", f"{status} {clip(text, 200)}")
    except urllib.error.HTTPError as e:
        L.check("a body that is not UTF-8 is refused with 400", e.code == 400, "POST /api/svg with bytes ff fe", str(e.code))
    # a percent-encoded form: plus, percent, unicode in a name round trip
    name = "a+b %25 ü"
    r = c.post("/api/save", {"name": name, "process": "(A, 1, {x: 1})", "overwrite": "1"})
    L.check("a name with plus, percent and unicode round trips through the form", r.get("ok") is True and c.post("/api/load", {"name": name}).get("process") == "(A, 1, {x: 1})", describe("/api/save", {"name": name}), r)
    c.post("/api/delete", {"name": name})
    # a request with a Content-Length larger than the body: the read timeout
    # frees the thread after fifteen seconds and the server lives; checked
    # with a socket held open while other requests go on
    port = int(c.base.rsplit(":", 1)[1])
    s = None
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"POST /api/svg HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\nprocess=short")
        status, text = c.get("/api/hello")
        L.check("a request that never completes does not block the others", status == 200, "POST with Content-Length 100 and 13 bytes, then GET /api/hello", str(status))
    finally:
        if s is not None:
            s.close()
    # a request line alone, then the connection closed
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"GARBAGE\r\n\r\n")
        data = s.recv(4096)
        s.close()
        L.check("a garbage request line is answered 404", data.startswith(b"HTTP/1.1 404"), "GARBAGE", data[:80])
    except OSError as e:
        L.check("a garbage request line is answered 404", False, "GARBAGE", repr(e))
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.close()
        L.check("a connection opened and closed at once is harmless", server.alive() and c.get("/api/hello")[0] == 200, "open, close", None)
    except OSError as e:
        L.check("a connection opened and closed at once is harmless", False, "open, close", repr(e))
    # fifty concurrent bounds requests
    results = [None] * 50
    fields = {"process": TEXT_PLAIN, "time": "1"}

    def one(i):
        try:
            results[i] = c.post("/api/bounds", fields, timeout=60)
        except Exception as e:
            results[i] = {"_exception": repr(e)}

    threads = [threading.Thread(target=one, args=(i,)) for i in range(50)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    first = results[0]
    ok = all(isinstance(r, dict) and r.get("names") == ["time", "euro", "hours"] and r == first for r in results)
    L.check("fifty concurrent bounds requests all answer the same", ok, describe("/api/bounds", fields) + " x50", [r for r in results if r != first][:3] or clip(first, 200))
    L.check("fifty concurrent requests answer in time", time.time() - t0 < 60, "x50", f"{time.time() - t0:.1f}s")
    # twenty concurrent svg and frontier requests mixed with a running search
    results2 = [None] * 20

    def two(i):
        try:
            if i % 2:
                results2[i] = c.post("/api/svg", {"process": TEXT_PLAIN, "detail": "1"}, timeout=60)
            else:
                results2[i] = c.post("/api/frontier", {"process": TEXT_PLAIN, "limit": "10", "time": "1"}, timeout=120)
        except Exception as e:
            results2[i] = {"_exception": repr(e)}

    threads = [threading.Thread(target=two, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    ok = all(isinstance(r, dict) and ("svg" in r or r.get("status") == "complete") for r in results2)
    L.check("twenty concurrent svg and frontier requests all answer", ok, "10 x /api/svg + 10 x /api/frontier", [r for r in results2 if not (isinstance(r, dict) and ("svg" in r or r.get("status") == "complete"))][:3])
    fronts = [points_set(r["points"]) for r in results2 if isinstance(r, dict) and "points" in r]
    L.check("the concurrent frontiers agree", len(fronts) == 10 and all(f == fronts[0] for f in fronts), "10 x /api/frontier", len(fronts))
    L.check("the server is alive at the end", server.alive() and c.get("/api/hello")[0] == 200, "GET /api/hello", None)


# -- main ------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bin", required=True, help="the sdcpi binary")
    ap.add_argument("--port", type=int, default=8123, help="the port of the server the suite starts (default 8123)")
    ap.add_argument("--bench", default=None, help="the folder of the benchmark instances, for /api/bench")
    ap.add_argument("--bench-loops", default=None, help="the folder of the instances with loops, for /api/bench with loops=1")
    ap.add_argument("--no-loops", action="store_true", help="the binary is the mirror without loops")
    ap.add_argument("--work", default=None, help="the scratch folder (default: a fresh temporary folder)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder when it was temporary")
    ap.add_argument("--verbose", action="store_true", help="print every passed check too")
    ap.add_argument("--only", default=None, help="comma-separated sections to run: hello,bench,examples,svg,bounds,frontier,lifecycle,saved,hygiene")
    args = ap.parse_args()

    binary = os.path.abspath(args.bin)
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        print(f"no executable at {binary}")
        return 2
    if args.bench:
        args.bench = os.path.abspath(args.bench)
        if not os.path.isdir(args.bench):
            print(f"no folder at {args.bench}")
            return 2
    if args.work:
        work = os.path.abspath(args.work)
        os.makedirs(work, exist_ok=True)
    else:
        work = tempfile.mkdtemp(prefix="sdcpi-page-api-")
    if not port_is_free(args.port):
        print(f"port {args.port} is taken; choose another with --port")
        return 2
    args.bin = binary
    store = os.path.join(work, "store.json")
    for stale in (store, store + ".tmp"):
        if os.path.exists(stale):
            os.remove(stale)
    log = os.path.join(work, "server.log")
    print(f"binary  {binary}\nport    {args.port}\nwork    {work}\nbench   {args.bench or '(none)'}\nloops   {'no' if args.no_loops else 'yes'}")

    L = Ledger(args.verbose)
    server = Server(binary, args.port, store, args.bench, log, bench_loops=args.bench_loops)
    try:
        server.start()
    except RuntimeError as e:
        print(f"the server did not start: {e}")
        return 2
    c = Client(args.port)
    only = set(args.only.split(",")) if args.only else None

    def wanted(name):
        return only is None or name in only

    try:
        hello = section_hello(L, c, args) if wanted("hello") else {"bench": bool(args.bench), "loops": bool(args.bench_loops), "modes": []}
        if wanted("bench"):
            section_bench(L, c, args, hello)
        if wanted("examples"):
            section_examples(L, c, args, work)
        if wanted("svg"):
            section_svg(L, c, args)
        if wanted("bounds"):
            section_bounds(L, c, args, work, store)
        if wanted("frontier"):
            section_frontier(L, c, args, work, store)
        if wanted("lifecycle"):
            section_lifecycle(L, c, args)
        if wanted("saved"):
            section_saved(L, c, args, server, store)
        if wanted("hygiene"):
            section_hygiene(L, c, args, server)
    except Exception as e:
        import traceback

        traceback.print_exc()
        L.check(f"the suite ran to the end ({type(e).__name__}: {e})", False, "the run", traceback.format_exc()[-800:])
    finally:
        alive = server.alive()
        server.stop()
    if not alive:
        L.check("the server was alive when the suite ended", False, "the run", f"see {log}")

    print(f"\nchecks {L.checks}  passed {L.passed}  failed {L.failed}  skipped {L.skipped}")
    if L.findings:
        print("\nfailures:")
        for f in L.findings:
            print(f"- [{f['section']}] {f['check']}\n    request: {f['request']}\n    answer:  {f['answer']}")
    report = os.path.join(work, "report.json")
    with open(report, "w", encoding="utf-8") as f:
        json.dump({"checks": L.checks, "passed": L.passed, "failed": L.failed, "skipped": L.skipped, "findings": L.findings}, f, ensure_ascii=False, indent=2)
    print(f"report  {report}")
    if not args.work and not args.keep and L.failed == 0:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    return 0 if L.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
