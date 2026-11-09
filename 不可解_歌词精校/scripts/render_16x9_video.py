from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import imageio_ffmpeg
import pygame


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main


WIDTH = 1920
HEIGHT = 1080
BACKGROUND = (14, 18, 30)
PREVIEW_CARD = (24, 32, 52)
CURRENT_CARD = (30, 39, 63)
TOP_RECT = pygame.Rect(0, 0, WIDTH, 230)
CURRENT_RECT = pygame.Rect(0, 250, WIDTH, 580)
BOTTOM_RECT = pygame.Rect(0, 850, WIDTH, 230)
TRANSITION_SECONDS = 0.46


def active_index(lines, position):
    for index, line in enumerate(lines):
        if line["start"] <= position < line["end"]:
            return index
    return None


def neighboring_indices(lines, position, active):
    if active is not None:
        return active - 1, active, active + 1
    before = [i for i, line in enumerate(lines) if line["end"] <= position]
    after = [i for i, line in enumerate(lines) if line["start"] >= position]
    return (before[-1] if before else None), None, (after[0] if after else None)


def item_at(lines, index):
    if index is None or not 0 <= index < len(lines):
        return None
    return lines[index]


def fitted_font(fonts, kind, text, preferred, maximum_width, minimum):
    size = preferred
    while size > minimum and fonts.get(kind, size).size(text)[0] > maximum_width:
        size -= 1
    return fonts.get(kind, size)


def draw_preview(surface, fonts, item, rect):
    pygame.draw.rect(surface, PREVIEW_CARD, rect)
    if item is None:
        return
    jp_font = fitted_font(fonts, "jp", item["jp"], 38, rect.width - 150, 22)
    zh_font = fitted_font(fonts, "ui", item.get("zh", ""), 26, rect.width - 150, 17)
    main.draw_text(
        surface,
        jp_font,
        item["jp"],
        (130, 146, 177),
        (rect.centerx, rect.centery - 30),
    )
    main.draw_text(
        surface,
        zh_font,
        item.get("zh", ""),
        (81, 105, 142),
        (rect.centerx, rect.centery + 30),
    )


