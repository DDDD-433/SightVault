[CmdletBinding()]
param(
    [int]$StartupTimeoutSec = 90,
    [int]$InferenceTimeoutSec = 600,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'qa\windows-validation')
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if ($env:OS -ne 'Windows_NT') { throw 'This runtime validator must run on native Windows.' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Run Setup-Windows.ps1 first.' }
if (-not [System.IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory = Join-Path $Root $OutputDirectory }
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$JsonPath = Join-Path $OutputDirectory 'windows-validation.json'
$LogPath = Join-Path $OutputDirectory 'windows-validation.log'
$Log = New-Object System.Collections.Generic.List[string]
$Checks = New-Object System.Collections.Generic.List[object]
$RunStarted = [DateTime]::UtcNow.ToString('o')
$Failure = $null
$Server = $null
$TempRoot = $null
$DemoBenchmark = [ordered]@{status='not-run'}
$SavedEnvironment = @{}
$EnvironmentNames = @('PIP_DISABLE_PIP_VERSION_CHECK','HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY','SIGHTVAULT_DATA','SIGHTVAULT_MODELS','SIGHTVAULT_PORT','SIGHTVAULT_DEVICE','SIGHTVAULT_VISION','PYTHONPATH','SIGHTVAULT_GUARD_LOG','SIGHTVAULT_GUARD_READY')
foreach ($name in $EnvironmentNames) { $SavedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }

function Write-RunLog([string]$Text) {
    $line = "[$([DateTime]::UtcNow.ToString('o'))] $Text"
    $Log.Add($line) | Out-Null
    Write-Host $Text
}

function Add-Check([string]$Name, [bool]$Passed, $Details) {
    $status = if ($Passed) { 'passed' } else { 'failed' }
    $Checks.Add([ordered]@{name=$Name; status=$status; passed=$Passed; details=$Details}) | Out-Null
    Write-RunLog ("CHECK {0}: {1}" -f $Name, $(if ($Passed) {'PASS'} else {'FAIL'}))
}

function Add-SkippedCheck([string]$Name, $Details) {
    $Checks.Add([ordered]@{name=$Name; status='skipped-not-validated'; passed=$null; details=$Details}) | Out-Null
    Write-RunLog (("CHECK {0}: SKIP (not validated)" -f $Name))
}

function Invoke-Python([string]$Name, [string[]]$Arguments) {
    Write-RunLog ("COMMAND: {0} {1}" -f $Python, ($Arguments -join ' '))
    $savedNativeErrorPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 turns redirected native stderr into ErrorRecords.
        # Keep the capture non-terminating so benign Python warnings are retained
        # and the executable's actual exit code decides success.
        $ErrorActionPreference = 'Continue'
        $lines = @(& $Python @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedNativeErrorPreference
    }
    $text = ($lines | ForEach-Object { $_.ToString() }) -join "`n"
    foreach ($line in $lines) { Write-RunLog $line.ToString() }
    if ($exitCode -ne 0) { throw "$Name failed with exit code $exitCode." }
    return $text
}

function Require([bool]$Condition, [string]$Name, $Details) {
    Add-Check $Name $Condition $Details
    if (-not $Condition) { throw "$Name failed." }
}

function Get-FreeLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return $listener.LocalEndpoint.Port }
    finally { $listener.Stop() }
}

function Invoke-LocalJson([string]$Uri, [string]$Method = 'Get', $Body = $null, [int]$TimeoutSec = 30) {
    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $Uri -Method $Method -TimeoutSec $TimeoutSec
    }
    $json = ConvertTo-Json -InputObject $Body -Depth 8 -Compress
    return Invoke-RestMethod -Uri $Uri -Method $Method -ContentType 'application/json' -Body $json -TimeoutSec $TimeoutSec
}

