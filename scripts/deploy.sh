#!/usr/bin/env bash
# Deploy backend (SAM) to AWS.
# Usage: ./scripts/deploy.sh [dev|staging|prod] [--guided]
set -euo pipefail

STAGE="${1:-dev}"
GUIDED="${2:-}"
STACK_NAME="multimodal-retrieval-${STAGE}"
REGION="${AWS_REGION:-us-east-1}"
S3_BUCKET="${SAM_DEPLOY_BUCKET:-${STACK_NAME}-sam-artifacts-$(aws sts get-caller-identity --query Account --output text)}"

echo "======================================================"
echo " Deploying: ${STACK_NAME}  region: ${REGION}"
echo "======================================================"

# Ensure SAM artifacts bucket exists
if ! aws s3 ls "s3://${S3_BUCKET}" >/dev/null 2>&1; then
    echo "Creating SAM artifacts bucket: ${S3_BUCKET}"
    aws s3 mb "s3://${S3_BUCKET}" --region "${REGION}"
    aws s3api put-bucket-versioning --bucket "${S3_BUCKET}" \
        --versioning-configuration Status=Enabled
fi

# Build
echo ""
echo "Building SAM application..."
sam build --parallel

# Deploy
echo ""
echo "Deploying SAM stack..."
SAM_ARGS=(
    --stack-name "${STACK_NAME}"
    --s3-bucket "${S3_BUCKET}"
    --region "${REGION}"
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND
    --parameter-overrides "Stage=${STAGE}"
    --no-fail-on-empty-changeset
)

if [[ "${GUIDED}" == "--guided" ]]; then
    sam deploy --guided "${SAM_ARGS[@]}"
else
    sam deploy "${SAM_ARGS[@]}"
fi

# Extract outputs
echo ""
echo "Retrieving stack outputs..."
OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs" \
    --output json)

CLOUDFRONT_DOMAIN=$(echo "${OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'CloudFrontDomain':
        print(o['OutputValue'])
        break
")
USER_POOL_ID=$(echo "${OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'UserPoolId':
        print(o['OutputValue'])
        break
")
USER_POOL_CLIENT_ID=$(echo "${OUTPUTS}" | python3 -c "
import json, sys
outputs = json.load(sys.stdin)
for o in outputs:
    if o['OutputKey'] == 'UserPoolClientId':
        print(o['OutputValue'])
        break
")

echo ""
echo "======================================================"
echo " Deployment complete!"
echo "======================================================"
echo " CloudFront:      ${CLOUDFRONT_DOMAIN}"
echo " UserPoolId:      ${USER_POOL_ID}"
echo " UserPoolClient:  ${USER_POOL_CLIENT_ID}"
echo ""
echo " Next steps:"
echo "  1. Update frontend/.env.local with these values"
echo "  2. Run: ./scripts/deploy-frontend.sh ${STAGE}"
echo "  3. Set the CloudFront private key in Secrets Manager"
echo "======================================================"

# Write env file for frontend
cat > "frontend/.env.local" <<EOF
VITE_API_URL=${CLOUDFRONT_DOMAIN}
VITE_USER_POOL_ID=${USER_POOL_ID}
VITE_USER_POOL_CLIENT_ID=${USER_POOL_CLIENT_ID}
VITE_AWS_REGION=${REGION}
VITE_CLOUDFRONT_DOMAIN=${CLOUDFRONT_DOMAIN}
EOF

echo " Frontend .env.local has been updated."
