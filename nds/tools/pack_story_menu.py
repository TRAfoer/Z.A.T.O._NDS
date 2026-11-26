"""Compile independent original-title/menu art and Fusion8/12 Unicode subset.

Original PNGs/font ZIP are read-only. New ZSTMENU1 contains no executable data.
"""
from pathlib import Path
import argparse,ast,hashlib,json,re,struct,sys,zipfile,zlib
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/font_lab'))
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
from font_core import parse_bdf
import anim_story as source

HEADER=struct.Struct('<8s24I');GLYPH=struct.Struct('<IIBBBB')
LABELS=['开始','读档','图鉴','设置','致谢','控制','退出','返回','日志','存档','标题','是','否','第 %u 页','自动存档','快速存档','NO SIGNAL','损坏存档','尚未解锁','中文','中英','语言','音乐','音效','读取中',
 '你确定要覆盖你的存档吗？','加载存档将丢失当前游戏进度。\n你确定要继续吗？','你确定要删除这个存档吗？','你确定要返回标题界面吗？\n未存档的进度将会丢失。','你确定要退出游戏吗？','存档完成。','操作失败。','播放已停止，可以关闭电源。','未选择存档','章节 %u  行 %u','此页面不能手动存档。','暂无已解锁CG','A 确定  B 返回  Y 删除','L/R 翻页','中文文本：原安装随附中文补丁','自动/快速页显示最新六档',
 '快进','自动','快速读档','调试','菜单','左右切章','开','关']
CREDIT_ADDITION='中文汉化(ParaTranz)：https://paratranz.cn/projects/17445\n翻译校对：bilibili@100ol  、bilibili@枫封峰\n移植制作：bilibili@神币の商家   GPT-6.1 Sol'
# URLs are split only by newlines: joining consecutive URL lines recovers the
# canonical address exactly. Each tool line fits the native 232px/12px body.
# License identifiers were checked against the official pages below and the
# installed font/BlocksDS/Python license files; BlocksDS has no single license.
TOOL_CREDITS=(
 dict(name='Fusion Pixel Font',url='https://github.com/TakWolf/fusion-pixel-font',
      license='SIL Open Font License 1.1',license_url='https://openfontlicense.org',
      license_source='https://github.com/TakWolf/fusion-pixel-font/blob/master/LICENSE-OFL',
      local_license='experiments/font_lab/out/LICENSE-OFL',
      lines=['Fusion Pixel Font','缝合像素字体 / TakWolf','8px / 12px 点阵字体',
             'Copyright (c) 2022, TakWolf','SIL Open Font License 1.1',
             'https://github.com/','TakWolf/fusion-pixel-font',
             'https://openfontlicense.org']),
 dict(name='ISAS FangSong',url='https://fontpixel.com/zh/fonts/isas-fangsong/',
      license='ISAS (1988)',license_url='https://xorg.freedesktop.org/archive/individual/font/font-isas-misc-1.0.4.tar.xz',
      license_source='https://xorg.freedesktop.org/archive/individual/font/font-isas-misc-1.0.4.tar.xz',
      local_font_source='experiments/video_lab/fonts/isas-fangsong-16/gb16fs.bdf',
      local_license='experiments/video_lab/fonts/isas-fangsong-16/COPYING',
      lines=['中科院仿宋点阵 / ISAS FangSong','中国科学院软件研究所',
             'The Institute of Software,',' Academia Sinica',
             'Copyright (C) 1988','开场视频16px文字','ISAS (1988)',
             'FontPixel 字体目录：','https://fontpixel.com/zh/fonts/',
             'isas-fangsong/','字体与许可原始来源：',
             'https://xorg.freedesktop.org/','archive/individual/font/',
             'font-isas-misc-1.0.4.tar.xz']),
 dict(name='BlocksDS',url='https://blocksds.skylyrac.net/docs/',
      license='各组件许可证见官方说明',license_url='https://blocksds.skylyrac.net/docs/guides/licenses/',
      license_source='https://blocksds.skylyrac.net/docs/guides/licenses/',
      local_license='C:/msys64/opt/wonderful/thirdparty/blocksds/core/licenses/',
      lines=['BlocksDS','Nintendo DS 开发工具链','各组件许可证见官方说明',
             'https://blocksds.skylyrac.net/docs/','许可证：',
             'https://blocksds.skylyrac.net/','docs/guides/licenses/']),
 dict(name='rpatool',url='https://github.com/Shizmob/rpatool',
      license='WTFPL Version 2',license_url='https://github.com/Shizmob/rpatool/blob/master/LICENSE',
      license_source='https://github.com/Shizmob/rpatool/blob/master/LICENSE',
      lines=['rpatool','RPA 资源读取工具 / Shiz','WTFPL Version 2',
             'https://github.com/Shizmob/rpatool','许可证：',
             'https://github.com/Shizmob/rpatool','/blob/master/LICENSE']),
 dict(name='Python',url='https://www.python.org/',
      license='PSF License Version 2',license_url='https://docs.python.org/3/license.html',
      license_source='https://docs.python.org/3/license.html',
      local_license='C:/Users/lhf19/AppData/Local/Programs/Python/Python312/LICENSE.txt',
      lines=['Python','Python Software Foundation','素材转换与构建脚本',
             'PSF License Version 2','https://www.python.org/',
             'https://docs.python.org/3/license.html']),
)
CONTROLS='A / 触屏：推进对白\nSTART：打开剧情菜单\nSELECT / 底部菜单：展开横条\n横条：左右选择，A确认\nL/R翻页；B/SELECT关闭\n外部触摸或其他键：仅关闭横条\n调试：横条末页，任意键退出\nB：回看  L：暂停  R：自动\nX：中英切换\n左右切章：设置中开启后可用\n\n文本：上下滚动，左右/LR翻页\n存读档：Y删除，B返回\n存档保存在同名.sav'

