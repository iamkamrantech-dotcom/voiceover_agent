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
  timeout: 90_000,
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
    },
  },
})
