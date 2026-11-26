"""Read-only beat analysis and candidate intro+loop edits for the NDS trial.

Run using .venv/Scripts/python.exe. Beat/bar phase is heuristic, not an authored
score; low-confidence decisions are labeled for audition.
"""
from pathlib import Path
import csv
import hashlib
import json
import subprocess
import sys
import wave
import librosa
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'nds/tools'))
from pack_audio import DEFAULT_GAME
from music_edit import render_edit

SR, HOP, TARGET = 11025, 256, 60.
OUT = Path(__file__).resolve().parent / 'out/music_loops'


def read_pcm(path, rate=SR, channels=1):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-vn',
        '-ar', str(rate), '-ac', str(channels), '-c:a', 'pcm_s16le', '-f', 's16le', '-'])
    return np.frombuffer(raw, dtype='<i2').reshape(-1, channels)


def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a)*np.linalg.norm(b)+1e-12))


def refine_tempo(tempo, beats):
    # Autocorrelation lag bins are coarse (~2.5 BPM near 120 at this hop).
    # Average over sixteen observed beat intervals for the reported tempo.
    if len(beats)>16:
        span=float(np.median(np.asarray(beats[16:])-np.asarray(beats[:-16])))
        if span>0:return np.array([60*16/span])
    return np.asarray(tempo).reshape(-1)


def choose_candidate(duration, beats, onset, chroma, rms):
    intervals = np.diff(beats)
    cv = float(np.std(intervals) / max(np.mean(intervals), 1e-9)) if len(intervals) else 1.
    lag = int(round(np.median(intervals) * SR / HOP)) if len(intervals) else 0
    centered = onset - np.mean(onset)
    periodicity = cosine(centered[:-lag], centered[lag:]) if 0 < lag < len(onset) else 0.
    stable = len(beats) > 20 and cv < .10 and periodicity > .18
    # Approximate 4/4 accents. This is explicitly NOT a downbeat classifier.
    beat_frames = np.clip(np.rint(beats*SR/HOP).astype(int), 0, len(onset)-1)
    accent = [float(np.mean(onset[beat_frames[p::4]])) if len(beat_frames[p::4]) else 0 for p in range(4)]
    phase = int(np.argmax(accent))
    intro_candidates = [float(beats[i]) for i in range(phase, len(beats), 4) if 4 <= beats[i] <= 10]
    intro = min(intro_candidates, key=lambda t: abs(t-6)) if intro_candidates else 6.
    chroma_global = np.mean(chroma, axis=1)
    energy_target = float(np.median(rms))
    candidates = []
    if stable:
        for i in range(phase, len(beats), 4):
            start = float(beats[i])
            if start < intro: continue
            # Whole four-bar groups under the tentative 4/4 interpretation.
            for count in range(16, 257, 16):
                if i+count >= len(beats): break
                end = float(beats[i+count])
                if not 52 <= intro+end-start <= 68: continue
                candidates.append((start, end, count))
    if not candidates:
        stable = False
        for start in np.arange(max(intro, .5), max(intro+.25, duration-54), .5):
            candidates.append((float(start), float(start+54), None))
    for start, end, count in candidates:
        a, b = int(start*SR/HOP), int(end*SR/HOP)
        if b <= a or b >= chroma.shape[1]: continue
        mean = np.mean(chroma[:, a:b], axis=1)
        represent = cosine(mean, chroma_global)
        width = max(1, int(.35*SR/HOP))
        seam = cosine(np.mean(chroma[:, max(0,a-width):a+1],axis=1),
                      np.mean(chroma[:, max(a,b-width):b+1],axis=1))
        energy = float(np.mean(rms[a:min(b,len(rms))]))
        energy_distance = abs(np.log((energy+1e-6)/(energy_target+1e-6)))
        boundary_energy = abs(np.log((float(rms[min(a,len(rms)-1)])+1e-6)/(float(rms[min(b,len(rms)-1)])+1e-6)))
        score = 3*represent + 2*seam - .5*energy_distance - .2*boundary_energy - .055*abs(intro+end-start-TARGET)
        yield dict(intro_seconds=intro, source_loop_start_seconds=start,
            source_loop_end_seconds=end, stored_seconds=intro+end-start,
            loop_seconds=end-start, beat_count=count, assumed_meter='4/4 (heuristic)' if stable else None,
            confidence='beat-grid candidate; audition required' if stable else 'weak beat: ambience seam candidate; audition required',
            beat_interval_cv=cv, onset_periodicity=periodicity, score=score,
            representativeness=represent, seam_chroma_similarity=seam)


