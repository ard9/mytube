/* state.js — shared app state + small pure helpers. */

export const state = {
  config: {},
  categories: [],        // [{name, count, channels:[{name, category, count, videos}]}]
  allVideos: [],         // flat list of every video
  notes: {},             // {path: text}
  progress: {},          // {path: {position, duration, watched}}
  current: null,         // currently watched video

  // browse controls
  search: '',
  view: { category: null, channel: null },  // sidebar selection
  filter: 'all',         // all | unwatched | inprogress | notes
  sort: 'newest',        // newest | oldest | title | longest

  // subtitle ("CC") search mode
  subSearch: false,
  subResults: {},        // {video_path: [{time, end, text}]}  (only when subSearch is on)

  // dictionary (words / sentences)
  dictionary: [],        // [{id, text, meaning, created, source, media, srs}]  newest first
  dictFfmpeg: true,      // whether the server can cut media clips
  dictSearch: '',        // filter text for the dictionary page
  dictRevealAll: false,  // false = meanings hidden (flashcard mode), reveal per-card on tap
  dictStatus: 'all',     // all | due | new | learning | mastered
  dictStats: null,       // {due, new, learning, review, mastered, streak, ...}

  // study session (spaced repetition)
  study: {
    active: false,
    queue: [],           // cards still to answer this session (objects with srs+_previews)
    index: 0,            // position in queue
    flipped: false,      // is the current card showing its meaning?
    done: 0,             // cards finished (graduated past today) this session
    again: 0,            // count of "Again"/"Hard" answers this session
    total: 0,            // queue length when the session started
  },

  // playback prefs (persisted locally per browser)
  playbackRate: Number(localStorage.getItem('mytube_rate')) || 1,
  autoplay: localStorage.getItem('mytube_autoplay') !== 'off',

  // "Add channel" subtitle preference: '' = off, 'all' = every language,
  // or a comma-separated list of language codes like "en,fa"
  subtitlesPref: '',
};

const AVATAR_COLORS = [
  '#d93025', '#1a73e8', '#188038', '#e37400', '#9334e6',
  '#129eaf', '#c5221f', '#1967d2', '#b06000', '#7b1fa2',
];

export function avatarColor(str = '') {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = str.charCodeAt(i) + ((h << 5) - h);
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

export function initial(str = '?') {
  return (str.trim()[0] || '?').toUpperCase();
}

export function fmtDuration(s) {
  if (!s || !isFinite(s)) return '';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (x) => String(x).padStart(2, '0');
  return h > 0 ? `${h}:${p(m)}:${p(sec)}` : `${m}:${p(sec)}`;
}

// Same formatting, just a clearer name for "jump to this moment" timestamps.
export const fmtTimestamp = fmtDuration;

export function fmtDate(yyyymmdd) {
  if (!yyyymmdd || yyyymmdd.length !== 8) return '';
  const y = yyyymmdd.slice(0, 4), m = yyyymmdd.slice(4, 6), d = yyyymmdd.slice(6, 8);
  return `${y}-${m}-${d}`;
}

export function esc(s = '') {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );
}

export function hasNote(video) {
  return !!(state.notes[video.path] || '').trim();
}

/* ----- watch progress helpers ----- */
export function progressOf(video) {
  const p = state.progress[video.path];
  if (!p) return { position: 0, duration: 0, percent: 0, watched: false };
  const duration = p.duration || video.duration || 0;
  const percent = duration ? Math.min(100, (p.position / duration) * 100) : 0;
  return { position: p.position || 0, duration, percent, watched: !!p.watched };
}
export function isWatched(video) {
  return progressOf(video).watched;
}
export function inProgress(video) {
  const p = progressOf(video);
  return !p.watched && p.position > 5;
}

/* ----- filtering + sorting for the grid ----- */
export function filteredVideos() {
  let list = state.allVideos;

  if (state.view.category) list = list.filter((v) => v.category === state.view.category);
  if (state.view.channel) list = list.filter((v) => v.uploader === state.view.channel);

  if (state.filter === 'unwatched') list = list.filter((v) => !isWatched(v));
  else if (state.filter === 'inprogress') list = list.filter(inProgress);
  else if (state.filter === 'notes') list = list.filter(hasNote);

  const q = state.search.trim().toLowerCase();
  if (q) {
    if (state.subSearch) {
      // Subtitle search mode: match only videos with a hit in state.subResults.
      list = list.filter((v) => state.subResults[v.path]);
    } else {
      list = list.filter(
        (v) =>
          v.title.toLowerCase().includes(q) ||
          v.uploader.toLowerCase().includes(q) ||
          v.category.toLowerCase().includes(q) ||
          v.filename.toLowerCase().includes(q)
      );
    }
  }

  list = list.slice();
  if (state.sort === 'newest') {
    list.sort((a, b) => (b.upload_date || '').localeCompare(a.upload_date || ''));
  } else if (state.sort === 'oldest') {
    list.sort((a, b) => (a.upload_date || '').localeCompare(b.upload_date || ''));
  } else if (state.sort === 'title') {
    list.sort((a, b) => a.title.localeCompare(b.title));
  } else if (state.sort === 'longest') {
    list.sort((a, b) => (b.duration || 0) - (a.duration || 0));
  }
  return list;
}
