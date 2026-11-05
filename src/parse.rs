//! The grammar of a process, and the writer of the instance file.
//!
//!     process ::= region ("," region)* | region op region
//!     region  ::= task | "(" process ")" | "@" saved ("[" shown "]")? | "(" prob process ")"
//!     op      ::= "||" | "^" | "^[" prob "]"
//!     task    ::= "(" name "," duration ")"
//!             |   "(" name "," duration "," "{" impacts? "}" ")"
//!     impacts ::= name ":" number ("," name ":" number)*
//!
//! `,` between regions is a sequence and associates to the left: `A, B, C`
//! reads `(A, B), C`, the tree staying binary, and the chain needs no
//! parentheses of its own; the comma is unambiguous because a task is
//! recognised by the name after its parenthesis and the commas of the tuple
//! are consumed inside it. `||` is a parallel composition, `^` a choice and
//! `^[p]` a nature node whose left operand is taken with probability `p` in
//! (0,1); each of these three carries its own parentheses, one operator per
//! pair, so no precedence has to be fixed, and mixing the sequence comma
//! with another operator demands the parentheses too. The outermost
//! parentheses of the whole term are optional. A duration is a
//! positive integer. The impact map of a task names only the impacts that are
//! strictly positive; a task may omit the map, or write `{}`, both meaning
//! every impact zero. The names are collected over the whole process in the
//! order of their first appearance, and that order is the meaning of the
//! vectors: `impact_names` in the emitted file lists them, and entry `i` of
//! every `impact` vector is the value of name `i`, zero where the task does
//! not name it. `#` starts a comment that runs to the end of the line.
//!
//! Identifiers are assigned by the in-order traversal, one-based: every node
//! of the low subtree numbers below its parent, every node of the high
//! subtree above, which is the numbering the reader of `tree.rs` checks.
//!
//! A loop `(p R)` is a region that runs `R` and, once `R` is done, runs it
//! again with probability `p` or leaves with probability `1 - p`: the
//! probability, in (0,1), sits right after the parenthesis, where no task
//! and no process can begin, and the loop node is the one unary node of the
//! tree, its region being its low child.
//!
//! A reference `@saved` stands for a saved process, spliced in place as a
//! region, which the single-entry single-exit shape of every process allows;
//! `@saved[shown]` shows it under another name. The saved text may refer to
//! other saved processes in turn, to any depth, and never to itself through
//! any chain: a cycle is an error. The impacts are merged by name, one table
//! over the whole assembly. The engine sees the assembled process, with its
//! identifiers in-order over the whole; beside them every node gets the
//! identifier it shows, the own identifier in the process that wrote it
//! prefixed by the identifiers of the references that led there, `7-3` for
//! the node 3 of the process the leaf 7 refers to. Who resolves a name is
//! the caller, through `to_yaml_with`; `to_yaml` resolves nothing and fails
//! on a reference.
//!
//! The bounds below are read on the tree as it is built, references included,
//! since a short text may stand for a very large process: a text saved once
//! and referred to many times is parsed once per reference, and the tree it
//! makes is what the traversals recurse over and what the instance file pads.

/// One node of the parsed process, before identifiers exist.
enum Ast {
    Task {
        name: String,
        duration: i64,
        /// Indices into the collected name table, with the value of each.
        impact: Vec<(usize, f64)>,
    },
    Op {
        kind: &'static str,
        prob: Option<f64>,
        low: Box<Ast>,
        high: Box<Ast>,
        size: Size,
    },
    /// A saved process spliced in place of a reference: one leaf of the
    /// process that names it, a whole subtree of the assembled one.
    Sub {
        name: String,
        shown: String,
        inner: Box<Ast>,
        size: Size,
    },
    /// A loop over a region, run again with the probability after each pass.
    Loop {
        prob: f64,
        inner: Box<Ast>,
        size: Size,
    },
}

impl Ast {
    fn size(&self) -> Size {
        match self {
            Ast::Task { .. } => Size::TASK,
            Ast::Op { size, .. } | Ast::Sub { size, .. } | Ast::Loop { size, .. } => *size,
        }
    }
}

/// What a subtree holds, kept on every node as it is built.
#[derive(Clone, Copy, Debug)]
struct Size {
    /// The levels under its root, a reference counting one: how far the
    /// traversals of the tree recurse.
    height: usize,
    /// The most loops inside one another.
    nest: usize,
    nodes: u64,
    tasks: u64,
    /// The sum over the nodes of their depth under the root, which is what
    /// the indentation of the instance file costs.
    depths: u64,
}

impl Size {
    const TASK: Size = Size { height: 0, nest: 0, nodes: 1, tasks: 1, depths: 0 };

    fn op(low: Size, high: Size) -> Size {
        Size {
            height: 1 + low.height.max(high.height),
            nest: low.nest.max(high.nest),
            nodes: low.nodes + high.nodes + 1,
            tasks: low.tasks + high.tasks,
            depths: low.depths + high.depths + low.nodes + high.nodes,
        }
    }

    fn reference(inner: Size) -> Size {
        Size { height: inner.height + 1, depths: inner.depths + inner.nodes, ..inner }
    }

    fn looped(inner: Size) -> Size {
        Size {
            height: inner.height + 1,
            nest: inner.nest + 1,
            nodes: inner.nodes + 1,
            tasks: inner.tasks,
            depths: inner.depths + inner.nodes,
        }
    }

