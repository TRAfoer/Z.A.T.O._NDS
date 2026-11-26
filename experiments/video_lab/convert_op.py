"""Repeatable native MJPEG/IMA conversion experiment for the complete Chinese OP.

Outputs are experiments only. Original WebM, approved music, native audio runtime
and the frozen hardware-test ROM remain read-only. JPEG frames are baseline
YCbCr 4:2:0, suitable for the bundled BlocksDS RGB555 TJpgDec C decoder.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
from io import BytesIO
import hashlib
import json
import os
import ctypes as C
import re
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
DECODER_DIR = Path('C:/msys64/opt/wonderful/thirdparty/blocksds/core/examples/demos/photo_slideshow/source')
ARM_GCC = Path('C:/msys64/opt/wonderful/toolchain/gcc-arm-none-eabi/bin/arm-none-eabi-gcc.exe')
DEFAULT_SOURCE = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Z.A.T.O.  I Love the World and Everything In It\game\tl\op.webm')
DEFAULT_OUTPUT = ROOT/'experiments/video_lab/out'
HEADER = struct.Struct('<8s16I')
INDEX = struct.Struct('<III')
HEADER_FIELDS = ('version width height screen_width screen_height x y fps_num fps_den frame_count '
    'duration_us entry_bytes index_offset payload_offset file_bytes max_frame_bytes').split()
MAX_FRAMES, MAX_FRAME_BYTES, AUDIO_RATE = 4096, 65536, 22050
BUDGET = 6*1024*1024


def execute(arguments, *, input_bytes=None, commands=None, environment=None):
    arguments = [str(argument) for argument in arguments]
    if commands is not None:
        commands.append(arguments)
    return subprocess.check_output(arguments, input=input_bytes, stderr=subprocess.PIPE, env=environment)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frame_count(duration_us, fps):
    if not 0 < duration_us <= 0xffffffff or not 0 < fps <= 60:
        raise ValueError('Invalid native duration/frame rate')
    count = (duration_us * fps + 999999) // 1000000
    if not 0 < count <= MAX_FRAMES:
        raise ValueError('Frame count exceeds bounded native index')
    return count


def _jpeg_end(data, start):
    if data[start:start+2] != b'\xff\xd8':
        raise ValueError(f'Missing JPEG SOI at {start}')
    position, scan = start+2, False
    while position < len(data):
        if scan:
            position = data.find(b'\xff', position)
            if position < 0 or position+1 >= len(data):
                break
            code = data[position+1]
            if code == 0 or 0xd0 <= code <= 0xd7:
                position += 2
                continue
            if code == 0xff:
                position += 1
                continue
            scan = False
        if data[position] != 0xff:
            raise ValueError(f'Missing JPEG marker at {position}')
        while position < len(data) and data[position] == 0xff:
            position += 1
        if position >= len(data):
            break
        code = data[position]
        position += 1
        if code == 0xd9:
            return position
        if code in (0, 0xd8):
            raise ValueError('Invalid nested/stuffed JPEG marker outside scan')
        if code == 1 or 0xd0 <= code <= 0xd7:
            continue
        if position+2 > len(data):
            break
        size = int.from_bytes(data[position:position+2], 'big')
        if size < 2 or position+size > len(data):
            raise ValueError('Truncated JPEG segment')
        position += size
        if code == 0xda:
            scan = True
    raise ValueError('Truncated JPEG frame without EOI')


def split_mjpeg(data):
    frames, position = [], 0
    while position < len(data):
        end = _jpeg_end(data, position)
        frames.append(data[position:end])
        position = end
    if not frames:
        raise ValueError('MJPEG stream is empty')
    return frames


def jpeg_info(frame):
    if _jpeg_end(frame, 0) != len(frame):
        raise ValueError('Trailing bytes after JPEG frame')
    position = 2
    while position+4 <= len(frame):
        if frame[position] != 0xff:
            break
        code = frame[position+1]
        size = int.from_bytes(frame[position+2:position+4], 'big')
        if code == 0xc0:
            payload = frame[position+4:position+2+size]
            if len(payload) < 15:
                raise ValueError('Incomplete baseline SOF')
            precision, height, width, components = struct.unpack_from('>BHHB', payload)
            if components != 3 or len(payload) < 6+components*3:
                raise ValueError('Native JPEG requires three complete color components')
            sampling = [payload[7+i*3] for i in range(components)]
            if (precision, width, height, components, sampling) != (8, 256, 144, 3, [0x22, 0x11, 0x11]):
                raise ValueError(f'Unsupported native JPEG format: {precision,width,height,components,sampling}')
            return dict(baseline=True, width=width, height=height, components=components, sampling='4:2:0')
        if code in (0xc1, 0xc2, 0xc3, 0xda):
            break
        position += 2 + size
    raise ValueError('JPEG must contain baseline SOF0')


def pack_video(frames, *, duration_us, fps):
    expected = frame_count(duration_us, fps)
    if len(frames) != expected:
        raise ValueError(f'Full duration needs {expected} frames, received {len(frames)}')
    payload_offset = HEADER.size + len(frames)*INDEX.size
    records, position, maximum = [], payload_offset, 0
    for number, frame in enumerate(frames):
        if not 4 <= len(frame) <= MAX_FRAME_BYTES:
            raise ValueError('JPEG exceeds bounded per-frame input buffer')
        jpeg_info(frame)
        records.append(INDEX.pack(position, len(frame), number*AUDIO_RATE//fps))
        position += len(frame)
        maximum = max(maximum, len(frame))
    header = HEADER.pack(b'ZATVID01', 1, 256, 144, 256, 192, 0, 24, fps, 1, len(frames),
        duration_us, INDEX.size, HEADER.size, payload_offset, position, maximum)
    result = header + b''.join(records) + b''.join(frames)
    inspect_video(result)
    return result


def inspect_video(data):
    if len(data) < HEADER.size:
        raise ValueError('Truncated native video header')
    magic, *values = HEADER.unpack_from(data)
    info = dict(zip(HEADER_FIELDS, values))
    if magic != b'ZATVID01' or info['version'] != 1:
        raise ValueError('Unknown native video format')
    if tuple(info[key] for key in ('width', 'height', 'screen_width', 'screen_height', 'x', 'y', 'fps_den')) != (256, 144, 256, 192, 0, 24, 1):
        raise ValueError('Invalid native video geometry/frame rate')
    if info['frame_count'] != frame_count(info['duration_us'], info['fps_num']):
        raise ValueError('Native index does not span the complete duration')
    expected_payload = HEADER.size + info['frame_count']*INDEX.size
    if (info['entry_bytes'], info['index_offset'], info['payload_offset'], info['file_bytes']) != (INDEX.size, HEADER.size, expected_payload, len(data)):
        raise ValueError('Invalid native video table/payload bounds')
    if expected_payload > len(data) or not 4 <= info['max_frame_bytes'] <= MAX_FRAME_BYTES:
        raise ValueError('Invalid maximum JPEG size')
    index, position, maximum = [], expected_payload, 0
    for number in range(info['frame_count']):
        offset, length, sample = INDEX.unpack_from(data, HEADER.size + number*INDEX.size)
        if offset != position or not 4 <= length <= info['max_frame_bytes'] or length > len(data)-offset or offset > len(data):
            raise ValueError('Native JPEG range is outside the contiguous payload')
        if sample != number*AUDIO_RATE//info['fps_num']:
            raise ValueError('Native JPEG timestamps differ from the audio sample clock')
        index.append(dict(offset=offset, length=length, audio_sample=sample))
        position += length
        maximum = max(maximum, length)
    if position != len(data) or maximum != info['max_frame_bytes']:
        raise ValueError('Native payload size does not match the bounded index')
    info['index'] = index
    return info


def build_native_validator(directory, decoder_dir=DECODER_DIR):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory/'validate_op.exe'
    subprocess.run([shutil.which('gcc') or 'gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra',
        '-I', str(decoder_dir), str(ROOT/'experiments/video_lab/validate_op.c'),
        str(Path(decoder_dir)/'tjpgd.twl.c'), '-o', str(executable)], check=True, capture_output=True)
    return executable


def wav_info(path):
    data = Path(path).read_bytes()
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('Invalid IMA WAV header')
    info, position = {}, 12
    while position+8 <= len(data):
        kind, length = struct.unpack_from('<4sI', data, position)
        payload = position+8
        if length > len(data)-payload:
            raise ValueError('Truncated IMA WAV chunk')
        if kind == b'fmt ':
            codec, channels, rate, _, block, bits = struct.unpack_from('<HHIIHH', data, payload)
            if (codec, channels, rate, block, bits) != (0x11, 2, AUDIO_RATE, 512, 4):
                raise ValueError('Soundtrack differs from the existing stereo IMA block format')
            samples = struct.unpack_from('<H', data, payload+18)[0]
            if samples != 505:
                raise ValueError('Invalid stereo IMA samples-per-block')
            info.update(channels=channels, rate=rate, block=block, samples_per_block=samples)
        elif kind == b'data':
            info.update(data_offset=payload, data_bytes=length)
        position = payload+length+(length&1)
    if not info or info.get('data_bytes', 1) % 512:
        raise ValueError('Soundtrack payload is not a complete IMA block sequence')
    info['padded_frames'] = info['data_bytes']//512*505
    return info


def convert_audio(source, directory, commands=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source_pcm = execute(['ffmpeg', '-v', 'error', '-i', source, '-map', '0:a:0', '-vn',
        '-ar', AUDIO_RATE, '-ac', 2, '-f', 's16le', '-c:a', 'pcm_s16le', '-'], commands=commands)
    if not source_pcm or len(source_pcm)%4:
        raise ValueError('Incomplete stereo reference PCM')
    reference_path = directory/'op_audio_reference_s16le.pcm'
    reference_path.write_bytes(source_pcm)
    soundtrack = directory/'op_soundtrack_ima.wav'
    execute(['ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ar', AUDIO_RATE, '-ac', 2,
        '-i', reference_path, '-c:a', 'adpcm_ima_wav', '-block_size', 512, '-map_metadata', '-1', soundtrack], commands=commands)
    info = wav_info(soundtrack)
    frames = len(source_pcm)//4
    if info['padded_frames'] < frames:
        raise ValueError('IMA soundtrack truncates decoded source audio')
    build = directory/'native'
    build.mkdir(exist_ok=True)
    decoder = build/'decode_ima.exe'
    execute([shutil.which('gcc') or 'gcc', '-std=gnu11', '-O2', '-I', ROOT/'nds/source',
        ROOT/'nds/tests/decode_ima.c', ROOT/'nds/source/ima_adpcm.c', '-o', decoder], commands=commands)
    actual = execute([decoder, soundtrack, info['data_offset'], info['data_bytes'], 2, frames], commands=commands)
    reference = execute(['ffmpeg', '-v', 'error', '-i', soundtrack, '-f', 's16le', '-c:a', 'pcm_s16le', '-'], commands=commands)[:len(source_pcm)]
    if actual != reference or len(actual) != len(source_pcm):
        raise ValueError('Actual native IMA decoder differs from FFmpeg soundtrack PCM')
    original = np.frombuffer(source_pcm, dtype='<i2').reshape(-1, 2).astype(np.float64)
    decoded = np.frombuffer(actual, dtype='<i2').reshape(-1, 2).astype(np.float64)
    error = decoded-original
    signal = np.mean(original**2, axis=0)
    noise = np.mean(error**2, axis=0)
    result = dict(info, file=soundtrack.name, frames=frames, seconds=frames/AUDIO_RATE,
        bytes=soundtrack.stat().st_size, sha256=sha256(soundtrack),
        source_pcm_sha256=hashlib.sha256(source_pcm).hexdigest(), decoded_pcm_sha256=hashlib.sha256(actual).hexdigest(),
        loop=False, music_edit=None, separate_video_soundtrack=True,
        runtime_c_equals_ffmpeg=True,
        different_channel_frames=int(np.count_nonzero(decoded[:,0] != decoded[:,1])),
        original_different_channel_frames=int(np.count_nonzero(original[:,0] != original[:,1])),
        channel_peaks=np.max(np.abs(decoded), axis=0).astype(int).tolist(),
        channel_snr_db=(10*np.log10(np.maximum(signal, 1e-20)/np.maximum(noise, 1e-20))).tolist(),
        channel_correlation=[float(np.corrcoef(original[:,i], decoded[:,i])[0,1]) for i in range(2)],
        source_channel_correlation=float(np.corrcoef(original.T)[0,1]),
        decoded_channel_correlation=float(np.corrcoef(decoded.T)[0,1]),
        native_decoder_source_sha256=sha256(ROOT/'nds/source/ima_adpcm.c'))
    (directory/'op_soundtrack_ima.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return result


def _rgb555_image(path):
    values = np.frombuffer(Path(path).read_bytes(), dtype='<u2').reshape(192, 256)
    rgb = np.stack([(values&31)*255//31, ((values>>5)&31)*255//31,
        ((values>>10)&31)*255//31], axis=-1).astype(np.uint8)
    return Image.fromarray(rgb)


def run_native_probe(directory, candidates, tcm=False):
    """Build a separate tiny ARM9 ROM and measure selected JPEGs with DS timers.

    candidates contains (packed_path, fps, quality). No final/movie ROM is built.
    JPEG read timing is reported separately; audio and display DMA are outside the
    decode timer. A future player still needs a combined card/audio/video test.
    """
    directory = Path(directory).resolve()
    probe = directory/('probe_tcm' if tcm else 'probe')
    source_dir, assets = probe/'source', probe/'nitrofs/probe'
    source_dir.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    for name in ('tjpgd.h', 'tjpgdcnf.h'):
        shutil.copyfile(DECODER_DIR/name, source_dir/name)
    shutil.copyfile(DECODER_DIR/'tjpgd.twl.c', source_dir/('tjpgd.itcm.c' if tcm else 'tjpgd.c'))
    if tcm:
        configuration=(source_dir/'tjpgdcnf.h').read_text()
        configuration, replacements=re.subn(r'#define\s+JD_SZBUF\s+[^\n]+', '#define JD_SZBUF 512', configuration)
        if replacements!=1:
            raise ValueError('Cannot locate the native decoder input-buffer configuration')
        (source_dir/'tjpgdcnf.h').write_text(configuration,encoding='utf-8')
    shutil.copyfile(ROOT/'experiments/video_lab/validate_op.c', source_dir/'main.c')
    records, case_metadata = [], []
    for packed_path, fps, quality in candidates:
        packed_path = Path(packed_path)
        data = packed_path.read_bytes()
        info = inspect_video(data)
        count = info['frame_count']
        largest = max(range(count), key=lambda i:info['index'][i]['length'])
        selected = sorted({0,count//4,count//2,count*3//4,count-1,largest})
        for frame in selected:
            row = info['index'][frame]
            jpeg = data[row['offset']:row['offset']+row['length']]
            (assets/f'{len(records):03d}.jpg').write_bytes(jpeg)
            records.append(struct.pack('<4I', fps, quality, frame, len(jpeg)))
            case_metadata.append(dict(candidate=packed_path.stem, fps=fps, quality=quality,
                frame=frame, bytes=len(jpeg), source_pack_sha256=sha256(packed_path)))
    if len(records)>32:
        raise ValueError('Native probe exceeds its bounded 32-input/64-result table')
    (assets/'index.bin').write_bytes(struct.pack('<8sII',b'ZOPPRB01',len(records),16)+b''.join(records))
    makefile='''BLOCKSDS ?= /opt/wonderful/thirdparty/blocksds/core
NAME := zato_op_decode_probe
GAME_TITLE := ZATO OP Decode Probe
GAME_SUBTITLE := ARM9 JPEG timings
GAME_AUTHOR := ZATO NDS
SOURCEDIRS := source
INCLUDEDIRS := source
NITROFSDIR := nitrofs
DEFINES := -DOP_NDS_PROBE=1
LIBS := -lnds9
CFLAGS := -std=gnu11 -Wall -Wextra -O3 -marm
include $(BLOCKSDS)/sys/default_makefiles/rom_arm9/Makefile
NDSTOOL_ARGS += -h 0x200
'''
    if tcm:
        makefile=makefile.replace('DEFINES := -DOP_NDS_PROBE=1','DEFINES := -DOP_NDS_PROBE=1 -DOP_PROBE_TCM=1')
    (probe/'Makefile').write_text(makefile, encoding='utf-8')
    command = ['C:/msys64/opt/wonderful/wonderful_shell.cmd','-defterm','-no-start','-here','-c','make -j4']
    build = subprocess.run(command, cwd=probe, capture_output=True, text=True)
    (probe/'build.log').write_text(build.stdout+build.stderr, encoding='utf-8')
    if build.returncode:
        raise RuntimeError(f'Native JPEG probe build failed; see {probe/"build.log"}')
    mapping = (probe/'build/zato_op_decode_probe.map').read_text()
    def offset(symbol):
        match = re.search(r'0x([0-9a-fA-F]+)\s+'+symbol+r'\s', mapping)
        if not match:
            raise ValueError(f'Probe MAP lacks {symbol}')
        return int(match[1],16)-0x02000000
    fields = ('magic ready cases completed errors bus_clock timer_divider total_decode_ticks '
        'max_decode_ticks total_read_ticks max_read_ticks jpeg_buffer_bytes work_buffer_bytes screen_bytes calibration_ticks').split()
    sample_fields = 'fps quality frame pass bytes decode_ticks read_ticks work_bytes painted'.split()
    status_offset, sample_offset = offset('g_op_probe_status'), offset('g_op_probe_samples')
    tools = Path(r'C:\DeskBox\Source\Nds_UI_Test\tools')
    if str(tools) not in sys.path:
        sys.path.insert(0,str(tools))
    from nds_emulator import Emulator
    rom = probe/'zato_op_decode_probe.nds'
    emu = Emulator(tools/'emulator/desmume_libretro.dll',rom)
    try:
        status = None
        for _ in range(100):
            emu.run(60)
            memory = emu.memory()
            status = dict(zip(fields,struct.unpack_from('<15I',memory,status_offset)))
            if status['ready']:
                break
        if not status or not status['ready'] or status['errors'] or status['completed']!=len(records)*2:
            raise ValueError(f'Native ARM9 probe did not finish: {status}')
        samples=[]
        for i in range(status['completed']):
            row=dict(zip(sample_fields,struct.unpack_from('<9I',memory,sample_offset+i*36)))
            row['decode_ms']=row['decode_ticks']*status['timer_divider']*1000/status['bus_clock']
            row['read_ms']=row['read_ticks']*status['timer_divider']*1000/status['bus_clock']
            row['read_plus_decode_ms']=row['decode_ms']+row['read_ms']
            samples.append(row)
        groups=[]
        for _,fps,quality in candidates:
            rows=[row for row in samples if row['fps']==fps and row['quality']==quality]
            times=np.array([row['decode_ms'] for row in rows])
            groups.append(dict(fps=fps,quality=quality,samples=len(rows),
                decode_min_ms=float(times.min()),decode_mean_ms=float(times.mean()),
                decode_p95_ms=float(np.quantile(times,.95)),decode_max_ms=float(times.max()),
                read_plus_decode_max_ms=max(row['read_plus_decode_ms'] for row in rows),
                decode_deadline_ms=1000/fps,all_decode_samples_within_frame=bool(np.all(times<1000/fps))))
        report=dict(result='PASS',rom=str(rom),rom_bytes=rom.stat().st_size,rom_sha256=sha256(rom),
            method='Actual standalone ARM9 NDS in DeSmuME; cascaded DS timers 0/1, BUS_CLOCK/64. Two passes over uniform-time and largest-byte JPEG samples.',
            status=status,cases=case_metadata,samples=samples,groups=groups,
            tcm_optimization=tcm, fixed_pixel_buffers_bytes=65536+status['work_buffer_bytes']+256*192*2,
            calibration_seconds=status['calibration_ticks']*status['timer_divider']/status['bus_clock'],
            limitation='Decoder and RGB555 output writes are measured. Audio mixing, full-video timing and screen DMA are not inside decode timing. Emulated NitroFS read cost is not physical R4 latency.',
            decoder_source_sha256=sha256(DECODER_DIR/'tjpgd.twl.c'))
        report_name='native_probe_tcm_report.json' if tcm else 'native_probe_report.json'
        (directory/report_name).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        if emu.frame is not None:
            emu.frame.save(directory/('native_probe_tcm.png' if tcm else 'native_probe.png'))
        return report
    finally:
        emu.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--rates', nargs='+', type=int, default=[15,20,30])
    parser.add_argument('--qualities', nargs='+', type=int, default=[2,4,8,12])
    parser.add_argument('--budget-bytes', type=int, default=BUDGET)
    args = parser.parse_args()
    if not all(rate in (15,20,30) for rate in args.rates) or not all(2 <= q <= 31 for q in args.qualities):
        parser.error('Use requested 15/20/30fps and MJPEG quantizers 2..31')
    if args.output.resolve().is_relative_to(args.source.parent.resolve()):
        parser.error('Output must remain outside the read-only original source directory')
    args.output.mkdir(parents=True, exist_ok=True)
    commands = []
    source_hash = sha256(args.source)
    protected_paths = [ROOT/'nds/artifacts/audio_manifest.json', ROOT/'nds/config/audio_edits.json',
        ROOT/'nds/zato_ep2d3_scene_nitrofs.nds']
    protected = {str(path): sha256(path) for path in protected_paths}
    probe = json.loads(execute(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', args.source], commands=commands))
    video = next(row for row in probe['streams'] if row['codec_type']=='video')
    audio_source = next(row for row in probe['streams'] if row['codec_type']=='audio')
    if (video['width'], video['height'], audio_source['channels']) != (1280,720,2):
        raise ValueError('Source dimensions/stereo channels differ from the audited Chinese OP')
    duration_us = int(Decimal(probe['format']['duration'])*1000000)
    audio = convert_audio(args.source, args.output, commands)
    print(f'Soundtrack: full {audio["frames"]} stereo frames, {audio["bytes"]:,} bytes, native C exact to FFmpeg', flush=True)
    native = build_native_validator(args.output/'native')
    license_path = DECODER_DIR.parent/'tjpgd.txt'
    shutil.copyfile(license_path, args.output/'native/tjpgd_LICENSE.txt')
    decoder_metadata = dict(name='BlocksDS bundled TJpgDec R0.03, DS RGB555 extension',
        source=str(DECODER_DIR/'tjpgd.twl.c'), sha256=sha256(DECODER_DIR/'tjpgd.twl.c'),
        config_sha256=sha256(DECODER_DIR/'tjpgdcnf.h'), license='ChaN permissive BSD-style; retain source copyright notice',
        license_file='native/tjpgd_LICENSE.txt',
        documentation='https://elm-chan.org/fsw/tjpgd/en/appnote.html',
        output='opaque A1B5G5R5; 256x144 written at (0,24) inside 256x192',
        memory='One bounded JPEG <=65536 bytes + 32768-byte work pool + 98304-byte 256x192 RGB555 buffer; future player may double-buffer',
        native_player_not_integrated=True,
        timing_limit='Host C decode demonstrates format/port compatibility; ARM9/R4 playback rate remains unmeasured')
    if ARM_GCC.is_file():
        arm_environment = os.environ.copy()
        arm_environment['PATH'] = 'C:\\msys64\\opt\\wonderful\\bin;C:\\msys64\\ucrt64\\bin;C:\\msys64\\usr\\bin;' + arm_environment['PATH']
        target_object = args.output/'native/tjpgd_arm9.o'
        execute([ARM_GCC, '-std=gnu11', '-O3', '-mcpu=arm946e-s', '-marm',
            '-ffunction-sections', '-fdata-sections', '-I', DECODER_DIR, '-c',
            DECODER_DIR/'tjpgd.twl.c', '-o', target_object], commands=commands, environment=arm_environment)
        size = execute([ARM_GCC.with_name('arm-none-eabi-size.exe'), target_object], commands=commands, environment=arm_environment).decode().strip()
        decoder_metadata['arm9_compile'] = dict(result='PASS', compiler=str(ARM_GCC),
            object=target_object.name, sha256=sha256(target_object), size_output=size,
            environment='Wonderful bin, MSYS2 ucrt64/bin and usr/bin must precede unrelated MinGW DLLs on PATH')
    scaled = args.output/'op_scaled_lossless_reference.mkv'
    execute(['ffmpeg', '-v', 'error', '-y', '-i', args.source, '-map', '0:v:0', '-an',
        '-vf', 'scale=256:144:flags=area:out_color_matrix=bt601:out_range=pc,format=yuv420p',
        '-c:v', 'ffv1', '-level', 3, '-threads', 1, '-color_range', 'pc', '-colorspace', 'smpte170m',
        '-map_metadata', '-1', scaled], commands=commands)
    candidates = []
    for fps in args.rates:
        count = frame_count(duration_us, fps)
        filtering = f'tpad=stop_mode=clone:stop_duration=0.1,fps=fps={fps}:round=up,format=yuvj420p'
        for quality in args.qualities:
            name = f'op_f{fps:02d}_q{quality:02d}'
            started = time.perf_counter()
            mjpeg = execute(['ffmpeg', '-v', 'error', '-y', '-i', scaled, '-an', '-vf', filtering,
                '-frames:v', count, '-c:v', 'mjpeg', '-q:v', quality, '-huffman', 'default',
                '-pix_fmt', 'yuvj420p', '-threads', 1, '-map_metadata', '-1', '-f', 'image2pipe', '-'], commands=commands)
            frames = split_mjpeg(mjpeg)
            packed = pack_video(frames, duration_us=duration_us, fps=fps)
            path = args.output/(name+'.zav')
            path.write_bytes(packed)
            information = inspect_video(packed)
            sample_dir = args.output/(name+'_native')
            validation = json.loads(execute([native, path, sample_dir], commands=commands))
            sample_metrics = []
            for sample in sorted(sample_dir.glob('*.rgb555')):
                number = int(sample.stem)
                image = _rgb555_image(sample)
                image.save(sample.with_suffix('.png'))
                expected = np.asarray(Image.open(BytesIO(frames[number])).convert('RGB')).astype(np.float64)
                actual = np.asarray(image.crop((0,24,256,168))).astype(np.float64)
                mse = float(np.mean((actual-expected)**2))
                sample_metrics.append(dict(frame=number, native_vs_libjpeg_psnr_db=10*np.log10(255**2/max(mse,1e-20))))
            total = len(packed)+audio['bytes']+(args.output/'op_soundtrack_ima.json').stat().st_size
            candidate = dict(name=name, fps=fps, quality=quality, frame_count=len(frames),
                duration_us=duration_us, file=path.name, sha256=sha256(path),
                jpeg_bytes=len(mjpeg), header_bytes=HEADER.size, index_bytes=count*INDEX.size,
                video_bytes=len(packed), soundtrack_bytes=audio['bytes'], bundle_bytes=total,
                bundle_mib=total/(1024*1024), within_budget=total <= args.budget_bytes,
                max_frame_bytes=information['max_frame_bytes'],
                native_validation=validation, native_sample_quality=sample_metrics,
                conversion_wall_seconds=time.perf_counter()-started)
            candidates.append(candidate)
            print(f'{name}: {count} frames, video {len(packed):,} B, stereo bundle {total:,} B ({total/1048576:.3f} MiB), native {validation["frames"]} frames OK', flush=True)
    eligible = [row for row in candidates if row['within_budget']]
    preferred = [row for row in eligible if row['fps']==20] or eligible
    chosen = min(preferred, key=lambda row:(row['quality'], -row['fps'])) if preferred else None
    if chosen:
        images = []
        for sample in sorted((args.output/(chosen['name']+'_native')).glob('*.png')):
            images.append(Image.open(sample).convert('RGB'))
        contact = Image.new('RGB', (256*len(images), 216), '#141414')
        draw = ImageDraw.Draw(contact)
        for number, image in enumerate(images):
            contact.paste(image, (number*256,24))
            draw.text((number*256+4,5), f'{chosen["name"]} sample {number+1}', fill='white')
        contact.save(args.output/'recommended_native_contact.png')
    if source_hash != sha256(args.source) or protected != {str(path):sha256(path) for path in protected_paths}:
        raise ValueError('Read-only source/frozen ROM/approved music changed during conversion')
    report = dict(result='PASS' if chosen else 'SIZE_EXCEEDED', source=dict(file=str(args.source),
        bytes=args.source.stat().st_size, sha256=source_hash, probe=probe),
        format='ZATVID01', header_fields=HEADER_FIELDS, header_bytes=HEADER.size,
        index_record='little-endian uint32 file_offset, byte_length, audio_sample_pts',
        max_index_frames=MAX_FRAMES, max_jpeg_bytes=MAX_FRAME_BYTES, duration_us=duration_us,
        duration_policy=f'All decoded source audio retained. Last image is held through the full {duration_us/1000000:.6f}s container duration; final partial frame is displayed only until that exact endpoint. No cuts or mono conversion.',
        geometry=dict(width=256,height=144,screen_width=256,screen_height=192,x=0,y=24),
        audio=audio, budget_bytes=args.budget_bytes, decoder=decoder_metadata,
        candidates=candidates, recommended=chosen['name'] if chosen else None,
        recommendation_note='Prefer 20fps and the lowest quantizer within budget; 15/30fps alternatives are measured. Actual ARM9 decode throughput must be verified before selecting the player rate.',
        protected_sha256=protected, source_and_protected_files_unchanged=True,
        commands=commands, ffmpeg_version=execute(['ffmpeg','-version']).decode(errors='replace').splitlines()[0])
    (args.output/'conversion_report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(f'{report["result"]}: {len(eligible)}/{len(candidates)} complete stereo bundles <= {args.budget_bytes:,} B; preferred {report["recommended"]}', flush=True)
    return 0 if chosen else 1


if __name__ == '__main__':
    raise SystemExit(main())
