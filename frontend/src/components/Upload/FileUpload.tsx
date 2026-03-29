import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { requestUploadUrl, uploadToS3, confirmUpload, uploadText } from '@/services/api'
import type { UploadState } from '@/types'

const ACCEPTED_MIME_TYPES: Record<string, string[]> = {
  'image/*': ['.png', '.jpg', '.jpeg', '.webp', '.gif'],
  'audio/*': ['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.webm'],
  'video/*': ['.mp4', '.mov', '.mkv', '.webm', '.flv', '.mpeg', '.wmv', '.3gp'],
  'application/pdf': ['.pdf'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  'text/plain': ['.txt'],
}

interface FileUploadProps {
  onTaskCreated?: (taskId: string) => void
}

export function FileUpload({ onTaskCreated }: FileUploadProps) {
  const [uploadState, setUploadState] = useState<UploadState>({
    file: null,
    text: '',
    title: '',
    status: 'idle',
    progress: 0,
  })
  const [mode, setMode] = useState<'file' | 'text'>('file')

  const onDrop = useCallback((accepted: File[]) => {
    if (accepted.length > 0) {
      setUploadState((s) => ({ ...s, file: accepted[0], status: 'idle', errorMessage: undefined }))
    }
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_MIME_TYPES,
    maxFiles: 1,
    multiple: false,
  })

  const handleFileUpload = async () => {
    const { file } = uploadState
    if (!file) return

    setUploadState((s) => ({ ...s, status: 'uploading', progress: 0, errorMessage: undefined }))
    try {
      // Step 1: get presigned URL
      const presigned = await requestUploadUrl(file.name, file.type, file.size)

      // Step 2: upload directly to S3
      await uploadToS3(presigned.uploadUrl, presigned.uploadFields, file, (pct) => {
        setUploadState((s) => ({ ...s, progress: pct }))
      })

      // Step 3: confirm upload and create task
      setUploadState((s) => ({ ...s, status: 'processing', progress: 100 }))
      const result = await confirmUpload(
        presigned.contentId,
        presigned.s3Key,
        file.name,
        file.type,
        file.size,
      )

      setUploadState((s) => ({ ...s, status: 'done', taskId: result.taskId }))
      onTaskCreated?.(result.taskId)
    } catch (err: unknown) {
      const msg = (err as { message?: string }).message ?? '上传失败'
      setUploadState((s) => ({ ...s, status: 'error', errorMessage: msg }))
    }
  }

  const handleTextUpload = async () => {
    const { text, title } = uploadState
    if (!text.trim()) return

    setUploadState((s) => ({ ...s, status: 'uploading', progress: 50, errorMessage: undefined }))
    try {
      const result = await uploadText(text, title || undefined)
      setUploadState((s) => ({ ...s, status: 'done', progress: 100, taskId: result.taskId }))
      onTaskCreated?.(result.taskId)
    } catch (err: unknown) {
      const msg = (err as { message?: string }).message ?? '上传失败'
      setUploadState((s) => ({ ...s, status: 'error', errorMessage: msg }))
    }
  }

  const reset = () => {
    setUploadState({ file: null, text: '', title: '', status: 'idle', progress: 0 })
  }

  const { status, progress, file, text, title, errorMessage, taskId } = uploadState

  if (status === 'done') {
    return (
      <div className="card text-center">
        <div className="text-4xl mb-3">✅</div>
        <h3 className="font-semibold text-lg text-gray-900 mb-1">上传成功！</h3>
        <p className="text-sm text-gray-500 mb-4">任务 ID: {taskId}</p>
        <p className="text-sm text-gray-600 mb-4">内容正在后台生成向量嵌入，可在任务列表中查看进度。</p>
        <button onClick={reset} className="btn-primary">继续上传</button>
      </div>
    )
  }

  return (
    <div className="card space-y-6">
      <div className="flex gap-2 border-b border-gray-200 pb-4">
        <button
          onClick={() => setMode('file')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            mode === 'file' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
          }`}
        >
          上传文件
        </button>
        <button
          onClick={() => setMode('text')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            mode === 'text' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
          }`}
        >
          输入文本
        </button>
      </div>

      {mode === 'file' && (
        <div className="space-y-4">
          <div
            {...getRootProps()}
            className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
              isDragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
            }`}
          >
            <input {...getInputProps()} />
            <div className="text-4xl mb-3">{file ? '📄' : '📁'}</div>
            {file ? (
              <div>
                <p className="font-medium text-gray-800">{file.name}</p>
                <p className="text-sm text-gray-500 mt-1">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
              </div>
            ) : (
              <div>
                <p className="text-gray-600">拖拽文件到此处，或点击选择文件</p>
                <p className="text-xs text-gray-400 mt-2">
                  支持：图片 (PNG/JPEG/WEBP/GIF)、音频 (MP3/WAV/OGG)、<br />
                  视频 (MP4/MOV/MKV...)、文档 (PDF/DOCX/TXT)
                </p>
              </div>
            )}
          </div>

          {status === 'uploading' || status === 'processing' ? (
            <div className="space-y-2">
              <div className="flex justify-between text-sm text-gray-600">
                <span>{status === 'uploading' ? '上传中...' : '处理中...'}</span>
                <span>{progress}%</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          ) : (
            <button
              onClick={handleFileUpload}
              disabled={!file || status !== 'idle'}
              className="btn-primary w-full"
            >
              开始上传
            </button>
          )}
        </div>
      )}

      {mode === 'text' && (
        <div className="space-y-4">
          <input
            type="text"
            value={title}
            onChange={(e) => setUploadState((s) => ({ ...s, title: e.target.value }))}
            placeholder="标题（可选）"
            className="input-field"
          />
          <textarea
            value={text}
            onChange={(e) => setUploadState((s) => ({ ...s, text: e.target.value }))}
            placeholder="输入文本内容（最多 50,000 字符）..."
            rows={8}
            className="input-field resize-none"
          />
          <div className="flex items-center justify-between">
            <span className={`text-xs ${text.length > 45000 ? 'text-red-500' : 'text-gray-400'}`}>
              {text.length.toLocaleString()} / 50,000 字符
            </span>
            <button
              onClick={handleTextUpload}
              disabled={!text.trim() || status !== 'idle'}
              className="btn-primary"
            >
              上传文本
            </button>
          </div>
        </div>
      )}

      {errorMessage && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          {errorMessage}
        </div>
      )}
    </div>
  )
}
