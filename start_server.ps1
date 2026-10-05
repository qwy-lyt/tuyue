# Start the server as an independent process.
#
# Launched from inside another tool, a child process dies when that tool cleans
# up its process tree. Start-Process detaches it, so the server keeps running on
# its own -- the same thing run.bat does when you double-click it.

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Output "ERROR: .venv not found. Run run.bat once to create it."
    exit 1
}

# Refuse to start a second copy: two servers cannot share port 8000.
$busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Output ("ALREADY_RUNNING pid=" + $busy[0].OwningProcess)
    exit 0
}

$log = Join-Path $project "data\server.log"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null

Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $project `
    -WindowStyle Hidden `
    -RedirectStandardOutput $log `
    -RedirectStandardError (Join-Path $project "data\server.err.log")

# Wait for it to answer rather than just assuming it came up.
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest 'http://127.0.0.1:8000/api/status' -TimeoutSec 2 -UseBasicParsing
        if ($response.StatusCode -eq 200) {
            Write-Output "STARTED"
            exit 0
        }
    } catch {
        # not up yet
    }
}

Write-Output "FAILED_TO_START (see data\server.err.log)"
exit 1
