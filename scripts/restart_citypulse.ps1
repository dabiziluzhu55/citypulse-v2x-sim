[CmdletBinding()]
param(
    [string]$QwenModelPath = 'D:\citypulse-models\Qwen2.5-0.5B-Instruct',
    [string]$QwenModelName = 'Qwen/Qwen2.5-0.5B-Instruct',
    [string]$EmbeddingModelPath = 'D:\citypulse-models\Qwen3-Embedding-0.6B',
    [switch]$RunSmokeTest
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
$frontendRoot = Join-Path $repoRoot 'frontend'
$runtimeRoot = Join-Path $repoRoot 'outputs\runtime'
$ragIndexManifest = Join-Path $repoRoot 'outputs\rag\chroma\index_manifest.json'
$qwenServer = Join-Path $repoRoot 'scripts\llm\qwen_transformers_server.py'
$tempRoot = Join-Path $repoRoot '.tmp-ai'
$runStamp = Get-Date -Format 'yyyyMMdd-HHmmss'

function Assert-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing: $Path"
    }
}

function Assert-Directory([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label is missing: $Path"
    }
}

function Stop-OwnedListener([int]$Port, [string]$CommandPattern, [string]$Label) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $processId = [int]$listener.OwningProcess
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
        $commandLine = [string]$processInfo.CommandLine
        if ($commandLine -notmatch $CommandPattern) {
            throw "Port $Port is owned by a non-CityPulse process; refusing to stop PID=$processId $commandLine"
        }
        Write-Host "Stopping ${Label}: PID=$processId port=$Port"
        Stop-Process -Id $processId -Force
    }

    $deadline = (Get-Date).AddSeconds(15)
    do {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "$Label did not release port $Port"
}

function Wait-Http([string]$Uri, [int]$TimeoutSeconds, [string]$Label) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                return $response
            }
        } catch {
            # A service may briefly refuse connections while loading models or maps.
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "$Label did not become ready within ${TimeoutSeconds}s: $Uri"
}

function Invoke-JsonPost([string]$Uri, [hashtable]$Body, [int]$TimeoutSeconds = 120) {
    $json = $Body | ConvertTo-Json -Depth 12 -Compress
    return Invoke-RestMethod `
        -Method Post `
        -Uri $Uri `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes($json)) `
        -TimeoutSec $TimeoutSeconds
}

