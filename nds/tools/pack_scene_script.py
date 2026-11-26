"""Compile only the audited ep2d3 fixture; unknown source behavior is an error."""
from pathlib import Path
import argparse,json,re,struct,sys,zlib,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/'experiments/asset_lab'))
from anim_story import build_story
OPS={name:i+1 for i,name in enumerate('TEXT GFX WAIT TRANSITION MUSIC STOP_MUSIC SFX STOP_SFX WINDOW CLEAR LITERAL HIDE_LITERAL END'.split())}
EVENT=struct.Struct('<HHI4i');HEADER=struct.Struct('<8s8I');CHECKPOINT=struct.Struct('<I24s')
CHECKPOINTS=[(240,'Before /240'),(564,'Video /564'),(573,'Keyboard /573'),(599,'Glitch /599'),
             (613,'Red /613'),(653,'Mirror /653'),(688,'After /688'),(777,'Window /777')]

def group_for(layers):
    if 'hands' in layers:return 'hands'
    if 'iss' in layers:return 'iss'
    if 'windo' in layers:return 'window'
    if 'ilooked' in layers or 'bodyshop' in layers:return 'mirror'
    if layers & {'carfix_red','ghost','tface','deface'}:return 'red'
    if 'carfix' in layers:return 'carfix'
    return 'black'

def compile_story(story,text_ids,literal_ids,gfx_ids,audio_assets):
    result=[];layers=set();previous=None
    def emit(op,line=0,a=0,b=0,c=0,d=0,flags=0):result.append(dict(op=OPS[op],name=op,line=line,a=a,b=b,c=c,d=d,flags=flags))
    for e in story['opening_setup']:
        if e['kind']=='scene':layers.clear()
        if e['kind'] in ('show','scene'):layers.add(e['image'])
        elif e['kind']=='hide':layers.discard(e['image'])
    def graphic(line):
        nonlocal previous
        name=group_for(layers)
        if name!=previous:
            phase={'hands':1,'red':1,'mirror':2,'iss':3,'window':4}.get(name,0)
            reset=bool(phase) and not(previous=='hands' and name=='red')
            emit('GFX',line,gfx_ids[name],phase,int(reset));previous=name
    graphic(0);emit('WINDOW',0,1)
    for e in story['events']:
        kind=e['kind'];line=e['line']
        if kind=='say':emit('TEXT',line,int(text_ids[str(line)]),flags=int('{nw}' in e['text']))
        elif kind in ('show','scene','hide'):
            if e['image']=='text':
                emit('HIDE_LITERAL' if kind=='hide' else 'LITERAL',line,0 if kind=='hide' else int(literal_ids[str(line)]));continue
            if kind=='scene':layers.clear()
            if kind=='hide':layers.discard(e['image'])
            else:layers.add(e['image'])
            graphic(line)
        elif kind=='wait':emit('WAIT',line,round(e['seconds']*60),int(e['hard']))
        elif kind=='with':
            expr=e['expr'];m=re.fullmatch(r'(Pause|Dissolve)\(([\d.]+)\)',expr)
            if m:emit('WAIT' if m[1]=='Pause' else 'TRANSITION',line,round(float(m[2])*60),0)
            elif expr=='dissolve':emit('TRANSITION',line,30)
            else:raise ValueError(f'Unsupported transition {expr} @{line}')
        elif kind=='command':
            command=e['value']
            play=re.fullmatch(r'play\s+(music|sound)\s*["\'](.+?)["\'](.*)',command)
            stop=re.fullmatch(r'stop\s+(music|sound)(.*)',command)
            if play:
                group='music' if play[1]=='music' else 'sfx';name=play[2].removeprefix('audio/')
                matches=[x for x in audio_assets if x['group']==group and name in x.get('aliases',[x['source']])]
                if len(matches)!=1:raise ValueError((group,name,matches))
                asset=matches[0];gain=re.search(r'volume\s+([\d.]+)',play[3]);fade=re.search(r'fadein\s+([\d.]+)',play[3])
                volume=(float(gain[1]) if gain else 1.)*asset.get('alias_gains',{}).get(name,1.)*.5
                emit('MUSIC' if group=='music' else 'SFX',line,asset['index'],round(volume*4096),round(float(fade[1])*22050) if fade else 0)
            elif stop:
                fade=re.search(r'fadeout\s+([\d.]+)',stop[2])
                emit('STOP_MUSIC' if stop[1]=='music' else 'STOP_SFX',line,round(float(fade[1])*22050) if fade else 0)
            elif command.startswith('window '):emit('WINDOW',line,int(command.split()[1]!='hide'))
            elif command=='nvl clear':emit('CLEAR',line)
            elif command in ('nvl show','nvl hide'):emit('WINDOW',line,int(command=='nvl show'))
            else:raise ValueError(f'Unsupported command {command} @{line}')
        else:raise ValueError(f'Unsupported event {kind} @{line}')
    emit('END',story['last_line']+1)
    checkpoints=[dict(line=line,name=name,pc=next(i for i,e in enumerate(result) if e['line']>=line)) for line,name in CHECKPOINTS]
    return result,checkpoints

def main():
    p=argparse.ArgumentParser();p.add_argument('--text',type=Path,default=ROOT/'artifacts/scene_text_manifest.json');p.add_argument('--gfx',type=Path,default=ROOT/'artifacts/scene_gfx_manifest.json');args=p.parse_args()
    text=json.loads(args.text.read_text(encoding='utf-8'));gfx=json.loads(args.gfx.read_text(encoding='utf-8'));audio=json.loads((ROOT/'artifacts/audio_manifest.json').read_text())
    groups=gfx['groups'];gfx_ids={x['name']:x['id'] for x in groups} if isinstance(groups,list) else {k:v['id'] for k,v in groups.items()}
    events,checkpoints=compile_story(build_story(),text['line_to_id'],text['literal_line_to_id'],gfx_ids,audio['assets'])
    payload=b''.join(EVENT.pack(e['op'],e['flags'],e['line'],e['a'],e['b'],e['c'],e['d']) for e in events)
    offset=HEADER.size+len(payload)
    payload+=b''.join(CHECKPOINT.pack(c['pc'],c['name'].encode().ljust(24,b'\0')) for c in checkpoints)
    header=HEADER.pack(b'ZSCENE01',1,len(events),len(checkpoints),24,HEADER.size,offset,HEADER.size+len(payload),zlib.crc32(payload))
    out=ROOT/'nitrofs/scene/script.bin';out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(header+payload)
    report=dict(events=events,checkpoints=checkpoints,bytes=out.stat().st_size,sha256=hashlib.sha256(out.read_bytes()).hexdigest(),gfx_ids=gfx_ids,source='ep2d3:240..815')
    (ROOT/'artifacts/scene_script_manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'PASS scene script: {len(events)} events, {len(checkpoints)} checkpoints, {out.stat().st_size}B')
if __name__=='__main__':main()
