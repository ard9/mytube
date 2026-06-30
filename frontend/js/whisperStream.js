/*
 * whisperStream.js
 * ----------------
 * Browser-side client for the real-time Whisper streaming endpoint
 * (WebSocket /api/conversation/stt_stream).
 *
 * What it does, end to end:
 *   1. Opens the mic and an AudioContext.
 *   2. Down-samples the mic to 16 kHz mono and ships small int16 PCM frames
 *      over a WebSocket every ~`sendIntervalMs`.
 *   3. Receives {committed, pending} updates and calls onPartial(committed,
 *      pending) so the UI can show stable text + a live grey tail — Google style.
 *   4. Runs a simple energy VAD; when it detects you've stopped speaking it
 *      sends an {"type":"endpoint"} control frame. The server replies with
 *      {"type":"final"} and onFinal(text) fires (commit the turn / send to LLM).
 *
 * Usage:
 *   const ws = new WhisperStream({
 *     onPartial: (committed, pending) => { ... live UI ... },
 *     onFinal:   (text) => { ... send `text` to the tutor ... },
 *     onError:   (msg) => { ... },
 *   });
 *   await ws.start();      // tap-the-mic
 *   ...
 *   ws.stop();             // release mic + socket
 *
 * It is transport/UI agnostic: it owns capture + VAD + socket only, and reports
 * through callbacks, so it drops into the existing hands-free flow cleanly.
 */

class WhisperStream {
  constructor(opts = {}) {
    this.onPartial = opts.onPartial || (() => {});
    this.onFinal = opts.onFinal || (() => {});
    this.onError = opts.onError || (() => {});
    this.onState = opts.onState || (() => {});   // 'listening' | 'speaking'
    this.onLevel = opts.onLevel || (() => {});   // (rms, threshold) for a live meter
    this.onBargeIn = opts.onBargeIn || (() => {}); // user started talking over the tutor

    // tunables (can be overridden; defaults mirror the backend defaults)
    this.sendIntervalMs = opts.sendIntervalMs || 250;
    this.lang = opts.lang || 'en';
    this.silenceMs = opts.silenceMs || 800;       // pause before endpointing
    this.sensitivity = opts.sensitivity ?? 0.5;   // 0..1

    this.ws = null;
    this.audioCtx = null;
    this.stream = null;
    this.source = null;
    this.processor = null;
    this.sink = null;

    this._pcmQueue = [];        // float32 chunks captured since last send
    this._sendTimer = null;
    this._ctxRate = 48000;

    // speech-gated streaming: only audio the VAD marks as speech is sent to
    // Whisper, so silence/noise (which Whisper hallucinates on) is never
    // transcribed. _prerollChunks keeps a little audio from just before speech
    // onset so the first word isn't clipped.
    this._streaming = false;
    this._prerollChunks = [];

    // VAD state
    this._noiseFloor = 0.01;
    this._calibStart = 0;
    this._aboveSince = 0;
    this._belowSince = 0;
    this._inSpeech = false;
    this._hadSpeech = false;    // did we capture any speech this utterance?
    // Capture mode:
    //   'active'   -> stream audio to Whisper + auto-send on a pause (normal listening)
    //   'speaking' -> mic muted (don't transcribe the tutor's TTS) BUT keep
    //                 watching for the user starting to talk, to fire onBargeIn
    //   'muted'    -> mic fully ignored (e.g. while the reply is being generated)
    this._mode = 'active';
  }

  /* Switch capture mode. See the _mode comment above for what each does. */
  setMode(mode) {
    // CRITICAL: do nothing if the mode is unchanged. listening->capturing are
    // BOTH 'active', so without this guard we'd wipe the VAD state (and restart
    // noise-floor calibration) in the middle of an utterance, which breaks
    // end-of-speech detection and makes it "keep listening" without ever sending.
    if (mode === this._mode) return;
    this._mode = mode;
    this._pcmQueue = [];
    this._streaming = false;
    this._prerollChunks = [];
    this._inSpeech = false;
    this._aboveSince = 0;
    this._belowSince = 0;
    this._hadSpeech = false;
    if (mode === 'active') {
      this._noiseFloor = 0.01;                 // clean recalibration for the new turn
      this._calibStart = performance.now();
    }
  }

