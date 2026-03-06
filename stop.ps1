$ErrorActionPreference = "SilentlyContinue"
$stopDockerDesktop = $false

$root = "C:\Users\hi\Downloads\webdev\Django_Projects\e-learning"
$stopDockerDesktop = $false   # set to $true if you want to fully close Docker Desktop too

# Stop Django runserver started from this project
$djangoProcIds = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "manage\.py\s+runserver" -and
        $_.CommandLine -like "*$root*"
    } |
    Select-Object -ExpandProperty ProcessId -Unique

foreach ($procId in $djangoProcIds) {
    Stop-Process -Id $procId -Force
}

# Fallback: stop process listening on :8000
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) {
    Stop-Process -Id $l.OwningProcess -Force
}

# Stop/remove Redis container (if Docker is up)
docker info *> $null
if ($LASTEXITCODE -eq 0) {
    docker rm -f redis_educa *> $null
    # Stop local PostgreSQL container if running (keep container/data intact).
    docker stop blog_db *> $null
}

# Optional full Docker shutdown
if ($stopDockerDesktop) {
    Get-Process -Name "Docker Desktop", "com.docker.backend", "com.docker.proxy", "vpnkit" -ErrorAction SilentlyContinue |
        Stop-Process -Force
}
