import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

/**
 * 8–10 分钟演示的浏览器端到端（T25 / M5-04）。
 *
 * 覆盖：登录 → 坐席工作台（导入会话、回放生成候选答复、打开引用原文、采纳）
 * → 质检任务（由 `make seed-demo` 用真实 worker + 显式回放生成）
 * → 复核（确认一条真命中、驳回一条误报、完成）→ 报告导出。
 *
 * 关键边界：
 * - 候选答复使用 `MODEL_MODE=replay`，不会调用真实云模型，也不烧钱；
 * - 复核/导出走真实 HTTP API 与 PostgreSQL；报告绑定 revision，导出后不再变；
 * - 回放不是模型质量证据，报告里必须如实标注。
 */

const E2E_DIR = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(E2E_DIR, '../..')
const SCREENSHOT_DIR = resolve(E2E_DIR, 'screenshots')
const API_BASE_URL = process.env.E2E_API_BASE_URL ?? 'http://127.0.0.1:8000'

const DEMO_AUTHOR = { username: 'demo-author', password: 'demo-only-correct-horse-battery' }
const DEMO_REVIEWER = { username: 'demo-reviewer', password: 'demo-only-correct-horse-battery' }

interface SeedState {
  users: Record<string, string>
  conversations: Record<string, { conversation_id: string; review_id?: string }>
  reviews: Record<string, { review_id: string; inspection_id: string; revision: number }>
}

interface InspectionRecord {
  inspection_id: string
  status: string
  findings: { rule_code?: string | null }[]
}

interface ReviewRecord {
  review_id: string
  inspection_id: string
  revision: number
}

async function apiLogin(
  request: APIRequestContext,
  account: { username: string; password: string },
): Promise<string> {
  const response = await request.post(`${API_BASE_URL}/api/v1/auth/login`, {
    data: account,
  })
  expect(response.status(), '登录 API 必须成功').toBe(200)
  const payload = (await response.json()) as { access_token: string }
  return payload.access_token
}

/**
 * 用真实 API + 真实 worker（显式回放）为一个已存在会话生成质检任务与复核。
 * 这样浏览器 e2e 不是只读取预置复核，而是走完“创建质检 → worker → 创建复核”。
 */
async function createInspectionAndReview(
  request: APIRequestContext,
  state: SeedState,
): Promise<{ review: ReviewRecord; inspection: InspectionRecord }> {
  const token = await apiLogin(request, DEMO_AUTHOR)
  const headers = {
    Authorization: `Bearer ${token}`,
    'Idempotency-Key': `e2e-inspection-${Date.now()}`,
  }
  const conversationId = state.conversations['s3-complaint-risk']?.conversation_id
  expect(conversationId, 'seed 必须包含 S3 风险会话').toBeTruthy()

  const created = await request.post(`${API_BASE_URL}/api/v1/inspections`, {
    headers,
    data: { conversation_id: conversationId, business_type: 'complaint' },
  })
  expect(created.status(), '创建质检应返回 202').toBe(202)
  const accepted = (await created.json()) as { inspection_id: string }

  // 真实 worker 进程：使用与后端相同的 DATABASE_URL / MODEL_MODE=replay。
  execFileSync(
    'uv',
    ['run', '--frozen', 'python', '-m', 'bank_assistant.worker', '--once'],
    { cwd: REPO_ROOT, env: process.env, stdio: 'pipe' },
  )

  let inspection: InspectionRecord | null = null
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const response = await request.get(
      `${API_BASE_URL}/api/v1/inspections/${accepted.inspection_id}`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect(response.status(), '读取质检应成功').toBe(200)
    inspection = (await response.json()) as InspectionRecord
    if (inspection.status === 'succeeded') {
      break
    }
    expect(inspection.status, '质检不应失败').not.toBe('failed')
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 500))
  }
  expect(inspection?.status, 'worker 应把质检执行成功').toBe('succeeded')
  expect(inspection?.findings.length, '投诉会话应有 2 条规则判断').toBe(2)

  const reviewerId = state.users['demo-reviewer']
  expect(reviewerId, 'seed 必须包含 demo-reviewer 用户 ID').toBeTruthy()
  const reviewResponse = await request.post(`${API_BASE_URL}/api/v1/reviews`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      inspection_id: accepted.inspection_id,
      assignee_id: reviewerId,
    },
  })
  expect(reviewResponse.status(), '创建复核应返回 201').toBe(201)
  const review = (await reviewResponse.json()) as ReviewRecord
  return { review, inspection: inspection as InspectionRecord }
}

function loadSeedState(): SeedState {
  const path = process.env.DEMO_SEED_STATE
  if (path === undefined || path === '') {
    throw new Error('缺少 DEMO_SEED_STATE；请通过 make test-e2e 启动隔离环境')
  }
  if (!existsSync(path)) {
    throw new Error(`演示状态清单不存在：${path}；请先运行 make seed-demo`)
  }
  return JSON.parse(readFileSync(path, 'utf-8')) as SeedState
}

function loadConversation(id: string): string {
  const path = resolve(E2E_DIR, '../../data/demo/conversations', `${id}.json`)
  const payload = JSON.parse(readFileSync(path, 'utf-8')) as { content: string }
  return payload.content
}

