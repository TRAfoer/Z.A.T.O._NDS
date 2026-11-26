#include "asset_pack.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv)
{
    uint8_t output[32];
    const uint8_t literal[] = {0x10,3,0,0,0,'A','B','C'};
    const uint8_t overlap[] = {0x10,6,0,0,0x40,'A',0x20,0};
    const uint8_t bad_distance[] = {0x10,3,0,0,0x80,0,0};
    assert(lz10_decode(literal, sizeof(literal), output, sizeof(output)) == 3);
    assert(!memcmp(output, "ABC", 3));
    assert(lz10_decode(overlap, sizeof(overlap), output, sizeof(output)) == 6);
    assert(!memcmp(output, "AAAAAA", 6));
    assert(lz10_decode(literal, sizeof(literal) - 1, output, sizeof(output)) < 0);
    assert(lz10_decode(literal, sizeof(literal), output, 2) < 0);
    assert(lz10_decode(bad_distance, sizeof(bad_distance), output, sizeof(output)) < 0);
    assert(argc == 2);
    AssetPack pack;
    if (!pack_open(&pack, argv[1])) { fprintf(stderr, "%s\n", pack.error); return 1; }
    BankCache cache = {.id = -1};
    uint16_t *pixels = malloc(256000 * 2);
    uint16_t *screen = malloc(256 * 192 * 2);
    for (unsigned i = 0; i < pack.header.frames; i++) {
        if (!pack_decode(&pack, &cache, i, pixels, 256000)) {
            fprintf(stderr, "%u %s: %s\n", i, pack.frames[i].name, pack.error);
            return 1;
        }
        uint32_t screen_hash;
        assert(pack_draw(&pack, &cache, i, screen, &screen_hash));
        const PackFrame *f = &pack.frames[i];
        for (int y = 0; y < 192; y++) {
            for (int x = 0; x < 256; x++) {
                int sx = x - f->x, sy = y - f->y;
                uint16_t expected = sx >= 0 && sy >= 0 && sx < f->width && sy < f->height ? pixels[sy * f->width + sx] : 0;
                assert(screen[y * 256 + x] == expected);
            }
        }
    }
    printf("PASS C decoder: %u frame hashes, %u dictionaries, LZ10 bounds/overlap/truncation\n", pack.header.frames, pack.header.banks);
    FILE *file = fopen(argv[1], "rb");
    assert(file);
    assert(!fseek(file, 0, SEEK_END));
    long bytes = ftell(file);
    assert(bytes > 0 && !fseek(file, 0, SEEK_SET));
    uint8_t *memory = malloc(bytes);
    assert(memory && fread(memory, 1, bytes, file) == (size_t)bytes);
    fclose(file);
    AssetPack embedded;
    assert(pack_open_memory(&embedded, memory, bytes));
    assert(embedded.banks == (const PackBank *)(memory + sizeof(PackHeader)));
    bank_cache_clear(&cache);
    for (unsigned i = 0; i < embedded.header.frames; i++) {
        if (!pack_decode(&embedded, &cache, i, pixels, 256000)) {
            fprintf(stderr, "Embedded %u %s: %s\n", i, embedded.frames[i].name, embedded.error);
            return 1;
        }
    }
    AssetPack invalid;
    assert(!pack_open_memory(&invalid, memory, sizeof(PackHeader) - 1));
    pack_close(&invalid);
    assert(!pack_open_memory(&invalid, memory + 1, bytes - 1));
    pack_close(&invalid);
    printf("PASS embedded memory decoder: %u frame hashes; zero-copy index; truncated/unaligned input rejected\n", embedded.header.frames);
    const PackFrame *shared_index = embedded.frames;
    assert(!pack_try_file(&embedded, "this-file-does-not-exist.zat"));
    assert(embedded.memory == memory && !embedded.file && embedded.frames == shared_index);
    assert(pack_try_file(&embedded, argv[1]));
    assert(embedded.memory == memory && embedded.file && embedded.frames == shared_index);
    bank_cache_clear(&cache);
    for (unsigned i = 0; i < embedded.header.frames; i++)
        assert(pack_draw(&embedded, &cache, i, screen, NULL));
    pack_use_memory(&embedded);
    assert(!embedded.file && embedded.frames == shared_index);
    bank_cache_clear(&cache);
    assert(pack_draw(&embedded, &cache, 0, screen, NULL));
    printf("PASS shared-index file backend, all file-streamed frames, missing-file retention and return to RAM\n");
    bank_cache_clear(&cache); pack_close(&embedded); free(memory);
    printf("PASS streamed viewports equal full decodes for all frames\n");
    free(screen); free(pixels); bank_cache_clear(&cache); pack_close(&pack);
    return 0;
}
