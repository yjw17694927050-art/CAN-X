pub mod dbc_file_bridge;
pub mod runtime_sidecar;

use std::env;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use runtime_sidecar::{LifecycleState, RuntimeLaunch, RuntimeSidecar};
use tauri::{Manager, RunEvent};

struct ManagedRuntime {
    launch: Option<RuntimeLaunch>,
    address: SocketAddr,
    inner: Mutex<ManagedRuntimeInner>,
}

struct ManagedRuntimeInner {
    sidecar: Option<RuntimeSidecar>,
    last_error: Option<String>,
}

impl ManagedRuntime {
    fn discover() -> Self {
        Self {
            launch: resolve_launch(),
            address: SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8765),
            inner: Mutex::new(ManagedRuntimeInner {
                sidecar: None,
                last_error: None,
            }),
        }
    }

    fn start(&self) -> Result<String, String> {
        let launch = self.launch.as_ref().ok_or_else(|| {
            "runtime.command_unavailable: no packaged runtime or development interpreter".to_owned()
        })?;
        let mut inner = self.inner.lock().map_err(|error| error.to_string())?;
        if inner.sidecar.is_some() {
            return Ok("ready".to_owned());
        }
        let mut sidecar = RuntimeSidecar::spawn(launch, self.address).map_err(|error| {
            inner.last_error = Some(error.to_string());
            error.to_string()
        })?;
        sidecar
            .wait_until_ready(Duration::from_secs(10))
            .map_err(|error| {
                inner.last_error = Some(error.to_string());
                error.to_string()
            })?;
        inner.sidecar = Some(sidecar);
        inner.last_error = None;
        Ok("ready".to_owned())
    }

    fn status(&self) -> Result<String, String> {
        let mut inner = self.inner.lock().map_err(|error| error.to_string())?;
        if let Some(sidecar) = inner.sidecar.as_mut() {
            let state = sidecar.poll_status().map_err(|error| error.to_string())?;
            return Ok(state.label().to_owned());
        }
        Ok(if inner.last_error.is_some() {
            "failed"
        } else {
            "unavailable"
        }
        .to_owned())
    }

    fn shutdown(&self) -> Result<(), String> {
        let mut inner = self.inner.lock().map_err(|error| error.to_string())?;
        if let Some(mut sidecar) = inner.sidecar.take() {
            let state = sidecar
                .poll_status()
                .map_err(|error| error.to_string())?
                .clone();
            if !matches!(
                state,
                LifecycleState::Stopped | LifecycleState::Failed { .. }
            ) {
                sidecar
                    .shutdown(Duration::from_secs(5))
                    .map_err(|error| error.to_string())?;
            }
        }
        Ok(())
    }

    fn restart(&self) -> Result<String, String> {
        self.shutdown()?;
        self.start()
    }
}

/// Development resolution: an explicit interpreter, then the repository virtualenv.
#[cfg(debug_assertions)]
fn resolve_launch() -> Option<RuntimeLaunch> {
    env::var_os("CANX_RUNTIME_PYTHON")
        .map(PathBuf::from)
        .or_else(find_workspace_python)
        .map(|interpreter| RuntimeLaunch::PythonModule { interpreter })
}

/// Distribution resolution: only the bundled executable, never a system Python.
#[cfg(not(debug_assertions))]
fn resolve_launch() -> Option<RuntimeLaunch> {
    if let Some(path) = env::var_os("CANX_RUNTIME_EXECUTABLE") {
        let executable = PathBuf::from(path);
        if executable.is_file() {
            return Some(RuntimeLaunch::BundledExecutable { executable });
        }
    }
    find_bundled_executable().map(|executable| RuntimeLaunch::BundledExecutable { executable })
}

#[cfg(not(debug_assertions))]
fn find_bundled_executable() -> Option<PathBuf> {
    let directory = env::current_exe().ok()?.parent()?.to_path_buf();
    [
        directory.join("canx-runtime.exe"),
        directory.join("resources").join("canx-runtime.exe"),
    ]
    .into_iter()
    .find(|candidate| candidate.is_file())
}

#[cfg(debug_assertions)]
fn find_workspace_python() -> Option<PathBuf> {
    let mut directory = env::current_dir().ok()?;
    loop {
        let candidate = directory.join(".venv").join("Scripts").join("python.exe");
        if candidate.is_file() {
            return Some(candidate);
        }
        if !directory.pop() {
            return None;
        }
    }
}

#[tauri::command]
fn runtime_status(runtime: tauri::State<'_, ManagedRuntime>) -> Result<String, String> {
    runtime.status()
}

#[tauri::command]
fn restart_runtime(runtime: tauri::State<'_, ManagedRuntime>) -> Result<String, String> {
    runtime.restart()
}

pub fn run() {
    let runtime = ManagedRuntime::discover();
    let _ = runtime.start();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(runtime)
        .invoke_handler(tauri::generate_handler![
            runtime_status,
            restart_runtime,
            // The bridge's command is registered through its module path: it is the
            // module that owns the filesystem decision, and naming it here is what
            // keeps `lib.rs` an assembly point rather than a place where file access
            // is declared.
            dbc_file_bridge::select_dbc_file
        ])
        .build(tauri::generate_context!())
        .expect("failed to build the CAN-X desktop shell");
    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit) {
            let _ = handle.state::<ManagedRuntime>().shutdown();
        }
    });
}
