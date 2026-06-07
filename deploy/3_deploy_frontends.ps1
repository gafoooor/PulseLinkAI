# =============================================================================
# Step 3 — Build the three React apps and deploy them to S3 + CloudFront
#
# Deployment order matters:
#   1. patient-app  (need its CF URL to embed in coordinator-dashboard links)
#   2. donor-screen
#   3. coordinator-dashboard (built last so it gets the patient-app CF URL)
#
# Run from the PROJECT ROOT:  .\deploy\3_deploy_frontends.ps1
# =============================================================================

. "$PSScriptRoot\config.ps1"

$ErrorActionPreference = "Continue"
$ROOT = Split-Path $PSScriptRoot -Parent

Write-Host ""
Write-Host "=== PulseLink - Step 3: Deploy Frontends to S3 + CloudFront ===" -ForegroundColor Cyan
Write-Host ""

if (-not $ALB_DNS) {
    Write-Host "ERROR: ALB_DNS not set. Run 2_create_infra.ps1 first." -ForegroundColor Red
    exit 1
}

$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$API_URL    = "http://$ALB_DNS"

# Helper: create an S3 static-website bucket, build app, upload, create CloudFront
function Deploy-Frontend {
    param(
        [string]$AppDir,
        [string]$BucketSuffix,
        [hashtable]$EnvVars
    )

    $BucketName = "$APP_NAME-$BucketSuffix-$ACCOUNT_ID"
    $AppPath    = "$ROOT\frontend\$AppDir"

    Write-Host ""
    Write-Host "--- Deploying $AppDir ---" -ForegroundColor Yellow

    # ---- S3 bucket (idempotent) ------------------------------------------
    Write-Host "  Creating S3 bucket '$BucketName'..."
    if ($AWS_REGION -eq "us-east-1") {
        aws s3api create-bucket --bucket $BucketName --region $AWS_REGION 2>&1 | Out-Null
    } else {
        aws s3api create-bucket --bucket $BucketName --region $AWS_REGION `
            --create-bucket-configuration LocationConstraint=$AWS_REGION 2>&1 | Out-Null
    }

    # Disable block-public-access
    aws s3api put-public-access-block --bucket $BucketName `
        --public-access-block-configuration `
        "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false" 2>&1 | Out-Null

    # Enable static website hosting
    aws s3api put-bucket-website --bucket $BucketName `
        --website-configuration '{\"IndexDocument\":{\"Suffix\":\"index.html\"},\"ErrorDocument\":{\"Key\":\"index.html\"}}' `
        --region $AWS_REGION 2>&1 | Out-Null

    # Public read bucket policy (write to file to avoid Windows quoting issues)
    $policyPath = "$env:TEMP\s3policy_$BucketSuffix.json"
    [System.IO.File]::WriteAllText($policyPath, "{""Version"":""2012-10-17"",""Statement"":[{""Effect"":""Allow"",""Principal"":""*"",""Action"":""s3:GetObject"",""Resource"":""arn:aws:s3:::$BucketName/*""}]}")
    aws s3api put-bucket-policy --bucket $BucketName --policy "file://$policyPath" --region $AWS_REGION | Out-Null
    Write-Host "  S3 bucket ready."

    # ---- Vite build with env vars ----------------------------------------
    Write-Host "  Building $AppDir..."
    Push-Location $AppPath
    try {
        # Write .env.production
        $envContent = ""
        foreach ($k in $EnvVars.Keys) {
            $envContent += "$k=$($EnvVars[$k])`n"
        }
        [System.IO.File]::WriteAllText("$AppPath\.env.production", $envContent)

        npm install --silent
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed for $AppDir" }
    } finally {
        Pop-Location
        Remove-Item "$AppPath\.env.production" -ErrorAction SilentlyContinue
    }
    Write-Host "  Build complete."

    # ---- Upload to S3 ----------------------------------------------------
    Write-Host "  Uploading to S3..."
    aws s3 sync "$AppPath\dist" "s3://$BucketName" `
        --delete `
        --region $AWS_REGION `
        --cache-control "max-age=86400" 2>&1 | Out-Null
    # index.html should not be cached (SPA routing)
    aws s3 cp "$AppPath\dist\index.html" "s3://$BucketName/index.html" `
        --cache-control "no-cache,no-store,must-revalidate" `
        --region $AWS_REGION 2>&1 | Out-Null
    Write-Host "  Uploaded to S3."

    # ---- CloudFront distribution -----------------------------------------
    Write-Host "  Creating CloudFront distribution (takes ~2 min)..."
    $WebsiteEndpoint = "$BucketName.s3-website.${AWS_REGION}.amazonaws.com"

    $cfConfig = @"
{
  "Origins": {
    "Quantity": 1,
    "Items": [{
      "Id": "S3-$BucketName",
      "DomainName": "$WebsiteEndpoint",
      "CustomOriginConfig": {
        "HTTPPort": 80, "HTTPSPort": 443,
        "OriginProtocolPolicy": "http-only"
      }
    }]
  },
  "DefaultCacheBehavior": {
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {"Quantity": 2, "Items": ["GET","HEAD"],"CachedMethods": {"Quantity": 2,"Items": ["GET","HEAD"]}},
    "ForwardedValues": {"QueryString": false,"Cookies": {"Forward": "none"}},
    "MinTTL": 0,"DefaultTTL": 86400,"MaxTTL": 31536000,
    "Compress": true,
    "TargetOriginId": "S3-$BucketName"
  },
  "CustomErrorResponses": {
    "Quantity": 1,
    "Items": [{"ErrorCode": 404,"ResponseCode": "200","ResponsePagePath": "/index.html","ErrorCachingMinTTL": 0}]
  },
  "DefaultRootObject": "index.html",
  "Comment": "PulseLink $AppDir",
  "Enabled": true,
  "PriceClass": "PriceClass_All",
  "CallerReference": "pulselink-$BucketSuffix-$(Get-Date -Format 'yyyyMMddHHmmss')"
}
"@

    [System.IO.File]::WriteAllText("$env:TEMP\cf_$BucketSuffix.json", $cfConfig)
    $cfResult = aws cloudfront create-distribution `
        --distribution-config "file://$env:TEMP\cf_$BucketSuffix.json" `
        --output json | ConvertFrom-Json

    $CF_DOMAIN = $cfResult.Distribution.DomainName
    $CF_ID     = $cfResult.Distribution.Id
    Write-Host "  CloudFront ID:     $CF_ID"
    Write-Host "  CloudFront domain: $CF_DOMAIN"

    return "https://$CF_DOMAIN"
}

# Disable S3 Block Public Access at the account level (required for public bucket policies)
Write-Host "Disabling S3 account-level block public access..."
aws s3control put-public-access-block `
    --account-id $ACCOUNT_ID `
    --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false" | Out-Null
Write-Host "  Done."

# ==========================================================================
# App 1: patient-app
# ==========================================================================
$PATIENT_CF_URL = Deploy-Frontend `
    -AppDir "patient-app" `
    -BucketSuffix "patient" `
    -EnvVars @{ VITE_API_BASE_URL = $API_URL }

