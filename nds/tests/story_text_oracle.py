"""Independent source-plan/Pillow oracle for the streamed ARM9 text format."""
from copy import copy
from pathlib import Path
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
import zipfile
import numpy as np
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
import pack_story_text as p
from anim_player import RichText
from anim_story import _characters,read_compiled
from anim_text import FOOTER,SceneText
from story_poem_oracle import poem_pages,POEM_RECORDS


def attach_poem_sources(records,ir,game):
    """Reconstruct poem references from original IR + official translation.

    Never take the producer's merged foreign plan as the source of truth.
    """
    events={event['source_event_id']:event for label in ir['labels'] for event in label['events']}
    extra=p.string_pairs(Path(game)/'tl/chinese/extra.rpy')
    references={}
    for id,(ru_id,en_id) in POEM_RECORDS.items():
        if id>=len(records):continue  # Deliberately cropped concurrency fixtures.
        key=(ru_id,en_id)
        if key not in references:
            ru_raw,en_raw=events[ru_id]['literal'],events[en_id]['literal']
            plans={language:RichText(raw,base_cps=0) for language,raw in [('russian_plan',ru_raw),('english_plan',en_raw),('chinese_plan',extra[en_raw])]}
            for language,plan in plans.items():
                for glyph in plan.glyphs:
                    glyph.update(font=8 if language=='english_plan' else 12,language='cn' if language=='chinese_plan' else 'en')
            references[key]=plans
        record=records[id];record['_poem_source']=references[key]
        assert record['plan'].text==references[key]['chinese_plan'].text,('poem CN source',id)
        for name,plan in references[key].items():
            assert record['poem'][name].text==plan.text,('poem source',id,name)


def context_for(ir,report,game):
    records,names,*_=p.prepare_records(ir,game)
    renderer=p.StoryRenderer(game,_characters(Path(game),read_compiled(Path(game)/'script.rpyc')))
    renderer.transparent_attributes={r['attribute'] for r in report['portraits'] if r['transparent']}
    p.extend_fonts(renderer,records,names)
    attach_poem_sources(records,ir,game)
    return dict(records=records,renderer=renderer,names=names)


