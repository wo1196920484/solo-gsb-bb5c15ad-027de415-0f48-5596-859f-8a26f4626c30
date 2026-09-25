/**
 * Speaking-time statistics over the current transcript segments.
 *
 * All math is done on integer milliseconds (segments carry fractional
 * seconds); results are converted back to seconds for display.
 *
 * Algorithm (shared with the turn-taking analysis via computeSpeechSlices):
 *   1. Drop segments with an empty/whitespace speaker, non-finite bounds,
 *      or zero/negative length (end <= start).
 *   2. Merge each speaker's own overlapping (or touching) intervals so a
 *      speaker can never be counted twice for the same millisecond.
 *   3. Sweep a line across every merged start/end instant: the elementary
 *      half-open slices [t, nextT) each carry a fixed active-speaker set.
 *      - exactly one active speaker  -> solo time for that speaker
 *      - two or more active speakers -> overlap time counted once for
 *        EVERY active speaker (never as solo); raw-segment overlap cannot
 *        accumulate because per-speaker intervals were merged first
 *      - every known speaker active  -> an all-speakers interval
 *   4. Adjacent slices with the same active-speaker set are joined back
 *      together for display.
 */

const toMs = (sec) => Math.round(Number(sec) * 1000)

/** Merge overlapping or touching [startMs, endMs) intervals of one speaker. */
function mergeRanges(ranges) {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const out = []
  for (const [a, b] of sorted) {
    const last = out[out.length - 1]
    // Half-open intervals that merely touch share no millisecond but are
    // joined anyway: one continuous stretch of the same speaker.
    if (last && a <= last[1]) {
      last[1] = Math.max(last[1], b)
    } else {
      out.push([a, b])
    }
  }
  return out
}

/**
 * Shared sweep: validate segments, merge each speaker's own intervals, then
 * cut the timeline at every merged start/end instant and join adjacent
 * elementary slices whose active-speaker sets are identical.
 *
 * Unlike analyzeSpeakingTime this keeps EVERY joined slice — solo (exactly
 * one speaker), co-speaking (two or more) and silence (none) — so callers
 * such as the turn-taking analysis can reason about the gaps between solos.
 *
 * @param {Array<{start:number,end:number,speaker:string}>} segments
 * @returns {{speakerNames: string[],
 *            slices: Array<{startMs:number,endMs:number,speakers:string[]}>}}
 *   slices are ordered by startMs and tile [firstBoundary, lastBoundary).
 */
export function computeSpeechSlices(segments) {
  // speaker name -> raw [startMs, endMs) intervals (insertion order kept)
  const bySpeaker = new Map()

  for (const s of segments || []) {
    const name = typeof s.speaker === 'string' ? s.speaker.trim() : ''
    if (!name) continue // ignore segments without a speaker
    const startSec = Number(s.start)
    const endSec = Number(s.end)
    if (!Number.isFinite(startSec) || !Number.isFinite(endSec)) continue
    const a = Math.max(0, toMs(startSec))
    const b = Math.max(0, toMs(endSec))
    if (b <= a) continue // ignore zero-length / invalid segments
    if (!bySpeaker.has(name)) bySpeaker.set(name, [])
    bySpeaker.get(name).push([a, b])
  }

  // Merge per speaker so self-overlapping raw segments count only once.
  const merged = new Map()
  for (const [name, ranges] of bySpeaker) {
    merged.set(name, mergeRanges(ranges))
  }
  const speakerNames = [...merged.keys()]
  const order = new Map(speakerNames.map((n, i) => [n, i]))

  // Collect every endpoint, grouped by timestamp: at one instant several
  // speakers may start/end, so events must be applied as a single batch.
  const eventMap = new Map() // t -> { start:Set, end:Set }
  const ensureEvents = (t) => {
    if (!eventMap.has(t)) eventMap.set(t, { start: new Set(), end: new Set() })
    return eventMap.get(t)
  }
  for (const [name, ranges] of merged) {
    for (const [a, b] of ranges) {
      ensureEvents(a).start.add(name)
      ensureEvents(b).end.add(name)
    }
  }

  const times = [...eventMap.keys()].sort((a, b) => a - b)
  const active = new Set()
  const slices = []

  const sameSet = (a, b) =>
    a.length === b.length && a.every((n, idx) => n === b[idx])

  for (let i = 0; i + 1 < times.length; i += 1) {
    const ev = eventMap.get(times[i])
    // An end and a start of the same speaker at the same instant cancel out
    // (adjacent half-open intervals), so remove endings before adding starts.
    ev.end.forEach((n) => active.delete(n))
    ev.start.forEach((n) => active.add(n))

    const t0 = times[i]
    const t1 = times[i + 1]
    if (t1 <= t0) continue

    const names = [...active].sort((a, b) => order.get(a) - order.get(b))
    // Join neighboring slices whose active-speaker sets are identical.
    const last = slices[slices.length - 1]
    if (last && last.endMs === t0 && sameSet(last.speakers, names)) {
      last.endMs = t1
    } else {
      slices.push({ startMs: t0, endMs: t1, speakers: names })
    }
  }

  return { speakerNames, slices }
}

