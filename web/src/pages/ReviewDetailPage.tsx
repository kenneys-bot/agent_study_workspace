/**
 * 质检复核详情（T24 #25 / M5-02、M5-03）。
 *
 * 并排展示不可变原文与机器判断/证据：
 * - 左侧原文来自 ``GET /conversations/{id}``（经 ``GET /inspections/{id}`` 找到
 *   会话）；证据坐标用契约 §4 的码点→UTF-16 换算高亮，emoji/换行不能切错；
 * - 右侧逐条展示 rule_code、severity、机器判断、理由、证据 quote 与轮次；
 * - 人工动作 confirm/reject/modify 只追加决定，都带 ``expected_revision``；
 *   409 明确提示并刷新到最新 revision，不静默重试；
 * - 「证据不足」单独列为待业务确认，绝不渲染成合格/clear。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import {
  ApiRequestError,
  asApiRequestError,
  completeReview,
  fetchConversation,
  fetchInspection,
  fetchReview,
  submitReviewDecision,
} from '../api/client'
import type {
  ConversationPublic,
  InspectionDetail,
  ReviewAction,
  ReviewDecisionInput,
  ReviewDetail,
  ReviewFinding,
  ReviewVerdict,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import ApiErrorNotice from '../components/ApiErrorNotice'
import {
  buildHighlightSegments,
  type CodePointRange,
} from '../lib/evidence'
import {
  REVIEW_ACTION_LABEL,
  REVIEW_ACTION_VERDICTS,
  formatReviewTime,
  reviewStatusMeta,
  verdictLabel,
} from '../lib/reviews'

type DetailState =
  | { status: 'loading' }
  | { status: 'error'; error: ApiRequestError }
  | {
      status: 'ready'
      review: ReviewDetail
      inspection: InspectionDetail | null
      conversation: ConversationPublic | null
      sourceError: ApiRequestError | null
    }

interface DecisionDraft {
  findingId: string
  action: ReviewAction
  reason: string
  finalVerdict: ReviewVerdict | ''
}

interface ConflictNotice {
  message: string
  code: string
  currentRevision: number | null
}

const STATUS_ACTION_LABEL: Record<string, string> = {
  'review.created': '创建复核',
  'review.decision_recorded': '记录逐项结论',
  'review.completed': '完成复核',
  'review.reopened': '重新打开',
}

function HighlightedText({
  text,
  ranges,
}: {
  text: string
  ranges: readonly CodePointRange[]
}) {
  const segments = useMemo(() => {
    if (ranges.length === 0) {
      return [{ text, highlighted: false }]
    }
    try {
      return buildHighlightSegments(text, ranges)
    } catch {
      // 坏坐标不能把整页打挂；保留原文并明确提示回验失败。
      return [{ text, highlighted: false }]
    }
  }, [text, ranges])

  return (
    <>
      {segments.map((segment, index) =>
        segment.highlighted ? (
          <mark
            key={`${index}-${segment.text.length}`}
            className="evidence-mark"
            data-testid="evidence-mark"
          >
            {segment.text}
          </mark>
        ) : (
          <span key={`${index}-${segment.text.length}`}>{segment.text}</span>
        ),
      )}
    </>
  )
}

function FindingEvidence({ finding }: { finding: ReviewFinding }) {
  if (finding.evidence.length === 0) {
    return <p className="muted-value">该判定没有证据区间。</p>
  }
  return (
    <ul className="evidence-list" aria-label="证据片段">
      {finding.evidence.map((span, index) => (
        <li key={`${span.turn_id}-${span.start}-${span.end}-${index}`}>
          <blockquote className="evidence-quote">
            {span.quote !== null && span.quote !== ''
              ? `“${span.quote}”`
              : '（证据 quote 缺失）'}
          </blockquote>
          <span className="trace">
            {span.turn_ordinal !== null
              ? `第 ${span.turn_ordinal} 轮`
              : '轮次未知'}
            {span.start !== null && span.end !== null
              ? ` · 码点 [${span.start}, ${span.end})`
              : ''}
            {span.validated ? ' · 已回验' : ' · 未通过回验'}
          </span>
          {!span.validated && span.validation_error !== undefined && (
            <span className="trace evidence-invalid">
              校验原因: {span.validation_error}
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

export default function ReviewDetailPage() {
  const { reviewId = '' } = useParams()
  const { signOut } = useAuth()
  const navigate = useNavigate()

  const [state, setState] = useState<DetailState>({ status: 'loading' })
  const [draft, setDraft] = useState<DecisionDraft | null>(null)
  const [validation, setValidation] = useState<string | null>(null)
  const [actionError, setActionError] = useState<ApiRequestError | null>(null)
  const [conflict, setConflict] = useState<ConflictNotice | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [completeBusy, setCompleteBusy] = useState(false)
  const [pendingConfirmation, setPendingConfirmation] = useState(false)

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

  const loadDetail = useCallback(
    async (mode: 'initial' | 'refresh'): Promise<ReviewDetail | null> => {
      if (mode === 'initial') {
        setState({ status: 'loading' })
      }
      try {
        const review = await fetchReview(reviewId)
        let inspection: InspectionDetail | null = null
        let conversation: ConversationPublic | null = null
        let sourceError: ApiRequestError | null = null
        try {
          inspection = await fetchInspection(review.inspection_id)
          conversation = await fetchConversation(inspection.conversation_id)
        } catch (cause) {
          const error = asApiRequestError(cause)
          if (handleUnauthorized(error)) {
            return null
          }
          sourceError = error
        }
        setState({ status: 'ready', review, inspection, conversation, sourceError })
        return review
      } catch (cause) {
        const error = asApiRequestError(cause)
        if (handleUnauthorized(error)) {
          return null
        }
        setState({ status: 'error', error })
        return null
      }
    },
    [reviewId, handleUnauthorized],
  )

  useEffect(() => {
    void loadDetail('initial')
  }, [loadDetail])

  const review = state.status === 'ready' ? state.review : null

  const highlightsByTurn = useMemo(() => {
    const map = new Map<string, CodePointRange[]>()
    if (review === null) {
      return map
    }
    for (const finding of review.findings) {
      for (const span of finding.evidence) {
        if (
          !span.validated ||
          span.turn_id === '' ||
          span.start === null ||
          span.end === null
        ) {
          continue
        }
        const ranges = map.get(span.turn_id) ?? []
        ranges.push({ start: span.start, end: span.end })
        map.set(span.turn_id, ranges)
      }
    }
    return map
  }, [review])

  const pendingFindings = useMemo(() => {
    if (review === null) {
      return [] as ReviewFinding[]
    }
    return review.findings.filter((finding) => {
      if (finding.applicable === false) {
        return false
      }
      if (finding.final_verdict !== null) {
        return finding.final_verdict === 'insufficient_evidence'
      }
      return finding.effective_machine_verdict === 'insufficient_evidence'
    })
  }, [review])

  const undecidedCount = review?.missing_decision_finding_ids.length ?? 0
  const allDecided = undecidedCount === 0
  const needsPendingConfirmation = pendingFindings.length > 0
  const isCompleted = review?.status === 'completed'
  const completeDisabled =
    review === null ||
    isCompleted ||
    !allDecided ||
    (needsPendingConfirmation && !pendingConfirmation) ||
    completeBusy ||
    actionBusy

  function openDraft(finding: ReviewFinding, action: ReviewAction) {
    setValidation(null)
    setActionError(null)
    setConflict(null)
    setDraft({
      findingId: finding.finding_id,
      action,
      reason: '',
      finalVerdict: '',
    })
  }

  async function submitDraft() {
    if (review === null || draft === null || actionBusy) {
      return
    }
    const reason = draft.reason.trim()
    if (reason === '') {
      setValidation('理由必填：确认、驳回或改判都必须说明依据。')
      return
    }
    let finalVerdict: ReviewVerdict | undefined
    if (draft.action === 'modify') {
      if (draft.finalVerdict === '') {
        setValidation('改判必须选择新的最终结论。')
        return
      }
      finalVerdict = draft.finalVerdict
    }

    const payload: ReviewDecisionInput = {
      finding_id: draft.findingId,
      action: draft.action,
      reason,
      expected_revision: review.revision,
      ...(finalVerdict !== undefined ? { final_verdict: finalVerdict } : {}),
    }

    setValidation(null)
    setActionError(null)
    setConflict(null)
    setActionBusy(true)
    try {
      await submitReviewDecision(review.review_id, payload)
      setDraft(null)
      await loadDetail('refresh')
    } catch (cause) {
      const error = asApiRequestError(cause)
      if (handleUnauthorized(error)) {
        return
      }
      if (error.status === 409) {
        const currentRevision =
          typeof error.details.current_revision === 'number'
            ? error.details.current_revision
            : null
        setConflict({
          message: error.message,
          code: error.code,
          currentRevision,
        })
        // 冲突后必须刷新到服务端最新 revision；不静默用旧版本重试。
        await loadDetail('refresh')
      } else {
        setActionError(error)
      }
    } finally {
      setActionBusy(false)
    }
  }

  async function handleComplete() {
    if (review === null || completeDisabled) {
      return
    }
    setActionError(null)
    setConflict(null)
    setCompleteBusy(true)
    try {
      const updated = await completeReview(review.review_id, {
        expected_revision: review.revision,
        pending_business_confirmation: needsPendingConfirmation
          ? pendingConfirmation
          : false,
      })
      setState((current) =>
        current.status === 'ready'
          ? { ...current, review: updated }
          : current,
      )
    } catch (cause) {
      const error = asApiRequestError(cause)
      if (handleUnauthorized(error)) {
        return
      }
      if (error.status === 409) {
        const currentRevision =
          typeof error.details.current_revision === 'number'
            ? error.details.current_revision
            : null
        setConflict({
          message: error.message,
          code: error.code,
          currentRevision,
        })
        await loadDetail('refresh')
      } else {
        setActionError(error)
      }
    } finally {
      setCompleteBusy(false)
    }
  }

  if (state.status === 'loading') {
    return (
      <section className="reviews" data-testid="review-detail-page">
        <p className="empty-state" role="status">
          正在读取复核详情、质检与原始会话…
        </p>
      </section>
    )
  }

  if (state.status === 'error') {
    return (
      <section className="reviews" data-testid="review-detail-page">
        <header className="page-header">
          <div>
            <p className="eyebrow">质检</p>
            <h1>复核详情</h1>
          </div>
          <Link className="button" to="/reviews">
            返回列表
          </Link>
        </header>
        <section className="panel">
          <ApiErrorNotice error={state.error} prefix="复核详情读取" />
          <div className="actions">
            <button
              type="button"
              className="button"
              onClick={() => void loadDetail('initial')}
            >
              重试
            </button>
          </div>
        </section>
      </section>
    )
  }

  const { review: current, inspection, conversation, sourceError } = state
  const statusMeta = reviewStatusMeta(current.status)

  return (
    <section className="reviews" data-testid="review-detail-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">质检复核</p>
          <h1>复核详情</h1>
          <p className="muted">
            左侧不可变原文，右侧机器判断与证据；人工结论只追加，不覆盖机器判断。
          </p>
        </div>
        <Link className="button" to="/reviews">
          返回列表
        </Link>
      </header>

      <section className="panel review-summary" aria-labelledby="review-summary-title">
        <div className="review-summary-head">
          <h2 id="review-summary-title">复核概况</h2>
          <span
            className={`status-badge status-${statusMeta.tone}`}
            data-testid="review-status"
          >
            {statusMeta.label}
          </span>
          <span className="status-badge status-revision" data-testid="review-revision">
            revision {current.revision}
          </span>
        </div>
        <p className="muted">{statusMeta.hint}</p>
        <dl className="detail-list review-summary-meta">
          <div>
            <dt>复核 ID</dt>
            <dd>
              <code>{current.review_id}</code>
            </dd>
          </div>
          <div>
            <dt>质检 ID</dt>
            <dd>
              <code>{current.inspection_id}</code>
              {inspection !== null && `（${inspection.status}）`}
            </dd>
          </div>
          <div>
            <dt>复核员</dt>
            <dd>
              <code>{current.assignee_id}</code>
            </dd>
          </div>
          <div>
            <dt>创建 / 最近更新</dt>
            <dd>
              {formatReviewTime(current.created_at)} / {formatReviewTime(current.updated_at)}
            </dd>
          </div>
          <div>
            <dt>逐项进度</dt>
            <dd data-testid="decision-progress">
              已结论 {current.decided_finding_count} / 适用规则{' '}
              {current.applicable_finding_count}
            </dd>
          </div>
        </dl>
        {current.last_reopen_reason !== null && (
          <div className="alert info" role="status">
            <strong>最近一次重新打开的理由</strong>
            <span>{current.last_reopen_reason}</span>
          </div>
        )}
      </section>

      {conflict !== null && (
        <div className="alert error conflict" role="alert" data-testid="revision-conflict">
          <strong>提交冲突：复核已被其他人更新，你的提交未生效</strong>
          <span>{conflict.message}</span>
          <span className="trace">错误码: {conflict.code}</span>
          {conflict.currentRevision !== null && (
            <span className="trace">
              当前 revision: {conflict.currentRevision}；页面已刷新为最新版本，请重新核对后再提交。
            </span>
          )}
          {conflict.currentRevision === null && (
            <span className="trace">页面已刷新为最新版本，请重新核对后再提交。</span>
          )}
        </div>
      )}

      {actionError !== null && (
        <ApiErrorNotice error={actionError} prefix="复核提交" />
      )}

      <div className="review-layout">
        <section className="panel review-source" aria-labelledby="source-title">
          <h2 id="source-title">原始会话</h2>
          <p className="muted">
            原文不可变；高亮坐标按 Unicode 码点换算为 UTF-16 后渲染，emoji 与换行不会切错位置。
          </p>

          {sourceError !== null && (
            <div className="actions">
              <ApiErrorNotice error={sourceError} prefix="原始会话读取" />
              <button
                type="button"
                className="button"
                onClick={() => void loadDetail('refresh')}
              >
                重试读取
              </button>
            </div>
          )}

          {sourceError === null && conversation === null && (
            <p className="empty-state" role="status">
              正在读取原始会话…
            </p>
          )}

          {sourceError === null && conversation !== null && (
            <>
              {conversation.redaction_applied && (
                <p className="muted">
                  展示文本已等长脱敏；证据坐标仍按不可变原文换算。
                </p>
              )}
              {conversation.turns.length === 0 ? (
                <p className="empty-state">会话没有可展示的轮次。</p>
              ) : (
                <ol className="turn-list review-turn-list" aria-label="原始会话轮次">
                  {conversation.turns.map((turn) => {
                    const ranges = highlightsByTurn.get(turn.turn_id) ?? []
                    return (
                      <li
                        key={turn.turn_id}
                        className="turn"
                        data-testid={`source-turn-${turn.ordinal}`}
                      >
                        <div className="turn-meta">
                          <span className="turn-ordinal">#{turn.ordinal}</span>
                          <span className="turn-speaker">{turn.speaker}</span>
                          <span className="turn-time">{formatReviewTime(turn.timestamp)}</span>
                        </div>
                        <p className="turn-content" data-testid={`turn-content-${turn.ordinal}`}>
                          <HighlightedText text={turn.content} ranges={ranges} />
                        </p>
                        {ranges.length > 0 && (
                          <span className="trace">证据高亮 {ranges.length} 处</span>
                        )}
                      </li>
                    )
                  })}
                </ol>
              )}
            </>
          )}
        </section>

        <section className="panel review-findings" aria-labelledby="findings-title">
          <h2 id="findings-title">机器判断与逐项复核</h2>
          <p className="muted">
            机器判断只读；confirm 确认机器结论，reject 驳回为未命中，modify 必须给出新的最终结论，三者都必须填写理由。
          </p>

          {current.findings.length === 0 ? (
            <p className="empty-state">本次质检没有逐规则判断。</p>
          ) : (
            <ul className="finding-list" aria-label="逐规则机器判断">
              {current.findings.map((finding) => {
                const isDraft = draft?.findingId === finding.finding_id
                const decided = finding.final_verdict !== null
                return (
                  <li
                    key={finding.finding_id}
                    className="finding"
                    data-testid={`finding-${finding.finding_id}`}
                  >
                    <div className="finding-head">
                      <code className="finding-rule">
                        {finding.rule_code ?? finding.rule_version_id}
                      </code>
                      <span className={`severity severity-${finding.severity}`}>
                        严重度 {finding.severity}
                      </span>
                      {finding.applicable === false && (
                        <span className="status-badge status-not-applicable">不适用</span>
                      )}
                    </div>

                    <dl className="detail-list finding-meta">
                      <div>
                        <dt>机器判断</dt>
                        <dd data-testid={`machine-verdict-${finding.finding_id}`}>
                          <strong>{verdictLabel(finding.effective_machine_verdict)}</strong>
                          {finding.machine_verdict !==
                            finding.effective_machine_verdict && (
                            <span className="trace">
                              {' '}
                              原始机器判断 {verdictLabel(finding.machine_verdict)}（证据回验后降级）
                            </span>
                          )}
                        </dd>
                      </div>
                      <div>
                        <dt>机器理由</dt>
                        <dd>{finding.rationale}</dd>
                      </div>
                      <div>
                        <dt>证据</dt>
                        <dd>
                          <FindingEvidence finding={finding} />
                        </dd>
                      </div>
                      <div>
                        <dt>人工结论</dt>
                        <dd data-testid={`final-verdict-${finding.finding_id}`}>
                          {decided ? (
                            <>
                              <strong>{verdictLabel(finding.final_verdict)}</strong>
                              {finding.latest_action !== null && (
                                <span className="trace">
                                  {' '}
                                  动作 {REVIEW_ACTION_LABEL[finding.latest_action] ?? finding.latest_action}
                                  {' '}· 已记录 {finding.decision_count} 次
                                </span>
                              )}
                            </>
                          ) : (
                            <span className="muted-value">尚未结论</span>
                          )}
                        </dd>
                      </div>
                    </dl>

                    {finding.applicable === false ? (
                      <p className="muted-value">不适用规则无需逐项结论，也不计入待业务确认。</p>
                    ) : isCompleted ? (
                      <p className="muted-value">复核已完成，逐项决定历史只读。</p>
                    ) : (
                      <div className="finding-actions">
                        <button
                          type="button"
                          className="button"
                          onClick={() => openDraft(finding, 'confirm')}
                          disabled={actionBusy}
                        >
                          确认
                        </button>
                        <button
                          type="button"
                          className="button"
                          onClick={() => openDraft(finding, 'reject')}
                          disabled={actionBusy}
                        >
                          驳回
                        </button>
                        <button
                          type="button"
                          className="button"
                          onClick={() => openDraft(finding, 'modify')}
                          disabled={actionBusy}
                        >
                          改判
                        </button>
                      </div>
                    )}

                    {isDraft && draft !== null && (
                      <div className="decision-form">
                        <h3>
                          {REVIEW_ACTION_LABEL[draft.action]} · 提交时使用 revision{' '}
                          {current.revision}
                        </h3>
                        {draft.action === 'modify' && (
                          <div className="decision-field">
                            <label htmlFor={`final-verdict-${finding.finding_id}`}>
                              新的最终结论
                            </label>
                            <select
                              id={`final-verdict-${finding.finding_id}`}
                              value={draft.finalVerdict}
                              onChange={(event) =>
                                setDraft({
                                  ...draft,
                                  finalVerdict: event.target.value as ReviewVerdict | '',
                                })
                              }
                              disabled={actionBusy}
                            >
                              <option value="">请选择（必须不同于机器判断）</option>
                              {REVIEW_ACTION_VERDICTS.filter(
                                (verdict) =>
                                  verdict !== finding.effective_machine_verdict,
                              ).map((verdict) => (
                                <option key={verdict} value={verdict}>
                                  {verdictLabel(verdict)}
                                </option>
                              ))}
                            </select>
                          </div>
                        )}
                        <div className="decision-field">
                          <label htmlFor={`reason-${finding.finding_id}`}>
                            理由（必填）
                          </label>
                          <textarea
                            id={`reason-${finding.finding_id}`}
                            value={draft.reason}
                            onChange={(event) =>
                              setDraft({ ...draft, reason: event.target.value })
                            }
                            rows={3}
                            maxLength={2000}
                            disabled={actionBusy}
                          />
                        </div>
                        {validation !== null && (
                          <div className="alert error" role="alert">
                            <span>{validation}</span>
                          </div>
                        )}
                        <div className="actions decision-actions">
                          <button
                            type="button"
                            className="button primary"
                            onClick={() => void submitDraft()}
                            disabled={actionBusy}
                          >
                            {actionBusy ? '提交中…' : '提交结论'}
                          </button>
                          <button
                            type="button"
                            className="button"
                            onClick={() => {
                              setDraft(null)
                              setValidation(null)
                            }}
                            disabled={actionBusy}
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </div>

      <section
        className="panel pending-confirmation"
        aria-labelledby="pending-title"
        data-testid="pending-business-confirmation"
      >
        <h2 id="pending-title">待业务确认（证据不足）</h2>
        <p className="muted">
          证据不足既不是风险命中，也不是合格。完成复核时它们会作为「待业务确认」保留，系统绝不会把它们转成 clear。
        </p>
        {pendingFindings.length === 0 ? (
          <p className="empty-state" data-testid="pending-empty">
            当前没有证据不足的待确认项。
          </p>
        ) : (
          <ul className="pending-list">
            {pendingFindings.map((finding) => (
              <li key={finding.finding_id} data-testid="pending-item">
                <strong>{finding.rule_code ?? finding.rule_version_id}</strong>
                <span>机器判断：证据不足</span>
                <span>
                  {finding.final_verdict === null
                    ? '尚未逐项结论'
                    : `人工结论：${verdictLabel(finding.final_verdict)}`}
                </span>
                <span className="trace">finding_id: {finding.finding_id}</span>
              </li>
            ))}
          </ul>
        )}

        {needsPendingConfirmation && !isCompleted && (
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={pendingConfirmation}
              onChange={(event) => setPendingConfirmation(event.target.checked)}
              disabled={completeBusy || actionBusy}
            />
            确认以上证据不足项保留为「待业务确认」，不视为合格
          </label>
        )}
      </section>

      {!isCompleted && (
        <section className="panel review-complete" aria-labelledby="complete-title">
          <h2 id="complete-title">完成复核</h2>
          <p className="muted">
            {allDecided
              ? '所有适用规则都已有最终结论，可以完成。'
              : `还有 ${undecidedCount} 条适用规则没有结论，完成按钮保持禁用。`}
          </p>
          {!allDecided && (
            <p className="muted" data-testid="undecided-hint">
              待结论 finding：
              {current.missing_decision_finding_ids.map((findingId) => {
                const finding = current.findings.find(
                  (item) => item.finding_id === findingId,
                )
                return finding?.rule_code ?? findingId
              }).join('、')}
            </p>
          )}
          <div className="actions">
            <button
              type="button"
              className="button primary"
              onClick={() => void handleComplete()}
              disabled={completeDisabled}
              data-testid="complete-button"
            >
              {current.status === 'completed'
                ? '已完成'
                : completeBusy
                  ? '完成中…'
                  : '完成复核'}
            </button>
          </div>
        </section>
      )}

      {isCompleted && (
        <div className="alert success completed-banner" role="status" data-testid="completed-banner">
          <strong>复核已完成</strong>
          <span>
            revision {current.revision}；机器判断保持只读，人工决定历史按追加顺序保留。
          </span>
          {current.pending_business_confirmation && (
            <span>仍有证据不足项以「待业务确认」保留，未被转成 clear。</span>
          )}
        </div>
      )}

      <section className="panel review-history" aria-labelledby="history-title">
        <h2 id="history-title">状态历史（追加只读）</h2>
        {current.status_history.length === 0 ? (
          <p className="empty-state">后端没有返回状态历史。</p>
        ) : (
          <ul className="status-history">
            {current.status_history.map((event) => (
              <li key={event.event_id} data-testid="status-history-item">
                <strong>{STATUS_ACTION_LABEL[event.action] ?? event.action}</strong>
                <span>
                  {event.previous_status !== null
                    ? `${event.previous_status} → ${event.status ?? '—'}`
                    : event.status ?? '—'}
                </span>
                {event.revision !== null && <span>revision {event.revision}</span>}
                <span className="trace">{formatReviewTime(event.timestamp)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel review-history" aria-labelledby="decisions-title">
        <h2 id="decisions-title">决定历史（追加只读）</h2>
        {current.decisions.length === 0 ? (
          <p className="empty-state">尚未记录任何逐项结论。</p>
        ) : (
          <ol className="decision-history" aria-label="决定历史">
            {current.decisions.map((decision) => {
              const finding = current.findings.find(
                (item) => item.finding_id === decision.finding_id,
              )
              return (
                <li key={decision.decision_id} data-testid="decision-history-item">
                  <div className="decision-history-head">
                    <strong>revision {decision.accepted_revision}</strong>
                    <span>{REVIEW_ACTION_LABEL[decision.action] ?? decision.action}</span>
                    <span>最终结论：{verdictLabel(decision.final_verdict)}</span>
                    <span className="trace">提交时 revision {decision.expected_revision}</span>
                  </div>
                  <p className="decision-rule">
                    规则：<code>{finding?.rule_code ?? decision.finding_id}</code>
                  </p>
                  <p className="decision-reason">理由：{decision.reason}</p>
                  <span className="trace">
                    {formatReviewTime(decision.created_at)} · actor {decision.actor_id}
                  </span>
                </li>
              )
            })}
          </ol>
        )}
      </section>
    </section>
  )
}
