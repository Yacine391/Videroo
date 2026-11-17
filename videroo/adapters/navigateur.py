"""Pilotage d'un profil Chrome dedie, conserve localement.

Aucune API privee : on pilote les interfaces normales des sites. La connexion
est faite manuellement par l'utilisateur, une seule fois, dans ce profil.

IMPORTANT — un seul thread pilote le navigateur. Playwright en mode synchrone
refuse qu'un objet cree dans un thread soit utilise depuis un autre. Les routes
web et le worker tournent dans des threads differents : toutes les operations
passent donc par `session().executer(...)`, qui les fait executer par l'unique
thread autorise.
"""
from __future__ import annotations

import base64
import contextlib
import os
import queue
import re
import signal
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from .. import config, db
from . import ecran

_verrou = threading.Lock()
# Une session = un thread navigateur + ses contextes Chrome. On en garde une par
# fournisseur : pendant que Grok rend une vidéo, ChatGPT prépare l'image suivante.
# Playwright interdit de toucher un objet depuis un autre thread que le sien, d'où
# le repère ci-dessous : à l'intérieur d'un thread navigateur, session() rend
# toujours CELLE de ce thread.
PRINCIPALE = "principal"
_sessions: dict[str, "SessionNavigateur"] = {}
_local = threading.local()


class NavigateurIndisponible(RuntimeError):
    pass


class NavigateurOccupe(RuntimeError):
    pass


# Commande à lancer pour que Videroo puisse se brancher sur le Chrome de l'utilisateur.
COMMANDE_CHROME = "google-chrome --remote-debugging-port=9222"


def _profil_occupe(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marqueur in message
        for marqueur in ("processsingleton", "already in use", "singletonlock", "profile is")
    )


def _moteur_absent(exc: Exception) -> bool:
    message = str(exc)
    return "Executable doesn" in message or "playwright install" in message


def _pourquoi_pas_de_navigateur(profil: str, echecs: list, derniere) -> str:
    """Le vrai motif, pas le dernier essai.

    Chaque ouverture essaie plusieurs reglages, et pour chacun le Chrome du
    systeme PUIS le chromium livre avec Playwright. Ce dernier n'est pas
    installe ici : c'est donc toujours lui qui parle en dernier. Le 7 novembre
    2026, un profil simplement deja ouvert ailleurs ressortait en « le moteur de
    navigation manque », et la reparation proposee — installer Playwright —
    n'avait aucun rapport. On cherche donc le premier echec qui dit autre chose.
    """
    parlants = [e for e in echecs if not _moteur_absent(e)]
    if parlants:
        return (
            f"Impossible de lancer le navigateur pour le profil « {profil} ». "
            f"Motif : {premiere_ligne(parlants[0])[:400]}"
        )
    message = str(derniere) if derniere is not None else "aucun détail"
    if echecs and all(_moteur_absent(e) for e in echecs):
        return (
            f"Le moteur de navigation manque pour le profil « {profil} » : le Chrome "
            "du système n'a pas démarré et le chromium de Playwright n'est pas "
            "installé. Lance une fois : ./.venv/bin/python -m playwright install "
            f"chromium Détail : {message[:300]}"
        )
    return (
        f"Impossible de lancer le navigateur pour le profil « {profil} ». "
        f"Dernière erreur : {message[:400]}"
    )


def fenetres_restees_ouvertes() -> list[dict]:
    """Chrome encore vivants sur NOS profils, typiquement après un arrêt brutal."""
    import os

    racine = str(config.DOSSIER_DONNEES.resolve())
    trouves = []
    for entree in os.scandir("/proc"):
        if not entree.name.isdigit():
            continue
        try:
            with open(f"/proc/{entree.name}/cmdline", "rb") as fh:
                arguments = fh.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        for argument in arguments:
            if argument.startswith("--user-data-dir=") and argument[16:].startswith(racine):
                # « profil » = NOM du dossier, comme profils_en_cours() : le
                # 16 novembre 2026, ce champ contenait le chemin complet, la
                # comparaison avec les noms ne trouvait jamais rien, et le ménage
                # du lancement a tué les Chrome qui travaillaient.
                trouves.append({
                    "pid": int(entree.name),
                    "profil": Path(argument[16:]).name,
                    "chemin": argument[16:],
                })
                break
    return trouves


def chromes_videroo() -> list[dict]:
    """Les Chrome de Videroo réellement en vie : où ils s'affichent, ce qu'ils pèsent.

    Sert à te montrer la vérité plutôt qu'à te la promettre : si une fenêtre
    apparaît sur ton bureau, c'est qu'un de ces processus tourne sur ton écran à
    toi plutôt que sur l'écran virtuel.
    """
    import os

    racine = str(config.DOSSIER_DONNEES.resolve())
    sortie = []
    for entree in os.scandir("/proc"):
        if not entree.name.isdigit():
            continue
        try:
            with open(f"/proc/{entree.name}/cmdline", "rb") as fh:
                arguments = fh.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        dossier = next(
            (a[16:] for a in arguments
             if a.startswith("--user-data-dir=") and a[16:].startswith(racine)),
            None,
        )
        if dossier is None or any(a.startswith("--type=") for a in arguments):
            continue  # on ne garde que le processus principal de chaque Chrome
        affichage, memoire = "", 0
        try:
            with open(f"/proc/{entree.name}/environ", "rb") as fh:
                for ligne in fh.read().decode("utf-8", "replace").split("\0"):
                    if ligne.startswith("DISPLAY="):
                        affichage = ligne[8:]
                        break
        except OSError:
            pass
        try:
            with open(f"/proc/{entree.name}/status", encoding="utf-8") as fh:
                for ligne in fh:
                    if ligne.startswith("VmRSS:"):
                        memoire = int(ligne.split()[1]) // 1024
                        break
        except (OSError, ValueError):
            pass
        sortie.append({
            "pid": int(entree.name),
            "profil": Path(dossier).name,
            "affichage": affichage,
            "memoire_mo": memoire,
            "sur_ton_ecran": affichage in ("", ":0", ":1"),
        })
    return sorted(sortie, key=lambda c: c["profil"])


def profils_en_cours() -> set[str]:
    """Les dossiers de profil que cette application pilote en ce moment.

    On rend des noms de DOSSIER, pas des noms de profil : « principal » vit dans
    « profil_navigateur », et comparer les deux de front ne protegerait jamais le
    profil principal.
    """
    ouverts: set[str] = set()
    for session_ in sessions_ouvertes().values():
        try:
            ouverts.update(session_.profils_ouverts())
        except Exception:
            continue
    return {config.chemin_profil(nom).name for nom in ouverts}


