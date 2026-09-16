use std::net::TcpListener;
use std::path::PathBuf;
use std::time::Duration;

use can_x_lib::runtime_sidecar::{LifecycleState, RuntimeLaunch, RuntimeLifecycle, RuntimeSidecar};

#[test]
fn lifecycle_marks_an_unexpected_child_exit_as_failed() {
    let mut lifecycle = RuntimeLifecycle::new();

    lifecycle.on_spawned();
    lifecycle.on_child_exit(Some(7));

    assert_eq!(
        lifecycle.state(),
        &LifecycleState::Failed { exit_code: Some(7) }
    );
}

#[test]
fn lifecycle_marks_a_normal_shutdown_as_stopped() {
    let mut lifecycle = RuntimeLifecycle::new();

    lifecycle.on_spawned();
    lifecycle.on_ready();
    lifecycle.begin_shutdown();
    lifecycle.on_child_exit(Some(0));

    assert_eq!(lifecycle.state(), &LifecycleState::Stopped);
}

#[test]
fn sidecar_starts_health_checks_and_gracefully_stops_the_python_runtime() {
    let python = match std::env::var_os("CANX_TEST_PYTHON") {
        Some(value) => PathBuf::from(value),
        None => return,
    };
    let listener = TcpListener::bind("127.0.0.1:0").expect("reserve a loopback test port");
    let address = listener.local_addr().expect("read loopback test address");
    drop(listener);

    let mut sidecar = RuntimeSidecar::spawn(
        &RuntimeLaunch::PythonModule {
            interpreter: python,
        },
        address,
    )
    .expect("spawn Python runtime");

    sidecar
        .wait_until_ready(Duration::from_secs(10))
        .expect("runtime becomes healthy");
    assert_eq!(sidecar.state(), &LifecycleState::Ready);

    sidecar
        .shutdown(Duration::from_secs(5))
        .expect("runtime exits after authenticated shutdown");
    assert_eq!(sidecar.state(), &LifecycleState::Stopped);
}
