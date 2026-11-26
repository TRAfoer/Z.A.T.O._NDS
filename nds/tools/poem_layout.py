"""Source-line preserving trilingual poetry for two 256x192 screens.

This module knows no source files, font atlas or text wire format. The caller
supplies three complete glyph streams and native styled glyph metrics.
"""

POEM_POLICY={
    'version':1,'rectangle':[8,24,248,168],
    'upper':'Russian12px, English8px interleaved,26px group pitch,22px nominal height.',
    'lower':'Chinese12px,16px line pitch,12px nominal height.',
    'pagination':'At most5 complete source-line groups per page; balance groups across pages.',
    'logical_order':'Complete Russian stream followed by complete English stream; Chinese independent.',
    'blank_lines':'Preserved as complete groups; newline belongs to preceding source line.',
}


def _lines(glyphs):
    lines=[[]]
    for index,glyph in enumerate(glyphs):
        lines[-1].append(index)
        if glyph['char']=='\n':lines.append([])
    return lines


def _line_entries(glyphs,indices,measure,font,y,page,index_offset=0):
    measured=[];pen=extent=0
    for index in indices:
        glyph=glyphs[index];size=glyph.get('font',font)
        if glyph['char']=='\n':w=h=advance=0
        else:w,h,advance=map(int,measure(glyph,size))
        if min(w,h,advance)<0:raise ValueError('Negative poem glyph metrics')
        measured.append((index,glyph,size,pen,w,h,advance))
        if glyph['char']!='\n':extent=max(extent,pen+w);pen+=advance
    if extent>240:raise ValueError(f'Poem source line exceeds240px: {extent}px')
    left=(256-extent)//2
    entries=[]
    for index,glyph,size,pen,w,h,advance in measured:
        x=left+pen
        if glyph['char']!='\n' and not(8<=x and x+w<=248 and 24<=y and y+h<=168):
            raise ValueError('Poem source glyph exceeds its page rectangle')
        entries.append(dict(index=index_offset+index,glyph=glyph,font=size,x=x,y=y,page=page,
                            width=advance,height=h,ink_width=w))
    return entries


def poem_pages(ru_glyphs,en_glyphs,cn_glyphs,measure):
    """Return centered lower/upper pages and their complete source-line groups.

    ``measure(glyph, font)`` returns(ink_width, ink_height, advance). Upper
    entries retain RU indexes followed by EN indexes even though visual entries
    alternate by source line; a wire writer must sort by index before encoding.
    """
    ru,en,cn=(_lines(stream) for stream in (ru_glyphs,en_glyphs,cn_glyphs))
    if not(len(ru)==len(en)==len(cn)):
        raise ValueError(f'Poem line-count mismatch: RU{len(ru)}, EN{len(en)}, CN{len(cn)}')
    count=len(cn);page_count=(count+4)//5
    base,extra=divmod(count,page_count)
    lower_pages=[];upper_pages=[];groups=[];line=0
    for page in range(page_count):
        size=base+int(page<extra)
        upper_top=24+(144-((size-1)*26+22))//2
        lower_top=24+(144-((size-1)*16+12))//2
        lower=[];upper=[]
        for row in range(size):
            ru_y=upper_top+row*26;en_y=ru_y+14;cn_y=lower_top+row*16
            lower.extend(_line_entries(cn_glyphs,cn[line],measure,12,cn_y,page))
            upper.extend(_line_entries(ru_glyphs,ru[line],measure,12,ru_y,page))
            upper.extend(_line_entries(en_glyphs,en[line],measure,8,en_y,page,len(ru_glyphs)))
            groups.append(dict(source_line=line,page=page,ru_y=ru_y,en_y=en_y,cn_y=cn_y,
                               ru_indices=ru[line],en_indices=en[line],cn_indices=cn[line]))
            line+=1
        lower_pages.append(lower);upper_pages.append(upper)
    return dict(lower_pages=lower_pages,upper_pages=upper_pages,groups=groups)
