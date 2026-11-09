"""Phrase Player: a small Pygame lyric player.

The project entry point lives here.  Each song owns a folder under ``songs``
with its own ``open.py``, ``song.json``, audio and lyrics.  Shared playback and
rendering code stays in this file so song folders remain small and easy to
copy for the next song.
"""

import argparse
import ctypes
import json
import os
import re
import subprocess
import time
import wave
from pathlib import Path

import imageio_ffmpeg
import pygame
from pykakasi import kakasi


ROOT = Path(__file__).resolve().parent
SONGS_DIR = ROOT / "songs"

TIMING_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*$"
)


# Spaces define phrase groups.  These readings are hand-checked where kakasi's
# single-character fallback would be ambiguous.
OVERRIDES = {}


# Per-character ruby for the display layer.  A single reading is used for a
# whole block when the phrase is a fixed expression.
CONTEXTUAL = {}


def _stamp(parts):
    hour, minute, second, fraction = parts
    milliseconds = int(fraction.ljust(3, "0"))
    return int(hour) * 3600 + int(minute) * 60 + int(second) + milliseconds / 1000


def parse_srt(path):
    """Parse an SRT file without assuming one text line per cue."""
    raw = path.read_text(encoding="utf-8-sig")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    rows = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.splitlines()]
        timing_index = next(
            (index for index, line in enumerate(lines) if "-->" in line),
            None,
        )
        if timing_index is None:
            continue
        match = TIMING_RE.match(lines[timing_index])
        if match is None:
            continue
        start = _stamp(match.groups()[:4])
        end = _stamp(match.groups()[4:])
        text = " ".join(
            line for line in lines[timing_index + 1 :] if line
        ).strip()
        if text and end > start:
            rows.append({"start": start, "end": end, "jp": text})
    return rows


def _reading(conv, text):
    return "".join(
        item.get("hepburn") or item.get("orig") or ""
        for item in conv.convert(text)
    )


def annotate_rows(rows, extra_readings=None, extra_ruby=None):
    extra_readings = extra_readings or {}
    extra_ruby = extra_ruby or {}
    conv = kakasi()
    result = []
    for row in rows:
        phrase_data = []
        for group in row["jp"].split():
            parts = OVERRIDES.get(group)
            if parts is None and group in extra_readings:
                parts = [(group, extra_readings[group])]
            if parts is None:
                parts = [(group, _reading(conv, group))]
            phrase_data.extend(
                {"jp": jp, "romaji": romaji} for jp, romaji in parts
            )

        for phrase in phrase_data:
            if phrase["jp"] in extra_ruby:
                phrase["ruby"] = extra_ruby[phrase["jp"]]
                continue
            if phrase["jp"] in extra_readings:
                phrase["ruby"] = [
                    (phrase["jp"], extra_readings[phrase["jp"]])
                ]
                continue
            chars = list(phrase["jp"])
            readings = CONTEXTUAL.get(phrase["jp"])
            if readings and len(readings) == 1:
                phrase["ruby"] = [(phrase["jp"], readings[0])]
                continue
            if not readings or len(readings) != len(chars):
                readings = [_reading(conv, char) for char in chars]
            phrase["ruby"] = list(zip(chars, readings))

        result.append(
            {
                "start": row["start"],
                "end": row["end"],
                "jp": row["jp"],
                "romaji": " ".join(phrase["romaji"] for phrase in phrase_data),
                "phrases": phrase_data,
            }
        )
    return result


def build_gaps(lines, duration=None):
    """Return all instrumental gaps, including the leading and trailing ones."""
    gaps = []

    def add(start, end):
        if end - start > 0.001:
            gaps.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "type": "instrumental",
                }
            )

    if not lines:
        if duration and duration > 0:
            add(0.0, duration)
        return gaps

    add(0.0, lines[0]["start"])
    for previous, following in zip(lines, lines[1:]):
        add(previous["end"], following["start"])
    if duration is not None:
        add(lines[-1]["end"], duration)
    return gaps


def _valid_lines(value):
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("start"), (int, float))
            and isinstance(item.get("end"), (int, float))
            and isinstance(item.get("jp"), str)
            for item in value
        )
    )


