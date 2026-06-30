/* ui.js — all DOM rendering (sidebar, grid, watch page). */

import { api } from './api.js';
import {
  state, avatarColor, initial, fmtDuration, fmtTimestamp, fmtDate, esc,
  hasNote, filteredVideos, progressOf, isWatched,
} from './state.js';

const $ = (id) => document.getElementById(id);

/* ============================================================
   Thumbnails (server ffmpeg -> browser frame grab fallback)
   ============================================================ */
function durationBadge(video) {
  return video.duration ? `<span class="duration">${fmtDuration(video.duration)}</span>` : '';
}

function overlays(video) {
  const p = progressOf(video);
  let html = '';
  if (p.watched) html += '<span class="watched-badge">WATCHED</span>';
  if (p.percent > 1) html += `<div class="watch-bar"><i style="width:${p.percent}%"></i></div>`;
  return html;
}

// Inner HTML for a .thumb / .un-thumb container.
function thumbInner(video) {
  let base;
  if (video.thumb) {
    // A real cover image (sibling) — use it for audio or video alike.
    base = `<img loading="lazy" src="${api.thumbUrl(video.thumb)}" alt="">`;
  } else if (video.media_type === 'audio') {
    // Audio has no video frame to grab — show a music icon instead of queuing
    // a thumbnail fetch (no data-thumb-for, so processThumbnails skips it).
    base = `<div class="placeholder audio-ph">&#127925;</div>`;
  } else {
    base = `<div class="placeholder" data-thumb-for="${esc(video.path)}">&#9654;</div>`;
  }
  const audioBadge = video.media_type === 'audio'
    ? '<span class="audio-badge">&#127925; Audio</span>' : '';
  return base + audioBadge + durationBadge(video) + overlays(video);
}

const thumbCache = {};
let thumbChain = Promise.resolve();

export function processThumbnails() {
  document.querySelectorAll('.placeholder[data-thumb-for]').forEach((ph) => {
    if (ph.dataset.queued) return;
    ph.dataset.queued = '1';
    const path = ph.getAttribute('data-thumb-for');
    thumbChain = thumbChain.then(() => fillPlaceholder(ph, path));
  });
}

async function fillPlaceholder(ph, path) {
  if (!ph.isConnected) return;
  let data = thumbCache[path];
  if (data === undefined) {
    data = await serverThumb(api.genThumbUrl(path));   // ffmpeg (fast, cached)
    if (!data) {
      try { data = await frameFromUrl(api.videoUrl(path)); }  // browser fallback
      catch { data = null; }
    }
    thumbCache[path] = data;
  }
  if (data && ph.isConnected) {
    const img = document.createElement('img');
    img.src = data;
    ph.replaceWith(img);
  }
}

function serverThumb(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img.naturalWidth ? url : null);
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

function frameFromUrl(url) {
  return new Promise((resolve) => {
    const v = document.createElement('video');
    v.preload = 'metadata'; v.muted = true; v.src = url;
    let done = false;
    const finish = (data) => { if (!done) { done = true; v.removeAttribute('src'); v.load?.(); resolve(data); } };
    v.addEventListener('loadeddata', () => {
      try { v.currentTime = Math.min((v.duration || 10) * 0.2, 8); } catch { finish(null); }
    });
    v.addEventListener('seeked', () => {
      try {
        const c = document.createElement('canvas');
        c.width = 320; c.height = 180;
        c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
        finish(c.toDataURL('image/jpeg', 0.7));
      } catch { finish(null); }
    });
    v.addEventListener('error', () => finish(null));
    setTimeout(() => finish(null), 8000);
  });
}

/* ============================================================
   Sidebar (categories -> channels)
   ============================================================ */
export function renderSidebar() {
  const box = $('channelList');
  box.innerHTML = '';

  state.categories.forEach((cat) => {
    const catEl = document.createElement('div');
    catEl.className = 'cat-group';

    const head = document.createElement('div');
    head.className = 'cat-head' + (state.view.category === cat.name && !state.view.channel ? ' active' : '');
    head.innerHTML = `
      <span class="cat-name">${esc(cat.name)}</span>
      <span class="cat-count">${cat.count}</span>`;
    head.onclick = () => window.MyTube.selectCategory(cat.name);
    catEl.appendChild(head);

    cat.channels.forEach((ch) => {
      const chEl = document.createElement('div');
      const active = state.view.channel === ch.name && state.view.category === cat.name;
      chEl.className = 'channel-item' + (active ? ' active' : '');
      chEl.innerHTML = `
        <div class="av" style="background:${avatarColor(ch.name)}">${esc(initial(ch.name))}</div>
        <div class="nm">${esc(ch.name)}</div>
        <div class="ct">${ch.count}</div>`;
      chEl.onclick = () => window.MyTube.selectChannel(cat.name, ch.name);
      catEl.appendChild(chEl);
    });

    box.appendChild(catEl);
  });

  $('folderName').textContent = state.config.library_path ? '📁 ' + state.config.library_path : '';
}

/* ============================================================
   Grid
   ============================================================ */
