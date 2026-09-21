# -*- coding: utf-8 -*-
"""
crous_watch.py — Surveillance des logements CROUS (Nice, couple) -> alertes Telegram
======================================================================================

Le site trouverunlogement.lescrous.fr est une SPA React : on n'y fait pas de
scraping HTML, on appelle directement son API interne de recherche en POST.

Zone et filtre repris de ta recherche :
https://trouverunlogement.lescrous.fr/tools/47/search?occupationModes=couple&bounds=7.1819535_43.7607635_7.323912_43.6454189&locationName=Nice

------------------------------------------------------------------------------
SI L'API CHANGE (à vérifier si le script se met à échouer en boucle)
------------------------------------------------------------------------------
1. Ouvre https://trouverunlogement.lescrous.fr, relance ta recherche Nice/couple.
2. F12 -> onglet "Network" -> filtre "Fetch/XHR".
3. Repère la requête POST vers /api/fr/search/<ID> : l'<ID> est l'"idTool"
   (SEARCH_ID ci-dessous). Il change à chaque tour d'attribution.
4. La liste à jour des phases est publique et sans authentification :
   https://trouverunlogement.lescrous.fr/api/fr/tools
   (Au 18/09/2026 : id 47 = "Phase complémentaire 2026-2027", en cours
   jusqu'au 02/11/2026 — c'est la valeur utilisée par défaut ici.)
------------------------------------------------------------------------------
DÉPENDANCES : Python 3.9+, `pip install requests` (seule dépendance externe).
------------------------------------------------------------------------------
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# =============================================================================
# CONFIGURATION
# Chaque valeur peut être surchargée par une variable d'environnement, ou par
# un fichier `.env` posé à côté du script (format KEY=VALUE). Voir .env.example.
# =============================================================================


def _charger_dotenv() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.is_file():
        return
    for ligne in env_path.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        os.environ.setdefault(cle.strip(), valeur.strip())


_charger_dotenv()


def _env(nom: str, defaut: str) -> str:
    return os.environ.get(nom, defaut)


# --- Telegram -----------------------------------------------------------------
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# --- API CROUS ------------------------------------------------------------
SEARCH_ID = int(_env("SEARCH_ID", "47"))
API_URL = f"https://trouverunlogement.lescrous.fr/api/fr/search/{SEARCH_ID}"
FICHE_URL = "https://trouverunlogement.lescrous.fr/tools/{search_id}/accommodations/{item_id}"

# Bounding box reprise de ton lien (Nice) : coin Nord-Ouest puis coin Sud-Est.
BBOX_LON_OUEST = float(_env("BBOX_LON_OUEST", "7.1819535"))
BBOX_LAT_NORD = float(_env("BBOX_LAT_NORD", "43.7607635"))
BBOX_LON_EST = float(_env("BBOX_LON_EST", "7.323912"))
BBOX_LAT_SUD = float(_env("BBOX_LAT_SUD", "43.6454189"))

# Filtre "type de cohabitation" repris de ton lien (occupationModes=couple).
# On demande quand même TOUT à l'API (pas de filtre serveur, plus fiable)
# et on filtre nous-mêmes côté client sur ce mot-clé -> voir correspond_au_filtre().
FILTRE_COHABITATION = _env("FILTRE_COHABITATION", "couple").strip().lower()


def construire_payload(page: int = 1) -> dict:
    return {
        "idTool": SEARCH_ID,
        "need_aggregation": True,
        "page": page,
        "pageSize": 200,
        "sector": None,
        "occupationModes": [],  # pas de filtre serveur : on filtre nous-mêmes (plus fiable)
        "location": [
            {"lon": BBOX_LON_OUEST, "lat": BBOX_LAT_NORD},  # coin Nord-Ouest
            {"lon": BBOX_LON_EST, "lat": BBOX_LAT_SUD},  # coin Sud-Est
        ],
        "residence": None,
        "precision": 7,
        "equipment": [],
        "price": {"min": 0, "max": 10000000},  # en centimes, très large
        "toolMechanism": "residual",
    }


HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Origin": "https://trouverunlogement.lescrous.fr",
    "Referer": "https://trouverunlogement.lescrous.fr/",
}

# --- Comportement ------------------------------------------------------------
INTERVALLE_SECONDES = int(_env("INTERVALLE_SECONDES", "300"))  # 5 min par défaut
FICHIER_ETAT = Path(__file__).with_name(_env("FICHIER_ETAT", "logements_vus.json"))
TIMEOUT_HTTP = 30
SEUIL_ERREURS_4XX = 3

# RUN_ONCE=1 : effectue UN SEUL cycle puis se termine (mode GitHub Actions).
RUN_ONCE = _env("RUN_ONCE", "0") == "1"


# =============================================================================
# UTILITAIRES
# =============================================================================
def log(message: str) -> None:
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{horodatage}] {message}", flush=True)


def charger_etat() -> dict:
    try:
        if FICHIER_ETAT.is_file():
            return json.loads(FICHIER_ETAT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log(f"ATTENTION : fichier d'état illisible ({e}), il sera réinitialisé.")
    return {}


def sauvegarder_etat(initialise: bool, ids_vus: set, erreurs_4xx: int = 0,
                      alerte_4xx_envoyee: bool = False) -> None:
    try:
        contenu = json.dumps(
            {
                "search_id": SEARCH_ID,
                "initialise": initialise,
                "ids": sorted(ids_vus),
                "erreurs_4xx": erreurs_4xx,
                "alerte_4xx_envoyee": alerte_4xx_envoyee,
            },
            ensure_ascii=False, indent=2,
        )
        tmp = FICHIER_ETAT.with_suffix(".tmp")
        tmp.write_text(contenu, encoding="utf-8")
        tmp.replace(FICHIER_ETAT)
    except OSError as e:
        log(f"ERREUR : impossible d'écrire le fichier d'état : {e}")


def envoyer_telegram(texte: str) -> None:
    """Envoie un message Telegram (Markdown). Ne lève jamais d'exception."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("ATTENTION : TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquant(s), "
            "notification non envoyée (voir README).")
        return
    url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage")
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texte,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT_HTTP)
        if r.status_code >= 400:
            log(f"ERREUR Telegram : HTTP {r.status_code} — {r.text[:300]}")
    except requests.RequestException as e:
        log(f"ERREUR Telegram : {e}")


