import unittest
from anim_player import RichText, ScenePlayer,PresentationClock


class TextTimingTests(unittest.TestCase):
    def test_presentation_clock_keeps_fractional_time_without_accumulating_timer_jitter(self):
        clock=PresentationClock();total=0.;presented=0.
        for dt in [.011,.023,.007,.045,.016,.018]*1000:
            total+=dt;presented+=clock.consume(dt)
        self.assertAlmostEqual(presented+clock.remainder,total,places=7)
        self.assertLess(clock.remainder,1/60)
    def test_first_glyph_starts_immediately_and_newline_consumes_no_cps_time(self):
        text=RichText('{cps=10}A\nB{nw}')
        self.assertEqual(text.visible(0),2)
        self.assertAlmostEqual(text.duration,.1)
        self.assertEqual(text.visible(.1),3)
    def test_authored_wait_speed_color_and_escaped_bracket(self):
        text = RichText('{cps=0}甲{w=0.3}{color=#f00}乙{/color}[[名{nw}')
        self.assertEqual(text.text, '甲乙[名')
        self.assertAlmostEqual(text.duration, .3)
        self.assertTrue(text.automatic)
        self.assertEqual(text.visible(.29), 1)
        self.assertEqual(text.glyphs[1]['color'], (255, 0, 0))

    def test_extend_does_not_replay_previous_waits(self):
        text = RichText('{cps=10}乙{w=0.2}{nw}', previous='甲{w=5}')
        self.assertEqual(text.text, '甲乙')
        self.assertAlmostEqual(text.duration, .2)
        self.assertEqual(text.visible(0), 2)


