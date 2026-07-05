# sync_logs.ps1 — ambil snapshot data bot Proxmox via HTTP (dashboard :8000) ke ./logs.
#
# SSH TIDAK diperlukan — dashboard sudah dengar di LAN (0.0.0.0:8000).
# Ubah IP di .env bila berubah:  DASH_URL=http://192.168.1.107:8000
#
# Pakai:  .\sync_logs.ps1
$ErrorActionPreference = "Stop"

$envFile = Join-Path $PSScriptRoot ".env"
function Get-EnvVal($n, $d) {
  if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern "^\s*$n\s*=" | Select-Object -First 1
    if ($m) { return ($m.Line -replace "^\s*$n\s*=", "").Trim().Trim('"') }
  }
  return $d
}

$base = Get-EnvVal "DASH_URL" "http://192.168.1.107:8000"
$dest = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$targets = @{
  "api/stats"            = "remote_stats.json"
  "api/trades?limit=100" = "remote_trades.json"
  "api/trades.csv"       = "remote_trades.csv"
  "api/gemini-usage"     = "remote_gemini_usage.json"
}
Write-Host "Sumber: $base  (SSH tidak dipakai)"
foreach ($ep in $targets.Keys) {
  $out = Join-Path $dest $targets[$ep]
  curl.exe -s -m 8 "$base/$ep" -o $out
  if ($LASTEXITCODE -eq 0 -and (Test-Path $out) -and (Get-Item $out).Length -gt 0) {
    Write-Host ("OK  {0,-22} -> logs/{1}" -f $ep, $targets[$ep])
  } else {
    Write-Warning "gagal $ep (dashboard mati / IP salah?)"
  }
}
Write-Host ""
Write-Host "CATATAN: regime belum ada di data ini - bot Proxmox masih pakai kode lama."
Write-Host "         Deploy ulang forward.py (patch regime) agar /api/trades memuat regime."
