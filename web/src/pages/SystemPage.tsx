import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  ApiRequestError,
  asApiRequestError,
  fetchMe,
  fetchSystemVersion,
} from '../api/client'
import type { SystemVersion, UserPublic } from '../api/types'
import { useAuth } from '../auth/AuthContext'

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; error: ApiRequestError }
  | { status: 'ready'; me: UserPublic; version: SystemVersion }

export default function SystemPage() {
  const { signOut } = useAuth()
  const navigate = useNavigate()
  const [state, setState] = useState<LoadState>({ status: 'loading' })

  const load = useCallback(async () => {
    setState({ status: 'loading' })
    try {
      const [me, version] = await Promise.all([fetchMe(), fetchSystemVersion()])
      setState({ status: 'ready', me, version })
    } catch (cause) {
      const error = asApiRequestError(cause)
      if (error.status === 401) {
        signOut()
        navigate('/login', { replace: true })
        return
      }
      setState({ status: 'error', error })
    }
  }, [navigate, signOut])

  useEffect(() => {
    void load()
  }, [load])

  if (state.status === 'loading') {
    return (
      <section className="panel" aria-busy="true">
        <h1>系统版本</h1>
        <p role="status">正在读取真实运行信息…</p>
      </section>
    )
  }

  if (state.status === 'error') {
    const forbidden = state.error.status === 403
    return (
      <section className="panel">
        <h1>{forbidden ? '无权限访问版本信息' : '加载系统版本失败'}</h1>
        <div className="alert error" role="alert">
          <span>{state.error.message}</span>
          <span className="trace">错误码: {state.error.code}</span>
          {state.error.traceId !== null && (
            <span className="trace">trace_id: {state.error.traceId}</span>
          )}
        </div>
        <div className="actions">
          <button type="button" className="button primary" onClick={() => void load()}>
            重试
          </button>
        </div>
      </section>
    )
  }

  const { me, version } = state

  return (
    <section className="panel" data-testid="system-version">
      <header className="page-header">
        <div>
          <p className="eyebrow">运行信息</p>
          <h1>系统版本</h1>
          <p className="muted">
            以下字段来自 <code>GET /api/v1/system/version</code> 与{' '}
            <code>GET /api/v1/me</code>，不使用静态假数据。
          </p>
        </div>
        <button type="button" className="button" onClick={() => void load()}>
          刷新
        </button>
      </header>

      <div className="cards">
        <article className="card" aria-labelledby="identity-title">
          <h2 id="identity-title">当前身份</h2>
          <dl className="detail-list">
            <div>
              <dt>用户名</dt>
              <dd>{me.username}</dd>
            </div>
            <div>
              <dt>角色</dt>
              <dd>{me.role}</dd>
            </div>
            <div>
              <dt>业务范围</dt>
              <dd>{me.scope_id}</dd>
            </div>
            <div>
              <dt>用户 ID</dt>
              <dd>
                <code>{me.user_id}</code>
              </dd>
            </div>
          </dl>
        </article>

        <article className="card" aria-labelledby="version-title">
          <h2 id="version-title">代码与模型版本</h2>
          <dl className="detail-list">
            <div>
              <dt>代码 SHA</dt>
              <dd>
                <code>{version.code_sha}</code>
              </dd>
            </div>
            <div>
              <dt>分支</dt>
              <dd>{version.git_branch}</dd>
            </div>
            <div>
              <dt>模型供应商</dt>
              <dd>{version.model_provider}</dd>
            </div>
            <div>
              <dt>对话模型</dt>
              <dd>
                {version.model_chat ?? (
                  <span className="muted-value">未配置</span>
                )}
              </dd>
            </div>
            <div>
              <dt>嵌入模型</dt>
              <dd>{version.embedding_model}</dd>
            </div>
            <div>
              <dt>索引版本</dt>
              <dd>
                {version.index_version ?? (
                  <span className="muted-value">未构建（运行 make index-rebuild）</span>
                )}
              </dd>
            </div>
            <div>
              <dt>运行模式</dt>
              <dd>
                <span className={`mode-badge mode-${version.mode}`}>
                  {version.mode}
                </span>
                {version.mode === 'replay' && (
                  <span className="muted-value"> 回放模式，不代表真实模型结论</span>
                )}
              </dd>
            </div>
            <div>
              <dt>应用版本</dt>
              <dd>{version.app_version}</dd>
            </div>
          </dl>
        </article>
      </div>
    </section>
  )
}
