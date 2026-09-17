<#
.SYNOPSIS
    Windows end-to-end smoke for the V0.3-07 native DBC file dialog bridge.

.DESCRIPTION
    Drives the *real* renderer path of the desktop DBC bridge inside a **packaged**
    can-x.exe:

        renderer selectDbcContent()
          -> Tauri invoke("select_dbc_file")
          -> Rust select_dbc_file
          -> native Windows file dialog
          -> bounded exact-byte read
          -> SelectedDbcContent
          -> TypeScript typed projection

    and checks the three things the bridge promises:

      * Step 1 — Cancel: the dialog is dismissed with its own Cancel button and the
        renderer receives `null` (cancel is control flow, not an error).
      * Step 2 — Select: a real `.dbc` fixture is chosen with the dialog's own Open
        button; the returned `sourceName` is a basename only and the SHA-256 of the
        bytes that crossed IPC equals the fixture's own SHA-256 (the exact-byte
        guarantee, measured at the far end of the chain).
      * Step 3 — Payload shape: one raw `invoke` observes the payload the Rust layer
        actually publishes, which must carry `source_name` and `content_base64` and
        nothing else — no path, no directory, no absolute path.

    Nothing here clicks inside the webview. The buttons it presses belong to the
    operating system's own file dialog, located through UI Automation and invoked
    with the control's InvokePattern (falling back to a real mouse click at the
    control's screen rectangle).

.PARAMETER ExePath
    The packaged executable under test. The V0.3-07 verification used
    `apps\desktop\src-tauri\target\release\can-x.exe`.

.PARAMETER FixturePath
    A real `.dbc` file to select. `tests\fixtures\dbc\basic_standard.dbc` works, but a
    fixture in a directory with a distinctive name makes the path-privacy check
    louder: if the directory name ever came back through IPC, the step-3 payload
    keys and the source name would show it.

.PARAMETER EvidencePath
    Where the JSON evidence record is written.

.NOTES
    Requires a **smoke build**: one made with `VITE_CANX_DBC_SMOKE=1` in the
    environment, so that the test-only harness in `apps/desktop/src/smoke/` is
    compiled in. An ordinary build contains no harness and this script will time out
    waiting for a dialog.

        set VITE_CANX_DBC_SMOKE=1
        cmd.exe /c scripts\package-windows.cmd
        powershell -File scripts\smoke-native-dbc-dialog.ps1 -ExePath <exe> -FixturePath <dbc> -EvidencePath <json>

    The harness publishes its state through `document.title`; WebView2 exposes that
    as the UI Automation name of the web-content pane inside the Tauri window, which
    is the only renderer state readable from outside without granting the renderer a
    new capability.
#>
param(
  [Parameter(Mandatory = $true)][string]$ExePath,
  [Parameter(Mandatory = $true)][string]$FixturePath,
  [Parameter(Mandatory = $true)][string]$EvidencePath
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
    return (Invoke-WebRequest -Uri 'http://127.0.0.1:8765/health' -UseBasicParsing -TimeoutSec 5).Content
  } catch {
    return "HEALTH_ERROR: $($_.Exception.Message)"
  }
}

if (-not (Test-Path -LiteralPath $ExePath)) { throw "executable not found: $ExePath" }
if (-not (Test-Path -LiteralPath $FixturePath)) { throw "fixture not found: $FixturePath" }

$fixtureHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $FixturePath).Hash.ToLowerInvariant()
$fixtureSize = (Get-Item -LiteralPath $FixturePath).Length

$evidence = [ordered]@{
  executable    = $ExePath
  executableSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
  fixture       = $FixturePath
  fixtureSize   = $fixtureSize
  fixtureSha256 = $fixtureHash
  startedAt     = (Get-Date).ToString('s')
  appPid        = 0
  cancel        = [ordered]@{ dialogOpened = $false; dialogName = ''; method = ''; dialogClosed = $false }
  select        = [ordered]@{ dialogOpened = $false; method = ''; dialogClosed = $false }
  payloadShape  = [ordered]@{ dialogOpened = $false; method = ''; dialogClosed = $false }
  smokeState    = ''
  healthBefore  = ''
  healthAfter   = ''
  appResponding = $true
  verdict       = [ordered]@{}
  events        = @()
}

$process = Start-Process -FilePath $ExePath -PassThru
$appPid = $process.Id
$evidence.appPid = $appPid
Write-Evidence "packaged app started: pid=$appPid"

# ---- Step 1 — Cancel ---------------------------------------------------------
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
  Start-Sleep -Milliseconds 1200
  Write-Evidence "STEP1 dialog closed=$($evidence.cancel.dialogClosed)"
}

