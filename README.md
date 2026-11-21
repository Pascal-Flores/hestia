# Hestia

Assistant vocal **local-first** dont le cœur est écrit en **Rust**.
Les services de reconnaissance vocale, de synthèse et d'écoute restent séparés.

- **Cœur Hestia (Rust / Axum)** : API HTTP, routage sémantique FastEmbed/ONNX, outils YAML, scheduling et administration web.
- **LLM local (llama.cpp)** : extraction JSON des paramètres et rédaction des réponses. Le modèle ne choisit pas l'outil.
- **Whisper (Python, faster-whisper)** : reconnaissance vocale en français, taille du modèle configurable.
- **Pocket TTS (Rust)** : synthèse française et normalisation préalable des nombres.
- **Listener (Python)** : micro ALSA, RNNoise, openWakeWord, capture des commandes et restitution audio.

## Installation Docker (Ubuntu / Linux avec micro)

```bash
cp .env.example .env  # uniquement au premier lancement
docker compose up -d --build
docker compose ps
```

Le tableau de bord est accessible sur **http://localhost:8765/**.
Le `listener` démarre **par défaut** et nécessite un périphérique `/dev/snd`.
Si la machine est sans microphone / sous Docker Desktop :

```bash
docker compose up -d --build --scale listener=0
```

Il n'y a plus de `--profile listener` ni de `compose.rust.yaml` à ajouter :
**`compose.yaml` utilise Rust par défaut**. Un petit fichier
`compose.rust.yaml` est conservé temporairement comme compatibilité
pour les anciennes installations qui l'avaient inscrit dans leur `.env`.

Pour suivre les commandes vocales :

```bash
docker compose logs -f listener
curl -fsS http://localhost:8765/health/live
curl -fsS http://localhost:8765/health/ready
```

Si un micro est silencieux, régler `LISTENER_INPUT_DEVICE` dans
`.env` (voir [docs/listener.md](docs/listener.md)). Le wake word de
démonstration est « Hey Jarvis », pas « Hestia ».

## Plugins et automatisations

Le répertoire `tools/` contient des plugins indépendants en
`tool.yaml` + script ou exécutable. L'interface web permet de les
activer, de configurer leur clé API et d'ajouter des exécutions
récurrentes (cron, paramètres JSON, fuseau horaire). Les secrets sont
conservés localement dans le volume Docker `hestia_data` et ne sont
pas renvoyés en clair par l'API d'administration.

Outils fournis : calculatrice scientifique, heure et uptime, météo et
prévisions Open-Meteo, recherche Brave avec clé API, recherche Wikipédia,
catalogue musical, radios / MPD, devises, dés. Aucun outil domotique
n'est inclus pour le moment. La lecture musicale nécessite un lecteur
MPD séparé ; la recherche de titres ne lance pas la lecture.

Un plugin `exec.call` peut utiliser `python3`, `sh`, `bash` ou un
programme natif (`./...`). **Le cœur reste en Rust**, mais les scripts
de plugins Python et les services `listener` / `stt` ont encore besoin
de Python 3. Ne pas installer de plugins inconnus : ce n'est pas une
sandbox de sécurité.

## Configurer le modèle STT et les ressources

Dans `.env` :

```dotenv
STT_MODEL=base
# ou STT_MODEL=small (meilleure reconnaissance, plus de RAM)
```

Les réglages et clés des plugins se font depuis le tableau de bord.
Un lanceur expérimental `./scripts/up.sh` sélectionne automatiquement
les profils mémoire 2g, 4g ou 8g ; le profil 2g n'est pas validé en
conditions réelles. `./scripts/memory.sh` affiche l'usage mémoire.

Documentation : [démarrage / migration Docker](docs/docker.md),
[configuration du microphone](docs/listener.md),
[manifestes Rust](core-rs/README.md).

## Sécurité et limites

L'API écoute **uniquement sur 127.0.0.1 par défaut**. Avant tout accès
depuis le réseau local, renseigner `HESTIA_API_KEY`, ouvrir
l'interface uniquement sur un réseau de confiance et prévoir un pare-feu.
Le volume `hestia_data` contient les secrets des plugins en clair dans
un fichier protégé (0600). Les étapes de code ne sont pas isolées des
droits du processus. Les performances sur carte ARM / 2 Go restent à
mesurer et le parcours vocal complet est encore expérimental.
