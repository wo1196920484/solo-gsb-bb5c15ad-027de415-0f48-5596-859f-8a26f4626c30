let counter = 0;

function rand() {
  counter += 1;
  return `${Date.now().toString(36)}${counter.toString(36)}${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

export const newSegmentId = () => `seg-${rand()}`;
export const newAnnotationId = () => `ann-${rand()}`;

export function emptyDocument() {
  return { segments: [], annotations: [] };
}

export function makeSegment(start, end, speaker = '', text = '') {
  return { id: newSegmentId(), start, end, speaker, text };
}

export function makeAnnotation(start, end, speaker = '', topic = '', note = '') {
  return { id: newAnnotationId(), start, end, speaker, topic, note };
}

export function fmtTime(sec) {
  if (!Number.isFinite(sec)) return '--:--';
  const total = Math.max(0, Math.round(sec * 1000)) / 1000;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n, w = 2) => String(Math.floor(n)).padStart(w, '0');
  const ms = Math.round((s - Math.floor(s)) * 1000);
  const base = h > 0
    ? `${pad(h)}:${pad(m)}:${pad(s)}`
    : `${pad(m)}:${pad(s)}`;
  return ms ? `${base}.${String(ms).padStart(3, '0')}` : base;
}

export function round3(n) {
  return Math.round(n * 1000) / 1000;
}

/**
 * Format a duration given in seconds: keep up to 3 decimal places
 * (millisecond precision) and trim trailing zeros.
 * e.g. 12 -> "12", 12.5 -> "12.5", 3.1415 -> "3.142"
 */
export function fmtSeconds(sec) {
  if (!Number.isFinite(sec)) return '–';
  const ms = Math.max(0, Math.round(sec * 1000));
  return (ms / 1000).toFixed(3).replace(/\.?0+$/, '');
}

/** Download a JSON document in the browser. */
export function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Read a File as text. */
export function readFileText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsText(file, 'utf-8');
  });
}

/** Deep clone a document. */
export const clone = (doc) => JSON.parse(JSON.stringify(doc));
