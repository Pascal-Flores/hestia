#!/usr/bin/env bash
# One-time native setup. Does not require Docker, root, or a Rust compiler.
# Requires: git, cmake, a C/C++ compiler, curl, tar, sha256sum, Python venv, ffmpeg.
set -euo pipefail
source "$(dirname "$0")/common.sh"
for cmd in git cmake curl tar sha256sum python3; do
  command -v "$cmd" >/dev/null || { echo "Missing command: $cmd" >&2; exit 1; }
done
command -v ffmpeg >/dev/null || echo 'Warning: install ffmpeg before the first audio transcription' >&2
mkdir -p "$NATIVE_HOME"/{bin,src,models,cache,logs,run}

if [[ ! -x "$NATIVE_HOME/venv/bin/uvicorn" ]]; then
  python3 -m venv "$NATIVE_HOME/venv"
fi
"$NATIVE_HOME/venv/bin/python" -m pip install --upgrade pip
"$NATIVE_HOME/venv/bin/python" -m pip install -e "$HESTIA_ROOT[api,embeddings-onnx]"

build_cpp() {
  local repo="$1" name="$2" target="$3"
  local source="$NATIVE_HOME/src/$name"
  if [[ ! -d "$source/.git" ]]; then
    git clone --depth 1 "https://github.com/ggml-org/$repo.git" "$source"
  fi
  # CPU-only build reduces dependencies and runtime memory.
  cmake -S "$source" -B "$source/build" -DCMAKE_BUILD_TYPE=Release \
    -DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_METAL=OFF
  cmake --build "$source/build" --config Release --target "$target" -j "${BUILD_JOBS:-2}"
  install -m 755 "$source/build/bin/$target" "$NATIVE_HOME/bin/$target"
}
build_cpp llama.cpp llama.cpp llama-server
build_cpp whisper.cpp whisper.cpp whisper-server

# Download multilingual Whisper model, not the English-only tiny.en.
for model in tiny base; do
  if [[ ! -f "$NATIVE_HOME/models/ggml-$model.bin" ]]; then
    if [[ "$model" == base && "${NATIVE_FETCH_BASE:-0}" != 1 ]]; then continue; fi
    "$NATIVE_HOME/src/whisper.cpp/models/download-ggml-model.sh" "$model"
    cp "$NATIVE_HOME/src/whisper.cpp/models/ggml-$model.bin" "$NATIVE_HOME/models/ggml-$model.bin"
  fi
done

# Native Pocket TTS: pinned release + SHA256, no PyTorch/Python in TTS process.
if [[ ! -x "$NATIVE_HOME/bin/pocket-tts" ]]; then
  case "$(uname -m)" in
    x86_64) arch=x86_64; digest=9abff0aa138a804e44052952d8c3bdaae247fb267ca8f4b83fe178897d1a0559 ;;
    aarch64) arch=aarch64; digest=2b9a727bffd86ea6d73370d675cbd91a7db118f11b7f5a60343c2d5da12b187a ;;
    *) echo "Unsupported Pocket TTS native CPU: $(uname -m)" >&2; exit 1 ;;
  esac
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  archive="pocket-tts-3.3.0-${arch}-unknown-linux-gnu.tar.gz"
  curl --fail --location --retry 3 -o "$tmp/pocket.tar.gz" \
    "https://github.com/kyutai-labs/pocket-tts/releases/download/v4.0.1/$archive"
  printf '%s  %s\n' "$digest" "$tmp/pocket.tar.gz" | sha256sum -c -
  mkdir -p "$tmp/unpacked"
  tar -xzf "$tmp/pocket.tar.gz" -C "$tmp/unpacked"
  binary=$(find "$tmp/unpacked" -type f -name pocket-tts | head -n 1)
  [[ -n "$binary" ]] || { echo 'Pocket TTS executable missing in archive' >&2; exit 1; }
  install -m 755 "$binary" "$NATIVE_HOME/bin/pocket-tts"
  rm -rf "$tmp"
  trap - EXIT
fi

echo 'Native stack installed. Run ./scripts/native/run.sh'
echo 'Tip: use ./scripts/native/install-service.sh to run at login with systemd --user.'
