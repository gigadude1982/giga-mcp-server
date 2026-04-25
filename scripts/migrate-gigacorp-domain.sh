#!/usr/bin/env bash
# Migrate mcp.gigacorp.co from the old manually-created App Runner service
# (giga-mcp-server) to the new CDK-managed service (giga-mcp-gigacorp-react).
#
# Phases:
#   1. disassociate    — Release mcp.gigacorp.co from the old service
#   2. validation      — After cdk deploy, push App Runner validation CNAMEs to Route 53
#   3. cutover         — Update mcp.gigacorp.co CNAME to the new service's default URL
#   4. cleanup         — Delete the old App Runner service
#
# Run phases in order, verifying success between each.
# Usage: ./scripts/migrate-gigacorp-domain.sh <phase>
set -euo pipefail

REGION="us-east-1"
OLD_SERVICE_NAME="giga-mcp-server"
NEW_SERVICE_NAME="giga-mcp-gigacorp-react"
DOMAIN="mcp.gigacorp.co"
HOSTED_ZONE_ID="Z08385601B5HCX1AG6EO1"
STACK_NAME="GigaMcpServer"

PHASE="${1:-}"
if [[ -z "$PHASE" ]]; then
  echo "Usage: $0 <disassociate|validation|cutover|cleanup>"
  exit 1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

confirm() {
  read -r -p "$1 [y/N] " response
  [[ "$response" =~ ^[Yy]$ ]]
}

service_arn() {
  aws apprunner list-services --region "$REGION" \
    --query "ServiceSummaryList[?ServiceName=='$1'].ServiceArn | [0]" \
    --output text
}

stack_output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

phase_disassociate() {
  local arn
  arn=$(service_arn "$OLD_SERVICE_NAME")
  if [[ "$arn" == "None" || -z "$arn" ]]; then
    echo "Old service '$OLD_SERVICE_NAME' not found — already removed?"
    return 0
  fi

  echo "Found old service: $arn"
  echo "About to disassociate $DOMAIN from $OLD_SERVICE_NAME."
  echo "After this, $DOMAIN will be DOWN until cdk deploy completes phase 2 + 3."
  confirm "Proceed?" || { echo "Aborted."; exit 1; }

  aws apprunner disassociate-custom-domain --region "$REGION" \
    --service-arn "$arn" \
    --domain-name "$DOMAIN" \
    --no-cli-pager > /dev/null
  echo "Disassociation initiated.  Wait ~1 min, then run: cd infra && npx cdk deploy"
}

phase_validation() {
  local records
  records=$(stack_output "ServicegigacorpreactCertificateValidationRecords")
  if [[ -z "$records" || "$records" == "None" ]]; then
    echo "No validation records in stack output — has cdk deploy completed?"
    exit 1
  fi

  echo "Reading validation records from stack output..."
  echo "Records (raw): $records"
  echo ""
  echo "Note: App Runner returns these as a list of {Name,Value,Type,Status} objects."
  echo "Parse and create them in Route 53 manually via the AWS console, or use:"
  echo ""
  echo "  aws apprunner describe-custom-domains --region $REGION \\"
  echo "    --service-arn \$(./scripts/migrate-gigacorp-domain.sh _new-arn) \\"
  echo "    --query 'CustomDomains[0].CertificateValidationRecords'"
  echo ""
  echo "Then for each record (Type=CNAME, Name=..., Value=...) run:"
  echo ""
  echo "  aws route53 change-resource-record-sets --hosted-zone-id $HOSTED_ZONE_ID \\"
  echo "    --change-batch '{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"<name>\",\"Type\":\"CNAME\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"<value>\"}]}}]}'"
  echo ""
  echo "Or, easier — auto-add via:"

  local new_arn
  new_arn=$(service_arn "$NEW_SERVICE_NAME")
  aws apprunner describe-custom-domains --region "$REGION" \
    --service-arn "$new_arn" \
    --query 'CustomDomains[0].CertificateValidationRecords' --output json \
    | python3 -c '
import json, sys, subprocess
records = json.load(sys.stdin) or []
zone = "'"$HOSTED_ZONE_ID"'"
changes = []
for r in records:
    changes.append({
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": r["Name"],
            "Type": r["Type"],
            "TTL": 300,
            "ResourceRecords": [{"Value": r["Value"]}],
        },
    })
if not changes:
    print("No validation records yet — wait a few seconds and retry.", file=sys.stderr)
    sys.exit(1)
batch = {"Changes": changes}
print(f"Adding {len(changes)} validation records to Route 53 zone {zone}...")
subprocess.run(["aws","route53","change-resource-record-sets",
                "--hosted-zone-id", zone,
                "--change-batch", json.dumps(batch)], check=True)
print("Done.  Wait 5–15 min for App Runner to issue the cert, then run phase 3.")
'
}

phase_cutover() {
  local default_url new_arn status
  new_arn=$(service_arn "$NEW_SERVICE_NAME")
  if [[ "$new_arn" == "None" || -z "$new_arn" ]]; then
    echo "New service '$NEW_SERVICE_NAME' not found — has cdk deploy run?"
    exit 1
  fi

  status=$(aws apprunner describe-custom-domains --region "$REGION" \
    --service-arn "$new_arn" \
    --query "CustomDomains[?DomainName=='$DOMAIN'].Status | [0]" \
    --output text)
  echo "Custom domain status: $status"
  if [[ "$status" != "active" ]]; then
    echo "Domain not active yet — wait for cert validation to complete, then retry."
    exit 1
  fi

  default_url=$(aws apprunner describe-service --region "$REGION" \
    --service-arn "$new_arn" \
    --query 'Service.ServiceUrl' --output text)
  echo "New App Runner default URL: $default_url"
  echo "Updating $DOMAIN CNAME → $default_url"
  confirm "Proceed?" || { echo "Aborted."; exit 1; }

  aws route53 change-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" \
    --change-batch "$(cat <<EOF
{
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "$DOMAIN",
      "Type": "CNAME",
      "TTL": 300,
      "ResourceRecords": [{"Value": "$default_url"}]
    }
  }]
}
EOF
)" > /dev/null
  echo "DNS updated.  Verify https://$DOMAIN responds, then run phase 4."
}

phase_cleanup() {
  local arn
  arn=$(service_arn "$OLD_SERVICE_NAME")
  if [[ "$arn" == "None" || -z "$arn" ]]; then
    echo "Old service '$OLD_SERVICE_NAME' already gone — nothing to clean up."
    return 0
  fi

  echo "Found old service: $arn"
  echo "About to DELETE $OLD_SERVICE_NAME.  This is permanent."
  echo "Make sure $DOMAIN is responding from the new service before proceeding."
  confirm "Proceed?" || { echo "Aborted."; exit 1; }

  aws apprunner delete-service --region "$REGION" \
    --service-arn "$arn" --no-cli-pager > /dev/null
  echo "Old service deletion initiated.  Wait ~5 min for it to fully delete."
}

# ---------------------------------------------------------------------------
# Internal helpers (used by the script itself)
# ---------------------------------------------------------------------------

case "$PHASE" in
  disassociate) phase_disassociate ;;
  validation)   phase_validation ;;
  cutover)      phase_cutover ;;
  cleanup)      phase_cleanup ;;
  _new-arn)     service_arn "$NEW_SERVICE_NAME" ;;
  *)
    echo "Unknown phase: $PHASE"
    echo "Usage: $0 <disassociate|validation|cutover|cleanup>"
    exit 1
    ;;
esac
