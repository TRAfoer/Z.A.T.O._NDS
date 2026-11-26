"""Story-only 1bpp glyphs with native shadow/underline style flags.

Language is an explicit property of the text plan, never guessed from its font
or Unicode range. This module owns glyph styling and plain fallback layout;
the story's paired CN/EN line scheduler is a separate module.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import argparse
import hashlib,json,math,re,zipfile
import numpy as np
from PIL import Image,ImageDraw
from pack_scene_text import Compiler,rgb15
from anim_text import SceneText
from portrait_preview import load_font
from anim_player import RichText

SHADOW=0x8000
UNDERLINE=0x4000
INDEX_MASK=0x3fff
MAX_GLYPHS=16383
SHADOW_COLOR=(96,96,96)
TYPOGRAPHY_POLICY={
    'version':1,'normal':'Original native8/12 glyph and advance, unchanged.',
    'enlarge':'Every source scale>1 uses exactly2x native pixels and2x advance; no intermediate size.',
    'shrink':'Source scale<1 steps native12 to10, native10 to8, with native8 as the readability floor.',
    'missing_native_size':'Try native8 then native12 by actual ZIP/BDF coverage when the smaller native size lacks the character; report the chosen fallback.',
    'italic':'Apply nearest-neighbor shear at native8/10/12 size before integer2x enlargement.',
    'bold':'Metadata only: CN right/down1px gray shadow; EN1px underline after scaling.',
}

def font_archive():
    analysis=Path(__file__).resolve().parents[2]/'experiments/font_lab/out/analysis.json'
    return Path(json.loads(analysis.read_text(encoding='utf8'))['font_archive'])

@lru_cache(maxsize=3)
def native_codepoints(size):
    # Subsets deliberately contain some boxed/mixed-size fallbacks. They do
    # not establish native coverage, and an unextended subset can omit a valid
    # character. Resolve against the read-only source BDF instead.
    with zipfile.ZipFile(font_archive()) as archive:
        data=archive.read(f'fusion-pixel-{size}px-monospaced-zh_hans.bdf')
    return frozenset(int(cp) for cp in re.findall(rb'^ENCODING ([0-9]+)\s*$',data,re.M))

@dataclass(frozen=True)
class GlyphMetrics:
    mask: Image.Image
    advance: int
    ink_width: int
    ink_height: int
    flags: int

class StoryTypographyRenderer(SceneText):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fonts[10]=load_font(10)
        self.size_fallbacks={}

    def resolve_font(self,char,font=12,scale=1.):
        if font not in (8,10,12) or not math.isfinite(scale) or scale<=0:
            raise ValueError('invalid native font/scale')
        if scale>=1:return font,2 if scale>1 else 1
        if font==8:return 8,1
        cp=ord(char)
        requested=10 if font==12 else 8
        if cp in native_codepoints(requested):return requested,1
        native=next((size for size in (8,12) if cp in native_codepoints(size)),font)
        self.size_fallbacks[(char,requested,native)]=dict(char=char,codepoint=f'U+{cp:04X}',requested_font=requested,native_font=native,
            reason=f'Character absent from native{requested}px BDF; preserve a real readable native glyph.')
        return native,1

    @lru_cache(maxsize=4096)
    def glyph(self,char,font=12,scale=1.,bold=False,italic=False):
        native,multiplier=self.resolve_font(char,font,scale)
        # In particular, shear BEFORE enlargement. Shearing a24px canvas would
        # move individual output pixels and destroy the uniform2x2 source cells.
        mask,advance=super().glyph(char,native,1.,False,italic)
        if multiplier==2:mask=mask.resize((mask.width*2,mask.height*2),Image.Resampling.NEAREST)
        return mask,advance*multiplier

    @staticmethod
    def style_flags(g):
        language=g.get('language','cn')
        if language not in ('cn','en'):raise ValueError(f'unknown glyph language {language!r}')
        if not g.get('bold',False) or g['char']=='\n':return 0
        return SHADOW if language=='cn' else UNDERLINE

    def measure(self,g,font=12):
        flags=self.style_flags(g)
        if g['char']=='\n':return GlyphMetrics(Image.new('L',(1,1)),0,0,0,0)
        mask,advance=self.glyph(g['char'],font,round(g.get('scale',1.),3),False,g.get('italic',False))
        width=mask.width+int(flags==SHADOW)
        if flags&UNDERLINE:width=max(mask.width,advance)
        return GlyphMetrics(mask,advance,width,mask.height+int(bool(flags)),flags)

    def metrics(self,g,font=12):
        m=self.measure(g,font);return m.ink_width,m.ink_height,m.advance

    def paint_glyph(self,image,g,x,y,font=12):
        m=self.measure(g,font)
        if g['char']=='\n':return 0
        x,y=round(x),round(y);color=tuple(g.get('color',(255,255,255)))
        if m.flags&SHADOW:image.paste(SHADOW_COLOR,(x+1,y+1),m.mask)
        image.paste(color,(x,y),m.mask)
        if m.flags&UNDERLINE:ImageDraw.Draw(image).line((x,y+m.mask.height,x+m.advance-1,y+m.mask.height),fill=color)
        return m.advance

    paint=paint_glyph

    def paint_page(self,image,layout,page,visible):
        if not layout:return image
        for item in layout[min(max(0,page),len(layout)-1)]:
            if item['index']<visible:self.paint_glyph(image,item['glyph'],item['x'],item['y'],item.get('font',12))
        return image

    def layout(self,plan,portrait=False,*,x=20,y=30,width=216,bottom=173,font=12):
        """Plain fallback only. Paired layouts consume measure() directly."""
        fingerprint=tuple((g['char'],g.get('font',font),round(g.get('scale',1.),3),g.get('bold',False),g.get('italic',False),g.get('language','cn'),tuple(g.get('color',(255,255,255)))) for g in plan.glyphs)
        key=('typography',fingerprint,portrait,x,y,width,bottom,font)
        if key in self._layouts:return self._layouts[key]
        pages=[[]];xpos=x;ypos=y;row_height=0
        def next_row():
            nonlocal xpos,ypos,row_height
            xpos=x;ypos+=max(font,row_height)+3;row_height=0
        for index,g in enumerate(plan.glyphs):
            size=g.get('font',font);m=self.measure(g,size);right=min(x+width,188) if portrait and ypos<60 else x+width
            if g['char']!='\n' and xpos>x and xpos+m.ink_width>right:
                next_row();right=min(x+width,188) if portrait and ypos<60 else x+width
            if ypos+m.ink_height>bottom:
                pages.append([]);xpos=x;ypos=y;row_height=0;right=min(x+width,188) if portrait and ypos<60 else x+width
            if g['char']!='\n' and (xpos+m.ink_width>right or ypos+m.ink_height>bottom):raise ValueError('styled glyph cannot fit layout bounds')
            pages[-1].append(dict(index=index,glyph=g,x=xpos,y=ypos,width=m.advance,height=m.ink_height,ink_width=m.ink_width,font=size))
            if g['char']=='\n':next_row()
            else:xpos+=m.advance;row_height=max(row_height,m.ink_height)
        if len(self._layouts)>128:self._layouts.clear()
        self._layouts[key]=pages;return pages

    def upper_overlay(self,image,display=None,*,elapsed=0.,literal=''):
        if literal:plan=RichText(literal,base_cps=0);visible=len(plan.glyphs)
        elif display and display['who']=='centered' and display.get('active',True):
            plan=display['text'];visible=len(plan.glyphs) if display['revealed'] else plan.visible(elapsed)
        else:return image
        layout=self.layout(plan,x=8,y=24,width=240,bottom=168)
        page=next((i for i,entries in reversed(list(enumerate(layout))) if any(e['index']<visible for e in entries)),0)
        rows={};entries=layout[page]
        offset=max(0,(144-(max((e['y']+e['height'] for e in entries),default=24)-24))//2)
        for item in entries:rows.setdefault(item['y'],[]).append(item)
        result=image.copy()
        for row in rows.values():
            span=max((e['x']+e['ink_width'] for e in row),default=8)-8;shift=(256-span)//2-8
            for item in row:
                if item['index']<visible:self.paint_glyph(result,item['glyph'],item['x']+shift,item['y']+offset,item['font'])
        return result

class StoryTypographyCompiler(Compiler):
    def __init__(self,renderer):
        super().__init__(renderer)
        # Keep glyphs=(offset,width,height) for layout/oracle callers. The
        # format4 writer combines this parallel advance with height on wire.
        self.glyph_advances=[]

    def glyph(self,g,font=12):
        flags=self.renderer.style_flags(g)
        if g['char']=='\n':return 65535,0
        mask,advance=self.renderer.glyph(g['char'],font,round(g.get('scale',1.),3),False,g.get('italic',False))
        if not(1<=mask.width<=64 and 1<=mask.height<=64 and 1<=advance<=64):raise ValueError('format4 glyph width/height/advance must be1..64')
        bits=np.packbits((np.asarray(mask)!=0).ravel()).tobytes();key=(mask.width,mask.height,advance,bits)
        if key not in self.glyph_ids:
            if len(self.glyphs)>=MAX_GLYPHS:raise ValueError('glyph count must remain <16384 for LOW14 style flags')
            self.glyph_ids[key]=len(self.glyphs);self.glyphs.append((len(self.atlas),mask.width,mask.height));self.glyph_advances.append(advance);self.atlas.extend(bits)
        return self.glyph_ids[key]|flags,advance

    def layout(self,plan,portrait=False,upper=False):
        pages=self.renderer.layout(plan,portrait=portrait,**(dict(x=8,y=24,width=240,bottom=168) if upper else {}));commands=[]
        for page,entries in enumerate(pages):
            shifts={};offset=0
            if upper:
                offset=max(0,(144-(max((e['y']+e['height'] for e in entries),default=24)-24))//2);rows={}
                for e in entries:rows.setdefault(e['y'],[]).append(e)
                for y,row in rows.items():shifts[y]=(256-(max((e['x']+e['ink_width'] for e in row),default=8)-8))//2-8
            for e in entries:
                g=e['glyph'];gid=self.glyph(g,e['font'])[0];x=e['x']+shifts.get(e['y'],0);y=e['y']+offset
                if gid!=65535:
                    m=self.renderer.measure(g,e['font'])
                    if not(0<=x and x+m.ink_width<=256 and 0<=y and y+m.ink_height<=192):raise ValueError('styled glyph outside screen')
                    if portrait and y<60 and x+m.ink_width>196:raise ValueError('styled glyph intrudes into Asya portrait')
                commands.append((gid,x,y,int(rgb15(g.get('color',(255,255,255)))),e['index'],page,0))
        return commands,len(pages)

def preview(path):
    """Program-drawn font rule comparison, explicitly not a game UI mockup."""
    renderer=StoryTypographyRenderer();legacy=SceneText();image=Image.new('RGB',(384,166));renderer.emit(image,'FONT RULE TEST (NOT GAME UI)',8,3,font=8)
    examples=[('CN / legacy bold','我爱这个世界，ABC','cn',12),('EN / legacy bold','Bold italic, scale 1.0','en',8)]
    for row,(label,text,language,font) in enumerate(examples):
        top=22+row*66;renderer.emit(image,label,8,top,font=8,color=(160,160,160))
        renderer.emit(image,'NEW: SHADOW' if language=='cn' else 'NEW: UNDERLINE',200,top,font=8,color=(160,160,160))
        for left,new in ((8,False),(200,True)):
            x=left
            for char in text:
                g=dict(char=char,language=language,bold=True,italic=language=='en',scale=1.,color=(224,208,160))
                if new:x+=renderer.paint_glyph(image,g,x,top+16,font)
                else:
                    mask,advance=legacy.glyph(char,font,1.,True,g['italic']);image.paste(g['color'],(x,top+16),mask);x+=advance
    renderer.emit(image,'BASE MASK/ADVANCE UNCHANGED; STYLE VIA GID FLAGS',8,153,font=8,color=(160,160,160))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);image.save(path);return path

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--preview',type=Path,required=True);args=parser.parse_args();print(preview(args.preview))
