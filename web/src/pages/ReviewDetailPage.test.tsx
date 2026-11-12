import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type {
  ConversationPublic,
  InspectionDetail,
  ReportExportDetail,
  ReportExportSummary,
  ReviewDecision,
  ReviewDetail,
} from '../api/types'
import { AuthProvider } from '../auth/AuthContext'
import { setToken } from '../auth/tokenStore'
import { errorPayload, jsonResponse } from '../test/http'
import ReviewDetailPage from './ReviewDetailPage'

const REVIEW_ID = 'reviews-1111-4111-8111-111111111111'
const INSPECTION_ID = 'inspections-1111-4111-8111-111111111111'
const CONVERSATION_ID = 'conversations-1111-4111-8111-111111111111'
const HIT_FINDING_ID = 'finding-hit'
const PENDING_FINDING_ID = 'finding-pending'
const NOT_APPLICABLE_FINDING_ID = 'finding-na'
const TURN_ONE_ID = 'turn-one'
const TURN_TWO_ID = 'turn-two'

const CONVERSATION: ConversationPublic = {
  conversation_id: CONVERSATION_ID,
  scope_id: 'demo-bank',
  owner_id: 'agent-owner',
  source_type: 'manual_paste',
  source_ref: null,
  content_hash: 'hash-immutable',
  data_classification: 'synthetic_demo',
  redaction_applied: false,
  created_at: '2026-11-12T09:00:00Z',
  updated_at: '2026-11-12T09:00:00Z',
  turns: [
    {
      turn_id: TURN_ONE_ID,
      ordinal: 1,
      speaker: '客服',
      // 码点：0客 1服 2： 3年 4费 5😀 6已 7免 8\n 9您 10放 11心
      content: '客服：年费😀已免\n您放心',
      original_content_length: 12,
      timestamp: '2026-11-12T09:00:00Z',
    },
    {
      turn_id: TURN_TWO_ID,
      ordinal: 2,
      speaker: '客户',
      // 码点：0第 1一 2行 3\n 4第 5二 6行
      content: '第一行\n第二行',
      original_content_length: 7,
      timestamp: '2026-11-12T09:00:05Z',
    },
  ],
  evidence_coordinate_basis: 'original_immutable_content',
}

const INSPECTION: InspectionDetail = {
  inspection_id: INSPECTION_ID,
  conversation_id: CONVERSATION_ID,
  conversation_hash: 'hash-immutable',
  status: 'succeeded',
  error_code: null,
  diagnostics: {},
  rule_count: 3,
  task_id: null,
  parent_run_id: null,
  run_id: null,
  started_at: '2026-11-12T09:01:00Z',
  finished_at: '2026-11-12T09:02:00Z',
  created_at: '2026-11-12T09:01:00Z',
  updated_at: '2026-11-12T09:02:00Z',
  findings: [],
}

const HIT_FINDING = {
  finding_id: HIT_FINDING_ID,
  rule_version_id: 'rule-version-1',
  rule_id: null,
  rule_code: 'DEMO-FEE-01',
  version_no: 1,
  severity: 'high',
  machine_verdict: 'hit',
  effective_machine_verdict: 'hit',
  evidence_valid: true,
  rationale: '在当前演示制度和会话条件下，该承诺缺少依据',
  evidence: [
    {
      turn_id: TURN_ONE_ID,
      turn_ordinal: 1,
      start: 5,
      end: 8,
      quote: '😀已免',
      validated: true,
    },
    {
      turn_id: TURN_TWO_ID,
      turn_ordinal: 2,
      start: 2,
      end: 5,
      quote: '行\n第',
      validated: true,
    },
  ],
  knowledge_refs: [],
  applicable: true,
  final_verdict: null,
  latest_action: null,
  latest_decision_id: null,
  decision_count: 0,
}

const PENDING_FINDING = {
  finding_id: PENDING_FINDING_ID,
  rule_version_id: 'rule-version-2',
  rule_id: null,
  rule_code: 'DEMO-FEE-02',
  version_no: 1,
  severity: 'medium',
  machine_verdict: 'insufficient_evidence',
  effective_machine_verdict: 'insufficient_evidence',
  evidence_valid: true,
  rationale: '现有会话与制度不足以判断年费减免条件',
  evidence: [],
  knowledge_refs: [],
  applicable: true,
  final_verdict: null,
  latest_action: null,
  latest_decision_id: null,
  decision_count: 0,
}

