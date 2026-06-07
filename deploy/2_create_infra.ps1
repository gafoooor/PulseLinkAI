# =============================================================================
# Step 2 — Create AWS infrastructure:
#   ECS Fargate cluster, ALB, Security Groups, IAM roles, ECS service
#
# Run from the PROJECT ROOT:  .\deploy\2_create_infra.ps1
# =============================================================================

. "$PSScriptRoot\config.ps1"

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "=== PulseLink - Step 2: Create AWS Infrastructure ===" -ForegroundColor Cyan
Write-Host ""

if (-not $ECR_IMAGE) {
    Write-Host "ERROR: ECR_IMAGE not set. Run 1_push_image.ps1 first." -ForegroundColor Red
    exit 1
}

$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$CLUSTER   = "$APP_NAME-cluster"
$SVC_NAME  = "$APP_NAME-service"
$LOG_GROUP = "/ecs/$APP_NAME"
$EXEC_ROLE = "${APP_NAME}EcsTaskExecutionRole"
$ALB_NAME  = "$APP_NAME-alb"
$TG_NAME   = "$APP_NAME-tg"
$TASK_FAM  = "$APP_NAME-backend"

# --------------------------------------------------------------------------
# 1. ECS Cluster
# --------------------------------------------------------------------------
Write-Host "Creating ECS cluster '$CLUSTER'..."
aws ecs create-cluster --cluster-name $CLUSTER --region $AWS_REGION 2>&1 | Out-Null
Write-Host "  Done."

# --------------------------------------------------------------------------
# 2. CloudWatch log group
# --------------------------------------------------------------------------
Write-Host "Creating CloudWatch log group '$LOG_GROUP'..."
aws logs create-log-group --log-group-name $LOG_GROUP --region $AWS_REGION 2>&1 | Out-Null
Write-Host "  Done."

# --------------------------------------------------------------------------
# 3. IAM Task Execution Role
# --------------------------------------------------------------------------
Write-Host "Creating IAM task execution role '$EXEC_ROLE'..."
$trustPolicyPath = "$env:TEMP\pulselink_trust.json"
[System.IO.File]::WriteAllText($trustPolicyPath, '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}')

# Create role (ignore error if it already exists)
aws iam create-role `
    --role-name $EXEC_ROLE `
    --assume-role-policy-document "file://$trustPolicyPath" `
    --output json | Out-Null

# Attach the AWS-managed execution policy
aws iam attach-role-policy `
    --role-name $EXEC_ROLE `
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy | Out-Null

$EXEC_ROLE_ARN = "arn:aws:iam::${ACCOUNT_ID}:role/$EXEC_ROLE"
Write-Host "  Role ARN: $EXEC_ROLE_ARN"

# --------------------------------------------------------------------------
# 4. Discover default VPC and public subnets
# --------------------------------------------------------------------------
Write-Host "Discovering default VPC and subnets..."
$VPC_ID = (aws ec2 describe-vpcs `
    --filters Name=isDefault,Values=true `
    --query "Vpcs[0].VpcId" `
    --output text `
    --region $AWS_REGION)

$SUBNETS_JSON = aws ec2 describe-subnets `
    --filters "Name=vpc-id,Values=$VPC_ID" `
    --query "Subnets[?MapPublicIpOnLaunch==``true``].SubnetId" `
    --output json `
    --region $AWS_REGION

$SUBNET_LIST = ($SUBNETS_JSON | ConvertFrom-Json) -join ","
$SUBNET_ARR  = ($SUBNETS_JSON | ConvertFrom-Json)

Write-Host "  VPC: $VPC_ID"
Write-Host "  Public subnets: $SUBNET_LIST"

# --------------------------------------------------------------------------
# 5. Security group — ALB (inbound HTTP 80 from anywhere)
# --------------------------------------------------------------------------
Write-Host "Creating ALB security group..."
$ALB_SG_JSON = aws ec2 create-security-group `
    --group-name "$APP_NAME-alb-sg" `
    --description "PulseLink ALB - allow HTTP 80 inbound" `
    --vpc-id $VPC_ID `
    --region $AWS_REGION `
    --output json 2>&1

