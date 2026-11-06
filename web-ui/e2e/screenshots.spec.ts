import { test, type Page } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const OUT = path.resolve(HERE, '../../outputs/screenshots')
const VIDEO = path.resolve(HERE, '../../tests/media/cuts.mp4')

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

async function shoot(page: Page, name: string, theme: string, size: string) {
  fs.mkdirSync(OUT, { recursive: true })
  await page.waitForTimeout(400)
  await page.screenshot({
    path: path.join(OUT, `${name}-${size}-${theme}.png`),
    fullPage: true,
  })
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

        // Overview. Wait for the figures to actually land, not just for
        // their labels, so the shot is never of a skeleton.
        await page.goto('/')
        await page.waitForSelector('text=Videos made')
        await page.waitForFunction(
          () => document.querySelectorAll('.skeleton').length === 0,
          undefined,
          { timeout: 30_000 },
        )
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
        await page.waitForFunction(
          () => document.querySelectorAll('.skeleton').length === 0,
          undefined,
          { timeout: 30_000 },
        )
        await shoot(page, 'jobs', theme, size.name)

        // A finished job, if one exists
        const firstJob = page.locator('[data-testid="jobs-tbody"] tr a').first()
        if (await firstJob.count()) {
          await firstJob.click()
          await page.waitForTimeout(1500)
          await shoot(page, 'job-detail', theme, size.name)
        }

        // Voice
        await page.goto('/voice')
        await page.waitForSelector('text=Active voice')
        await shoot(page, 'voice', theme, size.name)

        // Settings
        await page.goto('/settings')
        await page.waitForSelector('text=Generation')
        await shoot(page, 'settings', theme, size.name)

        await context.close()
      })
    }
  }
})