const NOT_APPLICABLE_FINDING = {
  finding_id: NOT_APPLICABLE_FINDING_ID,
  rule_version_id: 'rule-version-3',
  rule_id: null,
  rule_code: 'DEMO-COMPLAINT-01',
  version_no: 1,
  severity: 'low',
  machine_verdict: 'not_applicable',
  effective_machine_verdict: 'not_applicable',
  evidence_valid: true,
  rationale: '本会话不涉及投诉受理',
  evidence: [],
  knowledge_refs: [],
  applicable: false,
  final_verdict: null,
  latest_action: null,
  latest_decision_id: null,
  decision_count: 0,
}

const REVIEW: ReviewDetail = {
  review_id: REVIEW_ID,
  inspection_id: INSPECTION_ID,
  inspection_status: 'succeeded',
  assignee_id: 'reviewer-0001',
  status: 'pending',
  revision: 3,
  last_reopen_reason: null,
  created_at: '2026-11-12T09:03:00Z',
  updated_at: '2026-11-12T09:03:00Z',
  applicable_finding_count: 2,
  decided_finding_count: 0,
  missing_decision_finding_ids: [HIT_FINDING_ID, PENDING_FINDING_ID],
  pending_business_confirmation: false,
  pending_confirmation_finding_ids: [],
  findings: [HIT_FINDING, PENDING_FINDING, NOT_APPLICABLE_FINDING],
  decisions: [],
  status_history: [
    {
      event_id: 'event-1',
      action: 'review.created',
      previous_status: null,
      status: 'pending',
      revision: 1,
      actor_id: 'reviewer-0001',
      timestamp: '2026-11-12T09:03:00Z',
    },
  ],
}

const CONFIRM_DECISION: ReviewDecision = {
  decision_id: 'decision-1',
  review_id: REVIEW_ID,
  finding_id: HIT_FINDING_ID,
  action: 'confirm',
  final_verdict: 'hit',
  reason: '证据与原文一致',
  actor_id: 'reviewer-0001',
  expected_revision: 3,
  accepted_revision: 4,
  created_at: '2026-11-12T09:04:00Z',
}

const ALL_DECIDED: ReviewDetail = {
  ...REVIEW,
  status: 'in_review',
  revision: 6,
  decided_finding_count: 2,
  missing_decision_finding_ids: [],
  pending_business_confirmation: true,
  pending_confirmation_finding_ids: [PENDING_FINDING_ID],
  findings: [
    { ...HIT_FINDING, final_verdict: 'hit', latest_action: 'confirm', decision_count: 1 },
    {
      ...PENDING_FINDING,
      final_verdict: 'insufficient_evidence',
      latest_action: 'confirm',
      decision_count: 1,
    },
    NOT_APPLICABLE_FINDING,
  ],
  decisions: [
    CONFIRM_DECISION,
    {
      ...CONFIRM_DECISION,
      decision_id: 'decision-2',
      finding_id: PENDING_FINDING_ID,
      final_verdict: 'insufficient_evidence',
      accepted_revision: 5,
      expected_revision: 4,
    },
  ],
}

const COMPLETED: ReviewDetail = {
  ...ALL_DECIDED,
  status: 'completed',
  revision: 7,
  status_history: [
    ...ALL_DECIDED.status_history,
    {
      event_id: 'event-2',
      action: 'review.completed',
      previous_status: 'in_review',
      status: 'completed',
      revision: 7,
      actor_id: 'reviewer-0001',
      timestamp: '2026-11-12T09:05:00Z',
    },
  ],
}

const REPORT_SUMMARY: ReportExportSummary = {
  export_id: 'report-export-1',
  inspection_id: INSPECTION_ID,
  review_revision: 3,
  format: 'json',
  content_hash: 'sha256-report-content',
  artifact_ref: 'reports/report-export-1.json',
  generated_by: 'reviewer-0001',
  created_at: '2026-11-12T09:06:00Z',
  content_url: '/api/v1/reports/report-export-1',
}

const REPORT_DETAIL: ReportExportDetail = {
  ...REPORT_SUMMARY,
  content: { review_revision: 3, findings: [] },
}

interface StubOptions {
  review?: (call: number) => Response | Promise<Response>
  decision?: () => Response | Promise<Response>
  complete?: () => Response | Promise<Response>
  reportCreate?: () => Response | Promise<Response>
  reportRead?: () => Response | Promise<Response>
}

