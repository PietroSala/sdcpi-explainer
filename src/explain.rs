//! The explainer: a strategy read as one decision tree per choice node, on
//! the draws of nature alone, and beside it on the impacts paid so far.
//!
//! A strategy is a deterministic policy, and at a choice it decides by a
//! function of the draws that precede, its own earlier decisions being
//! determined by those draws (Theorem "A deterministic policy decides by the
//! draws" of parallel-paper/semantics-time.tex of the paper repository). An
//! explanation therefore reads the draws and nothing else.
//!
//! **A draw is named by its passes.** With loops a draw is taken at one node
//! once per pass, and a test names the draw it speaks of by the passes it is
//! taken in: for every loop above its node, the outermost first, the pass of
//! that loop, and for the draw of a loop the pass of its own region that the
//! draw follows. The index is nested as the loops are, so that a draw keeps
//! its name whatever the other passes did; a count of the draws at a node
//! along the run would not, a pass more of an inner loop moving the count of
//! every later draw. No two draws of a history have the same node and the
//! same passes, and two different histories at which a strategy meets the
//! same choice differ in what one such draw gave, or in whether it was
//! taken: the data of a choice are always separable.
//!
//! **A forced exit is no draw.** The exit a loop is forced to at the bound
//! on its passes is taken by every run, tells nothing apart, is not written
//! in the trail, and no test can speak of it: the draw after the pass `j`
//! of a loop exists for `j` below the bound only.
//!
//! **A test is one statement.** "In pass 3 of the loop L and pass 2 of the
//! loop L', the draw between A and B took A": true when that draw was taken
//! and gave that answer, false in every other case, when it gave the other
//! answer and when it was not taken at all, there being no such pass or the
//! run not reaching the node in it. A test has two branches, and a tree is
//! a nest of "if this, otherwise".
//!
//! For every choice node the histories at which the strategy decides it are
//! the rows, the draws their features, the child taken their label, and the
//! tree returned separates them with the fewest tests, and among the trees
//! with the fewest tests the least depth. The search for it is exact, a
//! memoised recursion on the sets of rows, as long as it stays within a
//! budget of sets; past the budget it goes on greedily and the explanation
//! says that it is not known to be minimal.
//!
//! **The impacts in place of the draws.** The journal paper gives a second
//! explainer, the current one, which reads at a choice the impacts paid so
//! far, the time in front when it is in: the cost of the history, what the
//! fast forwards paid up to the state the choice is due at. A decision
//! function of that vector exists exactly when no two histories at which
//! the strategy decides the choice pay the same and take different regions,
//! and then a tree of tests "this component is at most this value"
//! separates them, two different vectors differing in some component. Such
//! a tree is not always there, and when it is, it is given in two ways: the
//! tree with the fewest tests on every component, which may read more
//! components than it needs to be a function; and one tree per minimal set
//! of components on which a function exists, the least elements of the
//! lattice of the sets that suffice, by size, each with the fewest tests on
//! that set, a tree on a minimal set reading every component of it. The
//! sets that suffice are an upper set of the lattice of the subsets of the
//! components, known from their minimal elements. With the time in, every
//! reading is given with the time among the components and with it left
//! out; the minimal sets without the time are the minimal sets with it
//! that do not hold it, and the time alone may suffice, in which case it
//! is the one minimal set that holds it.

use std::collections::{BTreeMap, HashMap, HashSet};

use num_rational::BigRational;

use crate::num::{decimal, parse_decimal, Exact};
use crate::state::{Engine, Entry};
use crate::tree::{Kind, Tree};

/// One draw, as a test of an explanation names it: its node, a nature node
/// or a loop, and the passes it is taken in.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Feature {
    pub node: u32,
    /// For every loop above the node, the outermost first, the pass of that
    /// loop the draw is taken in, counted from 1; and at a loop, last, the
    /// pass of its own region that the draw follows, which is below the
    /// bound, the exit at the bound being forced and no draw.
    pub passes: Vec<u32>,
}

/// What a draw gave, or that it was not taken before the choice.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Value {
    /// The draw was not taken before the choice. No test states it.
    Absent,
    /// A nature node drew this child.
    Child(u32),
    /// A loop ran its region again.
    Again,
    /// A loop was left.
    Out,
}

/// A decision tree on the draws: a leaf takes a child of the choice, a test
/// states that one draw gave one answer, and has the tree to follow when it
/// did and the tree to follow otherwise, the draw having given the other
/// answer or not having been taken.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Node {
    Leaf(u32),
    Test { feature: Feature, value: Value, yes: Box<Node>, no: Box<Node> },
}

/// Whether a reading of the impacts has the time among its components:
/// `None` when the time is not in the cost, `In` when it is and the reading
/// may read it, `Out` when it is and the reading leaves it out.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Clock {
    None,
    In,
    Out,
}

impl Clock {
    pub fn word(self) -> &'static str {
        match self {
            Clock::None => "none",
            Clock::In => "in",
            Clock::Out => "out",
        }
    }
}

/// A test on the impacts paid so far: one component and one value, the
/// statement "the component is at most this value". The engine carries a
/// maximized component negated, so the statement is shown there as at
/// least the value with its sign turned back.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Cut {
    pub component: usize,
    pub at_most: BigRational,
}

/// A decision tree on the impacts: a leaf takes a child of the choice, a
/// test states that one component paid so far is at most one value.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ImpactNode {
    Leaf(u32),
    Test { cut: Cut, yes: Box<ImpactNode>, no: Box<ImpactNode> },
}

/// A tree on the impacts, with the components it reads.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ImpactTree {
    /// The components the tests of the tree read, in increasing order.
    pub components: Vec<usize>,
    pub tests: usize,
    pub depth: usize,
    /// Whether the tree is known to have the fewest tests on the components
    /// it was searched on.
    pub minimal: bool,
    pub root: ImpactNode,
}

/// The impacts read in place of the draws at one choice node, under one
/// availability of the time.
#[derive(Clone, Debug)]
pub struct ImpactReading {
    pub clock: Clock,
    /// The components the reading may use, in increasing order.
    pub available: Vec<usize>,
    /// Whether a function of the impacts exists: no two histories at which
    /// the strategy decides the choice pay the same on the available
    /// components and take different regions.
    pub separable: bool,
    /// When no function exists, two decisions, each a trail and the child
    /// taken, that pay the same and take different regions.
    pub clash: Option<((Vec<u32>, u32), (Vec<u32>, u32))>,
    /// The tree with the fewest tests on the available components.
    pub minimum: Option<ImpactTree>,
    /// The minimal sets of components on which a function exists, by size
    /// and then by their components, each with the tree with the fewest
    /// tests on it.
    pub sets: Vec<ImpactTree>,
    /// Whether every minimal set is listed: past `SETS_EXHAUSTIVE`
    /// available components one minimal set is found and the others are
    /// not looked for.
    pub sets_complete: bool,
}

/// The explanation of one choice node of a strategy.
#[derive(Clone, Debug)]
pub struct Explanation {
    /// The choice node.
    pub choice: u32,
    /// The histories at which the strategy decides it.
    pub histories: usize,
    /// The tests of the tree on the draws, its internal nodes.
    pub tests: usize,
    /// The most tests on a path of the tree on the draws.
    pub depth: usize,
    /// Whether the tree on the draws is known to have the fewest tests: the
    /// exact search ended within its budget.
    pub minimal: bool,
    /// The tree on the draws.
    pub root: Node,
    /// The impacts paid so far read in place of the draws: one reading when
    /// the time is not in the cost, and with the time in, the reading with
    /// the time first and the one with the time left out.
    pub impacts: Vec<ImpactReading>,
}

