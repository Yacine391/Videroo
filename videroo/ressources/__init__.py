"""Textes de référence conservés tels quels (jamais réécrits par l'application)."""
from __future__ import annotations

import hashlib
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent
PROMPT_REGLES_GROK = DOSSIER / "prompt_regles_grok.txt"
PROMPT_MINIATURE = DOSSIER / "prompt_miniature.txt"

#: Le metaprompt livre avec l'application, pour chaque famille d'histoires.
#: Celui des fruits reste celui que tu colles toi-meme dans l'onglet Metaprompt :
#: il n'est pas embarque ici, il t'appartient.
METAPROMPTS_LIVRES = {
    "chronique": DOSSIER / "metaprompt_chronique.txt",
    "bebe": DOSSIER / "metaprompt_bebe.txt",
}


def lire(chemin: Path) -> str:
    return chemin.read_text(encoding="utf-8") if chemin.exists() else ""


def regles_grok() -> str:
    return lire(PROMPT_REGLES_GROK)


def metaprompt_livre(type_histoire: str) -> str:
    """Le metaprompt fourni d'origine pour cette famille, ou chaine vide.

    Il sert de point de depart : des que tu enregistres le tien dans l'onglet
    Metaprompt, c'est le tien qui est utilise.
    """
    chemin = METAPROMPTS_LIVRES.get(type_histoire or "")
    return lire(chemin) if chemin else ""


def modele_miniature() -> str:
    """Le prompt de miniature de Yacine, conservé mot pour mot.

    Deux repères seulement y sont remplacés : {titre} et {episode}.
    """
    return lire(PROMPT_MINIATURE)


#: Guillemets décoratifs que les packs V6 collent autour du titre.
GUILLEMETS = "«»\"\u201c\u201d\u00ab\u00bb \t"


def titre_nu(titre: str) -> str:
    """Le titre sans les guillemets qui l'entourent déjà.

    Le pack V6 écrit « IL A TOUT DONNÉ À SA PETITE SŒUR », guillemets compris,
    et le modèle de miniature en ajoute les siens : le prompt demandait d'écrire
    « « IL A TOUT DONNÉ… » » dans l'image, guillemets doublés. Comme il dit
    « écris le titre exactement ainsi », ChatGPT les aurait dessinés.
    """
    return (titre or "").strip().strip(GUILLEMETS).strip()


def prompt_miniature(titre: str, episode: int | str = 1) -> str:
    modele = modele_miniature()
    if not modele:
        return ""
    return modele.replace("{titre}", titre_nu(titre)).replace(
        "{episode}", str(episode or 1)
    )


def empreinte_texte(texte: str) -> str:
    return hashlib.sha256((texte or "").encode("utf-8")).hexdigest()[:12]
