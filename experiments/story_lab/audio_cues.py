"""Convert passive main-label play/stop commands into source-located audio IR.

The only compiled-script decoder is inspect_story_passive.py's opcode simulator.
Source GLOBAL/REDUCE/NEWOBJ references remain inert Symbols. No game Python,
pickle class, expression or callable is imported, unpickled, evaluated or run.

The IR references the existing approved catalog; it does not convert audio, edit
musical decisions, package resources or alter the frozen hardware-test ROM.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from decimal import Decimal, DecimalException, ROUND_HALF_EVEN
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GAME = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Z.A.T.O.  I Love the World and Everything In It\game')
DEFAULT_MANIFEST = ROOT/'nds/artifacts/audio_manifest.json'
DEFAULT_OUTPUT = ROOT/'nds/artifacts/full_story_audio_cues.json'
MODULES = ('script', 'ep1', *(f'ep2d{i}' for i in range(1, 8)), 'ep3')
RATE, MASTER_GAIN = 22050, Decimal('0.5')
MAX_FRAME, MAX_GAIN = 0xffffffff, 0x7fffffff
_HEAD = re.compile(r'\s*(play|stop|queue)\b')
_COMMAND = re.compile(r'''\s*(play|stop)\s+(music|sound)(?=\s|["']|$)''')
_LITERAL = re.compile(r'''("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')(.*)\Z''', re.S)
_NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')

_spec = importlib.util.spec_from_file_location('zato_audio_passive_ast', ROOT/'nds/tools/inspect_story_passive.py')
_passive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_passive)
attrs = _passive.attrs


class AudioCueError(ValueError):
    def __init__(self, code, message, location):
        self.diagnostic = dict(location, code=code, message=message)
        super().__init__(f"{location['source_file']}:{location['source_line']} [{location['chapter']}] {code}: {message}; {location['source_command']}")


class AudioCueDiagnostics(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(f'{len(diagnostics)} audio cue conversion diagnostic(s)')


def _positive_decimal(value, what):
    try:
        number = Decimal(str(value))
    except (DecimalException, ValueError) as error:
        raise ValueError(f'{what} is not a static finite number: {value!r}') from error
    if not number.is_finite() or number < 0:
        raise ValueError(f'{what} must be finite and nonnegative: {value!r}')
    return number


class AudioCatalog:
    def __init__(self, manifest):
        if manifest.get('rate') != RATE:
            raise ValueError(f'Approved native audio catalog must use {RATE} Hz')
        self.bindings = {}
        self.assets = {'music': {}, 'sfx': {}}
        for row in manifest['assets']:
            group, index = row['group'], row['index']
            if group not in self.assets or not isinstance(index, int) or index < 0:
                raise ValueError('Invalid audio group or group-local index')
            if index in self.assets[group]:
                raise ValueError(f'Duplicate catalog index {group}:{index}')
            if row['rate'] != RATE or row['channels'] != (2 if group == 'music' else 1):
                raise ValueError(f'Catalog channel/rate mismatch: {group}:{index}')
            if not 0 <= row['loop_start_frame'] < row['loop_end_frame'] <= row['frames'] <= MAX_FRAME:
                raise ValueError(f'Catalog loop bounds invalid: {group}:{index}')
            self.assets[group][index] = row
            aliases = row.get('aliases', [row['source']])
            if row['source'] not in aliases:
                raise ValueError(f'Canonical source missing from aliases: {row["source"]}')
            for alias in aliases:
                key = (group, alias)
                if key in self.bindings:
                    raise ValueError(f'Ambiguous catalog alias {group}:{alias}')
                if alias not in row.get('alias_gains', {}):
                    raise ValueError(f'Approved alias gain is missing: {group}:{alias}')
                gain = _positive_decimal(row['alias_gains'][alias], 'Alias gain')
                self.bindings[key] = (row, gain)
        for group, rows in self.assets.items():
            if set(rows) != set(range(len(rows))):
                raise ValueError(f'Catalog indices must be contiguous within {group}')


def compile_command(command, catalog, *, chapter, source_file, source_line):
    """Compile one static command; non-audio statements return None.

    Explicit loop overrides and play-time replacement fadeout remain faithful IR
    data and carry a runtime requirement when the current frozen API cannot apply
    them directly. All commands actually used by the ten main labels are direct.
    """
    location = dict(chapter=chapter, source_file=source_file,
        source_line=source_line, source_command=command)
    def fail(code, message):
        raise AudioCueError(code, message, location)
    if not isinstance(command, str):
        fail('command_type', 'UserStatement.line is not a static string')
    if not _HEAD.match(command):
        return None
    match = _COMMAND.match(command)
    if not match:
        fail('audio_syntax', 'Only static play/stop on music or sound is supported')
    op, channel = match.groups()
    group, is_music = ('music', True) if channel == 'music' else ('sfx', False)
    tail = command[match.end():].strip()
    cue = dict(location, op=op, channel=channel, group=group,
        runtime_requirements=[], api='audio_scene_play' if op == 'play' else 'audio_scene_stop')
    alias = None
    if op == 'play':
        literal = _LITERAL.fullmatch(tail)
        if not literal:
            fail('asset_expression', 'Audio filename must be one quoted static string')
        try:
            path = ast.literal_eval(literal[1])
        except (SyntaxError, ValueError) as error:
            fail('asset_literal', str(error))
        if not isinstance(path, str) or any(ord(c) < 32 for c in path) or '\\' in path:
            fail('asset_path', 'Audio path must be a plain forward-slash string')
        if path.startswith('/') or '..' in path.split('/') or ':' in path:
            fail('asset_path', 'Audio path must stay relative to the game audio directory')
        alias = path.removeprefix('audio/')
        if (group, alias) not in catalog.bindings:
            fail('unknown_asset', f'No approved {group} asset/alias matches {alias!r}')
        tail = literal[2].strip()
        cue.update(source_audio=path, source_alias=alias)
    tokens = tail.split()
    options, loop = {}, is_music
    index = 0
    while index < len(tokens):
        option = tokens[index]
        if option in ('loop', 'noloop') and op == 'play':
            if 'loop' in options:
                fail('duplicate_option', 'loop/noloop may be specified only once')
            options['loop'] = option
            loop = option == 'loop'
            index += 1
            continue
        allowed = ('volume', 'fadein', 'fadeout') if op == 'play' else ('fadeout',)
        if option not in allowed:
            fail('unknown_option', f'Unsupported {op} modifier {option!r}')
        if option in options:
            fail('duplicate_option', f'{option} may be specified only once')
        if index + 1 >= len(tokens) or not _NUMBER.fullmatch(tokens[index + 1]):
            fail('option_expression', f'{option} requires one static numeric literal')
        try:
            options[option] = _positive_decimal(tokens[index + 1], option)
        except ValueError as error:
            fail('option_range', str(error))
        index += 2
    def frames(option):
        seconds = options.get(option, Decimal(0))
        if seconds > Decimal(MAX_FRAME) / RATE:
            fail('option_range', f'{option} exceeds the native uint32 frame range')
        return int((seconds * RATE).to_integral_value(rounding=ROUND_HALF_EVEN))
    cue.update(fadein_seconds=float(options.get('fadein', 0)),
        fadeout_seconds=float(options.get('fadeout', 0)), fadein_frames=frames('fadein'),
        fadeout_frames=frames('fadeout'))
    if op == 'stop':
        cue['native_args'] = dict(is_music=is_music, fade_frames=cue['fadeout_frames'])
        return cue
    row, alias_gain = catalog.bindings[(group, alias)]
    script_volume = options.get('volume', Decimal(1))
    try:
        gain = script_volume * alias_gain * MASTER_GAIN * 4096
        if gain > MAX_GAIN:
            fail('gain_range', 'Combined script/alias/master gain exceeds native int32 Q12')
        gain_q12 = int(gain.to_integral_value(rounding=ROUND_HALF_EVEN))
    except DecimalException:
        fail('gain_range', 'Combined gain exceeds supported decimal/native range')
    requirements = cue['runtime_requirements']
    if loop != is_music:
        requirements.append('sfx_loop' if loop else 'music_once')
    if cue['fadeout_frames']:
        requirements.append('replacement_fadeout')
    cue.update(index=row['index'], asset_source=row['source'], asset_file=row['file'],
        sample_rate=row['rate'], channels=row['channels'], asset_frames=row['frames'],
        script_volume=float(script_volume), alias_gain=float(alias_gain), master_gain=float(MASTER_GAIN),
        gain_q12=gain_q12, loop=loop, loop_mode='explicit' if 'loop' in options else 'channel_default',
        loop_start_frame=row['loop_start_frame'], loop_end_frame=row['loop_end_frame'],
        native_args=dict(is_music=is_music, index=row['index'], gain_q12=gain_q12,
            offset_frames=0, fade_frames=cue['fadein_frames']))
    return cue


def _compiled_bytes(data, path):
    if data[:10] != b'RENPY RPC2':
        raise ValueError(f'Unsupported compiled-script header: {path}')
    for offset in range(10, min(200, len(data) - 11), 12):
        slot, start, size = struct.unpack_from('<III', data, offset)
        if slot == 0:
            break
        if slot == 1:
            if start + size > len(data):
                raise ValueError(f'Truncated AST slot: {path}')
            return _passive.read(zlib.decompress(data[start:start + size]))
    raise ValueError(f'No passive AST slot: {path}')


def read_compiled(path):
    """Return inert Symbols using the mandated passive opcode reader only."""
    return _compiled_bytes(Path(path).read_bytes(), path)


def _main_chain(game):
    labels, sources = {}, []
    for module in MODULES:
        path = game/(module + '.rpyc')
        data = path.read_bytes()
        objects = _compiled_bytes(data, path)
        sources.append(dict(file=path.name, sha256=hashlib.sha256(data).hexdigest()))
        for node in objects:
            if node.cls != 'renpy.ast.Label':
                continue
            name = attrs(node).get('name')
            if not isinstance(name, str) or name in labels:
                raise ValueError(f'Invalid/duplicate passive AST label: {name!r}')
            labels[name] = (module, node)
    result, links, visited = [], [], set()
    current = 'start'
    while current is not None:
        if current not in labels or current in visited:
            raise ValueError(f'Missing or cyclic main-label jump: {current!r}')
        visited.add(current)
        module, label = labels[current]
        block = attrs(label).get('block')
        if not isinstance(block, list):
            raise ValueError(f'Main label has no passive block: {current}')
        active, target = [], None
        for event_index, node in enumerate(block):
            state = attrs(node)
            if node.cls in ('renpy.ast.If', 'renpy.ast.While', 'renpy.ast.Menu', 'renpy.ast.Call'):
                raise ValueError(f'{current}:{state.get("linenumber")}: unsupported main-label control flow {node.cls}')
            active.append((event_index, node))
            if node.cls == 'renpy.ast.Return':
                break
            if node.cls == 'renpy.ast.Jump':
                if state.get('expression') or not isinstance(state.get('target'), str):
                    raise ValueError(f'{current}:{state.get("linenumber")}: dynamic jump is not statically reachable')
                target = state['target']
                links.append(dict(chapter=current, target=target, source_file=state.get('filename'), source_line=state.get('linenumber')))
                break
        result.append(dict(chapter=current, module=module, events=active, source_event_count=len(block)))
        current = target
    expected = ['start', 'ep1', *(f'ep2d{i}' for i in range(1, 8)), 'ep3']
    if [chapter['chapter'] for chapter in result] != expected:
        raise ValueError('Reachable labels do not match the audited ten-chapter main chain')
    return result, links, sources


def build_audio_ir(game=DEFAULT_GAME, manifest_path=DEFAULT_MANIFEST):
    game, manifest_path = Path(game), Path(manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    catalog = AudioCatalog(manifest)
    chapters, links, sources = _main_chain(game)
    cues, diagnostics, chapter_sources = [], [], []
    for chapter in chapters:
        name, first = chapter['chapter'], len(cues)
        for event_index, node in chapter['events']:
            if node.cls != 'renpy.ast.UserStatement':
                continue
            state = attrs(node)
            try:
                cue = compile_command(state.get('line'), catalog, chapter=name,
                    source_file=state.get('filename', f'game/{chapter["module"]}.rpy'),
                    source_line=state.get('linenumber'))
            except AudioCueError as error:
                diagnostics.append(error.diagnostic)
                continue
            if cue is not None:
                if not isinstance(cue['source_line'], int) or cue['source_line'] <= 0:
                    raise ValueError(f'Audio command lacks a valid source line: {cue}')
                cue.update(cue_id=len(cues), chapter_cue_index=len(cues)-first,
                    source_event_index=event_index, compiled_source_file=chapter['module'] + '.rpyc')
                cues.append(cue)
        chapter_sources.append(dict(chapter=name, module=chapter['module'],
            source_event_count=chapter['source_event_count'], first_cue=first, cues=len(cues)-first))
    if diagnostics:
        raise AudioCueDiagnostics(diagnostics)
    return dict(format='ZATO_AUDIO_CUE_IR', version=1, result='PASS',
        method='inspect_story_passive opcode simulation only; GLOBAL/REDUCE/NEWOBJ remain inert; no source execution or pickle class loading',
        rate=RATE, master_gain=float(MASTER_GAIN), chapters=[c['chapter'] for c in chapters],
        sources=sources, chapter_sources=chapter_sources, main_jumps=links,
        approved_manifest=dict(file=str(manifest_path), sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            music_edit_profile_sha256=manifest.get('music_edit_profile_sha256')),
        loop_semantics='music defaults to approved intro-once/loop-region playback; sound defaults to one-shot; explicit loop/noloop is retained',
        summary=dict(cues=len(cues), by_chapter=dict(Counter(c['chapter'] for c in cues)),
            by_operation=dict(Counter(c['op'] + '_' + c['group'] for c in cues)),
            unique_commands=len({c['source_command'] for c in cues}),
            direct_native_cues=sum(not c['runtime_requirements'] for c in cues),
            alias_gain_adjusted_plays=sum(c.get('alias_gain', 1) != 1 for c in cues)),
        diagnostics=[], cues=cues)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, default=DEFAULT_GAME)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.game.resolve()) or args.output.resolve() == args.manifest.resolve():
        parser.error('Output must not overwrite the read-only original game or approved manifest')
    try:
        report = build_audio_ir(args.game, args.manifest)
    except AudioCueDiagnostics as error:
        print(json.dumps(dict(result='FAIL', diagnostics=error.diagnostics), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(args.output)
    print(f'PASS {len(report["chapters"])} reachable main labels, {len(report["cues"])} audio cues, no diagnostics: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