export function renderGrid() {
  const grid = $('grid');
  const list = filteredVideos();

  let title = 'All videos';
  if (state.view.channel) title = state.view.channel;
  else if (state.view.category) title = state.view.category;
  else if (state.filter === 'notes') title = 'Videos with notes';
  else if (state.filter === 'unwatched') title = 'Unwatched';
  else if (state.filter === 'inprogress') title = 'Continue watching';
  if (state.subSearch && state.search.trim()) title = `Caption matches for "${state.search.trim()}"`;
  $('browseTitle').textContent = title;
  $('browseCount').textContent = list.length ? `${list.length} videos` : '';

  if (state.allVideos.length === 0) { grid.innerHTML = emptyLibraryMarkup(); return; }
  if (list.length === 0) {
    const subMsg = state.subSearch
      ? '<p>No captions matched that phrase. Try a different word, or turn off caption search (CC).</p>'
      : '<p>Try a different search, category, or filter.</p>';
    grid.innerHTML = `<div class="empty small"><div class="big">&#128269;</div>
      <h2>No matches</h2>${subMsg}</div>`;
    return;
  }

  grid.innerHTML = '';
  list.forEach((v) => grid.appendChild(card(v)));
  processThumbnails();
}

function highlightMatch(text, q) {
  if (!q) return esc(text);
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx === -1) return esc(text);
  const before = esc(text.slice(0, idx));
  const hit = esc(text.slice(idx, idx + q.length));
  const after = esc(text.slice(idx + q.length));
  return `${before}<mark>${hit}</mark>${after}`;
}

function subMatchSnippet(v) {
  if (!state.subSearch) return '';
  const hits = state.subResults[v.path];
  if (!hits || !hits.length) return '';
  const q = state.search.trim();
  const first = hits[0];
  return `<span class="sub-match">
    <span class="ts">${fmtTimestamp(first.time)}</span>${highlightMatch(first.text, q)}
    <button class="dict-add-btn inline" title="Add this line to your dictionary">&#128214;&#43;</button>
  </span>`;
}

/* ----- "Find in this video" results (search within one video's own subtitle) ----- */
export function renderFindResults(matches, query) {
  const el = $('findResults');
  $('findClear').hidden = !query;

  if (!query) { el.innerHTML = ''; return; }

  if (!matches.length) {
    el.innerHTML = `<div class="find-empty">No matches for "${esc(query)}" in this video.</div>`;
    return;
  }

  const rows = matches.map((m, i) => `
    <div class="find-hit" data-time="${m.time}" data-idx="${i}">
      <span class="ts">${fmtTimestamp(m.time)}</span>
      <span class="txt">${highlightMatch(m.text, query)}</span>
      <button class="dict-add-btn" data-idx="${i}" title="Add this line to your dictionary">&#128214;&#43;</button>
    </div>`).join('');
  const count = `<div class="find-count">${matches.length} match${matches.length === 1 ? '' : 'es'}</div>`;
  el.innerHTML = rows + count;

  // "Add to dictionary" buttons (don't trigger the row's jump-to-time click).
  el.querySelectorAll('.dict-add-btn').forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const m = matches[Number(btn.dataset.idx)];
      window.MyTube.openAddToDict({
        text: m.text, start: m.time, end: m.end,
        path: state.current.path, title: state.current.title,
        media_type: state.current.media_type,
      });
    };
  });

  el.querySelectorAll('.find-hit').forEach((row) => {
    row.onclick = () => {
      const t = Number(row.dataset.time);
      const player = $('player');
      if (player.readyState > 0) {
        player.currentTime = Math.max(0, t - 1);
        player.play();
      } else {
        // Player hasn't loaded metadata yet (e.g. clicked immediately after
        // opening the video) — reload it targeting this timestamp via the
        // same media-fragment technique used for cross-library search hits,
        // so only the relevant byte range is fetched.
        player.src = `${api.videoUrl(state.current.path)}#t=${Math.max(0, t - 1)}`;
        player.load();
        player.play();
      }
    };
  });
}

function card(v) {
  const el = document.createElement('div');
  el.className = 'card' + (isWatched(v) ? ' watched' : '');
  el.innerHTML = `
    <div class="thumb">${thumbInner(v)}</div>
    <div class="info">
      <div class="avatar" style="background:${avatarColor(v.uploader)}">${esc(initial(v.uploader))}</div>
      <div class="text">
        <div class="title">${esc(v.title)}</div>
        <div class="sub">
          <span class="ch">${esc(v.uploader)}</span>
          <span class="meta-line">
            ${v.category && v.category !== 'Uncategorized' ? `<span class="tag">${esc(v.category)}</span>` : ''}
            ${v.upload_date ? `<span>${fmtDate(v.upload_date)}</span>` : ''}
          </span>
          ${hasNote(v) ? '<span class="has-note">&#9636; Notes</span>' : ''}
        </div>
        ${subMatchSnippet(v)}
      </div>
    </div>`;
  el.onclick = () => {
    const hits = state.subSearch ? state.subResults[v.path] : null;
    window.MyTube.openWatch(v, hits && hits[0] ? hits[0].time : null);
  };
  // "Add to dictionary" from a caption-search snippet (doesn't open the video).
  const addBtn = el.querySelector('.dict-add-btn');
  if (addBtn) {
    addBtn.onclick = (e) => {
      e.stopPropagation();
      const first = (state.subResults[v.path] || [])[0];
      if (!first) return;
      window.MyTube.openAddToDict({
        text: first.text, start: first.time, end: first.end,
        path: v.path, title: v.title, media_type: v.media_type,
      });
    };
  }
  return el;
}

