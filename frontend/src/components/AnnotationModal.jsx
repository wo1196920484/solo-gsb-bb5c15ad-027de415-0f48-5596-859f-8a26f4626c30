import { useEffect, useState } from 'react'
import { fmtTime } from '../utils.js'

const PALETTE = [
  '#5b8def', '#e36b6b', '#4cae8f', '#c98b3a', '#8e6bc0',
  '#3d9fb8', '#d17aa2', '#7b9b3f',
]

export default function AnnotationModal({ initial, speakers, onSave, onClose, onDelete }) {
  const [form, setForm] = useState(() => ({
    start: initial?.start ?? 0,
    end: initial?.end ?? 1,
    speaker: initial?.speaker || '',
    topic: initial?.topic || '',
    note: initial?.note || '',
    color: initial?.color || PALETTE[Math.floor(Math.random() * PALETTE.length)],
  }))
  const [error, setError] = useState('')

  useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  const submit = () => {
    const start = Number(form.start)
    const end = Number(form.end)
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      setError('时间必须是数字（秒）')
      return
    }
    if (end < start) {
      setError('结束时间不能早于开始时间')
      return
    }
    onSave({
      ...form,
      start: Math.round(start * 1000) / 1000,
      end: Math.round(end * 1000) / 1000,
      speaker: form.speaker.trim(),
      topic: form.topic.trim(),
      note: form.note,
    })
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <h3>{initial?.id ? '编辑校注' : '新建校注'}</h3>
        {initial?.source === 'rule' && (
          <div className="warn-box">
            此校注由规则「{initial.ruleName || '批量规则'}」生成。保存修改后将转为
            手工校注，再次运行该规则不会覆盖它。
          </div>
        )}
        <div className="range-readonly">
          区间 {fmtTime(Number(form.start))} – {fmtTime(Number(form.end))}
        </div>
        <label>
          开始（秒）
          <input
            type="number" step="0.001" min="0"
            value={form.start}
            onChange={(e) => set('start', e.target.value)}
          />
        </label>
        <label>
          结束（秒）
          <input
            type="number" step="0.001" min="0"
            value={form.end}
            onChange={(e) => set('end', e.target.value)}
          />
        </label>
        <label>
          说话人
          <input
            list="speaker-list"
            value={form.speaker}
            onChange={(e) => set('speaker', e.target.value)}
            placeholder="例如：张三 / 访谈者"
          />
          <datalist id="speaker-list">
            {speakers.map((s) => <option key={s} value={s} />)}
          </datalist>
        </label>
        <label>
          主题
          <input
            value={form.topic}
            onChange={(e) => set('topic', e.target.value)}
            placeholder="例如：童年经历 / 项目背景"
          />
        </label>
        <label>
          备注
          <textarea
            rows={4}
            value={form.note}
            onChange={(e) => set('note', e.target.value)}
            placeholder="对该区间的校注说明…"
          />
        </label>
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
        {error && <div className="error">{error}</div>}
        <div className="modal-actions">
          {initial?.id && (
            <button className="btn danger" onClick={() => onDelete(initial.id)}>
              删除
            </button>
          )}
          <span style={{ flex: 1 }} />
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn primary" onClick={submit}>保存</button>
        </div>
      </div>
    </div>
  )
}
