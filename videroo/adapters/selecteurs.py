"""Selecteurs des interfaces web, isoles pour pouvoir être repares seuls.

Les sites changent. Ce fichier (ou données/selecteurs.json, qui le surcharge)
est le SEUL endroit a modifier quand ChatGPT ou Grok bougent leur interface.
Aucune API privee n'est utilisee : uniquement l'interface normale.
"""
from __future__ import annotations

import json
from copy import deepcopy

from .. import config

DEFAUT: dict = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "zone_saisie": [
            # Vérifié sur chatgpt.com : c'est celui-ci qui répond aujourd'hui.
            "div.ProseMirror[contenteditable='true']",
            "#prompt-textarea",
            "div[contenteditable='true'][id='prompt-textarea']",
            "div[contenteditable='true']",
            "textarea[data-id='root']",
        ],
        "bouton_envoyer": [
            # Vérifié : le bouton porte aria-label « Envoyer », sans data-testid.
            "button[aria-label*='Envoyer']",
            "button[aria-label*='Send']",
            "button[data-testid='send-button']",
        ],
        "bouton_stop": [
            "button[data-testid='stop-button']",
            "button[aria-label*='Arrêter']",
            "button[aria-label*='Stop']",
        ],
        # Deux champs cachés coexistent : celui qui accepte les images d'abord.
        "champ_fichier": [
            "input[type='file'][accept*='image']",
            "input[type='file']",
        ],
        "bouton_joindre": [
            "button[aria-label*='Joindre']",
            "button[aria-label*='Attach']",
            "button[data-testid='composer-plus-btn']",
        ],
        # Relevés sur chatgpt.com le 6 novembre 2026 : l'image générée vit dans une
        # galerie dédiée, porte l'alt « Image générée N », et son src est un blob:
        # propre à l'onglet. L'ancien conteneur data-message-author-role a disparu.
        "images_reponse": [
            "[data-testid='generated-image-gallery'] img",
            "[data-testid='generated-image-preview'] img",
            "img[alt^='Image générée']",
            "img[alt^='Generated image']",
            "div[data-message-author-role='assistant'] img[src*='oaiusercontent']",
            "main img[src^='blob:']",
        ],
        "signes_connexion": [
            "button[data-testid='login-button']",
            "a[href*='auth/login']",
            "button:has-text('Se connecter')",
            "button:has-text('Log in')",
        ],
        "signes_quota": [
            "text=limite",
            "text=limit reached",
            "text=réessayez plus tard",
            "text=You've reached",
        ],
    },
    "grok": {
        "url": "https://grok.com/",
        "zone_saisie": [
            "textarea[aria-label*='Ask']",
            "textarea[placeholder*='Grok']",
            "textarea[placeholder*='Saisir']",
            "textarea[placeholder*='commandes slash']",
            "div[contenteditable='true']",
            "textarea",
        ],
        "bouton_envoyer": [
            "button[type='submit']",
            "button[aria-label*='Submit']",
            "button[aria-label*='Envoyer']",
        ],
        # Deux champs caches coexistent sur la page « Imagine » : celui de la barre
        # de saisie (« Joindre des fichiers ») et celui du panneau lateral
        # (« Importer une image »). Prendre le second poserait l'image de depart
        # sur le plan de travail au lieu de l'attacher au message : la scene
        # s'animerait sans sa reference, et rien ne s'en plaindrait. L'ordre du DOM
        # les separe aujourd'hui, mais rien ne le garantit : on ecarte donc le
        # panneau explicitement, et le selecteur large ne reste qu'en secours.
        "champ_fichier": [
            "input[type='file']:not(aside *)",
            "input[type='file']",
        ],
        "bouton_joindre": [
            "button[aria-label*='Attach' i]",
            "button[aria-label*='Joindre' i]",
            "button[aria-label*='Ajouter' i]",
            "button[aria-label*='Upload' i]",
            "input[type='file']",
        ],
        # Volontairement limites a la barre de saisie. Un « Options » clique au
        # hasard dans la barre laterale ouvre un menu qui coupe les clics de TOUTE
        # la page : la scene echoue alors 45 s plus tard sur une zone de saisie
        # pourtant bien presente (constate le 7 novembre 2026).
        "ouvrir_reglages": [
            "form:has(textarea) button[aria-label*='Settings']",
            "form:has(textarea) button[aria-label*='Réglages']",
            "form:has(textarea) button[aria-label*='Options']",
            "form:has(textarea) button[aria-label*='Paramètres']",
            "form:has([contenteditable='true']) button[aria-label*='Settings']",
            "form:has([contenteditable='true']) button[aria-label*='Options']",
        ],
        "reglage_duree": [
            "select[aria-label*='Duration']",
            "select[aria-label*='Durée']",
            "select[name*='duration']",
            "[data-testid='duration-select']",
        ],
        "options_duree": [
            "button:has-text('{duree}s')",
            "button:has-text('{duree} s')",
            "[role='option']:has-text('{duree}s')",
            "[role='radio']:has-text('{duree}s')",
            "label:has-text('{duree}s')",
        ],
        "reglage_format": [
            "select[aria-label*='Aspect']",
            "button:has-text('9:16')",
            "[role='option']:has-text('9:16')",
        ],
        "videos_reponse": ["video", "a[href$='.mp4']", "source[src$='.mp4']"],
        # Grok travaille encore (une seconde prise peut encore arriver).
        "signes_travail": [
            "text=/Travaille depuis/i",
            "text=/Working for/i",
            "text=/Thinking for/i",
        ],
        "bouton_telecharger": [
            "button[aria-label*='Download']",
            "button[aria-label*='Télécharger']",
            "a[download]",
        ],
        "signes_connexion": [
            "a[href*='sign-in']",
            "a[href*='signin']",
            "button:has-text('Sign in with')",
            "button:has-text('Se connecter avec')",
        ],
        # « text=... » cherche une SOUS-chaine : « text=limit » trouvait
        # « Unlimited », et « text=upgrade » n'importe quel bouton d'abonnement.
        # Un compte declare epuise par erreur est mis de cote pour des jours,
        # alors qu'une detection ratee ne coute qu'un essai : on prefere donc
        # des phrases entieres, et on cite ce qu'on a vu dans le message.
        "signes_quota": [
            "text=out of credits",
            "text=run out of",
            "text=rate limit",
            "text=usage limit",
            "text=limite d'utilisation",
            "text=réessayez plus tard",
            "text=try again later",
        ],
    },
}


def _fichier_surcharge():
    return config.DOSSIER_DONNEES / "selecteurs.json"


def charger() -> dict:
    valeurs = deepcopy(DEFAUT)
    fichier = _fichier_surcharge()
    if fichier.exists():
        try:
            surcharge = json.loads(fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return valeurs
        for site, groupes in (surcharge or {}).items():
            if not isinstance(groupes, dict):
                continue
            valeurs.setdefault(site, {})
            for cle, liste in groupes.items():
                valeurs[site][cle] = liste
    return valeurs


def enregistrer_surcharge(donnees: dict) -> str:
    config.preparer_dossiers()
    fichier = _fichier_surcharge()
    fichier.write_text(json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(fichier)


def modele_surcharge() -> str:
    return json.dumps(DEFAUT, ensure_ascii=False, indent=2)
