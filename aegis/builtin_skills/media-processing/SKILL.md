---
name: media-processing
description: Convert, trim, compress, and extract from audio/video/image files with ffmpeg and friends — losslessly where possible, with predictable quality/size. Use for transcode, clip, resize, extract audio/frames, or compress media.
version: 1.0.0
metadata:
  category: media
  tags: [media, ffmpeg, video, audio, image]
requires:
  bins: [ffmpeg]
---

## When to Use
You need to transform media files: change format, trim a clip, shrink file size, extract audio or frames, resize/convert images, or batch-process a folder. Prefer **stream-copy (no re-encode)** when only cutting/remuxing — it's instant and lossless.

## Procedure
1. **Inspect first.** Probe the file to learn codec, container, resolution, duration, and bitrate before deciding anything. The right command depends on what's actually inside.
2. **Pick re-encode vs copy.** If you're only cutting or changing container, use `-c copy` (no quality loss, near-instant). Re-encode only when you must change codec, size, or resolution.
3. **Control quality explicitly.** For H.264/H.265 use CRF (lower = better; ~18 visually lossless, ~23 good default) plus a `-preset` (slower = smaller). For audio, set a target bitrate (e.g. 192k AAC). Don't leave quality to defaults you didn't choose.
4. **Cut accurately.** Put `-ss` (start) and `-to`/`-t` (end/duration) correctly: `-ss` before `-i` is fast (keyframe-seek); after `-i` is frame-accurate but slower. State which you need.
5. **Batch with a loop**, not by hand. Process a folder with a shell loop; keep originals until output is verified.
6. **Verify** the output plays and matches the intended duration/size/quality before deleting anything.

## Quick Reference
```bash
ffprobe -hide_banner in.mp4                          # inspect codecs/streams/duration
ffmpeg -i in.mov -c:v libx264 -crf 23 -preset medium -c:a aac -b:a 192k out.mp4   # transcode
ffmpeg -ss 00:01:30 -to 00:02:00 -i in.mp4 -c copy clip.mp4                        # lossless cut
ffmpeg -i in.mp4 -vn -c:a libmp3lame -q:a 2 out.mp3                                # extract audio
ffmpeg -i in.mp4 -vf "scale=1280:-2" -crf 24 small.mp4                             # downscale 720p
ffmpeg -i in.mp4 -vf fps=1 frame_%04d.png                                          # 1 frame/sec
ffmpeg -i in.mp4 -vf "fps=12,scale=480:-1" -loop 0 out.gif                         # video → gif
for f in *.wav; do ffmpeg -i "$f" "${f%.wav}.flac"; done                           # batch convert
# images (ImageMagick, if installed):
magick in.png -resize 800x -strip out.jpg
```

## Pitfalls
- Re-encoding when a stream-copy would do — slow and lossy for no reason.
- Leaving CRF/bitrate to defaults, then being surprised by the size or quality.
- `-ss` placement: after `-i` is accurate but slow; before `-i` is fast but snaps to a keyframe. Choosing wrong gives a misaligned or sluggish cut.
- Forgetting `-c:a` so audio gets silently re-encoded or dropped.
- Odd width/height with some codecs → failure; use `scale=W:-2` to keep even dimensions.
- Overwriting or deleting the source before the output is verified.

## Verification
- Output **plays** end to end (probe it) and the duration matches the intended trim.
- File size / resolution / format match the goal; quality is acceptable on a spot-check.
- For lossless cuts, codecs are unchanged from the source (confirming `-c copy` worked).
- Batch runs produced one output per input with no errors; originals untouched until confirmed.