async function screenshot(page: Page, name: string): Promise<void> {
  mkdirSync(SCREENSHOT_DIR, { recursive: true })
  await page.screenshot({ path: resolve(SCREENSHOT_DIR, name), fullPage: true })
}

async function login(page: Page, account: { username: string; password: string }) {
  await page.goto('/login')
  await page.getByLabel('用户名').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible()
}

test('三个故事可完成：回放候选答复 → 质检复核（确认+驳回误报）→ 报告导出', async ({
  page,
  request,
}) => {
  const state = loadSeedState()
  // 质检链路：真实 API 创建任务 + 真实 worker（replay）+ 真实 API 创建复核。
  const { review } = await createInspectionAndReview(request, state)

  // 1. 登录（演示账号是公开的 demo-only 密码，不是秘密）。
  await login(page, DEMO_AUTHOR)
  await screenshot(page, '01-logged-in.png')

  // 2. 坐席工作台：导入合成会话 → 回放生成候选答复 → 打开引用原文 → 采纳。
  await page.goto('/console')
  await page.getByLabel('会话原文').fill(loadConversation('s3-complaint-risk'))
  await page.getByRole('button', { name: '导入会话' }).click()
  await expect(page.getByTestId('conversation-view')).toBeVisible()

  await page.getByRole('button', { name: '生成候选答复' }).click()
  await expect(page.getByTestId('suggestion-status')).toHaveText('已生成答复')
  const citations = page.getByTestId('citation')
  await expect(citations.first()).toBeVisible()
  await citations.first().getByRole('button', { name: '打开原文' }).click()
  await expect(page.getByTestId('citation-content')).toBeVisible()
  await screenshot(page, '02-console-suggestion.png')

  await page.getByRole('button', { name: '采纳', exact: true }).click()
  await page.getByRole('button', { name: '确认采纳' }).click()
  await expect(page.getByTestId('feedback-item')).toContainText('采纳')
  await page.getByRole('button', { name: '退出登录' }).click()

  // 3. 质检任务已由 seed 的真实 worker（显式回放）生成；复核员登录处理。
  await login(page, DEMO_REVIEWER)
  await page.goto(`/reviews/${review.review_id}`)
  await expect(page.getByTestId('review-status')).toHaveText('待复核')

  const cmpCard = page.locator('li.finding').filter({ hasText: 'DEMO-CMP-01' })
  const attCard = page.locator('li.finding').filter({ hasText: 'DEMO-ATT-01' })
  await expect(cmpCard).toBeVisible()
  await expect(attCard).toBeVisible()

  // 真命中：确认“阻碍投诉”。
  await cmpCard.getByRole('button', { name: '确认', exact: true }).click()
  await cmpCard.getByLabel('理由（必填）').fill('坐席用“不可能处理、投诉也没用”直接阻碍投诉，证据与原文一致。')
  await cmpCard.getByRole('button', { name: '提交结论' }).click()
  await expect(cmpCard).toContainText('动作 确认')

  // 误报：驳回“明显侮辱性表达”。“你别再打了”语气强硬但不是侮辱性表达。
  await attCard.getByRole('button', { name: '驳回', exact: true }).click()
  await attCard.getByLabel('理由（必填）').fill('“你别再打了”是强硬劝阻，不构成明显侮辱性表达，属于误报。')
  await attCard.getByRole('button', { name: '提交结论' }).click()
  await expect(attCard).toContainText('动作 驳回')
  await screenshot(page, '03-review-decisions.png')

  await page.getByTestId('complete-button').click()
  await expect(page.getByTestId('completed-banner')).toContainText('复核已完成')
  await screenshot(page, '04-review-completed.png')

  // 4. 报告导出：绑定当前 revision，下载真实产物并校验内容。
  const revisionText = await page.getByTestId('review-revision').innerText()
  const revision = Number.parseInt(revisionText.replace(/\D+/g, ''), 10)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('export-json').click(),
  ])
  const downloadPath = resolve(SCREENSHOT_DIR, 'report-export.json')
  await download.saveAs(downloadPath)
  const report = JSON.parse(readFileSync(downloadPath, 'utf-8')) as {
    inspection: { inspection_id: string; status: string }
    review: { review_id: string; review_revision: number }
    findings: { rule_code: string; machine_verdict: string; human_final_verdict: string | null }[]
  }
  expect(report.inspection.inspection_id).toBe(review.inspection_id)
  expect(report.inspection.status).toBe('succeeded')
  expect(report.review.review_id).toBe(review.review_id)
  expect(report.review.review_revision).toBe(revision)
  expect(report.findings.find((item) => item.rule_code === 'DEMO-CMP-01')).toMatchObject({
    machine_verdict: 'hit',
    human_final_verdict: 'hit',
  })
  expect(report.findings.find((item) => item.rule_code === 'DEMO-ATT-01')).toMatchObject({
    machine_verdict: 'hit',
    human_final_verdict: 'clear',
  })
  await expect(page.getByTestId('export-result')).toContainText('内容哈希')
  await screenshot(page, '05-report-exported.png')
})
