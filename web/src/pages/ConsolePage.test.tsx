import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type {
  ConversationPublic,
  KnowledgeVersion,
  SuggestionFeedbackPublic,
  SuggestionPublic,
} from '../api/types'
import { AuthProvider } from '../auth/AuthContext'
import { setToken, TOKEN_STORAGE_KEY } from '../auth/tokenStore'
import { errorPayload, jsonResponse } from '../test/http'
import ConsolePage from './ConsolePage'

const CONVERSATION: ConversationPublic = {
  conversation_id: '11111111-1111-4111-8111-111111111111',
  scope_id: 'demo-bank',
  owner_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  source_type: 'manual_paste',
  source_ref: null,
  content_hash: 'hash-immutable-conversation',
  data_classification: 'synthetic_demo',
  redaction_applied: false,
  created_at: '2026-11-12T09:00:00Z',
  updated_at: '2026-11-12T09:00:00Z',
  turns: [
    {
      turn_id: '21111111-1111-4111-8111-111111111111',
      ordinal: 1,
      speaker: '客服',
      content: '您好，请问有什么可以帮您？',
      original_content_length: 12,
      timestamp: '2026-11-12T09:00:00Z',
    },
    {
      turn_id: '22222222-2222-4222-8222-222222222222',
      ordinal: 2,
      speaker: '客户',
      content: '我的银行卡丢失了，怎么挂失？',
      original_content_length: 13,
      timestamp: '2026-11-12T09:00:05Z',
    },
  ],
  evidence_coordinate_basis: 'original_immutable_content',
}

const ANSWER =
  '建议先通过客服热线或手机银行冻结账户，再携带有效身份证件到网点办理正式挂失。'

const CITATION_ONE = {
  version_id: '31111111-1111-4111-8111-111111111111',
  chunk_id: '41111111-1111-4111-8111-111111111111',
  quote: '挂失后应立即致电客服热线冻结账户。',
}

const CITATION_TWO = {
  version_id: '32222222-2222-4222-8222-222222222222',
  chunk_id: '42222222-2222-4222-8222-222222222222',
  quote: '正式挂失需携带有效身份证件。',
}

const SUGGESTION: SuggestionPublic = {
  suggestion_id: '51111111-1111-4111-8111-111111111111',
  conversation_id: CONVERSATION.conversation_id,
  run_id: '61111111-1111-4111-8111-111111111111',
  query: '我的银行卡丢失了，怎么挂失？',
  rewritten_query: null,
  business_type: 'lost_card',
  status: 'answered',
  answer: ANSWER,
  citations: [CITATION_ONE, CITATION_TWO],
  missing_information: [],
  retrieval_snapshot: {
    index_version: 'idx-20261112T000000Z-abcdef12',
    rejected_citations: [
      {
        chunk_id: '49999999-9999-4999-8999-999999999999',
        version_id: '39999999-9999-4999-8999-999999999999',
        reason: 'not_recalled',
      },
    ],
  },
  mode: 'replay',
  created_at: '2026-11-12T09:01:00Z',
}

