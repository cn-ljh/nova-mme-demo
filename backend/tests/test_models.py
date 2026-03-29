"""Unit tests for shared/models.py validation logic."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "layers", "shared", "python"))

from shared.models import (
    detect_modality, validate_file_size, validate_text_length,
    should_use_async_api, ValidationError,
    SUPPORTED_MIME_TYPES, SIZE_LIMITS, MAX_TEXT_CHARS,
    user_pk, content_sk, task_sk, content_pk, task_pk,
    api_response, error_response,
)


class TestDetectModality:
    def test_image_types(self):
        for mime in ["image/png", "image/jpeg", "image/webp", "image/gif"]:
            assert detect_modality(mime) == "image"

    def test_audio_types(self):
        for mime in ["audio/mpeg", "audio/wav", "audio/ogg"]:
            assert detect_modality(mime) == "audio"

    def test_video_types(self):
        for mime in ["video/mp4", "video/quicktime", "video/webm"]:
            assert detect_modality(mime) == "video"

    def test_document_types(self):
        for mime in ["application/pdf", "text/plain"]:
            assert detect_modality(mime) == "document"

    def test_unsupported_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            detect_modality("application/x-unsupported")
        assert exc_info.value.error_code == "UNSUPPORTED_FILE_FORMAT"
        assert "supported formats" in exc_info.value.message.lower() or "Supported" in exc_info.value.message

    def test_case_insensitive(self):
        assert detect_modality("IMAGE/PNG") == "image"

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            detect_modality("")


class TestValidateFileSize:
    def test_image_within_limit(self):
        validate_file_size("image", 10 * 1024 * 1024)  # 10MB - no exception

    def test_image_at_limit(self):
        validate_file_size("image", 50 * 1024 * 1024)  # exactly 50MB - no exception

    def test_image_exceeds_limit(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_file_size("image", 51 * 1024 * 1024)
        assert exc_info.value.error_code == "FILE_TOO_LARGE"

    def test_video_within_limit(self):
        validate_file_size("video", 1 * 1024 * 1024 * 1024)  # 1GB - ok

    def test_video_exceeds_limit(self):
        with pytest.raises(ValidationError):
            validate_file_size("video", 3 * 1024 * 1024 * 1024)

    def test_text_modality_no_limit(self):
        # Text uses character limits, not byte limits
        validate_file_size("text", 10 * 1024 * 1024 * 1024)  # no exception


class TestValidateTextLength:
    def test_within_limit(self):
        validate_text_length("a" * 50_000)  # no exception

    def test_exceeds_limit(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_text_length("a" * 50_001)
        assert exc_info.value.error_code == "TEXT_TOO_LONG"

    def test_empty_text(self):
        validate_text_length("")  # no exception


class TestShouldUseAsyncApi:
    def test_text_always_sync(self):
        assert not should_use_async_api("text", 10 * 1024 * 1024)
        assert not should_use_async_api("text", 100 * 1024 * 1024)

    def test_image_always_sync(self):
        assert not should_use_async_api("image", 50 * 1024 * 1024)

    def test_small_audio_sync(self):
        assert not should_use_async_api("audio", 50 * 1024 * 1024, duration_seconds=20)

    def test_large_audio_async_by_size(self):
        assert should_use_async_api("audio", 200 * 1024 * 1024)

    def test_large_audio_async_by_duration(self):
        assert should_use_async_api("audio", 10 * 1024 * 1024, duration_seconds=60)

    def test_small_video_sync(self):
        assert not should_use_async_api("video", 50 * 1024 * 1024, duration_seconds=25)

    def test_large_video_async(self):
        assert should_use_async_api("video", 500 * 1024 * 1024)


class TestDynamoDBKeys:
    def test_user_pk(self):
        assert user_pk("abc") == "USER#abc"

    def test_content_sk(self):
        assert content_sk("xyz") == "CONTENT#xyz"

    def test_task_sk_format(self):
        sk = task_sk("2024-01-01T00:00:00Z", "task123")
        assert sk == "TASK#2024-01-01T00:00:00Z#task123"

    def test_content_pk(self):
        assert content_pk("abc") == "CONTENT#abc"

    def test_task_pk(self):
        assert task_pk("abc") == "TASK#abc"


class TestApiResponse:
    def test_api_response_structure(self):
        resp = api_response(200, {"key": "value"})
        assert resp["statusCode"] == 200
        assert "Access-Control-Allow-Origin" in resp["headers"]
        import json
        body = json.loads(resp["body"])
        assert body == {"key": "value"}

    def test_error_response_no_technical_details(self):
        resp = error_response(500, "Internal server error", "INTERNAL_ERROR", request_id="req-1")
        import json
        body = json.loads(resp["body"])
        assert body["error_code"] == "INTERNAL_ERROR"
        assert body["message"] == "Internal server error"
        assert "stacktrace" not in body
        assert "traceback" not in body
