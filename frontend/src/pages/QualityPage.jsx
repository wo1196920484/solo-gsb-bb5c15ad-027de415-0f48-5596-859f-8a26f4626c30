import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import Timeline from '../components/Timeline.jsx'
import { downloadJson, fmtTime, round3 } from '../utils.js'

const SEV_LABEL = { error: '严重', warning: '警告', info: '提示' }
const SEV_CLASS = { error: 'sev-error', warning: 'sev-warning', info: 'sev-info' }

const TYPE_META = {
  out_of_bounds: { label: '校注越界', icon: '🔹' },
  overlap: { label: '时间重叠', icon: '🔸' },
  speaker_mismatch: { label: '说话人不一致', icon: '🎤' },
  speaker_missing: { label: '说话人缺失', icon: '❔' },
}

const WINDOW_TOL = 1e-6

function sameWindow(a, b) {
  if (!a || !b) return false
  return Math.abs(a.window.start - b.window.start) < WINDOW_TOL
    && Math.abs(a.window.end - b.window.end) < WINDOW_TOL
}

function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
    + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export default function QualityPage() {
  const { id: projectId } = useParams()
  const [project, setProject] = useState(null)
  const [result, setResult] = useState(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')
  const [winInput, setWinInput] = useState({ start: '', end: '' })
  const [sevFilter, setSevFilter] = useState(() => new Set())
  const [typeFilter, setTypeFilter] = useState(() => new Set())
  const [activeIssue, setActiveIssue] = useState(null) // issue object
  // scan history
  const [scans, setScans] = useState([])
  const [selected, setSelected] = useState([]) // scan ids, up to 2
  const [comparing, setComparing] = useState(false)
  const [comparison, setComparison] = useState(null)
  const [viewScan, setViewScan] = useState(null) // stored snapshot shown read-only
  const timelineRef = useRef(null)

  const doc = project?.document || { segments: [], annotations: [] }
  // a historical snapshot, when open, takes over the result area
  const shown = viewScan || result
  const selectedScans = useMemo(
    () => scans.filter((s) => selected.includes(s.id)),
    [scans, selected])
  const windowMismatch = selectedScans.length === 2
    && !sameWindow(selectedScans[0], selectedScans[1])

  const duration = useMemo(() => {
    const ends = [...doc.segments, ...doc.annotations].map((x) => x.end || 0)
    return round3(Math.max(0, ...ends))
  }, [doc])

  const flash = (msg) => {
    setOk(msg)
    setTimeout(() => setOk(''), 3500)
  }

  const loadScans = () =>
    api.listQualityScans(projectId).then(setScans).catch(() => {})

  const runCheck = async (keepWindow) => {
    setChecking(true); setError('')
    const body = {}
    if (keepWindow) {
      const s = Number(winInput.start)
      const e = Number(winInput.end)
      if (winInput.start !== '' && Number.isFinite(s) && s >= 0) body.start = s
      if (winInput.end !== '' && Number.isFinite(e) && e >= 0) body.end = e
    }
    try {
      const data = await api.qualityCheck(projectId, Object.keys(body).length ? body : null)
      setResult(data)
      setViewScan(null)
      setComparison(null)
      loadScans()
      return data
    } catch (e) {
      setError(e.message)
      return null
    } finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    api.getProject(projectId).then((p) => {
      setProject(p)
    }).catch((e) => setError(e.message))
    loadScans()
  }, [projectId])

  const resetWindow = () => setWinInput({ start: '', end: '' })

  const toggle = (set, setter, value) => {
    const next = new Set(set)
    next.has(value) ? next.delete(value) : next.add(value)
    setter(next)
  }

  const toggleScan = (scan) => {
    setError('')
    setSelected((sel) => {
      if (sel.includes(scan.id)) return sel.filter((x) => x !== scan.id)
      if (sel.length >= 2) return [sel[1], scan.id]
      return [...sel, scan.id]
    })
  }

  const runCompare = async () => {
    if (selectedScans.length !== 2) return
    if (!sameWindow(selectedScans[0], selectedScans[1])) {
      setError('两次扫描的时间窗口不同，无法比较：请选择时间窗口'
        + '（开始 / 结束）完全相同的两次扫描。')
      return
    }
    setComparing(true); setError('')
    try {
      const data = await api.compareQualityScans(
        projectId, selectedScans[0].id, selectedScans[1].id)
      setComparison(data)
      setViewScan(null)
      setActiveIssue(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setComparing(false)
    }
  }

  const openScan = async (scan) => {
    setError('')
    try {
      const detail = await api.getQualityScan(projectId, scan.id)
      setViewScan(detail)
      setComparison(null)
      setActiveIssue(null)
    } catch (e) {
      setError(e.message)
    }
  }

  const locate = (issue) => {
    setActiveIssue(issue)
    setTimeout(() => timelineRef.current?.scrollIntoView(
      { behavior: 'smooth', block: 'center' }), 0)
  }

  const filtered = useMemo(() => {
    if (!shown) return []
    return shown.issues.filter((q) =>
      (sevFilter.size === 0 || sevFilter.has(q.severity)) &&
      (typeFilter.size === 0 || typeFilter.has(q.type)))
  }, [shown, sevFilter, typeFilter])

  const focusRange = activeIssue
    ? { start: activeIssue.start, end: activeIssue.end }
    : null
  const highlightIds = activeIssue?.annotationIds || []

  /** One-click trim of a fixable out-of-bounds annotation, then save. */
  const applyFix = async (issue) => {
    const fix = issue.fix
    const annId = issue.annotationId
    if (!fix || !annId) return
    const target = doc.annotations.find((a) => a.id === annId)
    if (!target) {
      setError('待裁剪的校注已不存在，请重新运行质检')
      return
    }
    const nextAnnotations = doc.annotations.map((a) => {
      if (a.id !== annId) return a
      const fixed = { ...a, start: fix.start, end: fix.end }
      // clipping a rule-generated annotation detaches it, mirroring the
      // editor: from now on re-running that rule won't overwrite the trim
      if (fixed.source === 'rule') {
        delete fixed.source
        delete fixed.ruleName
      }
      return fixed
    })
    const nextDoc = { ...doc, annotations: nextAnnotations }
    setError('')
    try {
      const saved = await api.saveProject(projectId, {
        name: project.name, document: nextDoc,
      })
      setProject(saved)
      flash(`已裁剪校注「${target.topic || annId}」到 ` +
        `${fmtTime(fix.start)} – ${fmtTime(fix.end)} 并保存`)
      const rechecked = await runCheck(true)
      if (rechecked) {
        const still = rechecked.issues.find((q) => q.issueId === issue.issueId)
        setActiveIssue(still || null)
      }
    } catch (e) {
      setError(`裁剪保存失败：${e.message}`)
    }
  }

  const exportResult = () => {
    if (!shown) return
    downloadJson(`${project?.name || 'project'}-quality-check.json`, {
      kind: 'annotation-quality-check',
      projectId,
      projectName: project?.name || '',
      exportedAt: new Date().toISOString(),
      scanId: viewScan?.id || result?.scanId || null,
      scannedAt: viewScan?.createdAt || result?.scannedAt || null,
      result: shown,
    })
  }

  if (!project && !error) return <div className="page">加载中…</div>

  return (
    <div className="page quality-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>校注质检 · {project?.name}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
          <button
            className="btn"
            disabled={!shown}
            onClick={exportResult}
          >
            ⬇️ 导出质检 JSON
          </button>
          <button
            className="btn primary"
            disabled={checking}
            onClick={() => runCheck(true)}
          >
            {checking ? '扫描中…' : '🔎 开始质检'}
          </button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {ok && <div className="ok">{ok}</div>}

      <div className="panel qc-controls">
        <div className="qc-window-row">
          <label>
            时间区间（秒，留空 = 整条转写）
            <div className="qc-window-inputs">
              <input
                type="number" min="0" step="0.1" placeholder="开始"
                value={winInput.start}
                onChange={(e) => setWinInput((w) => ({ ...w, start: e.target.value }))}
              />
              <span className="muted">–</span>
              <input
                type="number" min="0" step="0.1" placeholder="结束"
                value={winInput.end}
                onChange={(e) => setWinInput((w) => ({ ...w, end: e.target.value }))}
              />
              <button className="btn small" onClick={resetWindow}>
                重置为全部
              </button>
            </div>
          </label>
          <div className="muted small qc-window-hint">
            扫描线沿时间轴建立，检测校注越界、校注重叠与说话人不一致。
            当前文档时长 {fmtTime(duration)}。
          </div>
        </div>
      </div>

      {comparison ? (
        <CompareView comparison={comparison} onClose={() => setComparison(null)} />
      ) : (
        <>
          {viewScan && (
            <div className="panel qc-view-banner">
              <span>
                📜 正在查看历史扫描快照（{fmtDateTime(viewScan.createdAt)}）：
                窗口 {fmtTime(viewScan.window.start)} –{' '}
                {fmtTime(viewScan.window.end)}，快照只读，修复操作已禁用。
              </span>
              <button className="btn small" onClick={() => setViewScan(null)}>
                {result ? '← 返回最新扫描结果' : '关闭快照'}
              </button>
            </div>
          )}

          {shown && (
            <SummaryBar result={shown} />
          )}

          {shown && (
            <div className="panel qc-timeline-panel" ref={timelineRef}>
              <Timeline
                duration={duration}
                segments={doc.segments}
                annotations={doc.annotations}
                highlightIds={activeIssue ? highlightIds : null}
                focusRange={focusRange}
                readOnly
                onSelect={() => {}}
                onBackgroundClick={() => setActiveIssue(null)}
                onClickItem={(annId) => {
                  const issue = shown.issues.find(
                    (q) => q.annotationId === annId
                      || q.annotationIds.includes(annId))
                  if (issue) locate(issue)
                }}
              />
              {activeIssue && (
                <div className={`qc-focus-hint ${SEV_CLASS[activeIssue.severity]}`}>
                  <strong>
                    {TYPE_META[activeIssue.type].icon}{' '}
                    {TYPE_META[activeIssue.type].label} ·{' '}
                    {SEV_LABEL[activeIssue.severity]}
                  </strong>
                  <span>
                    定位 {fmtTime(activeIssue.start)} – {fmtTime(activeIssue.end)}
                  </span>
                  <button className="btn small" onClick={() => setActiveIssue(null)}>
                    取消定位
                  </button>
                </div>
              )}
            </div>
          )}

          {shown && (
            <FilterBar
              result={shown}
              sevFilter={sevFilter}
              typeFilter={typeFilter}
              onToggleSev={(v) => toggle(sevFilter, setSevFilter, v)}
              onToggleType={(v) => toggle(typeFilter, setTypeFilter, v)}
              shownCount={filtered.length}
            />
          )}

          {shown && filtered.length === 0 && (
            <div className="panel empty-hint">
              {shown.issues.length === 0
                ? '🎉 未发现问题：所有校注都在转写范围内、互不重叠且说话人一致。'
                : '当前筛选条件下没有问题。'}
            </div>
          )}

          <div className="qc-issue-list">
            {filtered.map((q) => (
              <IssueCard
                key={q.issueId}
                issue={q}
                active={activeIssue?.issueId === q.issueId}
                onLocate={() => locate(q)}
                onFix={() => applyFix(q)}
                projectId={projectId}
                readOnly={!!viewScan}
              />
            ))}
          </div>
        </>
      )}

      <ScanHistoryPanel
        scans={scans}
        selected={selected}
        comparing={comparing}
        onToggle={toggleScan}
        onClear={() => setSelected([])}
        onCompare={runCompare}
        onOpen={openScan}
        windowMismatch={windowMismatch}
      />
    </div>
  )
}

function SummaryBar({ result }) {
  const s = result.summary
  const boxes = [
    ['问题总数', s.total, ''],
    ['严重', s.error, 'sev-error'],
    ['警告', s.warning, 'sev-warning'],
    ['提示', s.info, 'sev-info'],
    ['可一键裁剪', s.fixable, ''],
    ['扫描线', s.scanLines, 'muted'],
  ]
  return (
    <div className="stats-grid qc-summary">
      {boxes.map(([label, value, cls]) => (
        <div key={label} className={`stat-box ${cls || ''}`}>
          <strong>{label}</strong>
          <span>{value}</span>
        </div>
      ))}
    </div>
  )
}

function FilterBar({
  result, sevFilter, typeFilter, onToggleSev, onToggleType, shownCount,
}) {
  const s = result.summary
  const sevItems = [
    ['error', `严重 ${s.error}`],
    ['warning', `警告 ${s.warning}`],
    ['info', `提示 ${s.info}`],
  ]
  const typeItems = [
    ['out_of_bounds', `🔹 越界 ${s.outOfBounds}`],
    ['overlap', `🔸 重叠 ${s.overlap}`],
    ['speaker_mismatch', `🎤 说话人不一致 ${s.speakerMismatch}`],
    ['speaker_missing', `❔ 说话人缺失 ${s.speakerMissing}`],
  ]
  return (
    <div className="panel qc-filters">
      <div className="qc-filter-group">
        <span className="muted small">级别：</span>
        {sevItems.map(([v, label]) => (
          <button
            key={v}
            className={`filter-chip ${SEV_CLASS[v]} ${
              sevFilter.has(v) ? 'on' : ''}`}
            onClick={() => onToggleSev(v)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="qc-filter-group">
        <span className="muted small">类型：</span>
        {typeItems.map(([v, label]) => (
          <button
            key={v}
            className={`filter-chip type ${typeFilter.has(v) ? 'on' : ''}`}
            onClick={() => onToggleType(v)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="muted small qc-filter-count">显示 {shownCount} / {s.total}</div>
    </div>
  )
}

function ScanHistoryPanel({
  scans, selected, comparing, onToggle, onClear, onCompare, onOpen,
  windowMismatch,
}) {
  const selectedCount = selected.length
  const pickedWindow = selectedCount === 1
    ? scans.find((s) => s.id === selected[0])
    : null
  const canCompare = selectedCount === 2 && !windowMismatch
  return (
    <div className="panel qc-history">
      <div className="qc-history-head">
        <h3>🕘 扫描历史</h3>
        <span className="muted small">
          每次「开始质检」自动保存时间窗口、统计与问题快照，共 {scans.length} 次。
          勾选两次同窗口扫描可按 issueId 对比。
        </span>
        <div className="qc-history-actions">
          <button
            className="btn small"
            disabled={selectedCount === 0}
            onClick={onClear}
          >
            清除选择
          </button>
          <button
            className="btn small primary"
            disabled={!canCompare || comparing}
            onClick={onCompare}
            title={selectedCount !== 2
              ? '请勾选两次扫描'
              : windowMismatch
                ? '时间窗口不同，禁止比较'
                : '比较两次扫描'}
          >
            {comparing ? '对比中…' : `⚖️ 对比所选（${selectedCount}/2）`}
          </button>
        </div>
      </div>
      {windowMismatch && (
        <div className="qc-compare-warn">
          ⚠️ 所选两次扫描的时间窗口不同，禁止比较：请选择时间窗口
          （开始 / 结束）完全相同的两次扫描。
        </div>
      )}
      {scans.length === 0 ? (
        <div className="muted small qc-history-empty">
          还没有扫描记录：点击「🔎 开始质检」后会自动保存。
        </div>
      ) : (
        <table className="run-table qc-scan-table">
          <thead>
            <tr>
              <th>对比</th>
              <th>扫描时间</th>
              <th>时间窗口</th>
              <th>问题总数</th>
              <th>严重 / 警告 / 提示</th>
              <th>可裁剪</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {scans.map((s) => {
              const isSel = selected.includes(s.id)
              const incompatible = !!pickedWindow && !isSel
                && !sameWindow(pickedWindow, s)
              return (
                <tr
                  key={s.id}
                  className={[
                    isSel ? 'selected' : '',
                    incompatible ? 'qc-scan-incompatible' : '',
                  ].join(' ')}
                >
                  <td>
                    <input
                      type="checkbox"
                      checked={isSel}
                      disabled={!isSel && selectedCount >= 2}
                      title={incompatible
                        ? '与已选扫描的时间窗口不同，不能与之比较'
                        : '选择用于对比'}
                      onChange={() => onToggle(s)}
                    />
                  </td>
                  <td>{fmtDateTime(s.createdAt)}</td>
                  <td className="qc-window-cell">
                    {fmtTime(s.window.start)} – {fmtTime(s.window.end)}
                    {!s.explicitWindow && (
                      <span className="tag qc-win-tag">整条转写</span>
                    )}
                    {incompatible && (
                      <span className="tag danger qc-win-tag">窗口不同</span>
                    )}
                  </td>
                  <td>{s.summary.total}</td>
                  <td>
                    <span className="qc-num-error">{s.summary.error}</span>
                    {' / '}
                    <span className="qc-num-warn">{s.summary.warning}</span>
                    {' / '}
                    <span className="muted">{s.summary.info}</span>
                  </td>
                  <td>{s.summary.fixable}</td>
                  <td>
                    <button className="btn small" onClick={() => onOpen(s)}>
                      查看快照
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

const COMPARE_GROUPS = [
  ['newIssues', '🆕 新增问题（旧扫描没有、本次出现）', 'qc-group-new'],
  ['resolvedIssues', '✅ 已解决（旧扫描有、本次消失）', 'qc-group-resolved'],
  ['persistentIssues', '➡️ 仍存在（两次扫描都有）', 'qc-group-persistent'],
]

function CompareView({ comparison, onClose }) {
  const { window, base, target, counts } = comparison
  return (
    <div className="qc-compare">
      <div className="panel qc-compare-head">
        <div>
          <h3>
            ⚖️ 扫描对比 · 同时间窗口{' '}
            {fmtTime(window.start)} – {fmtTime(window.end)}
          </h3>
          <div className="muted small">
            基准（旧）：{fmtDateTime(base.createdAt)}，{base.summary.total} 个问题
            {'  →  '}
            对比（新）：{fmtDateTime(target.createdAt)}，{target.summary.total} 个问题
            <br />
            按稳定 issueId 比对：同一 issueId 记为「仍存在」，仅在新扫描中出现为
            「新增」，仅在旧扫描中出现为「已解决」。
          </div>
        </div>
        <button className="btn" onClick={onClose}>退出对比</button>
      </div>

      <div className="stats-grid qc-compare-counts">
        <div className="stat-box qc-count-new">
          <strong>新增</strong>
          <span>{counts.new}</span>
        </div>
        <div className="stat-box qc-count-resolved">
          <strong>已解决</strong>
          <span>{counts.resolved}</span>
        </div>
        <div className="stat-box qc-count-persistent">
          <strong>仍存在</strong>
          <span>{counts.persistent}</span>
        </div>
      </div>

      {COMPARE_GROUPS.map(([key, title, cls]) => {
        const issues = comparison[key]
        return (
          <div key={key} className={`panel qc-compare-group ${cls}`}>
            <h4>{title} · {issues.length}</h4>
            {issues.length === 0 ? (
              <div className="muted small">无</div>
            ) : (
              <div className="qc-issue-list">
                {issues.map((q) => (
                  <IssueCard
                    key={`${key}:${q.issueId}`}
                    issue={q}
                    readOnly
                    projectId=""
                  />
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function IssueCard({ issue, active, onLocate, onFix, projectId, readOnly }) {
  const meta = TYPE_META[issue.type] || { label: issue.type, icon: '⚠️' }
  const editorUrl = `/projects/${projectId}?ann=${
    issue.annotationIds[0] || issue.annotationId || ''
  }&focusStart=${issue.start}&focusEnd=${issue.end}`
  return (
    <div
      className={`card qc-issue ${SEV_CLASS[issue.severity]} ${
        active ? 'active' : ''}`}
    >
      <div className="qc-issue-head">
        <span className={`qc-sev-badge ${SEV_CLASS[issue.severity]}`}>
          {SEV_LABEL[issue.severity]}
        </span>
        <strong>{meta.icon} {meta.label}</strong>
        <span className="muted small qc-issue-range">
          {fmtTime(issue.start)} – {fmtTime(issue.end)}
        </span>
        <span className="qc-issue-id" title={issue.issueId}>
          {issue.issueId}
        </span>
      </div>
      <div className="qc-issue-message">{issue.message}</div>
      <IssueEvidence issue={issue} />
      {!readOnly && (
        <div className="card-actions">
          <button className="btn small primary" onClick={onLocate}>
            📍 在时间轴定位
          </button>
          {issue.fixable && issue.fix && (
            <button className="btn small" onClick={onFix} title="裁剪到转写覆盖范围并保存">
              ✂️ 一键裁剪到 {fmtTime(issue.fix.start)} – {fmtTime(issue.fix.end)} 并保存
            </button>
          )}
          <Link className="btn small" to={editorUrl}>
            在编辑页处理 →
          </Link>
        </div>
      )}
    </div>
  )
}

function IssueEvidence({ issue }) {
  const ev = issue.evidence || {}
  if (issue.type === 'out_of_bounds') {
    return (
      <div className="qc-evidence">
        {!!ev.uncoveredRanges?.length && (
          <div className="muted small">
            未覆盖区间：
            {ev.uncoveredRanges.map((r, i) => (
              <span key={i} className="tag danger qc-range-tag">
                {fmtTime(r.start)}–{fmtTime(r.end)}
              </span>
            ))}
          </div>
        )}
        {!!ev.coveredRanges?.length && (
          <div className="muted small">
            可保留区间：
            {ev.coveredRanges.map((r, i) => (
              <span key={i} className="tag qc-range-tag">
                {fmtTime(r.start)}–{fmtTime(r.end)}
              </span>
            ))}
          </div>
        )}
        {ev.transcriptCoverage && (
          <div className="muted small">
            转写覆盖：{fmtTime(ev.transcriptCoverage.start)} –{' '}
            {fmtTime(ev.transcriptCoverage.end)}
          </div>
        )}
      </div>
    )
  }
  if (issue.type === 'overlap') {
    return (
      <div className="qc-evidence">
        <div className="muted small">
          重叠 {ev.overlapSeconds}s：
          {(ev.ranges || []).map((r, i) => (
            <span key={i} className="tag danger qc-range-tag">
              {fmtTime(r.start)}–{fmtTime(r.end)}
            </span>
          ))}
        </div>
        <div className="qc-ann-evidence">
          {issue.annotations.map((a) => (
            <span key={a.id} className="tag">
              {a.topic || '（未命名主题）'}
              {' '}[{fmtTime(a.start)}–{fmtTime(a.end)}]
              {a.speaker ? ` · ${a.speaker}` : ''}
            </span>
          ))}
        </div>
      </div>
    )
  }
  // speaker mismatch / missing
  return (
    <div className="qc-evidence">
      <div className="muted small">
        {issue.type === 'speaker_mismatch'
          && <>校注说话人：<span className="tag danger">{ev.annotationSpeaker || '（空）'}</span></>}
        {' '}片段说话人：
        {(ev.segmentSpeakers || []).map((sp) => (
          <span key={sp} className="tag">{sp || '（无说话人）'}</span>
        ))}
      </div>
      <div className="qc-seg-evidence">
        {(ev.segments || []).map((s) => (
          <div key={s.id} className="hit-seg">
            <span className="muted small">
              [{fmtTime(s.start)}–{fmtTime(s.end)}] {s.speaker || '（无说话人）'}
              {s.coveredSeconds != null ? ` · 覆盖 ${s.coveredSeconds}s` : ''}：
            </span>{' '}
            {s.text || '（无文本）'}
          </div>
        ))}
      </div>
    </div>
  )
}
