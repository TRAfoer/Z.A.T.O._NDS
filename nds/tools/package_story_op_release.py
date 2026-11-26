"""Package a verified integrated OP release; publish only with --publish.

No conversion, compilation, emulator, music edit or font/credits edit is done.
Every integrated proof must identify the supplied ROM. Existing releases are
never replaced, and ZIP payloads are checked byte for byte before publication.
"""
from pathlib import Path
import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile

from verify_scene_package import files as rom_files


ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / 'nds/artifacts'
FORBIDDEN = {'.sav', '.dsv', '.img'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def locate(value, directory):
    path = Path(value)
    choices = [path] if path.is_absolute() else [directory / path, ROOT / path]
    for choice in choices:
        if choice.is_file():
            return choice.resolve()
    raise ValueError(f'Required input not found: {value}')


def host_tests(path):
    text = Path(path).read_text(encoding='utf8')
    matches = list(re.finditer(r'^Ran\s+(\d+)\s+tests?\s+in\s+([\d.]+)s\s*$', text, re.M))
    require(bool(matches), f'No unittest test count in {path}')
    last = matches[-1]
    require(int(last[1]) > 0 and re.search(r'^OK(?:\s*\([^\n]*\))?\s*$', text[last.end():], re.M),
            f'Last unittest run did not finish OK: {path}')
    require(not re.search(r'^(?:FAILED|ERROR|FAIL):?\s', text[last.end():], re.M), f'Failed test summary: {path}')
    return dict(tests=int(last[1]), seconds=float(last[2]), log_sha256=sha(path))


def video_state(proof):
    state = proof.get('final_state', proof.get('final', {}))
    require(isinstance(state.get('video'), dict), 'Proof lacks final native video state')
    require(isinstance(state['video'].get('late_frames'), int), 'Proof lacks measured late_frames')
    return state['video']


def inventory(directory):
    return {p.relative_to(directory).as_posix(): dict(bytes=p.stat().st_size, sha256=sha(p))
            for p in sorted(directory.rglob('*')) if p.is_file()}


def verified_copy(source, destination):
    source, destination = Path(source), Path(destination)
    require(source.suffix.lower() not in FORBIDDEN and destination.suffix.lower() not in FORBIDDEN,
            f'Test storage must not enter a release: {source}')
    expected = sha(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    require(sha(source) == expected == sha(destination), f'Copy/source changed: {source}')


def check_inputs(args):
    expected = args.expected_sha.lower()
    require(re.fullmatch(r'[0-9a-f]{64}', expected), '--expected-sha must be a full SHA256')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.release_name), 'Use a plain release directory name')
    require(re.fullmatch(r'[a-z0-9_]+', args.proof_prefix), 'Use a lowercase proof prefix without path separators')
    require(sha(args.rom) == expected, 'Candidate ROM SHA mismatch')
    out, credits = args.video_output.resolve(), args.credits_output.resolve()
    report = read(out / 'report.json')
    movie = read(out / 'movie_manifest.json')
    credit_meta = read(credits / 'credits_update.json')
    require(report['status'] == 'VERIFIED', 'Video conversion/native-player report is not VERIFIED')
    require(report['no_added_line_breaks'] is True and report['audio_channels'] == 2, 'Line-break/stereo contract changed')
    require(report.get('protected_files_unchanged') is True, 'Missing read-only-source preservation proof')
    require(movie.get('source_read_only') is True and movie.get('approved_music_unchanged') is True,
            'Movie manifest does not preserve approved read-only sources/audio')
    require(tuple(movie[k] for k in ('width', 'height', 'screen_width', 'screen_height', 'x', 'y')) == (256, 144, 256, 192, 0, 24),
            'Movie must occupy the complete centered 256x144 upper screen')
    require(movie['audio_channels'] == 2 and movie['frames'] == report['frame_count'], 'Movie/report frame or stereo mismatch')
    require(movie['fps'] == report['fps'] and movie['duration_us'] == report['duration_us'], 'Movie/report timing mismatch')
    require(report['large_text_font']['sha256'] == credit_meta['font_sha256'], 'Font differs from the supplied credits')
    tests = host_tests(out / 'host_suite_final.log')
    prefix = args.proof_prefix
    proof_paths = {
        'package.json': ART / 'story_package_verification.json',
        'video_pause_and_skip.json': ART / f'{prefix}_video.json',
        'video_uninterrupted.json': ART / f'{prefix}_uninterrupted.json',
        'credits.json': ART / f'{prefix}_credits.json',
        'cold_save.json': ART / f'{prefix}_cold_save/report.json',
        'cold_load.json': ART / f'{prefix}_cold_load/report.json',
        'video_player_full.json': locate(report.get('native_full', 'native_player/full/verification.json'), out),
        'video_player_skip.json': locate(report.get('native_skip', 'native_player/skip/verification.json'), out),
    }
    proofs = {name: read(path) for name, path in proof_paths.items()}
    require(all(p.get('result') == 'PASS' for p in proofs.values()), 'Every required verification must PASS')
    package = proofs['package.json']
    require(package['sha256'] == expected and package['bytes'] == args.rom.stat().st_size, 'Package proof is for another ROM')
    for name in ('video_pause_and_skip.json', 'video_uninterrupted.json', 'credits.json'):
        require(proofs[name]['rom_sha256'] == expected, f'{name} is for another ROM')
    for name in ('cold_save.json', 'cold_load.json'):
        require(proofs[name]['identity']['rom_sha256'] == expected, f'{name} is for another ROM')
    for phase in ('save', 'load'):
        extracted = ART / f'{prefix}_cold_{phase}/extracted.sav'
        require(extracted.is_file() and extracted.stat().st_size == 524288, f'Missing real 512KiB FAT payload: {extracted}')
        require(sha(extracted) == proofs[f'cold_{phase}.json']['details']['saved']['save_sha256'], 'Extracted FAT payload/proof mismatch')
    require(proofs['cold_save.json']['details']['saved']['save_sha256'] == proofs['cold_load.json']['details']['saved']['save_sha256'],
            'Cold-load proof does not read the saved FAT payload')
    require(proofs['cold_save.json']['image_sha256'] == proofs['cold_load.json']['image_sha256'], 'Cold load changed the FAT image')
    require(proofs['credits.json']['identity']['menu_sha256'] == credit_meta['new_menu_sha256'], 'Credits proof/menu input mismatch')
    embedded = rom_files(args.rom)
    require(len(embedded) == package['files'], 'NitroFS file count differs from package proof')
    for name, payload in package['payloads'].items():
        require(name in embedded and len(embedded[name]) == payload['bytes'] and
                hashlib.sha256(embedded[name]).hexdigest() == payload['sha256'], f'ROM payload proof mismatch: {name}')
    require(hashlib.sha256(embedded['story/menu.bin']).hexdigest() == credit_meta['new_menu_sha256'], 'ROM lacks current font credits')
    require(hashlib.sha256(embedded['story/script.bin']).hexdigest() == credit_meta['story_identity'], 'Story/save identity changed')
    for name in ('movie/op.zpv', 'movie/op.wav'):
        source = locate(movie['embedded_files'][name], out)
        require(embedded[name] == source.read_bytes(), f'ROM/movie manifest payload mismatch: {name}')
        require(movie['payloads'][name]['sha256'] == sha(source), f'Movie payload metadata mismatch: {name}')
    require(package['payloads']['movie/op.zpv']['sha256'] == report['video_sha256'], 'Conversion report is for another video')
    require(package['payloads']['movie/op.wav']['sha256'] == report['audio_sha256'], 'Conversion report is for another soundtrack')
    cases = proofs['video_pause_and_skip.json'].get('cases', [])
    require({'natural', 'handoff'} <= {case.get('case') for case in cases}, 'Missing integrated pause/skip cases')
    for case in cases + [proofs['video_uninterrupted.json']]:
        require(case['result'] == 'PASS' and case['rom_sha256'] == expected, 'Nested playback proof is for another ROM')
        hashes = {entry['sha256'] for entry in case['embedded']}
        require({report['video_sha256'], report['audio_sha256']} <= hashes, 'Playback proof uses another video/audio')
    stress = next(case for case in cases if case['case'] == 'natural')
    uninterrupted = proofs['video_uninterrupted.json']
    for name, proof in (('uninterrupted', uninterrupted), ('pause stress', stress)):
        state = video_state(proof)
        require(state['decoded'] == state['displayed'] == movie['frames'] and state['finished'] and not state['errors'],
                f'{name} did not finish all movie frames')
    standalone = proofs['video_player_full.json']
    require(standalone['seen_frame_count'] == movie['frames'] and standalone['lower_screen_black_throughout'], 'Incomplete standalone OP proof')
    test_rom = locate(report['test_rom'], out)
    test_sha = sha(test_rom)
    for name in ('video_player_full.json', 'video_player_skip.json'):
        require(proofs[name]['rom_sha256'] == test_sha, f'{name} uses another standalone player ROM')
    test_payloads = rom_files(test_rom)
    require(all(test_payloads[name] == embedded[name] for name in ('movie/op.zpv', 'movie/op.wav')),
            'Standalone verification ROM contains another OP/soundtrack')
    require(report['native_decoder']['frames'] == movie['frames'], 'Native decoder did not validate every frame')
    script = read(ART / 'story_script_manifest.json')
    require(script['sha256'] == package['payloads']['story/script.bin']['sha256'], 'Story inventory is for another script')
    preview = args.preview.resolve() if args.preview else locate(report['preview'], out)
    plan = {
        'ZATO_Story_Chain.nds': args.rom.resolve(), 'preview/Opening.mp4': preview,
        'CREDITS.txt': credits / 'credits_updated.txt',
        'preview/Font_Credits.png': credits / 'ISAS_FangSong_Credits_Preview.png',
        'preview/Font_Credits_In_Game.png': ART / f'{prefix}_credits.png',
        'preview/Title.png': ART / f'{prefix}_uninterrupted_screens/title_after_natural.png',
        'preview/Cold_Load.png': ART / f'{prefix}_cold_load/cold_restored_after_two_commits.png',
        'verification/host_suite.log': out / 'host_suite_final.log',
        'verification/video_conversion.json': out / 'report.json',
        'verification/movie_manifest.json': out / 'movie_manifest.json',
        'verification/credits_resources.json': credits / 'credits_update.json',
        'licenses/ISAS-1988.txt': ROOT / 'experiments/video_lab/fonts/isas-fangsong-16/COPYING',
        'licenses/Fusion-Pixel-OFL-1.1.txt': ROOT / 'experiments/font_lab/out/LICENSE-OFL',
    }
    plan.update({'verification/' + name: path for name, path in proof_paths.items()})
    comparison = out / 'glyph_motion_comparison.mp4'
    if comparison.is_file():
        plan['preview/Glyph_Effect_Comparison.mp4'] = comparison
    licenses = sorted((args.blocksds / 'licenses').glob('*.txt'))
    require(bool(licenses), 'BlocksDS component license directory is missing')
    plan.update({'licenses/BlocksDS/' + path.name: path for path in licenses})
    for relative in ('libs/libnds/licenses/LICENSE', 'libs/libnds/licenses/LICENSE.FatFs', 'libs/maxmod/COPYING',
                     'sys/arm7/main_core/COPYING', 'sys/crts/COPYING.zlib', 'sys/crts/COPYING.mpl2', 'sys/dldi_r4/LICENSE'):
        plan['licenses/BlocksDS/' + relative.replace('/', '_')] = args.blocksds / relative
    for path in plan.values():
        require(path.is_file(), f'Required release input is missing: {path}')
        require(path.suffix.lower() not in FORBIDDEN, f'Excluded test storage: {path}')
    require(sha(plan['preview/Font_Credits_In_Game.png']) == proofs['credits.json']['screenshot_sha256'], 'Credits screenshot/proof mismatch')
    captures = uninterrupted.get('captures', [])
    title = next((item for item in captures if item.get('label') == 'title_after_natural'), None)
    require(title is not None, 'Uninterrupted proof has no Title capture')
    require(locate(title['path'], ROOT) == plan['preview/Title.png'].resolve(), 'Title screenshot is not the proved capture')
    metrics = dict(host_tests=tests, decoder_frames=report['native_decoder']['frames'], seek_checks=report['native_decoder']['seek_checks'],
                   standalone_late_frames=video_state(standalone)['late_frames'],
                   uninterrupted_late_frames=video_state(uninterrupted)['late_frames'],
                   pause_stress_late_frames=video_state(stress)['late_frames'],
                   chapters=len(script['chapters']), say_count=sum(c['text_count'] for c in script['chapters']),
                   nitrofs_files=package['files'], approved_catalog_audio_files_unchanged=package['audio_files_unchanged'])
    return dict(expected=expected, report=report, movie=movie, credits=credit_meta, proofs=proofs, proof_paths=proof_paths,
                plan=plan, metrics=metrics, sources={str(p.resolve()): sha(p) for p in set(plan.values())})