function stubDetailApi(options: StubOptions = {}) {
  let reviewCalls = 0
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/decisions') && method === 'POST') {
        return (options.decision ??
          (() => jsonResponse({ ...CONFIRM_DECISION, review_id: REVIEW_ID }, 201)))()
      }
      if (url.endsWith('/complete') && method === 'POST') {
        return (options.complete ?? (() => jsonResponse(COMPLETED)))()
      }
      if (url.endsWith('/api/v1/reports') && method === 'POST') {
        return (options.reportCreate ?? (() => jsonResponse(REPORT_SUMMARY, 201)))()
      }
      if (url.includes('/api/v1/reports/') && method === 'GET') {
        return (options.reportRead ?? (() => jsonResponse(REPORT_DETAIL)))()
      }
      if (url.includes('/api/v1/reviews/') && method === 'GET') {
        reviewCalls += 1
        return (options.review ?? (() => jsonResponse(REVIEW)))(reviewCalls)
      }
      if (url.includes('/api/v1/inspections/')) {
        return jsonResponse(INSPECTION)
      }
      if (url.includes('/api/v1/conversations/')) {
        return jsonResponse(CONVERSATION)
      }
      return jsonResponse({}, 404)
    },
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderDetailPage() {
  return render(
    <MemoryRouter initialEntries={[`/reviews/${REVIEW_ID}`]}>
      <AuthProvider>
        <Routes>
          <Route path="/reviews/:reviewId" element={<ReviewDetailPage />} />
          <Route path="/reviews" element={<div>list-stub</div>} />
          <Route path="/login" element={<div>login-stub</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

function findingCard(findingId: string) {
  return screen.getByTestId(`finding-${findingId}`)
}

async function openActionForm(
  findingId: string,
  actionLabel: '确认' | '驳回' | '改判',
  user: ReturnType<typeof userEvent.setup>,
) {
  const card = findingCard(findingId)
  await within(card).findByRole('button', { name: actionLabel })
  await user.click(within(card).getByRole('button', { name: actionLabel }))
  return screen.getByLabelText('理由（必填）')
}

interface ActionCase {
  name: string
  actionLabel: '确认' | '驳回' | '改判'
  finalVerdict?: string
  expected: Record<string, unknown>
}

const ACTION_CASES: ActionCase[] = [
  {
    name: 'confirm',
    actionLabel: '确认',
    expected: {
      finding_id: HIT_FINDING_ID,
      action: 'confirm',
      reason: '证据与原文一致',
      expected_revision: 3,
    },
  },
  {
    name: 'reject',
    actionLabel: '驳回',
    expected: {
      finding_id: HIT_FINDING_ID,
      action: 'reject',
      reason: '规则不适用于本业务',
      expected_revision: 3,
    },
  },
  {
    name: 'modify',
    actionLabel: '改判',
    finalVerdict: 'clear',
    expected: {
      finding_id: HIT_FINDING_ID,
      action: 'modify',
      final_verdict: 'clear',
      reason: '结合豁免条件应改判',
      expected_revision: 3,
    },
  },
]

describe('ReviewDetailPage', () => {
  it('并排渲染原文与证据，emoji/换行高亮一致，证据不足单独列为待业务确认', async () => {
    setToken('jwt-review')
    stubDetailApi()
    renderDetailPage()

    expect(await screen.findByTestId('review-status')).toHaveTextContent('待复核')
    expect(screen.getByTestId('review-revision')).toHaveTextContent('revision 3')

    const turnOne = screen.getByTestId('source-turn-1')
    expect(turnOne).toBeInTheDocument()
    expect(turnOne.textContent).toContain('客服：年费😀已免')
    const turnTwo = screen.getByTestId('source-turn-2')
    expect(turnTwo).toBeInTheDocument()
    expect(turnTwo.textContent).toContain('第一行')
    expect(turnTwo.textContent).toContain('第二行')

    const marks = screen.getAllByTestId('evidence-mark')
    expect(marks.map((mark) => mark.textContent)).toEqual(['😀已免', '行\n第'])
    // 拼接高亮片段后仍与原文逐字一致。
    expect(screen.getByTestId('turn-content-1').textContent).toBe(
      '客服：年费😀已免\n您放心',
    )
    expect(screen.getByTestId('turn-content-2').textContent).toBe('第一行\n第二行')

    const hitCard = findingCard(HIT_FINDING_ID)
    expect(within(hitCard).getByText('DEMO-FEE-01')).toBeInTheDocument()
    expect(within(hitCard).getByText('严重度 high')).toBeInTheDocument()
    expect(screen.getByTestId(`machine-verdict-${HIT_FINDING_ID}`)).toHaveTextContent(
      '命中风险',
    )
    expect(
      within(hitCard).getByText('“😀已免”'),
    ).toBeInTheDocument()
    expect(
      within(hitCard).getByText(/第 1 轮/),
    ).toBeInTheDocument()
    expect(
      within(hitCard).getByText(/第 2 轮/),
    ).toBeInTheDocument()

    expect(
      screen.getByTestId(`machine-verdict-${PENDING_FINDING_ID}`),
    ).toHaveTextContent('证据不足（待业务确认）')
    expect(screen.getByTestId('pending-business-confirmation')).toHaveTextContent(
      '证据不足既不是风险命中，也不是合格',
    )
    expect(screen.getAllByTestId('pending-item')).toHaveLength(1)
    expect(screen.getByTestId('pending-item')).toHaveTextContent('DEMO-FEE-02')

    expect(screen.getByTestId('decision-progress')).toHaveTextContent(
      '已结论 0 / 适用规则 2',
    )
    expect(screen.getByTestId('complete-button')).toBeDisabled()
    expect(screen.getByTestId('undecided-hint')).toHaveTextContent('DEMO-FEE-01')
    expect(screen.getByTestId('status-history-item')).toHaveTextContent('创建复核')
  })

  it.each(ACTION_CASES)(
    '提交 $name 结论时携带 expected_revision 与对应动作字段',
    async ({ actionLabel, finalVerdict, expected }) => {
      setToken('jwt-review')
      const user = userEvent.setup()
      const fetchMock = stubDetailApi()
      renderDetailPage()

      await screen.findByTestId('review-status')
      const reason = await openActionForm(HIT_FINDING_ID, actionLabel, user)
      await user.type(reason, String(expected.reason))
      if (finalVerdict !== undefined) {
        await user.selectOptions(screen.getByLabelText('新的最终结论'), finalVerdict)
      }
      await user.click(screen.getByRole('button', { name: '提交结论' }))

      await waitFor(() => {
        expect(
          fetchMock.mock.calls.some(
            ([input, init]) =>
              String(input).endsWith(`/api/v1/reviews/${REVIEW_ID}/decisions`) &&
              init?.method === 'POST',
          ),
        ).toBe(true)
      })

      const decisionCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith(`/api/v1/reviews/${REVIEW_ID}/decisions`) &&
          init?.method === 'POST',
      )
      expect(JSON.parse(String(decisionCall?.[1]?.body))).toEqual(expected)
    },
  )

  it('409 冲突时明确提示当前 revision，并刷新到最新版本', async () => {
    setToken('jwt-review')
    const user = userEvent.setup()
    const fetchMock = stubDetailApi({
      review: (call) =>
        jsonResponse(
          call === 1 ? REVIEW : { ...REVIEW, revision: 4, status: 'in_review' },
        ),
      decision: () =>
        jsonResponse(
          errorPayload({
            code: 'REVISION_CONFLICT',
            message: 'revision 冲突：期望 3，当前 4',
            details: { current_revision: 4 },
          }),
          409,
        ),
    })
    renderDetailPage()

    await screen.findByTestId('review-status')
    const reason = await openActionForm(HIT_FINDING_ID, '确认', user)
    await user.type(reason, '证据与原文一致')
    await user.click(screen.getByRole('button', { name: '提交结论' }))

    expect(await screen.findByTestId('revision-conflict')).toHaveTextContent(
      '提交冲突：复核已被其他人更新',
    )
    expect(screen.getByTestId('revision-conflict')).toHaveTextContent(
      '当前 revision: 4',
    )
    await waitFor(() => {
      expect(screen.getByTestId('review-revision')).toHaveTextContent('revision 4')
    })
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).includes('/api/v1/reviews/') &&
          (init?.method ?? 'GET') === 'GET',
      ).length,
    ).toBeGreaterThanOrEqual(2)
  })

  it('所有适用规则有结论后仍需显式确认待业务确认项，完成请求携带 expected_revision', async () => {
    setToken('jwt-review')
    const user = userEvent.setup()
    const fetchMock = stubDetailApi({
      review: () => jsonResponse(ALL_DECIDED),
    })
    renderDetailPage()

    await screen.findByTestId('review-status')
    expect(screen.getByTestId('decision-progress')).toHaveTextContent(
      '已结论 2 / 适用规则 2',
    )
    expect(screen.getByTestId('pending-item')).toHaveTextContent(
      '人工结论：证据不足（待业务确认）',
    )
    const completeButton = screen.getByTestId('complete-button')
    expect(completeButton).toBeDisabled()

    await user.click(
      screen.getByRole('checkbox', {
        name: /确认以上证据不足项保留为「待业务确认」/,
      }),
    )
    expect(completeButton).toBeEnabled()
    await user.click(completeButton)

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith(`/api/v1/reviews/${REVIEW_ID}/complete`) &&
            init?.method === 'POST',
        ),
      ).toBe(true)
    })
    const completeCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith(`/api/v1/reviews/${REVIEW_ID}/complete`) &&
        init?.method === 'POST',
    )
    expect(JSON.parse(String(completeCall?.[1]?.body))).toEqual({
      expected_revision: 6,
      pending_business_confirmation: true,
    })
    expect(await screen.findByTestId('completed-banner')).toHaveTextContent(
      '复核已完成',
    )
    expect(screen.getByTestId('completed-banner')).toHaveTextContent(
      '仍有证据不足项以「待业务确认」保留',
    )
  })

  it('未填写理由时阻止提交，不调用决定接口', async () => {
    setToken('jwt-review')
    const user = userEvent.setup()
    const fetchMock = stubDetailApi()
    renderDetailPage()

    await screen.findByTestId('review-status')
    await openActionForm(HIT_FINDING_ID, '确认', user)
    await user.click(screen.getByRole('button', { name: '提交结论' }))

    expect(
      await screen.findByText('理由必填：确认、驳回或改判都必须说明依据。'),
    ).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith('/decisions') && init?.method === 'POST',
      ),
    ).toBe(false)
  })

  it('提交中禁用重复提交', async () => {
    setToken('jwt-review')
    const user = userEvent.setup()
    let resolveDecision: (response: Response) => void = () => undefined
    stubDetailApi({
      decision: () =>
        new Promise<Response>((resolve) => {
          resolveDecision = resolve
        }),
    })
    renderDetailPage()

    await screen.findByTestId('review-status')
    const reason = await openActionForm(HIT_FINDING_ID, '确认', user)
    await user.type(reason, '证据与原文一致')
    await user.click(screen.getByRole('button', { name: '提交结论' }))

    const busyButton = await screen.findByRole('button', { name: '提交中…' })
    expect(busyButton).toBeDisabled()
    expect(
      within(findingCard(HIT_FINDING_ID)).getByRole('button', { name: '确认' }),
    ).toBeDisabled()

    resolveDecision(jsonResponse({ ...CONFIRM_DECISION }, 201))
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: '提交中…' }),
      ).not.toBeInTheDocument()
    })
  })

  it('导出报告固定 inspection_id 与当前 revision，并展示内容哈希', async () => {
    setToken('jwt-review')
    const user = userEvent.setup()
    const fetchMock = stubDetailApi()
    const createObjectURL = vi.fn(() => 'blob:report')
    Object.defineProperty(URL, 'createObjectURL', {
      value: createObjectURL,
      configurable: true,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(),
      configurable: true,
    })
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined)
    renderDetailPage()

    await screen.findByTestId('review-status')
    await user.click(screen.getByTestId('export-json'))

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith('/api/v1/reports') && init?.method === 'POST',
        ),
      ).toBe(true)
    })
    const createCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith('/api/v1/reports') && init?.method === 'POST',
    )
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      inspection_id: INSPECTION_ID,
      format: 'json',
      review_revision: 3,
    })
    expect(await screen.findByTestId('export-result')).toHaveTextContent(
      'sha256-report-content',
    )
    expect(screen.getByTestId('export-result')).toHaveTextContent('revision 3')
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(anchorClick).toHaveBeenCalledTimes(1)
    anchorClick.mockRestore()
  })

  it.each([
    { status: 403, heading: '无权限：复核详情读取' },
    { status: 404, heading: '复核详情读取失败：资源不存在或不可见' },
    { status: 503, heading: '复核详情读取失败：依赖服务不可用' },
  ])('$status 时显示显式错误状态', async ({ status, heading }) => {
    setToken('jwt-review')
    stubDetailApi({
      review: () =>
        jsonResponse(
          errorPayload({ code: `HTTP_${status}`, message: '请求失败' }),
          status,
        ),
    })
    renderDetailPage()

    expect(await screen.findByText(heading)).toBeInTheDocument()
    expect(screen.queryByTestId('review-status')).not.toBeInTheDocument()
  })
})
