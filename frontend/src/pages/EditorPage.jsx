import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api.js'
import Timeline from '../components/Timeline.jsx'
import SegmentList from '../components/SegmentList.jsx'
import AnnotationModal from '../components/AnnotationModal.jsx'
import QuoteSearch from '../components/QuoteSearch.jsx'
import SpeakingDurations from '../components/SpeakingDurations.jsx'
import TurnTakingPanel from '../components/TurnTakingPanel.jsx'
import {
  clone, downloadJson, emptyDocument, makeAnnotation, makeSegment, round3,
} from '../utils.js'

export default function EditorPage() {
  const { id } = useParams()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const [project, setProject] = useState(null)
  const [doc, setDoc] = useState(emptyDocument())
  const [name, setName] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [modal, setModal] = useState(null) // { initial }
  const [qualityFocus, setQualityFocus] = useState(null) // {annId,range}
  const [quoteFocus, setQuoteFocus] = useState(null) // {range,speaker}
  const [speakingFocus, setSpeakingFocus] = useState(null) // {range,speakers}
  const [turnFocus, setTurnFocus] = useState(null) // {range,from,to,kind}
  const [coverageFocus, setCoverageFocus] = useState(null) // {range}
  const importRef = useRef(null)
  const timelineRef = useRef(null)

  useEffect(() => {
    api.getProject(id).then((p) => {
      setProject(p)
      setDoc(p.document || emptyDocument())
      setName(p.name)
    }).catch((e) => setError(e.message))
  }, [id])

  // Returning from the speaker-normalization workbench: it already saved the
  // project, and this page's reload above reads the new names. Show a toast
  // summarizing the committed change (and drop the navigation state once).
  useEffect(() => {
    const info = location.state?.speakerRename
    if (!info) return undefined
    setMessage(
      `说话人归一已保存：更新转写片段 ${info.segments} 条 · 校注 ` +
      `${info.annotations} 条 · 规则 ${info.rules} 条；引语检索与发言时长` +
      '均使用新名称。')
    const timer = setTimeout(() => setMessage(''), 6000)
    window.history.replaceState(
      { ...window.history.state, usr: null }, '')
    return () => clearTimeout(timer)
  }, [location.state])

  // Returning from the error-word correction workbench: the corrected
  // segment texts were saved there; this page's reload shows them.
  useEffect(() => {
    const info = location.state?.correctionApply
    if (!info) return undefined
    setMessage(
      `错词校正已保存：影响 ${info.segments} 个片段、共 ` +
      `${info.replacements} 处替换；片段 ID、时间与校注均未变化。`)
    const timer = setTimeout(() => setMessage(''), 6000)
    window.history.replaceState(
      { ...window.history.state, usr: null }, '')
    return () => clearTimeout(timer)
  }, [location.state])

  // Deep link from the quality-check / coverage workbenches:
  // ?focusStart=…&focusEnd=…[&ann=…][&focusSource=coverage]
  useEffect(() => {
    const fs = Number(searchParams.get('focusStart'))
    const fe = Number(searchParams.get('focusEnd'))
    if (Number.isFinite(fs) && Number.isFinite(fe)) {
      const range = { start: fs, end: fe }
      if (searchParams.get('focusSource') === 'coverage') {
        setCoverageFocus({ range })
      } else {
        setQualityFocus({ annId: searchParams.get('ann'), range })
      }
      setTimeout(() => timelineRef.current?.scrollIntoView(
        { behavior: 'smooth', block: 'center' }), 300)
    }
  }, [searchParams])

  const clearQualityFocus = () => {
    setQualityFocus(null)
    setQuoteFocus(null)
    setSpeakingFocus(null)
    setTurnFocus(null)
    setCoverageFocus(null)
    if (searchParams.has('focusStart')) {
      searchParams.delete('ann')
      searchParams.delete('focusStart')
      searchParams.delete('focusEnd')
      searchParams.delete('focusSource')
      setSearchParams(searchParams, { replace: true })
    }
  }

  // Clicking a quote-search result: emphasize its time span on the timeline
  // and scroll the timeline into view.
  const locateQuote = (match) => {
    setQualityFocus(null)
    setSpeakingFocus(null)
    setTurnFocus(null)
    setCoverageFocus(null)
    setQuoteFocus({
      range: { start: match.start, end: match.end },
      speaker: match.speaker,
    })
    setTimeout(() => timelineRef.current?.scrollIntoView(
      { behavior: 'smooth', block: 'center' }), 0)
  }

  // Clicking a co-speaking interval in the speaking-time panel: emphasize
  // that interval on the same timeline and scroll it into view.
  const locateSpeaking = ({ start, end, speakers: activeSpeakers }) => {
    setQualityFocus(null)
    setQuoteFocus(null)
    setTurnFocus(null)
    setCoverageFocus(null)
    setSpeakingFocus({ range: { start, end }, speakers: activeSpeakers })
    setTimeout(() => timelineRef.current?.scrollIntoView(
      { behavior: 'smooth', block: 'center' }), 0)
  }

  // Clicking a turn-taking occurrence in the turn-taking panel: emphasize
  // the transition span on the same timeline and scroll it into view.
  const locateTurn = ({ start, end, from, to, kind }) => {
    setQualityFocus(null)
    setQuoteFocus(null)
    setSpeakingFocus(null)
    setCoverageFocus(null)
    setTurnFocus({ range: { start, end }, from, to, kind })
    setTimeout(() => timelineRef.current?.scrollIntoView(
      { behavior: 'smooth', block: 'center' }), 0)
  }

  const update = (patch) => {
    setDoc((d) => ({ ...d, ...patch }))
    setDirty(true)
  }

  const save = useCallback(async () => {
    setSaving(true); setError('')
    try {
      const p = await api.saveProject(id, { name: name.trim(), document: doc })
      setProject(p)
      setDirty(false)
      setMessage('已保存 ' + new Date().toLocaleTimeString())
      setTimeout(() => setMessage(''), 2500)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }, [id, name, doc])

  useEffect(() => {
    const h = (e) => {
      if (dirty) { e.preventDefault(); e.returnValue = '' }
    }
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [dirty])

  const speakers = useMemo(() => {
    const set = new Set()
    doc.segments.forEach((s) => s.speaker && set.add(s.speaker))
    doc.annotations.forEach((a) => a.speaker && set.add(a.speaker))
    return [...set]
  }, [doc])

  const duration = useMemo(() => {
    const ends = [...doc.segments, ...doc.annotations].map((x) => x.end || 0)
    return round3(Math.max(0, ...ends))
  }, [doc])

  const onTimelineSelect = (range) => {
    clearQualityFocus()
    setModal({ initial: range })
  }

  const saveAnnotation = (ann) => {
    const existing = doc.annotations.find((a) => a.id === ann.id)
    let nextAnn
    if (existing) {
      // Editing a rule-generated annotation detaches it: from now on it is
      // treated as a manual annotation and re-running the rule won't replace
      // or remove it.
      const wasRule = existing.source === 'rule'
      nextAnn = { ...ann }
      if (wasRule) {
        // detach ownership: ruleId/ruleSlot stay as provenance so a later
        // run recognizes this slot is occupied by a manual annotation.
        delete nextAnn.source
        delete nextAnn.ruleName
      }
    } else {
      nextAnn = makeAnnotation(ann.start, ann.end, ann.speaker,
        ann.topic, ann.note)
      nextAnn.color = ann.color
    }
    const next = existing
      ? doc.annotations.map((a) => (a.id === ann.id ? nextAnn : a))
      : [...doc.annotations, nextAnn]
    update({ annotations: next })
    setModal(null)
  }

  const deleteAnnotation = (annId) => {
    update({
      annotations: doc.annotations.filter((a) => a.id !== annId),
    })
    setModal(null)
  }

  const addSegment = () => {
    const last = doc.segments[doc.segments.length - 1]
    const start = last ? last.end : 0
    update({
      segments: [...doc.segments,
        makeSegment(start, round3(start + 1), '', '新片段')],
    })
  }

  const onImportFile = async (file) => {
    if (!window.confirm(
      '导入将替换全部转写片段（已有校注会保留）。确定继续？')) {
      importRef.current.value = ''
      return
    }
    setError('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const p = await api.importIntoProject(id, fd)
      setDoc(p.document)
      setProject(p)
      setDirty(true)
    } catch (e) {
      setError(`导入失败：${e.message}`)
    } finally {
      importRef.current.value = ''
    }
  }

  /** Offline workflow: export the current saved doc as a working copy. */
  const exportCopy = (side) => {
    const payload = {
      _offlineCopy: true,
      baseProjectId: id,
      baseProjectName: project?.name || '',
      exportedAt: new Date().toISOString(),
      editor: side,
      document: doc,
    }
    downloadJson(
      `${project?.name || 'project'}-${side}-copy.json`, payload)
  }

  if (!project && !error) return <div className="page">加载中…</div>

  return (
    <div className="page editor">
      <div className="page-head">
        <div className="title-line">
          <Link to="/" className="back">← 项目</Link>
          <input
            className="project-name"
            value={name}
            onChange={(e) => { setName(e.target.value); setDirty(true) }}
          />
          {dirty && <span className="badge warn">未保存</span>}
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${id}/quality`}>
            🧪 校注质检
          </Link>
          <Link className="btn" to={`/projects/${id}/coverage`}>
            🧭 覆盖盲区
          </Link>
          <Link className="btn" to={`/projects/${id}/speakers`}>
            🏷️ 说话人归一
          </Link>
          <Link className="btn" to={`/projects/${id}/rules`}>
            ⚙️ 规则批量标注
          </Link>
          <Link className="btn" to={`/projects/${id}/excerpts`}>
            📦 摘录包
          </Link>
          <Link className="btn" to={`/projects/${id}/public`}>
            🛡️ 公开版转写
          </Link>
          <Link className="btn" to={`/projects/${id}/corrections`}>
            📝 错词校正
          </Link>
          <button className="btn" onClick={() => importRef.current?.click()}>
            导入转写
          </button>
          <input
            ref={importRef} type="file" hidden
            accept=".json,.txt,.srt,.vtt"
            onChange={(e) => e.target.files?.[0] && onImportFile(e.target.files[0])}
          />
          <button className="btn" onClick={() => exportCopy('A')}>
            导出 A 离线副本
          </button>
          <button className="btn" onClick={() => exportCopy('B')}>
            导出 B 离线副本
          </button>
          <Link className="btn" to="/merge">前往三方合并 →</Link>
          <button className="btn primary" disabled={saving || !dirty} onClick={save}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {message && <div className="ok">{message}</div>}

      <div className="panel" ref={timelineRef}>
        <Timeline
          duration={duration}
          segments={doc.segments}
          annotations={doc.annotations}
          selectedId={modal?.initial?.id}
          highlightIds={qualityFocus?.annId ? [qualityFocus.annId] : null}
          focusRange={
            (qualityFocus || quoteFocus || speakingFocus
              || turnFocus || coverageFocus)?.range
            || null
          }
          focusVariant={
            quoteFocus ? 'quote'
              : speakingFocus ? 'speaking'
                : turnFocus ? 'turn'
                  : coverageFocus ? 'coverage'
                    : 'quality'
          }
          onSelect={onTimelineSelect}
          onBackgroundClick={clearQualityFocus}
          onClickItem={(annId) => {
            clearQualityFocus()
            const a = doc.annotations.find((x) => x.id === annId)
            if (a) setModal({ initial: clone(a) })
          }}
        />
        {qualityFocus && (
          <div className="qc-focus-hint sev-error">
            <strong>来自校注质检的定位</strong>
            <span>
              {qualityFocus.range.start.toFixed(2)}s –{' '}
              {qualityFocus.range.end.toFixed(2)}s
              {qualityFocus.annId ? ` · ${qualityFocus.annId}` : ''}
            </span>
            <button className="btn small" onClick={clearQualityFocus}>
              取消定位
            </button>
            <Link className="btn small" to={`/projects/${id}/quality`}>
              返回质检工作台 →
            </Link>
          </div>
        )}
        {quoteFocus && (
          <div className="qc-focus-hint quote-focus-hint">
            <strong>引语检索定位</strong>
            <span>
              {quoteFocus.speaker ? `${quoteFocus.speaker} · ` : ''}
              {quoteFocus.range.start.toFixed(2)}s –{' '}
              {quoteFocus.range.end.toFixed(2)}s
            </span>
            <button className="btn small" onClick={clearQualityFocus}>
              取消定位
            </button>
          </div>
        )}
        {speakingFocus && (
          <div className="qc-focus-hint speaking-focus-hint">
            <strong>同讲区间定位</strong>
            <span>
              {speakingFocus.speakers.join('、')} ·{' '}
              {speakingFocus.range.start.toFixed(2)}s –{' '}
              {speakingFocus.range.end.toFixed(2)}s
            </span>
            <button className="btn small" onClick={clearQualityFocus}>
              取消定位
            </button>
          </div>
        )}
        {turnFocus && (
          <div className="qc-focus-hint turn-focus-hint">
            <strong>
              {turnFocus.kind === 'interruption' ? '插话定位' : '普通接话定位'}
            </strong>
            <span>
              {turnFocus.from} → {turnFocus.to} ·{' '}
              {turnFocus.range.start.toFixed(2)}s –{' '}
              {turnFocus.range.end.toFixed(2)}s
            </span>
            <button className="btn small" onClick={clearQualityFocus}>
              取消定位
            </button>
          </div>
        )}
        {coverageFocus && (
          <div className="qc-focus-hint coverage-focus-hint">
            <strong>校注覆盖盲区定位</strong>
            <span>
              {coverageFocus.range.start.toFixed(2)}s –{' '}
              {coverageFocus.range.end.toFixed(2)}s
            </span>
            <button className="btn small" onClick={clearQualityFocus}>
              取消定位
            </button>
            <Link className="btn small" to={`/projects/${id}/coverage`}>
              返回覆盖盲区工作台 →
            </Link>
          </div>
        )}
      </div>

      <SpeakingDurations segments={doc.segments} onLocate={locateSpeaking} />

      <TurnTakingPanel segments={doc.segments} onLocate={locateTurn} />

      <QuoteSearch
        projectId={id}
        document={doc}
        speakers={speakers}
        onLocate={locateQuote}
      />

      <SegmentList
        segments={doc.segments}
        onChange={(segments) => update({ segments })}
        onDelete={(sid) => update({
          segments: doc.segments.filter((s) => s.id !== sid),
        })}
        onAdd={addSegment}
      />

      <AnnotationList
        annotations={doc.annotations}
        onClick={(a) => setModal({ initial: clone(a) })}
        onDelete={deleteAnnotation}
      />

      {modal && (
        <AnnotationModal
          initial={modal.initial}
          speakers={speakers}
          onSave={saveAnnotation}
          onClose={() => setModal(null)}
          onDelete={deleteAnnotation}
        />
      )}
    </div>
  )
}

function AnnotationList({ annotations, onClick, onDelete }) {
  return (
    <div className="annotation-list">
      <h3>校注（{annotations.length}）</h3>
      {annotations.length === 0 && (
        <div className="empty-hint">
          在上方时间轴拖选区间即可添加带说话人、主题、备注的校注。
        </div>
      )}
      <div className="ann-grid">
        {annotations.map((a) => (
          <div
            key={a.id}
            className="ann-chip"
            style={{ borderLeftColor: a.color || '#5b8def' }}
            onClick={() => onClick(a)}
          >
            <div className="ann-chip-head">
              <strong>
                {a.topic || '（未命名主题）'}
                {a.source === 'rule' && (
                  <span className="badge rule-tag" title={a.ruleName || ''}>
                    规则
                  </span>
                )}
              </strong>
              <button
                className="btn icon danger"
                onClick={(e) => { e.stopPropagation(); onDelete(a.id) }}
              >
                ✕
              </button>
            </div>
            <div className="muted small">
              {a.start.toFixed(2)}s – {a.end.toFixed(2)}s
              {a.speaker ? ` · ${a.speaker}` : ''}
            </div>
            {a.note && <div className="ann-note">{a.note}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
