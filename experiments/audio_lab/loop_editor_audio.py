"""PCM audition transport, independent of file conversion and the NDS build."""
from threading import Lock
import numpy as np

PREVIEW_RATE=44100


class PlaybackCursor:
    def __init__(self):
        self.lock=Lock();self.pcm=None;self.position=0;self.playing=False;self.eof=False
        self.loop_start=None;self.end=0;self.wraps=0;self.volume=1.

    def load(self,pcm,position=0,loop_start=None,loop_end=None):
        end=len(pcm) if loop_end is None else loop_end
        if not 0<=position<=end<=len(pcm):raise ValueError('Invalid playback range')
        if loop_start is not None and not 0<=loop_start<end:raise ValueError('Invalid playback loop')
        with self.lock:
            self.pcm=pcm;self.position=position;self.end=end;self.loop_start=loop_start
            self.playing=True;self.eof=False;self.wraps=0

    def pause(self):
        with self.lock:self.playing=False

    def resume(self):
        with self.lock:
            if self.pcm is not None and not self.eof:self.playing=True

    def stop(self):
        with self.lock:self.playing=False;self.pcm=None;self.position=0

    def snapshot(self):
        with self.lock:return dict(position=self.position,playing=self.playing,eof=self.eof,wraps=self.wraps,loaded=self.pcm is not None)

    def fill(self,outdata):
        outdata.fill(0)
        with self.lock:
            at=0
            while self.playing and at<len(outdata):
                if self.position>=self.end:
                    if self.loop_start is None:self.playing=False;self.eof=True;break
                    self.position=self.loop_start;self.wraps+=1
                count=min(len(outdata)-at,self.end-self.position)
                segment=self.pcm[self.position:self.position+count]
                outdata[at:at+count]=segment if self.volume==1 else (segment.astype(np.float32)*self.volume).astype(np.int16)
                self.position+=count;at+=count


class SoundPlayer:
    def __init__(self):
        self.cursor=PlaybackCursor();self.stream=None;self.status_count=0

    def play(self,pcm,**kwargs):
        self.cursor.load(pcm,**kwargs)
        if self.stream is None:
            import sounddevice as sd
            def callback(outdata,frames,time,status):
                if status:self.status_count+=1
                self.cursor.fill(outdata)
            try:
                self.stream=sd.OutputStream(samplerate=PREVIEW_RATE,channels=2,dtype='int16',
                    blocksize=1024,callback=callback,latency='high')
                self.stream.start()
            except Exception:
                if self.stream:self.stream.close();self.stream=None
                self.cursor.stop();raise

    def close(self):
        self.cursor.stop()
        if self.stream:self.stream.stop();self.stream.close();self.stream=None
