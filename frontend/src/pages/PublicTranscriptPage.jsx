import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { fmtTime } from '../utils.js'

/**
 * Public transcript (公开版转写) page.
 *
 * The user enters any number of sensitive words and the speakers to
 * anonymize; the server builds a read-only public version of the *saved*
 * transcript: every hit's non-whitespace characters become blocks (■) at
 * their original positions, and selected speakers become consistent
 * anonymous labels (说话人 1, 说话人 2, …). The downloaded JSON contains
 * only the segments — no annotations, no original speaker names, no
 * sensitive-word list — and the project itself is never modified.
 */
export default function PublicTranscriptPage() {
  const { id: projectId } = useParams()
  const [project, setProject] = useState(null)
  const [wordRows, setWordRows] = useState(['']) // sensitive-word inputs
  const [speakerRows, setSpeakerRows] = useState([]) // names to anonymize
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [preview, setPreview] = useState(null)
  const [stamp, setStamp] = useState('') // input signature the preview is based on
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    api.getProject(projectId)
      .then(setProject)
      .catch(() => setNotFound(true))
  }, [projectId])

  // speakers of the saved transcript, most frequent first (quick-add list)
  const existingSpeakers = useMemo(() => {
    const counts = new Map()
    ;(project?.document?.segments || []).forEach((s) => {
      const name = (s?.speaker || '').trim()
      if (name) counts.set(name, (counts.get(name) || 0) + 1)
    })
    return [...counts.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || (a.name < b.name ? -1 : 1))
  }, [project])

  const payload = useMemo(() => ({
    sensitiveWords: wordRows.map((w) => w.trim()).filter(Boolean),
    anonymizeSpeakers: speakerRows.map((s) => s.trim()).filter(Boolean),
    caseSensitive,
  }), [wordRows, speakerRows, caseSensitive])
  const currentStamp = useMemo(() => JSON.stringify(payload), [payload])
  const stale = !preview || stamp !== currentStamp

  // alias -> original name, for annotating the previewed transcript
  const aliasToOriginal = useMemo(() => {
    const map = new Map()
    ;(preview?.speakers || []).forEach((sp) => map.set(sp.alias, sp.original))
    return map
  }, [preview])

  const patchWord = (i, value) =>
    setWordRows((ws) => ws.map((w, j) => (j === i ? value : w)))
  const removeWord = (i) => setWordRows((ws) => ws.filter((_, j) => j !== i))
  const addWord = () => setWordRows((ws) => [...ws, ''])

  const patchSpeaker = (i, value) =>
    setSpeakerRows((ss) => ss.map((s, j) => (j === i ? value : s)))
  const removeSpeaker = (i) =>
    setSpeakerRows((ss) => ss.filter((_, j) => j !== i))
  const addSpeaker = (name = '') =>
    setSpeakerRows((ss) => {
      if (name && ss.some((s) => s.trim() === name.trim())) return ss
      return [...ss, name]
    })

  const runPreview = async () => {
    setError(''); setLoading(true)
    try {
      const data = await api.previewPublicTranscript(projectId, payload)
      setPreview(data)
      setStamp(currentStamp)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const download = async () => {
    setError(''); setDownloading(true)
    try {
      const res = await fetch(api.publicTranscriptDownloadUrl(projectId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        let detail = `下载失败 (${res.status})`
        try { detail = (await res.json()).detail || detail } catch { /* */ }
        throw new Error(detail)
      }
      const blob = await res.blob()
      const filename = filenameFromDisposition(res.headers.get('content-disposition'))
        || `${preview?.projectName || projectId}-公开版转写.json`
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

  if (notFound) {
    return (
      <div className="page">
        <div className="error">项目不存在。</div>
        <Link className="btn" to="/">← 返回项目列表</Link>
      </div>
    )
  }

  const s = preview?.summary

  return (
    <div className="page public-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>公开版转写{project ? ` · ${project.name}` : ''}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="warn-box">
        公开版转写为<strong>只读导出</strong>：基于<strong>已保存</strong>的转写
        （编辑页未保存的修改不包含在内）。敏感词命中的非空白字符按原位置替换为
        方块 ■，选中的说话人替换为一致的匿名编号；下载的 JSON
        <strong>只含转写片段</strong>（不含校注、原说话人姓名与敏感词原文），
        原项目不会被修改。
      </div>

      <div className="panel pt-words">
        <h3>
          敏感词（{payload.sensitiveWords.length}）
          <span className="muted small sp-panel-hint">
            可添加多条；匹配忽略空白，可跨时间相接、说话人相同的片段，遇空隙 /
            重叠 / 换人即断开；命中的非空白字符替换为 ■，重叠命中只遮盖一次。
          </span>
        </h3>
        <div className="sp-map-list">
          {wordRows.map((word, i) => (
            <div className="sp-map-row" key={i}>
              <input
                value={word}
                placeholder={`敏感词 ${i + 1}，如：李明`}
                onChange={(e) => patchWord(i, e.target.value)}
              />
              <button
                className="btn icon danger"
                title="删除此敏感词"
                disabled={wordRows.length === 1}
                onClick={() => removeWord(i)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
        <div className="sp-map-actions pt-word-actions">
          <button className="btn" onClick={addWord}>＋ 添加敏感词</button>
          <label className="quote-case" title="区分大小写">
            <input
              type="checkbox"
              checked={caseSensitive}
              onChange={(e) => setCaseSensitive(e.target.checked)}
            />
            区分大小写
          </label>
        </div>
      </div>

      <div className="panel pt-speakers">
        <h3>
          需匿名的说话人（{payload.anonymizeSpeakers.length}）
          <span className="muted small sp-panel-hint">
            选中的说话人在公开版中替换为「说话人 1」「说话人 2」……编号按其
            在已保存转写中首次出现的顺序分配，预览与下载一致；未选中的名称
            原样保留。
          </span>
        </h3>
        {existingSpeakers.length > 0 && (
          <div className="pt-existing">
            {existingSpeakers.map((sp) => {
              const added = speakerRows.some((s) => s.trim() === sp.name)
              return (
                <button
                  key={sp.name}
                  className="btn small"
                  disabled={added}
                  title={added ? '已加入匿名列表' : `匿名「${sp.name}」`}
                  onClick={() => addSpeaker(sp.name)}
                >
                  ＋ {sp.name}（{sp.count} 段）
                </button>
              )
            })}
          </div>
        )}
        <div className="sp-map-list">
          {speakerRows.length === 0 && (
            <div className="muted small sp-map-empty">
              尚无需匿名的说话人：点击上方名称或「＋ 添加说话人」手动输入。
            </div>
          )}
          {speakerRows.map((name, i) => (
            <div className="sp-map-row" key={i}>
              <input
                list="pt-speaker-list"
                value={name}
                placeholder={`说话人 ${i + 1}（精确名称）`}
                onChange={(e) => patchSpeaker(i, e.target.value)}
              />
              <button
                className="btn icon danger"
                title="移除此说话人"
                onClick={() => removeSpeaker(i)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
        <datalist id="pt-speaker-list">
          {existingSpeakers.map((sp) => (
            <option key={sp.name} value={sp.name} />
          ))}
        </datalist>
        <div className="sp-map-actions">
          <button className="btn" onClick={() => addSpeaker()}>
            ＋ 添加说话人
          </button>
        </div>
      </div>

      <div className="pt-run-row">
        <button className="btn primary big" disabled={loading} onClick={runPreview}>
          {loading ? '预览中…' : '🔍 生成公开版预览'}
        </button>
        <span className="muted small">
          预览只读不写库；确认遮盖与匿名无误后再下载。
        </span>
      </div>

      {preview && (
        <>
          <div className="panel pt-summary">
            <div className="preview-head">
              <h3 style={{ margin: 0 }}>公开版预览</h3>
              {stale && (
                <span className="badge warn">输入已修改，请重新生成预览</span>
              )}
            </div>
            <div className="stats-grid">
              <div className="stat-box">
                <strong>转写片段</strong><span>{s.segmentCount}</span>
              </div>
              <div className={`stat-box ${s.hitCount ? 'has-conflict' : ''}`}>
                <strong>敏感词命中</strong><span>{s.hitCount}</span>
              </div>
              <div className="stat-box">
                <strong>遮盖字符</strong><span>{s.maskedCharCount}</span>
              </div>
              <div className="stat-box">
                <strong>被遮盖片段</strong><span>{s.maskedSegmentCount}</span>
              </div>
              <div className="stat-box">
                <strong>匿名说话人</strong><span>{s.anonymizedSpeakerCount}</span>
              </div>
              <div className="stat-box">
                <strong>被匿名片段</strong><span>{s.anonymizedSegmentCount}</span>
              </div>
            </div>

            {preview.words.length > 0 && (
              <div className="pt-word-hits">
                {preview.words.map((w) => (
                  <span key={w.word} className={`tag ${w.count ? 'danger' : ''}`}>
                    「{w.word}」命中 {w.count} 处
                  </span>
                ))}
              </div>
            )}

            {preview.speakers.length > 0 && (
              <table className="run-table pt-map-table">
                <thead>
                  <tr><th>原说话人</th><th>匿名编号</th><th>涉及片段</th></tr>
                </thead>
                <tbody>
                  {preview.speakers.map((sp) => (
                    <tr key={sp.original}>
                      <td><strong>{sp.original}</strong></td>
                      <td><span className="tag pt-alias-tag">{sp.alias}</span></td>
                      <td>
                        {sp.appears
                          ? `${sp.segmentCount} 段`
                          : <span className="muted small">未出现</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <div className="sp-confirm-row">
              <button
                className="btn primary big"
                disabled={stale || downloading}
                onClick={download}
              >
                {downloading ? '下载中…' : '⬇️ 确认并下载公开版 JSON'}
              </button>
              <span className="muted small">
                {stale
                  ? '输入有改动，请先重新生成预览。'
                  : 'JSON 只含转写片段（ID、起止时间、匿名后说话人、遮盖后文本）；'
                    + '不含校注、原说话人姓名与敏感词原文，原项目不变。'}
              </span>
            </div>
          </div>

          <div className="panel pt-transcript">
            <h3>
              公开版片段（{preview.segments.length}）
              <span className="muted small sp-panel-hint">
                与下载的 JSON 内容一致；黄色高亮为被遮盖的字符。
              </span>
            </h3>
            {preview.segments.length === 0 ? (
              <div className="empty-hint">已保存的转写暂无片段。</div>
            ) : (
              <div className="quote-hit-list">
                {preview.segments.map((seg, i) => (
                  <SegmentRow
                    key={seg.id || `seg-${i}`}
                    seg={seg}
                    original={seg.anonymized
                      ? aliasToOriginal.get(seg.speaker)
                      : null}
                  />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function SegmentRow({ seg, original }) {
  return (
    <div className={`hit-item pt-seg${seg.masked ? ' pt-masked' : ''}`}>
      <div className="hit-range">
        <span className="exc-time">
          {fmtTime(seg.start)} – {fmtTime(seg.end)}
        </span>
        {seg.speaker ? ` · ${seg.speaker}` : ''}
        {original ? <span className="muted small">（原：{original}）</span> : null}
        {seg.id ? <span className="muted small"> · {seg.id}</span> : null}
        {seg.masked && (
          <span className="tag danger">遮盖 {seg.maskedChars} 字</span>
        )}
        {seg.anonymized && <span className="tag">已匿名</span>}
      </div>
      <div className="hit-text">
        <MarkedText text={seg.text} ranges={seg.maskedRanges} />
      </div>
    </div>
  )
}

/** Render segment text with the masked half-open [start,end) runs in <mark>. */
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

function filenameFromDisposition(disposition) {
  if (!disposition) return ''
  const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8) {
    try { return decodeURIComponent(utf8[1].trim()) } catch { /* fall through */ }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/i)
  return plain ? plain[1] : ''
}
