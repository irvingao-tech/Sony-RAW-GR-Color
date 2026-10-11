param([string]$PythonPath = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $PythonPath -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or later is required.' }
}
& $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $venvPython (Join-Path $projectRoot 'tools\run.py') doctor
if ($LASTEXITCODE -ne 0) { throw 'Decoder runtime check failed.' }
