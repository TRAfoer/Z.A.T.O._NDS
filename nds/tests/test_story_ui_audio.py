"""Real menu, shell and SAV container route each UI input to one audio cue."""
from pathlib import Path
import shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nds/tools'))
import pack_story_menu as packer
HARNESS=r'''
#include "story_shell.h"
#include "audio_demo.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned cues,heard,last,master_sfx=4096,prepared;
static bool safe=true;
static uint16_t pixels[256*192],upper[256*192],lower[256*192];
static void need(int ok,const char*why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(2);}}
bool audio_ui_prepare(void){prepared++;return true;}
void audio_ui_play(AudioUiCue cue){cues++;if(master_sfx){heard++;last=(unsigned)cue;}}
void audio_ui_stop(void){}
void audio_demo_service(void){}
void audio_scene_pause(bool p){(void)p;}
void audio_scene_stop(bool music,uint32_t fade){(void)music;(void)fade;}
void audio_scene_volumes(unsigned music,unsigned sfx){(void)music;master_sfx=sfx;}
static bool start(void*c){(void)c;return true;}
static bool capture(void*c,StorySaveMeta*m,uint8_t cp[768],uint8_t thumb[3072]){
 (void)c;if(!safe)return false;memset(m,0,sizeof(*m));m->chapter=1;m->text_id=7;memset(cp,7,768);memset(thumb,0,3072);return true;
}
static bool restore(void*c,const uint8_t cp[768]){(void)c;(void)cp;return true;}
static const uint16_t*screen(void*c){(void)c;return pixels;}
static void resume(void*c){(void)c;}
static const StoryShellOps ops={.new_game=start,.capture=capture,.restore=restore,.resume=resume,.screen=screen,.lower_screen=screen};
static void key(unsigned down){story_shell_input((StoryMenuInput){.down=down});}
static void touch(unsigned x,unsigned y){story_shell_input((StoryMenuInput){.down=STORY_MENU_KEY_TOUCH,.touch=true,.x=x,.y=y});}
static void sound(unsigned previous,AudioUiCue want,const char*why){need(cues==previous+1&&last==(unsigned)want,why);}
int main(int argc,char**argv){
 need(argc==4&&story_menu_open(argv[1]),"menu open");need(story_save_open(argv[2],story_menu_identity()),"save open");
 story_shell_init(&ops,NULL);story_shell_title();cues=heard=0;
 if(!strcmp(argv[3],"navigation")){
  key(STORY_MENU_KEY_DOWN);sound(0,AUDIO_UI_MOVE,"actual selection change needs one navigation cue");
  key(0);need(story_shell_draw(0,upper,lower)&&story_shell_draw(1,upper,lower)&&cues==1,"idle and redraw must be silent");
  key(STORY_MENU_KEY_A);sound(1,AUDIO_UI_CONFIRM,"open load needs one confirm cue");
  key(STORY_MENU_KEY_B);sound(2,AUDIO_UI_BACK,"submenu return needs one back cue");need(g_story_shell_status.selection==1,"return lost parent selection");
  touch(30,42);sound(3,AUDIO_UI_CONFIRM,"touching same selected title item must confirm");
  need(prepared==1,"shell must preload UI exactly once");
 }else if(!strcmp(argv[3],"gallery")){
  story_save_globals()->unlocks[0]=~0u;story_save_globals()->unlocks[1]=15;
  key(STORY_MENU_KEY_DOWN);key(STORY_MENU_KEY_DOWN);key(STORY_MENU_KEY_A);unsigned before=cues;
  key(STORY_MENU_KEY_RIGHT);sound(before,AUDIO_UI_MOVE,"gallery direction action must navigate rather than confirm");
  before=cues;key(STORY_MENU_KEY_A);sound(before,AUDIO_UI_CONFIRM,"same gallery item must confirm on A");
  before=cues;touch(102,46);sound(before,AUDIO_UI_CONFIRM,"same gallery item must confirm on touch");
  key(STORY_MENU_KEY_UP);before=cues;key(STORY_MENU_KEY_UP);need(cues==before,"gallery edge must be silent");
 }else if(!strcmp(argv[3],"settings")){
  for(unsigned i=0;i<3;i++)key(STORY_MENU_KEY_DOWN);key(STORY_MENU_KEY_A);
  key(STORY_MENU_KEY_DOWN);key(STORY_MENU_KEY_DOWN);unsigned before=cues;
  key(STORY_MENU_KEY_RIGHT);need(cues==before,"clamped volume action must stay silent");
  for(unsigned i=0;i<9;i++)key(STORY_MENU_KEY_LEFT);before=heard;
  key(STORY_MENU_KEY_LEFT);need(master_sfx==0&&heard==before,"new zero SFX setting must mute this same input cue");
  before=cues;key(STORY_MENU_KEY_LEFT);need(cues==before,"zero-volume boundary must not trigger UI");
  unsigned audible_before=heard;key(STORY_MENU_KEY_RIGHT);need(master_sfx>0&&heard==audible_before+1,"unmute feedback must use updated gain");
 }else if(!strcmp(argv[3],"scroll")){
  for(unsigned i=0;i<4;i++)key(STORY_MENU_KEY_DOWN);key(STORY_MENU_KEY_A);unsigned before=cues;
  for(unsigned i=0;i<20;i++)key(STORY_MENU_KEY_UP);need(cues==before,"text scroll top boundary must stay silent");
  key(STORY_MENU_KEY_DOWN);sound(before,AUDIO_UI_MOVE,"real text row scroll should tick");
  for(unsigned i=0;i<700;i++)key(STORY_MENU_KEY_DOWN);before=cues;
  for(unsigned i=0;i<20;i++)key(STORY_MENU_KEY_DOWN);need(cues==before,"text scroll bottom boundary must stay silent");
 }else if(!strcmp(argv[3],"confirm_error_auto")){
  key(STORY_MENU_KEY_A);need(!story_shell_active(),"start game");safe=false;
  unsigned before=cues;story_shell_observe(1,7,true,0,0,1);need(cues==before,"automatic save observation must stay silent");
  story_shell_open(STORY_MENU_SAVE);before=cues;key(STORY_MENU_KEY_A);sound(before,AUDIO_UI_ERROR,"unsafe save produces only an error cue");
  key(STORY_MENU_KEY_B);safe=true;story_shell_open(STORY_MENU_GAME);
  for(unsigned i=0;i<5;i++)key(STORY_MENU_KEY_DOWN);before=cues;key(STORY_MENU_KEY_A);
  sound(before,AUDIO_UI_CONFIRM,"NONE action opening confirm dialog must still confirm");need(g_story_shell_status.mode==STORY_MENU_CONFIRM,"return-title confirmation");
  before=cues;key(STORY_MENU_KEY_B);sound(before,AUDIO_UI_BACK,"canceling confirmation must return once");
  need(g_story_shell_status.selection==5,"cancel lost previous cursor");
 }else need(0,"case");
 story_save_close();story_menu_close();puts("PASS real UI audio routing");return 0;
}
'''


class StoryUiAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='zato_ui_audio_');cls.folder=Path(cls.temp.name);cls.pack=cls.folder/'menu.bin'
        packer.build(packer.source.DEFAULT_GAME,cls.pack,cls.folder/'menu.json')
        source=cls.folder/'ui.c';source.write_text(HARNESS,encoding='utf8');cls.exe=cls.folder/'ui.exe'
        r=subprocess.run([shutil.which('gcc'),'-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-I'+str(ROOT/'nds/source'),str(source),str(ROOT/'nds/source/story_menu.c'),str(ROOT/'nds/source/story_shell.c'),str(ROOT/'nds/source/story_save.c'),'-o',str(cls.exe)],capture_output=True,text=True)
        if r.returncode:raise AssertionError(r.stdout+r.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def check(self,case):
        r=subprocess.run([str(self.exe),str(self.pack),str(self.folder/(case+'.sav')),case],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_navigation_confirm_back_touch_and_idle(self):self.check('navigation')
    def test_settings_boundaries_and_immediate_zero_volume(self):self.check('settings')
    def test_gallery_direction_and_same_item_confirmation_are_distinct(self):self.check('gallery')
    def test_continuous_scroll_sounds_only_when_position_changes(self):self.check('scroll')
    def test_error_confirm_cancel_and_automatic_save_quiet(self):self.check('confirm_error_auto')


if __name__=='__main__':unittest.main()
