"""Small nonblocking player for the audited ep2d3 preview, not a Ren'Py VM."""
from bisect import bisect_right
from copy import deepcopy
import re


class PresentationClock:
    """60 Hz presentation timestamps; retain every fractional elapsed tick."""
    def __init__(self,hz=60):self.step=1/hz;self.remainder=0.
    def consume(self,dt):
        total=self.remainder+max(0.,dt)
        frames=int(total/self.step+1e-9)
        elapsed=frames*self.step;self.remainder=max(0.,total-elapsed)
        return elapsed


class RichText:
    def __init__(self, raw, previous='', base_cps=30):
        self.raw = previous + raw
        self.glyphs = []; self.times = []; self.duration = 0.; self.automatic = False
        self.diagnostics = [];self.pauses=[]
        cps = base_cps; color = (255, 255, 255); bold = italic = False; size = 1.
        stacks = {'cps': [], 'color': [], 'size': []}
        at = 0;clock=0.;last_time=0.;segment_has_glyph=False
        while at < len(self.raw):
            inherited = at < len(previous)
            c = self.raw[at]
            if self.raw.startswith(('{{', '[['), at): at += 2
            elif c == '{' and '}' in self.raw[at:]:
                end = self.raw.index('}', at); tag = self.raw[at+1:end]; at = end + 1
                if tag.startswith('cps='):
                    stacks['cps'].append(cps); cps = float(tag[4:])
                elif tag == '/cps': cps = stacks['cps'].pop() if stacks['cps'] else base_cps
                elif tag.startswith('w=') and not inherited:
                    begin=last_time if segment_has_glyph else clock
                    clock=begin+float(tag[2:]);last_time=clock;segment_has_glyph=False
                    self.duration=max(self.duration,clock)
                    self.pauses.append(dict(start=begin,end=clock,glyphs=len(self.glyphs)))
                elif tag == 'nw' and not inherited: self.automatic = True
                elif tag.startswith('color='):
                    stacks['color'].append(color); value = tag[6:].lstrip('#')
                    if len(value) == 3: value = ''.join(x*2 for x in value)
                    color = tuple(int(value[i:i+2], 16) for i in (0,2,4))
                elif tag == '/color': color = stacks['color'].pop() if stacks['color'] else (255,255,255)
                elif tag.startswith('size='):
                    stacks['size'].append(size)
                    value = tag[5:]; size = max(.5, (30+float(value))/30 if value[:1] in '+-' else float(value)/30)
                elif tag == '/size': size = stacks['size'].pop() if stacks['size'] else 1.
                elif tag in ('b', '/b'): bold = tag == 'b'
                elif tag in ('i', '/i'): italic = tag == 'i'
                elif tag not in ('w', 'nw') and not tag.startswith('w='): self.diagnostics.append('未实现文字标签 {'+tag+'}')
                continue
            else: at += 1
            stamp=0. if inherited else last_time if c=='\n' else clock
            if not inherited and c!='\n':
                last_time=clock;segment_has_glyph=True
                self.duration=max(self.duration,last_time)
                if cps>0:clock+=1/cps
            self.glyphs.append(dict(char=c, color=color, bold=bold, italic=italic, scale=size))
            self.times.append(stamp)
        self.text = ''.join(g['char'] for g in self.glyphs)

    def visible(self, elapsed): return bisect_right(self.times, max(0., elapsed) + 1e-9)


