#!/usr/bin/env bash
# Set up Cognito authentication for giga-mcp-server.
#
# Usage:
#   ./setup-auth.sh                    # Create user pool, client, and test user
#   ./setup-auth.sh --token            # Get an access token for the test user
#   ./setup-auth.sh --status           # Show current auth config
#
# Prerequisites:
#   - AWS CLI v2 configured with credentials
#
# Environment overrides:
#   AWS_REGION      (default: us-east-1)
#   APP_NAME        (default: giga-mcp-server)
#   CUSTOM_DOMAIN   (default: mcp.gigacorp.co)
#   TEST_USER_EMAIL (default: admin@gigacorp.co)
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
APP_NAME="${APP_NAME:-giga-mcp-server}"
CUSTOM_DOMAIN="${CUSTOM_DOMAIN:-mcp.gigacorp.co}"
TEST_USER_EMAIL="${TEST_USER_EMAIL:-admin@gigacorp.co}"
POOL_NAME="${APP_NAME}-users"
CLIENT_NAME="${APP_NAME}-client"

# --- Helper: find existing resources ---
find_user_pool_id() {
    aws cognito-idp list-user-pools --max-results 60 --region "$AWS_REGION" \
        --query "UserPools[?Name=='$POOL_NAME'].Id" --output text 2>/dev/null || true
}

find_client_id() {
    local pool_id="$1"
    aws cognito-idp list-user-pool-clients --user-pool-id "$pool_id" --region "$AWS_REGION" \
        --query "UserPoolClients[?ClientName=='$CLIENT_NAME'].ClientId" --output text 2>/dev/null || true
}

# --- Get token ---
if [[ "${1:-}" == "--token" ]]; then
    POOL_ID=$(find_user_pool_id)
    if [[ -z "$POOL_ID" ]]; then
        echo "ERROR: User pool '$POOL_NAME' not found. Run ./setup-auth.sh first."
        exit 1
    fi

    CLIENT_ID=$(find_client_id "$POOL_ID")
    if [[ -z "$CLIENT_ID" ]]; then
        echo "ERROR: App client not found."
        exit 1
    fi

    echo "Authenticating $TEST_USER_EMAIL..."
    echo ""

    read -rsp "Password: " PASSWORD
    echo ""

    RESULT=$(aws cognito-idp initiate-auth \
        --auth-flow USER_PASSWORD_AUTH \
        --client-id "$CLIENT_ID" \
        --auth-parameters "USERNAME=$TEST_USER_EMAIL,PASSWORD=$PASSWORD" \
        --region "$AWS_REGION" \
        --output json 2>&1) || {
        echo "Auth failed. If this is a new user, you may need to change the temporary password first:"
        echo "  aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID --username $TEST_USER_EMAIL --password 'YourNewPass!' --permanent --region $AWS_REGION"
        exit 1
    }

    # Check for auth challenge (e.g. NEW_PASSWORD_REQUIRED)
    CHALLENGE=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ChallengeName',''))" 2>/dev/null || true)
    if [[ -n "$CHALLENGE" ]]; then
        echo "Auth challenge: $CHALLENGE"
        echo "Set a permanent password first:"
        echo "  aws cognito-idp admin-set-user-password --user-pool-id $POOL_ID --username $TEST_USER_EMAIL --password 'YourNewPass!' --permanent --region $AWS_REGION"
        exit 1
    fi

    TOKEN=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['AuthenticationResult']['AccessToken'])")
    echo "Access token:"
    echo ""
    echo "$TOKEN"
    echo ""
    echo "Use this in the MCP Inspector's Authentication > Bearer Token field."
    exit 0
fi

