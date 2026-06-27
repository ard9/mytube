/* conversation.js — live speaking-practice agent.
 *
 * Flow:  mic → speech-to-text → LLM tutor → reply shown live + spoken aloud.
 * Input can be the browser's own (Google-powered) live recognition or Whisper
 * on the server. The tutor's voice can be the browser's speech synthesis or the
 * app's StyleTTS2. Everything is wired here; the backend lives in conversation.py.
 */

import { api } from './api.js';
import { esc } from './state.js';

const $ = (id) => document.getElementById(id);

const conv = {
  sessions: [],
  currentId: null,
  settings: null,
  avail: { whisper: false, styletts: false },
  sttMode: 'browser',     // browser | whisper
  voiceMode: 'browser',   // browser | styletts2
  autoplay: true,
  busy: false,            // an LLM request is in flight
  // speech recognition / recording state
  recog: null,
  recognizing: false,
  finalBuf: '',
  mediaRecorder: null,
  mediaChunks: [],
  recording: false,
  audioEl: null,
};

/* ---------- small helpers ---------- */
function setStatus(msg, kind = '') {
  const el = $('convStatus');
  if (!el) return;
  el.textContent = msg || '';
  el.className = 'conv-status' + (kind ? ' ' + kind : '');
}

function scrollThread() {
  const t = $('convThread');
  if (t) t.scrollTop = t.scrollHeight;
}

function inputsEnabled(on) {
  const mic = $('convMic'), text = $('convText'), send = $('convSend');
  // The mic is only usable when its mode is actually supported.
  const micOk = on && (conv.sttMode === 'browser' ? browserSttSupported() : conv.avail.whisper);
  if (mic) mic.disabled = !micOk;
  if (text) text.disabled = !on;
  if (send) send.disabled = !on;
}

function browserSttSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

/* ---------- rendering ---------- */
function renderSessions() {
  const box = $('convSessionList');
  if (!box) return;
  if (!conv.sessions.length) {
    box.innerHTML = '<p class="muted conv-side-empty">No conversations yet.</p>';
    return;
  }
  box.innerHTML = conv.sessions.map((s) => `
    <div class="conv-session ${s.id === conv.currentId ? 'active' : ''}" data-id="${s.id}">
      <div class="conv-session-main" data-open="${s.id}">
        <div class="conv-session-title">${esc(s.title || 'New conversation')}</div>
        <div class="conv-session-meta">${s.message_count || 0} messages</div>
      </div>
      <button class="conv-session-del" data-del="${s.id}" title="Delete">&#10005;</button>
    </div>
  `).join('');
}

function correctionsHtml(corrections) {
  if (!corrections || !corrections.length) return '';
  const items = corrections.map((c) => `
    <div class="conv-correction">
      <div class="conv-corr-line">
        <span class="conv-corr-bad">${esc(c.original || '')}</span>
        <span class="conv-corr-arrow">&#8594;</span>
        <span class="conv-corr-good">${esc(c.fixed || '')}</span>
      </div>
      ${c.explanation ? `<div class="conv-corr-exp" dir="auto">${esc(c.explanation)}</div>` : ''}
    </div>
  `).join('');
  return `<div class="conv-corrections"><div class="conv-corr-head">&#9998; Corrections</div>${items}</div>`;
}

function messageHtml(m) {
  if (m.role === 'user') {
    return `<div class="conv-msg user"><div class="conv-bubble">${esc(m.content || '')}</div></div>`;
  }
  const speakBtn = m.content
    ? `<button class="conv-speak" data-speak="${esc(m.content).replace(/"/g, '&quot;')}" title="Play">&#128266;</button>`
    : '';
  return `
    <div class="conv-msg bot">
      <div class="conv-bubble">
        <span class="conv-bubble-text">${esc(m.content || '')}</span>
        ${speakBtn}
      </div>
      ${correctionsHtml(m.corrections)}
    </div>`;
}

function renderThread(messages) {
  const t = $('convThread');
  if (!t) return;
  if (!messages || !messages.length) {
    t.innerHTML = `
      <div class="conv-empty" id="convEmpty">
        <div class="conv-empty-icon">&#127908;</div>
        <p>Say hello to start — your tutor is listening.</p>
      </div>`;
    return;
  }
  t.innerHTML = messages.map(messageHtml).join('');
  scrollThread();
}

