$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Force UTF-8 end-to-end (PowerShell logs + child process output).
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$PSDefaultParameterValues["Out-File:Encoding"] = "utf8"
$PSDefaultParameterValues["Set-Content:Encoding"] = "utf8"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

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

function Initialize-Utf8Log([string]$path) {
    try {
        # Write UTF-8 BOM so editors auto-detect encoding correctly.
        Set-Content -Path $path -Value "" -Encoding utf8 -NoNewline
        return $path
    }
    catch {
        # Fallback to timestamped file if the target log is locked.
        $dir = Split-Path -Path $path -Parent
        $name = [System.IO.Path]::GetFileNameWithoutExtension($path)
        $ext = [System.IO.Path]::GetExtension($path)
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $fallback = Join-Path $dir "$name-$stamp$ext"
        Set-Content -Path $fallback -Value "" -Encoding utf8 -NoNewline
        return $fallback
    }
}

try {
    # fresh UTF-8 log (prevents garbage text)
    Set-Content -Path $log -Value "" -Encoding utf8 -NoNewline

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

    # Ensure blog_db is running when present (used by local PostgreSQL setup).
    $blogRunning = (& docker ps --filter "name=^/blog_db$" --format "{{.Names}}" 2>$null | Select-Object -First 1)
    if ($blogRunning -ne "blog_db") {
        $blogExists = (& docker ps -a --filter "name=^/blog_db$" --format "{{.Names}}" 2>$null | Select-Object -First 1)
        if ($blogExists -eq "blog_db") {
            & docker start blog_db *> $null
            if ($LASTEXITCODE -ne 0) { throw "Failed to start blog_db." }
            Log "blog_db started"
        } else {
            Log "blog_db not found (skipping PostgreSQL container start)"
        }
    } else {
        Log "blog_db already running"
    }

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
        $djangoOut = Initialize-Utf8Log -path $djangoOut
        $djangoErr = Initialize-Utf8Log -path $djangoErr

        Log "Starting Django server"
        Log "Django stdout log: $djangoOut"
        Log "Django stderr log: $djangoErr"
        $cmdLine = 'set "PYTHONUTF8=1" && set "PYTHONIOENCODING=utf-8" && "' + $python + '" -X utf8 manage.py runserver 127.0.0.1:8000 --settings=edu.settings.local 1>>"' + $djangoOut + '" 2>>"' + $djangoErr + '"'
        Start-Process -FilePath "cmd.exe" `
            -WorkingDirectory $app `
            -ArgumentList @("/d","/s","/c",$cmdLine) `
            -WindowStyle Hidden
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

    if (-not $serverReady) { throw "Django did not bind to 127.0.0.1:8000. Check $djangoErr" }

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
