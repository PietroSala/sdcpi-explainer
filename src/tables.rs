//! The two tables of the memory blueprint: decision histories keyed by their
//! decisions, choice states keyed by the state itself.
//!
//! Everything that depends on the state alone, the two bounds and the two macro
//! steps that leave it, is held on the choice state and computed once, however
//! many histories end there and however many workers reach it. Everything that
//! depends on the history, its accumulated impact, its probability and the two
//! contributions it makes to the frontier sums, is held on the history and
//! carried incrementally, one addition and one multiplication per expansion.
//!
//! Both tables are keyed, and the key is what regulates the concurrency: two
//! workers that reach the same choice state find the same record, and the second
//! finds the work of the first rather than repeating it.
//!
//! The records live in the arena and are passed as plain shared references, not
//! in reference-counted pointers: see `arena.rs` for why that was the difference
//! between sixteen workers helping and sixteen workers hurting. The cells that
//! hold what is computed on demand are `OnceLock` and not `Mutex`, for the same
//! reason: after the first touch a read is one atomic load.

use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Mutex, OnceLock};

use crate::arena::Arena;
use crate::bound::{bounds, bounds_with};
use crate::num::{zeros, Num};
use crate::state::{Engine, StepError, Stop};
use crate::tree::Tree;

// ---------------------------------------------------------------------------
// a small fast hasher: the keys here are short byte and word slices, and the
// default hasher of the standard library is a keyed one built for a different
// threat model
// ---------------------------------------------------------------------------

#[derive(Default)]
pub struct FxHasher {
    hash: u64,
}

const SEED: u64 = 0x51_7c_c1_b7_27_22_0a_95;

impl FxHasher {
    #[inline]
    fn add(&mut self, word: u64) {
        self.hash = (self.hash.rotate_left(5) ^ word).wrapping_mul(SEED);
    }
}

impl Hasher for FxHasher {
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        let mut chunks = bytes.chunks_exact(8);
        for c in &mut chunks {
            self.add(u64::from_le_bytes(c.try_into().unwrap()));
        }
        let rest = chunks.remainder();
        if !rest.is_empty() {
            let mut buf = [0u8; 8];
            buf[..rest.len()].copy_from_slice(rest);
            self.add(u64::from_le_bytes(buf));
        }
    }

    #[inline]
    fn write_u8(&mut self, i: u8) {
        self.add(i as u64);
    }

    #[inline]
    fn write_u32(&mut self, i: u32) {
        self.add(i as u64);
    }

    #[inline]
    fn finish(&self) -> u64 {
        self.hash
    }
}

type FxBuild = BuildHasherDefault<FxHasher>;

fn shard_of_state(state: &[i8], shards: usize) -> usize {
    let mut h = FxHasher::default();
    for &v in state {
        h.write_u8(v as u8);
    }
    (h.finish() >> 32) as usize % shards
}

fn shard_of_dh(dh: &[u32], shards: usize) -> usize {
    let mut h = FxHasher::default();
    for &v in dh {
        h.write_u32(v);
    }
    (h.finish() >> 32) as usize % shards
}

// ---------------------------------------------------------------------------
// the records
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Branch {
    Low,
    High,
}

/// One outcome of the macro step out of a choice state under one action: the
/// decisions it takes, the impact to be added, the probability to be multiplied,
/// and the choice state it ends in.
pub struct Extension<'a, N: Num> {
    pub decisions: Box<[u32]>,
    pub impact: Box<[N]>,
    /// The gap the forced exits of the loops left along the macro step,
    /// `2k` values, lower then upper, unscaled as `impact` is.
    pub gap: Box<[N]>,
    /// The probability that the unbounded run stays within the bound along
    /// the macro step.
    pub stay: N::Prob,
    pub prob: N::Prob,
    pub cs: &'a ChoiceState<'a, N>,
}

pub struct ChoiceState<'a, N: Num> {
    pub state: Box<[i8]>,
    pub stop: Stop,
    /// `U_s`, from Definition 12.
    pub upper: Box<[N]>,
    /// `L_s`, from Definition 12.
    pub lower: Box<[N]>,
    /// The two bounds with the loops unbounded, `U^inf_s` and `L^inf_s`:
    /// the same vectors as `upper` and `lower` when the process has no loop.
    pub upper_inf: Box<[N]>,
    pub lower_inf: Box<[N]>,
    low: OnceLock<&'a [Extension<'a, N>]>,
    high: OnceLock<&'a [Extension<'a, N>]>,
}

