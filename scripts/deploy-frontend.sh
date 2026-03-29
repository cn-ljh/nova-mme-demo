#!/usr/bin/env bash
# Build and deploy frontend to S3 + CloudFront invalidation.
# Usage: ./scripts/deploy-frontend.sh [dev|staging|prod]
set -euo pipefail

STAGE="${1:-dev}"
STACK_NAME="multimodal-retrieval-${STAGE}"
REGION="${AWS_REGION:-us-east-1}"

echo "======================================================"
echo " Building and deploying frontend (stage: ${STAGE})"
echo "======================================================"

# Verify env file exists
if [[ ! -f "frontend/.env.local" ]]; then
    echo "ERROR: frontend/.env.local not found. Run ./scripts/deploy.sh first."
    exit 1
fi

cd frontend

# Install dependencies
echo "Installing npm dependencies..."
npm ci --silent

# Build
echo "Building React application..."
npm run build

cd ..

# Get the frontend S3 bucket name
FRONTEND_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" \
    --output text)

DISTRIBUTION_ID=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomain'].OutputValue" \
    --output text | grep -o 'E[A-Z0-9]*' | head -1)

echo ""
echo "Uploading to S3 bucket: ${FRONTEND_BUCKET}"

# Sync with cache headers
aws s3 sync frontend/dist "s3://${FRONTEND_BUCKET}" \
    --region "${REGION}" \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "index.html" \
    --delete

# Upload index.html with no-cache (so new deployments are picked up immediately)
aws s3 cp frontend/dist/index.html "s3://${FRONTEND_BUCKET}/index.html" \
    --region "${REGION}" \
    --cache-control "no-cache, no-store, must-revalidate"

# Invalidate CloudFront cache
if [[ -n "${DISTRIBUTION_ID}" ]]; then
    echo "Creating CloudFront invalidation..."
    aws cloudfront create-invalidation \
        --distribution-id "${DISTRIBUTION_ID}" \
        --paths "/*" \
        --region us-east-1
fi

echo ""
echo "======================================================"
echo " Frontend deployment complete!"
echo "======================================================"