# ---- Step 2 — select the fixture ---------------------------------------------
$dialog = Wait-CanxDialog $appPid 90
if ($null -eq $dialog) {
  Write-Evidence 'STEP2 FAILED: no native dialog appeared within 90s'
} else {
  $evidence.select.dialogOpened = $true
  [CanxSmokeInput]::SetForegroundWindow([IntPtr]$dialog.Current.NativeWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 400
  Write-Evidence "STEP2 file name set via: $(Set-DialogFileName $dialog $FixturePath)"
  Start-Sleep -Milliseconds 400
  $evidence.select.method = Invoke-DialogButton $dialog $OpenButtonId 'Button'
  Write-Evidence "STEP2 open dispatched via: $($evidence.select.method)"
  $evidence.select.dialogClosed = Wait-NoCanxDialog $appPid 40
  Start-Sleep -Milliseconds 1200
  Write-Evidence "STEP2 dialog closed=$($evidence.select.dialogClosed)"
}

# ---- Step 3 — raw payload shape ----------------------------------------------
$dialog = Wait-CanxDialog $appPid 90
if ($null -eq $dialog) {
  Write-Evidence 'STEP3 FAILED: no native dialog appeared within 90s'
} else {
  $evidence.payloadShape.dialogOpened = $true
  [CanxSmokeInput]::SetForegroundWindow([IntPtr]$dialog.Current.NativeWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 400
  Write-Evidence "STEP3 file name set via: $(Set-DialogFileName $dialog $FixturePath)"
  Start-Sleep -Milliseconds 400
  $evidence.payloadShape.method = Invoke-DialogButton $dialog $OpenButtonId 'Button'
  Write-Evidence "STEP3 open dispatched via: $($evidence.payloadShape.method)"
  $evidence.payloadShape.dialogClosed = Wait-NoCanxDialog $appPid 40
  Write-Evidence "STEP3 dialog closed=$($evidence.payloadShape.dialogClosed)"
}

$deadline = (Get-Date).AddSeconds(60)
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
$selectStep = $null
$payloadStep = $null
if ($null -ne $smoke) {
  $cancelStep = $smoke.results | Where-Object { $_.step -eq 'cancel' } | Select-Object -First 1
  $selectStep = $smoke.results | Where-Object { $_.step -eq 'select' } | Select-Object -First 1
  $payloadStep = $smoke.results | Where-Object { $_.step -eq 'payload_shape' } | Select-Object -First 1
}

$evidence.verdict = [ordered]@{
  cancelDialogOpened = [bool]$evidence.cancel.dialogOpened
  cancelReturnedNull = ($null -ne $cancelStep) -and ($cancelStep.returnedNull -eq $true)
  selectDialogOpened = [bool]$evidence.select.dialogOpened
  selectReturnedContent = ($null -ne $selectStep) -and ($selectStep.returnedNull -eq $false)
  basenameOnly       = ($null -ne $selectStep) -and ($selectStep.sourceNameHasSeparator -eq $false)
  exactBytes         = ($null -ne $selectStep) -and ($selectStep.byteCount -eq $fixtureSize)
  sha256Match        = ($null -ne $selectStep) -and ($selectStep.sha256 -eq $fixtureHash)
  payloadKeyCount    = if ($null -ne $payloadStep -and $null -ne $payloadStep.payloadKeys) { $payloadStep.payloadKeys.Count } else { -1 }
  payloadKeys        = if ($null -ne $payloadStep) { $payloadStep.payloadKeys } else { @() }
  selectedSourceName = if ($null -ne $selectStep) { $selectStep.sourceName } else { '' }
  returnedByteCount  = if ($null -ne $selectStep) { $selectStep.byteCount } else { -1 }
  returnedSha256     = if ($null -ne $selectStep) { $selectStep.sha256 } else { '' }
}

$evidence.healthAfter = Get-Health
Write-Evidence "runtime health after dialogs: $($evidence.healthAfter)"
try {
  $process.Refresh()
  $evidence.appResponding = $process.Responding
} catch {
  $evidence.appResponding = $false
}
Write-Evidence "app responding: $($evidence.appResponding)"

$evidence.events = @($script:Events)
$evidence.finishedAt = (Get-Date).ToString('s')
$evidence | ConvertTo-Json -Depth 6 | Set-Content -Path $EvidencePath -Encoding UTF8

Write-Output ''
Write-Output 'Cancel smoke:                ' + $(if ($evidence.verdict.cancelReturnedNull) { 'PASS' } else { 'FAIL' })
Write-Output 'Valid selection smoke:       ' + $(if ($evidence.verdict.selectReturnedContent) { 'PASS' } else { 'FAIL' })
Write-Output 'sourceName basename-only:    ' + $(if ($evidence.verdict.basenameOnly) { 'PASS' } else { 'FAIL' })
Write-Output 'exact byte count:            ' + $(if ($evidence.verdict.exactBytes) { 'PASS' } else { 'FAIL' })
Write-Output 'SHA256 equality:             ' + $(if ($evidence.verdict.sha256Match) { 'PASS' } else { 'FAIL' })
Write-Output 'payload key count == 2:      ' + $(if ($evidence.verdict.payloadKeyCount -eq 2) { 'PASS' } else { 'FAIL' })
Write-Output 'runtime sidecar health:      ' + $(if ($evidence.healthAfter -like '*"status":"ready"*') { 'PASS' } else { 'FAIL' })
Write-Output 'app responsiveness:          ' + $(if ($evidence.appResponding) { 'PASS' } else { 'FAIL' })
Write-Output "EVIDENCE_WRITTEN=$EvidencePath"
