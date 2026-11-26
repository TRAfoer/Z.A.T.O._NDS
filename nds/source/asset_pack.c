#include "asset_pack.h"
#include <stdlib.h>
#include <string.h>

_Static_assert(sizeof(PackHeader) == 40, "Pack header layout");
_Static_assert(sizeof(PackBank) == 16, "Bank layout");
_Static_assert(sizeof(PackFrame) == 196, "Frame layout");

__attribute__((weak, noinline)) void asset_io_yield(void) {}

uint32_t pixel_hash(const void *data, uint32_t bytes)
{
    const uint8_t *p = data;
    uint32_t hash = 2166136261u;
    while (bytes--) hash = (hash ^ *p++) * 16777619u;
    return hash;
}

int lz10_decode(const uint8_t *src, uint32_t bytes, uint8_t *dst, uint32_t capacity)
{
    if (bytes < 4 || src[0] != 0x10) return -1;
    uint32_t size = src[1] | (src[2] << 8) | (src[3] << 16);
    if (!size || size > capacity) return -1;
    uint32_t in = 4, out = 0;
    while (out < size) {
        if (in >= bytes) return -1;
        unsigned flags = src[in++];
        for (unsigned bit = 0x80; bit && out < size; bit >>= 1) {
            if (flags & bit) {
                if (in + 2 > bytes) return -1;
                unsigned token = (src[in] << 8) | src[in + 1];
                in += 2;
                unsigned count = (token >> 12) + 3, distance = (token & 4095) + 1;
                if (distance > out || count > size - out) return -1;
                while (count--) { dst[out] = dst[out - distance]; out++; }
            } else {
                if (in >= bytes) return -1;
                dst[out++] = src[in++];
            }
        }
    }
    return (int)out;
}

static bool fail(AssetPack *p, const char *message)
{
    snprintf(p->error, sizeof(p->error), "%s", message);
    return false;
}

static bool read_at(AssetPack *p, uint32_t offset, void *data, uint32_t size)
{
    if (offset > p->file_size || size > p->file_size - offset)
        return fail(p, "Asset file range invalid");
#ifndef ZATO_EMBEDDED
    if (p->file) {
        if (fseek(p->file, offset, SEEK_SET) || fread(data, 1, size, p->file) != size)
            return fail(p, "Asset file read failed");
        asset_io_yield();
        return true;
    }
#endif
    if (p->memory) {
        memcpy(data, p->memory + offset, size);
        return true;
    }
    return fail(p, "Asset data missing");
}

static bool read_index(AssetPack *p)
{
    if (!read_at(p, 0, &p->header, sizeof(p->header))) return false;
    PackHeader *h = &p->header;
    if (memcmp(h->magic, "ZATONDP1", 8) || h->version != 1 || h->frame_size != sizeof(PackFrame) ||
        !h->frames || h->frames > 4096 || !h->banks || h->banks > 1024 ||
        !h->stages || h->stages > h->frames || !h->actors || h->actors > h->frames ||
        h->default_stage >= h->stages || h->default_actor >= h->actors)
        return fail(p, "Asset header invalid");
    uint32_t at = sizeof(PackHeader);
    uint32_t index_bytes = h->banks * sizeof(PackBank) + h->frames * sizeof(PackFrame) + (h->stages + h->actors) * 2;
    if (at > p->file_size || index_bytes > p->file_size - at) return fail(p, "Truncated asset index");
    if (p->memory) {
        // The embedded package is aligned to 4 bytes. Tables remain in place:
        // cloning the 100 KiB index would waste scarce DS main memory.
        p->banks = (const PackBank *)(p->memory + at);
        at += h->banks * sizeof(PackBank);
        p->frames = (const PackFrame *)(p->memory + at);
        at += h->frames * sizeof(PackFrame);
        p->stages = (const uint16_t *)(p->memory + at);
        at += h->stages * 2;
        p->actors = (const uint16_t *)(p->memory + at);
    }
#ifndef ZATO_EMBEDDED
    else {
        p->banks = calloc(h->banks, sizeof(PackBank));
        p->frames = calloc(h->frames, sizeof(PackFrame));
        p->stages = calloc(h->stages, sizeof(uint16_t));
        p->actors = calloc(h->actors, sizeof(uint16_t));
        if (!p->banks || !p->frames || !p->stages || !p->actors) return fail(p, "Index allocation failed");
        if (!read_at(p, at, (void *)p->banks, h->banks * sizeof(PackBank))) return false;
        at += h->banks * sizeof(PackBank);
        if (!read_at(p, at, (void *)p->frames, h->frames * sizeof(PackFrame))) return false;
        at += h->frames * sizeof(PackFrame);
        if (!read_at(p, at, (void *)p->stages, h->stages * 2)) return false;
        at += h->stages * 2;
        if (!read_at(p, at, (void *)p->actors, h->actors * 2)) return false;
    }
#endif
    for (unsigned i = 0; i < h->stages; i++)
        if (p->stages[i] >= h->frames) return fail(p, "Stage index invalid");
    for (unsigned i = 0; i < h->actors; i++)
        if (p->actors[i] >= h->frames) return fail(p, "Actor index invalid");
    return true;
}