const VERSION: KnowledgeVersion = {
  version_id: CITATION_ONE.version_id,
  document_id: '71111111-1111-4111-8111-111111111111',
  version_no: 1,
  content: '演示制度全文：挂失后应立即致电客服热线冻结账户。\n正式挂失需携带有效身份证件。',
  content_hash: 'hash-version-one',
  status: 'published',
  valid_from: '2026-01-01T00:00:00Z',
  valid_to: null,
  author_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  reviewer_id: null,
  review_reason: null,
  retire_reason: null,
  revision: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  document: {
    document_id: '71111111-1111-4111-8111-111111111111',
    title: '演示·挂失处理制度',
    source_type: 'demo_policy',
    source_uri: 'https://example.test/demo/lost-card',
    business_type: 'lost_card',
    scope_id: 'demo-bank',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
}

const FEEDBACK: SuggestionFeedbackPublic = {
  feedback_id: '81111111-1111-4111-8111-111111111111',
  suggestion_id: SUGGESTION.suggestion_id,
  actor_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  action: 'adopt',
  edited_text: null,
  reason: null,
  created_at: '2026-11-12T09:02:00Z',
}

type Responder = () => Response | Promise<Response>

interface StubOptions {
  conversation?: Responder
  suggestion?: Responder
  version?: Responder
  feedback?: Responder
}

function stubApi(options: StubOptions = {}) {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input)
      const method = init?.method ?? 'GET'

      if (url.endsWith('/api/v1/conversations') && method === 'POST') {
        return (
          options.conversation ?? (() => jsonResponse(CONVERSATION, 201))
        )()
      }
      if (url.endsWith('/api/v1/suggestions') && method === 'POST') {
        return (options.suggestion ?? (() => jsonResponse(SUGGESTION, 201)))()
      }
      if (url.includes('/api/v1/knowledge/versions/')) {
        return (options.version ?? (() => jsonResponse(VERSION)))()
      }
      if (url.endsWith('/feedback') && method === 'POST') {
        return (options.feedback ?? (() => jsonResponse(FEEDBACK, 201)))()
      }
      return jsonResponse({}, 404)
    },
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderConsolePage() {
  return render(
    <MemoryRouter initialEntries={['/console']}>
      <AuthProvider>
        <Routes>
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/login" element={<div>login-stub</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

async function importConversation(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('会话原文'), '客服：您好\n客户：我的银行卡丢失了')
  await user.click(screen.getByRole('button', { name: '导入会话' }))
  await screen.findByTestId('conversation-view')
}

async function generateSuggestion(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '生成候选答复' }))
  await screen.findByTestId('suggestion-view')
}

