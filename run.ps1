# Launch Screen Translator with the project's venv; every argument is passed through to `python -m app`.
#   .\run.ps1                                     # translate the saved region (Ctrl+Alt+R picks one)
#   .\run.ps1 --lang en --region 0,1040,2560,400  # this run only
#   .\run.ps1 --help
# Set $env:SCREEN_TRANSLATOR_PYTHON to use another interpreter.
$py = $env:SCREEN_TRANSLATOR_PYTHON
if (-not $py) { $py = Join-Path $env:USERPROFILE ".venvs\screen-translator\Scripts\python.exe" }
if (-not (Test-Path $py)) {
    Write-Error "Python not found at $py (create the venv first, see README.md)"
    exit 1
}
# PowerShell parses an unquoted 0,1040,2560,400 as an array: turn it back into "0,1040,2560,400".
$pass = @(foreach ($a in $args) { if ($a -is [array]) { ($a | ForEach-Object { "$_" }) -join "," } else { "$a" } })
Push-Location $PSScriptRoot
try {
    & $py -m app @pass
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $code
