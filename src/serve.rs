//! `sdcpi serve`: one page on localhost, a mask over the command line.
//!
//! The artifact of a tool paper has to run on the virtual machine the
//! evaluation provides or inside a container, so an installer buys nothing
//! and a page the binary serves itself buys everything: the user installs
//! nothing that is not already there, the three desktop systems come for
//! free, and a container needs one port.
//!
//! The server is written here rather than taken from a crate. It answers on
//! the loopback address, serves one page held as a constant and a few
//! endpoints, and runs the search in this process rather than shelling out to
//! itself. Every connection gets a thread of its own, so that a browser
//! holding a connection open and silent, or a search of half a minute, does
//! not keep the drawing of the diagram waiting; the searches themselves run
//! one at a time, behind a lock, the search being what takes the processors.
//! A hundred and fifty lines of `TcpListener` are enough for that, and the
//! whole of what makes a web server hard, the routing, the static files, the
//! sessions, is absent by construction.
//!
//! What guards it, since it runs a computation on request:
//!   * it binds `127.0.0.1` and never `0.0.0.0`, so nothing outside the
//!     machine reaches it;
//!   * a browser on the machine is inside that address, so a request whose
//!     `Host` is no name of the loopback, or whose `Origin` is not the page
//!     it asks, is refused: a page open on any other site could otherwise
//!     rewrite the store or start a search;
//!   * the request line and the headers are capped, and so is the body, a
//!     request larger than the cap being refused before its body is read;
//!   * the socket carries a read timeout, so a client that opens a
//!     connection and says nothing does not hold the server;
//!   * the search is given a time limit that the page cannot remove, because
//!     a search with no limit does not have to terminate;
//!   * no path from a request ever reaches the file system: the page is a
//!     constant, the examples of the paper and of the thesis are constants,
//!     and the process is parsed by the parser of the crate. The one
//!     thing read from disk is an instance of the benchmark, addressed by
//!     four numbers and a mode that are checked against the grid before a
//!     path is built from them, under a folder fixed when the server starts;
//!     an instance not yet on disk is drawn by the generator of the suite,
//!     a fixed script given those same numbers.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::num::{self, Exact};
use crate::pareto;
use crate::search::Config;
use crate::tree::{Kind, Tree};
use crate::{parse, parse_args, SearchOptions};

const PAGE: &str = include_str!("serve/page.html");

/// The largest request body accepted, and the largest time limit the page may
/// ask for. A process is text and a generous one is a few kilobytes.
const MAX_BODY: usize = 256 * 1024;
/// The body of a request for an explanation carries the decisions of a
/// strategy, one per history, and a strategy is far larger than the process
/// it is a strategy of.
const MAX_EXPLAIN_BODY: usize = 64 * 1024 * 1024;
const MAX_LIMIT: f64 = 600.0;
/// The request line and the headers together: nothing the page sends comes
/// near it, and a connection that goes on sending past it is refused.
const MAX_HEAD: u64 = 64 * 1024;
/// What is read and dropped of a body refused, and for how long, so that the
/// client that is still sending it reads the reason and not a closed socket.
const MAX_DRAIN: usize = 64 * 1024 * 1024;
const DRAIN_TIME: Duration = Duration::from_secs(5);

const EXAMPLE: &str = "\
( (Intake, 1, {euro: 10, hours: 2})
, ( (Swap, 2, {euro: 50, hours: 4})
    ^ ( (Mend, 3, {euro: 20, hours: 9})
      , ( (Redo, 2, {euro: 40, hours: 6}) ^[0.3] (Pass, 1, {hours: 1}) ) ) )
, ( (Check, 1, {euro: 6, hours: 1}) || (File, 1, {euro: 4, hours: 1}) )
, ( (Courier, 1, {euro: 26, hours: 1}) ^ (Counter, 2, {euro: 2, hours: 2}) ) )";

/// Where the instances of the benchmark are, and the script that draws one
/// that is not there yet. Both are fixed when the server starts: `--bench`
/// names the folder of the instances, or the environment `SDCPI_BENCH`, or
/// the folder of the campaign beside this executable's tree; the generator
/// is the stage of the first suite beside it, when it is there.
pub struct Bench {
    instances: PathBuf,
    /// The folder of the instances with loops, the grid drawn from the
    /// branch `loops` of the benchmark: `--bench-loops`, or the environment
    /// `SDCPI_BENCH_LOOPS`, or the folder beside `instances` named
    /// `<instances>-loops`, where the generator writes with `--loops`.
    loops: PathBuf,
    generator: Option<PathBuf>,
    /// The file the saved processes live in: one JSON object, name to text,
    /// rewritten whole at every save, which is what a few kilobytes deserve.
    saved: PathBuf,
}

impl Bench {
    fn locate(flag: Option<&str>, loops_flag: Option<&str>, saved: Option<&str>) -> Bench {
        let root = std::env::current_exe()
            .ok()
            .and_then(|e| e.ancestors().nth(3).map(|p| p.to_path_buf()));
        let instances = match flag.map(PathBuf::from).or_else(|| std::env::var_os("SDCPI_BENCH").map(PathBuf::from)) {
            Some(p) => p,
            None => root
                .clone()
                .map(|r| r.join("scripts/pareto-frontier/experiment/instances"))
                .unwrap_or_else(|| PathBuf::from("scripts/pareto-frontier/experiment/instances")),
        };
        // the generator: beside this executable's tree, or beside the folder
        // of the instances, which sits three levels under the scripts
        let generator = root
            .map(|r| r.join("scripts/storm-comparison-ablation/make_bpmn_cpi.py"))
            .filter(|p| p.is_file())
            .or_else(|| {
                instances
                    .ancestors()
                    .nth(3)
                    .map(|s| s.join("storm-comparison-ablation/make_bpmn_cpi.py"))
                    .filter(|p| p.is_file())
            });
        let saved = saved
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("SDCPI_SAVED").map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from("sdcpi-processes.json"));
        let loops = loops_flag
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("SDCPI_BENCH_LOOPS").map(PathBuf::from))
            .unwrap_or_else(|| {
                let name = instances.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "instances".into());
                instances.with_file_name(format!("{name}-loops"))
            });
        Bench { instances, loops, generator, saved }
    }
    fn available(&self) -> bool {
        self.instances.is_dir() || self.generator.is_some()
    }
    /// Whether a process with loops can be loaded: the grid with loops is
    /// on disk, or the generator is there with the checkout of the branch
    /// `loops` beside it, from which it draws an instance not on disk.
    fn loops_available(&self) -> bool {
        self.loops.is_dir()
            || self
                .generator
                .as_ref()
                .and_then(|g| g.parent().map(|d| d.join("process-impact-benchmarks-loops").is_dir()))
                .unwrap_or(false)
    }
}

pub fn cmd_serve(args: &[String]) -> i32 {
    let args = match parse_args(args) {
        Ok(a) => a,
        Err(e) => return crate::fail(&e),
    };
    let bench = Bench::locate(args.flag("bench"), args.flag("bench-loops"), args.flag("saved"));
    let port: u16 = match args.flag("port") {
        None => 8080,
        Some(p) => match p.parse() {
            Ok(v) => v,
            Err(_) => return crate::fail(&format!("--port wants a number, found {p:?}")),
        },
    };
    // the loopback address and never every address: this server runs a
    // computation for whoever asks, and whoever asks has to be this machine
    let listener = match TcpListener::bind((Ipv4Addr::LOCALHOST, port)) {
        Ok(l) => l,
        Err(e) => return crate::fail(&format!("cannot listen on port {port}: {e}")),
    };
    println!("sdcpi serve: http://127.0.0.1:{port}  (ctrl-c to stop)");
    if bench.available() {
        println!("benchmark instances: {}", bench.instances.display());
    } else {
        println!("benchmark instances: none found, the loading panel is hidden");
    }
    println!("saved processes: {}", bench.saved.display());
    let bench = Arc::new(bench);
    for stream in listener.incoming() {
        match stream {
            Ok(s) => {
                let bench = bench.clone();
                // a deep term recurses through the parser, the tree and the
                // drawing; the bound of the parser sits at two thousand
                // levels, which needs more than the two megabytes a thread
                // gets by default
                let spawned = std::thread::Builder::new()
                    .stack_size(64 << 20)
                    .spawn(move || serve_one(s, &bench));
                if let Err(e) = spawned {
                    eprintln!("a connection could not be served: {e}");
                }
            }
            Err(e) => eprintln!("connection refused itself: {e}"),
        }
    }
    0
}

/// One search at a time: two searches at once would share the processors
/// and both would report a time neither would have taken alone.
static SEARCH: Mutex<()> = Mutex::new(());

/// One save at a time: the file is read, changed and rewritten whole.
static SAVING: Mutex<()> = Mutex::new(());

// -- the saved processes ------------------------------------------------------

