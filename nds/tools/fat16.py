"""Small independent FAT16 image maker/reader for the emulator-only spike."""
from pathlib import Path
import struct


def create(path):
    path=Path(path)
    if path.exists():raise FileExistsError(path)
    data=bytearray(32*1024*1024)
    data[:11]=b'\xeb\x3c\x90ZATOFAT '
    struct.pack_into('<HBHBHHBHHHII',data,11,512,4,1,2,512,0,0xf8,64,32,64,0,65536)
    data[36:39]=b'\x80\0\x29';struct.pack_into('<I',data,39,0x5a41544f)
    data[43:54]=b'ZATO_TEST  ';data[54:62]=b'FAT16   ';data[510:512]=b'\x55\xaa'
    for sector in (1,65):struct.pack_into('<HH',data,sector*512,0xfff8,0xffff)
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)


def files(path):
    data=Path(path).read_bytes();bps,spc,reserved,nfats,root_entries=struct.unpack_from('<HBHBH',data,11)
    fatsz=struct.unpack_from('<H',data,22)[0]
    root_at=(reserved+nfats*fatsz)*bps;root_bytes=((root_entries*32+bps-1)//bps)*bps
    fat_at=reserved*bps;data_at=root_at+root_bytes;result={};long={}
    assert bps==512 and data[510:512]==b'\x55\xaa'
    for at in range(root_at,root_at+root_bytes,32):
        entry=data[at:at+32]
        if not entry[0]:break
        if entry[0]==0xe5:long={};continue
        if entry[11]==0x0f:
            text=entry[1:11]+entry[14:26]+entry[28:32]
            long[entry[0]&31]=''.join(chr(x[0]) for x in struct.iter_unpack('<H',text) if x[0] not in (0,0xffff))
            continue
        if entry[11]&0x18:long={};continue
        name=''.join(long[i] for i in sorted(long)) if long else entry[:8].decode('ascii').rstrip()+'.'+entry[8:11].decode('ascii').rstrip()
        long={};cluster=struct.unpack_from('<H',entry,26)[0];size=struct.unpack_from('<I',entry,28)[0]
        payload=bytearray();visited=set()
        while len(payload)<size:
            assert 2<=cluster<0xfff8 and cluster not in visited,(name,cluster)
            visited.add(cluster);position=data_at+(cluster-2)*spc*bps
            payload+=data[position:position+spc*bps];cluster=struct.unpack_from('<H',data,fat_at+cluster*2)[0]
        result[name]=bytes(payload[:size])
    return result


def replace(path,name,payload):
    """Replace one existing file's bytes without reallocating its cluster chain.

    Test-fixture mutation only; caller must pass an isolated image copy.
    """
    path=Path(path);data=bytearray(path.read_bytes());bps,spc,reserved,nfats,root_entries=struct.unpack_from('<HBHBH',data,11)
    fatsz=struct.unpack_from('<H',data,22)[0];root_at=(reserved+nfats*fatsz)*bps
    root_bytes=((root_entries*32+bps-1)//bps)*bps;fat_at=reserved*bps;data_at=root_at+root_bytes;long={}
    for at in range(root_at,root_at+root_bytes,32):
        entry=data[at:at+32]
        if not entry[0]:break
        if entry[0]==0xe5:long={};continue
        if entry[11]==0x0f:
            text=entry[1:11]+entry[14:26]+entry[28:32];long[entry[0]&31]=''.join(chr(x[0]) for x in struct.iter_unpack('<H',text) if x[0] not in (0,0xffff));continue
        if entry[11]&0x18:long={};continue
        found=''.join(long[i] for i in sorted(long)) if long else entry[:8].decode('ascii').rstrip()+'.'+entry[8:11].decode('ascii').rstrip();long={}
        if found.lower()!=name.lower():continue
        assert len(payload)==struct.unpack_from('<I',entry,28)[0]
        cluster=struct.unpack_from('<H',entry,26)[0];cursor=0;visited=set()
        while cursor<len(payload):
            assert 2<=cluster<0xfff8 and cluster not in visited;visited.add(cluster)
            count=min(spc*bps,len(payload)-cursor);position=data_at+(cluster-2)*spc*bps
            data[position:position+count]=payload[cursor:cursor+count];cursor+=count;cluster=struct.unpack_from('<H',data,fat_at+cluster*2)[0]
        path.write_bytes(data);assert files(path)[found]==payload;return
    raise KeyError(name)
