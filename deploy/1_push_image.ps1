# =============================================================================
# Step 1 — Build the backend Docker image and push it to Amazon ECR
#
# Prerequisites:
#   - AWS CLI v2 installed and configured  (aws configure)
#   - Docker Desktop running
#   - Run from the PROJECT ROOT:  .\deploy\1_push_image.ps1
# =============================================================================

. "$PSScriptRoot\config.ps1"

$ErrorActionPreference = "Stop"
$ROOT = Split-Path $PSScriptRoot -Parent

Write-Host ""
Write-Host "=== PulseLink - Step 1: Build and Push Docker Image ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. Get AWS account ID -----------------------------------------------
Write-Host "Getting AWS account ID..."
$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: AWS CLI not configured. Run: aws configure" -ForegroundColor Red
    exit 1
}
Write-Host "Account: $ACCOUNT_ID  Region: $AWS_REGION"

$ECR_BASE = "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
$REPO_NAME = "$APP_NAME-backend"
$IMAGE_URI = "$ECR_BASE/${REPO_NAME}:latest"

# --- 2. Create ECR repository (idempotent) --------------------------------
Write-Host ""
Write-Host "Creating ECR repository '$REPO_NAME' (skipped if it already exists)..."
aws ecr create-repository `
    --repository-name $REPO_NAME `
    --region $AWS_REGION `
    --image-scanning-configuration scanOnPush=true `
    2>&1 | Out-Null
Write-Host "ECR repository ready."

# --- 3. Docker login to ECR -----------------------------------------------
Write-Host ""
Write-Host "Logging Docker into ECR..."
$loginPwd = aws ecr get-login-password --region $AWS_REGION
$loginPwd | docker login --username AWS --password-stdin $ECR_BASE
if ($LASTEXITCODE -ne 0) { Write-Host "ECR login failed." -ForegroundColor Red; exit 1 }

# --- 4. Build Docker image ------------------------------------------------
Write-Host ""
Write-Host "Building Docker image (this takes 2-4 minutes on first build)..."
docker build -t "${REPO_NAME}:latest" $ROOT
if ($LASTEXITCODE -ne 0) { Write-Host "Docker build failed." -ForegroundColor Red; exit 1 }

# --- 5. Tag and push -------------------------------------------------------
Write-Host ""
Write-Host "Pushing image to ECR..."
docker tag "${REPO_NAME}:latest" $IMAGE_URI
docker push $IMAGE_URI
if ($LASTEXITCODE -ne 0) { Write-Host "Docker push failed." -ForegroundColor Red; exit 1 }

# --- 6. Save image URI to config ------------------------------------------
$configPath = "$PSScriptRoot\config.ps1"
(Get-Content $configPath) -replace '^\$ECR_IMAGE\s*=.*', "`$ECR_IMAGE    = `"$IMAGE_URI`"" |
    Set-Content $configPath

Write-Host ""
Write-Host "=== Step 1 DONE ===" -ForegroundColor Green
Write-Host "Image URI: $IMAGE_URI"
Write-Host ""
Write-Host "Next: run  .\deploy\2_create_infra.ps1"
