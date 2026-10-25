# strategy-determination-bpmn-cpi

**Pietro Sala** — Version 0.4

The command-line tool `sdcpi`, Strategy Determination for BPMN+CPI,
lives at the root of this repository and is documented below on its own
terms; `examples/` holds two instances to explore it with.
`scripts/storm-comparison-ablation/` holds its test suite: a large
differential campaign that confronts the tool with the Storm model
checker and with three ablated configurations of its own search,
rebuildable from nothing, its own `README.md` walking the stages; the
library asks nothing of the scripts, and the scripts ask only the built
library. The recorded reference campaign ships as a split archive that
`setup.py` unpacks; the repository is private for now.

**Strategy Determination for BPMN+CPI**, a command-line tool. Given a business
process with decisions, chance and multi-dimensional costs, it answers one
question: is there a way of taking the decisions that keeps the expected cost
of the process under a budget, in every cost component at once, and if there
is, which one.

Rust, no dependencies:

```sh
cargo build --release
./target/release/sdcpi determine examples/tiny.yaml --B 0.9,0.7
```

## The page

    sdcpi serve                 # then open http://127.0.0.1:8080
    sdcpi serve --port 9000

One page on the loopback address, a mask over `pareto`: paste a process,
choose the workers, the ablation and a time limit, and read the frontier with
the strategy that attains every point. The switch "Expected time", on by
default, is the `--time 1` of the command line: the frontier is then taken
over the expected time and the impacts, the time first. The field "Max
iterations", 1 by default and greyed out while the process has no loop, is the `--max-iter` of the command line, the
passes a loop makes in one activation before its exit is forced; a point
whose strategy met a forced exit shows under its expected values what the
value becomes with the loops unbounded, whatever is taken past the bound,
lower to upper per component, and the table carries the upper increments in
a column "loops" of its own. The panel
that loads a process of the benchmark has a switch "Loops", shown when the
server has the grid with loops (`--bench-loops DIR`, see `LOOPS.md`): the
same coordinates then come from the benchmark with loops. The
server is in the binary, so there is nothing to install beyond what is
already there, it runs on the three desktop systems and inside a container
alike, and it binds `127.0.0.1` and never every address, because it runs a
computation for whoever asks.

The plot is one graph per direction present, the minimized components
and the maximized ones apart: a group of two impacts is drawn in the
plane of the two, the time, when it is in, as the colour of the points,
from light for the shortest to dark for the longest; one impact with the
time in its group in the plane of the two; three or more impacts as
parallel coordinates; one alone as a single axis of dots. The staircase
is drawn only when the whole frontier is two components.
The block "Objective function" at the top of the form sets, with a two-way switch per component, whether the frontier
takes the least or the greatest expected value, the time included; every
label of the page carries the arrow of its direction, the bounds on the
expected values and the ranges beside the plot among them, and with both
directions present the parallel coordinates split into two graphs, the
minimized axes and the maximized ones. Beside the strategy of a chosen point
the button "Use as bounds" writes its values into the bounds on the expected
values, as the upper end of a minimized component and the lower end of a
maximized one, so that the next search looks for the points at least as good.
The block "Tolerances" sets, per component, a fraction of its range within
which two points count as one: a point within the tolerances of a point
found is left out, a partial strategy that cannot leave them is closed, and
the frontier becomes an approximation, every strategy within the tolerances
of a point found and no two points reported within them of each other,
exact again at zero. A frontier of thousands of points along a line, which
the exact search cannot finish, becomes a score of points in seconds.
The table of the points shows a hundred rows at a time and the decisions of
a strategy forty at a time, a pager above each, so that a frontier of some
thousands of points costs nothing to draw.

The search runs in the background and the page watches it: every half a
second a snapshot, the minimal apexes recorded so far, is drawn in the place
of the result with the counters of the moment, the plot and the cards
alone; a point is final already when no node open at that moment has its
lower bound below it, the workers publishing what they hold open as they
go. Pause freezes the view and brings back the table, the ranges and the
strategy, so that the snapshot is explored like a result; Play resumes it;
Stop cuts the search, which then answers as at its time limit, with what it
found and the certificate of the cut. A search started while one runs cuts
the one before.


## Explaining a strategy

