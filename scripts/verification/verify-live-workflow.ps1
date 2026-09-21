param([int]$Port = 8020, [switch]$KeepArtifacts)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$apiDirectory = Join-Path $projectRoot "src\api"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$verificationDirectory = Join-Path $projectRoot ("data\verification-live-" + [guid]::NewGuid().ToString("N"))
$databasePath = Join-Path $verificationDirectory "offerpilot.db"
$audioPath = Join-Path $verificationDirectory "offerpilot-verification.wav"
$stdoutPath = Join-Path $verificationDirectory "api.stdout.log"
$stderrPath = Join-Path $verificationDirectory "api.stderr.log"
$performancePath = Join-Path $verificationDirectory "diagnosis-performance.json"
New-Item -ItemType Directory -Path $verificationDirectory | Out-Null

$originalEnvironment = @{}
foreach ($name in @("OFFERPILOT_SQLITE_PATH", "OFFERPILOT_DEBUG", "OFFERPILOT_CORS_ORIGINS", "OFFERPILOT_PROFILE_SIGNING_KEY", "PYTHONPATH")) {
  $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}
$env:OFFERPILOT_SQLITE_PATH = $databasePath
$env:OFFERPILOT_DEBUG = "true"
$env:OFFERPILOT_CORS_ORIGINS = "http://localhost:3000"
$env:OFFERPILOT_PROFILE_SIGNING_KEY = "verification-live-signing-key-00000001"
$env:PYTHONPATH = $apiDirectory

