//! The two bounds of Definition 12: `U_s` from above and `L_s` from below.
//!
//! One traversal of the tree computes both. Every case is shared but one, the
//! choice whose two branches are both idle, where the upper bound takes the
//! componentwise maximum of its branches and the lower bound their componentwise
//! minimum. The recursion reads the state only through the three phases of a
//! node and never through the elapsed progress, which is what lets it be
//! computed once per choice state and reused by every history that ends there.
//!
//! The guard of the sequence case is `s(high(v)) != -1` and not
//! `s(high(v)) >= 0`. The narrow guard leaves `U_s` an upper bound, the surplus
//! being non-negative, and destroys `L_s` outright: on `a -> b`, which carries no
//! XOR node at all, the state where the root runs, `a` is idle and `b` is done
//! would be charged the impact of `a` although the only continuation is the
//! closing move, which pays nothing.
//!
//! With the time as the component zero of the cost (`Tree::time`) the same
//! traversal computes the two bounds of the time, which part from the ones of
//! the impacts at two places. A task owes what is left of its duration,
//! `D(v) - s(v)` when it runs and `D(v)` when it is idle, the progress having
//! been paid by the fast forwards that made it; so the recursion does read the
//! elapsed progress there, of the tasks alone, which a choice state carries in
//! full. A parallel node lasts as long as the longer of its two regions, run by
//! run, so the upper bound takes the sum of the two regions, the greater of two
//! non-negative numbers being at most their sum, and the lower bound the
//! greater of the two lower bounds, the greater of two numbers being at least
//! each of them. The sequence, the choice and the nature cases are the ones of
//! the impacts.
//!
//! The arithmetic is the one of the type parameter, see `num.rs`: the
//! floating-point runs and the exact runs of the frontier take the
//! same traversal.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;

use crate::num::Num;
use crate::state::{DONE, IDLE};
use crate::tree::{Kind, Tree};

/// What ends a computation from outside: a deadline, and a flag another thread
/// raises. In rational arithmetic the numbers grow along the tree, and two
/// thousand draws with long probabilities one inside the other take minutes
/// over one state, so the walk of the bounds reads this as it goes.
#[derive(Clone, Default)]
pub struct Clock {
    pub deadline: Option<Instant>,
    pub cancel: Option<Arc<AtomicBool>>,
}

impl Clock {
    pub fn passed(&self) -> bool {
        self.deadline.map_or(false, |d| Instant::now() >= d)
            || self.cancel.as_ref().map_or(false, |c| c.load(Ordering::Relaxed))
    }
}

/// The clock as one walk reads it, every few nodes. Past it the walk goes on
/// through the tree doing no arithmetic, and what it finds is thrown away.
struct Walk<'c> {
    clock: &'c Clock,
    visits: u32,
    over: bool,
}

impl Walk<'_> {
    #[inline]
    fn over(&mut self) -> bool {
        self.visits = self.visits.wrapping_add(1);
        if !self.over && self.visits % 16 == 0 && self.clock.passed() {
            self.over = true;
        }
        self.over
    }
}

/// The pair `(U_s, L_s)`, each of `k` components.
pub struct Bounds<N: Num> {
    pub upper: Vec<N>,
    pub lower: Vec<N>,
}

/// Computes `U_s` and `L_s` at the root of the tree, the loops bounded as
/// the instance says.
pub fn bounds<N: Num>(tree: &Tree, s: &[i8]) -> Bounds<N> {
    bounds_with(tree, s, tree.max_iter)
}

/// The same with the bound on the passes given, `None` for the unbounded
/// process.
pub fn bounds_with<N: Num>(tree: &Tree, s: &[i8], max_iter: Option<u32>) -> Bounds<N> {
    bounds_until(tree, s, max_iter, &Clock::default()).expect("a clock that never passes")
}

/// The same under a clock: nothing when it passed before the walk ended.
pub fn bounds_until<N: Num>(tree: &Tree, s: &[i8], max_iter: Option<u32>, clock: &Clock) -> Option<Bounds<N>> {
    let k = tree.k;
    let mut stack: Vec<N> = Vec::with_capacity(8 * k);
    let mut cache: Vec<Option<Vec<N>>> = vec![None; tree.nodes.len()];
    let mut walk = Walk { clock, visits: 0, over: false };
    rec(tree, s, tree.root, &mut stack, max_iter, &mut cache, &mut walk);
    if walk.over {
        return None;
    }
    debug_assert_eq!(stack.len(), 2 * k);
    let lower = stack.split_off(k);
    Some(Bounds {
        upper: stack,
        lower,
    })
}