def readme(args, data):
    m, movie = data['metrics'], data['movie']
    return f'''Z.A.T.O. NDS 完全包 — {args.release_name}

运行 ZATO_Story_Chain.nds；当前{m['chapters']}章/{m['say_count']}条对白、图像、音乐和开场均已内置。
本轮字幕保留原片构图、换行、字号层级和时序，增加逐字灰白故障/抖动效果；
沿原视频已有的文字变化窗口细分逐字运动与闪动。原生16px ISAS仿宋字体与现有致谢保持。
开场完整256×144居中于上屏，上下各24px黑边；下屏全黑。
自然结束或A/START跳过后进入标题。

存档为ROM旁同名ZATO_Story_Chain.sav，512KiB；保留自己的旧档，本包不附测试存档。
烧录卡需支持homebrew和DLDI/FAT；无可写存储时游戏提示保存不可用。
A/正文触摸：推进；上下：滚动；START：菜单；SELECT/底栏：快捷菜单；
B：回看；X：中英；L：暂停；R：自动。设置中的左右切章默认关闭。

ROM实际容量{args.rom.stat().st_size:,}字节；按最新要求只记录，不设64,000,000字节门槛。
批准的立体声音频保持；本次未应用新的手调音乐标记。
preview/Opening.mp4是有声双屏预览；其余preview文件为游戏截图与真实C字体致谢预览。
SHA256SUMS.txt校验文件；manifest.json与verification记录本轮实际证据。

验证：{m['host_tests']['tests']}项主机测试；原生解码{m['decoder_frames']}帧/{m['seek_checks']}次seek；
主程序无暂停播放{movie['frames']}帧，迟帧{m['uninterrupted_late_frames']}次；
多暂停压力播放迟帧{m['pause_stress_late_frames']}次，另有暂停/跳过及普通双屏交接验证。
独立播放器迟帧{m['standalone_late_frames']}次；原生字体致谢导航、真实FAT跨进程512KiB存读档通过。
本轮是开场及相关衔接的增量验证，未重新跑全部{m['say_count']}句剧情，未验证实体NDS/烧录卡读速。
'''