/**
 * @param {Array<{start:number,end:number,speaker:string}>} segments
 * @returns {{
 *   speakers: Array<{name:string,soloMs:number,overlapMs:number,totalMs:number}>,
 *   overlapIntervals: Array<{startMs:number,endMs:number,durationMs:number,
 *                            speakers:string[],all:boolean}>,
 *   allIntervals: Array<{startMs:number,endMs:number,durationMs:number,
 *                        speakers:string[]}>,
 *   totalSoloMs:number, totalOverlapMs:number, totalAllMs:number,
 * }}
 */
export function analyzeSpeakingTime(segments) {
  const { speakerNames, slices } = computeSpeechSlices(segments)

  const soloMs = new Map(speakerNames.map((n) => [n, 0]))
  const overlapMs = new Map(speakerNames.map((n) => [n, 0]))
  const overlapIntervals = [] // {startMs,endMs,speakers}

  for (const slice of slices) {
    const duration = slice.endMs - slice.startMs
    if (slice.speakers.length === 1) {
      const name = slice.speakers[0]
      soloMs.set(name, soloMs.get(name) + duration)
    } else if (slice.speakers.length >= 2) {
      // Each participant speaks through the whole slice: count it once per
      // speaker, never as solo.
      slice.speakers.forEach((n) => overlapMs.set(n, overlapMs.get(n) + duration))
      overlapIntervals.push({
        startMs: slice.startMs,
        endMs: slice.endMs,
        speakers: [...slice.speakers],
      })
    }
  }

  // "All speakers" only makes sense with at least two known speakers.
  const total = speakerNames.length
  overlapIntervals.forEach((iv) => {
    iv.durationMs = iv.endMs - iv.startMs
    iv.all = total >= 2 && iv.speakers.length === total
  })
  const allIntervals = overlapIntervals
    .filter((iv) => iv.all)
    .map((iv) => ({
      startMs: iv.startMs,
      endMs: iv.endMs,
      durationMs: iv.durationMs,
      speakers: [...iv.speakers],
    }))

  const speakers = speakerNames.map((name) => {
    const solo = soloMs.get(name)
    const together = overlapMs.get(name)
    return { name, soloMs: solo, overlapMs: together, totalMs: solo + together }
  })

  return {
    speakers,
    overlapIntervals,
    allIntervals,
    totalSoloMs: speakers.reduce((sum, sp) => sum + sp.soloMs, 0),
    // Summed per participant: an N-speaker slice contributes N times.
    totalOverlapMs: speakers.reduce((sum, sp) => sum + sp.overlapMs, 0),
    totalAllMs: allIntervals.reduce((sum, iv) => sum + iv.durationMs, 0),
  }
}
