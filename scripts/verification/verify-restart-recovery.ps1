param(
  [int]$Port = 8010
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$apiDirectory = Join-Path $projectRoot "src\api"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$verificationDirectory = Join-Path $projectRoot ("data\verification-restart-" + [guid]::NewGuid().ToString("N"))
$databasePath = Join-Path $verificationDirectory "offerpilot.db"
New-Item -ItemType Directory -Path $verificationDirectory | Out-Null

$env:OFFERPILOT_SQLITE_PATH = $databasePath
$env:OFFERPILOT_DEBUG = "true"
$env:OFFERPILOT_REQUIRE_EMBEDDING = "false"
$env:OFFERPILOT_PROFILE_SIGNING_KEY = "verification-restart-signing-key-0001"
$env:MIMO_API_KEY = ""
$env:OFFERPILOT_CORS_ORIGINS = "http://localhost:3000"

function Start-VerificationApi {
  $server = Start-Process -FilePath $pythonPath -WorkingDirectory $apiDirectory -ArgumentList @(
    "-m", "uvicorn", "offerpilot.main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "warning"
  ) -WindowStyle Hidden -PassThru

  for ($attempt = 0; $attempt -lt 80; $attempt++) {
    try {
      $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
      if ($health.StatusCode -eq 200) { return $server }
    } catch {}
    Start-Sleep -Milliseconds 250
  }

  if (!$server.HasExited) { Stop-Process -Id $server.Id -Force }
  throw "Temporary API did not become healthy"
}

function Read-JsonResponse([System.Net.Http.HttpResponseMessage]$response) {
  $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
  if (!$response.IsSuccessStatusCode) { throw "HTTP $([int]$response.StatusCode): $text" }
  return $text | ConvertFrom-Json
}

$server = $null
$client = $null
try {
  $server = Start-VerificationApi
  $handler = [System.Net.Http.HttpClientHandler]::new()
  $handler.UseCookies = $true
  $client = [System.Net.Http.HttpClient]::new($handler)
  $client.DefaultRequestHeaders.Add("Origin", "http://localhost:3000")

  $bootstrapContent = [System.Net.Http.StringContent]::new("{}", [System.Text.Encoding]::UTF8, "application/json")
  $bootstrap = Read-JsonResponse ($client.PostAsync("http://127.0.0.1:$Port/api/profile/bootstrap", $bootstrapContent).GetAwaiter().GetResult())
  $sessionContent = [System.Net.Http.StringContent]::new("{}", [System.Text.Encoding]::UTF8, "application/json")
  $session = Read-JsonResponse ($client.PostAsync("http://127.0.0.1:$Port/api/sessions", $sessionContent).GetAwaiter().GetResult())

  $form = [System.Net.Http.MultipartFormDataContent]::new()
  $form.Add([System.Net.Http.StringContent]::new([string]$session.id), "session_id")
  $audioContent = [System.Net.Http.ByteArrayContent]::new([byte[]](0x52, 0x49, 0x46, 0x46, 0x08, 0x00, 0x00, 0x00, 0x57, 0x41, 0x56, 0x45))
  $audioContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("audio/wav")
  $form.Add($audioContent, "file", "restart-check.wav")
  $upload = Read-JsonResponse ($client.PostAsync("http://127.0.0.1:$Port/api/audio/upload", $form).GetAwaiter().GetResult())
  if ($upload.status -ne "approval_required" -or !$upload.request_id) { throw "Audio upload did not create an approval" }

  Stop-Process -Id $server.Id -Force
  $server.WaitForExit()
  $server = Start-VerificationApi

  $stateResponse = Read-JsonResponse ($client.GetAsync("http://127.0.0.1:$Port/api/coach/state?session_id=$($session.id)").GetAwaiter().GetResult())
  $approval = @($stateResponse.approvals | Where-Object { $_.request_id -eq $upload.request_id }) | Select-Object -First 1
  if (!$approval -or $approval.flow_kind -ne "audio" -or $approval.status -ne "pending") {
    throw "Approval was not recovered after API restart"
  }

  $approvalPayload = @{ request_id = $upload.request_id; session_id = $session.id } | ConvertTo-Json -Compress
  $approveBody = [System.Net.Http.StringContent]::new($approvalPayload, [System.Text.Encoding]::UTF8, "application/json")
  $approved = Read-JsonResponse ($client.PostAsync("http://127.0.0.1:$Port/api/permission/approve", $approveBody).GetAwaiter().GetResult())
  $resumeBody = [System.Net.Http.StringContent]::new($approvalPayload, [System.Text.Encoding]::UTF8, "application/json")
  $resumed = Read-JsonResponse ($client.PostAsync("http://127.0.0.1:$Port/api/audio/resume", $resumeBody).GetAwaiter().GetResult())
  if ($resumed.status -notin @("transcribed", "asr_failed")) { throw "Recovered approval did not reach an Audio terminal result" }

  [pscustomobject]@{
    Bootstrap = "ok"
    UploadApproval = $upload.status
    RestartRecoveredApproval = $approval.status
    ResumeStatus = $resumed.status
  } | Format-List
} finally {
  if ($client) { $client.Dispose() }
  if ($server -and !$server.HasExited) { Stop-Process -Id $server.Id -Force }
  if (Test-Path -LiteralPath $verificationDirectory) { Remove-Item -LiteralPath $verificationDirectory -Recurse -Force }
}