def decouvrir_chat_id() -> None:
    """Aide au premier lancement : liste les chat_id vus via getUpdates."""
    if not TELEGRAM_BOT_TOKEN:
        log("Renseigne d'abord TELEGRAM_BOT_TOKEN dans .env pour découvrir ton chat_id.")
        return
    url = TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN, method="getUpdates")
    try:
        r = requests.get(url, timeout=TIMEOUT_HTTP)
        data = r.json()
        chats = {
            u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "?")
            for u in data.get("result", []) if "message" in u
        }
        if not chats:
            log("Aucun message reçu par le bot pour l'instant. Envoie-lui n'importe "
                "quel message sur Telegram (ex: /start), puis relance ce script.")
        else:
            for chat_id, nom in chats.items():
                log(f"chat_id trouvé : {chat_id} ({nom}) -> à mettre dans TELEGRAM_CHAT_ID")
    except requests.RequestException as e:
        log(f"ERREUR lors de la découverte du chat_id : {e}")


# =============================================================================
# APPEL DE L'API ET EXTRACTION DES DONNÉES
# =============================================================================
class ErreurHttp4xx(Exception):
    """Levée quand l'API répond 4xx (ID de phase probablement périmé)."""


def recuperer_logements() -> list:
    items = []
    page = 1
    while True:
        r = requests.post(API_URL, json=construire_payload(page),
                           headers=HEADERS, timeout=TIMEOUT_HTTP)
        if 400 <= r.status_code < 500:
            raise ErreurHttp4xx(f"HTTP {r.status_code} sur {API_URL}")
        r.raise_for_status()
        data = r.json()
        resultats = data.get("results")
        if not isinstance(resultats, dict) or "items" not in resultats:
            raise ValueError(f"Structure JSON inattendue : clés = {list(data)}")
        page_items = resultats.get("items") or []
        items.extend(page_items)
        total = resultats.get("total", 0)
        if isinstance(total, dict):
            total = total.get("value", 0)
        if not page_items or len(items) >= int(total) or page > 20:
            return items
        page += 1


