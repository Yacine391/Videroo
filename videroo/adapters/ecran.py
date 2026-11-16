"""Écran virtuel : Chrome travaille à côté, sans jamais apparaître sur ton bureau.

Pourquoi ce module. Sous GNOME en session Wayland — c'est le cas ici — placer une
fenêtre « hors écran » ne sert à rien : le compositeur la replace sur l'écran. Et le
mode sans fenêtre de Chrome (headless) est repéré puis bloqué par Cloudflare sur
ChatGPT. La seule façon fiable d'avoir un vrai Chrome qui ne s'affiche jamais, c'est
de lui donner un écran X à lui, servi par Xvfb.

Si Xvfb n'est pas installé, le module le dit clairement et l'appelant retombe sur
l'ancien comportement : rien ne casse, mais des fenêtres peuvent réapparaître.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

BINAIRE = "Xvfb"
COMMANDE_INSTALLATION = "sudo apt install xvfb"
TAILLE = "1380x900x24"

# Xvfb peut aussi vivre dans le dossier personnel, sans droits administrateur :
# le paquet officiel Ubuntu y est simplement dépaqueté. On le cherche là aussi.
COPIE_LOCALE = Path.home() / ".local" / "opt" / "xvfb" / "usr" / "bin" / "Xvfb"

_processus: subprocess.Popen | None = None
_affichage: str = ""


class EcranIndisponible(RuntimeError):
    """Xvfb n'est pas installé ou n'a pas démarré."""


def chemin_binaire() -> str:
    """Xvfb du système, sinon la copie installée dans le dossier personnel."""
    trouve = shutil.which(BINAIRE)
    if trouve:
        return trouve
    if COPIE_LOCALE.is_file():
        return str(COPIE_LOCALE)
    return ""


def disponible() -> bool:
    return bool(chemin_binaire())


def _prise(numero: int) -> Path:
    return Path(f"/tmp/.X11-unix/X{numero}")


def actif() -> bool:
    return _processus is not None and _processus.poll() is None


def demarrer() -> str:
    """Démarre l'écran virtuel si besoin et rend la valeur de DISPLAY à employer."""
    global _processus, _affichage
    if actif():
        return _affichage
    binaire = chemin_binaire()
    if not binaire:
        raise EcranIndisponible(
            f"L'écran virtuel a besoin de Xvfb, qui n'est pas installé. "
            f"Une seule commande suffit : {COMMANDE_INSTALLATION}"
        )
    derniere = ""
    for numero in range(99, 120):
        if _prise(numero).exists():
            continue
        processus = subprocess.Popen(
            [binaire, f":{numero}", "-screen", "0", TAILLE, "-nolisten", "tcp", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        fin = time.monotonic() + 8
        while time.monotonic() < fin:
            if processus.poll() is not None:
                derniere = f"Xvfb :{numero} s'est arrêté tout de suite."
                break
            if _prise(numero).exists():
                _processus = processus
                _affichage = f":{numero}"
                return _affichage
            time.sleep(0.1)
        else:
            derniere = f"Xvfb :{numero} n'a pas répondu en 8 s."
        processus.terminate()
    raise EcranIndisponible(
        f"Aucun écran virtuel n'a pu démarrer. {derniere}".strip()
    )


#: À passer à Chrome avec l'environnement : sans ça, rien ne marche.
#  Chrome choisit son affichage tout seul (« ozone auto ») et préfère Wayland. Il
#  trouve la session de GNOME par XDG_RUNTIME_DIR/wayland-0 même si WAYLAND_DISPLAY
#  a été retiré, ignore DISPLAY, et la fenêtre réapparaît sur le bureau. Mesuré le
#  6 novembre 2026 : l'écran virtuel était vide pendant que la fenêtre s'affichait.
ARGUMENTS = ["--ozone-platform=x11"]


def environnement() -> dict[str, str]:
    """L'environnement à donner à Chrome pour qu'il aille sur l'écran virtuel.

    À employer avec ARGUMENTS : l'environnement seul ne suffit pas.
    On retire WAYLAND_DISPLAY, et XAUTHORITY qui vise l'écran de GNOME.
    """
    affichage = demarrer()
    env = {
        cle: valeur
        for cle, valeur in os.environ.items()
        if cle not in ("WAYLAND_DISPLAY", "XAUTHORITY", "EGL_PLATFORM", "GNOME_SETUP_DISPLAY")
    }
    env["DISPLAY"] = affichage
    return env


def fenetres() -> int | None:
    """Combien de fenêtres vivent sur l'écran virtuel, ou None si on ne sait pas.

    Sert de preuve plutôt que de promesse : si Chrome tourne mais que cet écran
    reste vide, c'est qu'il dessine ailleurs — donc sur ton bureau.
    """
    if not actif() or not shutil.which("xwininfo"):
        return None
    try:
        resultat = subprocess.run(
            ["xwininfo", "-display", _affichage, "-root", "-children"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if resultat.returncode != 0:
        return None
    return sum(1 for ligne in resultat.stdout.splitlines() if ligne.strip().startswith("0x"))


def arreter() -> None:
    global _processus, _affichage
    if _processus is not None and _processus.poll() is None:
        _processus.terminate()
        try:
            _processus.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - cas rare
            _processus.kill()
    _processus = None
    _affichage = ""


def etat() -> dict:
    binaire = chemin_binaire()
    return {
        "installe": bool(binaire),
        "binaire": binaire,
        "dans_le_dossier_personnel": bool(binaire) and not shutil.which(BINAIRE),
        "actif": actif(),
        "affichage": _affichage,
        "commande_installation": COMMANDE_INSTALLATION,
    }
