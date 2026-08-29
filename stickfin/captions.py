"""Build an .ass caption file. Three looks, matching the reference reels:

  explainer  navy, casual font, top area, phrase-by-phrase (synced by even
             split across each beat -- no word-level timing needed)
  skit       white ALL-CAPS bold on a black pill, upper third, one cue per line
  title      one persistent meme-style caption (script.title_card) on a white
             pill for the whole video
  none       no captions
"""
from __future__ import annotations

import json
import textwrap

_STYLES = {
    # name: (Fontname, Fontsize, PrimaryColour, BorderStyle, Outline, BackColour, alignment, marginV, bold)
    "explainer": ("Comic Sans MS", 58, "&H00663300", 1, 3, "&H00FFFFFF", 8, 250, -1),
    "skit":      ("Arial",         62, "&H00FFFFFF", 3, 6, "&H00000000", 8, 300, -1),
    "title":     ("Arial",         54, "&H00000000", 3, 8, "&H00FFFFFF", 8, 240, -1),
}


def _header(fmt_wh, style_name):
    w, h = fmt_wh
    font, size, primary, border, outline, back, align, marginv, bold = _STYLES[style_name]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{primary},&H000000FF,{back},{back},{bold},0,0,0,100,100,0,0,{border},{outline},0,{align},80,80,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _wrap(text: str, width: int = 30) -> str:
    return "\\N".join(textwrap.wrap(text, width=width)) or text


def _chunks(text: str, size: int = 3):
    words = text.split()
    for i in range(0, len(words), size):
        yield " ".join(words[i:i + size])


def build(script, out_path):
    style = script.caption_style
    if style == "none":
        return None

    narration = json.loads((script.build_dir / "narration.json").read_text())
    say_by_id = {b.id: b for b in script.beats}
    lines = [_header(_canvas(script.fmt), style)]

    if style == "title":
        text = _wrap(script.title_card or script.title, 26)
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(narration['total_s'])},Cap,,0,0,0,,{text}")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path

    t = 0.0
    for entry in narration["beats"]:
        d = entry["duration_s"]
        beat = say_by_id[entry["id"]]
        if beat.is_live or not beat.say:
            t += d
            continue
        if style == "skit":
            lines.append(f"Dialogue: 0,{_ts(t)},{_ts(t + max(d - 0.05, 0.2))},"
                         f"Cap,,0,0,0,,{_wrap(beat.say.upper(), 26)}")
        else:  # explainer -- phrase by phrase
            phrases = list(_chunks(beat.say, 3))
            span = d / max(len(phrases), 1)
            for j, ph in enumerate(phrases):
                s0 = t + j * span
                lines.append(f"Dialogue: 0,{_ts(s0)},{_ts(s0 + span)},Cap,,0,0,0,,{ph}")
        t += d

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def _canvas(fmt):
    from . import config
    return config.canvas(fmt)
