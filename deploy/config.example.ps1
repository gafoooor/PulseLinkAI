# =============================================================================
# PulseLink AWS Deployment — Configuration Template
# Copy this file to config.ps1, fill in your values, then run the scripts:
#   1_push_image.ps1  →  2_create_infra.ps1  →  3_deploy_frontends.ps1  →  4_add_backend_https.ps1
# =============================================================================

# AWS settings
$AWS_REGION   = "ap-south-1"   # or your preferred region
$APP_NAME     = "pulselink"

# Twilio credentials — get these from console.twilio.com
$TWILIO_ACCOUNT_SID       = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
$TWILIO_AUTH_TOKEN        = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
$TWILIO_PHONE_NUMBER      = "+1xxxxxxxxxx"         # outbound IVR call number
$TWILIO_WHATSAPP_FROM     = "whatsapp:+14155238886" # Twilio sandbox WhatsApp sender
$TWILIO_TEST_DONOR_PHONE  = "+91xxxxxxxxxx"         # phone that receives test IVR calls
$TWILIO_COORDINATOR_PHONE = "+91xxxxxxxxxx"         # phone that receives WhatsApp alerts

# Filled in automatically by the deploy scripts after infra is created.
# You can also fill these in manually if re-running individual scripts.
$ECR_IMAGE    = ""   # e.g. 123456789012.dkr.ecr.ap-south-1.amazonaws.com/pulselink-backend:latest
$ALB_DNS      = ""   # e.g. pulselink-alb-123456789.ap-south-1.elb.amazonaws.com
$PATIENT_CF   = ""   # CloudFront URL for patient app
$DONOR_CF     = ""   # CloudFront URL for donor screen
$COORD_CF     = ""   # CloudFront URL for coordinator dashboard
$BACKEND_CF   = ""   # CloudFront URL for backend API (HTTPS)
