#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include "ima_adpcm.h"
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

int main(int argc, char **argv)
{
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    if (argc != 6) return 2;
    FILE *file = fopen(argv[1], "rb");
    if (!file) return 3;
    unsigned offset = strtoul(argv[2], NULL, 10), bytes = strtoul(argv[3], NULL, 10);
    unsigned channels = strtoul(argv[4], NULL, 10), frames = strtoul(argv[5], NULL, 10);
    if (fseek(file, offset, SEEK_SET)) return 4;
    uint8_t block[512]; int16_t pcm[2048];
    while (bytes >= sizeof(block) && frames) {
        if (fread(block, 1, sizeof(block), file) != sizeof(block)) return 5;
        int count = ima_decode_block(block, sizeof(block), channels, pcm, 1024);
        if (count < 0) return 6;
        unsigned take = (unsigned)count < frames ? (unsigned)count : frames;
        if (fwrite(pcm, sizeof(int16_t) * channels, take, stdout) != take) return 7;
        frames -= take; bytes -= sizeof(block);
    }
    fclose(file);
    return frames ? 8 : 0;
}