/// The store read back: a flat JSON object of strings, name to text, which
/// is all the store holds, read by a parser of its own since the crate
/// carries none. Anything else in the file is an error.
pub(crate) fn read_store(path: &std::path::Path) -> Result<Vec<(String, String)>, String> {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(format!("{}: {e}", path.display())),
    };
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0usize;
    let skip = |i: &mut usize| {
        while *i < chars.len() && chars[*i].is_whitespace() {
            *i += 1;
        }
    };
    let string = |i: &mut usize| -> Result<String, String> {
        if chars.get(*i) != Some(&'"') {
            return Err(format!("a string was expected at character {}", *i));
        }
        *i += 1;
        let mut out = String::new();
        loop {
            let c = *chars.get(*i).ok_or("the file ends inside a string")?;
            *i += 1;
            match c {
                '"' => return Ok(out),
                '\\' => {
                    let e = *chars.get(*i).ok_or("the file ends inside an escape")?;
                    *i += 1;
                    match e {
                        '"' => out.push('"'),
                        '\\' => out.push('\\'),
                        '/' => out.push('/'),
                        'n' => out.push('\n'),
                        'r' => out.push('\r'),
                        't' => out.push('\t'),
                        'b' => out.push('\u{8}'),
                        'f' => out.push('\u{c}'),
                        'u' => {
                            let hex: String = chars.get(*i..*i + 4).ok_or("a short \\u escape")?.iter().collect();
                            *i += 4;
                            let code = u32::from_str_radix(&hex, 16).map_err(|_| "a bad \\u escape")?;
                            out.push(char::from_u32(code).unwrap_or('\u{fffd}'));
                        }
                        other => return Err(format!("an unknown escape \\{other}")),
                    }
                }
                c => out.push(c),
            }
        }
    };
    skip(&mut i);
    if chars.get(i) != Some(&'{') {
        return Err("the store does not open with a brace".to_string());
    }
    i += 1;
    let mut out = Vec::new();
    loop {
        skip(&mut i);
        match chars.get(i) {
            Some('}') => return Ok(out),
            Some(',') if !out.is_empty() => {
                i += 1;
                skip(&mut i);
            }
            _ if out.is_empty() => {}
            _ => return Err(format!("a comma or a brace was expected at character {i}")),
        }
        let name = string(&mut i)?;
        skip(&mut i);
        if chars.get(i) != Some(&':') {
            return Err(format!("a colon was expected at character {i}"));
        }
        i += 1;
        skip(&mut i);
        let text = string(&mut i)?;
        out.push((name, text));
    }
}

fn write_store(path: &std::path::Path, entries: &[(String, String)]) -> Result<(), String> {
    let body: Vec<String> = entries
        .iter()
        .map(|(n, t)| format!("  {}: {}", esc(n), esc(t)))
        .collect();
    let text = format!("{{\n{}\n}}\n", body.join(",\n"));
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))
}

/// A name as the user typed it, trimmed, bounded, and not empty.
fn clean_name(raw: Option<String>) -> Result<String, String> {
    let name = raw.unwrap_or_default().trim().to_string();
    if name.is_empty() {
        return Err("the process needs a name".to_string());
    }
    if name.chars().count() > 80 {
        return Err("a name is at most eighty characters".to_string());
    }
    if name.contains('"') {
        return Err("a name carries no double quote, which is how a name with spaces is referred to".to_string());
    }
    Ok(name)
}

/// The text a reference `@name` stands for: the process saved under that
/// name, or, when the store holds none, the built-in example of that id.
/// That way an example that refers to another, the release to its tests and
/// the stroke to its transport, reads on an empty store, and a saved process
/// of the same name wins. Everything that assembles a process resolves
/// through here, the refusal of a cycle included.
pub(crate) fn referred(name: &str, entries: &[(String, String)]) -> Result<String, String> {
    entries
        .iter()
        .find(|(n, _)| n == name)
        .map(|(_, t)| t.clone())
        .or_else(|| crate::examples::find(name).map(|e| e.text.to_string()))
        .ok_or_else(|| format!("no process is saved as {name:?}, and no example has that name"))
}

/// Whether saving `text` under `name` would close a cycle of references,
/// through the store as it stands and the examples behind it: the message of
/// the cycle, if it would. A depth first walk over the graph of the
/// references, apart from the grammar: an unknown name or a text that does not
/// parse on the way hides no reference behind it, and nothing is assembled, so
/// a save costs what the texts on the walk are long.
fn closes_a_cycle(text: &str, name: &str, entries: &[(String, String)]) -> Option<String> {
    let mut seen = std::collections::HashSet::new();
    let mut path = vec![(name.to_string(), parse::references_of(text).into_iter())];
    while let Some((_, refs)) = path.last_mut() {
        match refs.next() {
            None => {
                path.pop();
            }
            Some(next) if next == name => {
                let mut cycle: Vec<&str> = path.iter().map(|(n, _)| n.as_str()).collect();
                cycle.push(name);
                return Some(format!("the references form a cycle: {}", cycle.join(" > ")));
            }
            Some(next) => {
                if seen.insert(next.clone()) {
                    if let Ok(t) = referred(&next, entries) {
                        path.push((next, parse::references_of(&t).into_iter()));
                    }
                }
            }
        }
    }
    None
}

/// The names of the store a text comes to through its references, at any
/// depth, the examples on the way walked too, sorted.
fn saved_behind(text: &str, entries: &[(String, String)]) -> Vec<String> {
    let mut seen = std::collections::BTreeSet::new();
    let mut todo = parse::references_of(text);
    while let Some(next) = todo.pop() {
        if seen.insert(next.clone()) {
            if let Ok(t) = referred(&next, entries) {
                todo.extend(parse::references_of(&t));
            }
        }
    }
    seen.into_iter().filter(|n| entries.iter().any(|(e, _)| e == n)).collect()
}

/// The saved processes whose text refers to `name`, the references gathered
/// as the parser reads them.
fn referrers<'a>(name: &str, entries: &'a [(String, String)]) -> Vec<&'a str> {
    entries
        .iter()
        .filter(|(n, t)| n != name && parse::references_of(t).iter().any(|r| r == name))
        .map(|(n, _)| n.as_str())
        .collect()
}

/// The saved processes, by name, with the size of each.
fn saved_list(bench: &Bench) -> String {
    let _one = SAVING.lock().unwrap_or_else(|e| e.into_inner());
    match read_store(&bench.saved) {
        Err(e) => err(&e),
        Ok(mut entries) => {
            entries.sort_by(|a, b| a.0.cmp(&b.0));
            let items: Vec<String> = entries
                .iter()
                .map(|(n, t)| format!("{{\"name\": {}, \"chars\": {}}}", esc(n), t.chars().count()))
                .collect();
            format!("{{\"saved\": [{}]}}", items.join(", "))
        }
    }
}

/// Saves the text under the name; an existing name is not overwritten unless
/// the form says so, the page asking the user first.
fn save_process(body: &str, bench: &Bench) -> String {
    let name = match clean_name(field(body, "name")) {
        Ok(n) => n,
        Err(e) => return err(&e),
    };
    let text = match field(body, "process") {
        Some(t) if !t.trim().is_empty() => t,
        _ => return err("there is no process to save"),
    };
    if text.len() > MAX_BODY {
        return err("the process is too long to save");
    }
    let overwrite = matches!(field(body, "overwrite").as_deref(), Some("1") | Some("true"));
    let _one = SAVING.lock().unwrap_or_else(|e| e.into_inner());
    let mut entries = match read_store(&bench.saved) {
        Ok(e) => e,
        Err(e) => return err(&e),
    };
    // a text that would refer to itself through the store is not saved: the
    // save would leave every process on the cycle undrawable
    if let Some(cycle) = closes_a_cycle(&text, &name, &entries) {
        return err(&format!("not saved: {cycle}"));
    }
    match entries.iter_mut().find(|(n, _)| *n == name) {
        Some(entry) => {
            if !overwrite {
                return format!("{{\"exists\": true, \"name\": {}}}", esc(&name));
            }
            entry.1 = text;
        }
        None => entries.push((name.clone(), text)),
    }
    match write_store(&bench.saved, &entries) {
        Ok(()) => format!("{{\"ok\": true, \"name\": {}}}", esc(&name)),
        Err(e) => err(&e),
    }
}

/// Removes a saved process; the page asks the user first.
fn delete_saved(body: &str, bench: &Bench) -> String {
    let name = match clean_name(field(body, "name")) {
        Ok(n) => n,
        Err(e) => return err(&e),
    };
    let _one = SAVING.lock().unwrap_or_else(|e| e.into_inner());
    let mut entries = match read_store(&bench.saved) {
        Ok(e) => e,
        Err(e) => return err(&e),
    };
    let before = entries.len();
    // a process others refer to stays: deleting it would break them
    let used_by = referrers(&name, &entries);
    if !used_by.is_empty() {
        return err(&format!(
            "not deleted: {} is referred to by {}",
            name,
            used_by.join(", ")
        ));
    }
    entries.retain(|(n, _)| *n != name);
    if entries.len() == before {
        return err(&format!("no process is saved as {name:?}"));
    }
    match write_store(&bench.saved, &entries) {
        Ok(()) => format!("{{\"ok\": true, \"name\": {}}}", esc(&name)),
        Err(e) => err(&e),
    }
}

