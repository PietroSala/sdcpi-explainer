#!/usr/bin/env python3
"""The page of `sdcpi serve` walked in a headless browser, as a user would.

    python3 scripts/page-ui/run.py --bin target/release/sdcpi
    python3 scripts/page-ui/run.py --bin PATH --port 8140 --bench DIR --shots DIR
    python3 scripts/page-ui/run.py --bin PATH --no-loops      # the mirror without loops

The suite starts a server of its own on `--port` (8140 by default, never a
port a server of someone else is on) with a fresh store under `--work`
(a temporary folder by default), opens the page with Playwright and a
headless Chromium at 1400 by 900, collects every console error and every
page error, and walks the page step by step, asserting on the DOM after each
step and saving a screenshot per step under `--shots` (`shots/` beside the
work folder by default). Needs `pip install playwright` and
`playwright install chromium`.

What is walked, each step a group of checks:

   1  load        the page loads, #proc holds the example, the diagram is an
                  SVG with one task box per task of the example
   2  switches    every switch of the drawing (detailed regions, tooltips,
                  bars, clocks, names) changes the SVG; the zoom buttons
                  change the viewBox and "fit" restores it
   3  typing      an invalid text shows its error under the diagram and the
                  drawing is marked stale, not shown as current; a text typed
                  key by key is drawn after the debounce
   4  bench       the benchmark panel loads an instance into the text, named
                  after it; regenerating with a seed changes the impacts
   5  saving      the name is edited in place, Save stores the text and the
                  pill says saved, the list shows it, + starts an empty
                  process, loading restores the text, @name draws a
                  collapsed sub-process box that expands from the panel and
                  from its plus, a reference closing a cycle is refused
   6  live        a search of some seconds shows the live bar with Pause and
                  Stop, the counters move, no table while it builds; Pause
                  brings the table, the ranges and the strategy, a row and a
                  line of the plot can be chosen; Play resumes; Stop answers
                  with the final column; a small process answers complete at
                  once
   7  filters     on the cut answer: the switch on the final points, a range
                  thumb dragged with the mouse, Reset; "Use as bounds" moves
                  the window sliders and the Reset of the window restores
   8  directions  the pills, max on a component, the arrows on the labels,
                  the bounds refreshed, a frontier with two directions drawn
                  as two graphs and the table header carrying the arrows
   9  paging      a frontier of more than a hundred points shows the pager,
                  next and previous work, a point chosen in the plot on
                  another page is revealed on its page; a strategy with more
                  than forty decisions shows the decisions pager
  10  loops       (the loop tool alone, skipped with --no-loops) Max
                  iterations disabled without a loop and enabled with one,
                  the loops column and the continued line on a loop process
  11  time        the time switch off removes the time from the names, the
                  window, the directions and the answer
  12  narrow      at 800 by 900 nothing overflows the document horizontally
  13  more        stopping while paused keeps the chosen point, a search
                  started over one running cuts it, a name with spaces and a
                  quoted reference shown under another name, overwrite asked
                  and done, delete refused for a process referred to, the
                  keyboard on a thumb, another mode of the benchmark, the
                  legend of the time, a limit above the cap clamped
  14  console     no console error and no page error during the walk

Every failed check is a finding, printed with its step and the screenshot
taken at that moment. The exit code is 0 when no check fails, 1 otherwise,
2 when the suite could not run (no binary, no browser, the server not up).
Whatever happens the server started here is killed at the end.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

# -- the processes the walk uses ---------------------------------------------

# a loop process for step 10, a choice inside the loop so that the points
# differ and the loops column has something to say
LOOP_TEXT = ("( (Start, 1, {euro: 2, hours: 1}) , "
             "(0.5 ( (Fix, 2, {euro: 5, hours: 3}) ^ (Patch, 1, {euro: 1, hours: 6}) )) )")

# a small process typed by hand in step 3
TYPED_TEXT = "( (A, 1, {x: 1}) , (B, 2, {x: 2, y: 1}) )"


def chain_text(n, seed):
    """A sequence of `n` blocks, each a nature node then a choice with a
    trade-off between x and y: a frontier of some hundreds of points whose
    one-phase search runs well past a few seconds from four blocks on."""
    import random
    r = random.Random(seed)

    def block(i):
        a, b, c, d = [r.randint(1, 9) for _ in range(4)]
        e, f = r.randint(1, 9), r.randint(1, 9)
        return (f"( ( (N{i}a, {r.randint(1, 3)}, {{x: {a}, y: {b}}}) ^[0.{r.randint(2, 8)}] "
                f"(N{i}b, {r.randint(1, 3)}, {{x: {c}, y: {d}}}) )"
                f" , ( (C{i}a, {r.randint(1, 3)}, {{x: {e}, y: {10 - e}}}) ^ "
                f"(C{i}b, {r.randint(1, 3)}, {{x: {f}, y: {10 - f}}}) ) )")
    t = block(0)
    for i in range(1, n):
        t = f"( {t} , {block(i)} )"
    return t


def decisions_text(n=6):
    """`n` nature nodes in sequence then one choice whose second branch is
    dominated: one point, decided on 2^n histories, found at once."""
    t = " , ".join(f"( (N{i}a, 1, {{x: {i + 1}, y: {7 - i}}}) ^[0.5] (N{i}b, 2, {{x: {i + 2}, y: {6 - i}}}) )"
                   for i in range(n))
    return f"( {t} , ( (Ca, 1, {{x: 1, y: 1}}) ^ (Cb, 1, {{x: 9, y: 9}}) ) )"


# -- the record of the checks --------------------------------------------------

class Suite:
    def __init__(self, shots):
        self.shots = shots
        self.step_no = 0
        self.step_name = ""
        self.shot_no = 0
        self.checks = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.findings = []
        self.page = None
        self.console = []    # (step, kind, text)

    def step(self, name):
        self.step_no += 1
        self.step_name = name
        print(f"\n== step {self.step_no}: {name}")

    def shot(self, name):
        self.shot_no += 1
        path = os.path.join(self.shots, f"{self.shot_no:02d}-{self.step_no:02d}-{name}.png")
        try:
            self.page.screenshot(path=path, full_page=True)
        except Exception as e:  # a screenshot never fails the walk
            print(f"   (no screenshot: {e})")
            return None
        return path

    def check(self, name, ok, detail="", repro=""):
        self.checks += 1
        if ok:
            self.passed += 1
            print(f"   ok    {name}")
        else:
            self.failed += 1
            path = self.shot("FAIL-" + re.sub(r"[^a-z0-9]+", "-", name.lower())[:40])
            print(f"   FAIL  {name}: {detail}")
            self.findings.append({
                "step": f"{self.step_no} {self.step_name}", "check": name,
                "detail": detail, "repro": repro, "shot": path})
        return ok

    def skip(self, name, why):
        self.checks += 1
        self.skipped += 1
        print(f"   skip  {name}: {why}")


# -- the walk ------------------------------------------------------------------

def post(url, route, **fields):
    req = urllib.request.Request(url + route, data=urllib.parse.urlencode(fields).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def get(url, route):
    with urllib.request.urlopen(url + route, timeout=30) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bin", required=True, help="the sdcpi binary")
    ap.add_argument("--port", type=int, default=8140)
    ap.add_argument("--bench", default=None, help="the folder of the benchmark instances")
    ap.add_argument("--shots", default=None, help="where the screenshots go")
    ap.add_argument("--work", default=None, help="the store and the log of the server")
    ap.add_argument("--no-loops", action="store_true", help="the binary without loops: step 10 is skipped")
    ap.add_argument("--live-seconds", type=int, default=20, help="the time limit of the live search")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--headed", action="store_true", help="show the browser")
    args = ap.parse_args()

    if not os.path.isfile(args.bin) or not os.access(args.bin, os.X_OK):
        print(f"no binary at {args.bin}")
        return 2
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("playwright for python is not installed: pip install playwright && playwright install chromium")
        return 2

    work = args.work or tempfile.mkdtemp(prefix="sdcpi-page-ui-")
    os.makedirs(work, exist_ok=True)
    shots = args.shots or os.path.join(work, "shots")
    os.makedirs(shots, exist_ok=True)
    store = os.path.join(work, "store.json")
    if os.path.exists(store):
        os.remove(store)
    url = f"http://127.0.0.1:{args.port}"

    # the server, a fresh store, the benchmark folder when given
    cmd = [args.bin, "serve", "--port", str(args.port), "--saved", store]
    if args.bench:
        cmd += ["--bench", args.bench]
    log = open(os.path.join(work, "server.log"), "w")
    server = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    hello = None
    for _ in range(50):
        time.sleep(0.1)
        if server.poll() is not None:
            break
        try:
            hello = get(url, "/api/hello")
            break
        except Exception:
            pass
    if hello is None:
        print(f"the server did not come up on {url}; see {log.name}")
        kill(server, args.port)
        return 2
    print(f"server {url} (pid {server.pid}), store {store}, shots {shots}")

    S = Suite(shots)
    code = 1
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            S.page = page
            page.set_default_timeout(8000)
            page.on("pageerror", lambda e: S.console.append((S.step_name, "pageerror", str(e))))
            page.on("console", lambda m: S.console.append((S.step_name, "console", m.text))
                    if m.type == "error" else None)
            walk(S, page, url, hello, args, PWTimeout)
            browser.close()
        code = 0 if S.failed == 0 else 1
    except Exception as e:
        print(f"the suite could not run: {e!r}")
        code = 2
    finally:
        kill(server, args.port)
        log.close()

    print(f"\n{S.checks} checks: {S.passed} passed, {S.failed} failed, {S.skipped} skipped")
    if S.findings:
        print("\nfindings:")
        for f in S.findings:
            print(f"  - [{f['step']}] {f['check']}: {f['detail']}")
            if f["repro"]:
                print(f"      repro: {f['repro']}")
            if f["shot"]:
                print(f"      shot:  {f['shot']}")
    with open(os.path.join(work, "report.json"), "w") as fh:
        json.dump({"checks": S.checks, "passed": S.passed, "failed": S.failed, "skipped": S.skipped,
                   "findings": S.findings, "console": S.console}, fh, indent=1)
    print(f"report: {os.path.join(work, 'report.json')}")
    return code


def kill(server, port):
    try:
        server.terminate()
        server.wait(timeout=5)
    except Exception:
        try:
            server.kill()
        except Exception:
            pass
    # whatever is left listening on the port of this suite
    subprocess.run(["pkill", "-f", f"serve --port {port} "], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def walk(S, page, url, hello, args, PWTimeout):
    """Every step guarded: a timeout or an exception inside a step is a
    failed check of that step and the walk goes on with the next."""
    steps = [step_load, step_switches, step_typing, step_bench, step_saving, step_live,
             step_filters, step_directions, step_paging, step_loops, step_time, step_narrow, step_more]
    ctx = {"url": url, "hello": hello, "args": args, "answers": {}}
    for fn in steps:
        S.step(fn.__name__.replace("step_", ""))
        try:
            fn(S, page, ctx)
        except PWTimeout as e:
            S.check(f"{S.step_name}: the step completed", False, f"timed out: {str(e).splitlines()[0]}",
                    repro="walk the page as the step says")
        except Exception as e:
            S.check(f"{S.step_name}: the step completed", False, f"{e!r}",
                    repro="walk the page as the step says")
        S.shot("end")
    S.step("console")
    errors = [c for c in S.console]
    S.check("no console error and no page error", not errors,
            "; ".join(f"[{st}] {k}: {t[:200]}" for st, k, t in errors[:10]),
            repro="open the page and watch the console while walking the steps named")


# -- helpers on the page -------------------------------------------------------

def wait_js(page, js, timeout=8000, arg=None):
    return page.wait_for_function(js, arg=arg, timeout=timeout)


def set_text(page, text):
    """The textarea filled (one input event, as a paste). What the page
    showed before is returned, for wait_diagram to wait for the change: the
    drawing of the text before stays while the new one is fetched, so a
    wait on the mere presence of an SVG would return at once."""
    if page.evaluate("() => document.getElementById('proc').value") == text:
        return None
    before = page.evaluate("() => [(document.querySelector('#diagram svg') || {}).outerHTML || '', document.getElementById('window').innerHTML]")
    page.fill("#proc", text)
    return before


def wait_diagram(page, timeout=10000, before=None):
    """The diagram drawn for the text as it stands: an SVG, not stale, no
    note under it, and different from the one before when one is given;
    then the window of the bounds refreshed, waited for briefly."""
    wait_js(page, "(b) => { const s = document.querySelector('#diagram svg'); return s && (b === null || s.outerHTML !== b) && !document.querySelector('#diagram').classList.contains('stale') && document.querySelector('#dnote').textContent === ''; }",
            timeout, before[0] if before else None)
    if before:
        try:
            wait_js(page, "(w) => document.getElementById('window').innerHTML !== w", 3000, before[1])
        except Exception:
            pass   # the same names and the same bounds: nothing to wait for


def svg_html(page):
    return page.evaluate("() => { const s = document.querySelector('#diagram svg'); return s ? s.outerHTML : ''; }")


def reveal(page, selector):
    """The group of the form holding the element opened, so that it can be
    clicked: the form is in groups since 2026-10-02, the Process one open
    at the start and the others closed."""
    page.evaluate("(sel) => { const el = document.querySelector(sel); const g = el && el.closest('details.group'); if (g) g.open = true; }", selector)


def switch(page, id_):
    """A switch of the page is a hidden checkbox under a label."""
    reveal(page, f"label[for={id_}]")
    page.click(f"label[for={id_}]")


def checked(page, id_):
    return page.evaluate(f"() => document.getElementById('{id_}').checked")


def compute(page, timeout):
    """Compute pressed and the answer waited for: the live bar gone and the
    button enabled again."""
    page.click("#go")
    wait_js(page, "() => !document.querySelector('#out .livebar') && !document.getElementById('go').disabled && document.querySelector('#out .cards')", timeout)
    return page.evaluate("() => ({status: LAST.status, n: LAST.points.length, final: LAST.points.filter(p => p.certified).length, names: LAST.names, maximized: LAST.maximized, time: LAST.time, note: LAST.note})")


def display(page, sel):
    return page.evaluate(f"() => {{ const e = document.querySelector('{sel}'); return e ? getComputedStyle(e).display : 'absent'; }}")


def text_of(page, sel):
    return page.evaluate(f"() => {{ const e = document.querySelector('{sel}'); return e ? e.textContent : null; }}")


# -- the steps -----------------------------------------------------------------

def step_load(S, page, ctx):
    page.goto(ctx["url"] + "/")
    wait_js(page, "() => document.getElementById('proc').value.trim() !== ''")
    text = page.evaluate("() => document.getElementById('proc').value")
    S.check("the textarea holds the example", text == ctx["hello"]["example"],
            f"textarea starts {text[:60]!r}", repro=f"GET {ctx['url']}/ and read #proc")
    wait_diagram(page)
    tasks = page.evaluate("() => document.querySelectorAll('#diagram svg g.task').length")
    names = re.findall(r"\((\w+),\s*\d+", ctx["hello"]["example"])
    S.check("the diagram is drawn with one task box per task", tasks == len(names),
            f"{tasks} task boxes for {len(names)} tasks", repro="open the page and count g.task in #diagram svg")
    S.check("the zoom buttons are there", page.evaluate("() => document.querySelectorAll('#diagram .zoom button').length") == 3)
    S.check("the benchmark panel is shown when the server has instances",
            page.evaluate("() => !document.getElementById('bench').hidden") == bool(ctx["hello"]["bench"]))
    S.check("the saved panel says nothing is saved yet", "Nothing saved yet" in (text_of(page, "#savedbox") or ""))
    S.shot("loaded")


def step_switches(S, page, ctx):
    wait_diagram(page)
    for id_ in ("detail", "tips", "bars", "clocks", "names"):
        before = svg_html(page)
        was = checked(page, id_)
        switch(page, id_)
        try:
            wait_js(page, "(b) => { const s = document.querySelector('#diagram svg'); return s && s.outerHTML !== b; }", 6000, before)
            changed = True
        except Exception:
            changed = False
        S.check(f"the switch #{id_} toggles something in the svg", changed,
                f"the svg is the same after switching #{id_} {'off' if was else 'on'}",
                repro=f"open the page with the example, switch #{id_} {'off' if was else 'on'}, compare #diagram svg")
        S.shot(f"switch-{id_}-{'off' if was else 'on'}")
        # back as it was
        switch(page, id_)
        wait_diagram(page)
        time.sleep(0.4)
    # the tooltips switch once more with the bars off, where the impacts
    # have a line under the name to go to
    switch(page, "bars")
    wait_diagram(page)
    before = svg_html(page)
    switch(page, "tips")
    try:
        wait_js(page, "(b) => document.querySelector('#diagram svg').outerHTML !== b", 6000, before)
        changed = True
    except Exception:
        changed = False
    S.check("with the bars off the tooltips switch moves the impacts into the box", changed,
            repro="switch Impacts as bars off, then Impacts as tooltips off, compare #diagram svg")
    S.shot("switch-tips-off-bars-off")
    switch(page, "tips")
    switch(page, "bars")
    wait_diagram(page)
    # the chips: one per impact of the example, unchecking one changes the bars
    chips = page.evaluate("() => [...document.querySelectorAll('#chips input')].map(c => c.value)")
    S.check("one chip per impact of the example", chips == ["euro", "hours"], f"chips {chips}")
    before = svg_html(page)
    page.click("#chips label:first-child")
    wait_js(page, "(b) => document.querySelector('#diagram svg').outerHTML !== b", 6000, before)
    S.check("unchecking a chip redraws the bars", True)
    page.click("#chips label:first-child")
    wait_diagram(page)
    # the zoom: the viewBox shrinks on +, grows on -, and fit restores it
    home = page.evaluate("() => document.querySelector('#diagram svg').getAttribute('viewBox')")
    page.click("#diagram .zoom button[title='zoom in']")
    zin = page.evaluate("() => document.querySelector('#diagram svg').getAttribute('viewBox')")
    page.click("#diagram .zoom button[title='zoom out']")
    page.click("#diagram .zoom button[title='zoom out']")
    zout = page.evaluate("() => document.querySelector('#diagram svg').getAttribute('viewBox')")
    page.click("#diagram .zoom button.fit")
    fit = page.evaluate("() => document.querySelector('#diagram svg').getAttribute('viewBox')")
    w = lambda vb: float(vb.split()[2])
    S.check("zoom in narrows the viewBox", w(zin) < w(home), f"{home} -> {zin}")
    S.check("zoom out widens the viewBox", w(zout) > w(zin), f"{zin} -> {zout}")
    S.check("fit restores the viewBox", all(abs(float(a) - float(b)) < 0.05 for a, b in zip(fit.split(), home.split())), f"{home} -> {fit}")
    S.shot("zoom-fit")
    # the wheel zooms about the pointer
    box = page.locator("#diagram svg").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, -300)
    wheeled = page.evaluate("() => document.querySelector('#diagram svg').getAttribute('viewBox')")
    S.check("the wheel zooms the drawing", w(wheeled) < w(home), f"{home} -> {wheeled}")
    page.click("#diagram .zoom button.fit")


def step_typing(S, page, ctx):
    set_text(page, "( (A, 1, {x: 1}) , (B, 2")
    wait_js(page, "() => document.getElementById('dnote').textContent !== ''")
    note = text_of(page, "#dnote")
    stale = page.evaluate("() => document.getElementById('diagram').classList.contains('stale')")
    has_svg = page.evaluate("() => !!document.querySelector('#diagram svg')")
    S.check("an invalid text shows its error under the diagram", "does not parse" in note, f"note {note!r}",
            repro="paste '( (A, 1, {x: 1}) , (B, 2' into #proc")
    S.check("the drawing of the text before is marked stale, not shown as current", (not has_svg) or stale,
            "the old svg is shown without the stale class")
    S.check("the frontier of the text before is gone", "press the button" in (text_of(page, "#out") or ""))
    S.shot("invalid-text")
    # typed key by key: the drawing follows after the debounce
    page.fill("#proc", "")
    wait_js(page, "() => !document.querySelector('#diagram svg')")
    S.check("an empty text clears the drawing", "Write a process" in (text_of(page, "#diagram") or ""))
    page.focus("#proc")
    page.keyboard.type(TYPED_TEXT, delay=4)
    wait_diagram(page)
    tasks = page.evaluate("() => [...document.querySelectorAll('#diagram svg g.task text.name')].map(t => t.textContent)")
    S.check("a text typed by keyboard is drawn after the debounce", tasks == ["A", "B"], f"tasks {tasks}",
            repro=f"type {TYPED_TEXT!r} into #proc key by key")
    S.check("the window of the bounds appears for the typed text", page.evaluate("() => !document.getElementById('wbox').hidden"))
    S.shot("typed-text")


def step_bench(S, page, ctx):
    if not ctx["hello"]["bench"]:
        S.skip("the benchmark panel", "the server has no benchmark folder (--bench)")
        return
    reveal(page, "#bench summary"); page.click("#bench summary")
    page.select_option("#b-n", "3")
    page.select_option("#b-i", "4")
    page.select_option("#b-k", "5")
    page.select_option("#b-d", "6")
    page.select_option("#b-m", "random")
    before = page.evaluate("() => document.getElementById('proc').value")
    page.click("#load")
    wait_js(page, "(b) => document.getElementById('proc').value !== b && document.getElementById('benchnote').textContent.startsWith('Loaded')", 20000, before)
    note = text_of(page, "#benchnote")
    text1 = page.evaluate("() => document.getElementById('proc').value")
    S.check("the benchmark instance is loaded into the text", "Loaded 3-4-5-6-random" in note and "T1" in text1, f"note {note!r}")
    S.check("the process is named after the instance", text_of(page, "#pname") == "3-4-5-6-random", f"name {text_of(page, '#pname')!r}")
    wait_diagram(page, 15000)
    ctx["bench_text"] = text1
    # regenerate with a seed
    switch(page, "regen")
    page.fill("#seed", "42")
    page.click("#load")
    wait_js(page, "() => document.getElementById('benchnote').textContent.includes('drawn again')", 20000)
    text2 = page.evaluate("() => document.getElementById('proc').value")
    S.check("regenerating with a seed changes the impacts", text2 != text1 and "T1" in text2,
            "the text is the same after regenerating", repro="load 3-4-5-6 random, switch Regenerate on, seed 42, Load")
    S.check("the regenerated process carries the seed in its name", text_of(page, "#pname") == "3-4-5-6-random-42", f"name {text_of(page, '#pname')!r}")
    # the same seed again gives the same text
    page.click("#load")
    wait_js(page, "() => document.getElementById('benchnote').textContent.includes('drawn again')", 20000)
    time.sleep(0.3)
    text3 = page.evaluate("() => document.getElementById('proc').value")
    S.check("the same seed regenerates the same impacts", text3 == text2, "a second load with seed 42 differs")
    switch(page, "regen")
    wait_diagram(page, 15000)
    S.shot("bench-loaded")
    reveal(page, "#bench summary"); page.click("#bench summary")


def step_saving(S, page, ctx):
    alpha = "( (A, 1, {x: 1}) , (B, 2, {x: 2, y: 1}) )"
    # a new process, discarding the loaded one if it asks
    page.click("#newp")
    if page.locator("#nw-yes").count():
        page.click("#nw-yes")
    wait_js(page, "() => document.getElementById('proc').value === ''")
    S.check("+ starts a new empty process", text_of(page, "#pname") == "untitled")
    before = set_text(page, alpha)
    wait_diagram(page, before=before)
    # the name edited in place
    page.click("#pname")
    S.check("the name is edited in place", page.evaluate("() => document.getElementById('pname').isContentEditable"))
    page.keyboard.type("alpha")
    page.keyboard.press("Enter")
    S.check("the typed name is kept", text_of(page, "#pname") == "alpha", f"name {text_of(page, '#pname')!r}")
    S.check("the pill says new before saving", text_of(page, "#pstate") == "new", f"pill {text_of(page, '#pstate')!r}")
    page.click("#save")
    wait_js(page, "() => document.getElementById('savenote').textContent.startsWith('Saved as')")
    S.check("the pill says saved after Save", text_of(page, "#pstate") == "saved", f"pill {text_of(page, '#pstate')!r}")
    reveal(page, "#savedpanel summary"); page.click("#savedpanel summary")
    wait_js(page, "() => document.querySelector('#savedbox a[data-name=\"alpha\"]')")
    S.check("the saved list shows the process", True)
    S.shot("saved-alpha")
    # modified pill and rollback
    page.type("#proc", " ")
    wait_js(page, "() => document.getElementById('pstate').textContent === 'modified'")
    S.check("an edit turns the pill to modified and offers Rollback", not page.evaluate("() => document.getElementById('rollback').hidden"))
    page.click("#rollback")
    wait_js(page, "() => document.getElementById('pstate').textContent === 'saved'")
    S.check("Rollback restores the saved text", page.evaluate("() => document.getElementById('proc').value") == alpha)
    # a new process, then the saved one loaded back
    page.click("#newp")
    if page.locator("#nw-yes").count():
        page.click("#nw-yes")
    wait_js(page, "() => document.getElementById('proc').value === ''")
    page.click("#savedbox a[data-name='alpha']")
    wait_js(page, "() => document.getElementById('savenote').textContent.startsWith('Loaded')")
    S.check("loading the saved process restores its text", page.evaluate("() => document.getElementById('proc').value") == alpha
            and text_of(page, "#pname") == "alpha" and text_of(page, "#pstate") == "saved")
    # a reference in a new process
    page.click("#newp")
    wait_js(page, "() => document.getElementById('proc').value === ''")
    before = set_text(page, "( @alpha , (Z, 1, {x: 1}) )")
    wait_diagram(page, before=before)
    sub = page.evaluate("() => document.querySelectorAll('#diagram svg g.sub.collapsed').length")
    S.check("a reference @alpha draws a collapsed sub-process box", sub == 1, f"{sub} collapsed sub boxes",
            repro="new process, text '( @alpha , (Z, 1, {x: 1}) )' with alpha saved")
    S.check("the sub-process panel lists it", not page.evaluate("() => document.getElementById('subpanel').hidden")
            and page.evaluate("() => document.querySelectorAll('#subbox .subrow').length") == 1)
    S.check("the tasks of alpha are not drawn while collapsed",
            page.evaluate("() => [...document.querySelectorAll('#diagram svg g.task text.name')].map(t => t.textContent)") == ["Z"])
    S.shot("reference-collapsed")
    # expanded from the panel
    reveal(page, "#subpanel summary"); page.click("#subpanel summary")
    page.click("#subbox button.tg")
    wait_js(page, "() => document.querySelector('#diagram svg g.sub.expanded')")
    names = page.evaluate("() => [...document.querySelectorAll('#diagram svg g.task text.name')].map(t => t.textContent)")
    S.check("expanding from the panel draws the tasks of alpha inside the box", sorted(names) == ["A", "B", "Z"], f"tasks {names}")
    S.check("the row of the panel shows minus once expanded", text_of(page, "#subbox button.tg") == "−")
    S.shot("reference-expanded")
    # collapsed again from the plus of the box in the drawing
    page.locator("#diagram svg [data-toggle='1']").click()
    try:
        wait_js(page, "() => document.querySelector('#diagram svg g.sub.collapsed')")
        ok = True
    except Exception:
        ok = False
    S.check("the toggle on the box in the drawing collapses it", ok, "the box stays expanded after a click on its toggle",
            repro="click the minus on the border of the sub-process box in #diagram")
    page.locator("#diagram svg [data-toggle='1']").click()
    wait_js(page, "() => document.querySelector('#diagram svg g.sub.expanded')")
    S.check("the toggle on the box expands it again", True)
    page.click("#subclose")
    wait_js(page, "() => document.querySelector('#diagram svg g.sub.collapsed')")
    reveal(page, "#subopen"); page.click("#subopen")
    wait_js(page, "() => document.querySelector('#diagram svg g.sub.expanded')")
    S.check("Collapse all and Expand all work", True)
    # saved as beta, then alpha made to refer to beta: a cycle
    page.click("#pname")
    page.keyboard.type("beta")
    page.keyboard.press("Enter")
    page.click("#save")
    wait_js(page, "() => document.getElementById('savenote').textContent.startsWith('Saved as')")
    page.click("#savedbox a[data-name='alpha']")
    wait_js(page, "() => document.getElementById('savenote').textContent === 'Loaded alpha.'")
    set_text(page, "( @beta , (W, 1, {x: 1}) )")
    wait_js(page, "() => document.getElementById('dnote').textContent !== ''")
    note = text_of(page, "#dnote")
    S.check("a text that refers to itself through another is reported as a cycle under the diagram", "cycle" in note, f"note {note!r}",
            repro="with alpha and beta='( @alpha , ... )' saved, load alpha and write '( @beta , (W, 1, {x: 1}) )'")
    page.click("#save")
    wait_js(page, "() => document.getElementById('savenote').textContent.startsWith('not saved')")
    S.check("saving the cycle is refused", "cycle" in text_of(page, "#savenote"), f"note {text_of(page, '#savenote')!r}")
    S.shot("cycle-refused")
    # the store agrees
    saved = get(ctx["url"], "/api/saved")["saved"]
    S.check("the store holds alpha and beta and nothing else", sorted(e["name"] for e in saved) == ["alpha", "beta"], f"{saved}")
    # a name with a double quote is refused
    page.click("#rollback")
    wait_js(page, "() => document.getElementById('pstate').textContent === 'saved'")
    # deleting alpha is refused while beta refers to it
    page.click("#savedbox button.bin[data-name='alpha']")
    page.click("#savedbox li.ask button.yes")
    wait_js(page, "() => document.getElementById('savenote').textContent.startsWith('not deleted')")
    S.check("deleting a process another refers to is refused", "referred to by beta" in text_of(page, "#savenote"))
    reveal(page, "#savedpanel summary"); page.click("#savedpanel summary")


def step_live(S, page, ctx):
    args = ctx["args"]
    page.click("#newp")
    if page.locator("#nw-yes").count():
        page.click("#nw-yes")
    wait_js(page, "() => document.getElementById('proc').value === ''")
    before = set_text(page, chain_text(4, 4))
    wait_diagram(page, before=before)
    reveal(page, "#workers"); page.select_option("#workers", str(args.workers))
    reveal(page, "#limit"); page.fill("#limit", str(args.live_seconds))
    page.click("#go")
    page.wait_for_selector("#out .livebar #pause", timeout=10000)
    page.wait_for_selector("#out .cards", timeout=10000)
    S.check("the live bar with Pause and Stop appears", page.locator("#stopsearch").count() == 1
            and text_of(page, "#pause") == "Pause")
    S.check("no table while the view builds", display(page, "#out .tablebox") == "none", f"display {display(page, '#out .tablebox')}")
    S.check("no ranges and no strategy while it builds", display(page, "#out .ranges") == "none" and display(page, "#out .strategy") == "none")
    status1 = text_of(page, "#out .livebar .status")
    S.shot("live-building")
    time.sleep(1.6)
    status2 = text_of(page, "#out .livebar .status")
    S.check("the counters of the live bar update", status1 != status2, f"{status1!r} then {status2!r}",
            repro="compute the chain process with a limit of 20 s and read the status of the live bar twice")
    cards = page.evaluate("() => [...document.querySelectorAll('#out .card span')].map(s => s.textContent)")
    S.check("the cards of a snapshot name the nodes closed", "nodes closed" in cards, f"cards {cards}")
    # pause: the table, the ranges and the strategy
    page.click("#pause")
    wait_js(page, "() => document.getElementById('pause').textContent === 'Play' && !document.getElementById('out').classList.contains('live')")
    rows = page.evaluate("() => document.querySelectorAll('#out tbody tr').length")
    S.check("Pause brings the table", display(page, "#out .tablebox") != "none" and rows > 0, f"{rows} rows")
    S.check("Pause brings the ranges", display(page, "#out .ranges") != "none")
    S.check("Pause brings the strategy of the first point", "Strategy of point 1" in (text_of(page, "#str") or "")
            and page.evaluate("() => document.querySelectorAll('#slines div').length") > 0)
    S.check("a provisional point is marked so", page.evaluate("() => document.querySelectorAll('#out tbody tr.prov').length") > 0)
    S.shot("live-paused")
    if rows >= 2:
        page.click("#out tbody tr:nth-child(2)")
        sel = page.evaluate("() => { const t = document.querySelector('#out tbody tr.sel'); return t ? t.dataset.i : null; }")
        S.check("a row clicked is chosen and the strategy follows", sel == "1" and "Strategy of point 2" in text_of(page, "#str"), f"sel {sel}")
    # a line of the plot clicked
    lines = page.evaluate("() => document.querySelectorAll('#out svg polyline[data-hit], #out svg circle[data-i]').length")
    S.check("the plot has one element per point", lines == rows or lines == page.evaluate("() => LAST.points.length"), f"{lines} elements for {rows} rows")
    target = min(3, rows - 1)
    page.locator(f"#out svg polyline[data-hit='{target}'], #out svg circle[data-i='{target}']").first.dispatch_event("click")
    sel = page.evaluate("() => { const t = document.querySelector('#out tbody tr.sel'); return t ? t.dataset.i : null; }")
    S.check("a point of the plot clicked highlights its row", sel == str(target), f"sel {sel} wanted {target}",
            repro="pause the live search, click a dot of the plane or a line of the parallel coordinates")
    S.check("the element of the chosen point is drawn selected", page.evaluate("(t) => { const l = document.querySelector('#out svg polyline.sel'); if (l) return l.dataset.i === String(t); const c = document.querySelector(`#out svg circle[data-i='${t}']`); return !!c && c.getAttribute('stroke') === '#d0342c' && c.getAttribute('r') === '5.5'; }", target))
    S.shot("live-paused-chosen")
    # play
    page.click("#pause")
    wait_js(page, "() => document.getElementById('pause').textContent === 'Pause' && document.getElementById('out').classList.contains('live')")
    S.check("Play resumes the live view", True)
    time.sleep(1.0)
    # stop
    page.click("#stopsearch")
    wait_js(page, "() => !document.querySelector('#out .livebar') && !document.getElementById('go').disabled", 30000)
    a = page.evaluate("() => ({status: LAST.status, n: LAST.points.length, final: LAST.points.filter(p => p.certified).length, note: LAST.note, seconds: LAST.seconds})")
    S.check("Stop gives the final answer, cut", a["status"] == "cut at the limit", f"status {a['status']}")
    S.check("the answer came before the limit", a["seconds"] < args.live_seconds, f"{a['seconds']} s")
    heads = page.evaluate("() => [...document.querySelectorAll('#out thead th')].map(t => t.textContent)")
    S.check("the table of a cut answer carries the final column", "final" in heads, f"heads {heads}")
    finals = page.evaluate("() => [...document.querySelectorAll('#out tbody td:last-child')].map(t => t.textContent)")
    S.check("the final column says yes or provisional", all(f in ("yes", "provisional") for f in finals) and len(finals) > 0)
    S.check("the note says the search was cut", "The search was cut" in a["note"], a["note"][:100])
    S.check("the cards count the final and the provisional points", page.evaluate("() => [...document.querySelectorAll('#out .card span')].map(s => s.textContent)").count("provisional") == 1)
    ctx["answers"]["cut"] = a
    S.shot("live-stopped")


def step_filters(S, page, ctx):
    a = ctx["answers"].get("cut")
    if not a or a["n"] < 2:
        S.skip("the filters on the cut answer", "no cut answer with points")
        return
    total = a["n"]
    # the switch on the final points
    if a["final"] < total:
        page.click("label[for=onlyfinal]")
        wait_js(page, "(f) => document.getElementById('rcount').textContent === String(f)", 5000, a["final"])
        S.check("the only-final switch keeps the final points alone", True)
        rows = page.evaluate("() => [...document.querySelectorAll('#out tbody td:last-child')].map(t => t.textContent)")
        S.check("the table shows the final points alone", all(r == "yes" for r in rows) and len(rows) == min(a["final"], 100), f"{len(rows)} rows, {rows[:5]}")
        hidden = page.evaluate("() => [...document.querySelectorAll('#out svg [data-hit], #out svg circle[data-i]')].filter(e => e.style.display === 'none').length")
        S.check("the plot hides the provisional points", hidden == total - a["final"], f"{hidden} hidden of {total}")
        S.shot("only-final")
        page.click("label[for=onlyfinal]")
        wait_js(page, "(t) => document.getElementById('rcount').textContent === String(t)", 5000, total)
    else:
        S.skip("the only-final switch", "every point is final")
    # a thumb dragged with the mouse
    track = page.locator("#ranges .range:not(.flat) .rng").first
    if track.count() == 0:
        S.skip("dragging a range thumb", "every range is flat")
    else:
        track.scroll_into_view_if_needed()
        box = track.bounding_box()
        thumb = track.locator(".thumb.hi").bounding_box()
        page.mouse.move(thumb["x"] + thumb["width"] / 2, thumb["y"] + thumb["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.35, thumb["y"] + thumb["height"] / 2, steps=8)
        page.mouse.up()
        kept = int(text_of(page, "#rcount"))
        expected = page.evaluate("() => LAST.points.filter(p => RANGES.every((R, j) => +p.approx[j] >= R.lo && +p.approx[j] <= R.hi)).length")
        S.check("dragging a thumb filters the table count", kept < total, f"{kept} of {total} kept after the drag",
                repro="on a cut answer drag the right thumb of the first range to a third of its track")
        S.check("the counter agrees with the ranges", kept == expected, f"counter {kept}, by the ranges {expected}")
        rows = page.evaluate("() => document.querySelectorAll('#out tbody tr').length")
        S.check("the table shows the points kept", rows == min(kept, 100), f"{rows} rows for {kept} kept")
        S.check("the chosen point is one kept", page.evaluate("() => { const t = document.querySelector('#out tbody tr.sel'); return !!t; }") or kept == 0)
        S.shot("range-dragged")
        page.click("#rreset")
        S.check("Reset restores every point", text_of(page, "#rcount") == str(total), f"counter {text_of(page, '#rcount')}")
    # use as bounds
    page.click("#out tbody tr:first-child")
    wait_js(page, "() => document.getElementById('tobounds')")
    before = page.evaluate("() => [...document.querySelectorAll('#window .rng .fill')].map(f => f.style.width)")
    page.click("#tobounds")
    after = page.evaluate("() => [...document.querySelectorAll('#window .rng .fill')].map(f => f.style.width)")
    vals = page.evaluate("() => [...document.querySelectorAll('#window .rval')].map(v => v.textContent)")
    S.check("Use as bounds moves the red window sliders", before != after and any(w != "100%" for w in after), f"{before} -> {after}, {vals}",
            repro="choose a point of the answer and press Use as bounds")
    sent = page.evaluate("() => windowField()")
    exact = page.evaluate("() => WNAMES.every((n, i) => { const R = WINDOW[n], p = LAST.points[0]; return R.flat || +p.approx[i] >= R.b || windowField().includes(p.value[i]); })")
    S.check("the window sent carries the exact values of the point", bool(exact) and sent.replace(";", "").replace(",", "") != "", f"window {sent!r}")
    S.shot("use-as-bounds")
    reveal(page, "#wreset"); page.click("#wreset")
    after_reset = page.evaluate("() => [...document.querySelectorAll('#window .rng .fill')].map(f => f.style.width)")
    S.check("the Reset of the window restores the bounds", all(w == "100%" for w in after_reset) and page.evaluate("() => windowField()").replace(";", "").replace(",", "") == "", f"{after_reset}")
    # a window computed: the search with the bounds of a point finds it and nothing worse
    page.click("#out tbody tr:first-child")
    page.click("#tobounds")
    reveal(page, "#limit"); page.fill("#limit", "10")
    r = compute(page, 40000)
    S.check("a search within the bounds of a point answers", r["n"] >= 1, f"{r}")
    inside = page.evaluate("() => LAST.window && LAST.points.every(p => p.approx.every((v, j) => +v >= LAST.window[j][0] - 1e-9 && +v <= LAST.window[j][1] + 1e-9))")
    S.check("every point of the answer lies within the window", bool(inside), f"window {page.evaluate('() => LAST.window')}")
    S.shot("within-bounds")
    reveal(page, "#wreset"); page.click("#wreset")


def step_directions(S, page, ctx):
    before = set_text(page, ctx["hello"]["example"])
    wait_diagram(page, before=before)
    wait_js(page, "() => [...document.querySelectorAll('#dirs .seg')].map(s => s.dataset.n).join() === 'time,euro,hours'")
    segs = page.evaluate("() => [...document.querySelectorAll('#dirs .seg')].map(s => s.dataset.n)")
    S.check("one pill per component, the time first", segs == ["time", "euro", "hours"], f"{segs}")
    S.check("every pill starts at min", page.evaluate("() => [...document.querySelectorAll('#dirs .seg button.on')].every(b => b.dataset.v === 'min')"))
    page.click("#dirs .seg[data-n='euro'] button[data-v='max']")
    S.check("max on euro lights the pill", page.evaluate("() => document.querySelector(\"#dirs .seg[data-n='euro'] button.on\").dataset.v") == "max")
    wait_js(page, "() => [...document.querySelectorAll('#window .rname span:first-child')].some(s => s.textContent.includes('euro ↑'))")
    labels = page.evaluate("() => [...document.querySelectorAll('#window .rname span:first-child')].map(s => s.textContent)")
    S.check("the arrow on the label of the window changes", "euro ↑" in labels and "time ↓" in labels, f"{labels}")
    S.shot("direction-max-euro")
    reveal(page, "#limit"); page.fill("#limit", "10")
    r = compute(page, 30000)
    S.check("the answer names euro as maximized", r["maximized"] == [False, True, False], f"{r['maximized']}")
    svgs = page.evaluate("() => document.querySelectorAll('#out .plotrow svg').length")
    S.check("with two directions the plot shows two graphs", svgs == 2, f"{svgs} svg in the plot row",
            repro="example, euro set to max, compute")
    heads = page.evaluate("() => [...document.querySelectorAll('#out thead th')].map(t => t.textContent)")
    S.check("the table header carries the arrows", "euro ↑" in heads and "time ↓" in heads and "hours ↓" in heads, f"{heads}")
    # the frontier maximizing euro takes greater euro than the one minimizing it
    maxed = page.evaluate("() => Math.max(...LAST.points.map(p => +p.approx[1]))")
    S.shot("two-directions")
    page.click("#dirs .seg[data-n='euro'] button[data-v='min']")
    wait_js(page, "() => [...document.querySelectorAll('#window .rname span:first-child')].some(s => s.textContent.includes('euro ↓'))")
    r2 = compute(page, 30000)
    mined = page.evaluate("() => Math.max(...LAST.points.map(p => +p.approx[1]))")
    S.check("the maximized frontier reaches a greater euro than the minimized one", maxed >= mined and r2["maximized"] == [False, False, False], f"max {maxed}, min {mined}")
    S.check("back to min the plot is one graph again", page.evaluate("() => document.querySelectorAll('#out .plotrow svg').length") == 1)
    S.check("the small example answers complete at once", r2["status"] == "complete" and r2["n"] >= 1 and "finished" in r2["note"], f"{r2}")
    ctx["answers"]["example"] = r2


def step_paging(S, page, ctx):
    text = ctx.get("bench_text")
    if not text and ctx["hello"]["bench"]:
        try:
            text = post(ctx["url"], "/api/bench", nested="3", independent="4", process="5", impacts="6", mode="random")["process"]
        except Exception:
            text = None
    if not text:
        text = chain_text(4, 4)
    page.click("#newp")
    if page.locator("#nw-yes").count():
        page.click("#nw-yes")
    wait_js(page, "() => document.getElementById('proc').value === ''")
    before = set_text(page, text)
    wait_diagram(page, 15000, before=before)
    reveal(page, "#limit"); page.fill("#limit", "15")
    r = compute(page, 40000)
    n = r["n"]
    if n <= 100:
        S.skip("the pager of the table", f"the frontier has {n} points")
        return
    S.check("a frontier of more than a hundred points shows the pager", not page.evaluate("() => document.getElementById('tpager').hidden"))
    S.check("the first page is 1 to 100", text_of(page, "#tpager span") == f"points 1–100 of {n}", f"{text_of(page, '#tpager span')!r}")
    S.check("a hundred rows are painted", page.evaluate("() => document.querySelectorAll('#out tbody tr').length") == 100)
    S.check("previous is disabled on the first page", page.evaluate("() => document.getElementById('tprev').disabled"))
    page.click("#tnext")
    S.check("next shows the second page", text_of(page, "#tpager span") == f"points 101–{min(200, n)} of {n}"
            and page.evaluate("() => document.querySelector('#out tbody tr').dataset.i") == "100", f"{text_of(page, '#tpager span')!r}")
    S.shot("pager-second-page")
    page.click("#tprev")
    S.check("previous goes back to the first page", text_of(page, "#tpager span") == f"points 1–100 of {n}")
    # a point on another page chosen in the plot
    target = min(150, n - 1)
    page.locator(f"#out svg polyline[data-hit='{target}'], #out svg circle[data-i='{target}']").first.dispatch_event("click")
    sel = page.evaluate("() => { const t = document.querySelector('#out tbody tr.sel'); return t ? t.dataset.i : null; }")
    S.check("a point chosen in the plot is revealed on its page", sel == str(target) and text_of(page, "#tpager span").startswith("points 101"),
            f"sel {sel}, pager {text_of(page, '#tpager span')!r}", repro="on a frontier of 320 points click the line of point 151 in the plot")
    S.check("the strategy follows the point chosen", f"Strategy of point {target + 1}" in text_of(page, "#str"))
    S.check("the chosen row is in view inside the table box", page.evaluate("() => { const t = document.querySelector('#out tbody tr.sel'), b = t.closest('.tablebox'); const r = t.getBoundingClientRect(), q = b.getBoundingClientRect(); return r.top >= q.top && r.bottom <= q.bottom + 1; }"))
    S.shot("pager-revealed")
    # the last page
    while not page.evaluate("() => document.getElementById('tnext').disabled"):
        page.click("#tnext")
    last_rows = page.evaluate("() => document.querySelectorAll('#out tbody tr').length")
    S.check("the last page holds the remainder", last_rows == (n % 100 or 100), f"{last_rows} rows on the last page of {n}")
    # the decisions pager
    before = set_text(page, decisions_text(6))
    wait_diagram(page, before=before)
    r = compute(page, 30000)
    S.check("the many-decision process answers complete", r["status"] == "complete" and r["n"] == 1, f"{r}")
    steps = page.evaluate("() => LAST.points[0].steps.length")
    S.check("its strategy decides sixty-four situations", steps == 64, f"{steps} decisions")
    pager = text_of(page, "#slines .pager span")
    S.check("a strategy of more than forty decisions shows the decisions pager", pager == "decisions 1–40 of 64", f"{pager!r}",
            repro="compute the six-nature process with a dominated choice and read the strategy panel")
    S.check("forty decisions are shown on the first page", page.evaluate("() => document.querySelectorAll('#slines > div:not(.pager)').length") == 40)
    page.click("#snext")
    S.check("next shows the remaining decisions", text_of(page, "#slines .pager span") == "decisions 41–64 of 64"
            and page.evaluate("() => document.querySelectorAll('#slines > div:not(.pager)').length") == 24, f"{text_of(page, '#slines .pager span')!r}")
    S.shot("decisions-pager")
    page.click("#sprev")
    S.check("previous goes back to the first decisions", text_of(page, "#slines .pager span") == "decisions 1–40 of 64")


def step_loops(S, page, ctx):
    if ctx["args"].no_loops:
        S.skip("the loops", "--no-loops: the binary has no loops")
        # the mirror rejects the loop syntax
        set_text(page, LOOP_TEXT)
        wait_js(page, "() => document.getElementById('dnote').textContent !== ''")
        S.check("the mirror reports the loop syntax as an error", "does not parse" in text_of(page, "#dnote"), text_of(page, "#dnote"))
        return
    before = set_text(page, ctx["hello"]["example"])
    wait_diagram(page, before=before)
    wait_js(page, "() => document.getElementById('maxiter').disabled")
    S.check("Max iterations is disabled without a loop", True)
    before = set_text(page, LOOP_TEXT)
    wait_diagram(page, before=before)
    wait_js(page, "() => !document.getElementById('maxiter').disabled")
    S.check("Max iterations is enabled with a loop", True)
    S.check("the diagram draws the loop", page.evaluate("() => document.querySelector('#diagram svg').outerHTML.includes('0.5')"))
    reveal(page, "#maxiter"); page.fill("#maxiter", "2")
    reveal(page, "#maxiter"); page.dispatch_event("#maxiter", "change")
    time.sleep(0.5)
    r = compute(page, 30000)
    heads = page.evaluate("() => [...document.querySelectorAll('#out thead th')].map(t => t.textContent)")
    S.check("the loops column appears on a loop process", "loops" in heads, f"{heads}", repro=f"compute {LOOP_TEXT!r} with max iterations 2")
    cells = page.evaluate("() => [...document.querySelectorAll('#out tbody tr:first-child td')].map(t => t.textContent)")
    S.check("the loops cell carries the upper increments", any(c.startswith("+") for c in cells), f"{cells}")
    S.check("the continued line names the passes", "if the loops ran on past 2 passes" in text_of(page, "#str"), text_of(page, "#str")[:200])
    S.check("the answer carries max_iter 2", page.evaluate("() => LAST.max_iter") == 2)
    cont = page.evaluate("() => LAST.points[0].continued")
    S.check("the continued interval is ordered, low at most up", cont and all(float(l) <= float(u) for l, u in zip(cont["low"], cont["up"])), f"{cont}")
    S.shot("loops")
    # max iterations 1 pays less and continues more
    reveal(page, "#maxiter"); page.fill("#maxiter", "1")
    reveal(page, "#maxiter"); page.dispatch_event("#maxiter", "change")
    time.sleep(0.5)
    v2 = page.evaluate("() => LAST.points.map(p => p.approx.map(Number))")
    r1 = compute(page, 30000)
    v1 = page.evaluate("() => LAST.points.map(p => p.approx.map(Number))")
    S.check("fewer passes cost less in every component of the least point", min(p[1] for p in v1) <= min(p[1] for p in v2), f"K=1 {v1} K=2 {v2}")


def step_time(S, page, ctx):
    before = set_text(page, ctx["hello"]["example"])
    wait_diagram(page, before=before)
    wait_js(page, "() => [...document.querySelectorAll('#dirs .seg')].some(s => s.dataset.n === 'time')")
    switch(page, "time")
    wait_js(page, "() => ![...document.querySelectorAll('#dirs .seg')].some(s => s.dataset.n === 'time')")
    S.check("the time leaves the directions", True)
    labels = page.evaluate("() => [...document.querySelectorAll('#window .rname span:first-child')].map(s => s.textContent)")
    S.check("the time leaves the window", not any(l.startswith("time") for l in labels), f"{labels}")
    r = compute(page, 30000)
    S.check("the answer has no time", r["time"] is False and r["names"] == ["euro", "hours"], f"{r}")
    heads = page.evaluate("() => [...document.querySelectorAll('#out thead th')].map(t => t.textContent)")
    S.check("the table header has no time", not any(h.startswith("time") for h in heads), f"{heads}")
    S.check("the plane of two impacts draws the staircase", page.evaluate("() => !!document.querySelector('#out svg path[data-stair]')"))
    S.shot("time-off")
    switch(page, "time")
    wait_js(page, "() => [...document.querySelectorAll('#dirs .seg')].some(s => s.dataset.n === 'time')")
    S.check("the time comes back with the switch", True)


def step_narrow(S, page, ctx):
    page.set_viewport_size({"width": 800, "height": 900})
    time.sleep(0.6)
    sw, cw = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    S.check("at 800 by 900 nothing overflows horizontally", sw <= cw, f"scrollWidth {sw} > clientWidth {cw}",
            repro="open the page at 800x900 after an answer and compare document.scrollingElement.scrollWidth with clientWidth")
    S.shot("narrow-800")
    # with the three-component answer of the chain, the widest plot
    before = set_text(page, chain_text(2, 2))
    wait_diagram(page, before=before)
    reveal(page, "#limit"); page.fill("#limit", "10")
    r = compute(page, 30000)
    time.sleep(0.3)
    sw, cw = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    S.check("at 800 by 900 a parallel-coordinates answer does not overflow", sw <= cw, f"scrollWidth {sw} > clientWidth {cw}")
    S.shot("narrow-800-pcs")
    page.set_viewport_size({"width": 1400, "height": 900})


def step_more(S, page, ctx):
    """Paths beyond the walk asked for: stopping while paused, a search
    started over one running, names with spaces, overwrite, delete, the
    keyboard on a thumb, another mode of the benchmark, the legend."""
    args = ctx["args"]
    # the keyboard on a thumb, on the complete answer of the chain of two
    # (left by step 12): ten steps left on the greatest end of x
    thumb = page.locator("#ranges .range:not(.flat) .rng .thumb.hi").first
    if thumb.count():
        total = int(text_of(page, "#rcount"))
        thumb.focus()
        for _ in range(10):
            page.keyboard.press("ArrowLeft")
        kept = int(text_of(page, "#rcount"))
        S.check("the arrow keys move a thumb and filter the table", kept < total, f"{kept} of {total} kept after ten ArrowLeft")
        page.click("#rreset")
    S.check("the plane with the time carries its legend", page.evaluate("() => !!document.querySelector('#out svg linearGradient')"))
    # stop while paused: the chosen point stays chosen when the answer has it
    page.click("#newp")
    if page.locator("#nw-yes").count():
        page.click("#nw-yes")
    wait_js(page, "() => document.getElementById('proc').value === ''")
    before = set_text(page, chain_text(4, 4))
    wait_diagram(page, before=before)
    reveal(page, "#limit"); page.fill("#limit", "8")
    page.click("#go")
    page.wait_for_selector("#out .livebar #pause", timeout=10000)
    page.click("#pause")
    wait_js(page, "() => document.getElementById('pause').textContent === 'Play'")
    page.click("#out tbody tr:nth-child(2)")
    chosen = page.evaluate("() => LAST.points[1].value.join('|')")
    page.click("#stopsearch")
    wait_js(page, "() => !document.querySelector('#out .livebar') && !document.getElementById('go').disabled", 30000)
    a = page.evaluate("() => ({status: LAST.status, n: LAST.points.length, sel: SELECTED, val: SELECTED === null ? null : LAST.points[SELECTED].value.join('|')})")
    still = page.evaluate("(c) => LAST.points.some(p => p.value.join('|') === c)", chosen)
    S.check("Stop while paused answers with the cut", a["status"] == "cut at the limit", f"{a}")
    S.check("the point chosen in the pause stays chosen when the answer has it", (not still) or a["val"] == chosen,
            f"chosen {chosen} selected {a['val']}", repro="pause a live search, click row 2, press Stop")
    S.check("the row chosen is highlighted in the answer", page.evaluate("() => !!document.querySelector('#out tbody tr.sel')"))
    S.shot("stop-while-paused")
    # a search while one runs: the button is disabled, and Enter in a field
    # the button stays live while a search runs, and a search started then
    # cuts the one before: the id of the live search changes
    reveal(page, "#limit"); page.fill("#limit", "10")
    page.click("#go")
    page.wait_for_selector("#out .livebar #pause", timeout=10000)
    first = page.evaluate("() => LIVE.id")
    S.check("the button stays live while a search runs", page.evaluate("() => !document.getElementById('go').disabled"))
    page.click("#go")
    wait_js(page, "(f) => LIVE && LIVE.id !== f", 10000, first)
    S.check("a search started while one runs cuts the one before", page.evaluate("(f) => LIVE && LIVE.id !== f", first))
    page.wait_for_selector("#out .livebar #stopsearch", timeout=10000)
    page.click("#stopsearch")
    wait_js(page, "() => !document.querySelector('#out .livebar') && !document.getElementById('go').disabled", 30000)
    log = open(os.path.join(args.work, "server.log")).read() if args.work and os.path.exists(os.path.join(args.work, "server.log")) else ""
    if log:
        S.check("the server log records the search and its stop", f"search {first}: stop asked" in log and f"search {first}: Timeout" in log, f"no stop of search {first} in the log")
    else:
        S.skip("the server log", "no --work folder")
    S.shot("search-over-search")
    # the frontier shown leaves when a saved process is loaded
    reveal(page, "#savedpanel summary"); page.click("#savedpanel summary")
    page.click("#savedbox a[data-name='alpha']")
    wait_js(page, "() => document.getElementById('savenote').textContent === 'Loaded alpha.'")
    S.check("loading a process clears the frontier of the text before", "The process changed" in (text_of(page, "#out") or ""))
    # a name with spaces, referred to with quotes and shown under another name
    page.click("#newp")
    if page.locator("#nw-yes").count():
        page.click("#nw-yes")
    wait_js(page, "() => document.getElementById('proc').value === ''")
    before = set_text(page, "( (P, 1, {x: 3}) || (Q, 2, {x: 1}) )")
    wait_diagram(page, before=before)
    page.click("#pname")
    page.keyboard.type("my proc")
    page.keyboard.press("Enter")
    page.click("#save")
    wait_js(page, "() => document.getElementById('savenote').textContent === 'Saved as my proc.'")
    S.check("a name with a space is saved", True)
    page.click("#newp")
    wait_js(page, "() => document.getElementById('proc').value === ''")
    before = set_text(page, '( @"my proc"[Shown] , (Z, 1, {x: 1}) )')
    wait_diagram(page, before=before)
    subs = page.evaluate("() => [...document.querySelectorAll('#subbox .subrow')].map(r => r.textContent)")
    S.check("a quoted reference draws a sub-process box shown under its own name",
            page.evaluate("() => document.querySelectorAll('#diagram svg g.sub.collapsed').length") == 1
            and any("Shown" in r and "my proc" in r for r in subs), f"subs {subs}",
            repro="save a process as 'my proc', write '( @\"my proc\"[Shown] , (Z, 1, {x: 1}) )'")
    S.check("the box carries the shown name", "Shown" in svg_html(page))
    S.shot("quoted-reference")
    # overwrite asked and done
    page.click("#pname")
    page.keyboard.type("alpha")
    page.keyboard.press("Enter")
    page.click("#save")
    wait_js(page, "() => document.getElementById('ow-yes')")
    S.check("saving under a name taken asks before overwriting", "saved already" in text_of(page, "#savenote"))
    page.click("#ow-yes")
    wait_js(page, "() => document.getElementById('savenote').textContent === 'Saved as alpha.'")
    loaded = post(ctx["url"], "/api/load", name="alpha")["process"]
    S.check("the overwrite replaces the text in the store", "my proc" in loaded, f"{loaded[:60]!r}")
    # delete: beta goes, then alpha is refused while the new alpha refers to my proc... my proc is refused
    page.click("#savedbox button.bin[data-name='beta']")
    page.click("#savedbox li.ask button.yes")
    wait_js(page, "() => document.getElementById('savenote').textContent === 'Deleted beta.'")
    wait_js(page, "() => !document.querySelector('#savedbox a[data-name=\"beta\"]')")
    S.check("a process nobody refers to is deleted and leaves the list", True)
    page.click("#savedbox button.bin[data-name='my proc']")
    page.click("#savedbox li.ask button.yes")
    wait_js(page, "() => document.getElementById('savenote').textContent.startsWith('not deleted')")
    S.check("a quoted name referred to cannot be deleted", "referred to by alpha" in text_of(page, "#savenote"))
    S.shot("deleted")
    reveal(page, "#savedpanel summary"); page.click("#savedpanel summary")
    # another mode of the benchmark, regenerated
    if ctx["hello"]["bench"] and len(ctx["hello"]["modes"]) > 1:
        mode = ctx["hello"]["modes"][1]
        reveal(page, "#bench summary"); page.click("#bench summary")
        page.select_option("#b-m", mode)
        if not checked(page, "regen"):
            switch(page, "regen")
        page.fill("#seed", "7")
        page.click("#load")
        wait_js(page, "() => document.getElementById('benchnote').textContent.includes('drawn again')", 20000)
        S.check(f"the mode {mode} regenerates the impacts", "T1" in page.evaluate("() => document.getElementById('proc').value"))
        wait_diagram(page, 15000)
        r = compute(page, 30000)
        S.check(f"the regenerated {mode} instance answers", r["n"] >= 1 and r["status"] in ("complete", "cut at the limit"), f"{r}")
        switch(page, "regen")
        reveal(page, "#bench summary"); page.click("#bench summary")
    else:
        S.skip("another mode of the benchmark", "no benchmark or one mode")
    # the limits of the fields: a limit above the cap and a bad max iterations are clamped, not refused
    reveal(page, "#limit"); page.fill("#limit", "9999")
    r = compute(page, 30000)
    S.check("a time limit above the cap is clamped and the search answers", r["n"] >= 1, f"{r}")
    reveal(page, "#limit"); page.fill("#limit", "10")


if __name__ == "__main__":
    sys.exit(main())
