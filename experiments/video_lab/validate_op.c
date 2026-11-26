// Host proof of the bundled BlocksDS RGB555 TJpgDec path, not a video player.
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <direct.h>
#else
#include <sys/stat.h>
#endif
#include "tjpgd.h"
typedef unsigned long long VideoU64;
#ifdef OP_NDS_PROBE
#include <nds.h>
#include <filesystem.h>
#endif

#define HEADER_BYTES 72
#define MAX_FRAMES 4096
#define MAX_JPEG 65536
typedef struct {
    unsigned char jpeg[MAX_JPEG];
    size_t length, position;
    uint16_t screen[256 * 192];
    unsigned painted;
} Device;
static uint32_t read_u32(const unsigned char *p)
{ return p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24; }
#ifdef OP_PROBE_TCM
__attribute__((section(".itcm"),target("arm"),long_call))
#endif
static size_t input(JDEC *decoder, uint8_t *destination, size_t count)
{
    Device *d = decoder->device;
    if (count > d->length - d->position) count = d->length - d->position;
    if (destination) memcpy(destination, d->jpeg + d->position, count);
    d->position += count;
    return count;
}
#ifdef OP_PROBE_TCM
__attribute__((section(".itcm"),target("arm"),long_call))
#endif
static int output(JDEC *decoder, void *pixels, JRECT *r)
{
    Device *d = decoder->device;
    if (r->left > r->right || r->top > r->bottom || r->right >= 256 || r->bottom >= 144) return 0;
    const uint16_t *source = pixels;
    for (unsigned y = r->top; y <= r->bottom; ++y) {
        for (unsigned x = r->left; x <= r->right; ++x) {
            uint16_t pixel = *source++;
            if (!(pixel & 0x8000)) return 0;
            d->screen[(y + 24) * 256 + x] = pixel;
            d->painted++;
        }
    }
    return 1;
}
#ifdef OP_NDS_PROBE
typedef struct {
    uint32_t magic,ready,cases,completed,errors,bus_clock,timer_divider;
    uint32_t total_decode_ticks,max_decode_ticks,total_read_ticks,max_read_ticks;
    uint32_t jpeg_buffer_bytes,work_buffer_bytes,screen_bytes,calibration_ticks;
} ProbeStatus;
typedef struct {
    uint32_t fps,quality,frame,pass,bytes,decode_ticks,read_ticks,work_bytes,painted;
} ProbeSample;
volatile ProbeStatus g_op_probe_status;
volatile ProbeSample g_op_probe_samples[64];
#ifdef OP_PROBE_TCM
static unsigned char probe_work[10240] __attribute__((section(".dtcm"),aligned(32)));
#define PROBE_WORK_BYTES sizeof(probe_work)
#else
#define PROBE_WORK_BYTES 32768u
#endif
static uint32_t ticks(void)
{
    uint16_t high,low,check;
    do { high=TIMER_DATA(1);low=TIMER_DATA(0);check=TIMER_DATA(1); } while(high!=check);
    return (uint32_t)high<<16|low;
}
int main(void)
{
    consoleDemoInit();lcdMainOnTop();videoSetMode(MODE_5_2D);
    vramSetBankA(VRAM_A_MAIN_BG_0x06000000);
    int bg=bgInit(3,BgType_Bmp16,BgSize_B16_256x256,0,0);
    g_op_probe_status.magic=0x42504f5a;
    g_op_probe_status.bus_clock=BUS_CLOCK;g_op_probe_status.timer_divider=64;
    g_op_probe_status.jpeg_buffer_bytes=MAX_JPEG;g_op_probe_status.work_buffer_bytes=PROBE_WORK_BYTES;
    g_op_probe_status.screen_bytes=256*192*2;
    if(!nitroFSInit(NULL)){g_op_probe_status.errors++;goto finished;}
    FILE *catalog=fopen("nitro:/probe/index.bin","rb");
    unsigned char header[16],records[32*16];
    if(!catalog||fread(header,1,16,catalog)!=16||memcmp(header,"ZOPPRB01",8)||read_u32(header+12)!=16){
        if(catalog)fclose(catalog);
        g_op_probe_status.errors++;goto finished;
    }
    unsigned count=read_u32(header+8);
    if(!count||count>32||fread(records,16,count,catalog)!=count){fclose(catalog);g_op_probe_status.errors++;goto finished;}
    fclose(catalog);g_op_probe_status.cases=count*2;
    Device *device=malloc(sizeof(*device));
#ifdef OP_PROBE_TCM
    void *work=probe_work;
#else
    void *work=malloc(PROBE_WORK_BYTES);
#endif
    if(!device||!work){g_op_probe_status.errors++;goto finished;}
    for(unsigned p=0;p<256*192;p++)device->screen[p]=0x8000;
    TIMER_CR(0)=TIMER_CR(1)=0;TIMER_DATA(0)=TIMER_DATA(1)=0;
    TIMER_CR(1)=TIMER_ENABLE|TIMER_CASCADE;TIMER_CR(0)=TIMER_ENABLE|TIMER_DIV_64;
    swiWaitForVBlank();uint32_t calibration_start=ticks();
    for(unsigned i=0;i<60;i++)swiWaitForVBlank();
    g_op_probe_status.calibration_ticks=ticks()-calibration_start;
    for(unsigned pass=0;pass<2;pass++)for(unsigned i=0;i<count;i++){
        const unsigned char *record=records+i*16;
        ProbeSample sample={.fps=read_u32(record),.quality=read_u32(record+4),.frame=read_u32(record+8),.pass=pass,.bytes=read_u32(record+12)};
        char path[64];snprintf(path,sizeof(path),"nitro:/probe/%03u.jpg",i);
        uint32_t started=ticks();FILE *file=fopen(path,"rb");
        if(!file||sample.bytes<4||sample.bytes>MAX_JPEG||fread(device->jpeg,1,sample.bytes,file)!=sample.bytes){
            if(file)fclose(file);
            g_op_probe_status.errors++;goto finished;
        }
        fclose(file);sample.read_ticks=ticks()-started;
        device->length=sample.bytes;device->position=device->painted=0;
        JDEC decoder;started=ticks();
        JRESULT result=jd_prepare(&decoder,input,work,PROBE_WORK_BYTES,device);
        if(result!=JDR_OK||decoder.width!=256||decoder.height!=144){g_op_probe_status.errors++;goto finished;}
        sample.work_bytes=PROBE_WORK_BYTES-decoder.sz_pool;
        result=jd_decomp(&decoder,output,0);sample.decode_ticks=ticks()-started;sample.painted=device->painted;
        if(result!=JDR_OK||sample.painted!=256*144){g_op_probe_status.errors++;goto finished;}
        g_op_probe_samples[pass*count+i]=sample;
        g_op_probe_status.total_decode_ticks+=sample.decode_ticks;
        g_op_probe_status.total_read_ticks+=sample.read_ticks;
        if(sample.decode_ticks>g_op_probe_status.max_decode_ticks)g_op_probe_status.max_decode_ticks=sample.decode_ticks;
        if(sample.read_ticks>g_op_probe_status.max_read_ticks)g_op_probe_status.max_read_ticks=sample.read_ticks;
        g_op_probe_status.completed++;
        DC_FlushRange(device->screen,sizeof(device->screen));
        swiWaitForVBlank();dmaCopy(device->screen,bgGetGfxPtr(bg),sizeof(device->screen));
        printf("\x1b[0;0HZATO OP JPEG PROBE\n%lu/%lu samples\n%lu fps q%lu frame%lu\nDecode %lu ticks\nRead %lu ticks\nNo audio / no player",
            (unsigned long)g_op_probe_status.completed,(unsigned long)g_op_probe_status.cases,
            (unsigned long)sample.fps,(unsigned long)sample.quality,(unsigned long)sample.frame,
            (unsigned long)sample.decode_ticks,(unsigned long)sample.read_ticks);
    }
finished:
    g_op_probe_status.ready=1;
    while(1)swiWaitForVBlank();
}
#else
int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    FILE *file = fopen(argv[1], "rb");
    if (!file || fseek(file, 0, SEEK_END)) return 3;
    long file_bytes = ftell(file);
    unsigned char header[HEADER_BYTES];
    rewind(file);
    if (fread(header, 1, sizeof(header), file) != sizeof(header) || memcmp(header, "ZATVID01", 8)) return 4;
    uint32_t fields[16];
    for (unsigned i = 0; i < 16; ++i) fields[i] = read_u32(header + 8 + 4*i);
    uint32_t count = fields[9], payload = fields[13], next = payload;
    if (fields[0] != 1 || fields[1] != 256 || fields[2] != 144 || fields[3] != 256 ||
        fields[4] != 192 || fields[5] != 0 || fields[6] != 24 || !fields[7] || fields[7] > 60 ||
        fields[8] != 1 || !count || count > MAX_FRAMES || fields[11] != 12 || fields[12] != HEADER_BYTES ||
        payload != HEADER_BYTES + count*12 || fields[14] != (uint32_t)file_bytes ||
        !fields[15] || fields[15] > MAX_JPEG || file_bytes < (long)payload) return 5;
    if (count != ((VideoU64)fields[10] * fields[7] + 999999) / 1000000) return 6;
    unsigned char *index = malloc(count * 12);
    Device *device = malloc(sizeof(*device));
    void *work = malloc(32768);
    if (!index || !device || !work || fread(index, 12, count, file) != count) return 7;
