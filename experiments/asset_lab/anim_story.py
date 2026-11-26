"""Read-only ep2d3 story fixture for the desktop animation preview.

Only the repository's inert pickletools reader handles compiled Ren'Py data.
Referenced classes, game Python, ATL expressions, and translation statements are
never imported, unpickled, evaluated, or executed.  The result is JSON data, not
a general Ren'Py interpreter.  Missing translations stay null and diagnostic.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import zlib

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GAME = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Z.A.T.O.  I Love the World and Everything In It\game')
FIRST_LINE, LAST_LINE = 240, 815
SEQUENCE_NAMES = ('hands', 'crack', 'windo', 'ghost', 'tface', 'deface', 'ilooked', 'iss')

_spec = importlib.util.spec_from_file_location('zato_passive_story_reader', ROOT / 'nds/tools/inspect_story_passive.py')
_passive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_passive)
attrs, Symbol = _passive.attrs, _passive.Symbol


def read_compiled(path: Path):
    """Decode the AST slot using an opcode simulator, never pickle.load(s)."""
    data = Path(path).read_bytes()
    if data[:10] != b'RENPY RPC2':
        raise ValueError(f'Unsupported compiled-script header: {path}')
    for offset in range(10, min(200, len(data) - 11), 12):
        slot, start, size = struct.unpack_from('<III', data, offset)
        if slot == 1:
            if start + size > len(data):
                raise ValueError(f'Truncated AST slot: {path}')
            return _passive.read(zlib.decompress(data[start:start + size]))
        if slot == 0:
            break
    raise ValueError(f'No AST slot in {path}')


def _expression(value):
    while isinstance(value, Symbol) and value.cls in ('renpy.ast.PyExpr', 'renpy.ast.PyCode'):
        value = value.args[0] if value.cls == 'renpy.ast.PyExpr' else value.state[1]
    return value


def _literal(value):
    """Parse static literal syntax only; retain unevaluated expressions as text."""
    value = _expression(value)
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    if isinstance(value, (tuple, list)):
        return [_literal(v) if not isinstance(v, str) else v for v in value]
    return value


def _transparent(value):
    """Preserve complete ATL data without plain()'s audit-depth truncation."""
    if isinstance(value, Symbol):
        if value.cls in ('renpy.ast.PyExpr', 'renpy.ast.PyCode'):
            return _expression(value)
        return {'type': value.cls, 'args': _transparent(value.args),
                'state': _transparent(attrs(value))}
    if isinstance(value, (list, tuple)):
        return [_transparent(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _transparent(v) for k, v in value.items() if k != 'next'}
    return value


_SAY = re.compile(r'''^(?P<prefix>[^"']*?)(?P<literal>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')(?:\s+with\s+(?P<with_>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*))?\s*$''')


def _parse_say_statement(text):
    match = _SAY.fullmatch(text.strip())
    if not match:
        return None
    prefix = match.group('prefix').strip().split()
    # Prefixes are speaker/attribute tokens, never Python or Ren'Py commands.
    if prefix and not all(re.fullmatch(r'[A-Za-z_]\w*', token) for token in prefix):
        return None
    try:
        literal = ast.literal_eval(match.group('literal'))
    except (SyntaxError, ValueError):
        return None
    result = {'who': prefix[0] if prefix else None,
              'attributes': prefix[1:], 'text': literal}
    if match.group('with_') is not None:
        result['with_'] = match.group('with_')
    return result


def parse_translations(text: str) -> list[dict]:
    """Read one-to-one English-comment/Chinese-say pairs, retaining locations.

    A block can begin with nvl clear, so its source reference is metadata rather
    than the identity of its dialogue.  Matching uses literal English, speaker,
    attributes and block order.  Unrelated commands/Python are ignored as data.
    """
    rows = []
    reference, identifier, original = None, None, None
    source_file = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        ref = re.fullmatch(r'# (.+\.rpy):(\d+)', line)
        if ref:
            source_file, reference = ref.group(1), int(ref.group(2))
            original = None
            continue
        header = re.fullmatch(r'translate\s+\w+\s+([\w]+):', line)
        if header:
            identifier, original = header.group(1), None
            continue
        if not identifier or not raw[:1].isspace():
            continue
        if line.startswith('# '):
            candidate = _parse_say_statement(line[2:])
            if candidate is not None:
                original = candidate
            continue
        translated = _parse_say_statement(line)
        if translated is not None and original is not None:
            row = {'who': original['who'], 'attributes': original['attributes'],
                         'original': original['text'], 'text': translated['text'],
                         'translated_who': translated['who'],
                         'translated_attributes': translated['attributes'],
                         'translation_id': identifier, 'translation_line': number,
                         'translation_source_line': reference,
                         'translation_source_file': source_file}
            # Add optional fields only when authored, preserving existing slice
            # JSON and source-to-translation mappings byte for byte otherwise.
            if 'with_' in original:
                row['original_with_'] = original['with_']
            if 'with_' in translated:
                row['translated_with_'] = translated['with_']
            rows.append(row)
            original = None
    return rows


def _properties(atl):
    properties, transitions = {}, []
    if atl is None:
        return properties
    for statement in attrs(atl).get('statements', []):
        state = attrs(statement)
        targets = {str(k): _literal(v) for k, v in state.get('properties', [])}
        if not targets:
            continue
        warper = state.get('warper')
        if warper:
            duration = _literal(state.get('duration'))
            transitions.append({'warper': warper, 'duration': duration,
                                'properties': targets, 'source_line': state['loc'][1]})
            if warper == 'easein' and 'zoom' in targets:
                properties['zoom_easein'] = {'from': properties.get('zoom', 1.0),
                    'to': targets['zoom'], 'duration': duration}
        else:
            properties.update(targets)
    if transitions:
        properties['transitions'] = transitions
    return properties


def _sequence(node):
    data = attrs(node)
    frames, repeat = [], False
    for statement in attrs(data['atl']).get('statements', []):
        state = attrs(statement)
        if statement.cls == 'renpy.atl.RawRepeat':
            repeat = True
        elif statement.cls == 'renpy.atl.RawMultipurpose':
            for expression, _with in state.get('expressions', []):
                frames.append({'image': _literal(expression), 'duration': 0.0})
            if state.get('warper') == 'pause':
                if not frames:
                    raise ValueError('ATL pause precedes a frame')
                frames[-1]['duration'] += float(_literal(state['duration']))
    return {'frames': frames, 'repeat': repeat, 'source_line': data['linenumber'],
            'source_file': data['filename'], 'atl': _transparent(data['atl'])}


def _pause_from_python(code):
    """Recognize the single literal renpy.pause call without running it."""
    try:
        tree = ast.parse(code.strip(), mode='exec')
        call = tree.body[0].value if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            return None
        if not isinstance(call.func.value, ast.Name) or call.func.value.id != 'renpy' or call.func.attr != 'pause':
            return None
        if len(call.args) != 1 or any(k.arg != 'hard' for k in call.keywords):
            return None
        seconds = ast.literal_eval(call.args[0])
        hard = next((ast.literal_eval(k.value) for k in call.keywords if k.arg == 'hard'), False)
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and isinstance(hard, bool):
            return {'seconds': float(seconds), 'hard': hard}
    except (SyntaxError, ValueError, TypeError):
        pass
    return None


def _event(node, source_index):
    state = attrs(node)
    kind = node.cls.rsplit('.', 1)[-1].lower()
    event = {'kind': kind, 'line': state['linenumber'],
             'source_file': state['filename'], 'source_index': source_index}
    if kind in ('show', 'scene', 'hide'):
        imspec = state.get('imspec')
        components = list(imspec[0]) if imspec else []
        is_text = components[:1] == ['text']
        event.update(image='text' if is_text else ' '.join(components),
                     at=[_expression(v) for v in imspec[3]] if imspec else [],
                     behind=list(imspec[6]) if imspec else [],
                     properties=_properties(state.get('atl')),
                     image_expression=' '.join(components),
                     source_imspec=_transparent(imspec),
                     atl=_transparent(state.get('atl')))
        if is_text and len(components) > 1:
            event['literal'] = _literal(' '.join(components[1:]))
            event['literal_source'] = ' '.join(components[1:])
        return event
    if kind == 'say':
        event.update(who=state.get('who'), attributes=list(state.get('attributes') or []),
                     temporary_attributes=list(state.get('temporary_attributes') or []),
                     original=state['what'], text=None, translation_id=None,
                     translation_line=None, translation_source_line=None,
                     interact=state.get('interact', True))
        if state.get('with_') is not None:
            event['with_'] = _expression(state['with_'])
        return event
    if kind == 'with':
        event['expr'] = _expression(state['expr'])
        return event
    if kind == 'userstatement':
        value = state['line']
        pause = re.fullmatch(r'pause(?:\s+([0-9]+(?:\.[0-9]+)?))?', value)
        if pause:
            event.update(kind='wait', seconds=float(pause.group(1)) if pause.group(1) else None, hard=False, value=value)
        else:
            event.update(kind='command', value=value)
        return event
    if kind == 'python':
        value = _expression(state['code'])
        pause = _pause_from_python(value)
        if pause:
            event.update(kind='wait', value=value, **pause)
        else:
            event['value'] = value
        return event
    event['data'] = _transparent({k: v for k, v in state.items() if k not in ('next', 'block')})
    return event


def _translation_key(row):
    return row['who'], tuple(row['attributes']), row['original'], row.get('original_with_')


def _match_translations(block, rows):
    """Align from label start so repeated short utterances remain one-to-one."""
    matches, used, cursor = {}, set(), 0
    for index, node in enumerate(block):
        if node.cls != 'renpy.ast.Say':
            continue
        state = attrs(node)
        key = (state.get('who'), tuple(state.get('attributes') or []), state['what'],
               _expression(state['with_']) if state.get('with_') is not None else None)
        candidates = [i for i in range(cursor, len(rows)) if _translation_key(rows[i]) == key]
        if not candidates:
            # An earlier absent entry must not conceal a later exact match.
            candidates = [i for i in range(len(rows)) if i not in used and _translation_key(rows[i]) == key]
        if candidates:
            chosen = candidates[0]
            matches[index] = rows[chosen]
            used.add(chosen)
            cursor = max(cursor, chosen + 1)
    return matches


def _characters(game, script_objects):
    result = {}
    for node in script_objects:
        state = attrs(node)
        if node.cls != 'renpy.ast.Define' or state.get('varname') != 'a':
            continue
        code = _expression(state['code'])
        definition = {'source_file': state['filename'], 'source_line': state['linenumber'], 'expression': code}
        try:
            call = ast.parse(code, mode='eval').body
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'Character':
                definition['name'] = ast.literal_eval(call.args[0])
                for keyword in call.keywords:
                    if keyword.arg in ('image', 'color'):
                        definition[keyword.arg] = ast.literal_eval(keyword.value)
        except (SyntaxError, ValueError, IndexError):
            pass
        side_prefix = 'side ' + definition.get('image', 'as') + ' '
        definition['attributes'] = {
            path.stem[len(side_prefix):]: path.relative_to(game).as_posix()
            for path in sorted((game / 'images').rglob('*.png'))
            if path.stem.startswith(side_prefix)}
        result['a'] = definition
    return result


def build_story(game=DEFAULT_GAME) -> dict:
    """Return the literal ep2d3:240..815 timeline plus silent opening setup."""
    game = Path(game)
    source = game / 'ep2d3.rpyc'
    script = game / 'script.rpyc'
    translation = game / 'tl/chinese/ep2d3.rpy'
    objects = read_compiled(source)
    block = next(attrs(node)['block'] for node in objects
                 if node.cls == 'renpy.ast.Label' and attrs(node).get('name') == 'ep2d3')
    rows = parse_translations(translation.read_text(encoding='utf-8-sig'))
    matches = _match_translations(block, rows)
    diagnostics, events, opening = [], [], []
    for index, node in enumerate(block):
        line = attrs(node).get('linenumber', 0)
        if line > LAST_LINE:
            continue
        if line < FIRST_LINE:
            if node.cls in ('renpy.ast.Show', 'renpy.ast.Scene', 'renpy.ast.Hide'):
                opening.append(_event(node, index))
            elif node.cls == 'renpy.ast.UserStatement' and re.match(r'(?:play music|stop music|queue music|window |nvl |show screen|hide screen)', attrs(node)['line']):
                opening.append(_event(node, index))
            continue
        event = _event(node, index)
        if event['kind'] == 'say':
            match = matches.get(index)
            if match:
                for key in ('text', 'translation_id', 'translation_line', 'translation_source_line', 'translation_source_file'):
                    event[key] = match[key]
            else:
                diagnostics.append({'code': 'missing_translation', 'line': line,
                    'source_file': event['source_file'], 'who': event['who'],
                    'original': event['original'], 'resolved': False})
        elif event['kind'] == 'python':
            diagnostics.append({'code': 'unevaluated_python', 'line': line, 'value': event['value'], 'resolved': False})
        events.append(event)
    sequences = {' '.join(attrs(node)['imgname']): _sequence(node) for node in objects
                 if node.cls == 'renpy.ast.Image' and ' '.join(attrs(node)['imgname']) in SEQUENCE_NAMES}
    transforms = {attrs(node)['varname']: {'source_line': attrs(node)['linenumber'],
                   'source_file': attrs(node)['filename'], 'atl': _transparent(attrs(node)['atl'])}
                  for node in objects if node.cls == 'renpy.ast.Transform'}
    images = {path.stem: path for path in (game / 'images').rglob('*.png')}
    for name, sequence in sequences.items():
        for frame in sequence['frames']:
            if frame['image'] not in images:
                diagnostic = {'code': 'unresolved_image', 'image': frame['image'],
                    'sequence': name, 'line': sequence['source_line'], 'resolved': False}
                if frame['image'] == 'is10' and '10' in images:
                    diagnostic['candidate'] = images['10'].relative_to(game).as_posix()
                    diagnostic['message'] = 'ATL references is10; only 10.png exists. No alias was inferred.'
                diagnostics.append(diagnostic)
    return {'label': 'ep2d3', 'first_line': FIRST_LINE, 'last_line': LAST_LINE,
            'events': events, 'opening_setup': opening, 'sequences': sequences,
            'transforms': transforms, 'characters': _characters(game, read_compiled(script)),
            'checkpoints': {'before': FIRST_LINE, 'effect': 599, 'mirror': 653, 'after': 688, 'window': 789},
            'diagnostics': diagnostics,
            'source_sha256': {'game/' + path.relative_to(game).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (source, script, translation)},
            'method': 'inert pickletools opcode simulation; literal-only translation/ATL parsing; no game Python or pickle execution'}


def save_story(path=ROOT / 'experiments/asset_lab/out/anim_preview/story.json', game=DEFAULT_GAME):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    story = build_story(game)
    path.write_text(json.dumps(story, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return story


if __name__ == '__main__':
    story = save_story()
    print(json.dumps({'events': len(story['events']), 'dialogue': sum(e['kind'] == 'say' for e in story['events']),
                      'sequences': len(story['sequences']), 'diagnostics': story['diagnostics']}, ensure_ascii=False, indent=2))