function emptyLibraryMarkup() {
  return `<div class="empty"><div class="big">&#127916;</div>
    <h2>Your library is empty</h2>
    <p>Add a YouTube channel or video and it will download into your library, sorted by category and channel.</p>
    <button class="pill-btn accent" onclick="window.MyTube.goRoute('add')">&#43; Add channel</button>
    <p style="color:var(--text-dimmer);font-size:12px;margin-top:24px;">
      Current library: ${esc(state.config.library_path || '—')}</p></div>`;
}

export function loadingGrid() {
  $('grid').innerHTML = `<div class="empty"><div class="spinner"></div>
    <p>Scanning your library…</p></div>`;
}

/* ============================================================
   Watch page
   ============================================================ */
export async function renderWatch(video, jumpTo = null) {
  state.current = video;

  $('wTitle').textContent = video.title;
  $('wAvatar').style.background = avatarColor(video.uploader);
  $('wAvatar').textContent = initial(video.uploader);
  $('wChannel').textContent = video.uploader;
  $('wMeta').textContent = [
    video.category && video.category !== 'Uncategorized' ? video.category : null,
    video.duration ? fmtDuration(video.duration) : null,
    video.upload_date ? fmtDate(video.upload_date) : null,
    video.subtitle ? 'captions' : null,
  ].filter(Boolean).join(' • ');

  // watched toggle button label
  updateWatchedButton();

  const player = $('player');
  player.innerHTML = '';
  player.playbackRate = state.playbackRate;

  // Audio files play through the same <video> element (audio comes out, no
  // picture); flag the shell so it shows an audio look instead of a black box.
  const isAudio = video.media_type === 'audio';
  const shell = player.closest('.player-shell');
  if (shell) {
    shell.classList.toggle('audio-mode', isAudio);
    shell.dataset.audioTitle = isAudio ? (video.title || '') : '';
  }

  if (video.subtitle) {
    const track = document.createElement('track');
    track.kind = 'subtitles'; track.label = 'Captions'; track.srclang = 'en';
    track.src = api.subtitleUrl(video.subtitle); track.default = true;
    player.appendChild(track);
  }

  // Decide where playback should start: a subtitle-search hit takes
  // priority, otherwise resume from saved progress (unless already watched).
  const p = progressOf(video);
  let startAt = null;
  if (jumpTo !== null) {
    startAt = Math.max(0, jumpTo - 1);
  } else if (!p.watched && p.position > 5) {
    startAt = p.position;
  }

  // Using the #t=<seconds> media fragment means the *very first* network
  // request the browser makes already targets the right byte offset (via
  // an HTTP Range request), instead of fetching from the start of the file
  // and seeking afterward. This matters a lot for long videos served over
  // a real network connection — without it, every jump-to-timestamp click
  // (e.g. from a caption search result) would re-download some amount of
  // data from the beginning of the file first. preload="metadata" keeps
  // the initial request small (just enough to read duration/seek points)
  // rather than eagerly buffering video data we don't need yet.
  player.preload = 'metadata';
  player.src = startAt !== null
    ? `${api.videoUrl(video.path)}#t=${startAt}`
    : api.videoUrl(video.path);
  player.load();
  // Some browsers can reset playbackRate on src change; reapply once
  // metadata is ready (currentTime is already handled by the #t= fragment
  // above, so this only needs to re-set the rate, not seek again).
  player.onloadedmetadata = () => { player.playbackRate = state.playbackRate; };

  $('notes').value = state.notes[video.path] || '';
  updateNotesMeta();
  updateSpeedUI();
  updateAutoplayUI();
  updateGenSubsButton(video);
  updateFindBox(video);
  renderUpNext(video);
}

export function updateGenSubsButton(video) {
  const btn = $('genSubsBtn');
  if (!btn) return;
  // Only offer local generation when the video has no subtitle file yet.
  btn.hidden = !!video.subtitle;
}

export function updateFindBox(video) {
  const box = $('findBox');
  if (!box) return;
  // Only offer in-video search when this video actually has a subtitle.
  box.hidden = !video.subtitle;
  // Reset state for the new video so a stale query/results from a
  // previously watched video doesn't linger.
  $('findInput').value = '';
  $('findClear').hidden = true;
  $('findResults').innerHTML = '';
}

export function updateWatchedButton() {
  const btn = $('toggleWatched');
  if (!btn || !state.current) return;
  const watched = isWatched(state.current);
  btn.innerHTML = watched ? '&#10003; Watched' : 'Mark as watched';
  btn.classList.toggle('done', watched);
}

