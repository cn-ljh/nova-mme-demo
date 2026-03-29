import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'

type Mode = 'login' | 'register'

export function LoginPage() {
  const navigate = useNavigate()
  const { signIn, signUp } = useAuth()

  const [mode, setMode] = useState<Mode>('login')
  const [form, setForm] = useState({ username: '', password: '', email: '' })
  const [status, setStatus] = useState<'idle' | 'loading' | 'error' | 'registered'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  const set = (field: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [field]: e.target.value }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setStatus('loading')
    setErrorMsg('')
    try {
      if (mode === 'login') {
        await signIn(form.username, form.password)
        navigate('/')
      } else {
        await signUp(form.username, form.password, form.email)
        setStatus('registered')
      }
    } catch (err: unknown) {
      setStatus('error')
      setErrorMsg((err as { message?: string }).message ?? '操作失败，请重试')
    }
  }

  if (status === 'registered') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="card max-w-sm w-full text-center">
          <div className="text-4xl mb-3">📧</div>
          <h2 className="text-xl font-bold text-gray-900 mb-2">注册成功！</h2>
          <p className="text-gray-600 mb-4">请查收邮件并确认您的邮箱地址，然后登录。</p>
          <button onClick={() => { setMode('login'); setStatus('idle') }} className="btn-primary">
            去登录
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="text-5xl mb-3">🔍</div>
          <h1 className="text-2xl font-bold text-gray-900">多模态内容检索</h1>
          <p className="text-gray-500 mt-1 text-sm">基于 Amazon Bedrock Nova MME</p>
        </div>

        <div className="card space-y-5">
          <div className="flex border-b border-gray-200 pb-4 gap-2">
            {(['login', 'register'] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => { setMode(m); setStatus('idle'); setErrorMsg('') }}
                className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
                  mode === m ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                {m === 'login' ? '登录' : '注册'}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">用户名</label>
              <input
                type="text"
                required
                value={form.username}
                onChange={set('username')}
                className="input-field"
                placeholder="输入用户名"
                autoComplete="username"
              />
            </div>
            {mode === 'register' && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">邮箱</label>
                <input
                  type="email"
                  required
                  value={form.email}
                  onChange={set('email')}
                  className="input-field"
                  placeholder="输入邮箱地址"
                  autoComplete="email"
                />
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">密码</label>
              <input
                type="password"
                required
                value={form.password}
                onChange={set('password')}
                className="input-field"
                placeholder={mode === 'register' ? '至少8位，含大小写+数字+符号' : '输入密码'}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              />
            </div>

            {errorMsg && (
              <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg p-3">
                {errorMsg}
              </div>
            )}

            <button type="submit" disabled={status === 'loading'} className="btn-primary w-full">
              {status === 'loading' ? '处理中...' : mode === 'login' ? '登录' : '创建账户'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
