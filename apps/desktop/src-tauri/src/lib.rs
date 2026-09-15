pub mod runtime_sidecar;

use std::env;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use runtime_sidecar::{LifecycleState, RuntimeSidecar};
use tauri::{Manager, RunEvent};

struct ManagedRuntime {
    python: Option<PathBuf>,
    address: SocketAddr,
    inner: Mutex<ManagedRuntimeInner>,
}

struct ManagedRuntimeInner {
    sidecar: Option<RuntimeSidecar>,
    last_error: Option<String>,
}

impl ManagedRuntime {
    fn discover() -> Self {
        let python = env::var_os("CANX_RUNTIME_PYTHON")
            .map(PathBuf::from)
            .or_else(find_workspace_python);
        Self {
            python,
            address: SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 8765),
            inner: Mutex::new(ManagedRuntimeInner {
                sidecar: None,
                last_error: None,
            }),
        }
    }

    fn start(&self) -> Result<String, String> {
        let python = self
            .python
            .as_deref()
            .ok_or_else(|| "runtime.python_unavailable: set CANX_RUNTIME_PYTHON".to_owned())?;
        let mut inner = self.inner.lock().map_err(|error| error.to_string())?;
        if inner.sidecar.is_some() {
            return Ok("ready".to_owned());
        }
        let mut sidecar = RuntimeSidecar::spawn(python, self.address).map_err(|error| {
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
        .manage(runtime)
        .invoke_handler(tauri::generate_handler![runtime_status, restart_runtime])
        .build(tauri::generate_context!())
        .expect("failed to build the CAN-X desktop shell");
    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit) {
            let _ = handle.state::<ManagedRuntime>().shutdown();
        }
    });
}