function appendMessage(m) {
  const t = $('convThread');
  if (!t) return;
  const empty = $('convEmpty');
  if (empty) empty.remove();
  t.insertAdjacentHTML('beforeend', messageHtml(m));
  scrollThread();
}

function appendThinking() {
  const t = $('convThread');
  if (!t) return;
  t.insertAdjacentHTML('beforeend',
    `<div class="conv-msg bot" id="convThinking"><div class="conv-bubble conv-typing"><span></span><span></span><span></span></div></div>`);
  scrollThread();
}
function removeThinking() {
  const el = $('convThinking');
  if (el) el.remove();
}

/* ---------- sessions ---------- */
async function loadSessions() {
  try {
    const r = await api.convSessions();
    conv.sessions = r.sessions || [];
  } catch {
    conv.sessions = [];
  }
  renderSessions();
}

async function openSession(id) {
  conv.currentId = id;
  renderSessions();
  setStatus('');
  try {
    const s = await api.convGetSession(id);
    $('convTitle').textContent = s.title || 'Live conversation';
    renderThread(s.messages || []);
  } catch (e) {
    setStatus('Could not open this conversation: ' + e.message, 'error');
  }
}

async function newConversation() {
  stopSpeaking();
  try {
    const s = await api.convCreateSession();
    conv.currentId = s.id;
    $('convTitle').textContent = s.title || 'New conversation';
    renderThread([]);
    await loadSessions();
    renderSessions();
    // Have the tutor greet first.
    if (!isConfigured()) {
      setStatus('Add an API key in “AI settings” to start chatting.', 'warn');
      openSettings();
      return;
    }
    appendThinking();
    setStatus('Starting…');
    try {
      const r = await api.convGreeting(s.id);
      removeThinking();
      appendMessage(r.assistant);
      setStatus('');
      if (conv.autoplay) speak(r.reply);
      await loadSessions();
    } catch (e) {
      removeThinking();
      setStatus(llmErr(e), 'error');
    }
  } catch (e) {
    setStatus('Could not start a conversation: ' + e.message, 'error');
  }
}

async function deleteSession(id) {
  if (!confirm('Delete this conversation?')) return;
  try {
    await api.convDeleteSession(id);
    if (conv.currentId === id) {
      conv.currentId = null;
      $('convTitle').textContent = 'Live conversation';
      renderThread([]);
    }
    await loadSessions();
  } catch (e) {
    setStatus('Could not delete: ' + e.message, 'error');
  }
}

/* ---------- sending a turn ---------- */
function isConfigured() {
  const s = conv.settings;
  if (!s) return false;
  if (s.provider === 'gemini') return !!s.gemini_key_set;
  if (s.provider === 'openai') return !!s.openai_key_set;
  return !!s.openrouter_key_set;
}

function llmErr(e) {
  return 'Tutor error: ' + (e && e.message ? e.message : 'something went wrong');
}

async function sendText(text) {
  text = (text || '').trim();
  if (!text || conv.busy) return;
  if (!conv.currentId) { await newConversation(); if (!conv.currentId) return; }
  if (!isConfigured()) {
    setStatus('Add an API key in “AI settings” first.', 'warn');
    openSettings();
    return;
  }

  conv.busy = true;
  inputsEnabled(false);
  $('convText').value = '';
  autoSize();
  appendMessage({ role: 'user', content: text });
  appendThinking();
  setStatus('Thinking…');

  try {
    const r = await api.convSendMessage(conv.currentId, text);
    removeThinking();
    appendMessage(r.assistant);
    if (r.title) $('convTitle').textContent = r.title;
    setStatus('');
    if (conv.autoplay) speak(r.reply);
    loadSessions();
  } catch (e) {
    removeThinking();
    setStatus(llmErr(e), 'error');
  } finally {
    conv.busy = false;
    inputsEnabled(true);
    $('convText').focus();
  }
}

/* ---------- text-to-speech (tutor voice) ---------- */
function stopSpeaking() {
  try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch {}
  if (conv.audioEl) { try { conv.audioEl.pause(); } catch {} }
}