This repository is the third of the family, after the tool of the paper and
the one with loops, and what it adds is the explainer. A strategy is a
deterministic policy, and at a choice it decides by a function of the draws
of nature that precede, its own earlier decisions being determined by
those draws: the section "A strategy as a function of the draws" of
`parallel-paper/semantics-time.tex`, in the repository of the paper, proves
it, and `scripts/explainer/check_draws.py` checks every statement of it on
an independent implementation of the semantics. An explanation therefore
reads the draws and nothing else.

    sdcpi explain F.yaml --B 7.6,70.8,16.8 --time 1
    sdcpi pareto  F.yaml --onephase 1 --time 1 --explain 1 --json out.json

For every choice node the strategy decides, the explainer builds a decision
tree. Three rules make it.

**A test is one statement on one draw.** "The draw between A and B took A",
"the loop ran again after its pass 2", "the loop was left after its pass
1". The statement is true when that draw was taken and gave that answer,
and false in every other case: when the draw gave the other answer, and
when it was not taken at all, the run not having come to that pass or not
to that node in it. A test has two branches, and a tree reads as a nest of
"if this, otherwise".

**A draw is named by its passes.** Under loops a draw is taken at one node
once per pass, and it is named by the pass of every loop above its node,
the outermost first: "in pass 3 of the loop 9, pass 2 of the loop 4, the
draw at the nature node 2". The index is nested as the loops are, so a
draw keeps its name whatever the other passes did. A count of the draws at
a node along the run would not: the second draw at a node of an inner loop
is its second pass when the loop ran again, and the first pass of the next
outer pass when it did not. No two draws of a run have the same node and
the same passes, and two histories at which a strategy meets a choice
always differ in the truth of one statement, so a tree exists.

**A forced exit is no draw.** After its last pass, `--max-iter`, a loop is
left by every run: that exit tells nothing apart, the trail does not write
it, and no test speaks of it. The draw after the pass `j` of a loop exists
for `j` below the bound only.

The tree returned has the fewest tests, and among those the least depth,
found by an exact search on the sets of histories within a budget of sets,
two hundred thousand by default and the option `--explain-budget N` of
every command that explains; `--explain-budget inf` lifts the budget, the
search is then exhaustive and every tree certified, at a cost that may be
exponential in the rows of a choice. Past the budget the search goes on
greedily and the explanation says that the tree is not known to be the
fewest. On the running
example, for the strategy that mends and then follows the redo:

    choice 4 (Swap or Mend): 1 history, 0 tests, the fewest
      take Mend
    choice 16 (Courier or Counter): 2 histories, 1 test, the fewest
      if the draw at the nature node 8 (Redo or Pass) took Redo: take Counter
      otherwise: take Courier

and on a loop inside a loop, `(0.5 ((0.5 (A ^[0.5] B)) , (C ^ D)))` at two
passes, the head of a tree:

    choice 7 (C or D): 42 histories, 10 tests, the fewest
      if in pass 1 of the loop 9, pass 1 of the loop 4, the draw at the nature node 2 (A or B) took A:
        if in pass 1 of the loop 9, pass 2 of the loop 4, the draw at the nature node 2 (A or B) took B:
          if in pass 2 of the loop 9, pass 1 of the loop 4, the draw at the nature node 2 (A or B) took A: take C
          otherwise:
            if in pass 2 of the loop 9, the loop 4, which starts with A, was left after its pass 1: take D
            otherwise: take C
        otherwise: take C

**Past the bound.** A tree is a function of any run, every statement being
true or false of it, so it decides also a run that goes on past the bound,
where the exit is a draw again. Nothing more is asked of it there. Within
the bound it is the strategy, exactly; from the first draw that runs a loop
again past its last pass it takes some branch, possibly always the same,
and the whole rest of the run is paid between the two bounds of the
unbounded process at that state. That is the interval `continued` of the
point (see `LOOPS.md`), which holds whatever is taken past the bound and
wherever the loops sit. The check script and the suite try it at one pass
more: the explanation is the strategy up to the first draw past the bound,
and its value stays under the upper end of `continued`.

The explanation does not always take, past the bound, what the strategy
would take there: under a loop in a parallel branch the passes past the
bound move the draws of the other branch in time, and a tree that reads one
of them may take another branch. The note of the paper says when it does
continue the strategy; the tool does not rely on it.

