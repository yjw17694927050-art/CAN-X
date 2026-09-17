<#
.SYNOPSIS
    Windows end-to-end smoke for the V0.3-08 DBC import orchestration.

.DESCRIPTION
    Drives the *real* renderer path of a desktop DBC import inside a **packaged**
    can-x.exe, from the native dialog all the way to a project-owned asset:

        renderer selectDbcContent()                  renderer
          -> Tauri invoke("select_dbc_file")           importDbcFromNativeDialog(projectPath)
          -> Rust select_dbc_file                        -> POST /dbc/assets (Runtime)
          -> native Windows file dialog                  -> ProjectDbcService
          -> bounded exact-byte read                     -> project-owned DBC asset
          -> SelectedDbcContent

    and checks what each half promises:

      * Step 1 - Cancel: the dialog is dismissed with its own Cancel button; the
        renderer receives `null`; the orchestration reports `cancelled`; the project
        gains no asset and the Runtime records no mutation.
      * Step 2 - Payload shape: one raw `invoke` observes the payload the Rust layer
        actually publishes, which must still carry exactly `source_name` and
        `content_base64`. V0.3-08 did not widen the V0.3-07 IPC surface.
      * Step 3 - Import: a real `.dbc` fixture is chosen and the production
        orchestration runs against the smoke project. The Runtime's own asset
        metadata comes back, and - after the desktop is closed - the project is
        reopened **from the source side** (the Python domain, not the Runtime HTTP
        API) to prove the asset persisted: count, basename, bytes, SHA-256, size,
        registry metadata and loadability.

    Nothing here clicks inside the webview. The buttons it presses belong to the
    operating system's own file dialog, located through UI Automation and invoked
    with the control's InvokePattern (falling back to a real mouse click at the
    control's screen rectangle).

.PARAMETER ExePath
    The packaged executable under test, for example
    `apps\desktop\src-tauri\target\release\can-x.exe`.

.PARAMETER FixturePath
    A real `.dbc` file to select. `tests\fixtures\dbc\basic_standard.dbc` works, but a
    fixture in a directory with a distinctive name makes the path-privacy check
    louder: if the directory name ever came back through IPC or into the request, the
    payload keys, the source name and the stored bytes would show it.

.PARAMETER EvidencePath
    Where the JSON evidence record is written.

.PARAMETER ProjectPath
    The CAN-X project to import into. When omitted, a temporary project is created
    under the system temp directory and its path is recorded in the evidence.

.PARAMETER PythonPath
    Interpreter used for the source-side project checks (create and reopen). Defaults
    to the repository virtualenv.

.NOTES
    Requires a **smoke build**: one made with `VITE_CANX_DBC_SMOKE=1` and
    `VITE_CANX_DBC_SMOKE_PROJECT_PATH=<project>` in the environment, so that the
    test-only harness in `apps/desktop/src/smoke/` is compiled in and knows which
    project to import into. An ordinary build contains no harness and this script
    will time out waiting for a dialog.

        set VITE_CANX_DBC_SMOKE=1
        set VITE_CANX_DBC_SMOKE_PROJECT_PATH=<temp project>
        cmd.exe /c scripts\package-windows.cmd
        powershell -File scripts\smoke-native-dbc-dialog.ps1 -ExePath <exe> -FixturePath <dbc> -EvidencePath <json> -ProjectPath <temp project>

    The harness publishes its state through `document.title`; WebView2 exposes that
    as the UI Automation name of the web-content pane inside the Tauri window, which
    is the only renderer state readable from outside without granting the renderer a
    new capability.
#>
param(
  [Parameter(Mandatory = $true)][string]$ExePath,
  [Parameter(Mandatory = $true)][string]$FixturePath,
  [Parameter(Mandatory = $true)][string]$EvidencePath,
  [Parameter(Mandatory = $false)][string]$ProjectPath = '',
  [Parameter(Mandatory = $false)][string]$PythonPath = '.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class CanxSmokeInput {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@

# Windows dialog control ids, stable across system languages (IDOK / IDCANCEL).
$OpenButtonId = '1'
$CancelButtonId = '2'
$FileNameEditId = '1148'

$RuntimeBase = 'http://127.0.0.1:8765'

$script:Events = New-Object System.Collections.ArrayList

function Write-Evidence([string]$message) {
  $stamp = (Get-Date).ToString('HH:mm:ss.fff')
  $line = "[$stamp] $message"
  Write-Output $line
  [void]$script:Events.Add($line)
}

function Get-RootWindows([int]$ProcessId) {
  $root = [System.Windows.Automation.AutomationElement]::RootElement
  $cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $ProcessId)
  return $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
}

