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
  // hands-free voice-to-voice
  handsFree: false,
  v2vState: 'idle',          // idle | listening | capturing | thinking | speaking
  audioCtx: null,
  analyser: null,
  vadSource: null,
  vadStream: null,
  vadData: null,
  vadRAF: null,
  inSpeech: false,
  aboveSince: 0,
  belowSince: 0,
  calibStart: 0,
  noiseFloor: 0.01,
  silenceMs: 800,
  sensitivity: 0.5,
  // live (streaming) Whisper pipeline
  v2vRecog: null,
  lastInterim: '',
  pcmProcessor: null,
  pcmSink: null,
  pcmChunks: [],
  ctxRate: 48000,
  partialTimer: null,
  partialBusy: false,
  lastPartialSamples: 0,
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
  removeThinking();                       // never stack more than one
  t.insertAdjacentHTML('beforeend',
    `<div class="conv-msg bot conv-thinking"><div class="conv-bubble conv-typing"><span></span><span></span><span></span></div></div>`);
  scrollThread();
}
function removeThinking() {
  document.querySelectorAll('.conv-thinking').forEach((el) => el.remove());
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
      if (conv.autoplay) speakReply(r.reply);
      else if (conv.handsFree) setV2vState('listening');
      await loadSessions();
    } catch (e) {
      removeThinking();
      setStatus(llmErr(e), 'error');
      if (conv.handsFree) setV2vState('listening');
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
  if (s.provider === 'ollama') return !!(s.ollama_model && s.ollama_model.trim());
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
  if (conv.handsFree) setV2vState('thinking');
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
    if (conv.autoplay) speakReply(r.reply);
    else if (conv.handsFree) setV2vState('listening');
    loadSessions();
  } catch (e) {
    removeThinking();
    setStatus(llmErr(e), 'error');
    if (conv.handsFree) setV2vState('listening');
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

async function speak(text, onDone) {
  text = (text || '').trim();
  const done = typeof onDone === 'function' ? onceFn(onDone) : null;
  if (!text) { if (done) done(); return; }
  stopSpeaking();
  if (conv.voiceMode === 'styletts2' && conv.avail.styletts) {
    return speakStyleTts(text, done);
  }
  // Browser speech synthesis
  if (!('speechSynthesis' in window)) {
    setStatus('This browser has no speech synthesis. Try the StyleTTS2 voice.', 'warn');
    if (done) done();
    return;
  }
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'en-US';
  const v = pickEnglishVoice();
  if (v) u.voice = v;
  u.rate = 0.98;
  if (done) { u.onend = done; u.onerror = done; }
  window.speechSynthesis.speak(u);
}

function onceFn(fn) {
  let called = false;
  return (...args) => { if (!called) { called = true; fn(...args); } };
}

async function speakStyleTts(text, done) {
  try {
    setStatus('Generating voice…');
    const job = await api.startTts({ text, title: 'conversation' });
    const id = job.id;
    let tries = 0;
    while (tries++ < 240) {
      const st = await api.getTtsStatus(id);
      if (st.status === 'done' && st.entry_id) {
        setStatus('');
        // a barge-in during generation may have cancelled this turn
        if (conv.handsFree && conv.v2vState !== 'speaking') { if (done) done(); return; }
        if (!conv.audioEl) conv.audioEl = new Audio();
        conv.audioEl.onended = done || null;
        conv.audioEl.onerror = done || null;
        conv.audioEl.src = api.ttsMediaUrl(st.entry_id);
        conv.audioEl.play().catch(() => { if (done) done(); });
        return;
      }
      if (st.status === 'error') { setStatus('Voice error: ' + (st.error || ''), 'warn'); if (done) done(); return; }
      if (st.status === 'cancelled') { setStatus(''); if (done) done(); return; }
      await new Promise((res) => setTimeout(res, 500));
    }
    setStatus('Voice timed out.', 'warn');
    if (done) done();
  } catch (e) {
    setStatus('Voice error: ' + e.message, 'warn');
    if (done) done();
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

/* ===================================================================== */
/* Hands-free voice-to-voice mode (VAD-driven)                           */
/* ===================================================================== */
function speakReply(text) {
  if (conv.handsFree) {
    setV2vState('speaking');
    speak(text, () => {
      // only fall back to listening if we're still in the speaking state
      // (a barge-in may have already moved us to capturing/thinking)
      if (conv.handsFree && conv.v2vState === 'speaking') setV2vState('listening');
    });
  } else {
    speak(text);
  }
}

const V2V_LABELS = {
  idle: 'Idle',
  listening: 'Listening… just talk',
  capturing: 'You’re speaking…',
  thinking: 'Thinking…',
  speaking: 'Tutor speaking… (talk to interrupt)',
};

function setV2vState(s) {
  conv.v2vState = s;
  const orb = $('convV2vOrb');
  const label = $('convV2vLabel');
  if (orb) orb.className = 'conv-v2v-orb ' + s;
  if (label) label.textContent = V2V_LABELS[s] || s;
  // clear the recognition buffer whenever we stop actively listening,
  // so leaked TTS / stray audio never bleeds into the next turn
  if (s === 'thinking' || s === 'speaking') {
    conv.finalBuf = '';
    conv.lastInterim = '';
    const box = $('convInterim');
    if (box) box.hidden = true;
  }
}

function persistVadPrefs() {
  api.convSetSettings({ vad_silence_ms: conv.silenceMs, vad_sensitivity: conv.sensitivity })
    .catch(() => {});
}

async function toggleHandsFree() {
  if (conv.handsFree) stopHandsFree();
  else await startHandsFree();
}

async function startHandsFree() {
  if (!conv.currentId && !conv.sessions.length) {
    // will be created below; fine
  }
  if (!isConfigured()) {
    setStatus('Add an API key (or pick a local Ollama model) in “AI settings” first.', 'warn');
    openSettings();
    return;
  }
  // mic + audio graph for VAD
  try {
    conv.vadStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (e) {
    setStatus('Microphone permission is needed for voice-to-voice: ' + e.message, 'error');
    return;
  }
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    conv.audioCtx = new Ctx();
    if (conv.audioCtx.state === 'suspended') { try { await conv.audioCtx.resume(); } catch {} }
    // ONE source feeds both the VAD analyser and the PCM capture tap. Creating a
    // second source from the same stream makes Chrome hand the second one silence.
    conv.vadSource = conv.audioCtx.createMediaStreamSource(conv.vadStream);
    conv.analyser = conv.audioCtx.createAnalyser();
    conv.analyser.fftSize = 1024;
    conv.analyser.smoothingTimeConstant = 0.2;
    conv.vadSource.connect(conv.analyser);
    conv.vadData = new Uint8Array(conv.analyser.fftSize);
  } catch (e) {
    setStatus('Could not start audio analysis: ' + e.message, 'error');
    releaseVadStream();
    return;
  }

  // whisper input needs the server package; warn early instead of failing silently
  if (conv.sttMode === 'whisper' && conv.avail && !conv.avail.whisper) {
    setStatus('Whisper isn’t installed on the server (pip install faster-whisper). '
      + 'Switch input to “Browser mic” for instant live text.', 'warn');
  }

  conv.handsFree = true;
  conv.inSpeech = false;
  conv.aboveSince = 0;
  conv.belowSince = 0;
  conv.calibStart = performance.now();
  conv.noiseFloor = 0.01;
  updateV2vButton();
  $('convV2vPanel').hidden = false;
  $('convMic').disabled = true;          // manual push-to-talk is off while hands-free
  setStatus('Voice-to-voice on. Headphones strongly recommended to avoid echo.', 'warn');

  // browser STT: keep a recognizer running for the whole session
  if (conv.sttMode === 'browser') startV2vRecognition();
  else setupPcmTap();                    // whisper: live streaming PCM pipeline
  $('convSttSeg').classList.add('locked');

  // greet if this is a fresh/empty session, otherwise just listen
  setV2vState('listening');
  vadLoop();

  if (!conv.currentId) {
    await newConversation();   // greeting plays, then speakReply returns us to listening
  } else {
    try {
      const sess = await api.convGetSession(conv.currentId);
      if (!sess.messages || !sess.messages.length) {
        appendThinking();
        const r = await api.convGreeting(conv.currentId);
        removeThinking();
        appendMessage(r.assistant);
        speakReply(r.reply);
      }
    } catch (e) {
      removeThinking();
      setStatus(llmErr(e), 'error');
      if (conv.handsFree) setV2vState('listening');
    }
  }
}

function stopHandsFree() {
  conv.handsFree = false;
  if (conv.vadRAF) { cancelAnimationFrame(conv.vadRAF); conv.vadRAF = null; }
  stopV2vRecognition();
  teardownPcmTap();
  if (conv.vadSource) { try { conv.vadSource.disconnect(); } catch {} conv.vadSource = null; }
  if (conv.mediaRecorder && conv.recording) { try { conv.mediaRecorder.stop(); } catch {} }
  conv.recording = false;
  stopSpeaking();
  releaseVadStream();
  if (conv.audioCtx) { try { conv.audioCtx.close(); } catch {} conv.audioCtx = null; }
  conv.analyser = null;
  conv.inSpeech = false;
  setV2vState('idle');
  $('convV2vPanel').hidden = true;
  $('convInterim').hidden = true;
  $('convSttSeg').classList.remove('locked');
  updateV2vButton();
  $('convMic').disabled = false;
  setStatus('');
}

function releaseVadStream() {
  if (conv.vadStream) {
    conv.vadStream.getTracks().forEach((t) => { try { t.stop(); } catch {} });
    conv.vadStream = null;
  }
}

function updateV2vButton() {
  const btn = $('convV2vBtn');
  if (!btn) return;
  btn.classList.toggle('on', conv.handsFree);
  const b = btn.querySelector('b');
  if (b) b.textContent = conv.handsFree ? 'on' : 'off';
}

/* --- the VAD energy loop --- */
function vadLoop() {
  if (!conv.handsFree || !conv.analyser) return;
  conv.analyser.getByteTimeDomainData(conv.vadData);
  // RMS energy around the 128 midpoint, normalised to ~0..1
  let sum = 0;
  for (let i = 0; i < conv.vadData.length; i++) {
    const d = (conv.vadData[i] - 128) / 128;
    sum += d * d;
  }
  const rms = Math.sqrt(sum / conv.vadData.length);
  const now = performance.now();

  // calibrate the noise floor during the first ~500ms of silence
  if (now - conv.calibStart < 500) {
    conv.noiseFloor = Math.max(conv.noiseFloor * 0.9 + rms * 0.1, 0.005);
  }
  // sensitivity 0..1 -> multiplier ~3.2 (insensitive) .. 1.4 (very sensitive)
  const mult = 3.2 - conv.sensitivity * 1.8;
  let threshold = Math.max(0.012, conv.noiseFloor * mult);
  // be stricter about what counts as a barge-in while the tutor is speaking
  if (conv.v2vState === 'speaking') threshold *= 1.7;

  // live meter
  const fill = $('convV2vMeterFill');
  if (fill) fill.style.width = Math.min(100, Math.round((rms / (threshold * 2.2)) * 100)) + '%';

  const speaking = rms > threshold;
  if (speaking) {
    conv.belowSince = 0;
    if (conv.aboveSince === 0) conv.aboveSince = now;
    const minSpeech = conv.v2vState === 'speaking' ? 260 : 160; // debounce
    if (!conv.inSpeech && now - conv.aboveSince >= minSpeech) {
      conv.inSpeech = true;
      onSpeechStart();
    }
  } else {
    conv.aboveSince = 0;
    if (conv.inSpeech) {
      if (conv.belowSince === 0) conv.belowSince = now;
      if (now - conv.belowSince >= conv.silenceMs) {
        conv.inSpeech = false;
        conv.belowSince = 0;
        onSpeechEnd();
      }
    }
  }
  conv.vadRAF = requestAnimationFrame(vadLoop);
}

function onSpeechStart() {
  if (conv.v2vState === 'thinking') return;          // busy processing a turn
  if (conv.v2vState === 'speaking') {                // barge-in
    stopSpeaking();
  }
  beginCapture();
}

function beginCapture() {
  setV2vState('capturing');
  $('convInterim').hidden = false;
  $('convInterim').textContent = '…';
  if (conv.sttMode === 'whisper') {
    startV2vRecorder();
  } else {
    // browser recognition is already running; finalBuf accumulates
    if (!conv.v2vRecog) startV2vRecognition();
  }
}

function onSpeechEnd() {
  if (conv.v2vState !== 'capturing') return;
  if (conv.sttMode === 'whisper') {
    stopV2vRecorder();           // its onstop transcribes, then commits
  } else {
    // include the live interim: the final result frequently hasn't arrived yet
    // at the instant the VAD detects the pause
    const text = ((conv.finalBuf || '') + ' ' + (conv.lastInterim || '')).trim();
    conv.finalBuf = '';
    conv.lastInterim = '';
    $('convInterim').hidden = true;
    flushRecognition();          // discard pending results so they don't leak forward
    if (text) commitTurn(text);
    else setV2vState('listening');
  }
}

function commitTurn(text) {
  if (conv.busy) {                 // a previous turn is still being answered
    setStatus('One moment — still getting the last reply…', 'warn');
    setV2vState('listening');
    return;
  }
  setV2vState('thinking');
  sendText(text);                // handles append + LLM + speakReply + resume
}

/* --- browser recognition tuned for hands-free --- */
function startV2vRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { setStatus('This browser has no live recognition; switch input to Whisper.', 'warn'); return; }
  const r = new SR();
  conv.v2vRecog = r;
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
    conv.lastInterim = interim;          // keep the latest interim for endpointing
    if (conv.v2vState === 'capturing') {
      const box = $('convInterim');
      box.hidden = false;
      box.textContent = (conv.finalBuf + interim).trim() || '…';
    }
  };
  r.onerror = (ev) => {
    if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
      setStatus('Microphone was blocked.', 'error');
      stopHandsFree();
    }
  };
  r.onend = () => { if (conv.handsFree && conv.sttMode === 'browser') { try { r.start(); } catch {} } };
  try { r.start(); } catch {}
}

