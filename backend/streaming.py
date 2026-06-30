"""
streaming.py
------------
Real-time, low-latency speech-to-text on top of faster-whisper.

Whisper is NOT a streaming model: it transcribes a whole audio chunk at once.
Naively re-transcribing a growing buffer every few hundred ms "works", but the
already-shown words keep changing on every pass (flicker), which looks terrible.

This module implements the well-known fix used by whisper_streaming
(Macháček et al., 2023) and WhisperLive: **LocalAgreement-2**.

The idea:
  * Keep a rolling audio buffer of the *current, not-yet-finished* utterance.
  * Every ~`min_interval_ms` of new audio, re-transcribe the WHOLE buffer with
    word-level timestamps.
  * Only "commit" (freeze) the longest prefix of words that was identical across
    the last TWO passes. Two independent passes agreeing on a word is strong
    evidence it's correct, so it's safe to lock it in and never touch it again.
  * The still-unstable tail after the committed part is shown as "pending"
    (think of the grey live text in Google's dictation).
  * Once words are committed AND we have their end timestamp, we can cut that
    audio out of the buffer so it never grows unbounded and inference stays
    fast (this is the "buffer resets / max N seconds" behaviour).
  * On a VAD silence endpoint, `finish()` flushes whatever is left as final and
    the streamer resets for the next turn.

All the tunables (how often to infer, how big the buffer may grow, which model)
come from the conversation settings so they can be configured, exactly like the
rest of Echo.

This module owns ONLY the streaming algorithm + the thin faster-whisper glue.
Transport (a WebSocket) lives in main.py; audio capture lives in the frontend.
"""

from __future__ import annotations

import logging

import numpy as np

import conversation  # reuse its cached WhisperModel loader + settings

log = logging.getLogger("mytube.streaming")

SAMPLE_RATE = 16000  # everything here assumes 16 kHz mono float32, like Whisper wants


# --------------------------------------------------------------------------- #
# Config knobs (read from conversation settings, with sensible defaults)
# --------------------------------------------------------------------------- #
def _cfg() -> dict:
    s = conversation.load_settings()
    return {
        # how often (ms of wall time) we actually run an inference pass. Lower =
        # snappier but heavier. 400-600ms is a good balance on a GPU.
        "min_interval_ms": int(s.get("stream_min_interval_ms", 500) or 500),
        # how long (seconds) the audio buffer may grow before we force a trim up
        # to the last committed word. Bigger = more context for Whisper (better
        # accuracy) but slower passes. 12-16s is typical.
        "buffer_trim_s": float(s.get("stream_buffer_trim_s", 14.0) or 14.0),
        # the (small/fast) model used for the live stream. tiny/base keep latency
        # low; the heavier "real" model can re-clean the text on the final flush.
        "model": s.get("whisper_partial_model") or "tiny",
        "language": s.get("stream_language", "en") or "en",
    }


# --------------------------------------------------------------------------- #
# faster-whisper glue: numpy audio -> list of (start, end, word)
# --------------------------------------------------------------------------- #
def _transcribe_words(model, audio: np.ndarray, language: str) -> list[tuple]:
    """
    Run one inference pass over `audio` (float32 16k mono) and return a flat
    list of (start, end, word) tuples in *buffer-relative* seconds.

    Tuned for streaming: greedy (beam_size=1) for speed, word timestamps on (we
    need per-word end times to trim the buffer), and condition_on_previous_text
    OFF so a bad partial can't poison later passes with hallucinated drift.
    """
    segments, _info = model.transcribe(
        audio,
        language=language or None,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        word_timestamps=True,
        condition_on_previous_text=False,
        vad_filter=False,            # the frontend VAD already gates capture
    )
    words: list[tuple] = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                # w.word keeps its leading space, so "".join rebuilds text cleanly
                words.append((float(w.start), float(w.end), w.word))
        else:
            # some very short segments come back without per-word timing
            words.append((float(seg.start), float(seg.end), seg.text))
    return words