def make_archive(release):
    archive = release.parent / (release.name + '.zip')
    require(not archive.exists(), f'Archive already exists: {archive}')
    paths = [p for p in sorted(release.rglob('*')) if p.is_file()]
    require(not any(p.suffix.lower() in FORBIDDEN for p in paths), 'Test storage entered release')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in paths:
            output.write(path, release.name + '/' + path.relative_to(release).as_posix())
    with zipfile.ZipFile(archive) as check:
        require(check.testzip() is None, 'ZIP CRC validation failed')
        require(set(check.namelist()) == {release.name + '/' + p.relative_to(release).as_posix() for p in paths}, 'ZIP file inventory differs')
        for path in paths:
            with check.open(release.name + '/' + path.relative_to(release).as_posix()) as packed, path.open('rb') as original:
                while True:
                    chunk = original.read(1024 * 1024)
                    require(packed.read(len(chunk)) == chunk, f'ZIP byte mismatch: {path}')
                    if not chunk:
                        require(not packed.read(1), f'ZIP trailing data: {path}')
                        break
    archive_hash = sha(archive)
    archive.with_name(archive.name + '.sha256').write_text(archive_hash + '  ' + archive.name + '\n', encoding='utf8')
    return archive, archive_hash


def backup(path, directory):
    digest = sha(path)
    target = directory / f'{path.stem}_{digest}{path.suffix}'
    if target.exists():
        require(sha(target) == digest, f'Existing backup differs: {target}')
    else:
        verified_copy(path, target)
    return target, digest


