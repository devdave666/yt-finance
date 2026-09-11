# Music library

Real background music for the pipeline, curated by hand once and then used
automatically forever — same pattern as `assets/icons/` and `assets/sfx/`:
committed, frozen, no API calls at render time.

`stickfin/music.py` picks from whatever is here. An empty folder is fine —
long-form falls back to a synthesised drone (`sfx.build_bed`); a Short falls
back to plain room tone. Nothing breaks either way; it just sounds better
once tracks land.

**Filled 2026-09-11**: 3 tracks per mood, sourced from Mixkit (see below) --
YouTube Audio Library needs an interactive login'd UI with no API, so Claude
can't pull from it directly; Mixkit's tracks are plain, curl-able MP3 URLs
under an equally "no attribution required" licence, found via the JSON-LD
metadata embedded in Mixkit's own tag pages (`https://assets.mixkit.co/music/
<id>/<id>.mp3`) rather than the page's JS-rendered download button. Picked by
Mixkit's own genre tag (Ambient/Chillout/Corporate Music/Film Score/Drone
Music -- these read as instrumental; Pop/Hip Hop/Reggaeton/Country/R&B were
skipped as vocal-risk) and spot-checked with an ffmpeg spectrogram
(`showspectrumpic`) instead of by ear, since Claude can view an image but not
literally listen. **Worth a real listen before fully trusting the picks** --
the spectrogram check catches "this is clearly a sung pop track," not "this
fits the mood."

## Where to get tracks (safe for a monetized channel)

**Mixkit** (mixkit.co/free-stock-music) -- royalty-free, no attribution, no
sign-up, direct MP3 download, explicitly fine for YouTube/commercial use
(not for CDs/DVDs/broadcast, irrelevant here). The only source of the three
below that's actually automatable (plain HTTP, no login) -- the direct MP3 is
in the page's embedded JSON-LD (`"url":"https://assets.mixkit.co/music/
<id>/<id>.mp3"`), not the JS download button curl/WebFetch can't see.

**YouTube Audio Library** (studio.youtube.com → Audio Library) is the
recommended source — it's YouTube's own library, built for monetized
creators, and its "no attribution required" tracks are the simplest possible
case: drop the file in, nothing else to do.

1. Open **studio.youtube.com → Audio Library → Music**.
2. Filter **Attribution: "Attribution not required"** (skip anything marked
   "Attribution required" unless you're fine adding a credit line to every
   video description that uses it — if you do want one of those, tell Claude
   which track + its required credit text so it can be added automatically).
3. Filter by mood/genre per the folders below, preview, download the ones
   that fit, and drop the mp3 straight into the matching folder.
4. No renaming needed — any filename works, any of `.mp3 .wav .m4a .ogg .flac`.

**Alternative**: incompetech.com (Kevin MacLeod) — everything there is
CC-BY, which needs a per-track credit line in the description (the site
gives you the exact text). Only use it if you're OK with that extra step.

**Do not** use anything marked "Attribution: Not for commercial use" /
CC-BY-NC, or anything from Jamendo without an actual paid licence (Jamendo
runs its own YouTube Content ID matching — using a track without their
licence risks a claim).

## The four mood folders

- **`neutral/`** — the default bed. Calm, analytical, unobtrusive. Light
  piano/ambient/lo-fi instrumental, no vocals, no strong beat. This one needs
  the most tracks (5+) since it's picked most often.
- **`tense/`** — sections that name a fee, a loss, a trap, a crash (anywhere
  the script sets `tone: negative`). Slightly darker/moodier, still
  instrumental and still understated — not horror-movie, just "something's
  wrong here."
- **`resolve/`** — the closing section of a long-form video, where it lands
  the takeaway. Slightly brighter/more settled than `neutral`.
- **`playful/`** — skit-format Shorts (two characters, a situation). Lighter,
  a bit more rhythmic/upbeat. Still no vocals — vocals will fight the narration.

3-5 tracks per folder is enough to keep it from feeling repetitive; more is
better. If a folder ends up empty, `neutral` (or whatever folder has
anything) is used instead, so nothing is required before this ships — but
`neutral` having at least a few tracks matters most since it's the default.

## What happens automatically

- **Shorts**: one track for the whole video, mood picked from the script
  (`playful` for a skit, `neutral` otherwise).
- **Long-form**: the video is split into sections at the same beats that
  already get a sfx riser (`emphasis: true` in the script) — each section
  picks its own track by mood and crossfades into the next, so a 5-minute
  video isn't one loop start to finish. The very last section is always
  `resolve`.
- A track already used recently isn't picked again until the others in its
  mood have had a turn (`state/music_history.json`, same idea as topic
  rotation).

## One thing to check once real tracks land

The mix level (`config.MUSIC_BED_LUFS`, default -30 LUFS integrated) is set
from first principles, not from actually listening -- Claude can measure
levels (`ffmpeg volumedetect`) but can't literally listen. Measured on a real
build (`mixkit-digital-clouds-175.mp3` under a 24s explainer voiceover): bed
mean -31.2dB vs VO mean -15.4dB, ~16dB of headroom -- numerically the bed
sits well clear of the voice, but **have an actual listen to the first render
or two**; if it's too loud/quiet by ear, that's the one number to move
(`STICKFIN_MUSIC_BED_LUFS` env var, or tell Claude "music is too loud/quiet").
