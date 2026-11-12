/** 契约 §1 错误展示：状态码分类 + 稳定错误码 + trace_id，不渲染服务端 HTML。 */

import type { ReactNode } from 'react'

import type { ApiRequestError } from '../api/client'

export function errorHeading(error: ApiRequestError, prefix: string): string {
  if (error.status === 401) {
    return '登录状态已失效'
  }
  if (error.status === 403) {
    return `无权限：${prefix}`
  }
  if (error.status === 404) {
    return `${prefix}失败：资源不存在或不可见`
  }
  if (error.status === 409) {
    return `${prefix}冲突：版本或状态已变化`
  }
  if (error.status === 0) {
    return `${prefix}失败：无法连接后端`
  }
  if (error.status >= 500) {
    return `${prefix}失败：依赖服务不可用`
  }
  return `${prefix}失败`
}

export default function ApiErrorNotice({
  error,
  prefix,
  children,
}: {
  error: ApiRequestError
  prefix: string
  children?: ReactNode
}) {
  return (
    <div className="alert error" role="alert">
      <strong>{errorHeading(error, prefix)}</strong>
      <span>{error.message}</span>
      <span className="trace">错误码: {error.code}</span>
      {error.traceId !== null && <span className="trace">trace_id: {error.traceId}</span>}
      {children}
    </div>
  )
}
