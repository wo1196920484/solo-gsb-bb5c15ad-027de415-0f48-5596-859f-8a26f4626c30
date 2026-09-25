/**
 * Turn-taking ("接话") analysis over the current transcript segments.
 *
 * Builds on the same millisecond half-open sweep as the speaking-time
 * statistics (computeSpeechSlices in speakingTime.js):
 *   1. each speaker's own overlapping or touching segments are merged into
 *      half-open [startMs, endMs) intervals;
 *   2. the timeline is cut at every merged start/end boundary into joined
 *      slices — solo (exactly one active speaker), co-speaking (two or
 *      more) and silence (none).
 *
 * For every pair of temporally adjacent solo intervals with DIFFERENT
 * speakers (from -> to), the slices between them decide the transition:
 *   - only silence, total gap <= gapThresholdMs -> normal turn (普通接话);
 *     directly touching solos count as a zero-length gap;
 *   - only co-speaking slices whose active set is exactly {from, to}
 *     (at least one such slice)             -> interruption (插话);
 *   - anything else — a third speaker joins any co-speaking slice, or
 *     silence is mixed with co-speaking     -> not counted.
 *
 * Results are grouped by the directed speaker pair (from -> to); each
 * occurrence records the transition span [prevSoloEnd, nextSoloStart)
 * so the UI can locate it on the timeline.
 */

import { computeSpeechSlices } from './speakingTime.js'

export const DEFAULT_GAP_THRESHOLD_MS = 1000

/**
 * @param {Array<{start:number,end:number,speaker:string}>} segments
 * @param {number} gapThresholdMs  max silence (ms) still counted as 普通接话
 * @returns {{
 *   thresholdMs: number,
 *   pairs: Array<{
 *     from: string, to: string,
 *     normalCount: number, interruptionCount: number,
 *     normal: Array<{startMs:number,endMs:number,gapMs:number}>,
 *     interruption: Array<{startMs:number,endMs:number,gapMs:number}>,
 *   }>,
 *   totalNormal: number, totalInterruption: number,
 * }}
 */
export function analyzeTurnTaking(segments, gapThresholdMs = DEFAULT_GAP_THRESHOLD_MS) {
  const limitMs = Number.isFinite(Number(gapThresholdMs))
    ? Math.max(0, Math.round(Number(gapThresholdMs)))
    : DEFAULT_GAP_THRESHOLD_MS
  const { slices } = computeSpeechSlices(segments)

  const pairs = new Map() // 'from\nto' -> {from,to,normal:[],interruption:[]}
  const ensurePair = (from, to) => {
    const key = `${from}\n${to}`
    if (!pairs.has(key)) pairs.set(key, { from, to, normal: [], interruption: [] })
    return pairs.get(key)
  }

  let prevSolo = null
  let between = [] // non-solo slices since prevSolo
  for (const slice of slices) {
    if (slice.speakers.length === 1) {
      const from = prevSolo && prevSolo.speakers[0]
      const to = slice.speakers[0]
      if (prevSolo && from !== to) {
        const record = {
          startMs: prevSolo.endMs,
          endMs: slice.startMs,
          gapMs: slice.startMs - prevSolo.endMs,
        }
        const onlySilence = between.every((b) => b.speakers.length === 0)
        const onlyPairOverlap = between.length > 0 && between.every((b) =>
          b.speakers.length === 2 &&
          b.speakers.includes(from) && b.speakers.includes(to))
        if (onlySilence && record.gapMs <= limitMs) {
          ensurePair(from, to).normal.push(record)
        } else if (onlyPairOverlap) {
          ensurePair(from, to).interruption.push(record)
        }
        // A third speaker appeared, or silence mixed with co-speaking:
        // not counted.
      }
      prevSolo = slice
      between = []
    } else if (prevSolo) {
      between.push(slice)
    }
  }

  const pairList = [...pairs.values()].map((p) => ({
    ...p,
    normalCount: p.normal.length,
    interruptionCount: p.interruption.length,
  }))
  // Stable, deterministic order: earliest occurrence first, then names.
  const firstMs = (p) => Math.min(
    ...p.normal.concat(p.interruption).map((r) => r.startMs))
  pairList.sort((a, b) => firstMs(a) - firstMs(b)
    || a.from.localeCompare(b.from)
    || a.to.localeCompare(b.to))

  return {
    thresholdMs: limitMs,
    pairs: pairList,
    totalNormal: pairList.reduce((sum, p) => sum + p.normalCount, 0),
    totalInterruption: pairList.reduce((sum, p) => sum + p.interruptionCount, 0),
  }
}
