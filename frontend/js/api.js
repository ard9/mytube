/* api.js — thin wrappers around the backend REST API. */

async function json(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// fetch with an abort timeout so a hung AI provider surfaces an error
// instead of leaving the UI spinning forever.
function fetchT(url, opts, ms) {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), ms || 90000);
  return fetch(url, { ...(opts || {}), signal: ctrl.signal })
    .catch((e) => {
      if (e.name === 'AbortError') {
        throw new Error('The tutor took too long to respond. Check your AI provider / connection in AI settings.');
      }
      throw e;
    })
    .finally(() => clearTimeout(id));
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
  // Create a card from text and (optionally) generate + attach its TTS audio.
  dictWithAudio: (body) =>
    fetch('/api/dictionary/with_audio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json),
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

  // Text-to-speech (StyleTTS2 + gTTS)
  ttsAvailable: () => fetch('/api/tts/available').then(json),
  ttsGttsLanguages: () => fetch('/api/tts/gtts_languages').then(json),
  startTts: (body) =>
    fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json),
  getTtsStatus: (jobId) => fetch(`/api/tts/${jobId}`).then(json),
  cancelTts: (jobId) => fetch(`/api/tts/${jobId}/cancel`, { method: 'POST' }).then(json),
  getTtsLibrary: () => fetch('/api/tts/library').then(json),
  deleteTtsEntry: (id) => fetch(`/api/tts/library/${id}`, { method: 'DELETE' }).then(json),
  ttsToDictionary: (id, meaning) =>
    fetch(`/api/tts/library/${id}/to_dictionary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ meaning: meaning || '' }),
    }).then(json),
  ttsSegmentToDictionary: (id, fromIndex, toIndex, meaning) =>
    fetch(`/api/tts/library/${id}/segment_to_dictionary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_index: fromIndex, to_index: toIndex, meaning: meaning || '' }),
    }).then(json),
  ttsMediaUrl: (id) => `/api/tts/media?id=${encodeURIComponent(id)}`,
  getTtsVoices: () => fetch('/api/tts/voices').then(json),
  addTtsVoice: (name, file) => {
    const fd = new FormData();
    fd.append('name', name || '');
    fd.append('file', file);
    return fetch('/api/tts/voices', { method: 'POST', body: fd }).then(json);
  },
  deleteTtsVoice: (id) => fetch(`/api/tts/voices/${id}`, { method: 'DELETE' }).then(json),

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

  // Live conversation (speaking-practice agent)
  convAvailable: () => fetch('/api/conversation/available').then(json),
  convWhisperInfo: () => fetch('/api/conversation/whisper_info').then(json),
  convGetSettings: () => fetch('/api/conversation/settings').then(json),
  convSetSettings: (body) =>
    fetch('/api/conversation/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json),
  convSessions: () => fetch('/api/conversation/sessions').then(json),
  convCreateSession: () => fetch('/api/conversation/sessions', { method: 'POST' }).then(json),
  convGetSession: (id) => fetch(`/api/conversation/sessions/${id}`).then(json),
  convDeleteSession: (id) => fetch(`/api/conversation/sessions/${id}`, { method: 'DELETE' }).then(json),
  convRenameSession: (id, title) =>
    fetch(`/api/conversation/sessions/${id}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }).then(json),
  convGreeting: (id) =>
    fetchT(`/api/conversation/sessions/${id}/greeting`, { method: 'POST' }, 90000).then(json),
  convSendMessage: (id, text) =>
    fetchT(`/api/conversation/sessions/${id}/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }, 90000).then(json),
  convStt: (blob, model, vadFilter, partial) => {
    const fd = new FormData();
    fd.append('file', blob, blob.type && blob.type.includes('wav') ? 'clip.wav' : 'clip.webm');
    fd.append('model', model || '');
    fd.append('vad_filter', vadFilter === false ? 'false' : 'true');
    fd.append('partial', partial ? 'true' : 'false');
    return fetch('/api/conversation/stt', { method: 'POST', body: fd }).then(json);
  },
  ollamaStatus: (baseUrl) =>
    fetch(`/api/conversation/ollama/status?base_url=${encodeURIComponent(baseUrl || '')}`).then(json),
  ollamaPull: (model, baseUrl) =>
    fetch('/api/conversation/ollama/pull', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, base_url: baseUrl || '' }),
    }).then(json),
  ollamaPullStatus: (jobId) => fetch(`/api/conversation/ollama/pull/${jobId}`).then(json),
};
