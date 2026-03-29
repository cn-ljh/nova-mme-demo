#!/usr/bin/env bash
# Set up the local development environment.
# Usage: ./scripts/setup-dev.sh
set -euo pipefail

echo "======================================================"
echo " Setting up development environment"
echo "======================================================"

# Python virtual environment
echo ""
echo "[1/4] Setting up Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r backend/requirements-dev.txt --quiet
echo "  Python venv ready: .venv"

# Frontend dependencies
echo ""
echo "[2/4] Installing frontend npm dependencies..."
cd frontend
npm install --silent
cd ..
echo "  npm packages installed"

# SAM CLI check
echo ""
echo "[3/4] Checking SAM CLI..."
if command -v sam &>/dev/null; then
    echo "  SAM CLI found: $(sam --version)"
else
    echo "  WARNING: SAM CLI not found. Install from https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
fi

# AWS CLI check
echo ""
echo "[4/4] Checking AWS CLI and credentials..."
if command -v aws &>/dev/null; then
    echo "  AWS CLI found: $(aws --version 2>&1 | head -1)"
    if aws sts get-caller-identity &>/dev/null; then
        echo "  AWS credentials: OK ($(aws sts get-caller-identity --query Account --output text))"
    else
        echo "  WARNING: AWS credentials not configured. Run: aws configure"
    fi
else
    echo "  WARNING: AWS CLI not found. Install from https://aws.amazon.com/cli/"
fi

echo ""
echo "======================================================"
echo " Development setup complete!"
echo ""
echo " Quick start:"
echo "  source .venv/bin/activate   # activate Python venv"
echo "  cd frontend && npm run dev  # start frontend dev server"
echo "  pytest backend/tests/       # run backend tests"
echo "======================================================"
