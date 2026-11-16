"""Application locale Videroo (FastAPI). Ecoute uniquement sur 127.0.0.1."""
from __future__ import annotations

import contextlib
import json
import mimetypes
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (__version__, comptes, config, db, fichiers, media, parser_v6,
               reglages, ressources, runner, services)
from .adapters import navigateur, registre, selecteurs
from .adapters.registre import MODES

RACINE = Path(__file__).resolve().parent
gabarits = Jinja2Templates(directory=str(RACINE / "templates"))
_worker: list[runner.Worker] | None = None


@contextlib.asynccontextmanager
async def cycle(app: FastAPI):
    global _worker
    navigateur.ARRET.clear()  # un démarrage annule tout arrêt précédent
    config.preparer_dossiers()
    db.initialiser()
    registre.installer()
    for rapport in runner.recuperer_apres_plantage():
        print(f"[reprise] {rapport}")
    _worker = [runner.Worker(f) for f in runner.FOURNISSEURS]
    for w in _worker:
        w.start()
    print(f"Videroo {__version__} prete sur http://{config.HOTE}:{config.PORT}")
    yield
    navigateur.ARRET.set()  # les attentes en cours rendent la main tout de suite
    for w in _worker or []:
        w.stop()
    with contextlib.suppress(Exception):
        navigateur.fermer_toutes()
    # Les attentes ont rendu la main et les Chrome sont fermés : le signal a
    # servi. Le laisser levé bloquerait toute attente d'un futur démarrage
    # dans le même processus.
    navigateur.ARRET.clear()


app = FastAPI(title="Videroo", version=__version__, lifespan=cycle)
app.mount("/static", StaticFiles(directory=str(RACINE / "static")), name="static")


def empreinte_style() -> str:
    """Date de modification de la feuille de style, pour casser le cache.

    Sans ça, Chrome garde l'ancienne feuille et la page s'affiche de travers
    après chaque retouche. Une nouvelle adresse force un rechargement propre.
    """
    try:
        return str(int((RACINE / "static" / "style.css").stat().st_mtime))
    except OSError:
        return __version__


def rendu(request: Request, gabarit: str, contexte: dict) -> HTMLResponse:
    contexte.setdefault("version", __version__)
    contexte.setdefault("empreinte_style", empreinte_style())
    contexte.setdefault("message", request.query_params.get("msg"))
    contexte.setdefault("erreur", request.query_params.get("err"))
    contexte.setdefault("modes", MODES)
    contexte["request"] = request
    return gabarits.TemplateResponse(request, gabarit, contexte)


def retour(url: str, msg: str = "", err: str = "") -> RedirectResponse:
    params = {}
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
    return RedirectResponse(url, status_code=303)


# --------------------------------------------------------------------------
# Projets
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def accueil(request: Request):
    return rendu(
        request,
        "projets.html",
        {
            "projets": services.lister_projets(False),
            "archives": services.lister_projets(True),
            "outils": media.outils_disponibles(),
            "proprietaires": comptes.proprietaires(),
            "series_de": {p: services.serie_de(p) for p in comptes.proprietaires()},
            "navigateur": navigateur.session().etat(),
            "tache_active": runner.tache_active(),
            "types_projet": config.TYPES_PROJET,
            "type_defaut": config.TYPE_PROJET_DEFAUT,
        },
    )


@app.post("/projets/nouveau")
def projet_nouveau(
    nom: str = Form(""),
    type_histoire: str = Form(""),
    comptes_de: str = Form(""),
    chat_chatgpt: str = Form(""),
    refs_dans_chat: str = Form(""),
    episode: int = Form(1),
):
    if not nom.strip() and comptes_de:
        nom = services.nom_episode_suivant(comptes_de)
    projet_id = services.creer_projet(
        nom, type_histoire, comptes_de, chat_chatgpt, refs_dans_chat == "1", episode
    )
    choisi = services.type_histoire_valide(type_histoire)
    return retour(
        f"/projets/{projet_id}/histoire",
        f"Projet « {config.TYPES_PROJET[choisi]} » créé. Colle maintenant le pack "
        "produit par le métaprompt de ce type.",
    )


@app.post("/projets/{projet_id}/archiver")
def projet_archiver(projet_id: int, archive: int = Form(1)):
    services.archiver(projet_id, bool(archive))
    return retour("/", "Projet archivé." if archive else "Projet désarchivé.")


@app.get("/projets/{projet_id}")
def projet_racine(projet_id: int):
    return RedirectResponse(f"/projets/{projet_id}/histoire", status_code=303)


# --------------------------------------------------------------------------
# Histoire : import + validation
# --------------------------------------------------------------------------

@app.get("/projets/{projet_id}/histoire", response_class=HTMLResponse)
def page_histoire(request: Request, projet_id: int):
    from . import lecteur_souple

    d = services.detail(projet_id)
    inconnues = db.charger_json(d["projet"].get("etiquettes_inconnues"), [])
    appris = lecteur_souple.apprises()
    # Une étiquette apprise depuis (dans un autre projet) ne se repropose pas.
    inconnues = [e for e in inconnues if e.get("cle") not in appris]
    return rendu(
        request,
        "histoire.html",
        {"d": d, "onglet": "histoire", "inconnues": inconnues,
         "champs": lecteur_souple.CHAMPS},
    )


@app.post("/projets/{projet_id}/etiquettes")
async def apprendre_etiquettes(projet_id: int, request: Request):
    """Retient ce que sont les étiquettes inconnues, puis relit le pack stocké."""
    from . import lecteur_souple

    formulaire = await request.form()
    choix = {
        cle[3:]: str(valeur)
        for cle, valeur in formulaire.items()
        if cle.startswith("e::") and str(valeur) in lecteur_souple.CHAMPS
    }
    if not choix:
        return retour(f"/projets/{projet_id}/histoire", err="Aucune étiquette choisie.")
    retenues = lecteur_souple.apprendre(choix)
    pr = services.projet(projet_id)
    if not (pr.get("pack_brut") or "").strip():
        return retour(
            f"/projets/{projet_id}/histoire",
            f"{retenues} étiquette(s) retenue(s). Réimporte le pack pour les appliquer.",
        )
    resultat = services.analyser_pack(pr["pack_brut"], pr.get("type_histoire"), projet_id)
    try:
        bilan = services.appliquer_pack(projet_id, resultat)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/histoire", err=str(exc))
    a_verifier = sum(1 for a in bilan["avertissements"] if a["niveau"] != "info")
    return retour(
        f"/projets/{projet_id}/histoire",
        f"{retenues} étiquette(s) retenue(s), pack relu : {bilan['scenes']} scène(s), "
        f"{a_verifier} point(s) à corriger.",
    )


