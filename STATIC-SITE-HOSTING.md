# Static-site hosting for a board (S3 + CloudFront + OAC)

> **Status: manual CLI runbook today; TODO codify as a CDK `StaticSiteConstruct`.**
> Every board's MCP server is already provisioned by CDK (`infra/lib/constructs/giga-mcp-server-service.ts`). Its public-facing static site (the PWA / landing page) is **not** yet in CDK — it's set up by hand with the steps below. This doc captures the exact steps so they can be lifted into a construct and instantiated per board alongside the App Runner service.

## The pattern (mirror `www.gigacorp.co`, not `pitchvault.co`)

There are two static-site patterns live in this account. Use the **gigacorp** one:

| | gigacorp.co ✅ use this | pitchvault.co ❌ legacy |
| --- | --- | --- |
| S3 origin | REST endpoint (`<bucket>.s3.<region>.amazonaws.com`) | website endpoint (`<bucket>.s3-website-<region>...`) |
| Origin Access | **OAC** (Origin Access Control, sigv4) | none |
| Bucket public access | **fully blocked** (private) | fully open (world-readable) |
| CloudFront → S3 | HTTPS | http-only |
| Direct-to-S3 bypass | impossible | possible (S3 website URL is public) |

The gigacorp pattern keeps the bucket private and readable only by its CloudFront distribution via an OAC-signed request — the modern AWS-recommended setup.

## Prerequisites

- A Route 53 hosted zone for the parent domain (e.g. `gigacorp.co` → `Z08385601B5HCX1AG6EO1`).
- A **wildcard ACM cert** in **us-east-1** covering the subdomain. `gigacorp.co`'s cert is `*.gigacorp.co` (`arn:aws:acm:us-east-1:138606625420:certificate/a48766b5-50fc-47bd-84d5-394962e341d0`), so any `*.gigacorp.co` site reuses it — **no new cert / no DNS validation needed**. A new parent domain needs its own wildcard cert first.

## Steps (parameterized; worked example = `punch.gigacorp.co`)

```bash
SITE=punch.gigacorp.co
REGION=us-east-1
ZONE=Z08385601B5HCX1AG6EO1
CERT_ARN=arn:aws:acm:us-east-1:138606625420:certificate/a48766b5-50fc-47bd-84d5-394962e341d0
CF_ZONE=Z2FDTNDATAQYW2   # fixed CloudFront alias hosted-zone id

# 1. Private bucket (bucket name == site domain)
aws s3api create-bucket --bucket "$SITE" --region "$REGION"
aws s3api put-public-access-block --bucket "$SITE" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# 2. Upload content (set content-types explicitly)
aws s3 cp index.html "s3://$SITE/index.html" --content-type "text/html; charset=utf-8"
# ...other assets (image/png, etc.)

# 3. Origin Access Control (S3, sigv4, always-sign)
aws cloudfront create-origin-access-control --origin-access-control-config \
  "Name=$SITE-oac,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3"
#   -> note the OAC Id

# 4. CloudFront distribution (see /tmp/punch-dist.json shape used 2026-05-29):
#    - Origin DomainName: $SITE.s3.$REGION.amazonaws.com  (REST, NOT website endpoint)
#    - S3OriginConfig.OriginAccessIdentity: ""  + OriginAccessControlId: <OAC Id>
#    - Aliases: [$SITE]
#    - ViewerCertificate: ACMCertificateArn=$CERT_ARN, sni-only, TLSv1.2_2021
#    - DefaultRootObject: index.html
#    - DefaultCacheBehavior: redirect-to-https, GET/HEAD, Compress, managed CachePolicy
#      CachingOptimized (658327ea-f89d-4fab-a63d-7e88639e58f6)
#    - CustomErrorResponses: 403 & 404 -> /index.html (200)  [coming-soon: any path serves the page]
#    - PriceClass_100
aws cloudfront create-distribution --distribution-config file://dist.json
#   -> note the Distribution Id, DomainName, ARN

# 5. Bucket policy: allow ONLY this distribution (OAC service principal + SourceArn condition).
#    Note: this scoped policy is NOT considered "public", so BlockPublicPolicy=true is fine.
aws s3api put-bucket-policy --bucket "$SITE" --policy '{
  "Version":"2008-10-17","Statement":[{
    "Sid":"AllowCloudFrontServicePrincipal","Effect":"Allow",
    "Principal":{"Service":"cloudfront.amazonaws.com"},"Action":"s3:GetObject",
    "Resource":"arn:aws:s3:::'"$SITE"'/*",
    "Condition":{"StringEquals":{"AWS:SourceArn":"<DIST_ARN>"}}}]}'

# 6. Route 53 A + AAAA alias -> CloudFront (alias target zone is always Z2FDTNDATAQYW2)
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" --change-batch '{
  "Changes":[
    {"Action":"UPSERT","ResourceRecordSet":{"Name":"'"$SITE"'","Type":"A",
      "AliasTarget":{"HostedZoneId":"'"$CF_ZONE"'","DNSName":"<cf-domain>","EvaluateTargetHealth":false}}},
    {"Action":"UPSERT","ResourceRecordSet":{"Name":"'"$SITE"'","Type":"AAAA",
      "AliasTarget":{"HostedZoneId":"'"$CF_ZONE"'","DNSName":"<cf-domain>","EvaluateTargetHealth":false}}}]}'
```

CloudFront takes ~10–15 min to reach `Deployed`. After that + DNS propagation, `https://$SITE` serves the site. To push content updates later: re-`aws s3 cp` and (if needed) `aws cloudfront create-invalidation --distribution-id <id> --paths "/*"`.

## punch.gigacorp.co — provisioned 2026-05-29

- Bucket: `punch.gigacorp.co` (private, PAB on)
- OAC: `E8DZ0QVCTUY3J`
- Distribution: `E3SQXA18GBFLVP` (`d146qwuxkc1gxj.cloudfront.net`)
- Cert: reused wildcard `*.gigacorp.co`
- Content source: `~/dev/punch-pwa/landing/` (coming-soon placeholder)

## TODO — codify as `StaticSiteConstruct`

Add `infra/lib/constructs/static-site.ts` taking `{ domain, certArn, hostedZoneId, contentPath }` and creating the bucket + OAC + distribution + bucket policy + Route 53 alias + a `BucketDeployment` of `contentPath`. Instantiate per board in the stack next to `GigaMcpServerService`, gated by an optional `staticSite?: { domain; contentPath }` field on `BoardConfig`. Reuse the parent domain's wildcard cert when present. This makes a board's PWA hosting a one-line config change, matching the App Runner story.
