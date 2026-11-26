"""Check the ROM's actual NitroFS bytes, including approved audio preservation."""
from pathlib import Path
import argparse,hashlib,json,struct

ROOT=Path(__file__).resolve().parents[1]

def files(rom):
    data=Path(rom).read_bytes()
    if len(data)<512:raise ValueError('ROM header truncated')
    fo,fn,ao,an=struct.unpack_from('<4I',data,0x40)
    if fn<8 or an%8 or fo+fn>len(data) or ao+an>len(data):raise ValueError('FNT/FAT bounds')
    dirs=struct.unpack_from('<H',data,fo+6)[0]
    if not dirs or dirs*8>fn:raise ValueError('Directory count')
    result={};visited=set()
    def walk(id,prefix):
        index=id-0xf000
        if not 0<=index<dirs or id in visited:raise ValueError('Directory reference')
        visited.add(id)
        offset,fileid,_=struct.unpack_from('<IHH',data,fo+index*8)
        at=fo+offset
        while True:
            if not fo<=at<fo+fn:raise ValueError('Directory entry bounds')
            size=data[at];at+=1
            if not size:break
            n=size&127
            if not n or at+n+(2 if size&128 else 0)>fo+fn:raise ValueError('Name bounds')
            name=data[at:at+n].decode('utf-8');at+=n
            if '/' in name or name in ('.','..'):raise ValueError('Unsafe name')
            path=prefix+name
            if size&128:
                child=struct.unpack_from('<H',data,at)[0];at+=2;walk(child,path+'/')
            else:
                if fileid>=an//8:raise ValueError('FAT index')
                begin,end=struct.unpack_from('<II',data,ao+fileid*8);fileid+=1
                if begin>end or end>len(data) or path in result:raise ValueError('File bounds/duplicate')
                result[path]=data[begin:end]
    walk(0xf000,'')
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--rom',type=Path,default=ROOT/'zato_ep2d3_scene_nitrofs.nds')
    p.add_argument('--approved-rom',type=Path,default=ROOT.parent/'ZATO_Asset_Audio_Stereo_64MB.nds')
    args=p.parse_args();current=files(args.rom);approved=files(args.approved_rom)
    audio=[name for name in approved if name.startswith('audio/')]
    assert audio,'Approved ROM has no audio'
    assert all(current.get(name)==approved[name] for name in audio),'Approved audio changed'
    assert current['assets.zat']==approved['assets.zat'],'Existing asset pack changed'
    for kind in ('gfx','text','script'):
        name=f'scene/{kind}.bin'
        assert current[name]==(ROOT/'nitrofs'/name).read_bytes(),f'Embedded bytes differ: {name}'
    assert 0<args.rom.stat().st_size<64_000_000
    report=dict(result='PASS',rom=str(args.rom.resolve()),rom_bytes=args.rom.stat().st_size,
        sha256=hashlib.sha256(args.rom.read_bytes()).hexdigest(),files=len(current),
        required_scene_packs={name:len(current[name]) for name in current if name.startswith('scene/')},
        approved_audio_files_unchanged=len(audio),existing_assets_unchanged=True,
        approved_rom_sha256=hashlib.sha256(args.approved_rom.read_bytes()).hexdigest())
    (ROOT/'artifacts/scene_package_verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