**The impacts in place of the draws.** The journal paper gives a second
explainer, which reads at a choice what the run has paid so far: the cost
of the history at the state where the choice is due, the time elapsed in
front when the expected time is in, then the impacts of the tasks closed.
A function of that vector exists only when no two histories at which the
strategy decides the choice pay the same and take different regions, and
then a tree of tests "the euro paid so far is at most 40" separates them,
two different vectors differing in some component. Where it exists it is
given in two ways, since the fewest tests may need more components than a
function needs: the tree with the fewest tests on every component, and one
tree per minimal set of components on which a function exists, the least
elements of the lattice of the sets that suffice, by size, each with the
fewest tests on that set, such a tree reading every component of its set.
With the time in, each reading is given with the time among the components
and with it left out: the time alone may suffice, and then it is the one
minimal set that holds it. On the page every choice has a row of options:
`draws`, then the tree with the fewest tests on every impact and the time,
then the fewest with the time left out, one option when the two read the
same impacts with as many tests, each named by the impacts it reads, then
one option per minimal set, those with the time first, the sets the trees
before read left out of the row; a reading with no function is a line
under the row naming two histories that pay the same and take different
regions; a choice decided the same way at every history has no row. The command line prints
the same after the tree on the draws, on the running example and the
strategy that mends and then decides by the redo:

    choice 16 (Courier or Counter): 2 histories, 1 test, the fewest
      if the draw at the nature node 8 (Redo or Pass) took Redo: take Counter
      otherwise: take Courier
      by the impacts: 1 test, the fewest, reading time
        if the time elapsed is at most 6: take Courier
        otherwise: take Counter
      by the impacts, the time left out: 1 test, the fewest, reading euro
        if the euro paid so far is at most 40: take Courier
        otherwise: take Counter
      by the fewest impacts, hours: 1 test, the fewest
        if the hours paid so far is at most 14: take Courier
        otherwise: take Counter

A reading with no function says so and names two histories that pay the
same and take different regions; the sets of both readings are in the
JSON, the sets without the time twice. A maximized component is carried negated
by the engine and shown with its sign turned back, "at least" in place of
"at most". The minimal sets are enumerated in full up to twelve
components, the time among them, from the smallest, a set that holds one
found being left out; past twelve one minimal set is found, the
explanation says that the others were not looked for, and the sets of the
reading with the time left out, when that one is still within twelve, are
listed in their place.

`explain` is `determine --explain 1`; `--explain-json F` writes the trees,
and on `pareto` the option explains the strategy of every point, in the
report and in the JSON. A leaf is `{"take": node}`; a test is `{"draw":
node, "loop": bool, "options": [...], "passes": [{"loop": node, "pass":
i}, ...], "pass": j, "value": {"is": "child", "child": node} | {"is":
"again"} | {"is": "out"}, "yes": tree, "no": tree}`, `passes` the loops
above the node of the draw from the outermost, `pass` at a loop only, the
pass of its own the draw follows. Beside the tree on the draws every
choice carries `impacts`: `{"time": bool, "names": [...], "readings":
[...]}`, a reading being `{"time": "in" | "out" | "none", "available":
[...], "separable": bool, "clash": null | {"a": {"after": [...], "take":
node}, "b": {...}}, "minimum": null | tree, "sets": [tree, ...],
"sets_complete": bool}`, with the time in the cost the readings `in` and
`out` in that order and `none` otherwise, a tree `{"components": [...],
"tests": n, "depth": d, "minimal": bool, "tree": ...}` whose tests are
`{"component": j, "name": ..., "time": bool, "relation": "at most" | "at
least", "value": exact, "approx": number, "yes": ..., "no": ...}`. On the page the strategy of a chosen
point has two views, its decisions history by history and its explanation;
the page asks the server for the trees at `POST /api/explain`, with the
process and the decisions of the point. A strategy the search left partial
is explained on the histories it decides. The explainer on a count of the
draws, with three-way tests, is the tag `explainer-occurrences`.

## The model

A process is a YAML file holding a tree, binary but for its loop nodes. The
leaves are **tasks**, each with a `duration` (how long it runs) and an
`impact` (what it costs, a vector of non-negative numbers: money, energy,
hours, anything, one entry per component; components are compared entry by
entry and never added). The inner nodes compose their two children, a loop
node its one child:

| node | meaning |
|---|---|
| `sequence` | `low` runs, then `high` |
| `parallel` | both children run at the same time |
| `choice` | whoever runs the process picks one child |
| `nature: p` | chance picks: `low` with probability `p`, `high` with `1 - p` |
| `loop: p` | one child, `low`, runs and, once it is done, runs again with probability `p` or the loop is left with `1 - p` |

