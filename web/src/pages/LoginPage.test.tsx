import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { TOKEN_STORAGE_KEY } from '../auth/tokenStore'
import { AuthProvider } from '../auth/AuthContext'
import { errorPayload, jsonResponse } from '../test/http'
import LoginPage from './LoginPage'

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/system" element={<div>system-stub</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('LoginPage', () => {
  it('用真实凭据登录后保存令牌并进入版本页', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        access_token: 'jwt-from-login',
        token_type: 'bearer',
        expires_in: 1800,
        user: {
          user_id: 'u-1',
          username: 'alice',
          role: 'agent',
          scope_id: 'demo-bank',
        },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('用户名'), 'alice')
    await user.type(screen.getByLabelText('密码'), 'correct-horse')
    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByText('system-stub')).toBeInTheDocument()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBe('jwt-from-login')

    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(String(init?.body))).toEqual({
      username: 'alice',
      password: 'correct-horse',
    })
  })

  it('401 时展示后端错误信息，不保存令牌、不跳转', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        errorPayload({
          code: 'AUTHENTICATION_FAILED',
          message: '用户名或密码错误',
          trace_id: 'trace-login-1',
        }),
        401,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const user = userEvent.setup()
    renderLogin()

    await user.type(screen.getByLabelText('用户名'), 'alice')
    await user.type(screen.getByLabelText('密码'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByText('用户名或密码错误')).toBeInTheDocument()
    expect(screen.getByText('登录失败')).toBeInTheDocument()
    expect(screen.getByText('trace_id: trace-login-1')).toBeInTheDocument()
    expect(screen.queryByText('system-stub')).not.toBeInTheDocument()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
    expect(screen.getByRole('button', { name: '登录' })).toBeEnabled()
  })
})
