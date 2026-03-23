param(
  [Parameter(Mandatory = $true)]
  [string]$Midi,
  [string]$Out = "",
  [string]$Samples = "",
  [switch]$Play,
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $scriptDir "play_midi_with_piano_samples.py"

if (-not (Test-Path -LiteralPath $py)) {
  throw "Program not found: $py"
}
if (-not (Test-Path -LiteralPath $Midi)) {
  throw "MIDI not found: $Midi"
}

$args = @("--midi", $Midi)
if ($Samples.Trim().Length -gt 0) {
  if (-not (Test-Path -LiteralPath $Samples)) {
    throw "Samples dir not found: $Samples"
  }
  $args += @("--samples", $Samples)
}
if ($Out.Trim().Length -gt 0) { $args += @("--out", $Out) }
if ($Play) { $args += "--play" }

$pythonExe = $Python.Trim()
if ($pythonExe.Length -eq 0) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    $pythonExe = $cmd.Source
  }
}
if ($pythonExe.Length -eq 0) {
  $fallback = "E:\python\anaconda\python.exe"
  if (Test-Path -LiteralPath $fallback) {
    $pythonExe = $fallback
  }
}
if ($pythonExe.Length -eq 0 -or -not (Test-Path -LiteralPath $pythonExe)) {
  throw "Python not found. Install Python or pass -Python with full path to python.exe."
}

& $pythonExe $py @args