def load_lyrics(song, duration, reannotate=False):
    """Load editable annotations, or build them when the SRT is newer."""
    lyrics_path = song_path(song, "lyrics")
    annotation_path = song_path(song, "annotation")
    if lyrics_path is None or not lyrics_path.exists():
        raise FileNotFoundError(f"Lyrics file not found: {lyrics_path}")

    lines = None
    if annotation_path and annotation_path.exists() and not reannotate:
        if annotation_path.stat().st_mtime >= lyrics_path.stat().st_mtime:
            try:
                data = json.loads(annotation_path.read_text(encoding="utf-8-sig"))
                candidate = data.get("lines") if isinstance(data, dict) else None
                if _valid_lines(candidate):
                    lines = candidate
            except (OSError, ValueError, TypeError):
                lines = None

    if lines is None:
        rows = parse_srt(lyrics_path)
        if not rows:
            raise ValueError(f"No subtitle cues found in: {lyrics_path}")
        readings_path = song_path(song, "readings")
        readings = None
        if readings_path and readings_path.exists():
            try:
                loaded = json.loads(
                    readings_path.read_text(encoding="utf-8-sig")
                )
                if isinstance(loaded, dict):
                    readings = {
                        str(key): str(value)
                        for key, value in loaded.items()
                        if str(key).strip() and str(value).strip()
                    }
            except (OSError, ValueError, TypeError):
                readings = None
        ruby_path = song_path(song, "ruby")
        ruby_map = None
        if ruby_path and ruby_path.exists():
            try:
                loaded = json.loads(
                    ruby_path.read_text(encoding="utf-8-sig")
                )
                if isinstance(loaded, dict):
                    ruby_map = {
                        str(key): value
                        for key, value in loaded.items()
                        if isinstance(value, list) and value
                    }
            except (OSError, ValueError, TypeError):
                ruby_map = None
        lines = annotate_rows(rows, readings, ruby_map)

    payload = {
        "version": 1,
        "lines": lines,
        "gaps": build_gaps(lines, duration),
    }

    if annotation_path:
        annotation_path.parent.mkdir(parents=True, exist_ok=True)
        old_payload = None
        if annotation_path.exists():
            try:
                old_payload = json.loads(
                    annotation_path.read_text(encoding="utf-8-sig")
                )
            except (OSError, ValueError):
                old_payload = None
        if old_payload != payload:
            annotation_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    translations_path = song_path(song, "translations")
    if translations_path and translations_path.exists():
        try:
            translations = json.loads(
                translations_path.read_text(encoding="utf-8-sig")
            )
            if isinstance(translations, list):
                for line, translation in zip(lines, translations):
                    if isinstance(translation, str):
                        line["zh"] = translation.strip()
        except (OSError, ValueError, TypeError):
            pass

    character_timing_path = song_path(song, "character_timing")
    if character_timing_path and character_timing_path.exists():
        try:
            timing_rows = json.loads(
                character_timing_path.read_text(encoding="utf-8-sig")
            )
            if isinstance(timing_rows, list):
                for line, timing in zip(lines, timing_rows):
                    if isinstance(timing, dict) and isinstance(timing.get("chars"), list):
                        line["char_timings"] = timing["chars"]
        except (OSError, ValueError, TypeError):
            pass

    return lines


def read_song(song_dir):
    song_dir = Path(song_dir).expanduser().resolve()
    config_path = song_dir / "song.json"
    if not config_path.exists():
        return None
    with config_path.open("r", encoding="utf-8-sig") as handle:
        config = json.load(handle)
    config["_dir"] = song_dir
    config["_title"] = (
        config.get("display_title") or config.get("title") or song_dir.name
    )
    config["_enabled"] = bool(config.get("enabled", True))
    return config


