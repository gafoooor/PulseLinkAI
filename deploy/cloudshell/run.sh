#!/usr/bin/env bash
# =============================================================================
# PulseLink — CloudShell Deployment Script (Steps 2 + 3)
#
# Run this AFTER pushing the Docker image from your Windows machine.
#
# Usage (in CloudShell, from the project root):
#   bash deploy/cloudshell/run.sh
#
# Prerequisites:
#   1. Docker image already pushed to ECR  (run deploy/1_push_image.ps1 locally)
#   2. Project uploaded to CloudShell      (Actions > Upload file > select zip)
#   3. Edit the CONFIG section below
# =============================================================================
set -euo pipefail

# =============================================================================
# CONFIG — fill in before running
# =============================================================================
AWS_REGION="ap-south-1"
APP_NAME="pulselink"

# Your ECR image URI (output from 1_push_image.ps1)
ECR_IMAGE=""    # e.g. 123456789012.dkr.ecr.ap-south-1.amazonaws.com/pulselink-backend:latest

# Twilio credentials — get from https://console.twilio.com
TWILIO_ACCOUNT_SID="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TWILIO_AUTH_TOKEN="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TWILIO_PHONE_NUMBER="+1xxxxxxxxxx"
TWILIO_WHATSAPP_FROM="whatsapp:+14155238886"
TWILIO_TEST_DONOR_PHONE="+91xxxxxxxxxx"
TWILIO_COORDINATOR_PHONE="+91xxxxxxxxxx"
# =============================================================================

if [[ -z "$ECR_IMAGE" ]]; then
  echo "ERROR: Set ECR_IMAGE at the top of this script first."
  exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CLUSTER="${APP_NAME}-cluster"
SVC_NAME="${APP_NAME}-service"
LOG_GROUP="/ecs/${APP_NAME}"
EXEC_ROLE="${APP_NAME}EcsTaskExecutionRole"
ALB_NAME="${APP_NAME}-alb"
TG_NAME="${APP_NAME}-tg"
TASK_FAM="${APP_NAME}-backend"

echo ""
echo "=== Step 2: AWS Infrastructure ==="
echo ""

# --------------------------------------------------------------------------
# ECS Cluster
# --------------------------------------------------------------------------
echo "Creating ECS cluster..."
aws ecs create-cluster --cluster-name "$CLUSTER" --region "$AWS_REGION" > /dev/null 2>&1 || true

# --------------------------------------------------------------------------
# CloudWatch log group
# --------------------------------------------------------------------------
echo "Creating CloudWatch log group..."
aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$AWS_REGION" > /dev/null 2>&1 || true

# --------------------------------------------------------------------------
# IAM Task Execution Role
# --------------------------------------------------------------------------
echo "Creating IAM execution role..."
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam create-role --role-name "$EXEC_ROLE" \
    --assume-role-policy-document "$TRUST" > /dev/null 2>&1 || true
aws iam attach-role-policy --role-name "$EXEC_ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy > /dev/null 2>&1 || true
EXEC_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${EXEC_ROLE}"
echo "  Role: $EXEC_ROLE_ARN"

# --------------------------------------------------------------------------
# Default VPC + public subnets
# --------------------------------------------------------------------------
echo "Discovering default VPC..."
VPC_ID=$(aws ec2 describe-vpcs \
    --filters Name=isDefault,Values=true \
    --query "Vpcs[0].VpcId" --output text --region "$AWS_REGION")