#ifdef _WIN32
    if (_mkdir(argv[2]) && errno != EEXIST) return 8;
#else
    if (mkdir(argv[2], 0700) && errno != EEXIST) return 8;
#endif
    unsigned work_max = 0, jpeg_max = 0;
    clock_t started = clock();
    for (unsigned i = 0; i < count; ++i) {
        const unsigned char *record = index + i*12;
        uint32_t offset = read_u32(record), length = read_u32(record+4), sample = read_u32(record+8);
        if (offset != next || length < 4 || length > MAX_JPEG || offset > (uint32_t)file_bytes ||
            length > (uint32_t)file_bytes - offset || sample != (VideoU64)i * 22050 / fields[7]) return 9;
        if (fseek(file, offset, SEEK_SET) || fread(device->jpeg, 1, length, file) != length) return 10;
        if (device->jpeg[0] != 0xff || device->jpeg[1] != 0xd8 || device->jpeg[length-2] != 0xff || device->jpeg[length-1] != 0xd9) return 11;
        device->length = length; device->position = device->painted = 0;
        for (unsigned p = 0; p < 256*192; ++p) device->screen[p] = 0x8000;
        JDEC decoder;
        JRESULT result = jd_prepare(&decoder, input, work, 32768, device);
        if (result != JDR_OK || decoder.width != 256 || decoder.height != 144) {
            fprintf(stderr, "prepare frame %u returned %u, %ux%u\n", i, result, decoder.width, decoder.height);
            return 12;
        }
        unsigned used = 32768 - decoder.sz_pool;
        if (used > work_max) work_max = used;
        result = jd_decomp(&decoder, output, 0);
        if (result != JDR_OK || device->painted != 256*144) {
            fprintf(stderr, "decode frame %u returned %u, painted %u\n", i, result, device->painted);
            return 13;
        }
        if (i == 0 || i == count/4 || i == count/2 || i == count*3/4 || i+1 == count) {
            char path[1024]; snprintf(path, sizeof(path), "%s/%05u.rgb555", argv[2], i);
            FILE *sample_file = fopen(path, "wb");
            if (!sample_file || fwrite(device->screen, sizeof(uint16_t), 256*192, sample_file) != 256*192) return 14;
            fclose(sample_file);
        }
        if (length > jpeg_max) jpeg_max = length;
        next += length;
    }
    if (next != (uint32_t)file_bytes || jpeg_max != fields[15]) return 15;
    printf("{\"frames\":%u,\"width\":256,\"height\":144,\"screen_height\":192,\"y\":24,\"max_jpeg_bytes\":%u,\"work_bytes_max\":%u,\"host_cpu_seconds\":%.6f}\n",
        (unsigned)count, jpeg_max, work_max, (double)(clock()-started)/CLOCKS_PER_SEC);
    free(index); free(device); free(work); fclose(file);
    return 0;
}
#endif