def discover_songs():
    songs = []
    if not SONGS_DIR.exists():
        return songs
    for path in sorted(SONGS_DIR.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_dir() or path.name.startswith("_"):
            continue
        song = read_song(path)
        if song is not None:
            songs.append(song)
    return songs


def song_path(song, key):
    value = song.get(key)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = song["_dir"] / path
    return path.resolve()


def ensure_playback_audio(song):
    source = song_path(song, "audio")
    if source is None or not source.exists():
        raise FileNotFoundError(f"Audio file not found: {source}")

    playback = song_path(song, "playback_audio")
    if playback is None:
        playback = source.with_suffix(".wav")
    if playback.exists():
        return playback
    if source.suffix.lower() == ".wav":
        return source

    playback.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            str(playback),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError(
            "ffmpeg conversion failed:\n" + "\n".join(detail)
        )
    if not playback.exists():
        raise RuntimeError(f"Audio conversion did not create: {playback}")
    return playback


def audio_duration(path):
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def _first_existing(paths):
    for path in paths:
        if path.exists():
            return path
    return None


class FontBook:
    """Load fonts lazily and cache them by kind and point size."""

    def __init__(self):
        self.jp_path = _first_existing(
            [
                Path(r"C:\Windows\Fonts\msgothic.ttc"),
                Path(r"C:\Windows\Fonts\YuGothM.ttc"),
                Path(r"C:\Windows\Fonts\YuGothR.ttc"),
                Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"),
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            ]
        )
        self.ui_path = _first_existing(
            [
                Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
                Path(r"C:\Windows\Fonts\msyh.ttc"),
                Path(r"C:\Windows\Fonts\segoeui.ttf"),
                Path("/System/Library/Fonts/PingFang.ttc"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            ]
        )
        self._cache = {}

    def get(self, kind, size):
        size = max(8, int(size))
        key = (kind, size)
        if key not in self._cache:
            path = self.jp_path if kind == "jp" else self.ui_path
            self._cache[key] = pygame.font.Font(
                str(path) if path else None,
                size,
            )
        return self._cache[key]


def draw_text(surface, font, text, color, position, anchor="center"):
    image = font.render(str(text), True, color)
    rect = image.get_rect(**{anchor: position})
    surface.blit(image, rect)
    return rect


def volume_slider_rect(width, height):
    slider_width = max(90, min(150, int(width * 0.11)))
    return pygame.Rect(
        width - 56 - slider_width,
        height - 52,
        slider_width,
        8,
    )


def set_volume_from_x(player, slider, x):
    ratio = (x - slider.x) / max(1, slider.width)
    player.set_volume(ratio)


def draw_volume_slider(screen, fonts, player, width, height):
    slider = volume_slider_rect(width, height)
    pygame.draw.rect(screen, (53, 65, 89), slider, border_radius=4)
    fill = int(slider.width * player.volume)
    pygame.draw.rect(
        screen,
        (87, 205, 255),
        (slider.x, slider.y, fill, slider.height),
        border_radius=4,
    )
    pygame.draw.circle(
        screen,
        (225, 244, 255),
        (slider.x + fill, slider.centery),
        8,
    )
    pygame.draw.circle(
        screen,
        (53, 65, 89),
        (slider.x + fill, slider.centery),
        8,
        1,
    )
    draw_text(
        screen,
        fonts.get("ui", 14),
        "音量",
        (128, 145, 174),
        (slider.x - 12, slider.centery),
        "midright",
    )
    draw_text(
        screen,
        fonts.get("ui", 14),
        f"{int(round(player.volume * 100))}%",
        (128, 145, 174),
        (slider.centerx, slider.bottom + 9),
        "midtop",
    )


class Player:
    """Small playback wrapper synchronized to SDL_mixer's playback clock."""

    def __init__(self, audio_path, length):
        pygame.mixer.init()
        pygame.mixer.music.load(str(audio_path))
        self.length = max(0.01, float(length))
        self.offset = 0.0
        self.paused = True
        self.started = False
        self.anchor = time.monotonic()
        self.volume = 0.1
        pygame.mixer.music.set_volume(self.volume)

    def play_pause(self):
        if self.started and not self.paused and self.position() >= self.length - 0.05:
            self.offset = 0.0
            self.started = False
            self.paused = True
            pygame.mixer.music.stop()

        if not self.started:
            self._play_from(self.offset)
        elif self.paused:
            # Restart the current segment so the mixer position and the lyric
            # clock share the same zero point.  Calling ``unpause`` leaves
            # SDL_mixer's old get_pos() origin in place and can double-count
            # the already played segment when position() is sampled.
            self._play_from(self.offset)
        else:
            self.offset = self.position()
            pygame.mixer.music.pause()
            self.paused = True

    def seek(self, delta):
        self.seek_to(self.position() + delta)

    def seek_to(self, seconds):
        target = max(0.0, min(self.length - 0.01, float(seconds)))
        self.offset = target
        if self.started and not self.paused:
            self._play_from(target)
        else:
            # Keep the new position ready for the next play, even when paused.
            self.started = False
            self.paused = True
            pygame.mixer.music.stop()

    def _play_from(self, position):
        pygame.mixer.music.play(start=position)
        pygame.mixer.music.set_volume(self.volume)
        self.offset = position
        self.started = True
        self.paused = False
        self.anchor = time.monotonic()

    def set_volume(self, value):
        self.volume = max(0.0, min(1.0, float(value)))
        pygame.mixer.music.set_volume(self.volume)

    def position(self):
        if not self.started or self.paused:
            return self.offset
        # SDL_mixer is the clock that advances the actual audio.  Using a
        # separate monotonic clock accumulates small resampling and scheduling
        # differences, which become visible as lyric drift in the second half
        # of a long track.  get_pos() is reset by _play_from(), so offset is
        # the absolute position at the start of this playback segment.
        mixer_ms = pygame.mixer.music.get_pos()
        if mixer_ms >= 0:
            return min(self.length, self.offset + mixer_ms / 1000.0)
        elapsed = time.monotonic() - self.anchor
        if not pygame.mixer.music.get_busy() and elapsed > 0.05:
            return self.length
        return min(self.length, self.offset + elapsed)

    def stop(self):
        pygame.mixer.music.stop()


def draw_current_lyric(screen, fonts, item, rect, position=None):
    blocks = item.get("phrases") or [
        {"jp": item["jp"], "romaji": item.get("romaji", "")}
    ]
    measure_jp = fonts.get("jp", 34)
    measure_ui = fonts.get("ui", 24)

    def measure_block(phrase):
        ruby = phrase.get("ruby") or [
            (phrase["jp"], phrase.get("romaji", ""))
        ]
        widths = [
            max(measure_jp.size(char)[0], measure_ui.size(reading)[0], 24) + 4
            for char, reading in ruby
        ]
        return max(58, sum(widths) + 16)

    prepared = [(phrase, measure_block(phrase)) for phrase in blocks]
    available = max(200, rect.width - 52)
    rows = []
    row = []
    row_width = 0
    for phrase, block_width in prepared:
        needed = block_width if not row else block_width + 8
        if row and row_width + needed > available:
            rows.append((row, row_width - 8))
            row = []
            row_width = 0
        row.append((phrase, block_width))
        row_width += block_width + 8
    if row:
        rows.append((row, row_width - 8))
    if not rows:
        return

    # Shrink every row to fit the current panel instead of silently dropping
    # lines when the window is small or a lyric needs more than three rows.
    translation = item.get("zh", "").strip()
    if position is None:
        position = item.get("start", 0.0)
    line_duration = max(0.001, item.get("end", position) - item.get("start", position))
    lyric_progress = max(
        0.0,
        min(1.0, (position - item.get("start", position)) / line_duration),
    )
    translation_space = max(56, int(rect.height * 0.24)) if translation else 0
    lyric_height = max(40, rect.height - translation_space)
    block_step = max(30, min(82, (lyric_height - 12) // len(rows)))
    block_height = max(28, block_step - 5)
    jp_font = fonts.get("jp", max(12, min(34, int(block_step * 0.42))))
    roman_font = fonts.get("ui", max(8, min(24, int(block_step * 0.29))))
    y_block = rect.y + max(
        0,
        (lyric_height - len(rows) * block_step + 6) // 2,
    ) - min(10, max(4, rect.height // 30))

    char_timings = item.get("char_timings") or []
    total_cells = sum(
        len(phrase.get("ruby") or [(phrase["jp"], phrase.get("romaji", ""))])
        for block_row, _ in rows
        for phrase, _ in block_row
    )
    if len(char_timings) != total_cells:
        duration = max(0.001, item.get("end", position) - item.get("start", position))
        char_timings = [
            {
                "start": item.get("start", position) + duration * index / max(1, total_cells),
                "end": item.get("start", position) + duration * (index + 1) / max(1, total_cells),
            }
            for index in range(total_cells)
        ]
    cell_index = 0

    for block_row, block_width_total in rows:
        x = rect.centerx - block_width_total // 2
        for phrase, block_width in block_row:
            block_rect = pygame.Rect(x, y_block, block_width, block_height)
            pygame.draw.rect(
                screen,
                (47, 61, 91),
                block_rect,
                border_radius=max(8, block_height // 5),
            )
            ruby = phrase.get("ruby") or [
                (phrase["jp"], phrase.get("romaji", ""))
            ]
            cell_widths = [
                max(jp_font.size(char)[0], roman_font.size(reading)[0], 20) + 4
                for char, reading in ruby
            ]
            cursor = block_rect.centerx - sum(cell_widths) / 2
            for (char, reading), cell_width in zip(ruby, cell_widths):
                center_x = int(cursor + cell_width / 2)
                timing = char_timings[cell_index]
                timing_start = float(timing.get("start", item.get("start", position)))
                timing_end = float(timing.get("end", timing_start))
                cell_fill = max(
                    0.0,
                    min(1.0, (position - timing_start) / max(0.001, timing_end - timing_start)),
                )
                jp_color = (255, 255, 255)
                roman_color = (119, 205, 255)
                draw_text(
                    screen,
                    roman_font,
                    reading,
                    roman_color,
                    (center_x, block_rect.y + 3),
                    "midtop",
                )
                draw_text(
                    screen,
                    jp_font,
                    char,
                    jp_color,
                    (center_x, block_rect.y + 3 + roman_font.get_height()),
                    "midtop",
                )
                if cell_fill > 0:
                    # A clipped highlight sweeps across each character from
                    # left to right, keeping the karaoke motion readable even
                    # when a ruby cell contains more than one glyph.
                    highlight_clip = pygame.Rect(
                        block_rect.x,
                        block_rect.y,
                        max(1, round(block_rect.width * cell_fill)),
                        block_rect.height,
                    )
                    old_clip = screen.get_clip()
                    screen.set_clip(highlight_clip.clip(old_clip))
                    draw_text(
                        screen,
                        roman_font,
                        reading,
                        (255, 235, 126),
                        (center_x, block_rect.y + 3),
                        "midtop",
                    )
                    draw_text(
                        screen,
                        jp_font,
                        char,
                        (255, 244, 174),
                        (center_x, block_rect.y + 3 + roman_font.get_height()),
                        "midtop",
                    )
                    screen.set_clip(old_clip)
                cell_index += 1
                cursor += cell_width
            x += block_width + 8
        y_block += block_step

    if translation:
        chinese_font = fonts.get(
            "ui",
            max(22, min(36, int(rect.height * 0.12))),
        )
        draw_text(
            screen,
            chinese_font,
            translation,
            (202, 214, 235),
            (
                rect.centerx,
                rect.bottom - max(32, int(translation_space * 0.62)),
            ),
        )


def show_song_picker():
    pygame.init()
    pygame.display.set_caption("Phrase Player")
    screen = pygame.display.set_mode((960, 640), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    fonts = FontBook()
    songs = discover_songs()

    while True:
        width, height = screen.get_size()
        margin = max(28, width // 24)
        card_height = 88
        card_gap = 14
        top = 154
        cards = [
            (
                pygame.Rect(
                    margin,
                    top + index * (card_height + card_gap),
                    width - margin * 2,
                    card_height,
                ),
                song,
            )
            for index, song in enumerate(songs)
        ]
        mouse = pygame.mouse.get_pos()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return None
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                pygame.quit()
                return None
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for rect, song in cards:
                    if rect.collidepoint(event.pos) and song["_enabled"]:
                        pygame.quit()
                        return song

        screen.fill((14, 18, 30))
        draw_text(
            screen,
            fonts.get("ui", 36),
            "Phrase Player",
            (235, 242, 255),
            (margin, 42),
            "topleft",
        )
        draw_text(
            screen,
            fonts.get("ui", 16),
            "选择歌曲进入歌词窗口",
            (128, 145, 174),
            (margin, 92),
            "topleft",
        )

        if not songs:
            draw_text(
                screen,
                fonts.get("ui", 20),
                "songs 目录里还没有可用的歌曲配置。",
                (190, 201, 222),
                (width // 2, height // 2),
            )
        for rect, song in cards:
            enabled = song["_enabled"]
            hovered = enabled and rect.collidepoint(mouse)
            pygame.draw.rect(
                screen,
                (39, 53, 82) if hovered else (24, 32, 52),
                rect,
                border_radius=18,
            )
            pygame.draw.rect(
                screen,
                (87, 205, 255) if enabled else (58, 70, 94),
                rect,
                width=2,
                border_radius=18,
            )
            draw_text(
                screen,
                fonts.get("ui", 24),
                song["_title"],
                (255, 255, 255) if enabled else (125, 139, 163),
                (rect.x + 24, rect.centery - 12),
                "midleft",
            )
            detail = song["_dir"].name
            if not enabled:
                detail += "  ·  待制作"
            draw_text(
                screen,
                fonts.get("ui", 14),
                detail,
                (128, 145, 174),
                (rect.x + 24, rect.centery + 17),
                "midleft",
            )
            draw_text(
                screen,
                fonts.get("ui", 16),
                "打开" if enabled else "未完成",
                (87, 205, 255) if enabled else (92, 105, 128),
                (rect.right - 28, rect.centery),
                "midright",
            )

        pygame.display.flip()
        clock.tick(60)


def run_song(song_dir, reannotate=False):
    song = read_song(song_dir)
    if song is None:
        raise FileNotFoundError(f"Missing song.json in: {song_dir}")
    if not song["_enabled"]:
        raise RuntimeError(f"Song is not enabled yet: {song['_dir'].name}")

    audio_path = ensure_playback_audio(song)
    duration = audio_duration(audio_path)
    lyrics = load_lyrics(song, duration, reannotate=reannotate)

    pygame.init()
    pygame.display.set_caption(f"Player - {song['_title']}")
    if os.name == "nt":
        desktop_w = ctypes.windll.user32.GetSystemMetrics(0)
        desktop_h = ctypes.windll.user32.GetSystemMetrics(1)
    else:
        info = pygame.display.Info()
        desktop_w, desktop_h = info.current_w, info.current_h
    screen = pygame.display.set_mode(
        (desktop_w, desktop_h),
        pygame.RESIZABLE,
    )
    try:
        hwnd = pygame.display.get_wm_info().get("window")
        if hwnd and os.name == "nt":
            ctypes.windll.user32.ShowWindow(hwnd, 3)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass

    clock = pygame.time.Clock()
    fonts = FontBook()
    player = Player(audio_path, duration)
    active = None
    last_active = None
    transition_frame = None
    transition_new_frame = None
    transition_started = 0.0
    transition_duration = 0.45
    started_once = False
    dragging_volume = False
    running = True

    try:
        while running:
            seek_jump = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_SPACE:
                        player.play_pause()
                    elif event.key == pygame.K_LEFT:
                        player.seek(-5)
                        seek_jump = True
                    elif event.key == pygame.K_RIGHT:
                        player.seek(5)
                        seek_jump = True
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    width, height = screen.get_size()
                    slider = volume_slider_rect(width, height)
                    button = pygame.Rect(
                        width // 2 - 70,
                        height - 64,
                        140,
                        48,
                    )
                    bar = pygame.Rect(56, height - 106, width - 112, 8)
                    if slider.inflate(0, 24).collidepoint(event.pos):
                        dragging_volume = True
                        set_volume_from_x(player, slider, event.pos[0])
                    elif button.collidepoint(event.pos):
                        player.play_pause()
                    elif bar.inflate(0, 24).collidepoint(event.pos):
                        ratio = (event.pos[0] - bar.x) / max(1, bar.width)
                        player.seek_to(ratio * player.length)
                        seek_jump = True
                elif event.type == pygame.MOUSEMOTION and dragging_volume:
                    if event.buttons[0]:
                        width, height = screen.get_size()
                        set_volume_from_x(
                            player,
                            volume_slider_rect(width, height),
                            event.pos[0],
                        )
                    else:
                        dragging_volume = False
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    dragging_volume = False

            position = player.position()
            active = next(
                (
                    index
                    for index, line in enumerate(lyrics)
                    if line["start"] <= position < line["end"]
                ),
                None,
            )
            if started_once and active != last_active and not seek_jump:
                transition_frame = screen.copy()
                transition_new_frame = None
                transition_started = time.monotonic()
            elif seek_jump:
                transition_frame = None
                transition_new_frame = None
            last_active = active
            started_once = True

            width, height = screen.get_size()
            screen.fill((14, 18, 30))

            design_height = 1250
            layout_scale = min(
                1.0,
                max(0.45, (height - 100) / design_height),
            )
            row_gap = max(6, int(25 * layout_scale))
            top_margin = int(20 * layout_scale)
            bottom_limit = height - 120
            available_height = max(220, bottom_limit - top_margin)
            # The five lyric cards share the space above the timeline.  Scaling
            # the whole board by a fixed minimum used to make the last card
            # overlap the controls on 720p and smaller windows.
            row_unit = max(
                0.24,
                min(
                    1.0,
                    (available_height - 4 * row_gap) / 1100,
                ),
            )
            row_heights = [
                max(36, int(value * row_unit))
                for value in (200, 200, 300, 200, 200)
            ]
            if active is not None:
                indices = [
                    active + offset for offset in (-2, -1, 0, 1, 2)
                ]
            else:
                before = [
                    index
                    for index, line in enumerate(lyrics)
                    if line["end"] <= position
                ][-2:]
                after = [
                    index
                    for index, line in enumerate(lyrics)
                    if line["start"] >= position
                ][:2]
                indices = (
                    [None] * (2 - len(before))
                    + before
                    + [None]
                    + after
                )
                indices = (indices + [None] * 5)[:5]

            y = top_margin
            row_rects = []
            preview_jp = fonts.get(
                "jp",
                max(16, int(34 * max(layout_scale, 0.7))),
            )
            preview_ui = fonts.get(
                "ui",
                max(12, int(24 * max(layout_scale, 0.7))),
            )
            for row_index, (index, row_height) in enumerate(
                zip(indices, row_heights)
            ):
                item = (
                    lyrics[index]
                    if index is not None and 0 <= index < len(lyrics)
                    else None
                )
                current = row_index == 2
                rect = pygame.Rect(48, y, width - 96, row_height)
                row_rects.append(rect.copy())
                pygame.draw.rect(
                    screen,
                    (30, 39, 63) if current else (24, 32, 52),
                    rect,
                    border_radius=22,
                )
                if item is not None and current:
                    draw_current_lyric(screen, fonts, item, rect, position)
                elif item is not None:
                    draw_text(
                        screen,
                        preview_jp,
                        item["jp"],
                        (130, 146, 177),
                        (rect.centerx, rect.centery - 18),
                    )
                    draw_text(
                        screen,
                        preview_ui,
                        item.get("zh", ""),
                        (81, 105, 142),
                        (rect.centerx, rect.centery + 22),
                    )
                y += row_height + row_gap

            bar = pygame.Rect(56, height - 106, width - 112, 8)
            pygame.draw.rect(screen, (53, 65, 89), bar, border_radius=4)
            fill = int(
                bar.width
                * min(1.0, position / max(player.length, 0.01))
            )
            pygame.draw.rect(
                screen,
                (87, 205, 255),
                (bar.x, bar.y, fill, bar.height),
                border_radius=4,
            )
            draw_text(
                screen,
                fonts.get("ui", 16),
                format_time(position),
                (190, 201, 222),
                (56, height - 88),
                "topleft",
            )
            draw_text(
                screen,
                fonts.get("ui", 16),
                format_time(player.length),
                (190, 201, 222),
                (width - 88, height - 88),
                "topleft",
            )
            draw_text(
                screen,
                fonts.get("ui", 16),
                "←/→ 跳转 5 秒",
                (128, 145, 174),
                (width // 2, height - 88),
                "midtop",
            )
            button = pygame.Rect(width // 2 - 70, height - 64, 140, 48)
            pygame.draw.rect(
                screen,
                (87, 205, 255),
                button,
                border_radius=17,
            )
            draw_text(
                screen,
                fonts.get("ui", 16),
                "播放" if player.paused else "暂停",
                (10, 23, 38),
                button.center,
            )
            draw_volume_slider(screen, fonts, player, width, height)

            if transition_frame is not None:
                if transition_new_frame is None:
                    transition_new_frame = screen.copy()
                progress = min(
                    1.0,
                    (time.monotonic() - transition_started)
                    / transition_duration,
                )
                if progress >= 1.0:
                    transition_frame = None
                    transition_new_frame = None
                else:
                    board_bottom = min(height, int(y))
                    board_area = pygame.Rect(0, 0, width, board_bottom)
                    screen.set_clip(board_area)
                    screen.fill((14, 18, 30), board_area)

                    # Move each card together with its text.  The old whole-board
                    # scroll made the nearly identical card backgrounds look
                    # continuous, while text from the old/new frames visibly
                    # snapped between rows.  Pairing row surfaces keeps every
                    # lyric attached to its card as it moves to the row above.
                    eased = progress * progress * (3.0 - 2.0 * progress)

                    def shifted_slot(slot):
                        if slot < 0:
                            base = row_rects[0]
                            return base.move(
                                0,
                                -(base.height + row_gap),
                            )
                        if slot >= len(row_rects):
                            base = row_rects[-1]
                            return base.move(
                                0,
                                base.height + row_gap,
                            )
                        return row_rects[slot]

                    def tween_rect(start, end, amount):
                        return pygame.Rect(
                            round(start.x + (end.x - start.x) * amount),
                            round(start.y + (end.y - start.y) * amount),
                            max(1, round(start.width + (end.width - start.width) * amount)),
                            max(1, round(start.height + (end.height - start.height) * amount)),
                        )

                    def draw_row(frame, source, destination, alpha):
                        row_surface = frame.subsurface(source).copy()
                        if row_surface.get_size() != destination.size:
                            row_surface = pygame.transform.smoothscale(
                                row_surface,
                                destination.size,
                            )
                        row_surface.set_alpha(max(0, min(255, round(alpha))))
                        screen.blit(row_surface, destination)

                    # Old rows travel upward and fade into the corresponding
                    # new rendering.  This also smoothly changes the centre
                    # lyric between preview text and its detailed phrase blocks.
                    for row_index, source_rect in enumerate(row_rects):
                        destination = tween_rect(
                            source_rect,
                            shifted_slot(row_index - 1),
                            eased,
                        )
                        draw_row(
                            transition_frame,
                            source_rect,
                            destination,
                            255 * (1.0 - eased),
                        )

                    for row_index, source_rect in enumerate(row_rects):
                        origin = shifted_slot(row_index + 1)
                        destination = tween_rect(
                            origin,
                            source_rect,
                            eased,
                        )
                        draw_row(
                            transition_new_frame,
                            source_rect,
                            destination,
                            255 * eased,
                        )
                    screen.set_clip(None)

            pygame.display.flip()
            clock.tick(60)
    finally:
        player.stop()
        pygame.quit()


def format_time(seconds):
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phrase Player lyric player",
    )
    parser.add_argument(
        "--song",
        type=Path,
        help="song folder under songs/; omit it to show the song picker",
    )
    parser.add_argument(
        "--reannotate",
        action="store_true",
        help="rebuild lyrics.annotated.json from the SRT file",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list song folders and exit",
    )
    args = parser.parse_args(argv)

    if args.list:
        songs = discover_songs()
        if not songs:
            print("No songs found under songs/.")
        for song in songs:
            status = "ready" if song["_enabled"] else "draft"
            print(f"{song['_dir'].name}\t{song['_title']}\t{status}")
        return 0

    if args.song is not None:
        run_song(args.song, reannotate=args.reannotate)
        return 0

    selected = show_song_picker()
    if selected is not None:
        run_song(selected["_dir"], reannotate=args.reannotate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
