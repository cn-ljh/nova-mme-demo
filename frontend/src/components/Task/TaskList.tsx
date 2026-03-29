import type { TaskSummary, TaskStatus } from '@/types'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'

const STATUS_STYLES: Record<TaskStatus, string> = {
  pending: 'bg-yellow-50 text-yellow-700',
  processing: 'bg-blue-50 text-blue-700',
  completed: 'bg-green-50 text-green-700',
  failed: 'bg-red-50 text-red-700',
}

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '待处理',
  processing: '处理中',
  completed: '已完成',
  failed: '失败',
}

const MODALITY_EMOJI: Record<string, string> = {
  text: '📝', image: '🖼️', audio: '🎵', video: '🎬', document: '📄',
}

interface TaskListProps {
  tasks: TaskSummary[]
  isLoading: boolean
  onRefresh?: () => void
  hasMore?: boolean
  onLoadMore?: () => void
}

export function TaskList({ tasks, isLoading, onRefresh, hasMore, onLoadMore }: TaskListProps) {
  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
    } catch {
      return iso
    }
  }

  if (isLoading && tasks.length === 0) {
    return (
      <div className="flex justify-center py-12">
        <LoadingSpinner label="加载任务列表..." />
      </div>
    )
  }

  if (!isLoading && tasks.length === 0) {
    return (
      <div className="card text-center py-12 text-gray-400">
        <p className="text-3xl mb-2">📋</p>
        <p>暂无任务记录</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-500">{tasks.length} 条记录</span>
        {onRefresh && (
          <button onClick={onRefresh} className="btn-secondary text-sm py-1.5">
            刷新
          </button>
        )}
      </div>

      <div className="space-y-2">
        {tasks.map((task) => (
          <div key={task.taskId} className="card p-4 hover:shadow-md transition-shadow">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-xl flex-shrink-0">{MODALITY_EMOJI[task.modality] ?? '📄'}</span>
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-gray-800">
                      {task.taskType === 'upload' ? '上传' : '检索'}
                    </span>
                    <span className="badge bg-gray-100 text-gray-600">{task.modality}</span>
                    <span className={`badge ${STATUS_STYLES[task.status]}`}>
                      {STATUS_LABEL[task.status]}
                    </span>
                    {task.status === 'processing' && (
                      <span className="animate-spin text-blue-600 text-xs">⟳</span>
                    )}
                  </div>
                  {task.filename && (
                    <p className="text-xs text-gray-600 mt-0.5 truncate font-medium">{task.filename}</p>
                  )}
                  {task.resultSummary && (
                    <p className="text-xs text-gray-500 mt-0.5 truncate">{task.resultSummary}</p>
                  )}
                </div>
              </div>
              <div className="text-right flex-shrink-0">
                <p className="text-xs text-gray-400">{formatDate(task.createdAt)}</p>
                <p className="text-xs text-gray-300 font-mono mt-0.5">{task.taskId.slice(0, 8)}...</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      {hasMore && (
        <button
          onClick={onLoadMore}
          disabled={isLoading}
          className="btn-secondary w-full"
        >
          {isLoading ? '加载中...' : '加载更多'}
        </button>
      )}
    </div>
  )
}
