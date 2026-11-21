# Hestia Core — Rust

Le serveur `hestia-core` est la passerelle principale du projet.
Il remplace entièrement l'ancienne API Python. Les scripts des outils,
le `listener` et Whisper STT peuvent toujours utiliser Python 3,
mais leur orchestration est effectuée en Rust.

Fonctions : API HTTP Axum, routage d'exemples d'outils avec FastEmbed,
moteur de workflows YAML (extraction JSON, programmes, templates,
réponses), tableau de bord d'administration, gestion des secrets de
plugins et planification de workflows.

Un outil est déclaré dans `tools/<domaine>/<plugin>/tool.yaml`.
L'étape recommandée pour les programmes est `exec.call` :
elle transmet en entrée standard un JSON contenant `parameters` et
`context`, puis attend un objet JSON sur stdout. `python.call`
reste uniquement pris en charge pour la compatibilité d'anciens outils.

Démarrer tout le système avec `docker compose up -d --build`, puis
ouvrir http://localhost:8765/ et consulter [docs/docker.md](../docs/docker.md).
Sur une machine sans microphone : `docker compose up -d --scale listener=0`.

Les secrets sont sauvegardés localement en clair dans le volume Docker
`hestia_data` (permissions de fichier 0600). Le serveur n'est pas une
sandbox de scripts et ne doit pas être exposé directement sur Internet.
