param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [Parameter(Mandatory = $true)]
    [string]$Destination,

    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$destinationDirectory = Split-Path -Parent $destinationPath

if (-not (Test-Path -LiteralPath $destinationDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $destinationDirectory | Out-Null
}

if (Test-Path -LiteralPath $destinationPath) {
    throw "Destination already exists: $destinationPath"
}

if (-not $PythonPath) {
    $projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    $PythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python executable not found: $PythonPath"
}

$backupCode = @'
import sqlite3
import sys

source_path, destination_path = sys.argv[1:]
source = sqlite3.connect(source_path)
destination = sqlite3.connect(destination_path)
try:
    with destination:
        source.backup(destination)
finally:
    destination.close()
    source.close()
'@

& $PythonPath -c $backupCode $sourcePath $destinationPath
if ($LASTEXITCODE -ne 0) {
    throw "SQLite backup failed with exit code $LASTEXITCODE"
}

$integrityCode = @'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
try:
    pragma = bytes((80, 82, 65, 71, 77, 65, 32, 105, 110, 116, 101, 103, 114, 105, 116, 121, 95, 99, 104, 101, 99, 107)).decode()
    result = connection.execute(pragma).fetchone()[0]
finally:
    connection.close()
if result != bytes((111, 107)).decode():
    raise SystemExit(result)
'@

& $PythonPath -c $integrityCode $destinationPath
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $destinationPath -Force
    throw "Backup integrity check failed"
}

$hash = Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256
[pscustomobject]@{
    Source = $sourcePath
    Destination = $destinationPath
    Sha256 = $hash.Hash
    Integrity = "ok"
}
