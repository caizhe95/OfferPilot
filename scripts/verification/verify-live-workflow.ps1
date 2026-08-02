param(
  [int]$Port = 8020,
  [switch]$KeepArtifacts,
  [switch]$CleanupArtifacts
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$apiDirectory = Join-Path $projectRoot "src\api"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$backupScript = Join-Path $projectRoot "scripts\backup-database.ps1"
$dataRoot = Join-Path $projectRoot "data"
$verificationDirectory = Join-Path $dataRoot ("verification-live-" + [guid]::NewGuid().ToString("N"))
$databasePath = Join-Path $verificationDirectory "offerpilot.db"
$backupPath = Join-Path $verificationDirectory "offerpilot-backup.db"
$stdoutPath = Join-Path $verificationDirectory "api.stdout.log"
$stderrPath = Join-Path $verificationDirectory "api.stderr.log"
$environmentNames = @(
  "OFFERPILOT_SQLITE_PATH",
  "OFFERPILOT_DEBUG",
  "OFFERPILOT_CORS_ORIGINS",
  "OFFERPILOT_ALLOW_LEGACY_PROFILE_BOOTSTRAP"
)
$originalEnvironment = @{}
foreach ($name in $environmentNames) {
  $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}

if ($CleanupArtifacts) {
  if (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) {
    throw "API data directory was not found"
  }
  $resolvedDataRoot = (Resolve-Path -LiteralPath $dataRoot).Path
  $prefix = $resolvedDataRoot + [System.IO.Path]::DirectorySeparatorChar
  $artifacts = Get-ChildItem -LiteralPath $resolvedDataRoot -Directory -Filter "verification-live-*"
  foreach ($artifact in $artifacts) {
    if ($artifact.Name -notmatch "^verification-live-[0-9a-f]{32}$") {
      throw "Refusing to remove an unexpected verification directory"
    }
    $resolvedArtifact = (Resolve-Path -LiteralPath $artifact.FullName).Path
    if (!$resolvedArtifact.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove a verification directory outside the API data directory"
    }
    Remove-Item -LiteralPath $resolvedArtifact -Recurse -Force
  }
  Write-Output "Removed isolated live-workflow verification artifacts."
  exit 0
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
  throw "Python executable not found"
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
  throw "Port $Port is already in use; do not disrupt an existing service"
}

function Start-VerificationApi {
  $server = Start-Process -FilePath $pythonPath -WorkingDirectory $apiDirectory -ArgumentList @(
    "-m", "uvicorn", "offerpilot.main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "warning"
  ) -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru

  for ($attempt = 0; $attempt -lt 120; $attempt++) {
    try {
      $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
      if ($health.StatusCode -eq 200) { return $server }
    } catch {}
    Start-Sleep -Milliseconds 250
  }

  if (!$server.HasExited) { Stop-Process -Id $server.Id -Force }
  throw "Temporary API did not become healthy"
}

function Stop-VerificationApi([System.Diagnostics.Process]$Server) {
  if ($Server -and !$Server.HasExited) {
    Stop-Process -Id $Server.Id -Force
    $Server.WaitForExit()
  }
}

function Read-JsonResponse([System.Net.Http.HttpResponseMessage]$Response) {
  $content = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
  if (!$Response.IsSuccessStatusCode) {
    throw "HTTP $([int]$Response.StatusCode)"
  }
  return $content | ConvertFrom-Json
}

function Read-SseResponse([System.Net.Http.HttpResponseMessage]$Response) {
  $content = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
  if (!$Response.IsSuccessStatusCode) {
    throw "SSE HTTP $([int]$Response.StatusCode)"
  }
  $events = @()
  foreach ($line in $content -split "`r?`n") {
    if ($line.StartsWith("data: ")) {
      $events += ($line.Substring(6) | ConvertFrom-Json)
    }
  }
  if ($events.Count -eq 0) {
    throw "SSE response contained no application events"
  }
  return $events
}

function New-JsonContent([object]$Payload) {
  return [System.Net.Http.StringContent]::new(
    ($Payload | ConvertTo-Json -Depth 8 -Compress),
    [System.Text.Encoding]::UTF8,
    "application/json"
  )
}

function Require-CompletedRun([object[]]$Events, [string]$ExpectedMode) {
  $started = @($Events | Where-Object { $_.type -eq "session_start" -and $_.mode -eq $ExpectedMode })
  $completed = @($Events | Where-Object { $_.type -eq "run_complete" -and $_.status -eq "completed" })
  if ($started.Count -ne 1 -or $completed.Count -ne 1) {
    $eventSummary = @($Events | ForEach-Object {
      $errorCategory = ([string]$_.error -split ":", 2)[0]
      $errorCode = [string]$_.code
      "{0}:{1}:{2}:{3}" -f $_.type, $_.status, $errorCategory, $errorCode
    }) -join ","
    throw "$ExpectedMode SSE run did not complete exactly once; events=$eventSummary"
  }
  return [string]$started[0].trace_id
}

$server = $null
$client = $null
try {
  New-Item -ItemType Directory -Path $verificationDirectory | Out-Null
  $env:OFFERPILOT_SQLITE_PATH = $databasePath
  $env:OFFERPILOT_DEBUG = "true"
  $env:OFFERPILOT_CORS_ORIGINS = "http://localhost:3000"
  $env:OFFERPILOT_ALLOW_LEGACY_PROFILE_BOOTSTRAP = "false"

  Push-Location $apiDirectory
  & $pythonPath -m offerpilot.llm.provider_probe | Out-Null
  $probeExitCode = $LASTEXITCODE
  Pop-Location
  if ($probeExitCode -ne 0) { throw "Live Provider Probe failed" }

  $server = Start-VerificationApi
  $handler = [System.Net.Http.HttpClientHandler]::new()
  $handler.UseCookies = $true
  $client = [System.Net.Http.HttpClient]::new($handler)
  $client.Timeout = [TimeSpan]::FromSeconds(90)
  $client.DefaultRequestHeaders.Add("Origin", "http://localhost:3000")
  $baseUrl = "http://127.0.0.1:$Port"

  $bootstrapContent = New-JsonContent @{}
  $bootstrapResponse = $client.PostAsync("${baseUrl}/api/profile/bootstrap", $bootstrapContent).GetAwaiter().GetResult()
  $bootstrap = Read-JsonResponse $bootstrapResponse
  if (!$bootstrap.profile_id) { throw "Profile bootstrap did not issue a profile" }
  $sessionContent = New-JsonContent @{}
  $sessionResponse = $client.PostAsync("${baseUrl}/api/sessions", $sessionContent).GetAwaiter().GetResult()
  $session = Read-JsonResponse $sessionResponse

  $coachRequest = @{
    mode = "coach"
    session_id = [string]$session.id
    message = "Search the internal knowledge base for a ReAct tool-calling interview practice question."
  }
  $coachContent = New-JsonContent $coachRequest
  $coachResponse = $client.PostAsync("${baseUrl}/api/coach", $coachContent).GetAwaiter().GetResult()
  $coachEvents = Read-SseResponse $coachResponse
  $coachTraceId = Require-CompletedRun $coachEvents "coach"
  if (@($coachEvents | Where-Object { $_.type -eq "tool_call" -and $_.tool_name -eq "search_knowledge" }).Count -lt 1) {
    throw "Live Coach did not invoke search_knowledge"
  }
  if (@($coachEvents | Where-Object { $_.type -eq "tool_result" -and $_.tool_name -eq "search_knowledge" }).Count -lt 1) {
    throw "Live Coach did not receive search_knowledge results"
  }

  $diagnosisRequest = @{
    mode = "diagnosis"
    session_id = [string]$session.id
    diagnosis = @{
      question = "Explain how ReAct combines reasoning with tool calls and handles production failures."
      answer = "LIVE_WORKFLOW_ANSWER: ReAct combines observation, reasoning, and action. Production code validates tool parameters, returns results to context, enforces timeouts and retry boundaries, uses idempotency keys, records traces, and gives the user a recoverable next step after failures."
    }
  }
  $diagnosisContent = New-JsonContent $diagnosisRequest
  $diagnosisResponse = $client.PostAsync("${baseUrl}/api/coach", $diagnosisContent).GetAwaiter().GetResult()
  $diagnosisEvents = Read-SseResponse $diagnosisResponse
  $diagnosisTraceId = Require-CompletedRun $diagnosisEvents "diagnosis"
  $reportReady = @($diagnosisEvents | Where-Object { $_.type -eq "report_ready" }) | Select-Object -First 1
  if (!$reportReady -or !$reportReady.report_id -or @($diagnosisEvents | Where-Object { $_.type -eq "diagnosis_started" }).Count -ne 1) {
    throw "Live Diagnosis did not persist a report"
  }
  $diagnosisTraceResponse = $client.GetAsync("${baseUrl}/api/traces/$diagnosisTraceId").GetAwaiter().GetResult()
  $diagnosisTrace = Read-JsonResponse $diagnosisTraceResponse
  $traceEventTypes = @($diagnosisTrace.events | ForEach-Object { $_.event_type })
  if ("knowledge_retrieved" -notin $traceEventTypes -or "rrf_fused" -notin $traceEventTypes) {
    throw "Live Diagnosis trace did not record Embedding/RRF retrieval"
  }

  $exportPayload = @{ session_id = [string]$session.id; report_id = [string]$reportReady.report_id }
  $exportContent = New-JsonContent $exportPayload
  $exportResponse = $client.PostAsync("${baseUrl}/api/coach/reports/export", $exportContent).GetAwaiter().GetResult()
  $exportRequested = Read-JsonResponse $exportResponse
  if ($exportRequested.type -ne "permission_required" -or !$exportRequested.request_id) {
    throw "Report export did not create an approval"
  }
  $exportApprovalPayload = @{ session_id = [string]$session.id; request_id = [string]$exportRequested.request_id }
  $exportApprovalContent = New-JsonContent $exportApprovalPayload
  $exportApprovalResponse = $client.PostAsync("${baseUrl}/api/permission/approve", $exportApprovalContent).GetAwaiter().GetResult()
  $exportApproved = Read-JsonResponse $exportApprovalResponse
  if ($exportApproved.flow_kind -ne "export") { throw "Report export approval has the wrong flow kind" }
  $exportResumePayload = $exportPayload + @{ request_id = [string]$exportRequested.request_id }
  $exportResumeContent = New-JsonContent $exportResumePayload
  $exportResumeResponse = $client.PostAsync("${baseUrl}/api/coach/reports/export/resume", $exportResumeContent).GetAwaiter().GetResult()
  $exportResumed = Read-JsonResponse $exportResumeResponse
  if ($exportResumed.status -ne "executed" -or $exportResumed.report.id -ne $reportReady.report_id) {
    throw "Report export did not execute against the saved report"
  }

  $form = [System.Net.Http.MultipartFormDataContent]::new()
  $form.Add([System.Net.Http.StringContent]::new([string]$session.id), "session_id")
  $audioBytes = [byte[]](0x52, 0x49, 0x46, 0x46, 0x08, 0x00, 0x00, 0x00, 0x57, 0x41, 0x56, 0x45)
  $audioContent = [System.Net.Http.ByteArrayContent]::new($audioBytes)
  $audioContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("audio/wav")
  $form.Add($audioContent, "file", "verification-restart.wav")
  $audioResponse = $client.PostAsync("${baseUrl}/api/audio/upload", $form).GetAwaiter().GetResult()
  $audioRequested = Read-JsonResponse $audioResponse
  if ($audioRequested.status -ne "approval_required" -or !$audioRequested.request_id) {
    throw "Audio upload did not create an approval"
  }

  Stop-VerificationApi $server
  $server = Start-VerificationApi
  $recoveredStateResponse = $client.GetAsync("${baseUrl}/api/coach/state?session_id=$($session.id)").GetAwaiter().GetResult()
  $recoveredState = Read-JsonResponse $recoveredStateResponse
  $audioApproval = @($recoveredState.approvals | Where-Object { $_.request_id -eq $audioRequested.request_id }) | Select-Object -First 1
  if (!$audioApproval -or $audioApproval.flow_kind -ne "audio" -or $audioApproval.status -ne "pending") {
    throw "Audio approval did not recover after API restart"
  }
  $audioResolveContent = New-JsonContent @{ session_id = [string]$session.id; request_id = [string]$audioRequested.request_id }
  $audioResolveResponse = $client.PostAsync("${baseUrl}/api/permission/deny", $audioResolveContent).GetAwaiter().GetResult()
  $audioResolved = Read-JsonResponse $audioResolveResponse
  if ($audioResolved.status -ne "denied" -or $audioResolved.flow_kind -ne "audio") {
    throw "Audio approval did not reach a terminal state"
  }

  Stop-VerificationApi $server
  $server = $null
  & $backupScript -Source $databasePath -Destination $backupPath -PythonPath $pythonPath | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Database backup validation failed" }
  $backupCheck = & $pythonPath -c "import sqlite3, sys; conn = sqlite3.connect(sys.argv[1]); reports = conn.execute('SELECT COUNT(*) FROM diagnosis_reports').fetchone()[0]; integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]; conn.close(); raise SystemExit(0 if reports == 1 and integrity == 'ok' else 1)" $backupPath
  if ($LASTEXITCODE -ne 0) { throw "Database backup is missing the persisted diagnosis report" }

  $logPattern = "Authorization|offerpilot_profile|data:audio|verification-restart.wav|LIVE_WORKFLOW_ANSWER"
  $logMatches = @(Select-String -LiteralPath $stdoutPath, $stderrPath -Encoding UTF8 -Pattern $logPattern)
  if ($logMatches.Count -gt 0) { throw "Application logs contain protected request data" }

  [pscustomobject]@{
    LiveProviderProbe = "passed"
    ProfileBootstrap = "passed"
    CoachNativeTool = "search_knowledge"
    DiagnosisAndRrf = "passed"
    ReportPersistenceAndExport = "passed"
    AudioApprovalRestartRecovery = "passed"
    BackupIntegrity = "passed"
    ApplicationLogRedaction = "passed"
  } | Format-List
} finally {
  if ($client) { $client.Dispose() }
  Stop-VerificationApi $server
  foreach ($name in $environmentNames) {
    if ($null -eq $originalEnvironment[$name]) {
      Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    } else {
      Set-Item -Path "Env:$name" -Value $originalEnvironment[$name]
    }
  }
  if (!$KeepArtifacts -and (Test-Path -LiteralPath $verificationDirectory)) {
    $resolvedDirectory = (Resolve-Path -LiteralPath $verificationDirectory).Path
    $resolvedDataRoot = (Resolve-Path -LiteralPath $dataRoot).Path
    if (!$resolvedDirectory.StartsWith($resolvedDataRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove a verification directory outside the API data directory"
    }
    Remove-Item -LiteralPath $resolvedDirectory -Recurse -Force
  }
}
