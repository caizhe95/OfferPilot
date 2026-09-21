param([string]$ProjectName = "offerpilot-compose-$PID", [int]$ApiPort = 8010, [int]$WebPort = 3100)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$rootDirectory = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composeFile = Join-Path $rootDirectory "docker-compose.yml"
if (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in $ApiPort, $WebPort }) { throw "Ports $ApiPort or $WebPort are already in use" }

function Invoke-Compose([string[]]$ComposeArgs) {
  & docker compose --project-name $ProjectName --file $composeFile @ComposeArgs
  if ($LASTEXITCODE -ne 0) { throw "docker compose $($ComposeArgs -join ' ') failed" }
}
function Read-JsonResponse([System.Net.Http.HttpResponseMessage]$Response) {
  $text = $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
  if (!$Response.IsSuccessStatusCode) { throw "HTTP $([int]$Response.StatusCode): $text" }
  return $text | ConvertFrom-Json
}
function New-JsonContent([object]$Payload) {
  [System.Net.Http.StringContent]::new(($Payload | ConvertTo-Json -Depth 8 -Compress), [System.Text.Encoding]::UTF8, "application/json")
}
function Wait-Run([System.Net.Http.HttpClient]$Client, [string]$BaseUrl, [string]$RunId) {
  for ($attempt = 0; $attempt -lt 100; $attempt++) {
    $run = Read-JsonResponse ($Client.GetAsync("$BaseUrl/api/runs/$RunId").GetAwaiter().GetResult())
    if ($run.status -eq "waiting_approval") { return $run }
    Start-Sleep -Milliseconds 100
  }
  throw "Audio Run did not enter waiting_approval"
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
$env:OFFERPILOT_CORS_ORIGINS = "http://localhost:$WebPort"
$env:OFFERPILOT_DEBUG = "true"
$env:API_BASE_URL = "http://api:8000"
$env:API_PORT = "$ApiPort"
$env:WEB_PORT = "$WebPort"

$started = $false
$client = $null
try {
  Invoke-Compose @("config", "--quiet")
  Invoke-Compose @("build", "--no-cache")
  Invoke-Compose @("up", "--detach", "--wait", "--wait-timeout", "180")
  $started = $true
  $baseUrl = "http://localhost:$WebPort"
  $handler = [System.Net.Http.HttpClientHandler]::new()
  $handler.UseCookies = $true
  $client = [System.Net.Http.HttpClient]::new($handler)
  $client.DefaultRequestHeaders.Add("Origin", "http://localhost:$WebPort")
  $null = Read-JsonResponse ($client.PostAsync("$baseUrl/api/profile/bootstrap", (New-JsonContent @{})).GetAwaiter().GetResult())
  $session = Read-JsonResponse ($client.PostAsync("$baseUrl/api/sessions", (New-JsonContent @{})).GetAwaiter().GetResult())
  $form = [System.Net.Http.MultipartFormDataContent]::new()
  $audio = [System.Net.Http.ByteArrayContent]::new([byte[]](0x52, 0x49, 0x46, 0x46, 0x08, 0x00, 0x00, 0x00, 0x57, 0x41, 0x56, 0x45))
  $audio.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("audio/wav")
  $form.Add($audio, "file", "compose-restart.wav")
  $upload = Read-JsonResponse ($client.PostAsync("$baseUrl/api/sessions/$($session.id)/audio-uploads", $form).GetAwaiter().GetResult())
  $runRequest = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$baseUrl/api/sessions/$($session.id)/runs")
  $runRequest.Headers.Add("Idempotency-Key", "compose-audio-run")
  $runRequest.Content = New-JsonContent @{ type = "audio_transcription"; input = @{ upload_id = [string]$upload.upload.id } }
  $run = (Read-JsonResponse ($client.SendAsync($runRequest).GetAwaiter().GetResult())).run
  $null = Wait-Run $client $baseUrl $run.id
  Invoke-Compose @("restart", "api")
  Invoke-Compose @("up", "--detach", "--wait", "--wait-timeout", "180", "api")
  $events = (Read-JsonResponse ($client.GetAsync("$baseUrl/api/runs/$($run.id)/events").GetAwaiter().GetResult())).events
  if (!(@($events | Where-Object { $_.type -eq "approval_required" }).Count)) { throw "Approval event did not survive API restart" }
  [pscustomobject]@{ Compose = "valid"; WebProxy = "healthy"; Session = $session.id; AudioApprovalRestartRecovery = "passed" } | Format-List
} finally {
  if ($client) { $client.Dispose() }
  if ($started) { try { Invoke-Compose @("down", "--volumes", "--remove-orphans") } catch {} }
}
