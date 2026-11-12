/**
 * 质检复核的展示标签（T24 #25 / M5-02）。
 *
 * 标签只做“翻译”，不改写语义：
 * - ``insufficient_evidence`` 始终显示为「证据不足 / 待业务确认」，绝不显示成
 *   「未命中」或「合格」（GLOSSARY：证据不足、不适用）；
 * - ``not_applicable`` 是「不适用」，不构成待确认项；
 * - 人工动作是「确认/驳回/改判」，与机器判断分开呈现。
 */

import type { ReviewStatus, ReviewVerdict } from '../api/types'

export const REVIEW_STATUS_META: Record<
  string,
  { label: string; tone: string; hint: string }
> = {
  pending: {
    label: '待复核',
    tone: 'pending',
    hint: '尚未记录任何逐项结论。',
  },
  in_review: {
    label: '复核中',
    tone: 'in-review',
    hint: '已开始逐项复核，完成前每条适用规则都需要结论。',
  },
  completed: {
    label: '已完成',
    tone: 'completed',
    hint: '复核已收口；决定历史保持追加只读。',
  },
}

export function reviewStatusMeta(status: string) {
  return (
    REVIEW_STATUS_META[status] ?? {
      label: status,
      tone: 'unknown',
      hint: '后端返回了未识别的复核状态，请人工核实。',
    }
  )
}

export const REVIEW_ACTION_LABEL: Record<string, string> = {
  confirm: '确认',
  reject: '驳回',
  modify: '改判',
}

export const REVIEW_VERDICT_LABEL: Record<string, string> = {
  hit: '命中风险',
  clear: '未命中 / 合格',
  insufficient_evidence: '证据不足（待业务确认）',
  not_applicable: '不适用',
}

export function verdictLabel(verdict: string | null | undefined): string {
  if (verdict === null || verdict === undefined || verdict === '') {
    return '未结论'
  }
  return REVIEW_VERDICT_LABEL[verdict] ?? verdict
}

export const REVIEW_ACTION_VERDICTS: ReviewVerdict[] = [
  'hit',
  'clear',
  'insufficient_evidence',
  'not_applicable',
]

export function formatReviewTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function reviewStatusIs(value: string): value is ReviewStatus {
  return value === 'pending' || value === 'in_review' || value === 'completed'
}
