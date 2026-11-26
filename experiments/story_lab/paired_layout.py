"""Deterministic per-source-fragment CN/EN lines; no semantic alignment.

Indexes always refer to source RichText glyphs. Wrapping creates coordinates,
never new characters. Two closing marks may hang in the reserved right pocket.
"""
from bisect import bisect_left

HEAD_FORBIDDEN=set('，。！？；：、,.!?;:%％‰°)]}）］｝】〉》」』〕〗〙〛’”»…')


def _metrics(g,measure,font):
    if g['char']=='\n':return 0,0,0
    w,h,advance=measure(g,g.get('font',font))
    return int(w),int(h),int(advance)


def _limits(y,portrait,x,width,hard_right):
    hard=min(hard_right,190) if portrait and y<60 else hard_right
    return min(x+width,hard-24) if portrait and y<60 else min(x+width,hard),hard


def _candidates(gs,start,end,measure,font,x,normal,hard,english=False,hanging=0,split_closing=False):
    """Return legal exclusive cuts with cumulative advances, longest last."""
    position=x;fit=[]
    for index in range(start,end):
        g=gs[index];char=g['char'];w,_,advance=_metrics(g,measure,font)
        if char=='\n':fit.append((index+1,position-x));break
        if not char.isspace() and position+w>normal:
            if char not in HEAD_FORBIDDEN or hanging>=2 or position+w>hard:break
            hanging+=1
        if not char.isspace() and position+w>hard:break
        position+=advance;fit.append((index+1,position-x))
    if not fit:
        w,_,advance=_metrics(gs[start],measure,font)
        if x+w>hard:raise ValueError(f'One source glyph exceeds hard line bounds: {gs[start]!r}')
        fit=[(start+1,advance)]
    valid=[];punctuation_safe=[]
    for cut,advance in fit:
        if gs[cut-1]['char']=='\n':
            valid.append((cut,advance));punctuation_safe.append((cut,advance));continue
        if cut<end and gs[cut]['char'] in HEAD_FORBIDDEN and not split_closing:continue
        punctuation_safe.append((cut,advance))
        if english and cut<end and gs[cut-1]['char']!='\n':
            # All whitespace belongs to the preceding word. Internal straight
            # and curly apostrophes never form a boundary.
            if not gs[cut-1]['char'].isspace() or gs[cut]['char'].isspace():continue
        valid.append((cut,advance))
    # A token longer than a physical line is split without inserting a hyphen.
    # An all-punctuation source run can also be unavoidably non-typographic.
    first_ink=next((i+1 for i in range(start,end) if not gs[i]['char'].isspace()),None)
    if first_ink is not None:
        for choices in (valid,punctuation_safe,fit):
            ink=[item for item in choices if item[0]>=first_ink]
            if ink:return ink
    return valid or punctuation_safe or fit


def _advance(gs,start,end,measure,font):
    return sum(_metrics(g,measure,font)[2] for g in gs[start:end] if g['char']!='\n')


def _entry(gs,index,x,y,page,measure,font,hard):
    g=gs[index];w,h,advance=_metrics(g,measure,font);newline=g['char']=='\n'
    # Blank source glyphs keep their logical command even when their trailing
    # advances extend beyond the ink pocket; their blank canvas stays in bounds.
    px=min(x,hard-w) if g['char'].isspace() and not newline else x
    return dict(index=index,glyph=g,font=g.get('font',font),x=px,y=y,page=page,
                width=0 if newline else advance,height=0 if newline else h,ink_width=0 if newline else w)


def _put(gs,start,end,x,y,page,measure,font,hard):
    result=[]
    for index in range(start,end):
        result.append(_entry(gs,index,x,y,page,measure,font,hard))
        if gs[index]['char']!='\n':x+=_metrics(gs[index],measure,font)[2]
    return result


def layout_cn(glyphs,measure,portrait=False,*,x=20,y=30,width=216,bottom=173,hard_right=248):
    pages=[[]];cursor=0;row_y=y
    while cursor<len(glyphs):
        normal,hard=_limits(row_y,portrait,x,width,hard_right)
        end=_candidates(glyphs,cursor,len(glyphs),measure,12,x,normal,hard)[-1][0]
        height=max((_metrics(g,measure,12)[1] for g in glyphs[cursor:end] if g['char']!='\n'),default=12)
        if row_y+height>bottom:
            if row_y==y:raise ValueError('CN row is taller than a page')
            pages.append([]);row_y=y;continue
        page=len(pages)-1;pages[-1].extend(_put(glyphs,cursor,end,x,row_y,page,measure,12,hard))
        cursor=end;row_y+=max(15,height+2)
    return pages


def _hard_end(gs,start,end):
    return next((i+1 for i in range(start,end) if gs[i]['char']=='\n'),end)


def _blank_break(gs,start,end):
    for i in range(start,end):
        char=gs[i]['char']
        if char=='\n':return i+1
        if not char.isspace():break
    return start


def _nearest(candidates,total,fraction):
    num,den=(fraction.numerator,fraction.denominator) if hasattr(fraction,'numerator') else fraction
    return min(candidates,key=lambda value:(abs(value[1]*den-num*max(1,total)),-value[0]))[0]


