# Hestia natif (sans Docker) — Linux x86_64 et ARM64

Le profil natif réduit le nombre de runtimes Python :

- `llama-server` (llama.cpp C/C++) : LLM Qwen3 GGUF (Q4_K_M).
- `whisper-server` (whisper.cpp C/C++) : STT français, modèle `tiny` multilingue.
- `pocket-tts` (binaire Rust officiel) : TTS français `french`, quantifié INT8 par défaut.
- **Passerelle Hestia Python** : FastAPI + FastEmbed/ONNX + workflows YAML.

**Ce n'est pas encore un serveur Hestia entièrement Rust.** La passerelle Rust prévue reste
à implémenter et valider. Les outils peuvent néanmoins être en Python, Bash, Rust ou C via
`exec.call` + stdin/stdout JSON. L'exécution de code tiers n'est pas sandboxée.

## Installation (CPU, sans root pour les processus)

Sur Ubuntu/Debian, installer les prérequis une fois :

```bash
sudo apt update
sudo apt install -y git cmake build-essential curl ca-certificates python3-venv ffmpeg
```

Puis dans Hestia :

```bash
cp native.env.example native.env
./scripts/native/install.sh
./scripts/native/run.sh --dry-run
./scripts/native/run.sh --check
./scripts/native/run.sh
```

La préparation compile localement llama.cpp et whisper.cpp, télécharge le modèle Whisper
multilingue et installe le binaire Pocket TTS officiel vérifié par SHA256. Le modèle GGUF
Qwen3 et la voix française Pocket TTS sont téléchargés à la première utilisation et mis
en cache dans `.native/cache`. Cela demande de l'espace disque et du réseau.

Par défaut **aucun service de modèle n'est exposé sur le réseau** : ports 8080, 8001 et
8000 sur `127.0.0.1` ; Hestia écoute également `127.0.0.1:8765`.

Pour tester le tour vocal complet :

```bash
curl -sS http://127.0.0.1:8765/v1/audio/ask -F 'file=@question.wav' -o sortie.json
python3 - <<'PY'
import json, base64
result = json.load(open('sortie.json'))
print(result['transcript'], '->', result['text'])
open('reponse.wav', 'wb').write(base64.b64decode(result['audio_base64']))
PY
```

La passerelle convertit l'audio en WAV mono 16 kHz via `ffmpeg` avant de l'envoyer au
serveur whisper.cpp `/inference`, qui renvoie un JSON `{"text": ...}`. Cette route
est différente de `/v1/transcribe` utilisée par le profil Docker historique.

## Démarrage automatique et diagnostic

```bash
./scripts/native/install-service.sh
systemctl --user status hestia-native
journalctl --user -u hestia-native -f
./scripts/native/memory.sh
```

`run.sh` peut aussi rester lancé dans un terminal ; arrêter avec Ctrl+C.
Les logs des moteurs sont dans `.native/logs/`. L'installation automatique par systemd
est optionnelle et démarre au login ; pour un fonctionnement après déconnexion, voir
`loginctl enable-linger` selon la configuration du système.

## Profils mémoire

La mémoire détectée sélectionne automatiquement le profil, modifiable avec
`HESTIA_MEMORY_PROFILE` dans `native.env` :

| Profil | LLM | Contexte | STT |
| --- | --- | --- | --- |
| `2g` | Qwen3 0.6B Q4 | 1024 | Whisper Tiny |
| `4g` | Qwen3 0.6B Q4 | 2048 | Whisper Tiny |
| `8g` | Qwen3 1.7B Q4 | 4096 | Whisper Base |

**Attention : le profil 2g est un objectif expérimental, pas une limite ni une preuve de
consommation sous 2 Go.** Le poids résident à mesurer inclut Hestia, ONNX, LLM, STT, TTS,
l'OS et les pics pendant les requêtes. Le script `memory.sh` affiche RSS et PSS des
processus natifs ; PSS évite de compter plusieurs fois les pages mémoire partagées, mais ne
mesure pas à lui seul tous les besoins de l'OS. Mesurer aussi `free -m` pendant des appels
`/v1/audio/ask` et examiner les logs en cas d'OOM.

Les profils plus gros favorisent la qualité et le contexte, ils ne rendent pas
l'assistant plus modulaire : le mécanisme de plugins reste identique.

## Limites et sécurité

- La disponibilité et l'ABI des binaires précompilés Pocket TTS peuvent varier (glibc,
  AVX2 sur x86-64, ARM64). Sur un x86 plus ancien, choisir l'archive `-v2` compatible
  SSE4.2 selon la documentation Pocket TTS.
- Le serveur Whisper est lié uniquement à la boucle locale ; il reçoit des fichiers
  audio non fiables. Ne l'exposez pas sur Internet.
- Les scripts d'outils restent capables d'accéder aux fichiers/réseau sous les droits de
  l'utilisateur Hestia. Utiliser un compte dédié à privilèges limités pour tout outil tiers.
- Le système de confirmation actuel n'est pas une garantie d'autorisation de production.
- Il faut encore mesurer latence, mémoire et robustesse sur une machine x86 puis ARM64.
