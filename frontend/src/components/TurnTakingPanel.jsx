import { useMemo, useState } from 'react'
import { analyzeTurnTaking, DEFAULT_GAP_THRESHOLD_MS } from '../turnTaking.js'
import { fmtSeconds, fmtTime } from '../utils.js'

/**
 * Turn-taking ("接话关系") panel for the editor page.
 *
 * Props:
 *   segments          editor's current segments (unsaved edits included)
 *   onLocate(range)   focus the existing timeline on a transition span
 *
 * Statistics are recomputed synchronously from the live segment list, so any
 * unsaved edit (speaker, start/end, add/delete) refreshes the result at once.
 *
 * Same-speaker overlapping/touching segments are merged first; the timeline
 * is then cut at every boundary into solo / co-speaking / silence intervals.
 * Two temporally adjacent solo intervals with different speakers form:
 *   - 普通接话: only silence in between, not longer than the adjustable
 *     threshold (touching solos count as a zero-length gap);
 *   - 插话:     only co-speaking intervals of exactly these two speakers
 *     in between;
 *   - a third speaker or a mixed transition: not counted.
 * Counts and per-occurrence times are grouped by directed speaker pair;
 * clicking an occurrence locates it on the timeline above.
 */
export default function TurnTakingPanel({ segments, onLocate }) {
  const [thresholdText, setThresholdText] = useState(
    String(DEFAULT_GAP_THRESHOLD_MS / 1000))
  const [activeKey, setActiveKey] = useState(null)

  const parsed = Number(thresholdText)
  const thresholdMs = Number.isFinite(parsed) && parsed >= 0
    ? parsed * 1000
    : DEFAULT_GAP_THRESHOLD_MS

  const report = useMemo(
    () => analyzeTurnTaking(segments, thresholdMs),
    [segments, thresholdMs])

  const locate = (pair, rec, kind) => {
    const key = `${kind}-${rec.startMs}-${rec.endMs}-${pair.from}|${pair.to}`
    setActiveKey(key)
    onLocate?.({
      start: rec.startMs / 1000,
      end: rec.endMs / 1000,
      from: pair.from,
      to: pair.to,
      kind,
    })
  }

  const chip = (pair, rec, kind, i) => {
    const key = `${kind}-${rec.startMs}-${rec.endMs}-${pair.from}|${pair.to}`
    return (
      <button
        type="button"
        key={`${key}-${i}`}
        className={`turn-chip kind-${kind}${
          activeKey === key ? ' active' : ''
        }`}
        onClick={() => locate(pair, rec, kind)}
        title="点击在时间轴定位"
      >
        {fmtTime(rec.startMs / 1000)} – {fmtTime(rec.endMs / 1000)}
        <span className="muted">
          {' '}· {kind === 'normal' ? '静默' : '同讲'}{' '}
          {fmtSeconds(rec.gapMs / 1000)} s
        </span>
      </button>
    )
  }

  return (
    <div className="panel turn-panel">
      <h3>🔀 接话关系</h3>
      <div className="muted small turn-hint">
        先按毫秒半开区间合并同一说话人相交或相接的片段，再依全部起止边界划分
        独讲 / 同讲 / 静默区间。时间上相邻且说话人不同的两段独讲之间：仅有
        不超过阈值的静默记「普通接话」（端点相接视为零静默）；仅有这两人
        同时发言的区间记「插话」；出现第三人或静默与同讲混合过渡则不计。
        按有向说话人对统计，片段的任何未保存修改都会即时重算。
      </div>

      <div className="turn-controls">
        <label htmlFor="turn-gap-threshold">静默阈值</label>
        <input
          id="turn-gap-threshold"
          type="number"
          min="0"
          step="0.1"
          value={thresholdText}
          onChange={(e) => setThresholdText(e.target.value)}
        />
        <span className="muted small">
          秒 —— 独讲之间的静默不超过该时长才记为普通接话
        </span>
      </div>

      <div className="stats-grid turn-summary">
        <div className="stat-box">
          <span className="stat-num">{report.totalNormal}</span>
          <span className="muted small">普通接话（次）</span>
        </div>
        <div className="stat-box">
          <span className="stat-num">{report.totalInterruption}</span>
          <span className="muted small">插话（次）</span>
        </div>
        <div className="stat-box">
          <span className="stat-num">{report.pairs.length}</span>
          <span className="muted small">有向说话人对</span>
        </div>
      </div>

      {report.pairs.length === 0 ? (
        <div className="empty-hint">
          暂无接话记录（需要至少两段说话人不同、时间上相邻的独讲区间；
          已忽略空说话人与零长度片段）。
        </div>
      ) : (
        <div className="turn-pair-list">
          {report.pairs.map((pair) => (
            <div className="turn-pair" key={`${pair.from}\n${pair.to}`}>
              <div className="turn-pair-head">
                <span className="turn-pair-dir">
                  {pair.from} → {pair.to}
                </span>
                <span className="tag turn-tag-normal">
                  普通接话 {pair.normalCount}
                </span>
                <span className="tag turn-tag-interruption">
                  插话 {pair.interruptionCount}
                </span>
              </div>
              {pair.normal.length > 0 && (
                <div className="turn-group">
                  <div className="turn-group-title">
                    普通接话（{pair.normal.length}）
                  </div>
                  <div className="turn-chip-list">
                    {pair.normal.map((rec, i) => chip(pair, rec, 'normal', i))}
                  </div>
                </div>
              )}
              {pair.interruption.length > 0 && (
                <div className="turn-group">
                  <div className="turn-group-title">
                    插话（{pair.interruption.length}）
                  </div>
                  <div className="turn-chip-list">
                    {pair.interruption.map(
                      (rec, i) => chip(pair, rec, 'interruption', i))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