The smallest example, `examples/tiny.yaml`, is one coin flip between two
tasks:

```yaml
tree:
  - nature: 0.317537
    id: 2
    low:
      - task: T1
        id: 1
        duration: 2
        impact: [0.940423, 0.17498]
    high:
      - task: T2
        id: 3
        duration: 1
        impact: [0.830795, 0.799341]
```

The `id` fields number the nodes left to right and must be distinct; the
header keys above `tree:` are bookkeeping and optional. An instance is
always named on the command line by its path.

## Writing an instance

Instances can be written directly, in a grammar smaller than the YAML:

```
process ::= region ("," region)* | region op region
region  ::= task | "(" process ")" | "(" prob process ")" | "@" saved ("[" shown "]")?
op      ::= "||" | "^" | "^[" prob "]"
task    ::= "(" name "," duration ")"
        |   "(" name "," duration "," "{" name ":" value "," ... "}" ")"
```

A region may be `@saved`, a process saved on the page under that name,
spliced in place: every process is a single-entry single-exit region, so a
saved one stands wherever a task would. `@saved[shown]` draws it under
another name, and `@"a saved name"` refers to a name with spaces in it. A
reference carries neither a duration nor impacts, taking both from the
saved process. A saved process may refer to others, to any depth, and never
to itself through any chain, a cycle being an error; the impacts are merged
by name, one table over the whole assembly. The engine sees the assembled
process with its identifiers in-order over the whole; the page shows every
node under the identifier of the process that wrote it, prefixed by the
identifiers of the references that led there, `7-3` for the node 3 of the
process the leaf 7 refers to, and draws every saved process as one box
until it is expanded. On the command line `parse --saved F` resolves the
references against the store `F` the page writes, `sdcpi-processes.json` by
default.

A comma between regions is a sequence, as in the grammar of the model,
and it associates to the left: `A, B, C` means `(A, B), C`, the tree
staying binary, and the chain needs no parentheses of its own. The comma
is unambiguous: a task is recognised by the name after its parenthesis,
and the commas of the tuple are consumed inside it. `||` is a parallel
composition, `^` a choice and `^[p]` a nature node taking its left
operand with probability `p` in `(0, 1)`; each of these three carries
its own parentheses, one operator per pair, so no precedence exists to
remember, and mixing the sequence comma with another operator demands
the parentheses too. The outermost parentheses of the whole term are
optional. A duration is a positive integer. The map of a task names only the impacts
that are strictly positive: an absent name is zero, and a task may end at
the duration, or carry `{}`, both meaning every impact zero. `#` starts a
comment to the end of the line. The names are collected over the whole
process in the order they first appear; the emitted file lists them as
`impact_names`, and that order is the meaning of every `impact` vector and
of every budget `--B` you pass.

### Loops

`(p R)`, a probability right after the parenthesis, where no task and no
process can begin, is a **loop** over the process `R`: `R` runs and, once it
is done, runs again with probability `p` or the loop is left with
probability `1 - p`, with `p` in `(0, 1)`. `R` may be any process, a
sequence included, without parentheses of its own:

```
(0.5 (A, 1, {euro: 10}) , (B, 2, {euro: 4}))      # a loop over a sequence
( (Start, 1, {euro: 1}) || (0.1 (Retry, 2, {euro: 3})) )
(0.3 ( (A, 1, {x: 1}) ^ (B, 2, {x: 2}) ))        # a choice inside the loop
```

In the tree the loop is the one unary node, kind `loop` in the YAML, with the
region as its `low` child and no `high`; it carries `p` as a nature node
carries its probability. A full binary tree has `2t - 1` nodes and every loop
adds one; the identifiers stay in-order, the region first, then the loop
node. The page draws a loop as an exclusive join, the region, an exclusive
split and a back edge above the region, `p again` on the back edge and
`1 - p out` under the split.

