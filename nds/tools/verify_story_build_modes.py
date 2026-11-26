"""Real isolated OP -> no OP -> OP rebuilds, object/FAT/boot checks and guards."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT.parent
VARIANT='buildmode_probe'
NAME=f'zato_story_chain_{VARIANT}_nitrofs'
MOVIES={'movie/op.zpv','movie/op.wav'}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_video_references(nm_output):
    result=[]
    for line in nm_output.splitlines():
        words=line.split()
        if len(words)>=2 and words[-2]=='U' and (words[-1].startswith('story_video_') or words[-1]=='g_story_video_status'):result.append(words[-1])
    return sorted(result)


def check_movie_files(payloads,enabled):
    actual=MOVIES.intersection(payloads)
    assert actual==(MOVIES if enabled else set()),f'Incorrect actual FAT movie files: {sorted(actual)}; OP={enabled}'
    return sorted(actual)


def symbol_offset(mapping,name,required=True):
    values=[int(value,16) for value in re.findall(r'0x([0-9a-fA-F]+)\s+'+re.escape(name)+r'\s',mapping)]
    values=[value-0x02000000 for value in values if 0x02000000<=value<0x02400000]
    if not values:
        if not required:return None
        raise ValueError(f'No main-RAM symbol {name}')
    return values[0]


def boot_probe(rom,mapping,enabled,output):
    tools=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools');sys.path.insert(0,str(tools))
    from nds_emulator import Emulator
    text=Path(mapping).read_text();so=symbol_offset(text,'g_story_status');vo=symbol_offset(text,'g_story_video_status',enabled)
    body=re.search(r'typedef\s+struct\s*\{(.*?)\}\s*StoryStatus',(ROOT/'source/story_runtime.h').read_text(),re.S)[1]
    fields=[]
    for group in re.findall(r'uint32_t\s+([^;]+);',body):fields.extend(value.strip() for value in group.split(','))
    video_fields='magic active ready frame decoded displayed total_frames fps colors audio_frame paused seeking finished errors late_frames max_late_samples bytes reads commits source_hash requested_seek'.split()
    emu=Emulator(tools/'emulator/desmume_libretro.dll',Path(rom))
    try:
        for _ in range(600):
            emu.run(1);memory=emu.memory();story=dict(zip(fields,struct.unpack_from(f'<{len(fields)}I',memory,so)))
            video=dict(zip(video_fields,struct.unpack_from('<21I',memory,vo))) if vo is not None else None
            if enabled and video['active'] and video['audio_frame']>0:break
            if not enabled and story['ready']:break
        assert not story['errors'],story
        if enabled:assert video['active'] and video['audio_frame']>0 and not video['errors'] and not story['ready'],(story,video)
        else:assert story['ready'] and (video is None or not video['active']),(story,video)
        screenshot=Path(output).with_suffix('.png');emu.frame.save(screenshot)
        report=dict(result='PASS',entry='opening_movie' if enabled else 'ordinary_story',emulator_frames=emu.frame_count,story=story,video=video,screenshot=str(screenshot))
        Path(output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    finally:emu.close()


def protected_files():
    paths=[]
    for pattern in ('zato_story_chain_av_nitrofs.nds','zato_story_chain_nitrofs.nds','zato_ep2d3_scene_nitrofs.nds',
                    'build/zato_story_chain_av_nitrofs.map','build/zato_story_chain_av_nitrofs.elf',
                    'build/zato_story_chain_nitrofs.map','build/zato_story_chain_nitrofs.elf',
                    'artifacts/audio_manifest.json','config/audio_edits.json'):
        path=ROOT/pattern
        if path.exists():paths.append(path)
    paths.extend(path for path in WORKSPACE.glob('*.nds') if path.is_file())
    paths.extend(path for path in (ROOT/'nitrofs/story').glob('*') if path.is_file())
    paths.extend(path for path in (ROOT/'nitrofs/audio').rglob('*') if path.is_file())
    return sorted(set(paths))


def snapshot(paths):return {str(path):dict(bytes=path.stat().st_size,sha256=digest(path)) for path in paths}


def verify(output):
    sys.path.insert(0,str(ROOT/'tools'))
    from verify_scene_package import files
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    # The target is intentionally fixed; neither a CLI argument nor an inferred
    # current output can redirect this test to an AV/base/delivery binary.
    rom=ROOT/f'{NAME}.nds';mapping=ROOT/'build'/f'{NAME}.map';obj=ROOT/'build'/NAME/'source/story_runtime.c.o'
    manifest=json.loads((WORKSPACE/'experiments/video_lab/out/movie_manifest.json').read_text())
    before=snapshot(protected_files());phases=[];baseline=None
    env=os.environ.copy();env['PATH']='C:\\msys64\\opt\\wonderful\\bin;C:\\msys64\\ucrt64\\bin;C:\\msys64\\usr\\bin;'+env.get('PATH','')
    nm=Path('C:/msys64/opt/wonderful/toolchain/gcc-arm-none-eabi/bin/arm-none-eabi-nm.exe')
    try:
        for phase,enabled in (('01_op',True),('02_skip',False),('03_op',True)):
            print(f'Building {phase} with fixed Variant={VARIANT}',flush=True)
            command=['pwsh','-NoProfile','-File',str(ROOT/'build_story.ps1'),'-Variant',VARIANT,'-NoCopy','-UseExistingPacks']
            if not enabled:command.append('-SkipVideo')
            result=subprocess.run(command,cwd=WORKSPACE,capture_output=True,text=True,encoding='utf-8',errors='replace')
            (output/f'{phase}_build.stdout.log').write_text(result.stdout,encoding='utf-8')
            (output/f'{phase}_build.stderr.log').write_text(result.stderr,encoding='utf-8')
            assert result.returncode==0,f'{phase} build exit {result.returncode}; see build logs'
            nm_output=subprocess.check_output([str(nm),'-u',str(obj)],env=env,text=True)
            (output/f'{phase}_runtime.nm.txt').write_text(nm_output,encoding='utf-8')
            references=runtime_video_references(nm_output);assert bool(references)==enabled,references
            payloads=files(rom);movies=check_movie_files(payloads,enabled)
            other={name:hashlib.sha256(data).hexdigest() for name,data in payloads.items() if name not in MOVIES}
            if baseline is None:baseline=other
            assert other==baseline,'Nonmovie embedded payload changed between build modes'
            for name in movies:assert payloads[name]==Path(manifest['embedded_files'][name]).read_bytes(),name
            boot_path=output/f'{phase}_boot.json'
            boot=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--boot-child','--rom',str(rom),'--map',str(mapping),'--mode','on' if enabled else 'off','--boot-output',str(boot_path)],capture_output=True,text=True,encoding='utf-8',errors='replace')
            (output/f'{phase}_boot.stdout.log').write_text(boot.stdout,encoding='utf-8');(output/f'{phase}_boot.stderr.log').write_text(boot.stderr,encoding='utf-8')
            assert boot.returncode==0,f'{phase} real emulator boot failed; see logs'
            record=dict(phase=phase,op=enabled,rom=str(rom),rom_bytes=rom.stat().st_size,rom_sha256=digest(rom),map_sha256=digest(mapping),runtime_object_sha256=digest(obj),
                        runtime_movie_references=references,fat_file_count=len(payloads),fat_movie_files=movies,nonmovie_payloads_sha256=other,
                        boot=json.loads(boot_path.read_text()),build_stdout=str(output/f'{phase}_build.stdout.log'),build_stderr=str(output/f'{phase}_build.stderr.log'))
            phases.append(record);(output/f'{phase}.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
            assert snapshot([Path(path) for path in before])==before,'Protected source/delivery binary changed'
            print(f"PASS {phase}: entry={record['boot']['entry']}, movie files={movies}, ROM={record['rom_bytes']} bytes",flush=True)
    finally:
        # Always restore only the two named staging files, including after a
        # failed no-OP build. The canonical payloads stay read-only.
        stage=(ROOT/'storyfs/movie').resolve();assert stage.is_relative_to((ROOT/'storyfs').resolve());stage.mkdir(parents=True,exist_ok=True)
        for name in sorted(MOVIES):shutil.copyfile(manifest['embedded_files'][name],ROOT/'storyfs'/name)
    after=snapshot([Path(path) for path in before]);assert before==after,'Protected files changed'
    restored={name:dict(bytes=(ROOT/'storyfs'/name).stat().st_size,sha256=digest(ROOT/'storyfs'/name)) for name in sorted(MOVIES)}
    report=dict(result='PASS',variant=VARIANT,sequence='OP1 -> SkipVideo -> OP1',phases=phases,protected_files_unchanged=before,restored_op_staging=restored,
                strict_budget=False,note='Development sizes are reported after completed builds. Only an explicit -StrictBudget enables the strict size gate.')
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'artifacts/story_build_modes')
    parser.add_argument('--boot-child',action='store_true');parser.add_argument('--rom',type=Path);parser.add_argument('--map',type=Path);parser.add_argument('--mode',choices=('on','off'));parser.add_argument('--boot-output',type=Path);args=parser.parse_args()
    if args.boot_child:boot_probe(args.rom,args.map,args.mode=='on',args.boot_output)
    else:
        report=verify(args.output);print(json.dumps(dict(result=report['result'],variant=report['variant'],phases=[dict(phase=row['phase'],bytes=row['rom_bytes'],sha256=row['rom_sha256'],entry=row['boot']['entry']) for row in report['phases']]),indent=2))


if __name__=='__main__':main()
