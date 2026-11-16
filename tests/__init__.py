"""Base des tests locaux : dossier de donnees isole, aucun quota IA consomme."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

DOSSIER_TEST = Path(tempfile.gettempdir()) / "videroo_tests"
os.environ["VIDEROO_DONNEES"] = str(DOSSIER_TEST)

DONNEES = Path(__file__).resolve().parent / "donnees"


def reinitialiser() -> None:
    """Repart d'une base et d'un stockage vierges."""
    from videroo import config, db

    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        db._local.conn = None
    shutil.rmtree(DOSSIER_TEST, ignore_errors=True)
    config.preparer_dossiers()
    db.initialiser()


def pack_exemple() -> str:
    return (DONNEES / "pack_exemple.md").read_text(encoding="utf-8")


def metaprompt_exemple() -> str:
    return (DONNEES / "metaprompt_extrait.md").read_text(encoding="utf-8")


def fabriquer_image_test(cible, couleur: str = "steelblue", texte: str = "REF") -> str:
    """Petite image reelle (lisible par ffprobe) pour les tests, sans IA."""
    import subprocess
    from pathlib import Path

    from videroo.media import chemin_outil

    cible = Path(cible)
    cible.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            chemin_outil("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={couleur}:s=1080x1920:d=1",
            "-vf", f"drawtext=text='{texte}':fontcolor=white:fontsize=90:x=(w-tw)/2:y=(h-th)/2",
            "-frames:v", "1", str(cible),
        ],
        capture_output=True, text=True, timeout=120, check=True,
    )
    return str(cible)


def pack_variantes() -> str:
    """Pack au format réel observé : libellés abrégés, codes explicites, planche objet."""
    return (DONNEES / "pack_variantes.md").read_text(encoding="utf-8")
