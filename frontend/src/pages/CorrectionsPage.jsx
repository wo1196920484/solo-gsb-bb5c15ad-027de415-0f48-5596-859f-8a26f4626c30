import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { fmtTime } from '../utils.js'

/**
 * Transcript error-word batch correction (转写错词批量校正) workbench.
 *
 * The user configures any number of wrong-word -> right-word pairs; the
 * preview runs against the *saved* transcript and lists every hit segment
 * with its hits and corrected text.  Matching scans each segment's original
 * text once, left to right: at each position the longest wrong word wins
 * and replacement output is never re-matched.  Confirming re-validates the
 * word list and the preview's transcript version — if the saved transcript
 * was edited or re-imported in the meantime the write is rejected (409)
 * and a fresh preview is required.  On success the changed segment texts
 * are saved atomically; segment ids, times, speakers and all annotations
 * are preserved.
 */

/** Validate the correction rows client-side; returns '' when they are sound. */
export function validatePairs(rows) {
  const seen = new Set()
  for (const row of rows) {
    const from = (row.from || '').trim()
    const to = (row.to || '').trim()
    if (!from && !to) continue // completely blank row -> ignored
    if (!from) return '存在未填写错词的校正行，请补全或删除该行'
    if (seen.has(from)) {
      return `错词「${from}」配置了多组校正，请去重后再提交`
    }
    seen.add(from)
  }
  return ''
}

