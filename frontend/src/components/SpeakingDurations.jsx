import { useMemo, useState } from 'react'
import { analyzeSpeakingTime } from '../speakingTime.js'
import { fmtSeconds, fmtTime } from '../utils.js'

/**
 * Speaking-duration panel for the editor page.
 *
 * Props:
 *   segments          editor's current segments (unsaved edits included)
 *   onLocate(range)   focus the existing timeline on a co-speaking interval
 *
 * Statistics are recomputed synchronously from the live segment list, so any
 * unsaved edit (speaker, start/end, add/delete) refreshes the result at once.
 *
 * Per speaker: solo time (only this speaker active) and co-speaking time
 * (slice with >= 2 speakers, counted once per active speaker). The panel also
 * lists every multi-speaker interval (click to locate on the timeline) and
 * highlights the ones where every known speaker talks at once.
 */
export default function SpeakingDurations({ segments, onLocate }) {
  const [activeKey, setActiveKey] = useState(null)

  const report = useMemo(() => analyzeSpeakingTime(segments), [segments])

  const clickInterval = (iv) => {
    const key = `${iv.startMs}-${iv.endMs}-${iv.speakers.join('|')}`
    setActiveKey(key)
    onLocate?.({
      start: iv.startMs / 1000,
      end: iv.endMs / 1000,
      speakers: iv.speakers,
    })
  }

  const hasSpeakers = report.speakers.length > 0

  return (
    <div className="panel speaking-panel">
      <h3>🕓 发言时长</h3>
      <div className="muted small speaking-hint">
        按片段的半开时间区间统计：先合并每位说话人自身重叠的片段，再以全部
        起止时刻切分扫描线；仅一人说话计入「独讲」，多人同时说话时每位
        参与者各计一次「同讲」（不因片段重叠重复累加）。毫秒精度，显示为秒。
      </div>

      {!hasSpeakers && (
        <div className="empty-hint">
          暂无可统计的片段（已忽略空说话人与零长度片段）。
        </div>
      )}

      {hasSpeakers && (
        <>
          <div className="stats-grid speaking-summary">
            <div className="stat-box">
              <span className="stat-num">
                {fmtSeconds(report.totalSoloMs / 1000)}
                <span className="stat-unit">s</span>
              </span>
              <span className="muted small">独讲总时长</span>
            </div>
            <div className="stat-box">
              <span className="stat-num">
                {fmtSeconds(report.totalOverlapMs / 1000)}
                <span className="stat-unit">s</span>
              </span>
              <span className="muted small">
                同讲总时长（按说话人累计）
              </span>
            </div>
            <div className="stat-box">
              <span className="stat-num">
                {fmtSeconds(report.totalAllMs / 1000)}
                <span className="stat-unit">s</span>
              </span>
              <span className="muted small">
                全体同时发言（{report.allIntervals.length} 段）
              </span>
            </div>
            <div className="stat-box">
              <span className="stat-num">{report.speakers.length}</span>
              <span className="muted small">说话人数</span>
            </div>
          </div>

          <table className="run-table speaking-table">
            <thead>
              <tr>
                <th>说话人</th>
                <th>独讲时长</th>
                <th>同讲时长</th>
                <th>合计</th>
              </tr>
            </thead>
            <tbody>
              {report.speakers.map((sp) => (
                <tr key={sp.name}>
                  <td><strong>{sp.name}</strong></td>
                  <td className="sp-num">{fmtSeconds(sp.soloMs / 1000)} s</td>
                  <td className="sp-num">{fmtSeconds(sp.overlapMs / 1000)} s</td>
                  <td className="sp-num">{fmtSeconds(sp.totalMs / 1000)} s</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="speaking-interval-block">
            <h4>同讲区间（{report.overlapIntervals.length}）</h4>
            {report.overlapIntervals.length === 0 ? (
              <div className="muted small speaking-empty">
                没有多人同时发言的区间。
              </div>
            ) : (
              <div className="speaking-interval-list">
                {report.overlapIntervals.map((iv, i) => {
                  const key = `${iv.startMs}-${iv.endMs}-${iv.speakers.join('|')}`
                  return (
                    <button
                      type="button"
                      key={`${key}-${i}`}
                      className={`speaking-interval${iv.all ? ' all' : ''}${
                        activeKey === key ? ' active' : ''
                      }`}
                      onClick={() => clickInterval(iv)}
                      title="点击在时间轴定位"
                    >
                      <span className="speaking-interval-time">
                        {fmtTime(iv.startMs / 1000)} –{' '}
                        {fmtTime(iv.endMs / 1000)}
                      </span>
                      <span className="speaking-interval-speakers">
                        {iv.speakers.join('、')}
                      </span>
                      <span className="speaking-interval-meta">
                        {iv.all && <span className="tag speaking-all-tag">全体</span>}
                        <span className="muted">
                          {iv.speakers.length} 人 ·{' '}
                          {fmtSeconds(iv.durationMs / 1000)} s
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>

          {report.allIntervals.length > 0 && (
            <div className="speaking-interval-block">
              <h4>全体同时发言（{report.allIntervals.length}）</h4>
              <div className="speaking-interval-list">
                {report.allIntervals.map((iv, i) => {
                  const key = `${iv.startMs}-${iv.endMs}-${iv.speakers.join('|')}`
                  return (
                    <button
                      type="button"
                      key={`${key}-all-${i}`}
                      className={`speaking-interval all${
                        activeKey === key ? ' active' : ''
                      }`}
                      onClick={() => clickInterval(iv)}
                      title="点击在时间轴定位"
                    >
                      <span className="speaking-interval-time">
                        {fmtTime(iv.startMs / 1000)} –{' '}
                        {fmtTime(iv.endMs / 1000)}
                      </span>
                      <span className="speaking-interval-speakers">
                        {iv.speakers.join('、')}
                      </span>
                      <span className="speaking-interval-meta">
                        <span className="tag speaking-all-tag">全体</span>
                        <span className="muted">
                          {fmtSeconds(iv.durationMs / 1000)} s
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
