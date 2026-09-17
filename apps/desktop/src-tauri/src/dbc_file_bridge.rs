//! The desktop system layer's DBC file bridge.
//!
//! This module owns exactly one capability: turning a **user's explicit choice in a
//! native file dialog** into bounded, opaque DBC bytes that a renderer can hand to
//! the Python Runtime. It is the missing half of a boundary the Runtime half already
//! established — `POST /dbc/assets` accepts content and never a location — and it
//! exists so the renderer never needs either.
//!
//! ```text
//! User
//!   ↓
//! Native OS file dialog            (trusted: opened by Rust, not by the renderer)
//!   ↓
//! Tauri trusted system layer
//!   ↓
//! bounded exact-byte read          (refuses empty and oversized before publishing)
//!   ↓
//! SelectedDbcContent { source_name, content_base64 }
//!   ↓
//! typed TypeScript desktop bridge  (src/desktop/dbc-file-bridge.ts)
//! ```
//!
//! Four properties are load-bearing, and each is enforced here rather than
//! documented and hoped for:
//!
//! * **No caller filesystem path, ever.** The only registered command,
//!   [`select_dbc_file`], takes no caller-supplied argument at all. It opens the
//!   dialog itself and receives the chosen path *internally*, from the OS. There is
//!   no command in this crate — and no command this crate registers — through which
//!   a renderer could name a file to read.
//! * **No path comes back.** [`SelectedDbcContent`] carries a **basename** and
//!   encoded content and nothing else: no directory, no canonical path, no parent.
//!   [`DbcFileBridgeError`] carries a code, a human message and a recoverability
//!   flag, and its messages are static text that never quotes the file it refused.
//! * **Cancel is control flow, not an error.** A closed dialog resolves to
//!   `Ok(None)`; the renderer sees `null`. Nothing downstream has to translate a
//!   "user changed their mind" signal out of an error channel.
//! * **The read is bounded and byte-exact.** The bridge reads at most
//!   [`MAX_DBC_IMPORT_BYTES`] plus one byte, refuses what is empty or longer, and
//!   otherwise publishes the **exact original bytes** — Base64-encoded, never
//!   decoded as text, never re-encoded, never newline-normalised. Which encoding a
//!   DBC document is in is the Runtime's decision, and it needs the bytes to make it.
//!
//! The file *reading* half is deliberately separate from the *dialog* half —
//! [`read_selected_dbc`] takes a path and is an ordinary private function that can be
//! unit tested against a real filesystem, while only [`select_dbc_file`] touches the
//! OS dialog and only [`select_dbc_file`] is registered as a command. Reading a path
//! is not a capability; being *handed* one by a renderer would be, and that is what
//! the split keeps impossible.

use std::ffi::OsStr;
use std::fs::{self, File};
use std::io::Read;
use std::path::Path;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Runtime};
use tauri_plugin_dialog::DialogExt;

//: The desktop bridge's transfer and resource bound.
//:
//: Deliberately the same number as the Runtime's HTTP import guard so that anything
//: this bridge publishes can be submitted to `POST /dbc/assets` unchanged. It is a
//: **transport** bound and not a permanent property of the DBC domain: the domain
//: importer will happily parse a larger document handed to it by another trusted
//: caller. It exists so that a 5 GiB file the user clicked by accident cannot become
//: a 5 GiB allocation before anyone finds out it was the wrong file.
pub const MAX_DBC_IMPORT_BYTES: u64 = 16 * 1024 * 1024;

/// The only file extension this bridge accepts, compared case-insensitively.
pub const DBC_EXTENSION: &str = "dbc";

/// The name of the Tauri command that opens the native dialog.
///
/// Exported so that the architecture regression test can assert the registered
/// command surface without restating the name, and so the TypeScript bridge and the
/// Rust layer cannot drift apart on a string.
pub const SELECT_DBC_FILE_COMMAND: &str = "select_dbc_file";

/// The content of one DBC file the user explicitly chose.
///
/// Two fields, and the absence of a third is the design: there is no field for the
/// location the bytes came from, because a renderer that does not have it cannot
/// misuse it, and because provenance for a project-owned asset is the *name* the
/// file had — not the directory it happened to live in.
///
/// ```text
/// source_name      the file's basename only (for example `vehicle.dbc`)
/// content_base64   the exact original bytes, losslessly encoded
/// ```
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SelectedDbcContent {
    /// The basename of the file the user selected. Never a path.
    pub source_name: String,
    /// The exact original file bytes, standard Base64 with padding.
    pub content_base64: String,
}

