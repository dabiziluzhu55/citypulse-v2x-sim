export interface PlaybackRateSample {
  sessionId: string
  wallTimeMs: number
  elapsedSeconds: number
}

export function appendPlaybackRateSample(
  samples: PlaybackRateSample[],
  sample: PlaybackRateSample,
  windowMs = 5_000,
): PlaybackRateSample[] {
  const previous = samples.at(-1)
  if (
    !previous
    || previous.sessionId !== sample.sessionId
    || sample.elapsedSeconds < previous.elapsedSeconds
    || sample.wallTimeMs <= previous.wallTimeMs
  ) return [sample]
  return [...samples, sample]
    .filter((entry) => sample.wallTimeMs - entry.wallTimeMs <= windowMs)
}

export function calculatePlaybackRate(
  samples: PlaybackRateSample[],
  minimumWindowMs = 500,
): number | null {
  const first = samples[0]
  const last = samples.at(-1)
  if (!first || !last || first.sessionId !== last.sessionId) return null
  const wallSeconds = (last.wallTimeMs - first.wallTimeMs) / 1_000
  if (wallSeconds < minimumWindowMs / 1_000) return null
  const elapsed = last.elapsedSeconds - first.elapsedSeconds
  if (elapsed < 0) return null
  return Math.round(elapsed / wallSeconds * 100) / 100
}
