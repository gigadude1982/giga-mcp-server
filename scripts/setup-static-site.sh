#!/usr/bin/env bash
# Provision an S3 + CloudFront + OAC static site for a domain, mirroring the
# "gigacorp pattern" documented in STATIC-SITE-HOSTING.md.
#
# Unlike punch.gigacorp.co (a *.gigacorp.co subdomain reusing the wildcard cert
# with DNS in Route 53), this supports an apex domain whose DNS lives OUTSIDE
# AWS (e.g. Porkbun): it requests a fresh DNS-validated ACM cert and prints the
# records you must add at your registrar — it does NOT touch Route 53.
#
# Phases (run in order, completing the manual DNS step between cert and dist):
#   cert         Request the ACM cert; print the validation CNAME(s) for Porkbun.
#   bucket       Create the private bucket and upload CONTENT_DIR.
#   distribution Create OAC + CloudFront (cloned from TEMPLATE_DIST) + bucket policy.
#                Requires the cert to be ISSUED first. Prints the Porkbun DNS records.
#   status       Show cert + distribution status.
#   deploy       Re-sync CONTENT_DIR and invalidate (use for content updates later).
#
# Usage: ./scripts/setup-static-site.sh <phase>
set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
SITE="${SITE:-punchtamagotchi.com}"
WWW="www.$SITE"
REGION="${REGION:-us-east-1}"
if [[ "$REGION" != "us-east-1" ]]; then
  echo "ERROR: REGION must be us-east-1 (CloudFront requires ACM certs in us-east-1)."
  exit 1
fi
CONTENT_DIR="${CONTENT_DIR:-$HOME/dev/punch-pwa/dist}"
TEMPLATE_DIST="${TEMPLATE_DIST:-E3SQXA18GBFLVP}"   # punch.gigacorp.co — proven config to clone
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
DIST_ARN_PREFIX="arn:aws:cloudfront::$ACCOUNT_ID:distribution"
CF_ALIAS_ZONE="Z2FDTNDATAQYW2"   # fixed CloudFront hosted-zone id (for registrars that support ALIAS-to-hostname)

PHASE="${1:-}"

cert_arn() {
  aws acm list-certificates --region "$REGION" \
    --query "CertificateSummaryList[?DomainName=='$SITE'].CertificateArn | [0]" \
    --output text 2>/dev/null
}

dist_id_for_site() {
  # NB: guard with [?Aliases.Items] first — contains() throws on distributions
  # whose Aliases is null, which would abort the whole script under `set -e`.
  aws cloudfront list-distributions \
    --query "DistributionList.Items[?Aliases.Items] | [?contains(Aliases.Items, '$SITE')].Id | [0]" \
    --output text 2>/dev/null
}

oac_id_for_site() {
  aws cloudfront list-origin-access-controls \
    --query "OriginAccessControlList.Items[?Name=='$SITE-oac'].Id | [0]" \
    --output text 2>/dev/null
}

# ── cert ─────────────────────────────────────────────────────────────────────
phase_cert() {
  local arn; arn="$(cert_arn)"
  if [[ "$arn" == "None" || -z "$arn" ]]; then
    echo "==> Requesting ACM cert for $SITE + $WWW (us-east-1, DNS validation)..."
    arn="$(aws acm request-certificate --region "$REGION" \
      --domain-name "$SITE" \
      --subject-alternative-names "$WWW" \
      --validation-method DNS \
      --query CertificateArn --output text)"
    echo "    Cert: $arn"
    echo "    Waiting a few seconds for validation records to populate..."
    sleep 6
  else
    echo "==> Reusing existing cert: $arn"
  fi

  echo ""
  echo "==> Add these CNAME record(s) at Porkbun (cert validation):"
  echo "    (Porkbun strips the apex automatically — enter the host WITHOUT the trailing"
  echo "     '$SITE.'; if it wants the full name, paste it as-is.)"
  echo ""
  aws acm describe-certificate --region "$REGION" --certificate-arn "$arn" \
    --query "Certificate.DomainValidationOptions[].ResourceRecord.{Host:Name,Type:Type,Value:Value}" \
    --output table
  echo ""
  echo "After adding them, run: $0 status   (waits for ISSUED), then: $0 distribution"
}

