from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SONG = ROOT / "songs" / "不可解"
SRT = SONG / "lyrics" / "不可解.srt"
ANNOTATION = SONG / "lyrics" / "不可解.annotated.json"
TRANSLATIONS = SONG / "lyrics" / "中文.json"
TIMING = ROOT / "不可解_歌词精校" / "output" / "逐字时间.json"


def stamp(value: str) -> float:
    hh, mm, rest = value.replace(",", ".").split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(rest)


def parse_srt(path: Path) -> list[dict]:
    rows = []
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, end = [part.strip() for part in lines[1].split("-->", 1)]
        rows.append({"start": stamp(start), "end": stamp(end), "jp": "".join(lines[2:])})
    return rows


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


SILENT_RUBY = set("、。！？!?「」『』（）()…・：:；;")


def main() -> int:
    errors: list[str] = []
    srt = parse_srt(SRT)
    annotation = json.loads(ANNOTATION.read_text(encoding="utf-8-sig")).get("lines", [])
    translations = json.loads(TRANSLATIONS.read_text(encoding="utf-8-sig"))
    timing = json.loads(TIMING.read_text(encoding="utf-8-sig"))

    counts = {"SRT": len(srt), "annotation": len(annotation), "translation": len(translations), "timing": len(timing)}
    if len(set(counts.values())) != 1:
        errors.append(f"句数不一致: {counts}")

    for index, (srt_line, ann_line, timed_line) in enumerate(zip(srt, annotation, timing), 1):
        texts = [compact(srt_line["jp"]), compact(ann_line.get("jp", "")), compact(timed_line.get("jp", ""))]
        if len(set(texts)) != 1:
            errors.append(f"第 {index} 句日文不一致")
        ruby = [unit for phrase in ann_line.get("phrases", []) for unit in phrase.get("ruby", [])]
        ruby_chars = compact("".join(unit[0] for unit in ruby))
        timed_chars = compact("".join(char.get("char", "") for char in timed_line.get("chars", [])))
        if ruby_chars != timed_chars:
            errors.append(f"第 {index} 句 ruby 与逐字字符序列不一致")
        if any(
            len(unit) < 2
            or (not str(unit[1]).strip() and str(unit[0]) not in SILENT_RUBY)
            for unit in ruby
        ):
            errors.append(f"第 {index} 句存在空 ruby/罗马音")
        line_start = float(timed_line.get("start", srt_line["start"]))
        line_end = float(timed_line.get("end", srt_line["end"]))
        previous_start = line_start
        for char_index, char in enumerate(timed_line.get("chars", []), 1):
            start = float(char.get("start", -1))
            end = float(char.get("end", -1))
            if not line_start <= start <= end <= line_end:
                errors.append(f"第 {index} 句第 {char_index} 字超出句界")
            if start < previous_start - 1e-6:
                errors.append(f"第 {index} 句第 {char_index} 字时间倒序")
            previous_start = start

    if errors:
        print("校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"校验通过：{len(srt)} 句，文本、ruby、翻译和逐字时间一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
