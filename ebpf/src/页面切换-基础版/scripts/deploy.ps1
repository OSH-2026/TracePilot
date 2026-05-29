# TracePilot deploy (PowerShell)
param(
    [int]$Duration = 30,
    [string]$Package = ""
)

$d = 'D:\osh大作业\页面切换-基础版'
$dev = '/data/local/tmp'
$trace = '/sdcard/perfetto_trace.perfetto-trace'

Write-Host '=== TracePilot Deploy ==='

Write-Host '[1/6] Push binaries...'
adb push "$d\output\tracepilot-aarch64" "$dev/tracepilot"
adb push "$d\output\tracepilot.bpf.o" "$dev/"
adb push "$d\scripts\perfetto_config.pbtx" "$dev/"
adb shell "chmod +x $dev/tracepilot"
Write-Host '  Done.'

Write-Host '[2/6] Start Perfetto...'
adb shell "perfetto --txt -c $dev/perfetto_config.pbtx -o $trace --background"
Write-Host '  Started.'

Write-Host '[3/6] Start tracepilot...'
if ($Package) {
    $cmd = "$dev/tracepilot --duration $Duration --events-out $dev/tracepilot_events.bin --debug --package $Package"
} else {
    $cmd = "$dev/tracepilot --duration $Duration --events-out $dev/tracepilot_events.bin --debug"
}
Start-Process -FilePath adb -ArgumentList 'shell', $cmd -NoNewWindow
Write-Host '  Started.'

Write-Host "[4/6] Collecting $Duration seconds -- SWITCH PAGES NOW"
Start-Sleep -Seconds $Duration
Start-Sleep -Seconds 3
Write-Host '  Done.'

Write-Host '[5/6] Pull results...'
adb pull "$trace" "$d\output\perfetto_trace.perfetto-trace"
adb pull "$dev/tracepilot_events.bin" "$d\output\tracepilot_events.bin"
Write-Host '  Done.'

Write-Host '[6/6] Extract frames and analyze...'

$wslBase = '/mnt/d/osh大作业/页面切换-基础版'
$sqlExtract = @"
$wslBase/output/linux-amd64/trace_processor_shell $wslBase/output/perfetto_trace.perfetto-trace -q $wslBase/scripts/frame_query.sql
"@
$sqlExtract | Set-Content -LiteralPath "$d\output\extract.sh" -Encoding UTF8 -Force

wsl bash /mnt/d/osh大作业/页面切换-基础版/output/extract.sh | `
    Out-File -LiteralPath "$d\output\frames.txt" -Encoding ASCII

Write-Host "  Frames: $d\output\frames.txt"

$analyzeScript = @'
cd /home/pzy/pixel6a-bpf/页面切换-基础版
cp /mnt/d/osh大作业/页面切换-基础版/output/tracepilot_events.bin output/
cp /mnt/d/osh大作业/页面切换-基础版/output/frames.txt output/
./output/tracepilot --events-in output/tracepilot_events.bin --frame-data output/frames.txt --output output/result.json --top-k 10 --debug
cp output/result.json /mnt/d/osh大作业/页面切换-基础版/output/
'@
$analyzeScript | Set-Content -LiteralPath "$d\output\analyze.sh" -Encoding UTF8 -Force
wsl bash /mnt/d/osh大作业/页面切换-基础版/output/analyze.sh

Write-Host "  Result: $d\output\result.json"
Write-Host '=== Done ==='