def _attendre_profils_libres(profils: set[str], secondes: float = 8.0) -> set[str]:
    """Attend que plus aucun Chrome ne tienne ces dossiers de profil.

    Un Chrome qui vient de recevoir un SIGTERM met deux a trois secondes a
    mourir, et pendant ce temps il tient encore son dossier. Le 7 novembre 2026
    l'application a rouvert ChatGPT deux secondes apres l'avoir tue : la page a
    repondu, puis le premier vrai appel est tombe sur le cadavre. Un delai fixe
    ne sait pas si le menage est fini ; regarder, si.
    """
    import time

    fin = time.time() + secondes
    restants = profils
    while restants and time.time() < fin:
        restants = {f["profil"] for f in fenetres_restees_ouvertes()} & profils
        if not restants:
            return set()
        time.sleep(0.3)
    return restants


def fermer_fenetres_restees_ouvertes() -> int:
    """Termine les Chrome de Videroo dont plus personne ne se sert.

    « Restee ouverte » ne veut pas dire « ouverte ». Le 7 novembre 2026, ce
    menage, declenche au lancement de la chaine, a tue d'un SIGKILL le Chrome qui
    attendait justement une image chez ChatGPT : l'attente a couru 420 s contre
    une page morte, l'image a ete declaree introuvable, puis chaque scene
    suivante a echoue sur un contexte ferme. Une fenetre que cette application
    pilote a cet instant n'est pas une fenetre oubliee : on l'epargne.
    """
    import os
    import signal
    import time

    proteges = profils_en_cours()
    toutes = fenetres_restees_ouvertes()
    fenetres = [f for f in toutes if f["profil"] not in proteges]
    pids = sorted({f["pid"] for f in fenetres})
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    vises = {f["profil"] for f in fenetres}
    if pids:
        _attendre_profils_libres(vises)
    # Uniquement celles qu'on visait : un SIGKILL large reprendrait d'une main
    # ce que la protection vient de donner.
    restants = {f["pid"] for f in fenetres_restees_ouvertes()} & set(pids)
    for pid in restants:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if restants:
        _attendre_profils_libres(vises)
    if pids:
        # Des comptes ne disent pas QUI a ete ferme. Le 7 novembre 2026, savoir
        # quelle fenetre etait tombee a demande de relire tout le journal : la
        # ligne ne nommait personne. On nomme.
        epargnes = sorted({f["profil"] for f in toutes} - vises)
        detail = (
            " Épargnée(s), en cours d'utilisation : " + ", ".join(epargnes) + "."
            if epargnes else ""
        )
        db.journaliser(
            f"{len(pids)} fenêtre(s) Chrome de Videroo fermée(s) : "
            + ", ".join(sorted(vises))
            + ". Les profils sont de nouveau disponibles. Aucun autre "
            f"navigateur n'a été touché.{detail}"
        )
    return len(pids)


def _contexte_ferme(exc: Exception) -> bool:
    """L'erreur dit-elle que le navigateur a ete ferme, et non qu'il manque ?

    La difference compte : l'un se repare en rouvrant une fenetre, l'autre
    demande une installation. Les confondre envoyait installer Playwright alors
    qu'il tournait depuis des heures.
    """
    texte = str(exc).lower()
    return any(
        marque in texte
        for marque in ("has been closed", "target closed", "target page, context",
                       "browser closed", "connection closed")
    )


def raison_navigateur(exc: Exception) -> tuple[str, str]:
    """(ce qui s'est passe, quoi faire) quand une page refuse de s'ouvrir.

    On regarde avant d'accuser. Un navigateur ferme en cours de route et un
    navigateur absent produisent la meme panne a l'ecran, mais pas la meme
    reparation : le 7 novembre 2026, l'appli a envoye installer Playwright alors
    qu'il tournait depuis des heures et que le vrai coupable etait son propre
    menage de fenetres.
    """
    ok, souci = playwright_disponible()
    if not ok:
        return souci, (
            "Installe le navigateur : ./.venv/bin/python -m playwright install chromium."
        )
    if _contexte_ferme(exc):
        return (
            f"le navigateur a été fermé pendant l'ouverture de la page "
            f"({premiere_ligne(exc)})",
            "Clique « Réessayer » : la fenêtre se rouvre toute seule. Évite de "
            "relancer la chaîne pendant qu'une génération tourne — c'est ce qui "
            "ferme les fenêtres en cours.",
        )
    return premiere_ligne(exc), (
        "Regarde Admin > Navigateur. Si Playwright est bien là, passe la scène en "
        "mode assisté le temps de comprendre."
    )


def playwright_disponible() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False, (
            "Playwright n'est pas installé. Lance : ./.venv/bin/pip install playwright"
        )
    return True, "Playwright disponible."