/// The sets of rows the exact search may solve before it goes on greedily.
const BUDGET: usize = 200_000;

/// The most available components the minimal sets are enumerated on in
/// full, every subset being tried from the smallest.
const SETS_EXHAUSTIVE: usize = 12;

/// One history at which the strategy decides a choice: its draws by their
/// names, the cost paid up to the choice, the child taken and the trail.
struct Row {
    draws: Vec<(Feature, Value)>,
    cost: Vec<BigRational>,
    action: u32,
    trail: Vec<u32>,
}

/// The explanation of a strategy, one tree per choice node it decides, in
/// the order of the identifiers. The decisions are the ones the search
/// records, a trail and the child taken after it.
pub fn explain(tree: &Tree, decisions: &[(Vec<u32>, u32)]) -> Result<Vec<Explanation>, String> {
    let engine = Engine::new(tree);
    // per choice node, the rows: the draws as (feature, value) in the order
    // they were taken, the cost at the choice, and the child the strategy
    // starts
    let mut by_choice: BTreeMap<u32, Vec<Row>> = BTreeMap::new();
    for (dh, action) in decisions {
        let (entries, passes, due, cost) = engine.replay::<Exact>(dh)?;
        let v = due.ok_or_else(|| "a decision is recorded at a history that ends the run".to_string())?;
        let n = tree.node(v);
        if *action != n.low && *action != n.high {
            return Err(format!("the decision {action} is no child of the choice {v} it is recorded at"));
        }
        let mut seen: HashSet<Feature> = HashSet::new();
        let mut row = Vec::new();
        for (e, index) in entries.iter().zip(passes) {
            let (u, value) = match *e {
                Entry::Decision(..) => continue,
                Entry::Nature(u, c) => (u, Value::Child(c)),
                Entry::Again(u) => (u, Value::Again),
                Entry::Out(u) => (u, Value::Out),
            };
            let feature = Feature { node: u, passes: index };
            // a node is drawn once in given passes of the loops above it
            if !seen.insert(feature.clone()) {
                return Err(format!(
                    "the node {u} is drawn twice in the passes {:?}: the draws of a history are not told apart by their passes",
                    feature.passes
                ));
            }
            row.push((feature, value));
        }
        by_choice.entry(v).or_default().push(Row {
            draws: row,
            cost: cost.into_iter().map(|x| x.0).collect(),
            action: *action,
            trail: dh.clone(),
        });
    }
    let mut out = Vec::with_capacity(by_choice.len());
    for (v, rows) in by_choice {
        let (tests, depth, minimal, root) = learn_draws(v, &rows)?;
        let impacts = learn_impacts(tree, &rows);
        out.push(Explanation { choice: v, histories: rows.len(), tests, depth, minimal, root, impacts });
    }
    Ok(out)
}

// -- the search for a tree, on statements of any kind -----------------------

/// The tree the search returns: a leaf with the child it takes, or a test
/// by the index of its statement, with the tree to follow when the
/// statement holds and the tree to follow when it does not.
#[derive(Clone, Debug)]
enum Dt {
    Leaf(u32),
    Test(usize, Box<Dt>, Box<Dt>),
}

/// The rows of one choice node and what a test may state of them: for
/// every statement, of which rows it holds. The search reads the
/// statements by their index and nothing else, so one search serves the
/// draws and the impacts alike.
struct Learner<'a> {
    labels: &'a [u32],
    holds: &'a [Vec<bool>],
    /// A set of rows, sorted, to the tests and the depth of its tree and the
    /// statement at its root, `usize::MAX` at a leaf.
    memo: HashMap<Vec<u32>, (u32, u32, usize)>,
    /// False once the budget is passed: the search is greedy from there.
    exact: bool,
}

impl Learner<'_> {
    /// The rows of `set` of which the statement `p` holds, and the others.
    fn split(&self, set: &[u32], p: usize) -> (Vec<u32>, Vec<u32>) {
        set.iter().partition(|&&r| self.holds[p][r as usize])
    }

    /// How many rows of a set do not carry its commonest label: zero on a
    /// set a leaf decides.
    fn impurity(&self, set: &[u32]) -> usize {
        let mut count: HashMap<u32, usize> = HashMap::new();
        for &r in set {
            *count.entry(self.labels[r as usize]).or_insert(0) += 1;
        }
        set.len() - count.values().copied().max().unwrap_or(0)
    }

    /// The fewest tests, and among those the least depth, of a tree that
    /// separates `set`, `u32::MAX` when no statement separates it; the
    /// statement chosen stays in the memo.
    fn solve(&mut self, set: Vec<u32>) -> (u32, u32) {
        if let Some(&(t, d, _)) = self.memo.get(&set) {
            return (t, d);
        }
        if self.impurity(&set) == 0 {
            self.memo.insert(set, (0, 0, usize::MAX));
            return (0, 0);
        }
        // the statements that part the set, the most promising first: the
        // one that leaves the fewest rows on the wrong side of a leaf. Two
        // statements that part it alike are one candidate.
        let mut candidates: Vec<(usize, usize, Vec<u32>, Vec<u32>)> = Vec::new();
        let mut parted: HashSet<Vec<u32>> = HashSet::new();
        for p in 0..self.holds.len() {
            let (yes, no) = self.split(&set, p);
            if yes.is_empty() || no.is_empty() {
                continue;
            }
            let side = if yes[0] == set[0] { yes.clone() } else { no.clone() };
            if parted.insert(side) {
                let score = self.impurity(&yes) + self.impurity(&no);
                candidates.push((score, p, yes, no));
            }
        }
        candidates.sort_by(|a, b| (a.0, a.1).cmp(&(b.0, b.1)));
        if self.memo.len() > BUDGET {
            self.exact = false;
        }
        let mut best: (u32, u32, usize) = (u32::MAX, u32::MAX, usize::MAX);
        for (_, p, yes, no) in candidates {
            // a side that no leaf decides costs one test at least
            let owed = (self.impurity(&no) > 0) as u32;
            let (t1, d1) = self.solve(yes);
            if t1 == u32::MAX || 1 + t1 + owed > best.0 {
                continue;
            }
            let (t2, d2) = self.solve(no);
            if t2 == u32::MAX {
                continue;
            }
            let found = (1 + t1 + t2, 1 + d1.max(d2));
            if found < (best.0, best.1) {
                best = (found.0, found.1, p);
            }
            if !self.exact && best.0 != u32::MAX {
                // past the budget the first statement that works is kept
                break;
            }
        }
        self.memo.insert(set, best);
        (best.0, best.1)
    }

    fn build(&self, set: &[u32]) -> Dt {
        let (_, _, p) = self.memo[set];
        if p == usize::MAX {
            return Dt::Leaf(self.labels[set[0] as usize]);
        }
        let (yes, no) = self.split(set, p);
        Dt::Test(p, Box::new(self.build(&yes)), Box::new(self.build(&no)))
    }
}

/// The tree with the fewest tests, and among those the least depth, on
/// these statements: the tree, its tests, its depth and whether the search
/// stayed within its budget; `None` when no tree on the statements
/// separates the rows.
fn fewest(labels: &[u32], holds: &[Vec<bool>]) -> Option<(Dt, usize, usize, bool)> {
    let mut learner = Learner { labels, holds, memo: HashMap::new(), exact: true };
    let all: Vec<u32> = (0..labels.len() as u32).collect();
    let (tests, depth) = learner.solve(all.clone());
    if tests == u32::MAX {
        return None;
    }
    let root = learner.build(&all);
    Some((root, tests as usize, depth as usize, learner.exact))
}

