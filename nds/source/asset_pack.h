#ifndef ZATO_ASSET_PACK_H
#define ZATO_ASSET_PACK_H
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef struct {
    char magic[8];
    uint32_t version, frames, banks, stages, actors, default_stage, default_actor, frame_size;
} PackHeader;
typedef struct { uint32_t offset, packed, raw, tile_bytes; } PackBank;
typedef struct {
    uint32_t bank, map_offset, map_bytes, map_raw;
    uint16_t width, height, cols, rows;
    int16_t x, y;
    uint16_t category, format;
    uint32_t hash;
    uint16_t palette[16];
    char name[128];
} PackFrame;
typedef struct {
#ifndef ZATO_EMBEDDED
    FILE *file;
#endif
    const uint8_t *memory;
    uint32_t file_size;
    PackHeader header;
    const PackBank *banks;
    const PackFrame *frames;
    const uint16_t *stages, *actors;
    char error[96];
} AssetPack;
typedef struct { int id; uint8_t *data; uint32_t size; } BankCache;

int lz10_decode(const uint8_t *src, uint32_t bytes, uint8_t *dst, uint32_t capacity);
#ifndef ZATO_EMBEDDED
bool pack_open(AssetPack *pack, const char *path);
bool pack_try_file(AssetPack *pack, const char *path);
void pack_use_memory(AssetPack *pack);
#endif
bool pack_open_memory(AssetPack *pack, const void *memory, uint32_t bytes);
void pack_close(AssetPack *pack);
void bank_cache_clear(BankCache *cache);
/* Straight RGB15 words and optional alpha8 plane, in image-row order.
 * Format2 retains every alpha value and checks the RGBlo/RGBhi/A8 source
 * hash even when alpha is NULL. Formats0/1 keep their original RGB/hash;
 * their optional alpha plane is 0/255 from the existing high alpha bit.
 * capacity_pixels applies to both caller-owned planes; no image is cached. */
bool pack_decode_alpha(AssetPack *pack, BankCache *cache, unsigned frame_id,
                       uint16_t *rgb, uint8_t *alpha, uint32_t capacity_pixels);
bool pack_decode(AssetPack *pack, BankCache *cache, unsigned frame_id,
                 uint16_t *output, uint32_t capacity_pixels);
bool pack_draw(AssetPack *pack, BankCache *cache, unsigned frame_id,
               uint16_t *screen, uint32_t *hash);
uint32_t pixel_hash(const void *data, uint32_t bytes);
#endif
