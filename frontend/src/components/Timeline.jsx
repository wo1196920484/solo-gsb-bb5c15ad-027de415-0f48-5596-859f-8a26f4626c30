import { useEffect, useRef, useState } from 'react'
import { fmtTime, round3 } from '../utils.js'

/**
 * Horizontal timeline.
 * Props:
 *   duration      total seconds represented by the timeline
 *   segments      [{start,end,speaker}]
 *   annotations   [{id,start,end,topic,color?}]
 *   selectedId    highlighted annotation id
 *   highlightIds  extra annotation ids outlined (quality-check issues)
 *   focusRange    {start,end} range emphasized (quality-check location)
 *   focusVariant  'quality' (default, red dashed), 'quote' (green solid),
 *                 'speaking' (purple solid, co-speaking intervals),
 *                 'turn' (orange) or 'coverage' (amber, uncovered gaps)
 *   onSelect(a)   called when the user finishes dragging a range
 *   onClickItem(id)
 *   onBackgroundClick()  click on empty track (clears issue highlight)
 *   readOnly      disable drag-to-create (quality workbench)
 *   height        row height in px
 */
export default function Timeline({
  duration,
  segments = [],
  annotations = [],
  selectedId = null,
  highlightIds = null,
  focusRange = null,
  focusVariant = 'quality',
  onSelect,
  onClickItem,
  onBackgroundClick,
  readOnly = false,
  height = 64,
}) {
  const trackRef = useRef(null)
  const [drag, setDrag] = useState(null) // { startX }
  const [sel, setSel] = useState(null) // { start, end } in seconds
  const [hoverX, setHoverX] = useState(null) // offset px within track
  const [width, setWidth] = useState(800)

  useEffect(() => {
    const el = trackRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      setWidth(entries[0].contentRect.width)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const total = Math.max(
    duration || 0,
    ...segments.map((s) => s.end || 0),
    ...annotations.map((a) => a.end || 0),
    10,
  )

  const toX = (sec) => (sec / total) * width
  const toSec = (clientX) => {
    const rect = trackRef.current.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
    return round3(ratio * total)
  }

  const handleDown = (e) => {
    if (e.button !== 0) return
    const t = toSec(e.clientX)
    setDrag({ startX: e.clientX, anchor: t, moved: false })
    if (!readOnly) setSel({ start: t, end: t })
  }

  useEffect(() => {
    if (!drag) return
    const move = (e) => {
      const t = toSec(e.clientX)
      if (Math.abs(e.clientX - drag.startX) > 3) drag.moved = true
      setSel({
        start: Math.min(drag.anchor, t),
        end: Math.max(drag.anchor, t),
      })
    }
    const up = (e) => {
      const t = toSec(e.clientX)
      const start = Math.min(drag.anchor, t)
      const end = Math.max(drag.anchor, t)
      const wasDrag = drag.moved
      setDrag(null)
      if (!readOnly && wasDrag && end - start >= 0.05) {
        setSel({ start, end })
        onSelect && onSelect({ start, end })
      } else if (!wasDrag) {
        // plain click on empty track: clear issue focus
        onBackgroundClick && onBackgroundClick()
      }
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag])

  const ticks = []
  const step = total > 600 ? 120 : total > 120 ? 30 : total > 30 ? 10 : 5
  for (let t = 0; t <= total; t += step) ticks.push(t)

  return (
    <div className="timeline">
      <div className="timeline-ruler" style={{ width }}>
        {ticks.map((t) => (
          <span key={t} className="tick" style={{ left: toX(t) }}>
            {fmtTime(t)}
          </span>
        ))}
      </div>
      <div
        className="timeline-track"
        ref={trackRef}
        style={{
          height, width: '100%',
          cursor: readOnly ? 'default' : 'crosshair',
        }}
        onMouseDown={handleDown}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect()
          setHoverX(e.clientX - rect.left)
        }}
        onMouseLeave={() => setHoverX(null)}
      >
        {segments.map((s) => (
          <div
            key={s.id || `seg-${s.start}-${s.end}`}
            className="tl-segment"
            style={{
              left: toX(s.start),
              width: Math.max(2, toX(s.end) - toX(s.start) - 1),
            }}
            title={`${fmtTime(s.start)}–${fmtTime(s.end)} ${s.speaker || ''}`}
          >
            {s.speaker}
          </div>
        ))}
        {focusRange && (
          <div
            className={`tl-focus tl-focus-${focusVariant}`}
            style={{
              left: toX(focusRange.start),
              width: Math.max(1, toX(focusRange.end) - toX(focusRange.start)),
            }}
          />
        )}
        {annotations.map((a, i) => (
          <div
            key={a.id}
            className={`tl-annotation ${selectedId === a.id ? 'selected' : ''} ${
              highlightIds && highlightIds.includes(a.id) ? 'qc-hit' : ''
            }`}
            style={{
              left: toX(a.start),
              width: Math.max(3, toX(a.end) - toX(a.start) - 1),
              top: 4 + (i % 3) * 16,
              background: a.color,
            }}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation()
              onClickItem && onClickItem(a.id)
            }}
            title={`${fmtTime(a.start)}–${fmtTime(a.end)} ${a.topic || ''}`}
          >
            {a.topic || a.speaker}
          </div>
        ))}
        {sel && (
          <div
            className="tl-selection"
            style={{
              left: toX(sel.start),
              width: Math.max(1, toX(sel.end) - toX(sel.start)),
            }}
          />
        )}
        {hoverX != null && (
          <div className="tl-cursor" style={{ left: hoverX }} />
        )}
      </div>
      <div className="timeline-hint">
        {readOnly
          ? '点击问题卡片或校注条以定位；点击空白处取消定位'
          : '在时间轴上按住鼠标拖选区间以添加校注'}
        {sel ? ` · 已选 ${fmtTime(sel.start)} – ${fmtTime(sel.end)}` : ''}
      </div>
    </div>
  )
}
