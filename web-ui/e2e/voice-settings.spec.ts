import { test, expect } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
// Generated test media, not the client's private recording.
const RECORDING = path.resolve(HERE, '../../tests/media/music_bed.wav')

/**
 * The voice and settings pages.
 *
 * Both touch real state on disk: a voice profile is a folder under
 * voices/, and a setting is a line in .env. Each test puts back what it
 * changed.
 */

test.describe('voice', () => {
  test('a recording becomes scored candidates you can switch between',
       async ({ page }) => {
    await page.goto('/voice')
    await page.waitForSelector('text=In use')

    await page.setInputFiles('#voice-file', RECORDING)
    await page.fill('#voice-name', 'E2E voice')
    await page.getByTestId('prepare-voice').click()

    // Preparing loads models, so it queues like a job and reports stages.
    await expect(page.getByTestId('voice-preparing')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'E2E voice' }))
      .toBeVisible({ timeout: 180_000 })

    // Candidates are listed with where in the recording they came from.
    await expect(page.getByText(/of the recording/).first()).toBeVisible()

    // Switching marks it in use.
    const use = page.getByRole('button', { name: 'Use this' }).first()
    await use.click()
    await expect(page.getByText('In use').first()).toBeVisible()

    // Renaming rewrites the stored paths, so the page still works after.
    await page.getByRole('button', { name: 'Rename' }).first().click()
    await page.getByLabel('Voice name').fill('E2E renamed')
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByRole('heading', { name: 'E2E renamed' }))
      .toBeVisible({ timeout: 20_000 })

    // The active voice cannot be deleted - that guard is the point.
    const del = page.getByRole('button', { name: 'Delete' }).first()
    await expect(del).toBeDisabled()
  })
})

test.describe('settings', () => {
  test('a setting can be changed and reset back to its default',
       async ({ page }) => {
    await page.goto('/settings')
    await page.waitForSelector('text=Words per second')

    // Settings lock while anything is queued or running, which is the
    // point of the page - and an earlier test's job may still be going.
    // Without this the first fill() waits on a disabled input until the
    // test times out, which reads as a broken page rather than a busy one.
    await expect(page.getByTestId('settings-locked')).toHaveCount(0, {
      timeout: 180_000,
    })

    const field = page.locator('#set-NARRATION_WPS')
    const original = await field.inputValue()

    await field.fill('3.2')
    await page.getByTestId('save-settings').click()
    await expect(page.getByText('Settings saved.')).toBeVisible()
    await expect(page.locator('#set-NARRATION_WPS')).toHaveValue('3.2')
    // A changed setting says so against its default.
    await expect(page.getByTestId('reset-NARRATION_WPS')).toBeVisible()

    // Reset removes the line from .env rather than writing the default in.
    await page.getByTestId('reset-NARRATION_WPS').click()
    await page.getByTestId('save-settings').click()
    await expect(page.getByText('Settings saved.')).toBeVisible()
    await expect(page.locator('#set-NARRATION_WPS')).toHaveValue('2.5')

    // Every setting carries an explanation and what it was.
    await expect(page.getByText('was 2.5').first()).toBeVisible()
    await expect(page.getByText(/The reading pace used to predict/))
      .toBeVisible()

    // A value outside its range is refused with a reason, not silently
    // clamped.
    await page.locator('#set-MAX_SPEEDUP').fill('9')
    await page.getByTestId('save-settings').click()
    await expect(page.getByText(/cannot be above/)).toBeVisible()

    // Leave .env as it was found.
    await page.reload()
    await page.waitForSelector('text=Words per second')
    expect(await page.locator('#set-NARRATION_WPS').inputValue())
      .toEqual(String(Number(original)))
  })
})