// abort() (not stop()) discards any pending result so it can't bleed into the
// next turn; onend then restarts a clean recognizer.
function flushRecognition() {
  if (conv.v2vRecog) { try { conv.v2vRecog.abort(); } catch {} }
}

function stopV2vRecognition() {
  if (conv.v2vRecog) { try { conv.v2vRecog.onend = null; conv.v2vRecog.stop(); } catch {} conv.v2vRecog = null; }
}

/* --- live (streaming) Whisper for hands-free: raw PCM -> 16k WAV -> server --- */
function setupPcmTap() {
  if (!conv.audioCtx || !conv.vadSource) return;
  try {
    const proc = conv.audioCtx.createScriptProcessor(4096, 1, 1);
    const sink = conv.audioCtx.createGain();
    sink.gain.value = 0;                              // silent: never feed back to speakers
    proc.onaudioprocess = (e) => {
      if (conv.v2vState !== 'capturing') return;
      conv.pcmChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    conv.vadSource.connect(proc);                    // reuse the shared mic source
    proc.connect(sink);
    sink.connect(conv.audioCtx.destination);
    conv.pcmProcessor = proc;
    conv.pcmSink = sink;
    conv.ctxRate = conv.audioCtx.sampleRate;
  } catch (e) {
    setStatus('Could not start streaming capture: ' + e.message, 'warn');
  }
}

function teardownPcmTap() {
  if (conv.partialTimer) { clearInterval(conv.partialTimer); conv.partialTimer = null; }
  if (conv.pcmProcessor) {
    try { conv.pcmProcessor.disconnect(); conv.pcmProcessor.onaudioprocess = null; } catch {}
    conv.pcmProcessor = null;
  }
  if (conv.pcmSink) { try { conv.pcmSink.disconnect(); } catch {} conv.pcmSink = null; }
  conv.pcmChunks = [];
  conv.partialBusy = false;
  conv.lastPartialSamples = 0;
}

function pcmTotalSamples() {
  let n = 0;
  for (const c of conv.pcmChunks) n += c.length;
  return n;
}

function flattenPcm() {
  const total = pcmTotalSamples();
  const out = new Float32Array(total);
  let off = 0;
  for (const c of conv.pcmChunks) { out.set(c, off); off += c.length; }
  return out;
}

function resampleTo16k(input, srcRate) {
  const dstRate = 16000;
  if (srcRate === dstRate) return input;
  const ratio = srcRate / dstRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = pos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

function buildWav16k(maxSeconds) {
  if (!conv.pcmChunks.length) return null;
  let flat = flattenPcm();
  // for live partials, only transcribe the tail so latency stays bounded
  if (maxSeconds && flat.length > conv.ctxRate * maxSeconds) {
    flat = flat.slice(flat.length - Math.floor(conv.ctxRate * maxSeconds));
  }
  const pcm = resampleTo16k(flat, conv.ctxRate);
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buf);
  const ws = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  ws(0, 'RIFF'); view.setUint32(4, 36 + pcm.length * 2, true); ws(8, 'WAVE');
  ws(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, 16000, true);
  view.setUint32(28, 16000 * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  ws(36, 'data'); view.setUint32(40, pcm.length * 2, true);
  let off = 44;
  for (let i = 0; i < pcm.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: 'audio/wav' });
}

function startV2vRecorder() {
  // streaming capture: PCM accumulates via the tap; kick off the partial loop
  conv.pcmChunks = [];
  conv.lastPartialSamples = 0;
  if (conv.partialTimer) clearInterval(conv.partialTimer);
  conv.partialTimer = setInterval(runPartialTranscription, 700);
}

async function runPartialTranscription() {
  if (conv.partialBusy || conv.v2vState !== 'capturing') return;
  const total = pcmTotalSamples();
  if (total < conv.ctxRate * 0.25) return;                  // need ~0.25s of audio
  if (total - conv.lastPartialSamples < conv.ctxRate * 0.3) return; // ~0.3s new audio
  conv.lastPartialSamples = total;
  const wav = buildWav16k(7);          // last ~7s only -> fast partials
  if (!wav) return;
  conv.partialBusy = true;
  try {
    const res = await api.convStt(wav, conv.settings ? conv.settings.whisper_model : '', false, true);
    if (conv.v2vState === 'capturing' && res.text) {
      const box = $('convInterim');
      box.hidden = false;
      box.textContent = res.text;
    }
  } catch { /* ignore a dropped partial */ }
  finally { conv.partialBusy = false; }
}

async function stopV2vRecorder() {
  if (conv.partialTimer) { clearInterval(conv.partialTimer); conv.partialTimer = null; }
  const total = pcmTotalSamples();
  $('convInterim').hidden = true;
  if (total < conv.ctxRate * 0.3) { conv.pcmChunks = []; setV2vState('listening'); return; }
  setV2vState('thinking');
  $('convV2vLabel').textContent = 'Transcribing…';
  const wav = buildWav16k();
  conv.pcmChunks = [];
  try {
    const res = await api.convStt(wav, conv.settings ? conv.settings.whisper_model : '', false, false);
    const text = (res.text || '').trim();
    if (text) commitTurn(text);
    else { setStatus('Didn’t catch that — try again, or use Browser mic.', 'warn'); setV2vState('listening'); }
  } catch (e) {
    setStatus('Whisper error: ' + e.message, 'warn');
    setV2vState('listening');
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
  $('convProxy').value = s.proxy || '';
  $('convOllamaModel').value = s.ollama_model || '';
  $('convOllamaBase').value = s.ollama_base_url || '';
  setActiveBtn('convLevelRow', 'level', s.level || 'auto');
  setActiveBtn('convWhisperRow', 'whisper', s.whisper_model || 'base');
  setActiveBtn('convWhisperDeviceRow', 'device', s.whisper_device || 'auto');
  refreshWhisperBadge();
}

async function refreshWhisperBadge() {
  const badge = $('convWhisperBadge');
  if (!badge) return;
  try {
    const info = await api.convWhisperInfo();
    if (!info.available) {
      badge.textContent = 'Whisper: not installed (pip install faster-whisper)';
      badge.className = 'conv-whisper-badge down';
      return;
    }
    if (!info.loaded) {
      badge.textContent = info.cuda
        ? 'Whisper: GPU available — loads on first use'
        : 'Whisper: no CUDA GPU detected — will use CPU (slow)';
      badge.className = 'conv-whisper-badge ' + (info.cuda ? 'ok' : 'warn');
      return;
    }
    const onGpu = info.device === 'cuda';
    badge.textContent = onGpu
      ? `Whisper: running on GPU (${info.compute}) ✓`
      : 'Whisper: running on CPU (slow) — see below';
    badge.className = 'conv-whisper-badge ' + (onGpu ? 'ok' : 'warn');
  } catch {
    badge.textContent = 'Whisper: status unavailable';
    badge.className = 'conv-whisper-badge';
  }
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
  if (provider === 'ollama') refreshOllamaStatus();
}

/* ---------- Ollama (local models) ---------- */
async function refreshOllamaStatus() {
  const dot = $('convOllamaStatus');
  const txt = $('convOllamaStatusText');
  const base = $('convOllamaBase').value.trim();
  if (txt) txt.textContent = 'Checking Ollama…';
  if (dot) dot.className = 'conv-ollama-status checking';
  try {
    const s = await api.ollamaStatus(base);
    if (s.running) {
      if (dot) dot.className = 'conv-ollama-status ok';
      if (txt) txt.textContent = `Ollama is running — ${s.models.length} model(s) installed`;
      renderInstalled(s.models);
    } else {
      if (dot) dot.className = 'conv-ollama-status down';
      if (txt) txt.textContent = 'Ollama is not running — install from ollama.com and start it';
      renderInstalled([]);
    }
  } catch {
    if (dot) dot.className = 'conv-ollama-status down';
    if (txt) txt.textContent = 'Could not reach Ollama';
    renderInstalled([]);
  }
}

function renderInstalled(models) {
  const box = $('convOllamaInstalled');
  if (!box) return;
  if (!models || !models.length) { box.innerHTML = '<span class="muted">No models yet — download one above.</span>'; return; }
  box.innerHTML = models.map((m) => {
    const gb = m.size ? ` <span>${(m.size / 1e9).toFixed(1)}GB</span>` : '';
    return `<button class="conv-modelchip installed" data-model="${esc(m.name)}">${esc(m.name)}${gb}</button>`;
  }).join('');
}

async function pullOllamaModel() {
  const model = $('convOllamaModel').value.trim();
  if (!model) { setStatus('Type a model name first (e.g. llama3.2:3b).', 'warn'); return; }
  const base = $('convOllamaBase').value.trim();
  const btn = $('convOllamaPull');
  const prog = $('convOllamaProgress');
  const fill = $('convOllamaFill');
  const st = $('convOllamaPullStatus');
  btn.disabled = true;
  prog.hidden = false;
  fill.style.width = '0%';
  st.textContent = 'Starting download…';
  try {
    const job = await api.ollamaPull(model, base);
    let tries = 0;
    const poll = async () => {
      tries++;
      let j;
      try { j = await api.ollamaPullStatus(job.id); }
      catch { st.textContent = 'Lost track of the download.'; btn.disabled = false; return; }
      fill.style.width = (j.percent || 0) + '%';
      st.textContent = `${j.detail || j.status} — ${(j.percent || 0).toFixed(0)}%`;
      if (j.status === 'done') {
        st.textContent = 'Downloaded ✓';
        btn.disabled = false;
        setTimeout(() => (prog.hidden = true), 1500);
        refreshOllamaStatus();
        return;
      }
      if (j.status === 'error') {
        st.textContent = 'Error: ' + (j.error || 'download failed');
        btn.disabled = false;
        return;
      }
      if (tries > 5000) { st.textContent = 'Timed out.'; btn.disabled = false; return; }
      setTimeout(poll, 600);
    };
    poll();
  } catch (e) {
    st.textContent = 'Error: ' + e.message;
    btn.disabled = false;
  }
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
    proxy: $('convProxy').value.trim(),
    ollama_model: $('convOllamaModel').value.trim(),
    ollama_base_url: $('convOllamaBase').value.trim(),
    level: getActiveBtn('convLevelRow', 'level', 'auto'),
    whisper_model: getActiveBtn('convWhisperRow', 'whisper', 'base'),
    whisper_device: getActiveBtn('convWhisperDeviceRow', 'device', 'auto'),
  };
  try {
    conv.settings = await api.convSetSettings(body);
    const msg = $('convSettingsMsg');
    msg.hidden = false;
    msg.textContent = 'Saved ✓';
    setTimeout(() => (msg.hidden = true), 2000);
    setStatus('');
    refreshWhisperBadge();
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

  // voice-to-voice (hands-free) controls
  $('convV2vBtn').addEventListener('click', toggleHandsFree);
  $('convV2vPauseSeg').querySelectorAll('.conv-seg-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      $('convV2vPauseSeg').querySelectorAll('.conv-seg-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      conv.silenceMs = parseInt(btn.dataset.pause, 10) || 800;
      persistVadPrefs();
    });
  });
  $('convV2vSens').addEventListener('input', (e) => {
    conv.sensitivity = (parseInt(e.target.value, 10) || 50) / 100;
  });
  $('convV2vSens').addEventListener('change', persistVadPrefs);

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

  // Ollama: download button, suggested-model chips, installed list, address field
  $('convOllamaPull').addEventListener('click', pullOllamaModel);
  $('convOllamaSuggest').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-model]');
    if (chip) $('convOllamaModel').value = chip.dataset.model;
  });
  $('convOllamaInstalled').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-model]');
    if (chip) { $('convOllamaModel').value = chip.dataset.model; setStatus('Model selected — press Save AI settings.', 'warn'); }
  });
  $('convOllamaBase').addEventListener('change', refreshOllamaStatus);
  ['convLevelRow', 'convWhisperRow', 'convWhisperDeviceRow'].forEach((rowId) => {
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
    conv.silenceMs = parseInt(conv.settings.vad_silence_ms, 10) || 800;
    conv.sensitivity = (conv.settings.vad_sensitivity != null) ? conv.settings.vad_sensitivity : 0.5;
    setActiveSeg('convV2vPauseSeg', 'pause', String(conv.silenceMs));
    const sens = $('convV2vSens');
    if (sens) sens.value = Math.round(conv.sensitivity * 100);
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
