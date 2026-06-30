/* app.js — entrypoint: loads data, wires events, handles routing + progress. */

import { api } from './api.js';
import { state, isWatched, esc, fmtTimestamp } from './state.js';
import * as ui from './ui.js';
import * as dl from './download.js';
import * as conv from './conversation.js';

const $ = (id) => document.getElementById(id);

window.MyTube = {
  goRoute,
  selectCategory,
  selectChannel,
  openWatch,
  reloadLibrary: loadLibrary,
  // dictionary
  openAddToDict,
  openEditDict,
  deleteDictEntry,
  jumpToSource,
  // study (spaced repetition)
  startStudy,
  flipStudy,
  rateStudy,
  endStudy,
  // text-to-speech
  deleteTtsVoice,
  deleteTtsEntry,
  ttsToFlashcards,
  ttsAddSegment,
  // shared helpers used by other modules (e.g. conversation.js)
  showToast: (msg) => showToast(msg),
  refreshDictionary: async () => { await loadDictionary(); ui.renderDictionary(); },
};

/* ---------- routing ---------- */
function goRoute(name) {
  $('player').pause();
  closeSubgenBox();
  clearTimeout(findTimer);
  if (name === 'home') {
    state.view = { category: null, channel: null };
    state.filter = 'all';
    setActiveNav({ route: 'home' });
    syncFilterChips();
    ui.showView('browse');
    ui.renderSidebar();
    ui.renderGrid();
  } else if (name === 'add') {
    setActiveNav({ route: 'add' });
    ui.showView('add');
    dl.updateCommandPreview();
  } else if (name === 'downloads') {
    setActiveNav({ route: 'downloads' });
    ui.showView('downloads');
    dl.refreshJobs();
  } else if (name === 'dictionary') {
    setActiveNav({ route: 'dictionary' });
    ui.showView('dictionary');
    ui.renderDictionary();                       // show what we have immediately
    loadDictionary().then(() => ui.renderDictionary());  // then refresh from server
  } else if (name === 'tts') {
    setActiveNav({ route: 'tts' });
    ui.showView('tts');
    ui.renderTts();                              // show what we have immediately
    openTtsPage();                               // refresh availability + library + voices
  } else if (name === 'conversation') {
    setActiveNav({ route: 'conversation' });
    ui.showView('conversation');
    conv.openConversationPage();
  } else if (name === 'study') {
    setActiveNav({ route: 'study' });
    ui.showView('study');
    // If a session is already running, keep it; otherwise show the lobby and
    // refresh stats so the "cards due" count is current.
    ui.renderStudy();
    if (!state.study.active) {
      loadDictionary().then(() => ui.renderStudy());
    }
  } else if (name === 'settings') {
    setActiveNav({ route: 'settings' });
    $('setLibrary').value = state.config.library_path || '';
    $('setBin').value = state.config.ytdlp_bin || '';
    ui.showView('settings');
  }
}

function selectCategory(name) {
  state.view = { category: name, channel: null };
  state.filter = 'all';
  setActiveNav({});
  syncFilterChips();
  ui.showView('browse');
  ui.renderSidebar();
  ui.renderGrid();
}

function selectChannel(category, channel) {
  state.view = { category, channel };
  state.filter = 'all';
  setActiveNav({});
  syncFilterChips();
  ui.showView('browse');
  ui.renderSidebar();
  ui.renderGrid();
}

function setFilter(filter) {
  state.filter = filter;
  state.view = { category: null, channel: null };
  setActiveNav(filter === 'notes' ? { filter: 'notes' } : { route: 'home' });
  syncFilterChips();
  ui.showView('browse');
  ui.renderSidebar();
  ui.renderGrid();
}

async function openWatch(video, jumpTo = null) {
  ui.showView('watch');
  window.scrollTo(0, 0);
  await ui.renderWatch(video, jumpTo);
  attachProgressTracking();
  attachAutoplayOnEnd();
}

function setActiveNav({ route, filter } = {}) {
  document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
  if (route) document.querySelector(`.nav-item[data-route="${route}"]`)?.classList.add('active');
  if (filter) document.querySelector(`.nav-item[data-filter="${filter}"]`)?.classList.add('active');
}

function syncFilterChips() {
  document.querySelectorAll('.chip[data-filter]').forEach((c) =>
    c.classList.toggle('active', c.dataset.filter === state.filter)
  );
}

/* ---------- data loading ---------- */
async function loadConfig() {
  try { state.config = await api.getConfig(); } catch { state.config = {}; }
  state.quality = state.config.default_quality || '720';
}

async function loadLibrary() {
  ui.loadingGrid();
  try {
    const [lib, notes, prog] = await Promise.all([
      api.getLibrary(), api.getNotes(), api.getProgress(),
    ]);
    state.categories = lib.categories || [];
    state.allVideos = state.categories.flatMap((c) => c.channels.flatMap((ch) => ch.videos));
    state.notes = notes || {};
    state.progress = prog || {};
    state.config.library_path = lib.library_path;
  } catch (e) {
    $('grid').innerHTML = `<div class="empty"><div class="big">&#9888;</div>
      <h2>Could not reach the server</h2>
      <p>${e.message}. Make sure the backend is running.</p></div>`;
    return;
  }
  ui.renderSidebar();
  ui.renderGrid();
}

async function loadDictionary() {
  try {
    const res = await api.getDictionary();
    state.dictionary = res.entries || [];
    state.dictFfmpeg = res.ffmpeg !== false;
    state.dictStats = res.stats || null;
  } catch {
    state.dictionary = [];
    state.dictStats = null;
  }
  ui.renderDictHeader();   // keep the sidebar/topbar "due" badges current
}

async function loadTtsLibrary() {
  try {
    const res = await api.getTtsLibrary();
    state.ttsLibrary = res.entries || [];
  } catch {
    state.ttsLibrary = [];
  }
}

async function loadTtsVoices() {
  try {
    const res = await api.getTtsVoices();
    state.ttsVoices = res.voices || [];
  } catch {
    state.ttsVoices = [];
  }
}