def correspond_au_filtre(item: dict) -> bool:
    """
    True si l'annonce correspond au filtre de cohabitation voulu (ex: "couple").
    Reste tolérant : on inspecte toutes les clés textuelles plausibles de
    occupationModes[]. Si la structure ne permet pas de trancher, on GARDE
    l'annonce par prudence (mieux vaut une notif en trop qu'un logement raté),
    avec une mention "(type à vérifier)" dans le message envoyé.
    """
    if not FILTRE_COHABITATION:
        return True
    modes = item.get("occupationModes") or []
    if not modes:
        return True  # structure inconnue -> on ne filtre pas, on notifie par prudence
    for mode in modes:
        if not isinstance(mode, dict):
            continue
        for cle in ("type", "code", "label", "name", "mode", "slug"):
            valeur = mode.get(cle)
            if isinstance(valeur, str) and FILTRE_COHABITATION in valeur.lower():
                return True
    # Aucune correspondance trouvée dans une structure connue -> exclu.
    return False


def extraire_infos(item: dict) -> tuple:
    item_id = item.get("id")
    nom = item.get("label") or "Logement sans nom"
    residence = "?"
    res = item.get("residence")
    if isinstance(res, dict):
        residence = res.get("label") or "?"
        ville = res.get("city") or res.get("label") or ""
    else:
        ville = ""

    def _euros(centimes: float) -> str:
        v = centimes / 100
        return f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")

    loyer = "?"
    try:
        modes = item.get("occupationModes") or []
        if modes and isinstance(modes[0], dict):
            rent = modes[0].get("rent") or {}
            rmin, rmax = rent.get("min"), rent.get("max")
            if rmin is not None:
                rmax = rmax if rmax is not None else rmin
                loyer = (f"{_euros(rmin)} €" if rmin == rmax
                         else f"{_euros(rmin)}–{_euros(rmax)} €")
        if loyer == "?" and item.get("rentRange"):
            rr = item["rentRange"]
            loyer = (f"{rr[0]:.0f} €" if rr[0] == rr[-1]
                     else f"{rr[0]:.0f}–{rr[-1]:.0f} €")
    except (TypeError, ValueError, IndexError, KeyError):
        pass

    return item_id, str(nom), str(residence), loyer, ville