def words(image,alpha=False):
    a=np.asarray(image.convert('RGBA'),dtype=np.uint16);q=(a[:,:,:3]*31+127)//255
    w=(0x8000|q[:,:,0]|q[:,:,1]<<5|q[:,:,2]<<10).astype('<u2')
    return np.where(a[:,:,3]>=128,w,0).astype('<u2').tobytes() if alpha else w.tobytes()

def original_credits(game):
    lines=(game/'tl/chinese/screens.rpy').read_text(encoding='utf-8-sig').splitlines()
    values=[ast.literal_eval(x.strip()[4:]) for x in lines if x.strip().startswith('new ')]
    value=next(x for x in values if 'Ferry (Nopanamaman)' in x)
    return re.sub(r'\{[^{}]*\}','',value).strip()

def credits(game):
    localization,proofreading,port=CREDIT_ADDITION.splitlines()
    sections=['原作制作\n\n'+original_credits(game),
              '中文本地化\n\n'+localization+'\n'+proofreading,
              'NDS移植\n\n\n\n\n'+port,
              '字体与工具\n\n'+'\n\n'.join('\n'.join(item['lines']) for item in TOOL_CREDITS)]
    return '\n\n'.join(sections)

def gallery_rows():
    script=json.loads((ROOT/'nds/artifacts/story_script_manifest.json').read_text(encoding='utf8'))
    gfx=json.loads((ROOT/'nds/artifacts/story_gfx_manifest.json').read_text(encoding='utf8'))
    aliases={'hug':'irahug','isbored':'is_bored','issoft':'is_soft','isneutral':'is_neutral',
             'teasmile':'tea-smile','teafrown':'tea-frown','teafrownaway':'tea-frownaway','teayay':'tea-yay','teayayaway':'tea-yayaway','tealearn':'tea-learn',
             'teaifrown':'teai-frown','teaismile':'teai-smile','teaiyay':'teai-yay','teaihuh':'teai-huh'}
    rows=[]
    for bit,key in enumerate(script['unlocks']):
        names=[f'cnc{i}' for i in range(8)] if key=='game_completed' else [aliases.get(key.removesuffix('_unlocked'),key.removesuffix('_unlocked'))]
        for name in names:
            frame=gfx['name_to_image_id'][name]
            assert frame<gfx['static_frame_count'],('gallery image must be static',key,name)
            rows.append(dict(bit=bit,unlock=key,name=name,frame=frame))
    assert len(script['unlocks'])==36
    return rows

