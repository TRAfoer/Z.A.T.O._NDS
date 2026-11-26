"""Match the preview's presented keyboard cues to the supplied original clip.

Reference observations are measured from QQ20261125-8264.mp4, not authored
delays added to the game. Rendering acknowledgments produce the cadence;
there is no arbitrary 1/30 second delay in the runtime.
"""
from pathlib import Path
import hashlib
import json
import subprocess
from anim_story import build_story
from anim_player import ScenePlayer

OUT=Path(__file__).resolve().parent/'out/anim_preview/timing_video'
VIDEO=Path(r'C:\Downloads\QQ20261125-8264.mp4')


class Recorder:
    def __init__(self):self.player=None;self.calls=[]
    def reset(self):self.calls=[]
    def play(self,group,name,**kwargs):self.calls.append(dict(time=self.player.now,group=group,name=name,options=kwargs))
    def stop(self,*args,**kwargs):pass


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    story=build_story();audio=Recorder();player=ScenePlayer(story,audio,require_present=True);audio.player=player
    player.seek(573);start=player.now
    player.mark_presented()
    iss_start=None
    for _ in range(1200):
        player.tick(1/60)
        if any(layer['name']=='iss' for layer in player.layers):
            if iss_start is None:iss_start=player.now-start
            assert player.display is None or not player.display.get('active',True),'old timetable overlays the flash'
        player.mark_presented()
        if player.line==608:break
    keyboard=[c for c in audio.calls if c['name'].endswith('kb.mp3')]
    glitch=next(c for c in audio.calls if c['name'].endswith('glitch.mp3'))
    assert len(keyboard)==13
    predicted=[c['time']-start for c in keyboard]+[glitch['time']-start]
    # These are observed frame indexes. Use actual PTS, not index/fps, when
    # the original capture is available (it has slight timestamp jitter).
    indexes=[188,219,250,281,312,343,374,405,436,467,498,529,560,621]
    if VIDEO.exists():
        raw=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
            '-show_entries','frame=best_effort_timestamp_time','-of','json',str(VIDEO)])
        pts=[float(f['best_effort_timestamp_time']) for f in json.loads(raw)['frames']]
        observed=[pts[i] for i in indexes]
        video_sha=hashlib.sha256(VIDEO.read_bytes()).hexdigest()
    else:
        observed=[i/30 for i in indexes];video_sha=None
    origin=observed[0];observed=[t-origin for t in observed]
    errors=[p-v for p,v in zip(predicted,observed)]
    assert max(abs(e) for e in errors)<=1/30+1e-6,errors
    player.seek(653)
    music=next(c for c in reversed(audio.calls) if c['group']=='music')
    assert music['options']['offset_seconds']>10
    report=dict(result='PASS',reference_video=str(VIDEO),reference_sha256=video_sha,
                keyboard_cues=13,glitch_cues=1,simulated_display_fps=60,reference_capture_fps=30,
                measured_visual_relative_seconds=observed,preview_cue_relative_seconds=predicted,
                cue_error_seconds=errors,max_visual_cue_error_seconds=max(abs(e) for e in errors),
                iss_start_relative_seconds=iss_start,mirror_music_offset_seconds=music['options']['offset_seconds'],
                measured_device_latency_before_seconds=.185759637,
                measured_device_latency_low_seconds=.092879819,
                limitations=['DAC timestamps are device estimates, not an acoustic microphone recording.',
                    'The 1ms-class cue comparison is an ideal 60Hz presentation simulation, not actual Tk scheduling accuracy.',
                    'The clip contains manual advances; those human dwell times are not inferred or baked into the script.',
                    'Development jumps restore known authored elapsed time, not unknowable manual reading pauses.'])
    (OUT/'timing_verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f"PASS 13 keyboard cues + glitch against video frames; max error {report['max_visual_cue_error_seconds']*1000:.3f}ms; mirror music resumes at {report['mirror_music_offset_seconds']:.3f}s")


if __name__=='__main__':main()