The state of the semantics maps every node to a phase and a count, the count
of a node being the number of times it was closed in the activation under way
of the nearest loop above it (the whole run, with no loop above): the fast
forward and the up clause increment the count of the node they close. Two
clauses are added, both at a loop node `v` with region `R` and probability
`p`. With `v` running and `R` idle, `R` starts, as the first region of a
sequence starts. With `v` running and `R` done comes the draw, at the rank of
a nature node: with `m` the count of `R`, the passes closed in this
activation, the one just closed included, if `m < MAX_ITER` nature draws, `R`
starts again with probability `p` and the loop is left with `1 - p`; if
`m = MAX_ITER` the exit is forced, with probability 1. Leaving sets `v` done,
`R` idle, and resets the counts of every node in the subtree of `v`, `v`
included: the exit is the one closing that increments no count. The up clause
never fires at a loop node, whose closing is its exit, and the cost and the
time are paid as before, by the fast forward alone.

`MAX_ITER` is the option `--max-iter K` of every command that reads an
instance, the field "Max iterations" of the page, 1 by default. With it the
MDP is finite: every loop makes at most `K` passes per activation, and an
inner loop at most `K` passes per pass of the loop above it, so it may run
more than `K` times over the whole run. `--max-iter inf` is the process with
its loops unbounded, which has finitely many states up to the counts, reaches
the final state with probability 1 and has a finite expected cost; the two
bounds know it and `bound` accepts it, the search does not run it.

The two estimates get one case at a loop node `v`, region `R`, probability
`p`, for the upper and the lower bound alike and for every component, the
time included, the passes running one after the other. With `R` idle,
`X(v) = X̂(R) · (1 + g)`, where `X̂(R)` is the charge of `R` from its idle
state and `g` the expected number of further passes; with `R` running,
`X(v) = X_s(R) + g · X̂(R)`, the pass under way as the state charges it, then
the further passes; with `R` done and the draw pending, `X(v) = g · X̂(R)`.
Unbounded, `g = p / (1 - p)`; with `K` passes allowed and `m` closed in this
activation, the one under way counted as closed, `g = p + ... + p^(K - m)`,
and `g = p + ... + p^(K - 1)` from idle. Both bounds enclose the expected cost
of every strategy of the process they are computed for, the unbounded one or
the truncated one; the unbounded bounds hold for the truncated process from
above, fewer passes costing less, and the truncated bounds are what the
search uses. `sdcpi bound <instance> --max-iter K` prints the two bounds at
the initial state, the exact optimum of every component and the worst breach
of the enclosure over every reachable choice state, which must stay at zero.

In the unbounded process the exit at the bound is a draw: with `1 - p` the
loop is left, with `p` the region runs again and the run is one the search
never looked at. Whatever is decided from there, what the run still pays
lies between the two bounds of the unbounded process at that state. So a
run of the truncated process counts for its cost with the probability of
never leaving the bound, and every forced exit met after a path of
probability `P(h)` adds `P(h) · S · p · (cost so far + [L∞, U∞])`, with
`S` the probability of not having left before and the bounds taken where
the region runs again. The engine accumulates the two sums along every
history and over every frontier and reports, per point, what they add to
its value, lower and upper; the page shows it under the expected values of
a point, as "with the loops unbounded, whatever is taken past `K` passes",
and the upper increments alone in the column "loops" of the table. The
lower increment may be negative: past the bound the run is free, and may
take a cheaper branch than the strategy takes after the loop. `LOOPS.md`
has the statement and what the interval was before 2026-10-04.

`examples/line.cpi` is an eight-task manufacturing line in this grammar, and
the whole flow is one pipe:

```sh
./target/release/sdcpi parse --file examples/line.cpi --out line.yaml
./target/release/sdcpi determine line.yaml --B 100,8
```

The budget may also be given by name, in any order, and it is rearranged
against `impact_names` before starting:

```sh
./target/release/sdcpi determine line.yaml --B '{hours: 8, kwh: 100}'
```

## Model checker encodings

`sdcpi to_prism` writes the instance as a PRISM `mdp` model, one guarded
command per move of the semantics, one bounded integer variable per node,
and one transition reward structure per impact component, named `impact0`,
`impact1` and so on, paid on the transitions that complete tasks. The model
loads in PRISM and in Storm, and the header of the emitted file carries the
query shapes to run against it.

There are two encodings, and the flag `--encode-history` picks one:

- `true`, the default, records in the state the branch taken at every
  choice and every nature node, written once and never cleared. The plain
  state forgets a decision once its region closes, so two runs that decided
  differently meet again, and a memoryless scheduler on the plain model
  cannot react to a closed decision. With the trail those runs stay apart,
  while the values and the probabilities do not move, so a memoryless
  (positional) deterministic scheduler of the model checker ranges exactly
  over the deterministic strategies of the instance, which is the class
  `determine` searches. This is the encoding for multi-objective queries
  restricted to pure schedulers.
