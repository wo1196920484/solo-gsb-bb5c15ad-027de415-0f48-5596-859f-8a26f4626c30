import test from 'node:test'
import assert from 'node:assert/strict'

import { analyzeSpeakingTime } from './speakingTime.js'
import { analyzeTurnTaking } from './turnTaking.js'

const seg = (start, end, speaker) => ({ start, end, speaker })

/* ---------- speaking-time regression (shared sweep refactor) ---------- */

test('speaking time: solo / overlap / all-speakers still computed as before', () => {
  const r = analyzeSpeakingTime([
    seg(0, 4, 'A'),
    seg(3, 7, 'B'),
    seg(8, 9, ''), // ignored: empty speaker
    seg(5, 5, 'A'), // ignored: zero length
  ])
  const a = r.speakers.find((s) => s.name === 'A')
  const b = r.speakers.find((s) => s.name === 'B')
  assert.equal(a.soloMs, 3000)
  assert.equal(a.overlapMs, 1000)
  assert.equal(b.soloMs, 3000)
  assert.equal(b.overlapMs, 1000)
  assert.equal(r.totalSoloMs, 6000)
  assert.equal(r.totalOverlapMs, 2000) // counted once per participant
  assert.deepEqual(
    r.overlapIntervals.map((iv) => [iv.startMs, iv.endMs, iv.speakers]),
    [[3000, 4000, ['A', 'B']]])
  assert.equal(r.overlapIntervals[0].all, true)
  assert.equal(r.totalAllMs, 1000)
})

test('speaking time: self-overlapping and touching segments merge first', () => {
  const r = analyzeSpeakingTime([
    seg(0, 2, 'A'),
    seg(1, 3, 'A'), // self overlap -> merged, counted once
    seg(3, 4, 'A'), // touching -> merged as well
    seg(3.5, 5, 'B'),
  ])
  const a = r.speakers.find((s) => s.name === 'A')
  assert.equal(a.soloMs, 3500) // [0,3.5) solo
  assert.equal(a.overlapMs, 500) // [3.5,4) with B
  assert.equal(r.overlapIntervals.length, 1)
})

/* ---------- turn taking ---------- */

test('normal turn: same-speaker touching segments merge, gap under threshold', () => {
  const r = analyzeTurnTaking([
    seg(0, 2, 'A'),
    seg(2, 4, 'A'), // touching -> one solo [0,4)
    seg(4.5, 6, 'B'),
  ], 1000)
  assert.equal(r.totalNormal, 1)
  assert.equal(r.totalInterruption, 0)
  assert.equal(r.pairs.length, 1)
  const p = r.pairs[0]
  assert.equal(p.from, 'A')
  assert.equal(p.to, 'B')
  assert.equal(p.normalCount, 1)
  assert.deepEqual(p.normal[0], { startMs: 4000, endMs: 4500, gapMs: 500 })
})

test('normal turn: touching solos count as a zero-length gap', () => {
  const r = analyzeTurnTaking([seg(0, 2, 'A'), seg(2, 4, 'B')], 1000)
  assert.equal(r.totalNormal, 1)
  assert.equal(r.pairs[0].normal[0].gapMs, 0)
})

test('normal turn: gap equal to threshold counts, longer gap does not', () => {
  const at = analyzeTurnTaking([seg(0, 2, 'A'), seg(3, 4, 'B')], 1000)
  assert.equal(at.totalNormal, 1)
  const over = analyzeTurnTaking([seg(0, 2, 'A'), seg(3.001, 4, 'B')], 1000)
  assert.equal(over.totalNormal, 0)
  assert.equal(over.pairs.length, 0)
})

test('interruption: only two-speaker co-speaking between the solos', () => {
  const r = analyzeTurnTaking([
    seg(0, 4, 'A'),
    seg(3, 7, 'B'),
  ], 1000)
  assert.equal(r.totalNormal, 0)
  assert.equal(r.totalInterruption, 1)
  const p = r.pairs[0]
  assert.equal(p.from, 'A')
  assert.equal(p.to, 'B')
  assert.deepEqual(p.interruption[0], { startMs: 3000, endMs: 4000, gapMs: 1000 })
})

