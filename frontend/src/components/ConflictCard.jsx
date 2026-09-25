import { fmtTime } from '../utils.js'

const FIELD_LABELS = {
  start: '开始', end: '结束', speaker: '说话人', text: '文本',
  topic: '主题', note: '备注',
}

function ItemView({ item, kind, highlight = [], mark }) {
  if (!item) return <span className="muted">（{mark || '已删除'}）</span>
  const fields = kind === 'segments'
    ? ['start', 'end', 'speaker', 'text']
    : ['start', 'end', 'speaker', 'topic', 'note']
  return (
    <div className="item-view">
      {fields.map((f) => (
        <div
          key={f}
          className={`item-field ${highlight.includes(f) ? 'conflict-field' : ''}`}
        >
          <span className="field-label">{FIELD_LABELS[f]}</span>
          <span className="field-value">
            {f === 'start' || f === 'end'
              ? `${fmtTime(Number(item[f]))}（${item[f]}s）`
              : String(item[f] ?? '')}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function ConflictCard({ conflict, index, value, onChange }) {
  const isInterval = conflict.conflictType === 'interval'
  const kindLabel = conflict.kind === 'segments' ? '片段' : '校注'

  const setChoice = (choice, extra = {}) =>
    onChange({ choice, ...extra })

  const current = value?.choice
  const custom = value?.value || buildCustom(conflict)
  const fieldChoices = value?.fields || {}

  return (
    <div className={`conflict-card ${value ? 'resolved' : 'unresolved'}`}>
      <div className="conflict-head">
        <span className="conflict-index">#{index + 1}</span>
        <strong>
          {isInterval
            ? `区间重叠冲突（${conflict.aItems.length + conflict.bItems.length} 条校注）`
            : conflict.modifyDelete
              ? `${kindLabel}修改/删除冲突`
              : `${kindLabel}字段冲突`}
        </strong>
        <span className={`conflict-status ${value ? 'ok-text' : 'warn-text'}`}>
          {value ? '已裁决' : '待裁决'}
        </span>
      </div>

      {isInterval ? (
        <IntervalSides conflict={conflict} />
      ) : (
        <div className="conflict-columns">
          <div className="conflict-col base">
            <div className="col-title">共同基准</div>
            <ItemView item={conflict.base} kind={conflict.kind} mark="不存在" />
          </div>
          <div className="conflict-col side-a">
            <div className="col-title">A 版</div>
            <ItemView
              item={conflict.a}
              kind={conflict.kind}
              highlight={conflict.conflictingFields}
              mark="已删除"
            />
          </div>
          <div className="conflict-col side-b">
            <div className="col-title">B 版</div>
            <ItemView
              item={conflict.b}
              kind={conflict.kind}
              highlight={conflict.conflictingFields}
              mark="已删除"
            />
          </div>
        </div>
      )}

      {!isInterval && (
        <div className="conflict-fields-tags">
          冲突字段：
          {conflict.conflictingFields.map((f) => (
            <span key={f} className="tag danger">{FIELD_LABELS[f]}</span>
          ))}
          {conflict.partialMerge && (
            <span className="muted small">
              （其余字段的双方改动将自动并入）
            </span>
          )}
        </div>
      )}

      <div className="conflict-actions">
        {isInterval ? (
          <>
            <button
              className={`btn ${current === 'a' ? 'primary' : ''}`}
              onClick={() => setChoice('a')}
            >
              采用 A（{conflict.aItems.length} 条），丢弃 B
            </button>
            <button
              className={`btn ${current === 'b' ? 'primary' : ''}`}
              onClick={() => setChoice('b')}
            >
              采用 B（{conflict.bItems.length} 条），丢弃 A
            </button>
            <button
              className={`btn ${current === 'keep' ? 'primary' : ''}`}
              onClick={() => setChoice('keep')}
            >
              双方都保留
            </button>
            <button
              className={`btn ${current === 'drop' ? 'danger' : ''}`}
              onClick={() => setChoice('drop')}
            >
              全部丢弃
            </button>
          </>
        ) : (
          <>
            <button
              className={`btn ${current === 'a' ? 'primary' : ''}`}
              onClick={() => setChoice('a')}
            >
              整条采用 A {conflict.a === null ? '（删除）' : ''}
            </button>
            <button
              className={`btn ${current === 'b' ? 'primary' : ''}`}
              onClick={() => setChoice('b')}
            >
              整条采用 B {conflict.b === null ? '（删除）' : ''}
            </button>
            <button
              className={`btn ${current === 'drop' ? 'danger' : ''}`}
              onClick={() => setChoice('drop')}
            >
              丢弃该条
            </button>
          </>
        )}
        <button
          className={`btn ${current === 'custom' ? 'primary' : ''}`}
          onClick={() => setChoice('custom', { value: custom })}
        >
          自定义合并值 ✏️
        </button>
      </div>

      {/* Per-field adjudication (identity conflicts only) */}
      {!isInterval && (
        <details className="field-adjudicate">
          <summary>按字段逐项裁决（仅对冲突字段选择 A/B，其他字段自动合并）</summary>
          <div className="field-adjudicate-body">
            {conflict.conflictingFields.map((f) => (
              <div key={f} className="field-choice-row">
                <span className="field-label">{FIELD_LABELS[f]}</span>
                {['a', 'b'].map((side) => (
                  <button
                    key={side}
                    className={`btn small ${
                      (fieldChoices[f] || guessSide(conflict, f)) === side
                        ? 'primary' : ''
                    }`}
                    onClick={() => {
                      const next = { ...fieldChoices, [f]: side }
                      setChoice('fields', { fields: next })
                    }}
                  >
                    {side.toUpperCase()}:{' '}
                    {conflict[side] === null
                      ? '∅'
                      : f === 'start' || f === 'end'
                        ? conflict[side][f]
                        : String(conflict[side][f] ?? '')}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </details>
      )}

      {current === 'custom' && (
        <CustomEditor
          conflict={conflict}
          value={custom}
          onChange={(v) => onChange({ choice: 'custom', value: v })}
        />
      )}
    </div>
  )
}

function guessSide(conflict, f) {
  // default pick for display before explicit choice: A
  return 'a'
}

function buildCustom(conflict) {
  if (conflict.conflictType === 'interval') {
    const a = conflict.aItems[0] || {}
    const b = conflict.bItems[0] || {}
    return {
      id: a.id || b.id,
      start: a.start ?? b.start,
      end: a.end ?? b.end,
      speaker: a.speaker || b.speaker || '',
      topic: [a.topic, b.topic].filter(Boolean).join(' / '),
      note: [a.note, b.note].filter(Boolean).join('\n---\n'),
    }
  }
  const base = conflict.partialMerge || conflict.base || conflict.a || conflict.b || {}
  return { ...base, id: conflict.itemId }
}

function IntervalSides({ conflict }) {
  return (
    <div className="conflict-columns">
      <div className="conflict-col side-a">
        <div className="col-title">A 版新增（{conflict.aItems.length}）</div>
        {conflict.aItems.map((a) => (
          <ItemView key={a.id} item={a} kind="annotations" highlight={['start', 'end']} />
        ))}
      </div>
      <div className="conflict-col side-b">
        <div className="col-title">B 版新增（{conflict.bItems.length}）</div>
        {conflict.bItems.map((b) => (
          <ItemView key={b.id} item={b} kind="annotations" highlight={['start', 'end']} />
        ))}
      </div>
    </div>
  )
}

function CustomEditor({ conflict, value, onChange }) {
  const fields = conflict.kind === 'segments'
    ? ['start', 'end', 'speaker', 'text']
    : ['start', 'end', 'speaker', 'topic', 'note']
  const set = (f, v) => onChange({ ...value, [f]: v })

  return (
    <div className="custom-editor">
      <div className="col-title">合并后的自定义值</div>
      <div className="custom-grid">
        {fields.map((f) => (
          <label key={f} className={f === 'text' || f === 'note' ? 'wide' : ''}>
            {FIELD_LABELS[f]}
            {f === 'note' || f === 'text' ? (
              <textarea
                rows={2}
                value={value[f] ?? ''}
                onChange={(e) => set(f, e.target.value)}
              />
            ) : f === 'start' || f === 'end' ? (
              <input
                type="number" step="0.001"
                value={value[f] ?? 0}
                onChange={(e) => set(f, e.target.value)}
              />
            ) : (
              <input
                value={value[f] ?? ''}
                onChange={(e) => set(f, e.target.value)}
              />
            )}
          </label>
        ))}
      </div>
    </div>
  )
}