function Restore-Environment {
    foreach ($name in $SavedEnvironment.Keys) {
        if ($null -eq $SavedEnvironment[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item "Env:$name" $SavedEnvironment[$name] }
    }
}

try {
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    $env:HF_HUB_DISABLE_TELEMETRY = '1'
    $preflightText = Invoke-Python 'Native platform and dependency preflight' @('scripts\windows_setup_preflight.py')
    $preflight = $preflightText | ConvertFrom-Json
    Require ([bool]$preflight.passed) 'native_python_dependencies_sqlite' $preflight

    $modelText = Invoke-Python 'Required model checksum verification' @('handoff\verify_transfer.py', '--models')
    $modelReport = $modelText | ConvertFrom-Json
    Require ([bool]$modelReport.passed) 'required_model_checksums' $modelReport

    $pipCheck = Invoke-Python 'Dependency consistency' @('-m', 'pip', 'check')
    Add-Check 'pip_check' $true $pipCheck

    $savedNativeErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $tests = @(& $Python -m pytest -q tests 2>&1)
        $testsExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedNativeErrorPreference
    }
    foreach ($line in $tests) { Write-RunLog $line.ToString() }
    Add-Check 'pytest_tests' ($testsExit -eq 0) @{exit_code=$testsExit; output=(($tests | ForEach-Object {$_.ToString()}) -join "`n")}
    if ($testsExit -ne 0) { throw "Python tests failed with exit code $testsExit." }

    $sample = Join-Path $Root 'samples\terminal_cuda_01.png'
    if (-not (Test-Path -LiteralPath $sample -PathType Leaf)) { throw 'The public terminal smoke image is missing.' }
    $TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('SightVault-Windows-Validation-' + [Guid]::NewGuid().ToString('N'))
    $ImageRoot = Join-Path $TempRoot 'images'
    $DataRoot = Join-Path $TempRoot 'data'
    $GuardRoot = Join-Path $TempRoot 'offline-guard'
    New-Item -ItemType Directory -Force -Path $ImageRoot, $DataRoot, $GuardRoot | Out-Null
    $ImagePath = Join-Path $ImageRoot 'terminal_cuda_01.png'
    Copy-Item -LiteralPath $sample -Destination $ImagePath
    $GuardLog = Join-Path $TempRoot 'blocked-network-attempts.log'
    $GuardReady = Join-Path $TempRoot 'offline-guard-ready.txt'
    $GuardFile = Join-Path $GuardRoot 'sitecustomize.py'
    $GuardSource = @'
import errno, os, socket
_allowed = {'127.0.0.1', '::1', 'localhost'}
with open(os.environ['SIGHTVAULT_GUARD_READY'], 'w', encoding='utf-8') as ready:
    ready.write('active\n')
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo
def _host(value):
    if isinstance(value, tuple) and value:
        value = value[0]
    if isinstance(value, bytes):
        value = value.decode('ascii', 'ignore')
    return str(value).lower().rstrip('.')
def _block(value):
    host = _host(value)
    if host not in _allowed:
        try:
            with open(os.environ.get('SIGHTVAULT_GUARD_LOG', os.devnull), 'a', encoding='utf-8') as output:
                output.write(host + '\n')
        finally:
            raise OSError(errno.EPERM, 'SightVault offline validation blocked outbound access', host)
def _guard_connect(self, address):
    _block(address)
    return _connect(self, address)
def _guard_connect_ex(self, address):
    try:
        _block(address)
    except OSError as exc:
        return exc.errno or errno.EPERM
    return _connect_ex(self, address)
def _guard_getaddrinfo(host, *args, **kwargs):
    _block(host)
    return _getaddrinfo(host, *args, **kwargs)
socket.socket.connect = _guard_connect
socket.socket.connect_ex = _guard_connect_ex
socket.getaddrinfo = _guard_getaddrinfo
'@
    [System.IO.File]::WriteAllText($GuardFile, $GuardSource, [System.Text.UTF8Encoding]::new($false))

    $port = Get-FreeLoopbackPort
    $baseUrl = "http://127.0.0.1:$port"
    $appStdout = Join-Path $TempRoot 'server.stdout.log'
    $appStderr = Join-Path $TempRoot 'server.stderr.log'
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    $env:HF_HUB_DISABLE_TELEMETRY = '1'
    $env:SIGHTVAULT_DATA = $DataRoot
    $env:SIGHTVAULT_MODELS = Join-Path $Root '.models'
    $env:SIGHTVAULT_PORT = [string]$port
    $env:SIGHTVAULT_DEVICE = 'auto'
    $env:SIGHTVAULT_VISION = 'minicpm'
    $env:PYTHONPATH = $GuardRoot
    $env:SIGHTVAULT_GUARD_LOG = $GuardLog
    $env:SIGHTVAULT_GUARD_READY = $GuardReady
    $Server = Start-Process -FilePath $Python -ArgumentList ('"{0}" --port {1}' -f (Join-Path $Root 'app.py'), $port) -WorkingDirectory $Root -PassThru -WindowStyle Hidden -RedirectStandardOutput $appStdout -RedirectStandardError $appStderr
    Restore-Environment

    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSec)
    $status = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($Server.HasExited) { throw "SightVault exited during startup (code $($Server.ExitCode))." }
        try { $status = Invoke-LocalJson "$baseUrl/api/status" 'Get' $null 5; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    Require ($null -ne $status) 'loopback_startup' @{url=$baseUrl; process_id=$Server.Id}
    Require ($status.local_only -eq $true -and $status.cloud_api_calls -eq 0) 'local_only_status' @{local_only=$status.local_only; cloud_api_calls=$status.cloud_api_calls}
    Require ($status.instance_root -eq $Root -and $status.vision_backend -eq 'minicpm' -and $status.models.vision.model_id -eq 'openbmb/MiniCPM-V-4.7-1B') 'service_identity' @{instance_root=$status.instance_root; vision_backend=$status.vision_backend; vision_model_id=$status.models.vision.model_id}
    Require (Test-Path -LiteralPath $GuardReady -PathType Leaf) 'offline_socket_guard_loaded' @{guard_file=$GuardReady}

    $index = Invoke-LocalJson "$baseUrl/api/index" 'Post' @{folder=$ImageRoot; ocr='minicpm'; force=$true} 30
    $jobDeadline = [DateTime]::UtcNow.AddSeconds($InferenceTimeoutSec)
    $job = $null
    while ([DateTime]::UtcNow -lt $jobDeadline) {
        $job = Invoke-LocalJson "$baseUrl/api/jobs/$($index.job_id)" 'Get' $null 30
        if ($job.state -eq 'complete' -or $job.state -eq 'error' -or $job.state -eq 'cancelled') { break }
        Start-Sleep -Seconds 1
    }
    Require ($job.state -eq 'complete' -and $job.failed -eq 0 -and $job.indexed -eq 1) 'local_index_and_ocr_job' @{state=$job.state; indexed=$job.indexed; failed=$job.failed; warnings=$job.warnings; requested_ocr_backend='minicpm'}
    $status = Invoke-LocalJson "$baseUrl/api/status" 'Get' $null 10
    Require ($status.total -eq 1 -and $status.with_embeddings -eq 1 -and $status.with_ocr -eq 1) 'indexed_embedding_and_ocr_status' @{total=$status.total; with_embeddings=$status.with_embeddings; with_ocr=$status.with_ocr}
    $asset = Invoke-LocalJson "$baseUrl/api/assets/1" 'Get' $null 10
    Require ($asset.filename -eq 'terminal_cuda_01.png' -and $asset.ocr_backend -eq 'MiniCPM-OCR 1.0' -and [string]::IsNullOrWhiteSpace($asset.ocr_error) -and -not [string]::IsNullOrWhiteSpace($asset.ocr_text)) 'preferred_minicpm_ocr_readback' @{filename=$asset.filename; ocr_backend=$asset.ocr_backend; ocr_error=$asset.ocr_error; ocr_characters=$asset.ocr_text.Length}

    $searchUrl = "$baseUrl/api/search?q=CUDA%20out%20of%20memory&mode=semantic"
    $search = Invoke-LocalJson $searchUrl 'Get' $null $InferenceTimeoutSec
    $top = if ($search.results.Count -gt 0) { $search.results[0].filename } else { $null }
    Require ($top -eq 'terminal_cuda_01.png') 'single_item_semantic_search_execution' @{top_result=$top; result_count=$search.results.Count; latency_ms=$search.latency_ms; retrieval_quality_claim='execution smoke only; single-item corpus'}

    $analysis = Invoke-LocalJson "$baseUrl/api/analyze" 'Post' @{asset_id=1; question='What visible error message appears in this screenshot?'} $InferenceTimeoutSec
    Require (-not [string]::IsNullOrWhiteSpace($analysis.answer)) 'local_image_analysis' @{model=$analysis.model; backend=$analysis.backend; runtime=$analysis.runtime; device=$analysis.device; answer_characters=$analysis.answer.Length; latency_ms=$analysis.latency_ms}
    $statusAfterAnalysis = Invoke-LocalJson "$baseUrl/api/status" 'Get' $null 10
    $selectedVision = $statusAfterAnalysis.models.vision
    $analysisIdentityMatches = ($analysis.backend -eq 'minicpm' -and $analysis.model -eq 'MiniCPM-V 4.7 1B' -and
        -not [string]::IsNullOrWhiteSpace($analysis.runtime) -and -not [string]::IsNullOrWhiteSpace($analysis.device) -and
        $analysis.runtime -eq $selectedVision.runtime -and $analysis.device -eq $selectedVision.device -and
        $selectedVision.loaded -eq $true -and $selectedVision.model_id -eq 'openbmb/MiniCPM-V-4.7-1B')
    Require $analysisIdentityMatches 'selected_vision_backend_model_runtime_device' @{answer_backend=$analysis.backend; answer_model=$analysis.model; answer_runtime=$analysis.runtime; answer_device=$analysis.device; status_model_id=$selectedVision.model_id; status_loaded=$selectedVision.loaded}

    $blocked = if (Test-Path -LiteralPath $GuardLog) { @(Get-Content -LiteralPath $GuardLog | Where-Object { $_ -ne '' }) } else { @() }
    Require ($blocked.Count -eq 0) 'offline_process_socket_guard' @{blocked_nonloopback_attempts=$blocked.Count; isolation='Python process socket and DNS guard; not Windows Firewall'}
    Add-Check 'server_process_started' $true @{process_id=$Server.Id; port=$port}

    # Stop this validator-owned one-image service before the independent full
    # retrieval benchmark; both processes load EmbeddingGemma and must not overlap.
    if ($Server -and -not $Server.HasExited) {
        Stop-Process -Id $Server.Id -Force
        try { $Server.WaitForExit(10000) | Out-Null } catch {}
    }
    if ($Server -and -not $Server.HasExited) { throw 'The validator-owned local service did not exit before the separate retrieval benchmark.' }
    Add-Check 'validator_server_stopped_before_retrieval_benchmark' $true @{process_id=$Server.Id; exited=$true}
    $Server = $null

    $BenchmarkDataRoot = Join-Path $TempRoot 'retrieval-demo-data'
    $prepareText = Invoke-Python 'Verify and prepare a disposable public demo copy' @('scripts\prepare_disposable_demo_benchmark.py', '--source-root', $Root, '--output-data', $BenchmarkDataRoot)
    $preparedDemo = $prepareText | ConvertFrom-Json
    if ($preparedDemo.status -eq 'prepared') {
        $DemoBenchmark = [ordered]@{status='running'; report='public-demo-retrieval.json'}
        $env:HF_HUB_OFFLINE = '1'
        $env:TRANSFORMERS_OFFLINE = '1'
        $env:HF_HUB_DISABLE_TELEMETRY = '1'
        $env:SIGHTVAULT_DATA = $BenchmarkDataRoot
        $env:SIGHTVAULT_MODELS = Join-Path $Root '.models'
        $env:SIGHTVAULT_DEVICE = 'auto'
        $env:SIGHTVAULT_VISION = 'minicpm'
        $env:PYTHONPATH = $GuardRoot
        $env:SIGHTVAULT_GUARD_LOG = $GuardLog
        $env:SIGHTVAULT_GUARD_READY = $GuardReady
        $DemoReportPath = Join-Path $OutputDirectory 'public-demo-retrieval.json'
        $BenchmarkScript = Join-Path $Root 'scripts\benchmark.py'
        $BenchmarkStdout = Join-Path $OutputDirectory 'public-demo-retrieval.stdout.log'
        $BenchmarkStderr = Join-Path $OutputDirectory 'public-demo-retrieval.stderr.log'
        $BenchmarkCommand = ('"{0}" "{1}" --offline-check --output "{2}"' -f $Python, $BenchmarkScript, $DemoReportPath)
        Write-RunLog ("COMMAND: {0}" -f $BenchmarkCommand)
        $benchmarkProcess = Start-Process -FilePath $Python -ArgumentList ('"{0}" --offline-check --output "{1}"' -f $BenchmarkScript, $DemoReportPath) -WorkingDirectory $Root -PassThru -WindowStyle Hidden -RedirectStandardOutput $BenchmarkStdout -RedirectStandardError $BenchmarkStderr
        if (-not $benchmarkProcess.WaitForExit($InferenceTimeoutSec * 1000)) {
            Stop-Process -Id $benchmarkProcess.Id -Force -ErrorAction SilentlyContinue
            try { $benchmarkProcess.WaitForExit(10000) | Out-Null } catch {}
            throw "Public demo retrieval benchmark exceeded the ${InferenceTimeoutSec}-second timeout; see the retained stdout/stderr logs."
        }
        $benchmarkProcess.Refresh()
        foreach ($line in @(Get-Content -LiteralPath $BenchmarkStdout -ErrorAction SilentlyContinue)) { Write-RunLog ("BENCHMARK: " + $line) }
        foreach ($line in @(Get-Content -LiteralPath $BenchmarkStderr -ErrorAction SilentlyContinue)) { Write-RunLog ("BENCHMARK STDERR: " + $line) }
        if ($benchmarkProcess.ExitCode -ne 0) { throw "Public demo retrieval benchmark failed with exit code $($benchmarkProcess.ExitCode); see the retained logs." }
        if (-not (Test-Path -LiteralPath $DemoReportPath -PathType Leaf)) { throw 'Public demo retrieval benchmark did not write its JSON report.' }
        $benchmark = Get-Content -LiteralPath $DemoReportPath -Raw | ConvertFrom-Json
        $proof = @($benchmark.image_embedding_proof)
        $belowProofThreshold = @($proof | Where-Object { [double]$_.cosine_fresh_vs_stored -lt 0.99 })
        $proofFiles = @($proof | ForEach-Object { $_.file } | Sort-Object)
        $expectedProofFiles = @('kitchen_photo.jpg', 'object_study_01.png', 'terminal_cuda_01.png')
        $proofValid = ($proof.Count -eq 3) -and ($belowProofThreshold.Count -eq 0) -and (@(Compare-Object $proofFiles $expectedProofFiles).Count -eq 0)
        $negativeCount = @($benchmark.negative_queries).Count
        $retrievalValid = ($benchmark.total -eq 17 -and $benchmark.passed -eq 17 -and $benchmark.database.total -eq 62 -and $benchmark.database.with_embeddings -eq 62 -and $negativeCount -eq 4 -and
            $benchmark.negatives_below_all_positives -eq $true -and $proofValid -and $benchmark.offline_network_blocked -eq $true)
        Require $retrievalValid 'public_demo_offline_retrieval_17_of_17' @{passed=$benchmark.passed; total=$benchmark.total; database_total=$benchmark.database.total; database_with_embeddings=$benchmark.database.with_embeddings; negative_query_count=$negativeCount; negatives_below_all_positives=$benchmark.negatives_below_all_positives; image_embedding_proof=$proof; minimum_required_cosine=0.99; report='public-demo-retrieval.json'; prepared_demo=$preparedDemo}
        $postBenchmarkBlocked = if (Test-Path -LiteralPath $GuardLog) { @(Get-Content -LiteralPath $GuardLog | Where-Object { $_ -ne '' }) } else { @() }
        Require ($postBenchmarkBlocked.Count -eq 0) 'offline_public_demo_benchmark_socket_guard' @{blocked_nonloopback_attempts=$postBenchmarkBlocked.Count; isolation='HF offline flags and Python process socket/DNS guard; not Windows Firewall'}
        $DemoBenchmark = [ordered]@{status='passed'; report='public-demo-retrieval.json'; command=$BenchmarkCommand; output_data_root='temporary and removed'; copied_asset_count=$preparedDemo.asset_count; database_sha256_matches_package_manifest=$preparedDemo.database_sha256_matches_package_manifest; source_path_rebased=$preparedDemo.source_path_rebased; retrieval_cases_passed=$benchmark.passed; retrieval_cases_total=$benchmark.total; image_embedding_proof=$proof; negatives_below_all_positives=$benchmark.negatives_below_all_positives; offline_isolation='HF offline flags plus Python process socket/DNS guard; not Windows Firewall'; process_stdout='public-demo-retrieval.stdout.log'; process_stderr='public-demo-retrieval.stderr.log'}
    }
    elseif ($preparedDemo.status -eq 'skipped-not-validated') {
        $DemoBenchmark = [ordered]@{status='skipped-not-validated'; reason=$preparedDemo.reason; manual_instructions=$preparedDemo.manual_instructions}
        Add-SkippedCheck 'public_demo_offline_retrieval' $DemoBenchmark
    }
    else { throw "Unexpected disposable demo preparation status: $($preparedDemo.status)" }
}
catch {
    $Failure = $_.Exception.Message
    if ($DemoBenchmark.status -eq 'running') { $DemoBenchmark['status'] = 'failed'; $DemoBenchmark['reason'] = $Failure }
    Add-Check 'run_completion' $false @{error=$Failure}
    Write-RunLog ("ERROR: " + $Failure)
    if ($Server -and -not $Server.HasExited) {
        foreach ($file in @((Join-Path $TempRoot 'server.stdout.log'),(Join-Path $TempRoot 'server.stderr.log'))) {
            if (Test-Path -LiteralPath $file) {
                $tail = @(Get-Content -LiteralPath $file -Tail 30)
                foreach ($line in $tail) { Write-RunLog ("SERVER: " + $line) }
            }
        }
    }
}
finally {
    Restore-Environment
    if ($Server -and -not $Server.HasExited) {
        Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue
        try { $Server.WaitForExit(10000) | Out-Null } catch {}
    }
    $passed = ($null -eq $Failure) -and (@($Checks | Where-Object { $_.status -eq 'failed' }).Count -eq 0)
    $statusText = if ($passed) { 'passed' } else { 'failed' }
    $report = [ordered]@{
        schema_version = 1
        artifact_type = 'native Windows x64 runtime validation'
        started_at_utc = $RunStarted
        finished_at_utc = [DateTime]::UtcNow.ToString('o')
        source_root = $Root
        status = $statusText
        failure = $Failure
        public_demo_retrieval = $DemoBenchmark
        checks = @($Checks)
        isolation = 'HF offline flags plus a Python process socket/DNS guard; not a Windows Firewall test'
    }
    $json = ConvertTo-Json -InputObject $report -Depth 14
    [System.IO.File]::WriteAllText($JsonPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllLines($LogPath, $Log.ToArray(), [System.Text.UTF8Encoding]::new($false))
    if ($TempRoot -and (Test-Path -LiteralPath $TempRoot)) { Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host "JSON report: $JsonPath"
Write-Host "Text log: $LogPath"
if (-not $passed) { exit 1 }
exit 0
