"""Account for a measured ARM decode peak in the existing quality budget."""
from pathlib import Path
import json
from experiments.video_lab import deband_op as d,convert_palette as p,restore_op_text as t
from experiments.video_lab.lossless_packet_opt import decoded_tiles

def main():
    out=d.OUT;path=out/'selection.json';selection=json.loads(path.read_text())
    backup=out/'selection_before_service_fix.json'
    if not backup.exists():d.write(backup,selection)
    options=selection['options'];global_frame=256;family=global_frame//60;local=global_frame%60
    for profile in range(1,4):
        option=options[family][profile]
        if option.get('service_raw_keys'):continue
        data=Path(option['path']).read_bytes();tiles=list(decoded_tiles(data))
        data,meta=t.fast_packets(data,[],key_frames={local:tiles[local]})
        target=out/'families'/f'{family:02}_{option["profile"]}_service.zpv';target.write_bytes(data)
        option.update(path=str(target),bytes=len(data),service_raw_keys=[global_frame],service_fix_bytes=meta['added_bytes'])
    limit=selection['max_movie_bytes'];source=p.inspect_pack(Path(selection['source_movie']).read_bytes())
    profiles=d.choose(options,limit+(len(options)-1)*88)
    selected=[options[i][profile] for i,profile in enumerate(profiles)]
    data=d.stitch([Path(x['path']).read_bytes() for x in selected],source)
    assert len(data)<=limit
    (out/'op_deband.zpv').write_bytes(data)
    selection.update(selected=selected,candidate_video_bytes=len(data),video_delta=len(data)-selection['source_video_bytes'],
        selected_quality_loss=sum(x['loss'] for x in selected),
        service_adjustment=dict(measured_frame=256,reason='One late commit under pause/resume stress; eliminate its LZ+XOR work using an exact raw key when treated'))
    encoder=p.Encoder(out/'native')
    try:selection['native_decoder']=p.verify_native(encoder.executable,out/'op_deband.zpv',out/'native_samples')
    finally:encoder.close()
    d.write(path,selection)
    print('Movie bytes',len(data),'profiles',[x['profile'] for x in selected])

if __name__=='__main__':main()