describe('ConsolePage', () => {
  it('导入会话后展示轮次，生成答复时渲染答复、引用与检索快照，并可打开原文', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    const fetchMock = stubApi()
    renderConsolePage()

    await importConversation(user)

    expect(screen.getByText(CONVERSATION.conversation_id)).toBeInTheDocument()
    expect(screen.getByText('#1')).toBeInTheDocument()
    expect(screen.getByText('#2')).toBeInTheDocument()
    expect(screen.getByText('客服')).toBeInTheDocument()
    expect(screen.getByText('客户')).toBeInTheDocument()
    expect(screen.getByText('我的银行卡丢失了，怎么挂失？')).toBeInTheDocument()

    const importCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/api/v1/conversations'),
    )
    expect(importCall).toBeDefined()
    expect(JSON.parse(String(importCall?.[1]?.body))).toMatchObject({
      content: '客服：您好\n客户：我的银行卡丢失了',
      format: 'text',
    })

    await generateSuggestion(user)

    expect(screen.getByTestId('suggestion-status')).toHaveTextContent('已生成答复')
    expect(screen.getByTestId('suggestion-answer')).toHaveTextContent(ANSWER)
    expect(screen.getAllByTestId('citation')).toHaveLength(2)
    expect(screen.getByText('“挂失后应立即致电客服热线冻结账户。”')).toBeInTheDocument()
    expect(screen.getByText('“正式挂失需携带有效身份证件。”')).toBeInTheDocument()
    expect(screen.getByText('idx-20261112T000000Z-abcdef12')).toBeInTheDocument()
    expect(screen.getByTestId('rejected-citations')).toHaveTextContent('1 条')

    const suggestionCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/api/v1/suggestions'),
    )
    expect(JSON.parse(String(suggestionCall?.[1]?.body))).toEqual({
      conversation_id: CONVERSATION.conversation_id,
    })

    await user.click(screen.getAllByRole('button', { name: '打开原文' })[0])

    expect(await screen.findByTestId('citation-content')).toHaveTextContent(
      '演示制度全文：挂失后应立即致电客服热线冻结账户。',
    )
    expect(screen.getByText('演示·挂失处理制度')).toBeInTheDocument()
  })

  it('needs_human 只渲染状态与转人工提示，不渲染答复文本', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    stubApi({
      suggestion: () =>
        jsonResponse(
          {
            ...SUGGESTION,
            status: 'needs_human',
            answer: '',
            citations: [],
            missing_information: ['没有足够证据回答'],
          } satisfies SuggestionPublic,
          201,
        ),
    })
    renderConsolePage()

    await importConversation(user)
    await generateSuggestion(user)

    expect(screen.getByTestId('suggestion-status')).toHaveTextContent('转人工')
    expect(screen.queryByTestId('suggestion-answer')).not.toBeInTheDocument()
    expect(screen.getByTestId('no-answer')).toHaveTextContent(
      '转人工状态下不展示候选答复文本',
    )
    expect(screen.getByText('没有足够证据回答')).toBeInTheDocument()
  })

  it('生成候选答复遇到 5xx 时渲染显式失败状态，不渲染答复', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    stubApi({
      suggestion: () =>
        jsonResponse(
          errorPayload({
            code: 'SUGGESTION_UNAVAILABLE',
            message: '候选答复所需的知识索引或依赖不可用',
            details: {},
          }),
          503,
        ),
    })
    renderConsolePage()

    await importConversation(user)
    await user.click(screen.getByRole('button', { name: '生成候选答复' }))

    expect(
      await screen.findByText('候选答复生成失败：依赖服务不可用'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('候选答复所需的知识索引或依赖不可用'),
    ).toBeInTheDocument()
    expect(screen.getByText('错误码: SUGGESTION_UNAVAILABLE')).toBeInTheDocument()
    expect(screen.queryByTestId('suggestion-answer')).not.toBeInTheDocument()
  })

  it('后端返回 failed 状态时只渲染错误，不渲染答复', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    stubApi({
      suggestion: () =>
        jsonResponse(
          {
            ...SUGGESTION,
            status: 'failed',
            answer: '',
            citations: [],
            retrieval_snapshot: { error: 'model_failure' },
          } satisfies SuggestionPublic,
          201,
        ),
    })
    renderConsolePage()

    await importConversation(user)
    await generateSuggestion(user)

    expect(screen.getByTestId('suggestion-status')).toHaveTextContent('生成失败')
    expect(screen.getByRole('alert')).toHaveTextContent('生成失败')
    expect(screen.getByText('原因: model_failure')).toBeInTheDocument()
    expect(screen.queryByTestId('suggestion-answer')).not.toBeInTheDocument()
  })

  it('采纳时调用反馈接口，只追加反馈史，不改变候选答复状态或文本', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    const fetchMock = stubApi()
    renderConsolePage()

    await importConversation(user)
    await generateSuggestion(user)

    await user.click(screen.getByRole('button', { name: '采纳' }))
    await user.click(screen.getByRole('button', { name: '确认采纳' }))

    expect(await screen.findByTestId('feedback-item')).toHaveTextContent(
      '采纳（原样使用）',
    )

    const feedbackCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith(`/api/v1/suggestions/${SUGGESTION.suggestion_id}/feedback`),
    )
    expect(feedbackCall).toBeDefined()
    expect(feedbackCall?.[1]?.method).toBe('POST')
    expect(JSON.parse(String(feedbackCall?.[1]?.body))).toEqual({ action: 'adopt' })

    expect(screen.getByTestId('suggestion-status')).toHaveTextContent('已生成答复')
    expect(screen.getByTestId('suggestion-answer')).toHaveTextContent(ANSWER)
  })

  it('编辑后采纳提交 edit 动作与编辑文本', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    const fetchMock = stubApi({
      feedback: () =>
        jsonResponse(
          {
            ...FEEDBACK,
            action: 'edit',
            edited_text: '编辑后的答复文本',
          } satisfies SuggestionFeedbackPublic,
          201,
        ),
    })
    renderConsolePage()

    await importConversation(user)
    await generateSuggestion(user)

    await user.click(screen.getByRole('button', { name: '编辑后采纳' }))
    const editor = screen.getByLabelText('修改后的答复')
    await user.clear(editor)
    await user.type(editor, '编辑后的答复文本')
    await user.click(screen.getByRole('button', { name: '提交编辑后采纳' }))

    const item = await screen.findByTestId('feedback-item')
    expect(item).toHaveTextContent('编辑后采纳')
    expect(item).toHaveTextContent('编辑文本：编辑后的答复文本')
    const feedbackCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/feedback'),
    )
    expect(JSON.parse(String(feedbackCall?.[1]?.body))).toEqual({
      action: 'edit',
      edited_text: '编辑后的答复文本',
    })
  })

  it('拒绝时未填写理由则阻止提交，不调用反馈接口', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    const fetchMock = stubApi()
    renderConsolePage()

    await importConversation(user)
    await generateSuggestion(user)

    await user.click(screen.getByRole('button', { name: '拒绝' }))
    await user.click(screen.getByRole('button', { name: '提交拒绝' }))

    expect(
      await screen.findByText('拒绝时必须填写理由'),
    ).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).endsWith('/feedback')),
    ).toBe(false)
    expect(screen.queryByTestId('feedback-item')).not.toBeInTheDocument()
  })

  it('401 时清除令牌并跳转登录页', async () => {
    setToken('jwt-expired')
    const user = userEvent.setup()
    stubApi({
      conversation: () =>
        jsonResponse(
          errorPayload({
            code: 'UNAUTHENTICATED',
            message: '未认证：请先登录',
          }),
          401,
        ),
    })
    renderConsolePage()

    await user.type(screen.getByLabelText('会话原文'), '客服：您好')
    await user.click(screen.getByRole('button', { name: '导入会话' }))

    expect(await screen.findByText('login-stub')).toBeInTheDocument()
    expect(localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('403 时渲染无权限状态，不泄露会话内容', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    stubApi({
      conversation: () =>
        jsonResponse(
          errorPayload({ code: 'FORBIDDEN', message: '无权限访问该资源' }),
          403,
        ),
    })
    renderConsolePage()

    await user.type(screen.getByLabelText('会话原文'), '客服：您好')
    await user.click(screen.getByRole('button', { name: '导入会话' }))

    expect(
      await screen.findByText('无权限：会话导入'),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('conversation-view')).not.toBeInTheDocument()
  })

  it('409 时渲染显式冲突状态', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    stubApi({
      conversation: () =>
        jsonResponse(
          errorPayload({
            code: 'CONFLICT',
            message: '资源已被修改',
          }),
          409,
        ),
    })
    renderConsolePage()

    await user.type(screen.getByLabelText('会话原文'), '客服：您好')
    await user.click(screen.getByRole('button', { name: '导入会话' }))

    expect(
      await screen.findByText('会话导入冲突：资源状态或版本已变化'),
    ).toBeInTheDocument()
    expect(screen.getByText('资源已被修改')).toBeInTheDocument()
  })

  it('加载中禁用重复提交', async () => {
    setToken('jwt-console')
    const user = userEvent.setup()
    let resolveConversation: (response: Response) => void = () => undefined
    stubApi({
      conversation: () =>
        new Promise<Response>((resolve) => {
          resolveConversation = resolve
        }),
    })
    renderConsolePage()

    await user.type(screen.getByLabelText('会话原文'), '客服：您好')
    const importButton = screen.getByRole('button', { name: '导入会话' })
    await user.click(importButton)

    const busyButton = await screen.findByRole('button', { name: '导入中…' })
    expect(busyButton).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('正在导入并解析会话')

    resolveConversation(jsonResponse(CONVERSATION, 201))
    await waitFor(() => {
      expect(screen.getByTestId('conversation-view')).toBeInTheDocument()
    })
  })
})
