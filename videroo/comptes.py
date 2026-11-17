"""Comptes ChatGPT et Grok : un profil navigateur par compte, rotation sur quota.

Chaque compte a son propre dossier de profil, donc sa propre session. Plusieurs
abonnements Grok peuvent ainsi cohabiter, et la production passe au suivant quand
le quota du compte courant est atteint — mais seulement si rien n'a encore été
envoyé, pour ne jamais payer deux fois la même génération.
"""
from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import datetime, time, timedelta

from . import adresses, config, db, fichiers, reglages
from .adapters import navigateur, selecteurs

SITES = {
    "chatgpt": {
        "nom": "ChatGPT",
        "role": "Planches de référence et images de départ",
        "aide": "Connecte-toi avec le compte qui a accès à la génération d'images.",
    },
    "grok": {
        "nom": "Grok",
        "role": "Animation des scènes (Imagine Video)",
        "aide": "Connecte-toi avec le compte qui a accès à Imagine.",
    },
}

LIBELLES = {
    "connecte": "connecté",
    "deconnecte": "non connecté",
    "incertain": "à confirmer",
    "": "non vérifié",
}


JOURS = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]


class ErreurCompte(RuntimeError):
    pass


def _heure(texte: str | None) -> time | None:
    if not texte:
        return None
    try:
        heures, minutes = str(texte).strip().split(":")
        return time(int(heures), int(minutes))
    except (ValueError, TypeError):
        return None


def prochaine_occurrence(apres: datetime, jour: int, moment: time) -> datetime:
    """Prochaine fois que l'on passe ce jour de semaine à cette heure, après `apres`."""
    candidat = datetime.combine(apres.date(), moment)
    candidat += timedelta(days=(jour - candidat.weekday()) % 7)
    if candidat <= apres:
        candidat += timedelta(days=7)
    return candidat


def prochaine_recharge(ligne: dict) -> datetime | None:
    """Quand ce compte retrouvera son quota, d'après son horaire hebdomadaire."""
    jour = ligne.get("reset_jour")
    moment = _heure(ligne.get("reset_heure"))
    if jour is None or moment is None:
        return None
    depuis = ligne.get("quota_epuise_le")
    base = datetime.now()
    if depuis:
        try:
            base = datetime.fromisoformat(depuis)
        except ValueError:
            pass
    return prochaine_occurrence(base, int(jour), moment)


def horaire_lisible(ligne: dict) -> str:
    jour = ligne.get("reset_jour")
    moment = _heure(ligne.get("reset_heure"))
    if jour is None or moment is None:
        return ""
    return f"tous les {JOURS[int(jour)]} à {moment.strftime('%H:%M')}"


def definir_reinitialisation(
    compte_id: int, jour: str | int | None, heure: str | None
) -> dict:
    """Fixe l'horaire hebdomadaire de recharge du quota de ce compte."""
    c = compte(compte_id)
    if jour in (None, "", "aucun"):
        db.exec1(
            "UPDATE comptes_ia SET reset_jour=NULL, reset_heure=NULL WHERE id=?",
            (compte_id,),
        )
        db.journaliser(
            f"Compte « {c['libelle']} » : horaire de recharge retiré, retour au délai "
            "global."
        )
        return compte(compte_id)
    try:
        numero = int(jour)
    except (TypeError, ValueError) as exc:
        raise ErreurCompte("Jour de la semaine invalide.") from exc
    if not 0 <= numero <= 6:
        raise ErreurCompte("Jour de la semaine invalide.")
    moment = _heure(heure)
    if moment is None:
        raise ErreurCompte("Heure attendue au format HH:MM, par exemple 14:34.")
    db.exec1(
        "UPDATE comptes_ia SET reset_jour=?, reset_heure=? WHERE id=?",
        (numero, moment.strftime("%H:%M"), compte_id),
    )
    db.journaliser(
        f"Compte « {c['libelle']} » : quota rechargé tous les {JOURS[numero]} à "
        f"{moment.strftime('%H:%M')}."
    )
    return compte(compte_id)


# ---------------------------------------------------------------------------
# Retour du quota : ce que le fournisseur annonce, sinon ton horaire, sinon rien
# ---------------------------------------------------------------------------

