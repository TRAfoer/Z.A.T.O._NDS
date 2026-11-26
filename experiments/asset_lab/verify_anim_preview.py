"""Reproducible full-context checks and native-pixel preview artifacts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image
from anim_story import build_story
from anim_player import ScenePlayer
from anim_renderer import AnimRenderer
from anim_text import SceneText
from asset_model import DEFAULT_GAME

OUT=Path(__file__).resolve().parent/'out/anim_preview'
ROOT=Path(__file__).resolve().parents[2]


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rom=ROOT/'ZATO_Asset_Audio_Stereo_64MB.nds'
    rom_hash=hashlib.sha256(rom.read_bytes()).hexdigest()
    story=build_story();player=ScenePlayer(story);text=SceneText(DEFAULT_GAME,story['characters'])
    renderer=AnimRenderer(sequences=story['sequences'],cache_size=80,iss_mode='text')
    steps=0;page_checks=0;observed=set()
    while not player.done:
        if player.display:
            d=player.display;line=d['source']['line']
            if line not in observed:
                observed.add(line)
                for bilingual in (False,True):
                    lower,info=text.lower(d,elapsed=player.elapsed(),bilingual=bilingual)
                    assert lower.size==(256,192)
                    assert info['pages']>=1
                    page_checks+=1
                layout=text.layout(d['text'],portrait=bool(text.portrait(d)))
                assert ''.join(e['glyph']['char'] for page in layout for e in page)==d['text'].text
        if player.wait_until is not None:player.tick(min(.1,max(.001,player.wait_until-player.now)))
        elif player.blocked and player.display and player.display['text'].automatic:player.tick(.05)
        elif player.blocked:player.reveal();player.advance()
        else:player.tick(.05)
        steps+=1
        assert steps<100000,'Context player stalled'
    expected=[e['line'] for e in story['events'] if e['kind']=='say']
    assert [e['line'] for e in player.history]==expected
    assert len(expected)==278 and len(story['sequences'])==8
    assert not player.diagnostics,player.diagnostics
    assert not text.diagnostics,text.diagnostics
    cases=[('before',260,0.),('iss',603,.475),('hands',613,.15),('red',627,.2),
           ('mirror',661,.15),('signal',690,1.),('after',697,.3),('window',792,.4)]
    captures=[]
    for name,line,advance in cases:
        player.seek(line)
        player.tick(advance)
        if player.display:player.reveal()
        top=renderer.render(player.layers,player.now)
        metadata=deepcopy(renderer.metadata)
        top=text.upper_overlay(top,player.display,elapsed=player.elapsed(),literal=player.stage_text)
        lower,info=text.lower(player.display,elapsed=player.elapsed(),window_visible=player.window_visible,quick_menu=player.quick_menu)
        if name=='red':
            assert info['portrait']
            assert lower.getpixel((196,0))==(255,255,255) and lower.getpixel((255,59))==(255,255,255)
        if name=='signal':assert player.stage_text and np.asarray(top).any()
        dual=Image.new('RGB',(256,384));dual.paste(top,(0,0));dual.paste(lower,(0,192))
        dual.save(OUT/f'verified_{name}_dual.png')
        dual.resize((512,768),Image.Resampling.NEAREST).save(OUT/f'verified_{name}_dual_2x.png')
        captures.append(dict(name=name,requested_line=line,actual_line=player.line,pages=info['pages'],portrait=info['portrait'],layers=[x['name'] for x in player.layers],renderer=metadata))
    # A native-pixel animated sample, deliberately free of invented dialogue.
    player.seek(627);player.display=None
    frames=[];start=player.now
    for i in range(60):
        top=renderer.render(player.layers,start+i*.05)
        frames.append(top.resize((512,384),Image.Resampling.NEAREST))
    frames[0].save(OUT/'red_effect_2x.gif',save_all=True,append_images=frames[1:],duration=50,loop=0,optimize=False)
    assert hashlib.sha256(rom.read_bytes()).hexdigest()==rom_hash
    for rel,expected_hash in story['source_sha256'].items():
        path=DEFAULT_GAME/rel.removeprefix('game/')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==expected_hash
    report=dict(result='PASS',iss_preview_mode='native_text_fit',events=len(story['events']),dialogue=len(expected),sequences=len(story['sequences']),
                complete_context_history_matches_source=True,layout_checks=page_checks,simulation_steps=steps,
                captures=captures,source_sha256=story['source_sha256'],diagnostics=story['diagnostics'],
                rom_unchanged_sha256=rom_hash,
                limitations=['Desktop preview, not NDS performance or a full RenPy compatibility claim.',
                'Original-texture is10 remains unresolved; optional texture mode retains the previous valid frame. Default iss uses an explicit native-text approximation.',
                'Transforms/subpixel sampling approximate the source; independent source-composited comparisons are in render_evidence.json.',
                'Development jumps restore known authored music time; human reading pauses cannot be reconstructed.'])
    (OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'PASS {len(expected)} dialogue in exact source order; {page_checks} layouts; 8 scene captures; unchanged ROM/source hashes')


if __name__=='__main__':main()
