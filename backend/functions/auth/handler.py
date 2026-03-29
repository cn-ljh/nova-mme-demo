"""Auth Lambda: user registration, login proxy, and profile retrieval."""
import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.logger import get_logger, LogContext
from shared.models import api_response, error_response, ValidationError
from shared.dynamodb import put_task, now_iso

logger = get_logger(__name__)

USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
USER_POOL_CLIENT_ID = os.environ.get("USER_POOL_CLIENT_ID", "")


def lambda_handler(event: dict, context: Any) -> dict:
    request_id = event.get("requestContext", {}).get("requestId", "")
    path = event.get("path", "")
    method = event.get("httpMethod", "")

    with LogContext(logger, request_id=request_id):
        if path.endswith("/register") and method == "POST":
            return _register(event, request_id)
        elif path.endswith("/login") and method == "POST":
            return _login(event, request_id)
        elif path.endswith("/me") and method == "GET":
            return _get_profile(event, request_id)
        else:
            return error_response(404, "Not found", "NOT_FOUND", request_id=request_id)


def _register(event: dict, request_id: str) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Invalid JSON body", "INVALID_JSON", request_id=request_id)

    username = body.get("username", "").strip()
    password = body.get("password", "")
    email = body.get("email", "").strip()

    if not username or not password or not email:
        return error_response(
            400,
            "username, password, and email are required",
            "MISSING_FIELDS",
            request_id=request_id,
        )

    cognito = boto3.client("cognito-idp")
    try:
        response = cognito.sign_up(
            ClientId=USER_POOL_CLIENT_ID,
            Username=username,
            Password=password,
            UserAttributes=[
                {"Name": "email", "Value": email},
            ],
        )
        user_id = response["UserSub"]
        logger.info("User registered", extra={"user_id": user_id})
        return api_response(201, {"user_id": user_id, "username": username})

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "UsernameExistsException":
            return error_response(409, "Username already exists", "USERNAME_EXISTS", request_id=request_id)
        if code == "InvalidPasswordException":
            return error_response(
                400,
                "Password does not meet complexity requirements",
                "INVALID_PASSWORD",
                details={"requirements": "min 8 chars, uppercase, lowercase, number, symbol"},
                request_id=request_id,
            )
        if code == "InvalidParameterException":
            return error_response(400, exc.response["Error"]["Message"], "INVALID_PARAMETER", request_id=request_id)
        logger.error("Cognito sign-up error", exc_info=True)
        return error_response(500, "Registration failed", "REGISTRATION_FAILED", request_id=request_id)


def _login(event: dict, request_id: str) -> dict:
    """Proxy login to Cognito InitiateAuth.
    The frontend can also call Cognito directly using Amplify (preferred, uses SRP).
    This endpoint is provided for server-side or non-browser clients.
    """
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error_response(400, "Invalid JSON body", "INVALID_JSON", request_id=request_id)

    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        return error_response(400, "username and password are required", "MISSING_FIELDS", request_id=request_id)

    cognito = boto3.client("cognito-idp")
    try:
        response = cognito.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
            ClientId=USER_POOL_CLIENT_ID,
        )
        auth_result = response.get("AuthenticationResult", {})
        logger.info("User logged in", extra={"username": username})
        return api_response(200, {
            "id_token": auth_result.get("IdToken"),
            "access_token": auth_result.get("AccessToken"),
            "refresh_token": auth_result.get("RefreshToken"),
            "expires_in": auth_result.get("ExpiresIn", 86400),
        })

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            return error_response(401, "Invalid username or password", "INVALID_CREDENTIALS", request_id=request_id)
        if code == "UserNotConfirmedException":
            return error_response(403, "Email not verified. Please check your email.", "EMAIL_NOT_VERIFIED", request_id=request_id)
        logger.error("Cognito login error", exc_info=True)
        return error_response(500, "Login failed", "LOGIN_FAILED", request_id=request_id)


def _get_profile(event: dict, request_id: str) -> dict:
    """Return the authenticated user's profile from Cognito claims."""
    claims = (
        event.get("requestContext", {})
             .get("authorizer", {})
             .get("claims", {})
    )
    user_id = claims.get("sub", "")
    username = claims.get("cognito:username", claims.get("username", ""))
    email = claims.get("email", "")

    if not user_id:
        return error_response(401, "Unauthorized", "UNAUTHORIZED", request_id=request_id)

    return api_response(200, {
        "user_id": user_id,
        "username": username,
        "email": email,
    })
