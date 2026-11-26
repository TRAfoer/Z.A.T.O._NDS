"""Interactive ep2d3 animation/context fixture in the existing Python preview."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

from asset_model import DEFAULT_GAME
from anim_story import build_story
from anim_renderer import AnimRenderer
from anim_player import ScenePlayer,PresentationClock
from anim_text import SceneText
from anim_audio import SceneAudio

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent/'out/anim_preview'
sys.path.insert(0,str(ROOT/'experiments/audio_lab'))
from loop_editor_shortcuts import TransportSpaceShortcut

PRESETS={
    '仅动效 · 红色叠影':('pure_red',613),
    '仅动效 · 镜面叠影':('pure_mirror',653),
    '仅动效 · iss 闪现':('pure_iss',603),
    '仅动效 · 窗口循环':('pure_window',792),
    '前后剧情 · 从前段开始':('story',240),
    '前后剧情 · 键盘声与闪现':('story',573),
    '前后剧情 · 动效入口':('story',599),
    '前后剧情 · 镜面入口':('story',653),
    '前后剧情 · 动效之后':('story',688),
    '前后剧情 · 窗口前后':('story',777),
}


def audio_names(story):
    result=set()
    for e in story['opening_setup']+story['events']:
        if e['kind']!='command':continue
        m=re.match(r'play\s+(music|sound)\s*["\'](.+?)["\']',e['value'])
        if m:result.add(('music' if m[1]=='music' else 'sfx',m[2]))
    return sorted(result)


def prepare_renderer(renderer,story):
    # Preload only small RGBA textures; no source-resolution frame cache.
    names={f['image'] for name,seq in story['sequences'].items() for f in seq['frames'] if name!='iss' or renderer.iss_mode!='text'}
    names.update(e['image'] for e in story['opening_setup']+story['events'] if e['kind'] in ('scene','show') and e['image']!='text')
    for name in names:
        if name in renderer.index:renderer._texture(name)


class AnimPreview:
    def __init__(self,root,game=DEFAULT_GAME,scale=2,audio=True):
        self.root=root;self.game=Path(game);self.story=build_story(self.game)
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'story.json').write_text(json.dumps(self.story,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        self.renderer=AnimRenderer(self.game,self.story['sequences'],cache_size=80,iss_mode='text')
        prepare_renderer(self.renderer,self.story)
        self.text=SceneText(self.game,self.story['characters'])
        self.audio=SceneAudio(enabled=audio)
        self.audio.prepare(audio_names(self.story))
        self.player=ScenePlayer(self.story,self.audio,require_present=True)
        self.mode='';self.page=0;self.last_display=None;self.last_lower_key=None
        self.paused=False;self.job=None;self.last_tick=time.perf_counter();self.render_ms=[];self.pure_start=0.
        self.root.title('Z.A.T.O. · ep2d3 动效与剧情验证')
        self.root.configure(bg='#101010');self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.report_callback_exception=self.callback_error
        style=ttk.Style(root)
        for name,options in {
            'Anim.TFrame':dict(background='#101010'),
            'Anim.TLabel':dict(background='#101010',foreground='#ddd',font=('Microsoft YaHei UI',10)),
            'AnimMuted.TLabel':dict(background='#101010',foreground='#999',font=('Microsoft YaHei UI',9)),
            'Anim.TCheckbutton':dict(background='#101010',foreground='#ddd'),
        }.items():style.configure(name,**options)
        outer=ttk.Frame(root,style='Anim.TFrame',padding=14);outer.pack(fill='both',expand=True)
        controls=ttk.Frame(outer,style='Anim.TFrame',width=300);controls.pack(side='left',fill='y',padx=(0,16));controls.pack_propagate(False)
        ttk.Label(controls,text='ep2d3 · 特殊演出',style='Anim.TLabel',font=('Microsoft YaHei UI',16)).pack(anchor='w',pady=(0,10))
        ttk.Label(controls,text='独立场景 / 前后剧情验证',style='AnimMuted.TLabel').pack(anchor='w')
        self.preset=tk.StringVar(value=next(iter(PRESETS)))
        self.combo=ttk.Combobox(controls,textvariable=self.preset,values=list(PRESETS),state='readonly',width=29)
        self.combo.pack(fill='x',pady=(6,10));self.combo.bind('<<ComboboxSelected>>',self.select)
        row=ttk.Frame(controls,style='Anim.TFrame');row.pack(fill='x')
        ttk.Button(row,text='重播',command=self.select).pack(side='left')
        self.pause_button=ttk.Button(row,text='暂停',command=self.pause);self.pause_button.pack(side='left',padx=6)
        ttk.Button(row,text='下一句',command=self.next).pack(side='left')
        self.auto=tk.BooleanVar(value=False);self.bilingual=tk.BooleanVar(value=False)
        self.sound=tk.BooleanVar(value=audio);self.compare=tk.BooleanVar(value=False)
        for title,var,command in [('自动阅读',self.auto,self.change_auto),('中英对照',self.bilingual,self.redraw),('声音',self.sound,self.change_sound),('并排显示贴图 / 旧阈值对照',self.compare,self.resize)]:
            ttk.Checkbutton(controls,text=title,variable=var,command=command,style='Anim.TCheckbutton').pack(anchor='w',pady=3)
        ttk.Label(controls,text='显示倍率',style='AnimMuted.TLabel').pack(anchor='w',pady=(10,3))
        self.scale=tk.IntVar(value=scale)
        scales=ttk.Frame(controls,style='Anim.TFrame');scales.pack(anchor='w')
        for value in (1,2):ttk.Radiobutton(scales,text=f'{value}×',value=value,variable=self.scale,command=self.resize).pack(side='left')
        self.iss_text=tk.BooleanVar(value=True)
        ttk.Checkbutton(controls,text='iss · 使用点阵文字拟合',variable=self.iss_text,command=self.change_iss,style='Anim.TCheckbutton').pack(anchor='w',pady=(10,0))
        self.candidate=tk.BooleanVar(value=False)
        self.candidate_box=ttk.Checkbutton(controls,text='贴图候选：is10 → 10.png',variable=self.candidate,command=self.change_alias,style='Anim.TCheckbutton',state='disabled')
        self.candidate_box.pack(anchor='w',pady=(3,3))
        self.iss_notice=tk.StringVar(value='文字版不依赖 is10 贴图。\n取消文字拟合可查看原贴图。')
        ttk.Label(controls,textvariable=self.iss_notice,style='AnimMuted.TLabel',wraplength=285).pack(anchor='w')
        ttk.Button(controls,text='查看前后剧情原文 / 译文',command=self.transcript).pack(fill='x',pady=(14,6))
        ttk.Button(controls,text='导出当前双屏与参数',command=self.export).pack(fill='x')
        self.info=tk.StringVar();ttk.Label(controls,textvariable=self.info,style='AnimMuted.TLabel',wraplength=290,justify='left').pack(anchor='w',pady=(14,6))
        self.note=tk.StringVar(value='空格：暂停/继续；Enter/A/点击正文：继续。\n普通等待可跳过，强制停顿保留。')
        ttk.Label(controls,textvariable=self.note,style='AnimMuted.TLabel',wraplength=290,justify='left').pack(anchor='w',pady=(8,0))
        ttk.Label(controls,text='Python 预览，不代表 NDS 性能。\n本段统一完整场景坐标，普通背景规则不变。\n存读档和完整设置尚未接入。',style='AnimMuted.TLabel',wraplength=290).pack(anchor='w',side='bottom',pady=8)
        self.screens=ttk.Frame(outer,style='Anim.TFrame');self.screens.pack(side='left',anchor='n')
        self.panels=[]
        for title in ['当前 · 保留细线透明度 / RGB15','贴图 / 旧阈值 · 同一时刻对照']:
            panel=ttk.Frame(self.screens,style='Anim.TFrame')
            ttk.Label(panel,text=title,style='AnimMuted.TLabel').pack(anchor='w',pady=(0,5))
            top=tk.Canvas(panel,bg='black',highlightthickness=0);top.pack()
            ttk.Label(panel,text='上屏 256×192',style='AnimMuted.TLabel').pack(anchor='w',pady=(2,7))
            lower=tk.Canvas(panel,bg='black',highlightthickness=0,cursor='hand2');lower.pack();lower.bind('<Button-1>',self.touch)
            ttk.Label(panel,text='下屏 256×192',style='AnimMuted.TLabel').pack(anchor='w',pady=(2,0))
            self.panels.append((panel,top,lower))
        root.bind('<Return>',lambda e:self.key_next(e));root.bind('a',lambda e:self.key_next(e))
        self.space=TransportSpaceShortcut(root,self.pause)
        self.select();self.resize();self.last_tick=time.perf_counter();self.next_frame_at=self.last_tick+1/60;self.job=root.after(17,self.tick)

    def callback_error(self,kind,value,tb):
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'error.log').write_text(''.join(traceback.format_exception(kind,value,tb)),encoding='utf-8')
        self.paused=True;self.audio.pause();self.note.set(f'预览错误：{value}；详情在 out/anim_preview/error.log')

    def select(self,event=None):
        kind,line=PRESETS[self.preset.get()];self.mode=kind;self.player.seek(line)
        self.player.auto=self.auto.get();self.page=0;self.last_display=None;self.last_lower_key=None
        if kind.startswith('pure_'):
            # Freeze the audited scene state while independent ATL clocks run.
            self.player.wait_until=None;self.player.display=None;self.player.blocked=True;self.player.transition=None
            self.pure_start=self.player.now
        self.paused=False;self.pause_button.configure(text='暂停');self.audio.resume()
        self.last_tick=time.perf_counter();self.clock=PresentationClock();self.next_frame_at=self.last_tick+1/60
        if self.job is not None:
            self.root.after_cancel(self.job);self.job=self.root.after(17,self.tick)
        self.redraw()

    def change_auto(self):self.player.auto=self.auto.get()
    def change_sound(self):self.audio.enabled=self.sound.get()
    def change_alias(self):
        self.renderer.aliases={'is10':'10'} if self.candidate.get() else {};self.redraw()

    def change_iss(self):
        fitted=self.iss_text.get();self.renderer.iss_mode='text' if fitted else 'texture'
        self.candidate_box.configure(state='disabled' if fitted else 'normal')
        self.iss_notice.set('文字版不依赖 is10 贴图。\n取消文字拟合可查看原贴图。' if fitted else '贴图模式缺 is10，默认保持上一有效帧。\n候选映射仅供对照，未改动原素材。')
        self.redraw()

    def pause(self):
        self.paused=not self.paused;self.pause_button.configure(text='继续' if self.paused else '暂停')
        self.audio.pause() if self.paused else self.audio.resume()
        self.last_tick=time.perf_counter()

    def key_next(self,event):
        if event.widget.winfo_class() not in TransportSpaceShortcut.TEXT_CLASSES:self.next();return 'break'

    def next(self):
        if self.mode.startswith('pure_'):
            destination={'pure_mirror':'前后剧情 · 镜面入口','pure_window':'前后剧情 · 窗口前后'}.get(self.mode,'前后剧情 · 动效入口')
            self.preset.set(destination);self.select();return
        display=self.player.display
        if display and not display['revealed'] and self.player.elapsed()<display['text'].duration:self.player.advance()
        elif display and self.page < self.lower_info['pages']-1:self.page+=1
        else:self.player.advance();self.page=0
        self.redraw()

    def touch(self,event):
        x,y=event.x/self.scale.get(),event.y/self.scale.get()
        for x0,y0,x1,y1,label in self.text.footer_boxes:
            if x0<=x<=x1 and y0<=y<=y1:
                if label=='日志':self.transcript()
                elif label=='自动':self.auto.set(not self.auto.get());self.change_auto()
                elif label=='中英':self.bilingual.set(not self.bilingual.get());self.redraw()
                elif label=='返回':
                    rows=[e for e in self.story['events'] if e['kind']=='say' and e['line']<self.player.line]
                    if rows:
                        self.mode='story';self.player.seek(rows[-1]['line']);self.page=0
                        if self.paused:self.audio.pause()
                        self.last_tick=time.perf_counter();self.redraw()
                elif label=='快进':
                    self.auto.set(not self.auto.get());self.player.auto_delay=.1;self.change_auto()
                else:self.note.set('此入口用于演出与剧情核对，存读档及完整设置尚未接入。')
                return
        self.next()

    def resize(self):
        scale=self.scale.get()
        for i,(panel,top,lower) in enumerate(self.panels):
            panel.grid_forget()
            if i==0 or self.compare.get():panel.grid(row=0,column=i,sticky='n',padx=(0,12 if i==0 and self.compare.get() else 0))
            for canvas in (top,lower):canvas.configure(width=256*scale,height=192*scale)
        count=2 if self.compare.get() else 1
        self.root.geometry(f'{344+count*256*scale+(12 if count==2 else 0)}x{max(640,192*scale*2+90)}')
        self.last_lower_key=None;self.redraw()

    def put(self,canvas,image):
        scale=self.scale.get();photo=ImageTk.PhotoImage(image.resize((256*scale,192*scale),Image.Resampling.NEAREST))
        if getattr(canvas,'image_id',None) is None:canvas.image_id=canvas.create_image(0,0,anchor='nw',image=photo)
        else:canvas.itemconfigure(canvas.image_id,image=photo)
        canvas.image=photo

    def frame(self,mode='rgb15'):
        top=self.renderer.render(self.player.layers,self.player.now,mode)
        primary_metadata=deepcopy(self.renderer.metadata)
        if self.player.transition:
            tr=self.player.transition;old=self.renderer.render(tr['old'],self.player.now,mode)
            amount=min(1.,max(0.,(self.player.now-tr['start'])/tr['duration']));top=Image.blend(old,top,amount)
        if mode=='rgb15':self.current_metadata=primary_metadata
        return self.text.upper_overlay(top,self.player.display,elapsed=self.player.elapsed(),literal=self.player.stage_text)

    def redraw(self):
        if not hasattr(self,'panels'):return
        started=time.perf_counter();self.top=self.frame();metadata=deepcopy(self.current_metadata)
        display=self.player.display;identity=(display['source']['line'],display['raw']) if display else None
        if identity!=self.last_display:self.page=0;self.last_display=identity
        options=dict(elapsed=self.player.elapsed(),bilingual=self.bilingual.get(),window_visible=self.player.window_visible,quick_menu=self.player.quick_menu,auto=self.player.auto,auto_delay=self.player.auto_delay)
        self.lower,self.lower_info=self.text.lower(display,page=self.page,**options)
        if display and display['source'].get('who')=='extend' and identity!=getattr(self,'last_extended',None):
            self.page=self.lower_info['auto_page'];self.last_extended=identity
            self.lower,self.lower_info=self.text.lower(display,page=self.page,**options)
        if display:display['page_count']=self.lower_info['pages']
        self.put(self.panels[0][1],self.top);self.put(self.panels[0][2],self.lower)
        if self.compare.get():
            self.put(self.panels[1][1],self.frame('old'));self.put(self.panels[1][2],self.lower)
        self.render_ms.append((time.perf_counter()-started)*1000);self.render_ms=self.render_ms[-90:]
        source=display['source'] if display else {}
        text=(f"模式：{'仅动效循环' if self.mode.startswith('pure_') else '前后剧情'}\n原作：ep2d3:{self.player.line}\n图层："+' / '.join(x['name'] for x in self.player.layers)+
              f"\n剧情：278 条中文 / 8 组动画\n渲染均值：{sum(self.render_ms)/len(self.render_ms):.1f} ms（电脑）")
        if source:text+=f"\n译文：ep2d3.rpy:{source.get('translation_line','—')}\n标识：{source.get('translation_id','—')}"
        if self.player.wait_until is not None:text+=f"\n{'强制' if self.player.wait_hard else '普通'}停顿：{max(0,self.player.wait_until-self.player.now):.2f}s"
        if self.player.done:text+='\n已到验证片段末尾'
        errors=self.audio.status['errors']
        if errors:text+='\n音频：'+str(errors[-1])
        if self.player.diagnostics:text+='\n待支持：'+self.player.diagnostics[-1]
        if metadata['missing']:text+='\n待核：is10 缺帧，保持上一有效帧'
        if metadata.get('iss_text'):text+=f"\niss 文字：第 {metadata['iss_text']['step']}/10 档，{metadata['iss_text']['words']} 个 is"
        self.info.set(text)
        # Canvas redraws are idle work; acknowledge only the frame we queued,
        # not a future text state or a scene selected before this callback ran.
        serial=self.player.presentation_serial
        count=None
        if display:
            count=len(display['text'].glyphs) if display['revealed'] else display['text'].visible(self.player.elapsed())
        present_time=self.player.now
        self.root.after_idle(lambda:self.player.mark_presented(count,serial,present_time))

    def tick(self):
        now=time.perf_counter();dt=max(0.,now-self.last_tick);self.last_tick=now
        if not self.paused:
            self.player.tick(self.clock.consume(dt))
            if self.mode=='pure_red' and self.player.now-self.pure_start>=.45:
                self.player.layers=[x for x in self.player.layers if x['name']!='hands']
        self.redraw()
        current=time.perf_counter();self.next_frame_at+=1/60
        while self.next_frame_at<current:self.next_frame_at+=1/60
        self.job=self.root.after(max(1,math.ceil((self.next_frame_at-current)*1000)),self.tick)

    def transcript(self):
        window=tk.Toplevel(self.root);window.title('ep2d3 · 前后剧情核对');window.geometry('900x650')
        text=tk.Text(window,bg='black',fg='white',font=('Microsoft YaHei UI',11),wrap='word');text.pack(fill='both',expand=True)
        for e in self.story['events']:
            if e['kind']=='say':
                text.insert('end',f"\n[源 {e['line']} / 译文 {e['translation_line']}] {e['who'] or '旁白'}\n{e['text']}\n{e['original']}\n")
                if e['line']==self.player.line:text.mark_set('current_source','insert')
        text.configure(state='disabled')
        try:text.see('current_source')
        except tk.TclError:pass

    def export(self):
        self.redraw();dual=Image.new('RGB',(256,384));dual.paste(self.top,(0,0));dual.paste(self.lower,(0,192))
        for name,im in [('current_upper',self.top),('current_lower',self.lower),('current_dual',dual)]:im.save(OUT/(name+'.png'))
        dual.resize((512,768),Image.Resampling.NEAREST).save(OUT/'current_dual_2x.png')
        (OUT/'current_state.json').write_text(json.dumps(dict(preset=self.preset.get(),line=self.player.line,time=self.player.now,layers=self.player.layers,renderer=self.current_metadata,audio=self.audio.status,diagnostics=self.player.diagnostics,source=self.story['source_sha256']),ensure_ascii=False,indent=2),encoding='utf-8')
        self.note.set(f'已导出到 {OUT}')

    def close(self):
        if self.job is not None:self.root.after_cancel(self.job)
        self.audio.close();self.root.destroy()


def run(game=DEFAULT_GAME,scale=2,audio=True):
    root=tk.Tk();AnimPreview(root,game,scale,audio);root.mainloop()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--game',type=Path,default=DEFAULT_GAME)
    parser.add_argument('--scale',type=int,choices=(1,2),default=2);parser.add_argument('--no-audio',action='store_true')
    args=parser.parse_args();run(args.game,args.scale,not args.no_audio)


if __name__=='__main__':main()
