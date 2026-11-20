# Écoute permanente, wake word et nettoyage audio (Docker Compose)

Le service `listener` est **optionnel**. Il tourne sur l'ordinateur qui possède le micro
et les haut-parleurs, et appelle le Hestia existant à travers le réseau Docker interne.
Il n'enregistre **pas** la conversation en continu sur disque et n'envoie de son au STT
qu'après une activation vocale.

## Pipeline réel

```text
Micro ALSA (48 kHz, mono PCM16) -> callback PortAudio non bloquant / file bornée
  -> RNNoise natif (trames de 10 ms, réduction bruit / musique de fond partielle)
  -> filtre passe-bas FIR continu -> 16 kHz PCM16
  -> openWakeWord (ONNX), déclencheur
  -> enregistrement après wake word, VAD RMS + probabilité vocale RNNoise
  -> arrêt après silence, durée maximale ou absence de voix
  -> POST /v1/audio/ask (WAV 16 kHz)
  -> Whisper STT -> routage sémantique -> workflow/LLM -> Pocket TTS
  -> WAV retourné dans JSON/base64 -> lecture haut-parleur
```

**Limites :** RNNoise est un *speech enhancer*, pas un modèle de séparation vocale
music/speech garanti. Une musique avec chant, une TV diffusant des voix ou une personne
qui parle au même moment peuvent perturber l'activation et la transcription. L'atténuation
se teste en conditions réelles. Pour supprimer correctement de la musique sur laquelle
on parle, il faudrait tester un modèle de séparation plus lourd ; pour éliminer la sortie
des haut-parleurs de l'appareil, il faudrait une annulation d'écho avec signal de référence
(AEC), non implémentée ici. **Le microphone est mis en pause pendant la réponse audio**, donc
l'assistant ne se réactive pas sur sa propre voix (mais il n'y a pas de barge-in).

## Démarrer sur Ubuntu / Raspberry Pi avec micro local

Ce mode requiert Linux, un périphérique ALSA visible dans `/dev/snd`, et un micro/haut-parleur
accessibles depuis le conteneur. Il ne marche pas automatiquement sur Docker Desktop
Windows/macOS et peut demander une configuration de routage audio sur PipeWire/PulseAudio.

```sh
cp .env.example .env  # si ce n'est pas déjà fait
ls -l /dev/snd
# Construire et démarrer API + LLM + STT + TTS + écouteur :
docker compose --profile listener up -d --build
# Suivre la détection / les erreurs ALSA :
docker compose --profile listener logs -f listener
```

Le service `listener` **n'est pas lancé** par le simple `docker compose up -d`.
Le service Hestia et les modèles restent utilisables sans microphone ni périphérique audio.

### Sélection du microphone / haut-parleur

```sh
docker compose --profile listener run --rm --no-deps listener \
  python -m listener.app --list-devices
```

Renseigner `LISTENER_INPUT_DEVICE` et `LISTENER_OUTPUT_DEVICE` (indice ou sous-chaîne du nom
PortAudio), puis `docker compose --profile listener up -d`. Si `/dev/snd` appartient
au groupe `audio`, vérifier son GID via `stat -c %g /dev/snd/controlC0` et régler
`LISTENER_AUDIO_GID` en conséquence (défaut 29 sur Ubuntu). Pour les WAV de
diagnostic dans `./recordings`, régler `LISTENER_UID` et `LISTENER_GID` si votre compte
Linux n'utilise pas UID/GID 1000 (`id -u`, `id -g`).
Sur certaines machines, l'accès ALSA direct n'utilise pas les périphériques audio de la
session utilisateur ; dans ce cas utiliser une carte son USB directement accessible,
ou une configuration ALSA/Pulse/PipeWire spécifique à la machine.

### Si le journal affiche « Microphone overrun »

La première version exécutait RNNoise et openWakeWord sur le même thread que
`RawInputStream.read()`. Le traitement pouvait dépasser le budget de **80 ms**
et PortAudio perdait des échantillons. Le service utilise désormais un callback
PortAudio qui **copie seulement l'audio** dans une file bornée ; le filtrage et
l'inférence sont exécutés indépendamment. Par défaut, PortAudio choisit lui-même
la taille des callbacks (`LISTENER_PORTAUDIO_BLOCKSIZE=0`) ; le listener
recompose ensuite des blocs fixes de 80 ms pour RNNoise et openWakeWord.
Un bloc fixe de 3840 échantillons peut provoquer un tampon supplémentaire
sur certaines API audio ; il reste disponible en configuration pour comparer. Les blocs les plus anciens sont
abandonnés lorsque la file sature, plutôt que d'accumuler du retard.

Le service enregistre toutes les 30 secondes les informations suivantes :
`avg processing`, `max`, `max queued`, `dropped` et `PortAudio overflows`.
Une moyenne supérieure à 80 ms/bloc indique que le CPU est durablement incapable
de suivre le temps réel ; la file ne résout que les pics temporaires. Une perte
pendant l'enregistrement d'une commande annule cette commande pour éviter une
transcription incomplète. Pour identifier le goulot :

```sh
docker compose stats listener
docker compose --profile listener logs -f listener
# Test temporaire sans RNNoise : changer LISTENER_FILTER=none dans .env,
# puis redémarrer l'écouteur.
docker compose --profile listener up -d --force-recreate listener
```