- `false` emits the plain model, smaller, and right for every question
  about a single component, where a memoryless scheduler is already
  optimal.

```sh
./target/release/sdcpi to_prism line.yaml --out line.prism
```

`sdcpi to_objective` writes the question itself: the budget, in either form
`determine` accepts, becomes the property to run against that model, one
`R{"impactj"}<=Bj [ F n<root>=-2 ]` term per component joined in one
`multi(...)`, asking whether some scheduler keeps every expected total
reward within its bound by the time the root is done. Every value is kept
exactly as written, so `0.70` stays `0.70` and is never reformatted. Model
and property together are the whole handoff:

```sh
./target/release/sdcpi to_objective line.yaml --B '{kwh: 100, hours: 8}' --out line.props
storm --prism line.prism --prop line.props --multiobjective:purescheds positional --exact
```

## The question

Because chance is part of the model, a single run proves nothing; what the
decisions control is the **expected** impact, averaged over the draws of
`nature`. A **strategy** says which child to pick at every `choice`, and it
may decide differently depending on everything that happened so far. Given a
budget `B`, one value per component, the tool searches for a strategy whose
expected impact is at most `B` in every component. It answers `yes` with the
strategy, or `no`, meaning no strategy at all fits the budget.

The search works on the process directly, generating a situation only when
it reaches it, and it prunes with two estimates computed from the tree: an
optimistic one, the least any continuation can still cost, and a pessimistic
one, the most; a branch whose optimistic estimate already exceeds the budget
is dropped, and a branch whose pessimistic estimate fits is accepted without
being finished, which is why the returned strategy may leave some situations
undecided: any decision there fits the budget.

## Testing

Test suites live under `scripts/`, one folder with a `run.py` each:
`sdcpi test` lists them, `sdcpi test <suite> [options]` runs one, and
more suites will arrive as the library evolves. The dispatcher knows
only that contract, so the library stays free of what the suites
measure, and the options of a suite are listed by its own `--help`.

Nine suites test the tool and its page in every way at hand; each takes
`--bin PATH` (the mirror without loops with `--no-loops`), prints its
checks made, passed, failed and skipped, exits 0 exactly when nothing
failed, and says the rest in its `--help`:

| suite | what it checks |
|---|---|
| `cli-consistency` | every command over hand-written and random instances: the round trip of parse and to_text, the same frontier across workers, ablations and the two searches, determine against every point of the frontier, the bounds around the points, the windows, the JSON and the anytime file, the time limit, the loops at growing bounds, the exit codes |
| `mirror-agreement` | the loop tool against the mirror on processes without loops, random and from the benchmark, command by command (`--loop`, `--mirror`, `--bench`) |
| `fuzz` | random and mutated texts, instance files and request bodies: never a panic, a hang, a crash or an answer out of contract, on the command line and on the page (`--port`) |
| `storm-directions` | Storm as an outside referee: the optima of every component, the frontier with directions and the achievability of budgets |
| `page-api` | every route of the page server, the lifecycle of a search, the saved processes and their references, the hygiene of the server (`--port`, `--bench`) |
| `page-ui` | the page driven in a headless browser through Playwright for Python, a screenshot per step (`--port`, `--bench`, `--shots DIR`), console errors counted as defects |
| `harness-wide` | the exact harness of the loops over hundreds of random processes, the directed frontier against its oracle, the gap test |
| `tolerance` | the frontier within tolerances: every strategy within them of a point found, no two points reported within them of each other, exact again at zero |
| `explainer` | the explainer end to end on its outputs alone: every decision the leaf of its tree, the process walked by the trees paying the exact value, the fewest tests against an exhaustive search, the page equal to the command line, and the readings of the impacts (`--port`) |

The suites start their own page servers on the ports they are given, with
a store of their own, and never touch a server already running.

Beside the suites, three checks call no suite dispatcher:
`scripts/explainer/check_draws.py` checks the statements of the note on
the explainer on an independent simulator of the semantics,
`scripts/gap/check.py` the interval `continued` against its meaning, and
`scripts/loops/check.py` the engine with loops against an exact backward
induction, with `--directions N` an exact frontier oracle.

The suite that confronts the built binary with the recorded reference
campaign:

