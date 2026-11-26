"""Independent stable-palette/tile-delta/LZ10 OP experiment.

The existing native lz10_decode in nds/source/asset_pack.c is linked unchanged.
Every two-second family has one nondithered RGB555 palette, with index zero
reserved for black. Each frame is a bounded full, XOR, changed-tile or skip-run
packet; independently decodable full frames bound all seeks to two seconds.
"""
from __future__ import annotations

import argparse
from collections import Counter
from io import BytesIO
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

import numpy as np
from PIL import Image,ImageDraw
from experiments.video_lab import convert_op as op

ROOT=op.ROOT
HEADER=struct.Struct('<8s20I')
ENTRY=struct.Struct('<9I')
FIELDS=('version width height screen_width screen_height x y fps frame_count duration_us colors bpp '
    'raw_bytes family_count index_entry_bytes index_offset palette_offset payload_offset file_bytes max_packed').split()
KEY,XOR,TILE,RAW,SKIP=1,2,4,8,16
MODE_NAMES={KEY:'key',XOR:'xor',TILE:'tile',SKIP:'skip'}
MAX_PACKET=65536
COLOR_COUNTS=(16,32,48,64,128,256)


def tile_bytes(pixels,colors):
    pixels=np.asarray(pixels,dtype=np.uint8)
    if pixels.shape!=(144,256) or colors not in COLOR_COUNTS or int(pixels.max())>=colors:
        raise ValueError('Invalid palette frame dimensions/colors')
    tiles=pixels.reshape(18,8,32,8).transpose(0,2,1,3).reshape(-1)
    if colors==16:tiles=tiles[::2]|(tiles[1::2]<<4)
    return tiles.tobytes()


def tile_pixels(raw,colors):
    values=np.frombuffer(raw,dtype=np.uint8)
    if len(values)!=(18432 if colors==16 else 36864):raise ValueError('Invalid tile data size')
    if colors==16:
        unpacked=np.empty(len(values)*2,dtype=np.uint8);unpacked[::2]=values&15;unpacked[1::2]=values>>4;values=unpacked
    return values.reshape(18,32,8,8).transpose(0,2,1,3).reshape(144,256)


def skip_runs(difference):
    values=np.frombuffer(difference,dtype=np.uint8)
    nonzero=np.flatnonzero(values)
    if not len(nonzero):return struct.pack('<HH',len(values),0)
    cuts=np.flatnonzero(np.diff(nonzero)>4)
    starts=np.r_[nonzero[0],nonzero[cuts+1]];ends=np.r_[nonzero[cuts],nonzero[-1]]+1
    packet=bytearray();position=0
    for start,end in zip(starts,ends):
        packet.extend(struct.pack('<HH',int(start)-position,int(end-start)))
        packet.extend(difference[int(start):int(end)]);position=int(end)
    if position<len(values):packet.extend(struct.pack('<HH',len(values)-position,0))
    return bytes(packet)


def apply_skip(before,packet):
    result=bytearray(before);position=cursor=0
    while position<len(result):
        if cursor+4>len(packet):raise ValueError('Truncated skip packet')
        skip,take=struct.unpack_from('<HH',packet,cursor);cursor+=4
        if skip>len(result)-position:raise ValueError('Skip exceeds frame')
        position+=skip
        if take>len(result)-position or take>len(packet)-cursor or not(skip or take):raise ValueError('Take exceeds packet/frame')
        for i in range(take):result[position+i]^=packet[cursor+i]
        position+=take;cursor+=take
    if cursor!=len(packet):raise ValueError('Trailing skip bytes')
    return bytes(result)