def replace_copy(source, destination):
    descriptor, name = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        verified_copy(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def publish(args, data, release):
    root_rom = ROOT / 'ZATO_Story_Chain.nds'
    default = ROOT / 'experiments/video_lab/out/movie_manifest.json'
    backups = args.release_root / 'backups'
    backups.mkdir(parents=True, exist_ok=True)
    previous, previous_sha = backup(root_rom, backups)
    old_manifest, old_manifest_sha = backup(default, backups)
    movie = copy.deepcopy(data['movie'])
    movie['integrated_rom'] = dict(result='PASS', rom=str(root_rom), rom_bytes=args.rom.stat().st_size, rom_sha256=data['expected'])
    movie.setdefault('evidence', {}).update(integrated_uninterrupted=str(data['proof_paths']['video_uninterrupted.json'].resolve()),
        integrated_pause_skip=str(data['proof_paths']['video_pause_and_skip.json'].resolve()),
        credits=str(data['proof_paths']['credits.json'].resolve()), cold_save=str(data['proof_paths']['cold_save.json'].resolve()),
        cold_load=str(data['proof_paths']['cold_load.json'].resolve()))
    descriptor, name = tempfile.mkstemp(prefix='movie_manifest_publish_', suffix='.json', dir=backups)
    os.close(descriptor)
    staged_manifest = Path(name)
    try:
        write(staged_manifest, movie)
        require(sha(root_rom) == previous_sha and sha(default) == old_manifest_sha, 'Publication targets changed after backup')
        try:
            replace_copy(release / 'ZATO_Story_Chain.nds', root_rom)
            replace_copy(staged_manifest, default)
            require(sha(root_rom) == data['expected'] and read(default) == movie, 'Published payload mismatch')
        except BaseException:
            replace_copy(previous, root_rom)
            replace_copy(old_manifest, default)
            raise
    finally:
        staged_manifest.unlink(missing_ok=True)
    return dict(published=True, root_rom=str(root_rom), default_movie_manifest=str(default),
                previous_rom=str(previous), previous_rom_sha256=previous_sha,
                previous_movie_manifest=str(old_manifest), previous_movie_manifest_sha256=old_manifest_sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--video-output', type=Path, required=True, help='Conversion output, e.g. experiments/video_lab/out/text_animated')
    parser.add_argument('--release-name', required=True, help='New directory/ZIP name; existing packages are never replaced')
    parser.add_argument('--proof-prefix', required=True, help='Artifact prefix, e.g. op_glyph_release')
    parser.add_argument('--credits-output', type=Path, default=ROOT / 'experiments/video_lab/out/text_restored')
    parser.add_argument('--preview', type=Path, help='Override report.json preview path')
    parser.add_argument('--release-root', type=Path, default=ROOT / 'releases')
    parser.add_argument('--blocksds', type=Path, default=Path('C:/msys64/opt/wonderful/thirdparty/blocksds/core'))
    parser.add_argument('--publish', action='store_true', help='After full ZIP verification, back up and update root ROM/default movie manifest')
    args = parser.parse_args()
    args.rom, args.video_output, args.release_root = args.rom.resolve(), args.video_output.resolve(), args.release_root.resolve()
    data = check_inputs(args)
    release = args.release_root / args.release_name
    require(not release.exists() and not (release.parent / (release.name + '.zip')).exists(), 'Choose a new release name; existing packages remain intact')
    release.mkdir(parents=True)
    for name, source in data['plan'].items():
        verified_copy(source, release / name)
    (release / 'README.txt').write_text(readme(args, data), encoding='utf-8-sig')
    font = data['report']['large_text_font']
    (release / 'licenses/SOURCES.txt').write_text(
        f"Current OP large font: {font['name']}, native {font['size']}px\n"
        f"{font['copyright_notice']}\nSource: {font['source_url']}\nLicense: {font['license']}\n"
        f"Original BDF/COPYING: {font['license_url']}\nUnmodified font SHA256: {font['sha256']}\n"
        'Fusion Pixel Font / TakWolf: https://github.com/TakWolf/fusion-pixel-font (SIL OFL1.1)\n'
        'BlocksDS component licenses: https://blocksds.skylyrac.net/docs/guides/licenses/\n'
        'Other original-game and tool acknowledgments remain in CREDITS.txt.\n', encoding='utf8')
    manifest = dict(result='PASS', kind='full integrated story program', date=datetime.date.today().isoformat(),
        rom=dict(file='ZATO_Story_Chain.nds', bytes=args.rom.stat().st_size, sha256=data['expected']),
        actual_size_only=True, size_policy='Record actual bytes only; no hard 64,000,000-byte gate',
        metrics=data['metrics'], video={k: data['movie'][k] for k in ('frames', 'fps', 'duration_us', 'width', 'height', 'x', 'y', 'audio_channels')},
        text_motion=data['report'].get('text_motion'), font=font, save_identity=data['credits']['story_identity'],
        validation='Current OP and related integration increments; no full-story rerun or physical hardware throughput claim',
        files=inventory(release), file_inventory_scope='Payload files before manifest.json and SHA256SUMS.txt')
    write(release / 'manifest.json', manifest)
    entries = inventory(release)
    (release / 'SHA256SUMS.txt').write_text(''.join(f"{entry['sha256']}  {name}\n" for name, entry in entries.items()), encoding='utf8')
    require(all(sha(Path(path)) == digest for path, digest in data['sources'].items()), 'An input changed during packaging')
    archive, archive_hash = make_archive(release)
    publication = publish(args, data, release) if args.publish else dict(published=False)
    delivery = dict(status='FULL_INTEGRATED' if args.publish else 'FULL_PACKAGE_VERIFIED', rom=str(args.rom),
        rom_bytes=args.rom.stat().st_size, rom_sha256=data['expected'], release=str(release), package=str(archive),
        package_bytes=archive.stat().st_size, package_sha256=archive_hash, actual_size_only=True,
        metrics=data['metrics'], publication=publication, files=inventory(release))
    write(args.video_output / 'delivery_manifest.json', delivery)
    write(args.video_output / 'release_report.json', delivery)
    print(json.dumps({k: v for k, v in delivery.items() if k != 'files'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
