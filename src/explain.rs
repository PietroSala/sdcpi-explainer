//! The explainer: a strategy read as one decision tree per choice node, on
//! the draws of nature alone.
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

use std::collections::{BTreeMap, HashMap, HashSet};

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

/// The explanation of one choice node of a strategy.
#[derive(Clone, Debug)]
pub struct Explanation {
    /// The choice node.
    pub choice: u32,
    /// The histories at which the strategy decides it.
    pub histories: usize,
    /// The tests of the tree, its internal nodes.
    pub tests: usize,
    /// The most tests on a path of the tree.
    pub depth: usize,
    /// Whether the tree is known to have the fewest tests: the exact search
    /// ended within its budget.
    pub minimal: bool,
    pub root: Node,
}

/// The sets of rows the exact search may solve before it goes on greedily.
const BUDGET: usize = 200_000;

/// The explanation of a strategy, one tree per choice node it decides, in
/// the order of the identifiers. The decisions are the ones the search
/// records, a trail and the child taken after it.
pub fn explain(tree: &Tree, decisions: &[(Vec<u32>, u32)]) -> Result<Vec<Explanation>, String> {
    let engine = Engine::new(tree);
    // per choice node, the rows: the draws as (feature, value) in the order
    // they were taken, and the child the strategy starts
    let mut by_choice: BTreeMap<u32, Vec<(Vec<(Feature, Value)>, u32)>> = BTreeMap::new();
    for (dh, action) in decisions {
        let (entries, passes, due) = engine.replay(dh)?;
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
        by_choice.entry(v).or_default().push((row, *action));
    }
    let mut out = Vec::with_capacity(by_choice.len());
    for (v, rows) in by_choice {
        out.push(learn(v, &rows)?);
    }
    Ok(out)
}

/// The tree of one choice node from its rows.
fn learn(choice: u32, rows: &[(Vec<(Feature, Value)>, u32)]) -> Result<Explanation, String> {
    // the draws in the order they are first met, a row after the other
    let mut index: HashMap<Feature, usize> = HashMap::new();
    let mut features: Vec<Feature> = Vec::new();
    for (row, _) in rows {
        for (f, _) in row {
            if !index.contains_key(f) {
                index.insert(f.clone(), features.len());
                features.push(f.clone());
            }
        }
    }
    let values: Vec<Vec<Value>> = rows
        .iter()
        .map(|(row, _)| {
            let mut v = vec![Value::Absent; features.len()];
            for (f, x) in row {
                v[index[f]] = *x;
            }
            v
        })
        .collect();
    let labels: Vec<u32> = rows.iter().map(|(_, a)| *a).collect();
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
    let mut learner = Learner { values: &values, labels: &labels, statements: &statements, memo: HashMap::new(), exact: true };
    let all: Vec<u32> = (0..rows.len() as u32).collect();
    let (tests, depth) = learner.solve(all.clone());
    let root = learner.build(&all, &features);
    Ok(Explanation {
        choice,
        histories: rows.len(),
        tests: tests as usize,
        depth: depth as usize,
        minimal: learner.exact,
        root,
    })
}

struct Learner<'a> {
    values: &'a [Vec<Value>],
    labels: &'a [u32],
    /// A draw, by its position among the features, and an answer.
    statements: &'a [(usize, Value)],
    /// A set of rows, sorted, to the tests and the depth of its tree and the
    /// statement at its root, `usize::MAX` at a leaf.
    memo: HashMap<Vec<u32>, (u32, u32, usize)>,
    /// False once the budget is passed: the search is greedy from there.
    exact: bool,
}

