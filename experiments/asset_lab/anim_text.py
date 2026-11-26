"""Original-style lower screen using the existing Fusion Pixel subsets."""
from functools import lru_cache
from pathlib import Path
import sys
import numpy as np
from PIL import Image, ImageDraw

FONT_LAB = Path(__file__).resolve().parents[1] / 'font_lab'
if str(FONT_LAB) not in sys.path: sys.path.insert(0, str(FONT_LAB))
from portrait_preview import load_font, make_portrait
if __package__:
    from .anim_player import RichText
else:
    from anim_player import RichText

FOOTER = ['返回','日志','快进','自动','中英','存档','快速存档','快速读档','设置']


class SceneText:
    def __init__(self, game=None, characters=None):
        self.fonts = {size: load_font(size) for size in (8,12)}
        self.game = Path(game) if game else None; self.characters = characters or {}
        self.diagnostics = set(); self.footer_boxes = []; self._layouts = {}

    @lru_cache(maxsize=4096)
    def glyph(self, char, font=12, scale=1., bold=False, italic=False):
        glyphs, advances, ids = self.fonts[font]
        if char in ids:
            bitmap = glyphs[ids[char]]; advance = advances[char]
        else:
            self.diagnostics.add(f'缺字 U+{ord(char):04X}')
            bitmap = np.zeros((font,font), dtype=np.uint8); bitmap[1:-1,1] = 1; bitmap[1:-1,-2] = 1
            bitmap[1,1:-1] = 1; bitmap[-2,1:-1] = 1; advance = font
        mask = Image.fromarray(bitmap.astype(np.uint8)*255)
        side = max(1, round(font*scale))
        if side != font: mask = mask.resize((side,side), Image.Resampling.NEAREST)
        if bold:
            original = np.asarray(mask); shifted = np.zeros_like(original); shifted[:,1:] = original[:,:-1]
            mask = Image.fromarray(np.maximum(original, shifted))
        if italic:
            mask = mask.transform(mask.size, Image.Transform.AFFINE, (1,-.12,side*.06,0,1,0), Image.Resampling.NEAREST)
        return mask, max(1, round(advance*scale))

    def emit(self, image, text, x, y, *, font=12, color=(255,255,255)):
        for char in text:
            mask, advance = self.glyph(char, font)
            image.paste(color, (round(x),round(y)), mask); x += advance
        return x

    def portrait(self, display):
        if not self.game or not display or display['who'] != 'a': return None
        attributes = display.get('attributes', [])
        mapping = self.characters.get('a', {}).get('attributes', {})
        path = mapping.get(' '.join(attributes))
        if not path and len(attributes) == 1: path = mapping.get(attributes[0])
        if not path: return None
        return self._portrait(str(self.game/path))

    @lru_cache(maxsize=40)
    def _portrait(self, path): return make_portrait(Path(path))[0]

    def layout(self, plan, portrait=False, *, x=31, y=35, width=194, bottom=173, font=12):
        key = (plan.raw, portrait, x,y,width,bottom,font)
        if key in self._layouts: return self._layouts[key]
        pages = [[]]; xpos=x; ypos=y; row_height=font
        def next_row():
            nonlocal xpos,ypos,row_height
            xpos=x; ypos+=row_height+3; row_height=font
        for index,g in enumerate(plan.glyphs):
            char = g['char']; size = g.get('font',font)
            mask, advance = self.glyph(char, size, round(g['scale'],3), g['bold'],g['italic'])
            height = mask.height
            right = min(x+width,188) if portrait and ypos<60 else x+width
            if char != '\n' and xpos > x and xpos+advance > right: next_row()
            if ypos+height > bottom:
                pages.append([]); xpos=x; ypos=y; row_height=font
            pages[-1].append(dict(index=index,glyph=g,x=xpos,y=ypos,width=0 if char=='\n' else advance,height=0 if char=='\n' else height,font=size))
            if char == '\n': next_row()
            else: xpos+=advance; row_height=max(row_height,height)
        if len(self._layouts) > 128: self._layouts.clear()
        self._layouts[key] = pages
        return pages

    def paint_page(self, image, layout, page, visible):
        for item in layout[min(max(0,page),len(layout)-1)]:
            g=item['glyph']
            if item['index'] >= visible or g['char']=='\n': continue
            mask,_ = self.glyph(g['char'],item['font'],round(g['scale'],3),g['bold'],g['italic'])
            image.paste(tuple(g['color']), (item['x'],item['y']), mask)

    def lower(self, display, *, elapsed=0., page=0, bilingual=False, window_visible=True, quick_menu=True, auto=False, auto_delay=1.5):
        image = Image.new('RGB',(256,192),'black'); draw=ImageDraw.Draw(image)
        portrait = self.portrait(display)
        layout=[[]]; visible=0; auto_page=0
        if window_visible:
            draw.rectangle((4,10,251,190),outline='white',width=1)
            who = display['who'] if display else None
            name = '阿莎雅' if who=='a' else ''
            header='------'+('['+name+']' if name else '')
            right = 190 if portrait else 248
            draw.rectangle((7,0,right,19),fill='black')
            end=self.emit(image,header,8,4)
            while end+self.fonts[12][1]['-']<=right: end=self.emit(image,'-',end,4)
            if display and who != 'centered':
                plan = display['text']; source_count=len(plan.glyphs)
                visible = source_count if display['revealed'] else plan.visible(elapsed)
                if bilingual:
                    from copy import copy
                    plan=copy(plan); plan.glyphs=list(plan.glyphs)
                    english=RichText('\n'+display['original'],base_cps=0)
                    for g in english.glyphs: plan.glyphs.append(dict(g,font=8,color=(150,150,150),scale=1.))
                    plan.raw+='\x00'+display['original']
                    if visible==source_count: visible=len(plan.glyphs)
                layout=self.layout(plan,portrait=bool(portrait))
                auto_page = next((i for i,p in reversed(list(enumerate(layout))) if any(v['index']<visible for v in p)),0)
                shown_page = auto_page if display['text'].automatic else min(page,len(layout)-1)
                if auto and not display['text'].automatic:
                    ready=display['ready_at']-display.get('start',0.)-display.get('time_shift',0.) if 'ready_at' in display else display['text'].duration
                    shown_page=min(len(layout)-1,int(max(0.,elapsed-ready)/max(.1,auto_delay)))
                self.paint_page(image,layout,shown_page,visible)
                if portrait: image.paste(portrait,(196,0))
                if len(layout)>1:
                    self.emit(image,f'{shown_page+1}/{len(layout)}',226,169,font=8,color=(130,130,130))
        self.footer_boxes=[]
        if quick_menu:
            width=sum(self.fonts[8][1].get(c,8) for label in FOOTER for c in label)+4*(len(FOOTER)-1)
            xpos=(256-width)//2
            for label in FOOTER:
                end=self.emit(image,label,xpos,181,font=8,color=(150,150,150))
                self.footer_boxes.append((xpos,180,end,191,label)); xpos=end+4
        return image,dict(pages=len(layout),visible=visible,auto_page=auto_page,shown_page=shown_page if display and display['who']!='centered' and window_visible else 0,portrait=bool(portrait))

    def upper_overlay(self, image, display=None, *, elapsed=0., literal=''):
        if literal: plan=RichText(literal,base_cps=0); visible=len(plan.glyphs)
        elif display and display['who']=='centered' and display.get('active',True):
            plan=display['text']; visible=len(plan.glyphs) if display['revealed'] else plan.visible(elapsed)
        else: return image
        layout=self.layout(plan,x=8,y=24,width=240,bottom=168)
        page=next((i for i,p in reversed(list(enumerate(layout))) if any(v['index']<visible for v in p)),0)
        entries=layout[page]
        maxbottom=max((e['y']+e['height'] for e in entries),default=24)
        offset=max(0,(144-(maxbottom-24))//2)
        # Center each authored/wrapped line within the stage.
        rows={}
        for item in entries: rows.setdefault(item['y'],[]).append(item)
        result=image.copy()
        for row in rows.values():
            rowwidth=max((e['x']+e['width'] for e in row),default=8)-8
            shift=(256-rowwidth)//2-8
            for item in row:
                if item['index']>=visible or item['glyph']['char']=='\n':continue
                g=item['glyph'];mask,_=self.glyph(g['char'],item['font'],round(g['scale'],3),g['bold'],g['italic'])
                result.paste(tuple(g['color']),(item['x']+shift,item['y']+offset),mask)
        return result
