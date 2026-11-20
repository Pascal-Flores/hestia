# Déployer Hestia avec Docker Compose (optionnel)

Pour un déploiement sans conteneurs et des moteurs STT/TTS natifs, voir [native.md](native.md).

## Services

| Service | Port interne | Rôle |
|---|---|---|
| `hestia` | 8765 | API FastAPI, index d'embeddings ONNX, workflows YAML |
| `llm` | 8080 | `llama.cpp:server` (Qwen3 0.6B GGUF quantifié 4 bits) |
| `stt` | 8001 | faster-whisper, français, int8 CPU, modèle persistent |
| `tts` | 8000 | Pocket TTS officiel `serve`, français, modèle persistent |
| `listener` (opt-in) | aucun | Micro ALSA, RNNoise, openWakeWord, capture, lecture TTS |

Tous les services partagent un réseau Docker privé. **Seul Hestia est publié sur l'hôte**, à
`127.0.0.1:8765` ; il ne faut pas exposer ce déploiement directement sur Internet.

## Démarrage

Prérequis : Docker Engine + plugin Compose V2 sur un Linux amd64 ou arm64, plusieurs Go libres
sur le disque et une connexion Internet lors du premier démarrage.

```sh
cp .env.example .env
# Adapter les paramètres de .env si nécessaire
docker compose up -d --build
docker compose logs --follow hestia stt tts llm
```

Les quatre modèles sont téléchargés **au premier démarrage**, puis mis en cache. Les modèles
STT/TTS et embeddings peuvent démarrer plus lentement qu'Hestia. Le statut global se vérifie par :

```sh
curl -i http://127.0.0.1:8765/health/ready
```

- `200` : API, LLM, STT et TTS répondent à leurs vérifications de santé.
- `503` : un service n'est pas encore prêt, ou n'est pas accessible.

## API

Les routes sont accessibles sur `http://127.0.0.1:8765` ; `/docs` montre le schéma OpenAPI.

### Routage seul

```sh
curl -s http://127.0.0.1:8765/v1/route \
  -H 'Content-Type: application/json' \
  -d '{"text":"Quelle heure est-il ?"}'
```

### Requête texte → workflow

```sh
curl -s http://127.0.0.1:8765/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"Combien font 12 plus 30 ?"}'
```

### Audio vers texte

```sh
curl -s http://127.0.0.1:8765/v1/audio/transcribe \
  -F 'file=@question.wav'
```

### Texte vers WAV (Pocket TTS)

```sh
curl -s http://127.0.0.1:8765/v1/audio/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"Bonjour, je suis Hestia."}' \
  --output reponse.wav
```

Le profil vocal `mii` est activable avec `HESTIA_VOICE_PROFILE=mii` dans `.env`
(SoX traite la sortie du TTS). Il ne reproduit pas le moteur propriétaire de Nintendo.

### Prononciation des nombres dans Pocket TTS

Par défaut, la passerelle Hestia normalise les nombres **avant** d'appeler Pocket TTS :
`42` devient `quarante-deux`, `14:30` devient `quatorze heures trente`,
`20°C` devient `vingt degrés Celsius` et `3,5` devient
`trois virgule cinq`. La normalisation couvre aussi les dates usuelles,
les ordinaux, les pourcentages et les montants en euros. Seule la
prononciation est modifiée : les valeurs numériques originales restent
intactes dans les réponses HTTP et les paramètres d'outils.

Le traitement est déterministe et ne nécessite aucun appel LLM. Il est
activé par défaut dans `hestia`, aussi bien pour `/v1/audio/speak` que
`/v1/audio/ask`. Pour le désactiver :

```dotenv
HESTIA_TTS_NORMALIZE_NUMBERS=0
```

Puis `docker compose up -d --force-recreate hestia`.
Les URL et adresses e-mail, qui sont des identifiants, ne sont pas
converties en nombres.

### Tour vocal complet

```sh
curl -s http://127.0.0.1:8765/v1/audio/ask -F 'file=@question.wav' > sortie.json
python - <<'PY'
import base64, json
with open('sortie.json') as file:
    result = json.load(file)
print('Question:', result['transcript'])
print('Réponse:', result['text'])
with open('reponse.wav', 'wb') as file:
    file.write(base64.b64decode(result['audio_base64']))
PY
```

Le mode V1 utilise du JSON avec WAV encodé en base64 : **ce n'est pas du streaming** et il
propose un écouteur microphone avec wake word via le profil `listener` (voir [listener.md](listener.md)). Les microcontrôleurs pourront
appeler cette API depuis le réseau local après sécurisation.

### Outils

Les manifestes YAML et scripts sont dans `tools/` et montés en lecture seule dans le
conteneur `hestia`. Après ajout d'un outil, reconstruire l'index :

```sh
curl -X POST http://127.0.0.1:8765/v1/tools/reload
curl http://127.0.0.1:8765/v1/tools
```

## Configuration

Les variables commentées dans `.env.example` permettent de choisir les modèles, la langue,
les seuils de routage et l'interface d'écoute. Pour utiliser un grand LLM externe sur les
requêtes sans outil, renseigner `HESTIA_FALLBACK_LLM_URL`, `HESTIA_FALLBACK_LLM_MODEL` et
`HESTIA_FALLBACK_LLM_API_KEY`. Le LLM local continue de gérer l'extraction et la réponse des
workflows. **Ne pas envoyer de données privées à un fournisseur externe sans l'avoir décidé.**

Par défaut, l'API ne demande pas de clé mais reste accessible **uniquement depuis l'hôte**.
Pour une enceinte ESP32/terminal sur le LAN, définir :

```dotenv
HESTIA_BIND_IP=0.0.0.0
HESTIA_API_KEY=une-cle-longue-aleatoire
```

Les appels doivent alors comporter `-H 'X-Hestia-Key: une-cle-longue-aleatoire'`.
Ajouter un pare-feu et éviter les redirections de ports depuis Internet.
Les appels aux outils sensibles doivent demander confirmation côté application cliente ;
le simple drapeau API `confirmed` ne constitue **pas** une protection de production.

Pour arrêter : `docker compose down`. Pour supprimer **aussi les modèles mis en cache** :
`docker compose down -v` (à utiliser uniquement si la suppression est souhaitée).

## Limites et sécurité

- **CPU et mémoire** : le Raspberry Pi 5 8 Go reste une cible expérimentale, Pocket TTS
  français + STT + ONNX + LLM simultanés pouvant consommer beaucoup de RAM. Ne pas viser
  l'UNO Q 4 Go sans alléger ou externaliser les services.
- Les Dockerfiles installent les dépendances sur amd64/arm64, mais la disponibilité des roues
  PyTorch/Pocket TTS/ctranslate2 ARM64 dépend des versions publiées. À valider physiquement.
- Les tools Python sont du **code exécutable non fiable** : les sous-processus et leurs
  restrictions ne sont pas une sandbox. Ne pas installer de plugins inconnus ; utiliser des
  conteneurs ou restrictions supplémentaires pour les outils tiers.
- Le routage E5 reste à calibrer sur des requêtes positives/négatives en français. Les seuils
  par défaut ne sont **pas** des probabilités ni des niveaux de confiance éprouvés.
- L'API est locale ; le wake word du service `listener` est facultatif. Pas encore d'audio streaming ni de suivi conversationnel
  ou de réelle intégration Home Assistant.
- En cas d'échec d'installation ARM, tester l'empilement sur x86_64 ou séparer le TTS du Pi.
