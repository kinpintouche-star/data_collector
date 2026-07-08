param(
    [string]$EnvFile = ".env",
    [switch]$EnablePriorityCollector
)

$ErrorActionPreference = "Stop"

function Read-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Env file not found: $Path"
    }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$name] = $value
    }
    return $values
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI 'gh' is not installed. Install it, authenticate with 'gh auth login', then rerun this script."
}

$envValues = Read-DotEnv -Path $EnvFile
$requiredSecrets = @()
$optionalSecrets = @(
    "LIVE_REMOTE_DATABASE_URL",
    "DATABENTO_API_KEY",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_ENDPOINT_URL",
    "MARKET_ARCHIVE_KEY"
)

foreach ($name in $requiredSecrets) {
    if (-not $envValues.ContainsKey($name) -or -not $envValues[$name]) {
        throw "$name is missing or empty in $EnvFile"
    }
}

foreach ($name in ($requiredSecrets + $optionalSecrets)) {
    if ($envValues.ContainsKey($name) -and $envValues[$name]) {
        $envValues[$name] | gh secret set $name
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to configure GitHub Actions secret: $name"
        }
        Write-Output "$name configured as GitHub secret."
    }
}

if ($EnablePriorityCollector) {
    gh variable set ENABLE_PRIORITY_COLLECTOR --body "true"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to configure GitHub Actions variable: ENABLE_PRIORITY_COLLECTOR"
    }
    Write-Output "ENABLE_PRIORITY_COLLECTOR configured as GitHub variable."
} else {
    Write-Output "Priority collector schedule left disabled. Rerun with -EnablePriorityCollector to enable hourly priority runs."
}
