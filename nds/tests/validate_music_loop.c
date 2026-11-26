// Execute the actual runtime decoder/rings with host-only hardware and path stubs.
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static const char *override_catalog;
static FILE *fixture_fopen(const char *path, const char *mode)
{
    if (override_catalog && !strcmp(path, "nitro:/audio/index.bin")) return fopen(override_catalog, mode);
    char local[256];
    if (!strncmp(path, "nitro:/", 7)) {
        snprintf(local, sizeof(local), "nitrofs/%s", path+7);
        return fopen(local, mode);
    }
    return fopen(path, mode);
}
#define fopen fixture_fopen
#include "../source/audio_demo.c"
#undef fopen
mm_stream mock_stream;

int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "--reject")) {
        override_catalog=argv[2]; assert(!audio_demo_init());
        puts("PASS rejected invalid loop metadata"); return 0;
    }
    assert(argc == 3 && audio_demo_init());
    unsigned index=strtoul(argv[1],NULL,10);
    assert(index<g_audio_status.music_count);
    const AudioEntry *e=&entries[index];
    assert(e->loop_start<e->loop_end);
    FILE *reference=fopen(argv[2],"rb"); assert(reference);
    int16_t *expected=malloc(e->frames*4); assert(expected);
    assert(fread(expected,4,e->frames,reference)==e->frames); fclose(reference);
    g_audio_status.music_index=index;
    audio_demo_toggle_music();
    unsigned frame=0, wraps=0;
    int16_t out[512];
    while (wraps<3) {
        audio_demo_service();
        mock_stream.callback(256,out,MM_STREAM_16BIT_STEREO);
        for (unsigned i=0;i<256;i++) {
            assert(out[i*2]==expected[frame*2] && out[i*2+1]==expected[frame*2+1]);
            if (++frame==e->loop_end) { frame=e->loop_start; wraps++; }
        }
        assert(!g_audio_status.errors && !g_audio_status.music_underruns);
    }
    printf("PASS runtime PCM: %s, intro once, 3 exact wraps, loop start %u (block skip %u)\n",e->name,e->loop_start,e->loop_start%e->samples_per_block);
    voice_stop(&music); voice_stop(&sfx); free(entries); free(expected);
    return 0;
}
