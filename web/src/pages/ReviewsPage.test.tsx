import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { ReviewList, ReviewSummary } from '../api/types'
import { AuthProvider } from '../auth/AuthContext'
import { setToken, TOKEN_STORAGE_KEY } from '../auth/tokenStore'
import { errorPayload, jsonResponse } from '../test/http'
import ReviewsPage from './ReviewsPage'

function summary(index: number, status: string): ReviewSummary {
  return {
    review_id: `review-${index}`,
    inspection_id: `inspection-${index}`,
    assignee_id: `assignee-${index}`,
    status,
    revision: index,
    created_at: '2026-11-12T09:00:00Z',
    updated_at: '2026-11-12T10:00:00Z',
  }
}

const ITEMS = Array.from({ length: 20 }, (_, index) =>
  summary(index + 1, index % 2 === 0 ? 'pending' : 'in_review'),
)

const LIST: ReviewList = {
  items: ITEMS,
  total: 25,
  limit: 20,
  offset: 0,
}

type Responder = (url: string) => Response | Promise<Response>

function stubApi(responder?: Responder) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/api/v1/reviews')) {
      return responder !== undefined ? responder(url) : jsonResponse(LIST)
    }
    return jsonResponse({}, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderReviewsPage() {
  return render(
    <MemoryRouter initialEntries={['/reviews']}>
      <AuthProvider>
        <Routes>
          <Route path="/reviews" element={<ReviewsPage />} />
          <Route path="/reviews/:reviewId" element={<div>detail-stub</div>} />
          <Route path="/login" element={<div>login-stub</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('ReviewsPage', () => {
  it('渲染复核列表、状态与分页信息，并支持状态过滤和翻页', async () => {
    setToken('jwt-reviews')
    const user = userEvent.setup()
    const fetchMock = stubApi()
    renderReviewsPage()

    expect(screen.getByRole('status')).toHaveTextContent('正在读取复核列表')

    expect(await screen.findByText('review-1')).toBeInTheDocument()
    expect(screen.getAllByText('待复核').length).toBeGreaterThan(0)
    expect(screen.getAllByText('复核中').length).toBeGreaterThan(0)
    expect(screen.getByTestId('pagination-summary')).toHaveTextContent(
      '第 1–20 条 / 共 25 条',
    )

    const firstCall = fetchMock.mock.calls[0]
    expect(String(firstCall?.[0])).toContain('limit=20')
    expect(String(firstCall?.[0])).toContain('offset=0')

    await user.selectOptions(screen.getByLabelText('状态筛选'), 'completed')
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes('status=completed'),
        ),
      ).toBe(true)
    })

    await user.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes('offset=20')),
      ).toBe(true)
    })
  })

  it('无结果时显示明确的空状态', async () => {
    setToken('jwt-reviews')
    stubApi(() => jsonResponse({ items: [], total: 0, limit: 20, offset: 0 }))
    renderReviewsPage()

    expect(await screen.findByTestId('reviews-empty')).toHaveTextContent(
      '当前筛选条件下没有复核任务',
    )
  })

  it('403 时显示无权限状态，不渲染列表', async () => {
    setToken('jwt-reviews')
    stubApi(() =>
      jsonResponse(
        errorPayload({ code: 'FORBIDDEN', message: '无权限访问该资源' }),
        403,
      ),
    )
    renderReviewsPage()

    expect(await screen.findByText('无权限：复核列表读取')).toBeInTheDocument()
    expect(screen.getByText('无权限访问该资源')).toBeInTheDocument()
  })

  it('401 时清除令牌并跳转登录页', async () => {
    setToken('jwt-expired')
    stubApi(() =>
      jsonResponse(
        errorPayload({ code: 'UNAUTHENTICATED', message: '未认证：请先登录' }),
        401,
      ),
    )
    renderReviewsPage()

    expect(await screen.findByText('login-stub')).toBeInTheDocument()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
  })
})
