#include "story_save_fat.h"
#include "story_save.h"
#include <fat.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>
bool story_save_fat_path(char*out,size_t size,int argc,char*const argv[])
{
    if(!out||!size)return false;
    const char*path=argc>0&&argv&&argv[0]?argv[0]:NULL;
    if(path&&(!strncmp(path,"fat:/",5)||!strncmp(path,"sd:/",4))&&!strstr(path,"..")&&!strchr(path,'\\')){
        size_t length=strlen(path);
        if(length>=4&&path[length-4]=='.'&&tolower((unsigned char)path[length-3])=='n'&&
           tolower((unsigned char)path[length-2])=='d'&&tolower((unsigned char)path[length-1])=='s'){
            if(length+1>size)return false;
            memcpy(out,path,length-3);memcpy(out+length-3,"sav",4);return true;
        }
    }
    const char*fallback="fat:/ZATO_Story_Chain.sav";
    if(strlen(fallback)+1>size)return false;
    strcpy(out,fallback);return true;
}
bool story_save_fat_open(int argc,char*const argv[],const uint8_t identity[32])
{
    char path[512];
    if(!story_save_fat_path(path,sizeof(path),argc,argv)||!fatInitDefault()){
        /* Preserve store's diagnostic contract for UI, without creating files. */
        story_save_open(NULL,identity);return false;
    }
    return story_save_open(path,identity);
}