// -- the draws ---------------------------------------------------------------

/// The tree on the draws of one choice node from its rows: its tests, its
/// depth, whether it is known to be the fewest, and the tree.
fn learn_draws(choice: u32, rows: &[Row]) -> Result<(usize, usize, bool, Node), String> {
    // the draws in the order they are first met, a row after the other
    let mut index: HashMap<Feature, usize> = HashMap::new();
    let mut features: Vec<Feature> = Vec::new();
    for row in rows {
        for (f, _) in &row.draws {
            if !index.contains_key(f) {
                index.insert(f.clone(), features.len());
                features.push(f.clone());
            }
        }
    }
    let values: Vec<Vec<Value>> = rows
        .iter()
        .map(|row| {
            let mut v = vec![Value::Absent; features.len()];
            for (f, x) in &row.draws {
                v[index[f]] = *x;
            }
            v
        })
        .collect();
    let labels: Vec<u32> = rows.iter().map(|r| r.action).collect();
    // two rows with the same draws and two decisions: no function of the
    // draws exists, which the theory excludes of a deterministic policy
    let mut seen: HashMap<&Vec<Value>, u32> = HashMap::new();
    for (v, a) in values.iter().zip(&labels) {
        if let Some(b) = seen.insert(v, *a) {
            if b != *a {
                return Err(format!(
                    "the strategy takes {b} and {a} at the choice {choice} after the same draws: it is not a function of the draws"
                ));
            }
        }
    }
    // what a test may state: a draw, and an answer it gave in some row
    let mut statements: Vec<(usize, Value)> = Vec::new();
    for f in 0..features.len() {
        let mut met: Vec<Value> = values.iter().map(|v| v[f]).filter(|x| *x != Value::Absent).collect();
        met.sort();
        met.dedup();
        statements.extend(met.into_iter().map(|x| (f, x)));
    }
    let holds: Vec<Vec<bool>> = statements.iter().map(|&(f, x)| values.iter().map(|v| v[f] == x).collect()).collect();
    let (root, tests, depth, minimal) = fewest(&labels, &holds)
        .ok_or_else(|| format!("the histories of the choice {choice} are not told apart by their draws"))?;
    Ok((tests, depth, minimal, draws_node(&root, &statements, &features)))
}

fn draws_node(dt: &Dt, statements: &[(usize, Value)], features: &[Feature]) -> Node {
    match dt {
        Dt::Leaf(c) => Node::Leaf(*c),
        Dt::Test(p, yes, no) => {
            let (f, value) = statements[*p];
            Node::Test {
                feature: features[f].clone(),
                value,
                yes: Box::new(draws_node(yes, statements, features)),
                no: Box::new(draws_node(no, statements, features)),
            }
        }
    }
}

// -- the impacts -------------------------------------------------------------

/// The readings of the impacts at one choice node: with the time in the
/// cost, the reading that may read it and the one that leaves it out;
/// otherwise the one reading on the impacts.
fn learn_impacts(tree: &Tree, rows: &[Row]) -> Vec<ImpactReading> {
    let k = tree.k;
    if tree.time {
        vec![reading(rows, Clock::In, (0..k).collect()), reading(rows, Clock::Out, (1..k).collect())]
    } else {
        vec![reading(rows, Clock::None, (0..k).collect())]
    }
}

/// The cost of a row on these components.
fn projection<'a>(cost: &'a [BigRational], comps: &[usize]) -> Vec<&'a BigRational> {
    comps.iter().map(|&c| &cost[c]).collect()
}

/// Two rows that pay the same on these components and take different
/// regions, if there are two.
fn clash(rows: &[Row], comps: &[usize]) -> Option<(usize, usize)> {
    let mut order: Vec<usize> = (0..rows.len()).collect();
    order.sort_by(|&a, &b| {
        projection(&rows[a].cost, comps)
            .cmp(&projection(&rows[b].cost, comps))
            .then(rows[a].action.cmp(&rows[b].action))
    });
    for w in order.windows(2) {
        let (a, b) = (w[0], w[1]);
        if rows[a].action != rows[b].action && projection(&rows[a].cost, comps) == projection(&rows[b].cost, comps) {
            // the earlier decision first
            return Some((a.min(b), a.max(b)));
        }
    }
    None
}

/// Whether the decision is a function of the cost on these components: no
/// two rows pay the same on them and take different regions.
fn separable(costs: &[Vec<BigRational>], labels: &[u32], comps: &[usize]) -> bool {
    let mut order: Vec<usize> = (0..costs.len()).collect();
    order.sort_by(|&a, &b| projection(&costs[a], comps).cmp(&projection(&costs[b], comps)).then(labels[a].cmp(&labels[b])));
    order.windows(2).all(|w| labels[w[0]] == labels[w[1]] || projection(&costs[w[0]], comps) != projection(&costs[w[1]], comps))
}

/// The impacts read at one choice node on these components.
fn reading(rows: &[Row], clock: Clock, available: Vec<usize>) -> ImpactReading {
    if let Some((a, b)) = clash(rows, &available) {
        return ImpactReading {
            clock,
            available,
            separable: false,
            clash: Some(((rows[a].trail.clone(), rows[a].action), (rows[b].trail.clone(), rows[b].action))),
            minimum: None,
            sets: Vec::new(),
            sets_complete: true,
        };
    }
    // one row per distinct cost on the available components: the rows that
    // pay the same take the same region
    let mut order: Vec<usize> = (0..rows.len()).collect();
    order.sort_by(|&a, &b| projection(&rows[a].cost, &available).cmp(&projection(&rows[b].cost, &available)));
    order.dedup_by(|a, b| projection(&rows[*a].cost, &available) == projection(&rows[*b].cost, &available));
    let costs: Vec<Vec<BigRational>> = order.iter().map(|&r| rows[r].cost.clone()).collect();
    let labels: Vec<u32> = order.iter().map(|&r| rows[r].action).collect();
    let minimum = tree_on(&costs, &labels, &available);
    let (sets, sets_complete) = minimal_sets(&costs, &labels, &available);
    let sets: Vec<ImpactTree> = sets.iter().filter_map(|s| tree_on(&costs, &labels, s)).collect();
    ImpactReading { clock, available, separable: true, clash: None, minimum, sets, sets_complete }
}

/// The tree with the fewest tests on these components: the statements are
/// the cuts of every component at each of its values but the greatest,
/// "at most this value".
fn tree_on(costs: &[Vec<BigRational>], labels: &[u32], comps: &[usize]) -> Option<ImpactTree> {
    let mut cuts: Vec<Cut> = Vec::new();
    for &c in comps {
        let mut vals: Vec<&BigRational> = costs.iter().map(|v| &v[c]).collect();
        vals.sort();
        vals.dedup();
        for v in vals.iter().take(vals.len().saturating_sub(1)) {
            cuts.push(Cut { component: c, at_most: (*v).clone() });
        }
    }
    let holds: Vec<Vec<bool>> = cuts.iter().map(|q| costs.iter().map(|v| v[q.component] <= q.at_most).collect()).collect();
    let (root, tests, depth, minimal) = fewest(labels, &holds)?;
    let mut components: Vec<usize> = Vec::new();
    let root = impact_node(&root, &cuts, &mut components);
    components.sort_unstable();
    components.dedup();
    Some(ImpactTree { components, tests, depth, minimal, root })
}