Write-Host "  patient-app CF URL: $PATIENT_CF_URL" -ForegroundColor Green

# ==========================================================================
# App 2: donor-screen
# ==========================================================================
$DONOR_CF_URL = Deploy-Frontend `
    -AppDir "donor-screen" `
    -BucketSuffix "donor" `
    -EnvVars @{ VITE_API_BASE_URL = $API_URL }

Write-Host "  donor-screen CF URL: $DONOR_CF_URL" -ForegroundColor Green

# ==========================================================================
# App 3: coordinator-dashboard (built last — knows patient app CF URL)
# ==========================================================================
$COORD_CF_URL = Deploy-Frontend `
    -AppDir "coordinator-dashboard" `
    -BucketSuffix "coord" `
    -EnvVars @{
        VITE_API_BASE_URL    = $API_URL
        VITE_PATIENT_APP_URL = $PATIENT_CF_URL
    }

Write-Host "  coordinator CF URL: $COORD_CF_URL" -ForegroundColor Green

# ==========================================================================
# Save CF URLs to config
# ==========================================================================
$configPath = "$PSScriptRoot\config.ps1"
$cfg = Get-Content $configPath
$cfg = $cfg -replace '^\$PATIENT_CF\s*=.*', "`$PATIENT_CF   = `"$PATIENT_CF_URL`""
$cfg = $cfg -replace '^\$DONOR_CF\s*=.*',   "`$DONOR_CF     = `"$DONOR_CF_URL`""
$cfg = $cfg -replace '^\$COORD_CF\s*=.*',   "`$COORD_CF     = `"$COORD_CF_URL`""
$cfg | Set-Content $configPath

# ==========================================================================
# CORS update in ECS task definition — add CF origins
# ==========================================================================
Write-Host ""
Write-Host "Updating ECS task with CloudFront CORS origins..."
$CORS_ORIGINS = "$PATIENT_CF_URL,$DONOR_CF_URL,$COORD_CF_URL,http://localhost:5173,http://localhost:5174,http://localhost:5175"

# Get current task definition
$currentTask = aws ecs describe-task-definition `
    --task-definition "$APP_NAME-backend" `
    --region $AWS_REGION `
    --output json | ConvertFrom-Json