@app.post("/projets/{projet_id}/importer")
async def importer_pack(
    projet_id: int,
    texte: str = Form(""),
    fichier: UploadFile | None = File(None),
):
    contenu = texte or ""
    if fichier is not None and fichier.filename:
        brut = await fichier.read()
        try:
            contenu = brut.decode("utf-8")
        except UnicodeDecodeError:
            contenu = brut.decode("latin-1", errors="replace")
    if not contenu.strip():
        return retour(f"/projets/{projet_id}/histoire", err="Colle ton pack ou choisis un fichier.")
    resultat = services.analyser_pack(
        contenu, services.projet(projet_id).get("type_histoire"), projet_id
    )
    try:
        bilan = services.appliquer_pack(projet_id, resultat)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/histoire", err=str(exc))
    a_verifier = sum(1 for a in bilan["avertissements"] if a["niveau"] != "info")
    return retour(
        f"/projets/{projet_id}/histoire",
        f"Pack importé : {bilan['personnages']} personnage(s), {bilan['scenes']} scène(s). "
        f"{a_verifier} point(s) a corriger avant génération.",
    )


@app.post("/projets/{projet_id}/entete")
async def maj_entete(projet_id: int, request: Request):
    formulaire = await request.form()
    champs = {k: v for k, v in formulaire.items() if k in services.CHAMPS_PROJET_EDITABLES}
    for numerique in ("grok_episode", "export_largeur", "export_hauteur"):
        if numerique in champs:
            with contextlib.suppress(ValueError):
                champs[numerique] = int(champs[numerique])
    for reel in ("duree_cible_min", "duree_cible_max", "vitesse", "musique_volume"):
        if reel in champs:
            with contextlib.suppress(ValueError):
                champs[reel] = float(str(champs[reel]).replace(",", "."))
    services.maj_projet(projet_id, champs)
    cible = formulaire.get("retour_vers") or "histoire"
    services.memoriser_serie_de(projet_id)
    return retour(f"/projets/{projet_id}/{cible}", "Champs enregistrés.")


@app.post("/projets/{projet_id}/valider")
def valider(projet_id: int):
    bilan = services.valider_projet(projet_id)
    if bilan["refusees"]:
        detail = " | ".join(
            f"scene {r['numero']} : {', '.join(r['problemes'])}" for r in bilan["refusees"]
        )
        return retour(
            f"/projets/{projet_id}/scenes",
            msg=f"{len(bilan['validees'])} scène(s) validée(s).",
            err=f"Non validables — {detail}",
        )
    return retour(
        f"/projets/{projet_id}/scenes",
        f"{len(bilan['validees'])} scène(s) validée(s). Le projet est prêt à générer.",
    )


# --------------------------------------------------------------------------
# Personnages
# --------------------------------------------------------------------------

@app.get("/projets/{projet_id}/personnages", response_class=HTMLResponse)
def page_personnages(request: Request, projet_id: int):
    d = services.detail(projet_id)
    return rendu(request, "personnages.html", {"d": d, "onglet": "personnages"})


@app.post("/personnages/{personnage_id}/champs")
async def personnage_champs(personnage_id: int, request: Request):
    formulaire = await request.form()
    ligne = db.q1("SELECT projet_id FROM personnages WHERE id=?", (personnage_id,))
    if not ligne:
        return retour("/", err="Personnage inconnu.")
    services.maj_personnage(personnage_id, dict(formulaire))
    return retour(f"/projets/{ligne['projet_id']}/personnages", "Personnage enregistré.")


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------

@app.get("/projets/{projet_id}/scenes", response_class=HTMLResponse)
def page_scenes(request: Request, projet_id: int):
    d = services.detail(projet_id)
    return rendu(request, "scenes.html", {"d": d, "onglet": "scenes"})


@app.post("/scenes/{scene_id}/champs")
async def scene_champs(scene_id: int, request: Request):
    formulaire = await request.form()
    ligne = db.q1("SELECT projet_id FROM scenes WHERE id=?", (scene_id,))
    if not ligne:
        return retour("/", err="Scène inconnue.")
    champs = dict(formulaire)
    if "duree" in champs:
        with contextlib.suppress(ValueError):
            champs["duree"] = int(champs["duree"])
    for reel in ("trim_debut", "trim_fin"):
        if reel in champs:
            with contextlib.suppress(ValueError):
                champs[reel] = float(str(champs[reel]).replace(",", "."))
    services.maj_scene(scene_id, champs)
    return retour(f"/projets/{ligne['projet_id']}/scenes#scene-{scene_id}", "Scène enregistree.")


@app.post("/scenes/{scene_id}/valider")
def scene_valider(scene_id: int, valide: int = Form(1)):
    ligne = db.q1("SELECT projet_id, numero FROM scenes WHERE id=?", (scene_id,))
    if not ligne:
        return retour("/", err="Scène inconnue.")
    try:
        services.valider_scene(scene_id, bool(valide))
    except services.ErreurMetier as exc:
        return retour(f"/projets/{ligne['projet_id']}/scenes", err=str(exc))
    return retour(
        f"/projets/{ligne['projet_id']}/scenes",
        f"Scène {ligne['numero']} {'validée' if valide else 'dévalidée'}.",
    )


# --------------------------------------------------------------------------
# Generations
# --------------------------------------------------------------------------

@app.get("/projets/{projet_id}/generations", response_class=HTMLResponse)
def page_generations(request: Request, projet_id: int):
    d = services.detail(projet_id)
    return rendu(
        request,
        "generations.html",
        {
            "d": d,
            "onglet": "generations",
            "navigateur": navigateur.session().etat(),
            "regles_grok": ressources.regles_grok(),
            "mode_defaut": db.reglage("mode_defaut", "assiste"),
            "production": runner.etat_production(projet_id),
        },
    )


@app.post("/projets/{projet_id}/generer")
def generer(
    projet_id: int,
    cible_type: str = Form(...),
    cible_id: int = Form(...),
    fournisseur: str = Form(...),
    mode: str = Form("assiste"),
    origine: str = Form("auto"),
):
    try:
        tache_id = runner.demander(
            projet_id, cible_type, cible_id,
            fournisseur=fournisseur, mode=mode, origine=origine,
        )
    except runner.GenerationRefusee as exc:
        return retour(f"/projets/{projet_id}/generations", err=str(exc))
    except fichiers.MelangeDeProjets as exc:
        return retour(f"/projets/{projet_id}/generations", err=str(exc))
    return retour(
        f"/projets/{projet_id}/generations",
        f"Tâche #{tache_id} en file d'attente (une seule génération à la fois).",
    )