fn impact_node(dt: &Dt, cuts: &[Cut], components: &mut Vec<usize>) -> ImpactNode {
    match dt {
        Dt::Leaf(c) => ImpactNode::Leaf(*c),
        Dt::Test(p, yes, no) => {
            components.push(cuts[*p].component);
            ImpactNode::Test {
                cut: cuts[*p].clone(),
                yes: Box::new(impact_node(yes, cuts, components)),
                no: Box::new(impact_node(no, cuts, components)),
            }
        }
    }
}

/// The subsets of `0..n` of size `m`, in lexicographic order.
fn combinations(n: usize, m: usize) -> Vec<Vec<usize>> {
    let mut out = Vec::new();
    let mut c: Vec<usize> = (0..m).collect();
    if m > n {
        return out;
    }
    loop {
        out.push(c.clone());
        // the rightmost index that can still move
        let mut i = m;
        while i > 0 && c[i - 1] == n - m + i - 1 {
            i -= 1;
        }
        if i == 0 {
            return out;
        }
        c[i - 1] += 1;
        for j in i..m {
            c[j] = c[j - 1] + 1;
        }
    }
}

/// The minimal sets of components on which the decision is a function of
/// the cost, by size and then by their components, and whether they are
/// all there. A set that suffices keeps sufficing when a component is
/// added, so the sets are tried from the smallest, and a set that contains
/// one found is not minimal and is not tried. Past `SETS_EXHAUSTIVE`
/// components one minimal set is found by taking the components away one
/// at a time, and the rest are not looked for.
fn minimal_sets(costs: &[Vec<BigRational>], labels: &[u32], available: &[usize]) -> (Vec<Vec<usize>>, bool) {
    let n = available.len();
    if n > SETS_EXHAUSTIVE {
        let mut set: Vec<usize> = available.to_vec();
        let mut i = set.len();
        while i > 0 {
            i -= 1;
            let mut fewer = set.clone();
            fewer.remove(i);
            if separable(costs, labels, &fewer) {
                set = fewer;
            }
        }
        return (vec![set], false);
    }
    let mut found: Vec<Vec<usize>> = Vec::new();
    for size in 0..=n {
        for combo in combinations(n, size) {
            let set: Vec<usize> = combo.iter().map(|&i| available[i]).collect();
            if found.iter().any(|m| m.iter().all(|c| set.contains(c))) {
                continue;
            }
            if separable(costs, labels, &set) {
                found.push(set);
            }
        }
    }
    (found, true)
}

// -- the text ----------------------------------------------------------------

/// The name of a component as the page and the reports show it.
pub fn impact_name(tree: &Tree, c: usize) -> String {
    match tree.meta.impact_names.get(c) {
        Some(n) if !n.is_empty() => n.clone(),
        _ => format!("impact {c}"),
    }
}

/// A rational for a sentence: its decimal when that is exact, the fraction
/// otherwise.
fn show_rational(r: &BigRational) -> String {
    let d = decimal(r, 12);
    if parse_decimal(&d).as_ref() == Some(r) {
        d
    } else {
        r.to_string()
    }
}

/// The value a cut compares with, as it is shown, and the relation: the
/// sign turned back and the relation turned round on a maximized component.
fn cut_shown(tree: &Tree, cut: &Cut) -> (BigRational, &'static str) {
    if tree.maximized.get(cut.component).copied().unwrap_or(false) {
        (-cut.at_most.clone(), "at least")
    } else {
        (cut.at_most.clone(), "at most")
    }
}

/// What a test on the impacts states, as a sentence.
fn cut_text(tree: &Tree, cut: &Cut) -> String {
    let (value, relation) = cut_shown(tree, cut);
    let v = show_rational(&value);
    if tree.time && cut.component == 0 {
        format!("the time elapsed is {relation} {v}")
    } else if relation == "at least" {
        format!("the {} so far is {relation} {v}", impact_name(tree, cut.component))
    } else {
        format!("the {} paid so far is {relation} {v}", impact_name(tree, cut.component))
    }
}

/// Names joined as a sentence lists them: "a", "a and b", "a, b and c".
fn joined(names: &[String]) -> String {
    match names.len() {
        0 => "no impact".to_string(),
        1 => names[0].clone(),
        n => format!("{} and {}", names[..n - 1].join(", "), names[n - 1]),
    }
}

fn component_names(tree: &Tree, comps: &[usize]) -> String {
    let names: Vec<String> = comps.iter().map(|&c| impact_name(tree, c)).collect();
    joined(&names)
}

/// Where a draw is taken, for the loops above its node: "in pass 2 of the
/// loop 9, pass 1 of the loop 4, ", empty under no loop.
fn passes_text(above: &[u32], passes: &[u32]) -> String {
    if above.is_empty() {
        return String::new();
    }
    let parts: Vec<String> = above.iter().zip(passes).map(|(l, p)| format!("pass {p} of the loop {l}")).collect();
    format!("in {}, ", parts.join(", "))
}

/// What a test states, as a sentence: the draw by its passes, and the
/// answer.
fn statement_text(tree: &Tree, above: &[Vec<u32>], feature: &Feature, value: Value, name: &dyn Fn(u32) -> String) -> String {
    let d = tree.node(feature.node);
    let place = passes_text(&above[feature.node as usize], &feature.passes);
    match value {
        Value::Child(c) => format!(
            "{place}the draw at the nature node {} ({} or {}) took {}",
            feature.node,
            name(d.low),
            name(d.high),
            name(c)
        ),
        Value::Again | Value::Out => {
            // the pass of the loop itself is the last of the index; the
            // name of a region is the one of its first task
            let own = feature.passes.last().copied().unwrap_or(0);
            let which = if tree.kind(d.low) == Kind::Task {
                format!("the loop {} over {}", feature.node, name(d.low))
            } else {
                format!("the loop {}, which starts with {},", feature.node, name(d.low))
            };
            let what = if value == Value::Again { "ran again" } else { "was left" };
            format!("{place}{which} {what} after its pass {own}")
        }
        Value::Absent => "the draw was not taken".to_string(),
    }
}

fn count_text(tests: usize, minimal: bool) -> String {
    format!(
        "{} {}{}",
        tests,
        if tests == 1 { "test" } else { "tests" },
        if minimal { ", the fewest" } else { ", not known to be the fewest" }
    )
}

