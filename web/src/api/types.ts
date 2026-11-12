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

/* ---------------------------------------------------------------------------
 * 质检与人工复核（T24 #25 / M5-02）：机器判断、证据坐标与追加式决定。
 * 机器判断只读；人工结论只追加到 decisions，前端不覆盖也不改写。
 * ------------------------------------------------------------------------- */

export type ReviewStatus = 'pending' | 'in_review' | 'completed'
export type ReviewAction = 'confirm' | 'reject' | 'modify'
export type ReviewVerdict =
  | 'hit'
  | 'clear'
  | 'insufficient_evidence'
  | 'not_applicable'

/** 后端 annotate_evidence 的输出：码点坐标 + 轮次序号 + 回验结果。 */
export interface ReviewEvidenceSpan {
  turn_id: string
  turn_ordinal: number | null
  start: number | null
  end: number | null
  quote: string | null
  validated: boolean
  validation_error?: string
}

export interface ReviewFinding {
  finding_id: string
  rule_version_id: string
  rule_id: string | null
  rule_code: string | null
  version_no: number | null
  severity: string
  machine_verdict: string
  effective_machine_verdict: string
  evidence_valid: boolean
  rationale: string
  evidence: ReviewEvidenceSpan[]
  knowledge_refs: Record<string, unknown>[]
  applicable: boolean
  final_verdict: string | null
  latest_action: string | null
  latest_decision_id: string | null
  decision_count: number
}

export interface ReviewDecision {
  decision_id: string
  review_id: string
  finding_id: string
  action: string
  final_verdict: string
  reason: string
  actor_id: string
  expected_revision: number
  accepted_revision: number
  created_at: string
}

export interface ReviewStatusEvent {
  event_id: string
  action: string
  previous_status: string | null
  status: string | null
  revision: number | null
  actor_id: string
  timestamp: string
}

export interface ReviewDetail {
  review_id: string
  inspection_id: string
  inspection_status: string
  assignee_id: string
  status: string
  revision: number
  last_reopen_reason: string | null
  created_at: string
  updated_at: string
  applicable_finding_count: number
  decided_finding_count: number
  missing_decision_finding_ids: string[]
  pending_business_confirmation: boolean
  pending_confirmation_finding_ids: string[]
  findings: ReviewFinding[]
  decisions: ReviewDecision[]
  status_history: ReviewStatusEvent[]
}

export interface ReviewSummary {
  review_id: string
  inspection_id: string
  assignee_id: string
  status: string
  revision: number
  created_at: string
  updated_at: string
}

export interface ReviewList {
  items: ReviewSummary[]
  total: number
  limit: number
  offset: number
}

export interface ReviewDecisionInput {
  finding_id: string
  action: ReviewAction
  final_verdict?: ReviewVerdict
  reason: string
  expected_revision: number
}

export interface ReviewDecisionAccepted {
  review_id: string
  inspection_id: string
  status: string
  revision: number
  decision: ReviewDecision
}

export interface ReviewCompleteInput {
  expected_revision: number
  pending_business_confirmation: boolean
}

/** GET /inspections/{id} 的读取视图（复核页据此找到会话与原文轮次）。 */
export interface InspectionFinding {
  finding_id: string
  rule_version_id: string
  rule_id: string | null
  rule_code: string | null
  version_no: number | null
  machine_verdict: string
  severity: string
  rationale: string
  evidence: ReviewEvidenceSpan[]
  knowledge_refs: Record<string, unknown>[]
  evidence_valid: boolean
  raw_machine_verdict: string | null
  created_at: string
}

export interface InspectionDetail {
  inspection_id: string
  conversation_id: string
  conversation_hash: string
  status: string
  error_code: string | null
  diagnostics: Record<string, unknown>
  rule_count: number
  task_id: string | null
  parent_run_id: string | null
  run_id: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
  findings: InspectionFinding[]
}