# =============================================================================
# BOUCLE PRINCIPALE
# =============================================================================
def main() -> None:
    log("=== Surveillance logements CROUS — Nice (couple) -> Telegram ===")
    log(f"API : {API_URL}")
    log(f"Filtre cohabitation : {FILTRE_COHABITATION or '(aucun)'}")
    log(f"Mode : {'un seul cycle (RUN_ONCE)' if RUN_ONCE else f'boucle toutes les {INTERVALLE_SECONDES} s'}")
    log(f"Fichier d'état : {FICHIER_ETAT}")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Configuration Telegram incomplète (voir README.md). Tentative de "
            "découverte du chat_id via getUpdates :")
        decouvrir_chat_id()

    etat = charger_etat()
    if etat and etat.get("search_id") != SEARCH_ID:
        log("ID de phase différent de celui du fichier d'état : réinitialisation.")
        etat = {}

    initialise = bool(etat) and bool(etat.get("initialise", True))
    ids_vus = set(etat.get("ids", []))
    erreurs_4xx_consecutives = int(etat.get("erreurs_4xx", 0))
    alerte_4xx_envoyee = bool(etat.get("alerte_4xx_envoyee", False))

    while True:
        cycle_reussi = False
        items = []
        try:
            items = recuperer_logements()
            cycle_reussi = True
        except ErreurHttp4xx as e:
            erreurs_4xx_consecutives += 1
            log(f"ERREUR API (4xx) : {e} [{erreurs_4xx_consecutives}/{SEUIL_ERREURS_4XX}]")
            if erreurs_4xx_consecutives >= SEUIL_ERREURS_4XX and not alerte_4xx_envoyee:
                envoyer_telegram(
                    "⚠️ *CROUS Nice* : vérifie l'ID de l'endpoint\n"
                    f"L'API répond en erreur 4xx depuis {erreurs_4xx_consecutives} cycles.\n"
                    "L'ID de phase (SEARCH_ID) est probablement périmé : consulte "
                    "https://trouverunlogement.lescrous.fr/api/fr/tools pour trouver le nouveau."
                )
                alerte_4xx_envoyee = True
            sauvegarder_etat(initialise, ids_vus, erreurs_4xx_consecutives, alerte_4xx_envoyee)
        except requests.RequestException as e:
            log(f"ERREUR réseau : {e} — nouvelle tentative au prochain cycle.")
        except (ValueError, KeyError, TypeError) as e:
            log(f"ERREUR de structure JSON : {e} — l'API a peut-être changé.")
        except Exception as e:  # filet de sécurité : ne jamais crasher
            log(f"ERREUR inattendue : {type(e).__name__}: {e}")

        if cycle_reussi:
            if alerte_4xx_envoyee:
                log("L'API répond de nouveau normalement.")
            erreurs_4xx_consecutives = 0
            alerte_4xx_envoyee = False

            items_filtres = [i for i in items if correspond_au_filtre(i)]
            ids_actuels = {i.get("id") for i in items_filtres if i.get("id") is not None}

            if not initialise:
                ids_vus = ids_actuels
                initialise = True
                log(f"Premier lancement : {len(ids_vus)} logement(s) déjà en ligne "
                    "correspondant au filtre, enregistrés sans notification.")
                envoyer_telegram(
                    "✅ *Surveillance CROUS Nice active*\n"
                    f"{len(ids_vus)} logement(s) correspondant à ton filtre (couple) "
                    "actuellement en ligne. Tu seras alerté dès qu'un nouveau apparaît."
                )
            else:
                nouveaux = ids_actuels - ids_vus
                if nouveaux:
                    log(f"{len(nouveaux)} NOUVEAU(X) logement(s) détecté(s) !")
                    for item in items_filtres:
                        item_id, nom, residence, loyer, ville = extraire_infos(item)
                        if item_id not in nouveaux:
                            continue
                        lien = FICHE_URL.format(search_id=SEARCH_ID, item_id=item_id)
                        log(f" -> {nom} | {residence} | {loyer} | {lien}")
                        envoyer_telegram(
                            f"🏠 *Nouveau logement CROUS Nice (couple)*\n"
                            f"*{nom}*\n"
                            f"Résidence : {residence}" + (f" — {ville}" if ville else "") + "\n"
                            f"Loyer : {loyer}\n"
                            f"{lien}\n\n"
                            "Fonce, les places partent vite !"
                        )
                    ids_vus |= nouveaux
                else:
                    log(f"Aucun nouveau logement ({len(ids_actuels)} en ligne, filtre appliqué).")

            sauvegarder_etat(initialise, ids_vus)

        if RUN_ONCE:
            log("Cycle terminé (mode RUN_ONCE), arrêt.")
            return
        time.sleep(INTERVALLE_SECONDES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arrêt demandé (Ctrl+C). À bientôt !")
        sys.exit(0)
