"""Patch only NitroFS movie/op.zpv, then package/publish a verified successor.

The ARM executables and the remaining resources are copied from the specified
immutable base. No compiler, converter or emulator is invoked by this tool.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import shutil
import struct
import tempfile
import zipfile

from verify_scene_package import files

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 64 * 1024 * 1024
MOVIE = 'movie/op.zpv'
EXCLUDED = {'.sav', '.dsv', '.img'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def crc16(data):
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return value


def layout(data):
    require(len(data) >= 512, 'Truncated NDS')
    fnt, fnt_size, fat, fat_size = struct.unpack_from('<4I', data, 0x40)
    require(fnt_size >= 8 and fat_size and fat_size % 8 == 0 and fnt + fnt_size <= len(data)
            and fat + fat_size <= len(data), 'Invalid FNT/FAT')
    spans = [struct.unpack_from('<2I', data, fat + i * 8) for i in range(fat_size // 8)]
    names, seen = {}, set()
    count = struct.unpack_from('<H', data, fnt + 6)[0]
    def walk(identifier, prefix):
        index = identifier - 0xf000
        require(0 <= index < count and index not in seen, 'Invalid FNT directory')
        seen.add(index)
        offset, file_id, _ = struct.unpack_from('<IHH', data, fnt + index * 8)
        at = fnt + offset
        while True:
            require(fnt <= at < fnt + fnt_size, 'FNT entry outside bounds')
            size = data[at]
            at += 1
            if not size:
                return
            n = size & 127
            require(n and at + n + (2 if size & 128 else 0) <= fnt + fnt_size, 'Invalid FNT name')
            name = data[at:at + n].decode('utf8')
            at += n
            require('/' not in name and '\\' not in name and name not in ('.', '..'), 'Unsafe FNT name')
            if size & 128:
                child = struct.unpack_from('<H', data, at)[0]
                at += 2
                walk(child, prefix + name + '/')
            else:
                require(file_id < len(spans) and prefix + name not in names, 'Invalid FAT file ID')
                names[prefix + name] = file_id
                file_id += 1
    walk(0xf000, '')
    require(len(set(names.values())) == len(spans), 'Require one named file for each FAT entry')
    ordered = sorted((begin, end, i) for i, (begin, end) in enumerate(spans))
    for begin, end, _ in ordered:
        require(0 <= begin < end <= len(data) and begin % 512 == 0, 'Require bounded 512-aligned base files')
    require(all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), 'Overlapping base FAT entries')
    return dict(fnt=fnt, fnt_size=fnt_size, fat=fat, fat_size=fat_size, names=names, spans=spans, ordered=ordered)


def executable(data, offset):
    start, entry, ram, length = struct.unpack_from('<4I', data, offset)
    require(start + length <= len(data), 'ARM executable outside ROM')
    return dict(start=start, entry=entry, ram=ram, bytes=length, sha256=digest(data[start:start + length]))


def video_header(data):
    require(len(data) >= 88 and data[:8] == b'ZATPAL01', 'Expected ZATPAL01 video')
    keys = 'version width height screen_width screen_height x y fps frames duration_us colors bpp raw_bytes families entry_bytes index_at palette_at payload_at file_bytes max_packed'.split()
    h = dict(zip(keys, struct.unpack_from('<20I', data, 8)))
    require(h['file_bytes'] == len(data) and h['entry_bytes'] == 36 and 0 < h['max_packed'] <= 65536, 'Invalid video bounds')
    require(h['payload_at'] == h['palette_at'] + h['families'] * h['colors'] * 2 <= len(data), 'Invalid palette table')
    return h


def compare(base, candidate, expected_base_sha):
    require(sha(base) == expected_base_sha.lower(), 'Immutable baseline SHA mismatch')
    old, new = Path(base).read_bytes(), Path(candidate).read_bytes()
    require(0 < len(new) < LIMIT, f'ROM must be strictly below {LIMIT:,} bytes; got {len(new):,}')
    a, b = layout(old), layout(new)
    require(a['names'] == b['names'] and len(a['names']) == 84, 'Expected the same84 NitroFS file IDs')
    old_files, new_files = files(base), files(candidate)
    differences = [name for name in old_files if old_files[name] != new_files[name]]
    require(differences == [MOVIE], f'Only {MOVIE} may change: {differences}')
    arm = {}
    for name, offset in (('ARM9', 0x20), ('ARM7', 0x30)):
        before, after = executable(old, offset), executable(new, offset)
        require(before == after, f'{name} changed')
        arm[name] = before
    prefix_end = a['ordered'][0][0]
    require(prefix_end == b['ordered'][0][0], 'Executable/FNT prefix moved')
    ignored = set(range(a['fat'], a['fat'] + a['fat_size'])) | set(range(0x80, 0x84)) | {0x15e, 0x15f}
    require(all(old[i] == new[i] for i in range(prefix_end) if i not in ignored), 'Header/banner/executable prefix changed')
    require(struct.unpack_from('<I', new, 0x80)[0] == len(new), 'Header used-ROM length differs')
    require(struct.unpack_from('<H', new, 0x15e)[0] == crc16(new[:0x15e]), 'Header CRC mismatch')
    old_video, new_video = video_header(old_files[MOVIE]), video_header(new_files[MOVIE])
    for field in ('version', 'width', 'height', 'screen_width', 'screen_height', 'x', 'y', 'fps', 'frames', 'duration_us'):
        require(old_video[field] == new_video[field], f'OP geometry/timing changed: {field}')
    return dict(result='PASS', base_rom=str(Path(base).resolve()), base_sha256=expected_base_sha.lower(),
        candidate=str(Path(candidate).resolve()), rom_sha256=sha(candidate), rom_bytes=len(new), limit_bytes=LIMIT,
        remaining_bytes=LIMIT - len(new), growth_bytes=len(new) - len(old), program_unchanged=arm,
        NitroFS_files=84, unchanged_resources=83, changed_resources=differences,
        previous_video=dict(bytes=len(old_files[MOVIE]), sha256=digest(old_files[MOVIE])),
        video=dict(bytes=len(new_files[MOVIE]), sha256=digest(new_files[MOVIE]), header=new_video),
        unchanged_resource_hashes={name: digest(value) for name, value in new_files.items() if name != MOVIE})


def patch(args):
    require(not args.output.exists() and args.output.resolve() != args.base_rom.resolve(), 'Use a fresh independent output ROM')
    require(sha(args.base_rom) == args.expected_base_sha.lower(), 'Immutable baseline SHA mismatch')
    original = args.base_rom.read_bytes()
    table = layout(original)
    require(MOVIE in table['names'], 'Baseline has no OP')
    replacement = args.video.read_bytes()
    video_header(replacement)
    first = table['ordered'][0][0]
    for offset in (0x20, 0x30):
        part = executable(original, offset)
        require(part['start'] + part['bytes'] <= first, 'Executable overlaps NitroFS data')
    require(table['fat'] + table['fat_size'] <= first and table['fnt'] + table['fnt_size'] <= first, 'Tables overlap data')
    result = bytearray(original[:first])
    movie_id = table['names'][MOVIE]
    for begin, end, identifier in table['ordered']:
        result.extend(b'\xff' * (-len(result) % 512))
        start = len(result)
        result.extend(replacement if identifier == movie_id else original[begin:end])
        struct.pack_into('<2I', result, table['fat'] + identifier * 8, start, len(result))
    require(len(result) < LIMIT, f'Candidate is {len(result):,} bytes; must be < {LIMIT:,}')
    struct.pack_into('<I', result, 0x80, len(result))
    struct.pack_into('<H', result, 0x15e, crc16(result[:0x15e]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(result)
    report = compare(args.base_rom, args.output, args.expected_base_sha)
    require(files(args.output)[MOVIE] == replacement, 'Replacement video differs')
    report.update(video_source=str(args.video.resolve()), original_base_unchanged=sha(args.base_rom) == args.expected_base_sha.lower())
    write(args.report, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'unchanged_resource_hashes'}, indent=2))


def copy_file(source, destination):
    require(Path(source).suffix.lower() not in EXCLUDED and Path(destination).suffix.lower() not in EXCLUDED, 'Do not package saves/FAT images')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(sha(source) == sha(destination), f'Copy mismatch: {source}')


def inventory(folder):
    return {p.relative_to(folder).as_posix(): dict(bytes=p.stat().st_size, sha256=sha(p))
            for p in sorted(folder.rglob('*')) if p.is_file()}


def bind_proof(path, candidate_sha):
    proof = read(path)
    require(proof.get('result') == 'PASS', f'Proof did not PASS: {path}')
    identity = proof.get('identity', proof)
    require(identity.get('rom_sha256') == candidate_sha, f'Proof is not for the candidate: {path}')
    for case in proof.get('cases', []) if isinstance(proof.get('cases'), list) else []:
        require(case.get('result') == 'PASS' and case.get('rom_sha256') == candidate_sha, 'Nested video proof mismatch')
    return proof


def video_stats(proof):
    state = proof['final_state']['video']
    require(state['decoded'] == state['displayed'] == state['total_frames'] and state['finished'] and not state['errors'], 'Full OP playback incomplete')
    return {key: state[key] for key in ('decoded', 'displayed', 'total_frames', 'late_frames', 'max_late_samples')}


def package(args):
    checked = compare(args.base_rom, args.candidate, args.expected_base_sha)
    candidate_sha = checked['rom_sha256']
    require(candidate_sha == args.expected_sha.lower(), 'Candidate SHA mismatch')
    baseline = read(args.baseline_release / 'manifest.json')
    require(baseline['rom']['sha256'] == args.expected_base_sha.lower(), 'Baseline release is for another program')
    require(sha(args.baseline_release / 'Z.A.T.O.nds') == args.expected_base_sha.lower(), 'Baseline release ROM differs')
    for name, item in baseline['files'].items():
        path = args.baseline_release / name
        require(sha(path) == item['sha256'] and path.stat().st_size == item['bytes'], f'Baseline file changed: {name}')
    baseline_before = inventory(args.baseline_release)
    prefix = args.proof_prefix
    require(prefix and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in prefix), 'Invalid proof prefix')
    proof_paths = {
        'video_pause_skip': ROOT / f'nds/artifacts/{prefix}_video.json',
        'uninterrupted': ROOT / f'nds/artifacts/{prefix}_uninterrupted.json',
        'runtime': ROOT / f'nds/artifacts/{prefix}_upper_pages/report.json',
        'cold_save': ROOT / f'nds/artifacts/{prefix}_cold_save/report.json',
        'cold_load': ROOT / f'nds/artifacts/{prefix}_cold_load/report.json',
    }
    for override in args.proof:
        role, separator, path = override.partition('=')
        require(separator and role in proof_paths, '--proof requires an existing role=path')
        proof_paths[role] = Path(path)
    proofs = {name: bind_proof(path, candidate_sha) for name, path in proof_paths.items()}
    require(sha(args.map) == proofs['uninterrupted']['map_sha256'], 'Reused link map does not match video proof')
    cases = proofs['video_pause_skip'].get('cases', [])
    require({'natural', 'handoff'} <= {p.get('case') for p in cases}, 'Need full pause-stress and handoff cases')
    metrics = dict(uninterrupted=video_stats(proofs['uninterrupted']),
                   pause_stress=video_stats(next(p for p in cases if p['case'] == 'natural')))
    require(all(value['late_frames'] <= args.max_late_frames for value in metrics.values()), 'Video late-frame policy failed')
    saved, loaded = (proofs[name]['details']['saved']['save_sha256'] for name in ('cold_save', 'cold_load'))
    require(saved == loaded and proofs['cold_save']['image_sha256'] == proofs['cold_load']['image_sha256'], 'FAT cold load changed saved data')
    movie = read(args.movie_manifest)
    resources = files(args.candidate)
    for name in (MOVIE, 'movie/op.wav'):
        source = Path(movie['embedded_files'][name])
        if not source.is_absolute():
            source = args.movie_manifest.parent / source
        require(source.read_bytes() == resources[name], f'Movie manifest differs from ROM: {name}')
    release = args.release_root / args.release_name
    require(args.release_name not in ('.', '..') and '/' not in args.release_name and '\\' not in args.release_name, 'Plain release name required')
    archive = release.parent / (release.name + '.zip')
    require(not release.exists() and not archive.exists(), 'Use a new release directory/archive')
    release.mkdir(parents=True)
    for source in sorted(args.baseline_release.rglob('*')):
        if not source.is_file():
            continue
        name = source.relative_to(args.baseline_release)
        if name.as_posix() in ('Z.A.T.O.nds', 'manifest.json', 'SHA256SUMS.txt'):
            continue
        if name.as_posix() == 'README.txt':
            name = Path('README_previous_version.txt')
        elif name.parts[0] == 'verification':
            name = Path('verification/baseline_v1.0.0') / Path(*name.parts[1:])
        copy_file(source, release / name)
    copy_file(args.candidate, release / 'Z.A.T.O.nds')
    require(sha(release / 'Z.A.T.O.nds') == candidate_sha, 'Candidate changed during packaging')
    copy_file(args.preview, release / 'preview/Opening.mp4')
    copy_file(args.movie_manifest, release / 'verification/movie_manifest.json')
    for role, path in proof_paths.items():
        copy_file(path, release / f'verification/{role}.json')
    for attachment in args.attach:
        name, separator, path = attachment.partition('=')
        require(separator and name and '/' not in name and '\\' not in name and name not in ('.', '..'), '--attach requires filename=path')
        require(not (release / 'verification' / name).exists() and name != 'video_only_patch.json', 'Attachment would replace verified evidence')
        copy_file(Path(path), release / 'verification' / name)
    write(release / 'verification/video_only_patch.json', checked)
    (release / 'README.txt').write_text(f'''Z.A.T.O. NDS v{args.version}

运行Z.A.T.O.nds。此版逐镜头缓解开场视频色带；完整主程序及另外83个资源保持v1.0.0逐字节一致。
原有上屏三页安全切换、UI提示音、147×43署名图与8px间距、文字和存档格式均保留。
开场为上屏完整256×144居中，下屏全黑；保留原文字、字体、灰白故障效果和音轨。
预览：preview/Opening.mp4。旧版本操作说明保存在README_previous_version.txt，其旧容量/哈希/验证属于上一版。

A/正文触摸推进；上下滚动；B回看，A返回当前；START菜单；SELECT/底栏快捷菜单；
X中英；L暂停；R自动。存档为ROM旁同名Z.A.T.O.sav（512KiB）；先备份自己的旧档，本包不含测试存档。

当前ROM：{checked['rom_bytes']:,}字节，严格小于64MiB；余量{checked['remaining_bytes']:,}字节。
SHA256：{candidate_sha}
完整OP及暂停/跳过、普通上屏/菜单/冷载/历史、真实FAT跨进程存读档已按随包证据验证。
自然播放迟帧{metrics['uninterrupted']['late_frames']}；多暂停压力迟帧{metrics['pause_stress']['late_frames']}。
本轮未重新逐句通关全部剧情，也未声称实体NDS/烧录卡吞吐通过。
LICENSE、LICENSES与CREDITS沿用当前正式版；verification/baseline_v1.0.0内证据属于未改的原程序。
''', encoding='utf-8-sig')
    write(release / 'manifest.json', dict(result='PASS', version=args.version, release=release.name,
        rom=dict(file='Z.A.T.O.nds', bytes=checked['rom_bytes'], sha256=candidate_sha),
        base_sha256=args.expected_base_sha.lower(), strict_limit_bytes=LIMIT, metrics=metrics,
        unchanged_resources=83, program_unchanged=checked['program_unchanged'], files=inventory(release)))
    (release / 'SHA256SUMS.txt').write_text(''.join(f"{item['sha256']}  {name}\n" for name, item in inventory(release).items()), encoding='utf8')
    paths = [p for p in sorted(release.rglob('*')) if p.is_file()]
    require(not any(p.suffix.lower() in EXCLUDED for p in paths), 'Release contains test storage')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in paths:
            z.write(path, release.name + '/' + path.relative_to(release).as_posix())
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None, 'ZIP CRC failed')
        require(set(z.namelist()) == {release.name + '/' + p.relative_to(release).as_posix() for p in paths}, 'ZIP inventory differs')
        for path in paths:
            with path.open('rb') as source, z.open(release.name + '/' + path.relative_to(release).as_posix()) as packed:
                while True:
                    chunk = source.read(1024 * 1024)
                    require(packed.read(len(chunk)) == chunk, f'ZIP bytes differ: {path}')
                    if not chunk:
                        require(not packed.read(1), 'ZIP trailing bytes')
                        break
    archive_hash = sha(archive)
    archive.with_name(archive.name + '.sha256').write_text(archive_hash + '  ' + archive.name + '\n', encoding='utf8')
    require(inventory(args.baseline_release) == baseline_before, 'Baseline release changed during packaging')
    publication = dict(published=False)
    if args.publish:
        root_rom, default = ROOT / 'Z.A.T.O.nds', ROOT / 'experiments/video_lab/out/movie_manifest.json'
        require(sha(root_rom) == args.expected_base_sha.lower(), 'Root ROM changed; refusing publication')
        backups = args.release_root / 'backups'
        backups.mkdir(parents=True, exist_ok=True)
        old_rom = backups / f'Z.A.T.O_{sha(root_rom)}.nds'
        old_manifest = backups / f'movie_manifest_{sha(default)}.json'
        for source, target in ((root_rom, old_rom), (default, old_manifest)):
            if target.exists():
                require(sha(source) == sha(target), 'Existing backup differs')
            else:
                copy_file(source, target)
        published_movie = copy.deepcopy(movie)
        published_movie['integrated_rom'] = dict(result='PASS_VIDEO_ONLY_PATCH', rom=str(root_rom), rom_bytes=checked['rom_bytes'], rom_sha256=candidate_sha)
        published_movie.setdefault('evidence', {}).update({role: str(path.resolve()) for role, path in proof_paths.items()})
        try:
            atomic_copy(release / 'Z.A.T.O.nds', root_rom)
            descriptor, temporary = tempfile.mkstemp(prefix='deband_manifest_', suffix='.json', dir=backups)
            os.close(descriptor)
            try:
                write(temporary, published_movie)
                atomic_copy(Path(temporary), default)
            finally:
                Path(temporary).unlink(missing_ok=True)
            require(sha(root_rom) == candidate_sha and read(default) == published_movie, 'Published payload mismatch')
        except BaseException:
            atomic_copy(old_rom, root_rom)
            atomic_copy(old_manifest, default)
            raise
        publication = dict(published=True, previous_rom=str(old_rom), previous_manifest=str(old_manifest))
    report = dict(result='PASS', rom_sha256=candidate_sha, rom_bytes=checked['rom_bytes'], release=str(release.resolve()),
        archive=str(archive.resolve()), archive_bytes=archive.stat().st_size, archive_sha256=archive_hash,
        metrics=metrics, publication=publication, files=inventory(release))
    write(args.report, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'files'}, indent=2))


def atomic_copy(source, destination):
    descriptor, temporary = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
    os.close(descriptor)
    try:
        copy_file(source, Path(temporary))
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--base-rom', type=Path, default=ROOT / 'Z.A.T.O.nds')
    common.add_argument('--expected-base-sha', required=True)
    p = commands.add_parser('patch', parents=[common], help='Generate an independent OP-only FAT repack')
    p.add_argument('--video', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--report', type=Path, default=ROOT / 'nds/artifacts/deband_patch.json')
    p.set_defaults(run=patch)
    p = commands.add_parser('package', parents=[common], help='Verify/package current-program successor; publish explicitly')
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--expected-sha', required=True)
    p.add_argument('--map', type=Path, default=ROOT / 'nds/build/zato_story_chain_upper_pages_v1_nitrofs.map')
    p.add_argument('--baseline-release', type=Path, default=ROOT / 'releases/ZATO_NDS_v1.0.0')
    p.add_argument('--movie-manifest', type=Path, required=True)
    p.add_argument('--preview', type=Path, required=True)
    p.add_argument('--release-root', type=Path, default=ROOT / 'releases')
    p.add_argument('--release-name', default='ZATO_NDS_v1.0.1')
    p.add_argument('--version', default='1.0.1')
    p.add_argument('--proof-prefix', default='deband')
    p.add_argument('--proof', action='append', default=[], help='Override role=path (video_pause_skip/uninterrupted/runtime/cold_save/cold_load)')
    p.add_argument('--attach', action='append', default=[], help='Additional verification filename=path; no save/image payloads')
    p.add_argument('--max-late-frames', type=int, default=0)
    p.add_argument('--report', type=Path, default=ROOT / 'nds/artifacts/deband_delivery.json')
    p.add_argument('--publish', action='store_true')
    p.set_defaults(run=package)
    args = parser.parse_args()
    args.run(args)


if __name__ == '__main__':
    main()
