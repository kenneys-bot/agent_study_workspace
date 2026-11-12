import { defineConfig, devices } from '@playwright/test'

/**
 * 浏览器端到端配置（T25）。
 *
 * 服务由 `make test-e2e` 启动：一次性 compose 数据库（过渡期做法，见报告）、
 * 隔离后端（MODEL_MODE=replay）与前端 preview。测试不启动自己的服务器，避免
 * 出现两套环境；baseURL 与 API 地址通过环境变量传入。
 */
export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/test-results',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    acceptDownloads: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