class SessionNavigateur:
    """Toutes les operations navigateur sont serialisees sur un thread dedie.

    Plusieurs profils peuvent etre ouverts en meme temps : un par compte, pour
    que trois abonnements Grok cohabitent sans se melanger les sessions.
    """

    def __init__(self) -> None:
        self._pw = None
        self._chrome = None
        self._contexte_attache = None
        self._mode = "profil_dedie"
        self._contextes: dict[str, Any] = {}
        self._pages: dict[tuple[str, str], Any] = {}
        self._travaux: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._boucle, name="videroo-navigateur", daemon=True
        )
        self._occupe = threading.Event()
        self._thread.start()

    # ---------------------------------------------------------------- moteur

    def _boucle(self) -> None:  # pragma: no cover - boucle de fond
        _local.session = self  # tout ce qui tourne ici parle à CETTE session
        while True:
            resultat_box, fonction, args, kwargs = self._travaux.get()
            self._occupe.set()
            try:
                resultat_box["valeur"] = fonction(*args, **kwargs)
            except BaseException as exc:
                resultat_box["erreur"] = exc
            finally:
                self._occupe.clear()
                resultat_box["fini"].set()

    @property
    def occupe(self) -> bool:
        return self._occupe.is_set() or not self._travaux.empty()

    def executer(self, fonction: Callable, *args, refuser_si_occupe: bool = False, **kwargs):
        """Fait executer `fonction` par le thread navigateur et renvoie son resultat."""
        if threading.current_thread() is self._thread:
            return fonction(*args, **kwargs)
        if refuser_si_occupe and self.occupe:
            raise NavigateurOccupe(
                "Le navigateur est déjà utilisé par une génération en cours. "
                "Réessaie quand elle est terminée, ou mets le projet en pause."
            )
        boite: dict[str, Any] = {"fini": threading.Event()}
        self._travaux.put((boite, fonction, args, kwargs))
        boite["fini"].wait()
        if "erreur" in boite:
            raise boite["erreur"]
        return boite.get("valeur")

    # ------------------------------------------------- operations navigateur

    @property
    def ouvert(self) -> bool:
        return bool(self._contextes)

    def profils_ouverts(self) -> list[str]:
        return sorted(self._contextes)

    def _demarrer_playwright(self):
        if self._pw is None:
            ok, message = playwright_disponible()
            if not ok:
                raise NavigateurIndisponible(message)
            from playwright.sync_api import sync_playwright

            config.preparer_dossiers()
            self._pw = sync_playwright().start()
        return self._pw

    def _ouvrir(self, affichage: str | None = None, profil: str | None = None) -> None:
        from .. import reglages

        if affichage is None:
            affichage = reglages.affichage_navigateur()
        profil = profil or config.PROFIL_PRINCIPAL
        if profil in self._contextes:
            return
        self._demarrer_playwright()
        self._mode = str(reglages.lire("navigateur_mode"))

        if self._mode == "chrome_existant":
            # Un seul navigateur attache : tous les comptes y partagent la session.
            if self._chrome is None:
                self._brancher_sur_chrome(str(reglages.lire("cdp_url")))
            self._contextes[profil] = self._contexte_attache
            return

        # Un seul profil ouvert à la fois. Chaque Chrome pèse plusieurs centaines
        # de Mo et la machine n'a que 16 Go, partagés avec tout le reste : garder
        # deux navigateurs ouverts suffit à la faire ramer.
        for autre in list(self._contextes):
            if autre != profil:
                self._fermer(autre)
        # Fermer le dernier profil arrête aussi Playwright : on le relance.
        self._demarrer_playwright()

        dossier = config.chemin_profil(profil)
        dossier.mkdir(parents=True, exist_ok=True)
        # Arrivee ici, aucune session ne tient ce profil : un Chrome qui le tient
        # encore est un survivant en train de mourir. Lancer par-dessus donne un
        # contexte qui repond une fois puis meurt. On le laisse partir.
        tetus = _attendre_profils_libres({dossier.name})
        if tetus:
            db.journaliser(
                f"Un Chrome tient encore le profil « {profil} » après l'attente ; "
                "ouverture tentée quand même.",
                niveau="alerte",
            )
        base_args = [
            "--disable-blink-features=AutomationControlled",
            # Sobriété : sur un écran virtuel il n'y a pas de carte graphique, et
            # rien de tout cela n'est utile pour piloter une page.
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--mute-audio",
            "--renderer-process-limit=2",
        ]
        arguments = {
            "user_data_dir": str(dossier),
            "headless": False,
            "viewport": {"width": 1380, "height": 900},
            "accept_downloads": True,
            "args": base_args,
        }
        derniere: Exception | None = None
        assert self._pw is not None
        # Trois façons de travailler :
        #  - fenetre    : Chrome normal, visible ;
        #  - ecran_virtuel : un vrai Chrome, mais sur un écran X à lui (Xvfb). Rien
        #                 n'apparaît jamais sur le bureau, même sous GNOME/Wayland où
        #                 une fenêtre « hors écran » est ramenée par le compositeur ;
        #  - hors_ecran : Chrome normal placé loin de l'écran. Suffit sous X11 ;
        #  - invisible  : le headless intégré de Chrome. Le plus léger, mais souvent
        #                 bloqué par Cloudflare — on retombe alors sur hors_ecran.
        hors_ecran = {**arguments, "args": base_args + ["--window-position=-32000,-32000"]}
        virtuel = None
        if affichage == "ecran_virtuel":
            try:
                virtuel = {
                    **arguments,
                    "env": ecran.environnement(),
                    "args": base_args + ecran.ARGUMENTS,
                }
            except ecran.EcranIndisponible as exc:
                db.journaliser(
                    f"Écran virtuel impossible, repli sur la méthode hors écran : {exc}",
                    niveau="alerte",
                )
        if affichage == "fenetre":
            essais = [arguments]
        elif affichage == "invisible":
            essais = [{**arguments, "args": base_args + ["--headless=new"]}, hors_ecran]
        else:
            essais = [t for t in (virtuel, hors_ecran, arguments) if t is not None]
        echecs: list[Exception] = []
        derniere: Exception | None = None
        for tentative in essais:
            for canal in ("chrome", None):
                try:
                    if canal:
                        contexte = self._pw.chromium.launch_persistent_context(
                            channel=canal, **tentative
                        )
                    else:
                        contexte = self._pw.chromium.launch_persistent_context(**tentative)
                    self._contextes[profil] = contexte
                    args = tentative.get("args", [])
                    if tentative.get("env"):
                        comment = f"écran virtuel {tentative['env'].get('DISPLAY', '')}"
                    elif "--headless=new" in args or tentative["headless"]:
                        comment = "sans fenêtre"
                    elif any("window-position" in a for a in args):
                        comment = "hors écran"
                    else:
                        comment = "fenêtre visible"
                    db.journaliser(
                        f"Navigateur ouvert pour le profil « {profil} » "
                        f"(canal {canal or 'chromium'}, {comment}"
                        + (", par repli" if tentative is not essais[0] else "")
                        + f"), dossier {dossier}."
                    )
                    # Preuve, pas promesse : si l'écran virtuel reste vide alors que
                    # Chrome tourne, c'est qu'il dessine sur le bureau de l'utilisateur.
                    if tentative.get("env"):
                        presentes = ecran.fenetres()
                        if presentes == 0:
                            db.journaliser(
                                "Chrome est lancé mais l'écran virtuel est vide : la "
                                "fenêtre s'affiche donc sur ton bureau. Vérifie que "
                                "« --ozone-platform=x11 » est bien passé à Chrome.",
                                niveau="alerte",
                            )
                    return
                except Exception as exc:  # pragma: no cover - depend de la machine
                    derniere = exc
                    echecs.append(exc)
                    if _profil_occupe(exc):
                        raise NavigateurIndisponible(
                            f"Le profil « {profil} » est déjà ouvert dans une autre "
                            "fenêtre Chrome. Ferme-la, ou utilise « Fermer les fenêtres "
                            "restées ouvertes » dans Admin."
                        ) from exc
        raise NavigateurIndisponible(_pourquoi_pas_de_navigateur(profil, echecs, derniere))

    def _brancher_sur_chrome(self, adresse: str) -> None:
        """Se branche sur un Chrome lancé par l'utilisateur, avec ses sessions."""
        assert self._pw is not None
        try:
            self._chrome = self._pw.chromium.connect_over_cdp(adresse, timeout=15000)
        except Exception as exc:
            raise NavigateurIndisponible(
                f"Impossible de se brancher sur ton Chrome ({adresse}). "
                "Ferme complètement Chrome, puis relance-le avec :\n"
                f"{COMMANDE_CHROME}\n"
                f"Détail : {exc}"
            ) from exc
        contextes = self._chrome.contexts
        self._contexte_attache = contextes[0] if contextes else self._chrome.new_context()
        db.journaliser(
            f"Branché sur ton Chrome via {adresse} : les sessions déjà ouvertes sont "
            "utilisées telles quelles. Le multi-comptes n'est pas disponible dans ce mode."
        )

    def ouvrir(self, affichage: str | None = None, profil: str | None = None) -> None:
        self.executer(self._ouvrir, affichage, profil)

    def _page(self, cle: str, url: str, profil: str | None = None):
        """Rend une page VIVANTE, ou rouvre le navigateur une fois.

        Le contexte a pu etre ferme de l'exterieur : menage trop large, Chrome
        tue, plantage. Le garder en memoire faisait echouer TOUTES les scenes
        suivantes sur « navigateur indisponible », en accusant une installation
        de Playwright qui se porte tres bien. On rouvre une fois ; rouvrir une
        fenetre n'envoie aucun prompt et ne relance aucune generation.
        """
        from .. import reglages

        profil = profil or config.PROFIL_PRINCIPAL
        try:
            return self._page_vivante(cle, url, profil)
        except Exception as exc:
            if not _contexte_ferme(exc):
                raise
            db.journaliser(
                f"Le navigateur du profil « {profil} » avait été fermé pendant son "
                "utilisation : il est rouvert. Aucune génération n'est relancée."
            )
            self._oublier_profil(profil)
            self._ouvrir(reglages.affichage_navigateur(), profil)
            return self._page_vivante(cle, url, profil)

    def _page_vivante(self, cle: str, url: str, profil: str):
        """Une seule tentative. Toute mort du navigateur remonte a l'appelant."""
        from .. import reglages

        if profil not in self._contextes:
            self._ouvrir(reglages.affichage_navigateur(), profil)
        identifiant = (profil, cle)
        page = self._pages.get(identifiant)
        if page is not None and not page.is_closed():
            # is_closed() est un drapeau local : il dit « ouverte » d'un Chrome
            # deja mort. Le 7 novembre 2026 la scene 5 a recupere une page ainsi,
            # « prete a 0 s », et n'est tombee sur le cadavre qu'au premier vrai
            # appel — trop tard pour la reouverture. On fait un aller-retour.
            self._prouver_vivante(page)
            # Comparaison stricte : viser chatgpt.com alors qu'on est sur
            # /c/<conversation> doit bien rouvrir un NOUVEAU chat.
            if url and (page.url or "").rstrip("/") != url.rstrip("/"):
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
            return page
        page = self._contextes[profil].new_page()
        page.set_default_timeout(45000)
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        self._pages[identifiant] = page
        return page

    @staticmethod
    def _prouver_vivante(page) -> None:
        """Un aller-retour reel jusqu'au navigateur, pour preuve qu'il repond."""
        page.evaluate("1")

    def _oublier_profil(self, profil: str) -> None:
        """Oublie un contexte mort sans tenter de le refermer : il n'existe plus."""
        for identifiant in [c for c in self._pages if c[0] == profil]:
            self._pages.pop(identifiant, None)
        self._contextes.pop(profil, None)

    def page(self, cle: str, url: str, profil: str | None = None):
        """À n'appeler que depuis le thread navigateur (donc depuis un adaptateur)."""
        if threading.current_thread() is not self._thread:
            raise RuntimeError(
                "page() doit être appelée depuis le thread navigateur : "
                "passe par session().executer(...)."
            )
        return self._page(cle, url, profil)

    def _fermer(self, profil: str | None = None) -> None:
        cibles = [profil] if profil else list(self._contextes)
        for nom in cibles:
            for identifiant in [c for c in self._pages if c[0] == nom]:
                try:
                    self._pages[identifiant].close()
                except Exception:
                    pass
                self._pages.pop(identifiant, None)
            contexte = self._contextes.pop(nom, None)
            if contexte is not None and self._chrome is None:
                try:
                    contexte.close()
                except Exception:
                    pass
            db.journaliser(f"Navigateur fermé pour le profil « {nom} ».")
        if self._contextes:
            return
        if self._chrome is not None:
            # Chrome appartient à l'utilisateur : on se débranche, on ne le ferme pas.
            try:
                self._chrome.close()
            except Exception:
                pass
            self._chrome = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
        # Plus aucun profil ouvert DANS CETTE SESSION. L'écran virtuel est commun à
        # toutes : le 16 novembre 2026, la session Grok l'a éteint en fermant le
        # Chrome de Dariya, et le Chrome de ChatGPT, qui travaillait dessus, est
        # mort avec lui. On ne l'éteint que si plus personne ne s'en sert.
        with _verrou:
            autres = [s for s in _sessions.values() if s is not self and s._contextes]
        if not autres:
            ecran.arreter()

    def fermer(self, profil: str | None = None) -> None:
        self.executer(self._fermer, profil)

    def etat(self) -> dict:
        ok, message = playwright_disponible()
        return {
            "playwright": ok,
            "message": message,
            "ouvert": self.ouvert,
            "occupe": self.occupe,
            "mode": self._mode,
            "ecran": ecran.etat(),
            "chromes": chromes_videroo(),
            "profil": str(config.DOSSIER_PROFIL_NAVIGATEUR),
            "profils_ouverts": self.profils_ouverts(),
            "commande_chrome": COMMANDE_CHROME,
            "pages": [f"{p}/{c}" for p, c in self._pages],
        }