@app.post("/projets/{projet_id}/production")
def demarrer_production(projet_id: int, mode: str = Form("auto")):
    if mode not in MODES:
        return retour(f"/projets/{projet_id}/generations", err="Mode inconnu.")
    if mode == "auto":
        try:
            navigateur.session().ouvrir()
        except navigateur.NavigateurIndisponible as exc:
            return retour(
                f"/projets/{projet_id}/generations",
                err=f"Le mode automatique a besoin du navigateur dédié : {exc}",
            )
    # Le clic sur « Lancer la production complète » vaut validation des scènes
    # qui sont complètes ; celles qui posent problème restent de côté.
    bilan = services.valider_projet(projet_id)
    try:
        resultat = runner.demarrer_production(projet_id, mode)
    except runner.GenerationRefusee as exc:
        detail = ""
        if bilan["refusees"]:
            detail = " Scènes non validables : " + " | ".join(
                f"{r['numero']} ({', '.join(r['problemes'])})" for r in bilan["refusees"]
            )
        return retour(f"/projets/{projet_id}/generations", err=str(exc) + detail)
    lancee = resultat.get("libelle") or ""
    if resultat["tache_id"] is None:
        arret = runner.phrase_des_blocages(resultat.get("etat") or {})
        return retour(
            f"/projets/{projet_id}/generations",
            arret or "Chaîne lancée, mais rien à produire pour l'instant.",
        )
    rappel = (
        " Connecte-toi à ChatGPT et à Grok dans la fenêtre qui vient de s'ouvrir si ce"
        " n'est pas déjà fait."
        if mode == "auto"
        else ""
    )
    refus = ""
    if bilan["refusees"]:
        refus = (
            f" {len(bilan['refusees'])} scène(s) laissée(s) de côté : "
            + " | ".join(
                f"{r['numero']} ({', '.join(r['problemes'])})" for r in bilan["refusees"]
            )
        )
    return retour(
        f"/projets/{projet_id}/generations",
        f"{len(bilan['validees'])} scène(s) validée(s). Chaîne lancée, étape en cours : "
        f"{lancee or 'première étape'}.{rappel}",
        refus.strip(),
    )


@app.post("/projets/{projet_id}/production/arreter")
def arreter_production(projet_id: int):
    runner.arreter_production(projet_id)
    return retour(
        f"/projets/{projet_id}/generations",
        "Chaîne arrêtée. Tous les résultats déjà obtenus sont conservés.",
    )


@app.post("/projets/{projet_id}/partie-suivante")
def partie_suivante(projet_id: int):
    suite = services.creer_partie_suivante(projet_id)
    if not suite["cree"]:
        return retour(
            f"/projets/{suite['id']}",
            f"La partie {suite['episode']} existait déjà : la voici. Rien n'a été recréé.",
        )
    return retour(
        f"/projets/{suite['id']}",
        f"Partie {suite['episode']} prête : mêmes chats ChatGPT et Grok, "
        f"{suite['planches']} planche(s) reprise(s). Colle son pack ci-dessous. "
        "Rien n'a été lancé.",
    )


@app.post("/projets/{projet_id}/nouveau-chat")
def nouveau_chat(projet_id: int, fournisseur: str = Form("chatgpt")):
    colonne = "chatgpt_chat_url" if fournisseur == "chatgpt" else "grok_chat_url"
    db.exec1(f"UPDATE projets SET {colonne}=NULL WHERE id=?", (projet_id,))
    if fournisseur == "grok":
        db.exec1(
            "UPDATE projets SET grok_regles_installees=0, grok_serie_annoncee=0,"
            " grok_profil_regles=NULL WHERE id=?",
            (projet_id,),
        )
    db.journaliser(
        f"Conversation {fournisseur} oubliée : la prochaine génération ouvrira un "
        "nouveau chat pour cette histoire.",
        projet_id,
    )
    return retour(
        f"/projets/{projet_id}/generations",
        f"La prochaine génération {fournisseur} ouvrira un nouveau chat.",
    )


@app.post("/projets/{projet_id}/generer-lot")
def generer_lot(
    projet_id: int,
    genre: str = Form(...),
    fournisseur: str = Form(...),
    mode: str = Form("assiste"),
):
    mises_en_file, refus = [], []
    if genre == "planche":
        cibles = [
            (p["id"], f"{p['code']} {p['surnom']}")
            for p in db.q(
                "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre", (projet_id,)
            )
        ]
    else:
        cibles = [
            (s["id"], f"scene {s['numero']}")
            for s in db.q(
                "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (projet_id,)
            )
        ]
    for cible_id, libelle in cibles:
        try:
            runner.demander(
                projet_id, genre, cible_id,
                fournisseur=fournisseur, mode=mode, origine="auto",
            )
            mises_en_file.append(libelle)
        except (runner.GenerationRefusee, fichiers.MelangeDeProjets) as exc:
            refus.append(f"{libelle} : {exc}")
    return retour(
        f"/projets/{projet_id}/generations",
        msg=f"{len(mises_en_file)} tâche(s) en file. Elles partiront une par une.",
        err=(" | ".join(refus[:6]) + (" …" if len(refus) > 6 else "")) if refus else "",
    )


@app.post("/projets/{projet_id}/regenerer")
def regenerer(
    projet_id: int,
    cible_type: str = Form(...),
    cible_id: int = Form(...),
    fournisseur: str = Form(...),
    mode: str = Form("assiste"),
):
    try:
        tache_id = runner.regenerer(projet_id, cible_type, cible_id, fournisseur, mode)
    except (runner.GenerationRefusee, fichiers.MelangeDeProjets) as exc:
        return retour(f"/projets/{projet_id}/generations", err=str(exc))
    return retour(
        f"/projets/{projet_id}/generations",
        f"Régénération EXPLICITE demandée (tâche #{tache_id}). L'ancien fichier est conserve.",
    )


@app.post("/taches/{tache_id}/reessayer")
def tache_reessayer(tache_id: int):
    ligne = db.q1("SELECT projet_id FROM taches WHERE id=?", (tache_id,))
    if not ligne:
        return retour("/", err="Tâche inconnue.")
    try:
        nouvelle = runner.reessayer(tache_id)
    except (runner.GenerationRefusee, fichiers.MelangeDeProjets) as exc:
        return retour(f"/projets/{ligne['projet_id']}/generations", err=str(exc))
    return retour(
        f"/projets/{ligne['projet_id']}/generations",
        f"Réessai explicite en file (tâche #{nouvelle}).",
    )


@app.post("/taches/{tache_id}/annuler")
def tache_annuler(tache_id: int):
    ligne = db.q1("SELECT projet_id FROM taches WHERE id=?", (tache_id,))
    if not ligne:
        return retour("/", err="Tâche inconnue.")
    try:
        runner.annuler_tache(tache_id)
    except runner.GenerationRefusee as exc:
        return retour(f"/projets/{ligne['projet_id']}/generations", err=str(exc))
    return retour(f"/projets/{ligne['projet_id']}/generations", "Tâche annulée.")


@app.post("/projets/{projet_id}/statut")
def projet_statut(
    projet_id: int, statut: str = Form(...), retour_vers: str = Form("generations")
):
    # Liste fermée : la page de retour vient d'un formulaire, pas d'un chemin libre.
    pages = {
        "projets": "/",
        "generations": f"/projets/{projet_id}/generations",
        "atelier": f"/projets/{projet_id}/atelier",
        "montage": f"/projets/{projet_id}/montage",
    }
    ou = pages.get(retour_vers, pages["generations"])
    try:
        if statut == "arrete":
            nombre = runner.arreter_projet(projet_id)
            return retour(
                f"/projets/{projet_id}/generations",
                f"Projet arrêté : {nombre} tâche(s) en attente annulée(s). "
                "Les résultats déjà obtenus sont intacts.",
            )
        runner.changer_statut_projet(projet_id, statut)
    except runner.GenerationRefusee as exc:
        return retour(ou, err=str(exc))
    libelles = {
        "pause": "Projet en pause : aucune nouvelle tâche ne partira.",
        "en_cours": "Projet repris.",
        "valide": "Projet repris.",
        "termine": "Projet marqué comme terminé.",
    }
    return retour(ou, libelles.get(statut, "Statut changé."))


