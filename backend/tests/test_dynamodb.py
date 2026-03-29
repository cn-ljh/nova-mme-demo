"""Unit tests for DynamoDB operations."""
import sys
import os
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "layers", "shared", "python"))

from tests.conftest import dynamodb_table, make_api_event  # noqa: F401


@mock_aws
def test_put_and_get_content(dynamodb_table):
    from shared.models import ContentItem
    from shared.dynamodb import put_content, get_content_by_id

    item = ContentItem(
        content_id="c1",
        user_id="u1",
        modality="image",
        filename="photo.jpg",
        file_size=1024,
        mime_type="image/jpeg",
        s3_key="uploads/u1/c1/photo.jpg",
        s3_bucket="test-bucket",
        created_at="2024-01-01T00:00:00Z",
    )
    put_content(item)
    result = get_content_by_id("c1")
    assert result is not None
    assert result["data"]["modality"] == "image"
    assert result["data"]["filename"] == "photo.jpg"


@mock_aws
def test_put_and_get_task(dynamodb_table):
    from shared.models import TaskItem
    from shared.dynamodb import put_task, get_task_by_id

    task = TaskItem(
        task_id="t1",
        user_id="u1",
        task_type="upload",
        modality="image",
        status="pending",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    put_task(task)
    result = get_task_by_id("t1")
    assert result is not None
    assert result["data"]["status"] == "pending"
    assert result["data"]["task_type"] == "upload"


@mock_aws
def test_update_task_status(dynamodb_table):
    from shared.models import TaskItem
    from shared.dynamodb import put_task, update_task_status, get_task_by_id

    task = TaskItem(
        task_id="t2",
        user_id="u1",
        task_type="upload",
        modality="video",
        status="pending",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    put_task(task)
    update_task_status("t2", "u1", "2024-01-01T00:00:00Z", "processing")
    result = get_task_by_id("t2")
    assert result["data"]["status"] == "processing"


@mock_aws
def test_update_task_to_failed_with_error(dynamodb_table):
    from shared.models import TaskItem
    from shared.dynamodb import put_task, update_task_status, get_task_by_id

    task = TaskItem(
        task_id="t3",
        user_id="u1",
        task_type="upload",
        modality="audio",
        status="processing",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    put_task(task)
    update_task_status("t3", "u1", "2024-01-01T00:00:00Z", "failed", error_message="Bedrock API error")
    result = get_task_by_id("t3")
    assert result["data"]["status"] == "failed"
    assert result["data"]["error_message"] == "Bedrock API error"


@mock_aws
def test_get_user_tasks_isolation(dynamodb_table):
    from shared.models import TaskItem
    from shared.dynamodb import put_task, get_user_tasks

    # Create tasks for two different users
    for uid, mid in [("user_a", "t_a1"), ("user_b", "t_b1"), ("user_a", "t_a2")]:
        task = TaskItem(
            task_id=mid,
            user_id=uid,
            task_type="upload",
            modality="text",
            status="completed",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        put_task(task)

    tasks_a, _ = get_user_tasks("user_a")
    tasks_b, _ = get_user_tasks("user_b")

    # Each user only sees their own tasks
    assert len(tasks_a) == 2
    assert len(tasks_b) == 1
    for t in tasks_a:
        assert t["PK"] == "USER#user_a"
    for t in tasks_b:
        assert t["PK"] == "USER#user_b"


@mock_aws
def test_update_task_status_invalid_raises(dynamodb_table):
    from shared.dynamodb import update_task_status

    with pytest.raises(ValueError, match="Invalid status"):
        update_task_status("t_bad", "u1", "2024-01-01T00:00:00Z", "invalid_status")
