use std::fmt::{Display, Formatter};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LifecycleState {
    Unavailable,
    Starting,
    Ready,
    Stopping,
    Stopped,
    Failed { exit_code: Option<i32> },
}

impl LifecycleState {
    #[must_use]
    pub const fn label(&self) -> &'static str {
        match self {
            Self::Unavailable => "unavailable",
            Self::Starting => "starting",
            Self::Ready => "ready",
            Self::Stopping => "stopping",
            Self::Stopped => "stopped",
            Self::Failed { .. } => "failed",
        }
    }
}

#[derive(Debug)]
pub struct RuntimeLifecycle {
    state: LifecycleState,
}

impl RuntimeLifecycle {
    #[must_use]
    pub fn new() -> Self {
        Self {
            state: LifecycleState::Unavailable,
        }
    }

    #[must_use]
    pub fn state(&self) -> &LifecycleState {
        &self.state
    }

    pub fn on_spawned(&mut self) {
        self.state = LifecycleState::Starting;
    }

    pub fn on_ready(&mut self) {
        self.state = LifecycleState::Ready;
    }

    pub fn begin_shutdown(&mut self) {
        self.state = LifecycleState::Stopping;
    }

    pub fn on_child_exit(&mut self, exit_code: Option<i32>) {
        self.state = if self.state == LifecycleState::Stopping && exit_code == Some(0) {
            LifecycleState::Stopped
        } else {
            LifecycleState::Failed { exit_code }
        };
    }
}

impl Default for RuntimeLifecycle {
    fn default() -> Self {
        Self::new()
    }
}

/// How the desktop resolves and owns the runtime process.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RuntimeLaunch {
    /// Development: a Python interpreter running the `canx` module.
    PythonModule { interpreter: PathBuf },
    /// Distribution: the packaged `canx-runtime` executable.
    BundledExecutable { executable: PathBuf },
}

#[derive(Debug)]
pub struct SidecarError {
    pub code: &'static str,
    pub message: String,
    pub recoverable: bool,
}

impl Display for SidecarError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for SidecarError {}

pub struct RuntimeSidecar {
    child: Child,
    address: SocketAddr,
    session_token: String,
    lifecycle: RuntimeLifecycle,
}

impl RuntimeSidecar {
    pub fn spawn(launch: &RuntimeLaunch, address: SocketAddr) -> Result<Self, SidecarError> {
        let session_token = Uuid::new_v4().simple().to_string();
        let mut command = match launch {
            RuntimeLaunch::PythonModule { interpreter } => {
                let mut command = Command::new(interpreter);
                command.args(["-m", "canx"]);
                command
            }
            RuntimeLaunch::BundledExecutable { executable } => Command::new(executable),
        };
        let child = command
            .args([
                "--host",
                &address.ip().to_string(),
                "--port",
                &address.port().to_string(),
                "--session-token",
                &session_token,
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| SidecarError {
                code: "runtime.spawn_failed",
                message: error.to_string(),
                recoverable: true,
            })?;
        let mut lifecycle = RuntimeLifecycle::new();
        lifecycle.on_spawned();
        Ok(Self {
            child,
            address,
            session_token,
            lifecycle,
        })
    }

    #[must_use]
    pub fn state(&self) -> &LifecycleState {
        self.lifecycle.state()
    }

    pub fn poll_status(&mut self) -> Result<&LifecycleState, SidecarError> {
        if let Some(status) = self.child.try_wait().map_err(process_error)? {
            self.lifecycle.on_child_exit(status.code());
        } else if health_ready(self.address) {
            self.lifecycle.on_ready();
        }
        Ok(self.lifecycle.state())
    }

    pub fn wait_until_ready(&mut self, timeout: Duration) -> Result<(), SidecarError> {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            match self.poll_status()? {
                LifecycleState::Ready => return Ok(()),
                LifecycleState::Failed { exit_code } => {
                    return Err(SidecarError {
                        code: "runtime.exited_during_startup",
                        message: format!("Runtime exited during startup with code {exit_code:?}."),
                        recoverable: true,
                    });
                }
                _ => thread::sleep(Duration::from_millis(50)),
            }
        }
        Err(SidecarError {
            code: "runtime.startup_timeout",
            message: format!("Runtime was not healthy within {timeout:?}."),
            recoverable: true,
        })
    }

    pub fn shutdown(&mut self, timeout: Duration) -> Result<(), SidecarError> {
        self.lifecycle.begin_shutdown();
        let status = http_status(
            self.address,
            "POST",
            "/runtime/shutdown",
            Some(&self.session_token),
        )
        .map_err(network_error)?;
        if status != 202 {
            return Err(SidecarError {
                code: "runtime.shutdown_rejected",
                message: format!("Runtime rejected shutdown with HTTP {status}."),
                recoverable: true,
            });
        }

        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if let Some(status) = self.child.try_wait().map_err(process_error)? {
                self.lifecycle.on_child_exit(status.code());
                return if self.lifecycle.state() == &LifecycleState::Stopped {
                    Ok(())
                } else {
                    Err(SidecarError {
                        code: "runtime.shutdown_failed",
                        message: format!("Runtime exited with code {:?}.", status.code()),
                        recoverable: true,
                    })
                };
            }
            thread::sleep(Duration::from_millis(50));
        }

        self.child.kill().map_err(process_error)?;
        let status = self.child.wait().map_err(process_error)?;
        self.lifecycle.on_child_exit(status.code());
        Err(SidecarError {
            code: "runtime.shutdown_timeout",
            message: format!("Runtime required forced termination after {timeout:?}."),
            recoverable: true,
        })
    }
}

impl Drop for RuntimeSidecar {
    fn drop(&mut self) {
        if matches!(self.child.try_wait(), Ok(None)) {
            // Best-effort cleanup is limited to the exact child owned by this controller.
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

fn health_ready(address: SocketAddr) -> bool {
    matches!(
        http_response(address, "GET", "/health", None),
        Ok((200, response))
            if response.contains("\"service\":\"canx-runtime\"")
                && response.contains("\"schema_version\":1")
    )
}

fn http_status(
    address: SocketAddr,
    method: &str,
    path: &str,
    session_token: Option<&str>,
) -> std::io::Result<u16> {
    http_response(address, method, path, session_token).map(|(status, _response)| status)
}

fn http_response(
    address: SocketAddr,
    method: &str,
    path: &str,
    session_token: Option<&str>,
) -> std::io::Result<(u16, String)> {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(200))?;
    stream.set_read_timeout(Some(Duration::from_secs(1)))?;
    stream.set_write_timeout(Some(Duration::from_secs(1)))?;
    let token_header = session_token
        .map(|token| format!("X-CANX-Session-Token: {token}\r\n"))
        .unwrap_or_default();
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {address}\r\n{token_header}Content-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes())?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response)?;
    let response_text = String::from_utf8_lossy(&response).into_owned();
    let status = response_text
        .split_whitespace()
        .nth(1)
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, "invalid HTTP status")
        })?;
    Ok((status, response_text))
}

fn process_error(error: std::io::Error) -> SidecarError {
    SidecarError {
        code: "runtime.process_error",
        message: error.to_string(),
        recoverable: true,
    }
}

fn network_error(error: std::io::Error) -> SidecarError {
    SidecarError {
        code: "runtime.network_error",
        message: error.to_string(),
        recoverable: true,
    }
}
