#Requires -Version 7.0
<#
.SYNOPSIS
    End-to-end acceptance harness for the SightVault Windows desktop installer.

.DESCRIPTION
    Runs only on a clean, ephemeral, native Windows GitHub Actions runner. The harness
    refuses to run when a SightVault uninstall entry, the default application folder,
    the default per-user data folder or a running SightVault process already exists, so
    a real installation is never replaced.

    The harness installs the provided Inno Setup installer silently into a GUID-owned
    folder under RUNNER_TEMP (the path contains spaces on purpose), points
    SIGHTVAULT_HOME at a GUID-owned profile folder and exercises the installed app:

      1. --smoke, then --serve with --report and an owned --stop-file.
      2. Imports the bundled reviewed public demo through the token-guarded desktop API.
      3. Checks 62 records, preserved dates, stored image/thumbnail HTTP and exact-text
         search. Semantic search and analysis are not used, and model weights are
         intentionally absent: the harness verifies that no model was loaded or
         downloaded.
      4. Stops the server through the owned stop marker and records the library hash.
      5. Reinstalls the same installer as an upgrade, checks the library hash is
         unchanged, relaunches, checks the library again and stops it again.
      6. Uninstalls only this owned temporary installation and checks that the profile
         database and demo library survive.

    The JSON report records checks, step timings, exit codes, the installer SHA-256 and
    OS details. It never contains the per-launch desktop token. On failure the owned
    temporary folder is kept for evidence instead of being deleted.

.PARAMETER Installer
    Path to the produced SightVault-*-Windows-x64-Setup.exe.

.PARAMETER Report
    Path for the JSON QA report. Written on pass, failure and refusal.

.EXAMPLE
    pwsh -NoProfile -File scripts/qa_windows_desktop.ps1 `
        -Installer build/desktop/windows/SightVault-0.1.0-Windows-x64-Setup.exe `
        -Report build/desktop/windows/installer-qa.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$Report
)

$ErrorActionPreference = 'Stop'

# Inno Setup AppId from packaging/desktop/SightVault.iss. The uninstall entry is per user
# and is named "<AppId>_is1" by Inno Setup.
$script:uninstallKeyName = '{6F1D3C2A-8B4E-4F7A-9C21-5E3B7A9D0C14}_is1'
$script:desktopTokenHeader = 'X-SightVault-Desktop-Token'

$script:installerPath = [System.IO.Path]::GetFullPath($Installer)
$script:reportPath = [System.IO.Path]::GetFullPath($Report)

$script:checks = [System.Collections.Generic.List[object]]::new()
$script:steps = [System.Collections.Generic.List[object]]::new()
$script:failures = [System.Collections.Generic.List[string]]::new()
$script:timings = [ordered]@{}
$script:exitCodes = [ordered]@{}
$script:savedEnvironment = @{}
$script:startedUtc = [DateTime]::UtcNow
$script:clock = [System.Diagnostics.Stopwatch]::StartNew()
$script:osInfo = [ordered]@{ Caption = $null; Version = $null; Build = $null }
$script:ownedRoot = $null
$script:installDir = $null
$script:profileHome = $null
$script:artifactsDir = $null
$script:exePath = $null
$script:serveProcess = $null
$script:activeStopFile = $null
$script:resourcesPath = $null
$script:libraryFingerprint = $null
$script:dbHashAfterFirstRun = $null
$script:artifactSha = $null
$script:artifactSize = $null
$script:installerVersion = $null
$script:status = 'failed'
$script:errorText = $null
$script:runOk = $false
$script:retainedEvidence = $false

function Write-HarnessLog {
    param([string]$Text)
    Write-Host ('[{0}] {1}' -f [DateTime]::UtcNow.ToString('HH:mm:ss'), $Text)
}

function Add-Check {
    param([string]$Name, [bool]$Passed, [string]$Detail = '')
    $script:checks.Add([pscustomobject]@{ name = $Name; ok = $Passed; detail = $Detail })
    if (-not $Passed) { $script:failures.Add($Name) }
    $label = if ($Passed) { 'PASS' } else { 'FAIL' }
    $line = $label + ' ' + $Name
    if ($Detail) { $line += ' - ' + $Detail }
    Write-HarnessLog $line
}

function Require-Check {
    param([string]$Name, [bool]$Passed, [string]$Detail = '')
    Add-Check -Name $Name -Passed $Passed -Detail $Detail
    if (-not $Passed) { throw "Check failed: $Name" }
}

function Invoke-QaStep {
    param([string]$Name, [scriptblock]$Action)
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $null = & $Action
        $watch.Stop()
        $script:timings[$Name] = [int]$watch.ElapsedMilliseconds
        $script:steps.Add([pscustomobject]@{ name = $Name; ok = $true; duration_ms = [int]$watch.ElapsedMilliseconds })
        Write-HarnessLog ('STEP {0} finished in {1} ms' -f $Name, $watch.ElapsedMilliseconds)
    }
    catch {
        $watch.Stop()
        $message = $_.Exception.Message
        $script:timings[$Name] = [int]$watch.ElapsedMilliseconds
        $script:steps.Add([pscustomobject]@{ name = $Name; ok = $false; duration_ms = [int]$watch.ElapsedMilliseconds; error = $message })
        Write-HarnessLog ('STEP {0} failed after {1} ms: {2}' -f $Name, $watch.ElapsedMilliseconds, $message)
        throw
    }
}

function Get-JsonProperty {
    param($Object, [string]$Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return $Default }
    return $property.Value
}

