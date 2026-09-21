param([int]$Port = 8010)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$apiDirectory = Join-Path $projectRoot "src\api"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$verificationDirectory = Join-Path $projectRoot ("data\verification-restart-" + [guid]::NewGuid().ToString("N"))
$databasePath = Join-Path $verificationDirectory "offerpilot.db"
New-Item -ItemType Directory -Path $verificationDirectory | Out-Null

$originalEnvironment = @{}
foreach ($name in @("OFFERPILOT_SQLITE_PATH", "OFFERPILOT_DEBUG", "OFFERPILOT_REQUIRE_EMBEDDING", "OFFERPILOT_PROFILE_SIGNING_KEY", "MIMO_API_KEY", "OFFERPILOT_CORS_ORIGINS")) {
  $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}
$env:OFFERPILOT_SQLITE_PATH = $databasePath
$env:OFFERPILOT_DEBUG = "true"
$env:OFFERPILOT_REQUIRE_EMBEDDING = "false"
$env:OFFERPILOT_PROFILE_SIGNING_KEY = "verification-restart-signing-key-0001"
$env:MIMO_API_KEY = ""
$env:OFFERPILOT_CORS_ORIGINS = "http://localhost:3000"

function Start-VerificationApi {
  $server = Start-Process -FilePath $pythonPath -WorkingDirectory $apiDirectory -ArgumentList @("-m", "uvicorn", "offerpilot.main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "warning") -WindowStyle Hidden -PassThru
  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    try { if ((Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/ready" -TimeoutSec 2).StatusCode -eq 200) { return $server } } catch {}
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
  return [System.Net.Http.StringContent]::new(($Payload | ConvertTo-Json -Depth 8 -Compress), [System.Text.Encoding]::UTF8, "application/json")
}

function Wait-Run([System.Net.Http.HttpClient]$Client, [string]$RunId, [string[]]$ExpectedStates) {
  for ($attempt = 0; $attempt -lt 100; $attempt++) {
    $run = Read-JsonResponse ($Client.GetAsync("http://127.0.0.1:$Port/api/runs/$RunId").GetAwaiter().GetResult())
    if ($ExpectedStates -contains [string]$run.status) { return $run }
    Start-Sleep -Milliseconds 100
  }
  throw "Run did not reach one of: $($ExpectedStates -join ', ')"
}

$server = $null
$client = $null
try {
  $server = Start-VerificationApi
  $handler = [System.Net.Http.HttpClientHandler]::new()
  $handler.UseCookies = $true
  $client = [System.Net.Http.HttpClient]::new($handler)
  $client.DefaultRequestHeaders.Add("Origin", "http://localhost:3000")
  $baseUrl = "http://127.0.0.1:$Port"
  $null = Read-JsonResponse ($client.PostAsync("$baseUrl/api/profile/bootstrap", (New-JsonContent @{})).GetAwaiter().GetResult())
  $session = Read-JsonResponse ($client.PostAsync("$baseUrl/api/sessions", (New-JsonContent @{})).GetAwaiter().GetResult())

  $form = [System.Net.Http.MultipartFormDataContent]::new()
  $audio = [System.Net.Http.ByteArrayContent]::new([byte[]](0x52, 0x49, 0x46, 0x46, 0x08, 0x00, 0x00, 0x00, 0x57, 0x41, 0x56, 0x45))
  $audio.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("audio/wav")
  $form.Add($audio, "file", "restart-check.wav")
  $upload = Read-JsonResponse ($client.PostAsync("$baseUrl/api/sessions/$($session.id)/audio-uploads", $form).GetAwaiter().GetResult())
  $runRequest = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$baseUrl/api/sessions/$($session.id)/runs")
  $runRequest.Headers.Add("Idempotency-Key", "restart-audio-run")
  $runRequest.Content = New-JsonContent @{ type = "audio_transcription"; input = @{ upload_id = [string]$upload.upload.id } }
  $run = (Read-JsonResponse ($client.SendAsync($runRequest).GetAwaiter().GetResult())).run
  $null = Wait-Run $client $run.id @("waiting_approval")

  Stop-Process -Id $server.Id -Force
  $server.WaitForExit()
  $server = Start-VerificationApi
  $events = (Read-JsonResponse ($client.GetAsync("$baseUrl/api/runs/$($run.id)/events").GetAwaiter().GetResult())).events
  $approvalEvent = @($events | Where-Object { $_.type -eq "approval_required" } | Select-Object -Last 1)
  if (!$approvalEvent -or !$approvalEvent.data.approval_id) { throw "Approval event was not recovered" }
  $approved = Read-JsonResponse ($client.PostAsync("$baseUrl/api/approvals/$($approvalEvent.data.approval_id)/decision", (New-JsonContent @{ decision = "approve" })).GetAwaiter().GetResult())
  $terminal = Wait-Run $client $run.id @("completed", "failed")
  [pscustomobject]@{ Session = $session.id; Run = $run.id; ApprovalRecovered = $approvalEvent.data.approval_id; TerminalStatus = $terminal.status; ApprovalStatus = $approved.approval.status } | Format-List
} finally {
  if ($client) { $client.Dispose() }
  if ($server -and !$server.HasExited) { Stop-Process -Id $server.Id -Force }
  foreach ($name in $originalEnvironment.Keys) {
    if ($null -eq $originalEnvironment[$name]) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue } else { Set-Item -Path "Env:$name" -Value $originalEnvironment[$name] }
  }
  if (Test-Path -LiteralPath $verificationDirectory) { Remove-Item -LiteralPath $verificationDirectory -Recurse -Force }
}