    /// The bytes the instance file takes, four lines a node with four bytes
    /// of padding a line per level, and about a hundred of text.
    fn written(&self) -> u64 {
        16 * self.depths + 128 * self.nodes
    }
}

#[derive(Debug)]
pub struct Parsed {
    pub yaml: String,
    /// The identifier every node shows, by assembled identifier, index 0 a
    /// filler: the own identifier of the node in the process that wrote it,
    /// prefixed by the identifiers of the references that led there.
    pub labels: Vec<String>,
    /// The saved processes spliced in, parents before their children.
    pub subs: Vec<Sub>,
}

/// One saved process spliced into the assembly.
#[derive(Clone, Debug)]
pub struct Sub {
    /// The assembled identifier of its root.
    pub root: u32,
    /// The identifier it shows, that of the leaf that refers to it.
    pub label: String,
    /// The name it is saved under, every link of an alias chain joined.
    pub name: String,
    /// The links of that chain, one name each: the name it is referred to by
    /// and, when its text is itself one reference, the names that one leads to.
    pub links: Vec<String>,
    /// The name it is shown under, the saved name unless `[shown]` was given.
    pub shown: String,
    /// How many references lead to it, zero for one of the top process.
    pub depth: usize,
}

/// What a parse carries beside the scanner: the table of the impact names,
/// one over every process spliced in so that one name is one impact
/// everywhere, the resolver of a reference, and the chain of the references
/// being resolved, against a cycle.
struct Ctx<'r> {
    names: Vec<String>,
    /// Where a name sits in the table, so that reading a task with many
    /// impacts, once per reference, does not walk the table for each.
    index: std::collections::HashMap<String, usize>,
    resolver: Option<&'r mut dyn FnMut(&str) -> Result<String, String>>,
    chain: Vec<String>,
    /// The nodes built so far, a text referred to many times once per
    /// reference: what the parse has cost in time and in memory.
    built: u64,
    /// The bytes of the texts referred to, read so far.
    read: u64,
}

impl Ctx<'_> {
    /// One node just built: the bounds of the parser, read on the tree.
    fn count(&mut self, size: Size) -> Result<Size, String> {
        self.built += 1;
        if self.built > MAX_NODES || size.nodes > MAX_NODES {
            return Err(format!("the process assembles to more than {MAX_NODES} nodes"));
        }
        if size.height > MAX_DEPTH {
            return Err(format!("the term is deeper than {MAX_DEPTH}, parentheses and sequence folds together"));
        }
        if size.nest > MAX_NEST {
            return Err(format!("the loops are nested deeper than {MAX_NEST}"));
        }
        if size.written() > MAX_WRITTEN {
            return Err(format!("the process assembles to more than {} megabytes of instance file", MAX_WRITTEN >> 20));
        }
        Ok(size)
    }
}

struct Scanner<'a> {
    text: &'a [u8],
    at: usize,
}

impl<'a> Scanner<'a> {
    fn new(text: &'a str) -> Self {
        Scanner { text: text.as_bytes(), at: 0 }
    }

    fn skip(&mut self) {
        loop {
            while self.at < self.text.len() && self.text[self.at].is_ascii_whitespace() {
                self.at += 1;
            }
            if self.at < self.text.len() && self.text[self.at] == b'#' {
                while self.at < self.text.len() && self.text[self.at] != b'\n' {
                    self.at += 1;
                }
            } else {
                return;
            }
        }
    }

    fn peek(&mut self) -> Option<u8> {
        self.skip();
        self.text.get(self.at).copied()
    }

    fn eat(&mut self, c: u8) -> Result<(), String> {
        self.skip();
        if self.text.get(self.at) == Some(&c) {
            self.at += 1;
            Ok(())
        } else {
            Err(format!(
                "expected {:?} at byte {}, found {}",
                c as char,
                self.at,
                self.found(self.at)
            ))
        }
    }

    /// What sits at a byte position, for an error message: the whole UTF-8
    /// character, or the end of the input.
    fn found(&self, at: usize) -> String {
        match self.text.get(at) {
            None => "the end of the input".to_string(),
            Some(_) => {
                let end = (at + 4).min(self.text.len());
                let c = String::from_utf8_lossy(&self.text[at..end])
                    .chars()
                    .next()
                    .unwrap_or('\u{FFFD}');
                format!("{c:?}")
            }
        }
    }

    fn name(&mut self) -> Result<String, String> {
        self.skip();
        let start = self.at;
        while self.at < self.text.len()
            && (self.text[self.at].is_ascii_alphanumeric() || self.text[self.at] == b'_')
        {
            self.at += 1;
        }
        if self.at == start {
            return Err(format!("expected a name at byte {start}"));
        }
        Ok(String::from_utf8_lossy(&self.text[start..self.at]).into_owned())
    }

