"""Rebuild an independent ZAT with the approved library/vents palette families.

Frame IDs, geometry, names, categories and stage/actor lists are preserved.
The seven selected frames consume explicit common
idx4/mask1/pal streams, never independent RGB quantization or encode().
The production CLI also removes unreachable storage without changing pixels.
"""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
sys.path.insert(0,str(ROOT/'experiments/asset_lab'))
from pack_assets import HEADER,BANK,FRAME,MAX_BANK_RAW,lz10,unlz10,fnv
from asset_model import untile,unmask

SOURCE=ROOT/'nds/nitrofs/assets.zat'
FAMILIES=ROOT/'experiments/story_lab/out/palette_families'
OUTPUT=ROOT/'nds/nitrofs/story/assets.zat'
MANIFEST=ROOT/'nds/artifacts/story_assets_manifest.json'
SELECTED={'library':[172,173,174,175,176],'vents':[246,247]}


def sha(data):return hashlib.sha256(data).hexdigest()


def checked(condition,message):
    if not condition:raise ValueError(message)


def decompress(data,offset,packed,raw):
    checked(offset>=0 and packed>0 and offset+packed<=len(data),'Compressed block outside source package')
    try:result=unlz10(data[offset:offset+packed])
    except (AssertionError,IndexError,ValueError) as error:raise ValueError('Invalid source LZ10 block') from error
    checked(len(result)==raw,'Source LZ10 raw length mismatch')
    return result