function Test-IsUnder {
    param([string]$Path, [string]$Parent)
    if ([string]::IsNullOrWhiteSpace($Path) -or [string]::IsNullOrWhiteSpace($Parent)) { return $false }
    try {
        $child = [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
        $root = [System.IO.Path]::GetFullPath($Parent).TrimEnd([char[]]@('\', '/'))
    }
    catch {
        return $false
    }
    if ([string]::Equals($child, $root, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    return $child.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-SamePath {
    param([string]$Left, [string]$Right)
    if ([string]::IsNullOrWhiteSpace($Left) -or [string]::IsNullOrWhiteSpace($Right)) { return $false }
    try {
        $a = [System.IO.Path]::GetFullPath($Left).TrimEnd([char[]]@('\', '/'))
        $b = [System.IO.Path]::GetFullPath($Right).TrimEnd([char[]]@('\', '/'))
    }
    catch {
        return $false
    }
    return [string]::Equals($a, $b, [System.StringComparison]::OrdinalIgnoreCase)
}

function Wait-ForCondition {
    param([scriptblock]$Condition, [int]$TimeoutSeconds, [int]$IntervalMilliseconds = 250)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            if (& $Condition) { return $true }
        }
        catch {
            # A file that is still being written or a port that is not listening yet.
        }
        Start-Sleep -Milliseconds $IntervalMilliseconds
    }
    try { return [bool](& $Condition) } catch { return $false }
}

function Get-RegistryUninstallEntry {
    $entries = @()
    if (-not $IsWindows) { return $entries }
    $subKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + $script:uninstallKeyName
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryHive]::LocalMachine)) {
        foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
            $base = $null
            try {
                $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
                $key = $base.OpenSubKey($subKey)
                if ($null -ne $key) {
                    try {
                        $entries += [pscustomobject]@{
                            hive = $hive.ToString()
                            view = $view.ToString()
                            display_name = [string]$key.GetValue('DisplayName')
                            install_location = [string]$key.GetValue('InstallLocation')
                        }
                    }
                    finally {
                        $key.Dispose()
                    }
                }
            }
            catch {
                # A hive or view that is unavailable on this host cannot hold the entry.
            }
            finally {
                if ($null -ne $base) { $base.Dispose() }
            }
        }
    }
    return $entries
}

function Get-PreflightRefusals {
    $reasons = [System.Collections.Generic.List[string]]::new()
    if (-not $IsWindows) {
        $reasons.Add('This harness only runs on native Windows.')
        return $reasons
    }
    try {
        $osBuild = [Environment]::OSVersion.Version.Build
        if ($osBuild -gt 0 -and $osBuild -lt 19041) {
            $reasons.Add('The runner OS build (' + $osBuild + ') is older than Windows 10 version 2004 (10.0.19041).')
        }
    }
    catch {
        # The installer's MinVersion is still authoritative when the build cannot be read.
    }
    if ($env:GITHUB_ACTIONS -ne 'true') {
        $reasons.Add('GITHUB_ACTIONS is not true. This harness only runs on a clean ephemeral GitHub Actions runner.')
    }
    if ($env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
        $reasons.Add('The runner is not a GitHub-hosted ephemeral runner (RUNNER_ENVIRONMENT=' + [string]$env:RUNNER_ENVIRONMENT + ').')
    }
    if ($env:RUNNER_OS -ne 'Windows') {
        $reasons.Add('The GitHub Actions runner OS is not Windows (RUNNER_OS=' + [string]$env:RUNNER_OS + ').')
    }
    if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP) -or -not (Test-Path -LiteralPath $env:RUNNER_TEMP -PathType Container)) {
        $reasons.Add('RUNNER_TEMP is missing or not a folder, so no owned temporary location can be created.')
    }
    elseif (-not [System.IO.Path]::IsPathRooted($env:RUNNER_TEMP)) {
        $reasons.Add('RUNNER_TEMP is not an absolute path, so an owned temporary location cannot be guaranteed.')
    }
    if (Test-SamePath $script:reportPath $script:installerPath) {
        $reasons.Add('The QA report path must differ from the installer path.')
    }
    $installerName = [System.IO.Path]::GetFileName($script:installerPath)
    $versionMatch = [regex]::Match($installerName, '^SightVault-(?<version>.+)-Windows-x64-Setup\.exe$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($versionMatch.Success) {
        $script:installerVersion = $versionMatch.Groups['version'].Value
    }
    else {
        $reasons.Add('The input must be a SightVault-<version>-Windows-x64-Setup.exe installer.')
    }
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        $reasons.Add('The runner is not native Windows x64 (PROCESSOR_ARCHITECTURE=' + [string]$env:PROCESSOR_ARCHITECTURE + ').')
    }
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        $reasons.Add('PowerShell 7 or later is required.')
    }
    $entries = @(Get-RegistryUninstallEntry)
    if ($entries.Count -gt 0) {
        $reasons.Add('An existing SightVault uninstall entry would be replaced (' + [string]$entries[0].display_name + ').')
    }
    $localAppData = [string]$env:LOCALAPPDATA
    if (-not [string]::IsNullOrWhiteSpace($localAppData)) {
        $defaultApp = Join-Path $localAppData 'Programs\SightVault'
        if (Test-Path -LiteralPath $defaultApp) {
            $reasons.Add('The default per-user application folder already exists: ' + $defaultApp)
        }
        $defaultProfile = Join-Path $localAppData 'SightVault'
        if (Test-Path -LiteralPath $defaultProfile) {
            $reasons.Add('The default per-user data folder already exists: ' + $defaultProfile)
        }
    }
    $running = @(Get-Process -Name 'SightVault' -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        $reasons.Add('A SightVault process is already running (' + [string]$running[0].Id + ').')
    }
    return $reasons
}

function Restore-HarnessEnvironment {
    foreach ($name in @($script:savedEnvironment.Keys)) {
        $value = $script:savedEnvironment[$name]
        if ($null -eq $value) {
            Remove-Item -Path ('Env:' + $name) -ErrorAction SilentlyContinue
        }
        else {
            Set-Item -Path ('Env:' + $name) -Value $value
        }
    }
}