    /// The name of a saved process after `@`: letters, digits, `_`, `.` and
    /// `-`, or anything at all between double quotes.
    fn refname(&mut self) -> Result<String, String> {
        let start = self.at;
        if self.text.get(self.at) == Some(&b'"') {
            self.at += 1;
            let from = self.at;
            while self.at < self.text.len() && self.text[self.at] != b'"' {
                self.at += 1;
            }
            if self.at >= self.text.len() {
                return Err(format!("the quoted name at byte {start} never closes"));
            }
            let name = String::from_utf8_lossy(&self.text[from..self.at]).trim().to_string();
            self.at += 1;
            if name.is_empty() {
                return Err(format!("the quoted name at byte {start} is empty"));
            }
            return Ok(name);
        }
        while self.at < self.text.len()
            && (self.text[self.at].is_ascii_alphanumeric()
                || matches!(self.text[self.at], b'_' | b'.' | b'-'))
        {
            self.at += 1;
        }
        if self.at == start {
            return Err(format!("expected the name of a saved process after @ at byte {start}"));
        }
        Ok(String::from_utf8_lossy(&self.text[start..self.at]).into_owned())
    }

    /// The text up to the closing bracket of `[shown]`, the bracket consumed.
    fn until_bracket(&mut self) -> Result<String, String> {
        let start = self.at;
        while self.at < self.text.len() && self.text[self.at] != b']' && self.text[self.at] != b'\n' {
            self.at += 1;
        }
        if self.text.get(self.at) != Some(&b']') {
            return Err(format!("the [shown] name opened at byte {start} never closes on its line"));
        }
        let shown = String::from_utf8_lossy(&self.text[start..self.at]).trim().to_string();
        self.at += 1;
        Ok(shown)
    }

    fn number(&mut self) -> Result<f64, String> {
        self.skip();
        let start = self.at;
        while self.at < self.text.len()
            && (self.text[self.at].is_ascii_digit()
                || matches!(self.text[self.at], b'.' | b'e' | b'E' | b'+' | b'-'))
        {
            // a minus is part of an exponent alone; the grammar has no
            // negative quantities
            if self.text[self.at] == b'-'
                && !matches!(self.text.get(self.at.wrapping_sub(1)), Some(b'e') | Some(b'E'))
            {
                break;
            }
            self.at += 1;
        }
        let s = std::str::from_utf8(&self.text[start..self.at]).unwrap_or("");
        s.parse::<f64>().map_err(|_| {
            if s.is_empty() {
                format!("expected a number at byte {start}, found {}", self.found(start))
            } else {
                format!("expected a number at byte {start}, found {s:?}")
            }
        })
    }

    /// A positive integer written in digits alone: no sign, no point, no
    /// exponent, and it must fit, so nothing is rounded or saturated behind
    /// the writer of the text.
    fn integer(&mut self) -> Result<i64, String> {
        self.skip();
        let start = self.at;
        while self.at < self.text.len() && self.text[self.at].is_ascii_digit() {
            self.at += 1;
        }
        if self.at == start {
            return Err(format!(
                "expected an integer at byte {start}, found {}",
                self.found(start)
            ));
        }
        if matches!(self.text.get(self.at), Some(b'.') | Some(b'e') | Some(b'E')) {
            return Err(format!(
                "the integer at byte {start} carries a point or an exponent, and a duration is written in digits alone"
            ));
        }
        std::str::from_utf8(&self.text[start..self.at])
            .unwrap_or("")
            .parse::<i64>()
            .map_err(|_| format!("the integer at byte {start} does not fit in 64 bits"))
    }
}

/// The whole term, parentheses and sequence chain together, may reach this
/// deep and no deeper: the traversals of the tree recurse, and a bound here
/// turns what would abort the process into an error message. It holds of the
/// tree as it is built, references included, and not only of the descent
/// through the text, since a fold sits above the regions to its left.
const MAX_DEPTH: usize = 2000;

/// How many loops may sit inside one another. A run of a nest passes the bound
/// on the passes to the power of its depth through the innermost region, and
/// the bounds of a nest grow with the square of its depth in long rationals;
/// the benchmark with loops nests five at most.
const MAX_NEST: usize = 16;

/// The nodes of a process assembled, and the nodes built on the way to it. A
/// text of the largest body the page accepts, written out, holds fewer.
const MAX_NODES: u64 = 1 << 17;

/// The instance file of a process assembled, in bytes: a chain of two thousand
/// tasks takes a quarter of this.
const MAX_WRITTEN: u64 = 1 << 28;

/// The bytes read through references, a text referred to many times once per
/// reference.
const MAX_READ: u64 = 1 << 28;

/// The values of the impacts of a process assembled: one per task and impact
/// name, the names being one table over the whole assembly.
const MAX_CELLS: u64 = 1 << 22;

fn parse_process(
    sc: &mut Scanner,
    ctx: &mut Ctx<'_>,
    depth: usize,
) -> Result<Ast, String> {
    let mut low = parse_region(sc, ctx, depth)?;
    sc.skip();
    if sc.text.get(sc.at) == Some(&b',') {
        // a chain of sequences, folded to the left; every fold deepens the
        // left spine of the tree, so the folds count against the depth too
        let mut folds = 0usize;
        while sc.text.get(sc.at) == Some(&b',') {
            sc.at += 1;
            folds += 1;
            if depth + folds > MAX_DEPTH {
                return Err(format!("the term is deeper than {MAX_DEPTH}, parentheses and sequence folds together"));
            }
            let high = parse_region(sc, ctx, depth + folds)?;
            let size = ctx.count(Size::op(low.size(), high.size()))?;
            low = Ast::Op {
                kind: "sequence",
                prob: None,
                low: Box::new(low),
                high: Box::new(high),
                size,
            };
            sc.skip();
        }
        return Ok(low);
    }
    let (kind, prob) = if sc.text.get(sc.at..sc.at + 2) == Some(b"||") {
        sc.at += 2;
        ("parallel", None)
    } else if sc.text.get(sc.at) == Some(&b'^') {
        sc.at += 1;
        if sc.text.get(sc.at) == Some(&b'[') {
            sc.at += 1;
            let p = sc.number()?;
            if !(p > 0.0 && p < 1.0) {
                return Err(format!("a nature node carries {p}, and a probability lies in (0,1)"));
            }
            sc.eat(b']')?;
            ("nature", Some(p))
        } else {
            ("choice", None)
        }
    } else {
        // a single region: the whole process may be one task, and a
        // parenthesised region may wrap another
        return Ok(low);
    };
    let high = parse_region(sc, ctx, depth)?;
    let size = ctx.count(Size::op(low.size(), high.size()))?;
    Ok(Ast::Op { kind, prob, low: Box::new(low), high: Box::new(high), size })
}

