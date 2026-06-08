<#
.SYNOPSIS
    Record MIDI traffic on the Katana endpoint via Windows MIDI Services.

.DESCRIPTION
    Opens `midi endpoint <KATANA> monitor` capturing UMP words to a timestamped
    file under .\captures. Run this in an INTERACTIVE terminal, then drive Boss
    Tone Studio (send a patch, tweak knobs). Press ESC in this window to stop --
    that clean exit is what flushes the capture file to disk.

    Afterwards decode with:
        python decode_capture.py .\captures\<file>.midi2 --dt1-only

.NOTES
    - The `midi` monitor crashes if stdin is redirected, so always run it in a
      real console window (not piped, not in a background job).
    - The KATANA endpoint is multi-client: this monitor can run at the same time
      as Tone Studio and will see Tone Studio's outbound DT1 writes.
#>
[CmdletBinding()]
param(
    [string]$Label = "capture",
    # Main patch port by default. Use "DAW CTRL" or "CTRL" to target the others.
    [string]$PortName = "KATANA"
)

$ErrorActionPreference = "Stop"

$midi = "C:\Program Files\Windows MIDI Services\Tools\Console\midi.exe"
if (-not (Test-Path $midi)) {
    throw "midi.exe not found. Install with: winget install Microsoft.WindowsMIDIServicesSDK"
}

# Resolve the UMP endpoint id for the requested port from the enumeration.
$idLine = & $midi enumerate midi-services-endpoints --show-endpoint-id 2>$null |
    Select-String -Pattern 'midiu_ksa_\S+' | ForEach-Object { $_.Matches.Value } |
    Select-Object -First 1
if (-not $idLine) {
    throw "No KATANA (KSA) endpoint found. Is the amp connected and powered on?"
}
$endpointId = "\\?\swd#midisrv#$idLine"

$captureDir = Join-Path $PSScriptRoot "captures"
New-Item -ItemType Directory -Force -Path $captureDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$out = Join-Path $captureDir "$Label`_$stamp.midi2"

Write-Host "Endpoint : $endpointId"
Write-Host "Capturing: $out"
Write-Host "Now drive Tone Studio. Press ESC in this window to stop & flush.`n" -ForegroundColor Cyan

& $midi endpoint $endpointId monitor `
    --capture-to-file $out `
    --annotate-capture `
    --include-timestamp `
    --verbose

Write-Host "`nSaved: $out" -ForegroundColor Green
Write-Host "Decode with: python decode_capture.py `"$out`" --dt1-only"
