"""Compare complete decoded waveforms, including stereo, before ADPCM encoding."""
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
import subprocess
import numpy as np


def waveform_match(original, canonical, rate):
    """Recognize codec copies with constant gain; reject edits/stereo changes."""
    a = np.asarray(original, dtype=np.float64)
    b = np.asarray(canonical, dtype=np.float64)
    if a.ndim == 1: a = a[:, None]
    if b.ndim == 1: b = b[:, None]
    if a.shape[1] != b.shape[1] or not len(a) or not len(b): return None
    if abs(len(a) - len(b)) > max(2, int(rate * .002)): return None
    length = min(len(a), len(b))
    a, b = a[:length], b[:length]
    ea, eb = float(np.sum(a * a)), float(np.sum(b * b))
    if not ea or not eb:
        return dict(gain=1., min_correlation=1., relative_error=0., max_window_error=0.) if not ea and not eb else None
    gain = float(np.sum(a * b) / eb)
    if not .25 <= gain <= 4: return None
    correlations = []
    for ch in range(a.shape[1]):
        x, y = a[:, ch], b[:, ch]
        ex, ey = float(np.sum(x*x)), float(np.sum(y*y))
        if not ex and not ey: continue
        if not ex or not ey: return None
        corr = float(np.sum(x*y) / np.sqrt(ex*ey))
        if corr < .998: return None
        correlations.append(corr)
    residual = a - gain * b
    error = float(np.sqrt(np.sum(residual * residual) / ea))
    if error > .065: return None
    rms = np.sqrt(ea / a.size)
    max_window_error = 0.
    # Check the whole clip, rather than trusting matching introductions.
    for at in range(0, length, rate):
        x, delta = a[at:at+rate], residual[at:at+rate]
        energy = float(np.sum(x*x))
        if np.sqrt(energy / x.size) < rms * .03: continue
        local_error = float(np.sqrt(np.sum(delta*delta) / energy))
        if local_error > .2: return None
        max_window_error = max(max_window_error, local_error)
    return dict(gain=gain, min_correlation=min(correlations, default=1.),
                relative_error=error, max_window_error=max_window_error)


def decode(source, rate, channels):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(source),
        '-vn', '-ar', str(rate), '-ac', str(channels), '-f', 's16le', '-c:a', 'pcm_s16le', '-'])
    assert raw and len(raw) % (channels * 2) == 0
    return np.frombuffer(raw, dtype='<i2').reshape(-1, channels).astype(np.float64) / 32768


def deduplicate(audio_dir, names):
    # Prefer the OGG original when multiple codecs carry the same clip.
    names = sorted(names, key=lambda n: (not n.lower().endswith('.ogg'), n))
    def analyze(name):
        path = audio_dir / name
        pcm = decode(path, 8000, 1)
        return name, pcm, hashlib.sha256(path.read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=4) as pool:
        analyzed = list(pool.map(analyze, names))
    mono = {name: pcm for name, pcm, _ in analyzed}
    hashes = {name: sha for name, _, sha in analyzed}
    @lru_cache(maxsize=4)
    def stereo(name): return decode(audio_dir / name, 22050, 2)
    canonical_names, aliases, matches = [], {}, []
    for name in names:
        for canonical in canonical_names:
            probe = waveform_match(mono[name], mono[canonical], 8000)
            if probe is None: continue
            match = waveform_match(stereo(name), stereo(canonical), 22050)
            if match is None: continue
            aliases[name] = canonical
            matches.append(dict(source=name, canonical=canonical,
                source_sha256=hashes[name], canonical_sha256=hashes[canonical],
                byte_identical=hashes[name] == hashes[canonical], **match))
            break
        else:
            aliases[name] = name
            canonical_names.append(name)
    return aliases, dict(method='8 kHz mono full-waveform screening; 22050 Hz stereo per-channel and whole-duration verification; constant gain recorded',
        referenced_sources=len(names), unique_sources=len(canonical_names),
        source_hashes=hashes, duplicates=matches)