/// A reference `@saved` or `@saved[shown]`: the saved text is parsed in
/// place, through the resolver, its impacts into the one table, its
/// references resolved in turn.
fn parse_reference(sc: &mut Scanner, ctx: &mut Ctx<'_>, depth: usize) -> Result<Ast, String> {
    sc.eat(b'@')?;
    let name = sc.refname()?;
    // the [shown] name, which may sit a space away; nothing else of the
    // grammar opens on a bracket after a region
    let mark = sc.at;
    sc.skip();
    let shown = if sc.text.get(sc.at) == Some(&b'[') {
        sc.at += 1;
        let shown = sc.until_bracket()?;
        if shown.is_empty() { name.clone() } else { shown }
    } else {
        sc.at = mark;
        name.clone()
    };
    // a reference carries neither a duration nor impacts, it takes them from
    // the saved process: `(@name, 3, {...})` is refused with the reason and
    // not with a puzzle about a parenthesis
    let mark = sc.at;
    sc.skip();
    if sc.text.get(sc.at) == Some(&b',') {
        let mut j = sc.at + 1;
        while j < sc.text.len() && sc.text[j].is_ascii_whitespace() {
            j += 1;
        }
        if sc.text.get(j).map_or(false, |c| c.is_ascii_digit()) {
            return Err(format!(
                "@{name} is followed by a duration, and a reference carries neither a duration nor impacts, taking both from the saved process: write @{name} alone"
            ));
        }
    }
    sc.at = mark;
    if ctx.chain.iter().any(|c| c == &name) {
        let mut cycle = ctx.chain.clone();
        cycle.push(name.clone());
        return Err(format!("the references form a cycle: {}", cycle.join(" > ")));
    }
    let text = match ctx.resolver.as_mut() {
        Some(resolve) => resolve(&name)?,
        None => {
            return Err(format!(
                "the process refers to @{name}, and a reference is resolved against the saved processes of the page, or on the command line through --saved F"
            ))
        }
    };
    ctx.read += text.len() as u64;
    if ctx.read > MAX_READ {
        return Err(format!("the references read more than {} megabytes of text", MAX_READ >> 20));
    }
    ctx.chain.push(name.clone());
    let mut inner = Scanner::new(&text);
    // the saved process an error comes from is named once, the innermost
    let ast = parse_process(&mut inner, ctx, depth + 1).map_err(|e| {
        if e.starts_with("in the saved process ") { e } else { format!("in the saved process {name}: {e}") }
    })?;
    inner.skip();
    if inner.at != inner.text.len() {
        return Err(format!("in the saved process {name}: trailing input at byte {}", inner.at));
    }
    ctx.chain.pop();
    let size = ctx.count(Size::reference(ast.size()))?;
    Ok(Ast::Sub { name, shown, inner: Box::new(ast), size })
}

fn parse_region(
    sc: &mut Scanner,
    ctx: &mut Ctx<'_>,
    depth: usize,
) -> Result<Ast, String> {
    if depth > MAX_DEPTH {
        return Err(format!("the term is deeper than {MAX_DEPTH}, parentheses and sequence folds together"));
    }
    // a reference is a region of its own, with no parentheses of its own
    if sc.peek() == Some(b'@') {
        return parse_reference(sc, ctx, depth);
    }
    sc.eat(b'(')?;
    // a task opens on a name, a wrapped process on another parenthesis or on
    // a reference, a loop on its probability
    if matches!(sc.peek(), Some(b'(') | Some(b'@')) {
        let inner = parse_process(sc, ctx, depth + 1)?;
        sc.eat(b')')?;
        return Ok(inner);
    }
    if matches!(sc.peek(), Some(c) if c.is_ascii_digit() || c == b'.') {
        let at = sc.at;
        let p = sc.number()?;
        if !(p > 0.0 && p < 1.0) {
            return Err(format!(
                "the loop at byte {at} carries {p}, and the probability of running its region again lies in (0,1)"
            ));
        }
        let inner = parse_process(sc, ctx, depth + 1)?;
        sc.eat(b')')?;
        let size = ctx.count(Size::looped(inner.size()))?;
        return Ok(Ast::Loop { prob: p, inner: Box::new(inner), size });
    }
    // a task: name, duration, and an optional impact map
    let name = sc.name()?;
    sc.eat(b',')?;
    let d = sc.integer()?;
    if d <= 0 {
        return Err(format!("the task {name} carries the duration {d}, and a duration is a positive integer"));
    }
    // the state holds the progress of a task in a byte: a longer duration
    // would be written and refused by every command that reads the file
    if d > 127 {
        return Err(format!("the task {name} carries the duration {d}, and a duration is at most 127, the state holding a progress in a byte"));
    }
    let mut impact: Vec<(usize, f64)> = Vec::new();
    let mut named = std::collections::HashSet::new();
    sc.skip();
    if sc.peek() == Some(b',') {
        sc.eat(b',')?;
        sc.eat(b'{')?;
        if sc.peek() != Some(b'}') {
            loop {
                let iname = sc.name()?;
                sc.eat(b':')?;
                let v = sc.number()?;
                if v <= 0.0 {
                    return Err(format!(
                        "the impact {iname} of {name} is {v}, and the map names only what is strictly positive; leave a zero out"
                    ));
                }
                let idx = match ctx.index.get(&iname) {
                    Some(i) => *i,
                    None => {
                        ctx.names.push(iname.clone());
                        ctx.index.insert(iname.clone(), ctx.names.len() - 1);
                        ctx.names.len() - 1
                    }
                };
                if !named.insert(idx) {
                    return Err(format!("the task {name} names the impact {iname} twice"));
                }
                impact.push((idx, v));
                if sc.peek() == Some(b',') {
                    sc.eat(b',')?;
                } else {
                    break;
                }
            }
        }
        sc.eat(b'}')?;
    }
    sc.eat(b')')?;
    ctx.count(Size::TASK)?;
    Ok(Ast::Task { name, duration: d, impact })
}