bool pack_open_memory(AssetPack *p, const void *memory, uint32_t bytes)
{
    memset(p, 0, sizeof(*p));
    if (!memory || ((uintptr_t)memory & 3) || bytes < sizeof(PackHeader))
        return fail(p, "Embedded asset address invalid");
    p->memory = memory;
    p->file_size = bytes;
    return read_index(p);
}

#ifndef ZATO_EMBEDDED
bool pack_open(AssetPack *p, const char *path)
{
    memset(p, 0, sizeof(*p));
    p->file = fopen(path, "rb");
    if (!p->file) return fail(p, "Cannot open assets.zat");
    if (fseek(p->file, 0, SEEK_END)) return fail(p, "Cannot seek assets.zat");
    long length = ftell(p->file);
    if (length < (long)sizeof(PackHeader)) return fail(p, "Asset file too small");
    p->file_size = (uint32_t)length;
    return read_index(p);
}

bool pack_try_file(AssetPack *p, const char *path)
{
    if (!p->memory || p->file) return fail(p, "Embedded index must be ready before file selection");
    FILE *file = fopen(path, "rb");
    if (!file) return fail(p, "NitroFS resource file unavailable");
    PackHeader header;
    bool ok = !fseek(file, 0, SEEK_END);
    long size = ok ? ftell(file) : -1;
    ok = size >= 0 && (uint32_t)size == p->file_size && !fseek(file, 0, SEEK_SET) &&
         fread(&header, 1, sizeof(header), file) == sizeof(header) &&
         !memcmp(&header, &p->header, sizeof(header));
    if (!ok) { fclose(file); return fail(p, "NitroFS resource header/size mismatch"); }
    // Keep the immutable index in the embedded package. Only compressed bank
    // and map reads change backend; no second 100 KiB index allocation.
    p->file = file;
    p->error[0] = 0;
    return true;
}

void pack_use_memory(AssetPack *p)
{
    if (p->file) fclose(p->file);
    p->file = NULL;
    p->error[0] = 0;
}
#endif

void pack_close(AssetPack *p)
{
#ifndef ZATO_EMBEDDED
    if (p->file) fclose(p->file);
#endif
    if (!p->memory) {
        free((void *)p->banks); free((void *)p->frames);
        free((void *)p->stages); free((void *)p->actors);
    }
    memset(p, 0, sizeof(*p));
}

void bank_cache_clear(BankCache *c)
{
    free(c->data);
    c->data = NULL; c->size = 0; c->id = -1;
}

#ifndef ZATO_EMBEDDED
typedef struct {
    AssetPack *pack;
    uint32_t offset, remaining, cursor, filled;
    uint8_t buffer[1024];
} CompressedReader;

static int compressed_byte(CompressedReader *r)
{
    if (r->cursor == r->filled) {
        if (!r->remaining) return -1;
        r->filled = r->remaining < sizeof(r->buffer) ? r->remaining : sizeof(r->buffer);
        if (!read_at(r->pack, r->offset, r->buffer, r->filled)) return -1;
        r->offset += r->filled;
        r->remaining -= r->filled;
        r->cursor = 0;
    }
    return r->buffer[r->cursor++];
}

