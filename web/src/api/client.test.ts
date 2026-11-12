import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearToken, getToken, setToken, TOKEN_STORAGE_KEY } from '../auth/tokenStore'
import { jsonResponse, errorPayload } from '../test/http'
import {
  ApiRequestError,
  fetchSystemVersion,
  login,
  parseErrorPayload,
} from './client'

const VERSION = {
  code_sha: 'abc123',
  git_branch: 'ticket/T22',
  model_provider: 'deepseek',
  model_chat: 'deepseek-chat',
  embedding_model: 'BAAI/bge-small-zh-v1.5',
  index_version: 'idx-1',
  mode: 'live',
  app_version: '1.0.0',
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('API client', () => {
  it('附带内存中的 Bearer 令牌并请求真实路径', async () => {
    setToken('jwt-token-1')
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(VERSION))
    vi.stubGlobal('fetch', fetchMock)

    await fetchSystemVersion()

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/\/api\/v1\/system\/version$/)
    expect(new Headers(init?.headers).get('authorization')).toBe(
      'Bearer jwt-token-1',
    )
  })

  it('把契约 §1 错误体解析成 ApiRequestError', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(errorPayload({ trace_id: 'trace-403' }), 403),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchSystemVersion()).rejects.toMatchObject({
      status: 403,
      code: 'FORBIDDEN',
      message: '无权限访问该资源',
      traceId: 'trace-403',
      retryable: false,
    })
  })

  it('登录时 POST 真实凭据，不内置默认账号', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        access_token: 'jwt-token-2',
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

    const result = await login('alice', 'correct-horse')

    expect(result.access_token).toBe('jwt-token-2')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/\/api\/v1\/auth\/login$/)
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({
      username: 'alice',
      password: 'correct-horse',
    })
  })

  it('非契约错误响应也有稳定兜底，不会渲染服务端原文', () => {
    const payload = parseErrorPayload('<html>bad gateway</html>', 502)

    expect(payload.code).toBe('HTTP_502')
    expect(payload.message).toBe('依赖服务返回异常')
    expect(payload.retryable).toBe(true)
    expect(payload.details).toEqual({})
  })

  it('网络失败转换成可重试错误', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)

    const error = await fetchSystemVersion().catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error).toMatchObject({ code: 'NETWORK_ERROR', retryable: true })
  })
})

describe('token store', () => {
  it('同时保存到内存与 localStorage，退出时两处都清空', () => {
    setToken('jwt-token-3')
    expect(getToken()).toBe('jwt-token-3')
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBe('jwt-token-3')

    clearToken()
    expect(getToken()).toBeNull()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
  })
})