/// A saved process by its name.
fn load_saved(body: &str, bench: &Bench) -> String {
    let name = match clean_name(field(body, "name")) {
        Ok(n) => n,
        Err(e) => return err(&e),
    };
    let _one = SAVING.lock().unwrap_or_else(|e| e.into_inner());
    match read_store(&bench.saved) {
        Err(e) => err(&e),
        Ok(entries) => match entries.into_iter().find(|(n, _)| *n == name) {
            Some((n, t)) => format!("{{\"name\": {}, \"process\": {}}}", esc(&n), esc(&t)),
            None => err(&format!("no process is saved as {name:?}")),
        },
    }
}

// -- the examples -------------------------------------------------------------

/// The settings an example is computed with, as three fields of a JSON
/// object: whether the expected time is a component, the max iterations,
/// and the names of the components to maximize.
fn settings_json(e: &crate::examples::Example) -> String {
    let maximize: Vec<String> = e.maximize.iter().map(|n| esc(n)).collect();
    format!(
        "\"time\": {}, \"max_iter\": {}, \"maximize\": [{}]",
        e.time,
        e.max_iter,
        maximize.join(", ")
    )
}

/// The examples of the paper and of the thesis as the panel lists them:
/// everything but the text, which the page asks for when one is loaded.
fn examples_list() -> String {
    let items: Vec<String> = crate::examples::EXAMPLES
        .iter()
        .map(|e| {
            format!(
                "{{\"id\": {}, \"group\": {}, \"title\": {}, \"source\": {}, \"note\": {}, {}}}",
                esc(e.id),
                esc(e.group),
                esc(e.title),
                esc(e.source),
                esc(e.note),
                settings_json(e)
            )
        })
        .collect();
    format!("{{\"examples\": [{}]}}", items.join(", "))
}

/// One example by its id: the name it is loaded under, its text and its
/// settings. The examples are constants of the binary, so the id is looked
/// up in a list and nothing of the request reaches the file system.
fn load_example(body: &str) -> String {
    let id = match field(body, "id").map(|i| i.trim().to_string()).filter(|i| !i.is_empty()) {
        Some(i) => i,
        None => return err("no example was named"),
    };
    match crate::examples::find(&id) {
        Some(e) => format!(
            "{{\"name\": {}, \"process\": {}, {}}}",
            esc(e.id),
            esc(e.text),
            settings_json(e)
        ),
        None => err(&format!("no example is named {id:?}")),
    }
}

