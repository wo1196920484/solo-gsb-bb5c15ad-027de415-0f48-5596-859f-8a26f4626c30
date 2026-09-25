import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { fmtTime } from '../utils.js'

const PALETTE = [
  '#5b8def', '#e36b6b', '#4cae8f', '#c98b3a', '#8e6bc0',
  '#3d9fb8', '#d17aa2', '#7b9b3f',
]

export function emptyRule() {
  return {
    name: '',
    speaker: '',
    pattern: '',
    matchType: 'keyword',
    caseSensitive: false,
    minDuration: 0,
    topic: '',
    noteTemplate: '',
    color: PALETTE[0],
  }
}

export function normalizeRule(rule) {
  return {
    name: rule.name || '',
    speaker: rule.speaker || '',
    pattern: rule.pattern || '',
    matchType: rule.matchType || 'keyword',
    caseSensitive: !!rule.caseSensitive,
    minDuration: Number(rule.minDuration || 0),
    topic: rule.topic || '',
    noteTemplate: rule.noteTemplate || '',
    color: rule.color || PALETTE[0],
  }
}

export default function RulesPage() {
  const { id: projectId } = useParams()
  const [project, setProject] = useState(null)
  const [rules, setRules] = useState([])
  const [runs, setRuns] = useState([])
  const [editingId, setEditingId] = useState(null) // rule id | 'new' | null
  const [form, setForm] = useState(emptyRule())
  const [preview, setPreview] = useState(null) // { rule, data }
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')

  const loadProject = () =>
    api.getProject(projectId).then(setProject).catch((e) => setError(e.message))
  const loadRules = () =>
    api.listRules(projectId).then(setRules).catch(() => {})
  const loadRuns = () =>
    api.listRuleRuns(projectId).then(setRuns).catch(() => {})

  useEffect(() => {
    loadProject(); loadRules(); loadRuns()
  }, [projectId])

  const flash = (msg) => {
    setOk(msg)
    setTimeout(() => setOk(''), 3500)
  }

  const refreshAll = async () => {
    await Promise.all([loadRules(), loadRuns()])
  }

  const startNew = () => {
    setEditingId('new')
    setForm(emptyRule())
    setPreview(null)
    setError('')
  }

  const startEdit = (rule) => {
    setEditingId(rule.id)
    setForm(normalizeRule(rule))
    setPreview(null)
    setError('')
  }

  const cancelEdit = () => {
    setEditingId(null)
    setPreview(null)
  }

  const setField = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  const validate = (data) => {
    if (!data.name.trim()) return '请填写规则名称'
    if (data.matchType !== 'none' && !data.pattern.trim())
      return '关键词 / 正则不能为空（或选择「不限制文本」）'
    if (!Number.isFinite(Number(data.minDuration)) ||
        Number(data.minDuration) < 0)
      return '最短持续时间必须是非负数字'
    return ''
  }

  const openPreview = async (rule) => {
    setError('')
    setBusy(true)
    try {
      const data = await api.previewRule(rule.id)
      setPreview({ rule, data })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const saveRule = async () => {
    const err = validate(form)
    if (err) { setError(err); return null }
    setBusy(true); setError('')
    try {
      const saved = editingId === 'new'
        ? await api.createRule(projectId, form)
        : await api.updateRule(editingId, form)
      await loadRules()
      setEditingId(null)
      setPreview(null)
      flash('规则已保存')
      return saved
    } catch (e) {
      setError(e.message)
      return null
    } finally {
      setBusy(false)
    }
  }

  const removeRule = async (rule) => {
    if (!window.confirm(
      `确定删除规则「${rule.name}」？\n` +
      '该规则已生成的校注会转为普通手工校注（不会被删除）。'
    )) return
    setError('')
    try {
      await api.deleteRule(rule.id)
      if (editingId === rule.id) { setEditingId(null); setPreview(null) }
      if (preview?.rule.id === rule.id) setPreview(null)
      await Promise.all([refreshAll(), loadProject()])
    } catch (e) {
      setError(e.message)
    }
  }

  const applyRule = async (rule) => {
    setBusy(true); setError('')
    try {
      const res = await api.applyRule(rule.id)
      setProject((p) => ({ ...p, document: res.document }))
      await refreshAll()
      const r = res.run
      flash(`已应用「${rule.name}」：新建 ${r.createdCount} · 更新 ` +
            `${r.updatedCount} · 移除 ${r.removedCount} · 保留手工 ` +
            `${r.keptManualCount}`)
      return res
    } catch (e) {
      setError(e.message)
      return null
    } finally {
      setBusy(false)
    }
  }

  const undoRun = async (run) => {
    if (!window.confirm('撤销该次规则应用？手工校注不会受影响。')) return
    setBusy(true); setError('')
    try {
      const res = await api.undoRuleRun(run.id)
      setProject((p) => ({ ...p, document: res.document }))
      await refreshAll()
      setPreview(null)
      flash('已撤销该次应用')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!project && !error) return <div className="page">加载中…</div>

  const speakers = (project?.document?.segments || [])
    .map((s) => s.speaker).filter(Boolean)
  const ruleNameById = Object.fromEntries(rules.map((r) => [r.id, r.name]))

  return (
    <div className="page rules-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>规则批量标注 · {project?.name}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
          <a className="btn" href={api.exportUrl(projectId)} download>
            导出 JSON
          </a>
          {editingId ? (
            <button className="btn" onClick={cancelEdit}>取消编辑</button>
          ) : (
            <button className="btn primary" onClick={startNew}>
              ＋ 新建规则
            </button>
          )}
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {ok && <div className="ok">{ok}</div>}

      {editingId ? (
        <RuleEditor
          form={form}
          set={setField}
          isNew={editingId === 'new'}
          speakers={[...new Set(speakers)]}
          project={project}
          busy={busy}
          onValidate={() => validate(form)}
          onSave={saveRule}
          onApply={async () => {
            const stored = rules.find((r) => r.id === editingId)
            const isNew = editingId === 'new'
            const dirty = stored &&
              JSON.stringify(normalizeRule(stored)) !==
              JSON.stringify(normalizeRule(form))
            let rule = stored
            if (isNew || dirty) rule = await saveRule()
            if (rule) await applyRule(rule)
          }}
        />
      ) : (
        <RuleList
          rules={rules}
          busy={busy}
          onEdit={startEdit}
          onDelete={removeRule}
          onPreview={openPreview}
          onApply={applyRule}
        />
      )}

      {!editingId && preview && (
        <PreviewPanel
          project={project}
          preview={preview}
          onClose={() => setPreview(null)}
          onEdit={() => startEdit(preview.rule)}
          onApply={() => applyRule(preview.rule)}
          busy={busy}
        />
      )}

      <RunHistory
        runs={runs}
        ruleNameById={ruleNameById}
        busy={busy}
        onUndo={undoRun}
      />
    </div>
  )
}

function RuleEditor({
  form, set, isNew, speakers, project, busy, onValidate, onSave, onApply,
}) {
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)
  const [perror, setPerror] = useState('')

  const doPreview = async () => {
    const err = onValidate()
    if (err) { setPerror(err); return }
    setPreviewing(true); setPerror('')
    try {
      // drafts (new or unsaved edits) are previewed without persisting
      const data = await api.previewDraftRule(project.id, form)
      setPreview(data)
    } catch (e) {
      setPerror(e.message)
    } finally {
      setPreviewing(false)
    }
  }

  return (
    <div className="panel rule-editor">
      <h3>{isNew ? '新建规则' : '编辑规则'}</h3>
      <div className="rule-form-grid">
        <label>
          规则名称 *
          <input
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="例如：受访者谈改制"
          />
        </label>
        <label>
          说话人（留空 = 不限）
          <input
            list="rule-speaker-list"
            value={form.speaker}
            onChange={(e) => set('speaker', e.target.value)}
            placeholder="精确匹配片段说话人"
          />
          <datalist id="rule-speaker-list">
            {speakers.map((s) => <option key={s} value={s} />)}
          </datalist>
        </label>
        <label>
          文本匹配方式
          <select
            value={form.matchType}
            onChange={(e) => set('matchType', e.target.value)}
          >
            <option value="keyword">关键词（包含）</option>
            <option value="regex">正则表达式</option>
            <option value="none">不限制文本</option>
          </select>
        </label>
        <label>
          {form.matchType === 'regex' ? '正则表达式'
            : form.matchType === 'none' ? '关键词（已禁用）' : '关键词'}
          <input
            value={form.pattern}
            disabled={form.matchType === 'none'}
            onChange={(e) => set('pattern', e.target.value)}
            placeholder={form.matchType === 'regex'
              ? '例如：改制|下岗|分流' : '例如：改制'}
          />
        </label>
        <label className="rule-check">
          <input
            type="checkbox"
            checked={form.caseSensitive}
            onChange={(e) => set('caseSensitive', e.target.checked)}
          />
          区分大小写
        </label>
        <label>
          最短持续时间（秒）
          <input
            type="number" min="0" step="0.1"
            value={form.minDuration}
            onChange={(e) => set('minDuration', Number(e.target.value))}
          />
        </label>
        <label className="wide">
          生成校注的主题
          <input
            value={form.topic}
            onChange={(e) => set('topic', e.target.value)}
            placeholder="例如：企业改制"
          />
        </label>
        <label className="wide">
          备注模板
          <textarea
            rows={3}
            value={form.noteTemplate}
            onChange={(e) => set('noteTemplate', e.target.value)}
            placeholder="可用占位符：{speaker} {keyword} {text} {segments}（命中片段数）"
          />
        </label>
      </div>
      <div className="template-hint muted small">
        占位符：<code>{'{speaker}'}</code> 说话人 ·{' '}
        <code>{'{keyword}'}</code> 匹配词 ·{' '}
        <code>{'{text}'}</code> 命中片段文本 ·{' '}
        <code>{'{segments}'}</code> 合并片段数。相邻命中片段会自动合并为一个区间。
      </div>
      <div className="color-row">
        {PALETTE.map((c) => (
          <button
            key={c}
            type="button"
            className={`swatch ${form.color === c ? 'active' : ''}`}
            style={{ background: c }}
            onClick={() => set('color', c)}
            aria-label={`颜色 ${c}`}
          />
        ))}
      </div>
      <div className="rule-editor-actions">
        <button className="btn" disabled={previewing} onClick={doPreview}>
          {previewing ? '预览中…' : '🔍 预览命中'}
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn primary" disabled={busy} onClick={onSave}>
          保存规则
        </button>
        <button className="btn" disabled={busy} onClick={onApply}>
          保存并批量应用
        </button>
      </div>
      {perror && <div className="error">{perror}</div>}
      {preview && (
        <PreviewBody project={project} preview={preview} color={form.color} />
      )}
    </div>
  )
}

function RuleList({ rules, busy, onEdit, onDelete, onPreview, onApply }) {
  if (rules.length === 0) {
    return (
      <div className="panel empty-hint">
        还没有规则。点击「＋ 新建规则」，按说话人、关键词 / 正则、最短持续时间
        批量生成校注；应用时只会写入规则自己的结果，手工校注永不覆盖。
      </div>
    )
  }
  return (
    <div className="rule-cards">
      {rules.map((r) => (
        <div className="card rule-card" key={r.id}
          style={{ borderLeftColor: r.color }}>
          <div className="rule-card-body">
            <div className="rule-card-head">
              <strong>{r.name}</strong>
              {r.lastRun?.status === 'applied' && (
                <span className="badge ok-text">已应用</span>
              )}
              {r.lastRun?.status === 'superseded' && (
                <span className="badge warn-text">已被新应用取代</span>
              )}
            </div>
            <div className="muted small rule-cond">
              {r.speaker ? `说话人 = ${r.speaker}` : '说话人不限'}
              {' · '}
              {r.matchType === 'none'
                ? '文本不限'
                : `${r.matchType === 'regex' ? '正则' : '关键词'}「${r.pattern}」`
                  + (r.caseSensitive ? ' · 区分大小写' : '')}
              {r.minDuration > 0 && ` · ≥ ${r.minDuration}s`}
            </div>
            <div className="muted small">
              生成主题：{r.topic || '（空）'}
            </div>
            {r.lastRun && (
              <div className="muted small">
                上次{r.lastRun.status === 'applied' ? '应用' : '运行'}：
                新建 {r.lastRun.createdCount} · 更新 {r.lastRun.updatedCount}
                {' · '}{new Date(r.lastRun.createdAt).toLocaleString()}
              </div>
            )}
          </div>
          <div className="card-actions">
            <button className="btn small" disabled={busy}
              onClick={() => onPreview(r)}>
              预览
            </button>
            <button className="btn small" onClick={() => onEdit(r)}>
              编辑
            </button>
            <button className="btn small primary" disabled={busy}
              onClick={() => onApply(r)}>
              应用到时间轴
            </button>
            <button className="btn small danger" onClick={() => onDelete(r)}>
              删除
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

function PreviewPanel({ project, preview, onClose, onEdit, onApply, busy }) {
  return (
    <div className="panel preview-panel">
      <div className="preview-head">
        <h3>预览：{preview.rule.name}</h3>
        <div className="head-actions">
          <button className="btn small" onClick={onEdit}>编辑规则</button>
          <button
            className="btn small primary"
            disabled={busy}
            onClick={onApply}
          >
            确认批量写入
          </button>
          <button className="btn icon" onClick={onClose}>✕</button>
        </div>
      </div>
      <PreviewBody
        project={project}
        preview={preview.data}
        color={preview.rule.color}
      />
    </div>
  )
}

function PreviewBody({ preview, color, project }) {
  const s = preview.stats
  return (
    <div className="preview-body">
      <div className="stats-grid">
        <div className="stat-box">
          <strong>命中片段</strong><span>{s.matchedSegments}</span>
        </div>
        <div className="stat-box">
          <strong>拟生成区间</strong><span>{s.groups}</span>
        </div>
        <div className="stat-box">
          <strong>新建</strong><span>{s.willCreate}</span>
        </div>
        <div className="stat-box">
          <strong>更新本规则旧结果</strong><span>{s.willUpdate}</span>
        </div>
        <div className="stat-box">
          <strong>失效移除</strong><span>{s.willRemove}</span>
        </div>
        <div className={`stat-box ${s.keptManual ? 'has-conflict' : ''}`}>
          <strong>手工校注（保留）</strong><span>{s.keptManual}</span>
        </div>
        {s.suppressed > 0 && (
          <div className="stat-box">
            <strong>手工删除（不会复活）</strong><span>{s.suppressed}</span>
          </div>
        )}
      </div>
      {project && (
        <PreviewTimeline project={project} preview={preview} color={color} />
      )}
      {preview.hits.length === 0 ? (
        <div className="empty-hint">当前规则没有命中任何片段。</div>
      ) : (
        <div className="hit-list">
          {preview.hits.map((h) => (
            <div className="hit-item" key={h.index}>
              <div className="hit-range muted small">
                {fmtTime(h.annotation.start)} – {fmtTime(h.annotation.end)}
                {' · '}{h.segmentIds.length} 个片段
              </div>
              <div className="hit-text">
                {h.segments.map((seg) => (
                  <div key={seg.id} className="hit-seg">
                    <span className="muted small">
                      [{fmtTime(seg.start)}] {seg.speaker || '（无说话人）'}：
                    </span>{' '}
                    {seg.text}
                  </div>
                ))}
              </div>
              <div className="hit-ann" style={{ borderLeftColor: color }}>
                <strong>{h.annotation.topic || '（未命名主题）'}</strong>
                {h.annotation.note && (
                  <div className="muted small">{h.annotation.note}</div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PreviewTimeline({ project, preview, color }) {
  const segments = project.document.segments || []
  const annotations = project.document.annotations || []
  const total = Math.max(
    10,
    ...segments.map((x) => x.end || 0),
    ...annotations.map((x) => x.end || 0),
    ...preview.proposed.map((x) => x.end || 0),
  )
  const pct = (sec) => `${Math.max(0, (sec / total) * 100)}%`
  const width = (s, e) => `${Math.max(0.15, ((e - s) / total) * 100)}%`
  return (
    <div className="mini-timeline">
      <div className="mini-track">
        {segments.map((seg) => (
          <div
            key={seg.id}
            className="mini-seg"
            style={{ left: pct(seg.start), width: width(seg.start, seg.end) }}
            title={`${fmtTime(seg.start)} ${seg.speaker || ''} ${seg.text || ''}`}
          />
        ))}
        {annotations.map((a) => (
          <div
            key={a.id}
            className={`mini-ann ${a.source === 'rule'
              ? (preview.ruleId === a.ruleId ? 'rule-self' : 'rule')
              : 'manual'}`}
            style={{
              left: pct(a.start),
              width: width(a.start, a.end),
              background: a.source === 'rule'
                ? (preview.ruleId === a.ruleId ? (color || a.color) : '#9aa7bd')
                : '#e36b6b',
            }}
            title={a.topic || ''}
          />
        ))}
        {preview.proposed.map((a) => (
          <div
            key={`p-${a.id}`}
            className="mini-ann proposed"
            style={{
              left: pct(a.start),
              width: width(a.start, a.end),
              background: color || a.color,
            }}
            title={a.topic || ''}
          />
        ))}
      </div>
      <div className="mini-legend muted small">
        <span className="lg seg" /> 片段
        <span className="lg manual" /> 手工校注
        <span className="lg rule" /> 其他规则生成
        <span className="lg proposed" style={{ background: color }} />
        本规则拟生成 / 更新
      </div>
    </div>
  )
}

function RunHistory({ runs, ruleNameById, busy, onUndo }) {
  const applied = runs.filter((r) => r.status === 'applied')
  return (
    <div className="panel run-history">
      <h3>应用记录（可撤销本次应用）</h3>
      {applied.length === 0 ? (
        <div className="muted small">
          尚未应用过规则。应用后可在此一键撤销，仅回退该规则当次生成 / 更新的
          校注，手工标注不受影响。
        </div>
      ) : (
        <table className="run-table">
          <thead>
            <tr>
              <th>规则</th><th>时间</th><th>新建</th><th>更新</th>
              <th>移除</th><th>保留手工</th><th></th>
            </tr>
          </thead>
          <tbody>
            {applied.map((r) => (
              <tr key={r.id}>
                <td>{ruleNameById[r.ruleId] || '（规则已删除）'}</td>
                <td className="muted small">
                  {new Date(r.createdAt).toLocaleString()}
                </td>
                <td>{r.createdCount}</td>
                <td>{r.updatedCount}</td>
                <td>{r.removedCount}</td>
                <td>{r.keptManualCount}</td>
                <td>
                  <button className="btn small" disabled={busy}
                    onClick={() => onUndo(r)}>
                    撤销本次应用
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
