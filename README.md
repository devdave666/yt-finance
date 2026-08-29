# stickfin — autonomous stick-figure Shorts channel

Runs hands-off: a daily GitHub Actions cron picks a personal-finance topic,
writes the script, animates it, and posts it to YouTube. You only ever edit
[`themes.yaml`](themes.yaml). Setup steps: [`SETUP.md`](SETUP.md).

```
themes.yaml  (you seed once)
   │
   ├─ generate  Gemini (Vertex) picks next topic → writes scripts/auto/<date>-<slug>.yaml + meta
   ├─ audio     per-beat TTS (per-character voice) + live-clip audio  → audio/*.wav, voiceover.wav, narration.json
   ├─ plan      timings → shot list + asset list                       → timeline.json, asset_plan.json   (no API calls)
   ├─ assets    generate art (Nano Banana / Vertex) + cut out (rembg)  → assets/{bg,char,prop,cutout}/*.png
   ├─ render    composite each shot + hard-cut concat + mux + captions  → <slug>.mp4
   └─ publish   commit to media/ for hosting → Buffer → YouTube Short
```

Style target = the reference reels in [`refs/`](refs/): 9:16, thick-outline
figures, reusable pose/background assets composited as layers, **pose-to-pose
hard cuts on the beat** (no Ken Burns, no tweening), prominent synced captions.
`format: wide` (16:9 long-form) is a flag for the later phase.

`run_daily.py` is the full chain; `python -m stickfin.pipeline <script.yaml>`
runs a single hand-written script through everything after `generate`.

### Where the output is

```
build/<slug>/
  <slug>.mp4                 ← final video
  voiceover.wav  narration.json  timeline.json  asset_plan.json  captions.ass
  audio/<beat>.wav           per-beat audio
  assets/bg/<scene>.png      opaque backgrounds
  assets/char/_<name>.png    character reference sheet (identity lock)
  assets/char/<name>__<state>.png   transparent pose+expression cutouts
  assets/prop/<name>.png     transparent prop cutouts
  assets/cutout/<hash>.png   transparent cutouts of ingested photos/frames
  clips/*.mp4                per-shot intermediates
```

`--only b1,b2` writes to `build/<slug>__only-b1-b2/`.

### How it matches the references

| reference behaviour | how stickfin does it |
|---|---|
| 9:16, static camera | `format: short`, flat composite, no zoompan |
| pose-to-pose swaps, no tweening | each beat = a fresh composite; hard-cut concat |
| reusable characters / backgrounds | assets generated once, keyed by `name__state`, cached |
| photoreal cutouts (yacht, cash, faces) | `cutouts:` — local file or URL → rembg → composited |
| live-action reaction cutaway | `live: { src, trim }` beat — clip is scaled/cropped in, its own audio |
| a change every 1–2s | `MAX_HOLD_S` (1.8s) splits long beats into holds |
| prominent captions | `caption_style: explainer` / `skit` / `title` (see `refs/*.analysis.json`) |
| two voices in a skit | per-character `voice:` in `cast:` |

### GCP setup (one time)

Same project core-decor-automation uses. ADC only — the $300 Vertex credit
covers image + TTS; it does **not** cover the AI Studio API (calls force
`vertexai=True`).

```bash
gcloud auth application-default login
gcloud services enable texttospeech.googleapis.com --project project-58f4f689-36b9-406b-bfa
pip install -r requirements.txt      # ffmpeg + ffprobe already on PATH; rembg pulls onnxruntime (~180MB model on first run)
```

### Run

```bash
python run_daily.py                         # full autonomous chain (generate → … → publish)
python -m stickfin.generate                 # just write today's script from themes.yaml
python -m stickfin.pipeline scripts/auto/<file>.yaml   # run a script through audio→render
python -m stickfin.pipeline scripts/skit_doctor_glasses.yaml --only b1   # cheap style test (~$0.25)
```

`STICKFIN_AUTOPUBLISH=1` makes `run_daily.py` actually post; otherwise it
builds + commits only. Resume / redo a stage: `--stage assets`, `--from render`,
`--force`, `--music`, `--no-captions`.

Rough cost per short: TTS ~$0.01, ~25–30 assets @ ~$0.04 ≈ **$1.00–1.30**.

### No-spend dry run

```bash
python tools/fake_narration.py scripts/skit_doctor_glasses.yaml
python -m stickfin.pipeline scripts/skit_doctor_glasses.yaml --stage plan
python tools/make_placeholder_assets.py build/skit-doctor-glasses
python -m stickfin.pipeline scripts/skit_doctor_glasses.yaml --stage render
```

### Script format

Two worked examples:
- [`scripts/skit_doctor_glasses.yaml`](scripts/skit_doctor_glasses.yaml) — two-hander dialogue skit, persistent title caption
- [`scripts/explainer_compound_interest.yaml`](scripts/explainer_compound_interest.yaml) — single narrator, phrase-by-phrase captions, flat white scenes

Key fields: `format` (short/wide) · `caption_style` (explainer/skit/title/none)
· `title_card` · `cast:` (each with `voice`, `look`, `anchor` left/center/right)
· `scenes:` (each with a `bg` prompt or flat `color`) · `beats:` (each with
`scene`, `who` speaker, `say`, `cast:` name→"pose, expression", `props:`,
`cutouts:`, or `live: {src, trim}`).

### Reference analyses

`refs/s_ref*.analysis.json` — Gemini multimodal breakdowns of the three
reference reels (character spec, motion technique, hold times, caption style,
audio, remake notes). Regenerate with `python tools/analyze_refs.py <mp4>...`.

### Not built yet

- **Lip-sync**: swapping 2–3 mouth variants of the speaker during a line. The
  refs do a subtle "pop"; current motion is pose-swap-on-beat + captions.
- **Sub-shot variation**: a long beat currently repeats its composite for each
  hold. Add `steps:` to a beat for "prop appears mid-sentence" control.
- **Auto left/right blocking** refinements, prop-on-character attachment.
