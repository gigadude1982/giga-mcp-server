#!/usr/bin/env bash
# Deploy giga-mcp-server to AWS.
#
# 1. Ensures the shared ECR repository exists.
# 2. Runs `cdk deploy` to update App Runner service config (env vars, secrets, etc).
# 3. Builds and pushes the Docker image; App Runner auto-deploys on new image.
#
# Prerequisites:
#   - AWS CLI v2 configured with credentials
#   - Docker running
#   - Node.js + CDK dependencies installed (cd infra && npm ci)
#
# Usage:
#   ./scripts/deploy.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPO="giga-mcp-server"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO"

echo "==> Account: $ACCOUNT_ID | Region: $AWS_REGION"

# ── 1. Ensure ECR repo exists ────────────────────────────────────────────────
if ! aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" \
    > /dev/null 2>&1; then
  echo "==> Creating ECR repository: $ECR_REPO"
  aws ecr create-repository \
    --repository-name "$ECR_REPO" \
    --region "$AWS_REGION" \
    --image-scanning-configuration scanOnPush=true \
    --no-cli-pager
else
  echo "==> ECR repository already exists: $ECR_REPO"
fi

# ── 2. CDK deploy ────────────────────────────────────────────────────────────
echo ""
echo "==> Deploying CDK stack (GigaMcpServer)..."
cd "$REPO_ROOT/infra"
npx cdk deploy GigaMcpServer --require-approval never --no-cli-pager
cd "$REPO_ROOT"

# ── 3. Build & push image ────────────────────────────────────────────────────
echo ""
echo "==> Logging into ECR..."
aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin \
    "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

echo "==> Building image (linux/amd64)..."
docker build --platform linux/amd64 -t "$ECR_URI:latest" "$REPO_ROOT"

echo "==> Pushing image..."
docker push "$ECR_URI:latest"

echo ""
echo "==> Done. App Runner will detect the new image and redeploy automatically."
echo "    Monitor: https://console.aws.amazon.com/apprunner/home#/services"
