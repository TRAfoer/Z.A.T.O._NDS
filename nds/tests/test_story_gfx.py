"""Independent source pixels, hand-checked compositing and real C loader tests."""
from pathlib import Path
import os,shutil,struct,subprocess,sys,tempfile,unittest,zlib,json,re
import numpy as np
ROOT=Path(__file__).resolve().parents[2]

def literal_lz(raw):
    return b'\x10'+len(raw).to_bytes(3,'little')+b''.join(b'\0'+raw[i:i+8] for i in range(0,len(raw),8))

def fixture_assets(category=1):
    frame=struct.Struct('<4I4H2h2HI32s128s')
    red=np.full((8,8),0x801f,dtype='<u2');green=np.zeros((8,8),dtype='<u2');green[:,:4]=0x83e0
    blue=np.full((8,8),0xfc00,dtype='<u2');tiles=red.tobytes()+green.tobytes()+blue.tobytes()
    data_offset=40+16+3*196+6;payload=bytearray()
    def append(raw):
        while (data_offset+len(payload))%4:payload.append(0)
        offset=data_offset+len(payload);packed=literal_lz(raw);payload.extend(packed);return offset,len(packed)
    bank_offset,bank_bytes=append(tiles);records=[]
    for i in range(3):
        offset,size=append(struct.pack('<H',i))
        records.append(frame.pack(0,offset,size,2,8,8,1,1,124,92,category,0,0,b'\0'*32,f'fixture{i}.png'.encode().ljust(128,b'\0')))
    header=struct.pack('<8s8I',b'ZATONDP1',1,3,1,1,2,0,0,196)
    return header+struct.pack('<4I',bank_offset,bank_bytes,len(tiles),128)+b''.join(records)+struct.pack('<3H',0,1,2)+payload

def fixture_catalog(asset_bytes):
    images=[]
    for i in range(3):images.append((0,i,1,0,0,0,0,0,0,0,0,0))
    images += [(1,0,2,1,0,0,0,0,0,0,0,0),(1,2,2,0,0,0,0,0,0,0,0,0),
               (2,1,2,0,65536,65536,0,0,50,42,0,0),(3,0,0,0,0,0,0,0,0,0,0,0)]
    seq=[(0,6,0,0),(2,6,0,0),(0,6,0,0),(2,0,12,0)]
    base=(4096,0,65536,65536,0,32768,32768,32768,32768,1,-1,0)
    programs=[base,(2048,*base[1:]),(4096,2,*base[2:]),
              (*base[:7],0,32768,*base[9:11],1),(*base[:9],-1,*base[10:]),
              (*base[:2],65536,131072,60,*base[5:])]
    geo=[(16,8<<16,8<<16,0,0,192<<16,0,0)]*3
    io=64;so=io+len(images)*48;po=so+len(seq)*16;go=po+len(programs)*48
    payload=b''.join(struct.pack('<12i',*x) for x in images)+b''.join(struct.pack('<4I',*x) for x in seq)+b''.join(struct.pack('<12i',*x) for x in programs)+b''.join(struct.pack('<8i',*x) for x in geo)
    header=struct.pack('<8s14I',b'ZSTGFX01',1,64+len(payload),len(images),len(seq),len(programs),3,io,so,po,go,asset_bytes,zlib.crc32(payload),0,0)
    return bytearray(header+payload)

def blank():return np.full((192,256),0x8000,dtype='<u2')
def rect(screen,color,x=124,y=92,w=8,h=8):screen[max(y,0):min(y+h,192),max(x,0):min(x+w,256)]=color
def write_cases(path,actions):
    with path.open('wb') as f:
        f.write(struct.pack('<8sI',b'STORYTST',len(actions)))
        for fields,pixels in actions:
            f.write(struct.pack('<8I',*(value&0xffffffff for value in fields)))
            if pixels is not None:f.write(pixels.astype('<u2').tobytes())

