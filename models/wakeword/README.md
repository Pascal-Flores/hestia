# Wake word models

By default the listener uses the built-in **"hey jarvis"** openWakeWord model
for an immediate test. This is **not** a model for the word "Hestia".

To activate on **"Hestia"**, place your own trained `hestia.onnx` here and add
`LISTENER_WAKE_MODEL=/models/wakeword/hestia.onnx` to `.env`. Restart the
listener with `docker compose --profile listener up -d`.

Models must be compatible with openWakeWord's ONNX inference pipeline. See
<https://github.com/dscripka/openWakeWord#training-new-models> for training.

openWakeWord's **bundled pretrained models** have a CC BY-NC-SA license;
check usage terms before commercially distributing any wake-word model.
