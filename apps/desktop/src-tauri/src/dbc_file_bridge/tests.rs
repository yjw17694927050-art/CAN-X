//! Tests for the desktop DBC file bridge.
//!
//! Two kinds of evidence live here, and the difference matters:
//!
//! * **Behavioural tests** run [`read_selected_dbc`] and
//!   [`read_bounded_exact_bytes`] against a real filesystem and a real reader. They
//!   prove the rules the bridge claims to enforce, on actual bytes and actual files,
//!   without ever automating an OS dialog.
//! * **Architecture regressions** read this crate's own source and capability file
//!   and prove a *structural* fact that no runtime test could: that the registered
//!   command surface has no way for a renderer to name a file. A command that took a
//!   path would be a security hole whether or not a test happened to call it, so the
//!   surface itself is what gets asserted.

use std::fs;
use std::io::{self, Read};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};

use base64::Engine as _;

use super::{
    BASE64, DBC_EXTENSION, DbcFileBridgeError, MAX_DBC_IMPORT_BYTES, SELECT_DBC_FILE_COMMAND,
    SelectedDbcContent, read_bounded_exact_bytes, read_selected_dbc,
};

/// Every failure code this bridge can produce, so a test can assert that a refusal
/// is one of the declared ones rather than merely "an error".
const DECLARED_CODES: [&str; 5] = [
    "desktop.dbc_file_invalid_type",
    "desktop.dbc_file_empty",
    "desktop.dbc_file_too_large",
    "desktop.dbc_file_read_failed",
    "desktop.dbc_dialog_failed",
];

fn decided_code(error: &DbcFileBridgeError) -> &str {
    assert!(
        DECLARED_CODES.contains(&error.code.as_str()),
        "undeclared bridge error code: {}",
        error.code
    );
    error.code.as_str()
}

// ---------------------------------------------------------------------------
// A throwaway directory, named so that a leak would be unmistakable.
// ---------------------------------------------------------------------------

static NEXT_TEMP_DIR: AtomicU32 = AtomicU32::new(0);

