# Launcher daemon riset — dipanggil Scheduled Task "BinanceBot_Collectors" saat logon,
# atau manual: powershell -File start_collectors.ps1
# Dedupe via logs\*.pid: daemon yang masih hidup TIDAK dinyalakan dobel.

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo
New-Item -ItemType Directory -Force "$repo\logs" | Out-Null

$daemons = @(
    @{ Name = "l2collect";   Args = @("l2collect.py", "--symbols", "CRV/USDC:USDC", "BOME/USDC:USDC",
                                      "FIL/USDC:USDC", "NEAR/USDC:USDC", "NEO/USDC:USDC", "PNUT/USDC:USDC",
                                      "BTC/USDC:USDC", "ETH/USDC:USDC", "--levels", "10", "--interval", "2") },
    @{ Name = "oicollect";   Args = @("oicollect.py", "--interval", "3600") },
    @{ Name = "h28_forward"; Args = @("h28_forward.py", "--interval", "3600") }
)

foreach ($d in $daemons) {
    $pidFile = "$repo\logs\$($d.Name).pid"
    if (Test-Path $pidFile) {
        $oldPid = Get-Content $pidFile -ErrorAction SilentlyContinue
        $alive = $null
        try { $alive = Get-Process -Id $oldPid -ErrorAction Stop } catch {}
        if ($alive -and $alive.ProcessName -like "python*") {
            Write-Output "$($d.Name): sudah hidup (PID $oldPid) - lewati"
            continue
        }
    }
    $p = Start-Process -FilePath python -ArgumentList $d.Args -WorkingDirectory $repo `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$repo\logs\$($d.Name).log" `
        -RedirectStandardError  "$repo\logs\$($d.Name).err.log"
    $p.Id | Out-File $pidFile -Encoding ascii
    Write-Output "$($d.Name): dinyalakan (PID $($p.Id))"
}
