# Surveillance logements CROUS — Nice (couple) → Telegram

Surveille [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr) sur la zone
et le filtre de ta recherche (Nice, cohabitation "couple") et t'envoie un message Telegram dès
qu'un nouveau logement apparaît.

Le script n'utilise **aucun scraping HTML** : il appelle directement l'API interne
JSON que le site utilise (comme le fait ton navigateur), donc pas de dépendance lourde
(pas de Selenium/Playwright).

## 1. Créer ton bot Telegram (2 minutes)

1. Ouvre Telegram, cherche **@BotFather**, envoie `/newbot`, choisis un nom et un
   identifiant (doit finir par "bot"). Il te donne un **token** du type
   `123456789:AAAA...` → c'est ton `TELEGRAM_BOT_TOKEN`.
2. Cherche ton bot par son identifiant et envoie-lui n'importe quel message
   (ex : `/start`) — Telegram n'autorise un bot à écrire qu'à quelqu'un qui lui
   a déjà parlé.
3. Récupère ton `chat_id` de l'une de ces deux façons :
   - Lance le script une première fois sans `TELEGRAM_CHAT_ID` renseigné : il
     appelle automatiquement `getUpdates` et t'affiche le chat_id trouvé.
   - Ou cherche **@userinfobot** sur Telegram et envoie-lui `/start`, il te donne
     directement ton id.

## 2. Configuration

```bash
cp .env.example .env
# puis édite .env : TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID
```

La zone (Nice) et le filtre (couple) sont déjà réglés par défaut d'après ton lien de
recherche — rien d'autre à changer sauf si tu veux élargir/réduire la zone.

## 3. Lancer en local

```bash
pip install -r requirements.txt
python crous_watch.py
```

Le script boucle toutes les 5 minutes (réglable via `INTERVALLE_SECONDES`). Premier
lancement : il enregistre les logements déjà en ligne **sans** te spam-notifier dessus,
puis t'alerte uniquement sur les nouveautés.

Pour le laisser tourner en fond sur ta machine (Windows), tu peux créer une tâche
planifiée qui exécute `python crous_watch.py`, ou simplement laisser le terminal ouvert.

## 4. Tourner 24/7 gratuitement, sans garder ton PC allumé (recommandé)

Le dépôt inclut un workflow GitHub Actions (`.github/workflows/surveillance.yml`) qui
lance un cycle toutes les ~5 minutes, même PC éteint :

1. Crée un dépôt GitHub (public de préférence : minutes d'Actions illimitées et
   gratuites ; en privé le quota gratuit — 2000 min/mois — serait vite dépassé à ce
   rythme). Rien de sensible n'est publié : le token reste dans les *secrets*.
2. Pousse tous ces fichiers dedans (**sauf** `.env`, déjà ignoré si tu ajoutes un
   `.gitignore` avec `.env` dedans).
3. Sur GitHub → *Settings* → *Secrets and variables* → *Actions* → *New repository
   secret*, ajoute :
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Onglet *Actions* → workflow "Surveillance CROUS Nice" → *Run workflow* pour lancer
   un premier cycle à la main. Il enregistre l'existant (juste une notif de
   confirmation) et committe `logements_vus.json` — c'est comme ça que l'état est
   conservé entre deux cycles.

## Points d'attention

- **Délai réel** : les cron GitHub sont exécutés "au mieux" ; un cycle toutes les
  5 min planifiées prend souvent 5 à 15 min réelles en pratique.
- **Repos inactifs** : GitHub désactive les workflows planifiés après 60 jours sans
  activité (un email prévient ; il suffit de cliquer "Enable" pour réactiver). Les
  commits d'état comptent comme de l'activité, donc ça ne devrait pas arriver tant
  que le workflow tourne.
- **Changement de phase** : l'ID `47` ("Phase complémentaire 2026-2027") est valable
  jusqu'au 02/11/2026. Si l'API se met à répondre en erreur 4xx en boucle, c'est
  presque toujours cet ID qui est devenu périmé — le script t'envoie alors une
  notification Telegram automatique te demandant de vérifier
  https://trouverunlogement.lescrous.fr/api/fr/tools pour trouver le nouveau, à
  reporter dans `SEARCH_ID` (variable d'env ou secret GitHub).
- **Filtre "couple"** : le filtre est appliqué côté script (pas côté API, plus
  fiable) en inspectant la structure de chaque annonce. Si jamais la structure de
  l'API change et que le filtre ne reconnaît plus le type de logement, le script
  préfère notifier quand même (plutôt que rater un logement) : dans ce cas, vérifie
  simplement le lien avant de foncer.
- **Ne fais pas tourner les deux modes en même temps** (local + Actions) avec le
  même bot, sinon notifications en double.