def detect_features(y):
    spectrum = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    mel = librosa.feature.melspectrogram(S=spectrum**2, sr=SR, n_mels=64)
    onset = librosa.onset.onset_strength(S=librosa.power_to_db(mel), sr=SR, hop_length=HOP)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=SR, hop_length=HOP, units='time', trim=False)
    chroma = librosa.feature.chroma_stft(S=spectrum**2, sr=SR, n_fft=2048, hop_length=HOP)
    rms = librosa.feature.rms(S=spectrum, frame_length=2048, hop_length=HOP)[0]
    return refine_tempo(tempo, beats), beats, onset, chroma, rms


def analyze(row):
    source = DEFAULT_GAME / 'audio' / row['source']
    pcm = read_pcm(source)
    y = pcm[:, 0].astype(np.float32) / 32768
    tempo, beats, onset, chroma, rms = detect_features(y)
    result = dict(source=row['source'], source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_seconds=row.get('original_seconds', row['seconds']), estimated_bpm=float(np.asarray(tempo).ravel()[0]),
        beat_times_seconds=beats.tolist(), edited=False)
    if result['source_seconds'] <= 70:
        result['decision'] = 'Already about one minute; retain original whole-song loop'
        return result
    ranked = sorted(choose_candidate(result['source_seconds'], beats, onset, chroma, rms), key=lambda c:c['score'], reverse=True)
    if not ranked: raise ValueError(f'No candidate for {row["source"]}')
    chosen = ranked[0]
    edit = dict(intro_end_frame=round(chosen['intro_seconds']*22050),
        source_loop_start_frame=round(chosen['source_loop_start_seconds']*22050),
        source_loop_end_frame=round(chosen['source_loop_end_seconds']*22050), crossfade_frames=441)
    full = read_pcm(source, 22050, 2)
    edited, loop_start, loop_end = render_edit(full, edit)
    # Two wraps after the introduction are included in audition WAVs.
    preview = np.concatenate((edited, edited[loop_start:loop_end], edited[loop_start:loop_end]))
    rel = Path('previews') / (Path(row['source']).stem.replace(' ', '_') + '.wav')
    (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / rel), 'wb') as f:
        f.setnchannels(2); f.setsampwidth(2); f.setframerate(22050); f.writeframes(preview.tobytes())
    return result | dict(edited=True, decision=chosen, candidates=ranked[:5], edit=edit,
        preview=rel.as_posix(), output_frames=len(edited), loop_start_frame=loop_start, loop_end_frame=loop_end,
        edited_pcm_sha256=hashlib.sha256(edited.tobytes()).hexdigest())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / 'nds/artifacts/audio_manifest.json').read_text(encoding='utf-8'))
    songs = [r for r in manifest['assets'] if r['group']=='music' and r.get('original_seconds',r['seconds']) > 60]
    results = []
    cache = {}
    if (OUT/'analysis.json').is_file():
        cache = {r['source']:r for r in json.loads((OUT/'analysis.json').read_text(encoding='utf-8'))['songs']}
    for row in songs:
        prior = cache.get(row['source'])
        sha = hashlib.sha256((DEFAULT_GAME/'audio'/row['source']).read_bytes()).hexdigest()
        result = prior if prior and prior['source_sha256']==sha else analyze(row)
        result['estimated_bpm']=float(refine_tempo(result['estimated_bpm'],result['beat_times_seconds'])[0])
        results.append(result)
        print(f'{len(results)}/{len(songs)} {row["source"]}: BPM {result["estimated_bpm"]:.2f}; ' + (f'{result["decision"]["stored_seconds"]:.2f}s {result["decision"]["confidence"]}' if result['edited'] else 'keep complete'), flush=True)
        (OUT/'analysis.json').write_text(json.dumps(dict(library='librosa 0.11.0', sample_rate=SR, hop_length=HOP,
            limitation='Automatic musical candidates, not confirmed downbeats or human-auditioned loop quality', songs=results),ensure_ascii=False,indent=2),encoding='utf-8')
    edits = {r['source']:dict(source_sha256=r['source_sha256'], **r['edit']) for r in results if r['edited']}
    (OUT/'edits.json').write_text(json.dumps(dict(rate=22050, edits=edits),ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f); w.writerow(['source','BPM_estimate','original_seconds','stored_seconds','intro_seconds','loop_source_start','loop_source_end','confidence'])
        for r in results:
            c=r['decision'] if r['edited'] else {}
            w.writerow([r['source'],round(r['estimated_bpm'],3),r['source_seconds'],c.get('stored_seconds',r['source_seconds']),
                c.get('intro_seconds',0),c.get('source_loop_start_seconds',0),c.get('source_loop_end_seconds',r['source_seconds']),c.get('confidence','original loop retained')])
    print(f'PASS {len(results)} analyzed; {len(edits)} edits; previews contain intro once and loop three times')


if __name__ == '__main__': main()
