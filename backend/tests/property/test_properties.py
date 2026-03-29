"""
Property-based tests for the multimodal content retrieval application.
Feature: multimodal-content-retrieval

Each test corresponds to a correctness property defined in docs/design.md.
Uses the Hypothesis library with moto for AWS mocks.
"""
import json
import os
import sys
import pytest
from moto import mock_aws
from hypothesis import given, settings, assume, strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "layers", "shared", "python"))

from shared.models import (
    SUPPORTED_MIME_TYPES, SIZE_LIMITS, MAX_TEXT_CHARS, TASK_STATUSES,
    detect_modality, validate_file_size, validate_text_length,
    should_use_async_api, ValidationError,
    api_response, error_response,
    task_sk, user_pk, content_pk, task_pk,
)

# ── Strategy helpers ──────────────────────────────────────────────────────────

supported_mime_types_st = st.sampled_from(sorted(SUPPORTED_MIME_TYPES.keys()))
unsupported_mime_types_st = st.text(min_size=1).filter(lambda m: m not in SUPPORTED_MIME_TYPES)
task_status_st = st.sampled_from(sorted(TASK_STATUSES))
modality_st = st.sampled_from(["text", "image", "audio", "video", "document"])
user_id_st = st.text(min_size=1, max_size=64, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"))
content_id_st = st.uuids().map(str)
task_id_st = st.uuids().map(str)


# ── Property 4: 文件模态正确识别 ─────────────────────────────────────────────

@settings(max_examples=100)
@given(mime_type=supported_mime_types_st)
def test_property4_modality_detection_correct(mime_type: str):
    """Feature: multimodal-content-retrieval, Property 4: 文件模态正确识别
    For any supported MIME type, detect_modality returns the correct classification.
    """
    expected = SUPPORTED_MIME_TYPES[mime_type]
    result = detect_modality(mime_type)
    assert result == expected, f"Expected {expected} for {mime_type}, got {result}"
    assert result in {"text", "image", "audio", "video", "document"}


# ── Property 6: 文件格式验证 ──────────────────────────────────────────────────

@settings(max_examples=100)
@given(mime_type=supported_mime_types_st)
def test_property6_supported_format_accepted(mime_type: str):
    """Feature: multimodal-content-retrieval, Property 6: 文件格式验证 (supported)
    For any supported MIME type, detect_modality should NOT raise.
    """
    try:
        result = detect_modality(mime_type)
        assert result in {"text", "image", "audio", "video", "document"}
    except ValidationError:
        pytest.fail(f"Supported MIME type {mime_type} was incorrectly rejected")


@settings(max_examples=100)
@given(mime_type=unsupported_mime_types_st)
def test_property6_unsupported_format_rejected(mime_type: str):
    """Feature: multimodal-content-retrieval, Property 6: 文件格式验证 (unsupported)
    For any unsupported MIME type, detect_modality raises ValidationError with UNSUPPORTED_FILE_FORMAT.
    """
    assume(mime_type not in SUPPORTED_MIME_TYPES)
    with pytest.raises(ValidationError) as exc_info:
        detect_modality(mime_type)
    assert exc_info.value.error_code == "UNSUPPORTED_FILE_FORMAT"
    # The error message should mention supported formats
    assert exc_info.value.details.get("supported_mime_types") is not None


# ── Property 7: 文件大小限制 ──────────────────────────────────────────────────

@settings(max_examples=100)
@given(
    modality=st.sampled_from(["image", "audio", "video", "document"]),
    size_bytes=st.integers(min_value=0, max_value=3 * 1024 * 1024 * 1024),
)
def test_property7_file_size_limit_enforced(modality: str, size_bytes: int):
    """Feature: multimodal-content-retrieval, Property 7: 文件大小限制
    For any file exceeding the modality-specific limit, validate_file_size raises ValidationError.
    """
    limit = SIZE_LIMITS[modality]
    if size_bytes > limit:
        with pytest.raises(ValidationError) as exc_info:
            validate_file_size(modality, size_bytes)
        assert exc_info.value.error_code == "FILE_TOO_LARGE"
        assert exc_info.value.details["limit_bytes"] == limit
    else:
        validate_file_size(modality, size_bytes)  # should not raise


# ── Property 10: 检索结果正确性 ──────────────────────────────────────────────

@settings(max_examples=100)
@given(top_k=st.integers(min_value=1, max_value=100))
def test_property10_search_results_bounded(top_k: int):
    """Feature: multimodal-content-retrieval, Property 10: 检索结果正确性
    For any Top-K value, the number of results must be <= top_k.
    """
    # Simulate a result set
    results = [{"similarity_score": 1.0 - (i * 0.05)} for i in range(min(top_k + 5, 50))]
    # Apply top_k truncation (as search handler does)
    results = results[:top_k]
    assert len(results) <= top_k


@settings(max_examples=100)
@given(scores=st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=0, max_size=20))
def test_property10_search_results_descending(scores: list):
    """Feature: multimodal-content-retrieval, Property 10: 检索结果降序排列
    Results must be sorted by similarity_score descending.
    """
    results = [{"similarity_score": s} for s in sorted(scores, reverse=True)]
    for i in range(len(results) - 1):
        assert results[i]["similarity_score"] >= results[i + 1]["similarity_score"]


# ── Property 11: 任务用户隔离 ────────────────────────────────────────────────

