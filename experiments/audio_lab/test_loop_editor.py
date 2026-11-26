from pathlib import Path
import json
import os
import tempfile
import unittest
import numpy as np
from loop_editor_model import parse_time, format_time, validate_state, save_project, load_project, waveform_envelope, recent_project
from loop_editor_audio import PlaybackCursor


def state():
    return dict(intro_end_frame=144896,source_loop_start_frame=144896,source_loop_end_frame=1500000,
                crossfade_frames=441,keep_full=False,reviewed=True,note='人工调整')


class ModelTests(unittest.TestCase):
    def test_reopening_uses_recent_saved_project_including_initial_window(self):
        with tempfile.TemporaryDirectory() as folder:
            regular=Path(folder)/'manual.json';initial=Path(folder)/'initial.json'
            self.assertEqual(recent_project(regular,initial),regular)
            initial.write_text('{}');self.assertEqual(recent_project(regular,initial),initial)
            regular.write_text('{}');os.utime(regular,(100,100));os.utime(initial,(200,200))
            self.assertEqual(recent_project(regular,initial),initial)

    def test_time_input_and_invalid_values(self):
        self.assertEqual(parse_time('01:02.500'),1378125)
        self.assertEqual(parse_time('62.5'),1378125)
        self.assertEqual(format_time(1378125),'01:02.500')
        for value in ['nan','inf','-1','1:60','abc']:
            with self.assertRaises(ValueError):parse_time(value)

    def test_atomic_save_reload_exact_markers_backup_and_no_active_edit_write(self):
        sources={'song.ogg':dict(frames=2000000,source_sha256='abc')}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'manual.json'
            tracks={'song.ogg':state()}
            save_project(path,tracks,sources)
            self.assertEqual(load_project(path,sources),tracks)
            payload=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(payload['status'],'pending_confirmation')
            self.assertEqual(payload['edits']['song.ogg']['intro_end_frame'],144896)
            tracks['song.ogg']['note']='second'
            save_project(path,tracks,sources)
            self.assertEqual(json.loads(path.with_suffix('.json.bak').read_text(encoding='utf-8'))['tracks']['song.ogg']['note'],'人工调整')
            self.assertFalse((Path(folder)/'edits.json').exists())
            with self.assertRaises(ValueError):load_project(path,{'song.ogg':dict(frames=2000000,source_sha256='changed')})

    def test_optional_intro_hard_cut_and_invalid_ranges(self):
        s=state()|dict(intro_end_frame=0,source_loop_start_frame=0,crossfade_frames=0)
        self.assertIsNotNone(validate_state(s,2000000))
        self.assertIsNone(validate_state(s|dict(keep_full=True),2000000))
        for change in [dict(source_loop_end_frame=144896),dict(source_loop_start_frame=-1),dict(source_loop_end_frame=2000001),dict(intro_end_frame=0)]:
            with self.assertRaises(ValueError):validate_state(state()|change,2000000)

    def test_waveform_preserves_short_impulses(self):
        pcm=np.zeros((1000,2),dtype=np.int16);pcm[501,0]=32767;pcm[502,1]=-32768
        low,high=waveform_envelope(pcm,0,1000,100)
        self.assertEqual(int(high.max()),32767)
        self.assertEqual(int(low.min()),-32768)


class TransportTests(unittest.TestCase):
    def test_intro_once_and_arbitrary_wraps_without_silent_padding(self):
        pcm=np.repeat(np.arange(10,dtype=np.int16)[:,None],2,axis=1)
        cursor=PlaybackCursor();cursor.load(pcm,loop_start=3,loop_end=10)
        out=np.empty((25,2),dtype=np.int16);cursor.fill(out)
        np.testing.assert_array_equal(out[:,0],list(range(10))+list(range(3,10))*2+[3])

    def test_eof_pause_and_seek(self):
        pcm=np.ones((5,2),dtype=np.int16)*7
        cursor=PlaybackCursor();cursor.load(pcm,position=3)
        cursor.pause();out=np.empty((3,2),dtype=np.int16);cursor.fill(out)
        self.assertEqual(cursor.snapshot()['position'],3);self.assertFalse(out.any())
        cursor.resume();cursor.fill(out)
        np.testing.assert_array_equal(out[:,0],[7,7,0]);self.assertFalse(cursor.snapshot()['playing'])


if __name__=='__main__':unittest.main()
