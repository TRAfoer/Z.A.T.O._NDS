#include "scene_script.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc,char **argv)
{
    assert(argc>=3);
    SceneScript script={0};
    if (!strcmp(argv[1],"reject")) {
        if (scene_script_open(&script,argv[2])) {
            fprintf(stderr,"accepted invalid script\n");scene_script_close(&script);return 1;
        }
        assert(!script.data && !script.events && !script.checkpoints);
        assert(!script.count && !script.checkpoint_count && script.error[0]);
        scene_script_close(&script);return 0;
    }
    if (!scene_script_open(&script,argv[1])) {
        fprintf(stderr,"open failed: %s\n",script.error);return 1;
    }
    assert(script.count==(unsigned)strtoul(argv[2],NULL,10));
    assert(script.events && script.checkpoints && script.checkpoint_count);
    for (unsigned i=0;i<script.count;++i)
        assert(script.events[i].op>=SC_TEXT && script.events[i].op<=SC_END);
    assert(script.events[script.count-1].op==SC_END);
    for (unsigned i=0;i<script.checkpoint_count;++i)
        assert(script.checkpoints[i].pc<script.count && !script.checkpoints[i].name[23]);
    unsigned count=script.count;
    scene_script_close(&script);
    assert(!script.data && !script.events && !script.checkpoints && !script.count);
    printf("PASS native script loader: %u events\n",count);
    return 0;
}