```sh
./target/release/sdcpi test storm-comparison-ablation
```

The default run takes a few minutes: it unpacks the recorded campaign
into a fresh scratch folder, keeping the copy beside the scripts
pristine, and re-asks a small slice of the recorded questions through
the three ablated configurations of the search, which exercise the
binary just built, and through Storm, when it is on the PATH, which
referees the same questions from the outside. Every replayed round is
compared with the answer the campaign recorded, the exit code is zero
exactly when every round agrees, and `--full` extends the same
confrontation to the whole campaign.

## Commands

The core of the tool is `determine`; `info`, `bound` and `optima` are its
companions for exploring an instance.

```
sdcpi determine <instance> (--B a,b,... | --B-file F) [options]
                                             is there a strategy whose expected
                                             impact fits the budget, and which one

sdcpi explain   <instance> (--B a,b,... | --B-file F) [--explain-json F] [options]
                                             determine, with the strategy found
                                             explained: a decision tree per choice
                                             node on the draws of nature alone

sdcpi parse     (<process> | --file F) [--out F]
                                             turn a process written in the grammar
                                             below into an instance file, on
                                             standard output or into --out

sdcpi to_prism  <instance> [--encode-history true|false] [--out F]
sdcpi to_objective <instance> (--B a,b,... | --B-file F) [--out F]
sdcpi test      [<suite>] [options...]      run a test suite from scripts/,
                                            forwarding the options; no suite
                                            lists the suites
                                             translate the instance into a PRISM
                                             model, on standard output or into
                                             --out; see below for the two
                                             encodings

sdcpi info    <instance>                     what the file holds: sizes, counts
sdcpi bound   <instance>                     the two estimates at the start: one
                                             traversal of the tree, instant on any
                                             instance, every strategy enclosed
sdcpi optima  <instance>                     the exact least and greatest expected
                                             impact per component, over all
                                             strategies: walks every decision
                                             situation and may hit --max-states
```

## Options of `determine`

| option | meaning |
|---|---|
| `--B a,b,...` | the budget `B`, one value per component, in the order of the instance |
| `--B '{name: value, ...}'` | the budget by name: the map is rearranged against the `impact_names` of the instance before starting, and it must give every impact exactly one value |
| `--B-file F` | read `B` from a YAML file holding `B: [a, b, ...]` or `B: {name: value, ...}`, on one line |
| `--workers N` | parallel workers over one search (default 1) |
| `--ablation MODE` | which of the two prunings to keep: `both`, `accept` (pessimistic only), `reject` (optimistic only), `none`; for measurement, the default `both` is the tool |
| `--selection MODE` | which open situation to extend next: `weighted` (drawn, favouring likely ones, the default), `uniform` (drawn evenly), `oldest` (deterministic, for runs that must repeat exactly) |
| `--seed S` | seed of the drawn selection |
| `--timeout SECS` | give up after this many seconds |
| `--epsilon E` | relative slack when comparing with `B`, for budgets produced by floating point |
| `--steal MODE` | how an idle worker finds work: from its ring neighbour (`ring`) or from anyone (`any`) |
| `--print-size 1` | on `yes`, print how large the returned strategy is: its situations, how many stay undecided, how many decisions it prescribes |
| `--print-strategy 1` | print the strategy itself; without this the decisions are not recorded, which is faster, and only the answer is reported |
| `--window lo..hi,...` | the frontier among the strategies whose expected values lie within these ranges, one `lo..hi` per component in the order of the instance, either side empty for the bound at the initial state; a partial strategy whose interval, from its rejecting sum to its accepting one, misses the window in some component is left at once. On the page the same window is the block "Bounds on the expected values", one red slider per component between the two bounds of the process; a point the search found outside the window is kept apart and drawn grey, as context, and the button of a chosen point sends its exact value as the bound, a fraction `a/b` the option takes too |
| `--maximize a,b,...` | maximize these components, by name or index, the time included once `--time 1` is given: the frontier takes the greatest expected values there and the least elsewhere, a point being dominated when its minimized components are beaten from below and its maximized ones from above; a budget on a maximized component is a floor, and a window on it is written as the values are shown. An impact maximized is an impact negated, the engine minimizing the negation and the sign turned back wherever a value is shown; the time, which is no column of the impacts, is paid as minus the advance of the fast forward, and its two bounds swap at a parallel node, the lesser of the two uppers above and the sum below. The ablation of the lower bound gives way to the lower bound once a payment can be negative. On the page the same choice is the block "Objective function", one two-way switch per component, and every label carries the arrow of its direction |
| `--tolerance f,...` | one fraction per component, of the range of the component between its two bounds at the initial state, the time first when it is in: a point within these of a point found is left out, and a partial strategy that cannot leave them, its lower bound raised by them being a budget known feasible, is closed. The frontier is then an approximation: every strategy is within the tolerances of a point found, reported if within the window and drawn grey otherwise, and no two points reported are within them of each other. At zero the frontier is exact; the one-phase search alone reads them. On the page the same choice is the block "Tolerances", one green slider per component, each shown with what its fraction is worth |
| `--time 1` | the expected time as the component zero of the cost, in front of the impacts, which keep their order; a budget by name calls it `time`, or `expected time` when an impact of the instance is named `time` already. Every command that reads an instance takes it: `pareto` then takes the frontier over the time and the impacts, `bound` and `optima` report the time first, `to_prism` gives the model the reward `impact0`, paid `t_step` by every fast forward, and `to_objective` asks for it. The time is not one more impact: a task carries a zero there and the fast forward pays its advance once, so a parallel node lasts as long as the longer of its regions; its two bounds take the sum of the regions from above and the greater of the two lower bounds from below, and a task is bounded by what is left of its duration |
| `--max-iter K` | the passes a loop makes in one activation before its exit is forced, 1 by default; an inner loop makes at most `K` per pass of the loop above it. Every command that reads an instance takes it, and `bound` alone takes `inf`, the process with its loops unbounded, whose two bounds are computed and whose search is refused; the page sends it as "Max iterations" |
| `--explain-budget N` | the sets of histories the exact search for an explanation tree may solve before it goes on greedily, 200000 by default; `inf` lifts the budget, the search is then exhaustive and every tree certified |