fn serve_one(mut stream: TcpStream, bench: &Bench) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(15)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(60)));
    let (method, path, body) = match request(&mut stream) {
        Ok(v) => v,
        Err((code, reason)) => {
            send(&mut stream, code, "text/plain; charset=utf-8", &reason);
            return drain(&mut stream);
        }
    };
    match (method.as_str(), path.as_str()) {
        ("GET", "/") => send(&mut stream, 200, "text/html; charset=utf-8", PAGE),
        ("GET", "/api/hello") => send(&mut stream, 200, "application/json", &hello(bench)),
        ("POST", "/api/bench") => {
            let out = load_bench(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("GET", "/api/saved") => send(&mut stream, 200, "application/json", &saved_list(bench)),
        ("POST", "/api/save") => {
            let out = save_process(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/load") => {
            let out = load_saved(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/delete") => {
            let out = delete_saved(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("GET", "/api/examples") => send(&mut stream, 200, "application/json", &examples_list()),
        ("POST", "/api/example") => {
            let out = load_example(&body);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/frontier") => {
            let out = frontier(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/svg") => {
            let out = diagram(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/bounds") => {
            let out = bounds_of(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/explain") => {
            let out = explain_of(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/start") => {
            let out = start(&body, bench);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/snapshot") => {
            let out = snapshot(&body);
            send(&mut stream, 200, "application/json", &out)
        }
        ("POST", "/api/stop") => {
            let out = stop(&body);
            send(&mut stream, 200, "application/json", &out)
        }
        _ => send(&mut stream, 404, "text/plain; charset=utf-8", "no such page\n"),
    }
}

/// Whether a request may be answered. A browser sends the two headers on its
/// own and a page cannot forge them: the host it asks for has to be a name of
/// the loopback, against a page of another site that made its own name resolve
/// here, and the origin, when there is one, has to be the page that host
/// serves, against a page of another site or of another port. The port of the
/// host is not looked at, a tunnel being free to put this server behind another.
fn from_this_page(host: Option<&str>, origin: Option<&str>) -> bool {
    let loopback = |authority: &str| matches!(authority.rsplit_once(':').map_or(authority, |(n, _)| n), "127.0.0.1" | "localhost");
    host.map_or(true, loopback)
        && origin.map_or(true, |o| match (o.strip_prefix("http://"), host) {
            (Some(page), Some(h)) => page == h,
            (Some(page), None) => loopback(page),
            (None, _) => false,
        })
}

/// The method, the path and the body of one request. Nothing else of HTTP is
/// read, because nothing else is answered; the status and the reason when the
/// request is refused.
fn request(stream: &mut TcpStream) -> Result<(String, String, String), (u16, String)> {
    let mut reader = BufReader::new(stream.try_clone().map_err(|e| (400, e.to_string()))?);
    // the request line and the headers read through a cap: a line grows until
    // its newline arrives, and the read timeout does not fire while bytes keep
    // coming
    let too_long = || (400, format!("the request line and the headers are longer than the {MAX_HEAD} bytes accepted\n"));
    let mut head = (&mut reader).take(MAX_HEAD);
    let mut line = String::new();
    head.read_line(&mut line)
        .map_err(|e| (400, format!("the request line did not arrive: {e}")))?;
    if head.limit() == 0 && !line.ends_with('\n') {
        return Err(too_long());
    }
    let mut parts = line.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let path = parts.next().unwrap_or("").to_string();
    let mut length = 0usize;
    let (mut host, mut origin) = (None, None);
    loop {
        let mut h = String::new();
        let n = head.read_line(&mut h).map_err(|e| (400, e.to_string()))?;
        if head.limit() == 0 && !h.ends_with('\n') {
            return Err(too_long());
        }
        if n == 0 || h == "\r\n" || h == "\n" {
            break;
        }
        let lower = h.to_ascii_lowercase();
        if let Some(v) = lower.strip_prefix("content-length:") {
            length = v.trim().parse().unwrap_or(0);
        } else if let Some(v) = lower.strip_prefix("host:") {
            host = Some(v.trim().to_string());
        } else if let Some(v) = lower.strip_prefix("origin:") {
            origin = Some(v.trim().to_string());
        }
    }
    if !from_this_page(host.as_deref(), origin.as_deref()) {
        return Err((403, "the request does not come from the page of this server\n".to_string()));
    }
    let accepted = if path == "/api/explain" { MAX_EXPLAIN_BODY } else { MAX_BODY };
    if length > accepted {
        return Err((400, format!("the body is larger than the {accepted} bytes accepted\n")));
    }
    let mut body = vec![0u8; length];
    reader
        .read_exact(&mut body)
        .map_err(|e| (400, format!("the body did not arrive whole: {e}")))?;
    let body = String::from_utf8(body).map_err(|_| (400, "the body is not UTF-8".to_string()))?;
    Ok((method, path, body))
}

/// After a refusal: the socket is closed for writing and what the client is
/// still sending is read and dropped, up to a bound and for a moment, since a
/// socket closed with data unread resets, and the reset takes the answer with
/// it on the way to the client.
fn drain(stream: &mut TcpStream) {
    let _ = stream.shutdown(std::net::Shutdown::Write);
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let started = Instant::now();
    let mut sink = [0u8; 16 * 1024];
    let mut read = 0usize;
    while read < MAX_DRAIN && started.elapsed() < DRAIN_TIME {
        match stream.read(&mut sink) {
            Ok(0) | Err(_) => return,
            Ok(n) => read += n,
        }
    }
}

fn send(stream: &mut TcpStream, code: u16, kind: &str, body: &str) {
    let reason = match code {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        _ => "Error",
    };
    let head = format!(
        "HTTP/1.1 {code} {reason}\r\nContent-Type: {kind}\r\nContent-Length: {}\r\n\
         Cache-Control: no-store\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body.as_bytes());
    let _ = stream.flush();
}

// -- the form ---------------------------------------------------------------

/// One field of an `application/x-www-form-urlencoded` body.
fn field<'a>(body: &'a str, name: &str) -> Option<String> {
    for pair in body.split('&') {
        // a pair with no value says nothing, and hides nothing after it
        let Some((k, v)) = pair.split_once('=') else {
            continue;
        };
        if unescape(k) == name {
            return Some(unescape(v));
        }
    }
    None
}

fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' if i + 2 < b.len() => {
                let hex = |c: u8| match c {
                    b'0'..=b'9' => Some(c - b'0'),
                    b'a'..=b'f' => Some(c - b'a' + 10),
                    b'A'..=b'F' => Some(c - b'A' + 10),
                    _ => None,
                };
                match (hex(b[i + 1]), hex(b[i + 2])) {
                    (Some(h), Some(l)) => {
                        out.push(h * 16 + l);
                        i += 3;
                    }
                    _ => {
                        out.push(b[i]);
                        i += 1;
                    }
                }
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// One decision of a strategy, written with the names of the instance rather
/// than with the identifiers of its nodes. A decision says which branch to
/// take once the run has reached a point, and the point is the history: the
/// identifiers of the nodes already resolved. A reader wants the names.
fn decision(tree: &Tree, history: &[u32], action: u32) -> String {
    let taken: Vec<String> = history.iter().map(|&id| name_of(tree, id)).collect();
    if taken.is_empty() {
        format!("take {}", name_of(tree, action))
    } else {
        format!(
            "once {} is reached, take {}",
            taken.join(" and "),
            name_of(tree, action)
        )
    }
}

/// One node as the page reads it: the identifier, the name the sentences
/// use, and whether the node is a task or a region.
fn node_json(tree: &Tree, id: u32, labels: &[String]) -> String {
    let label = match labels.get(id as usize) {
        Some(l) if !l.is_empty() => l.clone(),
        _ => id.to_string(),
    };
    format!(
        "{{\"id\": {id}, \"label\": {}, \"name\": {}, \"task\": {}}}",
        esc(&label),
        esc(&name_of(tree, id)),
        tree.node(id).kind == Kind::Task
    )
}

/// The saved processes, read under the lock that guards the store.
fn store_entries(bench: &Bench) -> Result<Vec<(String, String)>, String> {
    let _one = SAVING.lock().unwrap_or_else(|e| e.into_inner());
    read_store(&bench.saved)
}

/// The process as the parser of the crate reads it, every reference to a
/// saved process, or to an example when the store holds no such name,
/// spliced in place: the instance the engine sees, and beside it the
/// identifier every node shows and the subprocesses. The store is read
/// only when the text refers to it.
fn assemble(text: &str, bench: &Bench, editing: Option<&str>) -> Result<parse::Parsed, String> {
    let entries = if text.contains('@') { store_entries(bench)? } else { Vec::new() };
    // the text being written stands for its own name, saved or not, so that
    // what the page shows is what a save would make of it
    let mut resolve = |name: &str| -> Result<String, String> {
        if Some(name) == editing {
            return Ok(text.to_string());
        }
        referred(name, &entries)
    };
    match editing {
        Some(name) => parse::to_yaml_as(text, name, &mut resolve),
        None => parse::to_yaml_with(text, &mut resolve),
    }
    .map_err(|e| format!("the process does not parse: {e}"))
}

/// The name the page is writing the process under, when it has one.
fn editing_name(body: &str) -> Option<String> {
    field(body, "name").map(|n| n.trim().to_string()).filter(|n| !n.is_empty())
}

/// The name of a node. A task carries its own; an internal node carries none
/// and takes the name of the first task of its region, which is the task the
/// run reaches first when that branch is taken, so that a decision reads as a
/// sentence about the process and not about the identifiers of its nodes.
pub(crate) fn name_of(tree: &Tree, id: u32) -> String {
    if let Some(n) = tree.task_names.get(id as usize) {
        if !n.is_empty() {
            return n.clone();
        }
    }
    let mut at = id;
    for _ in 0..tree.n_nodes {
        match tree.nodes.get(at as usize) {
            None => break,
            Some(node) => {
                if let Some(n) = tree.task_names.get(at as usize) {
                    if !n.is_empty() {
                        return n.clone();
                    }
                }
                if node.low == 0 || node.low == at {
                    break;
                }
                at = node.low;
            }
        }
    }
    format!("node {id}")
}

// -- the answers ------------------------------------------------------------

fn esc(s: &str) -> String {
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

fn hello(bench: &Bench) -> String {
    let modes: Vec<String> = crate::text::MODES.iter().map(|m| esc(m)).collect();
    format!(
        "{{\"grammar\": {}, \"example\": {}, \"bench\": {}, \"loops\": {}, \"modes\": [{}]}}",
        esc(
            "(name, duration, {impact: value, ...}) is a task, the map naming only the \
             impacts that are not zero; , is a sequence, || a parallel, ^ a choice of \
             the controller and ^[p] one of nature taking its left operand with \
             probability p; brackets group. (p R), a probability right after the \
             parenthesis, is a loop over the process R: once R is done it runs again \
             with probability p and the loop is left with 1 - p, the exit being forced \
             after the max iterations. @saved refers to a process saved on this \
             page, spliced in place as a region, @\"a saved name\" one with spaces, and \
             @saved[shown] shows it under another name; a saved process may refer to \
             others, never to itself. A name that is not saved is read among the \
             examples."
        ),
        esc(EXAMPLE),
        bench.available(),
        bench.available() && bench.loops_available(),
        modes.join(", ")
    )
}

/// An instance of the benchmark as text for the page: the four coordinates
/// and the mode are checked against the grid, the path is built from them
/// under the folder of the instances, an instance not on disk is drawn by
/// the generator of the suite, and the impacts are drawn again here when
/// asked, with the mode and the seed given.
fn load_bench(body: &str, bench: &Bench) -> String {
    let num = |name: &str, lo: u32, hi: u32| -> Result<u32, String> {
        let v = field(body, name).ok_or_else(|| format!("{name} is missing"))?;
        let v: u32 = v
            .trim()
            .parse()
            .map_err(|_| format!("{name} is not a number: {v:?}"))?;
        if v < lo || v > hi {
            return Err(format!("{name} must be between {lo} and {hi}"));
        }
        Ok(v)
    };
    let (n, i, k, d) = match (
        num("nested", 1, 10),
        num("independent", 1, 10),
        num("process", 1, 10),
        num("impacts", 2, 10),
    ) {
        (Ok(n), Ok(i), Ok(k), Ok(d)) => (n, i, k, d),
        (Err(e), ..) | (_, Err(e), ..) | (_, _, Err(e), _) | (_, _, _, Err(e)) => return err(&e),
    };
    let mode = field(body, "mode").unwrap_or_else(|| "random".into());
    if !crate::text::MODES.contains(&mode.as_str()) {
        return err(&format!("no mode {mode:?}"));
    }
    let regenerate = matches!(field(body, "regenerate").as_deref(), Some("1") | Some("true"));
    let seed = field(body, "seed")
        .and_then(|v| v.trim().parse::<u64>().ok())
        .unwrap_or(20260815);
    // the process with loops: the same coordinates in the grid drawn from
    // the branch with loops of the benchmark
    let loops = matches!(field(body, "loops").as_deref(), Some("1") | Some("true"));
    if loops && !bench.loops_available() {
        return err("the instances with loops are not on disk and the generator has no checkout of the branch loops beside it");
    }
    let key = if loops { format!("{n}-{i}-{k}-{d}-{mode}-loops") } else { format!("{n}-{i}-{k}-{d}-{mode}") };
    let folder = if loops { &bench.loops } else { &bench.instances };
    let path = folder
        .join(format!("{n}-nested"))
        .join(format!("{i}-independent"))
        .join(format!("{k}-process_number"))
        .join(format!("{d}-{mode}.yaml"));
    if !path.is_file() {
        let Some(generator) = &bench.generator else {
            return err(&format!(
                "the instance {key} is not on disk and no generator is beside this executable"
            ));
        };
        let mut command = std::process::Command::new("python3");
        command
            .arg(generator)
            .args(["--nested", &n.to_string(), "--independent", &i.to_string()])
            .args(["--process-number", &k.to_string(), "--dimensions", &d.to_string()])
            .args(["--mode", &mode, "--seed", "20260815", "--out"])
            .arg(folder);
        if loops {
            command.arg("--loops");
        }
        let run = command.output();
        match run {
            Ok(o) if o.status.success() && path.is_file() => {}
            Ok(o) => {
                return err(&format!(
                    "the generator did not write {key}: {}",
                    String::from_utf8_lossy(&o.stderr).trim()
                ))
            }
            Err(e) => return err(&format!("the generator could not be run: {e}")),
        }
    }
    let yaml = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) => return err(&format!("{key} could not be read: {e}")),
    };
    let tree = match Tree::parse(&yaml) {
        Ok(t) => t,
        Err(e) => return err(&format!("the instance {key} does not read: {e}")),
    };
    let fresh = if regenerate {
        match crate::text::regenerate(&tree, d as usize, &mode, seed) {
            Ok(f) => Some(f),
            Err(e) => return err(&e),
        }
    } else {
        None
    };
    let process = crate::text::to_text(&tree, fresh.as_deref());
    format!(
        "{{\"instance\": {}, \"process\": {}, \"regenerated\": {}}}",
        esc(&key),
        esc(&process),
        regenerate
    )
}

fn err(message: &str) -> String {
    format!("{{\"error\": {}}}", esc(message))
}

/// The diagram of the process as the page has it at the moment, drawn as
/// SVG; parsed by the parser of the crate and nothing else.
fn diagram(body: &str, bench: &Bench) -> String {
    let text = match field(body, "process") {
        Some(t) if !t.trim().is_empty() => t,
        _ => return err("no process was given"),
    };
    let editing = editing_name(body);
    let parsed = match assemble(&text, bench, editing.as_deref()) {
        Ok(p) => p,
        Err(e) => return err(&e),
    };
    let tree = match Tree::parse(&parsed.yaml) {
        Ok(t) => t,
        Err(e) => return err(&format!("the instance does not read: {e}")),
    };
    // the subprocesses the page has expanded, by the identifier they show;
    // everything starts collapsed
    let expanded: Vec<String> = field(body, "expand")
        .unwrap_or_default()
        .split(',')
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty())
        .collect();
    let subs: Vec<crate::svg::SubBox> = parsed
        .subs
        .iter()
        .map(|s| crate::svg::SubBox {
            root: s.root,
            label: s.label.clone(),
            name: s.name.clone(),
            shown: s.shown.clone(),
            expanded: expanded.contains(&s.label),
        })
        .collect();
    let listed_subs: Vec<String> = parsed
        .subs
        .iter()
        .map(|s| {
            format!(
                "{{\"label\": {}, \"name\": {}, \"shown\": {}, \"depth\": {}, \"expanded\": {}}}",
                esc(&s.label),
                esc(&s.name),
                esc(&s.shown),
                s.depth,
                expanded.contains(&s.label)
            )
        })
        .collect();
    let detailed = matches!(field(body, "detail").as_deref(), Some("1") | Some("true"));
    let tooltips = matches!(field(body, "tooltips").as_deref(), Some("1") | Some("true"));
    // the bars: the impacts named in `show`, alphabetical, all of them when
    // the field is absent
    let names = crate::svg::impact_names(&tree);
    let order = crate::svg::alphabetical(&tree);
    let bars = if matches!(field(body, "bars").as_deref(), Some("1") | Some("true")) {
        // the page names the impacts to hide, so that a process just loaded,
        // whose names the page does not know yet, is drawn whole and a name
        // hidden before stays hidden if the process still has it; `show`
        // names the ones to draw instead, and an empty list means all
        Some(match (field(body, "hide"), field(body, "show").filter(|l| !l.trim().is_empty())) {
            (Some(hidden), _) => {
                let hidden: Vec<String> = hidden.split(',').map(|t| t.trim().to_string()).collect();
                order.iter().copied().filter(|&j| !hidden.contains(&names[j])).collect()
            }
            (None, Some(list)) => {
                let wanted: Vec<String> = list.split(',').map(|t| t.trim().to_string()).collect();
                order.iter().copied().filter(|&j| wanted.contains(&names[j])).collect()
            }
            (None, None) => order.clone(),
        })
    } else {
        None
    };
    let clocks = matches!(field(body, "clocks").as_deref(), Some("1") | Some("true"));
    let show_names = matches!(field(body, "names").as_deref(), Some("1") | Some("true"));
    let style = crate::svg::Style {
        detailed,
        tooltips,
        bars,
        clocks,
        names: show_names,
        labels: if parsed.subs.is_empty() { None } else { Some(parsed.labels.clone()) },
        subs,
    };
    let listed: Vec<String> = order.iter().map(|&j| esc(&names[j])).collect();
    format!(
        "{{\"svg\": {}, \"impacts\": [{}], \"subs\": [{}]}}",
        esc(&crate::svg::render(&tree, &style)),
        listed.join(", "),
        listed_subs.join(", ")
    )
}

/// Runs one search and answers with the frontier, every point with the
/// strategy that attains it.
/// The components the page maximizes, `maximize=a,b` by name, applied to
/// the tree after the time, so that the time can be named too.
fn directions(tree: Tree, body: &str) -> Result<Tree, String> {
    match field(body, "maximize").filter(|m| !m.trim().is_empty()) {
        Some(m) => {
            // the page remembers a direction by name across the edits of the
            // text, so a name the process no longer has is dropped and not
            // refused
            let names: Vec<String> = m
                .split(',')
                .map(|t| t.trim().to_string())
                .filter(|t| !t.is_empty() && tree.meta.impact_names.iter().any(|n| n == t))
                .collect();
            tree.with_directions(&names)
        }
        None => Ok(tree),
    }
}

/// A double as JSON: a finite number, or null where a rational is past
/// what a double holds, which JSON cannot write.
fn jnum(x: f64) -> String {
    if x.is_finite() {
        format!("{x}")
    } else {
        "null".to_string()
    }
}

/// The same with six decimals.
fn jnum6(x: f64) -> String {
    if x.is_finite() {
        format!("{x:.6}")
    } else {
        "null".to_string()
    }
}

/// The points found outside the window, as the page draws them for
/// context: their values alone, shown, with no strategy.
fn outside_json(tree: &Tree, points: &[pareto::Point]) -> String {
    let items: Vec<String> = points
        .iter()
        .map(|p| {
            let approx: Vec<String> = tree.shown_vec(&p.value).iter().map(|v| jnum6(pareto::approx(v))).collect();
            format!("{{\"approx\": [{}]}}", approx.join(", "))
        })
        .collect();
    format!("[{}]", items.join(", "))
}

/// The directions of the components as the page reads them, one flag per
/// component, true where it is maximized.
fn maximized_json(tree: &Tree) -> String {
    let parts: Vec<String> = tree.maximized.iter().map(|m| m.to_string()).collect();
    format!("[{}]", parts.join(", "))
}

/// How long the two bounds of the page may take: it asks for them at every
/// change of the text, and no search stands behind them to cut them.
const BOUNDS_TIME: Duration = Duration::from_secs(20);

/// The two bounds of the process at its initial state, the extremes of the
/// window the page offers on the expected values, with the time and the
/// bound on the passes as the page has them.
fn bounds_of(body: &str, bench: &Bench) -> String {
    let text = match field(body, "process") {
        Some(t) if !t.trim().is_empty() => t,
        _ => return err("no process was given"),
    };
    let editing = editing_name(body);
    let parsed = match assemble(&text, bench, editing.as_deref()) {
        Ok(p) => p,
        Err(e) => return err(&e),
    };
    let tree = match Tree::parse(&parsed.yaml) {
        Ok(t) => t,
        Err(e) => return err(&format!("the instance does not read: {e}")),
    };
    let time = matches!(field(body, "time").as_deref(), Some("1") | Some("true"));
    let mut tree = match directions(if time { tree.with_time() } else { tree }, body) {
        Ok(t) => t,
        Err(e) => return err(&e),
    };
    tree.max_iter = Some(
        field(body, "max_iter")
            .and_then(|v| v.parse::<u32>().ok())
            .unwrap_or(crate::tree::DEFAULT_MAX_ITER)
            .clamp(1, 50),
    );
    let s0 = crate::state::Engine::new(&tree).initial_state();
    let clock = crate::bound::Clock { deadline: Some(Instant::now() + BOUNDS_TIME), cancel: None };
    let Some(b) = crate::bound::bounds_until::<Exact>(&tree, &s0, tree.max_iter, &clock) else {
        return err(&format!("the two bounds of the process take longer than {} seconds", BOUNDS_TIME.as_secs()));
    };
    let names: Vec<String> = (0..tree.k)
        .map(|c| match tree.meta.impact_names.get(c) {
            Some(n) if !n.is_empty() => esc(n),
            _ => esc(&format!("impact {c}")),
        })
        .collect();
    // the two bounds as they are shown, a maximized component reading the
    // bounds of its negation the other way round
    let rat = |v: &[Exact]| -> Vec<num_rational::BigRational> { v.iter().map(|x| x.0.clone()).collect() };
    let (shown_u, shown_l) = tree.shown_bounds(&rat(&b.upper), &rat(&b.lower));
    let side = |v: &[num_rational::BigRational]| -> String {
        let parts: Vec<String> = v.iter().map(|x| jnum(pareto::approx(x))).collect();
        format!("[{}]", parts.join(", "))
    };
    let exact = |v: &[num_rational::BigRational]| -> String {
        let parts: Vec<String> = v.iter().map(|x| esc(&x.to_string())).collect();
        format!("[{}]", parts.join(", "))
    };
    format!(
        "{{\"time\": {}, \"loops\": {}, \"maximized\": {}, \"names\": [{}], \"lower\": {}, \"upper\": {}, \"lower_exact\": {}, \"upper_exact\": {}}}",
        time,
        tree.loops().len(),
        maximized_json(&tree),
        names.join(", "),
        side(&shown_l),
        side(&shown_u),
        exact(&shown_l),
        exact(&shown_u)
    )
}

/// What a frontier request carries, read once: the tree with the time and
/// the bound on the passes as the page has them, the identifiers the nodes
/// show, the window and the options of the search.
struct Prepared {
    tree: Tree,
    labels: Vec<String>,
    time: bool,
    max_iter: u32,
    limit: f64,
    window: Option<Vec<(Option<num_rational::BigRational>, Option<num_rational::BigRational>)>>,
    tolerance: Option<Vec<num_rational::BigRational>>,
    opts: SearchOptions,
    workers: usize,
}

fn prepare(body: &str, bench: &Bench) -> Result<Prepared, String> {
    let text = match field(body, "process") {
        Some(t) if !t.trim().is_empty() => t,
        _ => return Err("no process was given".to_string()),
    };
    // the body of an explanation may be far larger than the others for its
    // decisions alone: the other fields are held to the cap of every route
    for name in ["process", "window", "tolerance"] {
        if field(body, name).map_or(false, |v| v.len() > MAX_BODY) {
            return Err(format!("the field {name} is longer than the {MAX_BODY} bytes accepted"));
        }
    }
    // the workers between 1 and 64 whatever the field says, and the limit
    // a finite number of seconds: the page is one client of many
    let workers_n: usize = field(body, "workers").and_then(|v| v.trim().parse::<usize>().ok()).unwrap_or(1).clamp(1, 64);
    let workers = workers_n.to_string();
    let ablation = field(body, "ablation").unwrap_or_else(|| "both".into());
    let limit = field(body, "limit")
        .and_then(|v| v.trim().parse::<f64>().ok())
        .filter(|v| v.is_finite())
        .unwrap_or(30.0)
        .clamp(1.0, MAX_LIMIT);
    // the passes a loop makes before its exit is forced, a small number
    let max_iter = field(body, "max_iter")
        .and_then(|v| v.parse::<u32>().ok())
        .unwrap_or(crate::tree::DEFAULT_MAX_ITER)
        .clamp(1, 50);

    // the process as the parser of the crate reads it, its references to the
    // saved processes spliced in, then the tree
    let editing = editing_name(body);
    let parsed = assemble(&text, bench, editing.as_deref())?;
    let labels = parsed.labels.clone();
    let tree = Tree::parse(&parsed.yaml).map_err(|e| format!("the instance does not read: {e}"))?;
    // the expected time as the component zero of the frontier, when the
    // page asks for it: the same transform the flag --time makes
    let time = matches!(field(body, "time").as_deref(), Some("1") | Some("true"));
    let mut tree = directions(if time { tree.with_time() } else { tree }, body)?;
    tree.max_iter = Some(max_iter);
    // the window on the expected values: `name=lo,hi;...` by the names of
    // the components, a name the process does not have ignored and a
    // component not named left open, or `lo,hi;lo,hi;...` by position, one
    // per component and never mixed with a part by name; either side empty
    // for the bound at the initial state
    let window = match field(body, "window").filter(|w| !w.trim().is_empty()) {
        None => None,
        Some(w) => {
            let mut out: Vec<(Option<num_rational::BigRational>, Option<num_rational::BigRational>)> = vec![(None, None); tree.k];
            let parts: Vec<&str> = w.split(';').collect();
            let named = parts.iter().any(|p| p.contains('='));
            if !named && parts.len() != tree.k {
                return Err(format!("{} ranges for {} components", parts.len(), tree.k));
            }
            for (pos, part) in parts.iter().enumerate() {
                let (slot, range) = match part.split_once('=') {
                    Some((n, r)) => match tree.meta.impact_names.iter().position(|m| m == n.trim()) {
                        Some(c) => (c, r),
                        None => continue,
                    },
                    None if named => return Err(format!("the part {part:?} of the window has no name, and the others do")),
                    None => (pos, *part),
                };
                let mut it = range.splitn(2, ',');
                let lo = it.next().unwrap_or("").trim().to_string();
                let hi = it.next().unwrap_or("").trim().to_string();
                out[slot] = (num::parse_rational(&lo), num::parse_rational(&hi));
            }
            Some(out)
        }
    };
    // a window on a maximized component is read on its negation: the two
    // ends swap and change sign
    let window = window.map(|w: Vec<(Option<num_rational::BigRational>, Option<num_rational::BigRational>)>| {
        w.into_iter()
            .enumerate()
            .map(|(c, (lo, hi))| {
                if tree.maximized.get(c).copied().unwrap_or(false) {
                    (hi.map(|x| -x), lo.map(|x| -x))
                } else {
                    (lo, hi)
                }
            })
            .collect::<Vec<_>>()
    });

    // the tolerances, `name=f;...` by the names of the components, a name
    // the process does not have ignored and a component not named at zero,
    // or `f;f;...` by position and never mixed with a part by name; fractions
    // of the range of every component, nothing when every one is zero
    let tolerance: Option<Vec<num_rational::BigRational>> = match field(body, "tolerance").filter(|t| !t.trim().is_empty()) {
        None => None,
        Some(t) => {
            let zero = num_rational::BigRational::from_integer(0.into());
            let one = num_rational::BigRational::from_integer(1.into());
            let mut out = vec![zero.clone(); tree.k];
            let parts: Vec<&str> = t.split(';').collect();
            let named = parts.iter().any(|p| p.contains('='));
            if !named && parts.len() != tree.k {
                return Err(format!("{} tolerances for {} components", parts.len(), tree.k));
            }
            for (pos, part) in parts.iter().enumerate() {
                let (slot, text) = match part.split_once('=') {
                    Some((n, f)) => match tree.meta.impact_names.iter().position(|m| m == n.trim()) {
                        Some(c) => (c, f),
                        None => continue,
                    },
                    None if named => return Err(format!("the part {part:?} of the tolerances has no name, and the others do")),
                    None => (pos, *part),
                };
                let f = match num::parse_rational(text.trim()) {
                    Some(f) if f >= zero && f <= one => f,
                    _ => return Err(format!("a tolerance wants a fraction between 0 and 1, found {text:?}")),
                };
                out[slot] = f;
            }
            if out.iter().all(|f| f.numer().sign() == num_bigint::Sign::NoSign) {
                None
            } else {
                Some(out)
            }
        }
    };

    // the flags the form gives, read by the very code the command line uses,
    // so that a default is never written twice
    let flags = vec![
        "--workers".to_string(),
        workers,
        "--ablation".to_string(),
        ablation,
    ];
    let args = parse_args(&flags)?;
    let opts = SearchOptions::from_args(&args)?;
    Ok(Prepared { tree, labels, time, max_iter, limit, window, tolerance, opts, workers: workers_n })
}

/// The options of the frontier search of the page, in one phase against
/// the antichain, with a live view and a cancel flag when the search runs
/// in the background.
fn options(
    prep: &Prepared,
    live: Option<std::sync::Arc<pareto::Live>>,
    cancel: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
) -> pareto::Options {
    pareto::Options {
        onephase: true,
        oracle: pareto::OracleKind::Antichain,
        extract: pareto::Extract::Tightest,
        certificates: pareto::Certificates::Both,
        print_certificate: false,
        // the page cannot take the limit away: a search with none does
        // not have to terminate, and this one answers a browser
        time_limit: Some(Duration::from_secs_f64(prep.limit)),
        anytime: None,
        precision: None,
        components: None,
        window: prep.window.clone(),
        tolerance: prep.tolerance.clone(),
        live: live.clone(),
        library: pareto::Library::Biodivine,
        search: Config {
            certificate: false,
            cancel,
            publish_open: live.is_some(),
            ..prep.opts.config::<Exact>(Vec::new(), true)
        },
    }
}

/// One point as the page reads it: the value, exact and approximate,
/// whether it is final, what its loops would still pay, and every decision
/// of its strategy as data, the point reached and the branch taken, each
/// node with its identifier, its name and whether it is a task.
fn point_json(tree: &Tree, labels: &[String], p: &pareto::Point) -> String {
    // the value as it is shown, the sign turned back on a maximized component
    let shown = tree.shown_vec(&p.value);
    let value: Vec<String> = shown.iter().map(|v| esc(&v.to_string())).collect();
    let approx: Vec<String> = shown.iter().map(|v| jnum6(pareto::approx(v))).collect();
    let steps: Vec<String> = match &p.witness {
        None => Vec::new(),
        Some(w) => w
            .decisions
            .iter()
            .map(|(history, action)| {
                let after: Vec<String> = history.iter().map(|&id| node_json(tree, id, labels)).collect();
                format!(
                    "{{\"after\": [{}], \"take\": {}, \"sentence\": {}}}",
                    after.join(", "),
                    node_json(tree, *action, labels),
                    esc(&decision(tree, history, *action))
                )
            })
            .collect(),
    };
    // what the loops would still pay beyond the bound on their passes,
    // when the witness met a forced exit
    let continued = if p.continued.iter().any(|g| g.numer().sign() != num_bigint::Sign::NoSign) {
        let k = p.continued.len() / 2;
        // the numbers at the precision of a double, a small gap being
        // the usual one, and the rationals beside them
        let side = |v: &[num_rational::BigRational]| -> String {
            let parts: Vec<String> = v.iter().map(|x| jnum(pareto::approx(x))).collect();
            format!("[{}]", parts.join(", "))
        };
        let exact = |v: &[num_rational::BigRational]| -> String {
            let parts: Vec<String> = v.iter().map(|x| esc(&x.to_string())).collect();
            format!("[{}]", parts.join(", "))
        };
        let (up, low) = tree.shown_bounds(&p.continued[k..], &p.continued[..k]);
        format!(
            "{{\"low\": {}, \"up\": {}, \"low_exact\": {}, \"up_exact\": {}}}",
            side(&low),
            side(&up),
            exact(&low),
            exact(&up)
        )
    } else {
        "null".to_string()
    };
    format!(
        "{{\"value\": [{}], \"approx\": [{}], \"certified\": {}, \"continued\": {}, \"steps\": [{}]}}",
        value.join(", "),
        approx.join(", "),
        p.certified,
        continued,
        steps.join(", ")
    )
}

/// The names of the components as the page reads them, the impacts by the
/// names the process gives them and an index where it gives none.
fn names_json(tree: &Tree, components: &[usize]) -> String {
    let names: Vec<String> = components
        .iter()
        .map(|&c| match tree.meta.impact_names.get(c) {
            Some(n) if !n.is_empty() => esc(n),
            _ => esc(&format!("impact {c}")),
        })
        .collect();
    names.join(", ")
}

/// The answer of a search that ended, cut or complete.
fn frontier_json(prep: &Prepared, report: &pareto::Report, seconds: f64) -> String {
    let tree = &prep.tree;
    let status = match &report.status {
        pareto::Status::Complete => "complete",
        pareto::Status::Timeout => "cut at the limit",
        pareto::Status::Failed(_) => "failed",
    };
    let comps: Vec<String> = report.components.iter().map(|c| c.to_string()).collect();
    let points: Vec<String> = report.points.iter().map(|p| point_json(tree, &prep.labels, p)).collect();
    let cut = matches!(report.status, pareto::Status::Timeout);
    let grey = if report.outside > 0 { " What the search found outside them is drawn grey." } else { "" };
    let mut note = if let pareto::Status::Failed(reason) = &report.status {
        format!(
            "The search failed: {reason}. A macro step, the forced moves between two choices, branches into more \
             outcomes than the search holds, so the points found, if any, are not known to be the frontier; a \
             smaller max iterations may fit."
        )
    } else if report.points.is_empty() && report.window.is_some() {
        if cut {
            format!(
                "The search was cut before a point landed within the bounds; a longer run may find one.{grey}"
            )
        } else {
            format!("No strategy keeps its expected values within the bounds: the frontier is empty there.{grey}")
        }
    } else if report.points.is_empty() {
        "The search was cut before it found a point; a longer run may find one.".to_string()
    } else if cut {
        format!(
            "The search was cut. {} of the {} points are final, no open branch \
             being able to beat them; the others are provisional, a longer run may replace them, \
             and the frontier may hold points not found yet.",
            report.certified_points,
            report.points.len()
        )
    } else {
        "The search finished, so every point is an optimum and the set is the whole frontier."
            .to_string()
    };
    if let Some(a) = &report.tolerance {
        let parts: Vec<String> = a
            .iter()
            .enumerate()
            .filter(|(_, x)| x.numer().sign() != num_bigint::Sign::NoSign)
            .map(|(c, x)| {
                let name = tree.meta.impact_names.get(c).cloned().unwrap_or_else(|| format!("impact {c}"));
                format!("{} {name}", jnum6(pareto::approx(x)))
            })
            .collect();
        note.push_str(&format!(
            " Tolerances of {}: a point within them of a point found was left out, and every strategy is within them of a point reported.",
            parts.join(", ")
        ));
    }
    if !report.points.is_empty() && report.outside > 0 {
        note.push_str(&format!(
            " {} {} found outside the bounds {} drawn grey, as context.",
            report.outside,
            if report.outside == 1 { "point" } else { "points" },
            if report.outside == 1 { "is" } else { "are" }
        ));
    }
    format!(
        "{{\"status\": {}, \"seconds\": {:.6}, \"time\": {}, \"max_iter\": {}, \"maximized\": {}, \"window\": {}, \"outside\": {}, \"components\": [{}], \"names\": [{}], \"apexes\": {}, \
         \"expanded\": {}, \"note\": {}, \"outside_points\": {}, \"tolerance\": {}, \"points\": [{}]}}",
        esc(status),
        seconds,
        prep.time,
        prep.max_iter,
        maximized_json(tree),
        match &report.window {
            Some(w) => {
                let parts: Vec<String> = w
                    .iter()
                    .enumerate()
                    .map(|(c, (a, b))| {
                        let (a, b) = if tree.maximized.get(c).copied().unwrap_or(false) { (-b.clone(), -a.clone()) } else { (a.clone(), b.clone()) };
                        format!("[{}, {}]", jnum(pareto::approx(&a)), jnum(pareto::approx(&b)))
                    })
                    .collect();
                format!("[{}]", parts.join(", "))
            }
            None => "null".to_string(),
        },
        report.outside,
        comps.join(", "),
        names_json(tree, &report.components),
        report.accepts,
        report.search.expanded,
        esc(&note),
        outside_json(tree, &report.outside_points),
        match &report.tolerance {
            Some(a) => {
                let parts: Vec<String> = a.iter().map(|x| jnum6(pareto::approx(x))).collect();
                format!("[{}]", parts.join(", "))
            }
            None => "null".to_string(),
        },
        points.join(", ")
    )
}

/// Whether the two bounds at the initial state, which the search reads before
/// its clock starts, end within the time limit: in long rationals they may not.
/// The reason, when they do not.
fn bounds_in_time(prep: &Prepared, cancel: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>) -> Result<(), String> {
    let s0 = crate::state::Engine::new(&prep.tree).initial_state();
    let clock = crate::bound::Clock { deadline: Some(Instant::now() + Duration::from_secs_f64(prep.limit)), cancel };
    match crate::bound::bounds_until::<Exact>(&prep.tree, &s0, prep.tree.max_iter, &clock) {
        Some(_) => Ok(()),
        None => Err(format!("the two bounds of the process did not end within the time limit of {} seconds", prep.limit)),
    }
}

/// Runs one search and answers with the frontier, every point with the
/// strategy that attains it.
fn frontier(body: &str, bench: &Bench) -> String {
    let prep = match prepare(body, bench) {
        Ok(p) => p,
        Err(e) => return err(&e),
    };
    let _one_at_a_time = SEARCH.lock().unwrap_or_else(|e| e.into_inner());
    let started = Instant::now();
    if let Err(e) = bounds_in_time(&prep, None) {
        return err(&e);
    }
    match pareto::frontier(&prep.tree, options(&prep, None, None)) {
        Ok(report) => frontier_json(&prep, &report, started.elapsed().as_secs_f64()),
        Err(e) => err(&e),
    }
}

/// The explanation of one strategy: the process as the page has it, with
/// the time and the bound on the passes of the search that found the
/// strategy, and the decisions of the strategy, `history>action;...`, a
/// history being its trail, identifiers joined by dots; the field
/// `explain_budget`, optional, is the budget of the exact search, a number
/// of sets or `inf` for none, the default of the explainer when absent. The
/// answer is one decision tree per choice node, on the draws alone.
fn explain_of(body: &str, bench: &Bench) -> String {
    let prep = match prepare(body, bench) {
        Ok(p) => p,
        Err(e) => return err(&e),
    };
    let text = field(body, "decisions").unwrap_or_default();
    let mut decisions: Vec<(Vec<u32>, u32)> = Vec::new();
    for part in text.split(';').filter(|p| !p.trim().is_empty()) {
        let Some((h, a)) = part.split_once('>') else {
            return err("a decision is written history>action");
        };
        let mut dh = Vec::new();
        for x in h.split('.').filter(|x| !x.trim().is_empty()) {
            match x.trim().parse::<u32>() {
                Ok(id) if id >= 1 && id <= prep.tree.n_nodes => dh.push(id),
                _ => return err(&format!("{x:?} is no node of the process")),
            }
        }
        match a.trim().parse::<u32>() {
            Ok(id) if id >= 1 && id <= prep.tree.n_nodes => decisions.push((dh, id)),
            _ => return err(&format!("{a:?} is no node of the process")),
        }
    }
    let budget = match field(body, "explain_budget").as_deref().map(str::trim) {
        None | Some("") => Some(crate::explain::DEFAULT_BUDGET),
        Some("inf") | Some("none") => None,
        Some(t) => match t.parse::<usize>() {
            Ok(b) => Some(b),
            Err(_) => return err(&format!("explain_budget wants a non-negative integer or inf, found {t:?}")),
        },
    };
    match crate::explain::explain_within(&prep.tree, &decisions, budget) {
        Ok(e) => {
            let node = |id: u32| node_json(&prep.tree, id, &prep.labels);
            format!("{{\"explanations\": {}}}", crate::explain::render_json(&prep.tree, &e, &node))
        }
        Err(e) => err(&e),
    }
}

// -- a search in the background, watched by the page ----------------------

/// A search started by the page and run in a thread of its own: the page
/// takes snapshots of it while it runs, the minimal apexes of the moment,
/// pauses its view to explore one, and may cut the search; the answer of
/// the search is kept once it ends.
struct Session {
    prep: Prepared,
    live: std::sync::Arc<pareto::Live>,
    cancel: std::sync::Arc<std::sync::atomic::AtomicBool>,
    started: Instant,
    done: Mutex<Option<String>>,
}

static SESSIONS: Mutex<Vec<(u64, std::sync::Arc<Session>)>> = Mutex::new(Vec::new());
static NEXT_SESSION: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

fn session_of(body: &str) -> Result<std::sync::Arc<Session>, String> {
    let id: u64 = field(body, "id")
        .and_then(|v| v.parse().ok())
        .ok_or_else(|| "no search was named".to_string())?;
    let sessions = SESSIONS.lock().unwrap_or_else(|e| e.into_inner());
    sessions
        .iter()
        .find(|(i, _)| *i == id)
        .map(|(_, s)| s.clone())
        .ok_or_else(|| format!("no search {id} is known"))
}

fn start(body: &str, bench: &Bench) -> String {
    let prep = match prepare(body, bench) {
        Ok(p) => p,
        Err(e) => return err(&e),
    };
    let workers = prep.workers;
    let session = std::sync::Arc::new(Session {
        prep,
        live: std::sync::Arc::new(pareto::Live::new(workers)),
        cancel: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
        started: Instant::now(),
        done: Mutex::new(None),
    });
    let id = NEXT_SESSION.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    // one line per search on the standard error, the log of the server:
    // what was asked, so that a run can be read back afterwards
    {
        let p = &session.prep;
        let window = match &p.window {
            Some(w) => {
                let parts: Vec<String> = w
                    .iter()
                    .map(|(a, b)| {
                        let f = |x: &Option<num_rational::BigRational>| x.as_ref().map(|r| format!("{}", pareto::approx(r))).unwrap_or_default();
                        format!("{}..{}", f(a), f(b))
                    })
                    .collect();
                parts.join(",")
            }
            None => "none".to_string(),
        };
        eprintln!(
            "search {id}: {} time={} maximized=[{}] window={window} workers={} limit={}s{}",
            field(body, "name").filter(|n| !n.trim().is_empty()).unwrap_or_else(|| "(unnamed)".to_string()),
            p.time,
            p.tree.maximized_names().join(","),
            p.workers,
            p.limit,
            format!(" max_iter={}", p.max_iter)
        );
    }
    {
        let mut sessions = SESSIONS.lock().unwrap_or_else(|e| e.into_inner());
        sessions.push((id, session.clone()));
        // the last few are kept, so that a page that lost one can ask again
        while sessions.len() > 6 {
            sessions.remove(0);
        }
    }
    let worker = session.clone();
    let spawned = std::thread::Builder::new().stack_size(64 << 20).spawn(move || {
        let _one_at_a_time = SEARCH.lock().unwrap_or_else(|e| e.into_inner());
        let started = Instant::now();
        if let Err(e) = bounds_in_time(&worker.prep, Some(worker.cancel.clone())) {
            eprintln!("search {id}: failed: {e}");
            *worker.done.lock().unwrap_or_else(|e| e.into_inner()) = Some(err(&e));
            return;
        }
        let json = match pareto::frontier(
            &worker.prep.tree,
            options(&worker.prep, Some(worker.live.clone()), Some(worker.cancel.clone())),
        ) {
            Ok(report) => {
                eprintln!(
                    "search {id}: {:?} after {:.2}s, {} points, {} final, {} apexes, {} outside the window",
                    report.status,
                    started.elapsed().as_secs_f64(),
                    report.points.len(),
                    report.certified_points,
                    report.accepts,
                    report.outside
                );
                frontier_json(&worker.prep, &report, started.elapsed().as_secs_f64())
            }
            Err(e) => {
                eprintln!("search {id}: failed: {e}");
                err(&e)
            }
        };
        *worker.done.lock().unwrap_or_else(|e| e.into_inner()) = Some(json);
    });
    match spawned {
        Ok(_) => format!("{{\"id\": {id}}}"),
        Err(e) => err(&format!("the search could not start: {e}")),
    }
}

/// The search as it stands: its answer once it ended, and while it runs
/// the minimal apexes of the moment, every one provisional, with the
/// counters of the oracle.
fn snapshot(body: &str) -> String {
    let session = match session_of(body) {
        Ok(s) => s,
        Err(e) => return err(&e),
    };
    if let Some(json) = session.done.lock().unwrap_or_else(|e| e.into_inner()).clone() {
        return json;
    }
    let prep = &session.prep;
    let apexes = session.live.apexes.lock().unwrap_or_else(|e| e.into_inner()).clone();
    let components: Vec<usize> = (0..prep.tree.k).collect();
    let mut minimal = pareto::minimal_apexes(&apexes, &components);
    minimal.sort_by(|a, b| a.value.cmp(&b.value));
    // an apex is final already when no node open at this moment has its
    // lower bound below it, as the certificate of a cut would say now;
    // until every worker has said what it holds, nothing is final
    let open = session.live.open_now();
    for p in minimal.iter_mut() {
        p.certified = match &open {
            None => false,
            Some(open) => !open.iter().any(|v| components.iter().all(|&c| v[c] <= p.value[c])),
        };
    }
    // the window as the search reads it: a point outside is set apart,
    // drawn as context and never a point of the frontier, as in the answer
    let (minimal, dropped): (Vec<pareto::Point>, Vec<pareto::Point>) = match &prep.window {
        None => (minimal, Vec::new()),
        Some(w) => minimal.into_iter().partition(|p| {
            (0..p.value.len()).all(|c| match w.get(c) {
                Some((lo, hi)) => {
                    lo.as_ref().map_or(true, |l| *l <= p.value[c]) && hi.as_ref().map_or(true, |h| p.value[c] <= *h)
                }
                None => true,
            })
        }),
    };
    let open_bounds = open.as_ref().map(|o| o.len()).unwrap_or(0);
    let final_points = minimal.iter().filter(|p| p.certified).count();
    let comps: Vec<String> = components.iter().map(|c| c.to_string()).collect();
    let points: Vec<String> = minimal.iter().map(|p| point_json(&prep.tree, &prep.labels, p)).collect();
    format!(
        "{{\"live\": true, \"status\": \"building\", \"seconds\": {:.3}, \"time\": {}, \"max_iter\": {}, \"maximized\": {}, \"window\": null, \"outside\": {}, \
         \"components\": [{}], \"names\": [{}], \"apexes\": {}, \"reads\": {}, \"pruned\": {}, \"open_bounds\": {}, \
         \"note\": {}, \"outside_points\": {}, \"points\": [{}]}}",
        session.started.elapsed().as_secs_f64(),
        prep.time,
        prep.max_iter,
        maximized_json(&prep.tree),
        dropped.len(),
        comps.join(", "),
        names_json(&prep.tree, &components),
        apexes.len(),
        session.live.reads.load(std::sync::atomic::Ordering::Relaxed),
        session.live.pruned.load(std::sync::atomic::Ordering::Relaxed),
        open_bounds,
        esc(&format!(
            "The search is running: the points are the minimal apexes recorded so far, {final_points} of them final already, no open node being able to beat them, the others provisional. Pause to explore the snapshot, stop to cut the search with its certificate."
        )),
        outside_json(&prep.tree, &dropped),
        points.join(", ")
    )
}

fn stop(body: &str) -> String {
    match session_of(body) {
        Ok(s) => {
            s.cancel.store(true, std::sync::atomic::Ordering::Relaxed);
            eprintln!("search {}: stop asked by the page", field(body, "id").unwrap_or_default());
            "{\"ok\": true}".to_string()
        }
        Err(e) => err(&e),
    }
}