function Find-CanxDialog([int]$ProcessId) {
  foreach ($window in (Get-RootWindows $ProcessId)) {
    if ($window.Current.ClassName -eq '#32770') { return $window }
  }
  return $null
}

function Wait-CanxDialog([int]$ProcessId, [int]$TimeoutSec) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $dialog = Find-CanxDialog $ProcessId
    if ($null -ne $dialog) { return $dialog }
    Start-Sleep -Milliseconds 200
  }
  return $null
}

function Wait-NoCanxDialog([int]$ProcessId, [int]$TimeoutSec) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if ($null -eq (Find-CanxDialog $ProcessId)) { return $true }
    Start-Sleep -Milliseconds 200
  }
  return $false
}

function Find-InDialog($Dialog, [string]$AutomationId, [string]$ClassName) {
  foreach ($element in $Dialog.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition)) {
    if ($element.Current.AutomationId -eq $AutomationId -and $element.Current.ClassName -eq $ClassName) {
      return $element
    }
  }
  return $null
}

function Get-SmokeTitle([int]$ProcessId) {
  foreach ($window in (Get-RootWindows $ProcessId)) {
    if ($window.Current.ClassName -ne 'Tauri Window') { continue }
    $descendants = $window.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($element in $descendants) {
      $name = $element.Current.Name
      if ($name -like 'CANXSMOKE*') { return $name }
    }
  }
  return ''
}

function Click-Element($Element) {
  $rect = $Element.Current.BoundingRectangle
  $x = [int]($rect.X + $rect.Width / 2)
  $y = [int]($rect.Y + $rect.Height / 2)
  [CanxSmokeInput]::SetCursorPos($x, $y) | Out-Null
  Start-Sleep -Milliseconds 200
  [CanxSmokeInput]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 100
  [CanxSmokeInput]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
  return "mouse click at $x,$y"
}

# Pressing the control is the point: InvokePattern is the control's own activation
# path. The mouse fallback exists because a bridge-level provider may not offer it.
function Invoke-DialogButton($Dialog, [string]$AutomationId, [string]$ClassName) {
  $element = Find-InDialog $Dialog $AutomationId $ClassName
  if ($null -eq $element) { return 'element not found' }
  try {
    $invoke = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invoke.Invoke()
    return 'InvokePattern'
  } catch {
    return (Click-Element $element)
  }
}

function Set-DialogFileName($Dialog, [string]$Path) {
  $edit = Find-InDialog $Dialog $FileNameEditId 'Edit'
  if ($null -eq $edit) { return 'file name edit not found' }
  try {
    $value = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    $value.SetValue($Path)
    return 'ValuePattern'
  } catch {
    [CanxSmokeInput]::SetForegroundWindow([IntPtr]$Dialog.Current.NativeWindowHandle) | Out-Null
    Start-Sleep -Milliseconds 300
    $null = Click-Element $edit
    Start-Sleep -Milliseconds 300
    [System.Windows.Forms.SendKeys]::SendWait('^a')
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait('{DEL}')
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait($Path)
    return 'focus + keyboard'
  }
}

function Get-Health {
  try {
    return (Invoke-WebRequest -Uri "$RuntimeBase/health" -UseBasicParsing -TimeoutSec 5).Content
  } catch {
    return "HEALTH_ERROR: $($_.Exception.Message)"
  }
}

function Wait-Health([int]$TimeoutSec) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $health = Get-Health
    if ($health -like '*"status":"ready"*') { return $health }
    Start-Sleep -Milliseconds 500
  }
  return (Get-Health)
}