class ScenePlayer:
    def __init__(self, story, audio=None, require_present=False):
        self.story = story; self.events = story['events']; self.audio = audio
        self.auto = False; self.auto_delay = 1.5; self.diagnostics = []; self._quiet = False
        self.require_present=require_present
        self.reset(); self._pump()

    def reset(self):
        if self.audio: self.audio.reset()
        self.cursor = 0; self.now = 0.; self.line = 0; self.layers = []; self.display = None
        self.blocked = False; self.wait_until = None; self.wait_hard = False; self.done = False
        self.transition = None; self.old_layers = None; self.stage_text = ''
        self.window_visible = True; self.quick_menu = True; self.music = None
        self.asya_attributes = []; self.history = []; self.nvl_raw = ''; self.nvl_original = ''
        self._awaiting_presentation=False
        self.presentation_serial=getattr(self,'presentation_serial',0)+1
        self._quiet = True
        for event in self.story.get('opening_setup', []): self._apply(event, replay=True)
        self._quiet = False; self.wait_until = None; self.display = None; self.blocked = False

    def seek(self, line):
        self.reset(); self._quiet = True
        while self.cursor < len(self.events) and self.events[self.cursor]['line'] < line:
            self._apply(self.events[self.cursor], replay=True); self.cursor += 1
        self.wait_until = None; self.blocked = False; self.transition = None; self.old_layers = None
        self._quiet = False
        if self.audio and self.music:
            self.audio.play('music', self.music['name'], volume=self.music['volume'],
                            fadein=self.music.get('fadein',0),offset_seconds=max(0.,self.now-self.music['start']))
        self._pump()

    def _audio(self, method, *args, **kwargs):
        if self.audio and not self._quiet: getattr(self.audio, method)(*args, **kwargs)

    def _wait(self, seconds, replay, hard=False):
        if replay: self.now += seconds
        else: self.wait_until = self.now + seconds; self.wait_hard = hard

    def _visual(self):
        if self.old_layers is None: self.old_layers = deepcopy(self.layers)
        self._awaiting_presentation=True
        self.presentation_serial+=1

    def _apply(self, e, replay=False):
        self.line = e['line']; kind = e['kind'].lower()
        if kind in ('show', 'scene', 'hide'):
            self._visual(); name = e.get('image', '')
            if name == 'text' or name.startswith('text '):
                self.stage_text = '' if kind == 'hide' else e.get('literal', e.get('text', e.get('display_text', name[5:].strip('"'))))
                return
            if kind == 'scene': self.layers = []; self.stage_text = ''
            tag = name.split()[0] if name else ''
            self.layers = [x for x in self.layers if x['name'].split()[0] != tag]
            if kind != 'hide':
                layer = dict(name=name, start=self.now, properties=deepcopy(e.get('properties', {})), at=e.get('at', []), behind=e.get('behind', []))
                targets = set(layer['behind'])
                index = next((i for i,x in enumerate(self.layers) if x['name'].split()[0] in targets), len(self.layers))
                self.layers.insert(index, layer)
        elif kind == 'say':
            who = e.get('who'); previous = ''; previous_original = ''
            if who == 'extend' and self.display:
                previous = self.display['raw']; previous_original = self.display['original']; who = self.display['who']
            elif who == 'n' and self.nvl_raw:
                previous = self.nvl_raw + '\n'; previous_original = self.nvl_original + '\n'
            raw = e.get('text', ''); original = e.get('original', ''); text = RichText(raw, previous=previous)
            if who == 'a' and e.get('attributes'): self.asya_attributes = e['attributes']
            self.display = dict(text=text, raw=previous+raw, original=previous_original+original, who=who, source=e, start=self.now, revealed=False, active=not replay, presented_count=-1, presented_at=float('-inf'), pause_index=0,time_shift=0.,attributes=list(self.asya_attributes) if who == 'a' else [])
            self._awaiting_presentation=not replay
            self.presentation_serial+=1
            if who == 'n': self.nvl_raw = previous+raw; self.nvl_original = previous_original+original
            if who != 'centered': self.window_visible = True
            self.history.append(dict(line=e['line'], text=raw, who=who, original=original))
            self.diagnostics.extend(x for x in text.diagnostics if x not in self.diagnostics)
            self.blocked = not replay; self.old_layers = None
            if replay and text.automatic: self.now += text.duration
        elif kind == 'wait': self._wait(float(e.get('seconds', 0)), replay, e.get('hard', False))
        elif kind in ('with', 'transition'):
            expr = e.get('expr', e.get('value', 'None'))
            pause = re.fullmatch(r'Pause\(\s*([\d.]+)\s*\)', str(expr)); dissolve = re.fullmatch(r'Dissolve\s*\(\s*([\d.]+)\s*\)', str(expr))
            seconds = float(pause[1]) if pause else float(dissolve[1]) if dissolve else .5 if expr == 'dissolve' else 0.
            if seconds and not pause and not replay: self.transition = dict(old=deepcopy(self.old_layers or []), start=self.now, duration=seconds)
            if seconds: self._wait(seconds, replay)
            elif expr not in ('None', None): self.diagnostics.append(f'未实现转场：{expr}')
            self.old_layers = None
        elif kind == 'command': self._command(e.get('value', e.get('command', e.get('text', ''))), replay)
        elif kind == 'python':
            code = e.get('code', e.get('value','')); pause = re.fullmatch(r'renpy\.pause\s*\(\s*([\d.]+),\s*hard=True\)', code)
            if pause: self._wait(float(pause[1]), replay)
            elif code.startswith('quick_menu = '): self.quick_menu = code.endswith('True')
            else: self.diagnostics.append('未实现 Python：'+code)
        else: self.diagnostics.append(f'未实现事件：{kind} @ {self.line}')

    def _command(self, command, replay=False):
        play = re.fullmatch(r'play\s+(music|sound)\s*["\'](.+?)["\'](.*)', command); stop = re.fullmatch(r'stop\s+(music|sound)(.*)', command)
        if play:
            group = 'music' if play[1] == 'music' else 'sfx'; name = play[2].removeprefix('audio/')
            gain = re.search(r'volume\s+([\d.]+)', play[3]); fade = re.search(r'fadein\s+([\d.]+)', play[3])
            volume = float(gain[1]) if gain else 1.; seconds = float(fade[1]) if fade else 0.
            if group == 'music': self.music = dict(name=name, volume=volume,start=self.now,fadein=seconds)
            self._audio('play', group, name, volume=volume, fadein=seconds)
        elif stop:
            group = 'music' if stop[1] == 'music' else 'sfx'; fade = re.search(r'fadeout\s+([\d.]+)', stop[2])
            if group == 'music': self.music = None
            self._audio('stop', group, fadeout=float(fade[1]) if fade else 0.)
        elif command.startswith('pause '): self._wait(float(command[6:].strip('() ')), replay)
        elif command.startswith('window '): self.window_visible = command.split()[1] != 'hide'
        elif command == 'nvl clear': self.nvl_raw = ''; self.nvl_original = ''; self.display = None; self.blocked = False
        elif command in ('nvl hide', 'nvl show'): self.window_visible = command == 'nvl show'
        elif command.startswith(('show screen date', 'hide screen date')):
            if not replay: self.diagnostics.append('日期画面尚未接入此预览：'+command)
        else: self.diagnostics.append('未实现命令：'+command)

    def _pump(self):
        for _ in range(4096):
            if self.wait_until is not None or self.blocked: return
            if self.cursor >= len(self.events): self.done = True; return
            event = self.events[self.cursor]; self.cursor += 1; self._apply(event)
        raise RuntimeError('Preview instruction budget exhausted')

    def elapsed(self):
        if not self.display:return 0.
        d=self.display;elapsed=max(0.,self.now-d['start']-d.get('time_shift',0.))
        if self.require_present and not d['revealed'] and d['pause_index']<len(d['text'].pauses):
            pause=d['text'].pauses[d['pause_index']]
            elapsed=min(elapsed,max(pause['start'],pause['end']-1e-6))
        return elapsed

    def reveal(self):
        if self.display:
            self.display['revealed'] = True;self.display['ready_at']=self.now
            self.display['pause_index']=len(self.display['text'].pauses)

    def mark_presented(self,visible_count=None,serial=None,present_time=None):
        """Acknowledge the actual rendered frame, never a predicted text state."""
        if serial is not None and serial!=self.presentation_serial:return
        self._awaiting_presentation=False
        if self.display and self.display.get('active',True):
            count=len(self.display['text'].glyphs) if self.display['revealed'] else self.display['text'].visible(self.elapsed())
            self.display['presented_count']=max(self.display.get('presented_count',-1),count if visible_count is None else visible_count)
            self.display['presented_at']=self.now if present_time is None else present_time

    def _end_interaction(self):
        if self.display and self.display['who']=='centered':self.display['active']=False
        self.wait_until=None;self.blocked=False

    def _deadline(self):
        if self.wait_until is not None:return self.wait_until
        if self.blocked and self.display:
            d=self.display;text=d['text']
            if self.require_present and not d['revealed'] and d['pause_index']<len(text.pauses):
                return d['start']+d['time_shift']+text.pauses[d['pause_index']]['end']
            if text.automatic or self.auto:
                pages=self.display.get('page_count',1)
                ready=d.get('ready_at',d['start']+d['time_shift']+text.duration)
                return ready+(0. if text.automatic else self.auto_delay*pages)
        return None

    def advance(self):
        if self.wait_until is not None:
            if self.wait_hard: return False
            self._end_interaction();self._pump(); return True
        if self.display and not self.display['revealed'] and self.elapsed() < self.display['text'].duration:
            elapsed=self.elapsed()
            d=self.display
            if self.require_present and d['pause_index']<len(d['text'].pauses):
                pause=d['text'].pauses[d['pause_index']]
                if elapsed<pause['start']-1e-9:d['start']-=pause['start']-elapsed
                else:
                    local=self.now-d['start']-d['time_shift']
                    d['start']-=max(0.,pause['end']-local)
                    d['pause_index']+=1;self.presentation_serial+=1;self._awaiting_presentation=True
                return False
            for pause in self.display['text'].pauses:
                if pause['end']>elapsed+1e-9:
                    target=pause['start'] if elapsed<pause['start']-1e-9 else pause['end']
                    self.display['start']-=target-elapsed
                    return False
            self.reveal(); return False
        self._end_interaction();self._pump(); return True

    def tick(self, dt):
        target = self.now + max(0., dt)
        if self.require_present:
            # Keep the real clock, but start newly issued events at the
            # current presentation time, never replay several overdue cues.
            self.now=target
            deadline=self._deadline()
            if not self._awaiting_presentation and deadline is not None and deadline<=target+1e-9:
                if self.blocked and self.display:
                    d=self.display
                    pause=d['text'].pauses[d['pause_index']] if not d['revealed'] and d['pause_index']<len(d['text'].pauses) else None
                    required=pause['glyphs'] if pause else len(d['text'].glyphs)
                    if d.get('presented_count',-1)<required:return
                    # Like the original timed interaction, its endpoint must
                    # have appeared in a frame before the following sound.
                    if self.display.get('presented_at',float('-inf'))+1e-9<deadline:return
                    if pause:
                        # {w} and final {nw} are separate interactions in the
                        # source engine; present the next segment before its
                        # own completion can trigger a following audio cue.
                        d['pause_index']+=1;d['time_shift']+=max(0.,self.now-deadline)
                        self.presentation_serial+=1;self._awaiting_presentation=True
                        return
                self._end_interaction();self._pump()
            if self.transition and self.now>=self.transition['start']+self.transition['duration']:self.transition=None
            return
        for _ in range(4096):
            deadline = self._deadline()
            if deadline is None or deadline > target + 1e-9: break
            self.now = max(self.now, deadline);self._end_interaction();self._pump()
        else: raise RuntimeError('Preview time budget exhausted')
        self.now = target
        if self.transition and self.now >= self.transition['start']+self.transition['duration']: self.transition = None
