import { defineConfig, devices } from '@playwright/test'

const PORT = 8099
const BASE_URL = `http://127.0.0.1:${PORT}`

/**
 * End-to-end tests run against the real FastAPI server with the mock
 * voice engine, so a whole job finishes in seconds instead of hours, and
 * into a throwaway output folder so real jobs are never touched.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Generous, because one job runs at a time: a test can legitimately be
  // waiting behind work an earlier one started.
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    // cwd is the project root, so these paths are relative to it.
    command: 'venv\\Scripts\\python.exe server.py --no-browser',
    cwd: '..',
    url: BASE_URL,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      TTS_ENGINE: 'mock',
      OUTPUT_DIR: 'outputs/_e2e',
      WORK_DIR: 'outputs/_e2e/_work',
      TTS_CACHE_DIR: 'outputs/_e2e/_tts_cache',
      LOG_DIR: 'outputs/_e2e/logs',
      API_PORT: String(PORT),
      APP_PASSWORD: '',
      // Three takes per block, so the review panel has something to choose
      // between - a draft would otherwise make exactly one.
      TTS_BEST_OF_N_DRAFT: '3',
      // Voice profiles and .env are real state. The tests change both, so
      // they are pointed somewhere disposable - activating a test voice in
      // the real folder would change what every future job sounds like.
      VOICES_DIR: 'outputs/_e2e/voices',
      ENV_FILE: 'outputs/_e2e/.env',
      // The mock engine finishes in under a second, which makes any
      // assertion about a RUNNING job a race. This slows each take enough
      // to watch it happen, and nothing else uses it.
      MOCK_DELAY: '0.45',
    },
  },
})
