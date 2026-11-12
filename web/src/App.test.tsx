import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import App from './App'
import { AuthProvider } from './auth/AuthContext'

describe('App 认证守卫', () => {
  it('未登录访问 /system 时跳转登录页', () => {
    render(
      <MemoryRouter initialEntries={['/system']}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    )

    expect(
      screen.getByRole('heading', { name: '登录本地工作台' }),
    ).toBeInTheDocument()
  })
})
