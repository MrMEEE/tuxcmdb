param(
    [string]$ServerUrl,
    [string]$AssetId,
    [string]$AssetName,
    [string]$ConfigPath = "$env:ProgramData\TuxCMDBAgent\config.json"
)

$ErrorActionPreference = "Stop"

function Ensure-Directory([string]$Path) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

function Get-OperatingSystem {
    return "windows"
}

function Read-Or-Register {
    param([string]$Path)

    Ensure-Directory -Path $Path
    if (Test-Path $Path) {
        $existing = Get-Content -Path $Path -Raw | ConvertFrom-Json
        if ($existing.server_url -and $existing.asset_id -and $existing.systempass) {
            return $existing
        }
    }

    if (-not $ServerUrl) {
        $ServerUrl = Read-Host "CMDB API URL (example: http://127.0.0.1:8080)"
    }
    if (-not $ServerUrl) {
        throw "Missing CMDB API URL"
    }

    $payload = @{}
    if ($AssetId) {
        $payload.asset_id = [int]$AssetId
    } else {
        if (-not $AssetName) {
            $AssetName = $env:COMPUTERNAME.ToLower()
        }
        $payload.assetname = $AssetName
    }

    $register = Invoke-RestMethod -Method Post -Uri "$($ServerUrl.TrimEnd('/'))/v1/agent/register" -ContentType "application/json" -Body ($payload | ConvertTo-Json)
    $config = [PSCustomObject]@{
        server_url = $ServerUrl.TrimEnd('/')
        asset_id = $register.id
        systempass = $register.systempass
    }
    $config | ConvertTo-Json | Set-Content -Path $Path -Encoding UTF8
    return $config
}

$config = Read-Or-Register -Path $ConfigPath
$bootstrapPayload = @{
    asset_id = $config.asset_id
    systempass = $config.systempass
    operating_system = Get-OperatingSystem
}

$bootstrap = Invoke-RestMethod -Method Post -Uri "$($config.server_url)/v1/agent/bootstrap" -ContentType "application/json" -Body ($bootstrapPayload | ConvertTo-Json)
if ([int]$bootstrap.approved -ne 2) {
    Write-Host "Asset not approved for reporting (state=$($bootstrap.approved)). Exiting."
    exit 0
}

$values = @(
    @{ attribute_name = "os"; value = (Get-OperatingSystem) }
)
foreach ($task in $bootstrap.tasks) {
    foreach ($command in $task.commands) {
        try {
            $result = powershell -NoProfile -NonInteractive -Command $command 2>&1 | Out-String
            if ($result) {
                $lines = $result -split "`r?`n"
                foreach ($line in $lines) {
                    $lineValue = $line.Trim()
                    if ($lineValue) {
                        $values += @{ attribute_name = $task.attribute_name; value = $lineValue }
                    }
                }
            }
        }
        catch {
        }
    }
}

if ($values.Count -eq 0) {
    Write-Host "No values to report"
    exit 0
}

$reportPayload = @{
    asset_id = $config.asset_id
    systempass = $config.systempass
    values = $values
}

Invoke-RestMethod -Method Post -Uri "$($config.server_url)/v1/agent/report" -ContentType "application/json" -Body ($reportPayload | ConvertTo-Json -Depth 5) | Out-Null
Write-Host "Reported $($values.Count) value(s)"