export default function CorrectionsPage() {
  const { id: projectId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState(null)
  const [rows, setRows] = useState([{ from: '', to: '' }]) // correction pairs
  const [preview, setPreview] = useState(null)
  const [stamp, setStamp] = useState('') // input signature the preview is based on
  const [result, setResult] = useState(null) // last committed apply result
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    api.getProject(projectId)
      .then(setProject)
      .catch(() => setNotFound(true))
  }, [projectId])

  const validationError = useMemo(() => validatePairs(rows), [rows])
  const completeRows = useMemo(
    () => rows
      .map((r) => ({ from: (r.from || '').trim(), to: (r.to || '').trim() }))
      .filter((r) => r.from),
    [rows])
  const payload = useMemo(() => ({ pairs: completeRows }), [completeRows])
  const currentStamp = useMemo(() => JSON.stringify(payload), [payload])
  const stale = !preview || stamp !== currentStamp

  const patchRow = (i, patch) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const removeRow = (i) => setRows((rs) => rs.filter((_, j) => j !== i))
  const addRow = () => setRows((rs) => [...rs, { from: '', to: '' }])

  const runPreview = async () => {
    if (validationError) { setError(validationError); return }
    setError(''); setLoading(true); setResult(null)
    try {
      const data = await api.previewCorrections(projectId, payload)
      setPreview(data)
      setStamp(currentStamp)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const confirm = async () => {
    if (!preview || stale || busy) return
    const s = preview.summary
    if (!window.confirm(
      `确定校正 ${s.changedSegmentCount} 个片段、共 ${s.hitCount} 处替换？\n` +
      '将在一次事务中保存改动的片段文本；片段 ID、起止时间、说话人及' +
      '全部校注保持不变。'
    )) return
    setBusy(true); setError('')
    try {
      const res = await api.applyCorrections(projectId, {
        pairs: completeRows,
        transcriptVersion: preview.transcriptVersion,
      })
      setResult(res)
      // the saved transcript moved on: any further correction needs a
      // fresh preview against the new version
      setPreview(null)
      setStamp('')
    } catch (e) {
      setError(e.message)
      // e.g. the saved transcript was edited / re-imported after the
      // preview (409): force a fresh preview before confirming again
      setStamp('')
    } finally {
      setBusy(false)
    }
  }

  if (notFound) {
    return (
      <div className="page">
        <div className="error">项目不存在。</div>
        <Link className="btn" to="/">← 返回项目列表</Link>
      </div>
    )
  }
  if (!project && !error) return <div className="page">加载中…</div>

  const s = preview?.summary
  const canConfirm = !!preview && !stale && !busy
    && s.changedSegmentCount > 0

  return (
    <div className="page corrections-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>转写错词批量校正{project ? ` · ${project.name}` : ''}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="warn-box">
        校正基于<strong>已保存</strong>的转写（编辑页未保存的修改不包含在内）。
        逐片段从左到右<strong>只扫描原文一次</strong>：同一位置
        <strong>优先最长错词</strong>，替换出的文字不再参与匹配。确认时会校验
        预览对应的转写版本与词表——若期间已保存的转写被编辑或重新导入，
        将被拒绝并要求重新预览；校验通过后在一次事务中保存改动的片段文本，
        片段 ID、起止时间、说话人及全部校注保持不变。
      </div>

      <div className="panel cr-pairs">
        <h3>
          错词 → 正词（{completeRows.length} 组）
          <span className="muted small sp-panel-hint">
            可添加多组；错词留空的行与重复错词无法提交；正词留空表示删除该
            错词；完全空白的行会被忽略。
          </span>
        </h3>
        <div className="sp-map-list">
          {rows.map((row, i) => (
            <div className="sp-map-row" key={i}>
              <input
                value={row.from}
                placeholder={`错词 ${i + 1}，如：的候`}
                onChange={(e) => patchRow(i, { from: e.target.value })}
              />
              <span className="sp-arrow">→</span>
              <input
                value={row.to}
                placeholder="正词（留空表示删除错词）"
                onChange={(e) => patchRow(i, { to: e.target.value })}
              />
              <button
                className="btn icon danger"
                title="删除此组"
                disabled={rows.length === 1}
                onClick={() => removeRow(i)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
        <div className="sp-map-actions">
          <button className="btn" onClick={addRow}>＋ 添加一组</button>
        </div>
        {validationError && <div className="error">{validationError}</div>}
      </div>

      <div className="pt-run-row">
        <button
          className="btn primary big"
          disabled={loading || !!validationError}
          onClick={runPreview}
        >
          {loading ? '预览中…' : '🔍 预览校正'}
        </button>
        <span className="muted small">
          预览只读不写库；逐片段列出命中与校正后文本，确认无误后再保存。
        </span>
      </div>

      {result && (
        <div className="ok cr-result">
          <div>
            <strong>已保存：</strong>
            影响 {result.counts.segments} 个片段，共{' '}
            {result.counts.replacements} 处替换；片段 ID、起止时间、说话人
            及全部校注均未变化。
          </div>
          <div className="cr-result-actions">
            <button
              className="btn primary"
              onClick={() => navigate(`/projects/${projectId}`, {
                state: { correctionApply: result.counts },
              })}
            >
              返回编辑页查看结果 →
            </button>
            <button
              className="btn"
              onClick={() => { setResult(null); runPreview() }}
            >
              继续校正（重新预览）
            </button>
          </div>
        </div>
      )}

      {preview && (
        <>
          <div className="panel cr-summary">
            <div className="preview-head">
              <h3 style={{ margin: 0 }}>校正预览</h3>
              {stale && (
                <span className="badge warn">输入已修改，请重新生成预览</span>
              )}
            </div>
            <div className="stats-grid">
              <div className="stat-box">
                <strong>校正组数</strong><span>{s.pairCount}</span>
              </div>
              <div className="stat-box">
                <strong>转写片段</strong><span>{s.segmentCount}</span>
              </div>
              <div className="stat-box">
                <strong>命中片段</strong><span>{s.hitSegmentCount}</span>
              </div>
              <div className={`stat-box ${s.changedSegmentCount ? 'has-conflict' : ''}`}>
                <strong>影响片段</strong><span>{s.changedSegmentCount}</span>
              </div>
              <div className="stat-box">
                <strong>替换处数</strong><span>{s.hitCount}</span>
              </div>
            </div>

            {preview.words.length > 0 && (
              <div className="pt-word-hits">
                {preview.words.map((w) => (
                  <span key={w.from} className={`tag ${w.count ? 'danger' : ''}`}>
                    「{w.from}」→「{w.to}」命中 {w.count} 处
                  </span>
                ))}
              </div>
            )}

            <div className="sp-confirm-row">
              <button
                className="btn primary big"
                disabled={!canConfirm}
                onClick={confirm}
              >
                {busy
                  ? '保存中…'
                  : `✅ 确认校正（影响 ${s.changedSegmentCount} 个片段）`}
              </button>
              <span className="muted small">
                {stale
                  ? '词表有改动或转写已变化，请先重新生成预览。'
                  : s.changedSegmentCount === 0
                    ? '当前词表不会改动任何片段文本。'
                    : '确认时校验预览对应的转写版本与词表；校验通过后在一次'
                      + '事务中保存改动的片段文本。'}
              </span>
            </div>
          </div>

          <div className="panel cr-segments">
            <h3>
              命中片段（{preview.segments.length}）
              <span className="muted small sp-panel-hint">
                上行为原文（黄色高亮为命中的错词），下行为校正后文本
                （绿色高亮为替换出的正词）。
              </span>
            </h3>
            {preview.segments.length === 0 ? (
              <div className="empty-hint">
                已保存的转写中没有命中任何错词。
              </div>
            ) : (
              <div className="quote-hit-list">
                {preview.segments.map((seg, i) => (
                  <SegmentPreview key={seg.id || `seg-${i}`} seg={seg} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function SegmentPreview({ seg }) {
  const originalRanges = seg.hits.map((h) => [h.start, h.end])
  const newRanges = correctedRanges(seg.hits)
  return (
    <div className={`hit-item pt-seg${seg.changed ? ' pt-masked' : ''}`}>
      <div className="hit-range">
        <span className="exc-time">
          {fmtTime(seg.start)} – {fmtTime(seg.end)}
        </span>
        {seg.speaker ? ` · ${seg.speaker}` : ''}
        {seg.id ? <span className="muted small"> · {seg.id}</span> : null}
        <span className="tag danger">命中 {seg.hitCount} 处</span>
        {!seg.changed && <span className="tag">文本不变</span>}
      </div>
      <div className="hit-text">
        <MarkedText text={seg.originalText} ranges={originalRanges} />
      </div>
      <div className="hit-text cr-new">
        <MarkedText text={seg.correctedText} ranges={newRanges} />
      </div>
    </div>
  )
}

/**
 * Map the original-text hit ranges to ranges in the corrected text: each
 * hit shifts later offsets by (right-word length - wrong-word length).
 */
function correctedRanges(hits) {
  const out = []
  let delta = 0
  for (const h of hits) {
    const start = h.start + delta
    out.push([start, start + h.to.length])
    delta += h.to.length - (h.end - h.start)
  }
  return out
}

/** Render text with the given half-open [start,end) runs in <mark>. */
function MarkedText({ text, ranges }) {
  const parts = []
  let cursor = 0
  const sorted = [...(ranges || [])].sort((a, b) => a[0] - b[0])
  sorted.forEach(([start, end], i) => {
    if (start > cursor) parts.push(text.slice(cursor, start))
    parts.push(<mark key={i}>{text.slice(start, end)}</mark>)
    cursor = Math.max(cursor, end)
  })
  if (cursor < text.length) parts.push(text.slice(cursor))
  return <span className="quote-marked">{parts}</span>
}