def upper_pages(renderer,plan,literal=False):
    base=8 if literal and getattr(plan,'compact_literal',False) else 12
    gap=2 if base==8 else 3;pages=[[]];x=8;y=24;height=base
    for index,g in enumerate(plan.glyphs):
        font=g.get('font',12);w,h,advance=measure_glyph(renderer,g,font);newline=g['char']=='\n'
        if not newline and x>8 and x+w>248:x=8;y+=height+gap;height=base
        if y+h>168:pages.append([]);x=8;y=24;height=base
        pages[-1].append(dict(index=index,glyph=g,font=font,x=x,y=y,width=0 if newline else advance,height=0 if newline else h,ink_width=0 if newline else w))
        if newline:x=8;y+=height+gap;height=base
        else:x+=advance;height=max(height,h)
    result=[]
    for entries in pages:
        maxbottom=max((e['y']+e['height'] for e in entries),default=24)
        offset=max(0,(144-(maxbottom-24))//2);rows={};page=[]
        for e in entries:rows.setdefault(e['y'],[]).append(e)
        for row in rows.values():
            width=max((e['x']+e['ink_width'] for e in row),default=8)-8;shift=(256-width)//2-8
            page.extend(dict(e,x=e['x']+shift,y=e['y']+offset) for e in row)
        result.append(page)
    return result


def centered_pages(renderer,plan):return upper_pages(renderer,plan)


def planes_for(renderer,record):
    plan=record['plan'];count=len(plan.glyphs)
    if record['kind']=='literal':
        if 'poem' in record:
            plans=record.get('_poem_source',record['poem'])
            reference=poem_pages(plans['russian_plan'].glyphs,plans['english_plan'].glyphs,plans['chinese_plan'].glyphs,
                lambda glyph,font:measure_glyph(renderer,glyph,font))
            return reference['lower_pages'],[[dict(e,index=e['index']+count) for e in page] for page in reference['upper_pages']]
        cn=upper_pages(renderer,plan,True)
        if 'original_plan' in record:
            bi=[[dict(e,index=e['index']+count) for e in entries] for entries in upper_pages(renderer,record['original_plan'],True)]
        else:bi=cn
    elif record['kind']=='date':
        x,y=record['native_position'];entries=[]
        for index,g in enumerate(plan.glyphs):
            mask,advance=renderer.glyph(g['char'],8)
            entries.append(dict(index=index,glyph=g,font=8,x=x,y=y,width=advance,height=mask.height));x+=advance
        cn=bi=[entries]
    elif record['who']=='centered':cn=bi=centered_pages(renderer,plan)
    else:
        display=dict(who=record['who'],attributes=record['attributes'])
        portrait=renderer.portrait(display) is not None
        english=record.get('english_plan')
        if english is None:
            english=RichText(record['original'],base_cps=0)
            english.glyphs=[dict(g,language='en',font=12 if 0x400<=ord(g['char'])<=0x4ff else 8,color=(150,150,150)) for g in english.glyphs]
        cn,bi=independent_lower_layout(renderer,plan.glyphs,english.glyphs,record.get('segments'),portrait)
    return cn,bi


@lru_cache(maxsize=3)
def native_coverage(font):
    # Read the complete supplied BDF coverage, not the producer's subset or
    # resolve_font policy: a missing subset entry is not a missing source glyph.
    from font_core import parse_bdf
    archive=json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'))['font_archive']
    with zipfile.ZipFile(archive) as z:
        return frozenset(parse_bdf(z.read(f'fusion-pixel-{font}px-monospaced-zh_hans.bdf')).glyphs)


def base_glyph(renderer,g,font):
    # Independently implement the approved discrete contract using native source
    # masks and NumPy pixel repetition, never the story renderer/resolver.
    scale=round(g.get('scale',1.),3);native=font
    if scale<1 and font>8:
        preferred=(10,8,12) if font>=12 else (8,12)
        native=next(size for size in preferred if ord(g['char']) in native_coverage(size))
    mask,advance=SceneText.glyph(renderer,g['char'],native,1.,False,g.get('italic',False))
    if scale>1:
        mask=Image.fromarray(np.repeat(np.repeat(np.asarray(mask),2,axis=0),2,axis=1))
        advance*=2
    return mask,advance


def measure_glyph(renderer,g,font):
    if g['char']=='\n':return 0,0,0
    mask,advance=base_glyph(renderer,g,font)
    shadow=g.get('bold',False) and g.get('language','cn')=='cn'
    underline=g.get('bold',False) and g.get('language','cn')=='en'
    return max(mask.width,advance if underline else 0)+int(shadow),mask.height+int(shadow or underline),advance


def row_thresholds(chinese,english,anchor):
    """Project printed EN letters onto printed CN positions, never blank tails."""
    cn=[entry['index'] for entry in chinese if not entry['glyph']['char'].isspace()]
    en=[i for i,entry in enumerate(english) if not entry['glyph']['char'].isspace()]
    if not en:return [cn[-1]+1 if cn else anchor]*len(english)
    if not cn:stamps=[anchor]*len(en)
    elif len(en)==1:stamps=[cn[-1]+1]
    else:stamps=[cn[j*(len(cn)-1)//(len(en)-1)]+1 for j in range(len(en))]
    result=[];printed=0
    for index in range(len(english)):
        while printed+1<len(en) and en[printed+1]<=index:printed+=1
        result.append(stamps[printed])
    return result


class LayoutPages(list):
    """Oracle pages also carry independently computed logical page caps."""
    def __init__(self,*args,limits=None,pairs=None):
        super().__init__(*args);self.limits=limits;self.pairs=pairs or []


NO_HEAD=set('，。！？；：、,.!?;:%％‰°)]}）］｝】〉》」』〕〗〙〛’”»…')


class SourceLine:
    def __init__(self,renderer,glyphs,font):
        self.glyphs=glyphs;self.chars=[g['char'] for g in glyphs]
        self.metrics=[measure_glyph(renderer,g,g.get('font',font)) for g in glyphs]
        self.font=font

    def hard_end(self,start,end):
        return next((i+1 for i in range(start,end) if self.chars[i]=='\n'),end)

    def blank_break(self,start,end):
        for index in range(start,end):
            if self.chars[index]=='\n':return index+1
            if not self.chars[index].isspace():break
        return None

    def endpoints(self,start,end,y,portrait,english=False,*,left=20,hanging=0,split_closing=False):
        """Vectorized prefix feasibility, separate from compiler's row walker."""
        if start==end:return [(start,0)]
        end=self.hard_end(start,end)
        hard=190 if portrait and y<60 else 248
        normal=166 if portrait and y<60 else 236
        chars=self.chars[start:end];metrics=np.asarray(self.metrics[start:end],dtype=np.int64)
        advances=metrics[:,2].copy()
        for i,char in enumerate(chars):
            if char=='\n':advances[i]=0
        cumulative=np.cumsum(advances);positions=np.r_[0,cumulative[:-1]]+left
        printed=np.array([not char.isspace() for char in chars])
        closing=np.array([char in NO_HEAD for char in chars]);beyond=printed&(positions+metrics[:,0]>normal)
        bad=(printed&(positions+metrics[:,0]>hard))|(beyond&~closing)|(np.cumsum(beyond)+hanging>2)
        invalid=np.flatnonzero(bad);fits=int(invalid[0]) if len(invalid) else len(chars)
        if not fits:
            if left+self.metrics[start][0]>hard:raise AssertionError('Source glyph cannot fit the physical text region')
            fits=1
        all_ends=[(start+i,int(cumulative[i-1])) for i in range(1,fits+1)]
        safe=[item for item in all_ends if split_closing or item[0]==end or self.chars[item[0]] not in NO_HEAD]
        words=[item for item in safe if not english or item[0]==end or self.chars[item[0]-1]=='\n' or
               (self.chars[item[0]-1].isspace() and not self.chars[item[0]].isspace())]
        selected=words or safe or all_ends
        printed_ends=[item for item in selected if any(not self.chars[i].isspace() for i in range(start,item[0]))]
        return printed_ends or selected

    def advance(self,start,end):return sum(self.metrics[i][2] for i in range(start,end) if self.chars[i]!='\n')
    def height(self,start,end):return max((self.metrics[i][1] for i in range(start,end) if self.chars[i]!='\n'),default=self.font)
    def ink(self,start,end):return any(not self.chars[i].isspace() for i in range(start,end))
    def keeps_both(self,choices,start,end):return [item for item in choices if self.ink(start,item[0]) and self.ink(item[0],end)]

    def entries(self,start,end,y,page,portrait,*,left=20):
        hard=190 if portrait and y<60 else 248;x=left;out=[]
        for index in range(start,end):
            g=self.glyphs[index];w,h,advance=self.metrics[index];newline=g['char']=='\n'
            xpos=min(x,hard-w) if g['char'].isspace() and not newline else x
            out.append(dict(index=index,glyph=g,font=g.get('font',self.font),x=xpos,y=y,page=page,
                            width=0 if newline else advance,height=0 if newline else h,ink_width=0 if newline else w))
            if not newline:x+=advance
        return out

    def first_piece_fits(self,start,end,y,portrait,left,hanging,english=False):
        """Carry checks a CN printed glyph / a complete EN token, not a prefix."""
        if start==end:return True
        if self.chars[start]=='\n':return True
        first=next((i for i in range(start,end) if not self.chars[i].isspace()),None)
        stop=end if first is None else first+1
        if first is not None and english:
            while stop<end and not self.chars[stop].isspace():stop+=1
        normal=166 if portrait and y<60 else 236;hard=190 if portrait and y<60 else 248
        for index in range(start,stop):
            char=self.chars[index];w,_,advance=self.metrics[index]
            if not char.isspace():
                if left+w>hard:return False
                if left+w>normal:
                    if english:return False
                    if char not in NO_HEAD or hanging==2:return False
                    hanging+=1
            left+=advance
        return True


def independent_lower_layout(renderer,chinese,english,segments=None,portrait=False):
    """Source-only reference layout; never imports/calls paired_layout.py."""
    cn=SourceLine(renderer,chinese,12);en=SourceLine(renderer,english,8)
    single=[[]];start=0;y=30
    while start<len(chinese):
        end=cn.endpoints(start,len(chinese),y,portrait)[-1][0];height=cn.height(start,end)
        if y+height>173:
            if y==30:raise AssertionError('CN row exceeds the page height')
            single.append([]);y=30;continue
        single[-1].extend(cn.entries(start,end,y,len(single)-1,portrait));start=end;y+=max(15,height+2)
    if segments is None:segments=[dict(source_event_id=None,cn_start=0,cn_end=len(chinese),en_start=0,en_end=len(english))]
    assert [(s['cn_start'],s['en_start']) for s in segments]==[(0,0)]+[(s['cn_end'],s['en_end']) for s in segments[:-1]]
    assert (segments[-1]['cn_end'],segments[-1]['en_end'])==(len(chinese),len(english))
    both=[[]];rows=[];y=30;frontier=None
    def nearest(choices,total,target):
        return min(choices,key=lambda item:(abs(Fraction(item[1],max(1,total))-target),-item[0]))[0]
    for segment in segments:
        c,e=segment['cn_start'],segment['en_start'];cstop,estop=segment['cn_end'],segment['en_end']
        first=True
        while c<cstop or e<estop:
            carry=bool(first and frontier and frontier['open'])
            cblank=cn.blank_break(c,cstop);eblank=en.blank_break(e,estop)
            if cblank is not None or eblank is not None:
                cend=cblank if cblank is not None else c;eend=eblank if eblank is not None else e
                cy=frontier['cy'] if carry else y;ey=frontier['ey'] if carry else cy+max(14,cn.height(c,cend)+1)
                ch,eh=cn.height(c,cend),en.height(e,eend)
                if carry and (cy+max(14,ch+1)>frontier['ey'] or ey+eh>frontier['next']-2):frontier['open']=False;continue
                if ey+eh>173:both.append([]);y=30;frontier=None;continue
                cent=cn.entries(c,cend,cy,len(both)-1,portrait,left=frontier['cx'] if carry else 20)
                eent=en.entries(e,eend,ey,len(both)-1,portrait,left=frontier['ex'] if carry else 20)
                stamps=row_thresholds(cent,eent,c)
                eent=[dict(entry,index=entry['index']+len(chinese),en_index=entry['index'],reveal=stamps[i]) for i,entry in enumerate(eent)]
                both[-1].extend(cent+eent);rows.append(dict(page=len(both)-1,cn_y=cy,en_y=ey,cn=cent,en=eent,source_event_id=segment['source_event_id'],carried=carry))
                y=frontier['next'] if carry else cy+max(25,ch+1+eh+2)
                c,e=cend,eend;first=False;frontier=None;continue
            if carry:
                carry=cn.first_piece_fits(c,cstop,frontier['cy'],portrait,frontier['cx'],frontier['hc']) and en.first_piece_fits(e,estop,frontier['ey'],portrait,frontier['ex'],frontier['he'],True)
            cy=frontier['cy'] if carry else y;cx=frontier['cx'] if carry else 20;ex=frontier['ex'] if carry else 20
            hc=frontier['hc'] if carry else 0;he=frontier['he'] if carry else 0
            cends=cn.endpoints(c,cn.hard_end(c,cstop),cy,portrait,left=cx,hanging=hc) if c<cstop else [(c,0)]
            # Capacity uses the earliest possible EN baseline. A tall CN glyph
            # may later move EN below the portrait; it cannot move EN into it.
            ey=frontier['ey'] if carry else cy+14
            eends=en.endpoints(e,en.hard_end(e,estop),ey,portrait,True,left=ex,hanging=he) if e<estop else [(e,0)]
            ctotal=cn.advance(c,cn.hard_end(c,cstop));etotal=en.advance(e,en.hard_end(e,estop))
            rates=([Fraction(cends[-1][1],max(1,ctotal))] if c<cstop else [])+([Fraction(eends[-1][1],max(1,etotal))] if e<estop else [])
            target=min(rates or [Fraction(1)])
            cend=nearest(cends,ctotal,target);eend=nearest(eends,etotal,target)
            if en.ink(eend,estop) and not cn.ink(cend,cstop):
                options=cn.keeps_both(cends,c,cstop)
                if options:cend=nearest(options,ctotal,target)
            if cn.ink(cend,cstop) and not en.ink(eend,estop):
                options=en.keeps_both(eends,e,estop)
                if options:eend=nearest(options,etotal,target)
            orphan=(cn.ink(cend,cstop) and not en.ink(eend,estop)) or (en.ink(eend,estop) and not cn.ink(cend,cstop))
            if carry and orphan:
                fy=y if y+25<=173 else 30
                fey=fy+max(14,cn.height(c,cstop)+1)
                full_cn=cn.hard_end(c,cstop)==cstop and any(end==cstop for end,_ in cn.endpoints(c,cstop,fy,portrait))
                full_en=en.hard_end(e,estop)==estop and any(end==estop for end,_ in en.endpoints(e,estop,fey,portrait,True))
                if full_cn and full_en:
                    frontier['open']=False;continue
            if cn.ink(cend,cstop) and not en.ink(eend,estop):
                hard_end=en.hard_end(e,estop)
                all_closing=all(char.isspace() or char in NO_HEAD for char in en.chars[e:hard_end])
                chars=en.endpoints(e,hard_end,ey,portrait,left=ex,hanging=he,split_closing=all_closing)
                options=en.keeps_both(chars,e,estop)
                if options:eend=nearest(options,etotal,target)
            ch=cn.height(c,cend);eh=en.height(e,eend)
            if carry and (cy+max(14,ch+1)>frontier['ey'] or frontier['ey']+eh>frontier['next']-2):
                frontier['open']=False;continue
            if not carry:ey=cy+max(14,ch+1)
            if ey+eh>173:
                if cy==30:raise AssertionError('CN/EN pair exceeds page height')
                both.append([]);y=30;frontier=None;continue
            page=len(both)-1;cent=cn.entries(c,cend,cy,page,portrait,left=cx);eent=en.entries(e,eend,ey,page,portrait,left=ex)
            thresholds=row_thresholds(cent,eent,c)
            eent=[dict(entry,index=entry['index']+len(chinese),en_index=entry['index'],reveal=thresholds[i]) for i,entry in enumerate(eent)]
            both[-1].extend(cent+eent)
            rows.append(dict(page=page,cn_y=cy,en_y=ey,cn=cent,en=eent,source_event_id=segment['source_event_id'],carried=carry))
            if carry:ch=max(ch,frontier['ch']);eh=max(eh,frontier['eh'])
            next_y=frontier['next'] if carry else cy+max(25,ch+1+eh+2)
            def hang_count(items):
                return sum(not item['glyph']['char'].isspace() and item['x']+item['ink_width']>(166 if portrait and item['y']<60 else 236) for item in items)
            frontier=dict(cy=cy,ey=ey,cx=cx+cn.advance(c,cend),ex=ex+en.advance(e,eend),ch=ch,eh=eh,next=next_y,
                          hc=hc+hang_count(cent),he=he+hang_count(eent),open=not ((cend>c and cn.chars[cend-1]=='\n') or (eend>e and en.chars[eend-1]=='\n')))
            c,e=cend,eend;y=next_y;first=False
    limits=[next((entry['index'] for later in both[page+1:] for entry in later if entry['glyph'].get('language','cn')!='en'),len(chinese)) for page in range(len(both))]
    return LayoutPages(single),LayoutPages(both,limits=limits,pairs=rows)


def physical_row_timing(record,pages):
    """Audit the real physical row after merging all append-source fragments."""
    merged={};matched=empty=unpaired=0
    for row in pages.pairs:
        cn,en=merged.setdefault((row['page'],row['cn_y'],row['en_y']),([],[]))
        cn.extend(row['cn']);en.extend(row['en'])
    for key,(cn,en) in merged.items():
        cn=[entry for entry in cn if not entry['glyph']['char'].isspace()]
        en=[entry for entry in en if not entry['glyph']['char'].isspace()]
        if not cn and not en:empty+=1;continue
        if not cn or not en:unpaired+=1;continue
        chinese=max(cn,key=lambda entry:entry['index']);english=max(en,key=lambda entry:entry['en_index'])
        cn_tick=record['timeline'][chinese['index']]
        en_tick=record['timeline'][english['reveal']-1] if english['reveal'] else 0
        assert cn_tick==en_tick,('physical row end mismatch',record['id'],record['event']['chapter'],record['event']['line'],key,
                               chinese['index'],cn_tick,english['en_index'],english['reveal'],en_tick)
        matched+=1
    return dict(matched_rows=matched,blank_rows=empty,unpaired_rows=unpaired)


def paint(renderer,image,entries,visible):
    for item in entries:
        g=item['glyph']
        threshold=item.get('reveal',item['index']+1)
        if threshold>visible or g['char']=='\n':continue
        mask,advance=base_glyph(renderer,g,item['font']);x,y=item['x'],item['y']
        if g.get('bold',False) and g.get('language','cn')=='cn':image.paste((96,96,96),(x+1,y+1),mask)
        image.paste(tuple(g['color']),(x,y),mask)
        if g.get('bold',False) and g.get('language','cn')=='en':ImageDraw.Draw(image).line((x,y+mask.height,x+advance-1,y+mask.height),fill=tuple(g['color']))


def background():
    v=(((np.arange(256*192,dtype=np.uint32)*37)&32767)|0x8000).reshape(192,256)
    rgb=np.stack(((v&31),((v>>5)&31),((v>>10)&31)),axis=2).astype(np.uint8)
    return Image.fromarray((rgb<<3)|(rgb>>2))


def lower(renderer,record,cn,bi,visible,page,bilingual,window=True,quick=True):
    image=Image.new('RGB',(256,192));draw=ImageDraw.Draw(image)
    portrait=renderer.portrait(dict(who=record['who'],attributes=record['attributes'])) if record else None
    if window:
        draw.rectangle((4,10,251,190),outline='white',width=1)
        if portrait:draw.line((191,10,251,10),fill='black')
        name=record['header_name'] if record else '';x=44;glyphs=[]
        for char in name:
            mask,advance=base_glyph(renderer,dict(char=char),12);glyphs.append((mask,x,advance));x+=advance
        if glyphs:
            left=min(x for mask,x,advance in glyphs);right=max(x+advance for mask,x,advance in glyphs)
            draw.line((left-2,10,right+1,10),fill='black')
            for mask,x,advance in glyphs:image.paste((255,255,255),(x,4),mask)
        if record and record['kind']=='say' and record['who']!='centered':
            layout=bi if bilingual else cn;shown=min(page,len(layout)-1)
            paint(renderer,image,layout[shown],visible)
            if portrait:image.paste(portrait,(196,0))
            if len(layout)>1:renderer.emit(image,f'{shown+1}/{len(layout)}',226,169,font=8,color=(130,130,130))
    if quick:
        total=sum(renderer.fonts[8][1].get(c,8) for label in FOOTER for c in label)+4*(len(FOOTER)-1);x=(256-total)//2
        for label in FOOTER:x=renderer.emit(image,label,x,181,font=8,color=(150,150,150))+4
    return image


def info_words(record,cn,bi):
    return [record['event']['line'],len(record['plan'].glyphs),record['duration'],len(record['pauses']),len(cn),len(bi),{'a':1,'n':2,'centered':3}.get(record['who'],0),int(record['continuation']),record['new_char_start']]


def page_for(record,cn,bi,visible,bilingual):
    count=len(record['plan'].glyphs)
    if bilingual and visible>=count:
        if record['kind']=='literal' and 'original_plan' in record:return len(bi)-1
    pages=bi if bilingual and getattr(bi,'limits',None) is not None else cn
    return next((i for i,page in reversed(list(enumerate(pages))) if any(e['index']<visible and 'en_index' not in e for e in page)),0)


def english_visible(record,bi,visible,page):
    if not record or page>=len(bi):return 0
    if getattr(bi,'limits',None) is not None:
        return sum('en_index' in entry and entry['glyph']['char']!='\n' and entry['reveal']<=visible for entry in bi[page])
    if record['kind']=='literal' and 'original_plan' in record and visible>=len(record['plan'].glyphs):
        return sum(entry['glyph']['char']!='\n' for entry in bi[page])
    return 0


def compare_geometry(context):
    """Diagnostic differential sweep; target layouts never feed golden pixels."""
    renderer=context['renderer'];differences=[]
    def signature(pages):
        return [(page,e['index'],e['x'],e['y'],e['font']) for page,entries in enumerate(pages) for e in entries]
    for number,record in enumerate(context['records']):
        cn,bi=planes_for(renderer,record);problems=[]
        if record['kind']=='say' and record['who']!='centered':
            portrait=renderer.portrait(dict(who=record['who'],attributes=record['attributes'])) is not None
            observed_cn=p.layout_cn(record['plan'].glyphs,renderer.metrics,portrait)
            observed=p.layout_paired(record['plan'].glyphs,record['english_plan'].glyphs,renderer.metrics,record['segments'],portrait)
            bi_cn=[[e for e in page if 'en_index' not in e] for page in bi]
            bi_en=[[dict(e,index=e['en_index']) for e in page if 'en_index' in e] for page in bi]
            for name,expected,actual in (('CN',signature(cn),signature(observed_cn)),('BI_CN',signature(bi_cn),signature(observed['cn_pages'])),('EN',signature(bi_en),signature(observed['en_pages']))):
                if expected!=actual:
                    at=next((i for i,(a,b) in enumerate(zip(expected,actual)) if a!=b),min(len(expected),len(actual)))
                    problems.append(dict(plane=name,at=at,oracle=expected[max(0,at-1):at+2],producer=actual[max(0,at-1):at+2]))
            stamps=[None]*len(record['english_plan'].glyphs)
            for page in bi:
                for entry in page:
                    if 'en_index' in entry:stamps[entry['en_index']]=entry['reveal']
            if stamps!=observed['en_reveal']:problems.append(dict(plane='threshold',at=next(i for i,(a,b) in enumerate(zip(stamps,observed['en_reveal'])) if a!=b)))
            if bi.limits!=observed['bi_limits']:problems.append(dict(plane='caps',oracle=bi.limits,producer=observed['bi_limits']))
            try:physical_row_timing(record,bi)
            except AssertionError as error:problems.append(dict(plane='physical_row_timing',detail=str(error)))
        elif record['kind']=='literal' and 'poem' not in record:
            variants=[('cn',record['plan'],cn,0)]+([('en',record['original_plan'],bi,len(record['plan'].glyphs))] if 'original_plan' in record else [])
            for language,plan,expected,offset in variants:
                entries,count=p.literal_entries(renderer,plan);actual=[[dict(e,index=e['index']+offset) for e in entries if e['page']==page] for page in range(count)]
                if signature(expected)!=signature(actual):problems.append(dict(plane='literal_'+language))
        elif record['kind']=='say':
            compiler=p.StoryTypographyCompiler(renderer);commands,count=compiler.layout(record['plan'],upper=True)
            actual=[(cmd[5],cmd[4],cmd[1],cmd[2]) for cmd in commands]
            expected=[(page,e['index'],e['x'],e['y']) for page,entries in enumerate(cn) for e in entries]
            if actual!=expected:problems.append(dict(plane='centered'))
        elif record['kind']=='date':
            compiler=p.StoryTypographyCompiler(renderer);x,y=record['native_position'];actual=[]
            for index,g in enumerate(record['plan'].glyphs):
                _,advance=compiler.glyph(g,8);actual.append((index,x,y));x+=advance
            if actual!=[(e['index'],e['x'],e['y']) for e in cn[0]]:problems.append(dict(plane='date'))
        if problems:differences.append(dict(id=record['id'],source_event_id=record['event']['source_event_id'],chapter=record['event']['chapter'],line=record['event']['line'],differences=problems))
        if (number+1)%2000==0:print(f'geometry {number+1}/{len(context["records"])} differences={len(differences)}',flush=True)
    return dict(result='PASS' if not differences else 'FAIL',records=len(context['records']),differences=differences)


def _compile_validator(directory):
    exe=Path(directory)/'story_text_validate.exe'
    command=[shutil.which('gcc') or 'gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-Inds/source','nds/source/story_text.c','nds/source/asset_pack.c','nds/tests/validate_story_text.c','-o',str(exe)]
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError(f'Story text validator compile failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}')
    return exe


def _publish_evidence(source,destination):
    # Only completed evidence is published. No live executable/input/result is
    # ever consumed from the shared artifacts directory during verification.
    with tempfile.NamedTemporaryFile(dir=destination.parent,prefix=destination.name+'.',delete=False) as file:
        temporary=Path(file.name);file.write(source.read_bytes())
    try:os.replace(temporary,destination)
    finally:temporary.unlink(missing_ok=True)


def verify_pack(report,ir,context,game,*,artifacts=None):
    with tempfile.TemporaryDirectory(prefix='zato_story_text_verify_') as directory:
        return _verify_pack(report,ir,context,game,Path(directory),Path(artifacts) if artifacts is not None else ROOT/'nds/artifacts')


def _verify_pack(report,ir,context,game,directory,out):
    package_sha=hashlib.sha256(Path(report['path']).read_bytes()).hexdigest()
    assert package_sha==report['sha256'],'Story packet and manifest SHA disagree before verification'
    protected=(ROOT/'nds/zato_ep2d3_scene_nitrofs.nds',ROOT/'nds/nitrofs/scene/text.bin')
    readonly_before={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    if context is None:context=context_for(ir,report,game)
    records=context['records'];renderer=context['renderer']
    attach_poem_sources(records,ir,game)
    out.mkdir(parents=True,exist_ok=True);exe=_compile_validator(directory)
    cases=[];expected=[];pixel_cases=metadata_cases=0;headers=set();portraits=set();literal_pages=[]
    row_audit=dict(matched_rows=0,blank_rows=0,unpaired_rows=0)
    def add(record,cn,bi,visible,page,bilingual,kind,clock=0,window=True,quick=True,name=None):
        nonlocal pixel_cases,metadata_cases
        id=record['id'] if record else 0xffffffff
        if kind==3:first=zlib.crc32(struct.pack('<9I',*info_words(record,cn,bi)));metadata_cases+=1
        elif kind==4:
            count=len(record['plan'].glyphs)
            if bilingual and getattr(bi,'limits',None) is not None:first=bi.limits[page] if page<len(bi) else count
            else:first=next((e['index'] for number,entries in enumerate(cn) if number>page for e in entries),count)
            metadata_cases+=1
        elif kind==6:first=0;metadata_cases+=1
        else:
            if kind==0:image=lower(renderer,record,cn,bi,visible,page,bilingual,window,quick)
            elif kind==7:
                image=lower(renderer,None,[],[],0,0,False,window,quick)
                if window:paint(renderer,image,cn[min(page,len(cn)-1)],0xffffffff)
            elif kind==5:image=background();renderer.emit(image,'ASCII 0z!',-2,188,font=8)
            else:
                image=background();poem='poem' in record
                layout=bi if poem or bilingual and record['kind']=='literal' and 'original_plan' in record else cn
                paint(renderer,image,layout[min(page,len(layout)-1)],0xffffffff if poem or bilingual and record['kind']=='literal' else visible)
            first=zlib.crc32(p.rgb15(np.asarray(image)).tobytes());pixel_cases+=1
            if name:image.save(out/f'story_text_golden_{name}.png')
        count=sum(t<=clock for t in record['timeline']) if record else 0
        selected_page=page_for(record,cn,bi,visible,bilingual) if record else 0
        cases.append((id,visible,page,int(bilingual),int(window)|(int(quick)<<1)|(clock<<8),kind));expected.append((first,selected_page,count,english_visible(record,bi,visible,page)))
    for record in records:
        cn,bi=planes_for(renderer,record);count=len(record['plan'].glyphs);id=record['id'];details=report['records'][id]
        if getattr(bi,'pairs',None):
            for key,value in physical_row_timing(record,bi).items():row_audit[key]+=value
        assert [details['char_count'],details['pages_cn'],details['pages_bi']]==[count,len(cn),len(bi)],(id,'source-plan shape mismatch')
        clocks=sorted({0,record['duration']//2,record['duration'],record['duration']+1,*[p['start_tick'] for p in record['pauses']],*[p['end_tick'] for p in record['pauses']]})
        for clock in clocks:add(record,cn,bi,sum(t<=clock for t in record['timeline']),0,False,3,clock=clock)
        for bilingual,layout in ((False,cn),(True,bi)):
            for page in [*range(len(layout)),len(layout),0xffffffff]:add(record,cn,bi,count,page,bilingual,4)
            if bilingual and (record['who']=='centered' and record['kind']=='say' or record['kind']=='date' or record['kind']=='literal' and 'original_plan' not in record):continue
            for page in range(len(layout)):
                name=None
                if record['kind'] in ('literal','date'):
                    name=f'{record["kind"]}_{record["event"]["chapter"]}_{record["event"]["line"]}_{"original" if bilingual else "cn"}_{page+1}'
                    literal_pages.append(dict(id=id,bilingual=bilingual,page=page+1,pages=len(layout),glyphs=sum(e['glyph']['char']!='\n' for e in layout[page])))
                elif not bilingual and page==0:
                    who=record['who'];attr=' '.join(record['attributes'])
                    if who not in headers:name=f'header_{who}';headers.add(who)
                    if who=='a' and attr not in portraits:name=f'asya_{attr or "none"}';portraits.add(attr)
                add(record,cn,bi,count,page,bilingual,2 if record['kind']=='date' else 1 if record['kind']=='literal' or record['who']=='centered' else 0,name=name)
                if 'poem' in record:add(record,cn,bi,count,page,bilingual,7,window=True,quick=False,name=f'poem_lower_{id}_{int(bilingual)}_{page}')
        if record['kind']=='say':
            visible=min(count,record['new_char_start']+1);page=page_for(record,cn,bi,visible,False)
            add(record,cn,bi,visible,page,False,1 if record['who']=='centered' else 0)
            if record['who']!='centered':
                for visible in sorted({min(count,record['new_char_start']+1),(count+record['new_char_start'])//2}):
                    add(record,cn,bi,visible,page_for(record,cn,bi,visible,True),True,0)
                for row in getattr(bi,'pairs',[]):
                    ink=[entry['index'] for entry in row['cn'] if not entry['glyph']['char'].isspace()]
                    for visible in sorted({0,*([ink[0]+1,ink[-1]+1] if ink else [])}):add(record,cn,bi,visible,row['page'],True,6)
        if (id+1)%2000==0:print(f'oracle text {id+1}/{len(records)} ({pixel_cases} pixel cases)',flush=True)
    for window,quick in ((False,False),(False,True),(True,False),(True,True)):add(None,[],[],0,0,False,0,window=window,quick=quick)
    add(None,[],[],0,0,False,5)
    cases_file=directory/'story_text_cases.bin';results_file=directory/'story_text_results.bin'
    cases_file.write_bytes(b''.join(struct.pack('<6I',*c) for c in cases))
    output=subprocess.check_output([str(exe),report['path'],str(cases_file),str(results_file)],text=True)
    actual=list(struct.iter_unpack('<4I',results_file.read_bytes()));assert len(actual)==len(expected)
    for number,(a,b) in enumerate(zip(actual,expected)):
        if a!=b:raise AssertionError(f'story native mismatch case {number}, request {cases[number]}, got {a}, expected {b}')
    memory=re.search(r'resident=(\d+) peak=(\d+) reads=(\d+) services=(\d+)',output)
    assert memory and int(memory[1])==report['managed_resident_bytes']
    # Repaired CRCs must not bypass structural checks. Each malformed package is
    # tested by the actual C reader, not a Python imitation of its bounds code.
    data=bytearray(Path(report['path']).read_bytes());h=struct.unpack_from('<32I',data,8);invalid=directory/'story_text_invalid.bin';rejects=[]
    variants=[('truncated',bytes(data[:-1]))]
    broken=bytearray(data);struct.pack_into('<I',broken,8+31*4,2);struct.pack_into('<I',broken,16,0);struct.pack_into('<I',broken,16,zlib.crc32(broken[:h[22]]));variants.append(('unknown body layout policy with valid shared CRC',broken))
    broken=bytearray(data);broken[h[22]+5]^=0x40;variants.append(('record codec/CRC',broken))
    broken=bytearray(data);struct.pack_into('<I',broken,h[8],0xfffffff0);struct.pack_into('<I',broken,16,0);struct.pack_into('<I',broken,16,zlib.crc32(broken[:h[22]]));variants.append(('directory bounds with valid shared CRC',broken))
    entry=struct.unpack_from('<IHHI',data,h[8]);at,flags,raw_size,record_crc=entry;packed_size=flags&32767
    original_raw=bytearray(data[at:at+packed_size] if flags&32768 else p.unlz10(data[at:at+packed_size]))
    def repair_record(raw):
        packed=p.lz10(bytes(raw));delta=len(packed)-packed_size
        broken=bytearray(data[:at]+packed+data[at+packed_size:]);struct.pack_into('<I',broken,12,len(broken));struct.pack_into('<H',broken,h[8]+4,len(packed));struct.pack_into('<I',broken,h[8]+8,zlib.crc32(raw))
        for id in range(1,h[3]):old=struct.unpack_from('<I',broken,h[8]+id*12)[0];struct.pack_into('<I',broken,h[8]+id*12,old+delta)
        struct.pack_into('<I',broken,16,0);struct.pack_into('<I',broken,16,zlib.crc32(broken[:h[22]]));return broken
    raw=bytearray(original_raw);struct.pack_into('<H',raw,2,0xffff);variants.append(('record bounds with valid CRCs',repair_record(raw)))
    if h[29]==4:
        f=struct.unpack_from('<24H',original_raw)
        assert f[19] and f[14] and f[5],'First record must exercise paired format4'
        for name,offset,value in (
            ('paired reserved field',46,1),
            ('paired CN count differs from source',38,f[12]-1),
            ('paired threshold count differs from EN',42,f[14]-1),
            ('paired EN thresholds exceed CN source',f[20],f[1]+1),
            ('paired EN thresholds go backwards',f[20]+(f[21]-1)*2,0),
            ('paired final page cap omits source tail',f[22]+(f[5]-1)*2,f[1]-1),
            ('paired page cap exceeds source',f[22],f[1]+1),
            ('body style low14 glyph ID invalid',f[11],0x3fff)):
            raw=bytearray(original_raw);struct.pack_into('<H',raw,offset,value);variants.append((name,repair_record(raw)))
        broken=bytearray(data);gid=struct.unpack_from('<H',broken,h[13])[0];struct.pack_into('<H',broken,h[13],gid|0x8000)
        struct.pack_into('<I',broken,16,0);struct.pack_into('<I',broken,16,zlib.crc32(broken[:h[22]]));variants.append(('static UI rejects body style flag',broken))
    for name,content in variants:
        invalid.write_bytes(content);result=subprocess.run([str(exe),str(invalid)],capture_output=True,text=True)
        assert result.returncode==3,(name,result.stdout,result.stderr);rejects.append(name)
    invalid.unlink()
    _publish_evidence(cases_file,out/'story_text_cases.bin')
    _publish_evidence(results_file,out/'story_text_results.bin')
    readonly={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    assert readonly==readonly_before,'Frozen scene ROM/text pack changed during verification'
    assert hashlib.sha256(Path(report['path']).read_bytes()).hexdigest()==package_sha,'Story packet changed during verification'
    return dict(result='PASS',package_sha256=package_sha,record_format=report['record_format'],records=len(records),pixel_cases=pixel_cases,metadata_cases=metadata_cases,physical_row_timing=row_audit,getter_draw_read_calls=0,native_output=output.strip(),managed_resident_bytes=int(memory[1]),managed_peak_bytes=int(memory[2]),read_calls=int(memory[3]),audio_service_calls=int(memory[4]),malformed_rejected=rejects,literal_date_pages_verified=literal_pages,case_sha256=hashlib.sha256(cases_file.read_bytes()).hexdigest(),frozen_readonly_sha256=readonly)
