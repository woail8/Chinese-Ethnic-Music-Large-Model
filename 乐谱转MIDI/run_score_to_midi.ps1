param(
  [string]$Python = "",
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Args
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $scriptDir "score_to_midi.py"

if (-not (Test-Path -LiteralPath $py)) {
  throw "Script not found: $py"
}

$pythonExe = $Python.Trim()
if ($pythonExe.Length -eq 0) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $pythonExe = $cmd.Source }
}
if ($pythonExe.Length -eq 0) {
  $fallback = "E:\python\anaconda\python.exe"
  if (Test-Path -LiteralPath $fallback) { $pythonExe = $fallback }
}
if ($pythonExe.Length -eq 0 -or -not (Test-Path -LiteralPath $pythonExe)) {
  throw "Python not found. Install Python or pass -Python with full path to python.exe."
}

& $pythonExe $py @Args

