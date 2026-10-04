//! The explainer: a strategy read as one decision tree per choice node, on
//! the draws of nature alone.
//!
//! A strategy is a deterministic policy, and at a choice it decides by a
//! function of the draws that precede, its own earlier decisions being
//! determined by those draws (Theorem "A deterministic policy decides by the
//! draws" of parallel-paper/semantics-time.tex of the paper repository). An
//! explanation therefore reads the draws and nothing else. With loops a draw
//! may be taken several times at one node, once per pass, and the reading is
//! the sequence; what the trees test is that sequence written as an
//! assignment indexed by the occurrences, the `j`-th draw at the node `u`
//! with what it gave, or that it was not taken: two different histories at
//! which a strategy meets the same choice differ in one such value, so the
//! data of a choice are always separable.
//!
//! For every choice node the histories at which the strategy decides it are
//! the rows, the draws their features, the child taken their label, and the
//! tree returned separates them with the fewest tests, and among the trees
//! with the fewest tests the least depth. The search for it is exact, a
//! memoised recursion on the sets of rows, as long as it stays within a
//! budget of sets; past the budget it goes on greedily and the explanation
//! says that it is not known to be minimal.

use std::collections::{BTreeMap, HashMap};

use crate::state::{Engine, Entry};
use crate::tree::{Kind, Tree};

/// The `occurrence`-th draw, counted from 1, at `node`, a nature node or a
/// loop: what a test of an explanation asks about.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Feature {
    pub node: u32,
    pub occurrence: u32,
}

/// What a draw gave, or that it was not taken before the choice.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Value {
    /// The draw was not taken before the choice.
    Absent,
    /// A nature node drew this child.
    Child(u32),
    /// A loop ran its region again.
    Again,
    /// A loop was left.
    Out,
}

