"""Read-only-source ep2d3 special-scene compositor, not an NDS runtime.

Every participating image uses the SAME 1280x720 -> 256x144 mapping at (0,24).
This deliberately special composition does not change ordinary preview framing.
Small RGBA textures retain Box coverage; RGB15 quantization follows composition.
"""
from __future__ import annotations

import ast
from collections import OrderedDict
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:
    from .asset_model import DEFAULT_GAME, rgb15
except ImportError:
    from asset_model import DEFAULT_GAME, rgb15

ROOT = Path(__file__).resolve().parents[2]
SEQUENCE_AUDIT = ROOT / 'nds/artifacts/anim_sequence_audit.json'
SOURCE_SIZE = (1280, 720)
STAGE_SIZE = (256, 144)
SCREEN_SIZE = (256, 192)
SHAKING_TARGETS = ((-1., 1.), (1., -1.), (1., -1.), (-1., 1.), (0., 0.))


def _literal(value):
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    return value


def load_sequences(path=SEQUENCE_AUDIT):
    """Read inert audited ATL, never import/evaluate the original game code."""
    result = {}
    for record in json.loads(Path(path).read_text(encoding='utf-8')):
        frames, image, repeat = [], None, False
        for statement in record['atl']['state']['statements']:
            state = statement['state']
            if statement['type'].endswith('.RawRepeat'):
                repeat = True
            for expression, _ in state.get('expressions', []):
                image = _literal(expression)
            if state.get('warper') == 'pause' and image is not None:
                frames.append({'image': image, 'duration': float(state['duration'])})
        result[' '.join(record['name'])] = {'frames': frames, 'repeat': repeat,
                                         'source_line': record['line']}
    return result


def _name(value):
    return ' '.join(value).lower() if isinstance(value, (list, tuple)) else str(value).lower()


def _shake(elapsed):
    """ep2d3:7-12: five linear 0.05s segments, initially (0,0), repeat."""
    phase = max(0., elapsed) % .25
    slot = min(int((phase + 1e-12) / .05), 4)
    amount = min(max((phase - slot * .05) / .05, 0.), 1.)
    previous = SHAKING_TARGETS[slot - 1] if slot else (0., 0.)
    target = SHAKING_TARGETS[slot]
    return tuple(a + (b - a) * amount for a, b in zip(previous, target))


def order_layers(layers):
    """Replay Ren'Py same-zorder insertion, preserving an already ordered list.

    Source: bundled renpy/display/core.py SceneLists.find_index:1015-1053.
    `behind` inserts BEFORE the earliest target in bottom-to-top draw order.
    """
    ordered = []
    for layer in layers:
        tag = _name(layer.get('tag', layer['name'])).split(' ')[0]
        behind = {_name(item) for item in layer.get('behind', [])}
        insert = len(ordered)
        for i, old in enumerate(ordered):
            oldtag = _name(old.get('tag', old['name'])).split(' ')[0]
            if oldtag == tag or oldtag in behind:
                insert = i
                break
        ordered = [old for i, old in enumerate(ordered)
                   if _name(old.get('tag', old['name'])).split(' ')[0] != tag]
        ordered.insert(min(insert, len(ordered)), layer)
    return ordered