/// A uniquely named directory under the OS temp root, removed when it drops.
///
/// The name deliberately contains `secret-program`: the privacy tests assert that
/// the *directory* never appears in anything the bridge publishes or reports, and a
/// distinctive marker is what makes that assertion mean something.
struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(label: &str) -> Self {
        let unique = NEXT_TEMP_DIR.fetch_add(1, Ordering::SeqCst);
        let path = std::env::temp_dir().join(format!(
            "canx-dbc-bridge-{label}-{}-{unique}",
            std::process::id()
        ));
        fs::create_dir_all(&path).expect("create the temporary directory");
        Self { path }
    }

    /// The directory name that must never cross the bridge.
    const MARKER: &'static str = "secret-program";

    fn write(&self, name: &str, bytes: &[u8]) -> PathBuf {
        let path = self.path.join(name);
        fs::write(&path, bytes).expect("write a fixture file");
        path
    }

    fn child(&self, name: &str) -> PathBuf {
        let path = self.path.join(name);
        fs::create_dir_all(&path).expect("create a nested directory");
        path
    }

    fn join(&self, name: &str) -> PathBuf {
        self.path.join(name)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn decode_base64(text: &str) -> Vec<u8> {
    BASE64
        .decode(text)
        .expect("the bridge emits decodable Base64")
}

fn serialized(value: &impl serde::Serialize) -> String {
    serde_json::to_string(value).expect("serialize")
}

// ---------------------------------------------------------------------------
// Behaviour: what the bridge accepts, and what it produces.
// ---------------------------------------------------------------------------

#[test]
fn a_valid_dbc_file_is_published_byte_for_byte() {
    let directory = TempDir::new(TempDir::MARKER);
    let bytes: Vec<u8> = (0u8..=255).cycle().take(4096).collect();
    let path = directory.write("vehicle.dbc", &bytes);

    let content = read_selected_dbc(&path).expect("accept a well-formed .dbc file");

    assert_eq!(content.source_name, "vehicle.dbc");
    assert_eq!(decode_base64(&content.content_base64), bytes);
}

#[test]
fn the_extension_is_compared_case_insensitively() {
    let directory = TempDir::new(TempDir::MARKER);
    let path = directory.write("BODY.DBC", b"VERSION \"\"\n");

    let content = read_selected_dbc(&path).expect("accept an uppercase extension");

    assert_eq!(content.source_name, "BODY.DBC");
}

#[test]
fn a_non_ascii_file_name_survives_as_the_source_name() {
    let directory = TempDir::new(TempDir::MARKER);
    let path = directory.write("车辆-总线.dbc", b"VERSION \"\"\n");

    let content = read_selected_dbc(&path).expect("accept a non-ASCII file name");

    assert_eq!(content.source_name, "车辆-总线.dbc");
}

#[test]
fn a_byte_order_mark_is_preserved() {
    let directory = TempDir::new(TempDir::MARKER);
    let bytes = b"\xEF\xBB\xBFVERSION \"\"\n".to_vec();
    let path = directory.write("bom.dbc", &bytes);

    let content = read_selected_dbc(&path).expect("accept a BOM-prefixed file");

    assert_eq!(decode_base64(&content.content_base64), bytes);
    assert_eq!(
        &decode_base64(&content.content_base64)[..3],
        b"\xEF\xBB\xBF",
        "the BOM must not be stripped"
    );
}

#[test]
fn bytes_that_are_not_utf8_are_preserved_exactly() {
    let directory = TempDir::new(TempDir::MARKER);
    // An incomplete UTF-8 sequence, a lone NUL and a legacy-codec byte: none of these
    // are valid UTF-8, and all of them occur in real DBC files written on Windows.
    let bytes = vec![0xFF, 0xFE, 0x80, 0x00, 0xC3, 0x28, 0x0D, 0x0A];
    let path = directory.write("legacy.dbc", &bytes);

    let content = read_selected_dbc(&path).expect("accept non-UTF-8 content");

    assert_eq!(decode_base64(&content.content_base64), bytes);
    assert!(
        std::str::from_utf8(&bytes).is_err(),
        "the fixture is only meaningful if it is not valid UTF-8"
    );
}

#[test]
fn carriage_returns_are_not_normalised() {
    let directory = TempDir::new(TempDir::MARKER);
    let bytes = b"VERSION \"\"\r\nNS_ :\r\n".to_vec();
    let path = directory.write("crlf.dbc", &bytes);

    let content = read_selected_dbc(&path).expect("accept CRLF content");

    assert_eq!(decode_base64(&content.content_base64), bytes);
}

#[test]
fn the_encoded_content_round_trips_to_the_original_bytes() {
    let directory = TempDir::new(TempDir::MARKER);
    let bytes: Vec<u8> = (0u8..=255).collect();
    let path = directory.write("round-trip.dbc", &bytes);

    let content = read_selected_dbc(&path).expect("accept the fixture");
    let decoded = decode_base64(&content.content_base64);

    assert_eq!(decoded.len(), bytes.len());
    assert_eq!(decoded, bytes, "decoded(content_base64) must be the file");
}

// ---------------------------------------------------------------------------
// Behaviour: the refusals.
// ---------------------------------------------------------------------------

#[test]
fn an_empty_file_is_refused_as_empty() {
    let directory = TempDir::new(TempDir::MARKER);
    let path = directory.write("empty.dbc", b"");

    let error = read_selected_dbc(&path).expect_err("refuse an empty file");

    assert_eq!(decided_code(&error), "desktop.dbc_file_empty");
    assert!(error.recoverable);
}

#[test]
fn a_file_that_is_not_a_dbc_is_refused_as_the_wrong_type() {
    let directory = TempDir::new(TempDir::MARKER);
    let path = directory.write("vehicle.txt", b"VERSION \"\"\n");

    let error = read_selected_dbc(&path).expect_err("refuse a .txt file");

    assert_eq!(decided_code(&error), "desktop.dbc_file_invalid_type");
}

#[test]
fn a_directory_is_refused_as_the_wrong_type() {
    let directory = TempDir::new(TempDir::MARKER);
    let path = directory.child("folder.dbc");

    let error = read_selected_dbc(&path).expect_err("refuse a directory");

    assert_eq!(decided_code(&error), "desktop.dbc_file_invalid_type");
}

#[test]
fn a_missing_file_is_refused_as_unreadable() {
    let directory = TempDir::new(TempDir::MARKER);
    let path = directory.join("absent.dbc");

    let error = read_selected_dbc(&path).expect_err("refuse a missing file");

    assert_eq!(decided_code(&error), "desktop.dbc_file_read_failed");
}

// ---------------------------------------------------------------------------
// Behaviour: the bound, including the two cases that are off by one.
// ---------------------------------------------------------------------------

#[test]
fn a_file_of_exactly_the_bound_is_accepted() {
    let directory = TempDir::new(TempDir::MARKER);
    let bytes = vec![0x41u8; MAX_DBC_IMPORT_BYTES as usize];
    let path = directory.write("at-the-bound.dbc", &bytes);

    let content = read_selected_dbc(&path).expect("accept a file of exactly the bound");
    let decoded = decode_base64(&content.content_base64);

    assert_eq!(decoded.len(), bytes.len());
    assert!(
        decoded == bytes,
        "a file at the bound is transferred intact"
    );
}

#[test]
fn one_byte_past_the_bound_is_refused() {
    let directory = TempDir::new(TempDir::MARKER);
    let bytes = vec![0x41u8; MAX_DBC_IMPORT_BYTES as usize + 1];
    let path = directory.write("past-the-bound.dbc", &bytes);

    let error = read_selected_dbc(&path).expect_err("refuse a file one byte past the bound");

    assert_eq!(decided_code(&error), "desktop.dbc_file_too_large");
    assert!(error.recoverable);
}

/// A reader that reports every byte it hands out, so "bounded" can be measured
/// rather than assumed.
struct CountingReader<R> {
    inner: R,
    handed_out: Arc<AtomicU64>,
}

impl<R: Read> Read for CountingReader<R> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let read = self.inner.read(buffer)?;
        self.handed_out.fetch_add(read as u64, Ordering::SeqCst);
        Ok(read)
    }
}

