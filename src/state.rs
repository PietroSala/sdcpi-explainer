//! States, triggers and the transition relation of Definitions 6 to 9, plus the
//! macro step the search actually walks.
//!
//! A state maps every node to `-1` when it is idle, `-2` when it is done, and to
//! its elapsed progress when it is running. The three phases and the five
//! triggers are transcribed from Definition 8, and the six clauses from
//! Definition 9, in the order of their rank: choice, down, up, nature, fast
//! forward, absorption. The witness of a rank is the holder of least identifier,
//! and since the arena is indexed by the identifier a scan in increasing index
//! is a scan in the order the definition asks for.
//!
//! ## One departure, and why it is sound
//!
//! Definition 9 advances every running node by `t` at a fast forward, "tasks and
//! internal nodes alike". The elapsed progress of an **internal** node is then
//! never read again: the five triggers test an internal node only for its sign,
//! `t` and the closers range over tasks alone, the cost sums tasks alone, and
//! the recursion of Definition 12 reads phases and nothing else. Two states that
//! differ only in the progress of internal nodes therefore have the same
//! successors up to the same difference, the same probabilities, the same costs
//! and the same bounds, so they are bisimilar and quotienting them changes no
//! value.
//!
//! Quotienting is worth doing: it is what lets a running internal node be one
//! value rather than a counter, which shrinks the key of a choice state and, far
//! more to the point, makes two histories that reach the same configuration at
//! different times share the memo entry instead of computing it twice.
//!
//! The time as the component zero of the cost (`Tree::time`) changes none of
//! this: the advance `t` is read off the running tasks, which keep their
//! progress in full, and the recursion of the bounds reads the progress of the
//! tasks alone.
//!
//! A loop node is the one internal node whose running value is read: it holds
//! the number of passes of its region closed in the activation under way,
//! which the draw at the loop compares with `MAX_ITER`. The counts of the
//! other nodes, which the semantics keeps for every node, are never read and
//! are quotiented away with the progress; the counts inside a loop region
//! are reset at the exit of the loop, which the quotient gives for free, the
//! region being idle then.
//!
//! The quotient is sound by construction here, no rule below reading the value
//! of an internal node other than through its sign. That the rules themselves
//! are Definition 9 is checked from outside, by the `bounds` command, which
//! computes the least and the greatest expected impact of each component over
//! this state graph and compares them with the numbers Storm returns on the
//! PRISM encoding of the same instance, an encoding that carries the progress of
//! every internal node in full.

use crate::num::{zeros, Num};
use crate::tree::{Kind, Tree};

pub const IDLE: i8 = -1;
pub const DONE: i8 = -2;

/// A state, indexed by node identifier; slot `0` is a filler.
pub type State = Vec<i8>;

/// What a state is, when the search stops at it.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Stop {
    /// `s_F`, the final absorbing state.
    Final,
    /// A state of rank 1: a choice is to be decided, at the witness carried.
    Choice(u32),
}

/// One entry of the trail of a history, typed: a decision, or a draw of one
/// of its three kinds. The draws are what an explanation reads.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Entry {
    /// A decision at a choice node: the node and the child it starts.
    Decision(u32, u32),
    /// A draw at a nature node: the node and the child drawn.
    Nature(u32, u32),
    /// The draw of a loop, below the bound, that runs the region again.
    Again(u32),
    /// The draw of a loop, below the bound, that leaves the loop.
    Out(u32),
}

pub struct Engine<'t> {
    pub tree: &'t Tree,
    /// A macro step that produces more outcomes than this fails rather than
    /// exhausting memory. The number of outcomes is exponential in the nature
    /// nodes that fire between two choice states.
    pub max_outcomes: usize,
}

/// One outcome of a macro step: the decisions it took, the impact it adds, the
/// probability it multiplies, and the choice state it ends in.
#[derive(Clone, Debug)]
pub struct Outcome<N: Num> {
    pub decisions: Vec<u32>,
    pub impact: Vec<N>,
    /// What the forced exits met along the macro step hide of the unbounded
    /// process: `2k` values, lower then upper, unscaled by the probability
    /// of the branch, as `impact` is. In the unbounded process the exit at
    /// the bound is a draw, and with the probability of the loop the region
    /// runs again: from there the run is no run of the process the search
    /// decides, and whatever is decided, what it still pays lies between
    /// the two bounds of the unbounded process at that state. The gap is,
    /// summed over the forced exits of the branch, the probability of
    /// leaving there, having stayed until then, times what was paid so far
    /// plus the bound; minus `impact` times the probability of leaving
    /// somewhere along the branch, which the truncated value counts whole.
    pub gap: Vec<N>,
    /// The probability, in the unbounded process, that the run stays within
    /// the bound along this branch: the product of the probabilities of
    /// leaving the loop at each of its forced exits.
    pub stay: N::Prob,
    pub prob: N::Prob,
    pub state: State,
    pub stop: Stop,
}

