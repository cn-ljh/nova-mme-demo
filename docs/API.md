# API Reference

**Base URL**: `https://<api-id>.execute-api.<region>.amazonaws.com/dev`
(or via CloudFront: `https://<cloudfront-domain>/api`)

All endpoints except `/api/auth/register` and `/api/auth/login` require authentication.

## Authentication

The API uses Amazon Cognito for authentication. Use the Amplify v6 SDK or call Cognito directly.

**Auth flow**: SRP (`ALLOW_USER_SRP_AUTH`) or password auth (`ALLOW_USER_PASSWORD_AUTH`)

**User Pool**: `<UserPoolId from CloudFormation outputs>`
**Client ID**: `<UserPoolClientId from CloudFormation outputs>`

After login, include the **IdToken** (not AccessToken) in all API requests:
```
Authorization: <id_token>
```
No "Bearer" prefix.

---

## POST /api/auth/register

Register a new user.

**Request**
```json
{
  "username": "alice",
  "password": "Str0ng!Pass",
  "email": "alice@example.com"
}
```

**Response 200**
```json
{
  "user_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "username": "alice",
  "message": "User registered successfully"
}
```

**Response 400** — username taken or invalid password policy
```json
{"error": "UsernameExistsException", "message": "User already exists"}
```

---

## POST /api/auth/login

Login and obtain tokens.

**Request**
```json
{
  "username": "alice",
  "password": "Str0ng!Pass"
}
```

**Response 200**
```json
{
  "id_token": "eyJraWQ...",
  "access_token": "eyJraWQ...",
  "refresh_token": "eyJjdHki...",
  "expires_in": 86400
}
```

**Response 401**
```json
{"error": "NotAuthorizedException", "message": "Incorrect username or password"}
```

---

## GET /api/auth/me

Get the authenticated user's profile.

**Response 200**
```json
{
  "user_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "username": "alice",
  "email": "alice@example.com"
}
```

---

## POST /api/content/request-upload

Request a presigned URL to upload a file directly to S3. Use for binary files (images, audio, video, documents).

**Request**
```json
{
  "filename": "lecture.mp3",
  "mime_type": "audio/mpeg",
  "file_size": 45678900
}
```

**Response 200**
```json
{
  "content_id": "3c9e4797-adf3-492c-b3c2-bf6bc7a10a36",
  "upload_url": "https://<content-bucket>.s3.amazonaws.com/uploads/...",
  "upload_fields": {
    "key": "uploads/{user_id}/{content_id}/lecture.mp3",
    "AWSAccessKeyId": "...",
    "x-amz-security-token": "...",
    "policy": "...",
    "signature": "..."
  },
  "s3_key": "uploads/{user_id}/{content_id}/lecture.mp3"
}
```

Then upload the file using an HTTP POST with `multipart/form-data`, including all `upload_fields` plus the file as the `file` field.

After the S3 upload completes, call **confirm-upload** to trigger embedding.

---

## POST /api/content/confirm-upload

Confirm a completed S3 upload and start embedding.

**Request**
```json
{
  "content_id": "3c9e4797-adf3-492c-b3c2-bf6bc7a10a36",
  "s3_key": "uploads/{user_id}/{content_id}/lecture.mp3",
  "filename": "lecture.mp3",
  "mime_type": "audio/mpeg",
  "file_size": 45678900
}
```

**Response 200**
```json
{
  "task_id": "ab830dd1-b6f0-4016-97ec-e67ee7192961",
  "content_id": "3c9e4797-adf3-492c-b3c2-bf6bc7a10a36",
  "status": "pending",
  "message": "Upload confirmed, embedding started"
}
```

---

## POST /api/content/upload-text

Upload plain text content directly (no S3 presigned URL needed).

**Request**
```json
{
  "text": "The quick brown fox jumps over the lazy dog.",
  "title": "Sample text"
}
```

**Response 200**
```json
{
  "task_id": "9236ec7e-2b24-4c3c-b90d-5c14d088b89f",
  "content_id": "06894bab-33ee-4270-ae2a-7c816000ad0a",
  "status": "pending",
  "modality": "text"
}
```

---

## GET /api/content

List all content uploaded by the authenticated user.

**Response 200**
```json
{
  "items": [
    {
      "content_id": "3c9e4797-adf3-492c-b3c2-bf6bc7a10a36",
      "filename": "sample-audio.mp3",
      "modality": "audio",
      "mime_type": "audio/mpeg",
      "file_size": 38291456,
      "is_indexed": true,
      "created_at": "2026-03-28T13:28:38.306858Z"
    }
  ],
  "count": 1
}
```

---

## GET /api/content/{content_id}

Get metadata for a specific content item.

**Response 200**
```json
{
  "content_id": "3c9e4797-adf3-492c-b3c2-bf6bc7a10a36",
  "user_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "filename": "sample-audio.mp3",
  "modality": "audio",
  "mime_type": "audio/mpeg",
  "file_size": 38291456,
  "s3_key": "uploads/{user_id}/{content_id}/lecture.mp3",
  "s3_bucket": "<content-bucket>",
  "is_indexed": true,
  "created_at": "2026-03-28T13:28:38.306858Z",
  "download_url": "https://...",
  "transcribe_status": "completed",
  "transcribe_job_name": "tr-3c9e4797-adf3-492c-b3c2-bf6bc7a10a36",
  "transcript": "佛学是一个系统的宗教思想体系..."
}
```

**Transcribe fields** (audio/video only):
| Field | Values | Description |
|-------|--------|-------------|
| `transcribe_status` | `pending`, `completed`, `failed` | Transcription pipeline status |
| `transcribe_job_name` | string | Amazon Transcribe job name |
| `transcript` | string (max 10k chars) | Full transcript text |

