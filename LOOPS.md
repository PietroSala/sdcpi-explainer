# Loops in sdcpi-loop

**Pietro Sala** — Version 0.1

This repository is `sdcpi-parallel` with loops. Everything of the parent holds;
this file states what the loops add, in the words the code and the note
`parallel-paper/semantics-time.tex` of the paper repository share.

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

The state of the paper maps every node to its phase; with loops it maps every
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
cost and the time are those of the paper: only the fast forward pays. With
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

The recursion of the paper gets one case, at a loop node `v`, region `R`,
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

At a forced exit along a path `h` of probability `P(h)`, the unbounded
process would still run `p / (1 - p)` passes in expectation, each worth a
charge of `R` **in the unbounded process**, the loops inside `R` unbounded
too, since the passes denied run there: the expected cost of the strategy in
the unbounded process exceeds its cost in the truncated one by a vector in
`P(h) · [L̂∞(R), Û∞(R)] · p / (1 - p)`, summed over the forced exits of the
strategy. The engine accumulates this interval along every history (`gap`,
`g_hat` scaled by the probability) and over every frontier, and reports it
per point as `continued`, lower and upper. For the time the lower bound is
taken as zero when the loop sits under a parallel node, the passes of one
branch not lengthening the whole run for certain.

For a frontier accepted before it closed, an apex, the gap also counts, for
every open history, the shift of the bounds of its state from the truncated
to the unbounded recursion, `P(h) · [L∞_s - L_s, U∞_s - U_s]`: the forced
exits the completions of the partial strategy have still to meet. So the
bounds of a frontier with the loops unbounded are its two sums plus
`continued`, whether the frontier is closed or not, and the gap of a point
does not depend on where the accept fired.

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

## Checks

`sdcpi bound <instance> --max-iter K` prints the two bounds at the initial
state, the exact optimum of every component and the worst breach of the
enclosure over every reachable choice state, which must stay at zero.