/// The explanation as lines of text, the names of the nodes given by `name`:
/// for every choice node the tree on the draws, then the trees on the
/// impacts, the fewest tests on every component with the time and with it
/// left out, and one per minimal set of components. A choice decided the
/// same way at every history is one leaf, and the impacts add nothing.
pub fn render_text(tree: &Tree, explanations: &[Explanation], name: &dyn Fn(u32) -> String) -> String {
    let above = tree.loops_above();
    let mut out = String::new();
    for e in explanations {
        let n = tree.node(e.choice);
        out.push_str(&format!(
            "choice {} ({} or {}): {} {}, {}\n",
            e.choice,
            name(n.low),
            name(n.high),
            e.histories,
            if e.histories == 1 { "history" } else { "histories" },
            count_text(e.tests, e.minimal)
        ));
        text_node(tree, &above, &e.root, 1, name, &mut out);
        if e.tests == 0 {
            continue;
        }
        // the options of the page, flat: the fewest tests on every
        // component, the same with the time left out, then the minimal
        // sets, those the two trees read left out
        let first = &e.impacts[0];
        let out_reading = e.impacts.get(1);
        let mut read: Vec<&Vec<usize>> = Vec::new();
        match &first.minimum {
            None => out.push_str(&none_text(first, name)),
            Some(m) => {
                read.push(&m.components);
                out.push_str(&format!(
                    "  by the impacts: {}, reading {}\n",
                    count_text(m.tests, m.minimal),
                    component_names(tree, &m.components)
                ));
                impact_text_node(tree, &m.root, 2, name, &mut out);
            }
        }
        if let Some(r) = out_reading {
            match &r.minimum {
                None => out.push_str(&none_text(r, name)),
                Some(m) => {
                    let same = first.minimum.as_ref().map_or(false, |f| f.components == m.components && f.tests == m.tests);
                    if same {
                        out.push_str("  by the impacts, the time left out: the same\n");
                    } else {
                        read.push(&m.components);
                        out.push_str(&format!(
                            "  by the impacts, the time left out: {}, reading {}\n",
                            count_text(m.tests, m.minimal),
                            component_names(tree, &m.components)
                        ));
                        impact_text_node(tree, &m.root, 2, name, &mut out);
                    }
                }
            }
        }
        // the minimal sets without the time are among those with it: one
        // list, the sets with the time first
        let with_time = |s: &ImpactTree| first.clock == Clock::In && s.components.contains(&0);
        // unless the reading with the time was cut past the exhaustive
        // enumeration, when the sets of the reading without it, complete,
        // are added
        let mut pool: Vec<&ImpactTree> = first.sets.iter().collect();
        if let Some(r) = out_reading {
            if !first.sets_complete && r.sets_complete {
                pool.extend(r.sets.iter().filter(|s| !first.sets.iter().any(|t| t.components == s.components)));
            }
        }
        let listed: Vec<&ImpactTree> = pool.into_iter().filter(|s| !read.contains(&&s.components)).collect();
        for s in listed.iter().filter(|s| with_time(s)).chain(listed.iter().filter(|s| !with_time(s))) {
            if with_time(s) && s.components.len() == 1 {
                out.push_str(&format!("  by the time alone: {}\n", count_text(s.tests, s.minimal)));
            } else {
                out.push_str(&format!(
                    "  by the fewest impacts{}, {}: {}\n",
                    if with_time(s) { " with the time" } else { "" },
                    component_names(tree, &s.components),
                    count_text(s.tests, s.minimal)
                ));
            }
            impact_text_node(tree, &s.root, 2, name, &mut out);
        }
        if !first.sets_complete {
            // the one set found may be the one a tree above reads, and then
            // it is listed nowhere else
            out.push_str(&format!(
                "  by the fewest impacts: one minimal set found{}, the others not looked for past {SETS_EXHAUSTIVE} components\n",
                if listed.is_empty() { ", read by a tree above" } else { "" }
            ));
        }
    }
    out
}

/// The line of a reading with no tree: the two histories that pay the
/// same and take different regions.
fn none_text(r: &ImpactReading, name: &dyn Fn(u32) -> String) -> String {
    let suffix = if r.clock == Clock::Out { ", the time left out" } else { "" };
    match &r.clash {
        Some(((ta, aa), (tb, ab))) => format!(
            "  by the impacts{suffix}: none, two histories pay the same and take different regions: {} take {}; {} take {}\n",
            trail_text(ta, name),
            name(*aa),
            trail_text(tb, name),
            name(*ab)
        ),
        // a function exists and no tree was found: the search does not
        // come here, every separable set of rows being parted by a cut
        None => format!("  by the impacts{suffix}: none\n"),
    }
}

/// A trail in a sentence: the nodes it writes, by their names.
fn trail_text(trail: &[u32], name: &dyn Fn(u32) -> String) -> String {
    if trail.is_empty() {
        "at the start".to_string()
    } else {
        let parts: Vec<String> = trail.iter().map(|&id| name(id)).collect();
        format!("after {}", parts.join(", "))
    }
}

fn text_node(tree: &Tree, above: &[Vec<u32>], node: &Node, indent: usize, name: &dyn Fn(u32) -> String, out: &mut String) {
    let pad = "  ".repeat(indent);
    match node {
        Node::Leaf(c) => out.push_str(&format!("{pad}take {}\n", name(*c))),
        Node::Test { feature, value, yes, no } => {
            let statement = statement_text(tree, above, feature, *value, name);
            for (head, child) in [(format!("if {statement}"), yes), ("otherwise".to_string(), no)] {
                match child.as_ref() {
                    Node::Leaf(c) => out.push_str(&format!("{pad}{head}: take {}\n", name(*c))),
                    _ => {
                        out.push_str(&format!("{pad}{head}:\n"));
                        text_node(tree, above, child, indent + 1, name, out);
                    }
                }
            }
        }
    }
}

fn impact_text_node(tree: &Tree, node: &ImpactNode, indent: usize, name: &dyn Fn(u32) -> String, out: &mut String) {
    let pad = "  ".repeat(indent);
    match node {
        ImpactNode::Leaf(c) => out.push_str(&format!("{pad}take {}\n", name(*c))),
        ImpactNode::Test { cut, yes, no } => {
            let statement = cut_text(tree, cut);
            for (head, child) in [(format!("if {statement}"), yes), ("otherwise".to_string(), no)] {
                match child.as_ref() {
                    ImpactNode::Leaf(c) => out.push_str(&format!("{pad}{head}: take {}\n", name(*c))),
                    _ => {
                        out.push_str(&format!("{pad}{head}:\n"));
                        impact_text_node(tree, child, indent + 1, name, out);
                    }
                }
            }
        }
    }
}

// -- the JSON ----------------------------------------------------------------