class Encoder:
    def __init__(self,directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        self.executable=directory/'palette_codec.exe'
        subprocess.run([shutil.which('gcc') or 'gcc','-std=gnu11','-O2','-ffunction-sections','-fdata-sections',
            '-Wl,--gc-sections','-I',str(ROOT/'nds/source'),str(ROOT/'experiments/video_lab/palette_codec.c'),
            str(ROOT/'nds/source/asset_pack.c'),'-o',str(self.executable)],check=True,capture_output=True)
        self.process=subprocess.Popen([str(self.executable),'--encode'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def encode(self,raw):
        if not 0<len(raw)<=MAX_PACKET:raise ValueError('LZ10 input exceeds bounded work buffer')
        self.process.stdin.write(struct.pack('<I',len(raw))+raw);self.process.stdin.flush()
        header=self.process.stdout.read(8)
        if len(header)!=8:raise ValueError('C encoder/actual LZ10 decoder rejected the frame')
        length,hashed=struct.unpack('<II',header)
        result=self.process.stdout.read(length)
        if len(result)!=length:raise ValueError('Truncated C encoder output')
        return result,hashed
    def close(self):
        if self.process.poll() is None:
            try:self.process.stdin.write(bytes(4));self.process.stdin.flush()
            except BrokenPipeError:pass
        self.process.stdin.close();self.process.wait()
        self.process.stdout.close();self.process.stderr.close()


def verify_native(executable,path,samples):
    return json.loads(subprocess.check_output([str(executable),'--verify',str(path),str(samples)]))


def build_pack(frames,palettes,*,colors,fps,duration_us,encoder):
    count=op.frame_count(duration_us,fps)
    raw_size=18432 if colors==16 else 36864
    family_frames=fps*2;families=(count+family_frames-1)//family_frames
    if len(frames)!=count or len(palettes)!=families or colors not in COLOR_COUNTS:raise ValueError('Incomplete palette-video duration/table')
    palette_data=b''.join(np.asarray(pal,dtype='<u2').tobytes() for pal in palettes)
    if len(palette_data)!=families*colors*2:raise ValueError('Invalid RGB555 palette length')
    palette_offset=HEADER.size+count*ENTRY.size;payload_offset=palette_offset+len(palette_data)
    position=payload_offset;records=[];payload=[];previous=bytes(raw_size);key=0;mode_counts=Counter();maximum=0;max_seek=0
    for number,current in enumerate(frames):
        if len(current)!=raw_size:raise ValueError('Invalid native tile buffer size')
        compressed,hashed=encoder.encode(current)
        best=(compressed,KEY,raw_size) if len(compressed)<raw_size else(current,KEY|RAW,raw_size)
        if number%family_frames:
            now=np.frombuffer(current,dtype=np.uint8);before=np.frombuffer(previous,dtype=np.uint8)
            difference=np.bitwise_xor(now,before).tobytes()
            tile_size=raw_size//576
            changed=np.any(now.reshape(576,tile_size)!=before.reshape(576,tile_size),axis=1)
            tile_packet=np.packbits(changed,bitorder='little').tobytes()+now.reshape(576,tile_size)[changed].tobytes()
            options=[(XOR,difference),(TILE,tile_packet),(SKIP,skip_runs(difference))]
            for mode,packet in options:
                if len(packet)>MAX_PACKET:continue
                compressed,_=encoder.encode(packet)
                candidate=(compressed,mode,len(packet)) if len(compressed)<len(packet) else(packet,mode|RAW,len(packet))
                if len(candidate[0])<len(best[0]):best=candidate
        packed,flags,decoded=best
        if flags&KEY:key=number
        max_seek=max(max_seek,number-key+1);mode_counts[MODE_NAMES[flags&~RAW]]+=1
        records.append(ENTRY.pack(position,len(packed),decoded,flags,key,number//family_frames,number*22050//fps,hashed,0))
        payload.append(packed);position+=len(packed);maximum=max(maximum,len(packed));previous=current
    header=HEADER.pack(b'ZATPAL01',1,256,144,256,192,0,24,fps,count,duration_us,colors,4 if colors==16 else 8,
        raw_size,families,ENTRY.size,HEADER.size,palette_offset,payload_offset,position,maximum)
    result=header+b''.join(records)+palette_data+b''.join(payload)
    info=inspect_pack(result)
    return result,dict(mode_counts=dict(mode_counts),max_seek_frames=max_seek,max_packed=maximum,
        index_bytes=count*ENTRY.size,palette_bytes=len(palette_data),payload_bytes=sum(map(len,payload)),
        raw_bytes=raw_size,frame_count=info['frame_count'])


def inspect_pack(data):
    if len(data)<HEADER.size:raise ValueError('Truncated palette video')
    magic,*values=HEADER.unpack_from(data);info=dict(zip(FIELDS,values))
    if magic!=b'ZATPAL01' or info['version']!=1 or info['colors'] not in COLOR_COUNTS:raise ValueError('Unknown palette-video format')
    count=op.frame_count(info['duration_us'],info['fps']);raw=18432 if info['colors']==16 else 36864
    families=(count+info['fps']*2-1)//(info['fps']*2)
    expected=(256,144,256,192,0,24,count,4 if info['colors']==16 else 8,raw,families,ENTRY.size,HEADER.size,
        HEADER.size+count*ENTRY.size,HEADER.size+count*ENTRY.size+families*info['colors']*2,len(data))
    actual=tuple(info[k] for k in ('width','height','screen_width','screen_height','x','y','frame_count','bpp','raw_bytes',
        'family_count','index_entry_bytes','index_offset','palette_offset','payload_offset','file_bytes'))
    if expected!=actual or info['payload_offset']>len(data) or not 0<info['max_packed']<=MAX_PACKET:raise ValueError('Invalid palette-video bounds/geometry')
    if info['colors']==256:
        for family in range(families):
            if struct.unpack_from('<H',data,info['palette_offset']+family*info['colors']*2)[0]&0x7fff:raise ValueError('256-color upload requires black index zero')
    rows=[];position=info['payload_offset'];key=0;last_palette=0;maximum=0
    for i in range(count):
        values=ENTRY.unpack_from(data,HEADER.size+i*ENTRY.size)
        offset,length,decoded,flags,nearest,palette,audio_sample,hashed,reserved=values
        mode=flags&~RAW
        if offset!=position or offset>len(data) or not 0<length<=MAX_PACKET or length>len(data)-offset or not 0<decoded<=MAX_PACKET:
            raise ValueError('Palette-video packet outside bounded payload')
        if mode not in MODE_NAMES or palette!=i//(info['fps']*2) or audio_sample!=i*22050//info['fps'] or reserved:raise ValueError('Invalid mode/palette/sample timestamp')
        if mode==KEY:key=i
        if (i==0 or palette!=last_palette) and mode!=KEY:raise ValueError('Palette change requires an independent keyframe')
        if nearest!=key or i-key>=info['fps']*2:raise ValueError('Unbounded/invalid keyframe seek')
        rows.append(dict(offset=offset,length=length,decoded=decoded,flags=flags,key=key,palette=palette,audio_sample=audio_sample,hash=hashed))
        position+=length;last_palette=palette;maximum=max(maximum,length)
    if position!=len(data) or maximum!=info['max_packed']:raise ValueError('Palette payload/index mismatch')
    info['index']=rows
    return info


def rgb555(rgb):
    rgb=np.asarray(rgb,dtype=np.uint16)
    return ((rgb[...,0]>>3)|((rgb[...,1]>>3)<<5)|((rgb[...,2]>>3)<<10)|0x8000).astype('<u2')


def rgb_from_555(values):
    values=np.asarray(values,dtype=np.uint16)
    return np.stack([(values&31)*255//31,((values>>5)&31)*255//31,((values>>10)&31)*255//31],axis=-1).astype(np.uint8)


def family_palettes(samples,colors,families):
    result=[]
    for family in range(families):
        group=samples[family*8:min((family+1)*8,len(samples))]
        canvas=Image.new('RGB',(64*len(group),36))
        for i,image in enumerate(group):canvas.paste(Image.fromarray(image),(64*i,0))
        quantized=canvas.quantize(colors=colors,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
        values=quantized.getpalette()[:colors*3]
        palette=np.array(values+[0]*(colors*3-len(values)),dtype=np.uint8).reshape(colors,3)
        packed=rgb555(palette)
        black=np.flatnonzero((packed&0x7fff)==0)
        if len(black):
            black=int(black[0]);packed[[0,black]]=packed[[black,0]]
        else:
            counts=np.bincount(np.asarray(quantized).reshape(-1),minlength=colors)
            least=int(np.argmin(counts));packed[least]=0x8000;packed[[0,least]]=packed[[least,0]]
        result.append(packed)
    return result


def palette_lookup(palette):
    colors=np.arange(32768,dtype=np.uint16)
    source=np.stack([colors&31,(colors>>5)&31,(colors>>10)&31],axis=1).astype(np.int16)
    palette=np.asarray(palette,dtype=np.uint16)
    target=np.stack([palette&31,(palette>>5)&31,(palette>>10)&31],axis=1).astype(np.int16)
    result=np.empty(32768,dtype=np.uint8)
    for at in range(0,32768,1024):
        difference=source[at:at+1024,None,:]-target[None,:,:]
        result[at:at+1024]=np.argmin(np.sum(difference*difference,axis=2,dtype=np.int32),axis=1)
    return result


def run_native_probe(directory,paths):
    """Measure real ARM9 LZ10/delta, Bmp8 index remapping, cache flush and DMA."""
    directory=Path(directory).resolve();probe=directory/'probe';source_dir=probe/'source';assets=probe/'nitrofs/probe'
    source_dir.mkdir(parents=True,exist_ok=True);assets.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'experiments/video_lab/palette_codec.c',source_dir/'main.itcm.c')
    shutil.copyfile(ROOT/'nds/source/asset_pack.c',source_dir/'asset_pack.itcm.c')
    shutil.copyfile(ROOT/'nds/source/asset_pack.h',source_dir/'asset_pack.h')
    encoder=Encoder(directory/'native');executable=encoder.executable;encoder.close()
    records=[];cases=[]
    for path in paths:
        path=Path(path);data=path.read_bytes();info=inspect_pack(data);rows=info['index'];count=info['frame_count']
        selected={0,count//4,count//2,count*3//4,count-1,min(count-1,info['fps']*2-1),min(count-1,info['fps']*2),min(count-1,info['fps']*2+1)}
        selected.add(max(range(count),key=lambda i:rows[i]['length']));selected.add(max(range(count),key=lambda i:rows[i]['decoded']))
        for mode in MODE_NAMES:
            matching=[i for i,row in enumerate(rows) if row['flags']&~RAW==mode]
            if matching:selected.add(matching[0])
        for frame in sorted(selected):
            row=rows[frame];case=len(records);packed=data[row['offset']:row['offset']+row['length']]
            (assets/f'{case:03d}.bin').write_bytes(packed)
            subprocess.run([str(executable),'--basis',str(path),str(frame),str(assets/f'{case:03d}.base')],check=True,capture_output=True)
            pal_at=info['palette_offset']+row['palette']*info['colors']*2
            (assets/f'{case:03d}.pal').write_bytes(data[pal_at:pal_at+info['colors']*2])
            records.append(struct.pack('<10I',info['fps'],info['colors'],frame,row['length'],row['decoded'],row['flags'],row['hash'],info['colors']*2,info['raw_bytes'],row['palette']))
            cases.append(dict(candidate=path.stem,fps=info['fps'],colors=info['colors'],frame=frame,flags=row['flags'],packed_bytes=row['length'],decoded_bytes=row['decoded'],source_sha256=op.sha256(path)))
    if len(records)>128:raise ValueError('Probe input table exceeds bounded 128-case capacity')
    preview=min(range(len(cases)),key=lambda i:(-cases[i]['colors'],abs(cases[i]['frame']/cases[i]['fps']-29.0985)))
    (assets/'index.bin').write_bytes(struct.pack('<8sII',b'ZPLPRB01',len(records),40)+b''.join(records))
    makefile='''BLOCKSDS ?= /opt/wonderful/thirdparty/blocksds/core
NAME := zato_op_palette_probe
GAME_TITLE := ZATO OP Palette Probe
GAME_SUBTITLE := ARM9 palette delta timings
GAME_AUTHOR := ZATO NDS
SOURCEDIRS := source
INCLUDEDIRS := source
NITROFSDIR := nitrofs
DEFINES := -DOP_PALETTE_PROBE=1
LIBS := -lnds9
CFLAGS := -std=gnu11 -Wall -Wextra -O3 -marm
include $(BLOCKSDS)/sys/default_makefiles/rom_arm9/Makefile
NDSTOOL_ARGS += -h 0x200
'''
    makefile=makefile.replace('DEFINES := -DOP_PALETTE_PROBE=1',f'DEFINES := -DOP_PALETTE_PROBE=1 -DOP_PREVIEW_CASE={preview}')
    (probe/'Makefile').write_text(makefile,encoding='utf-8')
    build=subprocess.run(['C:/msys64/opt/wonderful/wonderful_shell.cmd','-defterm','-no-start','-here','-c','make -j4'],cwd=probe,capture_output=True,text=True)
    (probe/'build.log').write_text(build.stdout+build.stderr,encoding='utf-8')
    if build.returncode:raise RuntimeError(f'Palette probe build failed: {probe/"build.log"}')
    mapping=(probe/'build/zato_op_palette_probe.map').read_text()
    import re
    def offset(name):
        match=re.search(r'0x([0-9a-fA-F]+)\s+'+name+r'\s',mapping)
        if not match:raise ValueError(f'Probe MAP lacks {name}')
        return int(match[1],16)-0x02000000
    status_offset=offset('g_pal_probe_status');sample_offset=offset('g_pal_probe_samples')
    fields='magic ready cases completed errors bus_clock timer_divider calibration_ticks total_ticks max_ticks frame_buffer_bytes work_buffer_bytes packed_buffer_bytes'.split()
    sample_fields='fps colors frame pass bytes flags decode_ticks read_ticks raw_bytes work_bytes'.split()
    tools=Path(r'C:\DeskBox\Source\Nds_UI_Test\tools');sys.path.insert(0,str(tools))
    from nds_emulator import Emulator
    rom=probe/'zato_op_palette_probe.nds';emu=Emulator(tools/'emulator/desmume_libretro.dll',rom)
    try:
        status=None
        for _ in range(100):
            emu.run(60);memory=emu.memory();status=dict(zip(fields,struct.unpack_from('<13I',memory,status_offset)))
            if status['ready']:break
        if not status or not status['ready'] or status['errors'] or status['completed']!=len(records)*2:raise ValueError(f'Native palette probe failed: {status}')
        results=[]
        for i in range(status['completed']):
            row=dict(zip(sample_fields,struct.unpack_from('<10I',memory,sample_offset+i*40)))
            row['decode_and_vram_ms']=row['decode_ticks']*status['timer_divider']*1000/status['bus_clock']
            row['read_ms']=row['read_ticks']*status['timer_divider']*1000/status['bus_clock'];results.append(row)
        groups=[]
        for fps,colors in sorted({(case['fps'],case['colors']) for case in cases}):
            rows=[row for row in results if row['fps']==fps and row['colors']==colors];times=np.array([row['decode_and_vram_ms'] for row in rows])
            groups.append(dict(fps=fps,colors=colors,samples=len(rows),mean_ms=float(times.mean()),p95_ms=float(np.quantile(times,.95)),max_ms=float(times.max()),
                max_read_plus_decode_ms=max(row['read_ms']+row['decode_and_vram_ms'] for row in rows),deadline_ms=1000/fps,all_samples_within_deadline=bool(np.all(times<1000/fps))))
        report=dict(result='PASS',method='Real standalone ARM9 ROM in DeSmuME. Cascaded DS timers BUS_CLOCK/64; actual existing C LZ10 decoder + delta + Bmp8 index remapping + DC flush + bitmap/palette VRAM DMA; frame hashes checked outside measured span.',
            rom=str(rom),rom_bytes=rom.stat().st_size,rom_sha256=op.sha256(rom),status=status,cases=cases,samples=results,groups=groups,
            calibration_seconds=status['calibration_ticks']*status['timer_divider']/status['bus_clock'],
            memory_buffers_bytes=status['frame_buffer_bytes']+status['work_buffer_bytes']+status['packed_buffer_bytes']+512+49152+512,
            decoder_source_sha256=op.sha256(ROOT/'nds/source/asset_pack.c'),preview_case=cases[preview],
            limitations='Not a full native video player. Audio is not running in the microbenchmark; emulated NitroFS read timing is not physical R4 latency. Full native audio/video scheduling and seek integration remain separate work.')
        (directory/'native_palette_probe_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        emu.run(2)
        if emu.frame is not None:emu.frame.save(directory/'native_palette_probe.png')
        return report
    finally:emu.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=op.DEFAULT_SOURCE)
    parser.add_argument('--output',type=Path,default=op.DEFAULT_OUTPUT/'palette')
    parser.add_argument('--rates',nargs='+',type=int,default=[15,20,30])
    parser.add_argument('--colors',nargs='+',type=int,default=[16,256])
    args=parser.parse_args()
    if any(fps not in(15,20,30) for fps in args.rates) or any(c not in COLOR_COUNTS for c in args.colors):parser.error('Use 15/20/30fps and power-of-two palettes 16..256')
    if args.output.resolve().is_relative_to(args.source.parent.resolve()):parser.error('Original source stays read-only')
    args.output.mkdir(parents=True,exist_ok=True);commands=[];source_hash=op.sha256(args.source)
    protected=[ROOT/'nds/artifacts/audio_manifest.json',ROOT/'nds/config/audio_edits.json',ROOT/'nds/zato_ep2d3_scene_nitrofs.nds']
    before={str(p):op.sha256(p) for p in protected}
    probe=json.loads(op.execute(['ffprobe','-v','error','-show_format','-of','json',args.source],commands=commands))
    duration_us=int(float(probe['format']['duration'])*1000000+0.5)
    families=math.ceil(duration_us/2_000_000)
    sample_bytes=op.execute(['ffmpeg','-v','error','-i',args.source,'-an','-vf',
        'tpad=stop_mode=clone:stop_duration=0.1,fps=4:round=up,scale=64:36:flags=area,format=rgb24',
        '-frames:v',op.frame_count(duration_us,4),'-f','rawvideo','-'],commands=commands)
    samples=np.frombuffer(sample_bytes,dtype=np.uint8).reshape(-1,36,64,3)
    palettes={colors:family_palettes(samples,colors,families) for colors in args.colors}
    lookups={}
    for colors in args.colors:
        print(f'Building {families} stable {colors}-color family lookup tables',flush=True)
        lookups[colors]=[palette_lookup(palette) for palette in palettes[colors]]
    encoder=Encoder(args.output/'native');candidates=[]
    try:
        for fps in args.rates:
            count=op.frame_count(duration_us,fps)
            raw=op.execute(['ffmpeg','-v','error','-i',args.source,'-an','-vf',
                f'tpad=stop_mode=clone:stop_duration=0.1,fps={fps}:round=up,scale=256:144:flags=area,format=rgb24',
                '-frames:v',count,'-f','rawvideo','-'],commands=commands)
            source=rgb555(np.frombuffer(raw,dtype=np.uint8).reshape(count,144,256,3));del raw
            sample_ids={0,count//4,count//2,count*3//4,count-1}
            for colors in args.colors:
                name=f'op_palette{colors}_f{fps:02d}';started=time.perf_counter()
                frames=[];mse=[];temporal=[];boundary_temporal=[];previous_error=None
                reference_images={};expected_images={}
                for i,reference in enumerate(source):
                    family=i//(fps*2);indices=lookups[colors][family][reference&0x7fff]
                    frames.append(tile_bytes(indices,colors))
                    reconstructed=palettes[colors][family][indices]
                    error=(rgb_from_555(reconstructed).astype(np.float32)-rgb_from_555(reference).astype(np.float32))
                    mse.append(float(np.mean(error**2)))
                    if previous_error is not None:
                        value=float(np.mean(np.abs(error-previous_error)));temporal.append(value)
                        if i%(fps*2)==0:boundary_temporal.append(value)
                    previous_error=error
                    if i in sample_ids:
                        reference_images[i]=rgb_from_555(reference);expected_images[i]=indices
                data,metadata=build_pack(frames,palettes[colors],colors=colors,fps=fps,duration_us=duration_us,encoder=encoder)
                path=args.output/(name+'.zpv');path.write_bytes(data)
                sample_dir=args.output/(name+'_samples');validation=verify_native(encoder.executable,path,sample_dir)
                quality_samples=[]
                for i in sorted(sample_ids):
                    tiles=(sample_dir/f'{i:05d}.tiles').read_bytes()
                    actual_indices=tile_pixels(tiles,colors)
                    if not np.array_equal(actual_indices,expected_images[i]):raise ValueError('Actual native LZ10/delta output pixels differ from quantized frame')
                    palette=np.frombuffer((sample_dir/f'{i:05d}.pal').read_bytes(),dtype='<u2')
                    image=Image.new('RGB',(256,192));image.paste(Image.fromarray(rgb_from_555(palette[actual_indices])),(0,24))
                    image.save(sample_dir/f'{i:05d}.png')
                    ref=Image.new('RGB',(256,192));ref.paste(Image.fromarray(reference_images[i]),(0,24));ref.save(sample_dir/f'{i:05d}_source.png')
                    quality_samples.append(dict(frame=i,seconds=i/fps,psnr_db=10*math.log10(255**2/max(mse[i],1e-20))))
                audio=json.loads((op.DEFAULT_OUTPUT/'op_soundtrack_ima.json').read_text())
                total=len(data)+audio['bytes']+(op.DEFAULT_OUTPUT/'op_soundtrack_ima.json').stat().st_size
                result=dict(name=name,file=path.name,sha256=op.sha256(path),fps=fps,colors=colors,bpp=4 if colors==16 else 8,
                    video_bytes=len(data),bundle_bytes=total,bundle_mib=total/1048576,within_6mib=total<=6*1048576,within_8mb=total<=8_000_000,
                    **metadata,native_verification=validation,quality_rgb15_source_psnr_db=10*math.log10(255**2/max(float(np.mean(mse)),1e-20)),
                    temporal_quantization_error_mae=float(np.mean(temporal)),temporal_error_p95=float(np.quantile(temporal,.95)),
                    temporal_error_max=float(max(temporal)),family_boundary_error_mae=float(np.mean(boundary_temporal)),
                    sample_quality=quality_samples,conversion_wall_seconds=time.perf_counter()-started)
                candidates.append(result)
                print(f'{name}: full {count} frames, {total:,} B stereo bundle ({total/1048576:.3f}MiB), PSNR {result["quality_rgb15_source_psnr_db"]:.2f}dB, native seeks {validation["seek_checks"]}',flush=True)
                del frames,data
            del source
    finally:encoder.close()
    if source_hash!=op.sha256(args.source) or before!={str(p):op.sha256(p) for p in protected}:raise ValueError('Read-only source/frozen ROM/music changed')
    report=dict(result='PASS',source=dict(file=str(args.source),sha256=source_hash),duration_us=duration_us,
        geometry=dict(width=256,height=144,x=0,y=24,screen_width=256,screen_height=192),
        palette_policy='One nondithered palette per fixed 2-second time family; RGB555 nearest-color mapping; index 0 reserved for black; all family boundaries are independent keyframes.',
        delta_modes=MODE_NAMES,compression='Existing native LZ10 decoder; bounded full/XOR/changed-8x8-tile/skip-run packets; raw storage when smaller',
        quality_metric='RGB PSNR against BOX-reduced RGB15 source; temporal MAE is the difference between successive quantization-error images, isolating palette flicker from source motion.',
        decoder_source=str(ROOT/'nds/source/asset_pack.c'),decoder_source_sha256=op.sha256(ROOT/'nds/source/asset_pack.c'),
        audio_file='../op_soundtrack_ima.wav',audio_metadata='../op_soundtrack_ima.json',audio=audio,
        candidates=candidates,commands=commands,protected_sha256=before,source_and_protected_files_unchanged=True)
    (args.output/'palette_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return 0


if __name__=='__main__':raise SystemExit(main())
