import { useNavigate } from 'react-router-dom'
import { FileUpload } from '@/components/Upload/FileUpload'

export function UploadPage() {
  const navigate = useNavigate()

  const handleTaskCreated = (taskId: string) => {
    console.log('Task created:', taskId)
    // Navigate to tasks after a short delay so user can see success state
    setTimeout(() => navigate('/tasks'), 2000)
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">上传内容</h1>
        <p className="text-gray-500 mt-1">
          支持文本、图片 (PNG/JPEG/WEBP/GIF)、音频 (MP3/WAV/OGG)、<br />
          视频 (MP4/MOV/MKV...) 和文档 (PDF/DOCX/TXT)
        </p>
      </div>

      <FileUpload onTaskCreated={handleTaskCreated} />

      <div className="card bg-blue-50 border-blue-100">
        <h3 className="font-medium text-blue-800 mb-2">💡 上传说明</h3>
        <ul className="text-sm text-blue-700 space-y-1">
          <li>• 文件上传后会自动生成向量嵌入，支持后续跨模态检索</li>
          <li>• 小文件（音视频 ≤100MB）立即处理；大文件进入异步处理队列</li>
          <li>• 处理状态可在任务列表中实时查看</li>
          <li>• 文件大小限制：图片 50MB，音频 1GB，视频 2GB，文档 634MB</li>
        </ul>
      </div>
    </div>
  )
}