/* ---------- watch progress tracking ---------- */
let progressTimer = null;
function attachProgressTracking() {
  const player = $('player');
  clearInterval(progressTimer);

  const save = () => {
    if (!state.current || !player.duration || isNaN(player.duration)) return;
    const path = state.current.path;
    const pos = player.currentTime, dur = player.duration;
    // update local cache immediately so the UI reflects it
    const prev = state.progress[path] || {};
    state.progress[path] = {
      position: pos, duration: dur,
      watched: prev.watched || pos >= dur * 0.9,
    };
    api.saveProgress(path, pos, dur).then(() => ui.updateWatchedButton()).catch(() => {});
  };

  // periodic save while playing
  progressTimer = setInterval(() => { if (!player.paused) save(); }, 5000);
  player.onpause = save;
}

/* ---------- autoplay: advance to the next "up next" video when one ends ---------- */
function attachAutoplayOnEnd() {
  const player = $('player');
  player.onended = () => {
    if (!state.current) return;
    const path = state.current.path;
    state.progress[path] = {
      position: player.duration, duration: player.duration, watched: true,
    };
    api.setWatched(path, true).then(() => ui.updateWatchedButton()).catch(() => {});

    if (state.autoplay) {
      const next = nextUpNextVideo(state.current);
      if (next) {
        setTimeout(() => openWatch(next), 600);
      }
    }
  };
}

function nextUpNextVideo(active) {
  const sameChannel = state.allVideos.filter(
    (v) => v.uploader === active.uploader && v.path !== active.path
  );
  if (sameChannel.length) return sameChannel[0];
  const others = state.allVideos.filter((v) => v.uploader !== active.uploader);
  others.sort((a, b) => (b.upload_date || '').localeCompare(a.upload_date || ''));
  return others[0] || null;
}

async function toggleWatched() {
  if (!state.current) return;
  const path = state.current.path;
  const next = !isWatched(state.current);
  const prev = state.progress[path] || { position: 0, duration: 0 };
  state.progress[path] = { ...prev, watched: next, position: next ? prev.position : 0 };
  ui.updateWatchedButton();
  try { await api.setWatched(path, next); } catch {}
}

/* ---------- subtitle ("CC") search ---------- */
let subSearchTimer = null;
async function runSubtitleSearch() {
  const q = state.search.trim();
  if (!q) { state.subResults = {}; ui.renderGrid(); return; }
  try {
    const res = await api.searchSubtitles(q);
    const map = {};
    (res.results || []).forEach((r) => { map[r.video_path] = r.matches; });
    state.subResults = map;
  } catch {
    state.subResults = {};
  }
  ui.renderGrid();
}

/* ---------- "Find in this video" search (within the currently open video only) ---------- */
let findTimer = null;
async function runFindInVideo() {
  if (!state.current) return;
  const q = $('findInput').value.trim();
  if (!q) { ui.renderFindResults([], ''); return; }
  let matches = [];
  try {
    const res = await api.searchSubtitleInVideo(state.current.path, q);
    matches = res.matches || [];
  } catch {
    matches = [];
  }
  ui.renderFindResults(matches, q);
}

/* ---------- notes saving (debounced) ---------- */
let noteTimer;
async function saveNote() {
  if (!state.current) return;
  const text = $('notes').value;
  state.notes[state.current.path] = text;
  try { await api.saveNote(state.current.path, text); } catch {}
  flash('savedTag');
  ui.updateNotesMeta();
}
function flash(id) {
  const el = $(id);
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 1400);
}

/* ---------- settings ---------- */
async function saveSettings() {
  const body = { library_path: $('setLibrary').value.trim(), ytdlp_bin: $('setBin').value.trim() };
  try {
    state.config = await api.setConfig(body);
    flash('settingsSaved');
    await loadLibrary();
  } catch (e) {
    alert('Could not save settings: ' + e.message);
  }
}

/* ---------- playback speed ---------- */
function setPlaybackRate(rate) {
  state.playbackRate = rate;
  localStorage.setItem('mytube_rate', String(rate));
  $('player').playbackRate = rate;
  ui.updateSpeedUI();
  $('speedMenu').classList.remove('show');
}

/* ---------- autoplay toggle ---------- */
function toggleAutoplay() {
  state.autoplay = !state.autoplay;
  localStorage.setItem('mytube_autoplay', state.autoplay ? 'on' : 'off');
  ui.updateAutoplayUI();
}

/* ---------- rename ---------- */
function openRenameModal() {
  if (!state.current) return;
  $('renameInput').value = state.current.title;
  $('renameModal').classList.add('show');
  $('renameInput').focus();
  $('renameInput').select();
}
function closeRenameModal() {
  $('renameModal').classList.remove('show');
}
async function confirmRename() {
  if (!state.current) return;
  const title = $('renameInput').value.trim();
  if (!title) return;
  try {
    const result = await api.renameVideo(state.current.path, title);
    closeRenameModal();
    await loadLibrary();
    const updated = state.allVideos.find((v) => v.path === result.new_path);
    if (updated) openWatch(updated);
  } catch (e) {
    alert('Could not rename: ' + e.message);
  }
}

/* ---------- delete ---------- */
function openDeleteModal() {
  if (!state.current) return;
  $('deleteTarget').textContent = state.current.title;
  $('deleteModal').classList.add('show');
}
function closeDeleteModal() {
  $('deleteModal').classList.remove('show');
}
async function confirmDelete() {
  if (!state.current) return;
  try {
    await api.deleteVideo(state.current.path);
    closeDeleteModal();
    goRoute('home');
    await loadLibrary();
  } catch (e) {
    alert('Could not delete: ' + e.message);
  }
}

/* ---------- local subtitle generation (Whisper) ---------- */
let subgenPollTimer = null;
let subgenAvailability = null; // cached {available, models, default_model}

function selectedSubgenLanguage() {
  const custom = $('subgenCustomLang').value.trim();
  if (custom) return custom;
  const active = $('subgenLangRow').querySelector('.q-btn.active');
  return active ? active.dataset.lang : '';
}
function selectedSubgenModel() {
  const active = $('subgenModelRow').querySelector('.q-btn.active');
  return active ? active.dataset.model : 'small';
}
function selectedSubgenModelPath() {
  return $('subgenUseCustomPath').checked ? $('subgenCustomPath').value.trim() : '';
}