@app.post("/projets/{projet_id}/importer-media")
async def importer_media(
    projet_id: int,
    cible_type: str = Form(...),
    cible_id: int = Form(...),
    fichier: UploadFile = File(...),
    retour_vers: str = Form("generations"),
):
    donnees = await fichier.read()
    try:
        resultat = services.importer_media(
            projet_id, cible_type, cible_id, fichier.filename or "", donnees
        )
    except (services.ErreurMetier, runner.GenerationRefusee, fichiers.MelangeDeProjets) as exc:
        return retour(f"/projets/{projet_id}/{retour_vers}", err=str(exc))
    infos = resultat["infos"]
    details = f"{infos['largeur']}x{infos['hauteur']}"
    if cible_type == "video":
        details += f", {infos['duree']:.2f} s, audio {'oui' if infos['a_audio'] else 'NON'}"
    return retour(
        f"/projets/{projet_id}/{retour_vers}",
        f"Import accepté : {Path(resultat['chemin']).name} ({details}).",
    )


@app.post("/projets/{projet_id}/deposer")
async def deposer_image(
    projet_id: int, fichier: UploadFile = File(...), cible: str = Form(...),
):
    """Glisser-déposer d'une image sur sa case (planche ou miniature). Aucune génération."""
    try:
        services.projet(projet_id)
    except services.ErreurMetier as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
    donnees = await fichier.read()
    return services.deposer_image(projet_id, fichier.filename or "", donnees, cible.strip())


@app.post("/navigateur/{action}")
def navigateur_action(action: str, retour_vers: str = Form("/")):
    try:
        if action == "ouvrir":
            navigateur.session().ouvrir()
            return retour(retour_vers, "Navigateur dédié ouvert. Connecte-toi a tes comptes.")
        if action == "fermer":
            navigateur.session().fermer()
            return retour(retour_vers, "Navigateur dédié ferme.")
    except navigateur.NavigateurIndisponible as exc:
        return retour(retour_vers, err=str(exc))
    return retour(retour_vers, err=f"Action inconnue : {action}")


# --------------------------------------------------------------------------
# Prompts en serie
# --------------------------------------------------------------------------

@app.get("/projets/{projet_id}/serie/{genre}", response_class=HTMLResponse)
def page_serie(request: Request, projet_id: int, genre: str):
    if genre not in ("planche", "image", "video"):
        return retour(f"/projets/{projet_id}/scenes", err="Genre de prompts inconnu.")
    d = services.detail(projet_id)
    return rendu(
        request,
        "serie.html",
        {
            "d": d,
            "onglet": "generations",
            "genre": genre,
            "elements": services.prompts_en_serie(projet_id, genre),
        },
    )


# --------------------------------------------------------------------------
# Montage / sous-titres / export
# --------------------------------------------------------------------------

@app.get("/projets/{projet_id}/montage", response_class=HTMLResponse)
def page_montage(request: Request, projet_id: int):
    d = services.detail(projet_id)
    return rendu(
        request,
        "montage.html",
        {
            "d": d,
            "onglet": "montage",
            "plan": services.plan_montage(projet_id),
            "sous_titres": services.sous_titres(projet_id),
            "scenes_exportees": services.scenes_exportees(projet_id),
        },
    )


@app.post("/projets/{projet_id}/musique")
async def musique(projet_id: int, fichier: UploadFile = File(...)):
    donnees = await fichier.read()
    try:
        services.importer_musique(projet_id, fichier.filename or "", donnees)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/montage", err=str(exc))
    return retour(f"/projets/{projet_id}/montage", "Musique ajoutée au montage.")


@app.post("/projets/{projet_id}/musique/retirer")
def musique_retirer(projet_id: int):
    services.retirer_musique(projet_id)
    return retour(f"/projets/{projet_id}/montage", "Musique retirée.")


@app.post("/projets/{projet_id}/soustitres/proposer")
def soustitres_proposer(projet_id: int):
    nombre = services.proposer_sous_titres(projet_id)
    return retour(
        f"/projets/{projet_id}/montage",
        f"{nombre} ligne(s) proposée(s) depuis les dialogues du pack. "
        "Corrige les timings à la main avant d'exporter.",
    )


@app.post("/projets/{projet_id}/soustitres/enregistrer")
async def soustitres_enregistrer(projet_id: int, request: Request):
    formulaire = await request.form()
    lignes: list[dict] = []
    index = 0
    while f"texte_{index}" in formulaire:
        lignes.append(
            {
                "debut": float(str(formulaire.get(f"debut_{index}") or 0).replace(",", ".")),
                "fin": float(str(formulaire.get(f"fin_{index}") or 0).replace(",", ".")),
                "texte": formulaire.get(f"texte_{index}") or "",
            }
        )
        index += 1
    nombre = services.remplacer_sous_titres(projet_id, lignes)
    return retour(f"/projets/{projet_id}/montage", f"{nombre} sous-titre(s) enregistré(s).")


@app.post("/projets/{projet_id}/soustitres/importer")
async def soustitres_importer(projet_id: int, fichier: UploadFile = File(...)):
    brut = await fichier.read()
    try:
        contenu = brut.decode("utf-8")
    except UnicodeDecodeError:
        contenu = brut.decode("latin-1", errors="replace")
    try:
        nombre = services.importer_srt(projet_id, contenu)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/montage", err=str(exc))
    return retour(f"/projets/{projet_id}/montage", f"{nombre} sous-titre(s) importé(s).")


@app.post("/projets/{projet_id}/soustitres/exporter")
def soustitres_exporter(projet_id: int):
    try:
        chemin = services.exporter_srt(projet_id)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/montage", err=str(exc))
    return retour(f"/projets/{projet_id}/montage", f"SRT écrit : {Path(chemin).name}")


@app.post("/projets/{projet_id}/transcription")
def transcription(projet_id: int):
    try:
        resultat = services.preparer_transcription(projet_id)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/montage", err=str(exc))
    return retour(f"/projets/{projet_id}/montage", resultat["message"])


@app.post("/projets/{projet_id}/exporter")
def exporter(projet_id: int, incruster_srt: int = Form(0)):
    try:
        resultat = services.exporter(projet_id, bool(incruster_srt))
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/export", err=str(exc))
    if not resultat["reussi"]:
        return retour(f"/projets/{projet_id}/export", err=resultat["message"])
    return retour(f"/projets/{projet_id}/export", resultat["message"])


@app.post("/projets/{projet_id}/scenes/{scene_id}/ajuster-duree")
def ajuster_duree(projet_id: int, scene_id: int, retour_vers: str = Form("scenes")):
    try:
        bilan = services.ajuster_duree_scene(projet_id, scene_id)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/{retour_vers}", err=str(exc))
    return retour(
        f"/projets/{projet_id}/{retour_vers}",
        f"Scène {bilan['numero']} ramenée à {bilan['duree_voulue']} s : coupe de "
        f"{bilan['trim_debut']:.2f} s au début et {bilan['trim_fin']:.2f} s à la fin. "
        "Le fichier d'origine n'est pas modifié.",
    )


