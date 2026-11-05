//! The examples of the paper and of the thesis, built into the binary.
//!
//! Every example is a process one of the two documents works on: the paper
//! "Explaining Strategies for Expected Impacts" and the PhD thesis
//! "Synthesizing Strategies for Quantitative Processes". Its text is a file
//! under `examples/`, embedded here, and beside it the manifest keeps the
//! settings it is read with: whether the expected time is a component of the
//! frontier; the max iterations; the components to maximize. The page lists
//! them in its panel "Examples" and loads one with one click, settings
//! included.
//!
//! An example is loaded under its id, and the id is also what a reference
//! `@id` resolves to on the page when the store of the saved processes holds
//! no process of that name: the release refers to its tests as `@tests` and
//! the stroke to its transport as `@transport`, and both read on an empty
//! store. On the command line the same two subprocesses are the entries of
//! `examples/store.json`, for `sdcpi parse --saved`.

/// One example: a process of a document, with the settings it is read with.
pub struct Example {
    /// The name the example is loaded under, and the name a reference to it
    /// is written with.
    pub id: &'static str,
    /// The document it comes from, `paper` or `thesis`.
    pub group: &'static str,
    pub title: &'static str,
    /// Where it appears in its document, in words.
    pub source: &'static str,
    /// What it shows, in one sentence.
    pub note: &'static str,
    /// The process, in the grammar of `parse`.
    pub text: &'static str,
    /// Whether the expected time is a component of the frontier: the switch
    /// "Expected time" of the page, `--time 1` on the command line.
    pub time: bool,
    /// The max iterations: the field of that name on the page, `--max-iter`
    /// on the command line.
    pub max_iter: u32,
    /// The components to maximize, by name: `--maximize` on the command line.
    pub maximize: &'static [&'static str],
}

/// The examples, the paper first, each document in the order it meets them.
pub const EXAMPLES: &[Example] = &[
    Example {
        id: "release",
        group: "paper",
        title: "The software release",
        source: "The running example",
        note: "A loop over the rounds of review, a choice of the fix and the tests as a subprocess: with max iterations 2 the frontier has 1937 points.",
        text: include_str!("../examples/release.cpi"),
        time: false,
        max_iter: 2,
        maximize: &[],
    },
    Example {
        id: "tests",
        group: "paper",
        title: "The tests of the release",
        source: "The running example, the subprocess of the release",
        note: "The subprocess the release refers to as @tests: the unit tests, then a draw of nature between the signoff and a choice between a patch and a refactoring.",
        text: include_str!("../examples/tests.cpi"),
        time: false,
        max_iter: 2,
        maximize: &[],
    },
    Example {
        id: "coin",
        group: "paper",
        title: "The coin",
        source: "The section on the explanations on the cost",
        note: "A strategy with no explanation on the cost: of the three points, (6, 6) is reached only by deciding on the draw, after two histories that paid the same.",
        text: include_str!("../examples/coin.cpi"),
        time: false,
        max_iter: 1,
        maximize: &[],
    },
    Example {
        id: "winery",
        group: "thesis",
        title: "The winery",
        source: "The motivation chapter, first scenario",
        note: "Two currencies in opposite directions, the euro kept low and the points kept high: a frontier of seven points.",
        text: include_str!("../examples/winery.cpi"),
        time: false,
        max_iter: 1,
        maximize: &["points"],
    },
    Example {
        id: "stroke",
        group: "thesis",
        title: "The stroke pathway",
        source: "The motivation chapter, second scenario",
        note: "The expected time as a component and the transport as a subprocess used twice: a frontier of twelve points.",
        text: include_str!("../examples/stroke.cpi"),
        time: true,
        max_iter: 1,
        maximize: &[],
    },
    Example {
        id: "transport",
        group: "thesis",
        title: "The transport of the stroke",
        source: "The motivation chapter, the subprocess of the stroke pathway",
        note: "The subprocess the stroke refers to as @transport: the helicopter, or the ambulance and then the road chosen after the draw of the jam.",
        text: include_str!("../examples/transport.cpi"),
        time: true,
        max_iter: 1,
        maximize: &[],
    },
    Example {
        id: "release-nested",
        group: "thesis",
        title: "The software release of the thesis",
        source: "The motivation chapter, third scenario",
        note: "A loop inside a loop and strategies too large to read: with max iterations 2 and the expected time the frontier has 1024 points.",
        text: include_str!("../examples/release-nested.cpi"),
        time: true,
        max_iter: 2,
        maximize: &[],
    },
    Example {
        id: "line",
        group: "thesis",
        title: "The metal line",
        source: "The chapter on the semantics, the running example",
        note: "The process the semantics is defined on, a draw of nature beside a choice and then a choice that may read the draw: a frontier of four points.",
        text: include_str!("../examples/line-thesis.cpi"),
        time: false,
        max_iter: 1,
        maximize: &[],
    },
    Example {
        id: "quantile",
        group: "thesis",
        title: "The quantile example",
        source: "The section on the quantile queries",
        note: "A choice between two draws of nature on which the expected impact and a quantile query disagree: the frontier is the one point (2, 2.5), the draw between T3 and T4.",
        text: include_str!("../examples/quantile.cpi"),
        time: false,
        max_iter: 1,
        maximize: &[],
    },
    Example {
        id: "loop-choice",
        group: "thesis",
        title: "The loop over a choice",
        source: "The chapter on time and loops",
        note: "One decision per pass of the loop: with max iterations 2 the frontier has four points, one per word of two decisions; the chapter computes it at three passes, where it has eight.",
        text: include_str!("../examples/loop-choice.cpi"),
        time: false,
        max_iter: 2,
        maximize: &[],
    },
];