impl<'a, N: Num> ChoiceState<'a, N> {
    #[inline]
    pub fn is_final(&self) -> bool {
        matches!(self.stop, Stop::Final)
    }

    /// The node the two actions start, at a choice state that is not final.
    pub fn action(&self, tree: &Tree, branch: Branch) -> u32 {
        match self.stop {
            Stop::Choice(w) => {
                let n = tree.node(w);
                match branch {
                    Branch::Low => n.low,
                    Branch::High => n.high,
                }
            }
            Stop::Final => panic!("the final state decides nothing"),
        }
    }
}

/// What one action does to one history: the histories that replace it, and the
/// three quantities the frontier needs to move from the one to the others.
///
/// Held on the history and computed once. The extensions of a choice state are
/// shared by every history that ends there; the histories they lead to are not,
/// since they carry the impact and the probability of this path. Two branches of
/// the computation tree that decided an earlier choice differently reach the
/// same history all the same, and then they share this too.
pub struct Expansion<'a, N: Num> {
    /// The children that are still open, which go into the frontier.
    pub open_children: Box<[&'a History<'a, N>]>,
    /// What the children that are already final add to the closed part.
    pub closed_delta: Box<[N]>,
    /// What the open children add to the accepting sum.
    pub open_e_delta: Box<[N]>,
    /// What the open children add to the rejecting sum.
    pub open_l_delta: Box<[N]>,
    /// What every child, open or final, adds to the gap of the frontier.
    pub gap_delta: Box<[N]>,
    /// What the open children add to the shift of the bounds.
    pub open_d_delta: Box<[N]>,
    /// How many children are already final. They leave no trace in the frontier
    /// beyond their contribution to the closed part, so a frontier that wants to
    /// say how many histories it holds has to be told their number here.
    pub closed_count: usize,
}

pub struct History<'a, N: Num> {
    /// A serial number, which gives the frontier an order that does not depend
    /// on where a record happens to sit in memory.
    pub id: u64,
    /// The key: the identifiers of the decision nodes taken, one per XOR node
    /// passed, choice and nature alike.
    pub dh: Box<[u32]>,
    /// `I_dh`, the impact accumulated along the path.
    pub impact: Box<[N]>,
    /// `G_dh`, the gap accumulated along the path, `2k` values, lower then
    /// upper, unscaled: over the forced exits of the path, the probability
    /// that the unbounded run leaves the bound there, having stayed until
    /// then, times what the path had paid plus the bound of the unbounded
    /// process at the state it leaves to; minus `I_dh` times the
    /// probability of having left somewhere along the path.
    pub gap: Box<[N]>,
    /// `S_dh`, the probability that the unbounded run stays within the
    /// bound along the path: the product, over its forced exits, of the
    /// probabilities of leaving the loop there.
    pub stay: N::Prob,
    /// `P_dh`, the product of the transition probabilities along the path.
    pub prob: N::Prob,
    /// The choice state the path ends in, shared with every other history that
    /// ends there.
    pub cs: &'a ChoiceState<'a, N>,
    /// `P_dh * (I_dh + U_cs)`, the contribution to the accepting sum of any
    /// frontier that holds this history.
    pub e_hat: Box<[N]>,
    /// `P_dh * (I_dh + L_cs)`, the contribution to the rejecting sum.
    pub l_hat: Box<[N]>,
    /// `P_dh * G_dh`, the gap of the path scaled by its probability.
    pub g_hat: Box<[N]>,
    /// `P_dh * (S_dh L^inf_cs - L_cs)` then `P_dh * (S_dh U^inf_cs - U_cs)`,
    /// `2k` values: what the bounds of the state still to run become with
    /// the loops unbounded, weighed by the probability of still being within
    /// the bound there, counted for the open histories of a frontier so that
    /// the gap of a frontier accepted before it closed covers what its
    /// completions have still to meet.
    pub d_hat: Box<[N]>,
    low: OnceLock<&'a Expansion<'a, N>>,
    high: OnceLock<&'a Expansion<'a, N>>,
}

