// Native format/bitplane proof, linked against the demo's existing LZ10 decoder.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "asset_pack.h"

static void need(int ok,const char *why){if(!ok){fprintf(stderr,"FAIL: %s\n",why);exit(2);}}
static uint16_t u16(const uint8_t *p){return p[0]|(p[1]<<8);}
static uint32_t u32(const uint8_t *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static uint32_t crc_step(uint32_t crc,const uint8_t *p,size_t n){
    while(n--){crc^=*p++;for(int i=0;i<8;i++)crc=(crc>>1)^(0xedb88320u&-(int32_t)(crc&1));}return crc;
}
static void range(size_t total,uint32_t offset,uint32_t bytes){need(offset<=total&&bytes<=total-offset,"file range");}
static uint8_t *read_all(const char *name,size_t *size){
    FILE *f=fopen(name,"rb");need(f!=NULL,"open");need(!fseek(f,0,SEEK_END),"seek");long n=ftell(f);
    need(n>=0&&n<64000000,"input size");*size=(size_t)n;rewind(f);
    uint8_t *p=malloc(*size?*size:1);need(p!=NULL,"alloc");need(fread(p,1,*size,f)==*size,"read");fclose(f);return p;
}
typedef struct{uint32_t first,count,size;uint8_t *data;} DecodedPage;
static DecodedPage *pages(const uint8_t *data,size_t total,const uint32_t *h,unsigned count){
    need(count>0&&count<4096,"page count");range(total,h[10],count*28);
    DecodedPage *out=calloc(count,sizeof(*out));need(out!=NULL,"page alloc");
    for(unsigned i=0;i<count;i++){
        const uint8_t *d=data+h[10]+i*28;uint32_t codec=u32(d+8),at=u32(d+12),bytes=u32(d+16),raw=u32(d+20);
        need(raw>0&&raw<=32768,"page raw budget");range(total,at,bytes);need(at>=h[11],"payload location");
        out[i].first=u32(d);out[i].count=u32(d+4);out[i].size=raw;out[i].data=malloc(raw);need(out[i].data!=NULL,"page alloc");
        if(codec==0){need(bytes==raw,"raw page size");memcpy(out[i].data,data+at,raw);}
        else{need(codec==1,"page codec");need(lz10_decode(data+at,bytes,out[i].data,raw)==(int)raw,"LZ10 decode");}
        need(~crc_step(~0u,out[i].data,raw)==u32(d+24),"page CRC");
    }return out;
}
static unsigned utf8(uint32_t cp,uint8_t *out){
    need(cp<=0x10ffff&&!(cp>=0xd800&&cp<=0xdfff),"Unicode scalar");
    if(cp<128){out[0]=(uint8_t)cp;return 1;}
    if(cp<2048){out[0]=0xc0|(cp>>6);out[1]=0x80|(cp&63);return 2;}
    if(cp<65536){out[0]=0xe0|(cp>>12);out[1]=0x80|((cp>>6)&63);out[2]=0x80|(cp&63);return 3;}
    out[0]=0xf0|(cp>>18);out[1]=0x80|((cp>>12)&63);out[2]=0x80|((cp>>6)&63);out[3]=0x80|(cp&63);return 4;
}
static void text_pack(const uint8_t *data,size_t total,const uint32_t *h,const char *output){
    unsigned symbols=h[1],unique=h[2],records=h[3],count=h[4],mode=h[5],bits=h[6];
    need(symbols>0&&symbols<=65535&&unique>0&&unique<=65535&&records<1000000,"text counts");
    range(total,h[7],symbols*4);range(total,h[8],records*2);range(total,h[9],unique*8);
    need(mode>=1&&mode<=4&&bits>0&&bits<=16,"text codec");
    DecodedPage *p=pages(data,total,h,count);FILE *file=fopen(output,"wb");need(file!=NULL,"output");uint32_t crc=~0u;
    for(unsigned i=0;i<records;i++){
        unsigned id=u16(data+h[8]+i*2);need(id<unique,"string id");const uint8_t *d=data+h[9]+id*8;
        unsigned page=u16(d),at=u16(d+2),bytes=u16(d+4),chars=u16(d+6);need(page<count,"string page");range(p[page].size,at,bytes);
        const uint8_t *s=p[page].data+at;uint8_t *out=malloc((size_t)chars*4+1);need(out!=NULL,"string alloc");unsigned n=0,cursor=0;
        if(mode==4){
            unsigned actual=0;for(unsigned k=0;k<bytes;k++)if((s[k]&0xc0)!=0x80)actual++;
            need(actual==chars&&bytes<=chars*4,"UTF8 count");memcpy(out,s,bytes);n=bytes;
        }else{
            if(mode==1)need(bytes==chars*2,"u16 count");
            if(mode==3)need(bytes==(chars*bits+7)/8,"packed count");
            for(unsigned k=0;k<chars;k++){
                unsigned symbol=0;
                if(mode==1)symbol=u16(s+k*2);
                else if(mode==2){
                    need(cursor<bytes,"vbyte truncated");unsigned b=s[cursor++];
                    if(b<224)symbol=b;else{need(b<240&&cursor<bytes,"vbyte prefix");symbol=224+((b&15)<<8)+s[cursor++];}
                }else for(unsigned b=0;b<bits;b++){unsigned bit=k*bits+b;symbol=(symbol<<1)|((s[bit/8]>>(7-bit%8))&1);}
                need(symbol<symbols,"symbol id");n+=utf8(u32(data+h[7]+symbol*4),out+n);
            }
            if(mode==2)need(cursor==bytes,"vbyte trailing bytes");
        }
        uint8_t length[4]={(uint8_t)n,(uint8_t)(n>>8),(uint8_t)(n>>16),(uint8_t)(n>>24)};
        crc=crc_step(crc,length,4);crc=crc_step(crc,out,n);
        need(fwrite(length,1,4,file)==4&&fwrite(out,1,n,file)==n,"write record");free(out);
    }
    fclose(file);need(~crc==h[13],"record CRC");
    for(unsigned i=0;i<count;i++)free(p[i].data);
    free(p);
    printf("PASS native text: %u records, codec %u, all raw tags/Unicode retained\n",records,mode);
}
static void font_pack(const uint8_t *data,size_t total,const uint32_t *h,const char *commands,const char *output){
    unsigned symbols=h[1],width=h[2],height=h[3],count=h[7];
    need(symbols>0&&symbols<65536&&width>0&&width<=32&&height>0&&height<=64,"font metrics");
    range(total,h[8],symbols*4);range(total,h[9],symbols);unsigned stride=(width*height+7)/8;
    DecodedPage *p=pages(data,total,h,count);uint8_t *glyphs=calloc(symbols,stride);need(glyphs!=NULL,"glyph alloc");unsigned expected=0;
    for(unsigned i=0;i<count;i++){
        need(p[i].first==expected&&p[i].count>0&&p[i].count<=symbols-expected&&p[i].size==p[i].count*stride,"glyph page");
        memcpy(glyphs+expected*stride,p[i].data,p[i].size);expected+=p[i].count;
    }need(expected==symbols&&~crc_step(~0u,glyphs,symbols*stride)==h[13],"glyph CRC");
    size_t n;uint8_t *cmd=read_all(commands,&n);need(n%12==0,"draw commands");uint8_t tiles[32*24*32]={0};
    for(size_t i=0;i<n;i+=12){
        unsigned id=u32(cmd+i),color=u32(cmd+i+8);int x=(int16_t)u16(cmd+i+4),y=(int16_t)u16(cmd+i+6);
        need(id<symbols&&color>0&&color<16,"draw glyph/color");
        for(unsigned yy=0;yy<height;yy++)for(unsigned xx=0;xx<width;xx++){
            unsigned bit=yy*width+xx;if(!(glyphs[id*stride+bit/8]&(0x80>>(bit%8))))continue;
            int dx=x+(int)xx,dy=y+(int)yy;if(dx<0||dy<0||dx>=256||dy>=192)continue;
            unsigned at=((dy/8)*32+dx/8)*32+(dy%8)*4+(dx%8)/2,shift=(dx&1)*4;
            tiles[at]=(tiles[at]&~(15<<shift))|(color<<shift);
        }
    }
    FILE *f=fopen(output,"wb");need(f!=NULL,"tile output");need(fwrite(tiles,1,sizeof(tiles),f)==sizeof(tiles),"write tiles");fclose(f);
    free(cmd);free(glyphs);for(unsigned i=0;i<count;i++)free(p[i].data);free(p);
    printf("PASS native font: %ux%u, %u symbols, %zu commands, 24576B NDS4bpp tiles\n",width,height,symbols,n/12);
}
int main(int argc,char **argv){
    need(argc==3||argc==4,"usage: pack output OR font commands output");size_t bytes;uint8_t *data=read_all(argv[1],&bytes);
    need(bytes>=64,"header");uint32_t h[14];for(unsigned i=0;i<14;i++)h[i]=u32(data+8+i*4);
    need(h[0]==1&&h[12]==bytes,"version/length");
    if(!memcmp(data,"ZATTXT01",8)){need(argc==3,"text args");text_pack(data,bytes,h,argv[2]);}
    else{need(!memcmp(data,"ZATFNT01",8)&&argc==4,"font magic/args");font_pack(data,bytes,h,argv[2],argv[3]);}
    free(data);return 0;
}
