import { useState } from 'react'
import { api } from '../api.js'
import { fmtTime } from '../utils.js'

/**
 * Quote (phrase) search panel for the editor page.
 *
 * Props:
 *   projectId  current project id
 *   document   editor's current document (unsaved edits are searched)
 *   speakers   speaker name list for the optional speaker filter
 *   onLocate(match)  focus the timeline on a result
 *
 * Matching runs on the server: only time-touching same-speaker segments are
 * concatenated, whitespace is ignored, and each hit carries per-segment
 * character ranges back so the original text can be marked verbatim.
 */
export default function QuoteSearch({ projectId, document, speakers, onLocate }) {
  const [query, setQuery] = useState('')
  const [speaker, setSpeaker] = useState('')
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [activeKey, setActiveKey] = useState(null)

  const run = async (e) => {
    e?.preventDefault()
    // empty / whitespace-only query never triggers a search
    if (!query.trim()) {
      setError('请输入要检索的引语')
      setResult(null)
      return
    }
    setLoading(true); setError('')
    try {
      const data = await api.quoteSearch(projectId, {
        query,
        speaker: speaker || null,
        caseSensitive,
        document,
      })
      setResult(data)
    } catch (err) {
      setError(err.message)
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="panel quote-search">
      <h3>引语检索</h3>
      <form className="quote-form" onSubmit={run}>
        <input
          className="quote-input"
          value={query}
          placeholder="输入引语短语（忽略空白，可跨时间相接且同说话人的片段）"
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          className="quote-speaker"
          value={speaker}
          onChange={(e) => setSpeaker(e.target.value)}
          title="按说话人过滤（可选）"
        >
          <option value="">全部说话人</option>
          {speakers.map((sp) => (
            <option key={sp} value={sp}>{sp}</option>
          ))}
        </select>
        <label className="quote-case" title="区分大小写">
          <input
            type="checkbox"
            checked={caseSensitive}
            onChange={(e) => setCaseSensitive(e.target.checked)}
          />
          区分大小写
        </label>
        <button className="btn primary" disabled={loading}>
          {loading ? '检索中…' : '🔍 检索'}
        </button>
      </form>
      <div className="muted small quote-hint">
        仅拼接时间相接、说话人相同的片段；空白不参与匹配，跨说话人的文字
        不会被当作同一句引语。点击结果可在时间轴定位。
      </div>
      {error && <div className="error">{error}</div>}

      {result && (
        <div className="quote-results">
          <div className="quote-result-head">
            {result.count > 0
              ? `命中 ${result.count} 处`
              : '没有找到匹配的引语'}
            {result.speaker && (
              <span className="tag">说话人：{result.speaker}</span>
            )}
          </div>
          <div className="quote-hit-list">
            {result.matches.map((m, i) => {
              const key = `${m.start}-${m.end}-${i}`
              return (
                <div
                  key={key}
                  className={`quote-hit${activeKey === key ? ' active' : ''}`}
                  onClick={() => { setActiveKey(key); onLocate(m) }}
                >
                  <div className="quote-hit-head">
                    <strong>{m.speaker || '（无说话人）'}</strong>
                    <span className="quote-hit-range">
                      {fmtTime(m.start)} – {fmtTime(m.end)}
                    </span>
                    <span className="tag">
                      {m.segmentCount} 个片段
                    </span>
                  </div>
                  <div className="quote-hit-text">
                    {m.segments.map((s) => (
                      <div className="quote-seg" key={s.id}>
                        <span className="quote-seg-time muted">
                          {fmtTime(s.start)}–{fmtTime(s.end)}
                        </span>
                        <MarkedText text={s.text} ranges={s.ranges} />
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

/** Render segment text with matched half-open [start,end) ranges in <mark>. */
function MarkedText({ text, ranges }) {
  const parts = []
  let cursor = 0
  const sorted = [...ranges].sort((a, b) => a[0] - b[0])
  sorted.forEach(([start, end], i) => {
    if (start > cursor) parts.push(text.slice(cursor, start))
    parts.push(<mark key={i}>{text.slice(start, end)}</mark>)
    cursor = Math.max(cursor, end)
  })
  if (cursor < text.length) parts.push(text.slice(cursor))
  return <span className="quote-marked">{parts}</span>
}