fn fmt_number(v: f64) -> String {
    if v.fract() == 0.0 && v.abs() < 1e15 {
        format!("{}", v as i64)
    } else {
        format!("{v}")
    }
}

fn emit(ast: &Ast, k: usize, next: &mut u32, indent: usize, out: &mut String) {
    let pad = "  ".repeat(indent);
    match ast {
        // a spliced process is its tree: the engine never sees the seam
        Ast::Sub { inner, .. } => emit(inner, k, next, indent, out),
        Ast::Loop { prob, inner, .. } => {
            // in-order with one child: the region first, then the node
            let my_id = *next + inner.size().nodes as u32 + 1;
            out.push_str(&format!("{pad}- loop: {prob}\n"));
            out.push_str(&format!("{pad}  id: {my_id}\n"));
            out.push_str(&format!("{pad}  low:\n"));
            emit(inner, k, next, indent + 2, out);
            *next += 1;
        }
        Ast::Task { name, duration, impact } => {
            let mut v = vec![0.0f64; k];
            for (i, x) in impact {
                v[*i] = *x;
            }
            let list: Vec<String> = v.iter().map(|x| fmt_number(*x)).collect();
            out.push_str(&format!("{pad}- task: {name}\n"));
            out.push_str(&format!("{pad}  id: {}\n", next_id(next)));
            out.push_str(&format!("{pad}  duration: {duration}\n"));
            out.push_str(&format!("{pad}  impact: [{}]\n", list.join(", ")));
        }
        Ast::Op { kind, prob, low, high, .. } => {
            // in-order: the low subtree first, the node, the high subtree;
            // the node line prints before its children, so its identifier is
            // reserved by the count of the low subtree
            let my_id = *next + low.size().nodes as u32 + 1;
            match prob {
                Some(p) => out.push_str(&format!("{pad}- {kind}: {p}\n")),
                None => out.push_str(&format!("{pad}- {kind}:\n")),
            }
            out.push_str(&format!("{pad}  id: {my_id}\n"));
            out.push_str(&format!("{pad}  low:\n"));
            emit(low, k, next, indent + 2, out);
            *next += 1; // the node itself, between the two subtrees
            out.push_str(&format!("{pad}  high:\n"));
            emit(high, k, next, indent + 2, out);
        }
    }
}

fn next_id(next: &mut u32) -> u32 {
    *next += 1;
    *next
}

/// The identifier every node shows and the subprocesses spliced in, by one
/// walk that numbers the assembled tree exactly as `emit` does, in-order,
/// and beside it every process by its own nodes, a spliced process counting
/// one leaf of the process that refers to it.
fn walk(
    ast: &Ast,
    next: &mut u32,
    own: &mut u32,
    prefix: &str,
    depth: usize,
    labels: &mut Vec<String>,
    subs: &mut Vec<Sub>,
) -> u32 {
    match ast {
        Ast::Task { .. } => {
            *next += 1;
            *own += 1;
            labels[*next as usize] = format!("{prefix}{own}");
            *next
        }
        Ast::Op { low, high, .. } => {
            walk(low, next, own, prefix, depth, labels, subs);
            *next += 1;
            *own += 1;
            let id = *next;
            labels[id as usize] = format!("{prefix}{own}");
            walk(high, next, own, prefix, depth, labels, subs);
            id
        }
        Ast::Loop { inner, .. } => {
            walk(inner, next, own, prefix, depth, labels, subs);
            *next += 1;
            *own += 1;
            let id = *next;
            labels[id as usize] = format!("{prefix}{own}");
            id
        }
        Ast::Sub { name, shown, inner, .. } => {
            *own += 1;
            let label = format!("{prefix}{own}");
            let at = subs.len();
            subs.push(Sub {
                root: 0,
                label: label.clone(),
                name: name.clone(),
                links: Vec::new(),
                shown: shown.clone(),
                depth,
            });
            // a saved text that is itself one reference is an alias: it has
            // no node of its own, so the two would share a root and one box;
            // the chain is one subprocess, named after every link, and the
            // nodes below it carry the leaf of every link in their labels
            let mut prefix = format!("{label}-");
            let mut chain = name.clone();
            let mut links = vec![name.clone()];
            let mut cur: &Ast = inner;
            while let Ast::Sub { name: n2, inner: i2, .. } = cur {
                chain = format!("{chain} = {n2}");
                links.push(n2.clone());
                prefix = format!("{prefix}1-");
                cur = i2;
            }
            subs[at].name = chain;
            subs[at].links = links;
            let mut inner_own = 0u32;
            let root = walk(cur, next, &mut inner_own, &prefix, depth + 1, labels, subs);
            // the root of the spliced tree is, seen from the process that
            // refers to it, the leaf: it shows that identifier, the one on
            // the box, so that a decision naming it can be found there
            labels[root as usize] = label;
            subs[at].root = root;
            root
        }
    }
}