#[test]
fn an_endless_stream_is_never_read_beyond_the_bound() {
    // `io::repeat` never ends. If the bound were applied after reading everything —
    // the "read it all, then check the length" mistake — this test would never
    // return, and the allocation would grow without limit first.
    let handed_out = Arc::new(AtomicU64::new(0));
    let endless = CountingReader {
        inner: io::repeat(0x41),
        handed_out: Arc::clone(&handed_out),
    };

    // Matched rather than `expect_err`: the `Ok` arm holds a buffer of the entire
    // bound, and formatting it into a panic message would bury the failure in tens of
    // megabytes of hex.
    let error = match read_bounded_exact_bytes(endless) {
        Err(error) => error,
        Ok(_) => panic!("an endless stream must be refused rather than read to completion"),
    };

    assert_eq!(decided_code(&error), "desktop.dbc_file_too_large");
    assert_eq!(
        handed_out.load(Ordering::SeqCst),
        MAX_DBC_IMPORT_BYTES + 1,
        "the reader must be stopped one byte past the bound, not one byte earlier or later"
    );
}

#[test]
fn the_bounded_read_accepts_a_stream_exactly_at_the_bound() {
    let bytes = vec![0x7Fu8; MAX_DBC_IMPORT_BYTES as usize];

    let raw = read_bounded_exact_bytes(bytes.as_slice()).expect("accept a stream at the bound");

    assert!(
        raw == bytes,
        "a stream of exactly the bound is transferred intact"
    );
}

#[test]
fn the_bounded_read_refuses_an_empty_stream() {
    let error = read_bounded_exact_bytes(io::empty()).expect_err("refuse an empty stream");

    assert_eq!(decided_code(&error), "desktop.dbc_file_empty");
}

// ---------------------------------------------------------------------------
// Privacy: nothing the bridge publishes may carry a location.
// ---------------------------------------------------------------------------

#[test]
fn a_successful_payload_carries_the_basename_and_nothing_else() {
    let directory = TempDir::new(TempDir::MARKER);
    let path = directory.write("vehicle.dbc", b"VERSION \"\"\n");

    let content = read_selected_dbc(&path).expect("accept the fixture");
    let text = serialized(&content);

    assert!(
        text.contains("vehicle.dbc"),
        "the basename is the provenance"
    );
    assert!(
        !text.contains(TempDir::MARKER),
        "the containing directory must not cross the boundary: {text}"
    );
    assert!(
        !text.contains("canx-dbc-bridge-"),
        "no part of the temp directory name may cross the boundary: {text}"
    );

    // The strongest form: the payload has exactly two fields, so there is no third
    // one hiding a path under a name this test did not think to check.
    let value: serde_json::Value = serde_json::from_str(&text).expect("payload is JSON");
    let object = value.as_object().expect("payload is a JSON object");
    let mut keys: Vec<&str> = object.keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(keys, ["content_base64", "source_name"]);
}

