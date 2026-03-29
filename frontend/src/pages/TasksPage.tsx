import { useEffect, useState } from 'react'
import { TaskList } from '@/components/Task/TaskList'
import { useTasks } from '@/hooks/useTasks'
import type { TaskStatus } from '@/types'

const STATUS_FILTERS: { value: TaskStatus | undefined; label: string }[] = [
  { value: undefined, label: '全部' },
  { value: 'pending', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
]

export function TasksPage() {
  const [statusFilter, setStatusFilter] = useState<TaskStatus | undefined>(undefined)
  const { tasks, isLoading, hasMore, fetchTasks, loadMore } = useTasks(20)

  useEffect(() => {
    fetchTasks(statusFilter)
  }, [statusFilter, fetchTasks])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">任务列表</h1>
        <p className="text-gray-500 mt-1">查看所有上传和检索任务的状态</p>
      </div>

      {/* Status filter tabs */}
      <div className="flex gap-2 flex-wrap border-b border-gray-200 pb-4">
        {STATUS_FILTERS.map(({ value, label }) => (
          <button
            key={label}
            onClick={() => setStatusFilter(value)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              statusFilter === value
                ? 'bg-blue-600 text-white'
                : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <TaskList
        tasks={tasks}
        isLoading={isLoading}
        onRefresh={() => fetchTasks(statusFilter)}
        hasMore={hasMore}
        onLoadMore={() => loadMore(statusFilter)}
      />
    </div>
  )
}
