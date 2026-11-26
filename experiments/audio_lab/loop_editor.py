"""Reusable two-marker music editor. Saving never converts assets or builds a ROM."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import argparse
import bisect
import math
import queue
import subprocess
import sys
import traceback
import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np
from loop_editor_model import ROOT, RATE, load_sources, load_project, save_project, validate_state, parse_time, format_time, waveform_envelope, recent_project
from loop_editor_audio import PREVIEW_RATE, SoundPlayer
from loop_editor_shortcuts import TransportSpaceShortcut
sys.path.insert(0,str(ROOT/'nds/tools'))
from music_edit import render_edit

COLORS={'bg':'#101720','panel':'#182331','text':'#e6edf3','muted':'#a3b3c5','S':'#66e0b5','E':'#ff8f99'}
FIELDS={'S':'source_loop_start_frame','E':'source_loop_end_frame'}


class Waveform(tk.Canvas):
    def __init__(self,parent,app,overview=False,**kwargs):
        super().__init__(parent,bg='#0c131d',highlightthickness=0,**kwargs)
        self.app=app;self.overview=overview;self.drag=None;self.cache=None
        self.bind('<Configure>',lambda e:self.render())
        self.bind('<Button-1>',self.press);self.bind('<B1-Motion>',self.motion)
        self.bind('<ButtonRelease-1>',self.release)
        if not overview:
            self.bind('<MouseWheel>',self.wheel)
            self.bind('<Double-Button-1>',lambda e:app.play('original'))

    def bounds(self):
        return (0,self.app.duration()) if self.overview else (self.app.view_start,self.app.view_end)

    def time_at(self,x):
        a,b=self.bounds();return max(0,min(self.app.duration(),a+x/max(1,self.winfo_width())*(b-a)))

    def render(self):
        self.delete('all');w,h=self.winfo_width(),self.winfo_height()
        if not self.app.current or self.app.pcm is None or w<5:return
        a,b=self.bounds();duration=max(b-a,.001);state=self.app.state()
        s,e=(state[FIELDS[k]]/RATE for k in ('S','E'))
        x=lambda t:(t-a)/duration*w
        self.create_rectangle(x(s),0,x(e),h,fill='#16352f',outline='')
        if not self.overview:self.create_rectangle(x(0),0,x(s),h,fill='#2b2920',outline='')
        key=(id(self.app.pcm),round(a,6),round(b,6),w,h)
        if not self.cache or self.cache[0]!=key:
            low,high=waveform_envelope(self.app.pcm,a*PREVIEW_RATE,b*PREVIEW_RATE,w)
            polylines=[]
            for ch in range(2):
                top=4 if self.overview else 64
                bottom=h-5 if self.overview else h-27
                lane=(bottom-top)/2;center=top+(ch+.5)*lane;amplitude=lane*.43
                xs=np.linspace(0,w,len(low))
                upper=center-high[:,ch].astype(float)/32768*amplitude
                lower=center-low[:,ch].astype(float)/32768*amplitude
                points=np.column_stack((np.r_[xs,xs[::-1]],np.r_[upper,lower[::-1]])).ravel().tolist()
                polylines.append((center,points))
            self.cache=(key,polylines)
        for ch,(center,points) in enumerate(self.cache[1]):
            self.create_line(0,center,w,center,fill='#344354')
            self.create_polygon(points,fill='#78b5e7' if ch==0 else '#70c4c2',outline='')
        if self.overview:
            self.create_rectangle(x(self.app.view_start),1,x(self.app.view_end),h-1,outline='#dce4ed',width=2)
        else:
            if self.app.show_beats.get():
                beats=self.app.sources[self.app.current]['beats']
                last=-100
                for t in beats:
                    px=x(t)
                    if 0<=px<=w and px-last>=9:
                        self.create_line(px,62,px,h-25,fill='#334634');last=px
            step=next((v for v in [.001,.005,.01,.05,.1,.5,1,2,5,10,20,30,60,120] if v/duration*w>=82),120)
            for t in np.arange(math.ceil(a/step)*step,b+step/10,step):
                px=x(t);self.create_line(px,h-23,px,h-16,fill='#7c8fa5')
                label=f'{t:.3f}s' if step<1 else format_time(round(t*RATE))
                self.create_text(px+3,h-10,text=label,anchor='w',fill=COLORS['muted'],font=('Segoe UI',9))
            self.create_text(8,76,text='L',fill=COLORS['muted'],anchor='w')
            self.create_text(8,h*.64,text='R',fill=COLORS['muted'],anchor='w')
        for i,(letter,t) in enumerate([('S',s),('E',e)]):
            px=x(t)
            if not -1<=px<=w+1:continue
            self.create_line(px,0,px,h,fill=COLORS[letter],width=2)
            if not self.overview:
                tx=max(4,min(w-130,px+6));ty=14+i*25
                self.create_rectangle(tx-2,ty-11,tx+125,ty+11,fill='#10221e' if letter=='S' else '#321b23',outline=COLORS[letter])
                self.create_text(tx+4,ty,text=f'{letter}  {format_time(round(t*RATE))}',anchor='w',fill=COLORS[letter],font=('Segoe UI',10,'bold'))
        self.cursor()

    def cursor(self):
        self.delete('cursor');a,b=self.bounds();w=self.winfo_width()
        if a<=self.app.position<=b:
            x=(self.app.position-a)/max(.001,b-a)*w
            self.create_line(x,54 if not self.overview else 0,x,self.winfo_height(),fill='#f0f3f8',dash=(3,3),tags='cursor')

    def press(self,event):
        if self.app.pcm is None:return
        if self.overview:self.app.center_view(self.time_at(event.x));self.drag='view';return
        if not self.app.commit():return
        a,b=self.bounds();w=self.winfo_width();state=self.app.state();hits=[]
        for i,k in enumerate(('S','E')):
            px=(state[FIELDS[k]]/RATE-a)/max(.001,b-a)*w
            tx=max(4,min(w-130,px+6));ty=14+i*25
            if abs(event.x-px)<9 or (tx-2<=event.x<=tx+125 and abs(event.y-ty)<12):hits.append((abs(event.y-ty),k))
        if hits:
            self.drag=min(hits)[1];self.app.selected_marker.set(self.drag);self.app.player.cursor.stop()
        else:
            self.drag='seek';self.app.seek_source(self.time_at(event.x))

    def motion(self,event):
        if self.drag=='view':self.app.center_view(self.time_at(event.x))
        elif self.drag in ('S','E'):self.app.move_marker(self.drag,self.time_at(event.x),snap=True)
        elif self.drag=='seek':self.app.position=self.time_at(event.x);self.app.draw_cursor()

    def release(self,event):
        if self.drag in ('S','E'):self.app.move_marker(self.drag,self.time_at(event.x),snap=True)
        self.drag=None

    def wheel(self,event):
        if event.state&1:self.app.center_view((self.app.view_start+self.app.view_end)/2-(self.app.view_end-self.app.view_start)*event.delta/120*.12)
        else:self.app.zoom(.7 if event.delta>0 else 1/.7,self.time_at(event.x))


class Editor:
    def __init__(self,root,project,select=None):
        self.root=root;self.project=Path(project).resolve()
        self.sources,self.initial=load_sources();self.tracks=deepcopy(self.initial)
        if self.project.exists():self.tracks=load_project(self.project,self.sources);self.saved=deepcopy(self.tracks)
        else:self.saved=None
        self.current=None;self.pcm=None;self.position=0.;self.view_start=0.;self.view_end=1.
        self.updating=False;self.pending=set();self.player=SoundPlayer();self.play_mode=None
        self.pool=ThreadPoolExecutor(max_workers=2);self.messages=queue.Queue();self.generation=0;self.futures=[];self.cache=OrderedDict()
        self._build();root.protocol('WM_DELETE_WINDOW',self.close)
        root.bind('<Control-s>',lambda e:self.save_all())
        self.space_shortcut=TransportSpaceShortcut(root,self.toggle_pause)
        root.bind('<Escape>',lambda e:self.stop())
        root.bind('<Key-s>',lambda e:self.hot_marker('S'));root.bind('<Key-e>',lambda e:self.hot_marker('E'))
        first=select if select in self.sources else ('above.mp3' if 'above.mp3' in self.sources else next(iter(self.sources)))
        self.refresh_list();self.tree.selection_set(first);self.tree.see(first)
        root.after(60,self.tick)

    def _build(self):
        r=self.root;r.title('ZATO 音乐剪辑器 — S / E 人工标记')
        sw,sh=r.winfo_screenwidth(),r.winfo_screenheight();w=min(1400,sw-70);h=min(900,sh-90)
        r.geometry(f'{w}x{h}+35+35');r.minsize(min(1120,w),min(720,h));r.configure(bg=COLORS['bg'])
        style=ttk.Style();style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10),background=COLORS['panel'],foreground=COLORS['text'])
        style.configure('TButton',padding=(10,6));style.map('TButton',background=[('active','#35485e')])
        style.configure('Treeview',background='#111c29',fieldbackground='#111c29',rowheight=31)
        style.map('Treeview',background=[('selected','#31516c')]);style.configure('TEntry',fieldbackground='#26384b',foreground=COLORS['text'])
        outer=ttk.Frame(r,padding=12);outer.pack(fill='both',expand=True)
        heading=ttk.Frame(outer);heading.pack(fill='x',pady=(0,10))
        ttk.Label(heading,text='ZATO / 音乐剪辑',font=('Microsoft YaHei UI',17,'bold')).pack(side='left')
        ttk.Label(heading,text='首次 0 → E　 /　以后 S → E 循环',foreground=COLORS['S']).pack(side='left',padx=25)
        ttk.Button(heading,text='保存全部  Ctrl+S',command=self.save_all).pack(side='right')
        body=ttk.Panedwindow(outer,orient='horizontal');body.pack(fill='both',expand=True)
        left=ttk.Frame(body,padding=(0,0,10,0));right=ttk.Frame(body);body.add(left,weight=0);body.add(right,weight=1)
        ttk.Label(left,text='原长 > 1 分钟 · 23 首',font=('Microsoft YaHei UI',11,'bold')).pack(anchor='w',pady=(0,8))
        self.tree=ttk.Treeview(left,columns=('duration','state'),show='tree headings',selectmode='browse',height=20)
        self.tree.heading('#0',text='曲目');self.tree.column('#0',width=155,minwidth=110)
        self.tree.heading('duration',text='原长');self.tree.column('duration',width=78,anchor='center',stretch=False)
        self.tree.heading('state',text='状态');self.tree.column('state',width=64,anchor='center',stretch=False)
        self.tree.pack(fill='both',expand=True);self.tree.bind('<<TreeviewSelect>>',self.select_track)
        self.tree.tag_configure('done',foreground=COLORS['S']);self.tree.tag_configure('dirty',foreground='#ffd28a')
        self.progress=ttk.Label(left,text='');self.progress.pack(anchor='w',pady=8)
        ttk.Button(left,text='保存当前曲目',command=self.save_current).pack(fill='x')
        self.title=ttk.Label(right,text='',font=('Microsoft YaHei UI',15,'bold'));self.title.pack(anchor='w')
        self.summary=ttk.Label(right,text='',foreground=COLORS['muted']);self.summary.pack(anchor='w',pady=(3,7))
        self.overview=Waveform(right,self,overview=True,height=67);self.overview.pack(fill='x',pady=(0,6))
        self.wave=Waveform(right,self,height=340);self.wave.pack(fill='both',expand=True)
        self.scroll=ttk.Scrollbar(right,orient='horizontal',command=self.scroll_view);self.scroll.pack(fill='x',pady=(3,5))
        nav=ttk.Frame(right);nav.pack(fill='x')
        for label,cmd in [('全曲',self.full_view),('聚焦 S–E',self.focus_loop),('放大 +',lambda:self.zoom(.6)),('缩小 −',lambda:self.zoom(1/.6))]:ttk.Button(nav,text=label,command=cmd).pack(side='left',padx=(0,4))
        self.show_beats=tk.BooleanVar(value=False);self.snap_beats=tk.BooleanVar(value=False)
        ttk.Checkbutton(nav,text='显示参考拍点',variable=self.show_beats,command=self.render).pack(side='left',padx=5)
        ttk.Checkbutton(nav,text='拖动吸附拍点',variable=self.snap_beats).pack(side='left')
        fields=ttk.Frame(right,padding=(0,10));fields.pack(fill='x')
        self.selected_marker=tk.StringVar(value='S');self.times={};self.entries={}
        for col,(k,label) in enumerate([('S','前奏结束 / 循环开始'),('E','循环结束')]):
            pane=ttk.Frame(fields);pane.grid(row=0,column=col,sticky='w',padx=(0,20))
            ttk.Radiobutton(pane,text=f'{k}　{label}',variable=self.selected_marker,value=k).pack(anchor='w')
            variable=tk.StringVar();self.times[k]=variable
            entry=ttk.Entry(pane,textvariable=variable,width=18,font=('Consolas',12));entry.pack(anchor='w',pady=3);self.entries[k]=entry
            variable.trace_add('write',lambda *_,key=k:self.field_dirty(key))
            entry.bind('<Return>',lambda e,key=k:self.apply_field(key));entry.bind('<FocusOut>',lambda e,key=k:self.apply_field(key))
        pane=ttk.Frame(fields);pane.grid(row=0,column=2,sticky='nw')
        ttk.Label(pane,text='接缝淡化 (ms)').pack(anchor='w')
        self.fade=tk.StringVar(value='20');self.fade.trace_add('write',lambda *_:self.field_dirty('fade'))
        entry=ttk.Entry(pane,textvariable=self.fade,width=8);entry.pack(anchor='w',pady=3)
        entry.bind('<Return>',lambda e:self.apply_field('fade'));entry.bind('<FocusOut>',lambda e:self.apply_field('fade'))
        step=ttk.Frame(right);step.pack(fill='x')
        ttk.Label(step,text='微调所选标记：').pack(side='left')
        for label,delta in [('−1s',-1),('−10ms',-.01),('+10ms',.01),('+1s',1)]:ttk.Button(step,text=label,command=lambda d=delta:self.nudge(d)).pack(side='left',padx=2)
        ttk.Button(step,text='上一拍',command=lambda:self.nudge_beat(-1)).pack(side='left',padx=(10,2))
        ttk.Button(step,text='下一拍',command=lambda:self.nudge_beat(1)).pack(side='left',padx=2)
        playback=ttk.Frame(right,padding=(0,10));playback.pack(fill='x')
        for label,mode in [('原曲 / 从光标','original'),('只听循环段','loop'),('前奏一次＋循环','arranged'),('试听接缝','seam')]:ttk.Button(playback,text=label,command=lambda m=mode:self.play(m)).pack(side='left',padx=(0,4))
        ttk.Button(playback,text='暂停 / 继续',command=self.toggle_pause).pack(side='left',padx=2)
        ttk.Button(playback,text='停止',command=self.stop).pack(side='left',padx=2)
        meta=ttk.Frame(right);meta.pack(fill='x')
        self.keep=tk.BooleanVar();self.reviewed=tk.BooleanVar()
        ttk.Checkbutton(meta,text='保留整曲',variable=self.keep,command=self.set_keep).pack(side='left')
        ttk.Checkbutton(meta,text='本曲已手调确认',variable=self.reviewed,command=self.set_reviewed).pack(side='left',padx=12)
        ttk.Label(meta,text='音量').pack(side='left',padx=(12,2))
        self.volume=tk.DoubleVar(value=75)
        ttk.Scale(meta,from_=0,to=100,variable=self.volume,command=lambda v:setattr(self.player.cursor,'volume',float(v)/100),length=100).pack(side='left')
        self.player.cursor.volume=.75
        self.transport=ttk.Label(meta,text='',foreground=COLORS['muted']);self.transport.pack(side='right')
        note=ttk.Frame(right);note.pack(fill='x',pady=(8,0));ttk.Label(note,text='备注　').pack(side='left')
        self.note=tk.StringVar();self.note.trace_add('write',self.note_changed);ttk.Entry(note,textvariable=self.note).pack(side='left',fill='x',expand=True)
        ttk.Label(right,text='点击波形定位；拖动 S/E；滚轮缩放，Shift+滚轮平移；Space 播放/暂停，S/E 将标记设到光标。',foreground=COLORS['muted'],font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(8,2))
        self.status=ttk.Label(outer,text='',foreground=COLORS['muted']);self.status.pack(anchor='w',pady=(9,2))
        ttk.Label(outer,text=f'人工设置：{self.project}　｜　仅保存标记，转换/导入等待你确认',font=('Microsoft YaHei UI',9),foreground=COLORS['muted']).pack(anchor='w')

    def state(self):return self.tracks[self.current]
    def duration(self):return self.sources[self.current]['frames']/RATE if self.current else 1.
    def dirty(self):return self.saved is None or self.tracks!=self.saved or bool(self.pending)

    def refresh_list(self):
        selected=self.tree.selection()
        for name,source in self.sources.items():
            changed=self.saved is None or self.tracks[name]!=self.saved[name]
            done=self.tracks[name]['reviewed'];tag='dirty' if changed else ('done' if done else '')
            text=('已调' if done else '待调')+('*' if changed else '')
            if self.tree.exists(name):self.tree.item(name,values=(format_time(source['frames']),text),tags=(tag,))
            else:self.tree.insert('', 'end',iid=name,text=name,values=(format_time(source['frames']),text),tags=(tag,))
        count=sum(s['reviewed'] for s in self.tracks.values())
        self.progress.configure(text=f'已手调 {count} / {len(self.tracks)}　* 为未保存')
        self.root.title(f'ZATO 音乐剪辑器 — S / E 人工标记'+(' *' if self.dirty() else ''))

    def select_track(self,event=None):
        selection=self.tree.selection()
        if not selection:return
        name=selection[0]
        if name==self.current:return
        if self.current and not self.commit():self.tree.selection_set(self.current);return
        self.player.cursor.stop();self.play_mode=None;self.current=name;self.pcm=None;self.position=0
        self.view_start=0;self.view_end=self.duration();self.generation+=1
        self.fill_fields();self.title.configure(text=name)
        self.status.configure(text='正在读取原曲并生成波形…');self.render()
        for f in self.futures:f.cancel()
        if name in self.cache:self.pcm=self.cache[name];self.cache.move_to_end(name);self.loaded();return
        generation=self.generation;source=self.sources[name]
        def load():
            try:
                raw=subprocess.check_output(['ffmpeg','-v','error','-i',source['path'],'-vn','-ar',str(PREVIEW_RATE),'-ac','2','-f','s16le','-c:a','pcm_s16le','-'])
                pcm=np.frombuffer(raw,dtype='<i2').reshape(-1,2)
                self.messages.put((generation,name,pcm,None))
            except Exception as error:self.messages.put((generation,name,None,str(error)))
        self.futures=[self.pool.submit(load)]

    def loaded(self):
        self.wave.cache=None;self.overview.cache=None;self.status.configure(text='原曲已就绪。S 同时是前奏结束点；保存不会转换音频或更新游戏。')
        self.render();self.update_summary()

    def fill_fields(self):
        self.updating=True;state=self.state()
        for k,key in FIELDS.items():self.times[k].set(format_time(state[key]))
        self.fade.set(f'{state["crossfade_frames"]*1000/RATE:.3f}'.rstrip('0').rstrip('.'))
        self.keep.set(state['keep_full']);self.reviewed.set(state['reviewed']);self.note.set(state.get('note',''))
        self.pending.clear();self.updating=False;self.update_summary()

    def update_summary(self):
        if not self.current:return
        state=self.state();source=self.sources[self.current]
        try:
            validate_state(state,source['frames'])
            end=source['frames'] if state['keep_full'] else state['source_loop_end_frame']
            start=0 if state['keep_full'] else state['source_loop_start_frame']
            self.summary.configure(text=f'原长 {format_time(source["frames"])}　首次 {format_time(end)}　循环 {format_time(end-start)}　BPM 参考 {source["bpm"]:.2f}（可关闭拍点）',foreground=COLORS['muted'])
        except ValueError as e:self.summary.configure(text=str(e),foreground=COLORS['E'])

    def field_dirty(self,key):
        if not self.updating and self.current:self.pending.add(key)

    def apply_field(self,key,quiet=True):
        if key not in self.pending:return True
        try:
            if key=='fade':
                value=float(self.fade.get())
                if not math.isfinite(value) or not 0<=value<=200:raise ValueError('淡化范围为 0–200 ms')
                frame=round(value*RATE/1000);self.state()['crossfade_frames']=frame
            else:
                frame=parse_time(self.times[key].get())
                self.state()[FIELDS[key]]=frame
                if key=='S':self.state()['intro_end_frame']=frame
            self.pending.discard(key);self.changed();return True
        except ValueError as e:
            self.status.configure(text=str(e))
            if not quiet:messagebox.showerror('时间输入有误',str(e),parent=self.root)
            return False

    def commit(self,validate=False):
        for key in list(self.pending):
            if not self.apply_field(key,quiet=False):return False
        if validate:
            try:validate_state(self.state(),self.sources[self.current]['frames'])
            except ValueError as e:messagebox.showerror('标记范围有误',str(e),parent=self.root);return False
        return True

    def changed(self):
        self.state()['keep_full']=False;self.state()['reviewed']=False;self.keep.set(False);self.reviewed.set(False)
        self.player.cursor.stop();self.play_mode=None;self.update_summary();self.refresh_list();self.render()

    def move_marker(self,k,seconds,snap=False):
        if self.pcm is None:return
        if snap and self.snap_beats.get() and self.sources[self.current]['beats']:
            seconds=min(self.sources[self.current]['beats'],key=lambda t:abs(t-seconds))
        state=self.state();frame=max(0,min(self.sources[self.current]['frames'],round(seconds*RATE)))
        frame=min(frame,state['source_loop_end_frame']-1) if k=='S' else max(frame,state['source_loop_start_frame']+1)
        state[FIELDS[k]]=frame
        if k=='S':state['intro_end_frame']=frame
        self.changed();self.fill_fields()

    def nudge(self,seconds):
        if self.current and self.commit():
            k=self.selected_marker.get();self.move_marker(k,self.state()[FIELDS[k]]/RATE+seconds)

    def nudge_beat(self,direction):
        if not self.current or not self.commit():return
        k=self.selected_marker.get();t=self.state()[FIELDS[k]]/RATE;beats=self.sources[self.current]['beats']
        if beats:
            i=bisect.bisect_right(beats,t+1e-6) if direction>0 else bisect.bisect_left(beats,t-1e-6)-1
            target=beats[max(0,min(len(beats)-1,i))]
        else:target=t+direction*60/max(1,self.sources[self.current]['bpm'] or 120)
        self.move_marker(k,target)

    def set_keep(self):
        if self.keep.get():
            self.state().update(intro_end_frame=0,source_loop_start_frame=0,source_loop_end_frame=self.sources[self.current]['frames'])
        self.state()['keep_full']=self.keep.get();self.state()['reviewed']=False
        self.player.cursor.stop();self.play_mode=None;self.fill_fields();self.refresh_list();self.render()

    def set_reviewed(self):
        wanted=self.reviewed.get()
        if not self.commit(validate=True):self.reviewed.set(False);return
        self.state()['reviewed']=wanted;self.reviewed.set(wanted);self.refresh_list()

    def note_changed(self,*_):
        if not self.updating and self.current:self.state()['note']=self.note.get();self.refresh_list()

    def render(self):
        self.wave.render();self.overview.render()
        total=self.duration();self.scroll.set(self.view_start/total,min(1,self.view_end/total))

    def full_view(self):self.view_start=0;self.view_end=self.duration();self.render()
    def focus_loop(self):
        if not self.current:return
        s=self.state();self.view_start=max(0,s['source_loop_start_frame']/RATE-2);self.view_end=min(self.duration(),s['source_loop_end_frame']/RATE+2);self.render()

    def center_view(self,center):
        width=self.view_end-self.view_start;self.view_start=max(0,min(self.duration()-width,center-width/2));self.view_end=self.view_start+width;self.render()

    def zoom(self,factor,anchor=None):
        if not self.current:return
        old=self.view_end-self.view_start;anchor=self.position if anchor is None else anchor
        if not self.view_start<=anchor<=self.view_end:anchor=(self.view_start+self.view_end)/2
        ratio=(anchor-self.view_start)/max(.001,old);width=max(.02,min(self.duration(),old*factor))
        self.view_start=max(0,min(self.duration()-width,anchor-width*ratio));self.view_end=self.view_start+width;self.render()

    def scroll_view(self,action,value,*args):
        width=self.view_end-self.view_start
        start=float(value)*self.duration() if action=='moveto' else self.view_start+float(value)*width*.1
        self.center_view(start+width/2)

    def seek_source(self,t):
        playing=self.player.cursor.snapshot()['playing'];self.player.cursor.stop();self.play_mode=None;self.position=t;self.draw_cursor()
        if playing:self.play('original')

    def play(self,mode):
        if self.pcm is None:return
        if not self.commit(validate=mode!='original'):return
        try:
            state=self.state();position=0;loop_start=None;pcm=self.pcm
            if mode=='original':
                position=min(len(pcm),round(self.position*PREVIEW_RATE))
                if position>=len(pcm):position=0
            else:
                if state['keep_full']:loop_start=0
                else:
                    edit={k:round(state[k]*PREVIEW_RATE/RATE) for k in ('intro_end_frame','source_loop_start_frame','source_loop_end_frame','crossfade_frames')}
                    edit['source_loop_end_frame']=min(len(pcm),edit['source_loop_end_frame'])
                    pcm,loop_start,_=render_edit(pcm,edit)
                if mode=='loop':pcm=pcm[loop_start:];loop_start=0
                elif mode=='seam':position=max(loop_start,len(pcm)-3*PREVIEW_RATE)
            self.player.play(pcm,position=position,loop_start=loop_start)
            self.play_mode=mode;self.status.configure(text='原曲 PCM 试听中；Space 暂停/继续，Esc 停止。修改标记后请重新试听。')
        except Exception as e:messagebox.showerror('试听失败',str(e),parent=self.root)

    def toggle_pause(self):
        s=self.player.cursor.snapshot()
        if s['playing']:self.player.cursor.pause()
        elif s['loaded'] and not s['eof']:self.player.cursor.resume()
        else:self.play('original')

    def hot_marker(self,k):
        if not isinstance(self.root.focus_get(),(ttk.Entry,tk.Entry)) and self.current:
            self.selected_marker.set(k);self.move_marker(k,self.position)

    def stop(self):self.player.cursor.stop();self.play_mode=None;self.transport.configure(text='已停止')

    def draw_cursor(self):self.wave.cursor();self.overview.cursor()

    def tick(self):
        try:
            while True:
                generation,name,pcm,error=self.messages.get_nowait()
                if pcm is not None:
                    self.cache[name]=pcm;self.cache.move_to_end(name)
                    while len(self.cache)>2:self.cache.popitem(last=False)
                if generation==self.generation:
                    if error:self.status.configure(text=f'读取失败：{error}')
                    else:self.pcm=pcm;self.loaded()
        except queue.Empty:pass
        state=self.player.cursor.snapshot()
        if state['loaded'] and self.play_mode:
            self.position=state['position']/PREVIEW_RATE
            if self.play_mode=='loop' and not self.state()['keep_full']:self.position+=self.state()['source_loop_start_frame']/RATE
            self.draw_cursor();self.transport.configure(text=f'{"播放" if state["playing"] else "暂停/结束"} {format_time(round(self.position*RATE))}　循环 {state["wraps"]}')
        self.root.after(40,self.tick)

    def save_all(self):
        if self.current and not self.commit(validate=True):return
        try:
            save_project(self.project,self.tracks,self.sources);self.saved=deepcopy(self.tracks)
            self.refresh_list();self.status.configure(text='全部设置已保存。关闭后可继续调整；尚未转换或更新游戏。')
        except Exception as e:messagebox.showerror('保存失败',str(e),parent=self.root)

    def save_current(self):
        if not self.current or not self.commit(validate=True):return
        baseline=deepcopy(self.saved if self.saved is not None else self.initial);baseline[self.current]=deepcopy(self.state())
        try:
            save_project(self.project,baseline,self.sources);self.saved=baseline;self.refresh_list();self.status.configure(text=f'{self.current} 已保存，其余未保存修改仍保留在窗口中。')
        except Exception as e:messagebox.showerror('保存失败',str(e),parent=self.root)

    def close(self):
        if self.dirty():
            answer=messagebox.askyesnocancel('保存人工标记？','还有未保存的设置。是否保存后关闭？',parent=self.root)
            if answer is None:return
            if answer:
                self.save_all()
                if self.dirty():return
        self.player.close();self.pool.shutdown(wait=False,cancel_futures=True);self.root.destroy()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path);parser.add_argument('--select')
    args=parser.parse_args();root=tk.Tk()
    def report(kind,error,tb):
        log=Path(__file__).resolve().parent/'out/editor.log';log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('a',encoding='utf-8') as f:traceback.print_exception(kind,error,tb,file=f)
        messagebox.showerror('音乐剪辑器',str(error),parent=root)
    root.report_callback_exception=report
    try:Editor(root,args.project or recent_project(),args.select)
    except Exception as e:report(type(e),e,e.__traceback__);root.destroy();return
    root.mainloop()


if __name__=='__main__':main()
