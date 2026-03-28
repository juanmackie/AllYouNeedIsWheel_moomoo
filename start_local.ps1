Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-PythonLauncher {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @Arguments
        return
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python @Arguments
        return
    }

    throw 'Python 3 was not found. Install Python 3 and try again.'
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMs = 1200
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $asyncResult = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $asyncResult.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }

        $client.EndConnect($asyncResult)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-ForHealthEndpoint {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 3
            if ($response.status -eq 'healthy') {
                return $true
            }
        } catch {
        }

        Start-Sleep -Seconds 1
    }

    return $false
}

function Get-OpenDExecutablePath {
    param(
        [string]$ConfiguredPath
    )

    $candidates = @(
        $ConfiguredPath,
        (Join-Path $env:LOCALAPPDATA 'Programs\OpenD\OpenD.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\OpenD\FutuOpenD.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\moomoo\OpenD\OpenD.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\moomoo\OpenD\FutuOpenD.exe'),
        (Join-Path $env:ProgramFiles 'OpenD\OpenD.exe'),
        (Join-Path $env:ProgramFiles 'OpenD\FutuOpenD.exe'),
        (Join-Path $env:ProgramFiles 'moomoo\OpenD\OpenD.exe'),
        (Join-Path $env:ProgramFiles 'moomoo\OpenD\FutuOpenD.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'OpenD\OpenD.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'OpenD\FutuOpenD.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'moomoo\OpenD\OpenD.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'moomoo\OpenD\FutuOpenD.exe')
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    return $null
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

Write-Host ''
Write-Host '== All You Need Is Wheel local launcher ==' -ForegroundColor Cyan

$venvDir = Join-Path $repoRoot '.venv'
$pythonExe = Join-Path $venvDir 'Scripts\python.exe'
$requirementsFile = Join-Path $repoRoot 'requirements.txt'
$requirementsStamp = Join-Path $venvDir 'requirements.sha256'

if (-not (Test-Path $pythonExe)) {
    Write-Host 'Creating local virtual environment...' -ForegroundColor Yellow
    Invoke-PythonLauncher -Arguments @('-m', 'venv', '.venv')
}

$requirementsHash = (Get-FileHash $requirementsFile -Algorithm SHA256).Hash
$needsInstall = -not (Test-Path $requirementsStamp)

if (-not $needsInstall) {
    $savedHash = (Get-Content $requirementsStamp -Raw).Trim()
    $needsInstall = $savedHash -ne $requirementsHash
}

if (-not $needsInstall) {
    try {
        & $pythonExe -c "import flask, waitress, dotenv" | Out-Null
    } catch {
        $needsInstall = $true
    }
}

if ($needsInstall) {
    Write-Host 'Installing Python dependencies...' -ForegroundColor Yellow
    & $pythonExe -m pip install -r $requirementsFile
    Set-Content -Path $requirementsStamp -Value $requirementsHash -NoNewline
}

$connectionPath = Join-Path $repoRoot 'connection.json'
$connectionExamplePath = Join-Path $repoRoot 'connection.json.example'

if (-not (Test-Path $connectionPath)) {
    Copy-Item $connectionExamplePath $connectionPath
    Write-Host 'Created connection.json from the example file.' -ForegroundColor Green
}

$connectionConfig = Get-Content $connectionPath -Raw | ConvertFrom-Json
$openDHost = if ($connectionConfig.host) { [string]$connectionConfig.host } else { '127.0.0.1' }
$openDPort = if ($connectionConfig.port) { [int]$connectionConfig.port } else { 11111 }
$autoLaunchOpenD = $false
if ($connectionConfig.PSObject.Properties.Name -contains 'auto_launch_opend') {
    $autoLaunchOpenD = [bool]$connectionConfig.auto_launch_opend
}

$openDPath = ''
if ($connectionConfig.PSObject.Properties.Name -contains 'opend_path') {
    $openDPath = [string]$connectionConfig.opend_path
}

if (($openDHost -eq '127.0.0.1' -or $openDHost -eq 'localhost') -and -not (Test-TcpPort -HostName $openDHost -Port $openDPort)) {
    $detectedOpenDPath = Get-OpenDExecutablePath -ConfiguredPath $openDPath
    if ($autoLaunchOpenD -and $detectedOpenDPath) {
        Write-Host 'Starting OpenD...' -ForegroundColor Yellow
        Start-Process -FilePath $detectedOpenDPath | Out-Null
        Start-Sleep -Seconds 2
    } else {
        Write-Host "OpenD is not reachable on ${openDHost}:${openDPort}." -ForegroundColor Yellow
        if (-not $detectedOpenDPath) {
            Write-Host 'Tip: set "auto_launch_opend": true and "opend_path" in connection.json to open OpenD automatically.' -ForegroundColor DarkYellow
        }
    }
}

$healthUrl = 'http://127.0.0.1:8000/health'
$appUrl = 'http://127.0.0.1:8000/'

$appIsRunning = $false
try {
    $healthResponse = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 3
    $appIsRunning = $healthResponse.status -eq 'healthy'
} catch {
    $appIsRunning = $false
}

if (-not $appIsRunning) {
    Write-Host 'Starting the local web app...' -ForegroundColor Yellow
    $serverCommand = "Set-Location '$repoRoot'; `$env:CONNECTION_CONFIG='connection.json'; & '$pythonExe' 'run_api.py'"
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $repoRoot -ArgumentList @(
        '-NoExit',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-Command', $serverCommand
    ) | Out-Null

    if (-not (Wait-ForHealthEndpoint -Url $healthUrl -TimeoutSeconds 60)) {
        throw 'The local web app did not finish starting within 60 seconds.'
    }
}

Write-Host 'Opening the dashboard in your browser...' -ForegroundColor Green
Start-Process $appUrl | Out-Null

Write-Host ''
Write-Host 'Ready:' -ForegroundColor Cyan
Write-Host ' - Browser opened at http://127.0.0.1:8000/'
Write-Host ' - If OpenD is open but not logged in, sign in there and return to the app.'
Write-Host ' - Leave the server PowerShell window open while you use the app.'
Write-Host ''
