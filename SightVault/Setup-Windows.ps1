[CmdletBinding()]
param(
    [switch]$DownloadModels,
    [string[]]$AdditionalModels = @()
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if ($env:OS -ne 'Windows_NT') {
    throw 'Setup-Windows.ps1 must run in Windows PowerShell or PowerShell on Windows.'
}
if ($AdditionalModels.Count -gt 0 -and -not $DownloadModels) {
    throw '-AdditionalModels requires -DownloadModels. Name each optional model group explicitly.'
}

$pythonPath = $env:PYTHON_EXE
if (-not $pythonPath) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $savedNativeErrorPreference = $ErrorActionPreference
        try {
            # A missing requested launcher version may write benign native stderr.
            # Windows PowerShell 5.1 must not turn that redirected stream into a
            # terminating error before the exit code can be checked.
            $ErrorActionPreference = 'Continue'
            $candidate = & $launcher.Source -3.12-64 -c 'import sys; print(sys.executable)' 2>$null
            $launcherExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $savedNativeErrorPreference
        }
        if ($launcherExitCode -eq 0 -and $candidate) { $pythonPath = ($candidate | Select-Object -Last 1).Trim() }
    }
}
if (-not $pythonPath) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonPath = $pythonCommand.Source }
}
if (-not $pythonPath -or -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Install 64-bit CPython 3.12 with the Python Launcher, or set PYTHON_EXE to its python.exe path.'
}

$version = & $pythonPath -c 'import platform,struct,sys; print(f"{sys.version_info.major}.{sys.version_info.minor}|{platform.machine()}|{struct.calcsize(chr(80))*8}")'
if ($LASTEXITCODE -ne 0) { throw 'Could not query the selected Python interpreter.' }
$parts = (($version | Select-Object -Last 1).Trim() -split '\|')
if ($parts.Count -ne 3 -or $parts[0] -ne '3.12' -or $parts[1].ToLowerInvariant() -notin @('amd64','x86_64') -or $parts[2] -ne '64') {
    throw "Expected native x64 Python 3.12; selected interpreter reported: $version"
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv')) {
        throw 'An incomplete .venv already exists. Rename it manually, then run setup again.'
    }
    & $pythonPath -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project virtual environment.' }
}

$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
& $venvPython -m pip install --require-hashes -r requirements-windows.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Pinned Windows dependency installation failed.' }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Installed Python dependencies are inconsistent.' }
& $venvPython scripts\windows_setup_preflight.py
if ($LASTEXITCODE -ne 0) { throw 'Windows setup preflight failed.' }
& $venvPython -m compileall -q app.py sightvault scripts
if ($LASTEXITCODE -ne 0) { throw 'Python source compilation failed.' }

$modelArgs = @('handoff\verify_transfer.py', '--models')
if ($DownloadModels) {
    $downloadArgs = @('handoff\download_pinned_models.py')
    foreach ($modelName in $AdditionalModels) { $downloadArgs += @('--with-model', $modelName) }
    & $venvPython @downloadArgs
    if ($LASTEXITCODE -ne 0) { throw 'Pinned model download or checksum verification failed.' }
    & $venvPython @modelArgs
    if ($LASTEXITCODE -ne 0) { throw 'Required model checksum verification failed.' }
    foreach ($modelName in $AdditionalModels) {
        & $venvPython handoff\verify_transfer.py --models --only $modelName
        if ($LASTEXITCODE -ne 0) { throw "Additional model checksum verification failed: $modelName" }
    }
} else {
    & $venvPython @modelArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'One or more required weights are not installed. Setup succeeded; download them explicitly with .\Setup-Windows.ps1 -DownloadModels.'
    }
}

Write-Host 'Windows setup checks completed. Runtime remains offline; model downloads occur only with -DownloadModels.'
