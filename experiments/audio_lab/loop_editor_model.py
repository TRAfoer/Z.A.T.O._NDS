"""Manual markers and safe draft persistence; no conversion/build side effects."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
RATE=22050
DEFAULT_PROJECT=Path(__file__).resolve().parent/'out/manual_music_edits.json'
TIMING=('intro_end_frame','source_loop_start_frame','source_loop_end_frame','crossfade_frames')


def recent_project(preferred=DEFAULT_PROJECT,initial=DEFAULT_PROJECT.with_name('manual_editor_smoke.json')):
    candidates=[p for p in (Path(preferred),Path(initial)) if p.is_file()]
    return max(candidates,key=lambda p:p.stat().st_mtime_ns) if candidates else Path(preferred)


def parse_time(text):
    try:
        parts=[Decimal(p) for p in str(text).strip().split(':')]
        if not 1<=len(parts)<=3 or any(not p.is_finite() or p<0 for p in parts):raise ValueError()
        if len(parts)>1 and any(p>=60 for p in parts[1:]):raise ValueError()
        total=Decimal(0)
        for p in parts:total=total*60+p
        return int((total*RATE).quantize(Decimal(1),rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise ValueError('请输入非负秒数或 分:秒.毫秒，例如 01:02.500') from None


def format_time(frame):
    millis=round(frame*1000/RATE)
    return f'{millis//60000:02d}:{millis//1000%60:02d}.{millis%1000:03d}'


def validate_state(state,frames):
    if any(type(state[k]) is not int for k in TIMING):raise ValueError('标记须为整数采样帧')
    intro,start,end,fade=(state[k] for k in TIMING)
    if intro!=start:raise ValueError('前奏结束与循环开始使用同一个 S 标记')
    if not 0<=start<end<=frames:raise ValueError('循环开始须早于结束，且都在原曲范围内')
    if not 0<=intro<=frames:raise ValueError('前奏结束超出原曲范围')
    if not 0<=fade<end-start:raise ValueError('淡化长度必须短于循环段')
    if intro and intro<=fade:raise ValueError('前奏须长于淡化；不需要前奏时设为 0')
    return None if state.get('keep_full') else {k:state[k] for k in TIMING}


def load_sources():
    full=ROOT/'nds/artifacts/audio_manifest_full_length.json'
    path=full if full.exists() else ROOT/'nds/artifacts/audio_manifest.json'
    manifest=json.loads(path.read_text(encoding='utf-8'))
    import sys
    sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
    from asset_model import DEFAULT_GAME
    analysis_path=Path(__file__).resolve().parent/'out/music_loops/analysis.json'
    analysis={}
    if analysis_path.exists():analysis={r['source']:r for r in json.loads(analysis_path.read_text(encoding='utf-8'))['songs']}
    sources={}
    states={}
    for row in manifest['assets']:
        frames=row.get('original_frames',row['frames'])
        if row['group']!='music' or frames/RATE<=60:continue
        name=row['source'];source=DEFAULT_GAME/'audio'/name
        sha=hashlib.sha256(source.read_bytes()).hexdigest()
        if sha!=row['source_sha256']:raise ValueError(f'原音频已变化，请先更新源清单：{name}')
        auto=analysis.get(name,{})
        sources[name]=dict(path=str(source),frames=frames,source_sha256=sha,
            bpm=auto.get('estimated_bpm',0),beats=auto.get('beat_times_seconds',[]))
        state=dict(intro_end_frame=0,source_loop_start_frame=0,source_loop_end_frame=frames,
            crossfade_frames=441,keep_full=True,reviewed=False,note='')
        if auto.get('edited'):
            start=auto['edit']['intro_end_frame']
            beats=sources[name]['beats']
            end=round(min(beats,key=lambda t:abs(t-60))*RATE) if beats else min(frames,60*RATE)
            state.update(intro_end_frame=start,source_loop_start_frame=start,source_loop_end_frame=end,keep_full=False)
        states[name]=state
    return sources,states


def save_project(path,tracks,sources):
    path=Path(path)
    if set(tracks)!=set(sources):raise ValueError('保存的曲目列表与源列表不一致')
    stored={};edits={}
    for name,state in tracks.items():
        try:edit=validate_state(state,sources[name]['frames'])
        except ValueError as e:raise ValueError(f'{name}：{e}') from e
        stored[name]=dict(state,source_sha256=sources[name]['source_sha256'])
        if edit is not None:edits[name]=dict(edit,source_sha256=sources[name]['source_sha256'])
    payload=dict(editor='ZATO manual music editor',version=1,rate=RATE,status='pending_confirmation',marker_mode='intro_end_equals_loop_start',
        saved_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),tracks=stored,edits=edits)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile('w',dir=path.parent,prefix=path.name+'.',suffix='.tmp',
            encoding='utf-8',delete=False) as f:
            temporary=Path(f.name);json.dump(payload,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        if path.exists():shutil.copyfile(path,path.with_suffix(path.suffix+'.bak'))
        os.replace(temporary,path)
    finally:
        if temporary and temporary.exists():temporary.unlink()
    return payload


def load_project(path,sources):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    if payload.get('version')!=1 or payload.get('rate')!=RATE:raise ValueError('不支持此编辑工程版本')
    if set(payload['tracks'])!=set(sources):raise ValueError('曲目列表已改变，请保留旧工程并另存新工程')
    tracks={}
    for name,stored in payload['tracks'].items():
        state=dict(stored);sha=state.pop('source_sha256')
        if sha!=sources[name]['source_sha256']:raise ValueError(f'源音频与保存时不一致：{name}')
        validate_state(state,sources[name]['frames']);tracks[name]=state
    return tracks


def waveform_envelope(pcm,start,end,bins):
    section=pcm[max(0,int(start)):min(len(pcm),int(end))]
    if not len(section):return np.zeros((1,2)),np.zeros((1,2))
    bins=max(1,min(int(bins),len(section)))
    edges=np.linspace(0,len(section),bins+1,dtype=int)[:-1]
    return np.minimum.reduceat(section,edges,axis=0),np.maximum.reduceat(section,edges,axis=0)