/// A typed failure from the desktop file bridge.
///
/// Tauri IPC is an architecture boundary exactly like HTTP is, so a failure that
/// crosses it carries the same three structured fields the rest of CAN-X uses:
/// a stable `code` a caller can branch on, a human `message`, and whether retrying
/// with a different file could reasonably succeed.
///
/// Every message here is static text. A refusal must never become a rendering of
/// the file that was refused, so no constructor accepts a path, a directory or a
/// file name, and there is no field into which one could leak.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DbcFileBridgeError {
    /// Stable diagnostic code, for example `desktop.dbc_file_empty`.
    pub code: String,
    /// Human-readable explanation. Never contains a filesystem path.
    pub message: String,
    /// Whether the caller can fix this by trying again with a different file.
    pub recoverable: bool,
}

impl DbcFileBridgeError {
    /// Build one failure from a code and a human message.
    fn of(code: &str, message: &str, recoverable: bool) -> Self {
        Self {
            code: code.to_owned(),
            message: message.to_owned(),
            recoverable,
        }
    }

    /// The chosen entry is not a regular `.dbc` file (wrong extension, or a directory).
    #[must_use]
    pub fn invalid_type() -> Self {
        Self::of(
            "desktop.dbc_file_invalid_type",
            "The selected entry is not a DBC (.dbc) file.",
            true,
        )
    }

    /// The chosen file exists but holds no bytes.
    #[must_use]
    pub fn empty() -> Self {
        Self::of(
            "desktop.dbc_file_empty",
            "The selected DBC file is empty.",
            true,
        )
    }

    /// The chosen file is longer than the bridge is willing to transfer.
    ///
    /// The bound is interpolated from the constant rather than repeated here, so a
    /// caller reading the message and a caller reading [`MAX_DBC_IMPORT_BYTES`]
    /// cannot be told two different things.
    #[must_use]
    pub fn too_large() -> Self {
        Self::of(
            "desktop.dbc_file_too_large",
            &format!(
                "The selected DBC file is larger than the {MAX_DBC_IMPORT_BYTES} byte import bound."
            ),
            true,
        )
    }

    /// The chosen file could not be opened or read.
    ///
    /// Also the answer for an entry that vanished between the dialog closing and the
    /// read starting: from here it is a file that cannot be read, and the caller's
    /// remedy is the same.
    #[must_use]
    pub fn read_failed() -> Self {
        Self::of(
            "desktop.dbc_file_read_failed",
            "The selected DBC file could not be read.",
            true,
        )
    }

    /// The native dialog itself could not be opened or did not answer.
    #[must_use]
    pub fn dialog_failed() -> Self {
        Self::of(
            "desktop.dbc_dialog_failed",
            "The DBC file dialog could not be opened.",
            true,
        )
    }
}

/// Open the native file dialog and read the DBC file the user chooses.
///
/// The one command this module registers. Its signature is the security invariant,
/// not merely a detail of it: **it declares no argument a caller can supply.** The
/// `AppHandle` is injected by Tauri from the running application, and there is
/// nothing else — so there is no `path`, no `source_path`, no `file_path` for a
/// renderer to populate, and no way to reach this function that would let one.
///
/// ```text
/// arguments: none
/// ```
///
/// Returns:
/// * `Ok(Some(content))` — the user chose a file and it satisfied every rule.
/// * `Ok(None)` — the user dismissed the dialog. Cancel is normal control flow; it
///   is not reported as a failure, and the renderer receives `null`.
/// * `Err(error)` — the choice could not be turned into content: wrong type, empty,
///   oversized, unreadable, or a dialog that failed to open.
///
/// The blocking dialog API is run on the async runtime's blocking pool, never on the
/// main thread: the dialog pumps its own message loop, and Tauri's event loop has to
/// stay free to serve it.
///
/// `pub(crate)` rather than `pub` on purpose. Tauri's command macro only exports its
/// generated wrapper macros to the crate root when the function is fully `pub`, and
/// that would move them *out of* this module — leaving `generate_handler!` in
/// `lib.rs` with a module path and no macro to resolve it against. The command is
/// assembled by this crate and reachable only through it; there is no reason for it
/// to be part of the library's public surface.
#[tauri::command]
pub(crate) async fn select_dbc_file<R: Runtime>(
    app: AppHandle<R>,
) -> Result<Option<SelectedDbcContent>, DbcFileBridgeError> {
    let handle = app.clone();
    let picked = tauri::async_runtime::spawn_blocking(move || {
        handle
            .dialog()
            .file()
            .add_filter("DBC", &[DBC_EXTENSION])
            .blocking_pick_file()
    })
    .await
    .map_err(|_| DbcFileBridgeError::dialog_failed())?;

    // A dismissed dialog is a decision, not a fault.
    let Some(chosen) = picked else {
        return Ok(None);
    };

    // `FilePath` is a path on desktop and a content URI on mobile; this bridge is a
    // desktop bridge, and anything else is not something it can read.
    let path = chosen
        .into_path()
        .map_err(|_| DbcFileBridgeError::dialog_failed())?;

    read_selected_dbc(&path).map(Some)
}

