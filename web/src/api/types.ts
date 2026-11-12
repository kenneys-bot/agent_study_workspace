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

/* ---------------------------------------------------------------------------
 * 坐席工作台（T23 #24）：会话、候选答复、引用与反馈。
 * 字段镜像后端契约 §5，不在前端自造状态或正确性标签。
 * ------------------------------------------------------------------------- */

export type SourceType = 'manual_paste' | 'file_upload' | 'demo_seed'
export type DataClassification = 'synthetic_demo' | 'internal'
export type SuggestionStatus =
  | 'answered'
  | 'needs_clarification'
  | 'needs_human'
  | 'failed'
export type FeedbackAction = 'adopt' | 'edit' | 'reject'

export interface TurnPublic {
  turn_id: string
  ordinal: number
  speaker: string
  /** 展示文本（可能已脱敏）；原文坐标以 original_content_length 为基准。 */
  content: string
  original_content_length: number
  timestamp: string
}

export interface ConversationPublic {
  conversation_id: string
  scope_id: string
  owner_id: string
  source_type: string
  source_ref: string | null
  content_hash: string
  data_classification: string
  redaction_applied: boolean
  created_at: string
  updated_at: string
  turns: TurnPublic[]
  evidence_coordinate_basis: 'original_immutable_content'
}

export interface ConversationCreateInput {
  content: string
  format?: 'text' | 'json'
  source_type?: SourceType
  data_classification?: DataClassification
}

export interface Citation {
  version_id: string
  chunk_id: string
  quote: string
}

export interface SuggestionPublic {
  suggestion_id: string
  conversation_id: string | null
  run_id: string
  query: string
  rewritten_query: string | null
  business_type: string | null
  status: string
  answer: string
  citations: Citation[]
  missing_information: string[]
  retrieval_snapshot: Record<string, unknown>
  mode: string
  created_at: string
}

export interface SuggestionCreateInput {
  conversation_id?: string
  query?: string
  business_type?: string
}

export interface SuggestionFeedbackPublic {
  feedback_id: string
  suggestion_id: string
  actor_id: string
  action: string
  edited_text: string | null
  reason: string | null
  created_at: string
}

export interface SuggestionFeedbackInput {
  action: FeedbackAction
  edited_text?: string
  reason?: string
}

export interface SuggestionDetail extends SuggestionPublic {
  feedback: SuggestionFeedbackPublic[]
}

export interface KnowledgeDocumentSummary {
  document_id: string
  title: string
  source_type: string
  source_uri: string | null
  business_type: string
  scope_id: string
  created_at: string
  updated_at: string
}

export interface KnowledgeVersion {
  version_id: string
  document_id: string
  version_no: number
  content: string
  content_hash: string
  status: string
  valid_from: string
  valid_to: string | null
  author_id: string
  reviewer_id: string | null
  review_reason: string | null
  retire_reason: string | null
  revision: number
  created_at: string
  updated_at: string
  document: KnowledgeDocumentSummary
}