# --------------------------------------------------------------------------- #
# LocalAgreement-2 hypothesis buffer
# --------------------------------------------------------------------------- #
class _HypothesisBuffer:
    """
    Holds the previous pass's unconfirmed tail (`buffer`) and confirms words that
    the new pass (`new`) agrees with, in order, from the front. This is the core
    of LocalAgreement-2.
    """

    def __init__(self) -> None:
        self.buffer: list[tuple] = []           # last pass's still-unconfirmed words
        self.new: list[tuple] = []              # current pass's words (absolute time)
        self.committed_in_buffer: list[tuple] = []  # words already committed (for dedup)
        self.last_committed_time: float = 0.0

    def insert(self, words: list[tuple], offset: float) -> None:
        # shift buffer-relative times to absolute (account for trimmed audio)
        shifted = [(s + offset, e + offset, w) for s, e, w in words]
        # keep only words that *start* after what we've already committed
        self.new = [t for t in shifted if t[0] > self.last_committed_time - 0.1]
        if not self.new:
            return
        # n-gram overlap removal: a new pass re-emits the tail of words we've
        # already committed (it sees the whole buffer). If the first new word
        # lines up with the commit boundary, strip leading new words that
        # duplicate the committed tail (up to a 5-gram) so they aren't repeated.
        first_start = self.new[0][0]
        if abs(first_start - self.last_committed_time) < 1.0 and self.committed_in_buffer:
            cn = len(self.committed_in_buffer)
            nn = len(self.new)
            for i in range(1, min(cn, nn, 5) + 1):
                committed_tail = " ".join(
                    self.committed_in_buffer[-j][2].strip() for j in range(i, 0, -1)
                )
                new_head = " ".join(self.new[j][2].strip() for j in range(i))
                if committed_tail == new_head:
                    del self.new[:i]
                    break

    def flush(self) -> list[tuple]:
        """Confirm the longest common prefix of `new` and `buffer`."""
        committed: list[tuple] = []
        while self.new and self.buffer:
            if self.new[0][2].strip() == self.buffer[0][2].strip():
                committed.append(self.new[0])
                self.last_committed_time = self.new[0][1]
                self.new.pop(0)
                self.buffer.pop(0)
            else:
                break
        # whatever the new pass produced becomes the tail to confirm next time
        self.buffer = self.new
        self.new = []
        self.committed_in_buffer.extend(committed)
        return committed


# --------------------------------------------------------------------------- #
# The streamer
# --------------------------------------------------------------------------- #
class WhisperStreamer:
    def __init__(self, model, language: str, min_interval_ms: int,
                 buffer_trim_s: float) -> None:
        self.model = model
        self.language = language
        self.min_interval_s = max(0.1, min_interval_ms / 1000.0)
        self.buffer_trim_s = buffer_trim_s

        self.audio = np.zeros(0, dtype=np.float32)
        self.buffer_time_offset = 0.0        # seconds of audio already trimmed away
        self.hyp = _HypothesisBuffer()
        self.committed: list[tuple] = []     # all confirmed (start, end, word)
        self._samples_since_infer = 0        # gate inference by *new audio*, not wall time

    # -- audio in -------------------------------------------------------------
    def insert_audio(self, pcm: np.ndarray) -> None:
        self.audio = np.append(self.audio, pcm)
        self._samples_since_infer += len(pcm)

    def due(self) -> bool:
        """True once enough fresh audio has arrived to justify another pass."""
        return self._samples_since_infer >= int(self.min_interval_s * SAMPLE_RATE)

    # -- one streaming step ---------------------------------------------------
    def process(self) -> tuple[str, str]:
        """
        Run a pass, confirm agreed words, maybe trim the buffer.
        Returns (committed_text_so_far, pending_text).
        """
        self._samples_since_infer = 0
        if len(self.audio) < int(0.3 * SAMPLE_RATE):
            return self.committed_text(), self.pending_text()

        words = _transcribe_words(self.model, self.audio, self.language)
        self.hyp.insert(words, self.buffer_time_offset)
        self.committed.extend(self.hyp.flush())

        if len(self.audio) / SAMPLE_RATE > self.buffer_trim_s:
            self._trim_to_last_committed()

        return self.committed_text(), self.pending_text()

    def _trim_to_last_committed(self) -> None:
        if not self.committed:
            return
        last_end = self.committed[-1][1]                 # absolute seconds
        cut_s = last_end - self.buffer_time_offset       # seconds into current buffer
        n = int(max(0.0, cut_s) * SAMPLE_RATE)
        if 0 < n < len(self.audio):
            self.audio = self.audio[n:]
            self.buffer_time_offset += n / SAMPLE_RATE

    # -- endpoint -------------------------------------------------------------
    def finish(self) -> str:
        """Flush the remaining unconfirmed tail as final text for this turn."""
        self.committed.extend(self.hyp.buffer)
        self.hyp.buffer = []
        return self.committed_text()

    # -- text views -----------------------------------------------------------
    def committed_text(self) -> str:
        return "".join(w for _, _, w in self.committed).strip()

    def pending_text(self) -> str:
        return "".join(w for _, _, w in self.hyp.buffer).strip()


# --------------------------------------------------------------------------- #
# Factory + helpers used by the WebSocket endpoint
# --------------------------------------------------------------------------- #
def make_streamer() -> WhisperStreamer:
    """Build a streamer using the current settings and the cached Whisper model."""
    cfg = _cfg()
    model = conversation._get_whisper(cfg["model"])   # reuses the GPU/CPU cache
    log.info("Streaming STT started: model=%s interval=%dms trim=%.0fs",
             cfg["model"], int(cfg["min_interval_ms"]), cfg["buffer_trim_s"])
    return WhisperStreamer(
        model=model,
        language=cfg["language"],
        min_interval_ms=cfg["min_interval_ms"],
        buffer_trim_s=cfg["buffer_trim_s"],
    )


def pcm16_to_float32(raw: bytes) -> np.ndarray:
    """Convert little-endian int16 PCM bytes (what the browser sends) to float32."""
    if not raw:
        return np.zeros(0, dtype=np.float32)
    ints = np.frombuffer(raw, dtype="<i2")
    return (ints.astype(np.float32) / 32768.0).copy()