@app.post("/projets/{projet_id}/scenes/{scene_id}/exporter")
def exporter_une_scene(
    projet_id: int,
    scene_id: int,
    incruster_srt: int = Form(0),
    avec_musique: int = Form(0),
    retour_vers: str = Form("montage"),
):
    try:
        resultat = services.exporter_scene(
            projet_id, scene_id, bool(incruster_srt), bool(avec_musique)
        )
    except (services.ErreurMetier, fichiers.MelangeDeProjets) as exc:
        return retour(f"/projets/{projet_id}/{retour_vers}", err=str(exc))
    if not resultat["reussi"]:
        return retour(
            f"/projets/{projet_id}/{retour_vers}",
            err=f"Scène {resultat['numero']} : {resultat['message']}",
        )
    return retour(
        f"/projets/{projet_id}/{retour_vers}",
        f"Scène {resultat['numero']} exportée — {resultat['message']}",
    )


@app.post("/projets/{projet_id}/exporter-scenes")
def exporter_les_scenes(
    projet_id: int, incruster_srt: int = Form(0), avec_musique: int = Form(0)
):
    bilan = services.exporter_toutes_les_scenes(
        projet_id, bool(incruster_srt), bool(avec_musique)
    )
    if not bilan["resultats"]:
        return retour(
            f"/projets/{projet_id}/montage",
            err="Aucune scène ne dispose d'une vidéo à exporter.",
        )
    erreurs = " | ".join(
        f"scène {e['numero']} : {e['message']}" for e in bilan["echecs"][:5]
    )
    message = f"{bilan['reussis']} scène(s) exportée(s) en MP4."
    if bilan["ignorees"]:
        message += (
            f" Scène(s) sans vidéo, laissée(s) de côté : "
            + ", ".join(str(x) for x in bilan["ignorees"])
            + "."
        )
    return retour(f"/projets/{projet_id}/montage", message, erreurs)


@app.post("/projets/{projet_id}/scenes-archive")
def archiver_les_scenes(projet_id: int):
    try:
        chemin = services.archiver_scenes(projet_id)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/montage", err=str(exc))
    return RedirectResponse(
        f"/media/{projet_id}/export/{Path(chemin).name}", status_code=303
    )


@app.get("/projets/{projet_id}/export", response_class=HTMLResponse)
def page_export(request: Request, projet_id: int):
    d = services.detail(projet_id)
    return rendu(
        request,
        "export.html",
        {"d": d, "onglet": "export", "controle": services.controle_final(projet_id)},
    )


@app.post("/projets/{projet_id}/scenes/{scene_id}/images-cles")
def images_cles(projet_id: int, scene_id: int, nombre: int = Form(6)):
    try:
        fichiers_crees = services.extraire_images_cles(projet_id, scene_id, nombre)
    except services.ErreurMetier as exc:
        return retour(f"/projets/{projet_id}/scenes", err=str(exc))
    except ValueError as exc:
        return retour(f"/projets/{projet_id}/scenes", err=str(exc))
    return retour(
        f"/projets/{projet_id}/scenes",
        f"{len(fichiers_crees)} image(s) clé extraite(s) dans le dossier de la scène.",
    )


# --------------------------------------------------------------------------
# Bibliotheque, metaprompt, reglages
# --------------------------------------------------------------------------

@app.get("/bibliotheque", response_class=HTMLResponse)
def page_bibliotheque(request: Request):
    return rendu(request, "bibliotheque.html", {"references": services.bibliotheque()})


@app.post("/bibliotheque/ajouter")
def bibliotheque_ajouter(
    url: str = Form(""), titre: str = Form(""), notes: str = Form(""),
    indicateurs: str = Form(""),
):
    try:
        services.ajouter_reference(url, titre, notes, indicateurs)
    except services.ErreurMetier as exc:
        return retour("/bibliotheque", err=str(exc))
    return retour("/bibliotheque", "Référence ajoutee.")


@app.post("/projets/{projet_id}/supprimer")
def projet_supprimer(projet_id: int, confirmation: str = Form("")):
    """Arrete le projet et efface tout : base et fichiers. Sans retour."""
    try:
        bilan = services.supprimer_projet(projet_id, confirmation)
    except services.SuppressionRefusee as exc:
        return retour(f"/projets/{projet_id}/atelier", err=str(exc))
    c = bilan["compte"]
    fichiers_dits = (
        "son dossier a été effacé" if bilan["fichiers_effaces"]
        else f"son dossier reste à effacer à la main : {bilan['dossier']}"
    )
    return retour(
        "/",
        f"« {bilan['titre']} » supprimé : {c['scenes']} scène(s), "
        f"{c['personnages']} personnage(s), {c['taches']} tâche(s), et "
        f"{fichiers_dits}.",
    )


@app.post("/bibliotheque/{ref_id}/supprimer")
def bibliotheque_supprimer(ref_id: int):
    services.supprimer_reference(ref_id)
    return retour("/bibliotheque", "Référence supprimée.")


@app.get("/metaprompt", response_class=HTMLResponse)
def page_metaprompt(request: Request):
    return rendu(
        request,
        "metaprompt.html",
        {
            "metas": services.tous_les_metaprompts(),
            "projets": services.lister_projets(False),
        },
    )


@app.post("/metaprompt")
async def metaprompt_enregistrer(
    texte: str = Form(""),
    type_histoire: str = Form(""),
    fichier: UploadFile | None = File(None),
):
    contenu = texte or ""
    if fichier is not None and fichier.filename:
        brut = await fichier.read()
        try:
            contenu = brut.decode("utf-8")
        except UnicodeDecodeError:
            contenu = brut.decode("latin-1", errors="replace")
    try:
        resultat = services.enregistrer_metaprompt(contenu, type_histoire)
    except services.ErreurMetier as exc:
        return retour("/metaprompt", err=str(exc))
    choisi = services.type_histoire_valide(type_histoire)
    type_detecte = parser_v6.detecter_type(contenu)
    # Le detecteur ne connait que les reperes du V6 : pour les autres familles,
    # un « inconnu » ne veut rien dire, on ne crie donc pas au loup.
    avertissement = (
        ""
        if type_detecte == "metaprompt" or choisi != config.TYPE_PROJET_DEFAUT
        else "Attention : ce texte ne ressemble pas au métaprompt V6 "
        f"(détecté comme « {type_detecte} »). Il a quand même été conservé tel quel."
    )
    return retour(
        "/metaprompt",
        f"Métaprompt « {config.TYPES_PROJET[choisi]} » conservé tel quel "
        f"({resultat['taille']} caractères, empreinte {resultat['empreinte']}).",
        avertissement,
    )


@app.get("/reglages")
def page_reglages_ancienne():
    return RedirectResponse("/admin", status_code=303)


def _etiquettes_apprises() -> list[tuple[str, str]]:
    from . import lecteur_souple

    return sorted(
        (cle, lecteur_souple.CHAMPS[champ])
        for cle, champ in lecteur_souple.apprises().items()
    )


