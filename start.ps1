$ErrorActionPreference = "Stop"

$root = "C:\Users\hi\Downloads\webdev\Django_Projects\e-learning"
$app = Join-Path $root "edu"
$python = Join-Path $root "env\educa\Scripts\python.exe"
$manage = Join-Path $app "manage.py"
$url = "http://127.0.0.1:8000"
$log = Join-Path $root "start.log"
$djangoOut = Join-Path $root "django-out.log"
$djangoErr = Join-Path $root "django-err.log"

function Log([string]$m) {
    "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m | Tee-Object -FilePath $log -Append
}

try {
    Set-Content -Path $log -Value ""
    Log "Launcher started"

    if (-not (Test-Path $python)) { throw "Python not found: $python" }
    if (-not (Test-Path $manage)) { throw "manage.py not found: $manage" }

    # Start Docker Desktop if needed
    if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
        $dockerExe1 = "$Env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
        $dockerExe2 = "${Env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe"

        if (Test-Path $dockerExe1) {
            Start-Process $dockerExe1
            Log "Starting Docker Desktop"
        } elseif (Test-Path $dockerExe2) {
            Start-Process $dockerExe2
            Log "Starting Docker Desktop"
        } else {
            throw "Docker Desktop executable not found."
        }
    }

    # Wait for Docker engine
    $dockerReady = $false
    for ($i = 0; $i -lt 90; $i++) {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
        Start-Sleep 2
    }
    if (-not $dockerReady) { throw "Docker engine not ready in time." }
    Log "Docker ready"

    # Ensure redis container is running (null-safe)
    $redisRunning = docker ps --filter "name=^/redis_educa$" --format "{{.Names}}" 2>$null | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($redisRunning) -or $redisRunning.Trim() -ne "redis_educa") {
        $redisExists = docker ps -a --filter "name=^/redis_educa$" --format "{{.Names}}" 2>$null | Select-Object -First 1
        if (-not [string]::IsNullOrWhiteSpace($redisExists) -and $redisExists.Trim() -eq "redis_educa") {
            docker rm -f redis_educa *> $null
        }

        docker run -d --rm --name redis_educa -p 6379:6379 redis:latest *> $null
        if ($LASTEXITCODE -ne 0) { throw "Failed to start redis_educa." }
        Log "Redis started"
    } else {
        Log "Redis already running"
    }

    # Start Django if not already listening
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

    # Wait for Django to bind to 8000
    $serverReady = $false
    for ($i = 0; $i -lt 45; $i++) {
        if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
            $serverReady = $true
            break
        }
        Start-Sleep 1
    }

    if (-not $serverReady) {
        throw "Django did not bind to 127.0.0.1:8000. Check django-err.log"
    }

    Start-Process $url
    Log "Browser opened: $url"
}
catch {
    Log ("ERROR: " + $_.Exception.Message)
    Start-Process notepad.exe $log
}
