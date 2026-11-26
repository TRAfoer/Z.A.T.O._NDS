# Z.A.T.O. NDS

《Z.A.T.O. // I Love the World and Everything In It》的独立Nintendo DS移植工程，使用C、BlocksDS / Wonderful和Python素材工具。

本仓库保存运行时、转换工具、预览程序、测试、构建脚本和构建配置。原游戏图片、音乐、剧情文件、字幕、字体、转换素材、ROM、存档、模拟器和历史构建均不纳入Git。原游戏与字体源文件按只读输入使用，工具输出写入本工作区。

仓库额外保留致谢署名图的数组头文件 `nds/source/credits_badge.h`；其本地生成脚本不纳入跟踪。

## 碎碎念

我其实玩 Z.A.T.O. 才过了一个月，但是我深深地感觉到这个游戏在剧情方面给予我的震撼，独特的画风，鲜明的剧情以及对于各种哲学方面的思考无时无刻令我着迷于其中，我反复游玩这个游戏，每次都能得到新的感悟。

恰逢我重启了我的nds，“把这个我最喜欢的神作放到我最喜欢的机器上”这个想法就顺水推舟了
得益于Codex的强大能力，我实际制作时间其实只有**两天**，大部分还是在调节音频和各种效果上，如果你看了源码里面的音频分包，就会知道游戏内的音乐的时长和循环起止其实是我一个一个手调的），当然如果这损失了原作的某些氛围感的话我在这里郑重道歉。我想让nds文件在包含原有素材的情况下严格小于64mb，所以做了点激进的优化，可能会出现一些兼容性问题。
另外，我调试了很久的人物立绘，包括为了2x2点对点缩放我使用了半身而不是原作的全身；转换后重建边缘白线来做到不让人物显得那么“双线性缩放”。以及对双屏的兼容巴拉巴拉的，我认为这应该是一个标准的nds游戏应该完成的部分，而且很明显做的还不够好。如果有观点、意见、建议、bug汇报，欢迎提issue和pr。

你应该现在放下手上所有的游戏，来感受Z.A.T.O.这款旷世神作（*仅个人观点->如有分歧你说了算
欢迎游玩！
asya好可爱！！asya好可爱！！asya好可爱！！asya好可爱！！asya好可爱！！asya好可爱！！asya好可爱！！asya好可爱！！asya好可爱！！asya好可爱！！asya好可爱！！



## 当前实现

- 上屏显示背景、角色、CG与演出；普通对白及操作菜单主要在下屏，沿用原作黑底细白框UI。
- 中文、英文交替排版并同步浮现；点阵字体按原生字号或整数倍显示；支持滚动文本、双屏读诗与历史回看。
- 标题、设置、日志、图鉴、致谢及手动/自动/快速存档；存档为ROM旁同名512KiB `.sav`，使用DLDI/FAT写入。
- NitroFS按需读取图片、立体声音乐与音效；包含上屏开场视频及独立UI提示音。
- 当前容量目标为完整ROM严格小于64MiB（67,108,864字节）。

主线构建入口为 `nds/build_story.ps1`。`nds/build.ps1`、`build_scene.ps1`及部分验证工具属于此前的素材/单场景试验。

## 源码结构

| 路径 | 内容 |
| --- | --- |
| `nds/source/` | ARM9运行时：剧情流、图像、点阵文字、菜单、音频、存档、视频 |
| `nds/tools/` | 素材转换、被动剧情解析、打包、预算和验证工具 |
| `nds/tests/` | Python回归、C验证程序与硬件接口测试桩 |
| `nds/config/audio_edits.json` | 已确认的音乐剪辑数值快照，不包含音频 |
| `experiments/asset_lab/` | 图像转换、双屏及演出预览 |
| `experiments/audio_lab/` | 波形试听、音乐循环标记编辑器 |
| `experiments/font_lab/` | 字体解析、字集筛选、文字与头像预览 |
| `experiments/story_lab/` | 剧情分析、音频事件、配对排版与色板研究 |
| `experiments/video_lab/` | 开场视频转换、字幕恢复及播放器验证 |

## 开发环境

当前构建脚本使用Windows PowerShell，默认工具链位于 `C:\msys64\opt\wonderful`。

- Python 3.12及Tkinter。
- BlocksDS / Wonderful，包含libnds、Maxmod、make与ndstool。
- FFmpeg，加入PATH；主机C测试还需要GCC。
- Python依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

预览程序可直接运行：

```powershell
python experiments/asset_lab/anim_preview.py --scale 2
python experiments/audio_lab/loop_editor.py
```

两者同样需要本地素材。根目录的 `.cmd` 是现有Windows快捷入口；音乐编辑器快捷入口默认查找 `experiments/audio_lab/.venv/`，也可使用上面的Python命令。

## 素材与构建

干净克隆需要另行提供以下输入，Git中不包含这些文件：

