[CmdletBinding()]
param(
    [switch]$DownloadModels,
    [string[]]$AdditionalModels = @()
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$Executable,
        [Parameter(Mandatory=$true)][string[]]$Arguments,
        [switch]$QuietOutput,
        [switch]$AllowFailure
    )

    $savedNativeErrorPreference = $ErrorActionPreference
    $captured = @()
    $exitCode = 1
    try {
        # Windows PowerShell 5.1 treats redirected native stderr as ErrorRecords.
        # Keep it non-terminating until the process exit code has been captured.
        $ErrorActionPreference = 'Continue'
        $captured = @(& $Executable @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedNativeErrorPreference
    }

    $lines = @($captured | ForEach-Object { $_.ToString() })
    if (-not $QuietOutput) {
        foreach ($line in $lines) { Write-Host $line }
    }
    if ($null -eq $exitCode) { $exitCode = 1 }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "$Name failed with exit code $exitCode."
    }
    return [pscustomobject]@{ ExitCode=[int]$exitCode; Output=[string[]]$lines }
}

if ($env:OS -ne 'Windows_NT') {
    throw 'Setup-Windows.ps1 must run in Windows PowerShell or PowerShell on Windows.'
}
if ($AdditionalModels.Count -gt 0 -and -not $DownloadModels) {
    throw '-AdditionalModels requires -DownloadModels. Name each optional model group explicitly.'
}

$manifestPath = Join-Path $PSScriptRoot 'handoff\MODEL_MANIFEST.json'
try {
    $modelManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -ErrorAction Stop
}
catch {
    throw "Could not read the pinned model manifest: $($_.Exception.Message)"
}
$supportedOptionalWindowsGroups = @()
foreach ($property in $modelManifest.sources.PSObject.Properties) {
    $source = $property.Value
    $isOptional = ($source.optional -eq $true) -or ($source.required -eq $false)
    $platforms = @($source.platforms)
    if ($isOptional -and (($platforms.Count -eq 0) -or ($platforms -contains 'windows-x64'))) {
        $supportedOptionalWindowsGroups += $property.Name
    }
}
foreach ($modelName in $AdditionalModels) {
    $sourceProperty = $modelManifest.sources.PSObject.Properties[$modelName]
    if ($null -eq $sourceProperty) {
        throw "Unknown optional model group '$modelName' in handoff\MODEL_MANIFEST.json."
    }
    $source = $sourceProperty.Value
    if (-not (($source.optional -eq $true) -or ($source.required -eq $false))) {
        throw "Model group '$modelName' is not optional and cannot be selected with -AdditionalModels."
    }
    $platforms = @($source.platforms)
    if (($platforms.Count -gt 0) -and ($platforms -notcontains 'windows-x64')) {
        throw "Optional model group '$modelName' supports $($platforms -join ', '), not windows-x64. Supported optional Windows groups: $($supportedOptionalWindowsGroups -join ', ')."
    }
}

$pythonPath = $env:PYTHON_EXE
if (-not $pythonPath) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $launcherResult = Invoke-Native -Name 'Python launcher probe' -Executable $launcher.Source -Arguments @('-3.12-64', '-c', 'import sys; print(sys.executable)') -QuietOutput -AllowFailure
        if ($launcherResult.ExitCode -eq 0 -and $launcherResult.Output.Count -gt 0) {
            $pythonPath = ($launcherResult.Output | Select-Object -Last 1).Trim()
        }
    }
}
if (-not $pythonPath) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonPath = $pythonCommand.Source }
}
if (-not $pythonPath -or -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Install 64-bit CPython 3.12 with the Python Launcher, or set PYTHON_EXE to its python.exe path.'
}

$versionResult = Invoke-Native -Name 'Selected Python interpreter query' -Executable $pythonPath -Arguments @('-c', 'import platform,struct,sys; print(f"{sys.version_info.major}.{sys.version_info.minor}|{platform.machine()}|{struct.calcsize(chr(80))*8}")') -QuietOutput
$version = if ($versionResult.Output.Count -gt 0) { $versionResult.Output | Select-Object -Last 1 } else { '' }
$parts = ($version.Trim() -split '\|')
if ($parts.Count -ne 3 -or $parts[0] -ne '3.12' -or $parts[1].ToLowerInvariant() -notin @('amd64','x86_64') -or $parts[2] -ne '64') {
    throw "Expected native x64 Python 3.12; selected interpreter reported: $version"
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv')) {
        throw 'An incomplete .venv already exists. Rename it manually, then run setup again.'
    }
    Invoke-Native -Name 'Virtual environment creation' -Executable $pythonPath -Arguments @('-m', 'venv', '.venv') | Out-Null
}

$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
Invoke-Native -Name 'Pinned Windows dependency installation' -Executable $venvPython -Arguments @('-m', 'pip', 'install', '--require-hashes', '-r', 'requirements-windows.lock.txt') | Out-Null
Invoke-Native -Name 'Installed dependency consistency check' -Executable $venvPython -Arguments @('-m', 'pip', 'check') | Out-Null
$preflightArgs = @('scripts\windows_setup_preflight.py')
foreach ($modelName in $AdditionalModels) { $preflightArgs += @('--additional-model', $modelName) }
Invoke-Native -Name 'Windows setup preflight' -Executable $venvPython -Arguments $preflightArgs | Out-Null
Invoke-Native -Name 'Python source compilation' -Executable $venvPython -Arguments @('-m', 'compileall', '-q', 'app.py', 'sightvault', 'scripts') | Out-Null

$modelArgs = @('handoff\verify_transfer.py', '--models')
if ($DownloadModels) {
    $downloadArgs = @('handoff\download_pinned_models.py')
    foreach ($modelName in $AdditionalModels) { $downloadArgs += @('--with-model', $modelName) }
    Invoke-Native -Name 'Pinned model download or checksum verification' -Executable $venvPython -Arguments $downloadArgs | Out-Null
    Invoke-Native -Name 'Required model checksum verification' -Executable $venvPython -Arguments $modelArgs | Out-Null
    foreach ($modelName in $AdditionalModels) {
        $verifyAdditionalArgs = @('handoff\verify_transfer.py', '--models', '--only', $modelName)
        Invoke-Native -Name "Additional model checksum verification: $modelName" -Executable $venvPython -Arguments $verifyAdditionalArgs | Out-Null
    }
} else {
    $verifyResult = Invoke-Native -Name 'Required model checksum verification' -Executable $venvPython -Arguments $modelArgs -AllowFailure
    if ($verifyResult.ExitCode -ne 0) {
        Write-Warning 'One or more required weights are not installed. Setup succeeded; download them explicitly with .\Setup-Windows.ps1 -DownloadModels.'
    }
}

Write-Host 'Windows setup checks completed. Runtime remains offline; model downloads occur only with -DownloadModels.'
