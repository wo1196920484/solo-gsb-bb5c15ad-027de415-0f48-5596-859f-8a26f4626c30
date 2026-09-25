import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { downloadJson, readFileText } from '../utils.js'

export default function ProjectListPage() {
  const [projects, setProjects] = useState([])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fileRef = useRef(null)

  const refresh = () => api.listProjects().then(setProjects).catch(setError)
  useEffect(() => { refresh() }, [])

  const create = async () => {
    const n = name.trim()
    if (!n) return
    setBusy(true); setError('')
    try {
      await api.createProject(n)
      setName('')
      await refresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (p) => {
    if (!window.confirm(`确定删除项目「${p.name}」？此操作不可恢复。`)) return
    await api.deleteProject(p.id)
    refresh()
  }

  const createFromFile = async (file) => {
    setBusy(true); setError('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const doc = await api.importTranscript(fd)
      const baseName = file.name.replace(/\.[^.]+$/, '')
      const p = await api.createProject(baseName || '导入的项目')
      await api.saveProject(p.id, {
        document: doc, documentVersion: p.documentVersion,
      })
      await refresh()
    } catch (e) {
      setError(`导入失败：${e.message}`)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const loadJson = async (file) => {
    setBusy(true); setError('')
    try {
      const doc = JSON.parse(await readFileText(file))
      if (!doc.segments) throw new Error('JSON 中缺少 segments')
      const p = await api.createProject(file.name.replace(/\.[^.]+$/, ''))
      await api.saveProject(p.id, {
        document: doc, documentVersion: p.documentVersion,
      })
      await refresh()
    } catch (e) {
      setError(`载入 JSON 失败：${e.message}`)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const onFile = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.name.endsWith('.json')) loadJson(file)
    else createFromFile(file)
  }

  return (
    <div className="page">
      <div className="page-head">
        <h2>项目</h2>
        <div className="head-actions">
          <input
            placeholder="新项目名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && create()}
          />
          <button className="btn primary" disabled={busy} onClick={create}>
            新建空项目
          </button>
          <button className="btn" disabled={busy} onClick={() => fileRef.current?.click()}>
            导入转写稿新建…
          </button>
          <input
            ref={fileRef} type="file" hidden
            accept=".json,.txt,.srt,.vtt"
            onChange={onFile}
          />
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <p className="muted">
        支持带时间戳的纯文本（如
        <code>[00:00:01] 张三: 你好</code>）、JSON 片段数组，或此前导出的校注 JSON。
      </p>

      <div className="card-list">
        {projects.map((p) => (
          <div className="card project-card" key={p.id}>
            <div className="card-body">
              <Link to={`/projects/${p.id}`} className="card-title">
                {p.name}
              </Link>
              <div className="muted small">
                {p.document.segments.length} 片段 ·{' '}
                {p.document.annotations.length} 校注 · 更新于{' '}
                {new Date(p.updatedAt).toLocaleString()}
              </div>
            </div>
            <div className="card-actions">
              <Link className="btn small" to={`/projects/${p.id}`}>打开</Link>
              <Link className="btn small" to={`/projects/${p.id}/speakers`}>
                说话人归一
              </Link>
              <Link className="btn small" to={`/projects/${p.id}/rules`}>
                规则标注
              </Link>
              <Link className="btn small" to={`/projects/${p.id}/coverage`}>
                覆盖盲区
              </Link>
              <button
                className="btn small"
                onClick={() => downloadJson(
                  `${p.name}-annotations.json`, p.document)}
              >
                导出 JSON
              </button>
              <button className="btn small danger" onClick={() => remove(p)}>
                删除
              </button>
            </div>
          </div>
        ))}
        {projects.length === 0 && (
          <div className="empty-hint">还没有项目，先新建一个或导入转写稿。</div>
        )}
      </div>
    </div>
  )
}
