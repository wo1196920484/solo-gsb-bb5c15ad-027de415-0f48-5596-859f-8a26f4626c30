import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'

/** Validate the mapping rows client-side; returns '' when they are sound. */
export function validateMappings(rows) {
  const direct = new Map()
  for (const row of rows) {
    const from = (row.from || '').trim()
    const to = (row.to || '').trim()
    if (!from && !to) continue
    if (!from) return '存在未填写旧名的映射行，请补全或删除该行'
    if (!to) return `映射「${from}」的目标名为空，请填写后再提交`
    if (direct.has(from)) return `旧名「${from}」配置了多条映射，请只保留一条`
    direct.set(from, to)
  }
  // walk every chain to detect cycles (a self mapping is a cycle too)
  for (const source of direct.keys()) {
    const path = [source]
    const seen = new Set(path)
    let cur = source
    while (direct.has(cur)) {
      const nxt = direct.get(cur)
      if (seen.has(nxt)) {
        const loop = path.slice(path.indexOf(nxt)).concat(nxt)
        return `检测到循环映射：${loop.join(' → ')}，请先解除循环`
      }
      path.push(nxt)
      seen.add(nxt)
      cur = nxt
    }
  }
  return ''
}

export default function SpeakersPage() {
  const { id: projectId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState(null)
  const [rows, setRows] = useState([]) // [{from, to}]
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getProject(projectId).then((p) => {
      setProject(p)
    }).catch((e) => setError(e.message))
  }, [projectId])

  const validationError = useMemo(() => validateMappings(rows), [rows])
  const completeRows = useMemo(
    () => rows.filter((r) => (r.from || '').trim() && (r.to || '').trim()),
    [rows])

  // Re-run the server-side preview (debounced) once the local mapping set is
  // valid; while the user is mid-edit with an invalid half-row we keep the
  // last good preview and surface the validation message instead.
  useEffect(() => {
    if (!project || validationError) return undefined
    const payload = rows
      .filter((r) => (r.from || '').trim() && (r.to || '').trim())
    const timer = setTimeout(() => {
      setError('')
      setLoading(true)
      api.previewSpeakerRename(projectId, payload)
        .then(setPreview)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false))
    }, 350)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, project, rows, validationError])

  const existingNames = preview?.names
    || (project ? aggregateLocally(project.document) : [])

  const addRow = (from = '', to = '') => {
    setRows((rs) => {
      if (from && rs.some((r) => (r.from || '').trim() === from.trim())) {
        return rs
      }
      return [...rs, { from, to }]
    })
  }
  const patchRow = (i, patch) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const removeRow = (i) => setRows((rs) => rs.filter((_, j) => j !== i))

  const confirm = async () => {
    if (validationError) { setError(validationError); return }
    if (!preview || preview.summary.renamed === 0) return
    const s = preview.summary
    if (!window.confirm(
      `确定归一 ${s.renamed} 个说话人名称？\n` +
      `将更新：转写片段 ${s.segments} 条 · 校注 ${s.annotations} 条 · ` +
      `规则 ${s.rules} 条。\n此操作在一次事务中完成，记录 ID 与其他内容不变。`
    )) return
    setBusy(true); setError('')
    try {
      const res = await api.renameSpeakers(projectId, completeRows)
      navigate(`/projects/${projectId}`, {
        state: { speakerRename: res.counts },
      })
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  if (!project && !error) return <div className="page">加载中…</div>

  const s = preview?.summary
  const canCommit = !!preview && !validationError && s?.renamed > 0 && !busy

  return (
    <div className="page speakers-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>说话人归一 · {project?.name}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {validationError && <div className="error">{validationError}</div>}

      <div className="panel sp-names">
        <h3>
          现有名称（{existingNames.length}）
          <span className="muted small sp-panel-hint">
            来自已保存的转写片段、校注与规则的精确说话人条件；
            纯空白说话人视为未填写，不参与归一。
          </span>
        </h3>
        {existingNames.length === 0 ? (
          <div className="empty-hint">
            当前项目还没有任何说话人名称：在片段或校注中填写说话人后即可归一。
          </div>
        ) : (
          <table className="run-table sp-name-table">
            <thead>
              <tr>
                <th>名称</th><th>转写片段</th><th>校注</th>
                <th>规则条件</th><th>合计</th><th></th>
              </tr>
            </thead>
            <tbody>
              {existingNames.map((n) => (
                <tr key={n.name} className={n.changed ? 'sp-will-rename' : ''}>
                  <td><strong>{n.name}</strong></td>
                  <td>{n.segments}</td>
                  <td>{n.annotations}</td>
                  <td>{n.rules}</td>
                  <td>{n.total}</td>
                  <td>
                    <button className="btn small" onClick={() => addRow(n.name)}>
                      ＋ 归一此名
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel sp-mappings">
        <h3>
          归一映射
          <span className="muted small sp-panel-hint">
            多条旧名可指向同一目标名（归并）；链式映射（A→B、B→C）会自动
            归并到最终名称 C。空目标与循环映射无法提交。
          </span>
        </h3>
        <div className="sp-map-list">
          {rows.length === 0 && (
            <div className="muted small sp-map-empty">
              尚无映射，点击下方按钮或名称行的「＋ 归一此名」开始配置。
            </div>
          )}
          {rows.map((row, i) => (
            <MappingRow
              key={i}
              row={row}
              names={existingNames}
              onChange={(patch) => patchRow(i, patch)}
              onRemove={() => removeRow(i)}
            />
          ))}
        </div>
        <div className="sp-map-actions">
          <button className="btn" onClick={() => addRow()}>＋ 添加映射</button>
        </div>
        {preview && preview.mappings.some((m) => m.chain.length > 2) && (
          <ChainPreview preview={preview} />
        )}
      </div>

      <div className="panel sp-preview">
        <h3>
          确认前预览：每个名称的最终去向
          {loading && <span className="muted small sp-panel-hint">更新中…</span>}
        </h3>
        {preview ? (
          <>
            <div className="stats-grid">
              <div className="stat-box">
                <strong>现有名称</strong><span>{s.speakers}</span>
              </div>
              <div className={`stat-box ${s.renamed ? 'has-conflict' : ''}`}>
                <strong>将更名</strong><span>{s.renamed}</span>
              </div>
              <div className="stat-box">
                <strong>受影响片段</strong><span>{s.segments}</span>
              </div>
              <div className="stat-box">
                <strong>受影响校注</strong><span>{s.annotations}</span>
              </div>
              <div className="stat-box">
                <strong>受影响规则</strong><span>{s.rules}</span>
              </div>
            </div>
            <table className="run-table sp-preview-table">
              <thead>
                <tr>
                  <th>名称</th><th>最终去向</th>
                  <th>转写片段</th><th>校注</th><th>规则条件</th>
                </tr>
              </thead>
              <tbody>
                {preview.names.map((n) => (
                  <tr key={n.name} className={n.changed ? 'sp-will-rename' : ''}>
                    <td>{n.name}</td>
                    <td>
                      {n.changed ? (
                        <span className="sp-final">
                          <span className="sp-from">{n.name}</span>
                          <span className="sp-arrow">→</span>
                          <strong>{n.final}</strong>
                        </span>
                      ) : (
                        <span className="muted small">（保持不变）</span>
                      )}
                    </td>
                    <td>{n.changed ? n.segments : '—'}</td>
                    <td>{n.changed ? n.annotations : '—'}</td>
                    <td>{n.changed ? n.rules : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="sp-confirm-row">
              <button className="btn primary big" disabled={!canCommit} onClick={confirm}>
                {busy ? '提交中…' : `✅ 确认归一（${s.renamed} 个名称）`}
              </button>
              <span className="muted small">
                {s.renamed === 0
                  ? '当前映射不会改动任何名称（可继续添加映射）。'
                  : '确认后将在一次事务中更新片段、校注与规则三处说话人字段，'
                    + '随后返回编辑页重新读取项目。'}
              </span>
            </div>
          </>
        ) : (
          <div className="empty-hint">
            {validationError ? '修正上方映射后显示预览。' : '正在生成预览…'}
          </div>
        )}
      </div>
    </div>
  )
}

function MappingRow({ row, names, onChange, onRemove }) {
  const options = names.map((n) => n.name)
  return (
    <div className="sp-map-row">
      <input
        list="sp-name-list"
        value={row.from}
        placeholder="旧名（精确选择）"
        onChange={(e) => onChange({ from: e.target.value })}
      />
      <span className="sp-arrow">→</span>
      <input
        list="sp-name-list"
        value={row.to}
        placeholder="目标名"
        onChange={(e) => onChange({ to: e.target.value })}
      />
      <datalist id="sp-name-list">
        {options.map((n) => <option key={n} value={n} />)}
      </datalist>
      <button className="btn icon danger" onClick={onRemove} title="删除此映射">
        ✕
      </button>
    </div>
  )
}

function ChainPreview({ preview }) {
  const chains = preview.mappings.filter((m) => m.chain.length > 2)
  return (
    <div className="sp-chains">
      {chains.map((m) => (
        <div key={m.from} className="sp-chain-tag" title="链式映射将归并到最终名称">
          {m.chain.map((n, i) => (
            <span key={i}>
              {i > 0 && <span className="sp-arrow">→</span>}
              <strong>{n}</strong>
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}

/** Local fallback used for the name list before the first preview arrives. */
function aggregateLocally(document) {
  const counts = new Map()
  const bump = (raw, key) => {
    const name = (raw || '').trim()
    if (!name) return
    const e = counts.get(name)
      || { name, segments: 0, annotations: 0, rules: 0, total: 0 }
    e[key] += 1
    e.total += 1
    counts.set(name, e)
  }
  ;(document.segments || []).forEach((s) => bump(s.speaker, 'segments'))
  ;(document.annotations || []).forEach((a) => bump(a.speaker, 'annotations'))
  return [...counts.values()].sort((a, b) => b.total - a.total
    || (a.name < b.name ? -1 : 1))
}
