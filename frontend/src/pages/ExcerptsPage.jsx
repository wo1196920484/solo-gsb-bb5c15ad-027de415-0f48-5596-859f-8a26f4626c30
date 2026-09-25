import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { fmtSeconds, fmtTime } from '../utils.js'

const REASON_TEXT = {
  crosses_start: '片段起于区间开始之前（结尾探入区间）',
  crosses_end: '片段终于区间结束之后（开头探入区间）',
  crosses_both: '片段在区间两侧都伸出',
  crosses_gap: '跨越区间之间的空隙（同时跨界多个合并区间）',
}

function reasonText(code) {
  return REASON_TEXT[code] || code
}

/** Validate the range rows client-side; '' means valid. */
export function validateRanges(rows) {
  if (rows.length === 0) return '请至少添加一个时间区间'
  for (let i = 0; i < rows.length; i += 1) {
    const s = Number(rows[i].start)
    const e = Number(rows[i].end)
    if (rows[i].start.trim() === '' || rows[i].end.trim() === ''
        || !Number.isFinite(s) || !Number.isFinite(e)) {
      return `第 ${i + 1} 行区间的起止时间必须是数字`
    }
    if (s < 0 || e < 0) return `第 ${i + 1} 行区间的时间不能为负数`
    if (e <= s) return `第 ${i + 1} 行区间的结束时间必须晚于开始时间`
  }
  return ''
}

