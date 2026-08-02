param(
  [string]$ProjectName = "offerpilot-verify-compose-$PID",
  [int]$ApiPort = 8010,
  [int]$WebPort = 3100
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

$rootDirectory = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composeFile = Join-Path $rootDirectory "docker-compose.yml"
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in $ApiPort, $WebPort }
if ($listeners) { throw "Ports $ApiPort or $WebPort are already in use; do not disrupt an existing service" }

function Invoke-Compose([string[]]$ComposeArgs) {
  & docker compose --project-name $ProjectName --file $composeFile @ComposeArgs
  if ($LASTEXITCODE -ne 0) { throw "docker compose $($ComposeArgs -join ' ') failed" }
}

function Read-JsonResponse([System.Net.Http.HttpResponseMessage]$Response) {
  $text = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
  if (!$Response.IsSuccessStatusCode) { throw "HTTP $([int]$Response.StatusCode): $text" }
  return $text | ConvertFrom-Json
}

$env:OFFERPILOT_OPENAI_API_KEY = ""
$env:OFFERPILOT_OPENAI_BASE_URL = ""
$env:OFFERPILOT_OPENAI_MODEL = ""
$env:OFFERPILOT_EMBEDDING_API_KEY = ""
$env:OFFERPILOT_EMBEDDING_BASE_URL = ""
$env:OFFERPILOT_REQUIRE_EMBEDDING = "false"
$env:MIMO_API_KEY = ""
$env:OFFERPILOT_ADMIN_KEY = "verification-admin-key"
$env:OFFERPILOT_PROFILE_SIGNING_KEY = "verification-compose-signing-key-with-at-least-32-bytes"
$env:API_BASE_URL = "http://api:8000"
$env:API_PORT = "$ApiPort"
$env:WEB_PORT = "$WebPort"
$env:OFFERPILOT_CORS_ORIGINS = "https://app.example.test"
$env:OFFERPILOT_DEBUG = "false"

$started = $false
$client = $null
try {
  Invoke-Compose @("config", "--quiet")
  Invoke-Compose @("build", "--no-cache")

  # Validate the production TLS/Secure-Cookie configuration before local HTTP verification.
  Invoke-Compose @(
    "run", "--rm", "--no-deps", "api", "python", "-c",
    "from offerpilot.core.config import settings; settings.validate_runtime_security(); print('production configuration valid')"
  )

  $env:OFFERPILOT_CORS_ORIGINS = "http://localhost:$WebPort"
  $env:OFFERPILOT_DEBUG = "true"
  $started = $true
  Invoke-Compose @("up", "--detach", "--wait", "--wait-timeout", "180")

  Invoke-Compose @(
    "exec", "-T", "api", "python", "-c",
    "from pathlib import Path; from offerpilot.harness.harness import rules_directory; assert Path('/app/knowledge').is_dir(); assert rules_directory().is_dir(); assert Path('/app/data').is_dir(); print('mounted assets valid')"
  )

  $handler = [System.Net.Http.HttpClientHandler]::new()
  $handler.UseCookies = $true
  $client = [System.Net.Http.HttpClient]::new($handler)
  $client.DefaultRequestHeaders.Add("Origin", "http://localhost:$WebPort")

  $bootstrapContent = [System.Net.Http.StringContent]::new("{}", [System.Text.Encoding]::UTF8, "application/json")
  $bootstrap = Read-JsonResponse ($client.PostAsync("http://localhost:$WebPort/api/profile/bootstrap", $bootstrapContent).GetAwaiter().GetResult())
  $sessionContent = [System.Net.Http.StringContent]::new("{}", [System.Text.Encoding]::UTF8, "application/json")
  $session = Read-JsonResponse ($client.PostAsync("http://localhost:$WebPort/api/sessions", $sessionContent).GetAwaiter().GetResult())

  $form = [System.Net.Http.MultipartFormDataContent]::new()
  $form.Add([System.Net.Http.StringContent]::new([string]$session.id), "session_id")
  $audioContent = [System.Net.Http.ByteArrayContent]::new([byte[]](0x52, 0x49, 0x46, 0x46, 0x08, 0x00, 0x00, 0x00, 0x57, 0x41, 0x56, 0x45))
  $audioContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("audio/wav")
  $form.Add($audioContent, "file", "compose-restart.wav")
  $upload = Read-JsonResponse ($client.PostAsync("http://localhost:$WebPort/api/audio/upload", $form).GetAwaiter().GetResult())
  if ($upload.status -ne "approval_required" -or !$upload.request_id) { throw "Audio upload did not create an approval" }

  Invoke-Compose @("restart", "api")
  Invoke-Compose @("up", "--detach", "--wait", "--wait-timeout", "180", "api")

  $stateResponse = Read-JsonResponse ($client.GetAsync("http://localhost:$WebPort/api/coach/state?session_id=$($session.id)").GetAwaiter().GetResult())
  $approval = @($stateResponse.approvals | Where-Object { $_.request_id -eq $upload.request_id }) | Select-Object -First 1
  if (!$approval -or $approval.flow_kind -ne "audio" -or $approval.status -ne "pending") {
    throw "Approval did not persist through the API container restart"
  }

  Invoke-Compose @("exec", "-T", "api", "test", "-f", "/app/data/offerpilot.db")
  $approvalPayload = @{ request_id = $upload.request_id; session_id = $session.id } | ConvertTo-Json -Compress
  $approveBody = [System.Net.Http.StringContent]::new($approvalPayload, [System.Text.Encoding]::UTF8, "application/json")
  $approved = Read-JsonResponse ($client.PostAsync("http://localhost:$WebPort/api/permission/approve", $approveBody).GetAwaiter().GetResult())
  $resumeBody = [System.Net.Http.StringContent]::new($approvalPayload, [System.Text.Encoding]::UTF8, "application/json")
  $resumed = Read-JsonResponse ($client.PostAsync("http://localhost:$WebPort/api/audio/resume", $resumeBody).GetAwaiter().GetResult())
  if ($resumed.status -notin @("transcribed", "asr_failed")) { throw "Recovered approval did not reach an Audio terminal result" }

  [pscustomobject]@{
    ProductionConfig = "valid"
    ApiHealth = "healthy"
    WebProxy = "healthy"
    MountedAssets = "valid"
    RestartRecoveredApproval = $approval.status
    ResumeStatus = $resumed.status
  } | Format-List
} finally {
  if ($client) { $client.Dispose() }
  if ($started) {
    try { Invoke-Compose @("down", "--volumes", "--remove-orphans") } catch {}
  }
}