export function updateSpeedUI() {
  const btn = $('speedBtn');
  if (!btn) return;
  const rate = state.playbackRate;
  btn.textContent = rate === 1 ? '1x' : `${rate}x`;
  $('speedMenu').querySelectorAll('[data-speed]').forEach((el) => {
    el.classList.toggle('sel', Number(el.dataset.speed) === rate);
  });
}

export function updateAutoplayUI() {
  const stateEl = $('autoplayState');
  const btn = $('toggleAutoplay');
  if (!stateEl || !btn) return;
  stateEl.textContent = state.autoplay ? 'On' : 'Off';
  btn.classList.toggle('off', !state.autoplay);
}

export function updateNotesMeta() {
  const len = $('notes').value.trim().length;
  $('notesMeta').textContent = len ? `${len} characters` : 'no notes yet';
}

// YouTube-style: same channel first, then everything else, scrollable.
function renderUpNext(active) {
  const box = $('upnext');
  box.innerHTML = '';

  const sameChannel = state.allVideos.filter(
    (v) => v.uploader === active.uploader && v.path !== active.path
  );
  const others = state.allVideos.filter(
    (v) => v.uploader !== active.uploader && v.path !== active.path
  );
  others.sort((a, b) => (b.upload_date || '').localeCompare(a.upload_date || ''));

  const list = [...sameChannel, ...others].slice(0, 60);
  list.forEach((v) => box.appendChild(upNextRow(v)));

  if (!box.children.length) box.innerHTML = '<p class="muted">No other videos yet.</p>';
  else processThumbnails();
}

function upNextRow(v) {
  const row = document.createElement('div');
  row.className = 'upnext' + (isWatched(v) ? ' watched' : '');
  row.innerHTML = `
    <div class="un-thumb">${thumbInner(v)}</div>
    <div class="un-info">
      <div class="un-title">${esc(v.title)}</div>
      <div class="un-sub">${esc(v.uploader)}</div>
      <div class="un-sub2">
        ${v.category && v.category !== 'Uncategorized' ? `<span class="tag">${esc(v.category)}</span>` : ''}
        ${isWatched(v) ? '<span class="seen">Watched</span>' : ''}
      </div>
    </div>`;
  row.onclick = () => window.MyTube.openWatch(v);
  return row;
}

/* ============================================================
   Dictionary
   ============================================================ */
function dictMediaMarkup(entry) {
  const m = entry.media || {};
  const parts = [];
  if (m.image) {
    parts.push(`<img class="dict-img" loading="lazy" src="${api.dictMediaUrl(m.image)}" alt="">`);
  }
  if (m.video) {
    parts.push(`<video class="dict-video" controls preload="metadata" src="${api.dictMediaUrl(m.video)}"></video>`);
  }
  if (m.audio) {
    parts.push(`<audio class="dict-audio" controls preload="metadata" src="${api.dictMediaUrl(m.audio)}"></audio>`);
  }
  return parts.length ? `<div class="dict-media">${parts.join('')}</div>` : '';
}

function dictSourceMarkup(entry) {
  const s = entry.source;
  if (!s) return '<span class="dict-source manual">Added manually</span>';
  const ts = fmtTimestamp(s.start || 0);
  return `<span class="dict-source jump" data-path="${esc(s.path)}" data-time="${s.start || 0}">
    &#9654; ${esc(s.title || s.path)} <span class="ts">@ ${ts}</span></span>`;
}

/* ----- SRS status helpers (shared by the word bank + study screen) ----- */
const MATURE_DAYS = 21;

function srsStatus(entry) {
  const s = entry.srs || { state: 'new', due: '', interval: 0 };
  const today = new Date().toISOString().slice(0, 10);
  const due = !!s.due && s.due <= today;
  if (s.state === 'review' && (s.interval || 0) >= MATURE_DAYS) {
    return { key: 'mastered', label: 'Mastered', due };
  }
  if (s.state === 'new') return { key: 'new', label: 'New', due: true };
  if (s.state === 'learning') return { key: 'learning', label: 'Learning', due };
  return { key: 'review', label: due ? 'Due' : 'Scheduled', due };
}

function nextDueLabel(entry) {
  const s = entry.srs;
  if (!s || s.state === 'new') return 'New card';
  const today = new Date().toISOString().slice(0, 10);
  if (s.due <= today) return 'Due now';
  const days = Math.max(0, Math.round((new Date(s.due) - new Date(today)) / 86400000));
  if (days <= 0) return 'Due now';
  if (days === 1) return 'Next: tomorrow';
  if (days < 7) return `Next: ${days}d`;
  if (days < 30) return `Next: ${Math.round(days / 7)}wk`;
  if (days < 365) return `Next: ${Math.round(days / 30)}mo`;
  return `Next: ${(days / 365).toFixed(1)}yr`;
}