def draw_current_mobile(surface, fonts, item, rect, position):
    """Large phone-readable karaoke layout with phrase-aware two-row wrapping."""
    phrases = item.get("phrases") or [
        {"jp": item["jp"], "romaji": item.get("romaji", "")}
    ]
    available = rect.width - 110
    jp_size, roman_size, block_height, gap = 82, 47, 172, 18

    def prepare(jp_px, roman_px):
        jp_font = fonts.get("jp", jp_px)
        roman_font = fonts.get("ui", roman_px)
        blocks = []
        for phrase in phrases:
            ruby = phrase.get("ruby") or [(phrase["jp"], phrase.get("romaji", ""))]
            cell_widths = [
                max(jp_font.size(char)[0], roman_font.size(reading)[0], 28) + 6
                for char, reading in ruby
            ]
            blocks.append((phrase, ruby, cell_widths, max(76, sum(cell_widths) + 22)))
        rows, row, row_width = [], [], 0
        for block in blocks:
            width = block[3]
            needed = width if not row else gap + width
            if row and row_width + needed > available:
                rows.append((row, row_width))
                row, row_width = [], 0
            row.append(block)
            row_width += width if len(row) == 1 else gap + width
        if row:
            rows.append((row, row_width))
        return jp_font, roman_font, rows

    jp_font, roman_font, rows = prepare(jp_size, roman_size)
    # The full song fits in two rows at the E size. This fallback also makes
    # future additions safe if a line contains an unusually wide phrase.
    while (len(rows) > 2 or any(width > available for _, width in rows)) and jp_size > 58:
        jp_size -= 2
        roman_size = max(34, round(jp_size * 47 / 82))
        block_height = max(126, round(jp_size * 172 / 82))
        jp_font, roman_font, rows = prepare(jp_size, roman_size)

    translation = item.get("zh", "").strip()
    total_height = len(rows) * block_height + max(0, len(rows) - 1) * gap
    lyric_area_bottom = rect.bottom - (88 if translation else 18)
    y = rect.y + max(18, (lyric_area_bottom - rect.y - total_height) // 2)
    timings = item.get("char_timings") or []
    total_cells = sum(len(block[1]) for row, _ in rows for block in row)
    if len(timings) != total_cells:
        duration = max(0.001, item["end"] - item["start"])
        timings = [
            {"start": item["start"] + duration * i / total_cells,
             "end": item["start"] + duration * (i + 1) / total_cells}
            for i in range(total_cells)
        ]
    cell_index = 0
    for row, row_width in rows:
        x = rect.centerx - row_width / 2
        for phrase, ruby, cell_widths, block_width in row:
            block_rect = pygame.Rect(round(x), y, block_width, block_height)
            pygame.draw.rect(surface, (47, 61, 91), block_rect, border_radius=18)
            cursor = block_rect.centerx - sum(cell_widths) / 2
            for (char, reading), cell_width in zip(ruby, cell_widths):
                center_x = round(cursor + cell_width / 2)
                timing = timings[cell_index]
                start = float(timing.get("start", item["start"]))
                end = float(timing.get("end", start))
                fill = max(0.0, min(1.0, (position - start) / max(0.001, end - start)))
                ry = block_rect.y + 7
                jy = ry + roman_font.get_height() + 1
                main.draw_text(surface, roman_font, reading, (119, 205, 255), (center_x, ry), "midtop")
                main.draw_text(surface, jp_font, char, (255, 255, 255), (center_x, jy), "midtop")
                if fill > 0:
                    old_clip = surface.get_clip()
                    clip = pygame.Rect(round(cursor), block_rect.y, max(1, round(cell_width * fill)), block_rect.height)
                    surface.set_clip(clip.clip(old_clip))
                    main.draw_text(surface, roman_font, reading, (255, 235, 126), (center_x, ry), "midtop")
                    main.draw_text(surface, jp_font, char, (255, 244, 174), (center_x, jy), "midtop")
                    surface.set_clip(old_clip)
                cursor += cell_width
                cell_index += 1
            x += block_width + gap
        y += block_height + gap
    if translation:
        zh_font = fitted_font(fonts, "ui", translation, 44, rect.width - 150, 26)
        main.draw_text(surface, zh_font, translation, (202, 214, 235), (rect.centerx, rect.bottom - 46))


def draw_board(lines, fonts, position, forced_active=None):
    surface = pygame.Surface((WIDTH, HEIGHT))
    surface.fill(BACKGROUND)
    active = active_index(lines, position) if forced_active is None else forced_active
    top, current, bottom = neighboring_indices(lines, position, active)
    draw_preview(surface, fonts, item_at(lines, top), TOP_RECT)
    pygame.draw.rect(surface, CURRENT_CARD, CURRENT_RECT)
    current_item = item_at(lines, current)
    if current_item is not None:
        draw_current_mobile(surface, fonts, current_item, CURRENT_RECT, position)
    draw_preview(surface, fonts, item_at(lines, bottom), BOTTOM_RECT)
    return surface


def render_frame(lines, fonts, position):
    active = active_index(lines, position)
    if active is None or active <= 0:
        return draw_board(lines, fonts, position)
    elapsed = position - lines[active]["start"]
    if not 0 <= elapsed < TRANSITION_SECONDS:
        return draw_board(lines, fonts, position)
    amount = elapsed / TRANSITION_SECONDS
    amount = amount * amount * (3.0 - 2.0 * amount)
    old = draw_board(lines, fonts, position, active - 1)
    new = draw_board(lines, fonts, position, active)
    # Upward motion plus a soft dissolve preserves the original lyric-wheel
    # feel without making phone viewers chase a full-screen jump.
    frame = pygame.Surface((WIDTH, HEIGHT))
    frame.fill(BACKGROUND)
    for rect, color in ((TOP_RECT, PREVIEW_CARD), (CURRENT_RECT, CURRENT_CARD), (BOTTOM_RECT, PREVIEW_CARD)):
        pygame.draw.rect(frame, color, rect)
        old_region = old.subsurface(rect).copy()
        new_region = new.subsurface(rect).copy()
        previous_clip = frame.get_clip()
        frame.set_clip(rect)
        # A full-slot vertical roll keeps every glyph crisp: the old slot
        # leaves through the top exactly as the new slot enters below it.
        frame.blit(old_region, (rect.x, rect.y - round(rect.height * amount)))
        frame.blit(new_region, (rect.x, rect.y + round(rect.height * (1.0 - amount))))
        frame.set_clip(previous_clip)
    return frame


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--crf", type=int, default=16)
    parser.add_argument("--preset", default="medium")
    args = parser.parse_args()

    song = main.read_song(ROOT / "songs" / "不可解")
    playback_audio = main.ensure_playback_audio(song)
    total_duration = main.audio_duration(playback_audio)
    start = max(0.0, min(float(args.start), total_duration))
    duration = total_duration - start if args.duration is None else min(
        float(args.duration), total_duration - start
    )
    lines = main.load_lyrics(song, total_duration)

    pygame.init()
    pygame.display.set_mode((1, 1))
    fonts = main.FontBook()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{WIDTH}x{HEIGHT}",
        "-framerate",
        str(args.fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        args.preset,
        "-crf",
        str(args.crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        str(args.output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    frame_count = round(duration * args.fps)
    started = time.monotonic()
    try:
        for frame_index in range(frame_count):
            position = start + frame_index / args.fps
            frame = render_frame(lines, fonts, position)
            process.stdin.write(pygame.image.tobytes(frame, "RGB"))
            if frame_index % (args.fps * 5) == 0:
                elapsed = time.monotonic() - started
                print(
                    f"frame={frame_index}/{frame_count} position={position:.2f}s elapsed={elapsed:.1f}s",
                    flush=True,
                )
    finally:
        if process.stdin:
            process.stdin.close()
        return_code = process.wait()
        pygame.quit()
    if return_code:
        raise SystemExit(return_code)
    print(json.dumps({"output": str(args.output), "frames": frame_count, "fps": args.fps}))


if __name__ == "__main__":
    main_cli()