export default function ExcerptsPage() {
  const { id: projectId } = useParams()
  const [projectName, setProjectName] = useState('')
  const [rows, setRows] = useState([{ start: '', end: '' }])
  const [preview, setPreview] = useState(null)
  const [stamp, setStamp] = useState('') // rows signature the preview is based on
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    api.getProject(projectId)
      .then((p) => setProjectName(p.name))
      .catch(() => setNotFound(true))
  }, [projectId])

  if (notFound) {
    return (
      <div className="page">
        <div className="error">项目不存在。</div>
        <Link className="btn" to="/">← 返回项目列表</Link>
      </div>
    )
  }

  const validationError = useMemo(() => validateRanges(rows), [rows])
  const currentStamp = useMemo(
    () => JSON.stringify(rows.map((r) => [Number(r.start), Number(r.end)])
      .sort((a, b) => a[0] - b[0] || a[1] - b[1])),
    [rows])
  const stale = !preview || stamp !== currentStamp

  const patchRow = (i, patch) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const removeRow = (i) => setRows((rs) => rs.filter((_, j) => j !== i))
  const addRow = () => setRows((rs) => [...rs, { start: '', end: '' }])

  const runPreview = async () => {
    if (validationError) { setError(validationError); return }
    setError(''); setLoading(true)
    try {
      const data = await api.previewExcerpts(
        projectId, rows.map((r) => ({
          start: Number(r.start), end: Number(r.end),
        })))
      setPreview(data)
      setStamp(currentStamp)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const download = async () => {
    if (validationError) { setError(validationError); return }
    setError(''); setDownloading(true)
    try {
      const res = await fetch(api.excerptsDownloadUrl(projectId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ranges: rows.map((r) => ({
            start: Number(r.start), end: Number(r.end),
          })),
        }),
      })
      if (!res.ok) {
        let detail = `下载失败 (${res.status})`
        try { detail = (await res.json()).detail || detail } catch { /* */ }
        throw new Error(detail)
      }
      const blob = await res.blob()
      const filename = filenameFromDisposition(res.headers.get('content-disposition'))
        || `${preview?.projectName || projectId}-摘录包.json`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message)
    } finally {
      setDownloading(false)
    }
  }

  const s = preview?.summary

  return (
    <div className="page excerpts-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>摘录包{projectName ? ` · ${projectName}` : ''}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="warn-box">
        摘录包为只读导出：仅收录<strong>完整落在合并区间内</strong>的转写片段，
        跨界片段整段列为排除项（不会截取片段文本充作摘录）；校注按区间边界
        裁剪，跨越多个不相连区间的校注会拆成多条并保留原校注 ID。
        确认下载不会修改原项目。
      </div>

      <div className="panel exc-ranges">
        <h3>
          摘录区间（秒）
          <span className="muted small sp-panel-hint">
            可添加任意多行，顺序不限、允许重叠；预览时相交或首尾相接的区间
            会自动合并。
          </span>
        </h3>
        <table className="run-table exc-range-table">
          <thead>
            <tr><th style={{ width: 48 }}>#</th><th>开始（秒）</th>
              <th>结束（秒）</th><th style={{ width: 60 }}></th></tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                <td className="muted">{i + 1}</td>
                <td>
                  <input
                    value={row.start}
                    inputMode="decimal"
                    placeholder="如 12.5"
                    onChange={(e) => patchRow(i, { start: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    value={row.end}
                    inputMode="decimal"
                    placeholder="如 90"
                    onChange={(e) => patchRow(i, { end: e.target.value })}
                  />
                </td>
                <td>
                  <button
                    className="btn icon danger"
                    title="删除此行"
                    disabled={rows.length === 1}
                    onClick={() => removeRow(i)}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="sp-map-actions" style={{ display: 'flex', gap: 8 }}>
          <button className="btn" onClick={addRow}>＋ 添加区间</button>
          <button
            className="btn primary"
            disabled={!!validationError || loading}
            onClick={runPreview}
          >
            {loading ? '预览中…' : '🔍 生成预览'}
          </button>
        </div>
        {validationError && <div className="error">{validationError}</div>}
      </div>

      {preview && (
        <>
          <div className="panel exc-summary">
            <div className="preview-head">
              <h3 style={{ margin: 0 }}>预览结果</h3>
              {stale && (
                <span className="badge warn">区间已修改，请重新生成预览</span>
              )}
            </div>
            <div className="stats-grid">
              <div className="stat-box">
                <strong>输入区间</strong><span>{s.inputRangeCount}</span>
              </div>
              <div className="stat-box">
                <strong>合并后区间</strong><span>{s.mergedRangeCount}</span>
              </div>
              <div className="stat-box">
                <strong>收录片段</strong><span>{s.segmentCount}</span>
              </div>
              <div className="stat-box">
                <strong>收录校注</strong><span>{s.annotationCount}</span>
              </div>
              <div className={`stat-box ${s.excludedSegmentCount ? 'has-conflict' : ''}`}>
                <strong>排除片段</strong><span>{s.excludedSegmentCount}</span>
              </div>
              <div className={`stat-box ${s.splitAnnotationCount ? 'has-conflict' : ''}`}>
                <strong>拆分校注（来源）</strong><span>{s.splitAnnotationCount}</span>
              </div>
            </div>
            <div className="sp-confirm-row">
              <button
                className="btn primary big"
                disabled={stale || downloading}
                onClick={download}
              >
                {downloading ? '下载中…' : '⬇️ 确认并下载摘录包 JSON'}
              </button>
              <span className="muted small">
                {stale
                  ? '区间有改动，请先重新生成预览。'
                  : 'JSON 含合并区间、收录片段、裁剪后校注（带来源标识）与排除项；'
                    + '原项目不会被修改。'}
              </span>
            </div>
          </div>

          {preview.intervals.map((iv) => (
            <IntervalCard key={iv.index} iv={iv} />
          ))}

          <GlobalExcluded items={preview.excludedSegments} />
        </>
      )}
    </div>
  )
}

function IntervalCard({ iv }) {
  return (
    <div className="panel exc-interval">
      <div className="preview-head">
        <h3 style={{ margin: 0 }}>
          区间 {iv.index + 1}：
          <span className="exc-time">
            {fmtTime(iv.start)} – {fmtTime(iv.end)}
          </span>
          <span className="muted small">
            （{fmtSeconds(iv.start)}s – {fmtSeconds(iv.end)}s，
            时长 {fmtSeconds(iv.end - iv.start)}s）
          </span>
        </h3>
        <div className="exc-counts">
          <span className="tag">片段 {iv.segmentCount}</span>
          <span className="tag">校注 {iv.annotationCount}</span>
          <span className={`tag ${iv.excludedSegmentCount ? 'danger' : ''}`}>
            排除 {iv.excludedSegmentCount}
          </span>
        </div>
      </div>

      {iv.sources.length > 1 && (
        <div className="muted small exc-merged-from">
          由 {iv.sources.length} 个相交 / 首尾相接的输入区间合并而成：
          {iv.sources.map((src, i) => (
            <span key={i} className="tag exc-src-tag">
              {fmtSeconds(src.start)}–{fmtSeconds(src.end)}
            </span>
          ))}
        </div>
      )}

      <h4>收录的转写片段（{iv.segmentCount}）</h4>
      {iv.segments.length === 0 ? (
        <div className="empty-hint">没有完整落在此区间内的转写片段。</div>
      ) : (
        <div className="quote-hit-list exc-seg-list">
          {iv.segments.map((seg, i) => (
            <div key={`${iv.index}-${seg.id || 'seg'}-${i}`} className="hit-item">
              <div className="hit-range">
                <span className="exc-time">
                  {fmtTime(seg.start)} – {fmtTime(seg.end)}
                </span>
                {seg.speaker ? ` · ${seg.speaker}` : ''}
                {seg.id ? <span className="muted small"> · {seg.id}</span> : null}
              </div>
              <div className="hit-text">{seg.text}</div>
            </div>
          ))}
        </div>
      )}

      <h4>裁剪后的校注（{iv.annotationCount}）</h4>
      {iv.annotations.length === 0 ? (
        <div className="muted small exc-empty-anns">
          没有与此区间时间相交的校注（仅端点相接不计）。
        </div>
      ) : (
        <div className="ann-grid exc-ann-grid">
          {iv.annotations.map((a) => (
            <div
              key={a.id}
              className="ann-chip"
              style={{ borderLeftColor: a.color || '#5b8def' }}
            >
              <div className="ann-chip-head">
                <strong>{a.topic || '（未命名主题）'}</strong>
              </div>
              <div className="muted small">
                <span className="exc-time">
                  {fmtTime(a.start)} – {fmtTime(a.end)}
                </span>
                {a.speaker ? ` · ${a.speaker}` : ''}
              </div>
              <div className="exc-source">
                <span className="muted small">来源校注：</span>
                <code>{a.sourceAnnotationId || '（原校注无 ID）'}</code>
                {a.sourcePartCount > 1 && (
                  <span className="tag danger exc-part-tag">
                    跨区间拆分 {a.sourcePart}/{a.sourcePartCount}
                  </span>
                )}
              </div>
              {a.note && <div className="ann-note">{a.note}</div>}
            </div>
          ))}
        </div>
      )}

      <h4>排除项：跨界转写片段（{iv.excludedSegmentCount}）</h4>
      {iv.excludedSegments.length === 0 ? (
        <div className="muted small">无跨界片段。</div>
      ) : (
        <div className="exc-excluded-list">
          {iv.excludedSegments.map((seg, i) => (
            <ExcludedRow
              key={`${iv.index}-${seg.id || 'seg'}-${i}`}
              seg={seg}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function ExcludedRow({ seg }) {
  return (
    <div className="hit-item exc-excluded">
      <div className="hit-range">
        <span className="exc-time">
          {fmtTime(seg.start)} – {fmtTime(seg.end)}
        </span>
        {seg.speaker ? ` · ${seg.speaker}` : ''}
        {seg.id ? <span className="muted small"> · {seg.id}</span> : null}
      </div>
      <div className="hit-text muted">{seg.text || '（无文本）'}</div>
      <div className="exc-reason">🚫 {reasonText(seg.reason)}</div>
    </div>
  )
}

function GlobalExcluded({ items }) {
  return (
    <div className="panel exc-global-excluded">
      <h3>
        排除片段汇总（去重，{items.length}）
        <span className="muted small sp-panel-hint">
          这些片段与摘录区间时间相交但未被完整包含，下载的 JSON 中同样列出，
          并注明其跨界原因与涉及的合并区间序号。
        </span>
      </h3>
      {items.length === 0 ? (
        <div className="empty-hint">没有跨界片段，全部相交片段均完整收录。</div>
      ) : (
        <div className="exc-excluded-list">
          {items.map((seg, i) => (
            <div key={`${seg.id || 'seg'}-${i}`}>
              <ExcludedRow seg={seg} />
              <div className="muted small exc-interval-ref">
                涉及合并区间：
                {seg.intervalIndices.map((i) => (
                  <span key={i} className="tag">区间 {i + 1}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function filenameFromDisposition(disposition) {
  if (!disposition) return ''
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8) {
    try { return decodeURIComponent(utf8[1].trim()) } catch { /* fall through */ }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/i)
  return plain ? plain[1] : ''
}
