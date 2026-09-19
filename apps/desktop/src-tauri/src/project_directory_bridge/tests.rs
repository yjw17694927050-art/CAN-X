//! Tests for the desktop project-directory bridge.
//!
//! Two kinds of evidence live here, and the difference matters:
//!
//! * **Behavioural tests** run [`resolve_selected_directory`] and [`directory_text`]
//!   against real values and a real temporary directory. They prove the contract the
//!   bridge claims — cancel is `None`, a selection is the chosen path, a refusal is a
//!   declared code — without ever automating an OS dialog.
//! * **Architecture regressions** read this crate's own source and prove *structural*
//!   facts no runtime test could: that the registered command accepts no caller path,
//!   and that this module contains no project-validation logic at all. A bridge that
//!   peeked at a manifest would be the wrong authority whether or not a test happened
//!   to call it, so the source itself is what gets asserted.

use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};

use tauri_plugin_dialog::FilePath;

use super::{
    ProjectDirectoryBridgeError, SELECT_PROJECT_DIRECTORY_COMMAND, directory_text,
    resolve_selected_directory,
};

/// Every failure code this bridge can produce, so a test can assert that a refusal is
/// one of the declared ones rather than merely "an error".
const DECLARED_CODES: [&str; 3] = [
    "desktop.project_directory_dialog_failed",
    "desktop.project_directory_unavailable",
    "desktop.project_directory_not_representable",
];

fn decided_code(error: &ProjectDirectoryBridgeError) -> &str {
    assert!(
        DECLARED_CODES.contains(&error.code.as_str()),
        "undeclared bridge error code: {}",
        error.code
    );
    error.code.as_str()
}

fn serialized(value: &impl serde::Serialize) -> String {
    serde_json::to_string(value).expect("serialize")
}

// ---------------------------------------------------------------------------
// A throwaway directory, named so that a leak would be unmistakable.
// ---------------------------------------------------------------------------

static NEXT_TEMP_DIR: AtomicU32 = AtomicU32::new(0);

/// A uniquely named directory under the OS temp root, removed when it drops.
///
/// The name deliberately contains `secret-program`: the privacy test feeds a directory
/// whose path carries that marker into the bridge, and a distinctive marker is what
/// makes "the refusal never mentions it" an assertion that means something.
struct TempDir {
    path: PathBuf,
}

impl TempDir {
    const MARKER: &'static str = "secret-program";