function Invoke-CityPulseSmokeTest {
    $apiRoot = 'http://127.0.0.1:5173/api/v1'
    $simulationBody = @{
        scenario_preset_id = 'xiongan_20'
        period = 'morning_peak'
        origins = @{}
        window_start_seconds = 0
        duration_seconds = 30
        control_mode = 'fixed'
        seed = 47
        step_length = 0.05
        realtime = $true
        gui = $false
        snapshot_interval_seconds = 0.2
        disturbance_targets = @()
    }
    $created = Invoke-JsonPost -Uri "$apiRoot/simulations" -Body $simulationBody
    $sessionId = [string]$created.session_id
    if (-not $sessionId) {
        throw 'Simulation start did not return session_id'
    }

    try {
        $snapshot = $null
        $deadline = (Get-Date).AddSeconds(25)
        do {
            Start-Sleep -Milliseconds 500
            $snapshot = Invoke-RestMethod -Uri "$apiRoot/simulations/$sessionId" -TimeoutSec 10
            if ($snapshot.state -eq 'FAILED') {
                throw "Simulation failed: $($snapshot.error)"
            }
            if ($snapshot.vehicles.Count -gt 0) {
                break
            }
        } while ((Get-Date) -lt $deadline)
        if ($null -eq $snapshot -or $snapshot.vehicles.Count -eq 0) {
            throw 'Simulation produced no vehicle snapshot within 25 seconds'
        }

        $chatBody = @{
            message = 'You must call get_current_summary first, then briefly describe current traffic.'
            history = @()
            active_scope = 'intersection:demo_2'
        }
        $chat = Invoke-JsonPost `
            -Uri "$apiRoot/simulations/$sessionId/copilot/chat" `
            -Body $chatBody `
            -TimeoutSeconds 180
        if (-not [string]$chat.answer) {
            throw 'Copilot returned no answer'
        }
        if ($chat.tool_calls.Count -lt 1) {
            throw 'Copilot did not execute the expected traffic-state tool call'
        }
        Write-Host "Smoke test passed: session=$sessionId vehicles=$($snapshot.vehicles.Count) tools=$($chat.tool_calls.Count)"
    } finally {
        try {
            Invoke-JsonPost -Uri "$apiRoot/simulations/$sessionId/stop" -Body @{} -TimeoutSeconds 30 | Out-Null
        } catch {
            Write-Warning "Failed to stop smoke-test session: $($_.Exception.Message)"
        }
    }
}

Assert-File $pythonExe 'Project Python'
Assert-File $qwenServer 'Qwen server script'
Assert-File (Join-Path $QwenModelPath 'model.safetensors') 'Qwen weights'
Assert-File (Join-Path $EmbeddingModelPath 'model.safetensors') 'RAG embedding weights'
Assert-File $ragIndexManifest 'RAG index manifest'
Assert-Directory (Join-Path $frontendRoot 'node_modules') 'Frontend dependencies'
$npmCommand = Get-Command npm.cmd -ErrorAction Stop

New-Item -ItemType Directory -Force -Path $runtimeRoot, $tempRoot | Out-Null

# Match CityPulse commands exactly so unrelated processes are never terminated.
Stop-OwnedListener 5173 'vite(\.js)?' 'Frontend'
Stop-OwnedListener 8000 'backend\.app\.main:app' 'Backend'
Stop-OwnedListener 18000 'qwen_transformers_server\.py' 'Qwen'

$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:HF_HOME = 'D:\citypulse-hf-cache'
$env:CITYPULSE_QWEN_BASE_URL = 'http://127.0.0.1:18000/v1'
$env:CITYPULSE_QWEN_MODEL = $QwenModelName
$env:CITYPULSE_QWEN_TIMEOUT_SECONDS = '120'
$env:CITYPULSE_QWEN_MAX_TOKENS = '192'
$env:RAG_EMBEDDING_MODEL_PATH = $EmbeddingModelPath
$env:RAG_EMBEDDING_DEVICE = 'cpu'
$env:RAG_QUERY_TIMEOUT_SECONDS = '120'

$qwenStdout = Join-Path $runtimeRoot "qwen-$runStamp.stdout.log"
$qwenStderr = Join-Path $runtimeRoot "qwen-$runStamp.stderr.log"
Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @(
        $qwenServer,
        '--model-path', $QwenModelPath,
        '--served-model-name', $QwenModelName,
        '--host', '127.0.0.1',
        '--port', '18000',
        '--max-input-tokens', '2048'
    ) `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $qwenStdout `
    -RedirectStandardError $qwenStderr | Out-Null
Wait-Http 'http://127.0.0.1:18000/health' 120 'Qwen' | Out-Null
$qwenHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:18000/health' -TimeoutSec 10
if (-not $qwenHealth.model_loaded -or $qwenHealth.model -ne $QwenModelName) {
    throw "Qwen model state is invalid: $($qwenHealth | ConvertTo-Json -Compress)"
}

$backendStdout = Join-Path $runtimeRoot "backend-$runStamp.stdout.log"
$backendStderr = Join-Path $runtimeRoot "backend-$runStamp.stderr.log"
Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @('-m', 'uvicorn', 'backend.app.main:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1') `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendStdout `
    -RedirectStandardError $backendStderr | Out-Null
Wait-Http 'http://127.0.0.1:8000/api/v1/health' 120 'Backend' | Out-Null
$backendHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 10
if (
    $backendHealth.status -ne 'ok' `
    -or -not $backendHealth.simulation_manager_ready `
    -or -not $backendHealth.generated_artifacts_ready
) {
    throw "Backend dependencies are not ready: $($backendHealth | ConvertTo-Json -Compress)"
}

$frontendStdout = Join-Path $runtimeRoot "frontend-$runStamp.stdout.log"
$frontendStderr = Join-Path $runtimeRoot "frontend-$runStamp.stderr.log"
Start-Process `
    -FilePath $npmCommand.Source `
    -ArgumentList @('run', 'dev', '--', '--host', '0.0.0.0') `
    -WorkingDirectory $frontendRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $frontendStdout `
    -RedirectStandardError $frontendStderr | Out-Null
Wait-Http 'http://127.0.0.1:5173/' 60 'Frontend' | Out-Null
Wait-Http 'http://127.0.0.1:5173/api/v1/health' 30 'Frontend proxy' | Out-Null

if ($RunSmokeTest) {
    Invoke-CityPulseSmokeTest
}

Write-Host ''
Write-Host 'CityPulse services started successfully:'
Write-Host '  Frontend  http://127.0.0.1:5173'
Write-Host '  Backend   http://127.0.0.1:8000/docs'
Write-Host '  Qwen  http://127.0.0.1:18000/health'
Write-Host "  Logs      $runtimeRoot (*-$runStamp.*.log)"
