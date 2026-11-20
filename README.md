# Hestia

Prototype d'assistant vocal **local-first** et extensible. Un outil est un dossier contenant un manifeste `tool.yaml` et, si nécessaire, un script Python. Le LLM **ne choisit pas** parmi tous les outils.

## Pipeline

`audio WAV → STT → embeddings → routage sémantique → étapes déclaratives (extraction LLM / Python / réponse LLM) → Pocket TTS → WAV`

1. Les exemples de chaque outil sont vectorisés une fois au démarrage. Un modèle multilingue sélectionne l'outil le plus proche par maximum de similarité cosinus entre ses exemples et la requête.
2. Si la similarité est insuffisante, aucun outil n'est exécuté. Si les deux premiers outils sont trop proches, Hestia demande une clarification (seuil et marge configurables).
3. Un petit LLM local extrait **uniquement** les paramètres de l'outil retenu sous forme JSON. Le schéma JSON est validé côté Python avant toute exécution.
4. Les étapes `python.call`, `tool.call`, `core.template` et `ai.respond` exécutent le workflow défini par l'outil. Les sorties sont référencées par `${{ steps.<id>.output }}` ; aucune expression arbitraire n'est évaluée.
5. Les demandes sans outil peuvent être transmises à un modèle de conversation plus puissant en configurant une URL de repli.

Le moteur API reste text-first ; la capture du micro et la détection du wake word sont prises en charge par le service Docker `listener` optionnel. Le streaming et les automatismes Home Assistant restent à développer.

## Déploiement natif recommandé (sans Docker)

Pour réduire les dépendances sur Raspberry Pi ou un PC Ubuntu, installer les binaires
**llama.cpp**, **whisper.cpp**, **Pocket TTS Rust** et la passerelle Hestia :

```bash
cp native.env.example native.env
./scripts/native/install.sh
./scripts/native/run.sh
```

Documentation et profils automatiques (2g/4g/8g) : **[docs/native.md](docs/native.md)**.
La cible **2 Go est expérimentale** et doit être confirmée par des mesures sur matériel.
Le cœur Hestia reste provisoirement en Python + FastEmbed/ONNX ; les moteurs LLM, STT et
TTS du mode natif n'ont pas de runtime Python. Les scripts d'outils peuvent utiliser
Python, Bash ou des exécutables C/Rust via `exec.call` et JSON sur stdin/stdout.

## Déploiement Docker Compose (optionnel pour les tests)

La stack CPU pré-configurée utilise **llama.cpp** (Qwen3-0.6B GGUF), **FastEmbed/ONNX**
(E5 multilingue), **faster-whisper** (`base` multilingue français, INT8 CPU) et **Pocket TTS** (`french`, voix `estelle`).
Elle est conçue pour Linux x86_64 et ARM64 ; le Raspberry Pi 5 8 Go est une cible de test,
**pas** une garantie de performances temps réel. L'UNO Q 4 Go est trop contraint pour
recommander cette stack complète. La première initialisation télécharge plusieurs modèles.

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8765/health/ready
```

Seul le port de l'API Hestia (`127.0.0.1:8765`) est publié par défaut.
Le téléchargement des modèles peut prendre plusieurs minutes ; pendant le chargement
`/health/ready` renvoie **503** avec l'état de chaque service. Les modèles sont mis en
cache dans quatre volumes Docker persistants.

Test texte (la sélection d'outil reste sémantique et indépendante du LLM) :

```bash
curl -s http://127.0.0.1:8765/v1/route \
  -H 'Content-Type: application/json' \
  -d '{"text":"Quelle heure est-il ?"}'
