import { fmtTime } from '../utils.js'

export default function SegmentList({ segments, onChange, onDelete, onAdd, readOnly }) {
  const update = (id, key, value) => {
    onChange(segments.map((s) => (s.id === id ? { ...s, [key]: value } : s)))
  }

  return (
    <div className="segment-list">
      <div className="segment-list-head">
        <h3>转写片段（{segments.length}）</h3>
        {!readOnly && (
          <button className="btn small" onClick={onAdd}>+ 新增片段</button>
        )}
      </div>
      <div className="segment-table" role="table">
        <div className="seg-row seg-head" role="row">
          <span style={{ width: 130 }}>时间</span>
          <span style={{ width: 110 }}>说话人</span>
          <span>文本</span>
          <span style={{ width: 40 }} />
        </div>
        {segments.map((s) => (
          <div className="seg-row" role="row" key={s.id}>
            <span className="seg-time" title={`${s.start} – ${s.end} 秒`}>
              {fmtTime(s.start)}
              <br />– {fmtTime(s.end)}
            </span>
            {readOnly ? (
              <>
                <span className="seg-speaker">{s.speaker}</span>
                <span className="seg-text">{s.text}</span>
                <span />
              </>
            ) : (
              <>
                <input
                  className="seg-speaker-input"
                  value={s.speaker}
                  onChange={(e) => update(s.id, 'speaker', e.target.value)}
                />
                <textarea
                  className="seg-text-input"
                  rows={1}
                  value={s.text}
                  onChange={(e) => update(s.id, 'text', e.target.value)}
                />
                <button
                  className="btn icon danger"
                  title="删除片段"
                  onClick={() => onDelete(s.id)}
                >
                  ✕
                </button>
              </>
            )}
          </div>
        ))}
        {segments.length === 0 && (
          <div className="empty-hint">暂无片段，请先导入转写稿。</div>
        )}
      </div>
    </div>
  )
}