    fn new() -> Self {
        let unique = NEXT_TEMP_DIR.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir().join(format!(
            "canx-{}-bridge-{}-{unique}",
            Self::MARKER,
            std::process::id()
        ));
        fs::create_dir_all(&path).expect("create the temporary directory");
        Self { path }
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

/// A path that contains the leak marker but cannot be represented as text.
#[cfg(windows)]
fn path_that_is_not_text() -> PathBuf {
    use std::ffi::OsString;
    use std::os::windows::ffi::OsStringExt;

    // A lone surrogate is not valid Unicode, so `Path::to_str` refuses it while the
    // rest of the name — including the marker — is still present in the raw bytes.
    let mut wide: Vec<u16> = format!("C:\\{}-", TempDir::MARKER).encode_utf16().collect();
    wide.push(0xD800);
    PathBuf::from(OsString::from_wide(&wide))
}

#[cfg(unix)]
fn path_that_is_not_text() -> PathBuf {
    use std::ffi::OsStr;
    use std::os::unix::ffi::OsStrExt;

    let mut bytes = b"/tmp/".to_vec();
    bytes.push(0xFF); // not valid UTF-8
    bytes.extend_from_slice(TempDir::MARKER.as_bytes());
    PathBuf::from(OsStr::from_bytes(&bytes))
}

// ---------------------------------------------------------------------------
// Behaviour: cancel and selection.
// ---------------------------------------------------------------------------

#[test]
fn a_dismissed_dialog_resolves_to_none() {
    let resolved = resolve_selected_directory(None).expect("a dismissal is not a failure");

    assert_eq!(resolved, None, "cancel is control flow, and it is `null`");
}

#[test]
fn a_chosen_directory_resolves_to_the_path_the_dialog_returned() {
    let directory = TempDir::new();
    let chosen = directory.path.clone();

    let resolved = resolve_selected_directory(Some(FilePath::from(chosen.clone())))
        .expect("a chosen directory is a success");

    assert_eq!(
        resolved,
        Some(
            chosen
                .to_str()
                .expect("the fixture path is text")
                .to_owned()
        ),
        "the bridge returns exactly what the dialog handed it"
    );
}

#[test]
fn the_published_value_is_the_directory_path_and_nothing_else() {
    let directory = TempDir::new();
    let chosen = directory.path.clone();

    let resolved = resolve_selected_directory(Some(FilePath::from(chosen.clone())))
        .expect("a chosen directory is a success");
    let text = serialized(&resolved);
    let value: serde_json::Value = serde_json::from_str(&text).expect("payload is JSON");

    // The selection itself is the whole payload: a single JSON string equal to the
    // chosen path. There is no object, and so no second field under which anything
    // the renderer did not ask for could travel.
    assert_eq!(
        value.as_str(),
        chosen.to_str(),
        "the payload is the path, unadorned: {text}"
    );
    assert!(
        text.contains(TempDir::MARKER),
        "the marker is present in the fixture, so the privacy assertions below are not vacuous"
    );
}

#[test]
fn a_directory_path_round_trips_through_the_text_conversion() {
    let directory = TempDir::new();

    let text = directory_text(&directory.path).expect("the fixture path is valid text");

    assert_eq!(PathBuf::from(&text), directory.path);
}

// ---------------------------------------------------------------------------
// Behaviour: the refusals.
// ---------------------------------------------------------------------------

#[test]
fn a_location_that_is_not_a_path_is_refused_as_unavailable() {
    // A non-`file://` URL is what a mobile content URI looks like to this type; it has
    // no filesystem path to hand to the Runtime.
    let picked: FilePath = "https://example.com/not-a-directory"
        .parse()
        .expect("parsing is infallible");

    let error = resolve_selected_directory(Some(picked)).expect_err("refuse a non-path location");

    assert_eq!(
        decided_code(&error),
        "desktop.project_directory_unavailable"
    );
    assert!(error.recoverable);
}

#[test]
#[cfg(any(windows, unix))]
fn a_path_that_is_not_text_is_refused_as_not_representable() {
    let picked = FilePath::from(path_that_is_not_text());

    let error = resolve_selected_directory(Some(picked)).expect_err("refuse a non-text path");

    assert_eq!(
        decided_code(&error),
        "desktop.project_directory_not_representable"
    );
    assert!(!error.recoverable);
}

#[test]
fn every_failure_is_a_declared_typed_code_with_static_text() {
    for (expected, error) in [
        (
            "desktop.project_directory_dialog_failed",
            ProjectDirectoryBridgeError::dialog_failed(),
        ),
        (
            "desktop.project_directory_unavailable",
            ProjectDirectoryBridgeError::unavailable(),
        ),
        (
            "desktop.project_directory_not_representable",
            ProjectDirectoryBridgeError::not_representable(),
        ),
    ] {
        assert_eq!(decided_code(&error), expected);
        assert!(
            error.message.starts_with("The "),
            "the message is static text, not a rendering of an input: {}",
            error.message
        );
    }
}

// ---------------------------------------------------------------------------
// Privacy: a refusal may never carry the selection.
// ---------------------------------------------------------------------------

#[test]
#[cfg(any(windows, unix))]
fn a_refusal_never_quotes_the_directory_it_refused() {
    for (label, picked) in [
        ("non-text path", FilePath::from(path_that_is_not_text())),
        (
            "non-path location",
            "https://example.com/secret-program"
                .parse::<FilePath>()
                .expect("parsing is infallible"),
        ),
    ] {
        let error =
            resolve_selected_directory(Some(picked)).expect_err("both fixtures must be refused");
        let text = serialized(&error);

        assert!(
            !text.contains(TempDir::MARKER),
            "{label}: the selected directory leaked into the refusal: {text}"
        );
        assert!(
            !error.message.contains(TempDir::MARKER),
            "{label}: the marker reached the message text: {}",
            error.message
        );

        // The strongest form: the error has exactly three fields, so there is no
        // fourth one hiding a path under a name this test did not think to check.
        let value: serde_json::Value = serde_json::from_str(&text).expect("error is JSON");
        let object = value.as_object().expect("error is a JSON object");
        let mut keys: Vec<&str> = object.keys().map(String::as_str).collect();
        keys.sort_unstable();
        assert_eq!(keys, ["code", "message", "recoverable"]);
    }
}

// ---------------------------------------------------------------------------
// Architecture regressions: the source itself.
// ---------------------------------------------------------------------------

fn crate_source(relative: &str) -> &'static str {
    match relative {
        "lib.rs" => include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/lib.rs")),
        "project_directory_bridge.rs" => include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/src/project_directory_bridge.rs"
        )),
        other => panic!("unknown source file {other}"),
    }
}

