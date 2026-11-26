"""Shrink uncompressed ZATPAL01 packets with exact, bounded raw deltas."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from experiments.video_lab import convert_palette as pal
from nds.tools.verify_story_dual_video_rom import lz10


def decoded_tiles(data, info=None):
    """Sequentially expand packed tile indices; no palette remapping or RGB conversion."""
    info=pal.inspect_pack(data) if info is None else info
    previous=bytes(info['raw_bytes'])
    for row in info['index']:
        offset=row['offset']
        packet=data[offset:offset+row['length']]
        if not row['flags']&pal.RAW:
            packet=lz10(packet,row['decoded'])
        if len(packet)!=row['decoded']:
            raise ValueError('Incorrect decoded length')
        mode=row['flags']&~pal.RAW
        if mode==pal.KEY:
            current=packet
        elif mode==pal.XOR:
            current=np.bitwise_xor(np.frombuffer(previous,np.uint8),np.frombuffer(packet,np.uint8)).tobytes()
        elif mode==pal.TILE:
            present=np.unpackbits(np.frombuffer(packet[:72],np.uint8),bitorder='little').astype(bool)
            tiles=np.frombuffer(previous,np.uint8).reshape(576,-1).copy()
            tiles[present]=np.frombuffer(packet[72:],np.uint8).reshape(int(present.sum()),tiles.shape[1])
            current=tiles.tobytes()
        else:
            current=pal.apply_skip(previous,packet)
        if len(current)!=info['raw_bytes']:
            raise ValueError('Incorrect frame size')
        yield current
        previous=current


def optimize_packets(data, *, max_delta_bytes=1024, watched_frames=()):
    """Use existing RAW SKIP/TILE encodings only when smaller than an existing RAW packet."""
    if not 4<=max_delta_bytes<=pal.MAX_PACKET:
        raise ValueError('Maximum raw delta must be between 4 and 65536 bytes')
    info=pal.inspect_pack(data)
    previous=bytes(info['raw_bytes'])
    key=0
    payload=bytearray()
    records=bytearray()
    max_packed=0
    changes=[]
    statistics=[]
    for n,(row,current) in enumerate(zip(info['index'],decoded_tiles(data,info),strict=True)):
        offset=row['offset']
        flags=row['flags']
        packet=data[offset:offset+row['length']]
        decoded=row['decoded']
        # The first key of each two-second palette family is mandatory for seeking.
        optional_raw=flags&pal.RAW and n%(info['fps']*2)!=0
        if (optional_raw and len(packet)>4) or n in watched_frames:
            now=np.frombuffer(current,np.uint8)
            delta=np.bitwise_xor(now,np.frombuffer(previous,np.uint8))
            changed_bytes=int(np.count_nonzero(delta))
            changed_tiles=np.any(delta.reshape(576,-1),axis=1)
            count=int(changed_tiles.sum())
            stats=dict(frame=n,changed_bytes=changed_bytes,changed_tiles=count,original_bytes=len(packet))
            if n in watched_frames:
                statistics.append(stats)
            if optional_raw and changed_bytes<=max_delta_bytes:
                skip=pal.skip_runs(delta.tobytes())
                tile=np.packbits(changed_tiles,bitorder='little').tobytes()+now.reshape(576,-1)[changed_tiles].tobytes()
                new_mode,new_packet=min(((pal.SKIP,skip),(pal.TILE,tile)),key=lambda item:len(item[1]))
                if len(new_packet)<len(packet) and len(new_packet)<=max_delta_bytes:
                    changes.append(dict(**stats,mode=pal.MODE_NAMES[new_mode],new_bytes=len(new_packet)))
                    packet=new_packet
                    flags=new_mode|pal.RAW
                    decoded=len(packet)
        if flags&pal.KEY:
            key=n
        records.extend(pal.ENTRY.pack(info['payload_offset']+len(payload),len(packet),decoded,flags,
                                      key,row['palette'],row['audio_sample'],row['hash'],0))
        payload.extend(packet)
        max_packed=max(max_packed,len(packet))
        previous=current
    info.update(file_bytes=info['payload_offset']+len(payload),max_packed=max_packed)
    header=pal.HEADER.pack(b'ZATPAL01',*(info[k] for k in pal.FIELDS))
    palette=data[info['palette_offset']:info['payload_offset']]
    result=header+records+palette+payload
    pal.inspect_pack(result)
    return result,dict(original_bytes=len(data),optimized_bytes=len(result),saved_bytes=len(data)-len(result),
                       max_delta_bytes=max_delta_bytes,replacements=changes,watched_frames=statistics)


def verify_equivalence(before,after):
    old=pal.inspect_pack(before)
    new=pal.inspect_pack(after)
    if before[old['palette_offset']:old['payload_offset']]!=after[new['palette_offset']:new['payload_offset']]:
        raise ValueError('Palette bytes changed')
    stream_hash=hashlib.sha256()
    for n,(a,b) in enumerate(zip(decoded_tiles(before,old),decoded_tiles(after,new),strict=True)):
        if a!=b or any(old['index'][n][k]!=new['index'][n][k] for k in ('hash','palette','audio_sample')):
            raise ValueError(f'Picture, palette or audio timestamp changed at frame {n}')
        stream_hash.update(a)
    return dict(frames=old['frame_count'],tile_stream_sha256=stream_hash.hexdigest())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--decoder',type=Path,required=True)
    parser.add_argument('--max-delta-bytes',type=int,default=1024)
    args=parser.parse_args()
    if args.source.resolve()==args.output.resolve():
        parser.error('Choose a separate output for the optimized pack')
    original=args.source.read_bytes()
    result,report=optimize_packets(original,max_delta_bytes=args.max_delta_bytes,
                                   watched_frames={1261,*range(1320,1365),*range(1413,1443)})
    report['exact_comparison']=verify_equivalence(original,result)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(result)
    report['native']=pal.verify_native(args.decoder,args.output,args.output.with_suffix('.samples'))
    report['source_sha256']=hashlib.sha256(original).hexdigest()
    report['optimized_sha256']=hashlib.sha256(result).hexdigest()
    args.output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(f"{len(report['replacements'])} packet replacements, {report['saved_bytes']:,} bytes saved; "
          f"{report['native']['frames']} frames and {report['native']['seek_checks']} seeks verified.")


if __name__=='__main__':
    main()
