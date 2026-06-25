/* api.js — thin wrappers around the backend REST API. */

async function json(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  // Config
  getConfig: () => fetch('/api/config').then(json),
  setConfig: (body) =>
    fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json),

  // Library
  getLibrary: () => fetch('/api/library').then(json),
  videoUrl: (path) => `/api/video?path=${encodeURIComponent(path)}`,
  thumbUrl: (path) => `/api/thumb?path=${encodeURIComponent(path)}`,
  genThumbUrl: (path) => `/api/genthumb?path=${encodeURIComponent(path)}`,
  subtitleUrl: (path) => `/api/subtitle?path=${encodeURIComponent(path)}`,

  // Notes
  getNotes: () => fetch('/api/notes').then(json),
  saveNote: (path, text) =>
    fetch('/api/notes', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, text }),
    }).then(json),

  // Manage: rename / delete
  renameVideo: (path, title) =>
    fetch('/api/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, title }),
    }).then(json),
  deleteVideo: (path) =>
    fetch('/api/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    }).then(json),

  // Subtitle search
  searchSubtitles: (q) => fetch(`/api/subtitle_search?q=${encodeURIComponent(q)}`).then(json),
  searchSubtitleInVideo: (path, q) =>
    fetch(`/api/subtitle_search_in_video?path=${encodeURIComponent(path)}&q=${encodeURIComponent(q)}`).then(json),

  // Dictionary (words / sentences, with optional audio/image/video clips)
  getDictionary: () => fetch('/api/dictionary').then(json),
  addDictionaryEntry: (body) =>
    fetch('/api/dictionary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json),
  updateDictionaryEntry: (id, body) =>
    fetch(`/api/dictionary/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json),
  deleteDictionaryEntry: (id) =>
    fetch(`/api/dictionary/${id}`, { method: 'DELETE' }).then(json),
  // Spaced repetition
  getDictStats: () => fetch('/api/dictionary/stats').then(json),
  getStudyCards: (limit = 0, includeNew = true) =>
    fetch(`/api/dictionary/study?limit=${limit}&new=${includeNew}`).then(json),
  reviewDictEntry: (id, rating) =>
    fetch(`/api/dictionary/${id}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating }),
    }).then(json),
  dictMediaUrl: (file) => `/api/dictionary/media?file=${encodeURIComponent(file)}`,
  uploadDictMedia: (id, kind, file) => {
    const fd = new FormData();
    fd.append('kind', kind);
    fd.append('file', file);
    return fetch(`/api/dictionary/${id}/media`, { method: 'POST', body: fd }).then(json);
  },
  removeDictMedia: (id, kind) =>
    fetch(`/api/dictionary/${id}/media/${kind}`, { method: 'DELETE' }).then(json),

  // Local subtitle generation (Whisper)
  transcribeAvailable: () => fetch('/api/transcribe/available').then(json),
  startTranscribe: (path, language, model, translate, modelPath) =>
    fetch('/api/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, language, model, translate, model_path: modelPath || '' }),
    }).then(json),
  getTranscribeJobs: () => fetch('/api/transcribe/jobs').then(json),
  getTranscribeStatus: (jobId) => fetch(`/api/transcribe/${jobId}`).then(json),
  cancelTranscribe: (jobId) => fetch(`/api/transcribe/${jobId}/cancel`, { method: 'POST' }).then(json),

  // Downloads
  startDownload: (url, quality, category, subtitles) =>
    fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, quality, category, subtitles }),
    }).then(json),
  getDownloads: () => fetch('/api/downloads').then(json),
  cancelDownload: (id) => fetch(`/api/downloads/${id}/cancel`, { method: 'POST' }).then(json),

  // Watch progress
  getProgress: () => fetch('/api/watch').then(json),
  saveProgress: (path, position, duration) =>
    fetch('/api/watch', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, position, duration }),
    }).then(json),
  setWatched: (path, watched) =>
    fetch('/api/watch/flag', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, watched }),
    }).then(json),
};