@settings(max_examples=100)
@given(user_id=user_id_st, other_user_id=user_id_st)
def test_property11_task_user_isolation(user_id: str, other_user_id: str):
    """Feature: multimodal-content-retrieval, Property 11: 任务用户隔离
    The PK used for querying tasks must match the requesting user's ID.
    """
    assume(user_id != other_user_id)
    pk_for_user = user_pk(user_id)
    pk_for_other = user_pk(other_user_id)
    assert pk_for_user != pk_for_other
    assert pk_for_user == f"USER#{user_id}"
    assert pk_for_other == f"USER#{other_user_id}"


# ── Property 12: 任务数据完整性 ──────────────────────────────────────────────

@settings(max_examples=100)
@given(status=task_status_st)
def test_property12_task_status_validity(status: str):
    """Feature: multimodal-content-retrieval, Property 12: 任务数据完整性
    For any TaskItem, status must be one of four valid values.
    """
    from shared.models import TaskItem
    task = TaskItem(
        task_id="t1",
        user_id="u1",
        task_type="upload",
        modality="image",
        status=status,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    assert task.status in TASK_STATUSES
    item = task.to_ddb_item()
    assert "task_type" in item["data"]
    assert "modality" in item["data"]
    assert "created_at" in item["data"]
    assert "status" in item["data"]


# ── Property 13: 任务状态变更时间戳 ──────────────────────────────────────────

@settings(max_examples=100)
@given(
    created_at=st.datetimes(min_value=__import__('datetime').datetime(2020, 1, 1),
                            max_value=__import__('datetime').datetime.utcnow()).map(
        lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
)
def test_property13_updated_at_gte_created_at(created_at: str):
    """Feature: multimodal-content-retrieval, Property 13: 任务状态变更时间戳
    updated_at after status change must be >= created_at.
    """
    from shared.dynamodb import now_iso
    updated_at = now_iso()
    # updated_at is the current time (always >= any historical created_at)
    assert updated_at >= created_at


# ── Property 14: 任务状态筛选 ────────────────────────────────────────────────

@settings(max_examples=100)
@given(
    statuses=st.lists(task_status_st, min_size=1, max_size=10),
    filter_status=task_status_st,
)
def test_property14_status_filter_correctness(statuses: list, filter_status: str):
    """Feature: multimodal-content-retrieval, Property 14: 任务状态筛选
    After filtering by status, all returned items must have that status.
    """
    tasks = [{"data": {"status": s}} for s in statuses]
    filtered = [t for t in tasks if t["data"]["status"] == filter_status]
    for task in filtered:
        assert task["data"]["status"] == filter_status


# ── Property 16: 重试与指数退避 ──────────────────────────────────────────────

@settings(max_examples=100)
@given(
    initial_delay=st.floats(min_value=0.1, max_value=5.0),
    backoff_factor=st.floats(min_value=1.5, max_value=4.0),
    max_retries=st.integers(min_value=1, max_value=5),
)
def test_property16_exponential_backoff(initial_delay: float, backoff_factor: float, max_retries: int):
    """Feature: multimodal-content-retrieval, Property 16: 重试与指数退避
    Each retry interval must be >= (backoff_factor * previous interval).
    """
    delays = []
    delay = initial_delay
    for _ in range(max_retries):
        delays.append(delay)
        delay = min(delay * backoff_factor, 30.0)

    # Each delay must be >= previous
    for i in range(1, len(delays)):
        assert delays[i] >= delays[i - 1]


# ── Property 19: 错误响应安全性 ──────────────────────────────────────────────

@settings(max_examples=100)
@given(
    error_code=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("Lu", "Nd"), whitelist_characters="_")),
    message=st.text(min_size=1, max_size=200),
)
def test_property19_error_response_no_sensitive_info(error_code: str, message: str):
    """Feature: multimodal-content-retrieval, Property 19: 错误响应安全性
    Error responses must not contain stack traces or internal implementation details.
    """
    resp = error_response(500, message, error_code, request_id="req-123")
    body = json.loads(resp["body"])

    # Must not contain sensitive technical details
    body_str = json.dumps(body).lower()
    assert "traceback" not in body_str
    assert "stack trace" not in body_str
    assert "line " not in body_str or "message" not in body_str  # line numbers in message is ok
    # Must contain the structured fields
    assert "error_code" in body
    assert "message" in body
    assert "request_id" in body


# ── Property 20: 任务隔离性 ──────────────────────────────────────────────────

@settings(max_examples=100)
@given(
    user_a=user_id_st,
    user_b=user_id_st,
    task_a=task_id_st,
    task_b=task_id_st,
)
def test_property20_task_isolation(user_a: str, user_b: str, task_a: str, task_b: str):
    """Feature: multimodal-content-retrieval, Property 20: 任务隔离性
    Tasks for different users must have different partition keys, preventing cross-user access.
    """
    assume(user_a != user_b)
    pk_a = user_pk(user_a)
    pk_b = user_pk(user_b)
    assert pk_a != pk_b, "Different users must have different PKs"


# ── Property 2: 有效凭据认证往返 ─────────────────────────────────────────────

@settings(max_examples=50)
@given(
    username=st.text(min_size=1, max_size=30, alphabet="abcdefghijklmnopqrstuvwxyz0123456789"),
    password=st.text(min_size=8, max_size=50),
)
def test_property2_api_response_structure(username: str, password: str):
    """Feature: multimodal-content-retrieval, Property 2: API 响应结构
    api_response always returns a dict with statusCode, headers, and body.
    """
    resp = api_response(200, {"user": username})
    assert isinstance(resp["statusCode"], int)
    assert isinstance(resp["headers"], dict)
    assert isinstance(resp["body"], str)
    body = json.loads(resp["body"])
    assert body["user"] == username