class AnimRenderer:
    def __init__(self, game=DEFAULT_GAME, sequences=None, aliases=None,
                 missing_policy='record', cache_size=48, iss_mode='texture'):
        if missing_policy not in ('record', 'black'):
            raise ValueError("missing_policy must be 'record' or 'black'")
        self.game = Path(game)
        if iss_mode not in ('text','texture'):raise ValueError('invalid iss_mode')
        self.iss_mode=iss_mode;self._iss_text=None
        self.sequences = load_sequences() if sequences is None else sequences
        self.aliases = {_name(k): _name(v) for k, v in (aliases or {}).items()}
        self.missing_policy = missing_policy
        self.cache_size = max(1, int(cache_size))
        self._cache = OrderedDict()
        self.index, self.collisions = {}, {}
        # The installed engine's common/00images.rpy:35-50 uses basename.lower().
        # Underscores are NOT replaced with spaces; subdirectories are ignored.
        for path in sorted((self.game / 'images').rglob('*')):
            if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                continue
            key = path.stem.lower()
            if key in self.index:
                self.collisions.setdefault(key, [str(self.index[key])]).append(str(path))
            else:
                self.index[key] = path
        self.metadata = {}

    @property
    def cache_bytes(self):
        return sum(im.width * im.height * 4 for im in self._cache.values())

    def _new_metadata(self, mode):
        self.metadata = {
            'preview_only': True, 'mode': mode, 'iss_mode': self.iss_mode,
            'composition': {'special_segment': True, 'label': 'ep2d3 special segment: full original scene / 5',
                            'source_size': list(SOURCE_SIZE), 'stage_size': list(STAGE_SIZE),
                            'screen_size': list(SCREEN_SIZE), 'position': [0, 24], 'scale': .2},
            'alpha_preserved': mode != 'old',
            'sequenceperiods': {name: math.fsum(float(f['duration']) for f in sequence['frames'])
                                for name, sequence in self.sequences.items()},
            'missing': [], 'aliases_used': [], 'diagnostics': [], 'layers': [],
            'image_name_rule': 'installed renpy/common/00images.rpy: basename stem.lower(); underscores preserved',
            'ordering': 'bottom to top; installed SceneLists.find_index same-zorder behind insertion',
            'transform_source': {'shaking': 'ep2d3.rpy:7-12 inert AST: linear .05s to (-1,1),(1,-1),(1,-1),(-1,1),(0,0), repeat',
                                 'hands': 'ep2d3.rpy:621-624: anchor/pos .5,.5; zoom1.2; easein10s zoom1.3',
                                 'default': 'installed renpy/common/00definitions.rpy:65-70: xpos/xanchor .5, ypos/yanchor 1.0; retain native image bounds',
                                 'easein': 'installed renpy/common/000atl.rpy:48: cos((1-x)*pi/2)'},
            'approximations': ['Pillow premultiplied RGBA Box textures and bilinear affine transforms approximate RenPy GPU sampling; not a fidelity claim'],
        }
        if self.collisions:
            self.metadata['diagnostics'].append({'kind': 'image_name_collisions', 'names': self.collisions})

    def _resolve(self, image, report=True):
        image = _name(image)
        target = self.aliases.get(image, image)
        if target != image and report:
            entry = {'image': image, 'target': target, 'status': 'explicit_candidate_mapping'}
            if entry not in self.metadata['aliases_used']:
                self.metadata['aliases_used'].append(entry)
        if target == 'black' or target in self.index:
            return target
        return None

    def _sample(self, name, elapsed):
        if name not in self.sequences:
            return name, None, 0
        sequence = self.sequences[name]
        frames = sequence['frames']
        period = self.metadata['sequenceperiods'][name]
        if not frames or period <= 0:
            self.metadata['diagnostics'].append({'kind': 'empty_sequence', 'sequence': name})
            return name, frames, 0
        phase = elapsed % period if sequence.get('repeat') else min(elapsed, period)
        if sequence.get('repeat') and period - phase < 1e-10:
            phase = 0.
        end = 0.
        for index, frame in enumerate(frames):
            end += float(frame['duration'])
            if phase < end - 1e-10:
                return _name(frame['image']), frames, index
        return _name(frames[-1]['image']), frames, len(frames) - 1

    def _selected(self, layer, now):
        name = _name(layer['name'])
        elapsed = max(0., float(now) - float(layer.get('start', 0.)))
        requested, frames, index = self._sample(name, elapsed)
        selected = self._resolve(requested)
        if selected is None:
            fallback = 'black'
            if self.missing_policy == 'record' and frames:
                # Deterministic timeline predecessor, independent of render call order.
                # On first cycle do not jump to future frames across the repeat seam.
                for previous in reversed(frames[:index]):
                    candidate = self._resolve(previous['image'], report=False)
                    if candidate:
                        fallback = candidate
                        break
            self.metadata['missing'].append({'layer': name, 'image': requested,
                                             'fallback': fallback, 'policy': self.missing_policy,
                                             'status': 'unresolved_preview_only'})
            self.metadata['diagnostics'].append({'kind': 'missing_image', 'image': requested,
                                                  'message': 'No implicit filename alias; preview fallback is not source fidelity.'})
            selected = fallback
        return name, requested, selected, elapsed

    def _texture(self, image, full=False):
        size = SOURCE_SIZE if full else STAGE_SIZE
        if image == 'black':
            return Image.new('RGBA', size, 'black')
        if not full and image in self._cache:
            self._cache.move_to_end(image)
            return self._cache[image]
        with Image.open(self.index[image]) as opened:
            result = opened.convert('RGBA')
        # Narrow images keep their native scene bounds; resizing all inputs to
        # the scene dimensions would stretch any ordinary preceding sprites.
        if not full:
            result = result.resize((max(1, round(result.width / 5)),
                                    max(1, round(result.height / 5))), Image.Resampling.BOX)
        if not full:
            self._cache[image] = result
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return result

    def _properties(self, layer, elapsed):
        props = {key: _literal(value) for key, value in layer.get('properties', {}).items()}
        zoom = float(props.get('zoom', 1.))
        tween = props.get('zoom_easein')
        if tween:
            fraction = min(max(elapsed / float(tween.get('duration', 10.)), 0.), 1.)
            zoom = float(tween.get('from', 1.2)) + (float(tween.get('to', 1.3)) - float(tween.get('from', 1.2))) * math.cos((1. - fraction) * math.pi / 2.)
        elif _name(layer['name']) == 'hands' and math.isclose(zoom, 1.2):
            zoom = 1.2 + .1 * math.cos((1. - min(elapsed / 10., 1.)) * math.pi / 2.)
        xoffset, yoffset = float(props.get('xoffset', 0.)), float(props.get('yoffset', 0.))
        transforms = [_name(transform) for transform in layer.get('at', [])]
        if 'shaking' in transforms:
            dx, dy = _shake(elapsed)
            xoffset, yoffset = xoffset + dx, yoffset + dy
        for transform in transforms:
            if transform != 'shaking':
                self.metadata['diagnostics'].append({'kind': 'unsupported_transform', 'transform': transform})
        return {'zoom': zoom, 'xzoom': float(props.get('xzoom', 1.)),
                'yzoom': float(props.get('yzoom', 1.)),
                'anchor': props.get('anchor', (.5, 1.)), 'pos': props.get('pos', (.5, 1.)),
                'xoffset_source': xoffset, 'yoffset_source': yoffset,
                'blend': props.get('blend', 'normal'), 'alpha': float(props.get('alpha', 1.))}

    @staticmethod
    def _transform(texture, transform, size, old=False):
        if old:
            texture = texture.copy()
            texture.putalpha(texture.getchannel('A').point(lambda alpha: 255 if alpha >= 128 else 0))
        if transform['xzoom'] < 0:
            texture = ImageOps.mirror(texture)
        if transform['yzoom'] < 0:
            texture = ImageOps.flip(texture)
        sx = max(abs(transform['zoom'] * transform['xzoom']), 1e-8)
        sy = max(abs(transform['zoom'] * transform['yzoom']), 1e-8)
        anchor, pos = transform['anchor'], transform['pos']
        # Ren'Py integer coordinates are pixels, floats are proportional.
        position = [float(v) * size[i] if isinstance(v, float) else float(v) * size[i] / SOURCE_SIZE[i]
                    for i, v in enumerate(pos)]
        anchors = [float(v) * texture.size[i] if isinstance(v, float) else float(v) * size[i] / SOURCE_SIZE[i]
                   for i, v in enumerate(anchor)]
        x = position[0] - anchors[0] * sx + transform['xoffset_source'] * size[0] / SOURCE_SIZE[0]
        y = position[1] - anchors[1] * sy + transform['yoffset_source'] * size[1] / SOURCE_SIZE[1]
        if texture.size != size or not (sx == sy == 1. and x == y == 0.):
            # transform() on RGBA uses Pillow's premultiplied-alpha path.
            texture = texture.transform(size, Image.Transform.AFFINE,
                                        (1. / sx, 0., -x / sx, 0., 1. / sy, -y / sy),
                                        resample=Image.Resampling.BILINEAR)
        if transform['alpha'] != 1.:
            texture = texture.copy()
            opacity = min(max(transform['alpha'], 0.), 1.)
            texture.putalpha(texture.getchannel('A').point(lambda alpha: round(alpha * opacity)))
        return texture

    @staticmethod
    def _blend(destination, source, blend):
        if blend != 'add':
            return Image.alpha_composite(destination, source)
        src = np.asarray(source, dtype=np.uint16)
        dst = np.asarray(destination, dtype=np.uint16)
        contribution = (src[:, :, :3] * src[:, :, 3:4] + 127) // 255
        rgb = np.minimum(dst[:, :, :3] + contribution, 255).astype(np.uint8)
        return Image.fromarray(rgb).convert('RGBA')

    def _compose(self, layers, now, mode, full=False, record=True):
        size = SOURCE_SIZE if full else STAGE_SIZE
        canvas = Image.new('RGBA', size, 'black')
        for layer in order_layers(layers):
            name=_name(layer['name'])
            fitted=name=='iss' and self.iss_mode=='text' and not full and mode!='old'
            if fitted:
                if __package__:
                    from .iss_text import IssText
                else:
                    from iss_text import IssText
                if self._iss_text is None:self._iss_text=IssText()
                elapsed=max(0.,float(now)-float(layer.get('start',0.)))
                requested,_,index=self._sample(name,elapsed)
                selected=f'text:is:{index+1}'
                texture=self._iss_text.render(index)
                self.metadata['iss_text']={k:v for k,v in self._iss_text.metadata.items() if k not in ('words','frames')}
                self.metadata['iss_text'].update(self._iss_text.metadata['frames'][min(index,9)])
            else:
                name, requested, selected, elapsed = self._selected(layer, now)
                texture=self._texture(selected,full=full)
            transform = self._properties(layer, elapsed)
            texture = self._transform(texture, transform, size,
                                      old=mode == 'old')
            if transform['blend'] not in ('normal', 'add'):
                self.metadata['diagnostics'].append({'kind': 'unsupported_blend', 'blend': transform['blend']})
            canvas = self._blend(canvas, texture, transform['blend'])
            if record:
                self.metadata['layers'].append({'name': name, 'requested': requested, 'selected': selected,
                                                'elapsed': elapsed, 'transform': transform})
        if full:
            canvas = canvas.convert('RGB').resize(STAGE_SIZE, Image.Resampling.BOX)
        else:
            canvas = canvas.convert('RGB')
        if mode in ('rgb15', 'old'):
            canvas = Image.fromarray(rgb15(canvas))
        screen = Image.new('RGB', SCREEN_SIZE, 'black')
        screen.paste(canvas, (0, 24))
        return screen

    def render(self, layers, now, mode='rgb15'):
        """Return PIL RGB 256x192; inspect metadata for unresolved source details.

        modes: rgb15 (default), rgba/reference (unquantized coverage), old
        (legacy alpha>=128 demonstration, using this same special composition).
        """
        if mode not in ('rgb15', 'rgba', 'reference', 'old'):
            raise ValueError('unknown compositor mode: ' + mode)
        self._new_metadata(mode)
        result = self._compose(layers, now, mode)
        self.metadata['cache_bytes'] = self.cache_bytes
        return result

    def render_pair(self, layers, now, mode='rgb15'):
        """Return (source-composite-then-Box reference, target composition).

        Full resolution textures are loaded transiently one at a time for the
        independent reference and never enter the bounded small-texture cache.
        """
        output = self.render(layers, now, mode)
        output_metadata = self.metadata
        self._new_metadata('reference')
        reference = self._compose(layers, now, 'reference', full=True, record=False)
        self.metadata = output_metadata
        self.metadata['reference'] = '1280x720 source composition then Box / 5; independent sampling baseline, no RGB15'
        return reference, output