Si la capture déborde encore, réduire la pression CPU des autres conteneurs,
notamment `LLM_THREADS` et `STT_CPU_THREADS`, ou augmenter
`LISTENER_QUEUE_BLOCKS` (16 blocs = 1,28 s ; maximum 128, avec latence accrue).
`LISTENER_INPUT_LATENCY=high` peut aider certains périphériques ALSA au prix
d'une latence de capture supplémentaire. L'absence d'overrun ne prouve pas la
qualité de la détection ; tester wake word et STT après chaque réglage.

### Wake word de test, puis « Hestia »

**Par défaut le déclencheur est « Hey Jarvis »**, car openWakeWord ne fournit pas de modèle
préentraîné spécifique à « Hestia ». Dire « Hey Jarvis », puis une commande comme
« Quelle heure est-il ? ». `LISTENER_WAKE_THRESHOLD` peut être ajusté pour limiter les
faux déclenchements (défaut 0,55). Un seuil élevé réduit les faux positifs mais risque de
manquer la commande.

Pour utiliser réellement **« Hestia »**, entraîner un modèle ONNX compatible openWakeWord
(voir <https://github.com/dscripka/openWakeWord#training-new-models>), puis le déposer
sous `models/wakeword/hestia.onnx`. Dans `.env` :

```dotenv
LISTENER_WAKE_MODEL=/models/wakeword/hestia.onnx
```

Recréer le service : `docker compose --profile listener up -d`.
Le modèle **n'est pas livré** avec cette V1. Les modèles préentraînés distribués par
openWakeWord ne sont pas automatiquement utilisables commercialement ; lire leurs licences.

### Comparer les enregistrements nettoyés et bruts

Arrêter temporairement l'écouteur qui monopolise le micro :

```sh
docker compose stop listener
docker compose --profile listener run --rm --no-deps listener \
  python -m listener.app --record-sample 8
# Puis comparer :
aplay recordings/raw.wav
aplay recordings/enhanced.wav
# Relancer :
docker compose --profile listener up -d
```

Le fichier brut est à **48 kHz**, l'amélioré à **16 kHz**, comme les données envoyées au
wake word et au STT. Les WAV sont enregistrés seulement lors de ce test explicite.

## Paramètres utiles (dans `.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `LISTENER_FILTER` | `rnnoise` | `rnnoise` ou `none` (comparaison) |
| `LISTENER_QUEUE_BLOCKS` | `16` | File audio bornée (80 ms/bloc) ; absorbe les pics CPU |
| `LISTENER_INPUT_LATENCY` | vide | Latence PortAudio `low` ou `high` |
| `LISTENER_PORTAUDIO_BLOCKSIZE` | `0` | Taille des callbacks choisie par le pilote ; `3840` force les anciens blocs de 80 ms |
| `LISTENER_METRICS_INTERVAL` | `30` | Intervalle du journal de performance, en secondes |
| `LISTENER_WAKE_MODEL` | `hey jarvis` | Modèle intégré ou chemin ONNX |
| `LISTENER_WAKE_THRESHOLD` | `0.55` | Score d'activation openWakeWord |
| `LISTENER_WAKE_CONSECUTIVE` | `1` | Nombre de trames positives de suite |
| `LISTENER_SPEECH_THRESHOLD` | `0.30` | Probabilité vocale RNNoise pour le VAD |
| `LISTENER_RMS_THRESHOLD` | `0.008` | Niveau minimum d'énergie voix |
| `LISTENER_SILENCE` | `0.85` | Secondes de silence après parole |
| `LISTENER_INITIAL_TIMEOUT` | `3.5` | Temps maximal pour commencer à parler |
| `LISTENER_MAX_UTTERANCE` | `12.0` | Limite de durée de commande |
| `LISTENER_INPUT_DEVICE` | auto | Périphérique d'entrée PortAudio |
| `LISTENER_OUTPUT_DEVICE` | auto | Périphérique de sortie PortAudio |

Les scores RNNoise et openWakeWord ne sont pas des probabilités de bon routage.
Calibrer avec des essais en condition réelle, avec musique/TV, à diverses distances.

## Confidentialité et sécurité

- Le filtrage, le wake word et le découpage de l'énoncé sont exécutés localement.
- L'audio n'est envoyé au STT qu'après activation, via le réseau Docker interne.
- Si un LLM distant est configuré comme repli, du **texte** peut lui être envoyé.
- `listener` reçoit uniquement le périphérique audio `/dev/snd`, sans port hôte exposé.
- Une protection par clé `HESTIA_API_KEY` est compatible avec cet écouteur.
- Le hotword activé par défaut est une démonstration ; ne pas l'utiliser sans vérifier
  la licence des modèles si le projet est distribué ou commercialisé.

Ce prototype ne fournit ni commande de volume adaptative, ni conversation multi-tour,
ni AEC de référence, ni annulation parfaite de musique, ni contrôle vocal d'autorisation
d'opérations sensibles. Il faut tester les performances RAM/CPU et les périphériques sur
la machine finale avant de déclarer l'ensemble compatible avec 2 Go.