def session(cle: str = PRINCIPALE) -> SessionNavigateur:
    """La session de ce fournisseur — ou celle du thread, si on est dedans.

    Un adaptateur appelle `session()` sans rien savoir : comme il s'exécute sur
    le thread d'une session, il récupère la sienne. C'est ce qui permet à Grok
    et à ChatGPT de tourner en parallèle sans qu'un objet Playwright traverse
    jamais une frontière de thread.
    """
    courante = getattr(_local, "session", None)
    if courante is not None:
        return courante
    with _verrou:
        if cle not in _sessions:
            _sessions[cle] = SessionNavigateur()
        return _sessions[cle]


def sessions_ouvertes() -> dict[str, SessionNavigateur]:
    with _verrou:
        return dict(_sessions)


#: Levé à l'arrêt de l'application. Les attentes longues (image, vidéo) le
#  regardent et rendent la main : le 16 novembre 2026, un arrêt a patienté
#  quinze minutes derrière une attente Grok, et l'ancien serveur, invisible,
#  a continué d'agir sur la chaîne pendant que le nouveau tournait.
ARRET = threading.Event()


def fermer_toutes(delai: float = 20.0) -> None:
    """Ferme toutes les sessions : plus un seul Chrome de Videroo en mémoire.

    Borné dans le temps : une session occupée ne retient pas l'arrêt au-delà
    de `delai` secondes ; ses Chrome sont alors terminés par le système.
    """
    fils = []
    for s in sessions_ouvertes().values():
        f = threading.Thread(target=lambda s=s: s.fermer(), daemon=True)
        f.start()
        fils.append(f)
    limite = time.monotonic() + delai
    for f in fils:
        f.join(max(0.0, limite - time.monotonic()))
    if any(f.is_alive() for f in fils):
        for fenetre in fenetres_restees_ouvertes():
            with contextlib.suppress(OSError):
                os.kill(fenetre["pid"], signal.SIGTERM)
    ecran.arreter()


