import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import ConflictCard from '../components/ConflictCard.jsx'
import { downloadJson, readFileText } from '../utils.js'

async function readDocFile(file) {
  const text = await readFileText(file)
  let data
  try {
    data = JSON.parse(text)
  } catch {
    // plain text transcript -> send to backend importer
    const doc = await api.importTranscript({ text })
    return { doc, label: file.name }
  }
  if (data && typeof data === 'object' && data.document && data.document.segments) {
    return { doc: data.document, label: file.name, meta: data }
  }
  if (data && Array.isArray(data.segments)) {
    return { doc: data, label: file.name }
  }
  throw new Error(`${file.name} 不是有效的校注文档（缺少 segments）`)
}

const STAT_LABELS = {
  segments: '转写片段',
  annotations: '校注',
}

export default function MergePage() {
  const [projects, setProjects] = useState([])
  const [baseProjectId, setBaseProjectId] = useState('')
  const [baseDoc, setBaseDoc] = useState(null)
  const [baseLabel, setBaseLabel] = useState('')
  const [a, setA] = useState(null)
  const [b, setB] = useState(null)
  const [result, setResult] = useState(null)
  const [resolutions, setResolutions] = useState({})
  const [saveName, setSaveName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => {})
  }, [])

  const pickBaseProject = async (pid) => {
    setBaseProjectId(pid)
    setResult(null)
    if (!pid) { setBaseDoc(null); setBaseLabel(''); return }
    const p = projects.find((x) => x.id === pid)
    if (p) {
      setBaseDoc(p.document)
      setBaseLabel(`项目：${p.name}`)
    } else {
      const fresh = await api.getProject(pid)
      setBaseDoc(fresh.document)
      setBaseLabel(`项目：${fresh.name}`)
    }
  }

  const loadFile = async (file, side) => {
    setError('')
    try {
      const { doc, label } = await readDocFile(file)
      if (side === 'base') {
        setBaseDoc(doc); setBaseLabel(`文件：${label}`)
        setBaseProjectId('')
      } else if (side === 'a') {
        setA({ doc, label })
      } else {
        setB({ doc, label })
      }
      setResult(null)
    } catch (e) {
      setError(e.message)
    }
  }

  const canMerge = baseDoc && a?.doc && b?.doc

  const runMerge = async (extraResolutions = undefined, persist = false) => {
    if (!canMerge) return
    setBusy(true); setError('')
    try {
      const payload = {
        base: baseDoc,
        a: a.doc,
        b: b.doc,
        resolutions: extraResolutions === undefined ? resolutions : extraResolutions,
      }
      // only the explicit "save as" button persists; analysis / live
      // re-adjudication must not create projects as a side effect.
      if (persist && saveName.trim()) payload.saveAsProject = saveName.trim()
      const r = await api.merge(payload)
      setResult(r)
      if (r.savedProject) {
        setSaveName('')
        api.listProjects().then(setProjects)
      }
      return r
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const applyResolutions = async (next) => {
    setResolutions(next)
    await runMerge(next)
  }

  const setResolution = (conflictId, res) => {
    applyResolutions({ ...resolutions, [conflictId]: res })
  }

  const bulkChoose = (choice) => {
    const next = { ...resolutions }
    for (const c of result.conflicts) {
      if (!next[c.id]) next[c.id] = { choice }
    }
    applyResolutions(next)
  }

  const resolvedCount = result
    ? result.conflicts.filter((c) => resolutions[c.id]).length
    : 0
  const allResolved = result && resolvedCount === result.conflicts.length

  const exportResult = () => {
    if (!result) return
    const payload = {
      _mergedResult: true,
      mergedAt: new Date().toISOString(),
      base: baseLabel,
      a: a.label,
      b: b.label,
      unresolvedConflicts: result.unresolved,
      document: result.document,
    }
    downloadJson('merged-annotations.json', payload)
  }

  const writeBack = async () => {
    if (!baseProjectId || !allResolved) return
    setBusy(true); setError('')
    try {
      const r = await api.merge({
        projectId: baseProjectId,
        a: a.doc,
        b: b.doc,
        resolutions,
      })
      setResult(r)
      setError('')
      alert(`已写回项目（${r.savedProject?.name || ''}）`)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page merge-page">
      <div className="page-head">
        <h2>三方合并（离线副本 A / B 对共同基准）</h2>
      </div>
      {error && <div className="error">{error}</div>}

      <div className="merge-sources">
        <SourceCard
          title="① 共同基准（Base）"
          badge={baseLabel}
        >
          <select
            value={baseProjectId}
            onChange={(e) => pickBaseProject(e.target.value)}
          >
            <option value="">— 选择一个项目作为基准 —</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <FilePick onFile={(f) => loadFile(f, 'base')} />
        </SourceCard>
        <SourceCard title="② 离线副本 A" badge={a?.label}>
          <FilePick onFile={(f) => loadFile(f, 'a')} />
          {a && <DocSummary doc={a.doc} />}
        </SourceCard>
        <SourceCard title="③ 离线副本 B" badge={b?.label}>
          <FilePick onFile={(f) => loadFile(f, 'b')} />
          {b && <DocSummary doc={b.doc} />}
        </SourceCard>
      </div>

      <div className="merge-run">
        <button
          className="btn primary big"
          disabled={!canMerge || busy}
          onClick={() => { setResolutions({}); runMerge({}) }}
        >
          {busy ? '合并中…' : '开始三方合并'}
        </button>
        {!canMerge && (
          <span className="muted small">
            需要先指定基准以及 A、B 两份文档（项目或导出的离线副本 JSON /
            纯文本转写稿）
          </span>
        )}
      </div>

      {result && (
        <MergeResult
          result={result}
          resolutions={resolutions}
          resolvedCount={resolvedCount}
          allResolved={!!allResolved}
          setResolution={setResolution}
          exportResult={exportResult}
          saveName={saveName}
          setSaveName={setSaveName}
          runMerge={(persist) => runMerge(undefined, persist)}
          bulkChoose={bulkChoose}
          canWriteBack={!!baseProjectId}
          writeBack={writeBack}
          busy={busy}
        />
      )}
    </div>
  )
}

function SourceCard({ title, badge, children }) {
  return (
    <div className="card source-card">
      <h4>{title}</h4>
      {badge && <div className="muted small source-badge">{badge}</div>}
      {children}
    </div>
  )
}

function FilePick({ onFile }) {
  return (
    <label className="btn small file-pick">
      选择文件…
      <input
        type="file" hidden
        accept=".json,.txt,.srt,.vtt"
        onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
      />
    </label>
  )
}

function DocSummary({ doc }) {
  return (
    <div className="muted small">
      ✓ {doc.segments.length} 片段 · {doc.annotations.length} 校注
    </div>
  )
}

function MergeResult({
  result, resolutions, resolvedCount, allResolved, setResolution,
  exportResult, saveName, setSaveName, runMerge, bulkChoose,
  canWriteBack, writeBack, busy,
}) {
  const { stats, conflicts, changes, document: merged } = result

  const autoGroups = useMemo(() => {
    const groups = {}
    for (const ch of changes) {
      if (ch.action === 'unchanged' || ch.action === 'conflict') continue
      const key = `${ch.kind}.${ch.action}`
      ;(groups[key] ||= []).push(ch)
    }
    return groups
  }, [changes])

  return (
    <div className="merge-result">
      <div className="panel stats-panel">
        <h3>合并结果概览</h3>
        <div className="stats-grid">
          {['segments', 'annotations'].map((kind) => (
            <div key={kind} className="stat-box">
              <strong>{STAT_LABELS[kind]}</strong>
              <span>新增 {stats[kind].added}</span>
              <span>修改 {stats[kind].modified}</span>
              <span>删除 {stats[kind].deleted}</span>
            </div>
          ))}
          <div className={`stat-box ${conflicts.length ? 'has-conflict' : ''}`}>
            <strong>冲突</strong>
            <span>{conflicts.length} 项</span>
            <span>已裁决 {resolvedCount}</span>
            <span>{allResolved ? '✓ 全部解决' : `待裁决 ${conflicts.length - resolvedCount}`}</span>
          </div>
        </div>
      </div>

      <details className="panel auto-changes" open={conflicts.length === 0}>
        <summary>
          自动合并的互不重叠改动（
          {Object.values(autoGroups).reduce((n, g) => n + g.length, 0)} 项）
        </summary>
        <div className="change-groups">
          {Object.entries(autoGroups).map(([key, items]) => (
            <div key={key} className="change-group">
              <strong>{changeLabel(key)}（{items.length}）</strong>
              <div className="change-items">
                {items.slice(0, 30).map((ch) => (
                  <span key={`${ch.kind}-${ch.itemId}-${ch.action}`} className="tag">
                    {ch.kind === 'segments' ? '片段' : '校注'} {shortId(ch.itemId)}
                    {ch.side ? ` · 来自${ch.side.toUpperCase()}` : ''}
                  </span>
                ))}
                {items.length > 30 && <span className="muted">…</span>}
              </div>
            </div>
          ))}
          {Object.keys(autoGroups).length === 0 && (
            <span className="muted small">无自动改动</span>
          )}
        </div>
      </details>

      {conflicts.length > 0 && (
        <div className="conflicts-section">
          <h3>
            需要逐项裁决的冲突（{resolvedCount}/{conflicts.length}）
          </h3>
          <div className="conflict-bulk">
            <button className="btn small" onClick={() => bulkChoose('a')}>
              未裁决项全部取 A
            </button>
            <button className="btn small" onClick={() => bulkChoose('b')}>
              未裁决项全部取 B
            </button>
          </div>
          {conflicts.map((c, i) => (
            <ConflictCard
              key={c.id}
              conflict={c}
              index={i}
              value={resolutions[c.id] || null}
              onChange={(res) => setResolution(c.id, res)}
            />
          ))}
        </div>
      )}

      {result.errors?.length > 0 && (
        <div className="error">
          裁决有误：
          <ul>
            {result.errors.map((e) => (
              <li key={e.conflictId}>{e.conflictId}: {e.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="panel final-doc">
        <h3>
          合并后的文档（{merged.segments.length} 片段 ·{' '}
          {merged.annotations.length} 校注）
        </h3>
        {!allResolved && (
          <div className="warn-box">
            尚有 {conflicts.length - resolvedCount} 项冲突未裁决；
            这些条目当前不会出现在合并文档中。
          </div>
        )}
        <div className="final-actions">
          <button className="btn" onClick={exportResult}>
            导出裁决结果 JSON
          </button>
          <div className="save-as">
            <input
              placeholder="另存为新项目名称"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
            />
            <button
              className="btn primary"
              disabled={!allResolved || !saveName.trim() || busy}
              onClick={() => runMerge(true)}
            >
              全部裁决后另存为新项目
            </button>
          </div>
          {canWriteBack && (
            <button
              className="btn"
              disabled={!allResolved || busy}
              onClick={writeBack}
              title="以合并结果覆盖基准项目的当前文档"
            >
              全部裁决后写回基准项目
            </button>
          )}
        </div>
        {result.savedProject && (
          <div className="ok">
            已保存为项目：
            <Link to={`/projects/${result.savedProject.id}`}>
              {result.savedProject.name}
            </Link>
          </div>
        )}
        <details>
          <summary>预览合并文档 JSON</summary>
          <pre className="json-preview">
            {JSON.stringify(merged, null, 2)}
          </pre>
        </details>
      </div>
    </div>
  )
}

function changeLabel(key) {
  const [kind, action] = key.split('.')
  const k = kind === 'segments' ? '片段' : '校注'
  const a = { added: '新增', modified: '修改', deleted: '删除' }[action]
  return `${k}${a}`
}

function shortId(id) {
  if (!id) return ''
  return id.length > 16 ? id.slice(-8) : id
}
