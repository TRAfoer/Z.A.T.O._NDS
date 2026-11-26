"""Read-only real-C compositor proof against original-based straight-alpha PNGs.

Builds a private host executable and cases in a TemporaryDirectory. It never
packs assets, edits source, writes ROMs, or changes shared build directories.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tempfile
import numpy as np
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tests'));sys.path.insert(0,str(ROOT/'nds/tools'))
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
from story_gfx_oracle import Oracle
from test_story_gfx import write_cases
from asset_model import DEFAULT_GAME

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rgb(words):
    return Image.fromarray(np.stack([(((words>>shift)&31).astype(np.uint16)*255+15)//31 for shift in (0,5,10)],axis=2).astype('u1'))

def run(args):
    manifest=json.loads(args.manifest.read_text(encoding='utf8'));oracle=Oracle(manifest_path=args.manifest)
    pack=Path(manifest['asset_pack_source']);catalog=args.catalog;before={p:sha(p) for p in (pack,catalog,args.manifest)}
    assets=json.loads(Path(manifest['asset_pack_manifest']).read_text(encoding='utf8'));ids=manifest['name_to_image_id']
    assert before[pack]==manifest['asset_pack_sha256']==assets['package_sha256']
    selected=assets['soft_alpha']['frame_ids'];source_checks=[]
    frozen=json.loads((ROOT/'nds/artifacts/assets_manifest.json').read_text(encoding='utf8'))['frames']
    for id in selected:
        f=assets['frames'][id];source=DEFAULT_GAME/f['name']
        with Image.open(source) as opened:original=opened.convert('RGBA')
        with Image.open(f['master']) as opened:master=opened.convert('RGBA')
        if id!=150:
            crop=original.crop((0,0,original.width,min(572,original.height) if original.height>=700 else original.height))
            aligned=Image.new('RGBA',(master.width*2,master.height*2));aligned.paste(crop,(0,0))
            expected=aligned.resize(master.size,Image.Resampling.BOX)
            assert master.tobytes()==expected.tobytes(),f['name']+' source BOX differs'
        else:
            # Source alpha and the separately frozen approved sixteen-color
            # PNG establish BG truth without consulting new packed tiles.
            previous=frozen[id];crop=original.crop(previous['source_crop'])
            aligned=Image.new('RGBA',(master.width*4,master.height*4));aligned.paste(crop,(0,0))
            box=np.asarray(aligned.resize(master.size,Image.Resampling.BOX))
            old_path=ROOT/'nds'/previous['master']
            with Image.open(old_path) as image:approved=np.asarray(image.convert('RGBA'))
            current=np.asarray(master);visible=approved[:,:,3]>=128
            np.testing.assert_array_equal(current[:,:,3],np.where(visible,box[:,:,3],0))
            quantize=lambda a:(a[:,:,:3].astype(np.uint16)*31+127)//255
            np.testing.assert_array_equal(quantize(current)[visible],quantize(approved)[visible])
        source_checks.append(dict(id=id,name=f['name'],source_sha256=sha(source),master_sha256=sha(f['master']),alpha_levels=len(np.unique(np.asarray(master)[:,:,3]))))
    actions=[];goldens=[];samples=[]
    def case(layers,ticks,label,preview=False):
        actions.append(((0,0,0,0,0,0,0,0),None))
        for tag,(image,place,program,start) in enumerate(layers):actions.append(((1,start,image,tag,place,program,-1,0),None))
        pixels=oracle.draw(layers,ticks);goldens.append(dict(label=label,ticks=ticks,layers=layers,sha256=hashlib.sha256(pixels.tobytes()).hexdigest()))
        actions.append(((3,ticks,0,0,0,0,0,0),pixels))
        if preview:samples.append((label,layers,ticks,pixels))
    # Every selected original on both dark and light backgrounds, plus actual
    # source BG160 and both PC-derived left/right halfbody positions.
    for id in selected:
        for bg in (None,ids['white'],ids['garagessummerb1']):
            for place in ((0,) if id==150 else (0,1,2)):
                layers=([(bg,0,0,0)] if bg is not None else [])+[(id,place,0,0)]
                case(layers,0,f'frame{id} bg{bg} place{place}',bg is None and place==0)
        # Existing explicit global multiply, add, flip and inverse sampling.
        for program,tick in ((1,0),(10,0),(12,0),(21,0),(2,5400)):
            case([(ids['cafe1'],0,0,0),(id,0,program,0)],tick,f'frame{id} program{program} tick{tick}')
    for bg in (None,ids['white'],ids['garagessummerb1']):
        for program in (0,1,12):
            for tick in (0,1,6,11,12,13,18,23,24,35,36,47,48,59,60,71,72,73,84,143,144):
                layers=([(bg,0,0,0)] if bg is not None else [])+[(ids['mublur'],0,program,0)]
                case(layers,tick,f'mublur bg{bg} program{program} tick{tick}',bg==ids['garagessummerb1'] and program==0 and tick in (0,12,18,72))
    case([(ids['gubw'],1,0,0),(ids['mubw'],2,0,0)],0,'source ep2d6:682 dual gray actors',True)
    compiler=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
    if not compiler:raise RuntimeError('Host C compiler unavailable')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='zato_soft_oracle_') as temp:
        tmp=Path(temp);exe=tmp/'compositor.exe';cases=tmp/'source.cases';write_cases(cases,actions)
        subprocess.run([compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),str(ROOT/'nds/source/story_gfx.c'),str(ROOT/'nds/tests/validate_story_gfx.c'),'-o',str(exe)],check=True)
        native=subprocess.run([str(exe),str(pack),str(catalog),str(cases)],capture_output=True,text=True)
        if native.returncode:raise AssertionError(native.stdout+native.stderr)
    # Contact sheet is a source-based host preview. Every shown pixel has just
    # been compared to actual story_gfx.c; this is explicitly not an NDS shot.
    legacy=Oracle(manifest_path=args.manifest);legacy.frames=frozen
    out=Image.new('RGB',(512,24+len(samples)*210),(24,24,24));draw=ImageDraw.Draw(out)
    draw.text((4,4),'HOST PIXEL PROOF / OLD BINARY | TRUE ALPHA / NOT AN NDS CAPTURE',fill='white')
    for n,(label,layers,tick,pixels) in enumerate(samples):
        y=24+n*210;draw.text((3,y),label,fill='white');out.paste(rgb(legacy.draw(layers,tick)),(0,y+18));out.paste(rgb(pixels),(256,y+18))
    preview=args.output.with_suffix('.png');out.save(preview)
    peak=re.search(r'peak heap (\d+)',native.stdout)
    report=dict(result='PASS',scope='Original RGBA BOX masters, all11 selected images, black/white/source backgrounds, left/right/clipping, sequence seams, existing global alpha/add/multiply/flip/scale. Full49152-pixel actual C comparison; no draw/version graphics I/O.',
        assets_path=str(pack),assets_sha256=before[pack],catalog_path=str(catalog),catalog_sha256=before[catalog],manifest_sha256=before[args.manifest],
        compositor_sha256=sha(ROOT/'nds/source/story_gfx.c'),oracle_sha256=sha(ROOT/'nds/tests/story_gfx_oracle.py'),tool_sha256=sha(__file__),
        golden_count=len(goldens),actions=len(actions),peak_host_heap_bytes=int(peak[1]) if peak else None,native=native.stdout.strip(),source_checks=source_checks,goldens=goldens,preview=str(preview),
        alpha_contract='Q5=(alpha8+4)>>3; global/sequence Q12 rounding as documented in source metadata; oldfmt0/1 branch unchanged.')
    assert all(sha(path)==digest for path,digest in before.items())
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:report[k] for k in ('result','golden_count','actions','peak_host_heap_bytes','assets_sha256','native','preview')},indent=2),flush=True)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=ROOT/'nds/artifacts/story_gfx_manifest.json')
    p.add_argument('--catalog',type=Path,default=ROOT/'nds/nitrofs/story/gfx.bin')
    p.add_argument('--output',type=Path,default=ROOT/'nds/artifacts/story_soft_alpha_pixel_verification.json')
    run(p.parse_args())
if __name__=='__main__':main()