# ── bucket ───────────────────────────────────────────────────────────────────
phase_bucket() {
  if aws s3api head-bucket --bucket "$SITE" 2>/dev/null; then
    echo "==> Bucket $SITE already exists."
  else
    echo "==> Creating private bucket: $SITE"
    aws s3api create-bucket --bucket "$SITE" --region "$REGION" >/dev/null
    aws s3api put-public-access-block --bucket "$SITE" \
      --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  fi

  [[ -d "$CONTENT_DIR" ]] || { echo "CONTENT_DIR not found: $CONTENT_DIR (run 'npm run build' first)"; exit 1; }
  echo "==> Syncing $CONTENT_DIR -> s3://$SITE"
  aws s3 sync "$CONTENT_DIR" "s3://$SITE" --delete
  # aws s3 sync guesses content-types from extensions but misses .webmanifest.
  if [[ -f "$CONTENT_DIR/manifest.webmanifest" ]]; then
    aws s3 cp "$CONTENT_DIR/manifest.webmanifest" "s3://$SITE/manifest.webmanifest" \
      --content-type "application/manifest+json" --metadata-directive REPLACE >/dev/null
  fi
  echo "    Upload complete."
}

# ── distribution ─────────────────────────────────────────────────────────────
phase_distribution() {
  local arn status; arn="$(cert_arn)"
  [[ "$arn" == "None" || -z "$arn" ]] && { echo "No cert found — run '$0 cert' first."; exit 1; }
  status="$(aws acm describe-certificate --region "$REGION" --certificate-arn "$arn" \
    --query Certificate.Status --output text)"
  if [[ "$status" != "ISSUED" ]]; then
    echo "Cert is '$status', not ISSUED. Add the validation CNAME(s) at Porkbun and wait."
    echo "Check with: $0 status"
    exit 1
  fi
  echo "==> Cert ISSUED: $arn"

  # OAC (idempotent).
  local oac; oac="$(oac_id_for_site)"
  if [[ "$oac" == "None" || -z "$oac" ]]; then
    echo "==> Creating Origin Access Control: $SITE-oac"
    oac="$(aws cloudfront create-origin-access-control --origin-access-control-config \
      "Name=$SITE-oac,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3" \
      --query OriginAccessControl.Id --output text)"
  else
    echo "==> Reusing OAC: $oac"
  fi

  local existing; existing="$(dist_id_for_site)"
  if [[ "$existing" != "None" && -n "$existing" ]]; then
    echo "==> Distribution already exists for $SITE: $existing — skipping create."
  else
    echo "==> Cloning template distribution $TEMPLATE_DIST -> new distribution for $SITE"
    local origin_id="S3-$SITE"
    local bucket_domain="$SITE.s3.$REGION.amazonaws.com"
    local caller="$SITE-$(date +%s)"
    local tmp; tmp="$(mktemp -t cf-dist-XXXX.json)"

    aws cloudfront get-distribution-config --id "$TEMPLATE_DIST" \
      --query DistributionConfig --output json \
    | python3 -c '
import json, sys
c = json.load(sys.stdin)
SITE, WWW, ORIGIN, BUCKET, OAC, CERT, CALLER = sys.argv[1:8]
c["CallerReference"] = CALLER
c["Comment"] = SITE + " static site"
c["Aliases"] = {"Quantity": 2, "Items": [SITE, WWW]}
o = c["Origins"]["Items"][0]
o["Id"] = ORIGIN
o["DomainName"] = BUCKET
o["OriginAccessControlId"] = OAC
c["DefaultCacheBehavior"]["TargetOriginId"] = ORIGIN
vc = c["ViewerCertificate"]
vc.pop("CloudFrontDefaultCertificate", None)
vc["ACMCertificateArn"] = CERT
vc["Certificate"] = CERT
vc["SSLSupportMethod"] = "sni-only"
vc["MinimumProtocolVersion"] = "TLSv1.2_2021"
vc["CertificateSource"] = "acm"
json.dump(c, sys.stdout)
' "$SITE" "$WWW" "$origin_id" "$bucket_domain" "$oac" "$arn" "$caller" > "$tmp"

    local result
    result="$(aws cloudfront create-distribution --distribution-config "file://$tmp")"
    existing="$(echo "$result" | python3 -c 'import json,sys;print(json.load(sys.stdin)["Distribution"]["Id"])')"
    rm -f "$tmp"
    echo "    Created distribution: $existing"
  fi

  # Scoped bucket policy: only this distribution may read the bucket.
  echo "==> Applying scoped bucket policy (OAC source-arn = $DIST_ARN_PREFIX/$existing)"
  aws s3api put-bucket-policy --bucket "$SITE" --policy "$(cat <<EOF
{"Version":"2008-10-17","Statement":[{
  "Sid":"AllowCloudFrontServicePrincipal","Effect":"Allow",
  "Principal":{"Service":"cloudfront.amazonaws.com"},"Action":"s3:GetObject",
  "Resource":"arn:aws:s3:::$SITE/*",
  "Condition":{"StringEquals":{"AWS:SourceArn":"$DIST_ARN_PREFIX/$existing"}}}]}
EOF
)"

  local cfdomain
  cfdomain="$(aws cloudfront get-distribution --id "$existing" \
    --query Distribution.DomainName --output text)"
  echo ""
  echo "============================================================"
  echo " CloudFront domain: $cfdomain"
  echo " Add these records at Porkbun, then wait ~15 min for deploy:"
  echo "   • $SITE   ALIAS  -> $cfdomain    (apex; Porkbun supports ALIAS)"
  echo "   • $WWW    CNAME  -> $cfdomain"
  echo " (Alternative: forward $WWW -> https://$SITE via Porkbun URL forwarding.)"
  echo "============================================================"
  echo "Then: $0 status  (until Deployed), and verify https://$SITE"
}

