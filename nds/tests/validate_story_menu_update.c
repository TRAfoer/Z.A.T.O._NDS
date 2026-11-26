#include "story_menu.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
void audio_demo_service(void){}
static StoryMenuAction key(StoryMenuState*s,unsigned k){return story_menu_input(s,(StoryMenuInput){k,false,0,0});}
static StoryMenuAction touch(StoryMenuState*s,unsigned x,unsigned y){return story_menu_input(s,(StoryMenuInput){STORY_MENU_KEY_TOUCH,true,(uint16_t)x,(uint16_t)y});}
static void dump(const char*folder,const char*name,const void*p,size_t n){char path[1024];snprintf(path,sizeof(path),"%s/%s",folder,name);FILE*f=fopen(path,"wb");assert(f&&fwrite(p,1,n,f)==n&&!fclose(f));}
int main(int argc,char**argv){
 assert(argc==3&&story_menu_open(argv[1]));StoryMenuState s;StoryMenuAction a;
 uint16_t*upper=malloc(98304),*lower=malloc(98304),*background=malloc(98304);assert(upper&&lower&&background);
 for(unsigned i=0;i<49152;i++)background[i]=0x8000|(24<<10)|(16<<5)|8;
 story_menu_init(&s,STORY_MENU_GAME,true);s.game_preview=background;s.game_lower_preview=background;
 assert(story_menu_draw(&s,0,upper,lower));assert(!memcmp(upper,background,98304));assert(lower[90*256+200]==(0x8000|(6<<10)|(4<<5)|2));assert(background[0]==(0x8000|(24<<10)|(16<<5)|8));dump(argv[2],"game_dim_lower.rgb15",lower,98304);
 story_menu_init(&s,STORY_MENU_SETTINGS,true);key(&s,STORY_MENU_KEY_DOWN);key(&s,STORY_MENU_KEY_DOWN);key(&s,STORY_MENU_KEY_DOWN);assert(s.selection==3&&!s.chapter_skip);a=key(&s,STORY_MENU_KEY_RIGHT);assert(a.kind==STORY_MENU_SETTINGS_CHANGED&&a.value==3&&s.chapter_skip);key(&s,STORY_MENU_KEY_LEFT);assert(!s.chapter_skip);
 story_menu_init(&s,STORY_MENU_QUICK,true);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_OPEN_HISTORY);key(&s,STORY_MENU_KEY_RIGHT);a=key(&s,STORY_MENU_KEY_A);assert(a.kind==STORY_MENU_OPEN_HISTORY&&a.value==1);
 key(&s,STORY_MENU_KEY_RIGHT);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_TOGGLE_SKIP);key(&s,STORY_MENU_KEY_RIGHT);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_TOGGLE_AUTO);
 key(&s,STORY_MENU_KEY_RIGHT);assert(s.quick_page==1&&s.selection==0);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_TOGGLE_LANGUAGE);
 key(&s,STORY_MENU_KEY_LEFT);assert(s.quick_page==0&&s.selection==3);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_TOGGLE_AUTO);
 key(&s,STORY_MENU_KEY_RIGHT);assert(s.quick_page==1&&s.selection==0);
 key(&s,STORY_MENU_KEY_RIGHT);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_OPEN_SAVE);key(&s,STORY_MENU_KEY_RIGHT);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_QUICK_SAVE);key(&s,STORY_MENU_KEY_RIGHT);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_QUICK_LOAD);
 key(&s,STORY_MENU_KEY_RIGHT);assert(s.quick_page==2&&s.selection==0);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_OPEN_SETTINGS);
 key(&s,STORY_MENU_KEY_LEFT);assert(s.quick_page==1&&s.selection==3);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_QUICK_LOAD);
 key(&s,STORY_MENU_KEY_RIGHT);key(&s,STORY_MENU_KEY_RIGHT);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_OPEN_DEBUG);
 key(&s,STORY_MENU_KEY_RIGHT);assert(s.quick_page==0&&s.selection==0);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_OPEN_HISTORY);
 key(&s,STORY_MENU_KEY_LEFT);assert(s.quick_page==2&&s.selection==1);assert(key(&s,STORY_MENU_KEY_A).kind==STORY_MENU_OPEN_DEBUG);
 assert(key(&s,STORY_MENU_KEY_SELECT).kind==STORY_MENU_CLOSE);assert(key(&s,STORY_MENU_KEY_B).kind==STORY_MENU_CLOSE);assert(key(&s,STORY_MENU_KEY_Y|STORY_MENU_KEY_A).kind==STORY_MENU_CLOSE);assert(touch(&s,20,100).kind==STORY_MENU_CLOSE);assert(touch(&s,252,170).kind==STORY_MENU_CLOSE);
 assert(touch(&s,72,167).kind==STORY_MENU_OPEN_DEBUG);assert(touch(&s,232,167).kind==STORY_MENU_NONE);touch(&s,20,185);assert(s.quick_page==1);touch(&s,230,185);assert(s.quick_page==2);assert(touch(&s,128,185).kind==STORY_MENU_NONE);
 assert(STORY_MENU_QUICK_Y==156&&STORY_MENU_QUICK_COLLAPSED_Y==174);
 assert(story_menu_bar_hit(4,174)&&story_menu_bar_hit(251,191)&&!story_menu_bar_hit(252,180)&&!story_menu_bar_hit(4,173));
 assert(touch(&s,72,155).kind==STORY_MENU_CLOSE);assert(touch(&s,72,156).kind==STORY_MENU_OPEN_DEBUG);assert(touch(&s,128,174).kind==STORY_MENU_NONE);
 story_menu_init(&s,STORY_MENU_QUICK,true);for(unsigned i=0;i<49152;i++)lower[i]=0x9abc;assert(story_menu_draw_bar(&s,true,lower));for(unsigned i=0;i<156*256;i++)assert(lower[i]==0x9abc);for(unsigned y=156;y<192;y++){assert(lower[y*256]==0x9abc&&lower[y*256+255]==0x9abc);}for(unsigned x=4;x<252;x++){assert(lower[156*256+x]==0xffff&&lower[174*256+x]==0xffff&&lower[191*256+x]==0xffff);}dump(argv[2],"quick_bar_12.rgb15",lower,98304);
 for(unsigned i=0;i<49152;i++)lower[i]=0x9abc;
 assert(story_menu_draw_bar(NULL,false,lower));for(unsigned i=0;i<174*256;i++)assert(lower[i]==0x9abc);for(unsigned x=4;x<252;x++){assert(lower[174*256+x]==0xffff&&lower[191*256+x]==0xffff);}dump(argv[2],"collapsed_bar_12.rgb15",lower,98304);
 char path[1024];snprintf(path,sizeof(path),"%s/text_pages.txt",argv[2]);FILE*f=fopen(path,"w");assert(f);
 for(unsigned mode=STORY_MENU_CREDITS;mode<=STORY_MENU_CONTROLS;mode++){
  story_menu_init(&s,(StoryMenuMode)mode,false);unsigned title=story_menu_text_page_count(&s);s.game_preview=background;unsigned game=story_menu_text_page_count(&s);fprintf(f,"%u %u %u\n",mode,title,game);assert(title&&game>=title);
  key(&s,STORY_MENU_KEY_DOWN);assert(s.scroll==1);key(&s,STORY_MENU_KEY_UP);assert(s.scroll==0);
  for(unsigned i=0;i<500;i++)key(&s,STORY_MENU_KEY_DOWN);
  unsigned end=s.scroll;key(&s,STORY_MENU_KEY_DOWN);assert(s.scroll==end);key(&s,STORY_MENU_KEY_UP);assert(s.scroll+1==end);
  assert(story_menu_draw(&s,0,upper,lower));assert(!memcmp(upper,background,98304));
  s.scroll=end;assert(story_menu_draw(&s,0,upper,lower));char name[64];snprintf(name,sizeof(name),"text_counter_%u_game.rgb15",mode);dump(argv[2],name,lower,98304);
  s.game_preview=NULL;s.scroll=0;for(unsigned i=0;i<500;i++)key(&s,STORY_MENU_KEY_DOWN);assert(story_menu_draw(&s,0,upper,lower));snprintf(name,sizeof(name),"text_counter_%u_title.rgb15",mode);dump(argv[2],name,lower,98304);
 }assert(!fclose(f));
 free(upper);free(lower);free(background);story_menu_close();puts("PASS quick12/dim/credits/text-page update");return 0;
}