function Get-LibraryDatabaseHash {
    $database = Join-Path $script:profileHome 'data\sightvault.sqlite3'
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) { return $null }
    return (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Stop-OwnedProcess {
    param($Process, [string]$ExpectedPath)
    if ($null -eq $Process) { return $false }
    try {
        $current = Get-Process -Id $Process.Id -ErrorAction Stop
    }
    catch {
        return $false
    }
    try {
        if ([math]::Abs(($current.StartTime - $Process.StartTime).TotalSeconds) -gt 2) { return $false }
    }
    catch {
        return $false
    }
    try {
        if ($current.Path -and $ExpectedPath -and -not [string]::Equals($current.Path, $ExpectedPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }
    }
    catch {
        # Path is unavailable for some protected processes; the PID and start time still match.
    }
    try {
        Stop-Process -Id $current.Id -Force -ErrorAction Stop
    }
    catch {
        return $false
    }
    try { $null = $Process.WaitForExit(15000) } catch { }
    return $true
}

function Invoke-NativeProcess {
    param([string]$FilePath, [string]$Arguments, [int]$TimeoutSeconds, [string]$Label)
    Write-HarnessLog ('RUN {0}: {1} {2}' -f $Label, $FilePath, $Arguments)
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $stopped = Stop-OwnedProcess -Process $process -ExpectedPath $FilePath
        throw ('{0} timed out after {1} s (the exact started process was stopped: {2}).' -f $Label, $TimeoutSeconds, $stopped)
    }
    $exitCode = [int]$process.ExitCode
    Write-HarnessLog ('EXIT {0}: {1}' -f $Label, $exitCode)
    return $exitCode
}

function Invoke-Installer {
    param([string]$LogPath, [string]$Label)
    # SightVault.iss fixes PrivilegesRequired=lowest and has no mode override, so the
    # installer and its uninstall registration stay in the current user's profile.
    $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR="{0}" /LOG="{1}"' -f $script:installDir, $LogPath
    return Invoke-NativeProcess -FilePath $script:installerPath -Arguments $arguments -TimeoutSeconds 900 -Label $Label
}

function Invoke-SightVaultRequest {
    param(
        [string]$Uri,
        [string]$Method = 'GET',
        [hashtable]$Headers = @{},
        [string]$OutFile
    )
    $request = @{
        Uri = $Uri
        Method = $Method
        NoProxy = $true
        TimeoutSec = 60
        SkipHttpErrorCheck = $true
        MaximumRedirection = 0
    }
    if ($Headers.Count -gt 0) { $request['Headers'] = $Headers }
    if ($OutFile) { $request['OutFile'] = $OutFile }
    $response = Invoke-WebRequest @request
    $json = $null
    if (-not $OutFile -and -not [string]::IsNullOrWhiteSpace($response.Content)) {
        try { $json = $response.Content | ConvertFrom-Json -ErrorAction Stop } catch { $json = $null }
    }
    return [pscustomobject]@{ Status = [int]$response.StatusCode; Json = $json }
}

function Start-OwnedServer {
    param([string]$StopFile, [string]$ReportFile)
    if (Test-Path -LiteralPath $StopFile) {
        throw ('Refusing to start: a stop marker already exists at {0}.' -f $StopFile)
    }
    Remove-Item -LiteralPath $ReportFile -Force -ErrorAction SilentlyContinue
    $arguments = '--serve --report "{0}" --stop-file "{1}"' -f $ReportFile, $StopFile
    Write-HarnessLog ('START serve: {0} {1}' -f $script:exePath, $arguments)
    $process = Start-Process -FilePath $script:exePath -ArgumentList $arguments -WorkingDirectory $script:installDir -PassThru -WindowStyle Hidden
    $script:serveProcess = $process
    $script:activeStopFile = $StopFile
    $deadline = [DateTime]::UtcNow.AddSeconds(240)
    $readyReport = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) {
            throw ('The installed app exited during startup with code {0}. See the profile logs.' -f $process.ExitCode)
        }
        if (Test-Path -LiteralPath $ReportFile) {
            try { $readyReport = Get-Content -LiteralPath $ReportFile -Raw | ConvertFrom-Json -ErrorAction Stop }
            catch { $readyReport = $null }
            if ($null -ne $readyReport) { break }
        }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $readyReport) {
        throw 'The installed app did not write its readiness report before the deadline.'
    }
    return $readyReport
}

function Read-OwnedInstanceRecord {
    param([int]$ExpectedPid)
    if (-not (Wait-ForCondition -TimeoutSeconds 30 -Condition { Test-Path -LiteralPath (Join-Path $script:profileHome 'instance.json') })) {
        throw 'The installed app did not write profile\instance.json.'
    }
    $recordPath = Join-Path $script:profileHome 'instance.json'
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json -ErrorAction Stop
    $recordPid = Get-JsonProperty $record 'pid'
    if ($null -eq $recordPid -or [int]$recordPid -ne $ExpectedPid) {
        throw 'profile\instance.json does not belong to the process this harness started.'
    }
    $token = [string](Get-JsonProperty $record 'token' '')
    if ($token -notmatch '^[0-9a-f]{64}$') {
        throw 'profile\instance.json does not contain a valid launch token.'
    }
    $url = [string](Get-JsonProperty $record 'url' '')
    $uri = $null
    if (-not [uri]::TryCreate($url, [UriKind]::Absolute, [ref]$uri)) {
        throw 'profile\instance.json does not contain an absolute URL.'
    }
    if ($uri.Scheme -ne 'http' -or $uri.Host -ne '127.0.0.1' -or $uri.Port -lt 1 -or $uri.Port -gt 65535 -or
        $uri.UserInfo -or $uri.AbsolutePath -notin @('', '/')) {
        throw 'profile\instance.json does not contain a plain 127.0.0.1 loopback origin.'
    }
    return [pscustomobject]@{ Pid = [int]$recordPid; Url = $url; Token = $token }
}

function Get-LibraryFingerprint {
    param([object[]]$Assets)
    $lines = @($Assets | Sort-Object { [int]$_.id } | ForEach-Object {
            '{0}|{1}|{2}|{3}|{4}' -f $_.id, $_.filename, $_.timestamp, $_.indexed_at, $_.sha256
        })
    $text = $lines -join "`n"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text))
    }
    finally {
        $sha.Dispose()
    }
    return (-join ($bytes | ForEach-Object { $_.ToString('x2') }))
}