# ---------------------------------------------------------------------------
# Aides de selection tolerantes (les sites changent souvent)
# ---------------------------------------------------------------------------

def _balayer(page, selecteurs: list[str], timeout: int):
    """Balaie TOUS les sélecteurs par passes rapides, jusqu'à la limite globale.

    Point important pour la vitesse : avec un wait_for par sélecteur, trois
    sélecteurs obsolètes coûtaient trois fois le délai complet — plus d'une
    minute avant d'atteindre celui qui marche. Ici chaque passe est immédiate,
    donc le bon sélecteur est trouvé en une seconde, où qu'il soit dans la liste.
    """
    import time as _t

    fin = _t.monotonic() + max(timeout, 300) / 1000
    while True:
        for s in selecteurs or []:
            try:
                element = page.locator(s).first
                if element.is_visible():
                    return element, s
            except Exception:
                continue
        if _t.monotonic() >= fin:
            return None, None
        try:
            page.wait_for_timeout(300)
        except Exception:
            return None, None


def premier_visible(page, selecteurs: list[str], timeout: int = 4000):
    element, _ = _balayer(page, selecteurs, timeout)
    return element


def present(page, selecteurs: list[str], timeout: int = 1500) -> str | None:
    _, trouve = _balayer(page, selecteurs, timeout)
    return trouve


def preuve(page, selecteurs: list[str], timeout: int = 1500) -> str:
    """Ce qui a été vu sur la page, cité, ou une chaîne vide si rien.

    Déclarer un compte épuisé le met de côté pour des jours. Un simple
    « text=limit » qui tombe sur « Unlimited » suffirait à le faire. On
    montre donc le sélecteur ET le texte trouvé, pour que la décision soit
    vérifiable au lieu d'être à croire.
    """
    element, trouve = _balayer(page, selecteurs, timeout)
    if element is None:
        return ""
    try:
        extrait = " ".join((element.inner_text(timeout=1200) or "").split())[:120]
    except Exception:
        extrait = ""
    return f"« {trouve} » → « {extrait} »" if extrait else f"« {trouve} »"


_JS_APRES_MESSAGE = r"""([marqueur, bornes, selecteurs, prompt]) => {
    const plat = s => (s || '').replace(/\s+/g, ' ').trim();
    const m = plat(marqueur);
    if (!m) return null;
    // Si ChatGPT rendait un jour notre prompt en plusieurs morceaux, ceux qui
    // suivent le marqueur ne sont pas « la réponse » : on les reconnaît à ce
    // qu'ils figurent mot pour mot dans le prompt envoyé.
    const notrePrompt = plat(prompt);
    const deNous = t => notrePrompt && t.length >= 12 && notrePrompt.includes(t);
    const hors = n => {
        const e = n.nodeType === 1 ? n : n.parentElement;
        return !e || !!e.closest('script, style, noscript, textarea, [contenteditable="true"], form');
    };
    // Tous les morceaux de texte de la page, dans l'ordre, hors zone de saisie.
    const marcheur = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const noeuds = [];
    for (let n = marcheur.nextNode(); n; n = marcheur.nextNode()) {
        if (!hors(n) && n.nodeValue.trim()) noeuds.push(n);
    }
    // Notre message : le DERNIER morceau qui porte le marqueur. S'il est coupé
    // entre plusieurs morceaux, on retombe sur le plus petit bloc qui le contient.
    let iMessage = -1;
    let occurrences = 0;
    for (let i = noeuds.length - 1; i >= 0; i--) {
        if (plat(noeuds[i].nodeValue).includes(m)) {
            occurrences += 1;
            if (iMessage < 0) iMessage = i;
        }
    }
    let ancre = iMessage >= 0 ? noeuds[iMessage] : null;
    if (!ancre) {
        const debut = m.slice(0, 24);
        const blocs = [...document.querySelectorAll('body *')].filter(e =>
            !hors(e) && e.textContent.length < 60000 && e.textContent.includes(debut)
            && plat(e.textContent).includes(m)
            && ![...e.children].some(c => plat(c.textContent).includes(m)));
        if (!blocs.length) return null;
        occurrences = blocs.length;
        ancre = blocs[blocs.length - 1];
        for (let i = noeuds.length - 1; i >= 0; i--) {
            if (ancre.contains(noeuds[i])) { iMessage = i; break; }
        }
    }
    const apres = n => !!(ancre.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING)
        && !ancre.contains(n);
    // La borne : le premier message d'une AUTRE scène envoyé après le nôtre.
    const autres = (bornes || []).map(plat).filter(b => b && b !== m);
    let borne = null, iBorne = noeuds.length;
    for (let i = iMessage + 1; i < noeuds.length && autres.length; i++) {
        const t = plat(noeuds[i].nodeValue);
        if (apres(noeuds[i]) && autres.some(b => t.includes(b))) { borne = noeuds[i]; iBorne = i; break; }
    }
    const avantBorne = n => !borne
        || !!(borne.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_PRECEDING);
    const texte = noeuds.slice(iMessage + 1, iBorne).filter(apres)
        .map(n => plat(n.nodeValue)).filter(t => !deNous(t)).join('\n');
    let images = [];
    for (const selecteur of (selecteurs || [])) {
        let elements = [];
        try { elements = [...document.querySelectorAll(selecteur)]; } catch (e) { continue; }
        if (!elements.length) continue;
        images = elements.filter(e => apres(e) && avantBorne(e))
            .map(e => e.getAttribute('src')).filter(Boolean);
        break;  // même règle que la lecture des images : le premier sélecteur qui voit
    }
    return {texte: texte, images: [...new Set(images)], borne: !!borne, occurrences: occurrences};
}"""


