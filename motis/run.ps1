# Local MOTIS 2 test instance for this feed (Windows PowerShell).
#
# Usage (from the repository folder):
#   powershell -ExecutionPolicy Bypass -File motis\run.ps1
#   powershell -ExecutionPolicy Bypass -File motis\run.ps1 -RtUrl https://<railway-domain>/trip_updates.pb
#
# Then open http://localhost:8080
param(
    [string]$RtUrl = "",
    [int]$Port = 8080,
    [string]$OsmUrl = "https://download.geofabrik.de/europe/croatia-latest.osm.pbf",
    [string]$GtfsUrl = "https://github.com/hoermalmeister/zadar_gtfs/raw/main/zadar_gtfs.zip"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # otherwise Invoke-WebRequest is extremely slow
$Name = "zadar"

$Work = Join-Path $PSScriptRoot "work"
New-Item -ItemType Directory -Force -Path $Work | Out-Null
Set-Location $Work

if (-not (Test-Path ".\motis.exe")) {
    Write-Host "== Downloading MOTIS (windows)"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/motis-project/motis/releases/latest/download/motis-windows.zip" -OutFile "motis-windows.zip"
    $tmp = Join-Path $Work "motis_unzip"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive -Path "motis-windows.zip" -DestinationPath $tmp
    $exe = Get-ChildItem -Path $tmp -Recurse -Filter "motis.exe" | Select-Object -First 1
    if (-not $exe) { throw "motis.exe not found in the downloaded archive" }
    Get-ChildItem -Path $exe.DirectoryName | Move-Item -Destination $Work -Force
    Remove-Item -Recurse -Force $tmp, "motis-windows.zip"
}

$OsmFile = Split-Path -Leaf $OsmUrl
if (-not (Test-Path $OsmFile) -or ((Get-Item $OsmFile).LastWriteTime -lt (Get-Date).AddDays(-30))) {
    Write-Host "== Downloading map $OsmUrl"
    Invoke-WebRequest -UseBasicParsing -Uri $OsmUrl -OutFile $OsmFile
}

Write-Host "== Downloading current GTFS"
Invoke-WebRequest -UseBasicParsing -Uri $GtfsUrl -OutFile "$($Name)_gtfs.zip"

Write-Host "== Writing config.yml"
$rt = ""
if ($RtUrl) {
    $rt = @"

      rt:
        - url: $RtUrl
"@
}
$config = @"
server:
  port: $Port
osm: $OsmFile
tiles:
  profile: tiles-profiles/full.lua
  db_size: 274877906944
  flush_threshold: 100000
timetable:
  first_day: TODAY
  num_days: 60
  railviz: true
  with_shapes: true
  update_interval: 30
  http_timeout: 30
  datasets:
    $($Name):
      path: $($Name)_gtfs.zip$rt
street_routing: true
osr_footpath: true
geocoding: true
reverse_geocoding: true
"@
[System.IO.File]::WriteAllText((Join-Path $Work "config.yml"), $config.Replace("`r`n", "`n"))

Write-Host "== Import (first run takes a while, later runs only redo what changed)"
& .\motis.exe import
if ($LASTEXITCODE -ne 0) { throw "motis import failed" }

Write-Host "== Server: http://localhost:$Port"
& .\motis.exe server