$envList = $currentTask.taskDefinition.containerDefinitions[0].environment
# Remove existing CORS_ORIGINS entry if present
$envList = @($envList | Where-Object { $_.name -ne "CORS_ORIGINS" })
$envList += @{ name = "CORS_ORIGINS"; value = $CORS_ORIGINS }

# Convert back to JSON for AWS CLI
$updatedEnvJson = $envList | ConvertTo-Json -Compress
$containerDef = $currentTask.taskDefinition.containerDefinitions[0]
$containerDef.environment = $envList

$containerDef | ConvertTo-Json -Depth 10 -Compress | ForEach-Object { [System.IO.File]::WriteAllText("$env:TEMP\updated_container.json", $_) }

$newTaskDef = @{
    family                  = $currentTask.taskDefinition.family
    networkMode             = $currentTask.taskDefinition.networkMode
    requiresCompatibilities = @("FARGATE")
    cpu                     = $currentTask.taskDefinition.cpu
    memory                  = $currentTask.taskDefinition.memory
    executionRoleArn        = $currentTask.taskDefinition.executionRoleArn
    containerDefinitions    = @($containerDef)
} | ConvertTo-Json -Depth 10 -Compress
[System.IO.File]::WriteAllText("$env:TEMP\new_task_def.json", $newTaskDef)

$newTaskResult = aws ecs register-task-definition `
    --cli-input-json "file://$env:TEMP\new_task_def.json" `
    --region $AWS_REGION `
    --output json | ConvertFrom-Json

$newRevision = $newTaskResult.taskDefinition.taskDefinitionArn

# Update the ECS service to use the new task revision
aws ecs update-service `
    --cluster "$APP_NAME-cluster" `
    --service "$APP_NAME-service" `
    --task-definition $newRevision `
    --region $AWS_REGION `
    --output json | Out-Null

Write-Host "  ECS service updated with CORS origins. Deploying new task..."

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  PulseLink is LIVE on AWS!" -ForegroundColor Green
Write-Host "============================================================"
Write-Host ""
Write-Host "  Backend API:         http://$ALB_DNS"
Write-Host "  API Docs:            http://$ALB_DNS/docs"
Write-Host "  Patient App:         $PATIENT_CF_URL"
Write-Host "  Donor Screen:        $DONOR_CF_URL"
Write-Host "  Coordinator:         $COORD_CF_URL"
Write-Host ""
Write-Host "  Twilio webhook base: http://$ALB_DNS"
Write-Host "  (WEBHOOK_BASE_URL is already set in the ECS task - no ngrok needed!)"
Write-Host ""
Write-Host "  NOTE: CloudFront distributions take 10-15 min to fully propagate."
Write-Host "  The backend ALB is immediately reachable."
Write-Host "============================================================" -ForegroundColor Green
