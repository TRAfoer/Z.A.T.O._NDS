#include "story_menu.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned services;
void audio_demo_service(void){services++;}
static StoryMenuAction key(StoryMenuState*s,unsigned k){return story_menu_input(s,(StoryMenuInput){k,false,0,0});}
static StoryMenuAction touch(StoryMenuState*s,unsigned x,unsigned y){return story_menu_input(s,(StoryMenuInput){0,true,(uint16_t)x,(uint16_t)y});}
static void dump(const char*folder,const char*name,const void*p,size_t n){char path[1024];snprintf(path,sizeof(path),"%s/%s",folder,name);FILE*f=fopen(path,"wb");assert(f&&fwrite(p,1,n,f)==n&&!fclose(f));}
int main(int argc,char**argv){
 assert(argc==4||argc==5);
 if(!strcmp(argv[2],"reject")){assert(!story_menu_open(argv[1]));assert(!story_menu_resident_bytes());return 0;}
 assert(story_menu_open(argv[1]));assert(story_menu_resident_bytes()<262144);assert(services);
 const unsigned before=story_menu_read_calls();const uint8_t*identity=story_menu_identity();assert(identity&&identity[0]==0x9e);
 unsigned bit,frame;assert(story_menu_gallery_count()==43&&story_menu_gallery(0,&bit,&frame));assert(!story_menu_gallery(999,&bit,&frame));
 StoryMenuState s;StoryMenuAction a;story_menu_init(&s,STORY_MENU_TITLE,false);
 uint16_t*upper=malloc(98308),*lower=malloc(98308);assert(upper&&lower);upper[49152]=0x1234;lower[49152]=0x5678;
 assert(story_menu_draw(&s,0,upper,lower));dump(argv[3],"title_upper.rgb15",upper,98304);
 dump(argv[3],"title_lower.rgb15",lower,98304);
 assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_NEW_GAME);key(&s,STORY_MENU_KEY_UP);assert(s.selection==6);
 assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_NONE&&s.mode==STORY_MENU_CONFIRM&&s.selection==1);
 assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_CANCEL&&s.mode==STORY_MENU_TITLE);
 s.selection=0;assert(touch(&s,24,46).kind==STORY_MENU_OPEN_LOAD);assert(touch(&s,255,191).kind==STORY_MENU_NONE);
 story_menu_init(&s,STORY_MENU_LOAD,true);s.slots[0].slot_id=42;s.slots[0].status=STORY_MENU_SLOT_VALID;
 assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_NONE&&s.mode==STORY_MENU_CONFIRM);key(&s,STORY_MENU_KEY_LEFT);
 a=key(&s,STORY_MENU_KEY_A);assert(a.kind==STORY_MENU_LOAD_SLOT&&a.value==42&&s.mode==STORY_MENU_LOAD);
 assert(key(&s,STORY_MENU_KEY_Y).kind==STORY_MENU_NONE&&s.prompt==STORY_MENU_PROMPT_DELETE);assert(key(&s,STORY_MENU_KEY_B).kind==STORY_MENU_CANCEL);
 s.in_game=false;a=touch(&s,10,40);assert(a.kind==STORY_MENU_LOAD_SLOT&&a.value==42);
 s.slots[0].status=STORY_MENU_SLOT_EMPTY;assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_NONE);
 assert(story_menu_draw(&s,0,upper,lower));dump(argv[3],"empty_lower.rgb15",lower,98304);
 s.page=9;assert(key(&s,STORY_MENU_KEY_R).kind==STORY_MENU_PAGE_CHANGED&&s.page==10);s.page=16;key(&s,STORY_MENU_KEY_R);assert(s.page==16);
 s.page=1;key(&s,STORY_MENU_KEY_L);assert(s.page==17);key(&s,STORY_MENU_KEY_L);assert(s.page==0);key(&s,STORY_MENU_KEY_R);assert(s.page==17);
 touch(&s,9+3*19,25);assert(s.page==1);
 story_menu_init(&s,STORY_MENU_SAVE,true);s.page=0;assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_NONE&&s.mode==STORY_MENU_MESSAGE);key(&s,STORY_MENU_KEY_B);
 s.page=1;s.slots[0].slot_id=7;a=key(&s,STORY_MENU_KEY_A);assert(a.kind==STORY_MENU_SAVE_SLOT&&a.value==7);
 s.slots[0].status=STORY_MENU_SLOT_VALID;key(&s,STORY_MENU_KEY_A);assert(s.mode==STORY_MENU_CONFIRM&&s.prompt==STORY_MENU_PROMPT_OVERWRITE);key(&s,STORY_MENU_KEY_B);
 story_menu_init(&s,STORY_MENU_SETTINGS,true);key(&s,STORY_MENU_KEY_RIGHT);assert(s.bilingual);key(&s,STORY_MENU_KEY_DOWN);s.music_volume=95;key(&s,STORY_MENU_KEY_RIGHT);assert(s.music_volume==100);
 s.music_volume=2;key(&s,STORY_MENU_KEY_LEFT);assert(s.music_volume==0);key(&s,STORY_MENU_KEY_DOWN);touch(&s,232,116);assert(s.sfx_volume==100);
 story_menu_init(&s,STORY_MENU_GALLERY,false);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_NONE);s.unlock_low=~0u;s.unlock_high=15;
 a=key(&s,STORY_MENU_KEY_A);assert(a.kind==STORY_MENU_GALLERY_SELECT&&a.value==0);
 uint8_t thumb[3072];for(unsigned y=0;y<48;y++)for(unsigned x=0;x<64;x++)thumb[y*64+x]=(uint8_t)(((x*4)&0xe0)|((y*4)&0x1c)|((x+y)&3));
 if(argc==5){FILE*f=fopen(argv[4],"rb");assert(f&&fread(thumb,1,3072,f)==3072&&!fclose(f));}
 story_menu_init(&s,STORY_MENU_SAVE,true);s.slots[0]=(StoryMenuSlot){0,1,0,0,1,0,thumb};assert(story_menu_draw(&s,0,upper,lower));dump(argv[3],"save_upper.rgb15",upper,98304);
 dump(argv[3],"save_lower.rgb15",lower,98304);
 s.page=17;assert(story_menu_draw(&s,0,upper,lower));dump(argv[3],"quick_lower.rgb15",lower,98304);s.page=1;
 s.selection=3;s.slots[3]=(StoryMenuSlot){3,1,0,0,1,0,thumb};assert(story_menu_draw(&s,0,upper,lower));uint16_t*held=malloc(98304);assert(held);memcpy(held,upper,98304);
 story_menu_confirm(&s,STORY_MENU_PROMPT_OVERWRITE,STORY_MENU_SAVE_SLOT,3);assert(story_menu_draw(&s,0,upper,lower));assert(!memcmp(held,upper,98304));
 key(&s,STORY_MENU_KEY_B);assert(s.selection==3);free(held);
 story_menu_init(&s,STORY_MENU_CREDITS,true);for(unsigned i=0;i<500;i++)key(&s,STORY_MENU_KEY_DOWN);assert(s.scroll<100);key(&s,STORY_MENU_KEY_UP);assert(s.scroll<100);
 story_menu_init(&s,STORY_MENU_TITLE,false);assert(story_menu_version(&s,0)==story_menu_version(&s,3));assert(story_menu_version(&s,0)!=story_menu_version(&s,4));
 story_menu_init(&s,STORY_MENU_SAVE,true);unsigned version=story_menu_version(&s,0);assert(version==story_menu_version(&s,1234));s.selection=1;assert(version!=story_menu_version(&s,0));
 uint16_t*game=malloc(98304);assert(game);for(unsigned i=0;i<49152;i++)game[i]=(uint16_t)(0x8000|(i&32767));
 for(unsigned mode=STORY_MENU_GAME;mode<=STORY_MENU_CONTROLS;mode++)if(mode==STORY_MENU_GAME||mode==STORY_MENU_SETTINGS||mode==STORY_MENU_CREDITS||mode==STORY_MENU_CONTROLS){story_menu_init(&s,(StoryMenuMode)mode,true);s.game_preview=game;assert(story_menu_draw(&s,300,upper,lower));assert(!memcmp(game,upper,98304));assert(story_menu_version(&s,0)==story_menu_version(&s,1234));}
 free(game);
 for(unsigned mode=0;mode<=STORY_MENU_MESSAGE;mode++){story_menu_init(&s,(StoryMenuMode)mode,true);s.return_mode=STORY_MENU_SAVE;assert(story_menu_draw(&s,300,upper,lower));char name[40];snprintf(name,sizeof(name),"mode%u_upper.rgb15",mode);dump(argv[3],name,upper,98304);snprintf(name,sizeof(name),"mode%u_lower.rgb15",mode);dump(argv[3],name,lower,98304);}
 assert(upper[49152]==0x1234&&lower[49152]==0x5678);assert(story_menu_read_calls()==before);
 story_menu_close();assert(!story_menu_resident_bytes());free(upper);free(lower);puts("PASS menu navigation/pixels, no draw/input I/O");return 0;
}
