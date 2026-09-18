@echo off
rem Run the AGENT-01 multi-agent orchestration tooling from the repository root.
rem
rem Usage:  scripts\agent.cmd validate-task --file .agent\examples\task.example.json
rem         scripts\agent.cmd plan --file .agent\examples\tasks.dependency.example.json
rem         scripts\agent.cmd worktree list
rem
rem Exit codes are the tooling contract: 0 success, 2 validation, 3 ownership or
rem conflict, 4 git state, 5 internal. The exit code is passed through unchanged.
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m tools.agent.cli %*
set "result=%errorlevel%"
popd
exit /b %result%
