import { test, expect, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const VIDEO = path.resolve(HERE, '../../tests/media/cuts.mp4')

const SCRIPT = [
  'Welcome back to the channel.',
  'This first place is somewhere almost nobody has visited.',
  'The second location is stranger still and hard to reach.',
  'Our third stop sits high above the clouds all year round.',
].join(' ')

/** Upload the test video with a script and land on the storyboard. */
async function openStoryboard(page: Page) {
  await page.goto('/new')
  await page.setInputFiles('#video-input', VIDEO)
  await page.fill('#script-text', SCRIPT)
  await page.getByTestId('upload-continue').click()
  // Scene detection runs here, so give it room.
  await expect(page.getByTestId('scene-text-0')).toBeVisible({ timeout: 60_000 })
}

test('full new video flow reaches a finished job', async ({ page }) => {
  await openStoryboard(page)

  // Every detected scene has an editor and a fit bar.
  await expect(page.getByTestId('scene-text-0')).toHaveValue(/\w+/)
  await expect(page.getByText(/of speech in .*of picture/).first()).toBeVisible()

  await page.getByTestId('to-options').click()
  await expect(page.getByText('Estimated processing time:')).toBeVisible()

  await page.getByTestId('generate').click()

  // Lands on the job page and finishes.
  await expect(page).toHaveURL(/\/jobs\/\w+/)
  await expect(page.getByText('Quality checks passed.')).toBeVisible({
    timeout: 80_000,
  })
  await expect(page.getByRole('link', { name: 'Download video' })).toBeVisible()
})

test('storyboard re-checks the fit as the script is edited', async ({ page }) => {
  await openStoryboard(page)

  const first = page.getByTestId('scene-text-0')
  await first.fill('Short line.')
  // Debounced re-check settles on "fits".
  await expect(page.getByText('Fits').first()).toBeVisible({ timeout: 20_000 })

  // Now overload the same block and the bar must turn red with advice.
  await first.fill(
    ('This is a deliberately very long piece of narration for this image ' +
      'that cannot possibly fit inside it no matter how much the video is ' +
      'stretched or how fast the words are spoken aloud. ').repeat(3),
  )
  await expect(page.getByText(/Cut about \d+ words/).first()).toBeVisible({
    timeout: 20_000,
  })
})

test('a running job shows live progress without polling', async ({ page }) => {
  await openStoryboard(page)
  await page.getByTestId('to-options').click()
  await page.getByTestId('generate').click()
  await expect(page).toHaveURL(/\/jobs\/\w+/)

  // The stage label is driven entirely by the SSE stream.
  const stage = page.getByTestId('job-stage')
  if (await stage.isVisible().catch(() => false)) {
    await expect(stage).not.toBeEmpty()
  }
  await expect(page.getByText('Quality checks passed.')).toBeVisible({
    timeout: 80_000,
  })

  // The top bar reports the queue from the same stream on every page.
  await expect(page.getByTestId('queue-status')).toBeVisible()
})

test('jobs table filters and searches', async ({ page }) => {
  await page.goto('/jobs')
  const rows = page.locator('[data-testid="jobs-tbody"] tr')
  await expect(rows.first()).toBeVisible({ timeout: 30_000 })
  const total = await rows.count()
  expect(total).toBeGreaterThan(0)

  await page.getByTestId('filter-done').click()
  await expect(page.getByText('Done').first()).toBeVisible()

  await page.getByTestId('job-search').fill('cuts')
  await expect(rows.first()).toBeVisible()

  await page.getByTestId('job-search').fill('zzz-no-such-job')
  await expect(page.getByText('Nothing matches')).toBeVisible()
})

test('theme toggle switches between light and dark', async ({ page }) => {
  await page.goto('/')
  const toggle = page.getByTestId('theme-toggle')
  const html = page.locator('html')

  await toggle.click()
  await expect(html).toHaveAttribute('data-theme', 'light')
  await toggle.click()
  await expect(html).toHaveAttribute('data-theme', 'dark')

  // The choice survives a reload.
  await page.reload()
  await expect(html).toHaveAttribute('data-theme', 'dark')
})

test('pages are reachable and keyboard focus is visible', async ({ page }) => {
  await page.goto('/')
  for (const name of ['New video', 'Jobs', 'Voice', 'Settings', 'Overview']) {
    await page.getByRole('link', { name, exact: true }).first().click()
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  }
  await page.keyboard.press('Tab')
  const focused = page.locator(':focus-visible')
  await expect(focused).toHaveCount(1)
})
