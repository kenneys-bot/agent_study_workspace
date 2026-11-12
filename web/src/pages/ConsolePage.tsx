/**
 * 坐席工作台（T23 #24 / M5-02、M5-03）。
 *
 * 三个界面故事：导入真实会话 → 生成候选答复并核验引用原文 → 提交坐席反馈。
 *
 * - 数据全部来自后端契约 §5，不使用静态假数据；
 * - 候选答复状态原样展示，``failed`` 只作系统失败处理，不伪装成业务状态；
 * - 采纳/编辑后采纳/拒绝只是**反馈**，不改变候选答复的状态或文本，也不是
 *   正确性标签（GLOSSARY：候选答复、采纳、引用）；
 * - 加载、空结果、失败、401/403/409/5xx 都有显式 UI 状态，重复点击被禁用。
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  ApiRequestError,
  asApiRequestError,
  createConversation,
  createSuggestion,
  fetchKnowledgeVersion,
  submitSuggestionFeedback,
} from '../api/client'
import type {
  Citation,
  ConversationPublic,
  FeedbackAction,
  KnowledgeVersion,
  SuggestionFeedbackPublic,
  SuggestionPublic,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'

type ImportState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; error: ApiRequestError }
  | { status: 'ready'; conversation: ConversationPublic }

type SuggestionState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; error: ApiRequestError }
  | { status: 'ready'; suggestion: SuggestionPublic }

type CitationPanelState =
  | { status: 'loading'; citation: Citation }
  | { status: 'error'; citation: Citation; error: ApiRequestError }
  | { status: 'ready'; citation: Citation; version: KnowledgeVersion }

type FeedbackMode = FeedbackAction | null

const BUSINESS_TYPE_OPTIONS = [
  { value: '', label: '未指定（由后端按会话内容判断）' },
  { value: 'lost_card', label: '挂失咨询' },
  { value: 'credit_card_fee', label: '信用卡费用/还款咨询' },
  { value: 'complaint', label: '投诉受理' },
] as const

const STATUS_META: Record<
  string,
  { label: string; hint: string; tone: string }
> = {
  answered: {
    label: '已生成答复',
    hint: '答复由本次可校验的知识引用支撑，请坐席核对后使用。',
    tone: 'answered',
  },
  needs_clarification: {
    label: '需澄清',
    hint: '当前信息不足，请补充客户诉求后再生成候选答复。',
    tone: 'clarification',
  },
  needs_human: {
    label: '转人工',
    hint: '当前条件下不提供自动答复，请转人工处理。',
    tone: 'human',
  },
  failed: {
    label: '生成失败',
    hint: '系统或模型执行失败，本次未生成答复。',
    tone: 'failed',
  },
}

const FEEDBACK_LABEL: Record<string, string> = {
  adopt: '采纳（原样使用）',
  edit: '编辑后采纳',
  reject: '拒绝',
}

function asArray(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value : null
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString('zh-CN', { hour12: false })
}

function errorHeading(
  error: ApiRequestError,
  scope: 'import' | 'suggestion' | 'feedback' | 'citation',
): string {
  const prefix =
    scope === 'import'
      ? '会话导入'
      : scope === 'suggestion'
        ? '候选答复生成'
        : scope === 'citation'
          ? '引用原文读取'
          : '反馈提交'
  if (error.status === 401) {
    return '登录状态已失效'
  }
  if (error.status === 403) {
    return `无权限：${prefix}`
  }
  if (error.status === 409) {
    return `${prefix}冲突：资源状态或版本已变化`
  }
  if (error.status === 404) {
    return `${prefix}失败：资源不存在或不可见`
  }
  if (error.status === 0) {
    return `${prefix}失败：无法连接后端`
  }
  if (error.status >= 500) {
    return `${prefix}失败：依赖服务不可用`
  }
  return `${prefix}失败`
}

function ErrorNotice({ error, heading }: { error: ApiRequestError; heading: string }) {
  return (
    <div className="alert error" role="alert">
      <strong>{heading}</strong>
      <span>{error.message}</span>
      <span className="trace">错误码: {error.code}</span>
      {error.traceId !== null && <span className="trace">trace_id: {error.traceId}</span>}
      {typeof error.details.suggestion_id === 'string' && (
        <span className="trace">建议记录 ID: {error.details.suggestion_id}</span>
      )}
    </div>
  )
}

export default function ConsolePage() {
  const { signOut } = useAuth()
  const navigate = useNavigate()

  const [transcript, setTranscript] = useState('')
  const [importState, setImportState] = useState<ImportState>({ status: 'idle' })

  const [businessType, setBusinessType] = useState('')
  const [suggestionState, setSuggestionState] = useState<SuggestionState>({
    status: 'idle',
  })
  const [citationPanel, setCitationPanel] = useState<CitationPanelState | null>(
    null,
  )

  const [feedbacks, setFeedbacks] = useState<SuggestionFeedbackPublic[]>([])
  const [feedbackMode, setFeedbackMode] = useState<FeedbackMode>(null)
  const [editedText, setEditedText] = useState('')
  const [rejectReason, setRejectReason] = useState('')
  const [feedbackBusy, setFeedbackBusy] = useState(false)
  const [feedbackError, setFeedbackError] = useState<ApiRequestError | null>(null)
  const [feedbackValidation, setFeedbackValidation] = useState<string | null>(
    null,
  )

  function handleUnauthorized(error: ApiRequestError): boolean {
    if (error.status !== 401) {
      return false
    }
    signOut()
    navigate('/login', { replace: true })
    return true
  }

  async function handleImport() {
    if (importState.status === 'loading' || transcript.trim() === '') {
      return
    }
    setImportState({ status: 'loading' })
    setSuggestionState({ status: 'idle' })
    setCitationPanel(null)
    setFeedbacks([])
    setFeedbackMode(null)
    try {
      const conversation = await createConversation({
        content: transcript,
        format: 'text',
        source_type: 'manual_paste',
        data_classification: 'synthetic_demo',
      })
      setImportState({ status: 'ready', conversation })
    } catch (cause) {
      const error = asApiRequestError(cause)
      if (handleUnauthorized(error)) {
        return
      }
      setImportState({ status: 'error', error })
    }
  }

  async function handleGenerate() {
    if (importState.status !== 'ready' || suggestionState.status === 'loading') {
      return
    }
    setSuggestionState({ status: 'loading' })
    setCitationPanel(null)
    setFeedbacks([])
    setFeedbackMode(null)
    setFeedbackError(null)
    setFeedbackValidation(null)
    try {
      const suggestion = await createSuggestion({
        conversation_id: importState.conversation.conversation_id,
        ...(businessType ? { business_type: businessType } : {}),
      })
      setSuggestionState({ status: 'ready', suggestion })
    } catch (cause) {
      const error = asApiRequestError(cause)
      if (handleUnauthorized(error)) {
        return
      }
      setSuggestionState({ status: 'error', error })
    }
  }

  async function handleOpenCitation(citation: Citation) {
    setCitationPanel({ status: 'loading', citation })
    try {
      const version = await fetchKnowledgeVersion(citation.version_id)
      setCitationPanel({ status: 'ready', citation, version })
    } catch (cause) {
      const error = asApiRequestError(cause)
      if (handleUnauthorized(error)) {
        return
      }
      setCitationPanel({ status: 'error', citation, error })
    }
  }

  function openFeedback(mode: FeedbackAction) {
    setFeedbackValidation(null)
    setFeedbackError(null)
    setFeedbackMode(mode)
    if (mode === 'edit' && suggestionState.status === 'ready') {
      setEditedText(suggestionState.suggestion.answer)
    }
    if (mode !== 'reject') {
      setRejectReason('')
    }
  }

  async function handleSubmitFeedback() {
    if (suggestionState.status !== 'ready' || feedbackMode === null || feedbackBusy) {
      return
    }

    let payload
    if (feedbackMode === 'edit') {
      if (editedText.trim() === '') {
        setFeedbackValidation('编辑后采纳必须填写修改后的文本')
        return
      }
      payload = { action: 'edit' as const, edited_text: editedText }
    } else if (feedbackMode === 'reject') {
      if (rejectReason.trim() === '') {
        setFeedbackValidation('拒绝时必须填写理由')
        return
      }
      payload = { action: 'reject' as const, reason: rejectReason }
    } else {
      payload = { action: 'adopt' as const }
    }

    setFeedbackValidation(null)
    setFeedbackBusy(true)
    setFeedbackError(null)
    try {
      const feedback = await submitSuggestionFeedback(
        suggestionState.suggestion.suggestion_id,
        payload,
      )
      // 只追加反馈史；不修改 suggestion 的状态或 answer。
      setFeedbacks((current) => [...current, feedback])
      setFeedbackMode(null)
      setEditedText('')
      setRejectReason('')
    } catch (cause) {
      const error = asApiRequestError(cause)
      if (handleUnauthorized(error)) {
        return
      }
      setFeedbackError(error)
    } finally {
      setFeedbackBusy(false)
    }
  }

  function handleUseEdited() {
    void handleSubmitFeedback()
  }

  const suggestion =
    suggestionState.status === 'ready' ? suggestionState.suggestion : null
  const statusMeta =
    suggestion !== null
      ? (STATUS_META[suggestion.status] ?? {
          label: suggestion.status,
          hint: '后端返回了未识别的候选答复状态，请人工核实。',
          tone: 'unknown',
        })
      : null

  const snapshot = suggestion?.retrieval_snapshot ?? {}
  const indexVersion =
    typeof snapshot.index_version === 'string' && snapshot.index_version !== ''
      ? snapshot.index_version
      : null
  const rejected = asArray(snapshot.rejected_citations)

  return (
    <section className="console">
      <header className="page-header">
        <div>
          <p className="eyebrow">坐席辅助</p>
          <h1>坐席工作台</h1>
          <p className="muted">
            粘贴会话 → 生成候选答复 → 核对引用原文 → 记录坐席反馈。数据来自{' '}
            <code>POST /api/v1/conversations</code> 与{' '}
            <code>POST /api/v1/suggestions</code>，不使用静态假数据。
          </p>
        </div>
      </header>

      <section className="panel" aria-labelledby="import-title">
        <h2 id="import-title">1. 导入会话</h2>
        <p className="muted">
          支持后端解析器接受的文本格式，每行使用「说话人：内容」，例如
          「客服：您好，请问有什么可以帮您？」。原文不可变，轮次序号由后端分配。
        </p>

        <div className="form console-form">
          <label htmlFor="transcript">会话原文</label>
          <textarea
            id="transcript"
            name="transcript"
            value={transcript}
            onChange={(event) => setTranscript(event.target.value)}
            rows={7}
            maxLength={200_000}
            placeholder={'客服：您好，请问有什么可以帮您？\n客户：我的银行卡丢失了，怎么挂失？'}
          />
          <button
            type="button"
            className="button primary"
            onClick={() => void handleImport()}
            disabled={importState.status === 'loading' || transcript.trim() === ''}
          >
            {importState.status === 'loading' ? '导入中…' : '导入会话'}
          </button>
        </div>

        {importState.status === 'idle' && (
          <p className="empty-state" role="status">
            尚未导入会话。粘贴一段会话后点击「导入会话」，这里会显示解析后的轮次。
          </p>
        )}

        {importState.status === 'loading' && (
          <p className="empty-state" role="status">
            正在导入并解析会话…
          </p>
        )}

        {importState.status === 'error' && (
          <div className="actions">
            <ErrorNotice
              error={importState.error}
              heading={errorHeading(importState.error, 'import')}
            />
            <button
              type="button"
              className="button"
              onClick={() => void handleImport()}
            >
              重试导入
            </button>
          </div>
        )}

        {importState.status === 'ready' && (
          <div className="conversation-view" data-testid="conversation-view">
            <h3>会话与轮次</h3>
            <dl className="detail-list">
              <div>
                <dt>会话 ID</dt>
                <dd>
                  <code>{importState.conversation.conversation_id}</code>
                </dd>
              </div>
              <div>
                <dt>轮次数</dt>
                <dd>{importState.conversation.turns.length}</dd>
              </div>
              <div>
                <dt>数据分级</dt>
                <dd>{importState.conversation.data_classification}</dd>
              </div>
              <div>
                <dt>脱敏展示</dt>
                <dd>
                  {importState.conversation.redaction_applied
                    ? '是（展示文本已等长遮蔽；证据坐标仍按原文）'
                    : '否'}
                </dd>
              </div>
            </dl>

            {importState.conversation.turns.length === 0 ? (
              <p className="empty-state">解析结果没有轮次，请检查会话格式。</p>
            ) : (
              <ol className="turn-list" aria-label="解析后的会话轮次">
                {importState.conversation.turns.map((turn) => (
                  <li key={turn.turn_id} className="turn" data-testid="turn">
                    <div className="turn-meta">
                      <span className="turn-ordinal">#{turn.ordinal}</span>
                      <span className="turn-speaker">{turn.speaker}</span>
                      <span className="turn-time">{formatTime(turn.timestamp)}</span>
                    </div>
                    <p className="turn-content">{turn.content}</p>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="suggestion-title">
        <header className="page-header console-section-header">
          <div>
            <h2 id="suggestion-title">2. 候选答复</h2>
            <p className="muted">
              依据已发布知识生成，供坐席检查与编辑；引用必须能打开原知识版本核对。
            </p>
          </div>
          <div className="inline-field">
            <label htmlFor="business-type">业务类型（可选）</label>
            <select
              id="business-type"
              value={businessType}
              onChange={(event) => setBusinessType(event.target.value)}
              disabled={suggestionState.status === 'loading'}
            >
              {BUSINESS_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </header>

        <div className="actions">
          <button
            type="button"
            className="button primary"
            onClick={() => void handleGenerate()}
            disabled={
              importState.status !== 'ready' || suggestionState.status === 'loading'
            }
          >
            {suggestionState.status === 'loading' ? '生成中…' : '生成候选答复'}
          </button>
          {importState.status !== 'ready' && (
            <span className="muted inline-note">先导入会话后才能生成候选答复。</span>
          )}
        </div>

        {suggestionState.status === 'idle' && importState.status === 'ready' && (
          <p className="empty-state" role="status">
            尚未生成候选答复。点击「生成候选答复」后会显示状态、答复、引用与检索快照。
          </p>
        )}

        {suggestionState.status === 'loading' && (
          <p className="empty-state" role="status">
            正在生成候选答复，请勿重复提交…
          </p>
        )}

        {suggestionState.status === 'error' && (
          <div className="actions">
            <ErrorNotice
              error={suggestionState.error}
              heading={errorHeading(suggestionState.error, 'suggestion')}
            />
            <button
              type="button"
              className="button"
              onClick={() => void handleGenerate()}
            >
              重试生成
            </button>
          </div>
        )}

        {suggestion !== null && statusMeta !== null && (
          <div className="suggestion-view" data-testid="suggestion-view">
            <div className="suggestion-status-row">
              <span
                className={`status-badge status-${statusMeta.tone}`}
                data-testid="suggestion-status"
              >
                {statusMeta.label}
              </span>
              <span className="muted">{statusMeta.hint}</span>
            </div>

            {suggestion.status === 'failed' && (
              <div className="alert error" role="alert">
                <strong>生成失败</strong>
                <span>系统或模型执行失败，本次没有可用的候选答复。</span>
                {typeof snapshot.error === 'string' && (
                  <span className="trace">原因: {snapshot.error}</span>
                )}
              </div>
            )}

            <dl className="detail-list suggestion-meta">
              <div>
                <dt>建议 ID</dt>
                <dd>
                  <code>{suggestion.suggestion_id}</code>
                </dd>
              </div>
              <div>
                <dt>run_id</dt>
                <dd>
                  <code>{suggestion.run_id}</code>
                </dd>
              </div>
              <div>
                <dt>业务类型</dt>
                <dd>{suggestion.business_type ?? '未指定'}</dd>
              </div>
              <div>
                <dt>运行模式</dt>
                <dd>{suggestion.mode}</dd>
              </div>
              <div>
                <dt>检索索引版本</dt>
                <dd>
                  {indexVersion !== null ? (
                    <code>{indexVersion}</code>
                  ) : (
                    <span className="muted-value">未记录</span>
                  )}
                </dd>
              </div>
              {rejected !== null && (
                <div>
                  <dt>引用校验拒绝</dt>
                  <dd data-testid="rejected-citations">
                    {rejected.length} 条
                    {rejected.length > 0 && '（未交付给坐席，见检索快照）'}
                  </dd>
                </div>
              )}
            </dl>

            {suggestion.status !== 'needs_human' &&
              suggestion.status !== 'failed' &&
              suggestion.answer.trim() !== '' && (
                <div className="answer-block">
                  <h3>候选答复</h3>
                  <p className="answer-text" data-testid="suggestion-answer">
                    {suggestion.answer}
                  </p>
                </div>
              )}

            {suggestion.status === 'needs_clarification' &&
              suggestion.answer.trim() === '' && (
                <p className="empty-state">
                  当前状态未提供答复文本，请补充信息后再生成。
                </p>
              )}

            {suggestion.status === 'needs_human' && (
              <p className="empty-state" data-testid="no-answer">
                转人工状态下不展示候选答复文本。
              </p>
            )}

            {suggestion.missing_information.length > 0 && (
              <div className="missing-info">
                <h3>缺失信息</h3>
                <ul>
                  {suggestion.missing_information.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="citations">
              <h3>引用（{suggestion.citations.length}）</h3>
              {suggestion.citations.length === 0 ? (
                <p className="empty-state">
                  本次没有可校验的引用，因此不提供无依据答复。
                </p>
              ) : (
                <ul className="citation-list" aria-label="候选答复引用">
                  {suggestion.citations.map((citation) => (
                    <li
                      key={`${citation.version_id}-${citation.chunk_id}`}
                      className="citation"
                      data-testid="citation"
                    >
                      <blockquote className="citation-quote">
                        “{citation.quote}”
                      </blockquote>
                      <dl className="detail-list citation-meta">
                        <div>
                          <dt>知识版本</dt>
                          <dd>
                            <code>{citation.version_id}</code>
                          </dd>
                        </div>
                        <div>
                          <dt>片段 ID</dt>
                          <dd>
                            <code>{citation.chunk_id}</code>
                          </dd>
                        </div>
                      </dl>
                      <button
                        type="button"
                        className="button"
                        onClick={() => void handleOpenCitation(citation)}
                      >
                        打开原文
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {citationPanel !== null && (
                <aside className="citation-panel" aria-labelledby="citation-panel-title">
                  <h3 id="citation-panel-title">知识版本原文</h3>
                  <p className="muted">
                    版本 ID: <code>{citationPanel.citation.version_id}</code>
                  </p>
                  {citationPanel.status === 'loading' && (
                    <p role="status">正在读取知识版本原文…</p>
                  )}
                  {citationPanel.status === 'error' && (
                    <ErrorNotice
                      error={citationPanel.error}
                      heading={errorHeading(citationPanel.error, 'citation')}
                    />
                  )}
                  {citationPanel.status === 'ready' && (
                    <>
                      <dl className="detail-list">
                        <div>
                          <dt>文档标题</dt>
                          <dd>{citationPanel.version.document.title}</dd>
                        </div>
                        <div>
                          <dt>版本号</dt>
                          <dd>{citationPanel.version.version_no}</dd>
                        </div>
                        <div>
                          <dt>状态</dt>
                          <dd>{citationPanel.version.status}</dd>
                        </div>
                        <div>
                          <dt>来源</dt>
                          <dd>
                            {citationPanel.version.document.source_uri ?? (
                              <span className="muted-value">未提供来源链接</span>
                            )}
                          </dd>
                        </div>
                      </dl>
                      <pre className="version-content" data-testid="citation-content">
                        {citationPanel.version.content}
                      </pre>
                    </>
                  )}
                  <button
                    type="button"
                    className="button"
                    onClick={() => setCitationPanel(null)}
                  >
                    关闭原文
                  </button>
                </aside>
              )}
            </div>

            <div className="feedback" aria-labelledby="feedback-title">
              <h3 id="feedback-title">3. 坐席反馈</h3>
              <p className="muted">
                反馈只记录坐席动作与修改文本，不改变候选答复的状态或内容，也不作为正确性标签。
              </p>

              <div className="actions feedback-actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => openFeedback('adopt')}
                  disabled={feedbackBusy}
                >
                  采纳
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => openFeedback('edit')}
                  disabled={feedbackBusy}
                >
                  编辑后采纳
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => openFeedback('reject')}
                  disabled={feedbackBusy}
                >
                  拒绝
                </button>
              </div>

              {feedbackMode === 'adopt' && (
                <div className="feedback-form">
                  <p className="muted">确认原样采纳该候选答复？</p>
                  <div className="actions">
                    <button
                      type="button"
                      className="button primary"
                      onClick={() => void handleSubmitFeedback()}
                      disabled={feedbackBusy}
                    >
                      {feedbackBusy ? '提交中…' : '确认采纳'}
                    </button>
                    <button
                      type="button"
                      className="button"
                      onClick={() => setFeedbackMode(null)}
                      disabled={feedbackBusy}
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {feedbackMode === 'edit' && (
                <div className="feedback-form">
                  <label htmlFor="edited-text">修改后的答复</label>
                  <textarea
                    id="edited-text"
                    value={editedText}
                    onChange={(event) => setEditedText(event.target.value)}
                    rows={5}
                    maxLength={4000}
                  />
                  <div className="actions">
                    <button
                      type="button"
                      className="button primary"
                      onClick={handleUseEdited}
                      disabled={feedbackBusy}
                    >
                      {feedbackBusy ? '提交中…' : '提交编辑后采纳'}
                    </button>
                    <button
                      type="button"
                      className="button"
                      onClick={() => setFeedbackMode(null)}
                      disabled={feedbackBusy}
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {feedbackMode === 'reject' && (
                <div className="feedback-form">
                  <label htmlFor="reject-reason">拒绝理由（必填）</label>
                  <textarea
                    id="reject-reason"
                    value={rejectReason}
                    onChange={(event) => setRejectReason(event.target.value)}
                    rows={3}
                    maxLength={2000}
                  />
                  <div className="actions">
                    <button
                      type="button"
                      className="button primary"
                      onClick={() => void handleSubmitFeedback()}
                      disabled={feedbackBusy}
                    >
                      {feedbackBusy ? '提交中…' : '提交拒绝'}
                    </button>
                    <button
                      type="button"
                      className="button"
                      onClick={() => setFeedbackMode(null)}
                      disabled={feedbackBusy}
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {feedbackValidation !== null && (
                <div className="alert error" role="alert">
                  <span>{feedbackValidation}</span>
                </div>
              )}

              {feedbackError !== null && (
                <ErrorNotice
                  error={feedbackError}
                  heading={errorHeading(feedbackError, 'feedback')}
                />
              )}

              <div className="feedback-history">
                <h4>已记录的反馈（{feedbacks.length}）</h4>
                {feedbacks.length === 0 ? (
                  <p className="empty-state">尚未提交反馈。</p>
                ) : (
                  <ul aria-label="已记录的反馈">
                    {feedbacks.map((feedback) => (
                      <li key={feedback.feedback_id} data-testid="feedback-item">
                        <strong>
                          {FEEDBACK_LABEL[feedback.action] ?? feedback.action}
                        </strong>
                        {feedback.edited_text !== null && (
                          <p>编辑文本：{feedback.edited_text}</p>
                        )}
                        {feedback.reason !== null && <p>理由：{feedback.reason}</p>}
                        <span className="trace">
                          {formatTime(feedback.created_at)} · {feedback.feedback_id}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        )}
      </section>
    </section>
  )
}