@app.post("/admin/etiquettes/oublier")
def etiquette_oublier(cle: str = Form("")):
    from . import lecteur_souple

    lecteur_souple.oublier(cle)
    return retour("/admin", f"Étiquette « {cle} » oubliée : elle sera de nouveau proposée.")


@app.get("/admin", response_class=HTMLResponse)
def page_admin(request: Request):
    return rendu(
        request,
        "admin.html",
        {
            "comptes": comptes.tableau(),
            "sites": comptes.SITES,
            "jours": comptes.JOURS,
            "rotation": {s: comptes.rotation_lisible(s) for s in comptes.SITES},
            "strategie": str(reglages.lire("strategie_rotation")),
            "champs": reglages.tous(),
            "groupes": reglages.GROUPES,
            "outils": media.outils_disponibles(),
            "navigateur": navigateur.session().etat(),
            "mode_navigateur": str(reglages.lire("navigateur_mode")),
            "fenetres_ouvertes": navigateur.fenetres_restees_ouvertes(),
            "etiquettes_apprises": _etiquettes_apprises(),
            "cdp_url": str(reglages.lire("cdp_url")),
            "selecteurs": json.dumps(selecteurs.charger(), ensure_ascii=False, indent=2),
            "modele_selecteurs": selecteurs.modele_surcharge(),
            "infos": {
                "hote": config.HOTE,
                "port": config.PORT,
                "donnees": str(config.DOSSIER_DONNEES),
                "base": str(config.CHEMIN_BASE),
                "simultanees": config.GENERATIONS_SIMULTANEES,
                "version": __version__,
            },
            "journal": [
                dict(j) for j in db.q("SELECT * FROM journal ORDER BY id DESC LIMIT 100")
            ],
        },
    )


@app.post("/admin/comptes/ajouter")
def compte_ajouter(
    site: str = Form(...),
    libelle: str = Form(""),
    email: str = Form(""),
    proprietaire: str = Form(""),
):
    try:
        comptes.ajouter(site, libelle, email, proprietaire)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    return retour(
        "/admin",
        f"Compte « {libelle} » ajouté. Clique sur « Ouvrir et me connecter » pour y "
        "ouvrir une session.",
    )


@app.post("/admin/comptes/{compte_id}/ouvrir")
def compte_ouvrir(compte_id: int):
    try:
        c = comptes.compte(compte_id)
        adresse = comptes.ouvrir_pour_connexion(compte_id)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    except navigateur.NavigateurOccupe as exc:
        return retour("/admin", err=str(exc))
    except navigateur.NavigateurIndisponible as exc:
        return retour("/admin", err=f"Navigateur indisponible : {exc}")
    return retour(
        "/admin",
        f"{comptes.SITES[c['site']]['nom']} ouvert pour « {c['libelle']} » ({adresse}). "
        "Connecte-toi dans cette fenêtre, puis reviens confirmer.",
    )


@app.post("/admin/comptes/{compte_id}/verifier")
def compte_verifier(compte_id: int):
    try:
        resultat = comptes.verifier(compte_id)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    except navigateur.NavigateurOccupe as exc:
        return retour("/admin", err=str(exc))
    except navigateur.NavigateurIndisponible as exc:
        return retour("/admin", err=f"Navigateur indisponible : {exc}")
    c = resultat["compte"]
    message = f"{c['libelle']} : {resultat['libelle']}. {resultat['detail']}"
    if resultat["etat"] == "connecte":
        return retour("/admin", message)
    return retour("/admin", err=message)


@app.post("/admin/comptes/{compte_id}/marquer")
def compte_marquer(compte_id: int, etat: str = Form(...)):
    try:
        c = comptes.marquer(compte_id, etat)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    return retour(
        "/admin",
        f"« {c['libelle'] } » marqué « {c['libelle_etat']} » d'après ta confirmation. "
        "La preuve définitive reste la première génération réussie.",
    )


@app.post("/admin/comptes/{compte_id}/modifier")
def compte_modifier(
    compte_id: int,
    libelle: str = Form(""),
    email: str = Form(""),
    actif: str = Form(""),
    chat_url: str = Form(""),
):
    try:
        comptes.modifier(compte_id, libelle, email, actif == "1", chat_url)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    return retour("/admin", "Compte enregistré.")


@app.post("/admin/comptes/{compte_id}/deplacer")
def compte_deplacer(compte_id: int, sens: str = Form("monter")):
    try:
        comptes.deplacer(compte_id, sens)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    return retour("/admin", "Ordre mis à jour : le premier compte disponible travaille.")


@app.post("/admin/comptes/{compte_id}/reinitialisation")
def compte_reinitialisation(
    compte_id: int, jour: str = Form(""), heure: str = Form("")
):
    try:
        c = comptes.definir_reinitialisation(compte_id, jour, heure)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    if c["horaire"]:
        return retour(
            "/admin",
            f"« {c['libelle']} » : quota rechargé {c['horaire']}."
            + (f" Prochaine recharge {c['prochaine_recharge']}." if not c["quota_dispo"] else ""),
        )
    return retour("/admin", f"« {c['libelle']} » : horaire retiré, délai global appliqué.")


@app.post("/local/comptes/{site}/a-court")
def compte_a_court(site: str, projet: int = 0):
    """Tu as vu que le compte actif n'a plus de quota : on passe au suivant.

    Ce bouton NE LANCE RIEN. Il met seulement le compte de cote pour que la
    prochaine generation, demandee par toi, parte sur le compte suivant.
    """
    if site not in comptes.SITES:
        return {"ok": False, "message": "Fournisseur inconnu."}
    ligne = db.q1("SELECT comptes_de FROM projets WHERE id=?", (projet,))
    pool = ligne["comptes_de"] if ligne else None
    avant = comptes.etat_rotation(site, pool)
    if not avant["actif_id"]:
        return {"ok": False, "message": f"Aucun compte actif pour {site}."}
    if not avant["secours"]:
        return {
            "ok": False,
            "message": (
                f"« {avant['actif']} » est le seul compte {site} disponible. "
                "Rien n'a été changé : le mettre de côté arrêterait tout. "
                "Ajoute un autre compte dans Admin, ou attends sa recharge."
            ),
        }
    comptes.marquer_quota_epuise(
        avant["actif_id"], "Déclaré à court par toi, depuis l'atelier."
    )
    apres = comptes.etat_rotation(site, pool)
    return {
        "ok": True,
        "message": (
            f"« {avant['actif']} » est mis de côté. La prochaine génération "
            f"{site} que TU lanceras partira sur « {apres['actif']} ». "
            "Rien n'a été lancé ni relancé."
        ),
    }


@app.post("/admin/comptes/{compte_id}/rearmer")
def compte_rearmer(compte_id: int):
    try:
        c = comptes.rearmer(compte_id)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    return retour("/admin", f"« {c['libelle']} » réarmé : il repasse dans la rotation.")


@app.post("/admin/comptes/{compte_id}/supprimer")
def compte_supprimer(compte_id: int, effacer_le_profil: str = Form("")):
    try:
        comptes.supprimer(compte_id, effacer_le_profil == "1")
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    return retour("/admin", "Compte supprimé.")


