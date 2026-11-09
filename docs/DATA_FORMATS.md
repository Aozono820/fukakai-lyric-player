# 数据格式

## song.json

`song.json` 是歌曲入口。相对路径以歌曲目录为基准。

```json
{
  "title": "标题",
  "display_title": "显示标题",
  "artist": "艺术家",
  "enabled": true,
  "audio": "audio/source.flac",
  "playback_audio": "audio/playback.wav",
  "lyrics": "lyrics/song.srt",
  "annotation": "lyrics/song.annotated.json",
  "readings": "lyrics/readings.json",
  "ruby": "lyrics/ruby.json",
  "translations": "lyrics/中文.json",
  "character_timing": "../../alignment/output/逐字时间.json"
}
```

## SRT

SRT 保存句级时间和用于分块的日文。词组之间可使用空格，播放器显示时会把词组做成独立卡片。

```srt
1
00:00:10,000 --> 00:00:13,200
词组一 词组二
```

## readings.json

保存短语级人工读音覆盖。它适合纠正自动注音无法判断的固定表达。

## ruby.json

保存精确到字符的显示读音。每个词组对应一组 `[日文单元, 罗马音]`。除拗音等合理组合外，应尽量保持一个显示字符对应一个 ruby 单元。

## annotation

annotation 是播放器读取的缓存，包含句子、词组和逐字符 ruby。修改 SRT/readings/ruby 后需要重建。

## 中文.json

JSON 数组，顺序和句数必须与 SRT 一致。

## 逐字时间.json

```json
[
  {
    "jp": "词组一 词组二",
    "start": 10.0,
    "end": 13.2,
    "chars": [
      {"char": "字", "start": 10.1, "end": 10.4}
    ]
  }
]
```

每个字符必须满足：

```text
句首 <= start <= end <= 句尾
```

字符顺序必须与 annotation 中所有 ruby 单元展开后的日文序列一致。