1. 已解包的原游戏。多数工具默认读取 `C:\Program Files (x86)\Steam\steamapps\common\Z.A.T.O.  I Love the World and Everything In It\game`；支持 `--game` 的入口可覆盖路径，其余入口需调整源码中的本地默认路径。
2. Fusion Pixel字体ZIP，以及开场使用的ISAS FangSong原生16px BDF。常用字体ZIP默认路径为 `C:\Downloads\Compressed\fusion-pixel.zip`。
3. 开场字幕/时间轴 `experiments/video_lab/op_text_cues.json`、开场视频转换输出及清单。这些包含原作或译文素材，不纳入仓库。

```powershell
python nds/tools/prepare_story_icon.py --source "路径/game/gui/icon.ico"
```

素材工具入口包括 `experiments/asset_lab/full_asset_audit.py`、`experiments/font_lab/build_font_lab.py`、`experiments/story_lab/analyze_story.py`、`nds/tools/pack_assets.py`、`pack_audio.py`、`pack_story_*.py`及 `experiments/video_lab/restore_op_text.py`。现有流水线仍依赖分阶段生成的清单和中间文件，首次从原素材重建需按对应脚本的输入准备；此仓库不附已转换素材，也不提供缺少这些输入时的一键ROM构建。

在本地已准备好转换结果后，主线构建命令为：

```powershell
.\nds\build_story.ps1 -UseExistingPacks
```

该命令读取 `nds/nitrofs/story/`中的剧情包、`nds/nitrofs/audio/`及相关清单，并通过 `experiments/video_lab/out/movie_manifest.json`取得开场视频。生成的完整ROM包含NitroFS资源，不需要玩家另放外置素材目录。构建脚本默认更新根目录 `ZATO_Story_Chain.nds`；使用 `-NoCopy -Variant candidate`可生成独立候选。

`nds/config/audio_edits.json`是已确认的剪辑快照。编辑器草稿不会自动应用，只有明确确认后才更新快照并转换。音乐保持立体声。

部分历史预算工具的 `--strict-budget`仍检查旧的64,000,000字节门槛；当前项目的64MiB目标需按成品实际字节数核对。

## 测试

不需要游戏素材的测试示例：

```powershell
python -m unittest discover -s nds/tests -p test_rom_budget.py -q
python -m unittest discover -s nds/tests -p test_music_edit.py -q
python -m unittest discover -s nds/tests -p test_story_save.py -q
```

完整回归命令为 `python -m unittest discover -s nds/tests -q`。完整套件中的图片、字体、对白、批准音频逐样比较等测试，需要本地原游戏、字体、转换包和试听基准，不能在只含源码的克隆中全部运行。

原生模拟器验证另需兼容的DeSmuME核心及 `nds_emulator`辅助工具；目前若干探针默认复用本机 `C:\DeskBox\Source\Nds_UI_Test\tools`，这些外部验证依赖不参与游戏运行，也不随仓库分发。验证用存档和FAT镜像另行准备，不纳入Git。

## 许可证

本项目自行编写的移植运行时、工具、构建脚本及测试代码采用 [MIT License](LICENSE)，可在保留版权和许可声明的前提下使用、修改及再发布，包括商业使用。许可证标准文本见 [Open Source Initiative](https://opensource.org/license/mit)。

项目MIT许可不覆盖原游戏的剧情、图像、音乐、译文、字体，亦不替代第三方代码、库及工具自身的许可证；这些内容的权利与许可仍归相应作者。`nds/source/credits_badge.h`内的署名图像与ChatGPT等标识也不包含在项目MIT授权内，纳入该文件不表示授予商标或标识使用权。

测试中的原文/译文摘录和原字形样本同样属于上述例外。`experiments/video_lab/test_restore_op_text.py`中用于像素对照的ISAS字形遵循其[ISAS (1988)原始许可](LICENSES/ISAS-1988.txt)，版权归中国科学院软件研究所；仓库仅附许可文本，不附字体包。

BlocksDS及其组成部分分别适用各自的许可证，制作发行包时需保留实际使用组件要求的声明，详见[BlocksDS许可说明](https://blocksds.skylyrac.net/docs/guides/licenses/)。

## 致谢

原作：Ferry // Nopanamaman. 2025。

中文汉化：[ParaTranz项目17445](https://paratranz.cn/projects/17445)。翻译校对：bilibili@100ol、bilibili@枫封峰。

NDS移植：bilibili@神币の商家、GPT-6.1 Sol。

字体与工具：[Fusion Pixel](https://github.com/TakWolf/fusion-pixel-font)、ISAS FangSong / 中国科学院软件研究所、[BlocksDS](https://blocksds.skylyrac.net/)、[rpatool](https://github.com/Shizmob/rpatool)、[Python](https://www.python.org/)。相关原素材、字体与工具的权利和许可分别归其作者；恢复本地素材并制作发行包时，保留相应署名及许可证。