impl<'a, N: Num> History<'a, N> {
    #[inline]
    pub fn is_open(&self) -> bool {
        !self.cs.is_final()
    }
}

/// The gap of a history before scaling, read back from `g_hat`: the scaled
/// value divided by the probability would need a division the arithmetic
/// does not offer, so the gap is kept on the history too.
#[inline]
fn elected_gap<'a, N: Num>(h: &History<'a, N>, c: usize) -> N {
    h.gap[c].clone()
}

// ---------------------------------------------------------------------------
// where the records live
// ---------------------------------------------------------------------------

/// Everything the tables own, gathered so that one value can be created before
/// the store and outlive every reference the search hands around.
pub struct Records<'a, N: Num> {
    pub choice_states: Arena<ChoiceState<'a, N>>,
    pub histories: Arena<History<'a, N>>,
    pub expansions: Arena<Expansion<'a, N>>,
    pub extensions: Arena<Box<[Extension<'a, N>]>>,
}

impl<'a, N: Num> Records<'a, N> {
    pub fn new() -> Records<'a, N> {
        Records {
            choice_states: Arena::new(),
            histories: Arena::new(),
            expansions: Arena::new(),
            extensions: Arena::new(),
        }
    }
}

impl<'a, N: Num> Default for Records<'a, N> {
    fn default() -> Records<'a, N> {
        Records::new()
    }
}

// ---------------------------------------------------------------------------
// the store
// ---------------------------------------------------------------------------

pub struct Store<'a, N: Num> {
    pub tree: &'a Tree,
    pub engine: Engine<'a>,
    records: &'a Records<'a, N>,
    cs_shards: Box<[Mutex<HashMap<Box<[i8]>, &'a ChoiceState<'a, N>, FxBuild>>]>,
    hist_shards: Box<[Mutex<HashMap<Box<[u32]>, &'a History<'a, N>, FxBuild>>]>,
    next_id: AtomicU64,
    /// Whether the process has a loop: without one the unbounded bounds are
    /// the bounds and are not computed twice.
    has_loops: bool,
    pub cs_made: AtomicUsize,
    pub hist_made: AtomicUsize,
    pub macro_steps: AtomicUsize,
    pub outcomes: AtomicUsize,
}

impl<'a, N: Num> Store<'a, N> {
    pub fn new(tree: &'a Tree, records: &'a Records<'a, N>, shards: usize) -> Store<'a, N> {
        let shards = shards.max(1);
        Store {
            tree,
            engine: Engine::new(tree),
            records,
            cs_shards: (0..shards).map(|_| Mutex::new(HashMap::default())).collect(),
            hist_shards: (0..shards).map(|_| Mutex::new(HashMap::default())).collect(),
            next_id: AtomicU64::new(1),
            has_loops: !tree.loops().is_empty(),
            cs_made: AtomicUsize::new(0),
            hist_made: AtomicUsize::new(0),
            macro_steps: AtomicUsize::new(0),
            outcomes: AtomicUsize::new(0),
        }
    }

    /// The choice state of `state`, created if it is met for the first time.
    /// Its two bounds are computed here, once, whoever gets there first.
    pub fn intern_cs(&self, state: Vec<i8>, stop: Stop) -> &'a ChoiceState<'a, N> {
        let shard = shard_of_state(&state, self.cs_shards.len());
        {
            let map = self.cs_shards[shard].lock().unwrap();
            if let Some(found) = map.get(state.as_slice()) {
                return found;
            }
        }
        // Computed outside the lock: the recursion is a walk of the whole tree,
        // and holding a shard while it runs would stall every other key that
        // hashes there.
        let b = bounds::<N>(self.tree, &state);
        let (upper_inf, lower_inf) = if self.has_loops {
            let i = bounds_with::<N>(self.tree, &state, None);
            (i.upper, i.lower)
        } else {
            (b.upper.clone(), b.lower.clone())
        };
        let record = ChoiceState {
            state: state.clone().into_boxed_slice(),
            stop,
            upper: b.upper.into_boxed_slice(),
            lower: b.lower.into_boxed_slice(),
            upper_inf: upper_inf.into_boxed_slice(),
            lower_inf: lower_inf.into_boxed_slice(),
            low: OnceLock::new(),
            high: OnceLock::new(),
        };
        let mut map = self.cs_shards[shard].lock().unwrap();
        if let Some(found) = map.get(state.as_slice()) {
            return found;
        }
        let placed = self.records.choice_states.alloc(record);
        map.insert(state.into_boxed_slice(), placed);
        self.cs_made.fetch_add(1, Ordering::Relaxed);
        placed
    }

    /// The extensions of a choice state under one action, computed on the first
    /// demand and shared from then on.
    pub fn extensions(
        &self,
        cs: &'a ChoiceState<'a, N>,
        branch: Branch,
    ) -> Result<&'a [Extension<'a, N>], StepError> {
        let cell = match branch {
            Branch::Low => &cs.low,
            Branch::High => &cs.high,
        };
        if let Some(found) = cell.get() {
            return Ok(found);
        }
        let action = cs.action(self.tree, branch);
        let outcomes = self.engine.step_action::<N>(&cs.state, action)?;
        self.macro_steps.fetch_add(1, Ordering::Relaxed);
        self.outcomes.fetch_add(outcomes.len(), Ordering::Relaxed);
        let built: Box<[Extension<'a, N>]> = outcomes
            .into_iter()
            .map(|o| Extension {
                decisions: o.decisions.into_boxed_slice(),
                impact: o.impact.into_boxed_slice(),
                gap: o.gap.into_boxed_slice(),
                stay: o.stay,
                prob: o.prob,
                cs: self.intern_cs(o.state, o.stop),
            })
            .collect();
        let placed: &'a [Extension<'a, N>] = self.records.extensions.alloc(built);
        Ok(cell.get_or_init(|| placed))
    }

