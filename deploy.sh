#!/usr/bin/env bash
# Deploy giga-mcp-server to AWS App Runner via ECR.
#
# Prerequisites:
#   - AWS CLI v2 configured with credentials
#   - Docker running
#
# Usage:
#   ./deploy.sh                        # First deploy (creates everything)
#   ./deploy.sh --update               # Update existing service with new image
#   ./deploy.sh --setup-domain         # Link custom domain (run after first deploy)
#
# Set these env vars or pass as arguments:
#   AWS_REGION          (default: us-east-1)
#   APP_NAME            (default: giga-mcp-server)
#   CUSTOM_DOMAIN       (default: mcp.gigacorp.co)

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
APP_NAME="${APP_NAME:-giga-mcp-server}"
CUSTOM_DOMAIN="${CUSTOM_DOMAIN:-mcp.gigacorp.co}"
ECR_REPO="$APP_NAME"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO"
IMAGE_TAG="latest"

echo "==> Account: $ACCOUNT_ID | Region: $AWS_REGION"

# --- ECR ---
if ! aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "==> Creating ECR repository: $ECR_REPO"
    aws ecr create-repository \
        --repository-name "$ECR_REPO" \
        --region "$AWS_REGION" \
        --image-scanning-configuration scanOnPush=true
fi

# --- Build & Push ---
echo "==> Logging into ECR"
aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

echo "==> Building image"
docker build --platform linux/amd64 -t "$ECR_URI:$IMAGE_TAG" .

echo "==> Pushing image"
docker push "$ECR_URI:$IMAGE_TAG"

# --- App Runner ---
if [[ "${1:-}" == "--setup-domain" ]]; then
    echo "==> Setting up custom domain: $CUSTOM_DOMAIN"
    SERVICE_ARN=$(aws apprunner list-services --query "ServiceSummaryList[?ServiceName=='$APP_NAME'].ServiceArn" --output text --region "$AWS_REGION")
    if [[ -z "$SERVICE_ARN" ]]; then
        echo "ERROR: Service '$APP_NAME' not found. Deploy first."
        exit 1
    fi

    # Associate custom domain with App Runner (App Runner handles the cert)
    RESULT=$(aws apprunner associate-custom-domain \
        --service-arn "$SERVICE_ARN" \
        --domain-name "$CUSTOM_DOMAIN" \
        --enable-www-subdomain=false \
        --region "$AWS_REGION" \
        --output json 2>&1) || true

    if echo "$RESULT" | grep -q "already associated"; then
        echo "Domain already associated. Checking status..."
    else
        echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
    fi

    # Show the DNS records that need to be created in Route 53
    echo ""
    echo "==> DNS records to create in Route 53 for $CUSTOM_DOMAIN:"
    echo ""
    aws apprunner describe-custom-domains \
        --service-arn "$SERVICE_ARN" \
        --region "$AWS_REGION" \
        --query 'CustomDomains[0].CertificateValidationRecords' \
        --output table 2>/dev/null || true

    # Get the App Runner target for the CNAME
    SERVICE_URL=$(aws apprunner describe-service \
        --service-arn "$SERVICE_ARN" \
        --query 'Service.ServiceUrl' \
        --output text \
        --region "$AWS_REGION")

    echo ""
    echo "Create these DNS records in Route 53 (gigacorp.co hosted zone):"
    echo ""
    echo "  1. CNAME: $CUSTOM_DOMAIN → $SERVICE_URL"
    echo "     (This points your domain to the App Runner service)"
    echo ""
    echo "  2. CNAME validation records shown above"
    echo "     (These prove domain ownership for the SSL certificate)"
    echo ""
    echo "After adding DNS records, validation takes ~5-10 minutes."
    echo "Check status: aws apprunner describe-custom-domains --service-arn $SERVICE_ARN --region $AWS_REGION"