## Output

Plain `key value` lines, one per row, made to be parsed as easily as read:
the instance, the budget `B` used, the answer, the seconds, and counters of
the work done (`expanded` situations, `choice states` met, `histories`
followed). With `--print-strategy 1` the decisions follow, one line per
decided situation.

## Exit

`0` on an answer either way, nonzero on a malformed instance or arguments.

## References

The problem the tool decides, the synthesis of a strategy whose expected
impact stays within a budget over processes with choices, probabilities
and multi-dimensional impacts, was introduced in [1]. The Petri net
semantics behind the model, and the PACO tool family this library
belongs to, are presented in [2]; `sdcpi` implements the synthesis on
the fly, exploring the process without ever building its state space,
and writes the encodings and the objectives for the Storm model checker
[3]. The process shapes and the impact modes of the campaign come from
the benchmark of [4], and the underlying formalism of strategies and
expected values is that of Markov decision processes [5].

1. E. Chini, P. Sala, A. Simonetti, O. Zare. Reactive synthesis for
   expected impacts. Electronic Proceedings in Theoretical Computer
   Science 409, pages 35–52, 2024.
   [doi:10.4204/EPTCS.409.7](https://doi.org/10.4204/EPTCS.409.7)
2. E. Chini, D. Amadori, P. Sala, S. N. Rajput, M. Baldi,
   M. Cappelletti. PACO: a Petri net based tool for designing,
   simulating, and analyzing multi-objectives stochastic processes.
   Application and Theory of Petri Nets and Concurrency, Springer, 2026.
   [doi:10.1007/978-3-032-27879-1_16](https://doi.org/10.1007/978-3-032-27879-1_16)
3. C. Hensel, S. Junges, J.-P. Katoen, T. Quatmann, M. Volk. The
   probabilistic model checker Storm. International Journal on Software
   Tools for Technology Transfer 24(4), pages 589–610, 2022.
   [doi:10.1007/s10009-021-00633-z](https://doi.org/10.1007/s10009-021-00633-z)
4. T. C. Workneh, P. Sala, R. Rizzi, M. Cristani. Business process
   compliance with impact constraints. Information Systems 129, 102505,
   2025.
   [doi:10.1016/j.is.2024.102505](https://doi.org/10.1016/j.is.2024.102505)
5. M. L. Puterman. Markov Decision Processes: Discrete Stochastic
   Dynamic Programming. Wiley, 2014.

## License

MIT; see [LICENSE](LICENSE).
