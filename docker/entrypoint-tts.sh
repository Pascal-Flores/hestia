#!/bin/sh
set -eu
if [ "${1:-}" = "pocket-tts" ]; then shift; fi
set -- /usr/local/bin/pocket-tts "$@"
# Compact setup quantizes the French model. TTS_QUANTIZE=0 disables it.
if [ "${TTS_QUANTIZE:-1}" = "1" ]; then
    set -- "$@" --quantize
fi
exec "$@"
