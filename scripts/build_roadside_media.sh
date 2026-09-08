#!/usr/bin/env bash
# Encode CARLA roadside camera PNG sequences into H.264 MP4s.
# Original frames are never deleted. Re-run after replacing images.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
MEDIA_ROOT="${ROADSIDE_MEDIA_ROOT:-${REPO_ROOT}/roadside_media}"
OUT_DIR="${ROADSIDE_MEDIA_OUT_DIR:-${MEDIA_ROOT}/encoded}"
FPS="${ROADSIDE_MEDIA_FPS:-10}"
HEIGHT="${ROADSIDE_MEDIA_HEIGHT:-720}"
CRF="${ROADSIDE_MEDIA_CRF:-26}"
PRESET="${ROADSIDE_MEDIA_PRESET:-fast}"
CAMERAS="${ROADSIDE_MEDIA_CAMERAS:-demo_14 demo_15 demo_19}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "error: ffmpeg is required" >&2
  exit 1
fi
if ! command -v ffprobe >/dev/null 2>&1; then
  echo "error: ffprobe is required" >&2
  exit 1
fi

if ! [[ "${FPS}" =~ ^[1-9][0-9]*([.][0-9]+)?$ ]]; then
  echo "error: FPS must be a positive number, got ${FPS}" >&2
  exit 1
fi
if ! [[ "${HEIGHT}" =~ ^[0-9]+$ ]] || (( HEIGHT < 2 )); then
  echo "error: HEIGHT must be an even positive integer, got ${HEIGHT}" >&2
  exit 1
fi
if (( HEIGHT % 2 != 0 )); then
  echo "error: HEIGHT must be even for yuv420p, got ${HEIGHT}" >&2
  exit 1
fi

FRAME_DURATION="$(awk -v fps="${FPS}" 'BEGIN { printf "%.6f", 1.0 / fps }')"
mkdir -p "${OUT_DIR}"

natural_sort_key() {
  # Sort by the last integer in the filename so frame_1, frame_2, frame_10 stay in order.
  awk '
    {
      path = $0
      n = split(path, parts, /[^0-9]+/)
      key = 0
      for (i = 1; i <= n; i++) if (parts[i] != "") key = parts[i] + 0
      printf "%020d\t%s\n", key, path
    }
  ' | sort -n | cut -f2-
}

escape_concat_path() {
  # ffmpeg concat demuxer uses single-quoted paths.
  local path="$1"
  path="${path//\'/\'\\\'\'}"
  printf "%s" "${path}"
}

encode_camera() {
  local camera="$1"
  local src="${MEDIA_ROOT}/${camera}"
  local concat="${OUT_DIR}/${camera}.concat.txt"
  local out="${OUT_DIR}/${camera}.mp4"

  if [[ ! -d "${src}" ]]; then
    echo "error: missing camera directory ${src}" >&2
    return 1
  fi

  local -a frames=()
  local file
  while IFS= read -r file; do
    [[ -n "${file}" ]] || continue
    frames+=("${file}")
  done < <(
    find "${src}" -maxdepth 1 -type f \( \
      -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webp' \
    \) | natural_sort_key
  )

  if (( ${#frames[@]} == 0 )); then
    echo "error: no images found in ${src}" >&2
    return 1
  fi

  {
    local i
    for ((i = 0; i < ${#frames[@]}; i++)); do
      printf "file '%s'\n" "$(escape_concat_path "${frames[$i]}")"
      printf "duration %s\n" "${FRAME_DURATION}"
    done
    # concat demuxer needs the last file repeated to honor its duration
    printf "file '%s'\n" "$(escape_concat_path "${frames[-1]}")"
  } > "${concat}"

  echo "encoding ${camera}: ${#frames[@]} frames -> ${out} (${FPS} fps, height ${HEIGHT}, crf ${CRF})"

  ffmpeg -hide_banner -y \
    -f concat -safe 0 -i "${concat}" \
    -vf "scale=-2:${HEIGHT}:flags=lanczos,format=yuv420p" \
    -r "${FPS}" \
    -vsync cfr \
    -c:v libx264 \
    -preset "${PRESET}" \
    -crf "${CRF}" \
    -pix_fmt yuv420p \
    -an \
    -movflags +faststart \
    "${out}"

  echo "--- ${camera} ---"
  echo "frames: ${#frames[@]}"
  echo "first:  ${frames[0]}"
  echo "last:   ${frames[-1]}"
  ffprobe -hide_banner -v error \
    -select_streams v:0 \
    -show_entries stream=width,height,avg_frame_rate,duration,codec_name,pix_fmt \
    -show_entries format=duration,size \
    -of default=nokey=0:noprint_wrappers=1 \
    "${out}"
  ls -lh "${out}" | awk '{ print "file_size: " $5 }'
}

failed=0
for camera in ${CAMERAS}; do
  if ! encode_camera "${camera}"; then
    failed=1
  fi
done

if (( failed != 0 )); then
  echo "error: one or more cameras failed to encode" >&2
  exit 1
fi

echo "done. mp4 files are in ${OUT_DIR}"
echo "original images under ${MEDIA_ROOT}/demo_* were not modified."
