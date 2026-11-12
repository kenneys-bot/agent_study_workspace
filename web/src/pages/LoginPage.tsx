import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { ApiRequestError, asApiRequestError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

interface LocationState {
  from?: string
}

export default function LoginPage() {
  const { token, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as LocationState | null)?.from ?? '/system'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<ApiRequestError | null>(null)

  if (token !== null) {
    return <Navigate to={from} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await signIn(username.trim(), password)
      navigate(from, { replace: true })
    } catch (cause) {
      setError(asApiRequestError(cause))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <p className="eyebrow">BANK ASSISTANT</p>
        <h1 id="login-title">登录本地工作台</h1>
        <p className="muted">
          使用后端已创建的账号登录。本页不内置任何演示凭据，也不展示静态假数据。
        </p>

        <form onSubmit={handleSubmit} className="form">
          <label htmlFor="username">用户名</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            maxLength={64}
          />

          <label htmlFor="password">密码</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            maxLength={256}
          />

          {error !== null && (
            <div className="alert error" role="alert">
              <strong>{error.status === 401 ? '登录失败' : '请求失败'}</strong>
              <span>{error.message}</span>
              {error.traceId !== null && (
                <span className="trace">trace_id: {error.traceId}</span>
              )}
            </div>
          )}

          <button type="submit" className="button primary" disabled={submitting}>
            {submitting ? '登录中…' : '登录'}
          </button>
        </form>
      </section>
    </main>
  )
}
