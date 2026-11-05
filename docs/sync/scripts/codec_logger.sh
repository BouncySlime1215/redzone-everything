#!/bin/zsh
# Read-only: stream each stick's decoder bitrate lines + YTTV metadata changes, stamped with stick epoch time.
S=${0:a:h}
for H in 100.110.165.150:5555 100.110.165.181:5555; do
  ( while true; do
      adb -s $H logcat -v epoch -T 1 MediaCodecLogger:I whad-MediaSession:I '*:S' 2>/dev/null \
        | grep --line-buffered -E "video\..*bitrateInKbps|onMetadataChanged" \
        | while IFS= read -r l; do print -r -- "$(date +%s.%N | cut -c1-14) ${H%%:*} $l"; done >> "$S/codec_${H%%:*}.log"
      sleep 5
    done ) &
done
wait