function pickEnglishVoice() {
  const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  return voices.find((v) => /en[-_]US/i.test(v.lang)) ||
         voices.find((v) => /^en/i.test(v.lang)) || null;
}

async function speak(text) {
  text = (text || '').trim();
  if (!text) return;
  stopSpeaking();
  if (conv.voiceMode === 'styletts2' && conv.avail.styletts) {
    return speakStyleTts(text);
  }
  // Browser speech synthesis
  if (!('speechSynthesis' in window)) {
    setStatus('This browser has no speech synthesis. Try the StyleTTS2 voice.', 'warn');
    return;
  }
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'en-US';
  const v = pickEnglishVoice();
  if (v) u.voice = v;
  u.rate = 0.98;
  window.speechSynthesis.speak(u);
}

async function speakStyleTts(text) {
  try {
    setStatus('Generating voice…');
    const job = await api.startTts({ text, title: 'conversation' });
    const id = job.id;
    let tries = 0;
    while (tries++ < 240) {
      const st = await api.getTtsStatus(id);
      if (st.status === 'done' && st.entry_id) {
        setStatus('');
        if (!conv.audioEl) conv.audioEl = new Audio();
        conv.audioEl.src = api.ttsMediaUrl(st.entry_id);
        conv.audioEl.play().catch(() => {});
        return;
      }
      if (st.status === 'error') { setStatus('Voice error: ' + (st.error || ''), 'warn'); return; }
      if (st.status === 'cancelled') { setStatus(''); return; }
      await new Promise((res) => setTimeout(res, 500));
    }
    setStatus('Voice timed out.', 'warn');
  } catch (e) {
    setStatus('Voice error: ' + e.message, 'warn');
  }
}

/* ---------- speech-to-text input ---------- */
function startBrowserRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { setStatus('This browser has no live speech recognition. Switch input to Whisper.', 'warn'); return; }
  const r = new SR();
  conv.recog = r;
  conv.finalBuf = '';
  r.lang = 'en-US';
  r.continuous = true;
  r.interimResults = true;

  r.onresult = (ev) => {
    let interim = '';
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const res = ev.results[i];
      if (res.isFinal) conv.finalBuf += res[0].transcript + ' ';
      else interim += res[0].transcript;
    }
    const box = $('convInterim');
    box.hidden = false;
    box.textContent = (conv.finalBuf + interim).trim() || '…';
  };
  r.onerror = (ev) => {
    if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
      setStatus('Microphone permission was blocked.', 'error');
    } else if (ev.error !== 'no-speech' && ev.error !== 'aborted') {
      setStatus('Recognition error: ' + ev.error, 'warn');
    }
  };
  r.onend = () => {
    conv.recognizing = false;
    setMicState(false);
    $('convInterim').hidden = true;
    const text = conv.finalBuf.trim();
    conv.finalBuf = '';
    if (text) sendText(text);
  };

  try {
    r.start();
    conv.recognizing = true;
    setMicState(true);
    setStatus('Listening… speak now, then tap the mic to send.');
  } catch (e) {
    setStatus('Could not start the mic: ' + e.message, 'error');
  }
}

function stopBrowserRecognition() {
  if (conv.recog) { try { conv.recog.stop(); } catch {} }
}

async function startWhisperRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus('This browser cannot record audio.', 'error');
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
    const mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    conv.mediaRecorder = mr;
    conv.mediaChunks = [];
    mr.ondataavailable = (e) => { if (e.data && e.data.size) conv.mediaChunks.push(e.data); };
    mr.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      setMicState(false);
      $('convInterim').hidden = true;
      const blob = new Blob(conv.mediaChunks, { type: mime || 'audio/webm' });
      if (!blob.size) { setStatus('No audio captured.', 'warn'); return; }
      setStatus('Transcribing with Whisper…');
      try {
        const r = await api.convStt(blob, conv.settings ? conv.settings.whisper_model : '');
        const text = (r.text || '').trim();
        if (text) { setStatus(''); sendText(text); }
        else setStatus('Whisper heard nothing — try again.', 'warn');
      } catch (e) {
        setStatus('Whisper error: ' + e.message, 'error');
      }
    };
    mr.start();
    conv.recording = true;
    setMicState(true);
    $('convInterim').hidden = false;
    $('convInterim').textContent = '● Recording… tap the mic to stop.';
    setStatus('Recording…');
  } catch (e) {
    setStatus('Microphone error: ' + e.message, 'error');
  }
}

