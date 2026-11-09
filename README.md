# 《不可解》日语逐字歌词播放器

一个使用 Pygame 实现的日语歌词播放器示例。它同时显示日文、逐字符罗马音和中文翻译，并按照人工复核后的逐字时间从左向右刷色。

演示视频：[BV1egWJ66EsQ](https://www.bilibili.com/video/BV1egWJ66EsQ)

本仓库只保留《不可解》相关的播放器示例、歌词结构、注音和时间数据，不包含音频或视频。你可以参考这里的文件结构和流程，让 AI 协助制作自己想要的歌曲。

## 功能

- 播放、暂停、时间轴跳转和音量控制
- 上一句、当前句、下一句的三层显示
- 换句时的纵向滚动过渡
- 日文字符、ruby/罗马音和中文翻译
- 逐字符真实时间刷色
- 长句自动按词组换成两行，必要时自适应缩小
- 句间空档、前奏、间奏和尾奏保持为空
- 生成 16:9、1080p、60 fps 的离线视频画面

## 快速开始

需要 Python 3.10 或更新版本。

```powershell
git clone <你的仓库地址>
cd fukakai-lyric-player
py -m pip install -r requirements.txt
```

把自己合法取得、与本项目时间轴匹配的《不可解》完整音频放到：

```text
songs/不可解/audio/source.flac
```

然后运行：

```powershell
py main.py --song "songs/不可解"
```

也可以直接运行：

```powershell
py songs/不可解/open.py
```

播放器首次启动时会自动生成 `audio/playback.wav`。它被 `.gitignore` 排除，不会误上传到 GitHub。

现有时间轴对应约 280.2 秒的录音室版本。若音源的前奏、间奏、语速、重复段或结尾不同，应重新制作时间轴，不能只靠整体平移解决。

## 数据是怎样连接的

一首歌分成三层，排查问题时不要混在一起：

1. `lyrics/不可解.srt`：句级时间和日文歌词，决定整句何时出现与切换。
2. `lyrics/不可解.annotated.json`、`readings.json`、`ruby.json`：分块、逐字符读音和罗马音。
3. `不可解_歌词精校/output/逐字时间.json`：每个字符的变色起止时间。

中文翻译位于 `lyrics/中文.json`。`song.json` 把这些文件和音频路径连接到播放器。

例如：

- 唱到下一句时仍显示上一句：先修 SRT 句界。
- 句子出现时间正确，但某个字变黄太晚：只修逐字时间。
- 罗马音读法或位置错误：修 `ruby.json`/`readings.json`，再重建 annotation。
- 后半段整体漂移：先检查音频版本和播放器时钟，不要直接拉伸全部字幕。

## 为自己的歌曲制作数据

完整流程见 [使用 AI 添加歌曲](docs/ADD_A_SONG_WITH_AI.md)。文件格式见 [数据格式](docs/DATA_FORMATS.md)，完成后按 [验收清单](docs/QA_CHECKLIST.md) 检查。

推荐顺序：

1. 确认音频版本。
2. 校对日文歌词和中文翻译。
3. 制作保留真实空档的句级 SRT。
4. 按词组分块，并把 ruby/罗马音拆到具体字符。
5. 使用 MMS_FA 生成逐字时间初稿。
6. 逐句试听，局部修正句界和字符时间。
7. 运行校验脚本，再在播放器中完整观看一遍。

运行本仓库的数据校验：

```powershell
py scripts/validate_song.py
```

## 重新生成《不可解》的逐字时间

安装额外依赖：

```powershell
py -m pip install -r requirements-align.txt
```

准备一个供模型分析的 WAV：

```text
不可解_歌词精校/input/不可解.wav
```

该文件可以是 16 kHz 单声道，只用于模型分析，不能作为播放器音频。然后运行：

```powershell
py 不可解_歌词精校/scripts/forced_align_japanese.py
```

模型会覆盖 `不可解_歌词精校/output/逐字时间.json`。不要因为一个字偏早或偏晚就重跑整首歌；局部修正通常更可靠。

## 生成 16:9 视频画面

```powershell
py 不可解_歌词精校/scripts/render_16x9_video.py `
  --fps 60 `
  --output renders/不可解_1080p60_silent.mp4
```

脚本生成无声画面。建议之后再把合法取得的原始音频单独混入，避免屏幕录音造成音质损失。生成文件不会被 Git 跟踪。

## 项目结构

```text
.
├─ main.py
├─ requirements.txt
├─ requirements-align.txt
├─ songs/不可解/
│  ├─ open.py
│  ├─ song.json
│  ├─ audio/README.md
│  └─ lyrics/
├─ 不可解_歌词精校/
│  ├─ input/                 # 不含音频
│  ├─ output/逐字时间.json
│  └─ scripts/
├─ scripts/validate_song.py
└─ docs/
```

## 版权与许可

程序代码和原创文档采用 MIT License。歌曲、歌词、录音、艺术家名称及其他第三方内容不包含在该许可中。

本仓库是非官方、非商业的技术演示，不提供歌曲音视频或下载方式。请仅使用自己有权使用的素材。详细声明见 [NOTICE.md](NOTICE.md)。如权利人要求更正或移除内容，可通过 GitHub Issue 联系维护者。