def position_fixture():
    """Distinct tile stripes make clipped/very wide positions observable."""
    shapes=[(225,15,450,2),(224,16,448,2),(225,16,450,2),
            (8,124,16,2),(1024,-384,8192,2),(1023,-384,8192,2),(225,15,450,1)]
    n=len(shapes);frame=struct.Struct('<4I4H2h2HI32s128s');payload=bytearray();patterns=[]
    tiles=[];maps=[]
    for i,(width,x,source,category) in enumerate(shapes):
        cols=(width+7)//8;ids=[];pixels=np.empty((8,width),dtype='<u2')
        for col in range(cols):
            color=0x8000|((col+i*3)%31+1)|(((col*3+i)%31)<<5)
            ids.append(len(tiles));tiles.append(np.full((8,8),color,dtype='<u2').tobytes())
            pixels[:,col*8:min(width,col*8+8)]=color
        maps.append(struct.pack('<'+'H'*len(ids),*ids));patterns.append(pixels)
    data_at=40+16+n*196+n*2
    def append(raw):
        while (data_at+len(payload))%4:payload.append(0)
        at=data_at+len(payload);packed=literal_lz(raw);payload.extend(packed);return at,len(packed)
    raw=b''.join(tiles);bank_at,bank_n=append(raw);records=[]
    for i,((width,x,source,category),mapping) in enumerate(zip(shapes,maps)):
        at,packed=append(mapping)
        records.append(frame.pack(0,at,packed,len(mapping),width,8,(width+7)//8,1,x,92,category,0,0,b'\0'*32,f'actor{i}'.encode().ljust(128,b'\0')))
    assets=struct.pack('<8s8I',b'ZATONDP1',1,n,1,1,n-1,0,0,196)+struct.pack('<4I',bank_at,bank_n,len(raw),128)+b''.join(records)+struct.pack('<'+'H'*n,*range(n))+payload
    images=[(0,i,1,0,0,0,0,0,0,0,0,0) for i in range(n)]+[(1,0,2,1,0,0,0,0,0,0,0,0)]
    seq=[(0,6,0,0),(1,6,0,0)];base=(4096,0,65536,65536,0,32768,32768,32768,32768,1,-1,0)
    programs=[base,(*base[:11],1)]
    geo=[(source,width<<16,8<<16,round((x+width/2-128)*65536),0,192<<16,0,0) for width,x,source,category in shapes]
    io=64;so=io+len(images)*48;po=so+len(seq)*16;go=po+len(programs)*48
    body=b''.join(struct.pack('<12i',*x) for x in images)+b''.join(struct.pack('<4I',*x) for x in seq)+b''.join(struct.pack('<12i',*x) for x in programs)+b''.join(struct.pack('<8i',*x) for x in geo)
    catalog=struct.pack('<8s14I',b'ZSTGFX01',1,64+len(body),len(images),len(seq),len(programs),n,io,so,po,go,len(assets),zlib.crc32(body),0,0)+body
    return assets,catalog,patterns

class StoryGfxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.tmp=Path(cls.temp.name);cls.exe=cls.tmp/'validate_story_gfx.exe'
        cc=os.environ.get('CC') or shutil.which('gcc') or shutil.which('clang')
        if not cc:raise unittest.SkipTest('host C compiler unavailable')
        source=os.environ.get('STORY_GFX_TEST_SOURCE') or str(ROOT/'nds/source/story_gfx.c')
        subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'nds/source'),source,str(ROOT/'nds/tests/validate_story_gfx.c'),'-o',str(cls.exe)],check=True)
        cls.assets=cls.tmp/'assets.zat';cls.assets.write_bytes(fixture_assets());cls.catalog=cls.tmp/'gfx.bin';cls.catalog.write_bytes(fixture_catalog(cls.assets.stat().st_size))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def native(self,a,b,c):
        r=subprocess.run([str(self.exe),str(a),str(b),str(c)],capture_output=True,text=True)
        match=re.search(r'case(\d+)',r.stderr);context=getattr(self,'case_labels',{}).get(int(match[1])) if match else None
        self.assertEqual(r.returncode,0,r.stdout+r.stderr+str(context or ''));return r.stdout
    def test_transparency_eight_tags_left_right_clip_behind_and_blends(self):
        actions=[]
        def command(op,t=0,a=0,b=0,c=0,d=0,e=-1,flag=0,pixels=None):actions.append(((op,t,a,b,c,d,e,flag),pixels))
        command(1,a=0,b=0)
        s=blank();rect(s,0x801f);command(3,pixels=s)
        command(1,a=1,b=1);s=s.copy();rect(s,0x83e0,w=4);command(3,pixels=s,flag=-1)
        command(2,a=1);command(1,a=1,b=1,d=1);s=blank();rect(s,0x801f);rect(s,0x8210,w=4);command(3,pixels=s)
        command(2,a=1);command(1,a=1,b=1,d=2);s=blank();rect(s,0x801f);rect(s,0x8000,w=4);command(3,pixels=s)
        command(0)
        for tag in range(8):command(1,a=0,b=tag)
        command(1,a=2,b=9,e=0);s=blank();rect(s,0x801f);command(3,pixels=s)
        command(0);command(1,a=0,b=0,c=1);command(1,a=2,b=1,c=2);s=blank();rect(s,0x801f,x=60);rect(s,0xfc00,x=187);command(3,pixels=s)
        command(1,a=1,b=0,c=-1);s=blank();rect(s,0x83e0,x=60,w=4);rect(s,0xfc00,x=187);command(3,pixels=s)
        command(0);command(1,a=2,b=0,d=3);s=blank();rect(s,0xfc00,x=-4);command(3,pixels=s)
        command(0);command(1,a=1,b=0,d=4);s=blank();rect(s,0x83e0,x=128,w=4);command(3,pixels=s)
        path=self.tmp/'hand.cases';write_cases(path,actions);print(self.native(self.assets,self.catalog,path).strip())
    def test_actor_spread_floor_clamp_expression_inheritance_sequence_and_move_endpoints(self):
        assets,catalog,patterns=position_fixture();ap=self.tmp/'actor_assets.zat';cp=self.tmp/'actor_gfx.bin'
        ap.write_bytes(assets);cp.write_bytes(catalog);actions=[]
        def op(code,t=0,a=0,b=0,c=0,d=0,e=-1,pixels=None):actions.append(((code,t,a,b,c,d,e,0),pixels))
        def gold(image,x):
            result=blank();pixels=patterns[image];width=pixels.shape[1]
            result[92:100,max(x,0):min(x+width,256)]=pixels[:,max(0,-x):min(width,256-x)]
            return result
        # First round the original PC projection, then floor signed dx*3/2.
        # Clamp the exact (possibly half-pixel) canvas center to [48,208].
        positions=[(-47,78),(-47,77),(-47,77),(44,204),(-304,-464),(-304,-463),(-26,57)]
        for image,(left,right) in enumerate(positions):
            for place,x in [(1,left),(2,right),(0,[15,16,16,124,-384,-384,15][image])]:
                op(0);op(1,a=image,b=0,c=place);op(3,pixels=gold(image,x))
        # Explicit anchor overrides keep packed center despite left placement.
        op(0);op(1,a=0,b=0,c=1,d=1);op(3,pixels=gold(0,15))
        op(0);op(1,a=0,b=0,c=1);op(1,a=1,b=0,c=-1);op(3,pixels=gold(1,-47))
        op(0);op(1,a=7,b=0,c=2);op(3,pixels=gold(0,78));op(3,t=6,pixels=gold(1,77))
        # Same-tag ease interpolates between spread endpoints; factories still
        # begin/end outside the viewport rather than applying the stable clamp.
        op(0);op(1,a=0,b=0,c=1);op(6);op(1,a=0,b=0,c=2);op(7,a=5,b=60);op(8)
        op(3,pixels=gold(0,-47));op(3,t=30,pixels=gold(0,15));op(3,t=60,pixels=gold(0,78))
        op(0,t=60);op(6,t=60);op(1,t=60,a=0,b=0,c=1);op(7,t=60,a=9,b=60);op(8,t=60)
        op(3,t=60,pixels=blank());op(3,t=120,pixels=gold(0,-47))
        op(6,t=120);op(2,t=120,a=0);op(7,t=120,a=8,b=60);op(8,t=120)
        op(3,t=120,pixels=gold(0,-47));op(3,t=180,pixels=blank())
        path=self.tmp/'actor_positions.cases';write_cases(path,actions);print(self.native(ap,cp,path).strip())
    def test_sequence_loop_and_internal_dissolve_hold_have_no_draw_io(self):
        actions=[((1,0,3,0,0,0,-1,0),None)]
        for tick,color,same in [(0,0x801f,0),(5,0x801f,1),(6,0xfc00,-1),(11,0xfc00,1),(12,0x801f,-1)]:
            s=blank();rect(s,color);actions.append(((3,tick,0,0,0,0,0,same),s))
        actions.extend([((0,0,0,0,0,0,0,0),None),((1,0,4,0,0,0,-1,0),None)])
        for tick,color in [(0,0x801f),(6,0x801f),(12,0xc010),(18,0xfc00),(1000,0xfc00)]:
            s=blank();rect(s,color);actions.append(((3,tick,0,0,0,0,0,0),s))
        path=self.tmp/'seq.cases';write_cases(path,actions);print(self.native(self.assets,self.catalog,path).strip())
    def test_output_needs_only_uint16_alignment(self):
        screen=blank();rect(screen,0x801f)
        actions=[((1,0,0,0,0,0,-1,0),None),((3,0,0,0,0,0,0,0),screen)]
        path=self.tmp/'unaligned.cases';write_cases(path,actions)
        env=dict(os.environ,STORY_GFX_UNALIGNED='1')
        result=subprocess.run([str(self.exe),str(self.assets),str(self.catalog),str(path)],capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_packer_produces_complete_source_catalog(self):
        out=self.tmp/'actual.bin';manifest=self.tmp/'actual.json'
        r=subprocess.run([sys.executable,str(ROOT/'nds/tools/pack_story_gfx.py'),'--output',str(out),'--manifest',str(manifest)],capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        m=json.loads(manifest.read_text(encoding='utf8'))
        self.assertEqual((m['static_frame_count'],m['named_definition_count'],m['sequence_count'],m['special_definition_count'],m['particle_definition_count'],m['inline_program_operations']),(505,20,5,8,7,27))
        self.assertEqual(len(m['operations']),3003)
        self.assertIn('Position(xalign=0.25)',[x['expression'] for x in m['placement_sources']])
        # The default frozen package remains supported after the selected ROM
        # switches to an approved replacement; only its recorded byte size differs.
        actual=out.read_bytes();selected=(ROOT/'nds/nitrofs/story/gfx.bin').read_bytes()
        self.assertEqual(actual[:48]+actual[52:],selected[:48]+selected[52:],'catalog programs/geometry must be deterministic across approved asset packs')
    @unittest.skipUnless((ROOT/'nds/artifacts/story_assets_manifest.json').exists(),'generate approved family package first')
    def test_catalog_accepts_explicit_approved_family_manifest_and_rejects_geometry_drift(self):
        package=ROOT/'nds/nitrofs/story/assets.zat';metadata=ROOT/'nds/artifacts/story_assets_manifest.json'
        out=self.tmp/'family.bin';manifest=self.tmp/'family.json'
        cmd=[sys.executable,str(ROOT/'nds/tools/pack_story_gfx.py'),'--assets',str(package),'--asset-manifest',str(metadata),'--output',str(out),'--manifest',str(manifest)]
        result=subprocess.run(cmd,capture_output=True,text=True,cwd=ROOT)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        m=json.loads(manifest.read_text(encoding='utf8'));a=json.loads(metadata.read_text(encoding='utf8'))
        self.assertEqual(m['asset_pack_sha256'],a['package_sha256'])
        self.assertEqual(struct.unpack_from('<I',out.read_bytes(),48)[0],package.stat().st_size)
        a['frames'][172]['width']+=1;bad=self.tmp/'bad_family.json';bad.write_text(json.dumps(a),encoding='utf8')
        cmd[cmd.index('--asset-manifest')+1]=str(bad)
        result=subprocess.run(cmd,capture_output=True,text=True,cwd=ROOT)
        self.assertNotEqual(result.returncode,0);self.assertIn('geometry',result.stderr)
    def test_move_transitions_only_move_tags_and_replay_is_metadata_only(self):
        actions=[((1,0,0,0,0,0,-1,0),None),((6,0,0,0,0,0,0,0),None),
                 ((1,0,2,1,2,0,-1,0),None),((7,0,7,60,0,0,0,0),None),((8,0,0,0,0,0,0,0),None)]
        s=blank();rect(s,0x801f);actions.append(((3,0,0,0,0,0,0,0),s))
        # easeinright: right edge x256 -> authored right x187; sin(pi/4).
        s=s.copy();rect(s,0xfc00,x=207);actions.append(((3,30,0,0,0,0,0,0),s))
        s=blank();rect(s,0x801f);rect(s,0xfc00,x=187);actions.append(((3,60,0,0,0,0,0,0),s))
        actions.extend([((6,60,0,0,0,0,0,0),None),((2,60,1,0,0,0,0,0),None),((7,60,6,60,0,0,0,0),None),((8,60,0,0,0,0,0,0),None)])
        s=blank();rect(s,0x801f);rect(s,0xfc00,x=130);actions.append(((3,90,0,0,0,0,0,0),s))
        s=blank();rect(s,0x801f);actions.append(((3,120,0,0,0,0,0,0),s))
        # Clear/replay many state changes without loading any banks or maps.
        actions.extend([((9,120,1,0,0,0,0,0),None),((0,120,0,0,0,0,0,0),None)])
        for i in range(50):actions.extend([((1,120,0,9,0,0,-1,0),None),((2,120,9,0,0,0,0,0),None)])
        actions.extend([((1,120,2,9,1,0,-1,0),None),((9,120,0,0,0,0,0,0),None),((10,120,0,0,0,0,0,0),None)])
        s=blank();rect(s,0xfc00,x=60);actions.append(((3,120,0,0,0,0,0,0),s))
        path=self.tmp/'move.cases';write_cases(path,actions);print(self.native(self.assets,self.catalog,path).strip())
    def test_ease_same_tag_and_remaining_enter_leave_factories(self):
        actions=[]
        def op(code,t=0,a=0,b=0,c=0,d=0,e=-1,pixels=None):actions.append(((code,t,a,b,c,d,e,0),pixels))
        op(1,a=0,b=0);op(1,a=2,b=1,c=1);op(6);op(1,a=2,b=1,c=2);op(7,a=5,b=60);op(8)
        s=blank();rect(s,0x801f);rect(s,0xfc00,x=123);op(3,30,pixels=s)
        # Finish, then entering left uses _moveleft: xpos0 xanchor1.
        op(0,t=60);op(1,t=60,a=0,b=0);op(6,t=60);op(1,t=60,a=2,b=1,c=1);op(7,t=60,a=9,b=60);op(8,t=60)
        s=blank();rect(s,0x801f);rect(s,0xfc00,x=40);op(3,90,pixels=s)
        s=blank();rect(s,0x801f);rect(s,0xfc00,x=60);op(3,120,pixels=s)
        op(6,t=120);op(2,t=120,a=1);op(7,t=120,a=8,b=60);op(8,t=120)
        s=blank();rect(s,0x801f);rect(s,0xfc00,x=117);op(3,150,pixels=s)
        s=blank();rect(s,0x801f);op(3,180,pixels=s)
        # Plain ease has no enter/leave factory in installed MoveTransition:
        # fresh tags are absent until the move ends; stable background remains.
        op(6,t=180);op(1,t=180,a=2,b=2,c=1);op(7,t=180,a=5,b=60);op(8,t=180)
        s=blank();rect(s,0x801f);op(3,210,pixels=s)
        s=blank();rect(s,0x801f);rect(s,0xfc00,x=60);op(3,240,pixels=s)
        # A batch with no WITH immediately releases its old-only tag metadata.
        op(6,t=240);op(2,t=240,a=2);op(8,t=240);s=blank();rect(s,0x801f);op(3,240,pixels=s)
        path=self.tmp/'all_move.cases';write_cases(path,actions);print(self.native(self.assets,self.catalog,path).strip())
    def test_catalog_bounds_and_crc_are_rejected(self):
        source=self.catalog.read_bytes();bad=[source[:n] for n in (0,8,63,len(source)-1)]
        for at,v in [(8,2),(16,0xffffffff),(24,0),(28,999),(32,60),(36,0xfffffffc),(40,64),(44,64),(48,0)]:
            x=bytearray(source);struct.pack_into('<I',x,at,v);bad.append(x)
        x=bytearray(source);x[-1]^=1;bad.append(x)
        seqoff,progoff,geooff=struct.unpack_from('<3I',source,36)
        for at,v in [(64,4),(68,3),(64+3*48+8,999),(seqoff,3),
                     (progoff,4097),(progoff+8,0),(progoff+36,0),
                     (progoff+44,8),(geooff,0)]:
            x=bytearray(source);struct.pack_into('<I',x,at,v);struct.pack_into('<I',x,52,zlib.crc32(x[64:]));bad.append(x)
        x=bytearray(source);struct.pack_into('<I',x,seqoff+4,0);struct.pack_into('<I',x,seqoff+16+4,0)
        struct.pack_into('<I',x,52,zlib.crc32(x[64:]));bad.append(x)
        for i,data in enumerate(bad):
            p=self.tmp/f'bad{i}.bin';p.write_bytes(data);self.native('reject',self.assets,p)
    @unittest.skipUnless((ROOT/'nds/nitrofs/story/gfx.bin').exists(),'generate normal catalog first')
    def test_real_masters_sequences_programs_and_all_source_cache_bounds(self):
        from story_gfx_oracle import Oracle
        oracle=Oracle();m=oracle.manifest;actions=[];ids=m['name_to_image_id'];self.case_labels={}
        def case(layers,ticks):
            actions.append(((0,0,0,0,0,0,0,0),None))
            for tag,(name,place,prog,start) in enumerate(layers):actions.append(((1,start,ids[name],tag,place,prog,-1,0),None))
            state=[(ids[name],place,prog,start) for name,place,prog,start in layers]
            self.case_labels[len(actions)]=(layers,ticks)
            actions.append(((3,ticks,0,0,0,0,0,0),oracle.draw(state,ticks)))
        case([('library',0,0,0),('gupanic',1,0,0),('iuglare',2,0,0)],0)
        case([('cafe3',0,0,0),('musoft',1,0,0),('iustare',2,0,0)],0)
        case([('black',0,0,0)],0)
        case([('cafe3',0,0,0),('black',0,0,0)],0)
        case([('black',0,0,0),('cafe3',0,0,0)],0)
        probes={'inejb':[0,29,30,45,59,60,119,120,135],
                'mublur':[0,6,11,12,18,23,60,72,84],
                'ppulse':[0,5,6,41,42,43],
                'tosyacg':[0,59,60,120,180,299,300,1000],
                'byebye':[0,8,9,35,36]}
        for name,ticks in probes.items():
            for tick in ticks:case([(name,0,0,0)],tick)
        for key in ['ep2d2:380','ep2d2:383','ep2d2:1982','ep2d6:841','ep2d7:750','ep2d7:1119','ep2d7:2221','ep3:683','ep3:2618']:
            row=m['show_keys'][key];prog=row['program_id'];duration=m['programs'][prog]['values'][4]
            for tick in sorted({0,duration//2,duration}):case([(row['name'],0,prog,0)],tick)
        for name in ['snow1','snow2','snowred1','snowred2','snowup1','snowup2','snowup3']:
            for tick in [0,29,180,2999,3000,6001,0xffffff00]:case([(name,0,0,0)],tick)
        # Trace every ordinary visual operation. No raster is rewritten; this
        # exercises all real bank/map dependency combinations and allocations.
        actions.append(((0,0,0,0,0,0,0,0),None));in_special=False
        for row in m['operations']:
            special=row['file']=='ep2d3' and (599<=row['line']<=687 or 792<=row['line']<=795)
            if special:
                if not in_special:actions.append(((0,0,0,0,0,0,0,0),None))
                in_special=True;continue
            in_special=False
            if row.get('literal'):continue
            if row['clear']:actions.append(((0,0,0,0,0,0,0,0),None))
            if row['kind']=='hide':actions.append(((2,0,row['tag_id'],0,0,0,0,0),None))
            else:actions.append(((1,0,row['image_id'],row['tag_id'],row['placement'],row['program_id'],row['behind_tag'],0),None))
        path=self.tmp/'actual.cases';write_cases(path,actions)
        print(self.native(Path(m.get('asset_pack_source',ROOT/'nds/nitrofs/assets.zat')),ROOT/'nds/nitrofs/story/gfx.bin',path).strip())
    @unittest.skipUnless((ROOT/'nds/nitrofs/story/gfx.bin').exists(),'generate normal catalog first')
    def test_every_source_batch_and_move_union_stays_bounded(self):
        sys.path.insert(0,str(ROOT/'experiments/asset_lab'));import anim_story as source
        m=json.loads((ROOT/'nds/artifacts/story_gfx_manifest.json').read_text(encoding='utf8'))
        actions=[];batch=False;clock=0
        def op(code,a=0,b=0,c=0,d=0,e=-1):actions.append(((code,clock,a,b,c,d,e,0),None))
        for module in ['script','ep1']+[f'ep2d{i}' for i in range(1,8)]+['ep3']:
            objects=source.read_compiled(source.DEFAULT_GAME/(module+'.rpyc'))
            block=next(source.attrs(x)['block'] for x in objects if x.cls=='renpy.ast.Label' and source.attrs(x).get('name')==('start' if module=='script' else module))
            for node in block:
                state=source.attrs(node);kind=node.cls.rsplit('.',1)[-1];line=state.get('linenumber',0)
                if module=='ep2d3' and (599<=line<=687 or 792<=line<=795):
                    if batch:op(8);batch=False
                    op(0);continue
                if kind in ('Show','Hide','Scene'):
                    row=m['show_keys'].get(f'{module}:{line}')
                    if row is None:continue
                    if not batch:op(6);batch=True
                    if row['clear']:op(0)
                    if kind=='Hide':op(2,row['tag_id'])
                    else:op(1,row['image_id'],row['tag_id'],row['placement'],row['program_id'],row['behind_tag'])
                elif kind=='With':
                    expression=str(source._expression(state['expr']))
                    moves={'ease':5,'easeoutleft':6,'easeinright':7,'easeoutright':8,'easeinleft':9}
                    if expression in moves:
                        if not batch:op(6);batch=True
                        op(7,moves[expression],30);op(8);clock+=30;op(11);batch=False
                    elif batch:op(8);batch=False
                elif kind=='Say' or kind=='Python' and 'pause' in str(source._expression(state.get('code'))) or kind=='UserStatement' and state['line'].startswith('pause'):
                    if batch:op(8);batch=False
            if batch:op(8);batch=False
        path=self.tmp/'batch_union.cases';write_cases(path,actions)
        print(self.native(Path(m.get('asset_pack_source',ROOT/'nds/nitrofs/assets.zat')),ROOT/'nds/nitrofs/story/gfx.bin',path).strip())

if __name__=='__main__':unittest.main()
