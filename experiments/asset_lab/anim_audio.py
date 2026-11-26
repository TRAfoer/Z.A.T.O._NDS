"""Play the animation preview through one stereo output stream.

Only approved ``nds/nitrofs`` WAVs are read. FFmpeg decodes to memory; this
module never converts source audio, writes assets, or edits loop approvals.
``prepare([(group, name), ...])`` preloads just the current scene's assets and
should run before playback (or on a worker while the GUI is loading).

Music keeps its approved stereo channels and intro/loop markers. The one SFX
voice plays once, duplicating mono at equal amplitude to both output channels.
Volume and manifest alias gains may exceed 1. The default master gain is 0.5
to provide mixing headroom; clipping happens after both voices are summed.

``start_stream=False`` plus ``decoder(path) -> PCM`` supports deterministic
offline use: call ``render(frames)`` to advance the exact same mixer used by
the device callback. Float PCM uses [-1, 1]; int16 input is normalized.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
import threading
import time
from typing import Callable, Iterable

import numpy as np


RATE = 22050
GROUPS = ("music", "sfx")


def _name(value: str) -> str:
    value = str(value).replace("\\", "/").strip()
    if value.casefold().startswith("audio/"):
        value = value[6:]
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError(f"Invalid audio name: {value!r}")
    return path.as_posix().casefold()


def _positive(value: float, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return result


@dataclass
class _Clip:
    pcm: np.ndarray
    loop_start: int
    loop_end: int


@dataclass
class _Voice:
    clip: _Clip
    group: str
    name: str
    source: str
    volume: float
    alias_gain: float
    position: int = 0
    fade_start: float = 1.0
    fade_target: float = 1.0
    fade_frames: int = 0
    fade_elapsed: int = 0
    stopping: bool = False

    @property
    def gain(self) -> float:
        if not self.fade_frames:
            return self.fade_target
        progress = min(1.0, self.fade_elapsed / self.fade_frames)
        return self.fade_start + (self.fade_target - self.fade_start) * progress

    def fade(self, target: float, frames: int):
        self.fade_start = self.gain
        self.fade_target = target
        self.fade_frames = frames
        self.fade_elapsed = 0

    def consume(self, frames: int, out: np.ndarray | None = None) -> bool:
        """Advance one voice; optionally add samples without clipping them."""
        count = frames
        if self.group == "sfx":
            count = min(count, len(self.clip.pcm) - self.position)
        if self.stopping:
            count = min(count, self.fade_frames - self.fade_elapsed)
        if count > 0:
            if out is not None:
                positions = self.position + np.arange(count)
                if self.group == "music":
                    wrapped = positions >= self.clip.loop_end
                    positions[wrapped] = self.clip.loop_start + (
                        (positions[wrapped] - self.clip.loop_end)
                        % (self.clip.loop_end - self.clip.loop_start))
                samples = self.clip.pcm[positions]
                if self.fade_frames and self.fade_elapsed < self.fade_frames:
                    progress = np.minimum(1.0, (self.fade_elapsed + np.arange(count))
                                          / self.fade_frames)
                    gain = self.fade_start + (self.fade_target - self.fade_start) * progress
                    samples = samples * gain[:, None]
                else:
                    samples = samples * self.fade_target
                out[:count] += samples * (self.volume * self.alias_gain)
            self.position += count
            self.fade_elapsed = min(self.fade_frames, self.fade_elapsed + count)
            if self.group == "music" and self.position >= self.clip.loop_end:
                self.position = self.clip.loop_start + ((self.position - self.clip.loop_end)
                                 % (self.clip.loop_end - self.clip.loop_start))
        if self.stopping and self.fade_elapsed >= self.fade_frames:
            return False
        return self.group == "music" or self.position < len(self.clip.pcm)


class SceneAudio:
    """Thread-safe scene audio; GUI actions return errors through ``status``.

    ``play`` replaces the current voice in that group and returns whether the
    asset was scheduled. Device failures do not invalidate that schedule: they
    are exposed in ``status['errors']`` and a wall clock tracks silent cursors.
    ``enabled=False`` mutes while advancing; ``pause()`` freezes both voices
    and their fades. ``reset()`` stops both voices and clears pause, retaining
    the prepared PCM cache. ``close()`` releases the one output device.

    Positions in ``status`` are mixed sample cursors, ahead of audible output
    by the device queue. Callback DAC timestamps expose that queue's clock;
    they do not make already buffered samples retractable on pause or reset.
    """

    def __init__(self, enabled=True, *, manifest_path=None, nitrofs_root=None,
                 master_gain=0.5, decoder: Callable | None = None,
                 stream_factory: Callable | None = None, start_stream=True):
        project = Path(__file__).resolve().parents[2]
        self.manifest_path = Path(manifest_path or project / "nds/artifacts/audio_manifest.json")
        self.nitrofs_root = Path(nitrofs_root or project / "nds/nitrofs").resolve()
        self.master_gain = _positive(master_gain, "master_gain")
        self._lock = threading.RLock()
        self._decode_lock = threading.Lock()
        self._stream_lock = threading.Lock()
        self._enabled = bool(enabled)
        self._paused = False
        self._closed = False
        self._errors = deque(maxlen=64)
        self._assets = {}
        self._aliases = {}
        self._cache = {}
        self._voices = {}
        self._decoder = decoder
        self._stream_factory = stream_factory
        self._start_stream = bool(start_stream)
        self._stream = None
        self._requested_latency = "low"
        self._stream_latency_seconds = None
        self._stream_active = False
        self._stream_failed = False
        self._clock_anchor = time.monotonic()
        self._callback_info = {"count": 0, "frames": 0, "total_frames": 0, "status": "",
                               "current_time": None, "output_buffer_dac_time": None}
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if manifest.get("rate", RATE) != RATE:
                raise ValueError(f"Manifest sample rate must be {RATE}")
            self._aliases = {_name(k): _name(v) for k, v in
                             manifest.get("source_aliases", {}).items()}
            for asset in manifest["assets"]:
                group = asset["group"]
                if group not in GROUPS:
                    continue
                for alias in [asset["source"], *asset.get("aliases", [])]:
                    self._assets[(group, _name(alias))] = asset
        except Exception as exc:
            self._error(f"Audio manifest: {exc}")

    def _error(self, message):
        with self._lock:
            message = str(message)
            if not self._errors or self._errors[-1] != message:
                self._errors.append(message)

    def _resolve(self, group, name):
        if group not in GROUPS:
            raise ValueError(f"Unknown audio group: {group!r}")
        alias = _name(name)
        canonical = self._aliases.get(alias, alias)
        asset = self._assets.get((group, alias)) or self._assets.get((group, canonical))
        if asset is None:
            raise KeyError(f"No approved {group} asset for {name!r}")
        gains = {_name(k): v for k, v in asset.get("alias_gains", {}).items()}
        gain = _positive(gains.get(alias, gains.get(canonical, 1.0)), "alias gain")
        path = (self.nitrofs_root / asset["file"]).resolve()
        if not path.is_relative_to(self.nitrofs_root):
            raise ValueError("Approved audio path escapes nitrofs")
        return asset, path, gain

    def _decode_ffmpeg(self, path, channels):
        command = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path)]
        if channels == 1:
            # Explicit unity duplication keeps the approved mono level intact.
            command += ["-af", "pan=stereo|c0=c0|c1=c0"]
        command += ["-f", "f32le", "-ar", str(RATE), "-ac", "2", "pipe:1"]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip()
                               or f"FFmpeg exited {result.returncode}")
        if len(result.stdout) % 8:
            raise ValueError("FFmpeg returned incomplete stereo float frames")
        return np.frombuffer(result.stdout, dtype="<f4").reshape(-1, 2)

    def _prepare_clip(self, asset, path):
        # A prepared voice must not wait for a worker decoding another asset.
        with self._lock:
            if self._closed:
                raise RuntimeError("Audio transport is closed")
            if path in self._cache:
                return self._cache[path]
        with self._decode_lock:
            with self._lock:
                if path in self._cache:
                    return self._cache[path]
                if self._closed:
                    raise RuntimeError("Audio transport is closed")
            if int(asset.get("rate", RATE)) != RATE:
                raise ValueError(f"Approved asset rate must be {RATE}: {path.name}")
            channels = int(asset["channels"])
            pcm = np.asarray(self._decoder(path) if self._decoder else
                             self._decode_ffmpeg(path, channels))
            if pcm.ndim == 1:
                pcm = pcm[:, None]
            if pcm.ndim != 2 or pcm.shape[1] not in (1, 2):
                raise ValueError("Decoded PCM must have one or two channels")
            if channels == 2 and pcm.shape[1] != 2:
                raise ValueError("Approved stereo asset decoded as mono")
            if pcm.dtype == np.int16:
                pcm = pcm.astype(np.float32) / 32768.0
            elif np.issubdtype(pcm.dtype, np.floating):
                pcm = np.array(pcm, dtype=np.float32, copy=True)
            else:
                raise ValueError("Decoded PCM must be float or int16")
            if channels == 1:
                pcm = np.repeat(pcm[:, :1], 2, axis=1)
            elif channels != 2:
                raise ValueError("Approved asset must be mono or stereo")
            frames = int(asset["frames"])
            if frames <= 0 or len(pcm) < frames:
                raise ValueError(f"Decoded {len(pcm)} frames; approved asset needs {frames}")
            pcm = np.ascontiguousarray(pcm[:frames])
            if not np.isfinite(pcm).all():
                raise ValueError("Decoded PCM contains nonfinite samples")
            start = int(asset.get("loop_start_frame", 0))
            end = int(asset.get("loop_end_frame", frames))
            if asset["group"] == "music" and not 0 <= start < end <= frames:
                raise ValueError(f"Invalid approved loop [{start}, {end}) for {frames} frames")
            pcm.flags.writeable = False
            clip = _Clip(pcm, start, end)
            with self._lock:
                if self._closed:
                    raise RuntimeError("Audio transport closed during preparation")
                self._cache[path] = clip
            return clip

    def prepare(self, names: Iterable[tuple[str, str]]) -> bool:
        """Synchronously decode only these assets; failures appear in status."""
        success = True
        for group, name in names:
            try:
                asset, path, _ = self._resolve(group, name)
                self._prepare_clip(asset, path)
            except Exception as exc:
                self._error(f"Prepare {group}/{name}: {exc}")
                success = False
        return success

    def _advance(self, frames, out=None):
        if self._paused or self._closed:
            return
        for group, voice in list(self._voices.items()):
            if not voice.consume(frames, out):
                del self._voices[group]

    def _sync_fallback(self):
        if self._stream_failed:
            now = time.monotonic()
            frames = int(max(0.0, now - self._clock_anchor) * RATE)
            if self._paused:
                self._clock_anchor = now
            elif frames:
                self._advance(frames)
                self._clock_anchor += frames / RATE

    def _ensure_stream(self):
        if not self._start_stream:
            return
        with self._stream_lock:
            with self._lock:
                if self._stream is not None or self._stream_failed or self._closed:
                    return
            stream = None
            try:
                factory = self._stream_factory
                if factory is None:
                    import sounddevice
                    factory = sounddevice.OutputStream
                stream = factory(samplerate=RATE, channels=2, dtype="float32",
                                 blocksize=512, latency=self._requested_latency,
                                 callback=self._callback)
                stream.start()
                with self._lock:
                    self._stream = stream
                    self._stream_latency_seconds = getattr(stream, "latency", None)
                    self._stream_active = True
            except Exception as exc:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                with self._lock:
                    self._stream_failed = True
                    self._clock_anchor = time.monotonic()
                self._error(f"Audio output device: {exc}")

    def play(self, group, name, volume=1.0, fadein=0, offset_seconds=0) -> bool:
        """Replace a voice at elapsed time, playing intro once then its loop.

        ``offset_seconds`` is total elapsed playback, including the intro and
        any completed loops. It also advances fade-in; an expired SFX stays
        silent instead of looping. Existing calls default to sample zero.
        """
        try:
            volume = _positive(volume, "volume")
            fade_frames = round(_positive(fadein, "fadein") * RATE)
            offset_frames = round(_positive(offset_seconds, "offset_seconds") * RATE)
            asset, path, alias_gain = self._resolve(group, name)
            clip = self._prepare_clip(asset, path)
            with self._lock:
                if self._closed:
                    raise RuntimeError("Audio transport is closed")
                self._sync_fallback()
                if group == "sfx" and offset_frames >= len(clip.pcm):
                    self._voices.pop(group, None)
                    return True
                voice = _Voice(clip, group, str(name), asset["source"], volume, alias_gain)
                voice.position = offset_frames
                if group == "music" and voice.position >= clip.loop_end:
                    voice.position = clip.loop_start + ((voice.position - clip.loop_end)
                                     % (clip.loop_end - clip.loop_start))
                if fade_frames:
                    voice.fade_start = voice.fade_target = 0.0
                    voice.fade(1.0, fade_frames)
                    voice.fade_elapsed = min(fade_frames, offset_frames)
                self._voices[group] = voice
            self._ensure_stream()
            return True
        except Exception as exc:
            self._error(f"Play {group}/{name}: {exc}")
            return False

    def stop(self, group, fadeout=0) -> bool:
        try:
            if group not in GROUPS:
                raise ValueError(f"Unknown audio group: {group!r}")
            frames = round(_positive(fadeout, "fadeout") * RATE)
            with self._lock:
                self._sync_fallback()
                voice = self._voices.get(group)
                if voice is not None and frames:
                    voice.fade(0.0, frames)
                    voice.stopping = True
                else:
                    self._voices.pop(group, None)
            return True
        except Exception as exc:
            self._error(f"Stop {group}: {exc}")
            return False

    @property
    def enabled(self):
        with self._lock:
            return self._enabled

    @enabled.setter
    def enabled(self, value):
        with self._lock:
            self._sync_fallback()
            self._enabled = bool(value)

    def pause(self):
        with self._lock:
            self._sync_fallback()
            self._paused = True

    def resume(self):
        with self._lock:
            self._clock_anchor = time.monotonic()
            self._paused = False

    def reset(self):
        with self._lock:
            self._voices.clear()
            self._paused = False
            self._clock_anchor = time.monotonic()

    def close(self):
        # PortAudio stop may wait for its callback: never hold the mixer lock.
        with self._stream_lock:
            with self._lock:
                self._closed = True
                self._voices.clear()
                self._cache.clear()
                stream, self._stream = self._stream, None
                self._stream_latency_seconds = None
                self._stream_active = False
            if stream is not None:
                try:
                    stream.stop()
                except Exception as exc:
                    self._error(f"Audio output stop: {exc}")
                try:
                    stream.close()
                except Exception as exc:
                    self._error(f"Audio output close: {exc}")

    def render(self, frames, dtype="float32"):
        """Return clipped stereo PCM and advance transport by exact frames."""
        if not isinstance(frames, (int, np.integer)) or frames < 0:
            raise ValueError("frames must be a nonnegative integer")
        if np.dtype(dtype) not in (np.dtype("float32"), np.dtype("int16")):
            raise ValueError("Output dtype must be float32 or int16")
        out = np.zeros((frames, 2), dtype=np.float32)
        with self._lock:
            self._advance(frames, out)
            if self._enabled:
                out *= self.master_gain
            else:
                out.fill(0)
        np.clip(out, -1.0, 1.0, out=out)
        if np.dtype(dtype) == np.dtype("int16"):
            return np.clip(np.rint(out * 32768.0), -32768, 32767).astype(np.int16)
        return out

    def _callback(self, outdata, frames, time_info, status):
        try:
            outdata[:] = self.render(frames)
            with self._lock:
                self._callback_info["count"] += 1
                self._callback_info["frames"] = frames
                self._callback_info["total_frames"] += frames
                self._callback_info["status"] = str(status) if status else ""
                self._callback_info["current_time"] = getattr(time_info, "currentTime", None)
                self._callback_info["output_buffer_dac_time"] = getattr(
                    time_info, "outputBufferDacTime", None)
            if status:
                self._error(f"Audio callback: {status}")
        except Exception as exc:
            outdata.fill(0)
            self._error(f"Audio callback failed: {exc}")

    @property
    def status(self):
        with self._lock:
            self._sync_fallback()
            return {
                "enabled": self._enabled, "paused": self._paused, "closed": self._closed,
                "rate": RATE, "channels": 2, "master_gain": self.master_gain,
                "stream_active": self._stream_active, "clock_fallback": self._stream_failed,
                "requested_latency": self._requested_latency,
                "stream_latency_seconds": self._stream_latency_seconds,
                "position_clock": "mixed",
                "cache_count": len(self._cache),
                "errors": list(self._errors), "callback": dict(self._callback_info),
                "active": {group: {
                    "name": voice.name, "source": voice.source, "position": voice.position,
                    "seconds": voice.position / RATE, "volume": voice.volume,
                    "alias_gain": voice.alias_gain, "fade_gain": voice.gain,
                    "loop_start": voice.clip.loop_start, "loop_end": voice.clip.loop_end,
                    "stopping": voice.stopping,
                } for group, voice in self._voices.items()},
            }