@app.post("/admin/comptes/{compte_id}/profil-separe")
def compte_profil_separe(compte_id: int, copier: str = Form("")):
    try:
        c = comptes.detacher_profil(compte_id, copier == "1")
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    suite = (
        "La session a été copiée : ouvre le site et change de compte dedans."
        if copier == "1"
        else "Le profil est vierge : clique sur « Ouvrir et me connecter » et choisis "
        "le bon compte Google."
    )
    return retour(
        "/admin",
        f"« {c['libelle']} » a maintenant son propre profil « {c['profil']} ». {suite}",
    )


@app.post("/admin/comptes/{compte_id}/deconnecter")
def compte_deconnecter(compte_id: int, confirmation: str = Form("")):
    if confirmation.strip().upper() != "EFFACER":
        return retour(
            "/admin",
            err="Pour déconnecter ce compte, écris EFFACER dans le champ de confirmation.",
        )
    try:
        chemin = comptes.effacer_profil(compte_id)
    except comptes.ErreurCompte as exc:
        return retour("/admin", err=str(exc))
    return retour("/admin", f"Profil effacé ({chemin}) : ce compte est déconnecté.")


@app.post("/admin/navigateur/menage")
def admin_navigateur_menage():
    try:
        navigateur.session().fermer()
    except Exception:
        pass
    nombre = navigateur.fermer_fenetres_restees_ouvertes()
    if not nombre:
        return retour("/admin", "Aucune fenêtre Videroo restée ouverte.")
    return retour(
        "/admin",
        f"{nombre} fenêtre(s) Videroo fermée(s). Tes connexions sont conservées : "
        "elles sont enregistrées dans les profils.",
    )


@app.post("/admin/navigateur/tester")
def admin_navigateur_tester():
    mode = str(reglages.lire("navigateur_mode"))
    try:
        navigateur.session().fermer()
        navigateur.session().ouvrir()
    except navigateur.NavigateurOccupe as exc:
        return retour("/admin", err=str(exc))
    except navigateur.NavigateurIndisponible as exc:
        return retour("/admin", err=str(exc))
    if mode == "chrome_existant":
        return retour(
            "/admin",
            f"Branchement réussi sur ton Chrome ({reglages.lire('cdp_url')}). "
            "Clique maintenant sur « Vérifier » pour chaque compte.",
        )
    return retour(
        "/admin",
        "Profil dédié ouvert. Connecte-toi dans cette fenêtre, puis clique « Vérifier ».",
    )


@app.post("/admin/navigateur/{action}")
def admin_navigateur(action: str):
    try:
        if action == "ouvrir":
            navigateur.session().ouvrir(affichage="fenetre")
            return retour("/admin", "Navigateur dédié ouvert.")
        if action == "fermer":
            navigateur.session().fermer()
            return retour("/admin", "Navigateur dédié fermé.")
    except (navigateur.NavigateurIndisponible, navigateur.NavigateurOccupe) as exc:
        return retour("/admin", err=str(exc))
    return retour("/admin", err=f"Action inconnue : {action}")


@app.post("/admin/reglages")
async def admin_reglages(request: Request):
    formulaire = await request.form()
    modifies, erreurs = [], []
    for champ in reglages.CHAMPS:
        cle = champ["cle"]
        if champ["type"] == "booleen":
            # Une case décochée n'envoie rien : on ne la traite que si le
            # formulaire la contenait vraiment (témoin caché).
            if f"{cle}__present" not in formulaire:
                continue
            valeur = "1" if formulaire.get(cle) else "0"
        elif cle not in formulaire:
            continue
        else:
            valeur = formulaire.get(cle)
        avant = str(reglages.lire(cle))
        try:
            apres = reglages.definir(cle, valeur)
        except reglages.ValeurRefusee as exc:
            erreurs.append(str(exc))
            continue
        if str(apres) != avant:
            modifies.append(champ["libelle"])
    if modifies:
        db.journaliser("Réglages modifiés : " + ", ".join(modifies))
    return retour(
        "/admin",
        f"{len(modifies)} réglage(s) enregistré(s)." if modifies else "Aucun changement.",
        " | ".join(erreurs),
    )


@app.post("/admin/reglages/reinitialiser")
def admin_reglages_reset():
    nombre = reglages.reinitialiser()
    db.journaliser("Réglages remis aux valeurs par défaut.")
    return retour("/admin", f"{nombre} réglage(s) remis à leur valeur par défaut.")


@app.post("/reglages/selecteurs")
def reglages_selecteurs(contenu: str = Form("")):
    try:
        donnees = json.loads(contenu)
    except ValueError as exc:
        return retour("/admin", err=f"JSON invalide : {exc}")
    chemin = selecteurs.enregistrer_surcharge(donnees)
    db.journaliser(f"Sélecteurs enregistrés dans {Path(chemin).name}.")
    return retour("/admin", f"Sélecteurs enregistrés dans {Path(chemin).name}.")


# --------------------------------------------------------------------------
# Medias et API d'etat
# --------------------------------------------------------------------------

@app.get("/media/{projet_id}/{sous_chemin:path}")
def servir_media(projet_id: int, sous_chemin: str):
    try:
        pr = services.projet(projet_id)
    except services.ErreurMetier:
        return JSONResponse({"erreur": "projet inconnu"}, status_code=404)
    cible = (Path(pr["dossier"]) / sous_chemin).resolve()
    try:
        fichiers.verifier_appartenance(cible, projet_id, pr["dossier"])
    except fichiers.MelangeDeProjets:
        return JSONResponse({"erreur": "chemin refuse"}, status_code=403)
    if not cible.exists() or not cible.is_file():
        return JSONResponse({"erreur": "fichier absent"}, status_code=404)
    return FileResponse(cible)


@app.get("/api/projets/{projet_id}/etat")
def api_etat(projet_id: int):
    active = runner.tache_active()
    taches = db.q(
        "SELECT statut, COUNT(*) n FROM taches WHERE projet_id=? GROUP BY statut",
        (projet_id,),
    )
    scenes = db.q(
        "SELECT numero, "
        " CASE WHEN video_path IS NOT NULL AND video_path<>'' THEN 'video'"
        "      WHEN image_path IS NOT NULL AND image_path<>'' THEN 'image'"
        "      WHEN valide=1 THEN 'validee' ELSE 'a_preparer' END AS etat"
        " FROM scenes WHERE projet_id=? ORDER BY numero",
        (projet_id,),
    )
    return {
        "statut_projet": services.projet(projet_id)["statut"],
        "production": runner.etat_production(projet_id),
        "tache_active": dict(active) if active else None,
        "taches": {r["statut"]: r["n"] for r in taches},
        "scenes": [dict(r) for r in scenes],
    }


# --------------------------------------------------------------------------
# Atelier : la vue simple, de haut en bas
# --------------------------------------------------------------------------