# --- Show status ---
if [[ "${1:-}" == "--status" ]]; then
    POOL_ID=$(find_user_pool_id)
    if [[ -z "$POOL_ID" ]]; then
        echo "No user pool found. Auth is not set up."
        exit 0
    fi

    CLIENT_ID=$(find_client_id "$POOL_ID")

    echo "User Pool:  $POOL_NAME ($POOL_ID)"
    echo "Client:     $CLIENT_NAME ($CLIENT_ID)"
    echo "Region:     $AWS_REGION"
    echo "Issuer:     https://cognito-idp.$AWS_REGION.amazonaws.com/$POOL_ID"
    echo ""

    # Check App Runner env
    SERVICE_ARN=$(aws apprunner list-services \
        --query "ServiceSummaryList[?ServiceName=='$APP_NAME'].ServiceArn" \
        --output text --region "$AWS_REGION" 2>/dev/null || true)

    if [[ -n "$SERVICE_ARN" ]]; then
        CONFIGURED_POOL=$(aws apprunner describe-service \
            --service-arn "$SERVICE_ARN" \
            --query 'Service.SourceConfiguration.ImageRepository.ImageConfiguration.RuntimeEnvironmentVariables.GIGA_COGNITO_USER_POOL_ID' \
            --output text --region "$AWS_REGION" 2>/dev/null || true)
        if [[ -n "$CONFIGURED_POOL" && "$CONFIGURED_POOL" != "None" ]]; then
            echo "App Runner: auth ENABLED (pool: $CONFIGURED_POOL)"
        else
            echo "App Runner: auth DISABLED (GIGA_COGNITO_USER_POOL_ID not set)"
        fi
    fi
    exit 0
fi

# --- Create resources ---
echo "==> Setting up Cognito auth for $APP_NAME"
echo ""

# User Pool
POOL_ID=$(find_user_pool_id)
if [[ -n "$POOL_ID" ]]; then
    echo "User pool '$POOL_NAME' already exists: $POOL_ID"
else
    echo "Creating user pool: $POOL_NAME"
    POOL_ID=$(aws cognito-idp create-user-pool \
        --pool-name "$POOL_NAME" \
        --auto-verified-attributes email \
        --admin-create-user-config AllowAdminCreateUserOnly=true \
        --policies 'PasswordPolicy={MinimumLength=12,RequireUppercase=true,RequireLowercase=true,RequireNumbers=true,RequireSymbols=false}' \
        --region "$AWS_REGION" \
        --query 'UserPool.Id' --output text)
    echo "Created user pool: $POOL_ID"
fi

# App Client
CLIENT_ID=$(find_client_id "$POOL_ID")
if [[ -n "$CLIENT_ID" ]]; then
    echo "App client '$CLIENT_NAME' already exists: $CLIENT_ID"
else
    echo "Creating app client: $CLIENT_NAME"
    CLIENT_ID=$(aws cognito-idp create-user-pool-client \
        --user-pool-id "$POOL_ID" \
        --client-name "$CLIENT_NAME" \
        --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
        --no-generate-secret \
        --access-token-validity 1 \
        --id-token-validity 1 \
        --refresh-token-validity 30 \
        --token-validity-units '{"AccessToken":"hours","IdToken":"hours","RefreshToken":"days"}' \
        --region "$AWS_REGION" \
        --query 'UserPoolClient.ClientId' --output text)
    echo "Created app client: $CLIENT_ID"
fi

# Test User
echo ""
echo "Creating test user: $TEST_USER_EMAIL"
aws cognito-idp admin-create-user \
    --user-pool-id "$POOL_ID" \
    --username "$TEST_USER_EMAIL" \
    --user-attributes Name=email,Value="$TEST_USER_EMAIL" Name=email_verified,Value=true \
    --temporary-password 'TempPass123!' \
    --message-action SUPPRESS \
    --region "$AWS_REGION" 2>/dev/null && echo "Created user: $TEST_USER_EMAIL" || echo "User already exists: $TEST_USER_EMAIL"

echo ""
echo "==> Cognito setup complete"
echo ""
echo "  User Pool ID:  $POOL_ID"
echo "  Client ID:     $CLIENT_ID"
echo "  Test User:     $TEST_USER_EMAIL"
echo "  Temp Password: TempPass123!"
echo ""
echo "==> Next steps:"
echo ""
echo "  1. Set a permanent password:"
echo "     aws cognito-idp admin-set-user-password \\"
echo "       --user-pool-id $POOL_ID \\"
echo "       --username $TEST_USER_EMAIL \\"
echo "       --password 'YourSecurePassword1' \\"
echo "       --permanent --region $AWS_REGION"
echo ""
echo "  2. Enable auth on App Runner by setting these env vars:"
echo "     GIGA_COGNITO_USER_POOL_ID=$POOL_ID"
echo "     GIGA_COGNITO_REGION=$AWS_REGION"
echo "     GIGA_COGNITO_CLIENT_ID=$CLIENT_ID"
echo "     GIGA_PUBLIC_URL=https://$CUSTOM_DOMAIN"
echo ""
echo "  3. Get an access token:"
echo "     ./setup-auth.sh --token"
echo ""
echo "==> Done."
