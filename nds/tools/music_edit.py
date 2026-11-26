"""Apply sample-accurate intro/loop decisions without changing loop duration."""
import numpy as np


def render_edit(pcm, edit):
    intro = int(edit['intro_end_frame'])
    start = int(edit['source_loop_start_frame'])
    end = int(edit['source_loop_end_frame'])
    fade = int(edit['crossfade_frames'])
    if not (0 <= fade < end-start and 0 <= start < end <= len(pcm)
            and (intro == 0 or fade < intro <= len(pcm))):
        raise ValueError('Invalid introduction/loop/crossfade frame range')
    opening = pcm[:intro].astype(np.float64).copy()
    loop = pcm[start:end].astype(np.float64).copy()
    # Blend into the context immediately BEFORE the loop head. Both joins then
    # continue naturally into source[start], while preserving the beat period.
    if fade:
        preroll = pcm[max(0,start-fade):start].astype(np.float64)
        if len(preroll)<fade:
            preroll=np.concatenate((np.repeat(pcm[:1],fade-len(preroll),axis=0),preroll))
        weight = (.5 - .5 * np.cos(np.linspace(0, np.pi, fade)))[:, None]
        if fade==1:weight[:]=1
        if intro: opening[-fade:] = opening[-fade:] * (1-weight) + preroll * weight
        loop[-fade:] = loop[-fade:] * (1-weight) + preroll * weight
    out = np.clip(np.rint(np.concatenate((opening, loop))), -32768, 32767).astype('<i2')
    return out, intro, len(out)
