"""Réglages modifiables depuis l'espace Admin, persistés dans la base.

Chaque réglage a une valeur par défaut issue de `config`. La lecture ne casse
jamais : si la base n'est pas prête, on renvoie la valeur par défaut.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import config, db

CHAMPS: list[dict[str, Any]] = [
    {
        "cle": "mode_defaut",
        "groupe": "Génération",
        "libelle": "Mode par défaut",
        "type": "choix",
        "choix": [
            ("auto", "auto — l'application pilote ChatGPT et Grok"),
            ("assiste", "assisté — elle prépare, tu lances et tu importes"),
            ("demo", "démo — fichiers factices, aucun quota"),
        ],
        "defaut": "auto",
        "aide": "Mode proposé par défaut au lancement d'une génération.",
    },
    {
        "cle": "delai_image",
        "groupe": "Génération",
        "libelle": "Attente maximale d'une image (secondes)",
        "type": "entier",
        "defaut": config.DELAI_ATTENTE_IMAGE,
        "min": 60,
        "max": 3600,
        "aide": "Délai dépassé = tâche « à vérifier ». Aucune relance automatique.",
    },
    {
        "cle": "delai_video",
        "groupe": "Génération",
        "libelle": "Attente maximale d'une vidéo (secondes)",
        "type": "entier",
        "defaut": config.DELAI_ATTENTE_VIDEO,
        "min": 60,
        "max": 7200,
        "aide": "Grok peut être long. Au-delà, la tâche passe en « à vérifier ».",
    },
    {
        "cle": "durees_grok",
        "groupe": "Génération",
        "libelle": "Durées acceptées par Grok (secondes, séparées par des virgules)",
        "type": "texte",
        "defaut": ",".join(str(d) for d in config.DUREES_GROK),
        "aide": "Une scène dont la durée n'est pas dans cette liste est refusée avant envoi.",
    },
    {
        "cle": "tolerance_duree",
        "groupe": "Génération",
        "libelle": "Écart de durée toléré sur une vidéo (secondes)",
        "type": "decimal",
        "defaut": 1.0,
        "aide": "Au-delà, la scène est marquée « durée non conforme » et tu peux l'ajuster.",
    },
    {
        "cle": "exiger_reglage_duree",
        "groupe": "Génération",
        "libelle": "Refuser d'envoyer à Grok si la durée n'a pas pu être réglée",
        "type": "booleen",
        "defaut": False,
        "aide": "Plus prudent pour tes quotas, mais bloque si Grok cache son contrôle de durée.",
    },
    {
        "cle": "url_chatgpt",
        "groupe": "Comptes",
        "libelle": "Adresse de ChatGPT",
        "type": "texte",
        "defaut": config.URL_CHATGPT,
        "aide": "Change-la si tu utilises une autre adresse ou un chat précis.",
    },
    {
        "cle": "url_grok",
        "groupe": "Comptes",
        "libelle": "Adresse de Grok",
        "type": "texte",
        "defaut": config.URL_GROK,
        "aide": "Mets ici l'adresse de ton chat de règles si tu veux y revenir directement.",
    },
    {
        "cle": "strategie_rotation",
        "groupe": "Comptes",
        "libelle": "Quel compte utiliser en premier",
        "type": "choix",
        "choix": [
            ("recharge_proche", "celui qui se recharge le plus tôt — on dépense d'abord le quota le plus périssable"),
            ("ordre_manuel", "l'ordre de la liste — tu décides avec les flèches"),
        ],
        "defaut": "recharge_proche",
        "aide": "Un quota qui se recharge dans 20 h vaut moins qu'un quota qui attend 5 jours.",
    },
    {
        "cle": "quota_reset_heures",
        "groupe": "Comptes",
        "libelle": "Un compte à court de quota redevient disponible après (heures)",
        "type": "decimal",
        "defaut": 12.0,
        "aide": "Passé ce délai, la rotation le réessaie. Tu peux aussi le réarmer à la main.",
    },
    {
        "cle": "navigateur_mode",
        "groupe": "Comptes",
        "libelle": "Comment Videroo accède au navigateur",
        "type": "choix",
        "choix": [
            ("profil_dedie", "profil dédié — fenêtre séparée, connexion à refaire dedans"),
            ("chrome_existant", "Chrome existant — se branche sur ton Chrome déjà connecté"),
        ],
        "defaut": "profil_dedie",
        "aide": "Choisis « Chrome existant » si Google refuse la connexion dans la fenêtre dédiée.",
    },
    {
        "cle": "cdp_url",
        "groupe": "Comptes",
        "libelle": "Adresse de débogage de ton Chrome",
        "type": "texte",
        "defaut": "http://127.0.0.1:9222",
        "aide": "Utilisée par le mode « Chrome existant ». Laisse telle quelle sauf besoin précis.",
    },
    {
        "cle": "navigateur_affichage",
        "groupe": "Comptes",
        "libelle": "Comment le navigateur travaille pendant les générations",
        "type": "choix",
        "choix": [
            ("ecran_virtuel", "écran à part — aucune fenêtre sur ton bureau, vrai Chrome (recommandé)"),
            ("hors_ecran", "hors écran — suffit sous X11, repris par GNOME sous Wayland"),
            ("invisible", "sans fenêtre — le plus léger, mais Cloudflare bloque ChatGPT"),
            ("fenetre", "fenêtre visible — pour regarder ce qui se passe"),
        ],
        "defaut": "ecran_virtuel",
        "aide": "La fenêtre s'ouvre toujours pour te connecter à un compte, quel que soit ce choix.",
    },
    {
        "cle": "export_largeur",
        "groupe": "Export",
        "libelle": "Largeur de l'export (pixels)",
        "type": "entier",
        "defaut": config.EXPORT_LARGEUR,
        "min": 240,
        "max": 3840,
        "aide": "1080 pour du vertical TikTok.",
    },
    {
        "cle": "export_hauteur",
        "groupe": "Export",
        "libelle": "Hauteur de l'export (pixels)",
        "type": "entier",
        "defaut": config.EXPORT_HAUTEUR,
        "min": 240,
        "max": 3840,
        "aide": "1920 pour du vertical TikTok.",
    },
    {
        "cle": "export_fps",
        "groupe": "Export",
        "libelle": "Images par seconde",
        "type": "entier",
        "defaut": config.EXPORT_FPS,
        "min": 12,
        "max": 60,
        "aide": "Monter au-dessus de la source duplique des images, sans gain réel.",
    },
    {
        "cle": "export_crf",
        "groupe": "Export",
        "libelle": "Qualité H.264 (CRF, plus bas = meilleure qualité)",
        "type": "entier",
        "defaut": 20,
        "min": 14,
        "max": 30,
        "aide": "18 à 22 est une bonne plage. Plus bas = fichier plus gros.",
    },
    {
        "cle": "export_preset",
        "groupe": "Export",
        "libelle": "Vitesse d'encodage",
        "type": "choix",
        "choix": [
            ("veryfast", "veryfast — rapide"),
            ("medium", "medium — équilibré"),
            ("slow", "slow — plus long, fichier plus petit"),
        ],
        "defaut": "medium",
        "aide": "N'influence pas la qualité visible, seulement la durée et la taille.",
    },
    {
        "cle": "duree_cible_min",
        "groupe": "Export",
        "libelle": "Durée souhaitée minimum (secondes)",
        "type": "decimal",
        "defaut": config.DUREE_CIBLE_MIN,
        "aide": "Un export plus court déclenche un avertissement.",
    },
    {
        "cle": "duree_cible_max",
        "groupe": "Export",
        "libelle": "Durée souhaitée maximum (secondes)",
        "type": "decimal",
        "defaut": config.DUREE_CIBLE_MAX,
        "aide": "Sert de repère à l'import du pack.",
    },
    {
        "cle": "musique_volume",
        "groupe": "Export",
        "libelle": "Volume par défaut de la musique (0 à 1)",
        "type": "decimal",
        "defaut": 0.15,
        "aide": "0.15 laisse les dialogues au premier plan.",
    },
]

PAR_CLE = {c["cle"]: c for c in CHAMPS}
GROUPES = ["Génération", "Comptes", "Export"]


def _defaut(cle: str) -> Any:
    champ = PAR_CLE.get(cle)
    return champ["defaut"] if champ else None


def lire(cle: str, defaut: Any = None) -> Any:
    try:
        valeur = db.reglage(cle)
    except sqlite3.Error:
        valeur = None
    if valeur is None or valeur == "":
        return _defaut(cle) if defaut is None else defaut
    return valeur


def lire_int(cle: str, defaut: int | None = None) -> int:
    try:
        return int(str(lire(cle, defaut)).strip())
    except (TypeError, ValueError):
        return int(defaut if defaut is not None else (_defaut(cle) or 0))


def lire_float(cle: str, defaut: float | None = None) -> float:
    try:
        return float(str(lire(cle, defaut)).replace(",", ".").strip())
    except (TypeError, ValueError):
        return float(defaut if defaut is not None else (_defaut(cle) or 0.0))


def lire_bool(cle: str, defaut: bool | None = None) -> bool:
    valeur = lire(cle, defaut)
    if isinstance(valeur, bool):
        return valeur
    return str(valeur).strip().lower() in ("1", "true", "oui", "on", "vrai")


def affichage_navigateur() -> str:
    """ecran_virtuel, fenetre, hors_ecran ou invisible."""
    valeur = str(lire("navigateur_affichage"))
    connus = ("ecran_virtuel", "fenetre", "hors_ecran", "invisible")
    return valeur if valeur in connus else "ecran_virtuel"


def durees_grok() -> tuple[int, ...]:
    brut = str(lire("durees_grok"))
    valeurs = []
    for morceau in brut.replace(";", ",").split(","):
        morceau = morceau.strip()
        if morceau.isdigit():
            valeurs.append(int(morceau))
    return tuple(valeurs) or config.DUREES_GROK


class ValeurRefusee(ValueError):
    pass


def definir(cle: str, valeur: Any) -> Any:
    """Valide puis enregistre. Lève ValeurRefusee avec un message affichable."""
    champ = PAR_CLE.get(cle)
    if champ is None:
        raise ValeurRefusee(f"Réglage inconnu : {cle}")
    brut = "" if valeur is None else str(valeur).strip()

    if champ["type"] == "booleen":
        propre = "1" if brut.lower() in ("1", "true", "oui", "on", "vrai") else "0"
    elif champ["type"] == "entier":
        if not brut.lstrip("-").isdigit():
            raise ValeurRefusee(f"« {champ['libelle']} » attend un nombre entier.")
        nombre = int(brut)
        if "min" in champ and nombre < champ["min"]:
            raise ValeurRefusee(
                f"« {champ['libelle']} » ne peut pas descendre sous {champ['min']}."
            )
        if "max" in champ and nombre > champ["max"]:
            raise ValeurRefusee(
                f"« {champ['libelle']} » ne peut pas dépasser {champ['max']}."
            )
        propre = str(nombre)
    elif champ["type"] == "decimal":
        try:
            nombre = float(brut.replace(",", "."))
        except ValueError as exc:
            raise ValeurRefusee(f"« {champ['libelle']} » attend un nombre.") from exc
        propre = str(nombre)
    elif champ["type"] == "choix":
        valides = [c[0] for c in champ["choix"]]
        if brut not in valides:
            raise ValeurRefusee(
                f"« {champ['libelle']} » : valeur inattendue. Choix possibles : "
                + ", ".join(valides)
            )
        propre = brut
    else:
        if cle == "durees_grok":
            morceaux = [m.strip() for m in brut.replace(";", ",").split(",") if m.strip()]
            if not morceaux or not all(m.isdigit() for m in morceaux):
                raise ValeurRefusee(
                    "Les durées Grok doivent être des nombres entiers séparés par des "
                    "virgules, par exemple : 6,10,15"
                )
            propre = ",".join(morceaux)
        elif cle.startswith("url_"):
            if brut and not brut.startswith(("http://", "https://")):
                raise ValeurRefusee(f"« {champ['libelle']} » doit commencer par https://")
            propre = brut
        else:
            propre = brut

    db.definir_reglage(cle, propre)
    return propre


def tous() -> list[dict]:
    """Les champs avec leur valeur courante, pour l'affichage."""
    sortie = []
    for champ in CHAMPS:
        valeur = lire(champ["cle"])
        sortie.append(
            {
                **champ,
                "valeur": valeur,
                "valeur_bool": lire_bool(champ["cle"]) if champ["type"] == "booleen" else None,
                "par_defaut": str(valeur) == str(champ["defaut"]),
            }
        )
    return sortie


def reinitialiser() -> int:
    for champ in CHAMPS:
        db.definir_reglage(champ["cle"], "")
    return len(CHAMPS)