# ── status ───────────────────────────────────────────────────────────────────
phase_status() {
  local arn; arn="$(cert_arn)"
  if [[ "$arn" != "None" && -n "$arn" ]]; then
    echo "Cert ($SITE): $(aws acm describe-certificate --region "$REGION" \
      --certificate-arn "$arn" --query Certificate.Status --output text)"
  else
    echo "Cert ($SITE): none requested yet"
  fi
  local id; id="$(dist_id_for_site)"
  if [[ "$id" != "None" && -n "$id" ]]; then
    aws cloudfront get-distribution --id "$id" \
      --query "Distribution.{Id:Id,Status:Status,Domain:DomainName,Aliases:DistributionConfig.Aliases.Items}" \
      --output table
  else
    echo "Distribution: none created yet"
  fi
}

# ── deploy (content updates) ──────────────────────────────────────────────────
phase_deploy() {
  phase_bucket
  local id; id="$(dist_id_for_site)"
  [[ "$id" == "None" || -z "$id" ]] && { echo "No distribution for $SITE yet."; exit 1; }
  echo "==> Invalidating CloudFront cache for $id"
  aws cloudfront create-invalidation --distribution-id "$id" --paths "/*" \
    --query "Invalidation.{Id:Id,Status:Status}" --output table
}

case "$PHASE" in
  cert)         phase_cert ;;
  bucket)       phase_bucket ;;
  distribution) phase_distribution ;;
  status)       phase_status ;;
  deploy)       phase_deploy ;;
  *)
    echo "Usage: $0 <cert|bucket|distribution|status|deploy>"
    echo "  SITE=$SITE  REGION=$REGION  CONTENT_DIR=$CONTENT_DIR"
    exit 1 ;;
esac