@app.get("/projets/{projet_id}/atelier", response_class=HTMLResponse)
def page_atelier(request: Request, projet_id: int):
    return rendu(
        request,
        "atelier.html",
        {
            "d": services.detail(projet_id),
            "onglet": "atelier",
            "fil": services.fil_production(projet_id),
            "serie": services.serie_du_projet(projet_id),
            "navigateur": navigateur.session().etat(),
            "mode_defaut": reglages.lire("mode_defaut"),
        },
    )


@app.get("/local/projets/{projet_id}/fil")
def api_fil(projet_id: int):
    """État complet de l'atelier, relu en boucle par la page sans la recharger."""
    try:
        return services.fil_production(projet_id)
    except services.ErreurMetier as exc:
        return JSONResponse({"erreur": str(exc)}, status_code=404)


def _lancer_la_chaine(projet_id: int, mode: str) -> dict:
    """Démarre la production complète. Rend un bilan, sans redirection."""
    if mode not in MODES:
        return {"ok": False, "message": "Mode inconnu."}
    if mode == "auto":
        # On n'ouvre AUCUN navigateur ici : la tâche ouvrira elle-même le profil du
        # compte qu'elle utilise. Ouvrir « principal » en plus, c'était un second
        # Chrome pour rien — plusieurs centaines de Mo sur une machine de 16 Go.
        pret, souci = navigateur.playwright_disponible()
        if not pret:
            return {
                "ok": False,
                "message": f"Le mode automatique a besoin du navigateur dédié : {souci}",
            }
        # Une fenêtre oubliée d'une session précédente garde le profil verrouillé et
        # peut réapparaître à l'écran : on fait le ménage avant de commencer.
        fermees = navigateur.fermer_fenetres_restees_ouvertes()
        if fermees:
            db.journaliser(
                f"{fermees} fenêtre(s) Chrome oubliée(s) fermée(s) avant de lancer.",
                projet_id,
            )
    # Le clic sur « Lancer » vaut validation des scènes complètes ; celles qui
    # posent problème restent de côté, nommées une par une.
    bilan = services.valider_projet(projet_id)
    refus = ""
    if bilan["refusees"]:
        refus = " Scènes laissées de côté : " + " | ".join(
            f"{r['numero']} ({', '.join(r['problemes'])})" for r in bilan["refusees"]
        )
    try:
        resultat = runner.demarrer_production(projet_id, mode)
    except runner.GenerationRefusee as exc:
        return {"ok": False, "message": str(exc) + refus}
    if not resultat.get("taches"):
        arret = runner.phrase_des_blocages(resultat.get("etat") or {})
        return {
            "ok": True,
            "message": (arret or "Chaîne lancée, mais rien à produire pour l'instant.")
            + refus,
        }
    combien = len(resultat["taches"])
    ensemble = " (ChatGPT et Grok en parallèle)" if combien > 1 else ""
    return {
        "ok": True,
        "message": (
            f"{len(bilan['validees'])} scène(s) validée(s). En cours{ensemble} : "
            f"{resultat.get('libelle') or 'première étape'}." + refus
        ),
    }


@app.post("/local/projets/{projet_id}/action")
async def api_action(projet_id: int, request: Request):
    """Les boutons de l'atelier. Chaque action reste explicite et nommée."""
    corps = await request.json()
    action = (corps.get("action") or "").strip()
    mode = corps.get("mode") or reglages.lire("mode_defaut")
    genre = corps.get("genre") or ""
    cible_id = int(corps.get("cible_id") or 0)
    fournisseur = corps.get("fournisseur") or ("grok" if genre == "video" else "chatgpt")

    try:
        if action == "lancer":
            return _lancer_la_chaine(projet_id, mode)
        if action == "arreter":
            runner.arreter_production(projet_id)
            # Plus rien à piloter : on rend la mémoire des navigateurs à la machine.
            with contextlib.suppress(Exception):
                navigateur.fermer_toutes()
            return {
                "ok": True,
                "message": (
                    "Chaîne arrêtée et navigateur fermé. Les résultats déjà obtenus "
                    "sont conservés."
                ),
            }
        if action == "generer":
            tache_id = runner.demander(
                projet_id, genre, cible_id,
                fournisseur=fournisseur, mode=mode, origine="auto",
            )
            return {"ok": True, "message": f"Génération lancée (tâche #{tache_id})."}
        if action == "regenerer":
            tache_id = runner.regenerer(projet_id, genre, cible_id, fournisseur, mode)
            return {
                "ok": True,
                "message": (
                    f"Régénération explicite demandée (tâche #{tache_id}). "
                    "L'ancien fichier est conservé."
                ),
            }
        if action == "reessayer":
            tache_id = runner.reessayer(int(corps.get("tache_id") or 0))
            return {"ok": True, "message": f"Réessai explicite en file (tâche #{tache_id})."}
        if action == "recuperer":
            # Va LIRE chez le fournisseur un résultat déjà produit. Rien n'est
            # envoyé, donc aucun quota n'est consommé : c'est le geste à faire
            # avant un réessai, pas après.
            return runner.recuperer_resultat(
                projet_id, genre, cible_id, int(corps.get("rang") or 1)
            )
    except (runner.GenerationRefusee, fichiers.MelangeDeProjets) as exc:
        return {"ok": False, "message": str(exc)}
    except services.ErreurMetier as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": False, "message": f"Action inconnue : {action or '(vide)'}."}


@app.get("/projets/{projet_id}/telecharger/scene/{scene_id}")
def telecharger_une_video(projet_id: int, scene_id: int):
    """La vidéo brute d'une scène, telle que Grok l'a rendue."""
    try:
        chemin, nom = services.video_a_telecharger(projet_id, scene_id)
    except (services.ErreurMetier, fichiers.MelangeDeProjets) as exc:
        return retour(f"/projets/{projet_id}/atelier", err=str(exc))
    return FileResponse(chemin, filename=nom, media_type="video/mp4")


@app.get("/projets/{projet_id}/telecharger/videos")
def telecharger_toutes_les_videos(projet_id: int):
    """Toutes les vidéos obtenues, en une archive, sans ré-encodage."""
    try:
        chemin, nom = services.archiver_videos(projet_id)
    except (services.ErreurMetier, fichiers.MelangeDeProjets) as exc:
        return retour(f"/projets/{projet_id}/atelier", err=str(exc))
    return FileResponse(chemin, filename=nom, media_type="application/zip")


@app.get("/projets/{projet_id}/telecharger/miniature")
def telecharger_la_miniature(projet_id: int):
    """La miniature TikTok telle que ChatGPT l'a rendue, sans retouche."""
    try:
        chemin, nom = services.miniature_a_telecharger(projet_id)
    except (services.ErreurMetier, fichiers.MelangeDeProjets) as exc:
        return retour(f"/projets/{projet_id}/atelier", err=str(exc))
    genre, _ = mimetypes.guess_type(chemin.name)
    return FileResponse(chemin, filename=nom, media_type=genre or "image/png")


@app.get("/api/sante")
def api_sante():
    return {
        "version": __version__,
        "outils": media.outils_disponibles(),
        "navigateur": navigateur.session().etat(),
        "base": str(config.CHEMIN_BASE),
        "workers": [
            {"fournisseur": w.fournisseur, "vivant": w.is_alive()}
            for w in (_worker or [])
        ],
    }