  async start() {
    if (this.ws) return;
    // 1) mic
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.audioCtx = new Ctx();
    if (this.audioCtx.state === 'suspended') { try { await this.audioCtx.resume(); } catch {} }
    this._ctxRate = this.audioCtx.sampleRate;

    // ONE source feeds both capture and the VAD analyser (a second source from
    // the same stream makes Chrome hand the second one silence).
    this.source = this.audioCtx.createMediaStreamSource(this.stream);

    this.analyser = this.audioCtx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.smoothingTimeConstant = 0.2;
    this._vadData = new Uint8Array(this.analyser.fftSize);
    this.source.connect(this.analyser);

    // capture tap (silent sink so it never feeds back to the speakers)
    this.processor = this.audioCtx.createScriptProcessor(4096, 1, 1);
    this.sink = this.audioCtx.createGain();
    this.sink.gain.value = 0;
    this.processor.onaudioprocess = (e) => {
      this._pcmQueue.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    this.source.connect(this.processor);
    this.processor.connect(this.sink);
    this.sink.connect(this.audioCtx.destination);

    // 2) socket
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/api/conversation/stt_stream`);
    this.ws.binaryType = 'arraybuffer';
    this.ws.onmessage = (ev) => this._onMessage(ev);
    this.ws.onerror = () => this.onError('Streaming connection error.');
    this.ws.onclose = () => { this._sendTimer && clearInterval(this._sendTimer); };
    await this._waitOpen();

    // 3) start shipping frames + VAD
    this._calibStart = performance.now();
    this._sendTimer = setInterval(() => this._flushFrame(), this.sendIntervalMs);
    this.onState('listening');
    this._vadLoop();
  }

  stop() {
    try { this.ws && this.ws.readyState === 1 && this.ws.send(JSON.stringify({ type: 'close' })); } catch {}
    if (this._sendTimer) { clearInterval(this._sendTimer); this._sendTimer = null; }
    if (this._raf) cancelAnimationFrame(this._raf);
    try { this.processor && this.processor.disconnect(); } catch {}
    try { this.source && this.source.disconnect(); } catch {}
    try { this.sink && this.sink.disconnect(); } catch {}
    try { this.audioCtx && this.audioCtx.close(); } catch {}
    try { this.stream && this.stream.getTracks().forEach((t) => t.stop()); } catch {}
    try { this.ws && this.ws.close(); } catch {}
    this.ws = null; this.audioCtx = null; this.stream = null;
    this._pcmQueue = [];
  }

  _waitOpen() {
    return new Promise((resolve, reject) => {
      if (!this.ws) return reject(new Error('no socket'));
      if (this.ws.readyState === 1) return resolve();
      this.ws.onopen = () => resolve();
      const t = setTimeout(() => reject(new Error('socket open timeout')), 5000);
      this.ws.addEventListener('open', () => clearTimeout(t), { once: true });
    });
  }

  _onMessage(ev) {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'partial') {
      this.onPartial(m.committed || '', m.pending || '');
    } else if (m.type === 'final') {
      const text = (m.text || '').trim();
      this._hadSpeech = false;
      if (text) this.onFinal(text);
      this.onState('listening');
    } else if (m.type === 'error') {
      this.onError(m.detail || 'Streaming error.');
    }
  }

  /* ship audio to Whisper, but ONLY while the VAD says we're in speech. When
     not speaking, audio is kept as a short pre-roll and not sent — so silence
     and background noise never reach Whisper (and never get hallucinated into
     text). */
  _flushFrame() {
    if (!this.ws || this.ws.readyState !== 1) return;
    if (this._mode !== 'active') { this._pcmQueue = []; this._prerollChunks = []; return; }
    if (!this._pcmQueue.length) return;

    if (!this._streaming) {
      // not speaking yet: stash as pre-roll, send nothing
      for (const c of this._pcmQueue) this._prerollChunks.push(c);
      this._pcmQueue = [];
      this._trimPreroll();
      return;
    }

    // speaking: prepend any pre-roll (once) so the first word isn't clipped
    let chunks = this._pcmQueue;
    if (this._prerollChunks.length) {
      chunks = this._prerollChunks.concat(chunks);
      this._prerollChunks = [];
    }
    this._pcmQueue = [];

    let len = 0;
    for (const c of chunks) len += c.length;
    const flat = new Float32Array(len);
    let off = 0;
    for (const c of chunks) { flat.set(c, off); off += c.length; }
    const pcm16 = this._to16kInt16(flat, this._ctxRate);
    if (pcm16.length) this.ws.send(pcm16.buffer);
  }

  _trimPreroll() {
    const maxSamples = Math.floor(this._ctxRate * 0.4);   // keep ~0.4s of pre-roll
    let total = 0;
    for (const c of this._prerollChunks) total += c.length;
    while (this._prerollChunks.length
           && total - this._prerollChunks[0].length >= maxSamples) {
      total -= this._prerollChunks.shift().length;
    }
  }

  _to16kInt16(input, srcRate) {
    const dst = 16000;
    let data = input;
    if (srcRate !== dst) {
      const ratio = srcRate / dst;
      const outLen = Math.floor(input.length / ratio);
      data = new Float32Array(outLen);
      for (let i = 0; i < outLen; i++) {
        const pos = i * ratio;
        const i0 = Math.floor(pos);
        const i1 = Math.min(i0 + 1, input.length - 1);
        const frac = pos - i0;
        data[i] = input[i0] * (1 - frac) + input[i1] * frac;
      }
    }
    const out = new Int16Array(data.length);
    for (let i = 0; i < data.length; i++) {
      const s = Math.max(-1, Math.min(1, data[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  /* energy VAD: auto-sends when you stop talking (active mode), and detects
     when you start talking over the tutor (speaking mode -> barge-in). */
  _vadLoop() {
    if (!this.analyser) return;
    // 'muted' = ignore the mic entirely (e.g. while the reply is generating)
    if (this._mode === 'muted') { this._raf = requestAnimationFrame(() => this._vadLoop()); return; }

    this.analyser.getByteTimeDomainData(this._vadData);
    let sum = 0;
    for (let i = 0; i < this._vadData.length; i++) {
      const d = (this._vadData[i] - 128) / 128;
      sum += d * d;
    }
    const rms = Math.sqrt(sum / this._vadData.length);
    const now = performance.now();
    if (now - this._calibStart < 500) {
      this._noiseFloor = Math.max(this._noiseFloor * 0.9 + rms * 0.1, 0.005);
    }
    const mult = 3.2 - this.sensitivity * 1.8;
    let threshold = Math.max(0.012, this._noiseFloor * mult);
    // while the tutor is speaking, require a louder/clearer onset before treating
    // it as a real barge-in, so residual TTS echo doesn't trigger a false one.
    if (this._mode === 'speaking') threshold *= 1.7;
    this.onLevel(rms, threshold);

    if (rms > threshold) {
      this._belowSince = 0;
      if (this._aboveSince === 0) this._aboveSince = now;
      const minSpeech = this._mode === 'speaking' ? 260 : 160;   // longer debounce for barge-in
      if (!this._inSpeech && now - this._aboveSince >= minSpeech) {
        this._inSpeech = true;
        this._hadSpeech = true;
        if (this._mode === 'active') {
          this._streaming = true;     // open the gate: from here, audio flows to Whisper
        } else if (this._mode === 'speaking') {
          // user is talking over the tutor (barge-in). Switch OURSELVES to active
          // and open the gate right here, preserving the in-speech state — do NOT
          // route this through setMode(), which would reset _inSpeech/_streaming
          // and recalibrate the noise floor against our own ongoing speech, so
          // the gate would never re-open and nothing would transcribe.
          this._mode = 'active';
          this._streaming = true;
          this.onBargeIn();           // host stops the TTS + moves UI to 'capturing'
        }
        this.onState('speaking');
      }
    } else {
      this._aboveSince = 0;
      if (this._inSpeech) {
        if (this._belowSince === 0) this._belowSince = now;
        if (now - this._belowSince >= this.silenceMs) {
          this._inSpeech = false;
          this._belowSince = 0;
          if (this._mode === 'active') this._endpoint();
        }
      }
    }
    this._raf = requestAnimationFrame(() => this._vadLoop());
  }

  _endpoint() {
    if (!this._hadSpeech) return;            // ignore pure-silence triggers
    // flush any buffered speech first, then tell the server to finalize
    this._flushFrame();
    this._streaming = false;                 // close the gate until the next utterance
    this._prerollChunks = [];
    try { this.ws && this.ws.readyState === 1 && this.ws.send(JSON.stringify({ type: 'endpoint' })); } catch {}
  }
}

export { WhisperStream };