fn parse_all(
    text: &str,
    name: Option<&str>,
    resolver: Option<&mut dyn FnMut(&str) -> Result<String, String>>,
) -> Result<Parsed, String> {
    let mut sc = Scanner::new(text);
    // the process being written, when it has a name, heads the chain: a
    // reference to its own name is a cycle at once, saved or not
    let chain = name.map(|n| vec![n.to_string()]).unwrap_or_default();
    let mut ctx = Ctx { names: Vec::new(), index: Default::default(), resolver, chain, built: 0, read: 0 };
    let ast = parse_process(&mut sc, &mut ctx, 0)?;
    sc.skip();
    if sc.at != sc.text.len() {
        return Err(format!("trailing input at byte {}", sc.at));
    }
    let names = ctx.names;
    if names.is_empty() {
        return Err("no impact is named anywhere; name at least one, the dimension being at least one".to_string());
    }
    let (nodes, tasks) = (ast.size().nodes as u32, ast.size().tasks as u32);
    let k = names.len();
    if tasks as u64 * k as u64 > MAX_CELLS {
        return Err(format!(
            "the process assembles to {tasks} tasks over {k} impacts, more than the {MAX_CELLS} values of an instance"
        ));
    }
    let mut out = String::new();
    out.push_str(&format!("impact_names: [{}]\n", names.join(", ")));
    out.push_str(&format!("dimensions: {k}\n"));
    out.push_str(&format!("tasks: {tasks}\n"));
    out.push_str(&format!("nodes: {nodes}\n"));
    out.push_str("tree:\n");
    let mut next = 0u32;
    emit(&ast, k, &mut next, 1, &mut out);
    let mut labels = vec![String::new(); nodes as usize + 1];
    let mut subs = Vec::new();
    let (mut next, mut own) = (0u32, 0u32);
    walk(&ast, &mut next, &mut own, "", 0, &mut labels, &mut subs);
    debug_assert_eq!(next, nodes);
    Ok(Parsed { yaml: out, labels, subs })
}

/// Parses the grammar and writes the instance file, identifiers in-order and
/// one-based, `impact_names` in the order of first appearance. A reference
/// to a saved process is an error here: see `to_yaml_with`.
pub fn to_yaml(text: &str) -> Result<Parsed, String> {
    parse_all(text, None, None)
}

/// The same, with every reference `@saved` resolved through `resolver`, which
/// gives the text saved under a name, and spliced in place.
pub fn to_yaml_with(
    text: &str,
    resolver: &mut dyn FnMut(&str) -> Result<String, String>,
) -> Result<Parsed, String> {
    parse_all(text, None, Some(resolver))
}

/// The same for a process being written under `name`: a reference to that
/// name is a cycle at once, whatever the store holds under it.
pub fn to_yaml_as(
    text: &str,
    name: &str,
    resolver: &mut dyn FnMut(&str) -> Result<String, String>,
) -> Result<Parsed, String> {
    parse_all(text, Some(name), Some(resolver))
}

