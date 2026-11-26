"""Verify all actual NitroFS payloads against frozen approvals and compiled packs."""
from pathlib import Path
import argparse,hashlib,json
from verify_scene_package import files
from story_build_report import report_package

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--rom',type=Path,default=ROOT/'zato_story_chain_nitrofs.nds')
    p.add_argument('--video-manifest',type=Path)
    p.add_argument('--strict-budget',action='store_true');args=p.parse_args()
    baseline=ROOT.parent/'ZATO_EP2D3_Scene_Test.nds'
    frozen=files(baseline);current=files(args.rom)
    expected={name:value for name,value in frozen.items() if name.startswith('audio/') or name=='scene/gfx.bin'}
    asset_manifest=json.loads((ROOT/'artifacts/story_assets_manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha256(frozen['assets.zat']).hexdigest()!=asset_manifest['source_package_sha256']:
        raise ValueError('Shared-palette assets do not derive from the frozen source package')
    expected['assets.zat']=(ROOT/'nitrofs/story/assets.zat').read_bytes()
    if hashlib.sha256(expected['assets.zat']).hexdigest()!=asset_manifest['package_sha256']:
        raise ValueError('Shared-palette asset manifest mismatch')
    family_ids=[172,173,174,175,176,246,247]
    soft=asset_manifest.get('soft_alpha')
    soft_ids=[150,330,375,407,451,452,453,454,455,456,459] if soft else []
    if soft and (soft.get('version')!=1 or soft.get('frame_ids')!=soft_ids or soft.get('format')!=2 or soft.get('tile_bytes')!=192):
        raise ValueError('Unexpected soft-alpha conversion contract')
    changed=sorted(family_ids+soft_ids);unchanged=505-len(changed)
    if asset_manifest['changed_frame_ids']!=changed or asset_manifest['unchanged_frame_count']!=unchanged:
        raise ValueError('Unexpected raster conversion scope')
    for kind in ('gfx','text','script'):
        name=f'story/{kind}.bin';expected[name]=(ROOT/'nitrofs'/name).read_bytes()
    if 'story/menu.bin' in current:
        expected['story/menu.bin']=(ROOT/'nitrofs/story/menu.bin').read_bytes()
    if args.video_manifest:
        video=json.loads(args.video_manifest.read_text(encoding='utf-8'))
        for name,path in video['embedded_files'].items():expected[name]=Path(path).read_bytes()
    if set(current)!=set(expected):raise ValueError(dict(missing=sorted(set(expected)-set(current)),unexpected=sorted(set(current)-set(expected))))
    for name,data in expected.items():
        if current[name]!=data:raise ValueError(f'Payload mismatch: {name}')
    budget=report_package(args.rom,strict_budget=args.strict_budget)
    size=budget['rom_bytes']
    report=dict(result='PASS',rom=str(args.rom.resolve()),bytes=size,sha256=hashlib.sha256(args.rom.read_bytes()).hexdigest(),
        files=len(current),budget=budget,audio_files_unchanged=sum(name.startswith('audio/') for name in expected),
        frozen_scene_rom_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
        asset_policy=dict(changed_frames=changed,unchanged_frames=unchanged,soft_alpha_frames=soft_ids,
                          shared_index_families=['library','vents'],proof='story_assets_verification.json',
                          soft_alpha_proof='story_soft_alpha_pixel_verification.json' if soft else None),
        payloads={name:dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest()) for name,data in current.items() if not name.startswith('audio/')})
    (ROOT/'artifacts/story_package_verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='payloads'},indent=2))

if __name__=='__main__':main()