#[test]
fn a_refusal_never_quotes_the_file_it_refused() {
    let directory = TempDir::new(TempDir::MARKER);

    let wrong_type = directory.write("vehicle.txt", b"VERSION \"\"\n");
    let missing = directory.join("absent.dbc");
    let empty = directory.write("empty.dbc", b"");

    for (label, error) in [
        (
            "wrong extension",
            read_selected_dbc(&wrong_type).expect_err("refuse a .txt"),
        ),
        (
            "missing file",
            read_selected_dbc(&missing).expect_err("refuse a missing file"),
        ),
        (
            "empty file",
            read_selected_dbc(&empty).expect_err("refuse empty"),
        ),
    ] {
        let text = serialized(&error);
        assert!(
            !text.contains(TempDir::MARKER),
            "{label}: the containing directory leaked: {text}"
        );
        assert!(
            !text.contains("canx-dbc-bridge-"),
            "{label}: part of the directory name leaked: {text}"
        );
        assert!(
            !text.contains("vehicle.txt") && !text.contains("empty.dbc"),
            "{label}: the refused file name leaked: {text}"
        );
        assert!(
            error.message.starts_with("The selected"),
            "{label}: the message is static text, not a rendering of the input: {}",
            error.message
        );
    }
}

#[test]
fn the_oversize_refusal_states_the_bound_it_enforced() {
    let error = DbcFileBridgeError::too_large();
    let text = serialized(&error);

    assert_eq!(decided_code(&error), "desktop.dbc_file_too_large");
    assert!(
        error.message.contains(&MAX_DBC_IMPORT_BYTES.to_string()),
        "the message must name the bound that was exceeded: {}",
        error.message
    );
    assert!(!text.contains(TempDir::MARKER));
}

// ---------------------------------------------------------------------------
// Architecture regressions: the command surface itself.
// ---------------------------------------------------------------------------

fn crate_source(relative: &str) -> &'static str {
    match relative {
        "lib.rs" => include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/lib.rs")),
        "dbc_file_bridge.rs" => {
            include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/src/dbc_file_bridge.rs"
            ))
        }
        other => panic!("unknown source file {other}"),
    }
}