def apres_notre_message(
    page, marqueur: str, bornes: list[str] | None = None,
    selecteurs_images: list[str] | None = None, prompt: str = "",
) -> dict | None:
    """Ce qui suit NOTRE message dans la conversation : texte et images.

    Une conversation garde tout : les réponses d'hier, les images des scènes
    d'avant, un vieux « vous avez atteint la limite ». Chercher dans la page
    entière, c'est lire tout ça comme si c'était la réponse du jour — le 16
    novembre 2026, « Récupérer » a ainsi rattaché à une scène la miniature de
    la partie précédente, et un mot du prompt a passé pour une limite d'usage.
    Seul ce qui vient APRÈS notre message, et avant le message de la scène
    suivante (`bornes`), est la réponse à notre demande.

    Rend None si le message est introuvable (conversation tronquée, marqueur
    vide) : l'appelant doit alors dire qu'il ne sait pas, pas deviner.
    Le texte garde un morceau de page par ligne ; `occurrences` dit combien de
    fois le marqueur est affiché (un même prompt renvoyé y figure plusieurs fois).
    """
    if not (marqueur or "").strip():
        return None
    # Les sélecteurs Playwright (text=…, :has-text) ne valent pas en CSS pur.
    css = [s for s in (selecteurs_images or []) if "text=" not in s and ":has-text" not in s]
    try:
        return page.evaluate(_JS_APRES_MESSAGE, [marqueur, list(bornes or []), css, prompt or ""])
    except Exception:
        return None


def sans_notre_message(texte: str, message: str) -> str:
    """`texte` privé des lignes de NOTRE message (celles d'au moins 12 caractères).

    Le texte lu « après le début de notre message » contient encore la suite de
    notre propre prompt : une réplique comme « réessayez plus tard » y passerait
    pour un refus du fournisseur. Les retours à la ligne sont gardés : ils
    séparent les morceaux de la page, et la citation d'un refus s'y arrête.
    """
    les_notres = {
        " ".join(l.split()) for l in (message or "").splitlines() if len(" ".join(l.split())) >= 12
    }
    garde: list[str] = []
    for ligne in (texte or "").splitlines():
        propre = " ".join(ligne.split())
        for notre in les_notres:
            if notre in propre:
                propre = propre.replace(notre, " ")
        propre = " ".join(propre.split())
        if propre:
            garde.append(propre)
    return "\n".join(garde)


def aplatir(texte: str) -> tuple[str, list[int]]:
    """Minuscules sans accents (’ devient '), et l'indice d'origine de chaque caractère.

    Les motifs de refus s'écrivent sans accents ; la citation, elle, doit
    reprendre le texte exact du fournisseur : l'index fait le lien.
    """
    plat: list[str] = []
    index: list[int] = []
    for i, c in enumerate(texte or ""):
        for d in unicodedata.normalize("NFD", c.lower()):
            if unicodedata.category(d) == "Mn":
                continue
            plat.append("'" if d in "’‘" else d)
            index.append(i)
    return "".join(plat), index


def citer_refus(texte: str, motifs: list[str], longueur_max: int = 260) -> str:
    """La phrase du fournisseur, telle qu'il l'a écrite, qui contient le premier
    motif trouvé (motifs sans accents, en minuscules), ou "" si aucun."""
    plat, index = aplatir(texte)
    for motif in motifs or []:
        trouve = re.search(motif, plat)
        if not trouve:
            continue
        ici = index[trouve.start()]
        # De la phrase qui contient le motif jusqu'à la fin de son morceau de
        # page : ni le « ChatGPT a dit : » d'avant, ni le pied de page d'après.
        point = max(texte.rfind(fin, 0, ici) for fin in (". ", "! ", "? ", "\n"))
        debut = point + 1 if point >= 0 else 0
        if ici - debut > longueur_max:
            debut = ici
        fin = texte.find("\n", ici)
        fin = min(fin if fin >= 0 else len(texte), debut + longueur_max)
        return " ".join(texte[debut:fin].split())
    return ""


def phrase_autour(texte: str, motif: str, longueur_max: int = 260) -> str:
    """La phrase du fournisseur qui contient `motif` (regex), citée telle quelle."""
    trouve = re.search(motif, texte, re.IGNORECASE)
    if not trouve:
        return ""
    point = texte.rfind(". ", 0, trouve.start())
    debut = point + 2 if point >= 0 else 0
    if trouve.start() - debut > longueur_max:
        debut = trouve.start()
    return texte[debut:debut + longueur_max].strip()


def premiere_ligne(exc: Exception) -> str:
    return str(exc).splitlines()[0].strip()


# Menus et fenetres flottantes qui, une fois ouverts, coupent les clics du RESTE
# de la page : la zone de saisie reste parfaitement visible, mais plus rien ne
# l'atteint. Vu le 7 novembre 2026 sur grok.com, ou un menu de la barre laterale
# ouvert par erreur a fait echouer une scene 45 s plus tard.
SURCOUCHES = [
    ("[data-radix-popper-content-wrapper]", "un menu déroulant"),
    ("[role='menu'][data-state='open']", "un menu"),
    ("[role='listbox'][data-state='open']", "une liste déroulante"),
    ("[role='dialog'][data-state='open']", "une fenêtre"),
    ("[role='menu']", "un menu"),
]


def surcouche_ouverte(page) -> str:
    """Nom lisible de la première surcouche ouverte, sinon chaîne vide."""
    for selecteur, nom in SURCOUCHES:
        try:
            if page.locator(selecteur).first.is_visible():
                return nom
        except Exception:
            continue
    return ""


def fermer_surcouches(page, essais: int = 3) -> str:
    """Referme par Échap ce qui traîne ouvert par-dessus la page.

    Échap ne valide rien, n'accepte rien et n'envoie rien : il ne fait que
    refermer. On ne l'envoie que si une surcouche est réellement ouverte, pour
    ne jamais toucher une zone de saisie qui se porte bien.
    """
    ferme = ""
    for _ in range(max(essais, 1)):
        nom = surcouche_ouverte(page)
        if not nom:
            break
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            break
        ferme = nom
    return ferme


def saisir(page, selecteurs: list[str], texte: str) -> tuple[bool, str]:
    """Ecrit dans une zone de saisie classique ou contenteditable.

    Rend (réussi, explication). L'explication compte : une zone bien présente
    mais recouverte par un menu ouvert n'est pas une zone « introuvable », et
    l'annoncer ainsi enverrait réparer un sélecteur qui marche.
    """
    zone, trouve = _balayer(page, selecteurs, 15000)
    if zone is None:
        liste = ", ".join(selecteurs or []) or "aucun sélecteur configuré"
        return False, f"aucune zone de saisie visible parmi : {liste}"
    try:
        # Délai court volontaire : un clic bloqué par une surcouche expirerait
        # sinon au bout du délai par défaut de la page, soit 45 s par scène.
        zone.click(timeout=8000)
    except Exception as exc:
        gene = surcouche_ouverte(page)
        if gene:
            return False, (
                f"la zone « {trouve} » est bien là, mais {gene} est ouvert "
                "par-dessus et absorbe les clics"
            )
        return False, (
            f"la zone « {trouve} » est visible mais refuse le clic : {premiere_ligne(exc)}"
        )
    try:
        zone.fill(texte)
        return True, trouve
    except Exception:
        try:
            page.keyboard.insert_text(texte)
            return True, f"{trouve} (saisie au clavier)"
        except Exception as exc:
            return False, (
                f"la zone « {trouve} » n'a pas accepté le texte : {premiere_ligne(exc)}"
            )


