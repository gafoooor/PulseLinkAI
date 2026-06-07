# =============================================================================
# Step 4 - Fix mixed-content: wrap the ALB in CloudFront (HTTPS)
#
# Problem: Frontends are HTTPS (CloudFront) but backend is HTTP (ALB).
#          Browsers block HTTPS pages from making HTTP API calls.
#
# Fix:
#   1. Create a CloudFront distribution in front of the ALB (free HTTPS).
#   2. Rebuild all 3 frontends with the new HTTPS backend URL.
#   3. Update WEBHOOK_BASE_URL in ECS so Twilio uses the HTTPS URL too.
#
# Run from the PROJECT ROOT:  .\deploy\4_add_backend_https.ps1
# =============================================================================

. "$PSScriptRoot\config.ps1"

$ErrorActionPreference = "Continue"
$ROOT = Split-Path $PSScriptRoot -Parent
$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)

if (-not $ALB_DNS) {
    Write-Host "ERROR: ALB_DNS not set. Run 2_create_infra.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== PulseLink - Step 4: Add HTTPS to Backend ===" -ForegroundColor Cyan
Write-Host ""

# =============================================================================
# 1. CloudFront distribution in front of the ALB (no caching, all methods)
# =============================================================================
Write-Host "Creating CloudFront distribution for the backend ALB..."

$callerRef = "pulselink-api-$(Get-Date -Format 'yyyyMMddHHmmss')"

$cfJson = "{" +
  """Origins"":{""Quantity"":1,""Items"":[{""Id"":""alb-origin"",""DomainName"":""$ALB_DNS"",""CustomOriginConfig"":{""HTTPPort"":80,""HTTPSPort"":443,""OriginProtocolPolicy"":""http-only""}}]}," +
  """DefaultCacheBehavior"":{" +
    """ViewerProtocolPolicy"":""redirect-to-https""," +
    """TargetOriginId"":""alb-origin""," +
    """AllowedMethods"":{""Quantity"":7,""Items"":[""GET"",""HEAD"",""OPTIONS"",""PUT"",""POST"",""PATCH"",""DELETE""],""CachedMethods"":{""Quantity"":2,""Items"":[""GET"",""HEAD""]}}," +
    """ForwardedValues"":{""QueryString"":true,""Cookies"":{""Forward"":""all""},""Headers"":{""Quantity"":3,""Items"":[""Content-Type"",""Accept"",""Origin""]}}," +
    """MinTTL"":0,""DefaultTTL"":0,""MaxTTL"":0,""Compress"":false" +
  "}," +
  """Comment"":""PulseLink Backend API""," +
  """Enabled"":true," +
  """PriceClass"":""PriceClass_All""," +
  """CallerReference"":""$callerRef""" +
"}"

[System.IO.File]::WriteAllText("$env:TEMP\cf_backend.json", $cfJson)

$cfResult = aws cloudfront create-distribution `
    --distribution-config "file://$env:TEMP\cf_backend.json" `
    --output json | ConvertFrom-Json

$BACKEND_CF_DOMAIN = $cfResult.Distribution.DomainName
$API_HTTPS_URL = "https://$BACKEND_CF_DOMAIN"
Write-Host "  Backend CloudFront: $API_HTTPS_URL" -ForegroundColor Green
Write-Host "  (CloudFront propagates in 10-15 min; rebuilding frontends now)"

# =============================================================================
# 2. Rebuild all three frontends with the new HTTPS backend URL
# =============================================================================
Write-Host ""
Write-Host "Rebuilding frontends..."

function Rebuild-And-Upload {
    param([string]$AppDir, [string]$BucketSuffix, [hashtable]$EnvVars)

    $BucketName = "$APP_NAME-$BucketSuffix-$ACCOUNT_ID"
    $AppPath    = "$ROOT\frontend\$AppDir"

    Write-Host ""
    Write-Host "--- $AppDir ---" -ForegroundColor Yellow

    $envContent = ""
    foreach ($k in $EnvVars.Keys) { $envContent += "$k=$($EnvVars[$k])`n" }
    [System.IO.File]::WriteAllText("$AppPath\.env.production", $envContent)

    Push-Location $AppPath
    try {
        npm install --silent
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Build failed for $AppDir" }
    } finally {
        Pop-Location
        Remove-Item "$AppPath\.env.production" -ErrorAction SilentlyContinue
    }

    Write-Host "  Uploading to S3..."
    aws s3 sync "$AppPath\dist" "s3://$BucketName" `
        --delete --region $AWS_REGION --cache-control "max-age=86400" | Out-Null
    aws s3 cp "$AppPath\dist\index.html" "s3://$BucketName/index.html" `
        --cache-control "no-cache,no-store,must-revalidate" --region $AWS_REGION | Out-Null
    Write-Host "  Done." -ForegroundColor Green
}

Rebuild-And-Upload -AppDir "patient-app" -BucketSuffix "patient" -EnvVars @{
    VITE_API_BASE_URL = $API_HTTPS_URL
}
Rebuild-And-Upload -AppDir "donor-screen" -BucketSuffix "donor" -EnvVars @{
    VITE_API_BASE_URL = $API_HTTPS_URL
}
Rebuild-And-Upload -AppDir "coordinator-dashboard" -BucketSuffix "coord" -EnvVars @{
    VITE_API_BASE_URL    = $API_HTTPS_URL
    VITE_PATIENT_APP_URL = $PATIENT_CF
}

# =============================================================================
# 3. Update WEBHOOK_BASE_URL in ECS task to the HTTPS CloudFront URL
# =============================================================================
Write-Host ""
Write-Host "Updating WEBHOOK_BASE_URL in ECS task..."

$currentTask = aws ecs describe-task-definition `
    --task-definition "$APP_NAME-backend" `
    --region $AWS_REGION --output json | ConvertFrom-Json

$envList = @($currentTask.taskDefinition.containerDefinitions[0].environment |
    Where-Object { $_.name -ne "WEBHOOK_BASE_URL" })
$envList += @{ name = "WEBHOOK_BASE_URL"; value = $API_HTTPS_URL }

$containerDef = $currentTask.taskDefinition.containerDefinitions[0]
$containerDef.environment = $envList

$newTaskJson = @{
    family                  = $currentTask.taskDefinition.family
    networkMode             = $currentTask.taskDefinition.networkMode
    requiresCompatibilities = @("FARGATE")
    cpu                     = $currentTask.taskDefinition.cpu
    memory                  = $currentTask.taskDefinition.memory
    executionRoleArn        = $currentTask.taskDefinition.executionRoleArn
    containerDefinitions    = @($containerDef)
} | ConvertTo-Json -Depth 10 -Compress

[System.IO.File]::WriteAllText("$env:TEMP\task_https.json", $newTaskJson)

$newTask = aws ecs register-task-definition `
    --cli-input-json "file://$env:TEMP\task_https.json" `
    --region $AWS_REGION --output json | ConvertFrom-Json

aws ecs update-service `
    --cluster "$APP_NAME-cluster" `
    --service "$APP_NAME-service" `
    --task-definition $newTask.taskDefinition.taskDefinitionArn `
    --region $AWS_REGION --output json | Out-Null

Write-Host "  ECS service updated with HTTPS webhook URL."

# Save backend CF URL to config.ps1
$configPath = "$PSScriptRoot\config.ps1"
$cfg = Get-Content $configPath
if ($cfg -match '^\$BACKEND_CF') {
    $cfg = $cfg -replace '^\$BACKEND_CF\s*=.*', "`$BACKEND_CF   = `"$API_HTTPS_URL`""
} else {
    $cfg += "`n`$BACKEND_CF   = `"$API_HTTPS_URL`""
}
$cfg | Set-Content $configPath

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  HTTPS fix applied!" -ForegroundColor Green
Write-Host "============================================================"
Write-Host ""
Write-Host "  Backend HTTPS:  $API_HTTPS_URL"
Write-Host "  Backend HTTP:   http://$ALB_DNS  (for internal use only)"
Write-Host ""
Write-Host "  Frontends rebuilt with HTTPS backend URL."
Write-Host "  Hard-refresh (Ctrl+Shift+R) after 10-15 min for propagation."
Write-Host "============================================================" -ForegroundColor Green