#[derive(Debug)]
pub enum StepError {
    /// More outcomes than `max_outcomes`: the macro step out of one choice
    /// state branches too widely to hold.
    TooManyOutcomes(usize),
    /// A loop met with no bound on its passes: the engine runs the process
    /// with `MAX_ITER` set and the unbounded one lives in the bounds alone.
    Unbounded,
}

impl<'t> Engine<'t> {
    pub fn new(tree: &'t Tree) -> Engine<'t> {
        Engine {
            tree,
            max_outcomes: 1 << 22,
        }
    }

    /// `s_0`: the root running, everything else idle (Section 4).
    pub fn initial_state(&self) -> State {
        let mut s = vec![IDLE; self.tree.n_nodes as usize + 1];
        s[self.tree.root as usize] = 0;
        s
    }

    #[inline]
    pub fn is_final(&self, s: &[i8]) -> bool {
        s[self.tree.root as usize] == DONE
    }

    /// The rank `r(s)` and the witness `w(s)` of Definition 8, or `None` at
    /// `s_F`, where no trigger holds.
    ///
    /// One pass in increasing identifier keeps, for each rank, the least
    /// identifier that holds it. Scanning in increasing identifier means the
    /// first rank-1 holder found is the witness of rank 1 and no better rank
    /// exists, which is the only early exit available: a smaller rank may still
    /// turn up at a larger identifier, so nothing else may stop the pass.
    pub fn rank_witness(&self, s: &[i8]) -> Option<(u8, u32)> {
        let t = self.tree;
        let mut best: [u32; 6] = [0; 6];
        for id in 1..=t.n_nodes {
            let n = t.node(id);
            let sv = s[id as usize];
            let rank = match n.kind {
                Kind::Task => {
                    if sv >= 0 && sv < n.duration as i8 {
                        5
                    } else {
                        continue;
                    }
                }
                Kind::Parallel => {
                    let (lo, hi) = (s[n.low as usize], s[n.high as usize]);
                    if lo == DONE && hi == DONE {
                        3
                    } else if sv >= 0 && lo == IDLE && hi == IDLE {
                        2
                    } else {
                        continue;
                    }
                }
                Kind::Sequence => {
                    let (lo, hi) = (s[n.low as usize], s[n.high as usize]);
                    if lo == DONE || hi == DONE {
                        3
                    } else if sv >= 0 && lo == IDLE && hi == IDLE {
                        2
                    } else {
                        continue;
                    }
                }
                Kind::Choice => {
                    let (lo, hi) = (s[n.low as usize], s[n.high as usize]);
                    if lo == DONE || hi == DONE {
                        3
                    } else if sv >= 0 && lo == IDLE && hi == IDLE {
                        1
                    } else {
                        continue;
                    }
                }
                Kind::Nature => {
                    let (lo, hi) = (s[n.low as usize], s[n.high as usize]);
                    if lo == DONE || hi == DONE {
                        3
                    } else if sv >= 0 && lo == IDLE && hi == IDLE {
                        4
                    } else {
                        continue;
                    }
                }
                // a loop opens its region as a sequence opens its first one,
                // and draws, with the rank of a nature node, when the region
                // is done: it is never closed by the up clause
                Kind::Loop => {
                    let lo = s[n.low as usize];
                    if lo == DONE {
                        4
                    } else if sv >= 0 && lo == IDLE {
                        2
                    } else {
                        continue;
                    }
                }
            };
            if best[rank] == 0 {
                best[rank] = id;
                if rank == 1 {
                    return Some((1, id));
                }
            }
        }
        for rank in 2..=5u8 {
            if best[rank as usize] != 0 {
                return Some((rank, best[rank as usize]));
            }
        }
        None
    }

    /// The down clause: `tau` starts the low child of a sequence and both
    /// children of a parallel node.
    #[inline]
    fn down(&self, s: &mut [i8], v: u32) {
        let n = self.tree.node(v);
        s[n.low as usize] = 0;
        if n.kind == Kind::Parallel {
            s[n.high as usize] = 0;
        }
    }

    /// The up clause: the hand-over of a sequence, or the closing of a node
    /// above the branches that are done.
    #[inline]
    fn up(&self, s: &mut [i8], v: u32) {
        let n = self.tree.node(v);
        if n.kind == Kind::Sequence
            && s[n.low as usize] == DONE
            && s[n.high as usize] == IDLE
        {
            s[n.low as usize] = IDLE;
            s[n.high as usize] = 0;
        } else {
            s[n.low as usize] = IDLE;
            s[n.high as usize] = IDLE;
            s[v as usize] = DONE;
        }
    }

    /// The fast-forward clause, the only one that pays. `t` is the least time to
    /// a completion over the running tasks, every task at that time completes in
    /// this one step, and the cost is the sum of their impacts.
    #[inline]
    fn fast_forward<N: Num>(&self, s: &mut [i8], impact: &mut [N]) {
        let tree = self.tree;
        let mut t = i32::MAX;
        for &u in &tree.tasks {
            let sv = s[u as usize];
            if sv >= 0 {
                let left = tree.node(u).duration as i32 - sv as i32;
                if left < t {
                    t = left;
                }
            }
        }
        debug_assert!(t > 0 && t != i32::MAX, "the fast forward fires with no running task");
        for &u in &tree.tasks {
            let sv = s[u as usize];
            if sv < 0 {
                continue;
            }
            let left = tree.node(u).duration as i32 - sv as i32;
            if left == t {
                s[u as usize] = DONE;
                let i = N::impact_of(tree, u);
                for c in 0..tree.k {
                    impact[c] = impact[c].add(&i[c]);
                }
            } else {
                s[u as usize] = (sv as i32 + t) as i8;
            }
        }
        // The time as the component zero of the cost: the fast forward pays
        // its advance there, once, whatever the number of tasks it moves.
        if tree.time {
            // the time maximized is paid as minus the advance, the engine
            // minimizing the negation
            let dt = N::from_u32(t as u32);
            impact[0] = if tree.maximized[0] { impact[0].sub(&dt) } else { impact[0].add(&dt) };
        }
        // The progress of a running internal node is not advanced: it is never
        // read, and quotienting it away is what makes a choice state one byte
        // per node. See the note at the head of this file.
    }

    /// Runs the forced moves from `start`, branching at every nature node, until
    /// each branch reaches a choice state or the final state. The decisions of
    /// each branch are appended to `prefix`, which carries the action taken at
    /// the choice state this macro step leaves, so that the first entry of an
    /// outcome is `low(cs)` or `high(cs)` as the board writes it.
    ///
    /// A macro step is one edge of the search and many transitions of the MDP.
    /// Nothing between two choice states is decided by a policy, so nothing
    /// between them has to be held.
    pub fn macro_step<N: Num>(
        &self,
        start: State,
        prefix: Vec<u32>,
    ) -> Result<Vec<Outcome<N>>, StepError> {
        let k = self.tree.k;
        let mut out: Vec<Outcome<N>> = Vec::new();
        // `left`: over the forced exits met so far, the probability of
        // leaving the bound there times what was paid plus the bound of the
        // unbounded process, lower then upper; `stay`: the probability of
        // not having left yet
        let mut stack: Vec<(State, Vec<u32>, Vec<N>, Vec<N>, N::Prob, N::Prob)> =
            vec![(start, prefix, zeros::<N>(k), zeros::<N>(2 * k), N::prob_one(), N::prob_one())];
        // the bounds of the unbounded process at the states where a region
        // runs again past the bound, which many branches share
        let mut beyond: std::collections::HashMap<State, crate::bound::Bounds<N>> = std::collections::HashMap::new();
        // the gap of a branch that ends: what was left, minus the impact
        // times the probability of having left
        let close = |left: Vec<N>, impact: &[N], stay: &N::Prob| -> Vec<N> {
            let gone = N::prob_complement(stay);
            (0..2 * k).map(|c| left[c].sub(&impact[c % k].scale(&gone))).collect()
        };

        while let Some((mut s, mut decisions, mut impact, mut left, mut stay, mut prob)) = stack.pop() {
            loop {
                match self.rank_witness(&s) {
                    None => {
                        let gap = close(left, &impact, &stay);
                        out.push(Outcome {
                            decisions,
                            impact,
                            gap,
                            stay,
                            prob,
                            state: s,
                            stop: Stop::Final,
                        });
                        break;
                    }
                    Some((1, v)) => {
                        let gap = close(left, &impact, &stay);
                        out.push(Outcome {
                            decisions,
                            impact,
                            gap,
                            stay,
                            prob,
                            state: s,
                            stop: Stop::Choice(v),
                        });
                        break;
                    }
                    Some((4, v)) if self.tree.kind(v) == Kind::Loop => {
                        // the draw of a loop whose region is done: the count
                        // of the passes closed in this activation sits on the
                        // node while it runs, the one just closed included
                        // now; below the bound the region runs again with
                        // probability p, the loop is left with 1 - p; at the
                        // bound the exit is forced, and what the unbounded
                        // process does there goes to the gap
                        let n = self.tree.node(v);
                        let bound = self.tree.max_iter.ok_or(StepError::Unbounded)?;
                        let closed = s[v as usize] as u32 + 1;
                        if closed < bound {
                            if out.len() + stack.len() + 2 > self.max_outcomes {
                                return Err(StepError::TooManyOutcomes(self.max_outcomes));
                            }
                            let p = N::prob_of(self.tree, v);
                            let mut s_exit = s.clone();
                            s_exit[n.low as usize] = IDLE;
                            s_exit[v as usize] = DONE;
                            let mut d_exit = decisions.clone();
                            d_exit.push(v);
                            stack.push((
                                s_exit,
                                d_exit,
                                impact.clone(),
                                left.clone(),
                                stay.clone(),
                                N::prob_mul(&prob, &N::prob_complement(&p)),
                            ));
                            s[n.low as usize] = 0;
                            s[v as usize] = closed as i8;
                            decisions.push(n.low);
                            prob = N::prob_mul(&prob, &p);
                        } else {
                            // In the unbounded process this is a draw. With
                            // probability p the region runs again: the run
                            // leaves the process the search decides, and
                            // from that state on, whatever is decided, it
                            // pays between the two bounds of the unbounded
                            // process there. With 1 - p the loop is left,
                            // and the run goes on as this one does.
                            let p = N::prob_of(self.tree, v);
                            let mut again = s.clone();
                            again[n.low as usize] = 0;
                            again[v as usize] = closed as i8;
                            let b = beyond
                                .entry(again)
                                .or_insert_with_key(|a| crate::bound::bounds_with::<N>(self.tree, a, None));
                            let leaving = N::prob_mul(&stay, &p);
                            for c in 0..k {
                                left[c] = left[c].add(&impact[c].add(&b.lower[c]).scale(&leaving));
                                left[k + c] = left[k + c].add(&impact[c].add(&b.upper[c]).scale(&leaving));
                            }
                            stay = N::prob_mul(&stay, &N::prob_complement(&p));
                            s[n.low as usize] = IDLE;
                            s[v as usize] = DONE;
                        }
                    }
                    Some((4, v)) => {
                        if out.len() + stack.len() + 2 > self.max_outcomes {
                            return Err(StepError::TooManyOutcomes(self.max_outcomes));
                        }
                        let n = self.tree.node(v);
                        let p = N::prob_of(self.tree, v);
                        let mut s_high = s.clone();
                        s_high[n.high as usize] = 0;
                        let mut d_high = decisions.clone();
                        d_high.push(n.high);
                        stack.push((
                            s_high,
                            d_high,
                            impact.clone(),
                            left.clone(),
                            stay.clone(),
                            N::prob_mul(&prob, &N::prob_complement(&p)),
                        ));

                        s[n.low as usize] = 0;
                        decisions.push(n.low);
                        prob = N::prob_mul(&prob, &p);
                    }
                    Some((2, v)) => self.down(&mut s, v),
                    Some((3, v)) => self.up(&mut s, v),
                    Some((5, _)) => self.fast_forward(&mut s, &mut impact),
                    Some((r, _)) => unreachable!("rank {r} is not a clause"),
                }
            }
        }
        Ok(out)
    }

    /// The trail of a history read back: its entries typed, a decision or a
    /// draw of one of its three kinds, and the choice due at its end, `None`
    /// when the run is over. An identifier alone does not say what it is,
    /// the one of a loop being written where the loop is started as a child
    /// and where it is left, so the trail is walked through the clauses, as
    /// the macro step walks them.
    ///
    /// Beside every entry, the passes it is taken in: for every loop above
    /// its node, the outermost first, the pass of that loop that is running,
    /// counted from 1, and for the draw of a loop, last, the pass of its own
    /// region that the draw follows. The exit a loop is forced to at the
    /// bound is no draw: it writes nothing in the trail and has no entry.
    ///
    /// Last, the cost paid along the trail, `k` values: what the fast
    /// forwards paid up to the state the trail ends at, the time in front
    /// when it is in. It is what an explanation reads when it reads the
    /// impacts in place of the draws.
    pub fn replay<N: Num>(&self, dh: &[u32]) -> Result<(Vec<Entry>, Vec<Vec<u32>>, Option<u32>, Vec<N>), String> {
        let t = self.tree;
        let above = t.loops_above();
        // the pass of every loop above `v` that is running: the count of a
        // loop node is the passes its region has closed
        let running = |s: &[i8], v: u32| -> Vec<u32> {
            above[v as usize].iter().map(|&l| s[l as usize] as u32 + 1).collect()
        };
        let mut passes: Vec<Vec<u32>> = Vec::with_capacity(dh.len());
        let mut s = self.initial_state();
        let mut cost: Vec<N> = zeros::<N>(t.k);
        let mut entries: Vec<Entry> = Vec::with_capacity(dh.len());
        let mut at = 0usize;
        // every turn of the loop applies one clause; a run of the process is
        // finite, and the bound is there for a trail that is not one
        let limit = 64 * (t.n_nodes as usize + 1) * (dh.len() + 2) * 128;
        for _ in 0..limit {
            match self.rank_witness(&s) {
                None => {
                    if at != dh.len() {
                        return Err(format!("the trail goes on past the end of the run, at entry {at}"));
                    }
                    return Ok((entries, passes, None, cost));
                }
                Some((1, v)) => {
                    if at == dh.len() {
                        return Ok((entries, passes, Some(v), cost));
                    }
                    let n = t.node(v);
                    let a = dh[at];
                    if a != n.low && a != n.high {
                        return Err(format!("entry {at} of the trail, {a}, is no child of the choice {v}"));
                    }
                    at += 1;
                    passes.push(running(&s, v));
                    s[a as usize] = 0;
                    entries.push(Entry::Decision(v, a));
                }
                Some((4, v)) if t.kind(v) == Kind::Loop => {
                    let n = t.node(v);
                    let bound = t.max_iter.ok_or("the loops have no bound on their passes")?;
                    let closed = s[v as usize] as u32 + 1;
                    if closed < bound {
                        let x = *dh.get(at).ok_or(format!("the trail ends at the draw of the loop {v}"))?;
                        at += 1;
                        // the passes of the loops above, and the pass of
                        // this loop that the draw follows
                        let mut index = running(&s, v);
                        index.push(closed);
                        passes.push(index);
                        if x == n.low {
                            s[n.low as usize] = 0;
                            s[v as usize] = closed as i8;
                            entries.push(Entry::Again(v));
                        } else if x == v {
                            s[n.low as usize] = IDLE;
                            s[v as usize] = DONE;
                            entries.push(Entry::Out(v));
                        } else {
                            return Err(format!("entry {}, {x}, is neither the region nor the exit of the loop {v}", at - 1));
                        }
                    } else {
                        s[n.low as usize] = IDLE;
                        s[v as usize] = DONE;
                    }
                }
                Some((4, v)) => {
                    let n = t.node(v);
                    let x = *dh.get(at).ok_or(format!("the trail ends at the draw of the nature node {v}"))?;
                    if x != n.low && x != n.high {
                        return Err(format!("entry {at} of the trail, {x}, is no child of the nature node {v}"));
                    }
                    at += 1;
                    passes.push(running(&s, v));
                    s[x as usize] = 0;
                    entries.push(Entry::Nature(v, x));
                }
                Some((2, v)) => self.down(&mut s, v),
                Some((3, v)) => self.up(&mut s, v),
                Some((5, _)) => self.fast_forward(&mut s, &mut cost),
                Some((r, _)) => return Err(format!("rank {r} is not a clause")),
            }
        }
        Err("the replay of the trail does not end".to_string())
    }

    /// The macro step out of the initial state, which no action precedes: the
    /// `InitHistories` of the board.
    pub fn initial_outcomes<N: Num>(&self) -> Result<Vec<Outcome<N>>, StepError> {
        self.macro_step(self.initial_state(), Vec::new())
    }

    /// The macro step out of the choice state `s` under the action that starts
    /// the child `action` of its witness.
    pub fn step_action<N: Num>(&self, s: &[i8], action: u32) -> Result<Vec<Outcome<N>>, StepError> {
        let mut next = s.to_vec();
        next[action as usize] = 0;
        self.macro_step(next, vec![action])
    }
}
