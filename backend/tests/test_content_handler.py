"""Unit tests for Content Lambda handler."""
import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "layers", "shared", "python"))

from tests.conftest import make_api_event  # noqa: F401


@mock_aws
def test_request_upload_returns_presigned_url(dynamodb_table, s3_buckets):
    with patch("shared.s3_client.generate_presigned_upload_url") as mock_presigned:
        mock_presigned.return_value = {
            "url": "https://s3.amazonaws.com/test-bucket",
            "fields": {"key": "uploads/u1/c1/photo.jpg", "Content-Type": "image/jpeg"},
        }
        from content import handler as content_handler

        event = make_api_event(
            method="POST",
            path="/api/content/request-upload",
            body={"filename": "photo.jpg", "mime_type": "image/jpeg", "file_size": 1024},
        )
        response = content_handler.lambda_handler(event, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "content_id" in body
    assert "upload_url" in body


@mock_aws
def test_request_upload_rejects_unsupported_mime(dynamodb_table, s3_buckets):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from content import handler as content_handler

    event = make_api_event(
        method="POST",
        path="/api/content/request-upload",
        body={"filename": "file.exe", "mime_type": "application/x-executable", "file_size": 1024},
    )
    response = content_handler.lambda_handler(event, None)
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error_code"] == "UNSUPPORTED_FILE_FORMAT"


@mock_aws
def test_request_upload_rejects_oversized_image(dynamodb_table, s3_buckets):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from content import handler as content_handler

    event = make_api_event(
        method="POST",
        path="/api/content/request-upload",
        body={
            "filename": "huge.jpg",
            "mime_type": "image/jpeg",
            "file_size": 100 * 1024 * 1024,  # 100MB > 50MB image limit
        },
    )
    response = content_handler.lambda_handler(event, None)
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error_code"] == "FILE_TOO_LARGE"


@mock_aws
def test_upload_text_creates_task(dynamodb_table, s3_buckets, sqs_queues):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from content import handler as content_handler

    event = make_api_event(
        method="POST",
        path="/api/content/upload-text",
        body={"text": "This is a test document.", "title": "Test Doc"},
    )
    response = content_handler.lambda_handler(event, None)
    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert "task_id" in body
    assert body["modality"] == "text"
    assert body["status"] == "pending"


@mock_aws
def test_upload_text_rejects_too_long(dynamodb_table, s3_buckets, sqs_queues):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from content import handler as content_handler

    event = make_api_event(
        method="POST",
        path="/api/content/upload-text",
        body={"text": "x" * 50_001},
    )
    response = content_handler.lambda_handler(event, None)
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert body["error_code"] == "TEXT_TOO_LONG"


@mock_aws
def test_get_content_forbidden_for_other_user(dynamodb_table, s3_buckets):
    """A user cannot access another user's content."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from content import handler as content_handler
    from shared.models import ContentItem
    from shared.dynamodb import put_content

    # Create content owned by user_a
    content = ContentItem(
        content_id="c_other",
        user_id="user_a",
        modality="image",
        filename="photo.jpg",
        file_size=1024,
        mime_type="image/jpeg",
        s3_key="uploads/user_a/c_other/photo.jpg",
        s3_bucket="test-content-bucket",
        created_at="2024-01-01T00:00:00Z",
    )
    put_content(content)

    # user_b tries to access it
    event = make_api_event(
        method="GET",
        path="/api/content/c_other",
        path_params={"content_id": "c_other"},
        user_id="user_b",
    )
    response = content_handler.lambda_handler(event, None)
    assert response["statusCode"] == 403


@mock_aws
def test_unauthorized_request_rejected(dynamodb_table, s3_buckets):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from content import handler as content_handler

    event = {
        "httpMethod": "GET",
        "path": "/api/content/c1",
        "pathParameters": {"content_id": "c1"},
        "queryStringParameters": {},
        "body": None,
        "requestContext": {"requestId": "test", "authorizer": {"claims": {}}},
    }
    response = content_handler.lambda_handler(event, None)
    assert response["statusCode"] == 401