static bool inflate_file(AssetPack *p, uint32_t offset, uint32_t packed, uint8_t *dst, uint32_t raw)
{
    CompressedReader reader = {.pack = p, .offset = offset, .remaining = packed};
    int header[4];
    for (unsigned i = 0; i < 4; i++) header[i] = compressed_byte(&reader);
    if (header[0] != 0x10 || header[1] < 0 || header[2] < 0 || header[3] < 0 ||
        (uint32_t)(header[1] | (header[2] << 8) | (header[3] << 16)) != raw)
        return fail(p, "File LZ10 header invalid");
    uint32_t out = 0;
    while (out < raw) {
        int flags = compressed_byte(&reader);
        if (flags < 0) return fail(p, "File LZ10 truncated flags");
        for (unsigned bit = 0x80; bit && out < raw; bit >>= 1) {
            int first = compressed_byte(&reader);
            if (first < 0) return fail(p, "File LZ10 truncated data");
            if (flags & bit) {
                int second = compressed_byte(&reader);
                if (second < 0) return fail(p, "File LZ10 truncated match");
                unsigned token = (first << 8) | second;
                unsigned count = (token >> 12) + 3, distance = (token & 4095) + 1;
                if (distance > out || count > raw - out) return fail(p, "File LZ10 match invalid");
                while (count--) { dst[out] = dst[out - distance]; out++; }
            } else dst[out++] = first;
        }
    }
    return true;
}
#endif

static bool inflate_at(AssetPack *p, uint32_t offset, uint32_t packed, uint8_t *dst, uint32_t raw)
{
    if (!packed || packed > 300000 || !raw || raw > 262144) return fail(p, "Compressed block size invalid");
    if (offset > p->file_size || packed > p->file_size - offset) return fail(p, "Compressed block range invalid");
#ifndef ZATO_EMBEDDED
    if (p->file) return inflate_file(p, offset, packed, dst, raw);
#endif
    if (p->memory) {
        // Decode from the embedded compressed bytes without a temporary copy.
        if (lz10_decode(p->memory + offset, packed, dst, raw) != (int)raw)
            return fail(p, "Embedded LZ10 decode failed");
        return true;
    }
    return fail(p, "Compressed asset data missing");
}

static bool cache_bank(AssetPack *p, BankCache *c, unsigned bank)
{
    if (bank >= p->header.banks) return fail(p, "Bank index invalid");
    if (c->id == (int)bank && c->data) return true;
    const PackBank *b = &p->banks[bank];
    if (!b->raw || b->raw > 262144 ||
        (b->tile_bytes != 128 && b->tile_bytes != 40 && b->tile_bytes != 192) || b->raw % b->tile_bytes)
        return fail(p, "Dictionary shape invalid");
    bank_cache_clear(c);
    c->data = malloc(b->raw);
    if (!c->data) return fail(p, "Dictionary allocation failed");
    if (!inflate_at(p, b->offset, b->packed, c->data, b->raw)) { bank_cache_clear(c); return false; }
    c->id = bank; c->size = b->raw;
    return true;
}

static uint16_t *frame_map(AssetPack *p, BankCache *c, unsigned id, uint32_t capacity)
{
    if (id >= p->header.frames) { fail(p, "Frame index invalid"); return NULL; }
    const PackFrame *f = &p->frames[id];
    uint32_t pixels = (uint32_t)f->width * f->height;
    if (!pixels || pixels > capacity || f->format > 2 || f->cols != (f->width + 7) / 8 ||
        f->rows != (f->height + 7) / 8 || f->map_raw != (uint32_t)f->cols * f->rows * 2 || f->map_raw > 32768)
        { fail(p, "Frame dimensions invalid"); return NULL; }
    if (!cache_bank(p, c, f->bank)) return NULL;
    const PackBank *bank = &p->banks[f->bank];
    const unsigned strides[3] = {128,40,192};
    if (bank->tile_bytes != strides[f->format]) { fail(p, "Frame bank format mismatch"); return NULL; }
    uint16_t *map = malloc(f->map_raw);
    if (!map) { fail(p, "Tile map allocation failed"); return NULL; }
    if (!inflate_at(p, f->map_offset, f->map_bytes, (uint8_t *)map, f->map_raw)) { free(map); return NULL; }
    for (unsigned i = 0; i < f->map_raw / 2; i++) {
        if (map[i] >= bank->raw / bank->tile_bytes) {
            free(map); fail(p, "Tile ID out of range"); return NULL;
        }
    }
    return map;
}

static inline uint16_t tile_pixel(const PackFrame *f, const uint8_t *tile, unsigned pos)
{
    if (f->format != 1) return tile[pos * 2] | (tile[pos * 2 + 1] << 8);
    unsigned color = (tile[pos / 2] >> ((pos & 1) * 4)) & 15;
    return tile[32 + pos / 8] & (1 << (pos & 7)) ? f->palette[color] | 0x8000 : 0;
}

static inline uint8_t tile_alpha(const PackFrame *f, const uint8_t *tile, unsigned pos, uint16_t value)
{
    return f->format == 2 ? tile[128 + pos] : (value & 0x8000 ? 255 : 0);
}