/// The command names listed in `tauri::generate_handler![...]`.
///
/// Module paths are stripped, so a command registered through the module that owns
/// its filesystem decision compares equal to the same command registered by bare
/// name. Comments are removed first: the handler list is annotated, and an annotation
/// is not a command.
fn registered_command_names(lib_source: &str) -> Vec<String> {
    let source = lib_source
        .lines()
        .map(|line| line.split("//").next().unwrap_or_default())
        .collect::<Vec<_>>()
        .join("\n");
    let marker = "generate_handler![";
    let start = source
        .find(marker)
        .expect("the shell registers an invoke handler")
        + marker.len();
    let end = source[start..]
        .find(']')
        .expect("the invoke handler list is closed")
        + start;
    let mut names: Vec<String> = source[start..end]
        .split(',')
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
        .map(|entry| entry.rsplit("::").next().unwrap_or(entry).to_owned())
        .collect();
    names.sort_unstable();
    names
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
fn the_registered_command_surface_is_exactly_what_we_intend() {
    let names = registered_command_names(crate_source("lib.rs"));

    assert_eq!(
        names,
        ["restart_runtime", "runtime_status", SELECT_DBC_FILE_COMMAND],
        "the desktop shell must keep the runtime lifecycle commands and add the DBC bridge, \
         and must not grow a command this test has not reviewed"
    );
}

#[test]
fn no_first_party_command_accepts_a_caller_supplied_filesystem_path() {
    let mut checked = 0;
    for relative in ["lib.rs", "dbc_file_bridge.rs"] {
        for parameters in command_parameter_lists(crate_source(relative)) {
            checked += 1;
            let lowered = parameters.to_ascii_lowercase();
            for forbidden in ["path", "file_name", "source_name", "directory", "bytes"] {
                assert!(
                    !lowered.contains(forbidden),
                    "{relative}: a command must not accept `{forbidden}` from a caller: ({parameters})"
                );
            }
        }
    }
    assert_eq!(checked, 3, "every first-party command must be inspected");
}

#[test]
fn the_dbc_selection_command_declares_a_single_injected_argument() {
    let parameters = command_parameter_lists(crate_source("dbc_file_bridge.rs"));

    assert_eq!(parameters.len(), 1, "this module declares one command");
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
fn no_forbidden_file_reading_command_name_is_registered() {
    let names = registered_command_names(crate_source("lib.rs"));

    for forbidden in [
        "read_dbc_file",
        "read_file",
        "read_bytes",
        "load_file",
        "open_path",
        "read_path",
    ] {
        assert!(
            !names.iter().any(|name| name == forbidden),
            "`{forbidden}` would let a renderer name a file to read, and must never be registered"
        );
    }
}

#[test]
fn a_registered_command_is_not_exported_as_a_path_taking_helper() {
    // The helper that *does* take a path must stay unregistered: if it were ever
    // promoted to a command, the surface above would still look small while a
    // renderer gained the ability to read any file it could name.
    let bridge = crate_source("dbc_file_bridge.rs");
    let command_positions = command_parameter_lists(bridge).len();

    assert_eq!(
        command_positions, 1,
        "adding a second command to this module is a security decision, not a detail"
    );
    assert!(
        bridge.contains("fn read_selected_dbc(path: &Path)"),
        "the path-taking reader must exist as a plain function"
    );
    assert!(
        !bridge.contains("#[tauri::command]\nfn read_selected_dbc")
            && !bridge.contains("#[tauri::command]\npub fn read_selected_dbc")
            && !bridge.contains("#[tauri::command]\nfn read_bounded"),
        "the path-taking reader must never be registered as a command"
    );
}

#[test]
fn the_renderer_capability_grants_no_dialog_or_filesystem_permission() {
    let capabilities = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/capabilities/default.json"
    ));
    let lowered = capabilities.to_ascii_lowercase();

    for forbidden in ["dialog", "fs:", "\"fs\"", "shell", "process"] {
        assert!(
            !lowered.contains(forbidden),
            "the native dialog belongs to the Rust system layer; the renderer must not \
             be granted `{forbidden}` — capabilities: {capabilities}"
        );
    }
}

#[test]
fn the_declared_extension_is_the_one_the_dialog_filters_on() {
    assert_eq!(DBC_EXTENSION, "dbc");
    assert_eq!(SELECT_DBC_FILE_COMMAND, "select_dbc_file");
}

#[test]
fn the_bridge_bound_matches_the_runtime_http_import_bound() {
    // The desktop bound and the Runtime bound are two independent declarations of the
    // same number. If they drift, a file the user could select would be refused by the
    // next stage for a reason that has nothing to do with its content.
    let runtime_source = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../runtime/canx/api/dbc.py"
    ));

    assert!(
        runtime_source.contains("MAX_DBC_IMPORT_BYTES = 16 * 1024 * 1024"),
        "the Runtime's import bound changed; the desktop bridge bound must be revisited"
    );
    assert_eq!(MAX_DBC_IMPORT_BYTES, 16 * 1024 * 1024);
}

#[test]
fn the_selected_content_shape_is_the_runtime_import_shape() {
    // V0.3-08 will map this payload onto the Runtime's POST /dbc/assets body. The two
    // key names are the whole of that mapping, so they are asserted rather than
    // assumed: a rename on either side would break the next stage silently.
    let content = SelectedDbcContent {
        source_name: "vehicle.dbc".to_owned(),
        content_base64: "VkVSU0lPTg==".to_owned(),
    };
    let value: serde_json::Value = serde_json::from_str(&serialized(&content)).expect("JSON");

    assert_eq!(
        value["source_name"].as_str(),
        Some("vehicle.dbc"),
        "SelectedDbcContent.source_name maps to the Runtime's source_name"
    );
    assert_eq!(
        value["content_base64"].as_str(),
        Some("VkVSU0lPTg=="),
        "SelectedDbcContent.content_base64 maps to the Runtime's content_base64"
    );

    let runtime_source = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../runtime/canx/api/dbc.py"
    ));
    for field in [
        "project_path: str",
        "source_name: str",
        "content_base64: str",
    ] {
        assert!(
            runtime_source.contains(field),
            "the Runtime import contract no longer declares `{field}`"
        );
    }
}