async function openSubgenBox() {
  if (!state.current) return;
  $('subgenBox').hidden = false;
  $('subgenForm').hidden = false;
  $('subgenProgress').hidden = true;
  $('subgenAvailHint').textContent = '';
  $('subgenUseCustomPath').checked = false;
  $('subgenCustomPath').hidden = true;
  $('subgenCustomPath').value = '';
  $('subgenCustomPathHint').hidden = true;

  if (!subgenAvailability) {
    try { subgenAvailability = await api.transcribeAvailable(); } catch { subgenAvailability = { available: false }; }
  }
  if (!subgenAvailability.available) {
    $('subgenAvailHint').textContent =
      "Local subtitle generation needs faster-whisper on the server. Run 'pip install faster-whisper' and restart MyTube, then try again.";
    $('subgenStart').disabled = true;
  } else {
    $('subgenStart').disabled = false;
  }
}

function closeSubgenBox() {
  $('subgenBox').hidden = true;
  clearInterval(subgenPollTimer);
  subgenPollTimer = null;
}

async function startSubgen() {
  if (!state.current) return;
  const btn = $('subgenStart');
  btn.disabled = true;
  try {
    const job = await api.startTranscribe(
      state.current.path,
      selectedSubgenLanguage(),
      selectedSubgenModel(),
      $('subgenTranslate').checked,
      selectedSubgenModelPath(),
    );
    $('subgenForm').hidden = true;
    $('subgenProgress').hidden = false;
    $('subgenFill').style.width = '0%';
    $('subgenFill').classList.add('busy');
    $('subgenStatus').textContent = 'Starting…';
    pollSubgen(job.id);
  } catch (e) {
    $('subgenAvailHint').textContent = 'Could not start: ' + e.message;
    btn.disabled = false;
  }
}

function pollSubgen(jobId) {
  clearInterval(subgenPollTimer);
  subgenPollTimer = setInterval(async () => {
    let job;
    try { job = await api.getTranscribeStatus(jobId); } catch { return; }

    const isBusyStage = job.stage === 'loading_model' || job.stage === 'analyzing' || (job.stage === 'transcribing' && !job.percent);
    $('subgenFill').classList.toggle('busy', isBusyStage);
    if (!isBusyStage) $('subgenFill').style.width = `${job.percent || 0}%`;

    const stageText = {
      queued: 'Queued…',
      loading_model: 'Loading Whisper model (first time may download it — this can take a while)…',
      analyzing: 'Reading audio…',
      transcribing: `Transcribing… ${job.percent || 0}%${job.segments_written ? ` · ${job.segments_written} lines so far` : ''}${job.detected_language ? ' · detected: ' + job.detected_language : ''}`,
      done: 'Done! Reloading…',
    }[job.stage];

    const statusText = job.status === 'error' ? 'Error: ' + (job.error || 'unknown error')
      : job.status === 'cancelled' ? 'Cancelled.'
      : (stageText || job.status);
    $('subgenStatus').textContent = statusText;

    if (job.status === 'done') {
      clearInterval(subgenPollTimer);
      subgenPollTimer = null;
      await loadLibrary();
      const updated = state.allVideos.find((v) => v.path === state.current.path);
      if (updated) {
        closeSubgenBox();
        openWatch(updated);
      }
    } else if (job.status === 'error' || job.status === 'cancelled') {
      clearInterval(subgenPollTimer);
      subgenPollTimer = null;
      $('subgenStart').disabled = false;
    }
  }, 1500);
}

async function cancelSubgenJob() {
  // Find the most recent job for the current video via the status text's job id stash.
  const jobs = await api.getTranscribeJobs().catch(() => []);
  const mine = jobs.find((j) => j.video_path === state.current?.path && (j.status === 'running' || j.status === 'queued'));
  if (mine) await api.cancelTranscribe(mine.id).catch(() => {});
  clearInterval(subgenPollTimer);
  subgenPollTimer = null;
  $('subgenStatus').textContent = 'Cancelled.';
}

/* ---------- dictionary ---------- */
let dictMode = 'create';   // 'create' | 'edit'
let dictPayload = null;    // {text,start,end,path,title} for from-video, or null for manual
let dictEditId = null;

const CAP_IDS = ['dictCapAudio', 'dictCapImage', 'dictCapVideo'];
const UPLOAD_IDS = [['dictUpAudio', 'audio'], ['dictUpImage', 'image'], ['dictUpVideo', 'video']];
let dictEditEntry = null;   // the entry currently open in the edit modal

function showDictModal(focusMeaning) {
  $('dictModal').classList.add('show');
  const f = focusMeaning ? $('dictMeaning') : $('dictText');
  f.focus();
  if (!focusMeaning) f.select();
}
function closeDictModal() {
  $('dictModal').classList.remove('show');
}

function clearDictUploads() {
  UPLOAD_IDS.forEach(([id]) => {
    $(id).value = '';
    $(`${id}Name`).textContent = '';
  });
}

// Show the media already attached to an entry (edit mode), with remove buttons.
function renderExistingMedia(entry) {
  const box = $('dictExistingMedia');
  const m = entry.media || {};
  const labels = { audio: '\u{1F50A} Audio', image: '\u{1F4F7} Image', video: '\u{1F3AC} Video' };
  const chips = Object.keys(labels)
    .filter((k) => m[k])
    .map((k) => `<span class="media-chip">${labels[k]}<button data-rm="${k}" title="Remove">&#10005;</button></span>`)
    .join('');
  box.innerHTML = chips ? `<div class="media-chips">${chips}</div>` : '';
  box.querySelectorAll('[data-rm]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        dictEditEntry = await api.removeDictMedia(dictEditEntry.id, btn.dataset.rm);
        renderExistingMedia(dictEditEntry);
        await loadDictionary();
        ui.renderDictionary();
      } catch (e) {
        alert('Could not remove media: ' + e.message);
      }
    };
  });
}

async function uploadChosenFiles(id) {
  for (const [inputId, kind] of UPLOAD_IDS) {
    const f = $(inputId).files[0];
    if (f) await api.uploadDictMedia(id, kind, f);
  }
}
function hasChosenFiles() {
  return UPLOAD_IDS.some(([id]) => $(id).files.length);
}

