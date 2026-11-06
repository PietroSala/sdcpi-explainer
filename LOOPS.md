# Loops in sdcpi

**Pietro Sala** — Version 0.3

This file states what the loops add to the tool, in the words of the code.

## Grammar

A loop is a region: `(p R)`, a parenthesis, a probability `p` in (0,1), the
region `R`, the parenthesis closed. `p` is the probability of running `R`
again once it is done, `1 - p` the probability of leaving. Examples:

```
(0.5 (A, 1, {euro: 10}) , (B, 2, {euro: 4}))      # a loop over a sequence
( (Start, 1, {euro: 1}) || (0.1 (Retry, 2, {euro: 3})) )
(0.3 ( (A, 1, {x: 1}) ^ (B, 2, {x: 2}) ))        # a choice inside the loop
```

In the tree the loop is the one unary node, kind `loop` in the YAML, with the
region as its `low` child and no `high`; it carries `p` as a nature node
carries its probability. A full binary tree has `2t - 1` nodes; every loop
adds one. Identifiers stay in-order: the region first, then the loop node.

## Semantics

A state without loops maps every node to its phase; with loops it maps every
node to a phase and a count. The count of a node is the number of times the
node was closed in the activation under way of the nearest loop above it
(the whole run, with no loop above): the fast forward and the up clause
increment the count of the node they close. Two clauses are added, both at a
loop node `v` with region `R` and probability `p`:

- **down at a loop** (rank 2): `v` running, `R` idle: `R` starts, as the
  first region of a sequence starts.
- **loop draw** (rank 4, the rank of a nature node): `v` running, `R` done.
  With `m` the count of `R`, the passes closed in this activation, the one
  just closed included: if `m < MAX_ITER`, nature draws, `R` starts again
  with probability `p` and the loop is left with `1 - p`; if `m = MAX_ITER`,
  the exit is forced, with probability 1. Leaving sets `v` done, `R` idle,
  raises the count of `v` by one, as every closing does, so that a loop
  above reads how many times its region, this loop, was closed, and resets
  the counts of every node strictly under `v`.

The up clause never fires at a loop node, whose closing is its exit. The
cost and the time do not change: only the fast forward pays. With
`MAX_ITER` the MDP is finite, every loop making at most `MAX_ITER` passes
per activation and an inner loop at most `MAX_ITER` passes per pass of the
loop above it. `MAX_ITER = inf` is the process with its loops unbounded,
which has finitely many states up to the counts, reaches the final state
with probability 1 and has a finite expected cost; the bounds know it, the
engine does not run it.

**In the engine** the count of a region sits on the running loop node above
it (`s[v]`, otherwise `0` for a running internal node), the counts of the
other nodes being never read and quotiented away with the progress of the
internal nodes; the reset at the exit comes for free, the region being idle
then. The trail of a history records the region started by a draw that runs
it again and the loop node itself at an exit; a forced exit records nothing,
having one successor.

## The bounds

The recursion of the bounds gets one case, at a loop node `v`, region `R`,
probability `p`, for `U` and `L` alike and for every component, the time
included (the passes run one after the other):

- `R` idle: `X(v) = X̂(R) · (1 + g)`, where `X̂(R)` is the charge of `R` from
  its idle state and `g = p + p² + ... ` the expected number of further
  passes;
- `R` running: `X(v) = X_s(R) + g · X̂(R)`, the pass under way as the state
  charges it, then the further passes;
- `R` done, the draw pending: `X(v) = g · X̂(R)`.

Unbounded, `g = p / (1 - p)`. With `MAX_ITER = K` and `m` passes closed in
this activation, the one under way counted as closed, `g = p + ... + p^(K-m)`
(`g = p + ... + p^(K-1)` from idle). Both bounds enclose the expected cost of
every policy of the process they are computed for, the unbounded one or the
truncated one. The unbounded bounds hold for the truncated process from above
(fewer passes cost less) and the truncated bounds are what the search uses.

## The gap at a forced exit

The search decides the process truncated at `MAX_ITER` passes, and its
strategies say nothing past the bound. In the unbounded process the exit
at the bound is a draw: with `1 - p` the loop is left, and the run is still
a run of the truncated process; with `p` the region runs again, and from
that state on the run is one the search never looked at. **Whatever is
decided from there**, what the run still pays lies between the two bounds
of the unbounded process at that state, `L∞` and `U∞` of the whole process
with the region started again. So, for a strategy and any policy of the
unbounded process that takes what the strategy takes until the first draw
past the bound:

- a run of the truncated process of probability `P`, with `S` the product
  of `1 - p` over the forced exits it met, counts for `P · S · cost`;
