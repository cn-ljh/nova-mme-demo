"""Unit tests for Task Lambda handler."""
import json
import sys
import os
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "layers", "shared", "python"))

from tests.conftest import make_api_event  # noqa: F401


@mock_aws
def test_list_tasks_returns_only_user_tasks(dynamodb_table):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from task import handler as task_handler
    from shared.models import TaskItem
    from shared.dynamodb import put_task

    user_a = "user_a"
    user_b = "user_b"

    for uid, tid in [(user_a, "ta1"), (user_a, "ta2"), (user_b, "tb1")]:
        task = TaskItem(
            task_id=tid,
            user_id=uid,
            task_type="upload",
            modality="text",
            status="completed",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        put_task(task)

    event = make_api_event(method="GET", path="/api/tasks", user_id=user_a)
    response = task_handler.lambda_handler(event, None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["count"] == 2


@mock_aws
def test_get_task_ownership_enforced(dynamodb_table):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from task import handler as task_handler
    from shared.models import TaskItem
    from shared.dynamodb import put_task

    task = TaskItem(
        task_id="t_owned_by_a",
        user_id="user_a",
        task_type="upload",
        modality="image",
        status="completed",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    put_task(task)

    # user_b tries to access user_a's task
    event = make_api_event(
        method="GET",
        path="/api/tasks/t_owned_by_a",
        path_params={"task_id": "t_owned_by_a"},
        user_id="user_b",
    )
    response = task_handler.lambda_handler(event, None)
    assert response["statusCode"] == 403


@mock_aws
def test_get_nonexistent_task_returns_404(dynamodb_table):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from task import handler as task_handler

    event = make_api_event(
        method="GET",
        path="/api/tasks/nonexistent",
        path_params={"task_id": "nonexistent"},
    )
    response = task_handler.lambda_handler(event, None)
    assert response["statusCode"] == 404


@mock_aws
def test_list_tasks_status_filter(dynamodb_table):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from task import handler as task_handler
    from shared.models import TaskItem
    from shared.dynamodb import put_task

    uid = "user_filter"
    for tid, status in [("t1", "pending"), ("t2", "completed"), ("t3", "failed")]:
        task = TaskItem(
            task_id=tid,
            user_id=uid,
            task_type="upload",
            modality="text",
            status=status,
            created_at=f"2024-01-0{tid[1]}T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        put_task(task)

    event = make_api_event(
        method="GET",
        path="/api/tasks",
        query_params={"status": "completed"},
        user_id=uid,
    )
    response = task_handler.lambda_handler(event, None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    # All returned tasks must have status=completed
    for task in body["tasks"]:
        assert task["status"] == "completed"


@mock_aws
def test_list_tasks_invalid_status_filter(dynamodb_table):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
    from task import handler as task_handler

    event = make_api_event(
        method="GET",
        path="/api/tasks",
        query_params={"status": "invalid_status"},
    )
    response = task_handler.lambda_handler(event, None)
    assert response["statusCode"] == 400