// payload = {text,start,end,path,title} to add from a video; null = manual add.
function openAddToDict(payload) {
  dictMode = 'create';
  dictPayload = payload;
  dictEditId = null;
  dictEditEntry = null;

  const hasSource = !!(payload && payload.path);
  $('dictModalTitle').textContent = hasSource ? 'Add to dictionary' : 'Add a word or sentence';
  $('dictText').value = (payload && payload.text) || '';
  $('dictMeaning').value = '';

  $('dictSourceLine').hidden = !hasSource;
  $('dictMediaOpts').hidden = !hasSource;   // capture checkboxes only when there's a video
  $('dictUploadOpts').hidden = hasSource;   // file uploads for manual entries
  $('dictExistingMedia').innerHTML = '';
  clearDictUploads();

  if (hasSource) {
    $('dictSourceLine').innerHTML =
      `From <b>${esc(payload.title || payload.path)}</b> &middot; ${fmtTimestamp(payload.start || 0)}`;
    const isAudio = payload.media_type === 'audio';
    $('dictCapAudio').checked = true;
    $('dictCapImage').checked = false;
    $('dictCapVideo').checked = false;
    // Audio sources have no picture, so only the audio clip can be captured.
    CAP_IDS.forEach((id) => {
      const audioOnlyDisabled = isAudio && (id === 'dictCapImage' || id === 'dictCapVideo');
      $(id).disabled = !state.dictFfmpeg || audioOnlyDisabled;
    });
    $('dictCapHint').textContent = !state.dictFfmpeg
      ? "ffmpeg isn't installed on the server, so clips can't be captured — the text & meaning will still be saved."
      : isAudio
        ? 'An audio clip is cut from this file around this line (audio has no picture, so image/video are unavailable).'
        : 'Clips are cut from the video around this subtitle line.';
  }
  showDictModal(hasSource);  // a sentence is pre-filled → jump straight to the meaning field
}

function openEditDict(entry) {
  dictMode = 'edit';
  dictEditId = entry.id;
  dictEditEntry = entry;
  dictPayload = null;

  $('dictModalTitle').textContent = 'Edit entry';
  $('dictText').value = entry.text;
  $('dictMeaning').value = entry.meaning || '';

  if (entry.source) {
    $('dictSourceLine').hidden = false;
    $('dictSourceLine').innerHTML =
      `From <b>${esc(entry.source.title || entry.source.path)}</b> &middot; ${fmtTimestamp(entry.source.start || 0)}`;
  } else {
    $('dictSourceLine').hidden = true;
  }
  $('dictMediaOpts').hidden = true;     // editing doesn't re-capture from the video
  $('dictUploadOpts').hidden = false;   // but you can attach/replace/remove media here
  clearDictUploads();
  renderExistingMedia(entry);
  showDictModal(true);
}