These fields are absent for non-audio/video content.

**Response 404** — not found or belongs to another user

---

## DELETE /api/content/{content_id}

Delete a content item (DynamoDB record + S3 object + vectors).

**Response 200**
```json
{"message": "Content deleted successfully"}
```

---

## POST /api/search

Semantic search across indexed content.

**Request (text query)**
```json
{
  "query": "Germanic tribes formation",
  "top_k": 10,
  "modality_filter": ["audio", "video"]
}
```

**Request (file query — base64)**
```json
{
  "query_file": "<base64-encoded-bytes>",
  "query_mime_type": "image/jpeg",
  "top_k": 5
}
```

All fields:
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | one of query/query_file | — | Text query |
| `query_file` | string (base64) | one of | — | File bytes |
| `query_mime_type` | string | if query_file | — | MIME type of query file |
| `top_k` | integer | no | 10 | Results to return (1–100) |
| `modality_filter` | string[] | no | all | Filter by modality |

**Response 200**
```json
{
  "results": [
    {
      "content_id": "fdbe0ee6-f19f-4c15-aa2a-e2ca32331b9f",
      "filename": "lecture-audio.mp3",
      "modality": "audio",
      "similarity_score": 0.545,
      "segments": [
        {
          "segment_index": 3,
          "similarity_score": 0.545,
          "time_offset_seconds": 90.5,
          "duration_seconds": 30.0,
          "is_transcript": true,
          "transcript_text": "佛学是一个学术研究领域，而佛教是..."
        },
        {
          "segment_index": 1,
          "similarity_score": 0.481,
          "time_offset_seconds": 30.0,
          "duration_seconds": 30.0,
          "is_transcript": false,
          "transcript_text": null
        }
      ],
      "download_url": "https://<content-bucket>.s3.amazonaws.com/uploads/...",
      "created_at": "2026-03-28T13:40:21.560910Z",
      "transcribe_status": "completed"
    },
    {
      "content_id": "9c706517-6345-4443-92d4-1fb79c71588a",
      "filename": "Agentic AI_驱动企业智能转型的下一代引擎.pdf",
      "modality": "document",
      "similarity_score": 0.7412,
      "segments": null,
      "download_url": "https://...",
      "created_at": "2026-03-28T12:15:30.805894Z",
      "transcribe_status": null
    }
  ],
  "total": 2,
  "query_time_ms": 610
}
```

**Notes**:
- Results are grouped by `content_id`. Up to 3 best-matching segments per content item are returned in the `segments` array, sorted by similarity score descending.
- `segments` is `null` for non-segmented content (images, short audio, text, documents).
- `similarity_score` at the top level is the best segment score for that content item.
- `is_transcript`: `true` if the segment matched via the Transcribe text pipeline, `false` if via the audio/video embedding pipeline.
- `transcript_text`: snippet of the matching transcript segment (present when `is_transcript=true`).
- `time_offset_seconds` / `duration_seconds`: position in the media file (null for transcript segments or when not available).
- Results are filtered to the authenticated user's content only.

---

## GET /api/tasks

List embedding tasks for the authenticated user, sorted by creation time (newest first).

**Query parameters**:
| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter: `pending`, `processing`, `completed`, `failed` |
| `limit` | integer | Page size (default 20, max 100) |
| `next_token` | string | Pagination token from previous response |

**Response 200**
```json
{
  "tasks": [
    {
      "task_id": "ab830dd1-b6f0-4016-97ec-e67ee7192961",
      "content_id": "3c9e4797-adf3-492c-b3c2-bf6bc7a10a36",
      "filename": "sample-audio.mp3",
      "modality": "audio",
      "status": "completed",
      "created_at": "2026-03-28T13:28:38.306858Z",
      "updated_at": "2026-03-28T13:29:15.123456Z"
    }
  ],
  "count": 1,
  "next_token": null
}
```

---

## GET /api/tasks/{task_id}

Get details for a specific task.

**Response 200**
```json
{
  "task_id": "ab830dd1-b6f0-4016-97ec-e67ee7192961",
  "content_id": "3c9e4797-adf3-492c-b3c2-bf6bc7a10a36",
  "filename": "sample-audio.mp3",
  "modality": "audio",
  "status": "completed",
  "created_at": "2026-03-28T13:28:38.306858Z",
  "updated_at": "2026-03-28T13:29:15.123456Z",
  "error_message": null
}
```

**Response 404** — task not found or belongs to another user

---

## Error Responses

All errors follow this format:
```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description"
}
```

Common HTTP status codes:
| Code | Meaning |
|------|---------|
| 400 | Invalid request (missing fields, unsupported format, file too large) |
| 401 | Missing or expired authentication token |
| 403 | Accessing another user's resource |
| 404 | Resource not found |
| 500 | Internal server error (see CloudWatch logs) |

---

## Supported File Formats

| Modality | MIME Types | Size Limit | Embedding |
|----------|-----------|------------|-----------|
| image | `image/png`, `image/jpeg`, `image/webp`, `image/gif` | 50 MB | sync |
| audio | `audio/mpeg`, `audio/wav`, `audio/ogg` | 1 GB | sync ≤30s; async >30s |
| video | `video/mp4`, `video/quicktime`, `video/x-matroska`, `video/webm`, `video/mpeg`, `video/x-flv`, `video/x-ms-wmv`, `video/3gpp` | 2 GB | sync ≤30s; async >30s |
| document | `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `text/plain` | 634 MB | sync |
| text | direct input | 50,000 chars | sync |
