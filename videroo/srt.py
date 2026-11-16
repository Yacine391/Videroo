"""Lecture et ecriture de sous-titres SRT."""
from __future__ import annotations

import re
from pathlib import Path

RE_TEMPS = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def secondes_vers_srt(valeur: float) -> str:
    valeur = max(0.0, float(valeur))
    heures = int(valeur // 3600)
    minutes = int((valeur % 3600) // 60)
    secondes = int(valeur % 60)
    milli = int(round((valeur - int(valeur)) * 1000))
    if milli == 1000:
        milli = 999
    return f"{heures:02d}:{minutes:02d}:{secondes:02d},{milli:03d}"


def ecrire(lignes: list[dict], cible: str | Path) -> str:
    """lignes : [{'debut': float, 'fin': float, 'texte': str}, ...]"""
    Path(cible).parent.mkdir(parents=True, exist_ok=True)
    morceaux: list[str] = []
    for i, ligne in enumerate(lignes, start=1):
        texte = (ligne.get("texte") or "").strip()
        if not texte:
            continue
        morceaux.append(
            f"{i}\n{secondes_vers_srt(ligne['debut'])} --> "
            f"{secondes_vers_srt(ligne['fin'])}\n{texte}\n"
        )
    contenu = "\n".join(morceaux)
    Path(cible).write_text(contenu, encoding="utf-8")
    return str(cible)


def lire(contenu: str) -> list[dict]:
    lignes: list[dict] = []
    blocs = re.split(r"\n\s*\n", contenu.replace("\r\n", "\n").replace("\r", "\n"))
    for bloc in blocs:
        m = RE_TEMPS.search(bloc)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        debut = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / (1000 if g[3] > 99 else 100 if g[3] > 9 else 10)
        fin = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / (1000 if g[7] > 99 else 100 if g[7] > 9 else 10)
        apres = bloc[m.end() :].strip("\n")
        texte = "\n".join(l for l in apres.split("\n") if l.strip())
        if texte:
            lignes.append({"debut": debut, "fin": fin, "texte": texte.strip()})
    return lignes


def depuis_scenes(scenes: list[dict], vitesse: float = 1.0) -> list[dict]:
    """Construit des timings a partir des dialogues du pack (point de départ editable)."""
    vitesse = vitesse or 1.0
    sortie: list[dict] = []
    curseur = 0.0
    for scene in scenes:
        duree_utile = float(scene.get("duree_utile") or scene.get("duree") or 0)
        duree_utile = duree_utile / vitesse
        dialogues = scene.get("dialogues") or []
        if not dialogues:
            curseur += duree_utile
            continue
        part = duree_utile / len(dialogues)
        for i, d in enumerate(dialogues):
            debut = curseur + i * part
            sortie.append(
                {
                    "debut": round(debut, 3),
                    "fin": round(debut + part, 3),
                    "texte": (d.get("texte") or "").strip(),
                }
            )
        curseur += duree_utile
    return sortie
