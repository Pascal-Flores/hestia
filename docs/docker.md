# Docker Compose — Hestia avec cœur Rust

La configuration principale est **`compose.yaml`**. Les services sont :
`hestia` (Rust + interface web), `llm` (llama.cpp), `stt` (faster-whisper),
`tts` (Pocket TTS Rust) et `listener` (microphone, RNNoise, wake word).

## Démarrer

```sh
cp .env.example .env  # uniquement si .env n'existe pas déjà
docker compose up -d --build
docker compose ps
```

L'interface est disponible à **http://localhost:8765/**. `listener` est
lancé **automatiquement** et utilise `/dev/snd` (Linux ALSA).
Pour démarrer les moteurs sans microphone :

```sh
docker compose up -d --build --scale listener=0
```

Pour consulter les journaux :

```sh
docker compose logs -f hestia
docker compose logs -f listener
curl -fsS http://localhost:8765/health/live
curl -fsS http://localhost:8765/health/ready
```

`/health/live` teste l'API Rust ; `/health/ready` teste aussi LLM/STT/TTS.
Le healthcheck du service Rust utilise **curl**, jamais `python`.

## Migration depuis les anciennes commandes Docker

La version précédente lançait Rust en surcouche :

```sh
docker compose -f compose.yaml -f compose.rust.yaml --profile listener up -d --build
```

Désormais `compose.yaml` **inclut Rust et le listener**. Utiliser :

```sh
git pull
docker compose up -d --build --force-recreate
docker compose ps
```

Si tu avais ajouté `COMPOSE_FILE=compose.yaml:compose.rust.yaml` dans
ton `.env`, **supprime cette ligne** pour revenir à la configuration
unique. Un `compose.rust.yaml` vide est conservé temporairement
pour ne pas faire échouer les déploiements existants.

Les volumes de modèles `llm_cache`, `stt_cache` et `tts_cache`
restent inchangés, de même que `hestia_data` qui contient les
paramètres du tableau de bord, les clés API et le cache FastEmbed.
L'ancien volume Python `embedding_cache` n'est plus utilisé.
**Ne pas exécuter `docker compose down -v`**, car cela supprimerait
aussi les modèles et les paramètres enregistrés.

## Configurer les services

Les réglages de lancement se trouvent dans `.env` :

- `STT_MODEL=tiny|base|small|medium` pour la précision de Whisper
  (par défaut `base`, modèle multilingue français en INT8 CPU).
- `LISTENER_INPUT_DEVICE` et `LISTENER_OUTPUT_DEVICE` pour
  sélectionner le matériel audio.
- `HESTIA_API_KEY` pour protéger les API et le tableau de bord,
  et `HESTIA_BIND_IP` pour un accès LAN explicite.
- `HESTIA_TTS_NORMALIZE_NUMBERS=1` pour prononcer les nombres
  français en toutes lettres avant Pocket TTS lors du premier lancement.
  Le réglage peut ensuite être modifié depuis le tableau de bord.

Les réglages avancés du cœur et les clés API des plugins sont
persistés dans `hestia_data`. Les valeurs déjà enregistrées dans ce
volume prennent le pas sur les défauts de l'environnement.

Pour modifier seulement le STT sans reconstruire tous les services :

```sh
docker compose up -d --no-deps --force-recreate stt
```

## Outils et requêtes

```sh
# Routage sémantique sans exécuter de plugin
curl -s http://localhost:8765/v1/route \
  -H 'Content-Type: application/json' \
  -d '{"text":"Quelle heure est-il ?"}'

# Réponse textuelle
curl -s http://localhost:8765/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"Calcule 2 puissance 8"}'

# Reconnaissance d'un fichier audio
curl -s http://localhost:8765/v1/audio/transcribe \
  -F 'file=@recordings/last-command.wav'

# Texte vers Pocket TTS
curl -s http://localhost:8765/v1/audio/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"Il est 14h30 et il fait 20 degrés."}' --output reponse.wav
```

La réponse de `/v1/audio/ask` contient la transcription, la réponse
textuelle et un WAV encodé en base64. Le listener réutilise cette route.

## Sécurité

Le dashboard est lié à `127.0.0.1:8765` par défaut et ne doit pas
être exposé publiquement. Le fichier persistant
`/data/hestia-settings.json` contient les clés API en clair,
avec permissions 0600. Protéger les sauvegardes Docker.

Les scripts `exec.call` sont exécutés avec les droits du conteneur,
sans sandbox stricte. N'installer que des outils de confiance.
La musique via MPD nécessite un service externe et une configuration
explicite. Le scheduler n'envoie pas encore de notifications automatiques.
