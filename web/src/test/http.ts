/** 测试用 HTTP 替身：不启动后端，也不访问网络。 */

import type { ApiErrorPayload } from '../api/types'

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

export function errorPayload(
  overrides: Partial<ApiErrorPayload> = {},
): ApiErrorPayload {
  return {
    code: 'FORBIDDEN',
    message: '无权限访问该资源',
    trace_id: 'trace-test-1',
    retryable: false,
    details: {},
    ...overrides,
  }
}
