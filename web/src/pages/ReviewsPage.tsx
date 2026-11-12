/**
 * 质检复核列表（T24 #25 / M5-02、M5-03）。
 *
 * 只读取契约 §5 的 ``GET /reviews``：状态过滤 + 分页 + 总数，不使用静态假数据。
 * 列表项不包含会话正文；点击进入详情后并排核对原文与证据。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import {
  ApiRequestError,
  asApiRequestError,
  fetchReviews,
} from '../api/client'
import type { ReviewList, ReviewStatus } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import ApiErrorNotice from '../components/ApiErrorNotice'
import { formatReviewTime, reviewStatusMeta } from '../lib/reviews'

const PAGE_SIZE = 20

const STATUS_FILTERS: { value: ReviewStatus | ''; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待复核' },
  { value: 'in_review', label: '复核中' },
  { value: 'completed', label: '已完成' },
]

type ListState =
  | { status: 'loading' }
  | { status: 'error'; error: ApiRequestError }
  | { status: 'ready'; page: ReviewList }

export default function ReviewsPage() {
  const { signOut } = useAuth()
  const navigate = useNavigate()

  const [statusFilter, setStatusFilter] = useState<ReviewStatus | ''>('')
  const [offset, setOffset] = useState(0)
  const [reloadToken, setReloadToken] = useState(0)
  const [state, setState] = useState<ListState>({ status: 'loading' })

  const handleUnauthorized = useCallback(
    (error: ApiRequestError): boolean => {
      if (error.status !== 401) {
        return false
      }
      signOut()
      navigate('/login', { replace: true })
      return true
    },
    [navigate, signOut],
  )

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' })

    fetchReviews({
      ...(statusFilter === '' ? {} : { status: statusFilter }),
      limit: PAGE_SIZE,
      offset,
    })
      .then((page) => {
        if (!cancelled) {
          setState({ status: 'ready', page })
        }
      })
      .catch((cause: unknown) => {
        const error = asApiRequestError(cause)
        if (cancelled || handleUnauthorized(error)) {
          return
        }
        setState({ status: 'error', error })
      })

    return () => {
      cancelled = true
    }
  }, [statusFilter, offset, reloadToken, handleUnauthorized])

  function changeFilter(value: string) {
    setStatusFilter(value as ReviewStatus | '')
    setOffset(0)
  }

  const page = state.status === 'ready' ? state.page : null
  const first = page !== null && page.items.length > 0 ? page.offset + 1 : 0
  const last = page !== null ? page.offset + page.items.length : 0

  return (
    <section className="reviews" data-testid="reviews-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">质检</p>
          <h1>质检复核</h1>
          <p className="muted">
            按状态查看人工复核任务；进入详情后并排核对不可变原文与机器证据，逐项确认、驳回或改判。
            数据来自 <code>GET /api/v1/reviews</code>，不使用静态假数据。
          </p>
        </div>
      </header>

      <section className="panel" aria-labelledby="review-list-title">
        <header className="review-list-header">
          <h2 id="review-list-title">复核任务</h2>
          <div className="inline-field">
            <label htmlFor="review-status-filter">状态筛选</label>
            <select
              id="review-status-filter"
              value={statusFilter}
              onChange={(event) => changeFilter(event.target.value)}
              disabled={state.status === 'loading'}
            >
              {STATUS_FILTERS.map((filter) => (
                <option key={filter.value} value={filter.value}>
                  {filter.label}
                </option>
              ))}
            </select>
          </div>
        </header>

        {state.status === 'loading' && (
          <p className="empty-state" role="status">
            正在读取复核列表…
          </p>
        )}

        {state.status === 'error' && (
          <div className="actions">
            <ApiErrorNotice error={state.error} prefix="复核列表读取" />
            <button
              type="button"
              className="button"
              onClick={() => setReloadToken((token) => token + 1)}
            >
              重试
            </button>
          </div>
        )}

        {page !== null && page.items.length === 0 && (
          <p className="empty-state" role="status" data-testid="reviews-empty">
            当前筛选条件下没有复核任务。新复核由已完成的质检创建。
          </p>
        )}

        {page !== null && page.items.length > 0 && (
          <>
            <ul className="review-list" aria-label="复核任务列表">
              {page.items.map((review) => {
                const meta = reviewStatusMeta(review.status)
                return (
                  <li key={review.review_id} className="review-list-item">
                    <div className="review-list-main">
                      <Link
                        to={`/reviews/${review.review_id}`}
                        className="review-link"
                      >
                        <code>{review.review_id}</code>
                      </Link>
                      <span className={`status-badge status-${meta.tone}`}>
                        {meta.label}
                      </span>
                      <span className="status-badge status-revision">
                        revision {review.revision}
                      </span>
                    </div>
                    <dl className="detail-list review-list-meta">
                      <div>
                        <dt>质检 ID</dt>
                        <dd>
                          <code>{review.inspection_id}</code>
                        </dd>
                      </div>
                      <div>
                        <dt>复核员</dt>
                        <dd>
                          <code>{review.assignee_id}</code>
                        </dd>
                      </div>
                      <div>
                        <dt>创建时间</dt>
                        <dd>{formatReviewTime(review.created_at)}</dd>
                      </div>
                      <div>
                        <dt>最近更新</dt>
                        <dd>{formatReviewTime(review.updated_at)}</dd>
                      </div>
                    </dl>
                    <Link
                      to={`/reviews/${review.review_id}`}
                      className="button"
                    >
                      打开复核
                    </Link>
                  </li>
                )
              })}
            </ul>

            <nav className="pagination" aria-label="复核分页">
              <button
                type="button"
                className="button"
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={page.offset === 0}
              >
                上一页
              </button>
              <span className="muted" data-testid="pagination-summary">
                第 {first}–{last} 条 / 共 {page.total} 条（每页 {page.limit} 条）
              </span>
              <button
                type="button"
                className="button"
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={page.offset + page.items.length >= page.total}
              >
                下一页
              </button>
            </nav>
          </>
        )}
      </section>
    </section>
  )
}