- a forced exit met after a path of probability `P`, with `S` so far and
  `C` paid so far, counts for `P · S · p · (C + [L∞, U∞])`, the bounds at
  the state where the region runs again.

The two sums are the ends between which the value lies with the loops
unbounded, and `continued` is what they add to the value of the point,
lower and upper. The engine carries `S` and the accumulated terms along
every history (`stay`, `gap`, `g_hat` scaled by the probability) and over
every frontier. The lower end may be negative in a component: past the
bound the run is free, and may take a cheaper branch than the strategy
takes after the loop. The interval holds with a loop under a parallel node
as elsewhere, and it shrinks as `p^MAX_ITER`.

Until 2026-10-04 `continued` was the expected cost of the passes denied
alone, `P(h) · p / (1 - p) · [L̂∞(R), Û∞(R)]` per forced exit, which assumes
that everything else goes as in the strategy. Under a loop in a parallel
branch that is not so, the passes past the bound moving in time the draws
and the choices of the other branch: a strategy may then have no
continuation at all, and the old interval was the interval of no policy.
The tag `continued-denied-passes` is that version. The narrower interval
remains a statement on the strategies that do continue.

For a frontier accepted before it closed, an apex, an open history of
probability `P`, with `S` and cost `C`, counts for `P · S · (C + [L∞_s,
U∞_s])` at its state `s`, where the two sums of the frontier count `P · (C
+ [L_s, U_s])`: `continued` carries the difference, so the bounds of a
frontier with the loops unbounded are its two sums plus `continued`,
whether the frontier is closed or not.

## Limits

- The count of a loop sits in the byte of its node: `--max-iter` takes at
  most 127, and the page at most 50.
- A macro step enumerates every path between two choice states. Loops
  nested over nature nodes with no choice between them multiply those paths,
  `14 + 14² + 14³` for three loops at three passes over one nature node, and
  the cap of four million outcomes is reached with two tasks: such a process
  is not searched, and the limit is reported as `TooManyOutcomes`.
- The exploration of the frontier meets the strategies in the order of its
  selection, and a loop multiplies the histories of a strategy, `2954` at
  three passes where two passes give `12` on one instance of seven tasks;
  the one-phase search then records many complete strategies before the
  pruning bites and can run past its limit on an instance whose frontier is
  one point. This is the search of the parent, not the semantics.
- Storm, on the model of `to_prism`, ranges over positional pure schedulers
  that keep no past of a loop, a subclass of the policies of the engine: it
  may return a subset of the frontier, never a point outside it.

## The command line and the page

`--max-iter K` on every command that reads an instance, default 1; `inf` on
`bound` alone. The page takes `max_iter` with the frontier request, and shows
`continued` beside a point when its loops met a forced exit.

The benchmark with loops is the branch `loops` of process-impact-benchmarks,
one thousand skeletons with a loop at least each, completed by the builder of
the first stage with `--loops` (impacts by the six modes in two to ten
dimensions, durations as before, the alternation of choice and nature forced
through the loops, a loop counting as a nature, its probability of running
again uniform in the unit interval) into a grid of fifty-four thousand
instances, `scripts/pareto-frontier/experiment/instances-loops`, out of git.
`sdcpi serve --bench-loops DIR` names that grid, or the environment
`SDCPI_BENCH_LOOPS`, or the folder `<bench>-loops` beside the one of `--bench`;
the panel of the benchmark on the page then shows the switch Loops, which
loads the same coordinates from the grid with loops, the key suffixed `-loops`,
and an instance not on disk is drawn by the builder with `--loops` when the
checkout of the branch sits beside it.

`--maximize a,b,...` on every command that reads an instance maximizes the
components named, the time included once it is in. An impact maximized is an
impact negated, the engine minimizing the negation and the sign turned back
wherever a value is shown, so the bounds, the loop charges and the gap of a
forced exit read the negation unchanged. The time maximized is paid as minus
the advance of the fast forward, a task owes minus what is left of its
duration, and at a parallel node the two bounds of the time swap: the upper
bound is the lesser of the two upper bounds and the lower bound the sum,
which is the negation of the rule for the time minimized, the sum above and
the greater of the two below. The gap of a forced exit under a parallel node
has its zero on the upper bound then, not on the lower one. The page sends
the maximized names as `maximize` with every request, and every answer
carries `maximized`, one flag per component.

## Checks

`sdcpi bound <instance> --max-iter K` prints the two bounds at the initial
state, the exact optimum of every component and the worst breach of the
enclosure over every reachable choice state, which must stay at zero.