/// The example of an id, when there is one.
pub fn find(id: &str) -> Option<&'static Example> {
    EXAMPLES.iter().find(|e| e.id == id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parse;
    use crate::tree::Tree;

    /// The resolver of the page on an empty store: a reference is the
    /// example of that id.
    fn built_in(name: &str) -> Result<String, String> {
        find(name).map(|e| e.text.to_string()).ok_or_else(|| format!("no example is named {name:?}"))
    }

    #[test]
    fn the_ids_are_distinct_and_can_be_referred_to_without_quotes() {
        for (i, e) in EXAMPLES.iter().enumerate() {
            assert!(EXAMPLES[..i].iter().all(|f| f.id != e.id), "{} is listed twice", e.id);
            assert!(
                !e.id.is_empty() && e.id.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-')),
                "{:?} is no name a reference can write bare",
                e.id
            );
            assert!(e.group == "paper" || e.group == "thesis", "{} has the group {:?}", e.id, e.group);
        }
    }

    #[test]
    fn every_example_opens_with_a_comment_and_carries_what_the_panel_shows() {
        for e in EXAMPLES {
            let comment = e.text.lines().take_while(|l| l.starts_with('#')).count();
            assert!((1..=3).contains(&comment), "{} opens with {comment} lines of comment", e.id);
            assert!(!e.title.is_empty() && !e.source.is_empty() && !e.note.is_empty(), "{}", e.id);
            assert!(e.max_iter >= 1, "{}", e.id);
        }
    }

    #[test]
    fn every_example_parses_on_an_empty_store_and_has_the_components_it_maximizes() {
        for e in EXAMPLES {
            let mut resolve = built_in;
            let parsed = parse::to_yaml_as(e.text, e.id, &mut resolve).unwrap_or_else(|err| panic!("{}: {err}", e.id));
            let tree = Tree::parse(&parsed.yaml).unwrap_or_else(|err| panic!("{}: {err}", e.id));
            for name in e.maximize {
                assert!(
                    tree.meta.impact_names.iter().any(|n| n == name),
                    "{} maximizes {name}, which is no impact of it",
                    e.id
                );
            }
        }
    }

    #[test]
    fn the_store_of_the_command_line_holds_the_subprocesses_as_the_examples_have_them() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("examples/store.json");
        let entries = crate::serve::read_store(&path).unwrap();
        assert!(!entries.is_empty(), "{} is empty or missing", path.display());
        for (name, text) in &entries {
            let e = find(name).unwrap_or_else(|| panic!("{name} of the store is no example"));
            assert_eq!(text, e.text, "{name} differs between examples/store.json and its file");
        }
        // every reference of an example resolves in the store, so that
        // `sdcpi parse --saved examples/store.json` reads every example
        for e in EXAMPLES {
            let mut resolve = |name: &str| -> Result<String, String> {
                entries
                    .iter()
                    .find(|(n, _)| n == name)
                    .map(|(_, t)| t.clone())
                    .ok_or_else(|| format!("{name} is not in examples/store.json"))
            };
            parse::to_yaml_with(e.text, &mut resolve).unwrap_or_else(|err| panic!("{}: {err}", e.id));
        }
    }
}