/// `U` and `L` of the region of the loop `v` from its idle state, the
/// charge of one pass: `2k` values, `U` then `L`, the loops inside the
/// region bounded by `max_iter`, or unbounded with `None`.
/// The charge is computed once per loop node and kept in `cache` for the
/// rest of the traversal: a loop inside a loop would otherwise be charged
/// again at every level above it, and the recursion would grow with two to
/// the depth of the nesting.
fn pass<N: Num>(tree: &Tree, v: u32, max_iter: Option<u32>, cache: &mut Vec<Option<Vec<N>>>, walk: &mut Walk) -> Vec<N> {
    if let Some(c) = &cache[v as usize] {
        return c.clone();
    }
    let idle = vec![IDLE; tree.nodes.len()];
    let mut st: Vec<N> = Vec::with_capacity(2 * tree.k);
    rec(tree, &idle, tree.node(v).low, &mut st, max_iter, cache, walk);
    cache[v as usize] = Some(st.clone());
    st
}

/// Pushes the `2k` values `U_s(v)` then `L_s(v)` on `st`.
///
/// The scratch stack keeps the recursion free of allocation: a combinator reads
/// the `4k` values its two children left and folds them into the `2k` values it
/// returns.
fn rec<N: Num>(t: &Tree, s: &[i8], v: u32, st: &mut Vec<N>, max_iter: Option<u32>, cache: &mut Vec<Option<Vec<N>>>, walk: &mut Walk) {
    let k = t.k;

    // s(v) = -2: the node is done and nothing is left to pay; past the clock
    // nothing is computed and what is left is thrown away
    if s[v as usize] == DONE || walk.over() {
        for _ in 0..2 * k {
            st.push(N::zero());
        }
        return;
    }

    let n = t.node(v);
    match n.kind {
        // A task owes its whole impact whatever its elapsed progress: the fast
        // forward pays it at the moment it completes and nothing before. The
        // time is the other way round, paid as it passes: a running task owes
        // what is left of its duration, an idle one the whole of it.
        Kind::Task => {
            let mut i = N::impact_of(t, v);
            if t.time {
                let sv = s[v as usize];
                let left = n.duration as i32 - if sv >= 0 { sv as i32 } else { 0 };
                i[0] = N::from_u32(left as u32);
                if t.maximized[0] {
                    i[0] = N::zero().sub(&i[0]);
                }
            }
            st.extend(i.iter().cloned());
            st.extend(i);
        }

        // The first region of a sequence is spent as soon as the second one has
        // left -1, the up clause retiring it at the hand-over and no clause
        // restoring it.
        Kind::Sequence => {
            if s[n.high as usize] != IDLE {
                rec(t, s, n.high, st, max_iter, cache, walk);
            } else {
                rec(t, s, n.low, st, max_iter, cache, walk);
                rec(t, s, n.high, st, max_iter, cache, walk);
                if walk.over() {
                    st.truncate(st.len() - 2 * k);
                } else {
                    fold_sum(st, k);
                }
            }
        }

        // A parallel node pays both of its branches, a branch that is done
        // contributing the zero vector by the first case; its time is the
        // longer of the two, bounded by their sum from above and by the
        // greater of the two lower bounds from below.
        Kind::Parallel => {
            rec(t, s, n.low, st, max_iter, cache, walk);
            rec(t, s, n.high, st, max_iter, cache, walk);
            if walk.over() {
                st.truncate(st.len() - 2 * k);
            } else {
                fold_parallel(st, k, t.time, t.time && t.maximized[0]);
            }
        }

        // A loop pays its region once per pass: the pass under way, if any,
        // as the recursion charges it from the state, and then the further
        // passes, `p + p^2 + ...` of them in expectation up to the bound on
        // the passes, each worth the charge of the region from its idle
        // state. Both bounds take the sum, the passes running one after the
        // other, the time included.
        Kind::Loop => {
            let p = N::prob_of(t, v);
            let r = n.low;
            let count = if s[v as usize] >= 0 { s[v as usize] as u32 } else { 0 };
            let closed = match s[r as usize] {
                DONE => {
                    for _ in 0..2 * k {
                        st.push(N::zero());
                    }
                    count + 1
                }
                x if x >= 0 => {
                    rec(t, s, r, st, max_iter, cache, walk);
                    count + 1
                }
                _ => {
                    rec(t, s, r, st, max_iter, cache, walk);
                    1
                }
            };
            let further = max_iter.map(|m| m.saturating_sub(closed));
            let g = N::geometric(&p, further);
            let one = pass::<N>(t, v, max_iter, cache, walk);
            if !walk.over() {
                let base = st.len() - 2 * k;
                for j in 0..2 * k {
                    st[base + j] = st[base + j].add(&one[j].scale(&g));
                }
            }
        }

        Kind::Choice | Kind::Nature => {
            let lo = s[n.low as usize];
            let hi = s[n.high as usize];
            if lo >= 0 {
                // The branch has been taken already, and it is the only one that
                // is not idle, by the branch invariant.
                rec(t, s, n.low, st, max_iter, cache, walk);
            } else if hi >= 0 {
                rec(t, s, n.high, st, max_iter, cache, walk);
            } else if lo == IDLE && hi == IDLE {
                rec(t, s, n.low, st, max_iter, cache, walk);
                rec(t, s, n.high, st, max_iter, cache, walk);
                if walk.over() {
                    st.truncate(st.len() - 2 * k);
                } else if n.kind == Kind::Choice {
                    fold_choice(st, k);
                } else {
                    let p = N::prob_of(t, v);
                    fold_nature(st, k, &p);
                }
            } else {
                // The taken branch is done and the closing move has not been
                // taken yet. Stated for both kinds of XOR node, so that the
                // recursion is total.
                for _ in 0..2 * k {
                    st.push(N::zero());
                }
            }
        }
    }
}

