import type { BlockDetail, Job } from '../api/types'
import { percent } from '../lib/format'

/**
 * One tile per block, so a long job shows what it is working on rather
 * than one moving bar.
 *
 * While the job runs the tiles come from its live block/take counters;
 * once it has finished they come from the record each block left behind,
 * and clicking one opens the review panel.
 */

type Tile = {
  index: number
  label: string
  state: 'waiting' | 'running' | 'done' | 'weak' | 'silent'
  similarity: number | null
  reliable: boolean
}

function toneFor(state: Tile['state']): { bg: string; fg: string } {
  switch (state) {
    case 'running':
      return { bg: 'var(--accent-wash)', fg: 'var(--accent)' }
    case 'done':
      return { bg: 'var(--success-wash)', fg: 'var(--success)' }
    case 'weak':
      return { bg: 'var(--warning-wash)', fg: 'var(--warning)' }
    case 'silent':
      return { bg: 'transparent', fg: 'var(--faint)' }
    default:
      return { bg: 'var(--raised)', fg: 'var(--muted)' }
  }
}

export function liveTiles(job: Job): Tile[] {
  const total = job.blocks || 0
  const current = job.block || 0
  return Array.from({ length: total }, (_, i) => {
    const index = i + 1
    const running = index === current && job.state === 'running'
    return {
      index: i,
      label: running && job.takes
        ? `Take ${job.take} of ${job.takes}`
        : index < current
          ? 'Done'
          : 'Waiting',
      state: running ? 'running' : index < current ? 'done' : 'waiting',
      similarity: null,
      reliable: true,
    }
  })
}

export function blockTiles(blocks: BlockDetail[], minimum: number): Tile[] {
  return blocks.map((block) => {
    if (block.silent) {
      return {
        index: block.block, label: 'Silent', state: 'silent' as const,
        similarity: null, reliable: true,
      }
    }
    const weak =
      !block.words_ok ||
      (block.similarity_reliable &&
        block.similarity !== null &&
        block.similarity < minimum)
    return {
      index: block.block,
      label: block.words_ok ? `Take ${block.take_number}` : 'Check the words',
      state: weak ? ('weak' as const) : ('done' as const),
      similarity: block.similarity,
      reliable: block.similarity_reliable,
    }
  })
}

export function BlockGrid({
  tiles,
  onOpen,
  selected,
}: {
  tiles: Tile[]
  onOpen?: (index: number) => void
  selected?: number | null
}) {
  if (!tiles.length) return null
  return (
    <ul
      className="grid gap-2 p-4"
      style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(104px, 1fr))' }}
      data-testid="block-grid"
    >
      {tiles.map((tile) => {
        const tone = toneFor(tile.state)
        const isSelected = selected === tile.index
        const inner = (
          <>
            <span className="flex items-baseline justify-between gap-1">
              <span className="text-xs font-medium">Block {tile.index + 1}</span>
              {tile.similarity !== null && (
                <span
                  className="tnum text-2xs"
                  title={
                    tile.reliable
                      ? 'Similarity to the client recording'
                      : 'This block is too short for the score to mean much'
                  }
                  style={{ color: tile.reliable ? tone.fg : 'var(--faint)' }}
                >
                  {percent(tile.similarity)}
                  {tile.reliable ? '' : '*'}
                </span>
              )}
            </span>
            <span className="truncate text-2xs" style={{ color: tone.fg }}>
              {tile.label}
            </span>
          </>
        )
        const className =
          'flex w-full flex-col gap-1 rounded-md border px-2.5 py-2 text-left ' +
          (isSelected ? 'border-accent' : 'border-border')

        return (
          <li key={tile.index}>
            {onOpen ? (
              <button
                type="button"
                className={`${className} hover:border-border-strong`}
                style={{ background: tone.bg }}
                onClick={() => onOpen(tile.index)}
                data-testid={`block-tile-${tile.index}`}
              >
                {inner}
              </button>
            ) : (
              <div className={className} style={{ background: tone.bg }}>
                {inner}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