function Start-VerificationApi {
  $server = Start-Process -FilePath $pythonPath -WorkingDirectory $apiDirectory -ArgumentList @("-m", "uvicorn", "offerpilot.main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "warning") -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
  for ($attempt = 0; $attempt -lt 120; $attempt++) {
    try {
      if ((Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/ready" -TimeoutSec 2).StatusCode -eq 200) { return $server }
    } catch {}
    Start-Sleep -Milliseconds 250
  }
  if (!$server.HasExited) { Stop-Process -Id $server.Id -Force }
  throw "Temporary API did not become ready"
}

function Read-JsonResponse([System.Net.Http.HttpResponseMessage]$Response) {
  $text = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
  if (!$Response.IsSuccessStatusCode) { throw "HTTP $([int]$Response.StatusCode): $text" }
  return $text | ConvertFrom-Json
}

function New-JsonContent([object]$Payload) {
  return [System.Net.Http.StringContent]::new(($Payload | ConvertTo-Json -Depth 12 -Compress), [System.Text.Encoding]::UTF8, "application/json")
}

function Start-Run([System.Net.Http.HttpClient]$Client, [string]$BaseUrl, [string]$SessionId, [string]$Type, [hashtable]$Payload) {
  $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$BaseUrl/api/sessions/$SessionId/runs")
  $request.Headers.Add("Idempotency-Key", [guid]::NewGuid().ToString("N"))
  $request.Content = New-JsonContent @{ type = $Type; input = $Payload }
  return (Read-JsonResponse ($Client.SendAsync($request).GetAwaiter().GetResult())).run
}

function Wait-Run([System.Net.Http.HttpClient]$Client, [string]$BaseUrl, [string]$RunId, [string[]]$States) {
  for ($attempt = 0; $attempt -lt 480; $attempt++) {
    $run = Read-JsonResponse ($Client.GetAsync("$BaseUrl/api/runs/$RunId").GetAwaiter().GetResult())
    if ($States -contains [string]$run.status) { return $run }
    if (@("failed", "cancelled", "interrupted") -contains [string]$run.status) {
      throw "Run reached terminal status $($run.status) with error_code=$($run.error_code)"
    }
    Start-Sleep -Milliseconds 250
  }
  throw "Run did not reach $($States -join ', ')"
}

function Read-SseEvents([System.Net.Http.HttpClient]$Client, [string]$BaseUrl, [string]$RunId) {
  $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, "$BaseUrl/api/runs/$RunId/stream?after=0")
  $request.Headers.Accept.Add([System.Net.Http.Headers.MediaTypeWithQualityHeaderValue]::new("text/event-stream"))
  $response = $Client.SendAsync($request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
  if (!$response.IsSuccessStatusCode) {
    $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $response.Dispose()
    throw "SSE HTTP $([int]$response.StatusCode): $text"
  }
  $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
  $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::UTF8)
  $events = [System.Collections.Generic.List[object]]::new()
  try {
    while (($line = $reader.ReadLine()) -ne $null) {
      if (!$line.StartsWith("data:")) { continue }
      $event = $line.Substring(5).TrimStart() | ConvertFrom-Json
      $events.Add($event)
      if (@("run_completed", "run_failed", "run_cancelled", "run_interrupted") -contains [string]$event.type) { break }
    }
  } finally {
    $reader.Dispose()
    $stream.Dispose()
    $response.Dispose()
    $request.Dispose()
  }
  return $events.ToArray()
}

function Get-RunEvents([System.Net.Http.HttpClient]$Client, [string]$BaseUrl, [string]$RunId) {
  return @((Read-JsonResponse ($Client.GetAsync("$BaseUrl/api/runs/$RunId/events").GetAwaiter().GetResult())).events)
}

function Get-RequiredEvent([object[]]$Events, [string]$Type) {
  $event = $Events | Where-Object { $_.type -eq $Type } | Select-Object -Last 1
  if ($null -eq $event) { throw "Required Run event was not persisted: $Type" }
  return $event
}

function Assert-SseStages([object[]]$Events) {
  foreach ($type in @("diagnosis_model_started", "diagnosis_model_completed", "report_ready", "run_completed")) {
    if (!($Events | Where-Object { $_.type -eq $type } | Select-Object -First 1)) {
      throw "SSE did not deliver required event: $type"
    }
  }
}

function Approve-Run([System.Net.Http.HttpClient]$Client, [string]$BaseUrl, [string]$RunId) {
  $events = Get-RunEvents $Client $BaseUrl $RunId
  $approval = Get-RequiredEvent $events "approval_required"
  if (!$approval.data.approval_id) { throw "Approval event did not contain approval_id" }
  return Read-JsonResponse ($Client.PostAsync("$BaseUrl/api/approvals/$($approval.data.approval_id)/decision", (New-JsonContent @{ decision = "approve" })).GetAwaiter().GetResult())
}

function Get-Median([double[]]$Values) {
  if (!$Values -or $Values.Count -eq 0) { throw "Cannot calculate a median from an empty set" }
  $sorted = @($Values | Sort-Object)
  return [double]$sorted[[int][math]::Floor($sorted.Count / 2)]
}

function New-VerificationAudio([string]$Path) {
  Add-Type -AssemblyName System.Speech
  $synthesizer = [System.Speech.Synthesis.SpeechSynthesizer]::new()
  try {
    $synthesizer.SetOutputToWaveFile($Path)
    $synthesizer.Speak("ReAct combines reasoning, actions, observations, timeouts, retries, and persistent run events.")
  } finally {
    $synthesizer.Dispose()
  }
}

function Upload-Audio([System.Net.Http.HttpClient]$Client, [string]$BaseUrl, [string]$SessionId, [string]$Path) {
  $form = [System.Net.Http.MultipartFormDataContent]::new()
  $fileStream = [System.IO.File]::OpenRead($Path)
  $content = [System.Net.Http.StreamContent]::new($fileStream)
  $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("audio/wav")
  $form.Add($content, "file", "offerpilot-verification.wav")
  try {
    return Read-JsonResponse ($Client.PostAsync("$BaseUrl/api/sessions/$SessionId/audio-uploads", $form).GetAwaiter().GetResult())
  } finally {
    $form.Dispose()
    $content.Dispose()
    $fileStream.Dispose()
  }
}

$server = $null
$client = $null
try {
  & $pythonPath (Join-Path $PSScriptRoot "provider_probe.py")
  if ($LASTEXITCODE -ne 0) { throw "Live Provider Probe failed" }

  $server = Start-VerificationApi
  $handler = [System.Net.Http.HttpClientHandler]::new()
  $handler.UseCookies = $true
  $client = [System.Net.Http.HttpClient]::new($handler)
  $client.Timeout = [TimeSpan]::FromSeconds(120)
  $client.DefaultRequestHeaders.Add("Origin", "http://localhost:3000")
  $baseUrl = "http://127.0.0.1:$Port"
  $null = Read-JsonResponse ($client.PostAsync("$baseUrl/api/profile/bootstrap", (New-JsonContent @{})).GetAwaiter().GetResult())

  $coachSession = Read-JsonResponse ($client.PostAsync("$baseUrl/api/sessions", (New-JsonContent @{})).GetAwaiter().GetResult())
  $coach = Start-Run $client $baseUrl $coachSession.id "coach" @{ message = "Search the internal knowledge base for a ReAct tool-calling interview practice question." }
  $coachResult = Wait-Run $client $baseUrl $coach.id @("completed")

  $fixedQuestion = "Explain the production safeguards required around a ReAct agent loop."
  $fixedAnswer = "ReAct combines reasoning, actions and observations. Production code validates tool parameters, enforces permissions and idempotency, applies timeouts with bounded retries, persists ordered Run events, supports cancellation, and records privacy-safe failure metrics."
  $performance = [System.Collections.Generic.List[object]]::new()

  for ($index = 1; $index -le 3; $index++) {
    $session = Read-JsonResponse ($client.PostAsync("$baseUrl/api/sessions", (New-JsonContent @{})).GetAwaiter().GetResult())
    $diagnosis = Start-Run $client $baseUrl $session.id "diagnosis" @{ question = $fixedQuestion; answer = $fixedAnswer }
    $sseEvents = @(Read-SseEvents $client $baseUrl $diagnosis.id)
    Assert-SseStages $sseEvents
    $diagnosisResult = Wait-Run $client $baseUrl $diagnosis.id @("completed")
    $events = Get-RunEvents $client $baseUrl $diagnosis.id

    $knowledge = Get-RequiredEvent $events "knowledge_merged"
    $context = Get-RequiredEvent $events "context_built"
    $modelStarted = Get-RequiredEvent $events "diagnosis_model_started"
    $modelCompleted = Get-RequiredEvent $events "diagnosis_model_completed"
    $validated = Get-RequiredEvent $events "output_validated"
    $reportReady = Get-RequiredEvent $events "report_ready"
    $completed = Get-RequiredEvent $events "run_completed"

    $points = @($diagnosisResult.result.diagnosis.exam_points)
    $expectedPointCount = [int]$modelStarted.data.exam_point_count
    $uniquePointCount = @($points | ForEach-Object { [string]$_.point_id } | Sort-Object -Unique).Count
    if ($points.Count -ne $expectedPointCount -or $uniquePointCount -ne $expectedPointCount) {
      throw "Diagnosis $index did not cover every selected stable point exactly once"
    }
    if (!$validated.data.success) { throw "Diagnosis $index failed structured output validation" }
    if ([string]$modelCompleted.data.finish_reason -eq "length") { throw "Diagnosis $index ended with finish_reason=length" }
    if ([double]$modelCompleted.data.duration_ms -gt 60000) { throw "Diagnosis $index model duration exceeded 60 seconds" }

    $performance.Add([pscustomobject]@{
      Index = $index
      SessionId = [string]$session.id
      RunId = [string]$diagnosis.id
      ReportId = [string]$diagnosisResult.result.report_id
      RetrievalMs = [double]$knowledge.data.duration_ms
      ContextMs = [double]$context.data.duration_ms
      ModelMs = [double]$modelCompleted.data.duration_ms
      FirstTokenMs = if ($null -eq $modelCompleted.data.first_token_ms) { $null } else { [double]$modelCompleted.data.first_token_ms }
      ValidationMs = [double]$validated.data.duration_ms
      PersistenceMs = [double]$reportReady.data.duration_ms
      RunTotalMs = [double]$completed.data.timing.total_duration_ms
      StreamMode = [string]$modelCompleted.data.stream_mode
      FinishReason = [string]$modelCompleted.data.finish_reason
      InputTokens = $modelCompleted.data.input_tokens
      OutputTokens = $modelCompleted.data.output_tokens
      ReasoningTokens = $modelCompleted.data.reasoning_tokens
      ExamPointCount = $points.Count
    })
  }

  $modelMedian = Get-Median @($performance | ForEach-Object { [double]$_.ModelMs })
  $runMedian = Get-Median @($performance | ForEach-Object { [double]$_.RunTotalMs })
  if ($modelMedian -gt 30000) { throw "Diagnosis model median ${modelMedian}ms exceeded the 30000ms gate" }
  if ($runMedian -gt 35000) { throw "Diagnosis Run median ${runMedian}ms exceeded the 35000ms gate" }

  $exportSource = $performance[0]
  $export = Start-Run $client $baseUrl $exportSource.SessionId "report_export" @{ report_id = $exportSource.ReportId }
  $null = Wait-Run $client $baseUrl $export.id @("waiting_approval")
  $null = Approve-Run $client $baseUrl $export.id
  $exportResult = Wait-Run $client $baseUrl $export.id @("completed")

  New-VerificationAudio $audioPath
  $audioSession = Read-JsonResponse ($client.PostAsync("$baseUrl/api/sessions", (New-JsonContent @{})).GetAwaiter().GetResult())
  $upload = Upload-Audio $client $baseUrl $audioSession.id $audioPath
  $audioRun = Start-Run $client $baseUrl $audioSession.id "audio_transcription" @{ upload_id = [string]$upload.upload.id }
  $null = Wait-Run $client $baseUrl $audioRun.id @("waiting_approval")
  $null = Approve-Run $client $baseUrl $audioRun.id
  $audioResult = Wait-Run $client $baseUrl $audioRun.id @("completed")
  if ([string]::IsNullOrWhiteSpace([string]$audioResult.result.transcript)) {
    throw "Audio workflow completed without a transcript"
  }

  [System.IO.File]::WriteAllText($performancePath, ($performance | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))

  if ($server -and !$server.HasExited) {
    Stop-Process -Id $server.Id -Force
    $server.WaitForExit()
    $server = $null
  }
  $logText = ""
  foreach ($path in @($stdoutPath, $stderrPath)) {
    if (Test-Path -LiteralPath $path) { $logText += Get-Content -LiteralPath $path -Encoding UTF8 -Raw }
  }
  foreach ($sensitive in @($fixedQuestion, $fixedAnswer, "candidate_answer", '"exam_points"')) {
    if ($logText.Contains($sensitive)) { throw "Sensitive model input or raw JSON was found in application logs" }
  }

  $performance | Format-Table Index, RetrievalMs, ContextMs, ModelMs, FirstTokenMs, ValidationMs, PersistenceMs, RunTotalMs, StreamMode, FinishReason, ExamPointCount -AutoSize
  [pscustomobject]@{
    ProviderProbe = "passed"
    CoachRun = $coachResult.status
    DiagnosisRuns = $performance.Count
    ModelMedianMs = $modelMedian
    RunMedianMs = $runMedian
    AudioRun = $audioResult.status
    ExportRun = $exportResult.status
    SensitiveLogScan = "passed"
    PerformanceArtifact = $performancePath
  } | Format-List
} finally {
  if ($client) { $client.Dispose() }
  if ($server -and !$server.HasExited) { Stop-Process -Id $server.Id -Force }
  foreach ($name in $originalEnvironment.Keys) {
    if ($null -eq $originalEnvironment[$name]) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue } else { Set-Item -Path "Env:$name" -Value $originalEnvironment[$name] }
  }
  if (!$KeepArtifacts -and (Test-Path -LiteralPath $verificationDirectory)) { Remove-Item -LiteralPath $verificationDirectory -Recurse -Force }
}