/* ----- the stats strip + study button on the word-bank header ----- */
export function renderDictHeader() {
  const st = state.dictStats;
  const strip = $('dictStatsStrip');
  const studyBtn = $('dictStudyBtn');
  const studyCount = $('dictStudyCount');
  if (!strip) return;

  if (!st || !st.total) {
    strip.innerHTML = '';
    if (studyBtn) studyBtn.classList.add('empty');
    if (studyCount) studyCount.textContent = '0';
  } else {
    const flame = st.streak > 0 ? `${st.streak}` : '0';
    strip.innerHTML = `
      <div class="stat streak" title="Day streak">
        <span class="stat-ico">&#128293;</span>
        <span class="stat-num">${flame}</span><span class="stat-lbl">day streak</span>
      </div>
      <div class="stat"><span class="stat-num">${st.reviewed_today}</span><span class="stat-lbl">reviewed today</span></div>
      <div class="stat"><span class="stat-num dot-new">${st.new}</span><span class="stat-lbl">new</span></div>
      <div class="stat"><span class="stat-num dot-mastered">${st.mastered}</span><span class="stat-lbl">mastered</span></div>`;
    const n = st.due || 0;
    if (studyCount) studyCount.textContent = String(n);
    if (studyBtn) {
      studyBtn.classList.toggle('empty', n === 0);
      studyBtn.querySelector('.study-cta-text').textContent =
        n === 0 ? 'All caught up' : 'Start studying';
    }
  }

  // Sidebar + topbar "due" badges.
  const due = st ? st.due : 0;
  for (const id of ['studyBadge', 'studyBadgeTop']) {
    const b = $(id);
    if (b) { b.textContent = String(due); b.hidden = !due; }
  }
}

export function renderDictionary() {
  renderDictHeader();
  const list = $('dictList');
  const q = state.dictSearch.trim().toLowerCase();

  let entries = state.dictionary;
  if (state.dictStatus !== 'all') {
    entries = entries.filter((e) => {
      const s = srsStatus(e);
      return state.dictStatus === 'due' ? s.due : s.key === state.dictStatus;
    });
  }
  if (q) {
    entries = entries.filter(
      (e) =>
        e.text.toLowerCase().includes(q) ||
        (e.meaning || '').toLowerCase().includes(q) ||
        (e.source && (e.source.title || '').toLowerCase().includes(q))
    );
  }

  document.querySelectorAll('#dictStatusChips .chip').forEach((c) =>
    c.classList.toggle('active', c.dataset.status === state.dictStatus)
  );
  $('dictFfmpegHint').hidden = state.dictFfmpeg;
  const toggle = $('dictRevealToggle');
  if (toggle) {
    toggle.hidden = !state.dictionary.length;
    toggle.textContent = state.dictRevealAll ? 'Hide meanings' : 'Show meanings';
  }

  if (!state.dictionary.length) {
    list.innerHTML = `<div class="empty small"><div class="big">&#128218;</div>
      <h2>Your word bank is empty</h2>
      <p>Search inside a video's captions and tap the &#128218;&#43; on any line to save the
      sentence — with its audio, a snapshot, or a video clip — or add a word manually.
      Everything you save becomes a flashcard you can drill in <b>Study</b>.</p>
      <button class="pill-btn accent" id="dictEmptyAdd"><span>&#43;</span> Add a word</button></div>`;
    $('dictEmptyAdd')?.addEventListener('click', () => window.MyTube.openAddToDict(null));
    return;
  }

  if (!entries.length) {
    const what = q ? `matches "${esc(state.dictSearch.trim())}"` : `is ${esc(state.dictStatus)}`;
    list.innerHTML = `<div class="empty small"><div class="big">&#128269;</div>
      <h2>Nothing here</h2><p>No word in your bank ${what}.</p></div>`;
    return;
  }

  list.innerHTML = '';
  entries.forEach((e) => list.appendChild(dictCard(e)));
}

function dictCard(entry) {
  const el = document.createElement('div');
  el.className = 'dict-card';
  const st = srsStatus(entry);
  el.classList.add(`s-${st.key}`);

  const hasMeaning = !!(entry.meaning && entry.meaning.trim());
  const meaningHtml = hasMeaning
    ? `<div class="dict-meaning-wrap${state.dictRevealAll ? ' revealed' : ''}">
         <div class="reveal-hint">&#128065; Tap to reveal meaning</div>
         <div class="dict-meaning">${esc(entry.meaning)}</div>
       </div>`
    : `<div class="dict-meaning empty">No meaning yet — click Edit to add one.</div>`;

  el.innerHTML = `
    <div class="dict-card-rail"></div>
    <div class="dict-card-body">
      <div class="dict-card-top">
        <span class="srs-badge ${st.key}">${st.label}</span>
        <span class="srs-next">${nextDueLabel(entry)}</span>
      </div>
      <div class="dict-text">${esc(entry.text)}</div>
      ${meaningHtml}
      ${dictMediaMarkup(entry)}
      <div class="dict-foot">
        ${dictSourceMarkup(entry)}
        <div class="dict-actions">
          <button class="pill-btn ghost small" data-edit>Edit</button>
          <button class="pill-btn ghost small danger" data-del>Delete</button>
        </div>
      </div>
    </div>`;

  const wrap = el.querySelector('.dict-meaning-wrap');
  if (wrap) wrap.onclick = () => wrap.classList.toggle('revealed');

  el.querySelector('[data-edit]').onclick = () => window.MyTube.openEditDict(entry);
  el.querySelector('[data-del]').onclick = () => window.MyTube.deleteDictEntry(entry);
  const jump = el.querySelector('.dict-source.jump');
  if (jump) {
    jump.onclick = () =>
      window.MyTube.jumpToSource(jump.dataset.path, Number(jump.dataset.time));
  }
  return el;
}

