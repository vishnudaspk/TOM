<#
.SYNOPSIS
    Measure VRAM footprint of llama-server: baseline (stopped), loaded-idle, and peak during generation.

.DESCRIPTION
    Polls nvidia-smi every 100ms to capture VRAM usage at three stages:
    1. Baseline - server stopped
    2. Loaded-idle - server running, model loaded, no requests
    3. Peak during generation - streaming 500 tokens

    Re-measure whenever the model, --ctx-size, KV cache type, or server flags change.

.EXAMPLE
    .\measure_vram.ps1 -Confirm

.NOTES
    RTX 4060 8GB (8188 MiB usable), Qwen2.5-7B-Instruct Q4_K_M, ctx 4096, flash-attn on, KV q8_0
    Per-process VRAM is N/A under WDDM; uses total GPU memory delta.
    Stops ONLY the llama-server owning the target port (default 65400), NOT Graphify's :8080.
#>

#requires -RunAsAdministrator

param(
    [string]$ModelPath = "C:\AI\models\Qwen2.5-7B-Instruct\Qwen2.5-7B-Instruct-Q4_K_M.gguf",
    [int]$Port = 65400,
    [int]$CtxSize = 4096,
    [string]$Ngl = "99",
    [string]$FlashAttn = "auto",
    [string]$KvCacheTypeK = "q8_0",
    [string]$KvCacheTypeV = "q8_0",
    [int]$PollIntervalMs = 100,
    [int]$GenTokens = 500,
    [switch]$Confirm
)

$ErrorActionPreference = "Stop"

function Get-VramUsage {
    $out = nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv,noheader,nounits
    $parts = $out.Trim().Split(',')
    return @{
        Total = [int]$parts[0].Trim()
        Used  = [int]$parts[1].Trim()
        Free  = [int]$parts[2].Trim()
    }
}

function Get-PidByPort {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Established,Listen -ErrorAction SilentlyContinue
    if ($conn -and $conn.OwningProcess) {
        return $conn.OwningProcess[0]
    }
    return $null
}

function Stop-LlamaServerByPort {
    param([int]$Port, [switch]$Confirm)
    $pid = Get-PidByPort -Port $Port
    if (-not $pid) {
        Write-Host "No process found listening on port $Port"
        return
    }
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Host "Process $pid no longer exists"
        return
    }
    if ($Confirm) {
        $confirm = Read-Host "Stop llama-server (PID $pid, $($proc.ProcessName)) on port $Port? [y/N]"
        if ($confirm -notin 'y','Y') {
            Write-Host "Aborted"
            return
        }
    }
    Write-Host "Stopping llama-server (PID $pid) on port $Port..."
    $proc | Stop-Process -Force
    $proc | Wait-Process
    Write-Host "Stopped."
}

function Start-LlamaServer {
    param(
        [string]$ModelPath,
        [int]$Port,
        [int]$CtxSize,
        [string]$Ngl,
        [string]$FlashAttn,
        [string]$KvCacheTypeK,
        [string]$KvCacheTypeV
    )
    $args = @(
        "-m", $ModelPath,
        "--port", $Port,
        "-c", $CtxSize,
        "-ngl", $Ngl,
        "--flash-attn", $FlashAttn,
        "--cache-type-k", $KvCacheTypeK,
        "--cache-type-v", $KvCacheTypeV
    )
    $proc = Start-Process "llama-server.exe" -ArgumentList $args -PassThru -WorkingDirectory (Split-Path $ModelPath)
    return $proc
}

function Wait-ServerReady {
    param([int]$Port)
    $url = "http://127.0.0.1:$Port/v1/models"
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $resp = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 1
            if ($resp) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "Server did not become ready in time"
}

function Generate-Tokens {
    param([int]$Port, [int]$GenTokens, [string]$ModelPath)
    $url = "http://127.0.0.1:$Port/v1/chat/completions"
    $prompt = "Write a detailed story about a kangaroo that learns to code in Python. " * 20
    $body = @{
        model = (Get-Item $ModelPath).BaseName
        messages = @(@{ role = "user"; content = $prompt })
        max_tokens = $GenTokens
        temperature = 0.7
        stream = $true
    } | ConvertTo-Json -Depth 5

    $resp = Invoke-RestMethod -Uri $url -Method Post -Body $body -ContentType "application/json" -TimeoutSec 120
    return $resp
}

# ===== MAIN =====
Write-Host "=== VRAM Measurement Script ==="
Write-Host "Model: $ModelPath"
Write-Host "Port: $Port, ctx: $CtxSize, ngl: $Ngl, flash-attn: $FlashAttn, cache-type-k: $KvCacheTypeK, cache-type-v: $KvCacheTypeV"
Write-Host ""

# 0. ENSURE PORT IS FREE (stop any existing server on this port)
Write-Host "0. Ensuring port $Port is free..."
Stop-LlamaServerByPort -Port $Port -Confirm:$Confirm
Start-Sleep -Seconds 1