def texte_zone(page, selecteurs: list[str]) -> str | None:
    """Contenu actuel de la zone de saisie, textarea ou contenteditable."""
    for s in selecteurs or []:
        try:
            element = page.locator(s).first
            if not element.is_visible(timeout=1200):
                continue
            try:
                return element.input_value(timeout=1200)
            except Exception:
                return element.inner_text(timeout=1200)
        except Exception:
            continue
    return None


def _attendre_vidage(page, selecteurs: list[str], secondes: float) -> bool:
    """Le message est parti quand la zone de saisie s'est vidée."""
    import time as _t

    fin = _t.time() + secondes
    while _t.time() < fin:
        contenu = texte_zone(page, selecteurs)
        if contenu is not None and not contenu.strip():
            return True
        page.wait_for_timeout(400)
    return False


def octets_de_image(page, src: str) -> bytes:
    """Rend les octets d'une image affichée, y compris une adresse blob:.

    Une adresse `blob:` n'existe que dans l'onglet qui l'a créée : aucune requête
    extérieure ne peut la lire, et c'est exactement sous cette forme que ChatGPT
    sert ses images générées. On demande donc les octets au navigateur lui-même.
    """
    if src.startswith("blob:") or src.startswith("data:"):
        b64 = page.evaluate(
            """async (src) => {
                // Sans limite, une lecture qui ne répond pas gelait toute la tâche
                // (aucun délai par défaut sur evaluate) : 30 s, puis une erreur.
                const arret = new AbortController();
                const minuteur = setTimeout(() => arret.abort(), 30000);
                let reponse;
                try {
                    reponse = await fetch(src, {signal: arret.signal});
                } finally {
                    clearTimeout(minuteur);
                }
                const tampon = new Uint8Array(await reponse.arrayBuffer());
                let binaire = '';
                const pas = 0x8000;  // par tranches : btoa cale sur un gros tableau
                for (let i = 0; i < tampon.length; i += pas) {
                    binaire += String.fromCharCode.apply(null, tampon.subarray(i, i + pas));
                }
                return btoa(binaire);
            }""",
            src,
        )
        return base64.b64decode(b64)
    reponse = page.request.get(src)
    if not reponse.ok:
        raise RuntimeError(f"HTTP {reponse.status}")
    return reponse.body()


def envoyer(page, selecteurs_zone: list[str], selecteurs_bouton: list[str]) -> tuple[bool, str]:
    """Envoie le message ET vérifie qu'il est bien parti.

    Un clic sur le bouton ne suffit pas : il peut tomber à côté, ou le bouton
    peut être encore désactivé. On confirme en regardant la zone de saisie se
    vider, et à défaut on tente la touche Entrée. Sans confirmation, on préfère
    le dire plutôt que d'attendre une image qui ne viendra jamais.
    """
    bouton = premier_visible(page, selecteurs_bouton, timeout=8000)
    if bouton is not None:
        try:
            bouton.click()
            if _attendre_vidage(page, selecteurs_zone, 8):
                return True, "bouton d'envoi"
        except Exception:
            pass
    try:
        zone = premier_visible(page, selecteurs_zone, timeout=4000)
        if zone is not None:
            zone.click()
            page.keyboard.press("Enter")
            if _attendre_vidage(page, selecteurs_zone, 8):
                return True, "touche Entrée"
    except Exception:
        pass
    return False, ""


def accepte_plusieurs_fichiers(element) -> bool:
    """Le champ porte-t-il l'attribut « multiple » ?

    On demande au DOM plutôt que de le deviner. Le 7 novembre 2026, la miniature
    a échoué sur « Non-multiple file input can only accept single file » : elle
    joint les quatre planches d'un coup, là où une scène n'en joint qu'une. Le
    champ de ChatGPT n'accepte qu'un fichier par dépôt.
    """
    try:
        return element.get_attribute("multiple") is not None
    except Exception:
        return False


def joindre_fichiers(
    page, selecteurs_input: list[str], chemins: list[Path], boutons: list[str] | None = None,
    bouton_d_abord: bool = False,
) -> tuple[bool, str]:
    """Joint des fichiers au champ caché du site. Rend (réussi, explication).

    Les champs de fichier de ChatGPT portent la classe « hidden ». Un locator
    attend qu'un élément soit visible : il expire au bout de son délai sans rien
    joindre. Le handle DOM, lui, ne fait pas ce contrôle — c'est le chemin fiable
    pour un champ caché. Le locator reste en secours.

    Quand le champ n'accepte qu'un fichier à la fois, on dépose les fichiers un
    par un : chaque dépôt déclenche son propre envoi, et le site les empile dans
    la zone de saisie. Déposer un fichier n'envoie aucun prompt.
    """
    if not chemins:
        return True, ""
    fichiers = [str(c) for c in chemins]
    soucis = []
    # Grok plante quand on injecte les fichiers dans son champ caché (constaté le
    # 9 novembre 2026), alors que le même dépôt fait à la main par le bouton « + »
    # passe. Pour lui, on fait comme toi : le bouton d'abord.
    if bouton_d_abord and boutons:
        reussi, detail = _par_le_bouton(page, boutons, fichiers)
        if reussi:
            return True, detail
        soucis.append(detail)
    for s in selecteurs_input or []:
        poignee = None
        try:
            poignee = page.query_selector(s)
        except Exception as exc:
            soucis.append(f"{s} (recherche) : {premiere_ligne(exc)}")
        groupes = _groupes_de_depot(fichiers, poignee)
        if poignee is not None:
            try:
                for rang, groupe in enumerate(groupes):
                    _souffler_entre_depots(page, rang)
                    poignee.set_input_files(groupe)
                return True, _comment(s, "champ caché", groupes)
            except Exception as exc:
                soucis.append(f"{s} (handle) : {premiere_ligne(exc)}")
        try:
            for rang, groupe in enumerate(groupes):
                _souffler_entre_depots(page, rang)
                page.locator(s).first.set_input_files(groupe, timeout=8000)
            return True, _comment(s, "champ", groupes)
        except Exception as exc:
            soucis.append(f"{s} : {premiere_ligne(exc)}")
    if boutons and not bouton_d_abord:
        reussi, detail = _par_le_bouton(page, boutons, fichiers)
        if reussi:
            return True, detail
        soucis.append(detail)
    return False, " | ".join(s for s in soucis[:4] if s)


