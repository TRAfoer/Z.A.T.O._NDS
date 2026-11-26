"""Audit-only symbolic pickle reader: never import/call referenced classes.

Handles only the opcodes observed in this game's compiled scripts; rejects others.
This is not a Ren'Py interpreter or a production story compiler.
"""
from pathlib import Path
import collections
import hashlib
import json
import pickletools
import re
import struct
import zlib

ROOT = Path(__file__).resolve().parents[2]
GAME = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Z.A.T.O.  I Love the World and Everything In It\game')


class Symbol:
    def __init__(self, cls, args=()):
        self.cls, self.args, self.state = cls, args, None
        self.items = {}


def read(raw):
    stack, memo, objects = [], {}, []
    marker = object()
    def marked():
        at = len(stack) - 1
        while stack[at] is not marker:
            at -= 1
        values = stack[at + 1:]
        del stack[at:]
        return values
    for op, arg, pos in pickletools.genops(raw):
        name = op.name
        if name == 'PROTO': continue
        if name == 'MARK': stack.append(marker)
        elif name == 'EMPTY_DICT': stack.append({})
        elif name == 'EMPTY_LIST': stack.append([])
        elif name == 'EMPTY_TUPLE': stack.append(())
        elif name in ('BINUNICODE', 'BININT', 'BININT1', 'BININT2', 'LONG1'): stack.append(arg)
        elif name == 'NONE': stack.append(None)
        elif name in ('NEWTRUE', 'NEWFALSE'): stack.append(name == 'NEWTRUE')
        elif name == 'GLOBAL': stack.append(Symbol('GLOBAL', arg))
        elif name in ('BINPUT', 'LONG_BINPUT'): memo[arg] = stack[-1]
        elif name in ('BINGET', 'LONG_BINGET'): stack.append(memo[arg])
        elif name == 'TUPLE': stack.append(tuple(marked()))
        elif name in ('TUPLE1', 'TUPLE2', 'TUPLE3'):
            size = int(name[-1]); values = tuple(stack[-size:]); del stack[-size:]; stack.append(values)
        elif name == 'APPEND':
            value = stack.pop(); stack[-1].append(value)
        elif name == 'APPENDS':
            values = marked(); stack[-1].extend(values)
        elif name == 'SETITEM':
            value, key = stack.pop(), stack.pop()
            target = stack[-1].items if isinstance(stack[-1], Symbol) else stack[-1]
            target[key] = value
        elif name == 'SETITEMS':
            pairs = marked()
            target = stack[-1].items if isinstance(stack[-1], Symbol) else stack[-1]
            target.update(zip(pairs[::2], pairs[1::2]))
        elif name in ('NEWOBJ', 'REDUCE'):
            args, cls = stack.pop(), stack.pop()
            assert isinstance(cls, Symbol) and cls.cls == 'GLOBAL'
            value = Symbol(cls.args.replace(' ', '.'), args)
            objects.append(value); stack.append(value)
        elif name == 'BUILD':
            state = stack.pop(); stack[-1].state = state
        elif name == 'STOP':
            assert len(stack) == 1
            return objects
        else: raise ValueError(f'Unsupported opcode {name} at {pos}')
    raise ValueError('No STOP')


def attrs(obj):
    state = obj.state
    if isinstance(state, dict): return state
    result = {}
    if isinstance(state, tuple):
        for part in state:
            if isinstance(part, dict): result.update(part)
    return result


def plain(value, depth=0):
    if depth > 5: return '<nested>'
    if isinstance(value, Symbol):
        if value.cls == 'renpy.ast.PyExpr':
            return plain(value.args[0], depth + 1)
        if value.cls == 'renpy.ast.PyCode':
            return plain(value.state[1], depth + 1)
        return {'type': value.cls, 'args': plain(value.args, depth + 1)}
    if isinstance(value, (list, tuple)): return [plain(v, depth + 1) for v in value]
    if isinstance(value, dict): return {str(k): plain(v, depth + 1) for k,v in value.items() if k != 'next'}
    return value


def main():
    names = ['script', 'ep1'] + [f'ep2d{i}' for i in range(1,8)] + ['ep3']
    auxiliary = ['jumpepisode','screens','options','album','snowblossom','gui']
    report = {'method': 'pickletools opcode simulation; GLOBAL/NEWOBJ/REDUCE are inert symbols, never imported or invoked. Observed-opcode inventory only, not a runtime.', 'files': []}
    main_counts = collections.Counter()
    commands, python, images, speakers, tags = collections.Counter(), [], [], collections.Counter(), collections.Counter()
    transitions, definitions, labels = collections.Counter(), [], []
    for name in names + auxiliary:
        path = GAME / (name + '.rpyc'); data = path.read_bytes()
        assert data[:10] == b'RENPY RPC2'
        for at in range(10, 200, 12):
            slot, start, size = struct.unpack_from('<III', data, at)
            if slot == 1: raw = zlib.decompress(data[start:start+size]); break
        else: raise ValueError(path)
        objects = read(raw)
        counts = collections.Counter(o.cls for o in objects if o.cls.startswith(('renpy.ast.', 'renpy.atl.')))
        row = dict(file=path.name, sha256=hashlib.sha256(data).hexdigest(), node_counts=counts)
        report['files'].append(row)
        if name not in names: continue
        main_counts.update(counts)
        for obj in objects:
            a = attrs(obj)
            if obj.cls == 'renpy.ast.UserStatement': commands[str(a.get('line'))] += 1
            if obj.cls == 'renpy.ast.With': transitions[str(plain(a.get('expr')))] += 1
            if obj.cls == 'renpy.ast.Python': python.append(dict(file=path.name, line=a.get('linenumber'), code=plain(a.get('code'))))
            if obj.cls == 'renpy.ast.Image': definitions.append(dict(file=path.name, name=plain(a.get('imgname')), code=plain(a.get('code')), atl=plain(a.get('atl'))))
            if obj.cls == 'renpy.ast.Label':
                labels.append(dict(file=path.name, name=a.get('name'), first_events=[dict(kind=v.cls, line=attrs(v).get('linenumber'), command=plain(attrs(v).get('line')), code=plain(attrs(v).get('code'))) for v in a.get('block', [])[:12]]))
            if obj.cls in ('renpy.ast.Show', 'renpy.ast.Hide', 'renpy.ast.Scene'):
                images.append(dict(file=path.name, line=a.get('linenumber'), kind=obj.cls, imspec=plain(a.get('imspec')), atl=plain(a.get('atl'))))
            if obj.cls == 'renpy.ast.Say':
                speakers[str(a.get('who'))] += 1
                tags.update(re.findall(r'\{[^{}]+\}', str(a.get('what'))))
    report.update(main_node_counts=main_counts, main_speakers=speakers, main_user_statements=commands, main_python=python, main_text_tags=tags, main_image_operations=images, main_transitions=transitions, main_image_definitions=definitions, main_labels=labels)
    output = ROOT / 'nds/artifacts/story_source_audit.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(main_node_counts=main_counts, main_speakers=speakers, unique_commands=len(commands), python_nodes=len(python), text_tags=tags, output=str(output)), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