impl Learner<'_> {
    /// The rows of `set` of which the statement `p` is true, and the others:
    /// those whose draw gave another answer and those where it was not taken.
    fn split(&self, set: &[u32], p: usize) -> (Vec<u32>, Vec<u32>) {
        let (f, x) = self.statements[p];
        set.iter().partition(|&&r| self.values[r as usize][f] == x)
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
    /// separates `set`; the statement chosen stays in the memo.
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
        for p in 0..self.statements.len() {
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
            if 1 + t1 + owed > best.0 {
                continue;
            }
            let (t2, d2) = self.solve(no);
            let found = (1 + t1 + t2, 1 + d1.max(d2));
            if found < (best.0, best.1) {
                best = (found.0, found.1, p);
            }
            if !self.exact && best.0 != u32::MAX {
                // past the budget the first statement that works is kept
                break;
            }
        }
        debug_assert!(best.2 != usize::MAX, "an impure set that no statement parts");
        self.memo.insert(set, best);
        (best.0, best.1)
    }

    fn build(&self, set: &[u32], features: &[Feature]) -> Node {
        let (_, _, p) = self.memo[set];
        if p == usize::MAX {
            return Node::Leaf(self.labels[set[0] as usize]);
        }
        let (f, value) = self.statements[p];
        let (yes, no) = self.split(set, p);
        Node::Test {
            feature: features[f].clone(),
            value,
            yes: Box::new(self.build(&yes, features)),
            no: Box::new(self.build(&no, features)),
        }
    }
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

/// The explanation as lines of text, the names of the nodes given by `name`.
pub fn render_text(tree: &Tree, explanations: &[Explanation], name: &dyn Fn(u32) -> String) -> String {
    let above = tree.loops_above();
    let mut out = String::new();
    for e in explanations {
        let n = tree.node(e.choice);
        out.push_str(&format!(
            "choice {} ({} or {}): {} {}, {} {}{}\n",
            e.choice,
            name(n.low),
            name(n.high),
            e.histories,
            if e.histories == 1 { "history" } else { "histories" },
            e.tests,
            if e.tests == 1 { "test" } else { "tests" },
            if e.minimal { ", the fewest" } else { ", not known to be the fewest" }
        ));
        text_node(tree, &above, &e.root, 1, name, &mut out);
    }
    out
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

/// The explanation as JSON, a node written by `node`, which gives the
/// object of an identifier: the page passes the one of its other answers.
pub fn render_json(tree: &Tree, explanations: &[Explanation], node: &dyn Fn(u32) -> String) -> String {
    let above = tree.loops_above();
    let items: Vec<String> = explanations
        .iter()
        .map(|e| {
            let n = tree.node(e.choice);
            format!(
                "{{\"choice\": {}, \"options\": [{}, {}], \"histories\": {}, \"tests\": {}, \"depth\": {}, \"minimal\": {}, \"tree\": {}}}",
                node(e.choice),
                node(n.low),
                node(n.high),
                e.histories,
                e.tests,
                e.depth,
                e.minimal,
                json_node(tree, &above, &e.root, node)
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
    }

    /// The replay types the identifier of a loop by where it stands: the
    /// start of the loop as the child of a choice, and its exit.
    #[test]
    fn replay_tells_the_start_of_a_loop_from_its_exit() {
        // identifiers: A 1, loop 2, choice 3, B 4, sequence 5, C 6, choice 7, D 8
        let t = tree_of("((0.5 (A, 1, {x: 1})) ^ (B, 1, {x: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 1}))", 3);
        let engine = Engine::new(&t);
        // take the loop, run it again once, leave it: the trail writes 2, 1, 2
        let (entries, passes, due) = engine.replay(&[2, 1, 2]).unwrap();
        assert_eq!(entries, vec![Entry::Decision(3, 2), Entry::Again(2), Entry::Out(2)]);
        assert_eq!(passes, vec![vec![], vec![1], vec![2]]);
        assert_eq!(due, Some(7));
        assert!(engine.replay(&[9]).is_err());
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
        let (entries, passes, due) = engine.replay(&[1, 2, 3, 6, 5, 1, 4]).unwrap();
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
        let (_, other, _) = engine.replay(&[1, 4, 6, 5, 1, 4]).unwrap();
        assert_eq!(other[4], vec![2, 1]);
    }

    /// Two decisions after the same draws are refused.
    #[test]
    fn two_decisions_after_the_same_draws_are_refused() {
        let t = tree_of("((A, 1, {x: 1}) ^[0.5] (B, 1, {x: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1);
        assert!(explain(&t, &[(vec![1], 5), (vec![1], 7)]).is_err());
    }
}
