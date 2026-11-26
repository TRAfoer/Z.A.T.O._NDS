BLOCKSDS ?= /opt/wonderful/thirdparty/blocksds/core
RESOURCE_MODE ?= nitrofs
ifeq ($(RESOURCE_MODE),nitrofs)
NAME := zato_asset_demo_nitrofs
NITROFSDIR := nitrofs
DEFINES := -DZATO_NITROFS=1
else ifeq ($(RESOURCE_MODE),adaptive)
NAME := zato_asset_demo_adaptive
NITROFSDIR := nitrofs
BINDIRS := embedded
DEFINES := -DZATO_ADAPTIVE=1
else ifeq ($(RESOURCE_MODE),memory)
NAME := zato_asset_demo_r4
BINDIRS := embedded
DEFINES := -DZATO_EMBEDDED=1
else
$(error RESOURCE_MODE must be nitrofs, memory, or adaptive)
endif
GAME_TITLE := ZATO Asset Lab
GAME_SUBTITLE := Two layers / 2x2 actors
GAME_AUTHOR := ZATO NDS
GAME_ICON := icon/steam.gif
ifeq ($(SCENE_DEMO),1)
ifneq ($(RESOURCE_MODE),nitrofs)
$(error The scene demo uses NitroFS; RESOURCE_MODE must be nitrofs)
endif
NAME := zato_ep2d3_scene_nitrofs
DEFINES += -DZATO_SCENE_DEMO=1
GAME_TITLE := ZATO EP2D3
GAME_SUBTITLE := Scene / Audio Test
endif
SOURCEDIRS := source
INCLUDEDIRS := source
LIBS := -lnds9 -lmm9
ifeq ($(STORY_DEMO),1)
ifneq ($(RESOURCE_MODE),nitrofs)
$(error The story chain uses NitroFS)
endif
ifeq ($(SCENE_DEMO),1)
$(error Select either STORY_DEMO or SCENE_DEMO)
endif
NAME := zato_story_chain_nitrofs
NITROFSDIR := storyfs
DEFINES += -DZATO_STORY_DEMO=1
GAME_TITLE := ZATO Story Chain
GAME_SUBTITLE := Mainline / Native NDS
LIBS += -lm
ifneq ($(STORY_OP),0)
DEFINES += -DZATO_STORY_VIDEO=1
endif
endif
LIBDIRS := $(BLOCKSDS)/libs/maxmod
CFLAGS := -std=gnu11 -Wextra
include $(BLOCKSDS)/sys/default_makefiles/rom_arm9/Makefile

# A pure DS/PassMe header is accepted by older homebrew loaders.
NDSTOOL_ARGS += -h 0x200

$(ROM): $(GAME_ICON)

$(ROM): nitrofs/assets.zat
$(ROM): $(shell find nitrofs/audio -type f 2>/dev/null)
ifeq ($(SCENE_DEMO),1)
$(ROM): $(shell find nitrofs/scene -type f 2>/dev/null)
endif
ifeq ($(STORY_DEMO),1)
$(ROM): $(shell find storyfs -type f 2>/dev/null)
endif