function stopWhisperRecording() {
  if (conv.mediaRecorder && conv.recording) {
    conv.recording = false;
    try { conv.mediaRecorder.stop(); } catch {}
  }
}

function setMicState(active) {
  const mic = $('convMic');
  if (mic) mic.classList.toggle('recording', active);
}

function toggleMic() {
  if (conv.busy) return;
  stopSpeaking();
  if (conv.sttMode === 'browser') {
    if (conv.recognizing) stopBrowserRecognition();
    else startBrowserRecognition();
  } else {
    if (conv.recording) stopWhisperRecording();
    else startWhisperRecording();
  }
}

/* ---------- settings drawer ---------- */
function openSettings() { $('convSettingsDrawer').hidden = false; }
function closeSettings() { $('convSettingsDrawer').hidden = true; }

function fillSettingsForm() {
  const s = conv.settings || {};
  selectProviderTab(s.provider || 'openrouter');
  $('convOrKey').value = s.openrouter_key || '';
  $('convOrModel').value = s.openrouter_model || '';
  $('convGemKey').value = s.gemini_key || '';
  $('convGemModel').value = s.gemini_model || '';
  $('convOaiKey').value = s.openai_key || '';
  $('convOaiBase').value = s.openai_base_url || '';
  $('convOaiModel').value = s.openai_model || '';
  setActiveBtn('convLevelRow', 'level', s.level || 'auto');
  setActiveBtn('convWhisperRow', 'whisper', s.whisper_model || 'base');
}

function setActiveBtn(rowId, attr, value) {
  const row = $(rowId);
  if (!row) return;
  row.querySelectorAll('.q-btn').forEach((b) =>
    b.classList.toggle('active', b.dataset[attr] === value));
}
function getActiveBtn(rowId, attr, fallback) {
  const el = $(rowId) && $(rowId).querySelector('.q-btn.active');
  return el ? el.dataset[attr] : fallback;
}

function selectProviderTab(provider) {
  $('convProviderRow').querySelectorAll('.q-btn').forEach((b) =>
    b.classList.toggle('active', b.dataset.provider === provider));
  document.querySelectorAll('.conv-provider-pane').forEach((p) =>
    p.hidden = p.dataset.pane !== provider);
}

async function saveSettings() {
  const provider = ($('convProviderRow').querySelector('.q-btn.active') || {}).dataset?.provider || 'openrouter';
  const body = {
    provider,
    openrouter_key: $('convOrKey').value.trim(),
    openrouter_model: $('convOrModel').value.trim(),
    gemini_key: $('convGemKey').value.trim(),
    gemini_model: $('convGemModel').value.trim(),
    openai_key: $('convOaiKey').value.trim(),
    openai_base_url: $('convOaiBase').value.trim(),
    openai_model: $('convOaiModel').value.trim(),
    level: getActiveBtn('convLevelRow', 'level', 'auto'),
    whisper_model: getActiveBtn('convWhisperRow', 'whisper', 'base'),
  };
  try {
    conv.settings = await api.convSetSettings(body);
    const msg = $('convSettingsMsg');
    msg.hidden = false;
    msg.textContent = 'Saved ✓';
    setTimeout(() => (msg.hidden = true), 2000);
    setStatus('');
  } catch (e) {
    setStatus('Could not save settings: ' + e.message, 'error');
  }
}

/* ---------- mode toggles ---------- */
function wireSegment(rowId, attr, onPick) {
  const row = $(rowId);
  if (!row) return;
  row.querySelectorAll('.conv-seg-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      row.querySelectorAll('.conv-seg-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      onPick(btn.dataset[attr]);
    });
  });
}

function autoSize() {
  const ta = $('convText');
  if (!ta) return;
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
}

