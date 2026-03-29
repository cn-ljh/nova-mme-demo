import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { useTasks } from '@/hooks/useTasks'
import { TaskList } from '@/components/Task/TaskList'

const QUICK_ACTIONS = [
  { to: '/upload', emoji: '📤', title: '上传内容', desc: '上传文本、图片、音视频或文档' },
  { to: '/search', emoji: '🔍', title: '检索内容', desc: '使用任意模态进行跨模态语义检索' },
  { to: '/tasks', emoji: '📋', title: '任务列表', desc: '查看所有上传和检索任务的状态' },
]

export function DashboardPage() {
  const { user } = useAuth()
  const { tasks, isLoading, fetchTasks } = useTasks(5)

  useEffect(() => {
    fetchTasks()
  }, [fetchTasks])

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          你好，{user?.username ?? '用户'} 👋
        </h1>
        <p className="text-gray-500 mt-1">欢迎使用多模态内容检索系统</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {QUICK_ACTIONS.map(({ to, emoji, title, desc }) => (
          <Link
            key={to}
            to={to}
            className="card hover:shadow-md hover:border-blue-200 transition-all group"
          >
            <div className="text-3xl mb-3">{emoji}</div>
            <h3 className="font-semibold text-gray-900 group-hover:text-blue-600 transition-colors">{title}</h3>
            <p className="text-sm text-gray-500 mt-1">{desc}</p>
          </Link>
        ))}
      </div>

      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">最近任务</h2>
          <Link to="/tasks" className="text-sm text-blue-600 hover:underline">查看全部</Link>
        </div>
        <TaskList tasks={tasks} isLoading={isLoading} onRefresh={() => fetchTasks()} />
      </div>
    </div>
  )
}
