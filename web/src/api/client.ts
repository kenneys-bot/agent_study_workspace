/**
 * 类型化 fetch 客户端。
 *
 * - 基地址来自 `VITE_API_BASE_URL`，默认 `http://localhost:8000`；
 * - 所有失败都解析契约 §1 的 `{code, message, trace_id, retryable, details}`，
 *   解析失败时给出可展示的兜底错误，不把服务端 HTML/堆栈直接渲染给用户；
 * - Bearer 令牌由 tokenStore 提供（内存 + localStorage）。
 */

import { getToken } from '../auth/tokenStore'
import type {
  ApiErrorPayload,
  ConversationCreateInput,
  ConversationPublic,
  InspectionDetail,
  KnowledgeVersion,
  LoginResponse,
  ReviewCompleteInput,
  ReviewDecisionAccepted,
  ReviewDecisionInput,
  ReviewDetail,
  ReviewList,
  ReviewStatus,
  SuggestionCreateInput,
  SuggestionDetail,
  SuggestionFeedbackInput,
  SuggestionFeedbackPublic,
  SuggestionPublic,
  SystemVersion,
  UserPublic,
} from './types'

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
).replace(/\/+$/, '')

const FALLBACK_MESSAGE_BY_STATUS: Record<number, string> = {
  400: '请求无效',
  401: '未认证：请先登录',
  403: '无权限访问该资源',
  404: '资源不存在或不可见',
  409: '资源状态或版本冲突',
  422: '请求参数校验失败',
  429: '请求过于频繁，请稍后重试',
  500: '服务内部错误',
  502: '依赖服务返回异常',
  503: '依赖服务暂不可用',
  504: '依赖服务超时',
}

export class ApiRequestError extends Error {
  readonly status: number
  readonly code: string
  readonly traceId: string | null
  readonly retryable: boolean
  readonly details: Record<string, unknown>

  constructor(options: {
    status: number
    code: string
    message: string
    traceId?: string | null
    retryable?: boolean
    details?: Record<string, unknown>
  }) {
    super(options.message)
    this.name = 'ApiRequestError'
    this.status = options.status
    this.code = options.code
    this.traceId = options.traceId ?? null
    this.retryable = options.retryable ?? false
    this.details = options.details ?? {}
  }
}

export function asApiRequestError(error: unknown): ApiRequestError {
  if (error instanceof ApiRequestError) {
    return error
  }
  return new ApiRequestError({
    status: 0,
    code: 'UNEXPECTED_ERROR',
    message: '发生未预期的错误，请重试',
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/** 把任意响应体解析成契约错误；非契约响应也返回可展示的稳定错误。 */
export function parseErrorPayload(
  body: unknown,
  status: number,
): ApiErrorPayload {
  if (
    isRecord(body) &&
    typeof body.code === 'string' &&
    typeof body.message === 'string'
  ) {
    return {
      code: body.code,
      message: body.message,
      trace_id: typeof body.trace_id === 'string' ? body.trace_id : '',
      retryable: body.retryable === true,
      details: isRecord(body.details) ? body.details : {},
    }
  }
  return {
    code: `HTTP_${status}`,
    message: FALLBACK_MESSAGE_BY_STATUS[status] ?? '请求失败，请稍后重试',
    trace_id: '',
    retryable: status === 429 || status >= 502,
    details: {},
  }
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) {
    return null
  }
  try {
    return JSON.parse(text) as unknown
  } catch {
    return null
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined && !headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }
  const token = getToken()
  if (token) {
    headers.set('authorization', `Bearer ${token}`)
  }

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })
  } catch {
    throw new ApiRequestError({
      status: 0,
      code: 'NETWORK_ERROR',
      message: '无法连接后端服务，请确认 API 已启动后重试',
      retryable: true,
    })
  }

  const body = await readJson(response)
  if (!response.ok) {
    const payload = parseErrorPayload(body, response.status)
    throw new ApiRequestError({
      status: response.status,
      code: payload.code,
      message: payload.message,
      traceId: payload.trace_id || null,
      retryable: payload.retryable,
      details: payload.details,
    })
  }
  return body as T
}