    /// The history of a decision sequence, created if it is met for the first
    /// time. Two branches of the computation tree that decided an earlier choice
    /// differently reach the same later history, and interning it is what keeps
    /// its impact, its probability and its two contributions computed once.
    pub fn intern_history(
        &self,
        dh: Vec<u32>,
        impact: Vec<N>,
        gap: Vec<N>,
        stay: N::Prob,
        prob: N::Prob,
        cs: &'a ChoiceState<'a, N>,
    ) -> &'a History<'a, N> {
        let shard = shard_of_dh(&dh, self.hist_shards.len());
        {
            let map = self.hist_shards[shard].lock().unwrap();
            if let Some(found) = map.get(dh.as_slice()) {
                return found;
            }
        }
        let k = self.tree.k;
        let mut e_hat = Vec::with_capacity(k);
        let mut l_hat = Vec::with_capacity(k);
        for c in 0..k {
            e_hat.push(impact[c].add(&cs.upper[c]).scale(&prob));
            l_hat.push(impact[c].add(&cs.lower[c]).scale(&prob));
        }
        let g_hat: Vec<N> = gap.iter().map(|g| g.scale(&prob)).collect();
        let mut d_hat: Vec<N> = Vec::with_capacity(2 * k);
        for c in 0..k {
            d_hat.push(cs.lower_inf[c].scale(&stay).sub(&cs.lower[c]).scale(&prob));
        }
        for c in 0..k {
            d_hat.push(cs.upper_inf[c].scale(&stay).sub(&cs.upper[c]).scale(&prob));
        }
        let record = History {
            id: self.next_id.fetch_add(1, Ordering::Relaxed),
            dh: dh.clone().into_boxed_slice(),
            impact: impact.into_boxed_slice(),
            gap: gap.into_boxed_slice(),
            stay,
            prob,
            cs,
            e_hat: e_hat.into_boxed_slice(),
            l_hat: l_hat.into_boxed_slice(),
            g_hat: g_hat.into_boxed_slice(),
            d_hat: d_hat.into_boxed_slice(),
            low: OnceLock::new(),
            high: OnceLock::new(),
        };
        let mut map = self.hist_shards[shard].lock().unwrap();
        if let Some(found) = map.get(dh.as_slice()) {
            return found;
        }
        let placed = self.records.histories.alloc(record);
        map.insert(dh.into_boxed_slice(), placed);
        self.hist_made.fetch_add(1, Ordering::Relaxed);
        placed
    }