/// A decision tree on the draws: a leaf takes a child of the choice, a test
/// asks what one draw gave and has one branch per answer met.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Node {
    Leaf(u32),
    Test { feature: Feature, branches: Vec<(Value, Node)> },
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
        let (entries, due) = engine.replay(dh)?;
        let v = due.ok_or_else(|| "a decision is recorded at a history that ends the run".to_string())?;
        let n = tree.node(v);
        if *action != n.low && *action != n.high {
            return Err(format!("the decision {action} is no child of the choice {v} it is recorded at"));
        }
        let mut seen: HashMap<u32, u32> = HashMap::new();
        let mut row = Vec::new();
        for e in &entries {
            let (u, value) = match *e {
                Entry::Decision(..) => continue,
                Entry::Nature(u, c) => (u, Value::Child(c)),
                Entry::Again(u) => (u, Value::Again),
                Entry::Out(u) => (u, Value::Out),
            };
            let occurrence = seen.entry(u).or_insert(0);
            *occurrence += 1;
            row.push((Feature { node: u, occurrence: *occurrence }, value));
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
    // the features in the order they are first met, a row after the other
    let mut index: HashMap<Feature, usize> = HashMap::new();
    let mut features: Vec<Feature> = Vec::new();
    for (row, _) in rows {
        for (f, _) in row {
            if !index.contains_key(f) {
                index.insert(*f, features.len());
                features.push(*f);
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
    let mut learner = Learner { values: &values, labels: &labels, memo: HashMap::new(), exact: true };
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
    /// A set of rows, sorted, to the tests and the depth of its tree and the
    /// feature at its root, `usize::MAX` at a leaf.
    memo: HashMap<Vec<u32>, (u32, u32, usize)>,
    /// False once the budget is passed: the search is greedy from there.
    exact: bool,
}

impl Learner<'_> {
    /// The rows of `set` by the value they have at the feature `f`.
    fn split(&self, set: &[u32], f: usize) -> Vec<(Value, Vec<u32>)> {
        let mut parts: BTreeMap<Value, Vec<u32>> = BTreeMap::new();
        for &r in set {
            parts.entry(self.values[r as usize][f]).or_default().push(r);
        }
        parts.into_iter().collect()
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
    /// separates `set`; the feature chosen stays in the memo.
    fn solve(&mut self, set: Vec<u32>) -> (u32, u32) {
        if let Some(&(t, d, _)) = self.memo.get(&set) {
            return (t, d);
        }
        if self.impurity(&set) == 0 {
            self.memo.insert(set, (0, 0, usize::MAX));
            return (0, 0);
        }
        // the features that part the set, the most promising first: the one
        // that leaves the fewest rows on the wrong side of a leaf
        let nf = self.values[0].len();
        let mut candidates: Vec<(usize, usize, Vec<(Value, Vec<u32>)>)> = Vec::new();
        for f in 0..nf {
            let parts = self.split(&set, f);
            if parts.len() > 1 {
                let score: usize = parts.iter().map(|(_, p)| self.impurity(p)).sum();
                candidates.push((score, f, parts));
            }
        }
        candidates.sort_by(|a, b| (a.0, a.2.len(), a.1).cmp(&(b.0, b.2.len(), b.1)));
        if self.memo.len() > BUDGET {
            self.exact = false;
        }
        let mut best: (u32, u32, usize) = (u32::MAX, u32::MAX, usize::MAX);
        for (_, f, parts) in candidates {
            let mut tests = 1u32;
            let mut depth = 0u32;
            let mut pruned = false;
            // every part that no leaf decides costs one test at least
            let mut owed: u32 = parts.iter().filter(|(_, p)| self.impurity(p) > 0).count() as u32;
            for (_, part) in parts {
                if self.impurity(&part) > 0 {
                    owed -= 1;
                }
                let (t, d) = self.solve(part);
                tests += t;
                depth = depth.max(d);
                if tests + owed > best.0 {
                    pruned = true;
                    break;
                }
            }
            if !pruned && (tests, depth + 1) < (best.0, best.1) {
                best = (tests, depth + 1, f);
            }
            if !self.exact && best.0 != u32::MAX {
                // past the budget the first feature that works is kept
                break;
            }
        }
        debug_assert!(best.2 != usize::MAX, "an impure set that no feature parts");
        self.memo.insert(set, best);
        (best.0, best.1)
    }

    fn build(&self, set: &[u32], features: &[Feature]) -> Node {
        let (_, _, f) = self.memo[set];
        if f == usize::MAX {
            return Node::Leaf(self.labels[set[0] as usize]);
        }
        let branches = self
            .split(set, f)
            .into_iter()
            .map(|(value, part)| (value, self.build(&part, features)))
            .collect();
        Node::Test { feature: features[f], branches }
    }
}

/// First, second, third and so on, as the reports write the occurrences.
pub fn ordinal(n: u32) -> String {
    let suffix = match (n % 10, n % 100) {
        (1, 11) | (2, 12) | (3, 13) => "th",
        (1, _) => "st",
        (2, _) => "nd",
        (3, _) => "rd",
        _ => "th",
    };
    format!("{n}{suffix}")
}

/// The explanation as lines of text, the names of the nodes given by `name`.
pub fn render_text(tree: &Tree, explanations: &[Explanation], name: &dyn Fn(u32) -> String) -> String {
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
        text_node(tree, &e.root, 1, name, &mut out);
    }
    out
}

fn value_text(value: Value, name: &dyn Fn(u32) -> String) -> String {
    match value {
        Value::Absent => "not drawn".to_string(),
        Value::Child(c) => format!("took {}", name(c)),
        Value::Again => "ran again".to_string(),
        Value::Out => "was left".to_string(),
    }
}

fn text_node(tree: &Tree, node: &Node, indent: usize, name: &dyn Fn(u32) -> String, out: &mut String) {
    let pad = "  ".repeat(indent);
    match node {
        Node::Leaf(c) => out.push_str(&format!("{pad}take {}\n", name(*c))),
        Node::Test { feature, branches } => {
            let d = tree.node(feature.node);
            let what = if tree.kind(feature.node) == Kind::Loop {
                // the name of a region is the one of its first task
                if tree.kind(d.low) == Kind::Task {
                    format!("of the loop {} over {}", feature.node, name(d.low))
                } else {
                    format!("of the loop {}, which starts with {}", feature.node, name(d.low))
                }
            } else {
                format!("at the nature node {} ({} or {})", feature.node, name(d.low), name(d.high))
            };
            out.push_str(&format!("{pad}the {} draw {what}:\n", ordinal(feature.occurrence)));
            for (value, child) in branches {
                match child {
                    Node::Leaf(c) => out.push_str(&format!("{pad}  {}: take {}\n", value_text(*value, name), name(*c))),
                    _ => {
                        out.push_str(&format!("{pad}  {}:\n", value_text(*value, name)));
                        text_node(tree, child, indent + 2, name, out);
                    }
                }
            }
        }
    }
}

/// The explanation as JSON, a node written by `node`, which gives the
/// object of an identifier: the page passes the one of its other answers.
pub fn render_json(tree: &Tree, explanations: &[Explanation], node: &dyn Fn(u32) -> String) -> String {
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
                json_node(tree, &e.root, node)
            )
        })
        .collect();
    format!("[{}]", items.join(", "))
}