# The Runtime is the authority for "what does this project own"; asking it over HTTP
# is an observation the renderer cannot fake, and it is the same endpoint a future DBC
# Workspace will read.
function Get-ProjectAssets([string]$ProjectRoot) {
  $encoded = [uri]::EscapeDataString($ProjectRoot)
  try {
    $response = Invoke-WebRequest -Uri "$RuntimeBase/dbc/assets?project_path=$encoded" -UseBasicParsing -TimeoutSec 10
    return ($response.Content | ConvertFrom-Json)
  } catch {
    return $null
  }
}

function Invoke-Python([string]$Source, [string[]]$Arguments) {
  $script = Join-Path $env:TEMP "canx-dbc-smoke-$([guid]::NewGuid().ToString('N')).py"
  Set-Content -Path $script -Value $Source -Encoding UTF8
  $previous = $env:PYTHONPATH
  $env:PYTHONPATH = Join-Path (Get-Location) 'runtime'
  try {
    return (& $PythonPath $script @Arguments) | Out-String
  } finally {
    $env:PYTHONPATH = $previous
    Remove-Item -LiteralPath $script -ErrorAction SilentlyContinue
  }
}

if (-not (Test-Path -LiteralPath $ExePath)) { throw "executable not found: $ExePath" }
if (-not (Test-Path -LiteralPath $FixturePath)) { throw "fixture not found: $FixturePath" }
if (-not (Test-Path -LiteralPath $PythonPath)) { throw "python not found: $PythonPath" }

$fixtureHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $FixturePath).Hash.ToLowerInvariant()
$fixtureSize = (Get-Item -LiteralPath $FixturePath).Length
$fixtureName = Split-Path -Leaf $FixturePath

# ---- Step 0 — a project that owns nothing yet ----------------------------------
$createdProject = $false
if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
  $ProjectPath = Join-Path $env:TEMP "canx-dbc-smoke-project-$([guid]::NewGuid().ToString('N'))"
  $createdProject = $true
}

$createSource = @'
import sys
from pathlib import Path

from canx.project.service import ProjectService

root = Path(sys.argv[1])
handle = ProjectService().create(root, display_name="V0.3-08 DBC import smoke")
handle.close()
print(root)
'@

if (-not (Test-Path -LiteralPath $ProjectPath)) {
  $created = Invoke-Python $createSource @($ProjectPath)
  Write-Evidence "smoke project created: $ProjectPath"
} else {
  $created = $ProjectPath
  Write-Evidence "smoke project reused: $ProjectPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath 'project.json'))) {
  throw "the smoke project was not created at $ProjectPath ($created)"
}

$initialDbFiles = @(Get-ChildItem -LiteralPath (Join-Path $ProjectPath 'dbc') -File -ErrorAction SilentlyContinue)
Write-Evidence "project dbc/ initial file count: $($initialDbFiles.Count)"

$evidence = [ordered]@{
  executable        = $ExePath
  executableSha256  = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
  fixture           = $FixturePath
  fixtureName       = $fixtureName
  fixtureSize       = $fixtureSize
  fixtureSha256     = $fixtureHash
  projectPath       = $ProjectPath
  projectWasCreated = $createdProject
  startedAt         = (Get-Date).ToString('s')
  appPid            = 0
  initialAssetCount = -1
  cancel            = [ordered]@{ dialogOpened = $false; dialogName = ''; method = ''; dialogClosed = $false }
  payloadShape      = [ordered]@{ dialogOpened = $false; method = ''; dialogClosed = $false }
  import            = [ordered]@{ dialogOpened = $false; method = ''; dialogClosed = $false }
  smokeState        = ''
  assetsAfterCancel = -1
  assetsAfterImport = -1
  runtimeAsset      = $null
  sourceCheck       = $null
  healthBefore      = ''
  healthAfter       = ''
  appResponding     = $true
  verdict           = [ordered]@{}
  events            = @()
}

$process = Start-Process -FilePath $ExePath -PassThru
$appPid = $process.Id
$evidence.appPid = $appPid
Write-Evidence "packaged app started: pid=$appPid"

$evidence.healthBefore = Wait-Health 60
Write-Evidence "runtime health before dialogs: $($evidence.healthBefore)"
$initialAssets = Get-ProjectAssets $ProjectPath
if ($null -ne $initialAssets) { $evidence.initialAssetCount = @($initialAssets.assets).Count }
Write-Evidence "runtime asset count before any dialog: $($evidence.initialAssetCount)"