if ($LASTEXITCODE -ne 0) {
    # Already exists — look it up
    $ALB_SG_ID = (aws ec2 describe-security-groups `
        --filters "Name=group-name,Values=$APP_NAME-alb-sg" "Name=vpc-id,Values=$VPC_ID" `
        --query "SecurityGroups[0].GroupId" --output text --region $AWS_REGION)
} else {
    $ALB_SG_ID = ($ALB_SG_JSON | ConvertFrom-Json).GroupId
    # Allow inbound HTTP
    aws ec2 authorize-security-group-ingress `
        --group-id $ALB_SG_ID `
        --protocol tcp --port 80 --cidr 0.0.0.0/0 `
        --region $AWS_REGION 2>&1 | Out-Null
}
Write-Host "  ALB SG: $ALB_SG_ID"

# --------------------------------------------------------------------------
# 6. Security group — ECS tasks (inbound 8000 from ALB SG only)
# --------------------------------------------------------------------------
Write-Host "Creating ECS task security group..."
$ECS_SG_JSON = aws ec2 create-security-group `
    --group-name "$APP_NAME-ecs-sg" `
    --description "PulseLink ECS - allow 8000 from ALB only" `
    --vpc-id $VPC_ID `
    --region $AWS_REGION `
    --output json 2>&1

if ($LASTEXITCODE -ne 0) {
    $ECS_SG_ID = (aws ec2 describe-security-groups `
        --filters "Name=group-name,Values=$APP_NAME-ecs-sg" "Name=vpc-id,Values=$VPC_ID" `
        --query "SecurityGroups[0].GroupId" --output text --region $AWS_REGION)
} else {
    $ECS_SG_ID = ($ECS_SG_JSON | ConvertFrom-Json).GroupId
    aws ec2 authorize-security-group-ingress `
        --group-id $ECS_SG_ID `
        --protocol tcp --port 8000 `
        --source-group $ALB_SG_ID `
        --region $AWS_REGION 2>&1 | Out-Null
}
Write-Host "  ECS SG: $ECS_SG_ID"

# --------------------------------------------------------------------------
# 7. Application Load Balancer
# --------------------------------------------------------------------------
Write-Host "Creating Application Load Balancer '$ALB_NAME' (takes ~2 min)..."
$ALB_JSON = aws elbv2 create-load-balancer `
    --name $ALB_NAME `
    --subnets $SUBNET_ARR `
    --security-groups $ALB_SG_ID `
    --scheme internet-facing `
    --type application `
    --ip-address-type ipv4 `
    --region $AWS_REGION `
    --output json
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ALB already exists - looking it up..."
    $ALB_JSON = aws elbv2 describe-load-balancers --names $ALB_NAME --region $AWS_REGION --output json
}
$ALB_ARN = ($ALB_JSON | ConvertFrom-Json).LoadBalancers[0].LoadBalancerArn
$ALB_DNS_NAME = ($ALB_JSON | ConvertFrom-Json).LoadBalancers[0].DNSName
Write-Host "  ALB ARN: $ALB_ARN"
Write-Host "  ALB DNS: $ALB_DNS_NAME"

# --------------------------------------------------------------------------
# 8. Target Group (HTTP, port 8000, /health)
# --------------------------------------------------------------------------
Write-Host "Creating target group '$TG_NAME'..."
$TG_JSON = aws elbv2 create-target-group `
    --name $TG_NAME `
    --protocol HTTP `
    --port 8000 `
    --vpc-id $VPC_ID `
    --target-type ip `
    --health-check-path /health `
    --health-check-interval-seconds 30 `
    --healthy-threshold-count 2 `
    --unhealthy-threshold-count 3 `
    --region $AWS_REGION `
    --output json
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Target group already exists - looking it up..."
    $TG_JSON = aws elbv2 describe-target-groups --names $TG_NAME --region $AWS_REGION --output json
}
$TG_ARN = ($TG_JSON | ConvertFrom-Json).TargetGroups[0].TargetGroupArn
Write-Host "  TG ARN: $TG_ARN"

# --------------------------------------------------------------------------
# 9. ALB Listener (port 80 → target group)
# --------------------------------------------------------------------------
Write-Host "Creating ALB listener..."
aws elbv2 create-listener `
    --load-balancer-arn $ALB_ARN `
    --protocol HTTP --port 80 `
    --default-actions Type=forward,TargetGroupArn=$TG_ARN `
    --region $AWS_REGION `
    --output json | Out-Null
Write-Host "  Listener created."

# --------------------------------------------------------------------------
# 10. ECS Task Definition
# --------------------------------------------------------------------------
Write-Host "Registering ECS task definition '$TASK_FAM'..."