function Assert-ImportedLibrary {
    param([string]$Prefix, [string]$ApiBase, [string]$DataDir)

    $statusResponse = Invoke-SightVaultRequest -Uri ($ApiBase + '/api/status')
    Require-Check ($Prefix + '_status_http_200') ($statusResponse.Status -eq 200) ('HTTP ' + $statusResponse.Status)
    $status = $statusResponse.Json
    Require-Check ($Prefix + '_status_reports_62_assets') ((Get-JsonProperty $status 'total' 0) -eq 62) ('total=' + (Get-JsonProperty $status 'total' 0))
    Require-Check ($Prefix + '_status_reports_62_embeddings') ((Get-JsonProperty $status 'with_embeddings' 0) -eq 62) ('with_embeddings=' + (Get-JsonProperty $status 'with_embeddings' 0))
    Require-Check ($Prefix + '_status_reports_ocr') ([int](Get-JsonProperty $status 'with_ocr' 0) -gt 0) ('with_ocr=' + (Get-JsonProperty $status 'with_ocr' 0))

    # List the whole corpus with an empty exact query. Empty queries never call a model,
    # and exact mode keeps the listing independent of semantic search.
    $listResponse = Invoke-SightVaultRequest -Uri ($ApiBase + '/api/search?q=&mode=exact&top_k=100')
    Require-Check ($Prefix + '_asset_list_http_200') ($listResponse.Status -eq 200) ('HTTP ' + $listResponse.Status)
    $assets = @(Get-JsonProperty $listResponse.Json 'results' @())
    Require-Check ($Prefix + '_asset_list_has_62_rows') ($assets.Count -eq 62) ('rows=' + $assets.Count)
    Require-Check ($Prefix + '_asset_list_has_no_warning') ([string]::IsNullOrEmpty([string](Get-JsonProperty $listResponse.Json 'warning' ''))) ('warning=' + (Get-JsonProperty $listResponse.Json 'warning' ''))
    Require-Check ($Prefix + '_asset_ids_and_names_unique') ((@($assets | ForEach-Object { [int]$_.id } | Sort-Object -Unique).Count -eq 62) -and (@($assets | ForEach-Object { [string]$_.filename } | Sort-Object -Unique).Count -eq 62)) 'IDs and filenames are unique.'

    $sampleFolder = Join-Path $DataDir 'demo-samples'
    $outside = @($assets | Where-Object { -not (Test-IsUnder ([string](Get-JsonProperty $_ 'filepath' '')) $sampleFolder) })
    Require-Check ($Prefix + '_sources_inside_owned_profile') ($outside.Count -eq 0) ('assets outside ' + $sampleFolder + ': ' + $outside.Count)

    $missingFiles = [System.Collections.Generic.List[string]]::new()
    $dateMismatches = [System.Collections.Generic.List[string]]::new()
    $sourceMismatches = [System.Collections.Generic.List[string]]::new()
    foreach ($asset in $assets) {
        $file = [string](Get-JsonProperty $asset 'filepath' '')
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            $missingFiles.Add([string]$asset.id)
            continue
        }
        if ([string](Get-JsonProperty $asset 'timestamp_source' '') -ne 'File modified time') {
            $sourceMismatches.Add([string]$asset.id)
        }
        $timestamp = [string](Get-JsonProperty $asset 'timestamp' '')
        $modified = (Get-Item -LiteralPath $file).LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ss') + '+00:00'
        if ($timestamp -ne $modified) {
            if ($dateMismatches.Count -lt 3) {
                $dateMismatches.Add(('id {0}: {1} != file mtime {2}' -f $asset.id, $timestamp, $modified))
            }
            else {
                $dateMismatches.Add([string]$asset.id)
            }
        }
    }
    Require-Check ($Prefix + '_source_files_present') ($missingFiles.Count -eq 0) ('missing source files: ' + ($missingFiles -join ','))
    Require-Check ($Prefix + '_dates_preserved') ($dateMismatches.Count -eq 0) ('timestamp/mtime mismatches: ' + ($dateMismatches -join '; '))
    Require-Check ($Prefix + '_timestamp_source_recorded') ($sourceMismatches.Count -eq 0) ('unexpected timestamp_source for IDs: ' + ($sourceMismatches -join ','))

    $imageAsset = $assets[0]
    $imageId = [int]$imageAsset.id
    $imageFile = Join-Path $script:artifactsDir ($Prefix + '-image.png')
    Remove-Item -LiteralPath $imageFile -Force -ErrorAction SilentlyContinue
    $imageResponse = Invoke-SightVaultRequest -Uri ($ApiBase + '/api/assets/' + $imageId + '/image') -OutFile $imageFile
    $imageDetail = 'HTTP ' + $imageResponse.Status
    $imageOk = $false
    if ($imageResponse.Status -eq 200 -and (Test-Path -LiteralPath $imageFile -PathType Leaf)) {
        $bytes = [System.IO.File]::ReadAllBytes($imageFile)
        $imageOk = $bytes.Length -gt 1024 -and $bytes[0] -eq 0x89 -and $bytes[1] -eq 0x50 -and $bytes[2] -eq 0x4E -and $bytes[3] -eq 0x47
        $imageDetail += (', ' + $bytes.Length + ' bytes, PNG signature=' + $imageOk)
    }
    Require-Check ($Prefix + '_image_http_served') $imageOk $imageDetail

    $thumbFile = Join-Path $script:artifactsDir ($Prefix + '-thumb.webp')
    Remove-Item -LiteralPath $thumbFile -Force -ErrorAction SilentlyContinue
    $thumbResponse = Invoke-SightVaultRequest -Uri ($ApiBase + '/api/assets/' + $imageId + '/image?thumbnail=true') -OutFile $thumbFile
    $thumbDetail = 'HTTP ' + $thumbResponse.Status
    $thumbOk = $false
    if ($thumbResponse.Status -eq 200 -and (Test-Path -LiteralPath $thumbFile -PathType Leaf)) {
        $thumbBytes = [System.IO.File]::ReadAllBytes($thumbFile)
        $thumbOk = $thumbBytes.Length -gt 256 -and
            $thumbBytes[0] -eq 0x52 -and $thumbBytes[1] -eq 0x49 -and $thumbBytes[2] -eq 0x46 -and $thumbBytes[3] -eq 0x46 -and
            $thumbBytes[8] -eq 0x57 -and $thumbBytes[9] -eq 0x45 -and $thumbBytes[10] -eq 0x42 -and $thumbBytes[11] -eq 0x50
        $thumbDetail += (', ' + $thumbBytes.Length + ' bytes, WebP signature=' + $thumbOk)
    }
    Require-Check ($Prefix + '_thumbnail_http_served') $thumbOk $thumbDetail

    $assetResponse = Invoke-SightVaultRequest -Uri ($ApiBase + '/api/assets/' + $imageId)
    Require-Check ($Prefix + '_asset_http_served') ($assetResponse.Status -eq 200 -and (Get-JsonProperty $assetResponse.Json 'source_exists' $false) -eq $true) ('HTTP ' + $assetResponse.Status)

    # Exact text search: derive a word from the imported OCR text, then search it with
    # mode=exact. Exact mode never calls the embedding model, so no weights are needed.
    $exactPassed = $false
    $exactDetail = 'No OCR word produced an exact match.'
    foreach ($asset in $assets) {
        if ($exactPassed) { break }
        $ocrText = [string](Get-JsonProperty $asset 'ocr_text' '')
        if ([string]::IsNullOrWhiteSpace($ocrText)) { continue }
        $candidates = @([regex]::Matches($ocrText, '[A-Za-z][A-Za-z0-9]{5,20}') | ForEach-Object { $_.Value } | Select-Object -Unique -First 3)
        foreach ($term in $candidates) {
            $searchUri = $ApiBase + '/api/search?q=' + [uri]::EscapeDataString($term) + '&mode=exact&top_k=100'
            $searchResponse = Invoke-SightVaultRequest -Uri $searchUri
            if ($searchResponse.Status -ne 200) { continue }
            $hits = @(Get-JsonProperty $searchResponse.Json 'results' @())
            $hit = $hits | Where-Object { [int](Get-JsonProperty $_ 'id' -1) -eq [int]$asset.id } | Select-Object -First 1
            if ($null -eq $hit) { continue }
            $warning = [string](Get-JsonProperty $searchResponse.Json 'warning' '')
            $exactPassed = ((Get-JsonProperty $searchResponse.Json 'mode' '') -eq 'exact') -and
                [string]::IsNullOrEmpty($warning) -and
                ([bool](Get-JsonProperty $hit 'exact_match' $false)) -and
                ([int](Get-JsonProperty $searchResponse.Json 'total_matches' 0) -ge 1)
            $exactDetail = ('asset_id={0}; mode={1}; total_matches={2}; exact_match={3}; warning="{4}"' -f
                $asset.id, (Get-JsonProperty $searchResponse.Json 'mode' ''), (Get-JsonProperty $searchResponse.Json 'total_matches' 0),
                (Get-JsonProperty $hit 'exact_match' $false), $warning)
            if ($exactPassed) { break }
        }
    }
    Require-Check ($Prefix + '_exact_text_search') $exactPassed $exactDetail

    # No model weights are bundled or downloaded: every model must stay unavailable and
    # unloaded, and the profile model folder must stay empty.
    $models = Get-JsonProperty $status 'models'
    Require-Check ($Prefix + '_model_status_present') ($null -ne $models) 'The status API reported model states.'
    foreach ($name in @('embedding', 'ocr', 'vision')) {
        $state = Get-JsonProperty $models $name
        $loaded = [bool](Get-JsonProperty $state 'loaded' $true)
        $available = [bool](Get-JsonProperty $state 'available' $true)
        Require-Check ($Prefix + '_model_' + $name + '_not_loaded') ((-not $loaded) -and (-not $available)) ('available=' + $available + '; loaded=' + $loaded)
    }
    $modelFolder = Join-Path $script:profileHome 'models'
    $modelFiles = @(Get-ChildItem -LiteralPath $modelFolder -Recurse -File -Force -ErrorAction SilentlyContinue)
    Require-Check ($Prefix + '_no_model_weights_in_profile') ($modelFiles.Count -eq 0) ($modelFiles.Count.ToString() + ' files in ' + $modelFolder)

    return Get-LibraryFingerprint -Assets $assets
}

