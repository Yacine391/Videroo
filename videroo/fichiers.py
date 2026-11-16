"""Noms de fichiers stables + empreinte anti-mélange entre projets."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from . import config


def slug(texte: str, defaut: str = "sans-nom") -> str:
    texte = unicodedata.normalize("NFD", texte or "")
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    texte = re.sub(r"[^A-Za-z0-9]+", "-", texte).strip("-").lower()
    # Un nom de fichier reste court : un titre mal lu ne doit pas faire planter le disque.
    return texte[:60].strip("-") or defaut


def creer_dossier_projet(projet_id: int, nom: str) -> Path:
    dossier = config.DOSSIER_PROJETS / f"{projet_id:04d}-{slug(nom)}"
    for sous in (
        "references", "scenes", "miniature", "export", "export/scenes", "imports",
        "musique",
    ):
        (dossier / sous).mkdir(parents=True, exist_ok=True)
    ecrire_empreinte(dossier, projet_id, nom)
    return dossier


def ecrire_empreinte(dossier: Path, projet_id: int, nom: str) -> None:
    (dossier / "projet.json").write_text(
        json.dumps({"projet_id": projet_id, "nom": nom}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def lire_empreinte(dossier: str | Path) -> dict:
    fichier = Path(dossier) / "projet.json"
    if not fichier.exists():
        return {}
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class MelangeDeProjets(RuntimeError):
    """Lève si un chemin ne correspond pas au projet attendu."""


def verifier_appartenance(chemin: str | Path, projet_id: int, dossier_projet: str | Path) -> Path:
    """Refuse tout chemin situe hors du dossier du projet attendu.

    C'est le garde-fou qui empêche qu'une référence ou un media d'un autre
    projet se glisse dans celui-ci.
    """
    p = Path(chemin).resolve()
    racine = Path(dossier_projet).resolve()
    try:
        p.relative_to(racine)
    except ValueError as exc:
        raise MelangeDeProjets(
            f"Le fichier {p} n'appartient pas au dossier du projet {projet_id} "
            f"({racine}). Import refusé pour éviter un mélange entre projets."
        ) from exc
    empreinte = lire_empreinte(racine)
    if empreinte and int(empreinte.get("projet_id", projet_id)) != int(projet_id):
        raise MelangeDeProjets(
            f"Le dossier {racine} porte l'empreinte du projet "
            f"{empreinte.get('projet_id')}, pas {projet_id}."
        )
    return p


def chemin_planche(dossier: str | Path, code: str, surnom: str, etat: str = "UNIQUE") -> Path:
    suffixe = "" if etat in ("", "UNIQUE") else f"_{slug(etat)}"
    return Path(dossier) / "references" / f"{code}_{slug(surnom)}{suffixe}.png"


def chemin_image_scene(dossier: str | Path, numero: int) -> Path:
    return Path(dossier) / "scenes" / f"scene-{numero:02d}_image.png"


def chemin_video_scene(dossier: str | Path, numero: int) -> Path:
    return Path(dossier) / "scenes" / f"scene-{numero:02d}_video.mp4"


def chemin_miniature(dossier: str | Path) -> Path:
    return Path(dossier) / "miniature" / "miniature.png"


def chemin_export_scene(dossier: str | Path, numero: int, titre: str = "") -> Path:
    """Nom stable d'une scène exportée seule : export/scenes/scene-07_titre.mp4"""
    base = f"scene-{numero:02d}"
    if titre:
        base = f"{base}_{slug(titre)}"
    return Path(dossier) / "export" / "scenes" / f"{base}.mp4"


def chemin_archive_scenes(dossier: str | Path, titre: str) -> Path:
    return Path(dossier) / "export" / f"{slug(titre, 'episode')}-scenes.zip"


def chemin_export(dossier: str | Path, titre: str) -> Path:
    return Path(dossier) / "export" / f"{slug(titre, 'episode')}.mp4"


def chemin_srt(dossier: str | Path, titre: str) -> Path:
    return Path(dossier) / "export" / f"{slug(titre, 'episode')}.srt"


def fichier_utilisable(chemin: str | Path | None) -> bool:
    if not chemin:
        return False
    p = Path(chemin)
    try:
        return p.exists() and p.stat().st_size > 0
    except OSError:  # nom trop long, caractères refusés par le disque…
        return False