# 1. BASELINE (server stopped)
Write-Host "1. BASELINE (server stopped)"
$baseline = Get-VramUsage
Write-Host "   Used: $($baseline.Used) MiB"
Write-Host "   Free: $($baseline.Free) MiB"
Write-Host ""

# 2. START SERVER
Write-Host "2. Starting llama-server..."
$server = Start-LlamaServer -ModelPath $ModelPath -Port $Port -CtxSize $CtxSize -Ngl $Ngl -FlashAttn $FlashAttn -KvCacheTypeK $KvCacheTypeK -KvCacheTypeV $KvCacheTypeV
Write-Host "   PID: $($server.Id)"
Write-Host "   Waiting for server to be ready..."

Wait-ServerReady -Port $Port
Start-Sleep -Seconds 2  # Let it settle

# 3. LOADED-IDLE
Write-Host "3. LOADED-IDLE (server running, no requests)"
$loadedIdle = Get-VramUsage
$deltaIdle = $loadedIdle.Used - $baseline.Used
Write-Host "   Used: $($loadedIdle.Used) MiB"
Write-Host "   Free: $($loadedIdle.Free) MiB"
Write-Host "   Delta (loaded - baseline): $deltaIdle MiB"
Write-Host ""

# 4. PEAK DURING GENERATION
Write-Host "4. PEAK DURING GENERATION ($GenTokens tokens, streaming)"
Write-Host "   Starting VRAM polling (${PollIntervalMs}ms interval)..."

$samples = @()
$stopPoll = $false
$pollJob = Start-Job -ScriptBlock {
    param($intervalMs, [ref]$stopPoll, [ref]$samples)
    while (-not $stopPoll.Value) {
        $vram = Get-VramUsage
        $samples.Value += [pscustomobject]@{
            Time = [DateTime]::UtcNow
            Used = $vram.Used
        }
        Start-Sleep -Milliseconds $intervalMs
    }
} -ArgumentList $PollIntervalMs, ([ref]$stopPoll), ([ref]$samples)

# Give polling a moment to start
Start-Sleep -Milliseconds 200

# Generate tokens
Write-Host "   Generating $GenTokens tokens (streaming)..."
$startTime = Get-Date
try {
    $result = Generate-Tokens -Port $Port -GenTokens $GenTokens -ModelPath $ModelPath
    $genTime = (Get-Date) - $startTime
    Write-Host "   Generation completed in $($genTime.TotalSeconds.ToString('F1'))s"
} catch {
    Write-Host "   Generation error: $_"
} finally {
    $stopPoll = $true
    $pollJob | Wait-Job | Out-Null
    $pollJob | Receive-Job
    $pollJob | Remove-Job
}

# Analyze samples
if ($samples.Count -gt 0) {
    $peakUsed = ($samples | Measure-Object -Property Used -Maximum).Maximum
    $avgUsed  = ($samples | Measure-Object -Property Used -Average).Average
    $peakDelta = $peakUsed - $baseline.Used
    Write-Host "   Samples collected: $($samples.Count)"
    Write-Host "   Peak VRAM used: $peakUsed MiB"
    Write-Host "   Average VRAM used: $([math]::Round($avgUsed, 0)) MiB"
    Write-Host "   Peak delta (peak - baseline): $peakDelta MiB"
} else {
    Write-Host "   No samples collected!"
}

Write-Host ""
Write-Host "=== SUMMARY ==="
Write-Host "Baseline (server stopped):     $($baseline.Used) MiB used"
Write-Host "Loaded-idle (server running):  $($loadedIdle.Used) MiB used  (delta: $deltaIdle MiB)"
if ($samples.Count -gt 0) {
    Write-Host "Peak during generation:        $peakUsed MiB used  (delta: $peakDelta MiB)"
}

# 5. STOP SERVER
Write-Host ""
Write-Host "5. Stopping server..."
Stop-LlamaServerByPort -Port $Port -Confirm:$Confirm
Start-Sleep -Seconds 1

$afterStop = Get-VramUsage
Write-Host "After stop: $($afterStop.Used) MiB used"

# Output machine-readable summary for CI
$summary = @{
    baseline_used_mib    = $baseline.Used
    loaded_idle_used_mib = $loadedIdle.Used
    delta_idle_mib       = $deltaIdle
    peak_used_mib        = if ($samples.Count -gt 0) { $peakUsed } else { $null }
    peak_delta_mib       = if ($samples.Count -gt 0) { $peakDelta } else { $null }
    samples_count        = $samples.Count
    model_path           = $ModelPath
    port                 = $Port
    ctx_size             = $CtxSize
    ngl                  = $Ngl
    flash_attn           = $FlashAttn
    cache_type_k         = $KvCacheTypeK
    cache_type_v         = $KvCacheTypeV
    timestamp            = (Get-Date).ToString("o")
}
$summary | ConvertTo-Json -Depth 3 | Out-File "vram_measurement_$(Get-Date -Format 'yyyyMMdd_HHmmss').json"
Write-Host ""
Write-Host "JSON summary saved to vram_measurement_$(Get-Date -Format 'yyyyMMdd_HHmmss').json"