# ---- Step 1 — Cancel ------------------------------------------------------------
$dialog = Wait-CanxDialog $appPid 120
if ($null -eq $dialog) {
  Write-Evidence 'STEP1 FAILED: no native dialog appeared within 120s'
} else {
  $evidence.cancel.dialogOpened = $true
  $evidence.cancel.dialogName = $dialog.Current.Name
  Write-Evidence "STEP1 native dialog opened: class=$($dialog.Current.ClassName) name=$($dialog.Current.Name)"
  [CanxSmokeInput]::SetForegroundWindow([IntPtr]$dialog.Current.NativeWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 400
  $evidence.cancel.method = Invoke-DialogButton $dialog $CancelButtonId 'Button'
  Write-Evidence "STEP1 cancel dispatched via: $($evidence.cancel.method)"
  $evidence.cancel.dialogClosed = Wait-NoCanxDialog $appPid 40
  Start-Sleep -Milliseconds 1500
  Write-Evidence "STEP1 dialog closed=$($evidence.cancel.dialogClosed)"
}

$afterCancel = Get-ProjectAssets $ProjectPath
if ($null -ne $afterCancel) { $evidence.assetsAfterCancel = @($afterCancel.assets).Count }
Write-Evidence "runtime asset count after cancel: $($evidence.assetsAfterCancel)"

# ---- Step 2 — raw payload shape -------------------------------------------------
$dialog = Wait-CanxDialog $appPid 90
if ($null -eq $dialog) {
  Write-Evidence 'STEP2 FAILED: no native dialog appeared within 90s'
} else {
  $evidence.payloadShape.dialogOpened = $true
  [CanxSmokeInput]::SetForegroundWindow([IntPtr]$dialog.Current.NativeWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 400
  Write-Evidence "STEP2 file name set via: $(Set-DialogFileName $dialog $FixturePath)"
  Start-Sleep -Milliseconds 400
  $evidence.payloadShape.method = Invoke-DialogButton $dialog $OpenButtonId 'Button'
  Write-Evidence "STEP2 open dispatched via: $($evidence.payloadShape.method)"
  $evidence.payloadShape.dialogClosed = Wait-NoCanxDialog $appPid 40
  Start-Sleep -Milliseconds 1500
  Write-Evidence "STEP2 dialog closed=$($evidence.payloadShape.dialogClosed)"
}

# ---- Step 3 — the real import ---------------------------------------------------
$dialog = Wait-CanxDialog $appPid 90
if ($null -eq $dialog) {
  Write-Evidence 'STEP3 FAILED: no native dialog appeared within 90s'
} else {
  $evidence.import.dialogOpened = $true
  [CanxSmokeInput]::SetForegroundWindow([IntPtr]$dialog.Current.NativeWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 400
  Write-Evidence "STEP3 file name set via: $(Set-DialogFileName $dialog $FixturePath)"
  Start-Sleep -Milliseconds 400
  $evidence.import.method = Invoke-DialogButton $dialog $OpenButtonId 'Button'
  Write-Evidence "STEP3 open dispatched via: $($evidence.import.method)"
  $evidence.import.dialogClosed = Wait-NoCanxDialog $appPid 40
  Write-Evidence "STEP3 dialog closed=$($evidence.import.dialogClosed)"
}

$deadline = (Get-Date).AddSeconds(90)
$title = ''
while ((Get-Date) -lt $deadline) {
  $title = Get-SmokeTitle $appPid
  if ($title -like 'CANXSMOKE*done*') { break }
  Start-Sleep -Milliseconds 500
}
Write-Evidence "final smoke title: $title"

$smoke = $null
if ($title -like 'CANXSMOKE *') {
  try { $smoke = ($title -replace '^CANXSMOKE\s+', '') | ConvertFrom-Json } catch { $smoke = $null }
}
if ($null -ne $smoke) { $evidence.smokeState = $smoke.state }

$cancelStep = $null
$payloadStep = $null
$importStep = $null
if ($null -ne $smoke) {
  $cancelStep = $smoke.results | Where-Object { $_.step -eq 'cancel' } | Select-Object -First 1
  $payloadStep = $smoke.results | Where-Object { $_.step -eq 'payload_shape' } | Select-Object -First 1
  $importStep = $smoke.results | Where-Object { $_.step -eq 'import' } | Select-Object -First 1
}

$afterImport = Get-ProjectAssets $ProjectPath
$runtimeAssets = @()
if ($null -ne $afterImport) {
  $runtimeAssets = @($afterImport.assets)
  $evidence.assetsAfterImport = $runtimeAssets.Count
}
if ($runtimeAssets.Count -ge 1) {
  $evidence.runtimeAsset = [ordered]@{
    assetId      = $runtimeAssets[0].asset_id
    sourceName   = $runtimeAssets[0].source_name
    sha256       = $runtimeAssets[0].sha256
    sizeBytes    = $runtimeAssets[0].size_bytes
    encoding     = $runtimeAssets[0].encoding
    importedAt   = $runtimeAssets[0].imported_at
  }
}
Write-Evidence "runtime asset count after import: $($evidence.assetsAfterImport)"

$evidence.healthAfter = Get-Health
Write-Evidence "runtime health after dialogs: $($evidence.healthAfter)"
try {
  $process.Refresh()
  $evidence.appResponding = $process.Responding
} catch {
  $evidence.appResponding = $false
}
Write-Evidence "app responding: $($evidence.appResponding)"

# ---- Close the desktop, then reopen the project from the source side ------------
$closed = $process.CloseMainWindow()
if (-not $process.WaitForExit(30000)) {
  Write-Evidence 'desktop did not exit on window close; terminating'
  Stop-Process -Id $appPid -Force -ErrorAction SilentlyContinue
  $null = $process.WaitForExit(10000)
}
Write-Evidence "desktop closed (CloseMainWindow=$closed)"

$sidecarDeadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $sidecarDeadline) {
  if ((Get-Health) -notlike '*"status":"ready"*') { break }
  Start-Sleep -Milliseconds 500
}
$healthAfterClose = Get-Health
Write-Evidence "runtime health after desktop close: $healthAfterClose"

$verifySource = @'
import hashlib
import json
import sys
from pathlib import Path

from canx.dbc.asset import resolve_asset_path
from canx.dbc.project_service import ProjectDbcService

root = Path(sys.argv[1])
fixture = Path(sys.argv[2])

service = ProjectDbcService(root)
assets = service.list_assets()
report = {
    "assetCount": len(assets),
    "dbFiles": sorted(path.name for path in (root / "dbc").glob("*.dbc")),
}
if len(assets) == 1:
    asset = assets[0]
    stored = resolve_asset_path(root, asset).read_bytes()
    source = fixture.read_bytes()
    document = service.load_asset(asset.asset_id)
    report.update(
        {
            "assetId": asset.asset_id,
            "projectIdPresent": bool(asset.project_id),
            "sourceName": asset.source_name,
            "relativePath": asset.relative_path,
            "sizeBytes": asset.size_bytes,
            "encoding": asset.encoding,
            "importedAtIso": asset.imported_at.isoformat(),
            "storedSize": len(stored),
            "storedSha256": hashlib.sha256(stored).hexdigest(),
            "bytesEqualToSource": stored == source,
            "messageCount": len(document.database.messages),
            "signalCount": sum(len(message.signals) for message in document.database.messages),
        }
    )
print(json.dumps(report))
'@

$sourceReport = Invoke-Python $verifySource @($ProjectPath, $FixturePath)
Write-Evidence "source-side reopen report: $($sourceReport.Trim())"
try {
  $evidence.sourceCheck = ($sourceReport | ConvertFrom-Json)
} catch {
  $evidence.sourceCheck = $null
}

$check = $evidence.sourceCheck

# The payload assertion is a set comparison, not a count: three fields where two are
# expected must fail even if one of them happens to look harmless.
$expectedPayloadKeys = @('content_base64', 'source_name')
$observedPayloadKeys = if ($null -ne $payloadStep -and $null -ne $payloadStep.payloadKeys) { @($payloadStep.payloadKeys) } else { @() }
$payloadKeysAreExpected = ($observedPayloadKeys -join ',') -eq ($expectedPayloadKeys -join ',')

$evidence.verdict = [ordered]@{
  initialAssetCountZero = ($evidence.initialAssetCount -eq 0)
  initialDbcDirectoryEmpty = ($initialDbFiles.Count -eq 0)
  cancelDialogOpened = [bool]$evidence.cancel.dialogOpened
  cancelOrchestratedCancelled = ($null -ne $cancelStep) -and ($cancelStep.outcome -eq 'cancelled')
  cancelStoredNothing = ($evidence.assetsAfterCancel -eq 0)
  payloadKeyCount = if ($null -ne $payloadStep -and $null -ne $payloadStep.payloadKeys) { @($payloadStep.payloadKeys).Count } else { -1 }
  payloadKeys = $observedPayloadKeys
  payloadKeyCountIsTwo = (@($observedPayloadKeys).Count -eq 2)
  payloadKeysAreExpected = $payloadKeysAreExpected
  importDialogOpened = [bool]$evidence.import.dialogOpened
  orchestrationImported = ($null -ne $importStep) -and ($importStep.outcome -eq 'imported')
  runtimeAssetCountOne = ($evidence.assetsAfterImport -eq 1)
  runtimeShaMatchesFixture = ($null -ne $evidence.runtimeAsset) -and ($evidence.runtimeAsset.sha256 -eq $fixtureHash)
  runtimeSizeMatchesFixture = ($null -ne $evidence.runtimeAsset) -and ($evidence.runtimeAsset.sizeBytes -eq $fixtureSize)
  runtimeNameIsFixtureBasename = ($null -ne $evidence.runtimeAsset) -and ($evidence.runtimeAsset.sourceName -eq $fixtureName)
  sourceAssetCountOne = ($null -ne $check) -and ($check.assetCount -eq 1)
  sourceBytesEqualFixture = ($null -ne $check) -and ($check.bytesEqualToSource -eq $true)
  sourceStoredSha256MatchesFixture = ($null -ne $check) -and ($check.storedSha256 -eq $fixtureHash)
  sourceSizeMatchesFixture = ($null -ne $check) -and ($check.sizeBytes -eq $fixtureSize)
  sourceNameIsFixtureBasename = ($null -ne $check) -and ($check.sourceName -eq $fixtureName)
  sourceAssetLoads = ($null -ne $check) -and ($check.messageCount -gt 0)
  rendererPayloadClean = ($null -ne $importStep) -and ($importStep.sourceNameHasSeparator -eq $false)
  runtimeHealthyThroughout = ($evidence.healthBefore -like '*"status":"ready"*') -and ($evidence.healthAfter -like '*"status":"ready"*')
  runtimeStoppedWithDesktop = ($healthAfterClose -notlike '*"status":"ready"*')
  appResponding = [bool]$evidence.appResponding
}

$evidence.events = @($script:Events)
$evidence.finishedAt = (Get-Date).ToString('s')
$evidenceJson = $evidence | ConvertTo-Json -Depth 8
$evidenceDir = Split-Path -Parent $EvidencePath
if (-not [string]::IsNullOrWhiteSpace($evidenceDir) -and -not (Test-Path -LiteralPath $evidenceDir)) {
  New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
}
Set-Content -Path $EvidencePath -Value $evidenceJson -Encoding UTF8

Write-Output ''
Write-Output 'Cancel dialog opened:              ' + $(if ($evidence.verdict.cancelDialogOpened) { 'PASS' } else { 'FAIL' })
Write-Output 'Cancel orchestrated cancelled:     ' + $(if ($evidence.verdict.cancelOrchestratedCancelled) { 'PASS' } else { 'FAIL' })
Write-Output 'Cancel stored nothing:             ' + $(if ($evidence.verdict.cancelStoredNothing) { 'PASS' } else { 'FAIL' })
Write-Output 'IPC payload key count == 2:        ' + $(if ($evidence.verdict.payloadKeyCountIsTwo) { 'PASS' } else { 'FAIL' })
Write-Output 'IPC payload keys are expected:     ' + $(if ($evidence.verdict.payloadKeysAreExpected) { 'PASS' } else { 'FAIL' })
Write-Output 'Orchestration imported:            ' + $(if ($evidence.verdict.orchestrationImported) { 'PASS' } else { 'FAIL' })
Write-Output 'Runtime asset count == 1:          ' + $(if ($evidence.verdict.runtimeAssetCountOne) { 'PASS' } else { 'FAIL' })
Write-Output 'Runtime SHA256 == fixture:         ' + $(if ($evidence.verdict.runtimeShaMatchesFixture) { 'PASS' } else { 'FAIL' })
Write-Output 'Runtime size == fixture:           ' + $(if ($evidence.verdict.runtimeSizeMatchesFixture) { 'PASS' } else { 'FAIL' })
Write-Output 'Source reopen asset count == 1:    ' + $(if ($evidence.verdict.sourceAssetCountOne) { 'PASS' } else { 'FAIL' })
Write-Output 'Stored bytes == source bytes:      ' + $(if ($evidence.verdict.sourceBytesEqualFixture) { 'PASS' } else { 'FAIL' })
Write-Output 'Stored SHA256 == source SHA256:    ' + $(if ($evidence.verdict.sourceStoredSha256MatchesFixture) { 'PASS' } else { 'FAIL' })
Write-Output 'Stored size == source size:        ' + $(if ($evidence.verdict.sourceSizeMatchesFixture) { 'PASS' } else { 'FAIL' })
Write-Output 'Source name == fixture basename:   ' + $(if ($evidence.verdict.sourceNameIsFixtureBasename) { 'PASS' } else { 'FAIL' })
Write-Output 'Asset loads from the project:      ' + $(if ($evidence.verdict.sourceAssetLoads) { 'PASS' } else { 'FAIL' })
Write-Output 'Renderer saw a basename only:      ' + $(if ($evidence.verdict.rendererPayloadClean) { 'PASS' } else { 'FAIL' })
Write-Output 'Runtime healthy throughout:        ' + $(if ($evidence.verdict.runtimeHealthyThroughout) { 'PASS' } else { 'FAIL' })
Write-Output 'Runtime stopped with desktop:      ' + $(if ($evidence.verdict.runtimeStoppedWithDesktop) { 'PASS' } else { 'FAIL' })
Write-Output 'App responsiveness:                ' + $(if ($evidence.verdict.appResponding) { 'PASS' } else { 'FAIL' })
Write-Output "EVIDENCE_WRITTEN=$EvidencePath"
Write-Output "SMOKE_PROJECT=$ProjectPath"

# ---- Verdict enforcement --------------------------------------------------------
# The exit code is the gate; the console is the explanation. Above this line the
# script has only *reported* — and a run that printed FAIL while exiting 0 is
# indistinguishable, to any calling script or CI step, from a run that passed. Every
# required verdict must hold before this process may claim success.
#
# The evidence file is already on disk at this point, so a failing run still leaves a
# complete record of what was observed; the failure is added to it, not substituted
# for it.
$requiredVerdicts = @(
  'initialAssetCountZero',
  'initialDbcDirectoryEmpty',
  'cancelDialogOpened',
  'cancelOrchestratedCancelled',
  'cancelStoredNothing',
  'payloadKeyCountIsTwo',
  'payloadKeysAreExpected',
  'importDialogOpened',
  'orchestrationImported',
  'runtimeAssetCountOne',
  'runtimeShaMatchesFixture',
  'runtimeSizeMatchesFixture',
  'runtimeNameIsFixtureBasename',
  'sourceAssetCountOne',
  'sourceBytesEqualFixture',
  'sourceStoredSha256MatchesFixture',
  'sourceSizeMatchesFixture',
  'sourceNameIsFixtureBasename',
  'sourceAssetLoads',
  'rendererPayloadClean',
  'runtimeHealthyThroughout',
  'runtimeStoppedWithDesktop',
  'appResponding'
)

$failedVerdicts = @($requiredVerdicts | Where-Object { $evidence.verdict[$_] -ne $true })
if ($failedVerdicts.Count -gt 0) {
  Write-Output ''
  Write-Output "FAILED VERDICTS ($($failedVerdicts.Count) of $($requiredVerdicts.Count)):"
  foreach ($name in $failedVerdicts) {
    Write-Output "  - $name = $($evidence.verdict[$name])"
  }
  throw "V0.3-08 packaged DBC smoke failed: $($failedVerdicts -join ', ')"
}
Write-Output ''
Write-Output "ALL REQUIRED VERDICTS PASSED ($($requiredVerdicts.Count))"