_JS_COLLER = """
async ({cible, nom, type, b64}) => {
  const el = document.querySelector(cible) || document.activeElement;
  if (!el) return false;
  el.focus();
  const brut = atob(b64), octets = new Uint8Array(brut.length);
  for (let i = 0; i < brut.length; i++) octets[i] = brut.charCodeAt(i);
  const dt = new DataTransfer();
  dt.items.add(new File([octets], nom, {type}));
  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  return true;
}
"""
_JS_MINIATURES = "() => document.querySelectorAll('img[src^=\"blob:\"], img[src^=\"data:image\"]').length"


def coller_images(page, zones: list[str], chemins: list[Path]) -> tuple[bool, str]:
    """Colle chaque image dans la zone de saisie, comme un Ctrl+V fait à la main.

    Chaque collage doit faire apparaître une miniature de plus dans la page ;
    sinon on s'arrête et on le dit : une vidéo ne doit jamais partir sans ses
    images de référence. Coller n'envoie aucun prompt.
    """
    cible = next((z for z in zones or [] if present(page, [z], timeout=1500)), None)
    if not cible:
        return False, "collage : aucune zone de saisie visible"
    for rang, chemin in enumerate(chemins):
        avant = page.evaluate(_JS_MINIATURES)
        type_mime = "image/png" if chemin.suffix.lower() == ".png" else "image/jpeg"
        page.evaluate(_JS_COLLER, {"cible": cible, "nom": chemin.name, "type": type_mime,
                                   "b64": base64.b64encode(chemin.read_bytes()).decode()})
        for _ in range(16):
            if page.evaluate(_JS_MINIATURES) > avant:
                break
            page.wait_for_timeout(500)
        else:
            return False, f"collage : aucune miniature n'est apparue pour {chemin.name}"
        _souffler_entre_depots(page, rang + 1)
    return True, f"collage (Ctrl+V) dans {cible}, {len(chemins)} image(s), miniatures vues"


def _par_le_bouton(page, boutons: list[str], fichiers: list[str]) -> tuple[bool, str]:
    """Certaines pages ne créent leur champ de fichier qu'au clic sur « Joindre ».

    On clique alors le bouton en attendant le sélecteur de fichiers du site, et on
    lui donne les fichiers un par un (un champ « un seul fichier » refuserait la
    liste entière). Si le clic ouvre un menu au lieu du sélecteur, on cherche son
    entrée « fichier / importer », et on referme tout en cas d'échec : un menu
    resté ouvert couperait les clics de toute la page.
    """
    soucis = []
    for b in boutons:
        if b.lstrip().startswith("input"):
            continue
        try:
            bouton = page.locator(b).first
            if not bouton.count():
                continue
            for rang, fichier in enumerate(fichiers):
                _souffler_entre_depots(page, rang)
                try:
                    with page.expect_file_chooser(timeout=4000) as choix:
                        bouton.click(timeout=3000)
                except Exception:
                    entree = page.get_by_role("menuitem", name=re.compile(
                        r"fichier|file|upload|import|photo|image", re.IGNORECASE)).first
                    with page.expect_file_chooser(timeout=4000) as choix:
                        entree.click(timeout=3000)
                choix.value.set_files(fichier)
            return True, f"via le bouton {b} ({len(fichiers)} fichier(s), sélecteur du site)"
        except Exception as exc:
            soucis.append(f"bouton {b} : {premiere_ligne(exc)}")
            with contextlib.suppress(Exception):
                page.keyboard.press("Escape")
    return False, " | ".join(soucis[:2]) or "aucun bouton « Joindre » trouvé sur la page"


def _souffler_entre_depots(page, rang: int) -> None:
    """Laisse au site le temps de prendre un fichier avant de lui donner le suivant.

    Un dépôt déclenche l'envoi du fichier par le site. Enchaîner quatre dépôts
    sans reprendre son souffle risquerait d'écraser la sélection avant qu'il
    l'ait lue. Attendre n'envoie rien.
    """
    if rang == 0:
        return
    try:
        page.wait_for_timeout(800)
    except Exception:
        pass


def _groupes_de_depot(fichiers: list[str], element) -> list[list[str]]:
    if len(fichiers) > 1 and not accepte_plusieurs_fichiers(element):
        return [[f] for f in fichiers]
    return [fichiers]


def _comment(selecteur: str, genre: str, groupes: list[list[str]]) -> str:
    un_par_un = " un par un" if len(groupes) > 1 else ""
    return f"{genre} « {selecteur} »{un_par_un}"


_JS_HORS_NAVIGATION = (
    "el => !el.closest('nav, aside, header, a[href], [role=\"navigation\"], "
    "[role=\"listitem\"], [data-sidebar], [class*=\"sidebar\" i]')"
)


def petit_bouton(page, selecteurs: list[str], longueur_max: int = 14, timeout: int = 2000):
    """Le premier VRAI petit bouton de réglage qui correspond, ou None.

    « button:has-text('9:16') » attrapait aussi, dans la barre latérale de Grok,
    la discussion « Anime vertical 9:16 avec kiwi accusateur… » : un clic
    dessus a ouvert ce vieux chat, et le prompt de la scène y est parti
    (16 novembre 2026). Un bouton de réglage a un texte court et ne vit ni
    dans la navigation, ni dans un lien.
    """
    import time as _t

    fin = _t.monotonic() + max(timeout, 300) / 1000
    while True:
        for s in selecteurs or []:
            try:
                for element in page.locator(s).all()[:20]:
                    try:
                        if not element.is_visible():
                            continue
                        texte = (element.inner_text(timeout=500) or "").strip()
                        if len(texte) > longueur_max:
                            continue
                        if not element.evaluate(_JS_HORS_NAVIGATION):
                            continue
                        return element
                    except Exception:
                        continue
            except Exception:
                continue
        if _t.monotonic() >= fin:
            return None
        try:
            page.wait_for_timeout(300)
        except Exception:
            return None


_JS_MINIATURES_DU_COMPOSEUR = """(selecteurs) => {
    let zone = null;
    for (const s of selecteurs) { zone = document.querySelector(s); if (zone) break; }
    if (!zone) return -1;
    // Le bloc de saisie : le formulaire, ou à défaut quelques parents au-dessus.
    let bloc = zone.closest('form');
    if (!bloc) { bloc = zone; for (let i = 0; i < 5 && bloc.parentElement; i++) bloc = bloc.parentElement; }
    return bloc.querySelectorAll('img[src^="blob:"], img[src^="data:image"]').length;
}"""


def nombre_miniatures(page, zones: list[str]) -> int:
    """Miniatures d'images jointes AUTOUR de la zone de saisie (-1 si inconnu).

    Compter dans toute la page mêlerait les images de la conversation, qui
    apparaissent et disparaissent au défilement.
    """
    try:
        return int(page.evaluate(_JS_MINIATURES_DU_COMPOSEUR, list(zones or [])))
    except Exception:
        return -1