SUBNETS=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" \
    --query "Subnets[?MapPublicIpOnLaunch==\`true\`].SubnetId" \
    --output text --region "$AWS_REGION" | tr '\t' ',')
echo "  VPC: $VPC_ID  Subnets: $SUBNETS"

# --------------------------------------------------------------------------
# Security Groups
# --------------------------------------------------------------------------
echo "Creating security groups..."
ALB_SG_ID=$(aws ec2 create-security-group \
    --group-name "${APP_NAME}-alb-sg" \
    --description "PulseLink ALB HTTP inbound" \
    --vpc-id "$VPC_ID" --region "$AWS_REGION" \
    --query GroupId --output text 2>/dev/null) || \
ALB_SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${APP_NAME}-alb-sg" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" --output text --region "$AWS_REGION")

aws ec2 authorize-security-group-ingress \
    --group-id "$ALB_SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0 \
    --region "$AWS_REGION" > /dev/null 2>&1 || true
echo "  ALB SG: $ALB_SG_ID"

ECS_SG_ID=$(aws ec2 create-security-group \
    --group-name "${APP_NAME}-ecs-sg" \
    --description "PulseLink ECS tasks inbound 8000" \
    --vpc-id "$VPC_ID" --region "$AWS_REGION" \
    --query GroupId --output text 2>/dev/null) || \
ECS_SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${APP_NAME}-ecs-sg" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" --output text --region "$AWS_REGION")

aws ec2 authorize-security-group-ingress \
    --group-id "$ECS_SG_ID" --protocol tcp --port 8000 \
    --source-group "$ALB_SG_ID" --region "$AWS_REGION" > /dev/null 2>&1 || true
echo "  ECS SG: $ECS_SG_ID"

# --------------------------------------------------------------------------
# ALB
# --------------------------------------------------------------------------
echo "Creating Application Load Balancer (takes ~2 min)..."
SUBNET_LIST=$(echo "$SUBNETS" | tr ',' ' ')
ALB_JSON=$(aws elbv2 create-load-balancer \
    --name "$ALB_NAME" \
    --subnets $SUBNET_LIST \
    --security-groups "$ALB_SG_ID" \
    --scheme internet-facing \
    --type application \
    --region "$AWS_REGION" \
    --output json)
ALB_ARN=$(echo "$ALB_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['LoadBalancers'][0]['LoadBalancerArn'])")
ALB_DNS=$(echo "$ALB_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['LoadBalancers'][0]['DNSName'])")
echo "  ALB DNS: $ALB_DNS"

# --------------------------------------------------------------------------
# Target Group
# --------------------------------------------------------------------------
echo "Creating target group..."
TG_ARN=$(aws elbv2 create-target-group \
    --name "$TG_NAME" \
    --protocol HTTP --port 8000 \
    --vpc-id "$VPC_ID" \
    --target-type ip \
    --health-check-path /health \
    --health-check-interval-seconds 30 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 3 \
    --region "$AWS_REGION" \
    --query "TargetGroups[0].TargetGroupArn" --output text)
echo "  TG ARN: $TG_ARN"

# Listener
aws elbv2 create-listener \
    --load-balancer-arn "$ALB_ARN" \
    --protocol HTTP --port 80 \
    --default-actions "Type=forward,TargetGroupArn=${TG_ARN}" \
    --region "$AWS_REGION" > /dev/null

# --------------------------------------------------------------------------
# ECS Task Definition
# --------------------------------------------------------------------------
echo "Registering ECS task definition..."
cat > /tmp/pulselink_task.json <<TASKEOF
{
  "family": "${TASK_FAM}",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "${EXEC_ROLE_ARN}",
  "containerDefinitions": [{
    "name": "${APP_NAME}",
    "image": "${ECR_IMAGE}",
    "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
    "essential": true,
    "environment": [
      {"name": "LLM_PROVIDER",             "value": "mock"},
      {"name": "MESSAGE_CHANNEL",          "value": "mock"},
      {"name": "VOICE_PROVIDER",           "value": "mock"},
      {"name": "EVENT_BUS",                "value": "memory"},
      {"name": "DATASET_CSV_PATH",         "value": "/app/Dataset.csv"},
      {"name": "TWILIO_ACCOUNT_SID",       "value": "${TWILIO_ACCOUNT_SID}"},
      {"name": "TWILIO_AUTH_TOKEN",        "value": "${TWILIO_AUTH_TOKEN}"},
      {"name": "TWILIO_PHONE_NUMBER",      "value": "${TWILIO_PHONE_NUMBER}"},
      {"name": "TWILIO_WHATSAPP_FROM",     "value": "${TWILIO_WHATSAPP_FROM}"},
      {"name": "TWILIO_TEST_DONOR_PHONE",  "value": "${TWILIO_TEST_DONOR_PHONE}"},
      {"name": "TWILIO_COORDINATOR_PHONE", "value": "${TWILIO_COORDINATOR_PHONE}"},
      {"name": "WEBHOOK_BASE_URL",         "value": "http://${ALB_DNS}"}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "${LOG_GROUP}",
        "awslogs-region": "${AWS_REGION}",
        "awslogs-stream-prefix": "ecs"
      }
    },
    "healthCheck": {
      "command": ["CMD-SHELL","curl -f http://localhost:8000/health || exit 1"],
      "interval": 30, "timeout": 10, "retries": 3, "startPeriod": 90
    }
  }]
}
TASKEOF
aws ecs register-task-definition \
    --cli-input-json file:///tmp/pulselink_task.json \
    --region "$AWS_REGION" > /dev/null
echo "  Task definition registered."

# --------------------------------------------------------------------------
# ECS Service
# --------------------------------------------------------------------------
echo "Creating ECS service (waiting for healthy state, ~3 min)..."
NETWORK_CONFIG="awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${ECS_SG_ID}],assignPublicIp=ENABLED}"
LB_CONFIG="targetGroupArn=${TG_ARN},containerName=${APP_NAME},containerPort=8000"
aws ecs create-service \
    --cluster "$CLUSTER" \
    --service-name "$SVC_NAME" \
    --task-definition "$TASK_FAM" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "$NETWORK_CONFIG" \
    --load-balancers "$LB_CONFIG" \
    --health-check-grace-period-seconds 120 \
    --region "$AWS_REGION" > /dev/null

aws ecs wait services-stable \
    --cluster "$CLUSTER" --services "$SVC_NAME" --region "$AWS_REGION"
echo "  ECS service is RUNNING."

echo ""
echo "=== Step 3: Build + Deploy Frontends ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
API_URL="http://${ALB_DNS}"

deploy_frontend() {
    local APP_DIR="$1"
    local BUCKET_SUFFIX="$2"
    local EXTRA_ENV="$3"   # optional extra env var like "VITE_PATIENT_APP_URL=..."

    local BUCKET="${APP_NAME}-${BUCKET_SUFFIX}-${ACCOUNT_ID}"
    local APP_PATH="${PROJECT_ROOT}/frontend/${APP_DIR}"

    echo ""
    echo "--- $APP_DIR ---"

    # S3 bucket
    if [[ "$AWS_REGION" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" > /dev/null 2>&1 || true
    else
        aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" \
            --create-bucket-configuration LocationConstraint="$AWS_REGION" > /dev/null 2>&1 || true
    fi
    aws s3api put-public-access-block --bucket "$BUCKET" \
        --public-access-block-configuration \
        "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false" > /dev/null
    aws s3api put-bucket-website --bucket "$BUCKET" \
        --website-configuration '{"IndexDocument":{"Suffix":"index.html"},"ErrorDocument":{"Key":"index.html"}}' \
        --region "$AWS_REGION" > /dev/null
    POLICY="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":\"*\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::${BUCKET}/*\"}]}"
    aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$POLICY" --region "$AWS_REGION" > /dev/null
    echo "  S3 bucket $BUCKET ready."

    # Write .env.production
    {
        echo "VITE_API_BASE_URL=${API_URL}"
        [[ -n "$EXTRA_ENV" ]] && echo "$EXTRA_ENV"
    } > "${APP_PATH}/.env.production"

    # npm build
    echo "  Running npm install + build..."
    (cd "$APP_PATH" && npm install --silent && npm run build)
    rm -f "${APP_PATH}/.env.production"
    echo "  Build done."

    # Upload to S3
    echo "  Uploading to S3..."
    aws s3 sync "${APP_PATH}/dist" "s3://${BUCKET}" \
        --delete --region "$AWS_REGION" \
        --cache-control "max-age=86400" > /dev/null
    aws s3 cp "${APP_PATH}/dist/index.html" "s3://${BUCKET}/index.html" \
        --cache-control "no-cache,no-store,must-revalidate" \
        --region "$AWS_REGION" > /dev/null
    echo "  Uploaded."

    # CloudFront distribution
    echo "  Creating CloudFront distribution..."
    WEBSITE_ENDPOINT="${BUCKET}.s3-website.${AWS_REGION}.amazonaws.com"
    CALLER_REF="${APP_NAME}-${BUCKET_SUFFIX}-$(date +%s)"
    CF_CONFIG=$(cat <<CFEOF
{
  "Origins":{"Quantity":1,"Items":[{"Id":"S3-${BUCKET}","DomainName":"${WEBSITE_ENDPOINT}","CustomOriginConfig":{"HTTPPort":80,"HTTPSPort":443,"OriginProtocolPolicy":"http-only"}}]},
  "DefaultCacheBehavior":{"ViewerProtocolPolicy":"redirect-to-https","TargetOriginId":"S3-${BUCKET}","ForwardedValues":{"QueryString":false,"Cookies":{"Forward":"none"}},"MinTTL":0,"DefaultTTL":86400,"MaxTTL":31536000,"Compress":true,"AllowedMethods":{"Quantity":2,"Items":["GET","HEAD"],"CachedMethods":{"Quantity":2,"Items":["GET","HEAD"]}}},
  "CustomErrorResponses":{"Quantity":1,"Items":[{"ErrorCode":404,"ResponseCode":"200","ResponsePagePath":"/index.html","ErrorCachingMinTTL":0}]},
  "DefaultRootObject":"index.html",
  "Comment":"PulseLink ${APP_DIR}",
  "Enabled":true,
  "PriceClass":"PriceClass_All",
  "CallerReference":"${CALLER_REF}"
}
CFEOF
)
    CF_DOMAIN=$(echo "$CF_CONFIG" | aws cloudfront create-distribution \
        --distribution-config /dev/stdin \
        --output text --query "Distribution.DomainName")
    echo "  CloudFront: https://${CF_DOMAIN}"
    echo "https://${CF_DOMAIN}"
}

# Deploy patient-app first (coordinator-dashboard needs its URL)
PATIENT_CF=$(deploy_frontend "patient-app"   "patient" "")
DONOR_CF=$(deploy_frontend   "donor-screen"  "donor"   "")
COORD_CF=$(deploy_frontend   "coordinator-dashboard" "coord" "VITE_PATIENT_APP_URL=${PATIENT_CF}")

echo ""
echo "============================================================"
echo "  PulseLink is LIVE on AWS!"
echo "============================================================"
echo ""
echo "  Backend API:    http://${ALB_DNS}"
echo "  API Docs:       http://${ALB_DNS}/docs"
echo "  Patient App:    ${PATIENT_CF}"
echo "  Donor Screen:   ${DONOR_CF}"
echo "  Coordinator:    ${COORD_CF}"
echo ""
echo "  Twilio webhook base: http://${ALB_DNS}"
echo "  (no ngrok needed — WEBHOOK_BASE_URL is set in ECS task)"
echo ""
echo "  NOTE: CloudFront takes 10-15 min to fully propagate."
echo "============================================================"