async function saveDict() {
  const text = $('dictText').value.trim();
  if (!text) { $('dictText').focus(); return; }
  const meaning = $('dictMeaning').value;
  const btn = $('dictModalSave');
  const label = btn.textContent;
  btn.disabled = true;

  try {
    if (dictMode === 'edit') {
      await api.updateDictionaryEntry(dictEditId, { text, meaning });
      if (hasChosenFiles()) { btn.textContent = 'Uploading…'; await uploadChosenFiles(dictEditId); }
      closeDictModal();
      showToast('Entry updated.');
    } else if (dictPayload && dictPayload.path) {
      // From a video: capture clips server-side.
      const capture = CAP_IDS
        .filter((id) => $(id).checked && !$(id).disabled)
        .map((id) => ({ dictCapAudio: 'audio', dictCapImage: 'image', dictCapVideo: 'video' }[id]));
      if (capture.length) btn.textContent = 'Capturing clip…';
      const res = await api.addDictionaryEntry({
        text, meaning,
        path: dictPayload.path, title: dictPayload.title || '',
        start: dictPayload.start || 0, end: dictPayload.end || 0, capture,
      });
      closeDictModal();
      showToast(res && res._warning ? res._warning : 'Saved to dictionary.');
    } else {
      // Manual: create the entry, then upload any attached files.
      const res = await api.addDictionaryEntry({ text, meaning });
      if (hasChosenFiles()) { btn.textContent = 'Uploading…'; await uploadChosenFiles(res.id); }
      closeDictModal();
      showToast('Saved to dictionary.');
    }
    await loadDictionary();
    ui.renderDictionary();   // harmless if the dictionary view isn't visible
  } catch (e) {
    alert('Could not save: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

async function deleteDictEntry(entry) {
  const preview = entry.text.length > 60 ? entry.text.slice(0, 60) + '…' : entry.text;
  if (!confirm(`Delete "${preview}" from your dictionary?\nAny saved audio/image/video clip will be removed too.`)) return;
  try {
    await api.deleteDictionaryEntry(entry.id);
    await loadDictionary();
    ui.renderDictionary();
  } catch (e) {
    alert('Could not delete: ' + e.message);
  }
}

function jumpToSource(path, time) {
  const v = state.allVideos.find((x) => x.path === path);
  if (v) openWatch(v, time);
  else showToast("That video isn't in your library anymore — the saved clip is still here.");
}

/* ---------- study session (spaced repetition) ---------- */
async function startStudy() {
  const shell = $('studyShell');
  if (shell) shell.innerHTML = '<div class="study-lobby"><div class="study-loading">Building your session…</div></div>';
  try {
    const res = await api.getStudyCards();
    state.dictStats = res.stats || state.dictStats;
    const cards = res.cards || [];
    if (!cards.length) {
      state.study.active = false;
      ui.renderStudy();
      ui.renderDictHeader();
      return;
    }
    state.study = {
      active: true,
      queue: cards,
      index: 0,
      flipped: false,
      done: 0,
      again: 0,
      total: cards.length,
    };
    ui.showView('study');
    setActiveNav({ route: 'study' });
    ui.renderStudy();
  } catch (e) {
    showToast('Could not start studying: ' + e.message);
    state.study.active = false;
    ui.renderStudy();
  }
}

function flipStudy() {
  if (!state.study.active) return;
  state.study.flipped = !state.study.flipped;
  ui.renderStudy();
}

async function rateStudy(rating) {
  const s = state.study;
  if (!s.active || !s.flipped) return;
  const entry = s.queue[s.index];
  if (!entry) return;

  if (rating <= 2) s.again += 1;   // "Again"/"Hard" = didn't really know it

  // Advance the UI immediately; persist in the background.
  s.index += 1;
  s.flipped = false;

  // A card you couldn't recall (Again) comes back later this same session.
  let requeue = null;
  if (rating === 1) {
    requeue = { ...entry };
    const insertAt = Math.min(s.queue.length, s.index + 3);
    s.queue.splice(insertAt, 0, requeue);
    s.total += 1;
  }

  ui.renderStudy();

  try {
    const res = await api.reviewDictEntry(entry.id, rating);
    state.dictStats = res.stats || state.dictStats;
    // keep the local copy in the main list fresh so the word bank reflects it
    const idx = state.dictionary.findIndex((x) => x.id === entry.id);
    if (idx >= 0 && res.entry) state.dictionary[idx].srs = res.entry.srs;
    // refresh the requeued copy so its rating-button labels stay accurate
    if (requeue && res.entry) {
      requeue.srs = res.entry.srs;
      requeue._previews = res.entry._previews || requeue._previews;
    }
    ui.renderDictHeader();
  } catch (e) {
    showToast('Review not saved: ' + e.message);
  }
}

function endStudy() {
  state.study.active = false;
  state.study.queue = [];
  state.study.index = 0;
  state.study.flipped = false;
  // Refresh counts, then drop the user back on the word bank.
  loadDictionary().then(() => {
    ui.renderDictionary();
  });
  goRoute('dictionary');
}

/* ---------- text-to-speech (StyleTTS2 + gTTS) ---------- */
let ttsPollTimer = null;
let ttsAvailChecked = false;
let ttsLangsLoaded = false;

async function openTtsPage() {
  if (!ttsAvailChecked) {
    try { state.ttsAvailable = await api.ttsAvailable(); }
    catch { state.ttsAvailable = { available: false, gtts: false }; }
    ttsAvailChecked = true;
  }

  // If the StyleTTS2 engine isn't installed but gTTS is, start on gTTS so the
  // page is useful out of the box.
  const av = state.ttsAvailable || {};
  if (!av.available && av.gtts) state.ttsEngine = 'gtts';

  buildTtsEngineButtons();
  buildTtsAccents();
  await ensureTtsLanguages();

  await Promise.all([loadTtsLibrary(), loadTtsVoices()]);
  if (state.ttsVoice && !state.ttsVoices.some((v) => v.id === state.ttsVoice)) {
    state.ttsVoice = '';
  }
  $('ttsFolder').value = (state.config && state.config.tts_output_dir) || '';
  applyTtsEngineUI();
  ui.renderTts();
}

// Render the StyleTTS2 / gTTS toggle, marking engines that aren't installed.
function buildTtsEngineButtons() {
  const av = state.ttsAvailable || {};
  const row = $('ttsEngineRow');
  if (!row) return;
  row.querySelectorAll('.q-btn').forEach((btn) => {
    const eng = btn.dataset.engine;
    const installed = eng === 'gtts' ? !!av.gtts : !!av.available;
    btn.classList.toggle('active', state.ttsEngine === eng);
    btn.disabled = false; // still selectable so we can show the install hint
    btn.dataset.installed = installed ? '1' : '0';
    btn.onclick = () => setTtsEngine(eng);
  });
}

function buildTtsAccents() {
  const av = state.ttsAvailable || {};
  const row = $('ttsAccentRow');
  if (!row) return;
  const accents = av.gtts_accents || [
    { tld: 'com', name: 'US' }, { tld: 'co.uk', name: 'UK' },
    { tld: 'com.au', name: 'Australia' }, { tld: 'co.in', name: 'India' },
  ];
  row.innerHTML = accents
    .map((a) => `<button class="q-btn${a.tld === state.ttsTld ? ' active' : ''}" data-tld="${a.tld}">${esc(a.name)}</button>`)
    .join('');
  row.querySelectorAll('.q-btn').forEach((btn) => {
    btn.onclick = () => {
      state.ttsTld = btn.dataset.tld;
      row.querySelectorAll('.q-btn').forEach((b) => b.classList.toggle('active', b === btn));
    };
  });
}

// Fill the language <select> from the server's full gTTS language list (once).
async function ensureTtsLanguages() {
  const sel = $('ttsLang');
  if (!sel || ttsLangsLoaded) return;
  let langs = (state.ttsAvailable && state.ttsAvailable.gtts_common_langs) || null;
  try {
    if (state.ttsAvailable && state.ttsAvailable.gtts) {
      const res = await api.ttsGttsLanguages();
      if (res && res.languages && res.languages.length) langs = res.languages;
    }
  } catch { /* fall back to the common list */ }
  langs = langs || [{ code: 'en', name: 'English' }];
  sel.innerHTML = langs
    .map((l) => `<option value="${esc(l.code)}">${esc(l.name)} (${esc(l.code)})</option>`)
    .join('');
  sel.value = state.ttsLang;
  if (sel.value !== state.ttsLang) { state.ttsLang = sel.value; }
  sel.onchange = () => {
    state.ttsLang = sel.value;
    // The accent picker only makes sense for English.
    $('ttsAccentWrap').hidden = state.ttsLang !== 'en';
  };
  ttsLangsLoaded = true;
}

function setTtsEngine(engine) {
  state.ttsEngine = engine === 'gtts' ? 'gtts' : 'styletts2';
  buildTtsEngineButtons();
  applyTtsEngineUI();
}

// Show/hide the engine-specific option blocks and set the availability hint.
function applyTtsEngineUI() {
  const av = state.ttsAvailable || {};
  const isGtts = state.ttsEngine === 'gtts';
  $('ttsGttsOpts').hidden = !isGtts;
  $('ttsStyleOpts').hidden = isGtts;
  $('ttsAccentWrap').hidden = !(isGtts && state.ttsLang === 'en');

  const installed = isGtts ? !!av.gtts : !!av.available;
  const hint = $('ttsAvailHint');
  if (!installed) {
    hint.hidden = false;
    hint.textContent = isGtts
      ? "This engine needs gTTS on the server. Run 'pip install gtts' and restart the app, then reload this page."
      : "This engine needs StyleTTS2 on the server. Run 'pip install styletts2' and restart the app, then reload this page.";
    $('ttsGenerate').disabled = true;
  } else {
    hint.hidden = true;
    $('ttsGenerate').disabled = false;
  }
}

async function saveTtsFolder() {
  const dir = $('ttsFolder').value.trim();
  $('ttsFolderSave').disabled = true;
  try {
    state.config = await api.setConfig({ tts_output_dir: dir });
    showToast(dir ? 'Saved — new audio will go to that folder.' : 'Cleared — using the default folder.');
  } catch (e) {
    showToast('Could not save the folder: ' + e.message);
  } finally {
    $('ttsFolderSave').disabled = false;
  }
}

function updateTtsCharCount() {
  const n = $('ttsText').value.length;
  $('ttsCharCount').textContent = `${n.toLocaleString()} character${n === 1 ? '' : 's'}`;
}

async function startTts() {
  const text = $('ttsText').value.trim();
  if (!text) { $('ttsText').focus(); return; }

  const btn = $('ttsGenerate');
  btn.disabled = true;
  try {
    const isGtts = state.ttsEngine === 'gtts';
    const job = await api.startTts({
      text,
      title: $('ttsTitle').value.trim(),
      engine: state.ttsEngine,
      voice_id: isGtts ? '' : (state.ttsVoice || ''),
      diffusion_steps: Number($('ttsSteps').value) || 5,
      embedding_scale: Number($('ttsScale').value) || 1,
      lang: state.ttsLang || 'en',
      tld: state.ttsTld || 'com',
      slow: !!($('ttsSlow') && $('ttsSlow').checked),
    });
    $('ttsProgress').hidden = false;
    $('ttsFill').style.width = '0%';
    $('ttsFill').classList.add('busy');
    $('ttsStatus').textContent = 'Starting…';
    pollTts(job.id);
  } catch (e) {
    showToast('Could not start: ' + e.message);
    btn.disabled = false;
  }
}

function pollTts(jobId) {
  clearInterval(ttsPollTimer);
  ttsPollTimer = setInterval(async () => {
    let job;
    try { job = await api.getTtsStatus(jobId); } catch { return; }

    const busy = job.stage === 'loading_model' || job.stage === 'encoding' ||
      (job.stage === 'synthesizing' && !job.percent);
    $('ttsFill').classList.toggle('busy', busy);
    if (!busy) $('ttsFill').style.width = `${job.percent || 0}%`;

    const stageText = {
      queued: 'Queued…',
      loading_model: 'Loading the StyleTTS2 model (first time may download it — this can take a while)…',
      synthesizing: `Generating speech… ${job.percent || 0}%${job.chunks_total ? ` · ${job.chunks_done}/${job.chunks_total} parts` : ''}`,
      encoding: 'Finishing the audio file…',
      done: 'Done!',
    }[job.stage];

    $('ttsStatus').textContent = job.status === 'error' ? 'Error: ' + (job.error || 'unknown error')
      : job.status === 'cancelled' ? 'Cancelled.'
      : (stageText || job.status);

    if (job.status === 'done') {
      clearInterval(ttsPollTimer);
      ttsPollTimer = null;
      $('ttsProgress').hidden = true;
      $('ttsGenerate').disabled = false;
      await loadTtsLibrary();
      ui.renderTts();
      showToast('Speech generated and saved.');
    } else if (job.status === 'error' || job.status === 'cancelled') {
      clearInterval(ttsPollTimer);
      ttsPollTimer = null;
      $('ttsGenerate').disabled = false;
    }
  }, 1200);
}

async function cancelTtsJob() {
  try {
    const all = await fetch('/api/tts/jobs').then((r) => r.json()).catch(() => []);
    const mine = (all || []).find((j) => j.status === 'running' || j.status === 'queued');
    if (mine) await api.cancelTts(mine.id).catch(() => {});
  } catch {}
  clearInterval(ttsPollTimer);
  ttsPollTimer = null;
  $('ttsStatus').textContent = 'Cancelled.';
  $('ttsGenerate').disabled = false;
}

async function saveTtsVoice() {
  const file = $('ttsVoiceFile').files[0];
  if (!file) return;
  const name = $('ttsVoiceName').value.trim();
  $('ttsVoiceSave').disabled = true;
  try {
    const v = await api.addTtsVoice(name, file);
    await loadTtsVoices();
    state.ttsVoice = v.id;
    resetVoiceUpload();
    ui.renderTtsVoices();
    showToast('Voice saved — selected for your next generation.');
  } catch (e) {
    showToast('Could not save voice: ' + e.message);
  } finally {
    $('ttsVoiceSave').disabled = false;
  }
}

function resetVoiceUpload() {
  $('ttsVoiceFile').value = '';
  $('ttsVoiceFileName').textContent = '';
  $('ttsVoiceName').value = '';
  $('ttsVoiceSaveRow').hidden = true;
}

async function deleteTtsVoice(id) {
  const v = state.ttsVoices.find((x) => x.id === id);
  if (!confirm(`Delete the voice "${v ? v.name : ''}"?`)) return;
  try {
    await api.deleteTtsVoice(id);
    if (state.ttsVoice === id) state.ttsVoice = '';
    await loadTtsVoices();
    ui.renderTtsVoices();
  } catch (e) {
    showToast('Could not delete voice: ' + e.message);
  }
}

async function deleteTtsEntry(entry) {
  const preview = entry.title || entry.text.slice(0, 50);
  if (!confirm(`Delete "${preview}" and its audio file?`)) return;
  try {
    await api.deleteTtsEntry(entry.id);
    await loadTtsLibrary();
    ui.renderTts();
  } catch (e) {
    showToast('Could not delete: ' + e.message);
  }
}

async function ttsToFlashcards(entry) {
  try {
    const created = await api.ttsToDictionary(entry.id, '');
    await loadDictionary();
    ui.renderDictionary();   // harmless if the word bank isn't visible
    if (created && created._warning) showToast(created._warning);
    openMeaningFor(created);
  } catch (e) {
    showToast('Could not add to Word bank: ' + e.message);
  }
}

async function ttsAddSegment(entry, fromIdx, toIdx) {
  if (toIdx == null) toIdx = fromIdx;
  try {
    const created = await api.ttsSegmentToDictionary(entry.id, fromIdx, toIdx, '');
    await loadDictionary();
    ui.renderDictionary();
    if (created && created._warning) showToast(created._warning);
    else if (created && created.media && !created.media.audio) {
      showToast("Saved the text, but the audio clip wasn't attached — check ffmpeg / the server console.");
    }
    openMeaningFor(created);
  } catch (e) {
    showToast('Could not add the line: ' + e.message);
  }
}

// After a card is created from TTS, open the edit modal (pre-filled, audio
// already attached) so the user can add the meaning right away.
function openMeaningFor(created) {
  if (!created || !created.id) { showToast('Added to your Word bank.'); return; }
  const fresh = state.dictionary.find((d) => d.id === created.id) || created;
  showToast('Added — now add a meaning if you like.');
  openEditDict(fresh);
}

/* ---------- small transient toast ---------- */
let toastTimer = null;
function showToast(msg) {
  let el = $('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

/* ---------- keyboard shortcuts (active only on the watch view) ---------- */
function handleKeydown(e) {
  if (!$('view-watch').classList.contains('active')) return;
  // Don't hijack typing in notes, search, modals, etc.
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'textarea' || tag === 'input') return;
  if ($('renameModal').classList.contains('show') || $('deleteModal').classList.contains('show')) return;
  if ($('dictModal').classList.contains('show')) return;

  const player = $('player');
  switch (e.key.toLowerCase()) {
    case ' ':
    case 'k':
      e.preventDefault();
      player.paused ? player.play() : player.pause();
      break;
    case 'arrowleft':
      e.preventDefault();
      player.currentTime = Math.max(0, player.currentTime - 5);
      break;
    case 'arrowright':
      e.preventDefault();
      player.currentTime = Math.min(player.duration || Infinity, player.currentTime + 5);
      break;
    case 'j':
      player.currentTime = Math.max(0, player.currentTime - 10);
      break;
    case 'l':
      player.currentTime = Math.min(player.duration || Infinity, player.currentTime + 10);
      break;
    case 'arrowup':
      e.preventDefault();
      player.volume = Math.min(1, player.volume + 0.1);
      break;
    case 'arrowdown':
      e.preventDefault();
      player.volume = Math.max(0, player.volume - 0.1);
      break;
    case 'm':
      player.muted = !player.muted;
      break;
    case 'f':
      if (document.fullscreenElement) document.exitFullscreen();
      else player.closest('.player-shell')?.requestFullscreen?.();
      break;
  }
}

/* ---------- event wiring ---------- */
function handleStudyKeydown(e) {
  if (!$('view-study').classList.contains('active')) return;
  if (!state.study.active) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea') return;

  if (e.key === 'Escape') { e.preventDefault(); endStudy(); return; }
  if (state.study.index >= state.study.queue.length) return;  // summary screen

  if (!state.study.flipped) {
    if (e.key === ' ' || e.key.toLowerCase() === 'enter') { e.preventDefault(); flipStudy(); }
    return;
  }
  // flipped → number keys rate the card
  if (['1', '2', '3', '4'].includes(e.key)) { e.preventDefault(); rateStudy(Number(e.key)); }
  else if (e.key === ' ') { e.preventDefault(); rateStudy(3); }  // Space = Good
}

function wireEvents() {
  document.querySelectorAll('[data-route]').forEach((el) =>
    el.addEventListener('click', () => goRoute(el.dataset.route))
  );
  // sidebar / chip filters
  document.querySelectorAll('[data-filter]').forEach((el) =>
    el.addEventListener('click', () => setFilter(el.dataset.filter))
  );
  // sort dropdown
  $('sortSelect').addEventListener('change', (e) => {
    state.sort = e.target.value;
    ui.renderGrid();
  });

  // search (debounced subtitle search; instant local filter otherwise)
  $('search').addEventListener('input', (e) => {
    state.search = e.target.value;
    if (!$('view-browse').classList.contains('active')) goRoute('home');
    if (state.subSearch) {
      clearTimeout(subSearchTimer);
      subSearchTimer = setTimeout(runSubtitleSearch, 300);
      ui.renderGrid(); // clear stale results immediately while debounce runs
    } else {
      ui.renderGrid();
    }
  });
  $('searchBtn').addEventListener('click', () => goRoute('home'));

  // "CC" toggle: search inside subtitle text instead of titles/metadata
  $('subSearchToggle').addEventListener('click', () => {
    state.subSearch = !state.subSearch;
    $('subSearchToggle').classList.toggle('active', state.subSearch);
    if (state.subSearch && state.search.trim()) runSubtitleSearch();
    else ui.renderGrid();
  });

  // notes
  $('saveNotes').addEventListener('click', saveNote);
  $('clearNotes').addEventListener('click', () => { $('notes').value = ''; saveNote(); });
  $('notes').addEventListener('input', () => {
    ui.updateNotesMeta();
    clearTimeout(noteTimer);
    noteTimer = setTimeout(saveNote, 1000);
  });
  $('notes').addEventListener('blur', saveNote);

  // watched toggle
  $('toggleWatched').addEventListener('click', toggleWatched);

  // playback speed
  $('speedBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    $('speedMenu').classList.toggle('show');
  });
  $('speedMenu').querySelectorAll('[data-speed]').forEach((el) => {
    el.addEventListener('click', () => setPlaybackRate(Number(el.dataset.speed)));
  });
  document.addEventListener('click', () => $('speedMenu').classList.remove('show'));

  // autoplay toggle
  $('toggleAutoplay').addEventListener('click', toggleAutoplay);

  // rename
  $('renameBtn').addEventListener('click', openRenameModal);
  $('renameCancel').addEventListener('click', closeRenameModal);
  $('renameConfirm').addEventListener('click', confirmRename);
  $('renameInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') confirmRename(); });
  $('renameModal').addEventListener('click', (e) => { if (e.target.id === 'renameModal') closeRenameModal(); });

  // delete
  $('deleteBtn').addEventListener('click', openDeleteModal);
  $('deleteCancel').addEventListener('click', closeDeleteModal);
  $('deleteConfirm').addEventListener('click', confirmDelete);
  $('deleteModal').addEventListener('click', (e) => { if (e.target.id === 'deleteModal') closeDeleteModal(); });

  // find in this video (search within the currently open video's subtitle only)
  $('findInput').addEventListener('input', () => {
    clearTimeout(findTimer);
    findTimer = setTimeout(runFindInVideo, 250);
  });
  $('findInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { clearTimeout(findTimer); runFindInVideo(); }
  });
  $('findClear').addEventListener('click', () => {
    $('findInput').value = '';
    ui.renderFindResults([], '');
    $('findInput').focus();
  });

  // local subtitle generation (Whisper)
  $('genSubsBtn').addEventListener('click', openSubgenBox);
  $('subgenClose').addEventListener('click', closeSubgenBox);
  $('subgenStart').addEventListener('click', startSubgen);
  $('subgenCancel').addEventListener('click', cancelSubgenJob);
  $('subgenLangRow').querySelectorAll('.q-btn').forEach((btn) => {
    btn.onclick = () => {
      $('subgenLangRow').querySelectorAll('.q-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      $('subgenCustomLang').value = '';
    };
  });
  $('subgenCustomLang').addEventListener('input', () => {
    if ($('subgenCustomLang').value.trim()) {
      $('subgenLangRow').querySelectorAll('.q-btn').forEach((b) => b.classList.remove('active'));
    }
  });
  $('subgenModelRow').querySelectorAll('.q-btn').forEach((btn) => {
    btn.onclick = () => {
      $('subgenModelRow').querySelectorAll('.q-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
    };
  });
  $('subgenUseCustomPath').addEventListener('change', () => {
    const on = $('subgenUseCustomPath').checked;
    $('subgenCustomPath').hidden = !on;
    $('subgenCustomPathHint').hidden = !on;
    if (on) $('subgenCustomPath').focus();
  });

  // dictionary
  $('dictAddManual').addEventListener('click', () => openAddToDict(null));
  $('dictSearchInput').addEventListener('input', (e) => {
    state.dictSearch = e.target.value;
    ui.renderDictionary();
  });
  $('dictRevealToggle').addEventListener('click', () => {
    state.dictRevealAll = !state.dictRevealAll;
    ui.renderDictionary();
  });
  // word-bank status filter (All / Due / New / Learning / Mastered)
  document.querySelectorAll('#dictStatusChips .chip').forEach((c) =>
    c.addEventListener('click', () => {
      state.dictStatus = c.dataset.status;
      ui.renderDictionary();
    })
  );
  // the big "Start studying" button on the word-bank header
  $('dictStudyBtn').addEventListener('click', () => {
    if (state.dictStats && state.dictStats.due > 0) startStudy();
    else goRoute('study');
  });
  // show the chosen filename next to each upload button
  UPLOAD_IDS.forEach(([id]) => {
    $(id).addEventListener('change', () => {
      const f = $(id).files[0];
      $(`${id}Name`).textContent = f ? f.name : '';
    });
  });
  $('dictModalCancel').addEventListener('click', closeDictModal);
  $('dictModalSave').addEventListener('click', saveDict);
  $('dictModal').addEventListener('click', (e) => { if (e.target.id === 'dictModal') closeDictModal(); });
  // Ctrl/Cmd+Enter saves from either field; Esc cancels.
  $('dictModal').addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); saveDict(); }
    else if (e.key === 'Escape') { e.preventDefault(); closeDictModal(); }
  });

  // keyboard shortcuts on the watch page
  document.addEventListener('keydown', handleKeydown);
  // keyboard shortcuts during a study session
  document.addEventListener('keydown', handleStudyKeydown);

  // download panel
  dl.initButtons();
  $('dlUrl').addEventListener('input', dl.updateCommandPreview);
  $('startDownload').addEventListener('click', dl.startDownload);
  $('copyCmd').addEventListener('click', () => {
    navigator.clipboard.writeText($('cmdPreview').textContent);
    $('copyCmd').textContent = 'Copied!';
    setTimeout(() => ($('copyCmd').textContent = 'Copy command'), 1500);
  });

  // text-to-speech
  $('ttsText').addEventListener('input', updateTtsCharCount);
  $('ttsGenerate').addEventListener('click', startTts);
  $('ttsCancel').addEventListener('click', cancelTtsJob);
  $('ttsFolderSave').addEventListener('click', saveTtsFolder);
  $('ttsFolder').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); saveTtsFolder(); }
  });
  $('ttsVoiceFile').addEventListener('change', () => {
    const f = $('ttsVoiceFile').files[0];
    $('ttsVoiceFileName').textContent = f ? f.name : '';
    $('ttsVoiceSaveRow').hidden = !f;
    if (f && !$('ttsVoiceName').value.trim()) {
      $('ttsVoiceName').value = f.name.replace(/\.[^.]+$/, '').slice(0, 40);
    }
  });
  $('ttsVoiceSave').addEventListener('click', saveTtsVoice);
  $('ttsVoiceCancel').addEventListener('click', resetVoiceUpload);
  $('ttsAdvancedToggle').addEventListener('click', () => {
    const adv = $('ttsAdvanced');
    adv.hidden = !adv.hidden;
    $('ttsAdvancedToggle').innerHTML = adv.hidden ? 'Advanced options &#9662;' : 'Advanced options &#9652;';
  });
  $('ttsSteps').addEventListener('input', () => { $('ttsStepsVal').textContent = $('ttsSteps').value; });
  $('ttsScale').addEventListener('input', () => {
    $('ttsScaleVal').textContent = Number($('ttsScale').value).toFixed(1);
  });

  // settings
  $('saveSettings').addEventListener('click', saveSettings);

  // live conversation agent
  conv.initConversation();
}

/* ---------- boot ---------- */
async function boot() {
  wireEvents();
  await loadConfig();
  await loadLibrary();
  loadDictionary();   // background; populates ffmpeg availability + entries
  dl.refreshJobs();
}
boot();