_NOMBRES = {"un": 1, "une": 1, "a": 1, "an": 1, "one": 1, "deux": 2, "two": 2,
            "trois": 3, "three": 3, "quatre": 4, "four": 4}
_RE_DELAI = re.compile(
    r"\b(?:dans|in|after|apres)\s+(?:environ\s+|about\s+)?"
    r"(\d+|un|une|a|an|one|deux|two|trois|three|quatre|four)\s*"
    r"(heures?|hours?|hrs?|h|minutes?|mins?|min)\b"
    r"(?:\s*(?:et|and)?\s*(\d+)\s*(minutes?|mins?|min)\b)?"
)


def _plat(texte: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (texte or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def delai_annonce(texte: str) -> timedelta | None:
    """Le délai que le fournisseur annonce lui-même (« dans 1 heure »), ou None.

    On ne devine rien : sans phrase de ce genre dans SON message, pas de délai.
    """
    trouve = _RE_DELAI.search(_plat(texte))
    if not trouve:
        return None
    nombre = trouve.group(1)
    quantite = int(nombre) if nombre.isdigit() else _NOMBRES[nombre]
    delai = (
        timedelta(hours=quantite) if trouve.group(2).startswith("h")
        else timedelta(minutes=quantite)
    )
    if trouve.group(3):
        delai += timedelta(minutes=int(trouve.group(3)))
    return delai if timedelta(0) < delai <= timedelta(days=8) else None


#: Marge après l'heure annoncée : « dans 1 heure » est arrondi par le fournisseur.
MARGE_RETOUR = timedelta(minutes=3)


def retour_prevu(ligne: dict) -> tuple[datetime | None, str]:
    """Quand ce compte retrouve son quota, et d'où vient cette heure.

    « annonce » : le fournisseur l'a écrit. « horaire » : ta recharge
    hebdomadaire réglée dans Admin. « estimation » : seulement le délai global
    des réglages — une supposition, à ne jamais présenter comme une annonce.
    """
    if not ligne.get("quota_epuise_le"):
        return None, ""
    annonce = ligne.get("quota_retour_le")
    if annonce:
        try:
            return datetime.fromisoformat(annonce), "annonce"
        except ValueError:
            pass
    recharge = prochaine_recharge(ligne)
    if recharge is not None:
        return recharge, "horaire"
    try:
        moment = datetime.fromisoformat(ligne["quota_epuise_le"])
    except ValueError:
        return None, ""
    return moment + timedelta(hours=reglages.lire_float("quota_reset_heures")), "estimation"


def quand_lisible(moment: datetime) -> str:
    """« vers 00:41 », ou « mardi 17/11 vers 14:34 » si ce n'est pas aujourd'hui."""
    if moment.date() == datetime.now().date():
        return f"vers {moment.strftime('%H:%M')}"
    return f"{JOURS[moment.weekday()]} {moment.strftime('%d/%m')} vers {moment.strftime('%H:%M')}"


def prochain_retour(site: str, proprietaire: str | None = None) -> tuple[datetime | None, str, str]:
    """Le premier compte de ce pool à revenir : (heure, source, libellé du compte)."""
    meilleurs: list[tuple[datetime, str, str]] = []
    for c in liste(site):
        if not c["actif"] or c["quota_dispo"] or not _meme_proprietaire(c, proprietaire):
            continue
        moment, source = retour_prevu(c)
        if moment is not None:
            meilleurs.append((moment, source, c["libelle"]))
    if not meilleurs:
        return None, "", ""
    return min(meilleurs, key=lambda m: m[0])


def a_des_comptes(site: str, proprietaire: str | None = None) -> bool:
    """Ce pool a-t-il au moins un compte actif, disponible ou non ?"""
    return any(c["actif"] and _meme_proprietaire(c, proprietaire) for c in liste(site))


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------

def assurer_comptes_par_defaut() -> None:
    """Chaque site a au moins un compte, rattaché au profil historique."""
    for site in SITES:
        if db.q1("SELECT id FROM comptes_ia WHERE site=?", (site,)):
            continue
        db.exec1(
            "INSERT INTO comptes_ia (site, libelle, email, profil, ordre, actif)"
            " VALUES (?,?,?,?,?,1)",
            (site, "Compte principal", "", config.PROFIL_PRINCIPAL, 0),
        )
        db.journaliser(
            f"Compte {SITES[site]['nom']} « Compte principal » créé sur le profil "
            "historique : ta session existante est conservée."
        )


def liste(site: str | None = None) -> list[dict]:
    assurer_comptes_par_defaut()
    if site:
        lignes = db.q(
            "SELECT * FROM comptes_ia WHERE site=? ORDER BY ordre, id", (site,)
        )
    else:
        lignes = db.q("SELECT * FROM comptes_ia ORDER BY site, ordre, id")
    sortie = []
    for l in lignes:
        sortie.append(_enrichir(dict(l)))
    return sortie


def partage_avec(compte_id: int, profil: str) -> list[str]:
    """Autres comptes qui utilisent le même dossier de profil."""
    return [
        f"{SITES[l['site']]['nom']} · {l['libelle']}"
        for l in db.q(
            "SELECT site, libelle FROM comptes_ia WHERE profil=? AND id<>?"
            " ORDER BY site, ordre",
            (profil, compte_id),
        )
    ]


def profil_libre(site: str, libelle: str) -> str:
    base = f"{site}-{fichiers.slug(libelle, 'compte')}"
    profil, rang = base, 1
    while db.q1("SELECT id FROM comptes_ia WHERE profil=?", (profil,)):
        rang += 1
        profil = f"{base}-{rang}"
    return profil


def detacher_profil(compte_id: int, copier: bool = False) -> dict:
    """Donne à ce compte son propre dossier de profil, isolé des autres.

    Sans copie, le profil est vierge : le site redemandera la connexion, et
    surtout Google ne réutilisera pas la session d'un autre compte. Avec copie,
    la session actuelle est dupliquée — pratique pour repartir d'un profil qui
    marche puis changer de compte dedans.
    """
    c = compte(compte_id)
    ancien = c["profil"]
    nouveau = profil_libre(c["site"], c["libelle"])
    navigateur.session().fermer(ancien)

    source = config.chemin_profil(ancien)
    cible = config.chemin_profil(nouveau)
    cible.mkdir(parents=True, exist_ok=True)
    if copier and source.is_dir():
        shutil.copytree(
            source,
            cible,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "Singleton*", "lockfile", "*.lock", "Crashpad", "ShaderCache",
                "GrShaderCache", "GraphiteDawnCache", "component_crx_cache",
            ),
        )
    db.exec1(
        "UPDATE comptes_ia SET profil=?, etat='', etat_source='', verifie_le=NULL"
        " WHERE id=?",
        (nouveau, compte_id),
    )
    db.journaliser(
        f"Compte « {c['libelle']} » déplacé sur son propre profil « {nouveau} »"
        + (" (session copiée)." if copier else " (profil vierge : reconnecte-toi).")
    )
    return compte(compte_id)


def _enrichir(d: dict) -> dict:
    d["libelle_etat"] = LIBELLES.get(d.get("etat") or "", d.get("etat"))
    d["quota_dispo"] = quota_disponible(d)
    d["chemin_profil"] = str(config.chemin_profil(d["profil"]))
    d["horaire"] = horaire_lisible(d)
    d["partage_avec"] = partage_avec(d["id"], d["profil"])
    recharge = prochaine_recharge(d)
    d["prochaine_recharge"] = (
        f"{JOURS[recharge.weekday()]} {recharge.strftime('%d/%m à %H:%M')}"
        if recharge
        else ""
    )
    d["prochaine_recharge_iso"] = recharge.isoformat(timespec="minutes") if recharge else ""
    retour, source = retour_prevu(d)
    d["retour_source"] = source if (retour and not d["quota_dispo"]) else ""
    d["retour_lisible"] = quand_lisible(retour) if d["retour_source"] else ""
    return d


def compte(compte_id: int) -> dict:
    ligne = db.q1("SELECT * FROM comptes_ia WHERE id=?", (compte_id,))
    if not ligne:
        raise ErreurCompte(f"Compte {compte_id} inconnu.")
    return _enrichir(dict(ligne))


def quota_disponible(ligne: dict) -> bool:
    """Disponible selon l'horaire hebdomadaire du compte, sinon selon le délai global."""
    epuise = ligne.get("quota_epuise_le")
    if not epuise:
        return True
    annonce = ligne.get("quota_retour_le")
    if annonce:
        # Le fournisseur a dit quand revenir : c'est lui qui sait.
        try:
            return datetime.now() >= datetime.fromisoformat(annonce)
        except ValueError:
            pass
    recharge = prochaine_recharge(ligne)
    if recharge is not None:
        return datetime.now() >= recharge
    try:
        moment = datetime.fromisoformat(epuise)
    except ValueError:
        return True
    heures = reglages.lire_float("quota_reset_heures")
    return datetime.now() >= moment + timedelta(hours=heures)


def ajouter(site: str, libelle: str, email: str = "", proprietaire: str = "") -> int:
    if site not in SITES:
        raise ErreurCompte(f"Site inconnu : {site}")
    libelle = (libelle or "").strip()
    if not libelle:
        raise ErreurCompte("Donne un nom à ce compte (par exemple « Dariya »).")
    base = f"{site}-{fichiers.slug(libelle, 'compte')}"
    profil, rang = base, 1
    while db.q1("SELECT id FROM comptes_ia WHERE profil=?", (profil,)):
        rang += 1
        profil = f"{base}-{rang}"
    rangee = db.q1(
        "SELECT COALESCE(MAX(ordre), -1) + 1 AS suivant FROM comptes_ia WHERE site=?",
        (site,),
    )
    compte_id = db.exec1(
        "INSERT INTO comptes_ia (site, libelle, email, profil, ordre, actif, proprietaire)"
        " VALUES (?,?,?,?,?,1,?)",
        (site, libelle, (email or "").strip(), profil, rangee["suivant"],
         (proprietaire or "").strip() or None),
    )
    config.chemin_profil(profil).mkdir(parents=True, exist_ok=True)
    db.journaliser(
        f"Compte {SITES[site]['nom']} « {libelle} » ajouté (profil {profil}). "
        "Connecte-toi dedans avant de l'utiliser."
    )
    return compte_id


def verifier_adresse_chat(adresse: str) -> str:
    """Une adresse de conversation utilisable, ou une erreur dite clairement.

    Deux refus, pour deux dégâts différents. Ce qui n'est pas une adresse web
    ferait ouvrir n'importe quoi au moment d'envoyer un prompt. Et une adresse
    web qui ne nomme aucune conversation — « https://grok.com/ » — ouvrirait un
    chat neuf à chaque scène tout en se faisant passer pour ton chat dédié :
    les règles y manqueraient, et chaque vidéo sortirait d'une conversation
    sans mémoire de la série.
    """
    from urllib.parse import urlparse

    adresse = (adresse or "").strip()
    if not adresse:
        return ""
    decoupe = urlparse(adresse)
    if decoupe.scheme not in ("http", "https") or not decoupe.netloc:
        raise ErreurCompte(
            "L'adresse du chat dédié doit être une adresse web complète, copiée "
            f"depuis la barre du navigateur (reçu : « {adresse[:60]} »)."
        )
    if not adresses.est_une_conversation(adresse):
        raise ErreurCompte(
            "Cette adresse n'ouvre pas une conversation précise : ouvre la "
            "conversation elle-même et recopie l'adresse de la barre du "
            "navigateur (elle contient « /c/… » ou « ?conversation=… »). "
            f"Reçu : « {adresse[:80]} »."
        )
    return adresse


def modifier(compte_id: int, libelle: str | None = None, email: str | None = None,
             actif: bool | None = None, chat_url: str | None = None) -> None:
    c = compte(compte_id)
    champs, valeurs = [], []
    if libelle is not None and libelle.strip():
        champs.append("libelle=?"); valeurs.append(libelle.strip())
    if email is not None:
        champs.append("email=?"); valeurs.append(email.strip())
    if actif is not None:
        champs.append("actif=?"); valeurs.append(1 if actif else 0)
    if chat_url is not None:
        champs.append("chat_url=?"); valeurs.append(verifier_adresse_chat(chat_url))
    if not champs:
        return
    valeurs.append(compte_id)
    db.exec1(f"UPDATE comptes_ia SET {', '.join(champs)} WHERE id=?", valeurs)
    db.journaliser(f"Compte « {c['libelle']} » modifié.")


def supprimer(compte_id: int, effacer_le_profil: bool = False) -> None:
    c = compte(compte_id)
    restants = db.q1(
        "SELECT COUNT(*) n FROM comptes_ia WHERE site=?", (c["site"],)
    )["n"]
    if restants <= 1:
        raise ErreurCompte(
            f"C'est le dernier compte {SITES[c['site']]['nom']} : ajoute-en un autre "
            "avant de supprimer celui-ci."
        )
    if effacer_le_profil and c["profil"] != config.PROFIL_PRINCIPAL:
        shutil.rmtree(config.chemin_profil(c["profil"]), ignore_errors=True)
    db.exec1("DELETE FROM comptes_ia WHERE id=?", (compte_id,))
    db.journaliser(
        f"Compte « {c['libelle']} » supprimé"
        + (" avec son profil." if effacer_le_profil else " (profil conservé).")
    )


def deplacer(compte_id: int, sens: str) -> None:
    """Change la priorité : le premier compte disponible est celui qui travaille."""
    c = compte(compte_id)
    freres = [x for x in liste(c["site"])]
    index = next(i for i, x in enumerate(freres) if x["id"] == compte_id)
    cible = index - 1 if sens == "monter" else index + 1
    if cible < 0 or cible >= len(freres):
        return
    autre = freres[cible]
    db.exec1("UPDATE comptes_ia SET ordre=? WHERE id=?", (cible, compte_id))
    db.exec1("UPDATE comptes_ia SET ordre=? WHERE id=?", (index, autre["id"]))
    db.journaliser(f"Ordre des comptes {SITES[c['site']]['nom']} modifié.")


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

def _meme_proprietaire(c: dict, proprietaire: str | None) -> bool:
    return (c.get("proprietaire") or "") == (proprietaire or "")


def proprietaires() -> list[str]:
    """Les autres personnes dont j'utilise les comptes, hors de ma rotation."""
    return [l["p"] for l in db.q(
        "SELECT DISTINCT proprietaire AS p FROM comptes_ia"
        " WHERE COALESCE(proprietaire, '') != '' ORDER BY p"
    )]


def ordre_utilisation(site: str, proprietaire: str | None = None) -> list[dict]:
    """Comptes disponibles, dans l'ordre où la production va les utiliser.

    Par défaut on dépense d'abord le quota qui se recharge le plus tôt : il est le
    plus périssable. Un compte sans horaire connu passe en dernier, et l'ordre de
    la liste sert à départager.
    """
    disponibles = [
        c for c in liste(site)
        if c["actif"] and c["quota_dispo"] and _meme_proprietaire(c, proprietaire)
    ]
    if reglages.lire("strategie_rotation") != "recharge_proche":
        return disponibles

    lointain = datetime.now() + timedelta(days=3650)

    def cle(c: dict):
        jour, moment = c.get("reset_jour"), _heure(c.get("reset_heure"))
        if jour is None or moment is None:
            return (lointain, c["ordre"], c["id"])
        return (
            prochaine_occurrence(datetime.now(), int(jour), moment),
            c["ordre"],
            c["id"],
        )

    return sorted(disponibles, key=cle)


def compte_actif(site: str, proprietaire: str | None = None) -> dict | None:
    """Compte que la production doit utiliser maintenant.

    Sans `proprietaire` : ma rotation. Avec : uniquement les comptes de cette
    personne. Les deux ne se mélangent jamais, dans un sens comme dans l'autre.
    """
    disponibles = ordre_utilisation(site, proprietaire)
    return disponibles[0] if disponibles else None


def etat_rotation(site: str, proprietaire: str | None = None) -> dict:
    """Qui travaille sur ce site, et qui prendrait la suite.

    Sert au bouton « ce compte est a court » : tant qu'aucun compte de
    secours n'est disponible, on ne propose rien, pour ne pas laisser croire
    qu'il reste du quota quelque part.
    """
    actif = compte_actif(site, proprietaire)
    suivants = comptes_de_secours(site, actif["id"], proprietaire) if actif else []
    return {
        "site": site,
        "actif_id": actif["id"] if actif else None,
        "actif": actif["libelle"] if actif else "",
        "suivant": suivants[0]["libelle"] if suivants else "",
        "secours": len(suivants),
    }


def comptes_de_secours(site: str, sauf_id: int, proprietaire: str | None = None) -> list[dict]:
    return [c for c in ordre_utilisation(site, proprietaire) if c["id"] != sauf_id]


def marquer_quota_epuise(compte_id: int, detail: str = "") -> dict:
    c = compte(compte_id)
    delai = delai_annonce(detail)
    retour = (
        (datetime.now() + delai + MARGE_RETOUR).isoformat(timespec="seconds")
        if delai else None
    )
    db.exec1(
        "UPDATE comptes_ia SET quota_epuise_le=?, quota_retour_le=? WHERE id=?",
        (db.maintenant(), retour, compte_id),
    )
    apres = compte(compte_id)
    if retour:
        quand = (
            f"{SITES[c['site']]['nom']} annonce son retour "
            f"{quand_lisible(datetime.fromisoformat(retour))}"
        )
    elif apres["prochaine_recharge"]:
        quand = f"Recharge prévue {apres['prochaine_recharge']}"
    else:
        quand = (
            f"Il sera réessayé dans {reglages.lire_float('quota_reset_heures'):g} h"
        )
    db.journaliser(
        f"Quota atteint sur le compte {SITES[c['site']]['nom']} « {c['libelle']} »"
        + (f" : {detail}" if detail else "")
        + f". {quand}, ou dès que tu le réarmes.",
        niveau="alerte",
    )
    return apres


def rearmer(compte_id: int) -> dict:
    c = compte(compte_id)
    db.exec1(
        "UPDATE comptes_ia SET quota_epuise_le=NULL, quota_retour_le=NULL WHERE id=?",
        (compte_id,),
    )
    db.journaliser(f"Compte « {c['libelle']} » réarmé : il repasse dans la rotation.")
    return compte(compte_id)


def noter_utilisation(compte_id: int) -> None:
    db.exec1(
        "UPDATE comptes_ia SET derniere_utilisation=? WHERE id=?",
        (db.maintenant(), compte_id),
    )


# ---------------------------------------------------------------------------
# Connexion et vérification
# ---------------------------------------------------------------------------

def url_site(site: str) -> str:
    personnalisee = reglages.lire(f"url_{site}", "")
    if personnalisee:
        return personnalisee
    return selecteurs.charger().get(site, {}).get("url", "")


def _sonder(site: str, profil: str) -> dict:
    """Regarde la page sans rien générer, et dit honnêtement ce qu'elle sait.

    Attention : certains sites, Grok notamment, affichent la même page et les
    mêmes cookies qu'on soit connecté ou non. Dans ce cas on répond « incertain »
    au lieu d'inventer une certitude : c'est à toi de confirmer.
    """
    sel = selecteurs.charger().get(site, {})
    page = navigateur.session().page(site, url_site(site), profil)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(2500)

    adresse = (page.url or "").lower()
    if any(
        marqueur in adresse
        for marqueur in ("/sign-in", "/signin", "/login", "/auth/", "accounts.")
    ):
        return {
            "etat": "deconnecte",
            "detail": f"Le site a redirigé vers une page de connexion ({page.url}).",
            "url": page.url,
        }

    composeur = navigateur.present(page, sel.get("zone_saisie", []), timeout=6000)
    connexion = navigateur.present(page, sel.get("signes_connexion", []), timeout=2000)

    if connexion and not composeur:
        return {
            "etat": "deconnecte",
            "detail": "Le site affiche un écran de connexion et aucune zone de saisie.",
            "url": page.url,
        }
    if composeur and not connexion:
        return {
            "etat": "connecte",
            "detail": "Zone de saisie accessible, aucun appel à se connecter.",
            "url": page.url,
        }
    if composeur and connexion:
        return {
            "etat": "incertain",
            "detail": (
                "La page montre à la fois une zone de saisie et un lien de connexion : "
                "ce site se présente pareil connecté ou non. Regarde la fenêtre du "
                "navigateur et confirme toi-même."
            ),
            "url": page.url,
        }
    return {
        "etat": "incertain",
        "detail": (
            "Ni zone de saisie ni écran de connexion reconnus. Le site a peut-être "
            "changé : vérifie les sélecteurs, ou confirme toi-même."
        ),
        "url": page.url,
    }


def verifier(compte_id: int) -> dict:
    c = compte(compte_id)
    resultat = navigateur.session().executer(
        _sonder, c["site"], c["profil"], refuser_si_occupe=True
    )
    db.exec1(
        "UPDATE comptes_ia SET etat=?, etat_source='auto', verifie_le=? WHERE id=?",
        (resultat["etat"], db.maintenant(), compte_id),
    )
    db.journaliser(
        f"Compte {SITES[c['site']]['nom']} « {c['libelle']} » : "
        f"{LIBELLES.get(resultat['etat'], resultat['etat'])}."
    )
    return {**resultat, "libelle": LIBELLES.get(resultat["etat"], resultat["etat"]),
            "compte": compte(compte_id)}


def marquer(compte_id: int, etat: str) -> dict:
    """Confirmation manuelle : c'est toi qui tranches quand le site est ambigu."""
    if etat not in ("connecte", "deconnecte"):
        raise ErreurCompte("État attendu : connecte ou deconnecte.")
    c = compte(compte_id)
    db.exec1(
        "UPDATE comptes_ia SET etat=?, etat_source='manuel', verifie_le=? WHERE id=?",
        (etat, db.maintenant(), compte_id),
    )
    db.journaliser(
        f"Compte « {c['libelle']} » marqué « {LIBELLES[etat]} » manuellement."
    )
    return compte(compte_id)


def ouvrir_pour_connexion(compte_id: int) -> str:
    """Ouvre le site dans le profil de CE compte, en fenêtre visible, sans rien générer.

    Même si les générations tournent en arrière-plan, se connecter demande une
    vraie fenêtre : on ferme donc le profil s'il tournait sans affichage.
    """
    c = compte(compte_id)
    url = url_site(c["site"])
    navigateur.session().fermer(c["profil"])
    navigateur.session().ouvrir(affichage="fenetre", profil=c["profil"])

    def _aller():
        page = navigateur.session().page(c["site"], url, c["profil"])
        try:
            page.bring_to_front()
        except Exception:
            pass
        return page.url

    adresse = navigateur.session().executer(_aller, refuser_si_occupe=True)
    db.exec1(
        "UPDATE comptes_ia SET etat='', etat_source='', verifie_le=NULL WHERE id=?",
        (compte_id,),
    )
    db.journaliser(
        f"Page {SITES[c['site']]['nom']} ouverte pour le compte « {c['libelle']} » "
        f"(profil {c['profil']}) : {adresse}"
    )
    return adresse


def effacer_profil(compte_id: int) -> str:
    """Déconnecte ce compte : supprime son profil navigateur local."""
    c = compte(compte_id)
    if c["partage_avec"]:
        raise ErreurCompte(
            "Ce profil est partagé avec : "
            + ", ".join(c["partage_avec"])
            + ". Donne d'abord un profil séparé à ce compte, sinon tu déconnecterais "
            "aussi les autres."
        )
    navigateur.session().fermer(c["profil"])
    chemin = config.chemin_profil(c["profil"])
    shutil.rmtree(chemin, ignore_errors=True)
    chemin.mkdir(parents=True, exist_ok=True)
    db.exec1(
        "UPDATE comptes_ia SET etat='', etat_source='', verifie_le=NULL,"
        " quota_epuise_le=NULL WHERE id=?",
        (compte_id,),
    )
    db.journaliser(f"Profil du compte « {c['libelle']} » effacé : déconnecté.")
    return str(chemin)


def tableau() -> dict[str, list[dict]]:
    """Comptes groupés par site, pour l'affichage."""
    return {site: liste(site) for site in SITES}


def rotation_lisible(site: str) -> list[dict]:
    """L'ordre d'utilisation réel, avec la date de recharge de chacun."""
    sortie = []
    for c in ordre_utilisation(site):
        jour, moment = c.get("reset_jour"), _heure(c.get("reset_heure"))
        quand = ""
        if jour is not None and moment is not None:
            suivante = prochaine_occurrence(datetime.now(), int(jour), moment)
            restant = suivante - datetime.now()
            heures = int(restant.total_seconds() // 3600)
            quand = (
                f"{JOURS[suivante.weekday()]} {suivante.strftime('%d/%m à %H:%M')} "
                f"(dans {heures} h)"
            )
        sortie.append({"libelle": c["libelle"], "recharge": quand})
    return sortie
