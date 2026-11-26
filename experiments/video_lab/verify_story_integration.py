"""Read-only immutable AV ROM smoke: pause, skip while paused, restored mainline."""
from pathlib import Path
import json
import re
import struct
import sys
from experiments.video_lab import convert_op as op


def main():
    root=op.ROOT;rom=root/'nds/zato_story_chain_av_nitrofs.nds';out=op.DEFAULT_OUTPUT/'story_integration';out.mkdir(exist_ok=True)
    assert 0<rom.stat().st_size<64_000_000,'Strict 64MB gate failed'
    tools=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools');sys.path.insert(0,str(tools))
    from nds_emulator import Emulator
    mapping=(root/'nds/build/zato_story_chain_av_nitrofs.map').read_text()
    def symbol(name):return int(re.search(r'0x([0-9a-fA-F]+)\s+'+name+r'\s',mapping)[1],16)-0x02000000
    header=(root/'nds/source/story_runtime.h').read_text();body=re.search(r'typedef\s+struct\s*\{(.*?)\}\s*StoryStatus',header,re.S)[1]
    story_fields=[]
    for group in re.findall(r'uint32_t\s+([^;]+);',body):story_fields.extend(v.strip() for v in group.split(','))
    video_fields='magic active ready frame decoded displayed total_frames fps colors audio_frame paused seeking finished errors late_frames max_late_samples bytes reads commits source_hash requested_seek'.split()
    audio_fields='magic initialized rate music_count sfx_count music_index sfx_index music_playing sfx_playing music_starts sfx_starts callbacks music_underruns sfx_underruns errors music_frames sfx_frames music_loops sfx_finishes ui_version nonzero_samples output_peak'.split()
    tables=[('story',story_fields,symbol('g_story_status')),('video',video_fields,symbol('g_story_video_status')),('audio',audio_fields,symbol('g_audio_status'))]
    emu=Emulator(tools/'emulator/desmume_libretro.dll',rom)
    def state():
        data=emu.memory();return {name:dict(zip(fields,struct.unpack_from(f'<{len(fields)}I',data,at))) for name,fields,at in tables}
    try:
        for _ in range(500):
            emu.run(1);s=state()
            if s['video']['active'] and s['video']['audio_frame']>=22050:break
        assert s['video']['active'] and not s['story']['ready'],s
        opening=s;emu.press('L',held=5,settle=15);paused=state();emu.run(60);held=state()
        assert paused['video']['paused'] and held['video']['audio_frame']==paused['video']['audio_frame'],(paused,held)
        emu.press('START',held=5,settle=5)
        for _ in range(500):
            emu.run(1);s=state()
            if s['story']['ready']:break
        assert s['story']['ready'] and not s['video']['active'] and not s['story']['paused'],s
        handoff=s;emu.frame.save(out/'mainline_after_skip.png');emu.run(360);s=state();emu.frame.save(out/'mainline_after_6s.png')
        assert not s['story']['errors'] and not s['audio']['errors'] and not s['audio']['music_underruns'] and not s['audio']['sfx_underruns'],s
        result=dict(result='PASS',rom=str(rom),rom_bytes=rom.stat().st_size,rom_sha256=op.sha256(rom),remaining_bytes=64_000_000-rom.stat().st_size,
            method='Actual integrated immutable ARM9 ROM. Automatic OP keeps StoryStatus.ready zero; L pauses; START skips while paused; normal story resumes unpaused with rebuilt Bmp16 screens. Six seconds of ordinary runtime then checked.',
            opening=opening,paused=paused,paused_60_vblanks=held,handoff=handoff,mainline_after_360_vblanks=s)
        (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k in ('result','rom','rom_bytes','rom_sha256','remaining_bytes','method')},indent=2))
    finally:emu.close()


if __name__=='__main__':main()