/// Read one already-chosen path into the payload the bridge publishes.
///
/// **Not a command.** It is a private function that happens to accept a path because
/// that is what reading a file requires; the path reaches it from the dialog in the
/// process above and from nowhere else. Keeping it a plain function is what makes
/// the bounded read testable against a real filesystem without automating an OS
/// dialog, and it is also what keeps "accepts a path" from ever becoming a
/// renderer-facing capability.
///
/// Validation runs before the file is opened (so a directory or a `.txt` is refused
/// without touching its contents), the metadata check runs before the read (so an
/// obviously huge file is refused without materialising even the bound), and the
/// bounded read below is the guard that actually has to hold — metadata can be stale
/// the moment it is read, and is never the only safety check.
fn read_selected_dbc(path: &Path) -> Result<SelectedDbcContent, DbcFileBridgeError> {
    let source_name = validate_selected_dbc(path)?;

    let file = File::open(path).map_err(|_| DbcFileBridgeError::read_failed())?;

    // A fast precheck, never the guard: it saves the read for a file that is
    // obviously too large, and it is allowed to be wrong.
    if let Ok(metadata) = file.metadata() {
        if metadata.len() > MAX_DBC_IMPORT_BYTES {
            return Err(DbcFileBridgeError::too_large());
        }
    }

    let raw = read_bounded_exact_bytes(file)?;

    Ok(SelectedDbcContent {
        source_name,
        content_base64: BASE64.encode(&raw),
    })
}

/// Prove the chosen entry is a regular `.dbc` file, and return its basename.
///
/// The dialog's own filter is a convenience for the user, not a security invariant:
/// it is a hint to the OS picker and says nothing about a path that arrives by any
/// other route, so the rules are restated here for the file that was actually
/// chosen.
///
/// ```text
/// vehicle.dbc        ACCEPT
/// BODY.DBC           ACCEPT   (extension compared case-insensitively)
/// vehicle.txt        REJECT   → desktop.dbc_file_invalid_type
/// a directory        REJECT   → desktop.dbc_file_invalid_type
/// a missing file     REJECT   → desktop.dbc_file_read_failed
/// ```
///
/// Only the basename is returned, and it is the *only* thing about the location that
/// ever leaves this module.
fn validate_selected_dbc(path: &Path) -> Result<String, DbcFileBridgeError> {
    let metadata = fs::metadata(path).map_err(|_| DbcFileBridgeError::read_failed())?;
    if !metadata.is_file() {
        return Err(DbcFileBridgeError::invalid_type());
    }

    let name = path
        .file_name()
        .and_then(OsStr::to_str)
        .ok_or_else(DbcFileBridgeError::invalid_type)?;
    if !has_dbc_extension(name) {
        return Err(DbcFileBridgeError::invalid_type());
    }

    Ok(name.to_owned())
}

/// Whether a file name ends in `.dbc`, ignoring case.
fn has_dbc_extension(name: &str) -> bool {
    Path::new(name)
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case(DBC_EXTENSION))
}

/// Read a stream into memory, bounded by [`MAX_DBC_IMPORT_BYTES`], with no tolerance
/// for an empty result.
///
/// The bound is applied **to the read**, not to its result: the reader is capped at
/// the bound plus one byte, so the allocation can never exceed that no matter how
/// long the underlying file, stream or device turns out to be. A file that grows
/// after its metadata was read is therefore still refused — by the read, which is
/// the check that cannot be raced. `+ 1` is what makes "at the bound" and "past the
/// bound" different observations: stopping exactly at the bound could not tell a
/// file of exactly 16 MiB from a 5 GiB one.
///
/// The returned bytes are the *exact* bytes the stream produced. Nothing here
/// decodes, normalises, trims or re-encodes them: a BOM stays, a non-UTF-8 byte
/// sequence stays, and CRLF line endings stay exactly as the file had them.
///
/// Errors:
/// * [`DbcFileBridgeError::read_failed`] if the stream could not be read.
/// * [`DbcFileBridgeError::empty`] if it produced no bytes at all.
/// * [`DbcFileBridgeError::too_large`] if the bound was exceeded.
fn read_bounded_exact_bytes<R: Read>(reader: R) -> Result<Vec<u8>, DbcFileBridgeError> {
    let mut raw = Vec::new();
    reader
        .take(MAX_DBC_IMPORT_BYTES + 1)
        .read_to_end(&mut raw)
        .map_err(|_| DbcFileBridgeError::read_failed())?;

    if raw.is_empty() {
        return Err(DbcFileBridgeError::empty());
    }
    if u64::try_from(raw.len()).unwrap_or(u64::MAX) > MAX_DBC_IMPORT_BYTES {
        return Err(DbcFileBridgeError::too_large());
    }

    Ok(raw)
}

#[cfg(test)]
mod tests;