// Each fold reads the `4k` values the two children left on the stack, writes the
// `2k` values of the node over the first half, and drops the second: the stack
// grows by `2k` per node and by nothing else.

/// `X(v) = X(low) + X(high)`, on both bounds.
#[inline]
fn fold_sum<N: Num>(st: &mut Vec<N>, k: usize) {
    let base = st.len() - 4 * k;
    for j in 0..2 * k {
        st[base + j] = st[base + j].add(&st[base + 2 * k + j]);
    }
    st.truncate(base + 2 * k);
}

/// `X(v) = X(low) + X(high)` on both bounds, except that with the time in the
/// lower bound of the component zero is `max(L(low)_0, L(high)_0)`: the node
/// lasts as long as the longer of its regions. With the time maximized the
/// component zero is minus the time, and the two bounds swap: the upper
/// bound is `min(U(low)_0, U(high)_0)`, minus the greater lower bound of the
/// time, and the lower bound is the sum, minus the sum of the upper bounds.
#[inline]
fn fold_parallel<N: Num>(st: &mut Vec<N>, k: usize, time: bool, time_max: bool) {
    let base = st.len() - 4 * k;
    for j in 0..2 * k {
        st[base + j] = if time && j == k && !time_max {
            st[base + j].max(&st[base + 2 * k + j])
        } else if time && j == 0 && time_max {
            st[base + j].min(&st[base + 2 * k + j])
        } else {
            st[base + j].add(&st[base + 2 * k + j])
        };
    }
    st.truncate(base + 2 * k);
}

/// The one case where the two bounds part company: the componentwise maximum
/// for `U`, which no policy can exceed whichever branch it takes, and the
/// componentwise minimum for `L`, which no policy can undercut.
#[inline]
fn fold_choice<N: Num>(st: &mut Vec<N>, k: usize) {
    let base = st.len() - 4 * k;
    for j in 0..k {
        st[base + j] = st[base + j].max(&st[base + 2 * k + j]);
        st[base + k + j] = st[base + k + j].min(&st[base + 3 * k + j]);
    }
    st.truncate(base + 2 * k);
}

/// A nature node contributes the expectation of its branches, weighted by the
/// probability its low branch carries, in both bounds.
#[inline]
fn fold_nature<N: Num>(st: &mut Vec<N>, k: usize, p: &N::Prob) {
    let base = st.len() - 4 * k;
    for j in 0..2 * k {
        st[base + j] = N::expect(p, &st[base + j], &st[base + 2 * k + j]);
    }
    st.truncate(base + 2 * k);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::num::Exact;

    #[test]
    fn a_walk_past_its_clock_gives_nothing_and_one_before_it_the_bounds() {
        let text = "(0.5 ((A, 1, {x: 1}) ^[0.3] ((B, 2, {x: 2}) ^ (C, 1, {x: 3}))) || (D, 1, {x: 1})), (E, 1, {x: 1})";
        let mut tree = Tree::parse(&crate::parse::to_yaml(text).unwrap().yaml).unwrap();
        tree.max_iter = Some(2);
        let s0 = crate::state::Engine::new(&tree).initial_state();
        let plain = bounds_with::<Exact>(&tree, &s0, tree.max_iter);
        let later = Clock { deadline: Some(Instant::now() + std::time::Duration::from_secs(600)), cancel: None };
        let timed = bounds_until::<Exact>(&tree, &s0, tree.max_iter, &later).unwrap();
        assert!(plain.upper.iter().zip(&timed.upper).all(|(a, b)| a.0 == b.0));
        assert!(plain.lower.iter().zip(&timed.lower).all(|(a, b)| a.0 == b.0));
        // the clock is read every few nodes: a process long enough to reach it
        let long = (0..64).map(|_| "(A, 1, {x: 1})").collect::<Vec<_>>().join(",");
        let tree = Tree::parse(&crate::parse::to_yaml(&long).unwrap().yaml).unwrap();
        let s0 = crate::state::Engine::new(&tree).initial_state();
        let raised = Clock { deadline: None, cancel: Some(Arc::new(AtomicBool::new(true))) };
        assert!(bounds_until::<Exact>(&tree, &s0, None, &raised).is_none());
        let past = Clock { deadline: Some(Instant::now()), cancel: None };
        assert!(bounds_until::<Exact>(&tree, &s0, None, &past).is_none());
    }
}