/* ============================================================
   Study (spaced-repetition flashcards)
   ============================================================ */
const RATINGS = [
  { n: 1, key: 'again', label: 'Again', kbd: '1' },
  { n: 2, key: 'hard', label: 'Hard', kbd: '2' },
  { n: 3, key: 'good', label: 'Good', kbd: '3' },
  { n: 4, key: 'easy', label: 'Easy', kbd: '4' },
];

function studyMediaMarkup(entry) {
  const m = entry.media || {};
  const parts = [];
  if (m.image) parts.push(`<img class="study-img" src="${api.dictMediaUrl(m.image)}" alt="">`);
  if (m.video) parts.push(`<video class="study-video" controls playsinline preload="metadata" src="${api.dictMediaUrl(m.video)}"></video>`);
  if (m.audio) parts.push(`<audio class="study-audio" id="studyAudio" controls preload="auto" src="${api.dictMediaUrl(m.audio)}"></audio>`);
  return parts.length ? `<div class="study-media">${parts.join('')}</div>` : '';
}

export function renderStudy() {
  const shell = $('studyShell');
  if (!shell) return;
  const s = state.study;

  // Not in a session yet → show the lobby (or the "all done" state).
  if (!s.active) {
    const st = state.dictStats || {};
    const due = st.due || 0;
    if (!st.total) {
      shell.innerHTML = `<div class="study-lobby">
        <div class="study-lobby-icon">&#128218;</div>
        <h2>Nothing to study yet</h2>
        <p>Capture some words from your videos first — every saved line becomes a flashcard here.</p>
        <button class="pill-btn accent" data-route="dictionary">Go to word bank</button></div>`;
      shell.querySelector('[data-route]').onclick = () => window.MyTube.goRoute('dictionary');
      return;
    }
    shell.innerHTML = `<div class="study-lobby">
      <div class="study-ring ${due ? '' : 'done'}">
        <span class="study-ring-num">${due}</span>
        <span class="study-ring-lbl">${due === 1 ? 'card due' : 'cards due'}</span>
      </div>
      <h2>${due ? 'Ready when you are' : 'You’re all caught up'}</h2>
      <p>${due
        ? `${st.due_review} to review · ${st.new_available} new · ${st.streak}-day streak &#128293;`
        : `Come back later for the next batch. ${st.streak}-day streak &#128293;`}</p>
      ${due ? `<button class="study-cta big" id="studyStart">
        <span class="study-cta-bolt">&#9889;</span>
        <span class="study-cta-text">Study ${due} ${due === 1 ? 'card' : 'cards'}</span></button>` : ''}
      <div class="study-legend">
        <span><i class="dot dot-new"></i>${st.new} new</span>
        <span><i class="dot dot-learning"></i>${st.learning} learning</span>
        <span><i class="dot dot-review"></i>${st.review} in review</span>
        <span><i class="dot dot-mastered"></i>${st.mastered} mastered</span>
      </div></div>`;
    const start = $('studyStart');
    if (start) start.onclick = () => window.MyTube.startStudy();
    return;
  }

  // Session finished.
  if (s.index >= s.queue.length) {
    const accuracy = s.total ? Math.round(((s.total - s.again) / s.total) * 100) : 100;
    shell.innerHTML = `<div class="study-lobby">
      <div class="study-done-check">&#10003;</div>
      <h2>Session complete</h2>
      <p>${s.total} ${s.total === 1 ? 'card' : 'cards'} reviewed · ${accuracy}% you knew on the first try.</p>
      <div class="study-done-actions">
        <button class="pill-btn accent" id="studyAgain">Study more</button>
        <button class="pill-btn ghost" id="studyToBank">Back to word bank</button>
      </div></div>`;
    $('studyAgain').onclick = () => window.MyTube.startStudy();
    $('studyToBank').onclick = () => window.MyTube.goRoute('dictionary');
    return;
  }

  // Active card.
  const entry = s.queue[s.index];
  const st = srsStatus(entry);
  const prev = entry._previews || {};
  const pct = s.total ? Math.round((s.index / s.total) * 100) : 0;

  shell.innerHTML = `
    <div class="study-top">
      <button class="study-exit" id="studyExit" title="End session">&#10005;</button>
      <div class="study-progress"><div class="study-progress-fill" style="width:${pct}%"></div></div>
      <div class="study-counter">${s.index + 1} / ${s.total}</div>
    </div>

    <div class="study-card ${s.flipped ? 'flipped' : ''}" id="studyCard">
      <div class="study-card-face front">
        <span class="srs-badge ${st.key}">${st.label}</span>
        <div class="study-word">${esc(entry.text)}</div>
        ${studyMediaMarkup(entry)}
        <div class="study-flip-hint">Tap, or press <kbd>Space</kbd>, to reveal</div>
      </div>
      <div class="study-card-face back">
        <div class="study-word small">${esc(entry.text)}</div>
        <div class="study-divider"></div>
        ${entry.meaning && entry.meaning.trim()
          ? `<div class="study-meaning">${esc(entry.meaning)}</div>`
          : `<div class="study-meaning empty">No meaning saved for this card yet.</div>`}
        ${entry.source ? `<div class="study-source jump" data-path="${esc(entry.source.path)}" data-time="${entry.source.start || 0}">
          &#9654; ${esc(entry.source.title || entry.source.path)} <span class="ts">@ ${fmtTimestamp(entry.source.start || 0)}</span></div>` : ''}
      </div>
    </div>

    ${s.flipped
      ? `<div class="rating-row">${RATINGS.map((r) => `
          <button class="rate-btn ${r.key}" data-rating="${r.n}">
            <span class="rate-label">${r.label}</span>
            <span class="rate-when">${prev[String(r.n)] || ''}</span>
            <span class="rate-kbd">${r.kbd}</span>
          </button>`).join('')}</div>`
      : `<button class="reveal-btn" id="studyReveal">Show answer <kbd>Space</kbd></button>`}
  `;

  $('studyExit').onclick = () => window.MyTube.endStudy();
  const card = $('studyCard');
  if (card) card.onclick = (e) => { if (!e.target.closest('a,button,.study-source,audio,video')) window.MyTube.flipStudy(); };
  const reveal = $('studyReveal');
  if (reveal) reveal.onclick = () => window.MyTube.flipStudy();
  shell.querySelectorAll('.rate-btn').forEach((b) =>
    (b.onclick = () => window.MyTube.rateStudy(Number(b.dataset.rating)))
  );
  const jump = shell.querySelector('.study-source.jump');
  if (jump) jump.onclick = () => window.MyTube.jumpToSource(jump.dataset.path, Number(jump.dataset.time));

  // Auto-play the captured audio so you hear it before recalling.
  if (!s.flipped) {
    const audio = $('studyAudio');
    if (audio) { audio.currentTime = 0; audio.play().catch(() => {}); }
  }
}

/* ============================================================
   View switching
   ============================================================ */
/* ============================================================
   Text to speech (StyleTTS2)
   ============================================================ */
export function renderTtsVoices() {
  const row = $('ttsVoiceRow');
  if (!row) return;

  const chips = (state.ttsVoices || [])
    .map(
      (v) => `<button class="q-btn tts-voice-chip${state.ttsVoice === v.id ? ' active' : ''}" data-voice="${esc(v.id)}">
        ${esc(v.name)}<span class="rm-voice" data-rmvoice="${esc(v.id)}" title="Delete this voice">&#10005;</span>
      </button>`
    )
    .join('');

  row.innerHTML =
    `<button class="q-btn${!state.ttsVoice ? ' active' : ''}" data-voice="">Default voice</button>` + chips;

  row.querySelectorAll('[data-voice]').forEach((btn) => {
    btn.onclick = (e) => {
      if (e.target.classList.contains('rm-voice')) return;
      state.ttsVoice = btn.dataset.voice;
      renderTtsVoices();
    };
  });
  row.querySelectorAll('[data-rmvoice]').forEach((x) => {
    x.onclick = (e) => {
      e.stopPropagation();
      window.MyTube.deleteTtsVoice(x.dataset.rmvoice);
    };
  });
}

export function renderTts() {
  renderTtsVoices();

  const list = $('ttsList');
  if (!list) return;
  const entries = state.ttsLibrary || [];

  $('ttsCount').textContent = entries.length
    ? `${entries.length} ${entries.length === 1 ? 'clip' : 'clips'}`
    : '';

  if (!entries.length) {
    list.innerHTML = `<div class="empty small"><div class="big">&#128266;</div>
      <h2>No audio yet</h2>
      <p>Type or paste some text on the left and press <b>Generate speech</b>. Your saved audio will appear here.</p></div>`;
    return;
  }

  list.innerHTML = '';
  entries.forEach((e) => list.appendChild(ttsCard(e)));
}

function ttsCard(e) {
  const el = document.createElement('div');
  el.className = 'tts-card';

  const isGtts = e.engine === 'gtts';
  const engineTag = `<span class="tag">${isGtts ? '&#127760; gTTS' : '&#129302; StyleTTS2'}</span>`;
  const voiceTag = isGtts
    ? (e.voice_name ? `<span class="tag">${esc(e.voice_name.replace(/^gTTS\s*·\s*/, ''))}</span>` : '')
    : (e.voice_name
        ? `<span class="tag">&#127908; ${esc(e.voice_name)}</span>`
        : '<span class="tag">Default voice</span>');
  const dur = e.duration ? fmtDuration(e.duration) : '';
  const downloadName = (e.title || 'audio').replace(/[^\w.-]+/g, '_').slice(0, 60) +
    (e.file && e.file.endsWith('.mp3') ? '.mp3' : '.wav');
  const folder = e.folder ? `<span class="tts-folder" title="${esc(e.folder)}">&#128193; ${esc(e.folder)}</span>` : '';
  const segs = Array.isArray(e.segments) ? e.segments : [];
  const hasSegs = segs.length > 0;

  // Body: a read-along transcript when we have timed segments, else a plain preview.
  let body;
  if (hasSegs) {
    const spans = segs.map((s) =>
      `<span class="tts-seg" data-i="${s.i}" data-start="${s.start}" data-end="${s.end}">${esc(s.text)} </span>`
    ).join('');
    body = `
      <div class="tts-transcript">${spans}</div>
      <div class="tts-seg-bar" hidden>
        <span class="tts-seg-sel"></span>
        <button class="pill-btn ghost small" data-segplay>&#9654; From here</button>
        <button class="pill-btn ghost small" data-segadd>&#128218;&#43; Add line to Word bank</button>
        <button class="pill-btn ghost small" data-segclear>Clear</button>
      </div>`;
  } else {
    const preview = e.text.length > 220 ? e.text.slice(0, 220) + '…' : e.text;
    body = `<div class="tts-card-text">${esc(preview)}</div>`;
  }

  el.innerHTML = `
    <div class="tts-card-head">
      <div class="tts-card-title">${esc(e.title || 'Untitled')}</div>
      <div class="tts-card-meta">${engineTag}${voiceTag}${dur ? `<span>${dur}</span>` : ''}<span>${e.chars} chars</span>${hasSegs ? '<span>&#128266; read-along</span>' : ''}</div>
    </div>
    ${body}
    <audio class="dict-audio" controls preload="none" src="${api.ttsMediaUrl(e.id)}"></audio>
    ${folder ? `<div class="tts-card-folder">${folder}</div>` : ''}
    <div class="tts-card-actions">
      <button class="pill-btn ghost small" data-add>&#128218;&#43; Add whole text</button>
      <a class="pill-btn ghost small" href="${api.ttsMediaUrl(e.id)}" download="${esc(downloadName)}">&#8595; Download</a>
      <button class="pill-btn ghost small danger" data-del>Delete</button>
    </div>`;

  el.querySelector('[data-add]').onclick = () => window.MyTube.ttsToFlashcards(e);
  el.querySelector('[data-del]').onclick = () => window.MyTube.deleteTtsEntry(e);
  if (hasSegs) wireReadAlong(el, e, segs);
  return el;
}