elif [[ "${1:-}" == "--update" ]]; then
    echo "==> Updating App Runner service"
    SERVICE_ARN=$(aws apprunner list-services --query "ServiceSummaryList[?ServiceName=='$APP_NAME'].ServiceArn" --output text --region "$AWS_REGION")
    if [[ -z "$SERVICE_ARN" ]]; then
        echo "ERROR: Service '$APP_NAME' not found. Run without --update first."
        exit 1
    fi
    aws apprunner update-service \
        --service-arn "$SERVICE_ARN" \
        --source-configuration "{
            \"ImageRepository\": {
                \"ImageIdentifier\": \"$ECR_URI:$IMAGE_TAG\",
                \"ImageRepositoryType\": \"ECR\",
                \"ImageConfiguration\": {
                    \"Port\": \"8000\",
                    \"RuntimeEnvironmentVariables\": {
                        \"GIGA_TRANSPORT\": \"streamable-http\",
                        \"GIGA_HOST\": \"0.0.0.0\",
                        \"GIGA_PORT\": \"8000\"
                    }
                }
            },
            \"AutoDeploymentsEnabled\": false,
            \"AuthenticationConfiguration\": {
                \"AccessRoleArn\": \"$(aws apprunner list-services --query \"ServiceSummaryList[?ServiceName=='$APP_NAME'].ServiceArn\" --output text --region \"$AWS_REGION\" | xargs -I{} aws apprunner describe-service --service-arn {} --query 'Service.SourceConfiguration.AuthenticationConfiguration.AccessRoleArn' --output text --region \"$AWS_REGION\")\"
            }
        }" \
        --region "$AWS_REGION"
    echo "==> Update initiated. Check status:"
    echo "    aws apprunner describe-service --service-arn $SERVICE_ARN --region $AWS_REGION"
else
    # Check if service already exists
    EXISTING=$(aws apprunner list-services --query "ServiceSummaryList[?ServiceName=='$APP_NAME'].ServiceArn" --output text --region "$AWS_REGION" 2>/dev/null || true)
    if [[ -n "$EXISTING" ]]; then
        echo "Service already exists. Use --update to redeploy."
        echo "Service ARN: $EXISTING"
        exit 0
    fi

    # Create IAM role for App Runner to pull from ECR
    ROLE_NAME="${APP_NAME}-apprunner-ecr"
    if ! aws iam get-role --role-name "$ROLE_NAME" > /dev/null 2>&1; then
        echo "==> Creating IAM role: $ROLE_NAME"
        aws iam create-role \
            --role-name "$ROLE_NAME" \
            --assume-role-policy-document '{
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "build.apprunner.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }'
        aws iam attach-role-policy \
            --role-name "$ROLE_NAME" \
            --policy-arn "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
        echo "    Waiting for IAM role propagation..."
        sleep 10
    fi
    ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/$ROLE_NAME"

    echo "==> Creating App Runner service: $APP_NAME"
    aws apprunner create-service \
        --service-name "$APP_NAME" \
        --source-configuration "{
            \"ImageRepository\": {
                \"ImageIdentifier\": \"$ECR_URI:$IMAGE_TAG\",
                \"ImageRepositoryType\": \"ECR\",
                \"ImageConfiguration\": {
                    \"Port\": \"8000\",
                    \"RuntimeEnvironmentVariables\": {
                        \"GIGA_TRANSPORT\": \"streamable-http\",
                        \"GIGA_HOST\": \"0.0.0.0\",
                        \"GIGA_PORT\": \"8000\"
                    }
                }
            },
            \"AutoDeploymentsEnabled\": false,
            \"AuthenticationConfiguration\": {
                \"AccessRoleArn\": \"$ROLE_ARN\"
            }
        }" \
        --instance-configuration "{
            \"Cpu\": \"0.25 vCPU\",
            \"Memory\": \"0.5 GB\"
        }" \
        --health-check-configuration "{
            \"Protocol\": \"HTTP\",
            \"Path\": \"/health\",
            \"Interval\": 20,
            \"Timeout\": 5,
            \"HealthyThreshold\": 1,
            \"UnhealthyThreshold\": 5
        }" \
        --region "$AWS_REGION" \
        --output json

    echo ""
    echo "==> Service creating. It takes 2-3 minutes to provision."
    echo ""
    echo "Next steps:"
    echo "  1. Set secrets (JIRA + Anthropic keys):"
    echo "     aws apprunner update-service --service-arn <ARN> \\"
    echo "       --source-configuration '{...RuntimeEnvironmentSecrets...}' --region $AWS_REGION"
    echo ""
    echo "  2. Or set them in the AWS Console under App Runner > $APP_NAME > Configuration"
    echo ""
    echo "  3. Get your service URL:"
    echo "     aws apprunner list-services --region $AWS_REGION"
    echo ""
    echo "  4. Set up custom domain ($CUSTOM_DOMAIN):"
    echo "     ./deploy.sh --setup-domain"
fi

echo "==> Done."
