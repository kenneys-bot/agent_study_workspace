/** 契约 §5 的前端类型：登录、当前身份与系统版本。 */

export interface UserPublic {
  user_id: string
  username: string
  role: string
  scope_id: string
}

export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: UserPublic
}

export interface SystemVersion {
  code_sha: string
  git_branch: string
  model_provider: string
  model_chat: string | null
  embedding_model: string
  index_version: string | null
  mode: string
  app_version: string
}

/** 契约 §1：`{code, message, trace_id, retryable, details}`。 */
export interface ApiErrorPayload {
  code: string
  message: string
  trace_id: string
  retryable: boolean
  details: Record<string, unknown>
}
