"""Shared pytest fixtures for all backend tests."""
import json
import os
import sys
import uuid

import boto3
import pytest
from moto import mock_aws

# Add shared layer to path for local testing
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "layers", "shared", "python"),
)

# ── AWS environment stubs ──────────────────────────────────────────────────────
AWS_REGION = "us-east-1"
TABLE_NAME = "test-content-table"
CONTENT_BUCKET = "test-content-bucket"
EMBEDDINGS_OUTPUT_BUCKET = "test-embeddings-output"
VECTOR_BUCKET_NAME = "test-vectors"
VECTOR_INDEX_NAME = "content-embeddings"
EMBEDDING_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue"
LARGE_FILE_EMBEDDING_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/test-large-queue"

os.environ.update({
    "AWS_DEFAULT_REGION": AWS_REGION,
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "TABLE_NAME": TABLE_NAME,
    "CONTENT_BUCKET": CONTENT_BUCKET,
    "EMBEDDINGS_OUTPUT_BUCKET": EMBEDDINGS_OUTPUT_BUCKET,
    "VECTOR_BUCKET_NAME": VECTOR_BUCKET_NAME,
    "VECTOR_INDEX_NAME": VECTOR_INDEX_NAME,
    "EMBEDDING_QUEUE_URL": EMBEDDING_QUEUE_URL,
    "LARGE_FILE_EMBEDDING_QUEUE_URL": LARGE_FILE_EMBEDDING_QUEUE_URL,
    "BEDROCK_MODEL_ID": "amazon.nova-2-multimodal-embeddings-v1:0",
    "BEDROCK_REGION": AWS_REGION,
    "STAGE": "test",
    "USER_POOL_ID": "us-east-1_TESTPOOL",
    "USER_POOL_CLIENT_ID": "testclientid",
    "CLOUDFRONT_KEY_PAIR_ID": "",
    "CLOUDFRONT_PRIVATE_KEY_SECRET_ARN": "",
    "CLOUDFRONT_DOMAIN": "",
    "POWERTOOLS_SERVICE_NAME": "test",
    "LOG_LEVEL": "WARNING",
})


# ── DynamoDB fixture ───────────────────────────────────────────────────────────

@pytest.fixture
def dynamodb_table():
    """Create a mocked DynamoDB table with the single-table schema."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = ddb.create_table(
            TableName=TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        table.wait_until_exists()
        yield table


@pytest.fixture
def s3_buckets():
    """Create mocked S3 buckets."""
    with mock_aws():
        s3 = boto3.client("s3", region_name=AWS_REGION)
        for bucket in [CONTENT_BUCKET, EMBEDDINGS_OUTPUT_BUCKET]:
            s3.create_bucket(Bucket=bucket)
        yield s3


@pytest.fixture
def sqs_queues():
    """Create mocked SQS queues."""
    with mock_aws():
        sqs = boto3.client("sqs", region_name=AWS_REGION)
        q1 = sqs.create_queue(QueueName="test-queue")
        q2 = sqs.create_queue(QueueName="test-large-queue")
        yield sqs, q1["QueueUrl"], q2["QueueUrl"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_api_event(
    method: str = "GET",
    path: str = "/",
    body: dict | None = None,
    path_params: dict | None = None,
    query_params: dict | None = None,
    user_id: str = "test-user-123",
    request_id: str = "test-request-id",
) -> dict:
    """Build a minimal API Gateway proxy event."""
    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
        "body": json.dumps(body) if body else None,
        "requestContext": {
            "requestId": request_id,
            "authorizer": {
                "claims": {
                    "sub": user_id,
                    "cognito:username": "testuser",
                    "email": "test@example.com",
                }
            },
        },
    }


def make_sqs_event(messages: list[dict]) -> dict:
    """Build a minimal SQS trigger event."""
    return {
        "Records": [
            {
                "messageId": str(uuid.uuid4()),
                "receiptHandle": "test-receipt",
                "body": json.dumps(msg),
                "attributes": {},
                "messageAttributes": {},
                "md5OfBody": "test",
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:test-queue",
                "awsRegion": AWS_REGION,
            }
            for msg in messages
        ]
    }