def _first_unit(gs,start,end,measure,font,english):
    """Complete first visible word/character, including leading whitespace."""
    position=0;right=0;height=0;seen=False
    for i in range(start,end):
        g=gs[i];char=g['char']
        if char=='\n':break
        if seen and english and char.isspace():break
        w,h,advance=_metrics(g,measure,font)
        if not char.isspace():right=max(right,position+w);height=max(height,h);seen=True
        position+=advance
        if seen and not english:break
    return right,height


def _ink(gs,start,end):
    return any(not g['char'].isspace() for g in gs[start:end])


def _retain_ink(candidates,gs,start,end):
    return [item for item in candidates if _ink(gs,start,item[0]) and _ink(gs,item[0],end)]


def _thresholds(cn,en,cstart,cend,estart,eend,anchor):
    c=[i for i in range(cstart,cend) if not cn[i]['char'].isspace()]
    e=[i for i in range(estart,eend) if not en[i]['char'].isspace()]
    if not e:return {i:(c[-1]+1 if c else anchor) for i in range(estart,eend)}
    if c:
        values=[c[-1]+1] if len(e)==1 else [c[(j*(len(c)-1))//(len(e)-1)]+1 for j in range(len(e))]
    else:values=[anchor]*len(e)
    result={index:value for index,value in zip(e,values)}
    for index in range(estart,eend):
        if index not in result:
            at=bisect_left(e,index);result[index]=values[at-1] if at else values[0]
    return result


def layout_paired(cn,en,measure,segments=None,portrait=False,*,x=20,y=30,width=216,bottom=173,hard_right=248):
    if segments is None:segments=[dict(source_event_id=None,cn_start=0,cn_end=len(cn),en_start=0,en_end=len(en))]
    c_seen=e_seen=0
    for segment in segments:
        if segment['cn_start']!=c_seen or segment['en_start']!=e_seen:raise ValueError('Source segments must exactly partition both glyph streams')
        c_seen=segment['cn_end'];e_seen=segment['en_end']
    if c_seen!=len(cn) or e_seen!=len(en):raise ValueError('Source segment coverage mismatch')
    cn_pages=[[]];en_pages=[[]];pairs=[];reveal=[None]*len(en);row_y=y;last_row=None
    for segment in segments:
        ci=segment['cn_start'];ei=segment['en_start'];ce=segment['cn_end'];ee=segment['en_end']
        try_carry=last_row is not None
        while ci<ce or ei<ee:
            break_c=_blank_break(cn,ci,ce);break_e=_blank_break(en,ei,ee)
            newline_c=break_c>ci;newline_e=break_e>ei
            carry=None
            if try_carry:
                candidate=last_row;cy=candidate['cn_y'];ey=candidate['en_y']
                normal,hard=_limits(cy,portrait,x,width,hard_right);en_normal,en_hard=_limits(ey,portrait,x,width,hard_right)
                cw,ch=_first_unit(cn,ci,ce,measure,12,False);ew,eh=_first_unit(en,ei,ee,measure,8,True)
                if newline_c or newline_e:cw=ch=ew=eh=0
                first=cn[ci]['char'] if ci<ce else ''
                cfits=(not cw or candidate['cn_x']+cw<=normal or first in HEAD_FORBIDDEN and candidate['cn_hang']<2 and candidate['cn_x']+cw<=hard)
                efits=not ew or candidate['en_x']+ew<=en_normal
                if cfits and efits and cy+max(14,ch+1)<=ey and ey+eh<=candidate['next_y']-2:carry=candidate
                try_carry=False
            cy=carry['cn_y'] if carry else row_y;cx=carry['cn_x'] if carry else x;ex=carry['en_x'] if carry else x
            normal,hard=_limits(cy,portrait,x,width,hard_right)
            cb=_hard_end(cn,ci,ce);eb=_hard_end(en,ei,ee)
            cc=_candidates(cn,ci,cb,measure,12,cx,normal,hard,hanging=carry['cn_hang'] if carry else 0) if ci<ce else [(ci,0)]
            en_y=carry['en_y'] if carry else cy+14
            en_normal,en_hard=_limits(en_y,portrait,x,width,hard_right)
            ec=_candidates(en,ei,eb,measure,8,ex,en_normal,en_hard,True,hanging=carry['en_hang'] if carry else 0) if ei<ee else [(ei,0)]
            ct=_advance(cn,ci,cb,measure,12);et=_advance(en,ei,eb,measure,8)
            rates=([(cc[-1][1],max(1,ct))] if ci<ce else [])+([(ec[-1][1],max(1,et))] if ei<ee else [])
            fraction=rates[0] if rates else (1,1)
            for rate in rates[1:]:
                if rate[0]*fraction[1]<fraction[0]*rate[1]:fraction=rate
            cend=_nearest(cc,ct,fraction);eend=_nearest(ec,et,fraction)
            # Avoid exhausting one side early when a legal cut can retain a
            # source character/word for the next paired row.
            if _ink(en,eend,ee) and not _ink(cn,cend,ce):
                choices=_retain_ink(cc,cn,ci,ce)
                if choices:cend=_nearest(choices,ct,fraction)
            if _ink(cn,cend,ce) and not _ink(en,eend,ee):
                choices=_retain_ink(ec,en,ei,ee)
                if choices:eend=_nearest(choices,et,fraction)
            orphan=_ink(cn,cend,ce)!=_ink(en,eend,ee)
            if carry and orphan and not (newline_c or newline_e):
                # Prefer moving this new fragment as a whole over spending its
                # sole English word in the tiny tail of an inherited row.
                fresh_y=row_y if row_y+25<=bottom else y
                fresh_normal,fresh_hard=_limits(fresh_y,portrait,x,width,hard_right)
                fresh_ch=max((_metrics(g,measure,12)[1] for g in cn[ci:ce] if not g['char'].isspace()),default=12)
                fresh_ey=fresh_y+max(14,fresh_ch+1)
                fresh_en_normal,fresh_en_hard=_limits(fresh_ey,portrait,x,width,hard_right)
                fits_cn=cb==ce and (not _ink(cn,ci,ce) or _candidates(cn,ci,ce,measure,12,x,fresh_normal,fresh_hard)[-1][0]==ce)
                fits_en=eb==ee and (not _ink(en,ei,ee) or _candidates(en,ei,ee,measure,8,x,fresh_en_normal,fresh_en_hard,True)[-1][0]==ee)
                if fits_cn and fits_en:last_row=None;continue
            if _ink(cn,cend,ce) and not _ink(en,eend,ee) and not (newline_c or newline_e):
                # A sole complete English word may need several Chinese rows.
                # Keep word boundaries normally, but split that word without
                # changing its characters when it would strand Chinese ink.
                all_closing=all(g['char'].isspace() or g['char'] in HEAD_FORBIDDEN for g in en[ei:eb])
                choices=_retain_ink(_candidates(en,ei,eb,measure,8,ex,en_normal,en_hard,hanging=carry['en_hang'] if carry else 0,split_closing=all_closing),en,ei,ee) if ei<ee else []
                if choices:eend=_nearest(choices,et,fraction)
            if newline_c or newline_e:cend=break_c;eend=break_e
            cn_height=max((_metrics(g,measure,12)[1] for g in cn[ci:cend] if g['char']!='\n'),default=12)
            actual_en_y=cy+max(14,cn_height+1)
            en_height=max((_metrics(g,measure,8)[1] for g in en[ei:eend] if g['char']!='\n'),default=8)
            if carry and (actual_en_y>carry['en_y'] or carry['en_y']+en_height>carry['next_y']-2):
                last_row=None;continue
            en_y=carry['en_y'] if carry else actual_en_y
            # Measuring at the earliest EN baseline is conservative around the
            # portrait. A taller CN row may move EN outside it, never inside it.
            _,en_hard=_limits(en_y,portrait,x,width,hard_right)
            if en_y+en_height>bottom:
                if cy==y:raise ValueError('One paired row is taller than a page')
                cn_pages.append([]);en_pages.append([]);row_y=y;last_row=None;continue
            page=len(cn_pages)-1
            c_entries=_put(cn,ci,cend,cx,cy,page,measure,12,hard);e_entries=_put(en,ei,eend,ex,en_y,page,measure,8,en_hard)
            cn_pages[-1].extend(c_entries);en_pages[-1].extend(e_entries)
            projected=_thresholds(cn,en,ci,cend,ei,eend,ci)
            for index,value in projected.items():reveal[index]=value
            pairs.append(dict(source_event_id=segment['source_event_id'],page=page,cn_y=cy,en_y=en_y,
                              cn_indices=list(range(ci,cend)),en_indices=list(range(ei,eend)),
                              cn_start=ci,cn_end=cend,en_start=ei,en_end=eend,carried=bool(carry)))
            row_y=carry['next_y'] if carry else cy+max(25,cn_height+1+en_height+2)
            last_row=dict(page=page,cn_y=cy,en_y=en_y,cn_x=cx+_advance(cn,ci,cend,measure,12),en_x=ex+_advance(en,ei,eend,measure,8),next_y=row_y,
                          cn_hang=(carry['cn_hang'] if carry else 0)+sum(not e['glyph']['char'].isspace() and e['x']+e['ink_width']>normal for e in c_entries),
                          en_hang=(carry['en_hang'] if carry else 0)+sum(not e['glyph']['char'].isspace() and e['x']+e['ink_width']>en_normal for e in e_entries))
            if any(g['char']=='\n' for g in cn[ci:cend]+en[ei:eend]):last_row=None
            ci=cend;ei=eend
    if any(value is None for value in reveal):raise AssertionError('EN reveal projection omitted a source glyph')
    limits=[]
    for page in range(len(cn_pages)):
        limits.append(next((e['index'] for entries in cn_pages[page+1:] for e in entries),len(cn)))
    return dict(cn_pages=cn_pages,en_pages=en_pages,pairs=pairs,en_reveal=reveal,bi_limits=limits)