/// The names a text refers to, as the parser reads them: a comment is no
/// reference, a quoted name is read whole and trimmed, a `[shown]` name is
/// not looked into. It reads the references alone, with no grammar around
/// them, so a text that does not parse still has its references, and nothing
/// is resolved or assembled. A name referred to twice is listed twice.
pub fn references_of(text: &str) -> Vec<String> {
    let mut sc = Scanner::new(text);
    let mut out = Vec::new();
    while let Some(c) = sc.peek() {
        sc.at += 1;
        if c != b'@' {
            continue;
        }
        // an unclosed or an empty name is an error of the grammar, and names
        // nothing here
        if let Ok(name) = sc.refname() {
            out.push(name);
            let mark = sc.at;
            sc.skip();
            if sc.text.get(sc.at) == Some(&b'[') {
                sc.at += 1;
                let _ = sc.until_bracket();
            } else {
                sc.at = mark;
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store(name: &str) -> Result<String, String> {
        match name {
            "Repair" => Ok("( (Mend, 3, {euro: 20}) ^ (Redo, 2, {euro: 40, hours: 6}) )".to_string()),
            "Ship" => Ok("(Pack, 1, {hours: 1}) , @Repair[Again]".to_string()),
            "Loop" => Ok("(A, 1, {x: 1}) , @Loop".to_string()),
            _ => Err(format!("no process is saved as {name:?}")),
        }
    }

    #[test]
    fn a_reference_splices_the_saved_process_and_labels_its_nodes() {
        let mut r = store;
        let p = to_yaml_with("( (Intake, 1, {euro: 10}) , @Repair[Fix] , @Ship )", &mut r).unwrap();
        // the assembled tree: Intake(1) seq(2) [Mend(3) choice(4) Redo(5)] seq(6) [Pack(7) seq(8) [Mend(9) choice(10) Redo(11)]]
        assert!(p.yaml.contains("nodes: 11"));
        assert_eq!(p.labels[1], "1");
        assert_eq!(p.labels[2], "2");
        assert_eq!(p.labels[3], "3-1");
        assert_eq!(p.labels[4], "3", "the root of a spliced tree shows the leaf that refers to it");
        assert_eq!(p.labels[5], "3-3");
        assert_eq!(p.labels[6], "4");
        assert_eq!(p.labels[7], "5-1");
        assert_eq!(p.labels[8], "5");
        assert_eq!(p.labels[9], "5-3-1");
        assert_eq!(p.labels[10], "5-3");
        assert_eq!(p.labels[11], "5-3-3");
        let subs: Vec<(String, String, String, u32, usize)> = p
            .subs
            .iter()
            .map(|s| (s.label.clone(), s.name.clone(), s.shown.clone(), s.root, s.depth))
            .collect();
        assert_eq!(subs[0], ("3".to_string(), "Repair".to_string(), "Fix".to_string(), 4, 0));
        assert_eq!(subs[1], ("5".to_string(), "Ship".to_string(), "Ship".to_string(), 8, 0));
        assert_eq!(subs[2], ("5-3".to_string(), "Repair".to_string(), "Again".to_string(), 10, 1));
        // the impacts merged by name, in the order of first appearance
        assert!(p.yaml.starts_with("impact_names: [euro, hours]\n"));
    }

    #[test]
    fn an_alias_chain_is_one_sub_process_named_after_every_link() {
        let mut r = |name: &str| -> Result<String, String> {
            match name {
                "Y" => Ok("(a, 1, {x: 1}) , (b, 1, {x: 2})".to_string()),
                "X" => Ok("@Y [Seen]".to_string()),
                "W" => Ok("(@X)".to_string()),
                _ => Err(format!("no process is saved as {name:?}")),
            }
        };
        let p = to_yaml_with("(c, 1, {x: 1}) , @W", &mut r).unwrap();
        assert_eq!(p.subs.len(), 1);
        assert_eq!(p.subs[0].label, "3");
        assert_eq!(p.subs[0].name, "W = X = Y");
        assert_eq!(p.subs[0].root, 4);
        assert_eq!(p.labels[3], "3-1-1-1");
        assert_eq!(p.labels[4], "3");
        assert_eq!(p.labels[5], "3-1-1-3");
    }

    #[test]
    fn a_loop_is_a_probability_after_the_parenthesis() {
        let p = to_yaml("(A, 1, {x: 1}) , (0.5 (B, 2, {x: 3}) , (C, 1, {x: 1}))").unwrap();
        // A(1) seq(2) [B(3) seq(4) C(5)] loop(6): one node more than a full binary tree
        assert!(p.yaml.contains("nodes: 6"), "{}", p.yaml);
        assert!(p.yaml.contains("- loop: 0.5\n        id: 6\n"), "{}", p.yaml);
        assert_eq!(p.labels[6], "6");
        let e = to_yaml("(1.5 (B, 2, {x: 3}))").unwrap_err();
        assert!(e.contains("(0,1)"), "{e}");
        assert!(to_yaml("(0.25 (B, 2, {x: 3}))").is_ok());
        assert!(to_yaml("( (A, 1, {x: 1}) || (0.1 (B, 2, {x: 3})) )").is_ok());
    }

    #[test]
    fn a_reference_with_a_duration_is_refused_with_the_reason() {
        let mut r = store;
        let e = to_yaml_with("(@Repair, 3, {euro: 1})", &mut r).unwrap_err();
        assert!(e.contains("neither a duration nor impacts"), "{e}");
        assert!(to_yaml_with("(@Repair), (Check, 1, {euro: 1})", &mut r).is_ok());
        assert!(to_yaml_with("@Repair, @Ship", &mut r).is_ok());
    }

    #[test]
    fn a_process_referring_to_its_own_name_is_a_cycle_before_it_is_saved() {
        let mut r = store;
        let e = to_yaml_as("(A, 1, {x: 1}) , @Repair", "Repair", &mut r).unwrap_err();
        assert!(e.contains("cycle") && e.contains("Repair > Repair"), "{e}");
        assert!(to_yaml_as("(A, 1, {x: 1}) , @Repair", "Other", &mut r).is_ok());
    }

    /// A deep term recurses through the parser, and in a thread of the stack
    /// the server gives its connections, not of the one a test gets.
    fn with_the_stack_of_a_connection(test: fn()) {
        std::thread::Builder::new().stack_size(64 << 20).spawn(test).unwrap().join().unwrap();
    }

    #[test]
    fn the_bound_on_the_depth_holds_of_the_tree_and_across_references() {
        with_the_stack_of_a_connection(the_bound_on_the_depth);
    }

    fn the_bound_on_the_depth() {
        // two thousand folds are the bound, one more is refused
        let chain = |n: usize| format!("(A, 1, {{x: 1}}){}", ", (A, 1, {x: 1})".repeat(n));
        assert!(to_yaml(&chain(2000)).is_ok());
        assert!(to_yaml(&chain(2001)).unwrap_err().contains("deeper"));
        // a first region deep in choices, under folds that count only the
        // regions to their right
        let d = 1950;
        let comb = format!(
            "{}(A, 1, {{x: 2}}){}{}",
            "((A, 1, {x: 1}) ^ ".repeat(d),
            ")".repeat(d),
            ", (A, 1, {x: 1})".repeat(d)
        );
        assert!(to_yaml(&comb).unwrap_err().contains("deeper"));
        // the same through a chain of saved processes, each below the bound
        let mut r = |name: &str| -> Result<String, String> {
            let i: usize = name[1..].parse().unwrap();
            if i == 1950 {
                return Ok("(A, 1, {x: 1})".to_string());
            }
            Ok(format!("@p{}{}", i + 1, ", (A, 1, {x: 1})".repeat(1998 - i)))
        };
        assert!(to_yaml_with("@p0", &mut r).unwrap_err().contains("deeper"));
    }

    #[test]
    fn a_nest_of_loops_is_bounded_through_references_too() {
        let nest = |n: usize| format!("{}(A, 1, {{x: 1}}){}", "(0.5 ".repeat(n), ")".repeat(n));
        assert!(to_yaml(&nest(MAX_NEST)).is_ok());
        assert!(to_yaml(&nest(MAX_NEST + 1)).unwrap_err().contains("nested"));
        let inner = nest(MAX_NEST);
        let mut r = |_: &str| Ok(inner.clone());
        assert!(to_yaml_with("(0.5 @n)", &mut r).unwrap_err().contains("nested"));
    }

    #[test]
    fn a_process_that_assembles_past_the_bounds_is_refused_as_it_is_built() {
        with_the_stack_of_a_connection(a_process_past_the_bounds);
    }

    fn a_process_past_the_bounds() {
        // a chain of texts that doubles at every link, thirty links under a
        // body of a few bytes: the parse ends long before the last one
        let mut r = |name: &str| -> Result<String, String> {
            let i: usize = name[1..].parse().unwrap();
            if i == 0 {
                return Ok("(A, 1, {x: 1})".to_string());
            }
            Ok(format!("@a{}, @a{}", i - 1, i - 1))
        };
        let e = to_yaml_with("@a30", &mut r).unwrap_err();
        assert!(e.contains("nodes"), "{e}");
        // a balanced text of small references passes the count of the nodes
        let mut wide = "@t".to_string();
        for _ in 0..17 {
            wide = format!("({wide},{wide})");
        }
        let mut t = |_: &str| Ok("(A, 1, {x: 1})".to_string());
        assert!(to_yaml_with(&wide, &mut t).unwrap_err().contains("nodes"));
        // the padding of the instance file: a large region under a deep comb
        let region = format!("({})", vec!["(A, 1, {x: 1})"; 2].join(","));
        let mut b = region.clone();
        for _ in 0..12 {
            b = format!("({b},{b})");
        }
        let deep = format!("{}{b}{}", "((A, 1, {x: 1}) ^ ".repeat(1900), ")".repeat(1900));
        assert!(to_yaml(&deep).unwrap_err().contains("megabytes"), "a region of eight thousand tasks under a comb of 1900");
        // many impacts over many tasks, the one task referred to many times
        let map: Vec<String> = (0..4000).map(|i| format!("n{i}: 1")).collect();
        let task = format!("(A, 1, {{{}}})", map.join(", "));
        let mut b = "@m".to_string();
        for _ in 0..11 {
            b = format!("({b},{b})");
        }
        let mut m = |_: &str| Ok(task.clone());
        assert!(to_yaml_with(&b, &mut m).unwrap_err().contains("values"));
    }

    #[test]
    fn the_references_of_a_text_are_read_as_the_parser_reads_them() {
        assert_eq!(references_of("(A, 1, {x: 1})"), Vec::<String>::new());
        assert_eq!(references_of("@a , @b.c-d[Shown @not this one], @a"), ["a", "b.c-d", "a"]);
        assert_eq!(references_of("# @comment\n@a # @no\n, @\" spaced # name \"[x]"), ["a", "spaced # name"]);
        // a name read to its end, as a prefix of another counts for nothing
        assert_eq!(references_of("@tests2"), ["tests2"]);
        // the grammar around them does not matter
        assert_eq!(references_of("(oops @a ) ) @b ("), ["a", "b"]);
        assert_eq!(references_of("@ @\"\" @\"never closes"), Vec::<String>::new());
        assert_eq!(references_of("@a [never closes\n@b"), ["a", "b"]);
    }

    #[test]
    fn a_cycle_and_an_unresolved_reference_are_errors() {
        let mut r = store;
        let e = to_yaml_with("@Loop", &mut r).unwrap_err();
        assert!(e.contains("cycle"), "{e}");
        let e = to_yaml("(A, 1, {x: 1}) , @Repair").unwrap_err();
        assert!(e.contains("saved processes"), "{e}");
        let e = to_yaml_with("@Nowhere", &mut r).unwrap_err();
        assert!(e.contains("Nowhere"), "{e}");
    }
}
