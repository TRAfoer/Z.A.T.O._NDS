#ifndef ZATO_STORY_SAVE_FAT_H
#define ZATO_STORY_SAVE_FAT_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
/* Only fat:/ or sd:/ .nds argv paths are accepted. No argv uses the fixed
 * fat:/ZATO_Story_Chain.sav fallback. Never save relative to NitroFS. */
bool story_save_fat_path(char *out,size_t size,int argc,char *const argv[]);
bool story_save_fat_open(int argc,char *const argv[],const uint8_t story_id[32]);
#endif
