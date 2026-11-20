# Hestia Rust Core et interface web

Cette migration est sur la branche `feat/rust-core-plugin-dashboard`, en PR brouillon.
Le serveur Rust remplace **l'API Hestia** ; le LLM llama.cpp, Whisper, Pocket TTS
et le microphone `listener` restent les services éprouvés de Compose.

## Démarrage (opt-in, ne modifie pas ton installation Python)

```sh
git fetch origin
git switch feat/rust-core-plugin-dashboard
cp .env.example .env # seulement si ton .env n'existe pas déjà
docker compose -f compose.yaml -f compose.rust.yaml --profile listener up -d --build
# ou : ./scripts/up-rust.sh
docker compose -f compose.yaml -f compose.rust.yaml ps
```

La première initialisation télécharge `multilingual-e5-small` (FastEmbed/ONNX).
La configuration et le modèle sont conservés dans le volume Docker `hestia_data`.
L'interface web : **http://localhost:8765/**. La route
`/health/ready` vérifie les moteurs indépendants.

Ne pas exposer l'interface directement sur Internet. Elle est liée à
`127.0.0.1` par défaut, avec la possibilité de définir une forte clé
`HESTIA_API_KEY` et d'utiliser le champ « Clé d'accès » du tableau de bord
si un accès LAN est volontairement activé. Les secrets des plugins sont
conservés **en clair dans un fichier local** protégé en lecture (0600), jamais
renvoyés par l'API d'administration : sécuriser les sauvegardes du volume.

## Plugins configurables

Les plugins sont dans `tools/<domaine>/<nom>/tool.yaml` et leur programme.
La page **Plugins** permet d'activer/désactiver chaque outil, de saisir la
clé Brave Search ou les accès MPD, ou de définir la ville météo par défaut.
Seuls les outils actifs sont indexés dans le moteur d'embeddings.

| Outil | Accès | Capacités |
| --- | --- | --- |
| `math.calculate` | aucun | calcul scientifique, pas d'eval arbitraire |
| `weather.current` / `weather.forecast` | Open-Meteo, sans clé | météo/ prévisions |
| `web.search` | clé Brave Search | recherche avec liens sources |
| `music.search` | iTunes Search, sans clé | catalogue de titres, sans lecture |
| `music.radio` | Radio Browser, sans clé | recherche de stations |
| `music.play_radio` | lecteur MPD existant sur le réseau | lecture de radio |
| `knowledge.wikipedia` | Wikipédia, sans clé | recherche d'articles |
| `finance.exchange` | Frankfurter, sans clé | taux de change indicatifs |
| `random.dice` | aucun | lancer de dés |

**Musique :** le cœur Hestia ne lit pas directement les flux musicaux.
L'outil `music.play_radio` transmet une station à un serveur MPD que tu dois
installer et configurer séparément. Il faut fournir son `MPD_HOST` dans
l'interface, ainsi que son port et son mot de passe si nécessaire.
Une recherche de musique ne déclenche pas une lecture.

## Outils universels

Un outil expose son comportement dans un manifeste YAML. Le step
`exec.call` utilise `command: python3` ou `command: bash` (ou un exécutable
`./programme` compilé en Rust/C), `argv` et `arguments`. Le programme reçoit
sur stdin un JSON `{"parameters":{...},"context":{...}}` et retourne un objet
JSON sur stdout. Les prompts `ai.extract` et `ai.respond` sont propres à
l'outil. Les scripts ne sont **pas sandboxés** : n'installer que des sources
de confiance, notamment lorsqu'ils reçoivent une clé API.

Exemple d'étapes YAML :

```yaml
steps:
  - id: extract
    uses: ai.extract
    with:
      prompt: Extrais le nom de la ville.
  - id: execute
    uses: exec.call
    with:
      command: python3
      argv: [main.py]
      arguments: ${{ steps.extract.output }}
  - id: respond
    uses: ai.respond
    with:
      data: ${{ steps.execute.output }}
      prompt: Résume uniquement le résultat réel.
```

## Récurrences

La page « Récurrences » permet de créer des tâches utilisant un outil marqué
`schedule_allowed: true`. Une récurrence contient : identifiant, outil,
cron à **5 champs UTC**, paramètres JSON préremplis, état activé/désactivé.
Exemple : `0 7 * * *` = tous les jours à 07:00 UTC. Les outils demandant
confirmation et les outils inconnus/désactivés ne peuvent pas être programmés.

Le scheduler déclenche directement le workflow avec ses paramètres : pas
besoin de redemander au LLM quel outil utiliser. Les exécutions sont
journalisées dans le tableau de bord (200 dernières), sans notification
push ni lecture vocale automatique pour cette version.

## Compatibilité

- Les routes HTTP existantes sont conservées : `/v1/chat`, `/v1/route`,
  `/v1/tools`, `/v1/audio/ask`, `/v1/audio/speak`,
  `/v1/audio/transcribe` et `/health/ready`.
- La conversion des nombres français avant Pocket TTS existe en Rust pour
  les cas fréquents (entiers, décimaux, heures, températures). Les cas
  particuliers et la qualité de synthèse demandent encore des essais.
- La nouvelle couche n'a pas encore été validée en conversation vocale
  réelle et les performances mémoire sur Raspberry Pi sont à mesurer.
- Si une incompatibilité survient, redémarre le service Python original :
  `docker compose -f compose.yaml up -d --build hestia`.
