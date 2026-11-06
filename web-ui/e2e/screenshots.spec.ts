import { test, type Page } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const OUT = path.resolve(HERE, '../../outputs/screenshots')
const VIDEO = path.resolve(HERE, '../../tests/media/cuts.mp4')
const RECORDING = path.resolve(HERE, '../../tests/media/music_bed.wav')

const SCRIPT = [
  'Welcome back to the channel.',
  'This first place is somewhere almost nobody has visited.',
  'The second location is stranger still and very hard to reach by any means.',
  'Our third stop sits high above the clouds all year round.',
].join(' ')

const SIZES = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]
const THEMES = ['light', 'dark'] as const

async function setTheme(page: Page, theme: string) {
  await page.addInitScript((value) => {
    try {
      window.localStorage.setItem('va-theme', value)
    } catch {
      /* ignore */
    }
  }, theme)
}

/** Never photograph a skeleton: it looks like a bug in a still image. */
async function settle(page: Page) {
  await page.waitForFunction(
    () => document.querySelectorAll('.skeleton').length === 0,
    undefined,
    { timeout: 60_000 },
  )
}

async function shoot(page: Page, name: string, theme: string, size: string) {
  fs.mkdirSync(OUT, { recursive: true })
  await page.waitForTimeout(400)
  await page.screenshot({
    path: path.join(OUT, `${name}-${size}-${theme}.png`),
    fullPage: true,
  })
}

/**
 * One finished job, made once and reused by every shot, so the job page is
 * photographed with real content rather than whatever happened to be first
 * in the list.
 */
let jobId = ''

async function ensureJob(page: Page) {
  if (jobId) return
  await page.goto('/new')
  await page.setInputFiles('#video-input', VIDEO)
  await page.fill('#script-text', SCRIPT)
  await page.getByTestId('upload-continue').click()
  await page.waitForSelector('[data-testid="scene-text-0"]', { timeout: 60_000 })
  await page.getByTestId('to-options').click()
  await page.waitForSelector('text=Estimated processing time:')
  await page.getByTestId('generate').click()
  await page.waitForURL(/\/jobs\/\w+/)
  jobId = page.url().split('/').pop() ?? ''
  await page.waitForSelector('text=Quality checks passed.', { timeout: 180_000 })
}

async function ensureVoice(page: Page) {
  await page.goto('/voice')
  await page.waitForSelector('text=In use')
  if (await page.getByText('No voices yet').count()) {
    await page.setInputFiles('#voice-file', RECORDING)
    await page.fill('#voice-name', 'Demo client')
    await page.getByTestId('prepare-voice').click()
    await page.waitForSelector('text=Demo client', { timeout: 180_000 })
  }
}

test.describe('screenshots', () => {
  for (const size of SIZES) {
    for (const theme of THEMES) {
      test(`${size.name} ${theme}`, async ({ browser }) => {
        const context = await browser.newContext({
          viewport: { width: size.width, height: size.height },
          deviceScaleFactor: 2,
        })
        const page = await context.newPage()
        await setTheme(page, theme)

        await ensureJob(page)
        await ensureVoice(page)

        // Overview
        await page.goto('/')
        await page.waitForSelector('text=Videos made')
        await settle(page)
        await shoot(page, 'overview', theme, size.name)

        // New video, step 1
        await page.goto('/new')
        await page.waitForSelector('#video-input', { state: 'attached' })
        await shoot(page, 'new-upload', theme, size.name)

        // Storyboard
        await page.setInputFiles('#video-input', VIDEO)
        await page.fill('#script-text', SCRIPT)
        await page.getByTestId('upload-continue').click()
        await page.waitForSelector('[data-testid="scene-text-0"]', {
          timeout: 60_000,
        })
        await page.waitForTimeout(2500) // let the fit bars settle
        await shoot(page, 'storyboard', theme, size.name)

        // Options
        await page.getByTestId('to-options').click()
        await page.waitForSelector('text=Estimated processing time:')
        await shoot(page, 'new-options', theme, size.name)

        // Jobs
        await page.goto('/jobs')
        await settle(page)
        await shoot(page, 'jobs', theme, size.name)

        // The finished job: player, timeline, quality checks, versions.
        await page.goto(`/jobs/${jobId}`)
        await page.waitForSelector('[data-testid="timeline"]', {
          timeout: 60_000,
        })
        await settle(page)
        await page.waitForTimeout(600)
        await shoot(page, 'job-finished', theme, size.name)

        // The block review panel, open on a real block with real takes.
        await page.getByTestId('block-tile-0').click()
        await page.waitForSelector('[data-testid="block-text"]')
        await page.waitForTimeout(900) // the fit bar re-checks on open
        await shoot(page, 'job-block-review', theme, size.name)
        await page.getByRole('button', { name: 'Close' }).click()

        // Voice and Settings while nothing is running, so Settings is shown
        // editable - its normal state - rather than locked behind a job.
        await page.goto('/voice')
        await page.waitForSelector('text=In use')
        await settle(page)
        await shoot(page, 'voice', theme, size.name)

        await page.goto('/settings')
        await page.waitForSelector('text=Voice model')
        await settle(page)
        await shoot(page, 'settings', theme, size.name)

        // A running job, caught in the act. The narration is unique to this
        // shot: identical text would be found in the TTS cache and the job
        // would be over before it could be photographed.
        await page.goto('/new')
        await page.setInputFiles('#video-input', VIDEO)
        await page.fill(
          '#script-text',
          `${SCRIPT} Recorded for the ${size.name} ${theme} pass.`,
        )
        await page.getByTestId('upload-continue').click()
        await page.waitForSelector('[data-testid="scene-text-0"]', {
          timeout: 60_000,
        })
        await page.getByTestId('to-options').click()
        await page.waitForSelector('text=Estimated processing time:')
        await page.getByTestId('generate').click()
        await page.waitForURL(/\/jobs\/\w+/)
        await page.waitForSelector('[data-testid="pipeline-stepper"]', {
          timeout: 60_000,
        })
        await page.getByTestId('log-toggle').click()
        await page.waitForTimeout(700)
        await shoot(page, 'job-running', theme, size.name)

        // Settings locked behind that running job - the other half of the
        // page, with every control disabled and the reason stated.
        await page.goto('/settings')
        await page.waitForSelector('[data-testid="settings-locked"]', {
          timeout: 30_000,
        })
        await shoot(page, 'settings-locked', theme, size.name)

        await context.close()
      })
    }
  }
})
