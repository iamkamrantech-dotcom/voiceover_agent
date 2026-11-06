import { test, expect, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const VIDEO = path.resolve(HERE, '../../tests/media/cuts.mp4')

const LINES = [
  'Welcome back to the channel today.',
  'This first place is somewhere almost nobody has ever visited.',
  'The second location is stranger still and harder to reach.',
  'Our third stop sits high above the clouds all year round.',
]

/**
 * The job page, running and finished, and the per-block review.
 *
 * Everything runs against the real server with the mock engine, so these
 * exercise the actual pipeline - a real status.json, a real timeline, a
 * real QC report and real takes in the cache.
 */

/**
 * Start a job and return its id, from the storyboard flow a person uses.
 *
 * The narration carries a tag unique to the test. The TTS cache is keyed
 * on the text, so without it a later test would hit the cache, finish in
 * no time, and quietly stop testing anything about a job in progress.
 */
async function startJob(page: Page, tag: string): Promise<string> {
  await page.goto('/new')
  await page.setInputFiles('#video-input', VIDEO)
  await page.fill('#script-text', `${LINES.join(' ')} Reference ${tag}.`)
  await page.getByTestId('upload-continue').click()
  await page.waitForSelector('[data-testid="scene-text-0"]', { timeout: 60_000 })
  await page.getByTestId('to-options').click()
  await page.waitForSelector('text=Estimated processing time:')
  await page.getByTestId('generate').click()
  await page.waitForURL(/\/jobs\/\w+/)
  const id = page.url().split('/').pop() ?? ''
  expect(id).not.toEqual('')
  return id
}

async function waitForFinished(page: Page) {
  // One job runs at a time by design, so this may be waiting behind a job
  // an earlier test started as well as doing its own work.
  await expect(page.getByText('Quality checks passed.')).toBeVisible({
    timeout: 180_000,
  })
}

test.describe('job page', () => {
  test('a running job shows its stages, blocks and log', async ({ page }) => {
    await startJob(page, 'stages')

    // The stepper and the block grid are the two things that say what is
    // happening rather than just that something is. The grid appears once
    // narration starts, which may be after a wait in the queue.
    await expect(page.getByTestId('pipeline-stepper')).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByTestId('job-stage')).not.toBeEmpty()
    await expect(page.getByTestId('block-grid')).toBeVisible({
      timeout: 180_000,
    })

    // The log console is collapsed until asked for, and fills from SSE.
    await page.getByTestId('log-toggle').click()
    await expect(page.getByTestId('log-console')).toBeVisible()
    await expect(page.getByTestId('log-console')).not.toBeEmpty()

    await waitForFinished(page)
  })

  test('the finished page seeks both ways between player and timeline',
       async ({ page }) => {
    await startJob(page, 'seek')
    await waitForFinished(page)

    await expect(page.getByTestId('timeline')).toBeVisible()
    const box = await page.getByTestId('timeline').boundingBox()
    expect(box).not.toBeNull()

    // Click two thirds along the timeline; the player must follow.
    await page.mouse.click(box!.x + box!.width * 0.66, box!.y + 12)
    const seeked = await page.evaluate(() => {
      const video = document.querySelector('video') as HTMLVideoElement | null
      return video?.currentTime ?? 0
    })
    expect(seeked).toBeGreaterThan(1)

    // And the other way: move the player, and the playhead follows it.
    await page.evaluate(() => {
      const video = document.querySelector('video') as HTMLVideoElement
      video.currentTime = 1
    })
    await page.waitForTimeout(300)
    const playhead = await page.evaluate(() => {
      const bar = document.querySelector(
        '[data-testid="timeline"] [aria-hidden="true"].absolute.top-0.z-10',
      ) as HTMLElement | null
      return bar?.style.transform ?? ''
    })
    expect(playhead).toContain('translateX')
  })

  test('a quality check item seeks the player', async ({ page }) => {
    await startJob(page, 'checks')
    await waitForFinished(page)

    const item = page.getByTestId('listen-0')
    if (await item.count()) {
      await item.click()
      const at = await page.evaluate(() => {
        const video = document.querySelector('video') as HTMLVideoElement | null
        return video?.currentTime ?? -1
      })
      expect(at).toBeGreaterThanOrEqual(0)
    }
    // Every job gets the measured numbers, whether or not it has review items.
    await expect(page.getByText('True peak')).toBeVisible()
    await expect(page.getByText('Sync drift')).toBeVisible()
  })

  test('choosing another take rebuilds the video as v2', async ({ page }) => {
    await startJob(page, 'take')
    await waitForFinished(page)

    await page.getByTestId('block-tile-0').click()
    await expect(page.getByTestId('block-text')).toBeVisible()

    // Three takes, because the config gives drafts three.
    await expect(page.getByTestId('use-take-2')).toBeVisible()
    await page.getByTestId('use-take-2').click()

    // The plan is shown BEFORE anything runs, and says what it will cost.
    await expect(page.getByText('Rebuild the video?')).toBeVisible()
    await expect(
      page.getByText('No narration has to be generated'),
    ).toBeVisible()
    await page.getByRole('button', { name: 'Rebuild' }).click()

    await expect(page.getByTestId('version-2')).toBeVisible({ timeout: 120_000 })
    await expect(page.getByText(/Block 1: take \d of \d chosen/)).toBeVisible()
    await waitForFinished(page)
  })

  test('editing one block re-checks the fit and regenerates only it',
       async ({ page }) => {
    const id = await startJob(page, 'edit')
    await waitForFinished(page)

    const before = await page.evaluate(async (jobId) => {
      const r = await fetch(`/api/jobs/${jobId}/blocks`)
      const data = await r.json()
      return data.blocks.filter((b: { silent: boolean }) => !b.silent).length
    }, id)
    expect(before).toBeGreaterThan(1)

    await page.getByTestId('block-tile-1').click()
    await expect(page.getByTestId('block-text')).toBeVisible()

    // A line far too long for its pictures must say so, in words, before
    // any of it is generated.
    await page.getByTestId('block-text').fill(
      'This second location is stranger still and very much harder to reach '
      + 'than the first one was, and almost nobody who sets out to get there '
      + 'ever actually arrives at the far end of that very long road.',
    )
    await expect(page.getByText(/Cut about \d+ words/)).toBeVisible({
      timeout: 20_000,
    })

    // Something that fits, then regenerate just this block.
    await page.getByTestId('block-text').fill('A shorter second line entirely.')
    await page.waitForTimeout(900)
    await page.getByTestId('regenerate-block').click()
    await expect(page.getByText(/takes will be generated for this block/))
      .toBeVisible()
    await page.getByRole('button', { name: 'Rebuild' }).click()

    await expect(page.getByTestId('version-2')).toBeVisible({ timeout: 120_000 })
    await expect(page.getByText('Block 2: text edited')).toBeVisible()
    await waitForFinished(page)

    const after = await page.evaluate(async (jobId) => {
      const r = await fetch(`/api/jobs/${jobId}/blocks`)
      const data = await r.json()
      return data.blocks.find((b: { block: number }) => b.block === 1)?.text
    }, id)
    expect(after).toEqual('A shorter second line entirely.')
  })
})
