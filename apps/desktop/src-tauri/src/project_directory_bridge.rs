//! The desktop system layer's project-directory selection bridge.
//!
//! This module owns exactly one capability: turning a **user's explicit choice in a
//! native directory dialog** into the directory path that the Python Runtime already
//! treats as the input of its project-inspection contract. It is the filesystem half
//! of "open a project", and it exists so a renderer can name a project by *pointing at
//! it* without ever being able to read a path of its own choosing.
//!
//! ```text
//! User
//!   ↓
//! Native OS directory dialog        (trusted: opened by Rust, not by the renderer)
//!   ↓
//! Tauri trusted system layer
//!   ↓
//! the chosen directory path, as text
//!   ↓
//! typed TypeScript desktop bridge   (src/desktop/project-directory-bridge.ts)
//! ```
//!
//! Three properties are load-bearing, and each is enforced here rather than
//! documented and hoped for:
//!
//! * **No caller filesystem path, ever.** The only registered command,
//!   [`select_project_directory`], declares no argument a caller can supply. It opens
//!   the dialog itself and receives the chosen directory *internally*, from the OS.
//!   There is no `path`, no `directory` and no `project_path` for a renderer to
//!   populate, and no way to reach this function that would let one.
//! * **Selection is the whole authority.** This bridge decides nothing about what a
//!   project *is*: the directory the user points at is relayed as text and never
//!   inspected. Whether that directory is a CAN-X project — its layout, its manifest,
//!   its database — is the Runtime's decision, taken against the same path this bridge
//!   merely carries. The bridge proves no property of the selection; it transports it.
//! * **Cancel is control flow, not an error.** A dismissed dialog resolves to
//!   `Ok(None)`; the renderer sees `null`. Nothing downstream has to translate a
//!   "user changed their mind" signal out of an error channel.
//!
//! A failure never carries the selection. [`ProjectDirectoryBridgeError`] holds a code,
//! a human message and a recoverability flag; every message is static text, no
//! constructor accepts a path, and there is no field into which one could leak.

use std::path::Path;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Runtime};
use tauri_plugin_dialog::{DialogExt, FilePath};

/// The name of the Tauri command that opens the native directory dialog.
///
/// Exported so that the architecture regression test can assert the registered
/// command surface without restating the name, and so the TypeScript bridge and the
/// Rust layer cannot drift apart on a string.
pub const SELECT_PROJECT_DIRECTORY_COMMAND: &str = "select_project_directory";

/// A typed failure from the desktop project-directory bridge.
///
/// Tauri IPC is an architecture boundary exactly like HTTP is, so a failure that
/// crosses it carries the same three structured fields the rest of CAN-X uses: a
/// stable `code` a caller can branch on, a human `message`, and whether retrying could
/// reasonably succeed.
///
/// Every message here is static text. A refusal must never become a rendering of the
/// directory that was refused, so no constructor accepts a path, and there is no field
/// into which one could leak.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectDirectoryBridgeError {
    /// Stable diagnostic code, for example `desktop.project_directory_dialog_failed`.
    pub code: String,
    /// Human-readable explanation. Never contains a filesystem path.
    pub message: String,
    /// Whether the caller can fix this by trying again.
    pub recoverable: bool,
}

impl ProjectDirectoryBridgeError {
    /// Build one failure from a code and a human message.
    fn of(code: &str, message: &str, recoverable: bool) -> Self {
        Self {
            code: code.to_owned(),
            message: message.to_owned(),
            recoverable,
        }
    }

    /// The native dialog itself could not be opened or did not answer.
    #[must_use]
    pub fn dialog_failed() -> Self {
        Self::of(
            "desktop.project_directory_dialog_failed",
            "The project directory dialog could not be opened.",
            true,
        )
    }

    /// The dialog answered with a location that is not a filesystem path.
    ///
    /// A directory picker on desktop hands back a path, but the dialog location type
    /// is shared with mobile content URIs; anything this bridge cannot turn into a
    /// path is refused here rather than guessed at.
    #[must_use]
    pub fn unavailable() -> Self {
        Self::of(
            "desktop.project_directory_unavailable",
            "The selected location could not be resolved to a filesystem path.",
            true,
        )
    }