function wireReadAlong(card, entry, segs) {
  const audio = card.querySelector('audio');
  const spans = Array.from(card.querySelectorAll('.tts-seg'));
  const bar = card.querySelector('.tts-seg-bar');
  const sel = card.querySelector('.tts-seg-sel');
  let selected = -1;       // index of the sentence chosen for adding
  let reading = -1;        // index currently being spoken

  // Karaoke highlight: follow playback and light up the active sentence.
  audio.addEventListener('timeupdate', () => {
    const t = audio.currentTime;
    let cur = -1;
    for (let k = 0; k < segs.length; k++) {
      if (t >= segs[k].start && t < segs[k].end) { cur = k; break; }
      if (t >= segs[k].start) cur = k;   // during a gap, keep the last spoken one lit
    }
    if (cur !== reading) {
      if (reading >= 0 && spans[reading]) spans[reading].classList.remove('reading');
      if (cur >= 0 && spans[cur]) {
        spans[cur].classList.add('reading');
        spans[cur].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
      reading = cur;
    }
  });
  audio.addEventListener('ended', () => {
    if (reading >= 0 && spans[reading]) spans[reading].classList.remove('reading');
    reading = -1;
  });

  function selectSeg(i) {
    if (selected >= 0 && spans[selected]) spans[selected].classList.remove('selected');
    selected = i;
    spans[i].classList.add('selected');
    const text = segs[i].text;
    sel.textContent = text.length > 70 ? text.slice(0, 70) + '…' : text;
    bar.hidden = false;
  }
  function clearSel() {
    if (selected >= 0 && spans[selected]) spans[selected].classList.remove('selected');
    selected = -1;
    bar.hidden = true;
  }

  spans.forEach((sp, i) => { sp.onclick = () => selectSeg(i); });
  bar.querySelector('[data-segplay]').onclick = () => {
    if (selected < 0) return;
    audio.currentTime = segs[selected].start + 0.001;
    audio.play();
  };
  bar.querySelector('[data-segadd]').onclick = () => {
    if (selected < 0) return;
    window.MyTube.ttsAddSegment(entry, selected);
  };
  bar.querySelector('[data-segclear]').onclick = clearSel;
}

export function showView(name) {
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  $(`view-${name}`).classList.add('active');
}