test('third speaker in between: not counted', () => {
  const r = analyzeTurnTaking([
    seg(0, 3, 'A'),
    seg(2, 4, 'C'), // C overlaps both A and B's surroundings
    seg(3, 6, 'B'),
  ], 1000)
  // solo A [0,2) -> solo B [4,6): between are {A,C} and {C,B} -> third person
  assert.equal(r.totalNormal, 0)
  assert.equal(r.totalInterruption, 0)
  assert.equal(r.pairs.length, 0)
})

test('mixed silence + co-speaking transition: not counted', () => {
  const r = analyzeTurnTaking([
    seg(0, 2, 'A'),
    seg(2.5, 3, 'A'), // separate A interval after a silence gap
    seg(2.5, 4, 'B'),
  ], 1000)
  // solo A [0,2) -> solo B [3,4): between are silence [2,2.5) and {A,B}
  // [2.5,3) -> mixed -> not counted
  assert.equal(r.totalNormal, 0)
  assert.equal(r.totalInterruption, 0)
})

test('directed pairs: A->B and B->A are separate, occurrences keep times', () => {
  const r = analyzeTurnTaking([
    seg(0, 1, 'A'),
    seg(1.5, 2, 'B'),
    seg(2.5, 3, 'A'),
    seg(3.2, 4, 'B'),
  ], 1000)
  assert.equal(r.pairs.length, 2)
  const ab = r.pairs.find((p) => p.from === 'A' && p.to === 'B')
  const ba = r.pairs.find((p) => p.from === 'B' && p.to === 'A')
  assert.equal(ab.normalCount, 2)
  assert.deepEqual(
    ab.normal.map((r2) => [r2.startMs, r2.endMs]),
    [[1000, 1500], [3000, 3200]])
  assert.equal(ba.normalCount, 1)
  assert.deepEqual(ba.normal[0], { startMs: 2000, endMs: 2500, gapMs: 500 })
  assert.equal(r.totalNormal, 3)
})

test('pairs are ordered by first occurrence time', () => {
  const r = analyzeTurnTaking([
    seg(0, 1, 'B'),
    seg(1.2, 2, 'C'),
    seg(3, 4, 'A'),
    seg(4.2, 5, 'B'),
  ], 1000)
  assert.deepEqual(
    r.pairs.map((p) => `${p.from}->${p.to}`),
    ['B->C', 'C->A', 'A->B'])
})

test('invalid segments are ignored; single speaker yields nothing', () => {
  const r = analyzeTurnTaking([
    seg(0, 1, 'A'),
    seg(1.2, 2, ''), // no speaker
    seg(2, 2, 'B'), // zero length
    seg('x', 3, 'B'), // non-finite
    seg(2.5, 3, 'A'),
  ], 1000)
  assert.equal(r.pairs.length, 0)
  assert.equal(r.totalNormal, 0)
  assert.equal(r.totalInterruption, 0)
})

test('interruption then normal accumulate on the same directed pair', () => {
  const r = analyzeTurnTaking([
    seg(0, 4, 'A'),
    seg(3, 6, 'B'), // overlap [3,4) -> interruption A->B
    seg(7, 8, 'A'), // gap 1s -> normal B->A
    seg(8.5, 9, 'B'), // gap 0.5s -> normal A->B
  ], 1000)
  const ab = r.pairs.find((p) => p.from === 'A' && p.to === 'B')
  const ba = r.pairs.find((p) => p.from === 'B' && p.to === 'A')
  assert.equal(ab.interruptionCount, 1)
  assert.equal(ab.normalCount, 1)
  assert.equal(ba.normalCount, 1)
  assert.equal(r.totalInterruption, 1)
  assert.equal(r.totalNormal, 2)
})

test('threshold is configurable: larger threshold admits longer silences', () => {
  const segs = [seg(0, 2, 'A'), seg(4, 5, 'B')]
  assert.equal(analyzeTurnTaking(segs, 1000).totalNormal, 0)
  assert.equal(analyzeTurnTaking(segs, 2000).totalNormal, 1)
  assert.equal(analyzeTurnTaking(segs, 0).totalNormal, 0)
})