curl -s http://127.0.0.1:8765/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"Combien font 12 plus 30 ?"}'
```

Pour le STT/TTS et le tour complet audio, voir **[docs/docker.md](docs/docker.md)**.
Les outils sont montés depuis `./tools` en lecture seule. Après ajout d'un manifeste,
`POST /v1/tools/reload` reconstruit l'index sans reconstruire les images.


### Écoute permanente (Docker, optionnelle)

Un cinquième service `listener` peut capturer le microphone local, appliquer **RNNoise**
avant le wake word et le STT, reconnaître un mot d'activation openWakeWord, enregistrer
la commande puis jouer le WAV de Pocket TTS. Il n'est **pas** activé par défaut :

```sh
docker compose --profile listener up -d --build
docker compose --profile listener logs -f listener
```

Le wake word livré pour test est **« Hey Jarvis »**, pas « Hestia » : il faut fournir un
modèle ONNX personnalisé pour prononcer « Hestia ». RNNoise atténue certains bruits et
une partie de la musique, mais ne garantit **pas** une séparation musicale complète.
Linux + accès micro/haut-parleur ALSA (`/dev/snd`) requis.

Voir [docs/listener.md](docs/listener.md) pour le choix des périphériques, l'entraînement
d'un modèle, les paramètres de sensibilité et l'enregistrement de comparaison brut/nettoyé.

## Installation

Python 3.10+ :

    python -m venv .venv
    source .venv/bin/activate
    pip install -e '.[embeddings,dev]'
    hestia tools

Les embeddings utilisent par défaut `intfloat/multilingual-e5-small` via sentence-transformers et s'exécutent sur CPU. Le modèle se télécharge au premier lancement. Les préfixes `query:` et `passage:` nécessaires aux modèles E5 sont appliqués automatiquement.

Lancer un serveur LLM compatible OpenAI (par exemple `llama-server`) et configurer ses paramètres :

    export HESTIA_LLM_URL=http://127.0.0.1:8080/v1/chat/completions
    export HESTIA_LLM_MODEL=local
    hestia route "Combien font 4 plus 8 ?"
    hestia ask "Combien font 4 plus 8 ?" --debug
    hestia ask "Quelle heure est-il ?"

Le routage sémantique, l'extraction des paramètres et la formulation sont des tâches distinctes ; le LLM est appelé uniquement dans les étapes qui le demandent. Un petit modèle local n'a jamais à sélectionner lui-même un outil.

Pour déléguer les demandes sans outil à un modèle distant/plus puissant :

    export HESTIA_FALLBACK_LLM_URL=https://exemple.invalid/v1/chat/completions
    export HESTIA_FALLBACK_LLM_MODEL=modele
    export HESTIA_FALLBACK_LLM_API_KEY=secret

Attention : les seuils `HESTIA_ROUTE_MIN_SCORE` (défaut `0.80`) et `HESTIA_ROUTE_MIN_MARGIN` (défaut `0.035`) sont des valeurs initiales, **pas des scores calibrés**. Constituer un jeu de requêtes positives et négatives pour calibrer séparément chaque outil.

## Déclarer un outil

Voir `tools/math/add/tool.yaml` + `main.py`. Le manifeste définit les métadonnées, phrases d'exemple, schéma JSON des paramètres, permissions et liste des steps. Les types de steps disponibles en V1 :

| Step | Rôle |
| --- | --- |
| `ai.extract` | Message + prompt spécialisé → JSON `{status, parameters}` ; valide `inputs` |
| `python.call` | Lance `main.py:run(parameters, context)` en sous-processus, stdin/stdout JSON |
| `ai.respond` | Résultat API + prompt de réponse → texte naturel |
| `tool.call` | Exécute un autre outil par son ID, avec arguments explicites ; cycle interdit |
| `core.template` | Réponse déterministe avec des champs `{{ key }}` |

`ai.extract` peut retourner `ready`, `missing_parameters` (avec `question`), ou `not_applicable`. Une sortie incorrecte bloque l'appel Python. Pour une composition, `tool.call` reçoit `with.tool` et `with.arguments` (objet ou référence de step). Les paramètres transmis à un sous-outil sont validés selon son schéma, et les étapes `ai.extract` déjà alimentées utilisent ces paramètres sans rappeler le LLM.

Les références à des steps précédents doivent occuper une **valeur YAML entière**, par exemple `arguments: ${{ steps.extract.output }}` ou `data: ${{ steps.execute.output }}`. La forme avec chemin `steps.extract.output.piece` est aussi supportée.

### Permissions et sécurité

Une permission demandée dans un manifeste est refusée par défaut ; on peut l'autoriser explicitement via `hestia --allow homeassistant.read ask "..."`. `requires_confirmation: true` exige une confirmation avant exécution (en CLI texte interactif). Les appels à d'autres outils revérifient les permissions et bloquent les cycles.

Les scripts sont exécutés dans un sous-processus Python `-I`, avec délai et variables d'environnement explicitement autorisées par le manifeste (`with.env`). **Ce n'est PAS une sandbox de sécurité** : un script Python peut toujours accéder au système de fichiers ou au réseau avec les droits de son processus. N'installer que des outils de confiance ; une exécution réellement isolée nécessitera un conteneur, un utilisateur système distinct ou un autre mécanisme OS. Ne jamais exposer des secrets à des plugins non fiables.

## Voix (facultatif)

    pip install -e '.[voice]'
    pip install pocket-tts
    # SoX doit être installé pour le profil vocal optionnel `mii`
    hestia voice-file --input requete.wav --output reponse.wav --profile mii

`voice-file` utilise `faster-whisper` local (par défaut `tiny`, français), puis le même workflow, puis `pocket-tts generate --language french`. Le profil `mii` applique un effet SoX simple (pitch + filtre), **pas** une émulation des voix Nintendo. Pocket TTS est lancé par commande pour ce prototype : pour réduire la latence il faudra un serveur TTS persistant ou l'API de streaming. Un écouteur micro en continu est disponible via le profil Docker `listener`, mais pas via la CLI native.

## Tests

    pip install -e '.[dev]'
    pytest -q

Les tests utilisent un faux embedder et un faux LLM ; ils vérifient le routage, la validation, les exécutions Python et les refus sans téléchargement de modèle. Les performances sur Raspberry Pi et UNO Q restent à mesurer sur matériel réel.