class PlayerTests(unittest.TestCase):
    def story(self, events):
        return dict(events=events, opening_setup=[], checkpoints={})

    def test_waits_keep_overshoot_and_separate_layer_lifetimes(self):
        p = ScenePlayer(self.story([
            dict(kind='show', line=1, image='ghost'),
            dict(kind='wait', line=2, seconds=.45, hard=True),
            dict(kind='hide', line=3, image='ghost'),
            dict(kind='show', line=4, image='mirror'),
            dict(kind='wait', line=5, seconds=.1, hard=True),
            dict(kind='say', line=6, text='完成', original='done', who=None),
        ]))
        p.tick(.50)
        self.assertEqual([x['name'] for x in p.layers], ['mirror'])
        self.assertIsNone(p.display)
        p.tick(.06)
        self.assertEqual(p.line, 6)
        self.assertEqual(p.display['text'].text, '完成')

    def test_nw_advances_but_ordinary_say_waits(self):
        p = ScenePlayer(self.story([
            dict(kind='say', line=1, text='{cps=0}甲{w=0.2}{nw}', original='', who=None),
            dict(kind='say', line=2, text='{cps=0}乙', original='', who=None),
        ]))
        p.tick(.19)
        self.assertEqual(p.line, 1)
        p.tick(.02)
        self.assertEqual(p.line, 2)
        p.tick(20)
        self.assertEqual(p.line, 2)

    def test_seek_reconstructs_prior_layers_and_extend_prefix(self):
        p = ScenePlayer(self.story([
            dict(kind='show', line=10, image='carfix_red'),
            dict(kind='say', line=11, text='甲', original='A', who='a'),
            dict(kind='say', line=12, text='乙', original='B', who='extend'),
        ]))
        p.seek(12)
        self.assertEqual([x['name'] for x in p.layers], ['carfix_red'])
        self.assertEqual(p.display['text'].text, '甲乙')
        self.assertEqual(p.display['who'], 'a')

    def test_seek_keeps_authored_automatic_text_animation_phase(self):
        story=self.story([
            dict(kind='show',line=1,image='ghost'),
            dict(kind='say',line=2,text='{cps=0}甲{w=0.2}{nw}',original='',who=None),
            dict(kind='say',line=3,text='乙',original='',who=None),
        ])
        natural=ScenePlayer(story); natural.tick(.2)
        direct=ScenePlayer(story); direct.seek(3)
        self.assertAlmostEqual(direct.now-direct.layers[0]['start'], natural.now-natural.layers[0]['start'])

    def test_literal_soft_pause_and_seek_target_sound(self):
        class Audio:
            def __init__(self):self.calls=[]
            def reset(self):self.calls=[]
            def play(self,*args,**kwargs):self.calls.append(args)
        audio=Audio();p=ScenePlayer(self.story([
            dict(kind='command',line=1,value='play sound "audio/glitch.mp3"'),
            dict(kind='show',line=2,image='text',literal='[SIGNAL]'),
            dict(kind='wait',line=3,seconds=1,hard=False),
            dict(kind='say',line=4,text='甲',original='',who=None),
        ]),audio=audio)
        p.seek(1)
        self.assertEqual(audio.calls,[('sfx','glitch.mp3')])
        self.assertEqual(p.stage_text,'[SIGNAL]')
        self.assertTrue(p.advance())
        self.assertEqual(p.line,4)

    def test_auto_reading_waits_for_all_adapted_pages(self):
        p=ScenePlayer(self.story([
            dict(kind='say',line=1,text='{cps=0}甲',original='A',who=None),
            dict(kind='say',line=2,text='乙',original='B',who=None),
        ]))
        p.auto=True;p.auto_delay=1.;p.display['page_count']=2
        p.tick(1.01)
        self.assertEqual(p.line,1)
        p.tick(1.)
        self.assertEqual(p.line,2)

    def test_automatic_last_glyph_must_be_presented_before_following_sound(self):
        class Audio:
            def __init__(self):self.calls=[]
            def reset(self):pass
            def play(self,*args,**kwargs):self.calls.append(args)
        audio=Audio()
        p=ScenePlayer(self.story([
            dict(kind='say',line=1,text='{cps=10}AB{nw}',original='',who='centered'),
            dict(kind='command',line=2,value='play sound "audio/glitch.mp3"'),
            dict(kind='scene',line=3,image='iss'),
            dict(kind='wait',line=4,seconds=.5,hard=True),
        ]),audio,require_present=True)
        p.mark_presented();p.tick(.1)
        self.assertEqual(p.line,1)
        self.assertEqual(p.display['text'].visible(p.elapsed()),2)
        self.assertEqual(audio.calls,[])
        p.mark_presented();p.tick(.016)
        self.assertEqual(audio.calls,[('sfx','glitch.mp3')])
        self.assertFalse(p.display['active'])

    def test_slow_frame_never_replays_several_timed_sounds_at_once(self):
        class Audio:
            def __init__(self):self.calls=[]
            def reset(self):pass
            def play(self,*args,**kwargs):self.calls.append(args)
        audio=Audio();p=ScenePlayer(self.story([
            dict(kind='wait',line=1,seconds=.1,hard=True),
            dict(kind='command',line=2,value='play sound "audio/a.mp3"'),
            dict(kind='say',line=3,text='{cps=0}甲{w=.1}{nw}',original='',who=None),
            dict(kind='command',line=4,value='play sound "audio/b.mp3"'),
            dict(kind='say',line=5,text='{cps=0}乙{w=.1}{nw}',original='',who=None),
        ]),audio,require_present=True)
        p.mark_presented();p.tick(.45)
        self.assertEqual(audio.calls,[('sfx','a.mp3')])
        self.assertAlmostEqual(p.now,.45)
        p.mark_presented();p.tick(.05)
        self.assertEqual(len(audio.calls),1)
        p.tick(.05)
        self.assertEqual(len(audio.calls),1)
        p.mark_presented();p.tick(.016)
        self.assertEqual(len(audio.calls),1)
        p.mark_presented();p.tick(.016)
        self.assertEqual(audio.calls[-1],('sfx','b.mp3'))

    def test_music_seek_restores_known_elapsed_audio_position(self):
        class Audio:
            def __init__(self):self.calls=[]
            def reset(self):self.calls=[]
            def play(self,*args,**kwargs):self.calls.append((args,kwargs))
        audio=Audio();p=ScenePlayer(self.story([
            dict(kind='command',line=1,value='play music "audio/paradox.mp3" fadein 5'),
            dict(kind='wait',line=2,seconds=3,hard=True),
            dict(kind='say',line=3,text='甲',original='',who=None),
        ]),audio)
        p.seek(3)
        self.assertEqual(audio.calls[-1][1]['offset_seconds'],3.)

    def test_timed_text_wait_can_be_dismissed_without_skipping_the_whole_line(self):
        p=ScenePlayer(self.story([dict(kind='say',line=1,text='{cps=10}AB{w=1}C{nw}',original='',who=None)]))
        p.advance()
        self.assertEqual(p.display['text'].visible(p.elapsed()),2)
        self.assertAlmostEqual(p.elapsed(),.1)
        p.advance()
        self.assertEqual(p.display['text'].visible(p.elapsed()),3)
        self.assertAlmostEqual(p.elapsed(),1.1)

    def test_stale_frame_acknowledgment_does_not_present_a_new_scene(self):
        p=ScenePlayer(self.story([dict(kind='say',line=1,text='甲',original='',who=None),
                                 dict(kind='say',line=2,text='乙',original='',who=None)]),require_present=True)
        old=p.presentation_serial;p.seek(2)
        p.mark_presented(serial=old)
        self.assertTrue(p._awaiting_presentation)

    def test_timed_text_endpoint_is_presented_before_next_cue(self):
        p=ScenePlayer(self.story([
            dict(kind='say',line=1,text='{cps=0}甲{w=1}{nw}',original='',who='centered'),
            dict(kind='say',line=2,text='{cps=0}乙',original='',who='centered'),
        ]),require_present=True)
        p.mark_presented();p.tick(1.)
        self.assertEqual(p.line,1)
        p.mark_presented();p.tick(1/60)
        self.assertEqual(p.line,1)
        p.mark_presented();p.tick(1/60)
        self.assertEqual(p.line,2)
        self.assertAlmostEqual(p.display['start'],1+2/60)


if __name__ == '__main__': unittest.main()
