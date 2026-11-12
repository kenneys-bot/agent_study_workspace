import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { SystemVersion, UserPublic } from '../api/types'
import { AuthProvider } from '../auth/AuthContext'
import { setToken, TOKEN_STORAGE_KEY } from '../auth/tokenStore'
import { errorPayload, jsonResponse } from '../test/http'
import SystemPage from './SystemPage'

const ME: UserPublic = {
  user_id: 'user-0001',
  username: 'alice',
  role: 'agent',
  scope_id: 'demo-bank',
}

const VERSION: SystemVersion = {
  code_sha: '0123456789abcdef0123456789abcdef01234567',
  git_branch: 'ticket/T22',
  model_provider: 'deepseek',
  model_chat: 'deepseek-chat',
  embedding_model: 'BAAI/bge-small-zh-v1.5',
  index_version: 'idx-20261112T000000Z-abcdef12',
  mode: 'live',
  app_version: '1.0.0',
}

function stubApi(overrides: { me?: Response; version?: Response } = {}) {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/v1/me')) {
        return overrides.me ?? jsonResponse(ME)
      }
      if (url.endsWith('/api/v1/system/version')) {
        return overrides.version ?? jsonResponse(VERSION)
      }
      return jsonResponse({}, 404)
    },
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderSystemPage() {
  return render(
    <MemoryRouter initialEntries={['/system']}>
      <AuthProvider>
        <Routes>
          <Route path="/system" element={<SystemPage />} />
          <Route path="/login" element={<div>login-stub</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('SystemPage', () => {
  it('展示 /me 与 /system/version 返回的真实字段', async () => {
    setToken('jwt-system')
    const fetchMock = stubApi()
    renderSystemPage()

    expect(screen.getByRole('status')).toHaveTextContent('正在读取真实运行信息')

    expect(await screen.findByText('alice')).toBeInTheDocument()
    expect(screen.getByText('demo-bank')).toBeInTheDocument()
    expect(screen.getByText('agent')).toBeInTheDocument()
    expect(screen.getByText(VERSION.code_sha)).toBeInTheDocument()
    expect(screen.getByText('ticket/T22')).toBeInTheDocument()
    expect(screen.getByText('deepseek')).toBeInTheDocument()
    expect(screen.getByText('deepseek-chat')).toBeInTheDocument()
    expect(screen.getByText('BAAI/bge-small-zh-v1.5')).toBeInTheDocument()
    expect(screen.getByText(VERSION.index_version ?? '')).toBeInTheDocument()
    expect(screen.getByText('live')).toBeInTheDocument()
    expect(screen.getByText('1.0.0')).toBeInTheDocument()

    expect(fetchMock).toHaveBeenCalledTimes(2)
    for (const call of fetchMock.mock.calls) {
      expect(new Headers(call[1]?.headers).get('authorization')).toBe(
        'Bearer jwt-system',
      )
    }
  })

  it('索引未构建时明确显示未构建，不伪造索引版本', async () => {
    setToken('jwt-system')
    stubApi({
      version: jsonResponse({ ...VERSION, index_version: null } satisfies SystemVersion),
    })
    renderSystemPage()

    expect(
      await screen.findByText('未构建（运行 make index-rebuild）'),
    ).toBeInTheDocument()
  })

  it('403 时渲染无权限状态，不展示任何版本字段', async () => {
    setToken('jwt-system')
    stubApi({
      me: jsonResponse(
        errorPayload({ code: 'FORBIDDEN', message: '无权限访问该资源' }),
        403,
      ),
    })
    renderSystemPage()

    expect(
      await screen.findByText('无权限访问版本信息'),
    ).toBeInTheDocument()
    expect(screen.getByText('无权限访问该资源')).toBeInTheDocument()
    expect(screen.queryByText(VERSION.code_sha)).not.toBeInTheDocument()
  })

  it('401 时清除令牌并跳转登录页', async () => {
    setToken('jwt-expired')
    stubApi({
      me: jsonResponse(
        errorPayload({ code: 'UNAUTHENTICATED', message: '未认证：请先登录' }),
        401,
      ),
    })
    renderSystemPage()

    expect(await screen.findByText('login-stub')).toBeInTheDocument()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
  })
})