def build(game,output,manifest):
    rows=gallery_rows();strings=LABELS+[credits(game),CONTROLS]+[x['name'] for x in rows]
    needed=set(''.join(strings))|{chr(x) for x in range(32,127)};needed.discard('\n')
    analysis=json.loads((ROOT/'experiments/font_lab/out/analysis.json').read_text(encoding='utf8'));archive=Path(analysis['font_archive'])
    glyphs=[];atlas=bytearray();missing=[];fallback=[]
    with zipfile.ZipFile(archive) as z:
        fonts={n:parse_bdf(z.read(f'fusion-pixel-{n}px-monospaced-zh_hans.bdf')) for n in (8,12)}
        for size in (8,12):
            for char in sorted(needed,key=ord):
                cp=ord(char)
                if cp not in fonts[size].glyphs:
                    if size==8 and cp in fonts[12].glyphs:fallback.append(dict(char=char,requested=8,actual=12));continue
                    missing.append(dict(char=char,codepoint=cp,font=size));continue
                font=fonts[size];mask=font.bitmap(cp);at=len(atlas);atlas.extend(np.packbits(mask.reshape(-1)).tobytes())
                advance=font.glyphs[cp]['advance'];assert 1<=advance<=16
                glyphs.append((cp,at,font.width,font.height,advance,size))
    if missing:raise ValueError(f'UI font lacks required characters: {missing}')
    glyph_at=HEADER.size;atlas_at=glyph_at+len(glyphs)*GLYPH.size;string_at=(atlas_at+len(atlas)+3)&~3
    text_at=string_at+len(strings)*8;pool=bytearray();directory=[]
    for value in strings:
        raw=value.encode('utf8');directory.append((len(pool),len(raw)));pool.extend(raw)
    title_at=(text_at+len(pool)+3)&~3;logo_at=title_at+256*184*2;eyes_at=logo_at+256*184*2;snow_at=eyes_at+75*192*2
    empty_at=snow_at+6*6*2;gallery_at=empty_at+64*36*2;identity_at=gallery_at+len(rows)*8;total=identity_at+32
    h=[1,total,0,len(glyphs),glyph_at,atlas_at,len(atlas),len(strings),string_at,text_at,len(pool),title_at,logo_at,eyes_at,snow_at,6,6,gallery_at,len(rows),identity_at,len(LABELS),len(LABELS)+1,len(LABELS),empty_at]
    data=bytearray(HEADER.pack(b'ZSTMENU1',*h));data.extend(b''.join(GLYPH.pack(*g) for g in glyphs));data.extend(atlas);data.extend(bytes(string_at-len(data)))
    data.extend(b''.join(struct.pack('<II',*x) for x in directory));data.extend(pool);data.extend(bytes(title_at-len(data)))
    images={name:Image.open(game/name).convert('RGBA') for name in ['gui/main_menu.png','gui/overlay/main_menu.png','images/sprites/snow/snow2.png','gui/button/slot_idle_background.png']}
    data.extend(words(images['gui/main_menu.png'].crop((280,0,1280,720)).resize((256,184),Image.Resampling.LANCZOS)))
    data.extend(words(images['gui/overlay/main_menu.png'].crop((280,0,1280,720)).resize((256,184),Image.Resampling.LANCZOS),True))
    data.extend(words(images['gui/overlay/main_menu.png'].crop((0,0,280,720)).resize((75,192),Image.Resampling.LANCZOS)))
    data.extend(words(images['images/sprites/snow/snow2.png'].resize((6,6),Image.Resampling.LANCZOS),True))
    data.extend(words(images['gui/button/slot_idle_background.png'].crop((10,10,266,154)).resize((64,36),Image.Resampling.LANCZOS)))
    for i,row in enumerate(rows):row['string_id']=len(LABELS)+2+i;data.extend(struct.pack('<4H',row['bit'],row['frame'],row['string_id'],0))
    identity=hashlib.sha256((ROOT/'nds/nitrofs/story/script.bin').read_bytes()).digest();data.extend(identity)
    assert len(data)==total
    struct.pack_into('<I',data,16,zlib.crc32(data))
    output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(data)
    report=dict(format='ZSTMENU1',version=1,path=str(output.resolve()),bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),story_identity_sha256=identity.hex(),
        source_game=str(game),glyph_count=len(glyphs),atlas_bytes=len(atlas),string_count=len(strings),labels=LABELS,credits_string=strings[len(LABELS)],controls_string=CONTROLS,
        source_images_sha256={name:hashlib.sha256((game/name).read_bytes()).hexdigest() for name in images},font_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),font8_fallback12=fallback,missing_glyphs=[],gallery=rows,
        geometry=dict(title=[0,4,256,184],eyes=[0,0,75,192],selected_preview=[32,10,192,144],slots=dict(x=[8,91,174],y=[38,106],width=74,height=62),thumbnail='64x48 RGB332, upper 3x scale; lower48x36 with8px horizontal letterbox'),
        title_snow=dict(count=50,source_speed=50,source_wind=10,source_depth=10,source_xborder=[0,100],source_yborder=[50,400],native_scale='256/1000',random='deterministic hash seed adaptation, original depth/speed/alpha family'),
        translator_attribution=CREDIT_ADDITION,attribution_source='User-supplied exact additional credit text, original author credits retained.',
        original_credits_sha256=hashlib.sha256(original_credits(game).encode('utf8')).hexdigest(),credit_tools=list(TOOL_CREDITS),
        credits_layout=dict(font=12,line_pitch=14,body_width=232,scroll='continuous rows'),
        credits_badge=dict(width=147,height=43,x=54,gap=8,top_padding=4,reserved_rows=4,
            storage='native 1bpp const header',packed_bytes=791,section='NDS移植'),
        resident_bytes=len(data),notes=['Only this new menu pack and manifest written; original game/font/story packs unchanged.','Every string has independent Unicode mapping; no story_text gid-to-Unicode assumption.','The 43 CG entries map 36 unlock bits; game_completed unlocks8 bonus images.'])
    manifest.parent.mkdir(parents=True,exist_ok=True);manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--game',type=Path,default=source.DEFAULT_GAME);p.add_argument('--output',type=Path,default=ROOT/'nds/nitrofs/story/menu.bin');p.add_argument('--manifest',type=Path,default=ROOT/'nds/artifacts/story_menu_manifest.json');a=p.parse_args();r=build(a.game,a.output,a.manifest);print(json.dumps({k:r[k] for k in ['bytes','glyph_count','string_count','story_identity_sha256']},ensure_ascii=False))
if __name__=='__main__':main()
