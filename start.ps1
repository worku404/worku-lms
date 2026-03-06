$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = "C:\Users\hi\Downloads\webdev\Django_Projects\e-learning"
$app = Join-Path $root "edu"
$python = Join-Path $root "env\educa\Scripts\python.exe"
$manage = Join-Path $app "manage.py"
$url = "http://127.0.0.1:8000"
$log = Join-Path $root "start.log"
$djangoOut = Join-Path $root "django-out.log"
$djangoErr = Join-Path $root "django-err.log"
$lockFile = Join-Path $root ".start.lock"

function Log([string]$m) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    $line | Out-File -FilePath $log -Append -Encoding utf8
    Write-Output $line
}

try {
    # fresh UTF-8 log (prevents garbage text)
    Set-Content -Path $log -Value "" -Encoding utf8

    # simple lock to avoid double-click race
    if (Test-Path $lockFile) {
        $age = (Get-Date) - (Get-Item $lockFile).LastWriteTime
        if ($age.TotalMinutes -lt 10) {
            Log "Another launcher instance is already running. Exiting."
            exit 0
        }
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    }
    New-Item -Path $lockFile -ItemType File -Force | Out-Null

    Log "Launcher started"

    if (-not (Test-Path $python)) { throw "Python not found: $python" }
    if (-not (Test-Path $manage)) { throw "manage.py not found: $manage" }

    # Use your exact Docker Desktop path
    if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
        $dockerExe = "$Env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
        if (-not (Test-Path $dockerExe)) { throw "Docker Desktop executable not found: $dockerExe" }
        Start-Process $dockerExe
        Log "Starting Docker Desktop"
    }

    # Wait for Docker engine (up to 240s)
    $dockerReady = $false
    for ($i = 0; $i -lt 120; $i++) {
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
        Start-Sleep 2
    }
    if (-not $dockerReady) { throw "Docker engine not ready in time (240s)." }
    Log "Docker ready"

    # Ensure redis_educa is running
    $redisRunning = (& docker ps --filter "name=^/redis_educa$" --format "{{.Names}}" 2>$null | Select-Object -First 1)
    if ($redisRunning -ne "redis_educa") {
        $redisExists = (& docker ps -a --filter "name=^/redis_educa$" --format "{{.Names}}" 2>$null | Select-Object -First 1)
        if ($redisExists -eq "redis_educa") {
            & docker rm -f redis_educa *> $null
        }

        & docker run -d --rm --name redis_educa -p 6379:6379 redis:latest *> $null
        if ($LASTEXITCODE -ne 0) {
            # one retry if name conflict/race
            & docker rm -f redis_educa *> $null
            & docker run -d --rm --name redis_educa -p 6379:6379 redis:latest *> $null
            if ($LASTEXITCODE -ne 0) { throw "Failed to start redis_educa." }
        }
        Log "Redis started"
    } else {
        Log "Redis already running"
    }

    # Start Django if needed
    if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
        if (Test-Path $djangoOut) { Remove-Item $djangoOut -Force }
        if (Test-Path $djangoErr) { Remove-Item $djangoErr -Force }

        Log "Starting Django server"
        Start-Process -FilePath $python `
            -WorkingDirectory $app `
            -ArgumentList @("manage.py","runserver","127.0.0.1:8000","--settings=edu.settings.local") `
            -WindowStyle Hidden `
            -RedirectStandardOutput $djangoOut `
            -RedirectStandardError $djangoErr
    } else {
        Log "Django already running"
    }

    # Wait for Django
    $serverReady = $false
    for ($i = 0; $i -lt 60; $i++) {
        if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
            $serverReady = $true
            break
        }
        Start-Sleep 1
    }

    if (-not $serverReady) { throw "Django did not bind to 127.0.0.1:8000. Check django-err.log" }

    Start-Process $url
    Log "Browser opened: $url"
}
catch {
    Log ("ERROR: " + $_.Exception.Message)
    Start-Process notepad.exe $log
}
finally {
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}