    /// The chosen directory exists but its path cannot be represented as text.
    ///
    /// A path is not guaranteed to be valid Unicode. Rather than lossily rewriting the
    /// user's selection into something the Runtime could not open, the bridge refuses.
    #[must_use]
    pub fn not_representable() -> Self {
        Self::of(
            "desktop.project_directory_not_representable",
            "The selected directory path cannot be represented as text.",
            false,
        )
    }
}

/// Open the native directory dialog and return the directory the user chooses.
///
/// The one command this module registers. Its signature is the security invariant, not
/// merely a detail of it: **it declares no argument a caller can supply.** The
/// `AppHandle` is injected by Tauri from the running application, and there is nothing
/// else — so there is no way for a renderer to name a directory, and no way to reach
/// this function that would let one.
///
/// ```text
/// arguments: none
/// ```
///
/// Returns:
/// * `Ok(Some(path))` — the user chose a directory; `path` is exactly what the dialog
///   returned, as text. This bridge does **not** judge whether that directory is a
///   project: that decision belongs to the Runtime, against this same path.
/// * `Ok(None)` — the user dismissed the dialog. Cancel is normal control flow; it is
///   not reported as a failure, and the renderer receives `null`.
/// * `Err(error)` — the choice could not be turned into a path: a dialog that failed to
///   open, a location that is not a path, or a path that is not valid text.
///
/// The blocking dialog API is run on the async runtime's blocking pool, never on the
/// main thread: the dialog pumps its own message loop, and Tauri's event loop has to
/// stay free to serve it.
///
/// `pub(crate)` rather than `pub` on purpose. Tauri's command macro only exports its
/// generated wrapper macros to the crate root when the function is fully `pub`, and
/// that would move them *out of* this module — leaving `generate_handler!` in `lib.rs`
/// with a module path and no macro to resolve it against. The command is assembled by
/// this crate and reachable only through it; there is no reason for it to be part of
/// the library's public surface.
#[tauri::command]
pub(crate) async fn select_project_directory<R: Runtime>(
    app: AppHandle<R>,
) -> Result<Option<String>, ProjectDirectoryBridgeError> {
    let handle = app.clone();
    let picked =
        tauri::async_runtime::spawn_blocking(move || handle.dialog().file().blocking_pick_folder())
            .await
            .map_err(|_| ProjectDirectoryBridgeError::dialog_failed())?;

    resolve_selected_directory(picked)
}

/// Turn a dialog outcome into the value the bridge publishes.
///
/// **Not a command.** It is a plain function that happens to accept the dialog's own
/// [`FilePath`] because that is what resolving a selection requires; the value reaches
/// it from the dialog in the process above and from nowhere else. Keeping it a plain
/// function is what makes both halves of the contract — a dismissed dialog and a chosen
/// directory — testable without automating an OS dialog, and it is also what keeps
/// "receives a location" from ever becoming a renderer-facing capability.
///
/// ```text
/// None                       → Ok(None)                       (the user dismissed it)
/// Some(path)                 → Ok(Some(path as text))
/// Some(non-path location)    → Err(desktop.project_directory_unavailable)
/// Some(path that is not text)→ Err(desktop.project_directory_not_representable)
/// ```
fn resolve_selected_directory(
    picked: Option<FilePath>,
) -> Result<Option<String>, ProjectDirectoryBridgeError> {
    let Some(chosen) = picked else {
        return Ok(None);
    };

    // `FilePath` is a path on desktop and a content URI on mobile; this bridge is a
    // desktop bridge, and anything else is not something it can hand to the Runtime.
    let path = chosen
        .into_path()
        .map_err(|_| ProjectDirectoryBridgeError::unavailable())?;

    directory_text(&path).map(Some)
}

/// Render a chosen directory path as the text the caller receives.
///
/// A refusal rather than a lossy rewrite: a path that is not valid Unicode is not
/// silently replaced with one that is, because the caller would then hold a string
/// that names a directory the user never chose.
fn directory_text(path: &Path) -> Result<String, ProjectDirectoryBridgeError> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(ProjectDirectoryBridgeError::not_representable)
}

#[cfg(test)]
mod tests;