/// The parameter lists of every `#[tauri::command]` declared in a source file.
///
/// The list is located after `fn`, not after the attribute: a visibility such as
/// `pub(crate)` carries parentheses of its own, and mistaking those for the parameter
/// list would let this check inspect the wrong thing while still reporting success.
fn command_parameter_lists(source: &str) -> Vec<String> {
    let marker = "#[tauri::command]";
    let mut lists = Vec::new();
    let mut rest = source;
    while let Some(index) = rest.find(marker) {
        rest = &rest[index + marker.len()..];
        let Some(function) = rest.find("fn ") else {
            break;
        };
        rest = &rest[function + "fn ".len()..];
        let Some(open) = rest.find('(') else { break };
        let Some(close) = rest[open..].find(')') else {
            break;
        };
        lists.push(rest[open + 1..open + close].trim().to_owned());
        rest = &rest[open + close..];
    }
    lists
}

#[test]
fn this_module_registers_exactly_one_command() {
    let parameters = command_parameter_lists(crate_source("project_directory_bridge.rs"));

    assert_eq!(
        parameters.len(),
        1,
        "adding a second command to this module is a security decision, not a detail"
    );
}

#[test]
fn the_selection_command_declares_a_single_injected_argument() {
    let parameters = command_parameter_lists(crate_source("project_directory_bridge.rs"));

    let arguments: Vec<&str> = parameters[0]
        .split(',')
        .map(str::trim)
        .filter(|argument| !argument.is_empty())
        .collect();

    assert_eq!(
        arguments,
        ["app: AppHandle<R>"],
        "the selection command must take nothing a caller can supply — the handle is \
         injected by Tauri, and there is no second argument for a renderer to fill"
    );
}

#[test]
fn the_selection_command_is_registered_in_the_shell() {
    let lib = crate_source("lib.rs");

    assert!(
        lib.contains("pub mod project_directory_bridge;"),
        "the module must be reachable from the crate root"
    );
    assert!(
        lib.contains("project_directory_bridge::select_project_directory"),
        "the command must be registered through the module that owns the filesystem \
         decision, not by bare name"
    );
}

#[test]
fn the_command_constant_names_the_registered_command() {
    assert_eq!(SELECT_PROJECT_DIRECTORY_COMMAND, "select_project_directory");
}

#[test]
fn the_module_carries_no_project_validation_logic() {
    let source = crate_source("project_directory_bridge.rs");

    // This bridge relays a selection; it does not judge it. The tokens below are the
    // fingerprints of a project-identity check, and their absence is the invariant that
    // keeps project authority with the Runtime.
    for forbidden in [
        "project.json",
        "project.db",
        "schema",
        "ProjectService",
        "serde_json",
        "read_to_string",
        "read_dir",
        "is_project",
    ] {
        assert!(
            !source.contains(forbidden),
            "the bridge must not inspect the selected directory for `{forbidden}`"
        );
    }
}

#[test]
fn the_bridge_publishes_no_side_channel_for_the_selection() {
    let source = crate_source("project_directory_bridge.rs");

    // The command's return value is the only thing this bridge publishes. A log line,
    // an event or a debug print would be a second channel out of the same function, and
    // a selection that leaked through it would never show up in the return type a
    // reviewer reads — so the absence of such a channel is asserted, not assumed.
    //
    // The tokens are spelled as whole macro paths (`log::warn!`, not `log::`) so that a
    // module path such as `tauri_plugin_dialog::` — which merely contains the letters —
    // is not mistaken for a logging call.
    for forbidden in [
        "println!",
        "print!",
        "eprintln!",
        "dbg!",
        "log::trace!",
        "log::debug!",
        "log::info!",
        "log::warn!",
        "log::error!",
        "tracing::",
        ".emit(",
        "console",
    ] {
        assert!(
            !source.contains(forbidden),
            "the bridge must not publish the selection through `{forbidden}`"
        );
    }
}
