#Requires -Version 7.0
<#
Build the SightVault Windows x64 desktop app (onedir folder) and, when Inno Setup 6
is installed, a per-user installer. Run on a native Windows x64 host; see the
release QA report for actual build and inference evidence.

Uses a separate build environment (.venv-desktop). Without a signing certificate the
executable and installer are unsigned and SmartScreen will warn. Nothing here changes
system security settings.

Optional environment:
  SIGHTVAULT_BUILD_VENV                  build environment folder (default .venv-desktop)
  SIGHTVAULT_DEMO_DB, SIGHTVAULT_DEMO_DATA_ROOT  verified public demo to bundle
  SIGHTVAULT_SIGNTOOL_CERT_SHA1          certificate thumbprint in the user/machine store
  SIGHTVAULT_TIMESTAMP_URL               RFC 3161 timestamp server for signtool
#>
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')
$Root = (Get-Location).Path
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Build the Windows app on a native x64 Windows host.' }
$Lock = 'packaging\desktop\requirements-desktop-windows.lock.txt'
if (-not (Test-Path $Lock)) { throw "Missing $Lock. Resolve it from packaging\desktop\requirements-desktop.in." }
$Venv = if ($env:SIGHTVAULT_BUILD_VENV) { $env:SIGHTVAULT_BUILD_VENV } else { '.venv-desktop' }
if (-not (Test-Path "$Venv\Scripts\python.exe")) { py -3.12 -m venv $Venv; if ($LASTEXITCODE -ne 0) { throw 'Creating the build environment failed.' } }
$Py = Join-Path (Resolve-Path $Venv).Path 'Scripts\python.exe'
& $Py -c "import platform,sys; assert sys.version_info[:2]==(3,12); assert platform.machine()=='AMD64'"
if ($LASTEXITCODE -ne 0) { throw 'Native Windows x64 Python 3.12 required.' }
& $Py -m pip install --require-hashes -r requirements-windows.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed.' }
& $Py -m pip install --require-hashes -r $Lock
if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }

$Out = Join-Path $Root 'build\desktop\windows'
# Only this script's own derived output folder is replaced.
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Path $Out | Out-Null
$StageArgs = @('--stage', "$Out\stage", '--platform', 'windows-x64')
if ($env:SIGHTVAULT_DEMO_DB) { $StageArgs += @('--demo-db', $env:SIGHTVAULT_DEMO_DB, '--demo-data-root', $env:SIGHTVAULT_DEMO_DATA_ROOT) }
& $Py packaging\desktop\stage_resources.py @StageArgs
if ($LASTEXITCODE -ne 0) { throw 'Staging failed.' }

$env:SIGHTVAULT_STAGE = "$Out\stage"; $env:SIGHTVAULT_TARGET = 'windows-x64'; $env:PYTHONHASHSEED = '0'
& $Py -m PyInstaller --noconfirm --clean --distpath "$Out\dist" --workpath "$Out\work" packaging\desktop\SightVault.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$Exe = "$Out\dist\SightVault\SightVault.exe"
$Version = & $Py -c "from sightvault.desktop import APP_VERSION; print(APP_VERSION)"

function Sign-File($Path) {
  if (-not $env:SIGHTVAULT_SIGNTOOL_CERT_SHA1) { return }
  $Timestamp = if ($env:SIGHTVAULT_TIMESTAMP_URL) { $env:SIGHTVAULT_TIMESTAMP_URL } else { throw 'Set SIGHTVAULT_TIMESTAMP_URL for signing.' }
  signtool sign /sha1 $env:SIGHTVAULT_SIGNTOOL_CERT_SHA1 /fd SHA256 /tr $Timestamp /td SHA256 $Path
  if ($LASTEXITCODE -ne 0) { throw "signtool failed for $Path" }
}
Sign-File $Exe
& $Py scripts/verify_desktop_artifact.py create "$Out\dist\SightVault" --out "$Out\contents-manifest.json"
if ($LASTEXITCODE -ne 0) { throw 'Artifact inventory failed.' }
& $Py scripts/verify_desktop_artifact.py verify "$Out\dist\SightVault" "$Out\contents-manifest.json" --report "$Out\contents-verification.json"
if ($LASTEXITCODE -ne 0) { throw 'Artifact contents verification failed.' }

# Headless start/stop check in a disposable per-user folder. The exe is windowed, so capture stdout explicitly.
$SmokeHome = Join-Path ([IO.Path]::GetTempPath()) ("sightvault-smoke-" + [guid]::NewGuid())
$PreviousHome = $env:SIGHTVAULT_HOME
try {
  $env:SIGHTVAULT_HOME = Join-Path $SmokeHome 'home'
  $Smoke = Start-Process -FilePath $Exe -ArgumentList @('--smoke', '--report', ('"{0}"' -f "$Out\smoke.json")) -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput "$Out\smoke.stdout.txt" -RedirectStandardError "$Out\smoke.err.txt"
} finally {
  if ($null -eq $PreviousHome) { Remove-Item Env:\SIGHTVAULT_HOME -ErrorAction SilentlyContinue }
  else { $env:SIGHTVAULT_HOME = $PreviousHome }
  Remove-Item -Recurse -Force $SmokeHome -ErrorAction SilentlyContinue
}
if ($Smoke.ExitCode -ne 0) { throw "Smoke start failed; see $Out\smoke.json and $Out\smoke.err.txt" }
if (-not (Get-Content -Raw "$Out\smoke.json" | ConvertFrom-Json).ready) { throw 'Frozen app did not report ready.' }

$Iscc = Get-Command iscc -ErrorAction SilentlyContinue
if ($Iscc) {
  & $Iscc.Source "/DAppVersion=$Version" "/DSourceDir=$Out\dist\SightVault" "/DOutputDir=$Out" packaging\desktop\SightVault.iss
  if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }
  $Artifact = "$Out\SightVault-$Version-Windows-x64-Setup.exe"
  Sign-File $Artifact
} else {
  Write-Warning 'Inno Setup (iscc) not found; producing a zipped app folder instead of an installer.'
  $Artifact = "$Out\SightVault-$Version-Windows-x64-folder.zip"
  Compress-Archive -Path "$Out\dist\SightVault" -DestinationPath $Artifact
}
(Get-FileHash -Algorithm SHA256 $Artifact).Hash.ToLower() + '  ' + (Split-Path $Artifact -Leaf) |
  Set-Content -Encoding ascii "$Artifact.sha256"
& $Py -m pip freeze --all | Set-Content -Encoding utf8 "$Out\build-environment.txt"
$Signed = if ($env:SIGHTVAULT_SIGNTOOL_CERT_SHA1) { 'Authenticode signed' } else { 'unsigned' }
Write-Host "Built $Artifact ($Signed). Smoke report: $Out\smoke.json"