/* ---------- public: init + open ---------- */
export function initConversation() {
  // create the playback audio element lazily; nothing else needed here.

  $('convNewBtn').addEventListener('click', newConversation);

  // session list (event delegation)
  $('convSessionList').addEventListener('click', (e) => {
    const del = e.target.closest('[data-del]');
    if (del) { e.stopPropagation(); deleteSession(del.dataset.del); return; }
    const open = e.target.closest('[data-open]');
    if (open) openSession(open.dataset.open);
  });

  // thread: replay a bot line
  $('convThread').addEventListener('click', (e) => {
    const sp = e.target.closest('[data-speak]');
    if (sp) speak(sp.dataset.speak);
  });

  // composer
  $('convMic').addEventListener('click', toggleMic);
  $('convSend').addEventListener('click', () => sendText($('convText').value));
  $('convText').addEventListener('input', autoSize);
  $('convText').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText($('convText').value); }
  });
  $('convAutoplay').addEventListener('change', (e) => { conv.autoplay = e.target.checked; });

  // input + voice mode segments
  wireSegment('convSttSeg', 'stt', (v) => {
    conv.sttMode = v;
    if (v === 'whisper' && !conv.avail.whisper) {
      setStatus('Whisper isn’t installed on the server (pip install faster-whisper). Using browser mic instead.', 'warn');
    }
    inputsEnabled(!conv.busy);
  });
  wireSegment('convVoiceSeg', 'voice', (v) => {
    conv.voiceMode = v;
    if (v === 'styletts2' && !conv.avail.styletts) {
      setStatus('StyleTTS2 isn’t installed on the server (pip install styletts2). Using browser voice instead.', 'warn');
    }
  });

  // settings drawer
  $('convSettingsToggle').addEventListener('click', () => {
    if ($('convSettingsDrawer').hidden) { fillSettingsForm(); openSettings(); }
    else closeSettings();
  });
  $('convSettingsClose').addEventListener('click', closeSettings);
  $('convSettingsSave').addEventListener('click', saveSettings);
  $('convProviderRow').querySelectorAll('.q-btn').forEach((b) =>
    b.addEventListener('click', () => selectProviderTab(b.dataset.provider)));
  ['convLevelRow', 'convWhisperRow'].forEach((rowId) => {
    const row = $(rowId);
    row.querySelectorAll('.q-btn').forEach((b) =>
      b.addEventListener('click', () => {
        row.querySelectorAll('.q-btn').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
      }));
  });

  // some browsers populate voices asynchronously
  if ('speechSynthesis' in window) {
    window.speechSynthesis.onvoiceschanged = () => {};
  }
}

export async function openConversationPage() {
  // refresh availability + settings + sessions
  try { conv.avail = await api.convAvailable(); } catch {}
  try { conv.settings = await api.convGetSettings(); } catch {}

  // reflect saved defaults into the toggles
  if (conv.settings) {
    conv.voiceMode = conv.settings.voice_mode || 'browser';
    conv.sttMode = conv.settings.stt_mode || 'browser';
    setActiveSeg('convVoiceSeg', 'voice', conv.voiceMode);
    setActiveSeg('convSttSeg', 'stt', conv.sttMode);
  }

  // availability hints
  const hints = [];
  if (!browserSttSupported()) hints.push('Your browser lacks live mic recognition — use Chrome, or the Whisper input mode.');
  if (!conv.avail.whisper) hints.push('Whisper input needs: pip install faster-whisper.');
  if (!conv.avail.styletts) hints.push('StyleTTS2 voice needs: pip install styletts2.');
  const hintEl = $('convAvailHint');
  if (hintEl) {
    hintEl.hidden = hints.length === 0;
    hintEl.innerHTML = hints.map(esc).join('<br>');
  }

  await loadSessions();
  inputsEnabled(true);

  // open the most recent session, or invite to start one
  if (!conv.currentId && conv.sessions.length) {
    openSession(conv.sessions[0].id);
  } else if (conv.currentId) {
    openSession(conv.currentId);
  } else {
    renderThread([]);
  }

  if (!isConfigured()) {
    setStatus('Add a free API key in “AI settings” to begin.', 'warn');
  } else {
    setStatus('');
  }
}

function setActiveSeg(rowId, attr, value) {
  const row = $(rowId);
  if (!row) return;
  row.querySelectorAll('.conv-seg-btn').forEach((b) =>
    b.classList.toggle('active', b.dataset[attr] === value));
}