$taskDef = @"
{
  "family": "$TASK_FAM",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "$EXEC_ROLE_ARN",
  "containerDefinitions": [
    {
      "name": "$APP_NAME",
      "image": "$ECR_IMAGE",
      "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
      "essential": true,
      "environment": [
        {"name": "LLM_PROVIDER",              "value": "mock"},
        {"name": "MESSAGE_CHANNEL",           "value": "mock"},
        {"name": "VOICE_PROVIDER",            "value": "mock"},
        {"name": "EVENT_BUS",                 "value": "memory"},
        {"name": "DATASET_CSV_PATH",          "value": "/app/Dataset.csv"},
        {"name": "TWILIO_ACCOUNT_SID",        "value": "$TWILIO_ACCOUNT_SID"},
        {"name": "TWILIO_AUTH_TOKEN",         "value": "$TWILIO_AUTH_TOKEN"},
        {"name": "TWILIO_PHONE_NUMBER",       "value": "$TWILIO_PHONE_NUMBER"},
        {"name": "TWILIO_WHATSAPP_FROM",      "value": "$TWILIO_WHATSAPP_FROM"},
        {"name": "TWILIO_TEST_DONOR_PHONE",   "value": "$TWILIO_TEST_DONOR_PHONE"},
        {"name": "TWILIO_COORDINATOR_PHONE",  "value": "$TWILIO_COORDINATOR_PHONE"},
        {"name": "WEBHOOK_BASE_URL",          "value": "http://$ALB_DNS_NAME"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "$LOG_GROUP",
          "awslogs-region": "$AWS_REGION",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
        "interval": 30,
        "timeout": 10,
        "retries": 3,
        "startPeriod": 90
      }
    }
  ]
}
"@

[System.IO.File]::WriteAllText("$env:TEMP\pulselink_task_def.json", $taskDef)
aws ecs register-task-definition `
    --cli-input-json "file://$env:TEMP\pulselink_task_def.json" `
    --region $AWS_REGION `
    --output json | Out-Null
Write-Host "  Task definition registered."

# --------------------------------------------------------------------------
# 11. ECS Service
# --------------------------------------------------------------------------
Write-Host "Creating ECS service '$SVC_NAME' (takes 1-2 min to stabilize)..."
$networkConfig = "awsvpcConfiguration={subnets=[$SUBNET_LIST],securityGroups=[$ECS_SG_ID],assignPublicIp=ENABLED}"
$lbConfig = "targetGroupArn=$TG_ARN,containerName=$APP_NAME,containerPort=8000"

aws ecs create-service `
    --cluster $CLUSTER `
    --service-name $SVC_NAME `
    --task-definition $TASK_FAM `
    --desired-count 1 `
    --launch-type FARGATE `
    --network-configuration $networkConfig `
    --load-balancers $lbConfig `
    --health-check-grace-period-seconds 120 `
    --region $AWS_REGION `
    --output json | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Service already exists - updating task definition..."
    aws ecs update-service `
        --cluster $CLUSTER `
        --service $SVC_NAME `
        --task-definition $TASK_FAM `
        --region $AWS_REGION `
        --output json | Out-Null
}
Write-Host "  Service created/updated. Waiting for it to become healthy (~3 min)..."

# Wait for the service to stabilize
aws ecs wait services-stable `
    --cluster $CLUSTER `
    --services $SVC_NAME `
    --region $AWS_REGION
Write-Host "  Service is RUNNING."

# --------------------------------------------------------------------------
# 12. Save ALB DNS to config
# --------------------------------------------------------------------------
$configPath = "$PSScriptRoot\config.ps1"
(Get-Content $configPath) -replace '^\$ALB_DNS\s*=.*', "`$ALB_DNS      = `"$ALB_DNS_NAME`"" |
    Set-Content $configPath

Write-Host ""
Write-Host "=== Step 2 DONE ===" -ForegroundColor Green
Write-Host ""
Write-Host "Backend is live at:  http://$ALB_DNS_NAME"
Write-Host "Health check:        http://$ALB_DNS_NAME/health"
Write-Host "API docs:            http://$ALB_DNS_NAME/docs"
Write-Host ""
Write-Host "IMPORTANT - Update your Twilio Voice webhook URLs to:"
Write-Host "  Outbound: (set automatically on each call via WEBHOOK_BASE_URL)"
Write-Host ""
Write-Host "Next: run  .\deploy\3_deploy_frontends.ps1"