function Stop-OwnedServerGracefully {
    param($Process, [string]$StopFile, [string]$CheckPrefix)
    if (Test-Path -LiteralPath $StopFile) {
        throw ('Refusing to reuse an existing stop marker at {0}.' -f $StopFile)
    }
    New-Item -ItemType File -Path $StopFile -Force | Out-Null
    if (-not $Process.WaitForExit(90000)) {
        $stopped = Stop-OwnedProcess -Process $Process -ExpectedPath $script:exePath
        Add-Check ($CheckPrefix + '_stopped_within_deadline') $false ('timed out after 90 s; exact owned process force-stopped=' + $stopped)
        throw 'The installed app did not stop after the owned stop marker was created.'
    }
    $exitCode = [int]$Process.ExitCode
    Require-Check ($CheckPrefix + '_exit_zero') ($exitCode -eq 0) ('exit code ' + $exitCode)
    $script:serveProcess = $null
    $script:activeStopFile = $null
    Require-Check ($CheckPrefix + '_instance_record_removed') (Wait-ForCondition -TimeoutSeconds 15 -Condition { -not (Test-Path -LiteralPath (Join-Path $script:profileHome 'instance.json')) }) 'profile\instance.json was removed.'
    return $exitCode
}

function Write-QaReport {
    $report = [ordered]@{
        artifact_type = 'SightVault Windows desktop installer acceptance'
        status = $script:status
        ok = ($script:status -eq 'passed')
        started_utc = $script:startedUtc.ToString('o')
        finished_utc = [DateTime]::UtcNow.ToString('o')
        duration_ms = [int]$script:clock.ElapsedMilliseconds
        error = $script:errorText
        os = [ordered]@{
            caption = $script:osInfo.Caption
            version = $script:osInfo.Version
            build = $script:osInfo.Build
            architecture = [string]$env:PROCESSOR_ARCHITECTURE
            runner_os = [string]$env:RUNNER_OS
            runner_image = [string]$env:ImageOS
            powershell = $PSVersionTable.PSVersion.ToString()
        }
        artifact = [ordered]@{
            path = $script:installerPath
            version = $script:installerVersion
            sha256 = $script:artifactSha
            size_bytes = $script:artifactSize
        }
        owned = [ordered]@{
            root = $script:ownedRoot
            install_dir = $script:installDir
            profile = $script:profileHome
            retained_for_evidence = $script:retainedEvidence
        }
        exit_codes = $script:exitCodes
        timings_ms = $script:timings
        checks = $script:checks.ToArray()
        steps = $script:steps.ToArray()
        failures = $script:failures.ToArray()
    }
    $directory = Split-Path -Parent $script:reportPath
    if (-not [string]::IsNullOrWhiteSpace($directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $temporary = $script:reportPath + '.tmp'
    [System.IO.File]::WriteAllText($temporary, ($report | ConvertTo-Json -Depth 12) + "`n", [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $script:reportPath -Force
    Write-HarnessLog ('QA report written to ' + $script:reportPath)
}

# ---------------------------------------------------------------------------------------
# Preflight: environment and ownership. Nothing is installed before this passes.
# ---------------------------------------------------------------------------------------

if ($IsWindows) {
    try {
        $operatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        $script:osInfo['Caption'] = [string]$operatingSystem.Caption
        $script:osInfo['Version'] = [string]$operatingSystem.Version
        $script:osInfo['Build'] = [string]$operatingSystem.BuildNumber
    }
    catch {
        # The report keeps the remaining OS fields; refusal/failure is unaffected.
    }
}

$refusals = @(Get-PreflightRefusals)
if ($refusals.Count -gt 0) {
    foreach ($reason in $refusals) {
        Add-Check 'preflight_refusal' $false $reason
    }
    # Record the artifact identity when it is already readable, without running anything.
    if (Test-Path -LiteralPath $script:installerPath -PathType Leaf) {
        try {
            $script:artifactSize = (Get-Item -LiteralPath $script:installerPath).Length
            $script:artifactSha = (Get-FileHash -LiteralPath $script:installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        catch {
            # The refusal stands even when the artifact cannot be hashed.
        }
    }
    $script:status = 'refused'
    $script:errorText = ($refusals -join ' ')
    Write-QaReport
    exit 2
}
Add-Check 'preflight_clean_ephemeral_windows_runner' $true ('GitHub Actions runner, ' + [string]$env:ImageOS + ', ' + [string]$env:PROCESSOR_ARCHITECTURE)

if (-not (Test-Path -LiteralPath $script:installerPath -PathType Leaf)) {
    $script:status = 'failed'
    $script:errorText = 'Installer not found: ' + $script:installerPath
    Add-Check 'installer_present' $false $script:installerPath
    Write-QaReport
    exit 1
}
$installerItem = Get-Item -LiteralPath $script:installerPath
$script:artifactSize = $installerItem.Length
$script:artifactSha = (Get-FileHash -LiteralPath $script:installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
Add-Check 'installer_present' $true ($script:installerPath + ' (' + $script:artifactSize + ' bytes)')

$ownedName = 'sightvault-desktop-qa-' + [guid]::NewGuid().ToString('N')
$script:ownedRoot = Join-Path $env:RUNNER_TEMP $ownedName
$script:installDir = Join-Path $script:ownedRoot 'SightVault App'
$script:profileHome = Join-Path $script:ownedRoot 'SightVault Home'
$script:artifactsDir = Join-Path $script:ownedRoot 'qa'
$script:exePath = Join-Path $script:installDir 'SightVault.exe'

foreach ($name in @('SIGHTVAULT_HOME', 'SIGHTVAULT_DATA', 'SIGHTVAULT_MODELS')) {
    $script:savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    # Create the unpredictable root without -Force, so an existing GUID path is never reused.
    New-Item -ItemType Directory -Path $script:ownedRoot | Out-Null
    New-Item -ItemType Directory -Path $script:installDir, $script:profileHome, $script:artifactsDir | Out-Null
    Require-Check 'owned_temp_layout_created' ((Test-IsUnder $script:ownedRoot $env:RUNNER_TEMP) -and (Test-IsUnder $script:installDir $script:ownedRoot)) ($script:installDir + ' | ' + $script:profileHome)
    $env:SIGHTVAULT_HOME = $script:profileHome
    Remove-Item -Path 'Env:SIGHTVAULT_DATA' -ErrorAction SilentlyContinue
    Remove-Item -Path 'Env:SIGHTVAULT_MODELS' -ErrorAction SilentlyContinue

    Invoke-QaStep -Name 'install' -Action {
        $exitCode = Invoke-Installer -LogPath (Join-Path $script:artifactsDir 'install-first.log') -Label 'first install'
        $script:exitCodes['install'] = $exitCode
        Require-Check 'installer_exit_zero' ($exitCode -eq 0) ('exit code ' + $exitCode)
        Require-Check 'installed_executable_present' (Test-Path -LiteralPath $script:exePath -PathType Leaf) $script:exePath
        $entries = @(Get-RegistryUninstallEntry)
        $perUserEntries = @($entries | Where-Object { $_.hive -eq 'CurrentUser' })
        Require-Check 'per_user_uninstall_entry_registered' ($perUserEntries.Count -ge 1) ($perUserEntries.Count.ToString() + ' per-user uninstall entries')
        if ($perUserEntries.Count -ge 1 -and -not [string]::IsNullOrWhiteSpace([string]$perUserEntries[0].install_location)) {
            Add-Check 'uninstall_entry_points_to_owned_install' (Test-SamePath ([string]$perUserEntries[0].install_location) $script:installDir) ([string]$perUserEntries[0].install_location)
        }
        $localAppData = [string]$env:LOCALAPPDATA
        if (-not [string]::IsNullOrWhiteSpace($localAppData)) {
            Require-Check 'default_app_folder_not_created' (-not (Test-Path -LiteralPath (Join-Path $localAppData 'Programs\SightVault'))) 'The install went to the owned folder.'
        }
    }

    Invoke-QaStep -Name 'smoke' -Action {
        $smokeReport = Join-Path $script:artifactsDir 'smoke.json'
        Remove-Item -LiteralPath $smokeReport -Force -ErrorAction SilentlyContinue
        $arguments = '--smoke --report "{0}"' -f $smokeReport
        $exitCode = Invoke-NativeProcess -FilePath $script:exePath -Arguments $arguments -TimeoutSeconds 240 -Label 'smoke'
        $script:exitCodes['smoke'] = $exitCode
        Require-Check 'smoke_exit_zero' ($exitCode -eq 0) ('exit code ' + $exitCode)
        Require-Check 'smoke_report_written' (Test-Path -LiteralPath $smokeReport -PathType Leaf) $smokeReport
        $smoke = Get-Content -LiteralPath $smokeReport -Raw | ConvertFrom-Json -ErrorAction Stop
        Require-Check 'smoke_ready_true' ((Get-JsonProperty $smoke 'ready' $false) -eq $true) ('ready=' + (Get-JsonProperty $smoke 'ready' $false))
        Require-Check 'smoke_no_server_error' ([string]::IsNullOrEmpty([string](Get-JsonProperty $smoke 'error' ''))) ('error=' + (Get-JsonProperty $smoke 'error' ''))
        $smokeUrl = [string](Get-JsonProperty $smoke 'url' '')
        $smokeUri = $null
        $urlOk = [uri]::TryCreate($smokeUrl, [UriKind]::Absolute, [ref]$smokeUri) -and
            $smokeUri.Scheme -eq 'http' -and $smokeUri.Host -eq '127.0.0.1' -and $smokeUri.Port -ge 1
        Require-Check 'smoke_loopback_url' $urlOk $smokeUrl

        $paths = Get-JsonProperty $smoke 'paths'
        $pathChecks = @(
            @('base', $script:profileHome),
            @('data', (Join-Path $script:profileHome 'data')),
            @('models', (Join-Path $script:profileHome 'models')),
            @('logs', (Join-Path $script:profileHome 'logs'))
        )
        $badPaths = @()
        foreach ($pair in $pathChecks) {
            $actual = [string](Get-JsonProperty $paths $pair[0] '')
            if (-not (Test-SamePath $actual $pair[1])) { $badPaths += ($pair[0] + '=' + $actual) }
        }
        Require-Check 'smoke_per_user_paths' ($badPaths.Count -eq 0) ($badPaths -join '; ')
        Require-Check 'smoke_models_folder_writable' ((Get-JsonProperty $paths 'models_writable' $false) -eq $true) 'models_writable=true'

        $resources = [string](Get-JsonProperty $paths 'resources' '')
        $script:resourcesPath = $resources
        Require-Check 'smoke_source_resources_inside_install' (Test-IsUnder $resources $script:installDir) $resources
        $buildInfoPath = Join-Path $resources 'build-info.json'
        Require-Check 'smoke_build_info_present' (Test-Path -LiteralPath $buildInfoPath -PathType Leaf) $buildInfoPath
        $buildInfo = Get-Content -LiteralPath $buildInfoPath -Raw | ConvertFrom-Json -ErrorAction Stop
        Require-Check 'smoke_build_info_identity' ([string](Get-JsonProperty $buildInfo 'artifact_type' '') -eq 'SightVault desktop build inputs') ('artifact_type=' + (Get-JsonProperty $buildInfo 'artifact_type' ''))
        Require-Check 'smoke_build_platform_windows_x64' ([string](Get-JsonProperty $buildInfo 'platform' '') -eq 'windows-x64') ('platform=' + (Get-JsonProperty $buildInfo 'platform' ''))
        Require-Check 'smoke_build_version_matches_installer' ([string](Get-JsonProperty $buildInfo 'app_version' '') -eq $script:installerVersion) ('app_version=' + (Get-JsonProperty $buildInfo 'app_version' '') + '; installer_version=' + $script:installerVersion)
        Require-Check 'smoke_build_has_no_model_weights' ((Get-JsonProperty $buildInfo 'model_weights_included' $true) -eq $false) 'model_weights_included=false'
        Require-Check 'smoke_build_has_source_revision' (-not [string]::IsNullOrWhiteSpace([string](Get-JsonProperty $buildInfo 'source_revision' ''))) ('source_revision=' + (Get-JsonProperty $buildInfo 'source_revision' ''))
        $startUrl = [string](Get-JsonProperty $smoke 'start_url' '')
        Require-Check 'smoke_first_run_opens_setup' ($startUrl -match '^http://127\.0\.0\.1:\d+/desktop/setup$') $startUrl

        $modelFiles = @(Get-ChildItem -LiteralPath (Join-Path $script:profileHome 'models') -Recurse -File -Force -ErrorAction SilentlyContinue)
        Require-Check 'smoke_no_weights_downloaded' ($modelFiles.Count -eq 0) ($modelFiles.Count.ToString() + ' files in the profile model folder')
    }

    Invoke-QaStep -Name 'serve_import_verify' -Action {
        $stopFile = Join-Path $script:profileHome 'qa.stop'
        $readyReport = Start-OwnedServer -StopFile $stopFile -ReportFile (Join-Path $script:artifactsDir 'serve-first.json')
        Require-Check 'serve_readiness_report' ((Get-JsonProperty $readyReport 'ready' $false) -eq $true) ('ready=' + (Get-JsonProperty $readyReport 'ready' $false))
        $record = Read-OwnedInstanceRecord -ExpectedPid $script:serveProcess.Id
        Add-Check 'instance_record_owned_pid' ($record.Pid -eq $script:serveProcess.Id) ('pid=' + $record.Pid)
        Add-Check 'instance_record_loopback_url' ($record.Url -match '^http://127\.0\.0\.1:\d+$') $record.Url
        $apiBase = $record.Url.TrimEnd('/')
        $token = $record.Token
        $serveReportUrl = [string](Get-JsonProperty $readyReport 'url' '')
        Add-Check 'serve_report_url_matches_instance' ($serveReportUrl.TrimEnd('/') -eq $apiBase) $serveReportUrl

        $statusResponse = Invoke-SightVaultRequest -Uri ($apiBase + '/api/status')
        Require-Check 'status_http_200' ($statusResponse.Status -eq 200) ('HTTP ' + $statusResponse.Status)
        $status = $statusResponse.Json
        Require-Check 'status_local_only' ((Get-JsonProperty $status 'local_only' $false) -eq $true) ('local_only=' + (Get-JsonProperty $status 'local_only' $false))
        Require-Check 'status_no_cloud_calls' ((Get-JsonProperty $status 'cloud_api_calls' -1) -eq 0) ('cloud_api_calls=' + (Get-JsonProperty $status 'cloud_api_calls' -1))
        Require-Check 'status_instance_identity' (Test-SamePath ([string](Get-JsonProperty $status 'instance_root' '')) $script:resourcesPath) ('instance_root=' + (Get-JsonProperty $status 'instance_root' ''))
        Require-Check 'library_empty_before_import' ((Get-JsonProperty $status 'total' -1) -eq 0) ('total=' + (Get-JsonProperty $status 'total' -1))

        $unauthenticated = Invoke-SightVaultRequest -Uri ($apiBase + '/desktop/api/demo/import') -Method 'POST'
        Require-Check 'demo_import_requires_token' ($unauthenticated.Status -eq 403) ('HTTP ' + $unauthenticated.Status)

        $tokenHeaders = @{}
        $tokenHeaders[$script:desktopTokenHeader] = $token
        $importResponse = Invoke-SightVaultRequest -Uri ($apiBase + '/desktop/api/demo/import') -Method 'POST' -Headers $tokenHeaders
        Require-Check 'demo_import_http_200' ($importResponse.Status -eq 200) ('HTTP ' + $importResponse.Status)
        Require-Check 'demo_import_62_assets' ([int](Get-JsonProperty $importResponse.Json 'imported' 0) -eq 62) ('imported=' + (Get-JsonProperty $importResponse.Json 'imported' 0))
        # The 62-asset fixture has 120 unique evidence files: 60 images and 60 thumbnails,
        # because two image pairs are byte-identical and share stored evidence.
        Require-Check 'demo_import_evidence_files' ([int](Get-JsonProperty $importResponse.Json 'evidence_files' 0) -eq 120) ('evidence_files=' + (Get-JsonProperty $importResponse.Json 'evidence_files' 0))
        Require-Check 'demo_import_preserved_indexed_timestamps' ((Get-JsonProperty $importResponse.Json 'indexed_timestamps_preserved' $false) -eq $true) 'indexed_timestamps_preserved=true'
        $importFolder = [string](Get-JsonProperty $importResponse.Json 'folder' '')
        Require-Check 'demo_import_folder_inside_profile' (Test-IsUnder $importFolder (Join-Path $script:profileHome 'data')) $importFolder

        $script:libraryFingerprint = Assert-ImportedLibrary -Prefix 'imported' -ApiBase $apiBase -DataDir (Join-Path $script:profileHome 'data')
        Add-Check 'imported_library_fingerprint' (-not [string]::IsNullOrWhiteSpace($script:libraryFingerprint)) $script:libraryFingerprint

        $exitCode = Stop-OwnedServerGracefully -Process $script:serveProcess -StopFile $stopFile -CheckPrefix 'first_stop'
        $script:exitCodes['serve_stop'] = $exitCode
        $script:dbHashAfterFirstRun = Get-LibraryDatabaseHash
        Require-Check 'database_hash_captured_after_graceful_stop' ($null -ne $script:dbHashAfterFirstRun) ('sha256=' + $script:dbHashAfterFirstRun)
    }

    Invoke-QaStep -Name 'upgrade_install' -Action {
        $exitCode = Invoke-Installer -LogPath (Join-Path $script:artifactsDir 'install-upgrade.log') -Label 'upgrade install'
        $script:exitCodes['upgrade_install'] = $exitCode
        Require-Check 'upgrade_installer_exit_zero' ($exitCode -eq 0) ('exit code ' + $exitCode)
        Require-Check 'upgrade_executable_present' (Test-Path -LiteralPath $script:exePath -PathType Leaf) $script:exePath
        $hashAfterUpgrade = Get-LibraryDatabaseHash
        Require-Check 'database_hash_unchanged_by_upgrade' ($null -ne $hashAfterUpgrade -and $hashAfterUpgrade -eq $script:dbHashAfterFirstRun) ('sha256=' + $hashAfterUpgrade)
    }

    Invoke-QaStep -Name 'relaunch_verify' -Action {
        $stopFile = Join-Path $script:profileHome 'qa.stop'
        if (Test-Path -LiteralPath $stopFile) {
            Remove-Item -LiteralPath $stopFile -Force
            Add-Check 'stale_stop_marker_removed_before_relaunch' $true $stopFile
        }
        $readyReport = Start-OwnedServer -StopFile $stopFile -ReportFile (Join-Path $script:artifactsDir 'serve-upgraded.json')
        Require-Check 'relaunch_readiness_report' ((Get-JsonProperty $readyReport 'ready' $false) -eq $true) ('ready=' + (Get-JsonProperty $readyReport 'ready' $false))
        $record = Read-OwnedInstanceRecord -ExpectedPid $script:serveProcess.Id
        Add-Check 'relaunch_instance_record_owned_pid' ($record.Pid -eq $script:serveProcess.Id) ('pid=' + $record.Pid)
        $apiBase = $record.Url.TrimEnd('/')

        $fingerprint = Assert-ImportedLibrary -Prefix 'relaunch' -ApiBase $apiBase -DataDir (Join-Path $script:profileHome 'data')
        Require-Check 'relaunch_library_unchanged' ($null -ne $script:libraryFingerprint -and $fingerprint -eq $script:libraryFingerprint) $fingerprint

        $exitCode = Stop-OwnedServerGracefully -Process $script:serveProcess -StopFile $stopFile -CheckPrefix 'relaunch_stop'
        $script:exitCodes['relaunch_stop'] = $exitCode
        $hashAfterRelaunch = Get-LibraryDatabaseHash
        Require-Check 'database_hash_unchanged_after_relaunch' ($null -ne $hashAfterRelaunch -and $hashAfterRelaunch -eq $script:dbHashAfterFirstRun) ('sha256=' + $hashAfterRelaunch)
    }

    Invoke-QaStep -Name 'uninstall' -Action {
        $uninstaller = Get-ChildItem -LiteralPath $script:installDir -Filter 'unins*.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 1
        Require-Check 'uninstaller_present' ($null -ne $uninstaller) $script:installDir
        $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="{0}"' -f (Join-Path $script:artifactsDir 'uninstall.log')
        $exitCode = Invoke-NativeProcess -FilePath $uninstaller.FullName -Arguments $arguments -TimeoutSeconds 300 -Label 'uninstall'
        $script:exitCodes['uninstall'] = $exitCode
        Require-Check 'uninstaller_exit_zero' ($exitCode -eq 0) ('exit code ' + $exitCode)

        $removed = Wait-ForCondition -TimeoutSeconds 180 -Condition { -not (Test-Path -LiteralPath $script:installDir) }
        Require-Check 'installed_app_removed' $removed $script:installDir
        $entryRemoved = Wait-ForCondition -TimeoutSeconds 60 -Condition { @(Get-RegistryUninstallEntry).Count -eq 0 }
        Require-Check 'uninstall_entry_removed' $entryRemoved 'The per-user uninstall entry is gone.'

        $hashAfterUninstall = Get-LibraryDatabaseHash
        Require-Check 'profile_database_retained' ($null -ne $hashAfterUninstall -and $hashAfterUninstall -eq $script:dbHashAfterFirstRun) ('sha256=' + $hashAfterUninstall)
        $sampleFolder = Join-Path $script:profileHome 'data\demo-samples'
        $sampleCount = @(Get-ChildItem -LiteralPath $sampleFolder -File -ErrorAction SilentlyContinue).Count
        Require-Check 'profile_demo_library_retained' ($sampleCount -eq 62) ($sampleCount.ToString() + ' sample files in ' + $sampleFolder)
        $imageFolder = Join-Path $script:profileHome 'data\images'
        $imageCount = @(Get-ChildItem -LiteralPath $imageFolder -File -ErrorAction SilentlyContinue).Count
        # 60 unique stored images for the 62-asset fixture (two byte-identical pairs share evidence).
        Require-Check 'profile_evidence_images_retained' ($imageCount -eq 60) ($imageCount.ToString() + ' stored images in ' + $imageFolder)
        $thumbFolder = Join-Path $script:profileHome 'data\thumbs'
        $thumbCount = @(Get-ChildItem -LiteralPath $thumbFolder -File -ErrorAction SilentlyContinue).Count
        Require-Check 'profile_evidence_thumbnails_retained' ($thumbCount -eq 60) ($thumbCount.ToString() + ' stored thumbnails in ' + $thumbFolder)
    }

    $script:runOk = $true
}
catch {
    $script:runOk = $false
    $script:errorText = $_.Exception.Message
    Write-HarnessLog ('FAILED: ' + $script:errorText)
}
finally {
    # Stop only the exact server process this harness started. The stop marker is tried
    # first; a force stop is recorded as a failure but never touches another process.
    $process = $script:serveProcess
    if ($null -ne $process) {
        $alive = $false
        try { $alive = -not $process.HasExited } catch { $alive = $false }
        if ($alive) {
            if ($script:activeStopFile) {
                try { New-Item -ItemType File -Path $script:activeStopFile -Force | Out-Null } catch { }
                if ($process.WaitForExit(30000)) {
                    Add-Check 'cleanup_owned_server_stopped' $true 'Stopped through the owned stop marker.'
                    $alive = $false
                }
            }
        }
        if ($alive) {
            if (Stop-OwnedProcess -Process $process -ExpectedPath $script:exePath) {
                Add-Check 'cleanup_owned_server_force_stopped' $false 'The owned server did not stop through its marker; only the exact started process was stopped.'
            }
            else {
                Add-Check 'cleanup_owned_server_identity' $false 'The started process could not be identified safely; no process was stopped.'
            }
        }
        $script:serveProcess = $null
    }

    $environmentRestored = $false
    try {
        Restore-HarnessEnvironment
        $environmentRestored = $true
    }
    catch {
        $environmentRestored = $false
    }
    Add-Check 'environment_restored' $environmentRestored 'SIGHTVAULT_HOME, SIGHTVAULT_DATA and SIGHTVAULT_MODELS were restored.'

    $keepEvidence = (-not $script:runOk) -or ($script:failures.Count -gt 0)
    if ($null -ne $script:ownedRoot -and (Test-Path -LiteralPath $script:ownedRoot)) {
        if ($keepEvidence) {
            $script:retainedEvidence = $true
            Add-Check 'owned_temp_retained_for_evidence' $true ('Kept ' + $script:ownedRoot + ' for inspection.')
        }
        elseif (Test-IsUnder $script:reportPath $script:ownedRoot) {
            $script:retainedEvidence = $true
            Add-Check 'owned_temp_retained_for_evidence' $true 'The report path is inside the owned folder, so it was kept.'
        }
        else {
            $rootFull = [System.IO.Path]::GetFullPath($script:ownedRoot).TrimEnd([char[]]@('\', '/'))
            $runnerTemp = [System.IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd([char[]]@('\', '/'))
            $leaf = Split-Path -Leaf $rootFull
            $safeToDelete = $rootFull.StartsWith($runnerTemp + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -and
                ($leaf -match '^sightvault-desktop-qa-[0-9a-f]{32}$')
            if (-not $safeToDelete) {
                Add-Check 'owned_temp_removed' $false 'The owned folder did not match the expected RUNNER_TEMP layout; nothing was deleted.'
            }
            else {
                Remove-Item -LiteralPath $rootFull -Recurse -Force -ErrorAction SilentlyContinue
                Add-Check 'owned_temp_removed' (-not (Test-Path -LiteralPath $rootFull)) $rootFull
            }
        }
    }
}

if ($script:runOk -and $script:failures.Count -eq 0) {
    $script:status = 'passed'
}
else {
    $script:status = 'failed'
}
Write-QaReport
if ($script:status -eq 'passed') { exit 0 }
exit 1