fn json_node(tree: &Tree, n: &Node, node: &dyn Fn(u32) -> String) -> String {
    match n {
        Node::Leaf(c) => format!("{{\"take\": {}}}", node(*c)),
        Node::Test { feature, branches } => {
            let items: Vec<String> = branches
                .iter()
                .map(|(value, child)| {
                    let v = match value {
                        Value::Absent => "{\"is\": \"absent\"}".to_string(),
                        Value::Child(c) => format!("{{\"is\": \"child\", \"child\": {}}}", node(*c)),
                        Value::Again => "{\"is\": \"again\"}".to_string(),
                        Value::Out => "{\"is\": \"out\"}".to_string(),
                    };
                    format!("{{\"value\": {v}, \"tree\": {}}}", json_node(tree, child, node))
                })
                .collect();
            // a nature node is named by its two children, a loop by its
            // region
            let d = tree.node(feature.node);
            let is_loop = tree.kind(feature.node) == Kind::Loop;
            let options = if is_loop { node(d.low) } else { format!("{}, {}", node(d.low), node(d.high)) };
            format!(
                "{{\"draw\": {}, \"loop\": {}, \"options\": [{}], \"occurrence\": {}, \"branches\": [{}]}}",
                node(feature.node),
                is_loop,
                options,
                feature.occurrence,
                items.join(", ")
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
                feature: Feature { node: 2, occurrence: 1 },
                branches: vec![(Value::Child(1), Node::Leaf(7)), (Value::Child(3), Node::Leaf(5))],
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

    /// A loop over a choice: the choice is decided once per pass and the
    /// passes are told apart by the draws of the loop, by their occurrence.
    #[test]
    fn a_loop_is_read_by_the_occurrences_of_its_draw() {
        // identifiers: A 1, choice 2, B 3, loop 4; the region of the loop is 2
        let t = tree_of("(0.5 ((A, 1, {x: 1}) ^ (B, 1, {x: 2})))", 3);
        let decisions = vec![(vec![], 1), (vec![1, 2], 3), (vec![1, 2, 3, 2], 1)];
        let e = explain(&t, &decisions).unwrap();
        assert_eq!((e[0].choice, e[0].histories, e[0].tests, e[0].minimal), (2, 3, 2, true));
        let first = Feature { node: 4, occurrence: 1 };
        let second = Feature { node: 4, occurrence: 2 };
        // either draw may sit at the root: both trees have two tests
        match &e[0].root {
            Node::Test { feature, .. } => assert!(*feature == first || *feature == second),
            _ => panic!("a tree with two tests has a test at its root"),
        }
    }

    /// The replay types the identifier of a loop by where it stands: the
    /// start of the loop as the child of a choice, and its exit.
    #[test]
    fn replay_tells_the_start_of_a_loop_from_its_exit() {
        // identifiers: A 1, loop 2, choice 3, B 4, sequence 5, C 6, choice 7, D 8
        let t = tree_of("((0.5 (A, 1, {x: 1})) ^ (B, 1, {x: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 1}))", 3);
        let engine = Engine::new(&t);
        // take the loop, run it again once, leave it: the trail writes 2, 1, 2
        let (entries, due) = engine.replay(&[2, 1, 2]).unwrap();
        assert_eq!(entries, vec![Entry::Decision(3, 2), Entry::Again(2), Entry::Out(2)]);
        assert_eq!(due, Some(7));
        assert!(engine.replay(&[9]).is_err());
    }

    /// Two decisions after the same draws are refused.
    #[test]
    fn two_decisions_after_the_same_draws_are_refused() {
        let t = tree_of("((A, 1, {x: 1}) ^[0.5] (B, 1, {x: 2})) , ((C, 1, {x: 1}) ^ (D, 1, {x: 3}))", 1);
        assert!(explain(&t, &[(vec![1], 5), (vec![1], 7)]).is_err());
    }
}
