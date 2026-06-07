# TracePilot deploy (PowerShell) - scenario-aware collection
param(
    [Parameter(Mandatory = $false)]
    [ValidateSet('page_switch', 'video')]
    [string]$Scenario = 'page_switch',

    [int]$Duration = 30,
    [string]$Package = '',
    [switch]$NoPush
)

$ErrorActionPreference = 'Stop'

# Resolve project root from script location (avoid hard-coded Unicode paths in source)
$d = Split-Path -Parent $PSScriptRoot
$dev = "/data/local/tmp"
$perfCfg = "/data/misc/perfetto-configs/tracepilot.pbtx"
$scenarioDir = Join-Path $d "output\$Scenario"
$trace = "/sdcard/perfetto_trace_${Scenario}.perfetto-trace"
$deviceScenario = "$dev/$Scenario"
$eventsDev = "$deviceScenario/events.bin"

# Windows path -> WSL /mnt/<drive>/...
$driveLetter = $d.Substring(0, 1).ToLower()
$wslRest = $d.Substring(2).Replace('\', '/')
$wslRoot = "/mnt/$driveLetter$wslRest"

New-Item -ItemType Directory -Force -Path $scenarioDir | Out-Null

if ($Scenario -eq 'page_switch') {
    $hint = 'Switch pages / tabs NOW (do NOT play video)'
} else {
    $hint = 'Play video continuously NOW (avoid page switching)'
}

Write-Host '=== TracePilot Deploy ==='
Write-Host "Scenario: $Scenario"
Write-Host "Duration: ${Duration}s"
Write-Host "Package:  $(if ($Package) { $Package } else { '(all)' })"
Write-Host "Output:   $scenarioDir"
Write-Host ''

function Invoke-AdbSu {
    param([Parameter(Mandatory = $true)][string]$Command)
    adb shell "su -c '$Command'"
}

$rootCheck = adb shell "su -c 'id -u'" 2>&1
if ($rootCheck -notmatch '0') {
    Write-Error "Device root required (su failed). Got: $rootCheck"
    exit 1
}

if (-not $NoPush) {
    Write-Host '[1/6] Push binaries...'
    adb push (Join-Path $d 'output\tracepilot-aarch64') "$dev/tracepilot"
    adb push (Join-Path $d 'output\tracepilot.bpf.o') "$dev/tracepilot.bpf.o"
    adb push (Join-Path $d 'scripts\perfetto_config.pbtx') "$dev/perfetto_config.pbtx"
    Invoke-AdbSu "mkdir -p /data/misc/perfetto-configs $deviceScenario"
    Invoke-AdbSu "cp $dev/perfetto_config.pbtx $perfCfg && chmod 644 $perfCfg"
    Invoke-AdbSu "chmod 755 $dev/tracepilot && chmod 644 $dev/tracepilot.bpf.o"
    Write-Host '  Done.'
} else {
    Write-Host '[1/6] Skipping push (--NoPush)'
    Invoke-AdbSu "mkdir -p /data/misc/perfetto-configs $deviceScenario"
    Invoke-AdbSu "test -f $dev/perfetto_config.pbtx && cp $dev/perfetto_config.pbtx $perfCfg && chmod 644 $perfCfg || true"
}

Write-Host '[2/6] Start Perfetto...'
Invoke-AdbSu "perfetto --txt -c $perfCfg -o $trace --background"
Write-Host '  Started.'

Write-Host '[3/6] Start tracepilot...'
if ($Package) {
    $loaderCmd = "$dev/tracepilot --duration $Duration --events-out $eventsDev --debug --package $Package"
} else {
    $loaderCmd = "$dev/tracepilot --duration $Duration --events-out $eventsDev --debug"
}
$loaderProc = Start-Process -FilePath adb -ArgumentList @('shell', "su -c '$loaderCmd'") -PassThru -NoNewWindow
Write-Host '  Started.'

Write-Host "[4/6] Collecting $Duration seconds"
Write-Host "  $hint"
Start-Sleep -Seconds $Duration
Write-Host '  Collection period ended, waiting for tracepilot to flush events.bin...'
if (-not $loaderProc.HasExited) {
    $null = $loaderProc.WaitForExit(600000)
}
for ($i = 0; $i -lt 120; $i++) {
    $sizeRaw = adb shell "su -c 'stat -c %s $eventsDev 2>/dev/null || echo 0'" 2>$null
    $size = ($sizeRaw | Out-String).Trim()
    if ($size -match '^\d+$' -and [int64]$size -gt 0) {
        Start-Sleep -Seconds 2
        $size2 = (adb shell "su -c 'stat -c %s $eventsDev'" 2>$null | Out-String).Trim()
        if ($size2 -eq $size) { break }
    }
    Start-Sleep -Seconds 2
}
Write-Host '  Done.'

Write-Host '[5/6] Pull results...'
$perfLocal = Join-Path $scenarioDir 'perfetto_trace.perfetto-trace'
$eventsLocal = Join-Path $scenarioDir 'events.bin'
$identityLocal = Join-Path $scenarioDir 'identity_map.json'
New-Item -ItemType Directory -Force -Path $scenarioDir | Out-Null

$prevEA = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
adb pull $trace $perfLocal 2>&1 | Out-Host
if (-not (Test-Path $eventsLocal)) {
    adb pull $eventsDev $eventsLocal 2>&1 | Out-Host
}
if (-not (Test-Path $identityLocal)) {
    adb pull "$deviceScenario/identity_map.json" $identityLocal 2>&1 | Out-Host
    if (-not (Test-Path $identityLocal)) {
        adb pull "$dev/identity_map.json" $identityLocal 2>&1 | Out-Host
    }
}
$ErrorActionPreference = $prevEA

if (-not (Test-Path $eventsLocal)) {
    Write-Warning "events.bin not pulled; try manually: adb pull $eventsDev `"$eventsLocal`""
} else {
    $evSize = (Get-Item -LiteralPath $eventsLocal).Length
    Write-Host "  events.bin: $evSize bytes"
}
Write-Host '  Done.'

Write-Host '[6/6] Extract frames and analyze (WSL)...'

$framesLocal = Join-Path $scenarioDir 'frames.txt'
$resultLocal = Join-Path $scenarioDir 'result.json'
$pkgArg = if ($Package) { "--package $Package" } else { '' }

$wslScript = @"
set -euo pipefail
SCENARIO_DIR='$wslRoot/output/$Scenario'
mkdir -p "`$SCENARIO_DIR"

if command -v trace_processor_shell >/dev/null 2>&1; then
  TP=trace_processor_shell
elif [ -x '$wslRoot/output/linux-amd64/trace_processor_shell' ]; then
  TP='$wslRoot/output/linux-amd64/trace_processor_shell'
else
  echo 'ERROR: trace_processor_shell not found' >&2
  exit 1
fi

"`$TP" -q '$wslRoot/scripts/frame_query.sql' "`$SCENARIO_DIR/perfetto_trace.perfetto-trace" > "`$SCENARIO_DIR/frames.txt"

if [ -f '$wslRoot/scripts/thermal_query.sql' ]; then
  "`$TP" -q '$wslRoot/scripts/thermal_query.sql' "`$SCENARIO_DIR/perfetto_trace.perfetto-trace" > "`$SCENARIO_DIR/thermal_profile.txt" 2>/dev/null || true
  if [ -s "`$SCENARIO_DIR/thermal_profile.txt" ]; then
    echo "Thermal profile: `$SCENARIO_DIR/thermal_profile.txt"
  else
    echo "(no thermal samples - freq-based throttle still available)"
  fi
fi

SF=`$(grep -c '"SF"' "`$SCENARIO_DIR/frames.txt" || true)
VD=`$(grep -c '"VD"' "`$SCENARIO_DIR/frames.txt" || true)
echo "Frame types: SF=`$SF VD=`$VD"

if [ -x '$wslRoot/output/tracepilot' ] && [ -f "`$SCENARIO_DIR/events.bin" ]; then
  ANALYZE=(
    '$wslRoot/output/tracepilot'
    --events-in "`$SCENARIO_DIR/events.bin"
    --frame-data "`$SCENARIO_DIR/frames.txt"
    --output "`$SCENARIO_DIR/result.json"
    --top-k 10 -G -s '$Scenario' $pkgArg --debug
  )
  if [ -s "`$SCENARIO_DIR/thermal_profile.txt" ]; then
    ANALYZE+=(--thermal-data "`$SCENARIO_DIR/thermal_profile.txt")
  fi
  "`${ANALYZE[@]}" || true
  echo "Result: `$SCENARIO_DIR/result.json"
else
  echo 'WARN: tracepilot or events.bin missing; run make loader' >&2
fi
"@

$scriptPath = Join-Path $scenarioDir '_wsl_analyze.sh'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($scriptPath, $wslScript, $utf8NoBom)

$wslScriptPath = "$wslRoot/output/$Scenario/_wsl_analyze.sh"
wsl bash $wslScriptPath

Write-Host "  Frames: $framesLocal"
Write-Host "  Result: $resultLocal"
Write-Host "=== Done ($Scenario) ==="
Write-Host ''
if ($Scenario -eq 'page_switch') {
    Write-Host 'Collection complete. Results in output\page_switch\'
} else {
    Write-Host "Next: .\scripts\deploy.ps1 -Scenario page_switch $(if ($Package) { "-Package $Package" })"
}