fn json_string(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    o.push('"');
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            '\r' => o.push_str("\\r"),
            '\t' => o.push_str("\\t"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

/// The explanation as JSON, a node written by `node`, which gives the
/// object of an identifier: the page passes the one of its other answers.
pub fn render_json(tree: &Tree, explanations: &[Explanation], node: &dyn Fn(u32) -> String) -> String {
    let above = tree.loops_above();
    let names: Vec<String> = (0..tree.k).map(|c| json_string(&impact_name(tree, c))).collect();
    let items: Vec<String> = explanations
        .iter()
        .map(|e| {
            let n = tree.node(e.choice);
            let readings: Vec<String> = e.impacts.iter().map(|r| reading_json(tree, r, node)).collect();
            format!(
                "{{\"choice\": {}, \"options\": [{}, {}], \"histories\": {}, \"tests\": {}, \"depth\": {}, \"minimal\": {}, \"tree\": {}, \"impacts\": {{\"time\": {}, \"names\": [{}], \"readings\": [{}]}}}}",
                node(e.choice),
                node(n.low),
                node(n.high),
                e.histories,
                e.tests,
                e.depth,
                e.minimal,
                json_node(tree, &above, &e.root, node),
                tree.time,
                names.join(", "),
                readings.join(", ")
            )
        })
        .collect();
    format!("[{}]", items.join(", "))
}

/// A leaf is `{"take": node}`. A test is the draw it speaks of, `draw` with
/// `loop`, `options` (the two children of a nature node, the region of a
/// loop), `passes` (the loops above, the outermost first, each with the
/// pass the draw is taken in) and, at a loop, `pass`, the pass of its own
/// that the draw follows; the answer it states, `value`; the tree to follow
/// when the draw was taken and gave that answer, `yes`, and the tree to
/// follow in every other case, `no`.
fn json_node(tree: &Tree, above: &[Vec<u32>], n: &Node, node: &dyn Fn(u32) -> String) -> String {
    match n {
        Node::Leaf(c) => format!("{{\"take\": {}}}", node(*c)),
        Node::Test { feature, value, yes, no } => {
            let v = match value {
                Value::Absent => "{\"is\": \"absent\"}".to_string(),
                Value::Child(c) => format!("{{\"is\": \"child\", \"child\": {}}}", node(*c)),
                Value::Again => "{\"is\": \"again\"}".to_string(),
                Value::Out => "{\"is\": \"out\"}".to_string(),
            };
            let d = tree.node(feature.node);
            let is_loop = tree.kind(feature.node) == Kind::Loop;
            let options = if is_loop { node(d.low) } else { format!("{}, {}", node(d.low), node(d.high)) };
            let passes: Vec<String> = above[feature.node as usize]
                .iter()
                .zip(&feature.passes)
                .map(|(l, p)| format!("{{\"loop\": {}, \"pass\": {p}}}", node(*l)))
                .collect();
            let own = if is_loop {
                format!(", \"pass\": {}", feature.passes.last().copied().unwrap_or(0))
            } else {
                String::new()
            };
            format!(
                "{{\"draw\": {}, \"loop\": {}, \"options\": [{}], \"passes\": [{}]{own}, \"value\": {v}, \"yes\": {}, \"no\": {}}}",
                node(feature.node),
                is_loop,
                options,
                passes.join(", "),
                json_node(tree, above, yes, node),
                json_node(tree, above, no, node)
            )
        }
    }
}

/// A reading of the impacts: `time` is `in`, `out` or `none`; `available`
/// the components it may read; `separable` whether a function of them
/// exists, and when not, `clash` holds two decisions, `{"after": [nodes],
/// "take": node}` each, that pay the same and take different regions;
/// `minimum` the tree with the fewest tests on the available components,
/// `sets` one tree per minimal set of components, by size, and
/// `sets_complete` whether every minimal set is there.
fn reading_json(tree: &Tree, r: &ImpactReading, node: &dyn Fn(u32) -> String) -> String {
    let nums = |v: &[usize]| -> String { v.iter().map(|c| c.to_string()).collect::<Vec<_>>().join(", ") };
    let clash = match &r.clash {
        None => "null".to_string(),
        Some(((ta, aa), (tb, ab))) => {
            let one = |t: &[u32], a: u32| -> String {
                let after: Vec<String> = t.iter().map(|&id| node(id)).collect();
                format!("{{\"after\": [{}], \"take\": {}}}", after.join(", "), node(a))
            };
            format!("{{\"a\": {}, \"b\": {}}}", one(ta, *aa), one(tb, *ab))
        }
    };
    let tree_json = |t: &ImpactTree| -> String {
        format!(
            "{{\"components\": [{}], \"tests\": {}, \"depth\": {}, \"minimal\": {}, \"tree\": {}}}",
            nums(&t.components),
            t.tests,
            t.depth,
            t.minimal,
            impact_json_node(tree, &t.root, node)
        )
    };
    let minimum = match &r.minimum {
        None => "null".to_string(),
        Some(m) => tree_json(m),
    };
    let sets: Vec<String> = r.sets.iter().map(|s| tree_json(s)).collect();
    format!(
        "{{\"time\": \"{}\", \"available\": [{}], \"separable\": {}, \"clash\": {}, \"minimum\": {}, \"sets\": [{}], \"sets_complete\": {}}}",
        r.clock.word(),
        nums(&r.available),
        r.separable,
        clash,
        minimum,
        sets.join(", "),
        r.sets_complete
    )
}

/// A leaf is `{"take": node}`. A test is `component`, its index, with its
/// `name`, whether it is the `time`, the `relation` shown, `at most` or `at
/// least` on a maximized component, the `value` compared with as an exact
/// rational and its `approx`, and the two trees `yes` and `no`.
fn impact_json_node(tree: &Tree, n: &ImpactNode, node: &dyn Fn(u32) -> String) -> String {
    match n {
        ImpactNode::Leaf(c) => format!("{{\"take\": {}}}", node(*c)),
        ImpactNode::Test { cut, yes, no } => {
            let (value, relation) = cut_shown(tree, cut);
            let approx = crate::pareto::approx(&value);
            format!(
                "{{\"component\": {}, \"name\": {}, \"time\": {}, \"relation\": \"{}\", \"value\": {}, \"approx\": {}, \"yes\": {}, \"no\": {}}}",
                cut.component,
                json_string(&impact_name(tree, cut.component)),
                tree.time && cut.component == 0,
                relation,
                json_string(&value.to_string()),
                if approx.is_finite() { format!("{approx}") } else { "null".to_string() },
                impact_json_node(tree, yes, node),
                impact_json_node(tree, no, node)
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree_of(text: &str, max_iter: u32) -> Tree {
        let parsed = crate::parse::to_yaml(text).expect("the text parses");
        let mut t = Tree::parse(&parsed.yaml).expect("the instance reads");
        t.max_iter = Some(max_iter);
        t
    }

    fn tests_of(n: &Node, out: &mut Vec<(Feature, Value)>) {
        if let Node::Test { feature, value, yes, no } = n {
            out.push((feature.clone(), *value));
            tests_of(yes, out);
            tests_of(no, out);
        }
    }

    /// The components a tree on the impacts reads, by its tests.
    fn cuts_of(n: &ImpactNode, out: &mut Vec<Cut>) {
        if let ImpactNode::Test { cut, yes, no } = n {
            out.push(cut.clone());
            cuts_of(yes, out);
            cuts_of(no, out);
        }
    }

    /// A nature node and then a choice: the strategy that follows the draw
    /// is one test on that draw.
    #[test]
    fn one_draw_one_test() {
        // identifiers in order: A 1, nature 2, B 3, sequence 4, C 5, choice 6, D 7
        let t = tree_of("((A, 1, {x: 1}) ^[0.5] (B, 1, {x: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1);
        let decisions = vec![(vec![1], 7), (vec![3], 5)];
        let e = explain(&t, &decisions).unwrap();
        assert_eq!(e.len(), 1);
        assert_eq!((e[0].choice, e[0].histories, e[0].tests, e[0].depth, e[0].minimal), (6, 2, 1, 1, true));
        assert_eq!(
            e[0].root,
            Node::Test {
                feature: Feature { node: 2, passes: vec![] },
                value: Value::Child(1),
                yes: Box::new(Node::Leaf(7)),
                no: Box::new(Node::Leaf(5)),
            }
        );
    }

    /// The same process with a strategy that ignores the draw: no test.
    #[test]
    fn a_constant_decision_is_a_leaf() {
        let t = tree_of("((A, 1, {x: 1}) ^[0.5] (B, 1, {x: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1);
        let e = explain(&t, &[(vec![1], 5), (vec![3], 5)]).unwrap();
        assert_eq!((e[0].tests, e[0].depth), (0, 0));
        assert_eq!(e[0].root, Node::Leaf(5));
        // the impacts add nothing: a leaf, and the empty set suffices
        let r = &e[0].impacts[0];
        assert!(r.separable);
        assert_eq!(r.minimum.as_ref().map(|m| (m.tests, m.root.clone())), Some((0, ImpactNode::Leaf(5))));
        assert_eq!(r.sets.iter().map(|s| s.components.clone()).collect::<Vec<_>>(), vec![Vec::<usize>::new()]);
    }

    /// A test is false when its draw gave the other answer and when it was
    /// not taken: one statement, "the draw between A and B took A", parts
    /// the run that took A from the one that took B and from the two that
    /// never came to that draw.
    #[test]
    fn a_test_is_false_when_the_draw_gave_the_other_answer_or_was_not_taken() {
        // identifiers: A 1, nature 2, B 3, nature 4, X 5, nature 6, Y 7,
        // sequence 8, C 9, choice 10, D 11
        let t = tree_of(
            "(((A, 1, {x: 1}) ^[0.5] (B, 1, {x: 2})) ^[0.5] ((X, 1, {x: 1}) ^[0.5] (Y, 1, {x: 2}))) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))",
            1,
        );
        let decisions = vec![(vec![2, 1], 9), (vec![2, 3], 11), (vec![6, 5], 11), (vec![6, 7], 11)];
        let e = explain(&t, &decisions).unwrap();
        assert_eq!((e[0].choice, e[0].histories, e[0].tests, e[0].minimal), (10, 4, 1, true));
        assert_eq!(
            e[0].root,
            Node::Test {
                feature: Feature { node: 2, passes: vec![] },
                value: Value::Child(1),
                yes: Box::new(Node::Leaf(9)),
                no: Box::new(Node::Leaf(11)),
            }
        );
    }

    /// A loop over a choice: the choice is decided once per pass and the
    /// passes are told apart by the draws of the loop, each named by the
    /// pass it follows. The exit forced after the third pass is no draw.
    #[test]
    fn a_loop_is_read_by_the_passes_of_its_draw() {
        // identifiers: A 1, choice 2, B 3, loop 4; the region of the loop is 2
        let t = tree_of("(0.5 ((A, 1, {x: 1}) ^ (B, 1, {x: 2})))", 3);
        let decisions = vec![(vec![], 1), (vec![1, 2], 3), (vec![1, 2, 3, 2], 1)];
        let e = explain(&t, &decisions).unwrap();
        assert_eq!((e[0].choice, e[0].histories, e[0].tests, e[0].minimal), (2, 3, 2, true));
        let mut all = Vec::new();
        tests_of(&e[0].root, &mut all);
        // the two tests state that the loop ran again after its first pass
        // and after its second; none speaks of a draw after the third
        all.sort();
        assert_eq!(
            all,
            vec![
                (Feature { node: 4, passes: vec![1] }, Value::Again),
                (Feature { node: 4, passes: vec![2] }, Value::Again)
            ]
        );
        // the impacts paid so far tell the passes apart as well: the three
        // histories pay 0, 1 and 3 on x, and two cuts separate them
        let r = &e[0].impacts[0];
        assert!(r.separable);
        assert_eq!(r.minimum.as_ref().map(|m| m.tests), Some(2));
    }

    /// The replay types the identifier of a loop by where it stands: the
    /// start of the loop as the child of a choice, and its exit.
    #[test]
    fn replay_tells_the_start_of_a_loop_from_its_exit() {
        // identifiers: A 1, loop 2, choice 3, B 4, sequence 5, C 6, choice 7, D 8
        let t = tree_of("((0.5 (A, 1, {x: 1})) ^ (B, 1, {x: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 1}))", 3);
        let engine = Engine::new(&t);
        // take the loop, run it again once, leave it: the trail writes 2, 1, 2
        let (entries, passes, due, cost) = engine.replay::<Exact>(&[2, 1, 2]).unwrap();
        assert_eq!(entries, vec![Entry::Decision(3, 2), Entry::Again(2), Entry::Out(2)]);
        assert_eq!(passes, vec![vec![], vec![1], vec![2]]);
        assert_eq!(due, Some(7));
        // two passes of A paid 2 on x
        assert_eq!(cost, vec![Exact(BigRational::from_integer(2.into()))]);
        assert!(engine.replay::<Exact>(&[9]).is_err());
    }

    /// A loop inside a loop: a draw is named by the pass of the outer loop
    /// and the pass of the inner one, so that the first draw of the second
    /// outer pass has one name whether the inner loop ran once or twice in
    /// the first; and the exit forced at the bound writes no entry.
    #[test]
    fn a_loop_inside_a_loop_is_indexed_by_both_passes() {
        // identifiers: A 1, nature 2, B 3, inner loop 4, sequence 5, C 6,
        // choice 7, D 8, outer loop 9
        let t = tree_of("(0.5 ((0.5 ((A, 1, {x: 1}) ^[0.5] (B, 1, {x: 2}))) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))))", 2);
        let engine = Engine::new(&t);
        // first outer pass: A, the inner loop again, B, its exit forced at
        // the bound and unwritten, the choice takes C; the outer loop again;
        // second outer pass: A, the inner loop left
        let (entries, passes, due, _) = engine.replay::<Exact>(&[1, 2, 3, 6, 5, 1, 4]).unwrap();
        assert_eq!(
            entries,
            vec![
                Entry::Nature(2, 1),
                Entry::Again(4),
                Entry::Nature(2, 3),
                Entry::Decision(7, 6),
                Entry::Again(9),
                Entry::Nature(2, 1),
                Entry::Out(4),
            ]
        );
        assert_eq!(
            passes,
            vec![vec![1, 1], vec![1, 1], vec![1, 2], vec![1], vec![1], vec![2, 1], vec![2, 1]]
        );
        assert_eq!(due, Some(7));
        // the inner loop left at once in the first outer pass: the first
        // draw of the second outer pass keeps its name
        let (_, other, _, _) = engine.replay::<Exact>(&[1, 4, 6, 5, 1, 4]).unwrap();
        assert_eq!(other[4], vec![2, 1]);
    }

    /// Two decisions after the same draws are refused.
    #[test]
    fn two_decisions_after_the_same_draws_are_refused() {
        let t = tree_of("((A, 1, {x: 1}) ^[0.5] (B, 1, {x: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1);
        assert!(explain(&t, &[(vec![1], 5), (vec![1], 7)]).is_err());
    }

    /// The example of the journal: nature pays (2, 1) or (1, 2) before the
    /// choice, and the strategy decides by which. Either impact alone
    /// suffices, so the minimal sets are the two of one component, and the
    /// fewest tests is one.
    #[test]
    fn the_impacts_are_read_on_the_minimal_sets() {
        // identifiers: A 1, nature 2, B 3, sequence 4, C 5, choice 6, D 7
        let t = tree_of("((A, 1, {x: 2, y: 1}) ^[0.5] (B, 1, {x: 1, y: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1);
        let e = explain(&t, &[(vec![1], 7), (vec![3], 5)]).unwrap();
        assert_eq!(e[0].impacts.len(), 1);
        let r = &e[0].impacts[0];
        assert_eq!(r.clock, Clock::None);
        assert!(r.separable && r.clash.is_none() && r.sets_complete);
        let m = r.minimum.as_ref().unwrap();
        assert_eq!((m.tests, m.depth, m.minimal, m.components.len()), (1, 1, true, 1));
        assert_eq!(r.sets.iter().map(|s| s.components.clone()).collect::<Vec<_>>(), vec![vec![0], vec![1]]);
        for s in &r.sets {
            assert_eq!((s.tests, s.minimal), (1, true));
            let mut cuts = Vec::new();
            cuts_of(&s.root, &mut cuts);
            assert_eq!(cuts.len(), 1);
            assert_eq!(cuts[0].component, s.components[0]);
            // the cut sits at the lesser of the two values, 1
            assert_eq!(cuts[0].at_most, BigRational::from_integer(1.into()));
        }
        // the text names the component and the value
        let text = render_text(&t, &e, &|id| crate::serve::name_of(&t, id));
        assert!(text.contains("by the impacts: 1 test, the fewest, reading "), "{text}");
        assert!(text.contains("paid so far is at most 1: take "), "{text}");
        // the set the minimum reads is left out of the list, the other is there
        let m = r.minimum.as_ref().unwrap().components[0];
        assert!(!text.contains(&format!("by the fewest impacts, {}: ", impact_name(&t, m))), "{text}");
        assert!(text.contains(&format!("by the fewest impacts, {}: 1 test, the fewest", impact_name(&t, 1 - m))), "{text}");
    }

    /// With the time in, the readings are two, and the minimal sets with
    /// the time left out are the minimal sets with it that do not hold it.
    /// Here the time tells nothing apart, both tasks lasting one.
    #[test]
    fn with_the_time_in_there_are_two_readings() {
        let t = tree_of("((A, 1, {x: 2, y: 1}) ^[0.5] (B, 1, {x: 1, y: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1).with_time();
        let e = explain(&t, &[(vec![1], 7), (vec![3], 5)]).unwrap();
        assert_eq!(e[0].impacts.iter().map(|r| r.clock).collect::<Vec<_>>(), vec![Clock::In, Clock::Out]);
        let (i, o) = (&e[0].impacts[0], &e[0].impacts[1]);
        assert_eq!(i.available, vec![0, 1, 2]);
        assert_eq!(o.available, vec![1, 2]);
        assert!(i.separable && o.separable);
        assert_eq!(i.sets.iter().map(|s| s.components.clone()).collect::<Vec<_>>(), vec![vec![1], vec![2]]);
        let without_time: Vec<Vec<usize>> = i.sets.iter().filter(|s| !s.components.contains(&0)).map(|s| s.components.clone()).collect();
        assert_eq!(without_time, o.sets.iter().map(|s| s.components.clone()).collect::<Vec<_>>());
        assert!(!i.minimum.as_ref().unwrap().components.contains(&0));
        let text = render_text(&t, &e, &|id| crate::serve::name_of(&t, id));
        assert!(text.contains("by the impacts, the time left out: the same"), "{text}");
    }

    /// When the time alone tells the histories apart, the one minimal set
    /// with the time is the time, the bottom of the lattice, and with the
    /// time left out no function of the impacts exists.
    #[test]
    fn the_time_alone_may_suffice() {
        // A lasts 1, B lasts 2, both pay 1 on x
        let t = tree_of("((A, 1, {x: 1}) ^[0.5] (B, 2, {x: 1})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1).with_time();
        let e = explain(&t, &[(vec![1], 7), (vec![3], 5)]).unwrap();
        let (i, o) = (&e[0].impacts[0], &e[0].impacts[1]);
        assert!(i.separable);
        assert_eq!(i.sets.iter().map(|s| s.components.clone()).collect::<Vec<_>>(), vec![vec![0]]);
        let m = i.minimum.as_ref().unwrap();
        assert_eq!((m.tests, m.components.clone()), (1, vec![0]));
        assert!(!o.separable);
        let ((ta, aa), (tb, ab)) = o.clash.clone().unwrap();
        assert_eq!(((ta, aa), (tb, ab)), ((vec![1], 7), (vec![3], 5)));
        let text = render_text(&t, &e, &|id| crate::serve::name_of(&t, id));
        assert!(text.contains("if the time elapsed is at most 1: take D"), "{text}");
        assert!(text.contains("by the impacts, the time left out: none, two histories pay the same and take different regions: after A take D; after B take C"), "{text}");
        // the one minimal set is the one the minimum reads: left out of the list
        assert!(!text.contains("by the fewest impacts"), "{text}");
    }

    /// A decision that is the parity of two draws needs both impacts: the
    /// one minimal set is the pair, and the fewest tests on it is three, as
    /// on the draws.
    #[test]
    fn a_parity_needs_both_impacts() {
        // identifiers: A1 1, nature 2, A2 3, sequence 4, B1 5, nature 6,
        // B2 7, sequence 8, C 9, choice 10, D 11
        let t = tree_of(
            "(((A1, 1, {x: 1, y: 1}) ^[0.5] (A2, 1, {x: 2, y: 1})) , ((B1, 1, {x: 1, y: 1}) ^[0.5] (B2, 1, {x: 1, y: 2}))) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))",
            1,
        );
        let decisions = vec![(vec![1, 5], 9), (vec![1, 7], 11), (vec![3, 5], 11), (vec![3, 7], 9)];
        let e = explain(&t, &decisions).unwrap();
        assert_eq!(e[0].tests, 3);
        let r = &e[0].impacts[0];
        assert!(r.separable);
        assert_eq!(r.sets.iter().map(|s| s.components.clone()).collect::<Vec<_>>(), vec![vec![0, 1]]);
        assert_eq!(r.sets[0].tests, 3);
        assert_eq!(r.minimum.as_ref().unwrap().tests, 3);
    }

    /// Two histories that pay the same and take different regions: no
    /// function of the impacts, and the clash names them.
    #[test]
    fn the_same_impacts_and_different_regions_have_no_impact_explainer() {
        let t = tree_of("((A, 1, {x: 1}) ^[0.5] (B, 1, {x: 1})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1);
        let e = explain(&t, &[(vec![1], 5), (vec![3], 7)]).unwrap();
        assert_eq!(e[0].tests, 1);
        let r = &e[0].impacts[0];
        assert!(!r.separable && r.minimum.is_none() && r.sets.is_empty());
        assert_eq!(r.clash.clone().unwrap(), ((vec![1], 5), (vec![3], 7)));
        let json = render_json(&t, &e, &|id| id.to_string());
        assert!(json.contains("\"separable\": false"), "{json}");
        assert!(json.contains("\"clash\": {\"a\": {\"after\": [1], \"take\": 5}, \"b\": {\"after\": [3], \"take\": 7}}"), "{json}");
    }

    /// A maximized component is carried negated: the cut is shown with its
    /// sign turned back and the relation turned round.
    #[test]
    fn a_maximized_component_is_shown_at_least() {
        let t = tree_of("((A, 1, {x: 2}) ^[0.5] (B, 1, {x: 1})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1)
            .with_directions(&["x".to_string()])
            .unwrap();
        let e = explain(&t, &[(vec![1], 7), (vec![3], 5)]).unwrap();
        let text = render_text(&t, &e, &|id| crate::serve::name_of(&t, id));
        // internally the rows pay -2 and -1; the cut at -2 reads "at least 2"
        assert!(text.contains("if the x so far is at least 2: take D"), "{text}");
        let json = render_json(&t, &e, &|id| id.to_string());
        assert!(json.contains("\"relation\": \"at least\", \"value\": \"2\", \"approx\": 2"), "{json}");
    }

    /// The subsets come by size and in lexicographic order.
    #[test]
    fn combinations_are_in_order() {
        assert_eq!(combinations(3, 0), vec![Vec::<usize>::new()]);
        assert_eq!(combinations(3, 2), vec![vec![0, 1], vec![0, 2], vec![1, 2]]);
        assert_eq!(combinations(2, 3), Vec::<Vec<usize>>::new());
        assert_eq!(combinations(4, 4), vec![vec![0, 1, 2, 3]]);
    }
}
