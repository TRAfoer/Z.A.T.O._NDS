#include "ima_adpcm.h"

static const int steps[89] = {
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,
    118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,
    963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,
    4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,
    20350,22385,24623,27086,29794,32767
};
static const int adjustments[8] = {-1,-1,-1,-1,2,4,6,8};

int ima_decode_block(const uint8_t *block, unsigned bytes, unsigned channels,
                     int16_t *pcm, unsigned capacity)
{
    if ((channels != 1 && channels != 2) || bytes < channels * 4 ||
        (bytes - channels * 4) % (channels * 4)) return -1;
    unsigned frames = (bytes - channels * 4) * 2 / channels + 1;
    if (frames > capacity) return -1;
    int predictor[2], index[2];
    for (unsigned c = 0; c < channels; c++) {
        predictor[c] = (int16_t)(block[c * 4] | (block[c * 4 + 1] << 8));
        index[c] = block[c * 4 + 2];
        if (index[c] > 88) return -1;
        pcm[c] = predictor[c];
    }
    unsigned cursor = channels * 4, frame = 1;
    while (cursor < bytes) {
        for (unsigned c = 0; c < channels; c++) {
            for (unsigned b = 0; b < 4; b++) {
                unsigned packed = block[cursor++];
                for (unsigned n = 0; n < 2; n++) {
                    unsigned code = (packed >> (n * 4)) & 15;
                    int step = steps[index[c]];
                    int delta = (step >> 3) + ((code & 1) ? step >> 2 : 0) +
                                ((code & 2) ? step >> 1 : 0) + ((code & 4) ? step : 0);
                    predictor[c] += code & 8 ? -delta : delta;
                    if (predictor[c] > 32767) predictor[c] = 32767;
                    if (predictor[c] < -32768) predictor[c] = -32768;
                    index[c] += adjustments[code & 7];
                    if (index[c] < 0) index[c] = 0;
                    if (index[c] > 88) index[c] = 88;
                    pcm[(frame + b * 2 + n) * channels + c] = predictor[c];
                }
            }
        }
        frame += 8;
    }
    return frames;
}