    /// What one action does to one history. Computed on the first demand and
    /// shared from then on, which is what keeps the inner loop of the search
    /// free of the work of rebuilding a decision sequence, hashing it and
    /// looking it up once per expansion rather than once per history.
    pub fn children(
        &self,
        elected: &'a History<'a, N>,
        branch: Branch,
    ) -> Result<&'a Expansion<'a, N>, StepError> {
        let cell = match branch {
            Branch::Low => &elected.low,
            Branch::High => &elected.high,
        };
        if let Some(found) = cell.get() {
            return Ok(found);
        }
        let k = self.tree.k;
        let exts = self.extensions(elected.cs, branch)?;
        let mut open_children: Vec<&'a History<'a, N>> = Vec::with_capacity(exts.len());
        let mut closed_delta = zeros::<N>(k);
        let mut open_e_delta = zeros::<N>(k);
        let mut open_l_delta = zeros::<N>(k);
        let mut gap_delta = zeros::<N>(2 * k);
        let mut open_d_delta = zeros::<N>(2 * k);
        for ext in exts.iter() {
            let mut dh = Vec::with_capacity(elected.dh.len() + ext.decisions.len());
            dh.extend_from_slice(&elected.dh);
            dh.extend_from_slice(&ext.decisions);
            let mut impact = Vec::with_capacity(k);
            for c in 0..k {
                impact.push(elected.impact[c].add(&ext.impact[c]));
            }
            // the gap of the path. With S the probability of having stayed
            // within the bound up to the elected history, G the gap there,
            // g, s and i the gap, the stay and the impact of the macro step:
            // G' = G + S g - (1 - S) i, the macro step counting for what the
            // run had not left yet, and its impact being no cost of the runs
            // that had; S' = S s
            let gone = N::prob_complement(&elected.stay);
            let gap: Vec<N> = (0..2 * k)
                .map(|c| {
                    elected_gap(elected, c)
                        .add(&ext.gap[c].scale(&elected.stay))
                        .sub(&ext.impact[c % k].scale(&gone))
                })
                .collect();
            let stay = N::prob_mul(&elected.stay, &ext.stay);
            let h = self.intern_history(dh, impact, gap, stay, N::prob_mul(&elected.prob, &ext.prob), ext.cs);
            for c in 0..2 * k {
                gap_delta[c] = gap_delta[c].add(&h.g_hat[c]);
            }
            if h.is_open() {
                for c in 0..k {
                    open_e_delta[c] = open_e_delta[c].add(&h.e_hat[c]);
                    open_l_delta[c] = open_l_delta[c].add(&h.l_hat[c]);
                }
                for c in 0..2 * k {
                    open_d_delta[c] = open_d_delta[c].add(&h.d_hat[c]);
                }
                open_children.push(h);
            } else {
                for c in 0..k {
                    closed_delta[c] = closed_delta[c].add(&h.e_hat[c]);
                }
            }
        }
        let built = Expansion {
            closed_count: exts.len() - open_children.len(),
            open_children: open_children.into_boxed_slice(),
            closed_delta: closed_delta.into_boxed_slice(),
            open_e_delta: open_e_delta.into_boxed_slice(),
            open_l_delta: open_l_delta.into_boxed_slice(),
            gap_delta: gap_delta.into_boxed_slice(),
            open_d_delta: open_d_delta.into_boxed_slice(),
        };
        let placed = self.records.expansions.alloc(built);
        Ok(cell.get_or_init(|| placed))
    }

    /// The histories reachable from the initial state without deciding
    /// anything: the label of the root of the computation tree.
    pub fn initial_histories(&self) -> Result<Vec<&'a History<'a, N>>, StepError> {
        let outcomes = self.engine.initial_outcomes::<N>()?;
        self.macro_steps.fetch_add(1, Ordering::Relaxed);
        self.outcomes.fetch_add(outcomes.len(), Ordering::Relaxed);
        Ok(outcomes
            .into_iter()
            .map(|o| {
                let cs = self.intern_cs(o.state, o.stop);
                self.intern_history(o.decisions, o.impact, o.gap, o.stay, o.prob, cs)
            })
            .collect())
    }
}
