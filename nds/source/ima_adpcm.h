#ifndef ZATO_IMA_ADPCM_H
#define ZATO_IMA_ADPCM_H
#include <stdint.h>
int ima_decode_block(const uint8_t *block, unsigned bytes, unsigned channels,
                     int16_t *pcm, unsigned capacity_frames);
#endif
