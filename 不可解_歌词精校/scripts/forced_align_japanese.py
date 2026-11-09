from __future__ import annotations

import json
import re
from pathlib import Path

import torch
import soundfile as sf
import torchaudio
from pykakasi import kakasi


ROOT = Path(__file__).resolve().parents[1]
WAV = ROOT / "input" / "不可解.wav"
SRT = ROOT / "input" / "不可解.srt"
ANNOTATION = ROOT.parent / "songs" / "不可解" / "lyrics" / "不可解.annotated.json"
OUT = ROOT / "output" / "逐字时间.json"


def stamp(value):
    hh, mm, rest = value.split(":")
    ss, ms = re.split(r"[,.]", rest)
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_srt(path):
    rows = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            start, end = [x.strip() for x in lines[1].split("-->", 1)]
            rows.append({"start": stamp(start), "end": stamp(end), "jp": "".join(lines[2:])})
    return rows


def main():
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model().eval()
    labels = bundle.get_labels()
    label_ids = {label: index for index, label in enumerate(labels)}
    samples, sample_rate = sf.read(str(WAV), dtype="float32", always_2d=True)
    waveform = torch.from_numpy(samples.mean(axis=1)).unsqueeze(0)
    if sample_rate != bundle.sample_rate:
        waveform = torchaudio.functional.resample(waveform, sample_rate, bundle.sample_rate)
    sample_rate = bundle.sample_rate
    rows = parse_srt(SRT)
    annotated = json.loads(ANNOTATION.read_text(encoding="utf-8-sig"))
    annotated_lines = annotated.get("lines", [])
    output = []

    with torch.inference_mode():
        for row in rows:
            source_line = annotated_lines[len(output)] if len(output) < len(annotated_lines) else {}
            ruby_chars = [
                (char, reading)
                for phrase in source_line.get("phrases", [])
                for char, reading in phrase.get("ruby", [])
            ]
            chars = [char for char, _ in ruby_chars] or list(row["jp"].replace(" ", ""))
            roman_chars = []
            char_map = []
            for index, (_, reading) in enumerate(ruby_chars or [(char, "") for char in chars]):
                roman = re.sub(r"[^a-z']", "", reading.lower())
                for token in roman:
                    if token in label_ids:
                        roman_chars.append(token)
                        char_map.append(index)
            pad = 0.35
            begin = max(0.0, row["start"] - pad)
            finish = min(waveform.shape[-1] / sample_rate, row["end"] + pad)
            clip = waveform[:, round(begin * sample_rate):round(finish * sample_rate)]
            emissions, _ = model(clip)
            log_probs = emissions.log_softmax(dim=-1)
            target = torch.tensor([[label_ids[token] for token in roman_chars]], dtype=torch.int64)
            aligned, scores = torchaudio.functional.forced_align(log_probs, target)
            spans = torchaudio.functional.merge_tokens(aligned[0], scores[0], blank=0)
            char_times = [{"char": char, "start": None, "end": None} for char in chars]
            frame_step = clip.shape[-1] / sample_rate / log_probs.shape[1]
            token_cursor = 0
            for token_span in spans:
                token_id = int(token_span.token)
                while token_cursor < len(roman_chars) and label_ids[roman_chars[token_cursor]] != token_id:
                    token_cursor += 1
                if token_cursor >= len(char_map):
                    continue
                char_index = char_map[token_cursor]
                token_cursor += 1
                start = begin + token_span.start * frame_step
                end = begin + token_span.end * frame_step
                item = char_times[char_index]
                item["start"] = start if item["start"] is None else min(item["start"], start)
                item["end"] = end if item["end"] is None else max(item["end"], end)
            cursor = row["start"]
            fallback = (row["end"] - row["start"]) / max(1, len(chars))
            for item in char_times:
                if item["start"] is None:
                    item["start"] = cursor
                    item["end"] = cursor + fallback
                item["start"] = max(row["start"], min(row["end"], item["start"]))
                item["end"] = max(item["start"], min(row["end"], item["end"]))
                cursor = max(cursor, item["end"])
            output.append({"jp": row["jp"], "start": row["start"], "end": row["end"], "chars": char_times})

    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE={OUT} lines={len(output)}")


if __name__ == "__main__":
    main()