export function login(
  username: string,
  password: string,
): Promise<LoginResponse> {
  return request<LoginResponse>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export function fetchMe(): Promise<UserPublic> {
  return request<UserPublic>('/api/v1/me')
}

export function fetchSystemVersion(): Promise<SystemVersion> {
  return request<SystemVersion>('/api/v1/system/version')
}

/* ---------------------------------------------------------------------------
 * 坐席工作台（T23 #24）：会话导入、候选答复、引用原文与反馈。
 * 状态字段原样展示，不在客户端把反馈或答复“改写”成正确性标签。
 * ------------------------------------------------------------------------- */

export function createConversation(
  input: ConversationCreateInput,
): Promise<ConversationPublic> {
  return request<ConversationPublic>('/api/v1/conversations', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function fetchConversation(
  conversationId: string,
): Promise<ConversationPublic> {
  return request<ConversationPublic>(
    `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
  )
}

export function createSuggestion(
  input: SuggestionCreateInput,
): Promise<SuggestionPublic> {
  return request<SuggestionPublic>('/api/v1/suggestions', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function fetchSuggestion(
  suggestionId: string,
): Promise<SuggestionDetail> {
  return request<SuggestionDetail>(
    `/api/v1/suggestions/${encodeURIComponent(suggestionId)}`,
  )
}

export function submitSuggestionFeedback(
  suggestionId: string,
  input: SuggestionFeedbackInput,
): Promise<SuggestionFeedbackPublic> {
  return request<SuggestionFeedbackPublic>(
    `/api/v1/suggestions/${encodeURIComponent(suggestionId)}/feedback`,
    {
      method: 'POST',
      body: JSON.stringify(input),
    },
  )
}

export function fetchKnowledgeVersion(
  versionId: string,
): Promise<KnowledgeVersion> {
  return request<KnowledgeVersion>(
    `/api/v1/knowledge/versions/${encodeURIComponent(versionId)}`,
  )
}

/* ---------------------------------------------------------------------------
 * 质检复核（T24 #25 / M5-02）：复核列表/详情、逐项决定与完成。
 * 决定与完成都携带 expected_revision；409 由调用方显式展示并刷新。
 * ------------------------------------------------------------------------- */

export interface ReviewListQuery {
  status?: ReviewStatus
  limit?: number
  offset?: number
}

export function fetchReviews(query: ReviewListQuery = {}): Promise<ReviewList> {
  const params = new URLSearchParams()
  if (query.status !== undefined) {
    params.set('status', query.status)
  }
  params.set('limit', String(query.limit ?? 20))
  params.set('offset', String(query.offset ?? 0))
  return request<ReviewList>(`/api/v1/reviews?${params.toString()}`)
}

export function fetchReview(reviewId: string): Promise<ReviewDetail> {
  return request<ReviewDetail>(
    `/api/v1/reviews/${encodeURIComponent(reviewId)}`,
  )
}

export function submitReviewDecision(
  reviewId: string,
  input: ReviewDecisionInput,
): Promise<ReviewDecisionAccepted> {
  return request<ReviewDecisionAccepted>(
    `/api/v1/reviews/${encodeURIComponent(reviewId)}/decisions`,
    {
      method: 'POST',
      body: JSON.stringify(input),
    },
  )
}

export function completeReview(
  reviewId: string,
  input: ReviewCompleteInput,
): Promise<ReviewDetail> {
  return request<ReviewDetail>(
    `/api/v1/reviews/${encodeURIComponent(reviewId)}/complete`,
    {
      method: 'POST',
      body: JSON.stringify(input),
    },
  )
}

export function fetchInspection(
  inspectionId: string,
): Promise<InspectionDetail> {
  return request<InspectionDetail>(
    `/api/v1/inspections/${encodeURIComponent(inspectionId)}`,
  )
}