class SourcePack:
    def __init__(self,path):
        self.path=Path(path).resolve();self.data=self.path.read_bytes()
        checked(len(self.data)>=HEADER.size,'Truncated source header')
        self.header=HEADER.unpack_from(self.data);h=self.header
        checked(h[0]==b'ZATONDP1' and h[1]==1 and h[2]==505 and h[8]==FRAME.size,'Unexpected source asset header')
        at=HEADER.size+h[3]*BANK.size
        end=at+h[2]*FRAME.size+(h[4]+h[5])*2
        checked(end<=len(self.data),'Truncated source tables')
        self.banks=[BANK.unpack_from(self.data,HEADER.size+i*BANK.size) for i in range(h[3])]
        self.frames=[list(FRAME.unpack_from(self.data,at+i*FRAME.size)) for i in range(h[2])]
        self.list_bytes=self.data[at+h[2]*FRAME.size:end]
        self.raw_banks={};self.maps={}
        for f in self.frames:
            checked(f[0]<h[3] and f[4]>0 and f[5]>0 and f[6]==(f[4]+7)//8 and f[7]==(f[5]+7)//8 and f[3]==f[6]*f[7]*2,'Invalid source frame shape')
            checked(f[11] in (0,1,2),'Invalid source frame format')
        for offset,packed,raw,stride in self.banks:
            checked(0<raw<=MAX_BANK_RAW and stride in (40,128,192) and raw%stride==0 and 0<packed<=300000 and offset+packed<=len(self.data),'Invalid source dictionary')

    def tiles(self,id):
        f=self.frames[id];bid=f[0];b=self.banks[bid]
        if bid not in self.raw_banks:self.raw_banks[bid]=decompress(self.data,*b[:3])
        raw=self.raw_banks[bid];mapping=decompress(self.data,f[1],f[2],f[3]);self.maps[id]=mapping
        ids=np.frombuffer(mapping,dtype='<u2');stride=b[3]
        checked(stride=={0:128,1:40,2:192}[f[11]] and np.all(ids<len(raw)//stride),'Source tile ID/format invalid')
        return [raw[int(i)*stride:(int(i)+1)*stride] for i in ids]


def replacements(source,folder):
    folder=Path(folder).resolve();report_path=folder/'report.json'
    report=json.loads(report_path.read_text(encoding='utf8'))
    checked(report['current_pack']['sha256']==sha(source.data),'Family masters belong to a different source package')
    entries={f['key']:f for f in report['new_families']};result={};families=[]
    for key,ids in SELECTED.items():
        family=entries[key];checked(family['frame_ids']==ids,'Family frame ID list changed')
        path=folder/key;index=(path/'common.idx4').read_bytes();mask=(path/'common.mask1').read_bytes()
        checked(sha(index)==family['shared_index_sha256'] and sha(mask)==family['shared_mask_sha256'],'Shared index/mask stream hash mismatch')
        first=source.frames[ids[0]];count=first[6]*first[7]
        checked(len(index)==count*32 and len(mask)==count*8,'Shared tiled stream length mismatch')
        tiles=[index[i*32:(i+1)*32]+mask[i*8:(i+1)*8] for i in range(count)]
        shape=(first[5],first[4]);plane=untile([t[:32] for t in tiles],shape);visible=unmask([t[32:] for t in tiles],shape)
        with np.load(path/'family_arrays.npz',allow_pickle=False) as arrays:
            checked(np.array_equal(plane,arrays['indices']) and np.array_equal(visible,arrays['mask']),'Explicit streams differ from family array master')
            edge=arrays['edge'];palettes=arrays['palettes'].copy()
            checked(edge.shape==shape and np.all(visible[edge]) and np.all(plane[edge]==15),'Fixed gray outline index invalid')
            checked(palettes.shape==(len(ids),16) and np.all(palettes[:,15]==0x2108),'Fixed gray slot must be RGB15(8,8,8)')
        changed=[]
        for i,id in enumerate(ids):
            f=source.frames[id];variant=family['variants'][i]
            checked(variant['frame_id']==id and f[4:12]==[245,102,31,13,5,45,0,1],'Selected family geometry/category changed')
            checked(f[-1].split(b'\0')[0].decode()=='images/bgs/'+variant['name']+'.png','Selected family asset name changed')
            palette=(path/(variant['name']+'.pal')).read_bytes()
            checked(palette==palettes[i].astype('<u2').tobytes(),'Family palette differs from array master')
            words=np.where(visible,palettes[i][plane]|0x8000,0).astype('<u2')
            checked(sha(words.tobytes())==variant['native_word_sha256'],'Family native master pixel hash mismatch')
            old_tiles=source.tiles(id);old_alpha=unmask([t[32:] for t in old_tiles],shape)
            checked(np.array_equal(visible,old_alpha),'Replacement must preserve original alpha exactly')
            result[id]=dict(tiles=tiles,palette=palette,hash=fnv(words.tobytes()),master=str(path/(variant['name']+'.png')),native_sha256=sha(words.tobytes()))
            changed.append(dict(variant,old_hash=f[12],new_hash=result[id]['hash'],master=result[id]['master'],palette=str(path/(variant['name']+'.pal'))))
        families.append(dict(key=key,frame_ids=ids,gray_slot=15,gray_rgb15=0x2108,gray_outline_pixels=int(edge.sum()),
            shared_index_sha256=sha(index),shared_mask_sha256=sha(mask),variants=changed,
            lossy=True,note='Joint source BOX quantization to fifteen content slots plus fixed gray. All variants use the same index and alpha planes. RGB15 changes and source errors are listed per frame.'))
    return result,families,report_path


def build(source=SOURCE,families=FAMILIES,output=OUTPUT,manifest=MANIFEST,soft_alpha=False,soft_masters=None,compact=False):
    source=Path(source).resolve();output=Path(output).resolve();manifest=Path(manifest).resolve()
    protected={source,SOURCE.resolve(),(ROOT/'nds/storyfs/assets.zat').resolve(),(ROOT/'nds/nitrofs/story/text.bin').resolve()}
    for path in (output,manifest):
        checked(path not in protected and path.suffix.lower()!='.nds' and not path.is_relative_to((ROOT/'nds/nitrofs/scene').resolve()),'Refusing to overwrite a protected source/scene/text/ROM')
    checked(output!=manifest,'Package and manifest must have separate paths')
    original=SourcePack(source);replacement,family_rows,family_report=replacements(original,families)
    frames=[f.copy() for f in original.frames];affected=sorted({frames[i][0] for i in replacement})
    bank_blobs=[];bank_stats=[];maps={}
    for bid,b in enumerate(original.banks):
        members=[i for i,f in enumerate(frames) if f[0]==bid]
        if bid not in affected:
            bank_blobs.append((original.data[b[0]:b[0]+b[1]],b[2],b[3]))
            bank_stats.append(dict(id=bid,rebuilt=False,raw_bytes=b[2],lz10_bytes=b[1],old_raw_bytes=b[2],old_lz10_bytes=b[1]))
            for id in members:
                f=frames[id];maps[id]=(original.data[f[1]:f[1]+f[2]],None)
            continue
        dictionary=[];lookup={}
        for id in members:
            tiles=replacement[id]['tiles'] if id in replacement else original.tiles(id)
            checked(all(len(t)==b[3] for t in tiles),'Rebuilt dictionary has mixed tile strides')
            ids=[]
            for tile in tiles:
                if tile not in lookup:lookup[tile]=len(dictionary);dictionary.append(tile)
                ids.append(lookup[tile])
            raw_map=np.asarray(ids,dtype='<u2').tobytes();blob=lz10(raw_map)
            checked(unlz10(blob)==raw_map,'Rebuilt map LZ10 roundtrip failed')
            maps[id]=(blob,raw_map)
        raw=b''.join(dictionary);checked(0<len(raw)<=MAX_BANK_RAW,'Rebuilt dictionary exceeds 256KiB')
        blob=lz10(raw);checked(unlz10(blob)==raw,'Rebuilt dictionary LZ10 roundtrip failed')
        bank_blobs.append((blob,len(raw),b[3]));bank_stats.append(dict(id=bid,rebuilt=True,raw_bytes=len(raw),lz10_bytes=len(blob),old_raw_bytes=b[2],old_lz10_bytes=b[1]))
    for id,item in replacement.items():frames[id][12]=item['hash'];frames[id][13]=item['palette']
    soft_report=None
    if soft_alpha:
        from convert_story_soft_alpha import convert,MASTERS
        soft,soft_report=convert(original.frames,soft_masters or MASTERS,source_pack=original)
        # Keep every old compressed dictionary verbatim. New soft dictionaries
        # are shared within source folders and split only at whole-frame edges.
        for folder in sorted({str(Path(original.frames[id][-1].split(b'\0')[0].decode()).parent) for id in soft}):
            dictionary=[];lookup={};members=[]
            def finish():
                if not dictionary:return
                raw=b''.join(dictionary);blob=lz10(raw)
                checked(0<len(raw)<=MAX_BANK_RAW and unlz10(blob)==raw,'Soft dictionary LZ10/size')
                bid=len(bank_blobs);bank_blobs.append((blob,len(raw),192))
                bank_stats.append(dict(id=bid,rebuilt=True,soft_alpha=True,members=members.copy(),raw_bytes=len(raw),lz10_bytes=len(blob),old_raw_bytes=0,old_lz10_bytes=0))
            for id,item in soft.items():
                if str(Path(original.frames[id][-1].split(b'\0')[0].decode()).parent)!=folder:continue
                missing=set(item['tiles'])-set(lookup)
                if (len(lookup)+len(missing))*192>MAX_BANK_RAW:
                    finish();dictionary=[];lookup={};members=[]
                checked(len(set(item['tiles']))*192<=MAX_BANK_RAW,'Single soft frame exceeds dictionary limit')
                for tile in item['tiles']:
                    if tile not in lookup:lookup[tile]=len(dictionary);dictionary.append(tile)
                raw_map=np.asarray([lookup[t] for t in item['tiles']],dtype='<u2').tobytes();blob=lz10(raw_map)
                checked(unlz10(blob)==raw_map,'Soft map LZ10 roundtrip')
                maps[id]=(blob,raw_map);members.append(id)
                f=frames[id];f[0]=len(bank_blobs);f[11]=2;f[12]=item['hash'];f[13]=bytes(32)
            finish()
        replacement.update(soft)
    data_offset=HEADER.size+len(bank_blobs)*BANK.size+len(frames)*FRAME.size+len(original.list_bytes)
    payload=bytearray()
    def append(blob):
        while (data_offset+len(payload))%4:payload.append(0)
        offset=data_offset+len(payload);payload.extend(blob);return offset
    bank_records=[BANK.pack(append(blob),len(blob),raw,stride) for blob,raw,stride in bank_blobs]
    shared_maps={};rows=[];old_meta=json.loads((ROOT/'nds/artifacts/assets_manifest.json').read_text(encoding='utf8'))
    for id,f in enumerate(frames):
        blob,raw_map=maps[id];key=(f[0],blob)
        if key not in shared_maps:shared_maps[key]=append(blob)
        f[1]=shared_maps[key];f[2]=len(blob)
        rows.append(dict(old_meta['frames'][id],bank=f[0],format=f[11],hash=f[12],map_offset=f[1],map_bytes=f[2],map_raw=f[3],
            changed=id in replacement,master=replacement[id]['master'] if id in replacement else old_meta['frames'][id]['master']))
    header=list(original.header);header[3]=len(bank_blobs)
    data=HEADER.pack(*header)+b''.join(bank_records)+b''.join(FRAME.pack(*f) for f in frames)+original.list_bytes+payload
    for family in family_rows:
        ids=family['frame_ids'];shared={tuple(frames[id][:4]) for id in ids}
        checked(len(shared)==1,'Selected family must directly share one binary tile map')
        family['binary_shared_map']=dict(bank=frames[ids[0]][0],offset=frames[ids[0]][1],packed_bytes=frames[ids[0]][2],raw_bytes=frames[ids[0]][3],all_frames_identical=True)
    checked(source.read_bytes()==original.data,'Read-only source package changed during rebuild')
    h=original.header;stages=list(struct.unpack_from('<'+'H'*h[4],original.list_bytes));actors=list(struct.unpack_from('<'+'H'*h[5],original.list_bytes,h[4]*2))
    report=dict(version=1,compression='LZ10',package_path=str(output),package_bytes=len(data),package_sha256=sha(data),
        source_package_path=str(source),source_package_bytes=len(original.data),source_package_sha256=sha(original.data),bytes_saved=len(original.data)-len(data),
        family_report_path=str(family_report),family_report_sha256=sha(family_report.read_bytes()),frame_count=len(frames),frame_record_bytes=FRAME.size,
        changed_frame_ids=sorted(replacement),unchanged_frame_count=len(frames)-len(replacement),
        changes='Only library/library2..5 and vents/vents_red RGB15 quantization changes. All other frame pixels, alpha, palette, geometry, names, categories and ID ordering are retained. The two selected families consume explicit shared index/mask streams.',
        lossy=True,lossy_note='The shared-index constraint is a lossy redesign of seven previously independently quantized backgrounds; no content is removed. Per-frame source BOX and previous-master error metrics are recorded below.',
        bank_count=len(bank_blobs),rebuilt_bank_ids=affected,max_bank_raw_bytes=max(b[1] for b in bank_blobs),banks=bank_stats,
        stages=stages,actors=actors,default_stage=h[6],default_actor=h[7],stage_actor_lists_unchanged=True,
        background_outline=dict(width=1,placement='inward exterior boundary',rgb=[66,66,66],palette_slots=1,colors_total=16,family_gray_slot=15),
        families=family_rows,frames=rows)
    if soft_report:
        report['soft_alpha']=soft_report
        report['changes']+=' Ten explicitly selected soft actors and cinemablur150 use straight RGB15+alpha8 tiles; cinemablur preserves approved RGB/grayborder/visibility. All other frame streams remain unchanged.'
    if compact:
        from compact_asset_storage import compact_storage
        data,storage=compact_storage(data)
        packed_header=HEADER.unpack_from(data)
        packed_banks=[BANK.unpack_from(data,HEADER.size+i*BANK.size) for i in range(packed_header[3])]
        frame_at=HEADER.size+packed_header[3]*BANK.size
        packed_frames=[FRAME.unpack_from(data,frame_at+i*FRAME.size) for i in range(packed_header[2])]
        old_stats={row['id']:row for row in report['banks']};bank_rows=[]
        for row in storage['bank_rows']:
            if row['new_id'] is None:continue
            stat=dict(old_stats[row['old_id']],id=row['new_id'],storage_old_id=row['old_id'],
                raw_bytes=row['raw_bytes'],lz10_bytes=row['lz10_bytes'],removed_tile_bytes=row['removed_tile_bytes'])
            bank_rows.append(stat)
        report.update(package_bytes=len(data),package_sha256=sha(data),bytes_saved=len(original.data)-len(data),
            bank_count=len(packed_banks),banks=bank_rows,max_bank_raw_bytes=max(b[2] for b in packed_banks),
            rebuilt_bank_ids=[storage['bank_remap'][i] for i in affected if storage['bank_remap'][i] is not None],
            storage_compaction=storage)
        for row,frame in zip(report['frames'],packed_frames):
            row.update(bank=frame[0],map_offset=frame[1],map_bytes=frame[2],map_raw=frame[3])
        for family in report['families']:
            frame=packed_frames[family['frame_ids'][0]]
            family['binary_shared_map']=dict(bank=frame[0],offset=frame[1],packed_bytes=frame[2],raw_bytes=frame[3],all_frames_identical=True)
        report['changes']+=' Unreferenced banks/tiles are removed and identical tile maps shared, with all frame tile bytes and visible metadata preserved exactly.'
    output.parent.mkdir(parents=True,exist_ok=True);manifest.parent.mkdir(parents=True,exist_ok=True)
    output.write_bytes(data);manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=SOURCE);parser.add_argument('--families',type=Path,default=FAMILIES)
    parser.add_argument('--output',type=Path,default=OUTPUT);parser.add_argument('--manifest',type=Path,default=MANIFEST)
    parser.add_argument('--legacy-binary-alpha',action='store_false',dest='soft_alpha',help='Build only the previous seven palette-family replacements')
    parser.add_argument('--soft-masters',type=Path)
    parser.add_argument('--no-compact',action='store_false',dest='compact',help='Keep legacy storage for conversion comparisons')
    args=parser.parse_args();report=build(**vars(args))
    print(json.dumps({k:report[k] for k in ('package_path','package_bytes','package_sha256','source_package_bytes','bytes_saved','changed_frame_ids','rebuilt_bank_ids','max_bank_raw_bytes')},indent=2))
