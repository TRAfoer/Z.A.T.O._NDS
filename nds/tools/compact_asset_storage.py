"""Lossless storage-only compaction of existing ZATONDP1 bytes.

No image conversion or cross-bank merging occurs. Frame IDs and all fields
after the four storage words, including palettes/hashes/names, stay exact.
"""
import hashlib
import struct
from pack_assets import HEADER,BANK,FRAME,MAX_BANK_RAW,lz10,unlz10


def _check(condition,message):
    if not condition:raise ValueError(message)


def _decode(data,at,packed,raw,first,label):
    _check(at>=first and packed>0 and at<=len(data) and packed<=len(data)-at,f'{label}: compressed range')
    blob=data[at:at+packed]
    _check(len(blob)>=4 and blob[0]==16 and int.from_bytes(blob[1:4],'little')==raw,f'{label}: LZ10 header')
    try:result=unlz10(blob)
    except (AssertionError,IndexError,ValueError,OverflowError) as error:raise ValueError(f'{label}: invalid LZ10') from error
    _check(len(result)==raw,f'{label}: raw length')
    return result,blob


def compact_storage(data):
    """Return ``(bytes, report)`` without modifying input or any files.

    Only storage references/offsets and bank count may change. Unchanged bank
    compressed streams are copied verbatim. Changed dictionaries retain old
    used-tile order, then maps are remapped and exactly equal maps share storage.
    All input dictionaries/maps are validated before output is assembled.
    """
    _check(isinstance(data,(bytes,bytearray,memoryview)),'Expected asset bytes')
    data=bytes(data);_check(len(data)>=HEADER.size,'Truncated asset header')
    h=list(HEADER.unpack_from(data));nf,nb=h[2],h[3]
    _check(h[0]==b'ZATONDP1' and h[1]==1 and h[8]==FRAME.size and nf>0 and nb>0,'Asset header/version')
    _check(h[4]<=nf and h[5]<=nf,'Stage/actor count')
    frame_at=HEADER.size+nb*BANK.size
    list_at=frame_at+nf*FRAME.size;first=list_at+(h[4]+h[5])*2
    _check(first<=len(data),'Truncated asset tables')
    _check((not h[4] and not h[6]) or h[6]<h[4],'Default stage index')
    _check((not h[5] and not h[7]) or h[7]<h[5],'Default actor index')
    list_bytes=data[list_at:first]
    for value in struct.iter_unpack('<H',list_bytes):_check(value[0]<nf,'Stage/actor frame ID')
    banks=[BANK.unpack_from(data,HEADER.size+i*BANK.size) for i in range(nb)]
    frames=[FRAME.unpack_from(data,frame_at+i*FRAME.size) for i in range(nf)]
    raw_banks=[];bank_blobs=[]
    for bid,(at,packed,raw,stride) in enumerate(banks):
        _check(stride in (40,128,192) and 0<raw<=MAX_BANK_RAW and raw%stride==0 and packed<=300000,f'Bank{bid}: shape')
        decoded,blob=_decode(data,at,packed,raw,first,f'Bank{bid}')
        raw_banks.append(decoded);bank_blobs.append(blob)
    maps=[];map_blobs=[];used=[set() for _ in banks];members=[[] for _ in banks]
    for fid,f in enumerate(frames):
        bid,at,packed,raw,width,height,cols,rows=f[:8]
        _check(bid<nb and 0<width<=1024 and 0<height<=2048 and cols==(width+7)//8 and rows==(height+7)//8 and raw==cols*rows*2 and raw<=32768 and packed<=300000,f'Frame{fid}: shape/range')
        _check(f[11] in (0,1,2) and banks[bid][3]==(128,40,192)[f[11]],f'Frame{fid}: tile format')
        decoded,blob=_decode(data,at,packed,raw,first,f'Frame{fid} map')
        ids=tuple(value[0] for value in struct.iter_unpack('<H',decoded))
        _check(all(value<len(raw_banks[bid])//banks[bid][3] for value in ids),f'Frame{fid}: tile ID')
        used[bid].update(ids);members[bid].append(fid);maps.append(ids);map_blobs.append(blob)
    bank_remap={};rows=[];new_banks=[];new_map_raw=[None]*nf;new_map_blobs=[None]*nf
    removed=[]
    for bid,b in enumerate(banks):
        stride=b[3];count=b[2]//stride
        if not members[bid]:
            bank_remap[bid]=None;removed.append(bid)
            rows.append(dict(old_id=bid,new_id=None,raw_bytes=0,lz10_bytes=0,tile_bytes=stride,
                old_raw_bytes=b[2],old_lz10_bytes=b[1],removed_tile_bytes=b[2],unused_tiles=count,duplicate_used_tiles=0,compressed_copied=False,frame_ids=[]))
            continue
        new_id=len(new_banks);bank_remap[bid]=new_id;lookup={};remap={};tiles=[];duplicates=0
        for index in range(count):
            if index not in used[bid]:continue
            tile=raw_banks[bid][index*stride:(index+1)*stride]
            if tile in lookup:duplicates+=1
            else:lookup[tile]=len(tiles);tiles.append(tile)
            remap[index]=lookup[tile]
        raw=b''.join(tiles);changed=len(raw)!=b[2]
        blob=lz10(raw) if changed else bank_blobs[bid]
        _check(unlz10(blob)==raw,f'Bank{bid}: output roundtrip')
        new_banks.append((blob,len(raw),stride))
        for fid in members[bid]:
            mapped=struct.pack('<'+'H'*len(maps[fid]),*(remap[index] for index in maps[fid]))
            old_raw=struct.pack('<'+'H'*len(maps[fid]),*maps[fid])
            encoded=lz10(mapped) if mapped!=old_raw else map_blobs[fid]
            _check(unlz10(encoded)==mapped,f'Frame{fid}: output map roundtrip')
            new_map_raw[fid]=mapped;new_map_blobs[fid]=encoded
        rows.append(dict(old_id=bid,new_id=new_id,raw_bytes=len(raw),lz10_bytes=len(blob),tile_bytes=stride,
            old_raw_bytes=b[2],old_lz10_bytes=b[1],removed_tile_bytes=b[2]-len(raw),unused_tiles=count-len(used[bid]),duplicate_used_tiles=duplicates,compressed_copied=not changed,frame_ids=members[bid]))
    # Raw map equality is independent of the frame's bank. Keep the shortest
    # existing encoding, with a deterministic byte tie break, for each map.
    best_maps={}
    for raw,blob in zip(new_map_raw,new_map_blobs):
        if raw not in best_maps or (len(blob),blob)<(len(best_maps[raw]),best_maps[raw]):best_maps[raw]=blob
    new_first=HEADER.size+len(new_banks)*BANK.size+nf*FRAME.size+len(list_bytes);payload=bytearray()
    def append(blob):
        while (new_first+len(payload))%4:payload.append(0)
        at=new_first+len(payload);payload.extend(blob);return at
    new_bank_records=[BANK.pack(append(blob),len(blob),raw,stride) for blob,raw,stride in new_banks]
    map_offsets={};new_frames=[]
    for fid,f in enumerate(frames):
        raw=new_map_raw[fid];blob=best_maps[raw]
        if raw not in map_offsets:map_offsets[raw]=append(blob)
        # Copy the complete non-storage tail as raw bytes, including any
        # authored padding in names, instead of normalizing metadata strings.
        tail=data[frame_at+fid*FRAME.size+16:frame_at+(fid+1)*FRAME.size]
        new_frames.append(struct.pack('<4I',bank_remap[f[0]],map_offsets[raw],len(blob),f[3])+tail)
    h[3]=len(new_banks)
    result=HEADER.pack(*h)+b''.join(new_bank_records)+b''.join(new_frames)+list_bytes+payload
    report=dict(version=1,lossless=True,old_bytes=len(data),new_bytes=len(result),bytes_saved=len(data)-len(result),
        old_sha256=hashlib.sha256(data).hexdigest(),new_sha256=hashlib.sha256(result).hexdigest(),
        frame_count=nf,old_bank_count=nb,new_bank_count=len(new_banks),bank_remap=bank_remap,bank_rows=rows,
        removed_banks=removed,removed_bank_rows=[rows[i] for i in removed],
        removed_tile_bytes=sum(row['removed_tile_bytes'] for row in rows),
        maps_unique_before=len({(f[1],f[2]) for f in frames}),maps_unique_after=len(map_offsets),
        copied_bank_count=sum(row['compressed_copied'] for row in rows),trimmed_bank_count=sum(row['new_id'] is not None and not row['compressed_copied'] for row in rows),
        frame_metadata_and_lists_preserved=True,cross_bank_merge=False)
    return result,report
