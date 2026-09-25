import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { fmtSeconds, fmtTime } from '../utils.js'

/**
 * 校注覆盖盲区工作台。
 *
 * 读取**已保存**项目文档，按说话人统计实际发言时长、被同名校注覆盖时长与
 * 覆盖率，并逐段列出未被覆盖的发言区间。可按说话人筛选；点击任一盲区即
 * 携带 ?focusStart/focusSource=coverage 深链返回编辑页并在时间轴定位。
 */
export default function CoveragePage() {
  const { id: projectId } = useParams()
  const [project, setProject] = useState(null)
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('') // '' = 全部
  const [activeKey, setActiveKey] = useState(null)

  useEffect(() => {
    setLoading(true)
    api.getProject(projectId).then((p) => {
      setProject(p)
      return api.coverageGaps(projectId)
    }).then((r) => {
      setReport(r)
    }).catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [projectId])

  const bySpeaker = useMemo(() => {
    const m = new Map()
    ;(report?.speakers || []).forEach((sp) => m.set(sp.speaker, sp))
    return m
  }, [report])

  const shownSpeakers = useMemo(() => {
    const all = report?.speakers || []
    return filter ? all.filter((sp) => sp.speaker === filter) : all
  }, [report, filter])

  const shownGaps = useMemo(() => shownSpeakers
    .flatMap((sp) => sp.uncoveredRanges.map(
      (g) => ({ ...g, speaker: sp.speaker }))),
  [shownSpeakers])

  const locateUrl = (gap) =>
    `/projects/${projectId}?focusStart=${gap.start}&focusEnd=${gap.end}` +
    `&focusSource=coverage`

  if (!project && !error) return <div className="page">加载中…</div>

  const s = report?.summary
  const allSpeakers = report?.speakers || []

  return (
    <div className="page coverage-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>校注覆盖盲区 · {project?.name}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
        </div>
      </div>
      {error && <div className="error">{error}</div>}

      <div className="muted small cv-hint">
        基于<strong>已保存</strong>的转写片段与校注统计（半开区间，毫秒精度）：
        先合并同一说话人重叠或相接的发言片段；仅<strong>同名说话人</strong>
        的校注可覆盖其发言，校注超出发言的部分不计入，重叠校注不重复计时；
        多人同时发言分别计入各自说话人。
      </div>

      {s && (
        <div className="stats-grid cv-summary">
          <div className="stat-box">
            <span className="stat-num">{s.speakers}</span>
            <span className="muted small">说话人数</span>
          </div>
          <div className="stat-box">
            <span className="stat-num">
              {fmtSeconds(s.speechSeconds)}<span className="stat-unit">s</span>
            </span>
            <span className="muted small">实际发言总时长</span>
          </div>
          <div className="stat-box">
            <span className="stat-num">
              {fmtSeconds(s.coveredSeconds)}<span className="stat-unit">s</span>
            </span>
            <span className="muted small">已覆盖总时长</span>
          </div>
          <div className="stat-box cv-rate-box">
            <span className="stat-num">{pct(s.coverage)}</span>
            <span className="muted small">总覆盖率</span>
          </div>
          <div className="stat-box has-conflict">
            <span className="stat-num">
              {fmtSeconds(s.uncoveredSeconds)}
              <span className="stat-unit">s</span>
            </span>
            <span className="muted small">
              未覆盖总时长（{s.uncoveredIntervalCount} 段）
            </span>
          </div>
        </div>
      )}

      <div className="panel cv-filters">
        <span className="muted small">按说话人筛选盲区：</span>
        <button
          className={`filter-chip ${filter === '' ? 'on' : ''}`}
          onClick={() => { setFilter(''); setActiveKey(null) }}
        >
          全部（{allSpeakers.length}）
        </button>
        {allSpeakers.map((sp) => (
          <button
            key={sp.speaker}
            className={`filter-chip cv-speaker-chip ${
              filter === sp.speaker ? 'on' : ''}`}
            onClick={() => { setFilter(sp.speaker); setActiveKey(null) }}
            title={`覆盖率 ${pct(sp.coverage)} · ${sp.uncoveredCount} 段盲区`}
          >
            {sp.speaker}
            <span className="cv-chip-meta">
              {pct(sp.coverage)}
              {sp.uncoveredCount > 0 && (
                <span className="cv-chip-gap"> · {sp.uncoveredCount} 盲区</span>
              )}
            </span>
          </button>
        ))}
      </div>

      <div className="panel">
        <h3>
          按说话人统计
          {loading && <span className="muted small sp-panel-hint">加载中…</span>}
        </h3>
        {shownSpeakers.length === 0 ? (
          <div className="empty-hint">
            当前已保存的转写中还没有可统计的发言（空说话人与零长度片段已忽略）。
          </div>
        ) : (
          <table className="run-table cv-table">
            <thead>
              <tr>
                <th>说话人</th>
                <th>实际发言时长</th>
                <th>已覆盖时长</th>
                <th>未覆盖时长</th>
                <th>覆盖率</th>
                <th>盲区段数</th>
              </tr>
            </thead>
            <tbody>
              {shownSpeakers.map((sp) => (
                <tr
                  key={sp.speaker}
                  className={sp.uncoveredCount ? 'cv-has-gap' : 'cv-full-row'}
                >
                  <td>
                    <button
                      className="cv-name-link"
                      onClick={() => {
                        setFilter(filter === sp.speaker ? '' : sp.speaker)
                        setActiveKey(null)
                      }}
                    >
                      <strong>{sp.speaker}</strong>
                    </button>
                  </td>
                  <td className="cv-num">{fmtSeconds(sp.speechSeconds)} s</td>
                  <td className="cv-num">{fmtSeconds(sp.coveredSeconds)} s</td>
                  <td className="cv-num">
                    {fmtSeconds(sp.uncoveredSeconds)} s
                  </td>
                  <td className="cv-num">
                    <CoverageBar ratio={sp.coverage} />
                  </td>
                  <td className="cv-num">
                    {sp.uncoveredCount === 0
                      ? <span className="tag cv-ok-tag">已全覆盖</span>
                      : <span className="tag danger">{sp.uncoveredCount}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h3>
          未覆盖发言区间{filter ? ` · ${filter}` : ''}
          <span className="muted small sp-panel-hint">
            点击任一行返回编辑页并在时间轴上定位该区间
          </span>
        </h3>
        {shownGaps.length === 0 ? (
          <div className="empty-hint">
            {allSpeakers.length === 0
              ? '暂无可统计的发言。'
              : '🎉 当前筛选范围内没有盲区，该说话人的发言已全部被校注覆盖。'}
          </div>
        ) : (
          <div className="cv-gap-list">
            {shownGaps.map((g, i) => {
              const key = `${g.speaker}|${g.start}|${g.end}`
              return (
                <Link
                  key={key}
                  to={locateUrl(g)}
                  className={`cv-gap${activeKey === key ? ' active' : ''}`}
                  onClick={() => setActiveKey(key)}
                >
                  <span className="cv-gap-time">
                    {fmtTime(g.start)} – {fmtTime(g.end)}
                  </span>
                  <span className="cv-gap-speaker">{g.speaker}</span>
                  <span className="cv-gap-duration muted">
                    {fmtSeconds(g.duration)} s 未覆盖
                  </span>
                  <span className="cv-gap-go">去校注 →</span>
                </Link>
              )
            })}
          </div>
        )}
        {shownGaps.length > 0 && filter === '' && (
          <div className="muted small cv-list-foot">
            共 {shownGaps.length} 段盲区，合计未覆盖{' '}
            {fmtSeconds(shownGaps.reduce((t, g) => t + g.duration, 0))} s。
          </div>
        )}
      </div>
    </div>
  )
}

function pct(ratio) {
  return `${(Math.round(ratio * 1000) / 10).toFixed(1)}%`
}

function CoverageBar({ ratio }) {
  const pctNum = Math.round(ratio * 100)
  const cls = ratio >= 1
    ? 'cv-bar cv-bar-full'
    : ratio >= 0.6
      ? 'cv-bar'
      : 'cv-bar cv-bar-low'
  return (
    <span className="cv-bar-wrap">
      <span className={cls}>
        <span
          className="cv-bar-fill"
          style={{ width: `${pctNum}%` }}
        />
      </span>
      <span className="cv-bar-text">{pct(ratio)}</span>
    </span>
  )
}
