const BASE = '/api';

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: options.body instanceof FormData
      ? undefined
      : { 'Content-Type': 'application/json' },
    ...options,
  });
  if (res.status === 204) return null;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new Error((data && data.detail) || `请求失败 (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export const api = {
  listProjects: () => request('/projects'),
  createProject: (name) =>
    request('/projects', { method: 'POST', body: JSON.stringify({ name }) }),
  getProject: (id) => request(`/projects/${id}`),
  saveProject: (id, { name, document, documentVersion }) =>
    request(`/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name, document, documentVersion }),
    }),
  deleteProject: (id) => request(`/projects/${id}`, { method: 'DELETE' }),
  exportUrl: (id) => `${BASE}/projects/${id}/export`,

  importTranscript: async (payload) => {
    if (payload instanceof FormData) {
      return request('/import', { method: 'POST', body: payload });
    }
    return request('/import', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  importIntoProject: (id, formData) =>
    request(`/projects/${id}/import`, { method: 'POST', body: formData }),

  merge: (payload) =>
    request('/merge', { method: 'POST', body: JSON.stringify(payload) }),

  // ----- rule-based batch annotation -----
  listRules: (projectId) =>
    request(`/projects/${projectId}/rules`),
  createRule: (projectId, rule) =>
    request(`/projects/${projectId}/rules`, {
      method: 'POST', body: JSON.stringify(rule),
    }),
  updateRule: (ruleId, rule) =>
    request(`/rules/${ruleId}`, { method: 'PUT', body: JSON.stringify(rule) }),
  deleteRule: (ruleId) =>
    request(`/rules/${ruleId}`, { method: 'DELETE' }),
  previewDraftRule: (projectId, rule) =>
    request(`/projects/${projectId}/rules/preview`, {
      method: 'POST', body: JSON.stringify({ rule }),
    }),
  previewRule: (ruleId) =>
    request(`/rules/${ruleId}/preview`, { method: 'POST' }),
  applyRule: (ruleId) =>
    request(`/rules/${ruleId}/apply`, { method: 'POST' }),
  listRuleRuns: (projectId) =>
    request(`/projects/${projectId}/rule-runs`),
  undoRuleRun: (runId) =>
    request(`/rule-runs/${runId}/undo`, { method: 'POST' }),

  // ----- annotation quality check -----
  qualityCheck: (projectId, window = null) =>
    request(`/projects/${projectId}/quality-check`, {
      method: 'POST',
      body: JSON.stringify(window || {}),
    }),
  listQualityScans: (projectId) =>
    request(`/projects/${projectId}/quality-scans`),
  getQualityScan: (projectId, scanId) =>
    request(`/projects/${projectId}/quality-scans/${scanId}`),
  compareQualityScans: (projectId, scanIdA, scanIdB) =>
    request(`/projects/${projectId}/quality-scans/compare`, {
      method: 'POST',
      body: JSON.stringify({ scanIdA, scanIdB }),
    }),

  // ----- quote search -----
  quoteSearch: (projectId, { query, speaker = null, caseSensitive = false,
    document = null }) =>
    request(`/projects/${projectId}/quote-search`, {
      method: 'POST',
      body: JSON.stringify({ query, speaker, caseSensitive, document }),
    }),

  // ----- speaker normalization -----
  previewSpeakerRename: (projectId, mappings) =>
    request(`/projects/${projectId}/speaker-rename/preview`, {
      method: 'POST',
      body: JSON.stringify({ mappings }),
    }),
  renameSpeakers: (projectId, mappings) =>
    request(`/projects/${projectId}/speaker-rename`, {
      method: 'POST',
      body: JSON.stringify({ mappings }),
    }),

  // ----- excerpt package (摘录包) -----
  previewExcerpts: (projectId, ranges) =>
    request(`/projects/${projectId}/excerpts/preview`, {
      method: 'POST',
      body: JSON.stringify({ ranges }),
    }),
  excerptsDownloadUrl: (projectId) =>
    `${BASE}/projects/${projectId}/excerpts/download`,

  // ----- public transcript (公开版转写) -----
  previewPublicTranscript: (projectId, payload) =>
    request(`/projects/${projectId}/public-transcript/preview`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  publicTranscriptDownloadUrl: (projectId) =>
    `${BASE}/projects/${projectId}/public-transcript/download`,

  // ----- transcript error-word batch correction (转写错词批量校正) -----
  previewCorrections: (projectId, payload) =>
    request(`/projects/${projectId}/corrections/preview`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  applyCorrections: (projectId, payload) =>
    request(`/projects/${projectId}/corrections/apply`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // ----- annotation coverage gaps (校注覆盖盲区) -----
  coverageGaps: (projectId, speaker = null) => {
    const qs = speaker
      ? `?speaker=${encodeURIComponent(speaker)}`
      : ''
    return request(`/projects/${projectId}/coverage${qs}`)
  },
};