static inline bool valid_alpha_pixel(const PackFrame *f, uint16_t value, uint8_t alpha)
{
    return f->format != 2 || (alpha ? (value & 0x8000) != 0 : value == 0);
}

static inline uint32_t hash_pixel(uint32_t hash, uint16_t value, uint8_t alpha, unsigned format)
{
    hash = (hash ^ (value & 255)) * 16777619u;
    hash = (hash ^ (value >> 8)) * 16777619u;
    if (format == 2) hash = (hash ^ alpha) * 16777619u;
    return hash;
}

bool pack_decode_alpha(AssetPack *p, BankCache *c, unsigned id, uint16_t *output, uint8_t *alpha, uint32_t capacity)
{
    if (!output) return fail(p, "Decoded RGB output missing");
    uint16_t *map = frame_map(p, c, id, capacity);
    if (!map) return false;
    const PackFrame *f = &p->frames[id];
    const PackBank *bank = &p->banks[f->bank];
    uint32_t full_hash = 2166136261u;
    for (unsigned y = 0; y < f->height; y++) {
        for (unsigned tx = 0; tx < f->cols; tx++) {
            unsigned index = map[(y / 8) * f->cols + tx];
            const uint8_t *tile = c->data + index * bank->tile_bytes;
            for (unsigned x = 0; x < 8 && tx * 8 + x < f->width; x++) {
                unsigned pos = (y & 7) * 8 + x,at = y * f->width + tx * 8 + x;
                uint16_t value = tile_pixel(f, tile, pos);
                uint8_t opacity = tile_alpha(f, tile, pos, value);
                if (!valid_alpha_pixel(f, value, opacity)) { free(map); return fail(p, "Straight-alpha pixel invalid"); }
                output[at] = value;
                if (alpha) alpha[at] = opacity;
                full_hash = hash_pixel(full_hash, value, opacity, f->format);
            }
        }
    }
    free(map);
    if (full_hash != f->hash) return fail(p, "Decoded pixel hash mismatch");
    return true;
}

bool pack_decode(AssetPack *p, BankCache *c, unsigned id, uint16_t *output, uint32_t capacity)
{
    return pack_decode_alpha(p, c, id, output, NULL, capacity);
}

bool pack_draw(AssetPack *p, BankCache *c, unsigned id, uint16_t *screen, uint32_t *hash)
{
    uint16_t *map = frame_map(p, c, id, 256000);
    if (!map) return false;
    const PackFrame *f = &p->frames[id];
    const PackBank *bank = &p->banks[f->bank];
    memset(screen, 0, 256 * 192 * 2);
    uint32_t full_hash = 2166136261u;
    // Decode in image-row order directly into the viewport, checking the full
    // image hash as we go. No full-sized intermediate actor bitmap is needed.
    for (int y = 0; y < f->height; y++) {
        int dy = y + f->y;
        for (unsigned tx = 0; tx < f->cols; tx++) {
            const uint8_t *tile = c->data + map[(y / 8) * f->cols + tx] * bank->tile_bytes;
            for (unsigned local_x = 0; local_x < 8 && tx * 8 + local_x < f->width; local_x++) {
                unsigned pos = (y & 7) * 8 + local_x;
                uint16_t value = tile_pixel(f, tile, pos);
                uint8_t alpha = tile_alpha(f, tile, pos, value);
                if (!valid_alpha_pixel(f, value, alpha)) { free(map); return fail(p, "Straight-alpha pixel invalid"); }
                full_hash = hash_pixel(full_hash, value, alpha, f->format);
                if (f->format == 2) {
                    unsigned a = (alpha + 4u) >> 3;
                    unsigned r = ((value & 31) * a + 16) >> 5;
                    unsigned g = (((value >> 5) & 31) * a + 16) >> 5;
                    unsigned b = (((value >> 10) & 31) * a + 16) >> 5;
                    value = a ? (uint16_t)(0x8000 | r | (g << 5) | (b << 10)) : 0;
                }
                int dx = (int)(tx * 8 + local_x) + f->x;
                if (dx >= 0 && dx < 256 && dy >= 0 && dy < 192) screen[dy * 256 + dx] = value;
            }
        }
    }
    free(map);
    if (full_hash != f->hash) return fail(p, "Streamed image hash mismatch");
    if (hash) *hash = pixel_hash(screen, 256 * 192 * 2);
    return true;
}
