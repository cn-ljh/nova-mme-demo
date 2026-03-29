/**
 * API client using Axios with automatic Cognito token injection.
 */
import axios, { type AxiosInstance, type AxiosResponse } from 'axios'
import { getIdToken } from './auth'
import type {
  PresignedUploadResponse,
  ConfirmUploadResponse,
  TextUploadResponse,
  ContentDetail,
  SearchResponse,
  TaskListResponse,
  TaskDetail,
  ApiError,
} from '@/types'

// Convert snake_case keys to camelCase recursively
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function snakeToCamel(obj: any): any {
  if (Array.isArray(obj)) return obj.map(snakeToCamel)
  if (obj !== null && typeof obj === 'object') {
    return Object.fromEntries(
      Object.entries(obj).map(([k, v]) => [
        k.replace(/_([a-z])/g, (_, c) => c.toUpperCase()),
        snakeToCamel(v),
      ]),
    )
  }
  return obj
}

const BASE_URL = import.meta.env.VITE_API_URL ?? ''

function createApiClient(): AxiosInstance {
  const client = axios.create({
    baseURL: BASE_URL,
    timeout: 60_000,
  })

  // Inject access token into every request
  client.interceptors.request.use(async (config) => {
    try {
      const token = await getIdToken()
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
    } catch {
      // Not authenticated; let the server return 401
    }
    return config
  })

  // Convert snake_case response keys to camelCase
  client.interceptors.response.use(
    (res) => {
      if (res.data && typeof res.data === 'object') {
        res.data = snakeToCamel(res.data)
      }
      return res
    },
    (error) => {
      const apiError: ApiError = error.response?.data ?? {
        errorCode: 'NETWORK_ERROR',
        message: error.message ?? 'Network error',
        requestId: '',
      }
      return Promise.reject(apiError)
    },
  )

  return client
}

const api = createApiClient()

// ============================================================
// Content endpoints
// ============================================================

export async function requestUploadUrl(
  filename: string,
  mimeType: string,
  fileSize: number,
): Promise<PresignedUploadResponse> {
  const res: AxiosResponse<PresignedUploadResponse> = await api.post('/api/content/request-upload', {
    filename,
    mime_type: mimeType,
    file_size: fileSize,
  })
  return res.data
}

export async function uploadToS3(
  uploadUrl: string,
  uploadFields: Record<string, string>,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<void> {
  const formData = new FormData()
  Object.entries(uploadFields).forEach(([k, v]) => formData.append(k, v))
  formData.append('file', file)

  await axios.post(uploadUrl, formData, {
    onUploadProgress: (evt) => {
      if (onProgress && evt.total) {
        onProgress(Math.round((evt.loaded / evt.total) * 100))
      }
    },
  })
}

export async function confirmUpload(
  contentId: string,
  s3Key: string,
  filename: string,
  mimeType: string,
  fileSize: number,
): Promise<ConfirmUploadResponse> {
  const res: AxiosResponse<ConfirmUploadResponse> = await api.post('/api/content/confirm-upload', {
    content_id: contentId,
    s3_key: s3Key,
    filename,
    mime_type: mimeType,
    file_size: fileSize,
  })
  return res.data
}

export async function uploadText(text: string, title?: string): Promise<TextUploadResponse> {
  const res: AxiosResponse<TextUploadResponse> = await api.post('/api/content/upload-text', {
    text,
    title: title ?? 'Untitled',
  })
  return res.data
}

export async function getContent(contentId: string): Promise<ContentDetail> {
  const res: AxiosResponse<ContentDetail> = await api.get(`/api/content/${contentId}`)
  return res.data
}

export async function getDownloadUrl(contentId: string): Promise<string> {
  const res: AxiosResponse<{ downloadUrl: string }> = await api.get(`/api/content/${contentId}/download`)
  return res.data.downloadUrl
}

// ============================================================
// Search endpoint
// ============================================================

const LARGE_FILE_THRESHOLD = 5 * 1024 * 1024 // 5MB

export async function requestQueryUploadUrl(
  filename: string,
  mimeType: string,
  fileSize: number,
): Promise<{ uploadUrl: string; uploadFields: Record<string, string>; s3Key: string }> {
  const res = await api.post('/api/content/query-upload', {
    filename,
    mime_type: mimeType,
    file_size: fileSize,
  })
  return res.data
}

export async function search(params: {
  queryText?: string
  queryFile?: File
  topK?: number
  modalityFilter?: string[]
  onStatus?: (msg: string) => void
}): Promise<SearchResponse> {
  const body: Record<string, unknown> = {
    top_k: params.topK ?? 10,
  }

  if (params.queryText) {
    body.query_text = params.queryText
  }

  if (params.queryFile) {
    if (params.queryFile.size > LARGE_FILE_THRESHOLD) {
      // Large file: upload to S3 via presigned URL to avoid API Gateway 10MB limit
      params.onStatus?.('正在上传查询文件...')
      const { uploadUrl, uploadFields, s3Key } = await requestQueryUploadUrl(
        params.queryFile.name,
        params.queryFile.type,
        params.queryFile.size,
      )
      await uploadToS3(uploadUrl, uploadFields, params.queryFile)
      body.query_s3_key = s3Key
      body.query_file_type = params.queryFile.type
      params.onStatus?.('正在生成查询向量...')
    } else {
      // Small file: encode as base64
      const arrayBuffer = await params.queryFile.arrayBuffer()
      const base64 = btoa(
        new Uint8Array(arrayBuffer).reduce((data, byte) => data + String.fromCharCode(byte), ''),
      )
      body.query_file = base64
      body.query_file_type = params.queryFile.type
    }
  }

  if (params.modalityFilter?.length) {
    body.modality_filter = params.modalityFilter
  }

  const res: AxiosResponse<SearchResponse> = await api.post('/api/search', body)
  return res.data
}

// ============================================================
// Task endpoints
// ============================================================

export async function getTasks(params?: {
  status?: string
  pageSize?: number
  nextToken?: string
}): Promise<TaskListResponse> {
  const queryParams = new URLSearchParams()
  if (params?.status) queryParams.set('status', params.status)
  if (params?.pageSize) queryParams.set('page_size', String(params.pageSize))
  if (params?.nextToken) queryParams.set('next_token', params.nextToken)

  const res: AxiosResponse<TaskListResponse> = await api.get(`/api/tasks?${queryParams}`)
  return res.data
}

export async function getTask(taskId: string): Promise<TaskDetail> {
  const res: AxiosResponse<TaskDetail> = await api.get(`/api/tasks/${taskId}`)
  return res.data
}

export default api
