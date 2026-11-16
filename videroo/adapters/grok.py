"""Automatisation de Grok (Imagine Vidéo) selon la methode de l'utilisateur.

Methode appliquee :
  1. un chat dédié ou le prompt de règles est installe UNE SEULE FOIS ;
  2. a chaque nouvelle serie : « Nouvelle serie "<nom>" : <prompt vidéo> »
     accompagne de l'image de départ de la scène ; pour la partie 2 et les
     suivantes, la serie existe deja : « "<nom>" - Épisode N : » a la place ;
  3. ensuite, serie + episode + scène sont rappeles a chaque message ;
  4. une seule génération par scène, jamais de clic sur Régénérer.
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import config, db, media, reglages, ressources
from ..adresses import _cle as _cle_adresse, est_une_conversation, meme_conversation
from . import base, navigateur, selecteurs


def annonce_de_partie(ctx: base.ContexteTache) -> str:
    """Le premier message d'une partie dans une conversation.

    La partie 1 cree la serie. Les suivantes la continuent : dire « Nouvelle
    serie » ferait repartir Grok sur Episode 1, a cote des videos deja faites.
    """
    serie = ctx.serie or "serie_sans_nom"
    if (ctx.episode or 1) > 1:
        return f'"{serie}" - Épisode {ctx.episode} :'
    return f'Nouvelle série "{serie}" :'


def _page_en_erreur(page) -> bool:
    """Vrai si Grok a remplacé la conversation par sa page « erreur, réessayez »."""
    for texte in ("n'a pas pu se charger", "Une erreur est survenue", "Something went wrong"):
        try:
            if page.get_by_text(texte, exact=False).first.is_visible(timeout=1500):
                return True
        except Exception:
            continue
    return False


def _cle_serie_du_chat(adresse: str) -> str:
    """Clé de réglage : quelle série ce chat Grok a reçue en dernier."""
    hote, chemin, parametres = _cle_adresse(adresse)
    return "grok_derniere_serie:" + hote + chemin + "?" + "&".join(f"{n}={v}" for n, v in parametres)


def message_scene(ctx: base.ContexteTache, premiere_de_serie: bool, reprise: bool = False) -> str:
    serie = ctx.serie or "serie_sans_nom"
    numero = ctx.extras.get("scene_numero")
    if premiere_de_serie:
        entete = annonce_de_partie(ctx)
    elif reprise:
        # Le même chat a servi à une autre série entre-temps : sans ce rappel,
        # Grok continuerait l'autre (mauvais dossier, mauvaise numérotation).
        entete = (
            f'Reprenons la série "{serie}" — Épisode {ctx.episode} : il me manque la '
            f"scène {numero}."
        )
    else:
        entete = f'Série "{serie}" — Épisode {ctx.episode} — scène {numero} :'
    rappel = (
        f"Durée : {ctx.duree} s. UNE SEULE génération (one-shot, voix + lèvres "
        f"ensemble, Choix A). Enregistre dans Video/{serie}/Episode {ctx.episode}/ "
        f"au prochain numéro videoN, sans écraser un plan précédent."
    )
    # Le 16 novembre 2026, la scène 1 du « Mariage forcé » est sortie en anglais :
    # le mode agent réécrit le prompt pour la génération et a traduit la
    # réplique. La langue des voix ne doit dépendre ni du pack ni de l'agent.
    rappel += (
        "\nLANGUE : les répliques entre « » sont dites EN FRANÇAIS, mot pour mot, "
        "sans aucune traduction ni reformulation — même si tu réécris le reste du "
        "prompt en anglais pour la génération. Voix françaises, aucun mot anglais "
        "prononcé."
    )
    return f"{entete}\n\n{ctx.prompt}\n\n{rappel}"


def regles_presentes(projet, profil: str) -> bool:
    """Les règles sont-elles vraiment dans le chat qu'on va ouvrir ?

    Trois conditions, et il les faut toutes : la case est cochée, on connaît
    l'adresse de la conversation (sans elle on atterrit sur un chat neuf), et
    cette conversation appartient au profil Chrome qu'on utilise maintenant
    (un chat d'un autre compte Grok n'existe pas dans cette session).
    """
    chat_connu = est_une_conversation(projet["grok_chat_url"] or "")
    profil_pose = projet["grok_profil_regles"] or ""
    return (
        bool(projet["grok_regles_installees"])
        and chat_connu
        and (not profil_pose or profil_pose == profil)
    )


def adresse_du_chat(page, secondes: float = 15) -> str:
    """L'adresse de la conversation, une fois que Grok l'a réellement posée.

    Juste après l'envoi, page.url vaut encore « grok.com/ » : la retenir telle
    quelle ferait croire à un chat dédié qui n'existe pas. Les règles seraient
    comptées comme installées dans une conversation impossible à retrouver, et
    toutes les scènes suivantes partiraient dans un chat sans règles. Mieux
    vaut ne rien retenir que retenir une adresse fausse.
    """
    fin = time.time() + secondes
    while time.time() < fin:
        adresse = page.url or ""
        if est_une_conversation(adresse):
            return adresse
        time.sleep(0.5)
    return ""


class AdaptateurGrok(base.Adaptateur):
    nom = "grok-auto"

    def generer(self, ctx: base.ContexteTache) -> base.ResultatGeneration:
        sel = selecteurs.charger()["grok"]
        # Le chat dédié du compte d'abord : chez toi, c'est la conversation du
        # projet « Video » de Grok, la seule qui sache animer une image.
        chat_dedie = (ctx.chat_compte or "").strip()
        url = chat_dedie or ctx.url_chat or reglages.lire("url_grok") or sel["url"]

        if not ctx.serie:
            return base.ResultatGeneration(
                base.ECHEC,
                "Aucun nom de serie Grok defini pour ce projet. Rien n'a été envoyé.",
                instruction="Renseigne « Série Grok » et « Épisode » dans l'onglet "
                "Générations du projet.",
            )

        try:
            page = navigateur.session().page("grok", url, ctx.profil)
        except Exception as exc:
            quoi, action = navigateur.raison_navigateur(exc)
            return base.ResultatGeneration(
                base.INTERVENTION,
                f"Navigateur dédié indisponible : {quoi}. Rien n'a été envoyé.",
                instruction=action,
            )

        # Grok répond parfois « Une erreur est survenue — Cette page n'a pas pu se
        # charger » sur la conversation pourtant valide (constaté le 9 novembre
        # 2026, conversation de 250+ vidéos). Page vide : ni pièce jointe ni
        # saisie. On recharge, sans rien envoyer, avant de conclure.
        for essai in range(3):
            if not _page_en_erreur(page):
                break
            if essai == 2:
                return base.ResultatGeneration(
                    base.ECHEC,
                    "Grok affiche « Une erreur est survenue » sur ton chat, même après "
                    "deux rechargements. Rien n'a été envoyé, aucun quota consommé.",
                    instruction="Ouvre ce chat dans ton navigateur pour vérifier qu'il "
                    "charge, puis clique « Réessayer ».",
                    url=page.url,
                )
            ctx.journaliser("Grok affiche sa page d'erreur : rechargement, rien n'a été envoyé.")
            page.reload(wait_until="domcontentloaded")
            time.sleep(8)

        if navigateur.present(page, sel.get("signes_connexion", []), timeout=2500):
            return base.ResultatGeneration(
                base.INTERVENTION,
                "Grok demande une connexion.",
                instruction="Connecte-toi a ton compte Grok dans le navigateur dédié, "
                "puis clique sur Reprendre.",
                url=url,
            )

        # Quota atteint ? On le voit AVANT d'envoyer : la bascule est alors gratuite.
        vu = navigateur.preuve(page, sel.get("signes_quota", []), timeout=2000)
        if vu:
            return base.ResultatGeneration(
                base.QUOTA,
                f"Grok signale une limite d'usage sur « {ctx.compte_libelle} » : "
                f"{vu}. Rien n'a été envoyé.",
                instruction="Si ce texte n'a rien à voir avec un quota, c'est une "
                "fausse alerte : réarme le compte dans Admin > Mes comptes et "
                "corrige « signes_quota » dans Admin > Sélecteurs.",
                url=page.url,
            )

        if chat_dedie and not meme_conversation(chat_dedie, page.url):
            return base.ResultatGeneration(
                base.INTERVENTION,
                f"Le chat dédié de « {ctx.compte_libelle} » ne s'est pas ouvert : "
                f"attendu {chat_dedie}, obtenu {page.url}. Rien n'a été envoyé.",
                instruction="Vérifie que ce compte est bien connecté et que l'adresse "
                "du chat lui appartient (Admin > Mes comptes). Envoyer le prompt "
                "ailleurs produirait une vidéo hors de ta série.",
                url=page.url,
            )

        projet = db.q1("SELECT * FROM projets WHERE id=?", (ctx.projet_id,))
        if projet is None:
            return base.ResultatGeneration(base.ECHEC, "Projet introuvable.")

        # Etape 1 : installer le prompt de regles une seule fois PAR CHAT. Un chat
        # appartient a un profil Chrome : change de compte Grok et le chat des
        # regles n'existe plus. Sans adresse de conversation retenue non plus, on
        # repart forcement d'un chat neuf. Dans ces deux cas les regles ne sont
        # pas la, quoi qu'en dise la case : on les reinstalle (un message texte,
        # aucune video, donc aucun quota video consomme).
        profil_des_regles = projet["grok_profil_regles"] or ""
        deja_la = bool(chat_dedie) or regles_presentes(projet, ctx.profil)
        if chat_dedie:
            ctx.journaliser(
                f"Chat dédié de « {ctx.compte_libelle} » : les règles y sont déjà, "
                "posées de ta main. Aucun prompt de règles renvoyé."
            )
        if not deja_la:
            if projet["grok_regles_installees"] and not est_une_conversation(
                projet["grok_chat_url"] or ""
            ):
                ctx.journaliser(
                    "Règles notées comme installées, mais aucune adresse de "
                    "conversation n'avait été retenue : on repart d'un chat neuf et "
                    "on les réinstalle (un message texte, aucune vidéo)."
                )
            elif projet["grok_regles_installees"]:
                ctx.journaliser(
                    f"Les règles sont dans le chat du profil « {profil_des_regles} », "
                    f"pas dans « {ctx.profil} » : réinstallation ici "
                    "(un message texte, aucune vidéo)."
                )
            texte = ressources.regles_grok()
            navigateur.fermer_surcouches(page)
            saisi, pourquoi = navigateur.saisir(page, sel.get("zone_saisie", []), texte)
            if not saisi:
                return base.ResultatGeneration(
                    base.INTERVENTION,
                    f"Règles non saisies dans Grok, rien n'a été envoyé : {pourquoi}.",
                    instruction="Colle toi-même le prompt de règles (onglet Générations > "
                    "« Copier le prompt de règles Grok ») dans un nouveau chat, puis coche "
                    "« Règles installees ».",
                    url=page.url,
                )
            parti, _ = navigateur.envoyer(
                page, sel.get("zone_saisie", []), sel.get("bouton_envoyer", [])
            )
            if not parti:
                return base.ResultatGeneration(
                    base.INTERVENTION,
                    "Les règles sont saisies dans Grok mais ne sont pas parties.",
                    instruction="Envoie le message toi-même, puis coche « Règles installées ».",
                    url=page.url,
                )
            adresse = adresse_du_chat(page)
            db.exec1(
                "UPDATE projets SET grok_regles_installees=1, grok_profil_regles=?,"
                " grok_serie_annoncee=0 WHERE id=?",
                (ctx.profil, ctx.projet_id),
            )
            if adresse:
                db.exec1(
                    "UPDATE projets SET grok_chat_url=? WHERE id=?",
                    (adresse, ctx.projet_id),
                )
                ctx.journaliser(
                    f"Prompt de règles installé dans le chat Grok {adresse} "
                    "(aucune vidéo générée)."
                )
            else:
                ctx.journaliser(
                    "Prompt de règles envoyé (aucune vidéo générée), mais Grok n'a pas "
                    "donné d'adresse de conversation : rien n'est retenu plutôt qu'une "
                    "adresse fausse. La prochaine scène réinstallera les règles."
                )
            time.sleep(12)

        # « Nouvelle série "<nom>" : » doit être dit dans CHAQUE conversation qui
        # ignore encore cette série : un chat ne sait rien de ce qui a été annoncé
        # dans un autre, et un compte de secours ouvre forcément un autre chat.
        premiere = not meme_conversation(projet["grok_serie_annoncee_url"] or "", page.url)
        if premiere:
            ctx.journaliser(
                "Cette conversation n'a pas encore reçu cette partie : le message "
                f"commencera par « {annonce_de_partie(ctx)} »."
            )
        self._attendre_que_grok_ait_fini(page, sel, ctx)
        videos_avant = self._videos_au_depart(page, sel, ctx)
        ctx.journaliser(
            f"Repère posé : {len(videos_avant)} vidéo(s) déjà dans cette "
            "conversation. Seule une vidéo apparue après l'envoi sera retenue."
        )

        # @image1 = image de depart, @image2... = planches des personnages,
        # dans l'ordre fourni par le runner, celui du prompt.
        a_joindre = [Path(c) for c in ctx.fichiers_joints]
        image = a_joindre[0] if a_joindre else None
        if image is None or not image.exists():
            return base.ResultatGeneration(
                base.ECHEC,
                "Image de départ absente : rien n'a été envoyé a Grok.",
                instruction="Génère ou importe l'image de la scène avant d'animer.",
            )
        absentes = [str(c) for c in a_joindre[1:] if not c.exists()]
        if absentes:
            return base.ResultatGeneration(
                base.ECHEC,
                "Planches de référence introuvables sur le disque : "
                + ", ".join(absentes)
                + ". Rien n'a été envoyé à Grok, aucun quota consommé.",
                instruction="Produis ou importe ces planches avant d'animer.",
            )
        # Coller les images (Ctrl+V), comme tu le fais : c'est ce qui passe chez
        # Grok. Le bouton « + » puis le champ caché ne servent qu'en secours.
        joint, detail = navigateur.coller_images(page, sel.get("zone_saisie", []), a_joindre)
        if not joint:
            ctx.journaliser(f"Collage impossible ({detail}) : essai par le bouton « + ».")
            joint, detail = navigateur.joindre_fichiers(
                page, sel.get("champ_fichier", []), a_joindre, sel.get("bouton_joindre", []),
                bouton_d_abord=True,
            )
        if not joint:
            return base.ResultatGeneration(
                base.ECHEC,
                "Impossible de joindre l'image de départ. Aucun prompt envoyé, aucun "
                f"quota consommé. Motif : {detail}",
                instruction="Répare le sélecteur « champ_fichier » dans Admin > "
                "Sélecteurs, ou passe en mode assisté.",
            )
        ctx.journaliser(
            f"{len(a_joindre)} fichier(s) joint(s) ({detail}), @image1 d'abord : "
            + ", ".join(c.name for c in a_joindre)
        )
        time.sleep(4)
        # Repères pour le moment de l'envoi : la conversation où l'on est, et les
        # miniatures déposées. Si l'une ou l'autre a bougé d'ici là, rien ne part.
        adresse_de_depart = page.url
        miniatures_jointes = navigateur.nombre_miniatures(page, sel.get("zone_saisie", []))

        # Régler la durée DANS l'interface, pas seulement dans le texte du prompt.
        duree_reglee, detail_duree = self._regler_duree(page, sel, ctx.duree)
        if duree_reglee:
            ctx.journaliser(f"Durée réglée sur {ctx.duree} s dans Grok ({detail_duree}).")
        else:
            note = (
                f"Le contrôle de durée de Grok est introuvable : {ctx.duree} s est "
                "demandé dans le texte du prompt, mais pas réglé dans l'interface. "
                "Vérifie la durée obtenue, ou répare « reglage_duree » / "
                "« options_duree » dans Admin > Sélecteurs."
            )
            if reglages.lire_bool("exiger_reglage_duree"):
                return base.ResultatGeneration(
                    base.ECHEC,
                    "Durée non réglable dans l'interface Grok : rien n'a été envoyé, "
                    "aucun quota consommé.",
                    instruction=note,
                    url=page.url,
                )
            ctx.journaliser(note)
        self._regler_format(page, sel)

        # Chercher la durée peut laisser un menu ouvert, et un menu ouvert coupe
        # les clics de toute la page : la zone de saisie reste visible mais
        # n'accepte plus rien. On referme avant d'écrire.
        referme = navigateur.fermer_surcouches(page)
        if referme:
            ctx.journaliser(
                f"{referme.capitalize()} restait ouvert après le réglage : refermé avant "
                "la saisie (Échap ne valide ni n'envoie rien)."
            )

        # Grok peut planter juste après le dépôt des fichiers (page « Une erreur est
        # survenue », constaté le 9 novembre 2026) : plus de zone de saisie. Rien
        # n'est encore parti : on recharge et on redépose, deux fois au plus.
        for essai in range(3):
            if not _page_en_erreur(page):
                break
            if essai == 2:
                return base.ResultatGeneration(
                    base.ECHEC,
                    f"Grok plante après le dépôt des {len(a_joindre)} fichier(s), même "
                    "après deux rechargements. Rien n'a été envoyé, aucun quota consommé.",
                    instruction="Ouvre le chat dans ton navigateur, vérifie qu'il charge, "
                    "puis clique « Réessayer » (ou passe cette scène en mode assisté).",
                    url=page.url,
                )
            ctx.journaliser(
                "Grok a affiché sa page d'erreur après le dépôt des fichiers : "
                "rechargement et nouveau dépôt, rien n'a été envoyé."
            )
            page.reload(wait_until="domcontentloaded")
            time.sleep(8)
            if _page_en_erreur(page):
                continue
            joint, detail = navigateur.joindre_fichiers(
                page, sel.get("champ_fichier", []), a_joindre, sel.get("bouton_joindre", []),
            bouton_d_abord=True,
            )
            if not joint:
                return base.ResultatGeneration(
                    base.ECHEC,
                    "Après rechargement, impossible de rejoindre l'image de départ. "
                    f"Aucun prompt envoyé, aucun quota consommé. Motif : {detail}",
                    url=page.url,
                )
            time.sleep(6)

        cle_chat = _cle_serie_du_chat(page.url)
        serie_ici = f"{ctx.serie}|{ctx.episode}"
        reprise = not premiere and (db.reglage(cle_chat) or "") != serie_ici
        if reprise:
            ctx.journaliser(
                "Ce chat Grok a servi à une autre série depuis : le message commencera "
                f"par « Reprenons la série \"{ctx.serie}\" … il me manque la scène "
                f"{ctx.extras.get('scene_numero')} »."
            )
        texte = message_scene(ctx, premiere, reprise)
        ailleurs = self._chat_change(page, adresse_de_depart)
        if ailleurs:
            return ailleurs
        saisi, pourquoi = navigateur.saisir(page, sel.get("zone_saisie", []), texte)
        if not saisi:
            return base.ResultatGeneration(
                base.ECHEC,
                f"Prompt vidéo non saisi dans Grok, rien n'a été envoyé et aucun "
                f"quota consommé : {pourquoi}.",
                instruction="Si la zone est bien là mais recouverte, ferme ce qui est "
                "ouvert dans la page puis clique « Réessayer ». Si elle est vraiment "
                "absente, répare « zone_saisie » dans Admin > Sélecteurs.",
                url=page.url,
            )
        ailleurs = self._chat_change(page, adresse_de_depart)
        if ailleurs:
            return ailleurs
        if miniatures_jointes > 0 and (
            navigateur.nombre_miniatures(page, sel.get("zone_saisie", [])) < miniatures_jointes
        ):
            return base.ResultatGeneration(
                base.ECHEC,
                "Les images jointes ont disparu de la zone de saisie avant l'envoi : "
                "rien n'a été envoyé à Grok, aucun quota consommé.",
                instruction="Clique « Réessayer » : l'application rouvrira le chat et "
                "redéposera l'image de départ et les planches.",
                url=page.url,
            )
        envoye, comment = navigateur.envoyer(
            page, sel.get("zone_saisie", []), sel.get("bouton_envoyer", [])
        )
        if not envoye:
            return base.ResultatGeneration(
                base.ECHEC,
                "Le prompt est saisi dans Grok mais il n'est pas parti : la zone de "
                "saisie ne s'est pas vidée. Rien n'a été consommé.",
                instruction="La page est ouverte avec le prompt prêt : envoie-le "
                "toi-même, puis clique « Importer le résultat ».",
                url=page.url,
            )
        # Point de non-retour : le quota video peut avoir ete depense.
        ctx.envoye = True
        db.definir_reglage(cle_chat, serie_ici)
        ctx.journaliser(f"Prompt vidéo envoyé à Grok ({comment}).")
        if premiere:
            db.exec1(
                "UPDATE projets SET grok_serie_annoncee=1, grok_serie_annoncee_url=?"
                " WHERE id=?",
                (page.url, ctx.projet_id),
            )
            # Un chat dédié appartient au compte, pas au projet : l'écrire ici
            # ferait suivre ce chat à une autre histoire.
            if not chat_dedie:
                adresse = adresse_du_chat(page)
                if adresse:
                    db.exec1(
                        "UPDATE projets SET grok_chat_url=? WHERE id=?",
                        (adresse, ctx.projet_id),
                    )
        ctx.journaliser(
            f"Prompt vidéo envoyé a Grok ({annonce_de_partie(ctx) if premiere else 'série en cours'}). "
            "UNE SEULE génération demandée, aucun clic sur Régénérer."
        )

        # Une vidéo qui démarre sur l'image d'une AUTRE scène n'est pas la nôtre :
        # le 12 novembre 2026, une seconde prise de la scène 5 arrivée en retard
        # a été prise pour la scène 6, et tout le projet 38 s'est décalé d'une
        # scène. Elle est mise de côté et l'attente continue, dans le même délai.
        ecartees: set[str] = set()
        fin_attente = time.time() + reglages.lire_int("delai_video")
        while True:
            attente = self._attendre_video(
                page, sel, videos_avant | ecartees,
                max(0, int(fin_attente - time.time())), ctx,
                marqueur=texte.split("\n", 1)[0],
            )
            if attente != "video":
                break
            nombre = len(self._sources_videos(page, sel) - videos_avant - ecartees)
            # La vidéo à garder est celle que l'agent met dans SA RÉPONSE à ce
            # message — pas une prise brute apparue ailleurs dans la page.
            reponse = self._attendre_video_de_la_reponse(
                page, texte.split("\n", 1)[0], videos_avant | ecartees, fin_attente, ctx
            )
            resultat = self._telecharger(
                page, sel, ctx, videos_avant | ecartees, ecartees, reponse=reponse
            )
            if resultat is not None:
                break
        if attente != "video" and ctx.extras.get("doute"):
            chemin, autre = ctx.extras["doute"]
            return base.ResultatGeneration(
                base.A_VERIFIER,
                f"La seule vidéo apparue démarre sur l'image de la scène {autre}, pas "
                "sur celle de cette scène : c'est sans doute une prise de la scène "
                f"{autre} arrivée en retard. Elle est gardée à part ({Path(chemin).name}) "
                "et n'a pas été rattachée. Rien n'a été relancé.",
                instruction="Regarde la conversation Grok. Si la vidéo de cette scène "
                "y est, clique « Récupérer le résultat » : l'application choisira celle "
                "qui démarre sur la bonne image.",
                url=page.url,
            )
        if attente == "quota":
            # Le prompt est deja parti : on ne sait pas si un credit a ete pris.
            # On met donc le compte de cote (la suite partira toute seule sur le
            # compte suivant) mais on ne relance RIEN sur cet element.
            return base.ResultatGeneration(
                base.A_VERIFIER,
                "Grok annonce la fin de ton quota sur ce compte, après l'envoi. "
                "Aucune vidéo n'est apparue et l'application ne relance RIEN ici.",
                instruction="Le compte est mis de côté : la prochaine génération "
                "partira sur le compte suivant. Si la vidéo finit par sortir, "
                "clique « Récupérer le résultat ».",
                url=page.url,
                compte_a_court=True,
            )
        if attente != "video":
            return base.ResultatGeneration(
                base.A_VERIFIER,
                f"Aucune vidéo détectée après {reglages.lire_int("delai_video")} s. "
                "L'application ne relance RIEN.",
                instruction="Regarde la page ouverte. Si la vidéo est prête, télécharge-la "
                "et clique « Importer le résultat ».",
                url=page.url,
            )

        if resultat.etat == base.TERMINE and nombre > 1:
            resultat.message += (
                f" Grok a produit {nombre} prises pour cette scène : celle qui démarre le "
                "plus près de l'image de départ a été enregistrée. Si ce n'est pas la "
                "bonne, utilise « Importer le résultat » pour choisir l'autre — aucune "
                "nouvelle génération ne sera lancée."
            )
        return resultat

    ATTENTE_FIN_DE_TRAVAIL = 360

    def _attendre_que_grok_ait_fini(self, page, sel, ctx) -> None:
        """Attend que Grok ne travaille plus avant de relever les vidéos présentes.

        Grok sort parfois DEUX prises pour un prompt, la seconde une minute après
        la première. Si la scène suivante relève son repère avant, cette prise en
        retard passe pour sa vidéo : le 12 novembre 2026, tout le projet 38
        s'est décalé d'une scène. Attendre ne renvoie rien et ne coûte rien.
        """
        signes = sel.get("signes_travail", [])
        if not signes or not navigateur.present(page, signes, timeout=1500):
            return
        ctx.journaliser(
            "Grok travaille encore sur la demande précédente (une seconde prise peut "
            "arriver) : attente avant de préparer cette scène, rien n'est envoyé."
        )
        fin = time.time() + self.ATTENTE_FIN_DE_TRAVAIL
        while time.time() < fin:
            time.sleep(5)
            if not navigateur.present(page, signes, timeout=800):
                time.sleep(5)  # laisser la dernière prise s'afficher
                return
        ctx.journaliser(
            f"Grok travaille toujours au bout de {self.ATTENTE_FIN_DE_TRAVAIL // 60} min : "
            "on continue ; le contrôle par l'image de départ écartera une prise "
            "d'une autre scène."
        )

    @staticmethod
    def _chat_change(page, adresse_de_depart: str) -> base.ResultatGeneration | None:
        """ÉCHEC si la page n'est plus la conversation où les images ont été déposées."""
        if meme_conversation(adresse_de_depart, page.url):
            return None
        return base.ResultatGeneration(
            base.ECHEC,
            f"Le chat Grok a changé pendant la préparation (de {adresse_de_depart} "
            f"à {page.url}) : rien n'a été envoyé, aucun quota consommé.",
            instruction="Clique « Réessayer » : l'application rouvrira le bon chat. "
            "Si ça se répète, signale-le : un clic de réglage a dû ouvrir une autre "
            "discussion.",
            url=page.url,
        )

    def _regler_duree(self, page, sel, duree: int | None) -> tuple[bool, str]:
        """Règle la durée dans l'interface Grok. Ne lance aucune génération."""
        if not duree:
            return False, ""

        def essayer_menu() -> str:
            for s in sel.get("reglage_duree", []):
                try:
                    element = page.locator(s).first
                    element.wait_for(state="visible", timeout=2500)
                except Exception:
                    continue
                for etiquette in (f"{duree}s", f"{duree} s", f"{duree} secondes"):
                    try:
                        element.select_option(label=etiquette, timeout=4000)
                        return f"menu « {etiquette} »"
                    except Exception:
                        pass
                try:
                    element.select_option(value=str(duree), timeout=4000)
                    return f"menu valeur {duree}"
                except Exception:
                    continue
            return ""

        def essayer_boutons() -> str:
            for modele in sel.get("options_duree", []):
                cible = modele.replace("{duree}", str(duree))
                element = navigateur.petit_bouton(page, [cible], timeout=2000)
                if element is None:
                    continue
                try:
                    element.click(timeout=4000)
                    return f"bouton « {cible} »"
                except Exception:
                    continue
            return ""

        detail = essayer_menu() or essayer_boutons()
        if detail:
            return True, detail

        # La commande est peut-être derrière un panneau de réglages.
        for s in sel.get("ouvrir_reglages", []):
            try:
                panneau = page.locator(s).first
                panneau.wait_for(state="visible", timeout=1500)
                panneau.click()
                time.sleep(1.5)
            except Exception:
                continue
            detail = essayer_menu() or essayer_boutons()
            if detail:
                navigateur.fermer_surcouches(page)
                return True, f"{detail} (panneau de réglages)"
        navigateur.fermer_surcouches(page)
        return False, ""

    def _regler_format(self, page, sel) -> bool:
        """Choisit le format vertical 9:16 s'il est proposé."""
        for s in sel.get("reglage_format", []):
            try:
                if s.lstrip().startswith("select"):
                    element = page.locator(s).first
                    element.wait_for(state="visible", timeout=1500)
                    element.select_option(label="9:16", timeout=4000)
                    return True
                # Un bouton : seulement un vrai petit bouton « 9:16 », jamais un
                # titre de discussion de la barre latérale qui contiendrait 9:16.
                element = navigateur.petit_bouton(page, [s], timeout=1500)
                if element is None:
                    continue
                element.click(timeout=4000)
                return True
            except Exception:
                continue
        return False

    @staticmethod
    def _sources_videos(page, sel) -> set[str]:
        trouvees: set[str] = set()
        for s in sel.get("videos_reponse", []):
            try:
                for element in page.locator(s).all():
                    src = element.get_attribute("src") or element.get_attribute("href")
                    if src:
                        trouvees.add(src)
            except Exception:
                continue
        return trouvees

    def recuperer(self, ctx: base.ContexteTache) -> base.ResultatGeneration:
        """Récupère la dernière vidéo de la conversation, sans rien envoyer.

        Même raison que côté ChatGPT : quand la récolte rate alors que Grok a
        bien produit le plan, relancer la génération le ferait payer deux fois.
        On ouvre la conversation, on lit, on télécharge. Aucune saisie, aucun
        clic sur Régénérer.
        """
        sel = selecteurs.charger()["grok"]
        chat = (ctx.chat_compte or "").strip() or ctx.url_chat
        if not chat:
            return base.ResultatGeneration(
                base.INTERVENTION,
                "Aucune conversation Grok connue pour ce compte : impossible de savoir "
                "où regarder.",
                instruction="Colle l'adresse du chat dédié dans Admin > Mes comptes.",
            )
        try:
            page = navigateur.session().page("grok", chat, ctx.profil)
        except Exception as exc:
            quoi, action = navigateur.raison_navigateur(exc)
            return base.ResultatGeneration(
                base.INTERVENTION,
                f"Navigateur dédié indisponible : {quoi}. Rien n'a été envoyé.",
                instruction=action,
            )
        if not meme_conversation(chat, page.url):
            return base.ResultatGeneration(
                base.INTERVENTION,
                f"La conversation attendue ne s'est pas ouverte : attendu {chat}, "
                f"obtenu {page.url}. Rien n'a été lu.",
                instruction="Vérifie que ce compte est bien connecté dans le "
                "navigateur dédié.",
                url=page.url,
            )

        self._videos_au_depart(page, sel, ctx, attente_max=20)
        ordonnees = self._videos_ordonnees(page, sel)
        directes = [a for a in ordonnees if not a.startswith(("blob:", "data:"))]
        if not directes:
            return base.ResultatGeneration(
                base.A_VERIFIER,
                "Aucune vidéo téléchargeable trouvée dans cette conversation. Rien "
                "n'a été envoyé, aucun quota consommé.",
                instruction="Télécharge le plan depuis la page, puis utilise "
                "« Importer le résultat ».",
                url=page.url,
            )
        rang = max(1, int(ctx.extras.get("rang", 1)))
        if rang > len(directes):
            return base.ResultatGeneration(
                base.A_VERIFIER,
                f"Cette conversation ne contient que {len(directes)} vidéo(s) "
                f"téléchargeable(s) : impossible de remonter à la {rang}ᵉ.",
                url=page.url,
            )
        if rang == 1:
            # D'abord la vidéo que l'agent a mise dans sa réponse au message de
            # cette scène : c'est celle que tu vois dans le chat.
            numero = ctx.extras.get("scene_numero")
            for marqueur in (
                f"Épisode {ctx.episode} — scène {numero} :",
                f"il me manque la scène {numero}.",
                annonce_de_partie(ctx) if numero == 1 else "",
            ):
                reponse = self._videos_de_la_reponse(page, marqueur) if marqueur else None
                if reponse:
                    directes = [a for a in reponse if not a.startswith(("blob:", "data:"))] or directes
                    break
        choisie = self._choisir_par_image(page, ctx, directes) if rang == 1 else None
        if choisie is not None:
            return choisie
        adresse = directes[-rang]
        try:
            reponse = page.request.get(adresse)
            if not reponse.ok:
                raise RuntimeError(f"réponse {reponse.status}")
            ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
            ctx.chemin_sortie.write_bytes(reponse.body())
        except Exception as exc:
            return base.ResultatGeneration(
                base.A_VERIFIER,
                f"Dernière vidéo repérée mais téléchargement impossible ({exc}).",
                instruction="Télécharge-la manuellement, puis clique « Importer le "
                "résultat ».",
                url=page.url,
            )
        taille = ctx.chemin_sortie.stat().st_size
        if taille == 0:
            ctx.chemin_sortie.unlink(missing_ok=True)
            return base.ResultatGeneration(
                base.A_VERIFIER,
                "Le fichier récupéré était vide : rien n'a été enregistré.",
                url=page.url,
            )
        return base.ResultatGeneration(
            base.TERMINE,
            f"Vidéo récupérée sans aucune génération ({taille // (1024 * 1024)} Mo) : la "
            + ("dernière" if rang == 1 else f"{rang}ᵉ en partant de la fin")
            + f" des {len(directes)} vidéos téléchargeables de la conversation. "
            "Regarde-la : rien ne garantit que ce soit celle de cette scène.",
            chemin=str(ctx.chemin_sortie),
            url=page.url,
        )

    #: Combien de vidéos récentes on examine pour trouver celle de la scène.
    CANDIDATES_RECUPERATION = 8

    def _choisir_par_image(self, page, ctx, directes: list[str]):
        """La vidéo récente qui démarre le plus près de l'image de cette scène.

        « La dernière vidéo du chat » n'est pas forcément la nôtre : une prise
        en retard ou la scène suivante peuvent être passées devant. On lit les
        plus récentes (lire ne renvoie rien à Grok), on écarte celles qui
        démarrent sur l'image d'une autre scène, et on garde la plus proche de
        notre image de départ. Rend None si on ne peut pas trancher.
        """
        if not ctx.fichiers_joints or not Path(ctx.fichiers_joints[0]).exists():
            return None
        image = ctx.fichiers_joints[0]
        dossier = ctx.chemin_sortie.parent
        dossier.mkdir(parents=True, exist_ok=True)
        meilleure: tuple[float, bytes, int] | None = None
        candidates = directes[-self.CANDIDATES_RECUPERATION:][::-1]
        essai = dossier / f".{ctx.chemin_sortie.stem}_essai{ctx.chemin_sortie.suffix}"
        try:
            for rang, adresse in enumerate(candidates, start=1):
                try:
                    reponse = page.request.get(adresse)
                    if not reponse.ok:
                        continue
                    octets = reponse.body()
                except Exception:
                    continue
                essai.write_bytes(octets)
                if self._autre_scene(ctx, essai) is not None:
                    continue
                ecart = media.ecart_debut(essai, image)
                if ecart is not None and (meilleure is None or ecart < meilleure[0]):
                    meilleure = (ecart, octets, rang)
        finally:
            essai.unlink(missing_ok=True)
        if meilleure is None:
            return None
        ecart, octets, rang = meilleure
        ctx.chemin_sortie.write_bytes(octets)
        return base.ResultatGeneration(
            base.TERMINE,
            f"Vidéo récupérée sans aucune génération : parmi les {len(candidates)} "
            "dernières vidéos de la conversation, c'est celle qui démarre sur l'image "
            f"de cette scène ({'la dernière' if rang == 1 else f'{rang}ᵉ en partant de la fin'}, "
            f"écart {ecart:.0f}). Regarde-la avant de valider.",
            chemin=str(ctx.chemin_sortie),
            url=page.url,
        )

    @staticmethod
    def _videos_ordonnees(page, sel) -> list[str]:
        """Les adresses des vidéos DANS L'ORDRE de la page.

        « La dernière » n'a de sens que dans un ordre : un ensemble n'en a pas.
        """
        vues: list[str] = []
        for s in sel.get("videos_reponse", []):
            try:
                for element in page.locator(s).all():
                    src = element.get_attribute("src") or element.get_attribute("href")
                    if src and src not in vues:
                        vues.append(src)
            except Exception:
                continue
        return vues

    def _videos_au_depart(self, page, sel, ctx, attente_max: float = 30) -> set[str]:
        """Les vidéos déjà là, une fois la conversation vraiment chargée.

        Tes conversations du projet « Video » en contenaient 226 et 177 le
        7 novembre 2026, et elles n'arrivent pas toutes dans la même seconde.
        Poser le repère trop tôt ferait passer pour neuve une vidéo ancienne
        arrivée juste après : l'attente s'arrêterait aussitôt et un plan d'une
        autre scène serait enregistré comme réussi. Un faux succès est le pire
        des résultats, parce qu'il ne se signale pas. On attend donc que le
        compte cesse de bouger avant de fixer le repère.
        """
        vues = self._sources_videos(page, sel)
        fin = time.time() + attente_max
        stables = 0
        while time.time() < fin and stables < 3:
            time.sleep(1.5)
            maintenant = self._sources_videos(page, sel)
            stables = stables + 1 if maintenant == vues else 0
            vues = maintenant
        if stables < 3:
            ctx.journaliser(
                f"La conversation chargeait encore au bout de {attente_max:.0f} s "
                f"({len(vues)} vidéos vues) : le repère est posé maintenant. Une vidéo "
                "ancienne arrivée après pourrait être prise pour la nouvelle — regarde "
                "le plan rendu avant de le valider."
            )
        return vues

    def _attendre_video(
        self, page, sel, avant: set[str], delai: int, ctx=None, marqueur: str = ""
    ) -> str:
        """« video », « quota » ou « rien » au bout du delai.

        Grok annonce souvent la fin de son quota APRES l'envoi, dans sa
        reponse. Sans ce controle on attendait quinze minutes dans le vide,
        puis on repartait sur le meme compte a court : le compte suivant
        n'etait jamais utilise.
        """
        # Regarder souvent ne coûte rien et fait gagner jusqu'à cinq secondes par
        # scène : c'est l'attente de Grok qui domine, pas notre boucle.
        fin = time.time() + delai
        prochain_controle = time.time() + 20
        # Le 7 novembre 2026, la video de la scene 10 a ete « trouvee » sept
        # secondes apres l'envoi : c'etait celle de la scene 9, que la page
        # venait d'afficher en descendant dans une conversation de 184 videos.
        # Grok ne produit rien en sept secondes. Une source qui apparait avant
        # ce plancher rejoint donc le repere, et on continue d'attendre.
        plancher = time.time() + config.DELAI_MINIMUM_VIDEO
        # Le relevé d'AVANT l'envoi : les vidéos écartées comme « trop tôt » ne
        # doivent pas masquer la nôtre si elle passe ensuite dans la réponse.
        avant_envoi = {a.split("?")[0] for a in avant}
        while time.time() < fin:
            nouvelles = self._sources_videos(page, sel) - avant
            if nouvelles and time.time() < plancher and marqueur:
                # Le 16 novembre 2026, Grok a rendu une vidéo de 6 s en 25 s : le
                # plancher l'a prise pour une ancienne vidéo et l'attente a couru
                # jusqu'au bout. Une vidéo placée dans la RÉPONSE à notre message
                # est forcément neuve, quelle que soit sa vitesse.
                dans_la_reponse = self._videos_de_la_reponse(page, marqueur) or []
                if any(a not in avant_envoi for a in dans_la_reponse):
                    time.sleep(3)
                    return "video"
            if nouvelles:
                if time.time() < plancher:
                    avant |= nouvelles
                    if ctx is not None:
                        ctx.journaliser(
                            f"{len(nouvelles)} vidéo(s) apparue(s) trop tôt pour "
                            "être la nôtre : la page a chargé d'anciennes vidéos "
                            "de la conversation. Repère élargi, attente "
                            "poursuivie, rien n'a été relancé."
                        )
                else:
                    time.sleep(3)  # laisser la lecture se stabiliser
                    return "video"
            if time.time() >= prochain_controle:
                prochain_controle = time.time() + 20
                # La vidéo prime : un signe de quota n'est retenu que si rien
                # n'est apparu, sinon on jetterait un résultat déjà payé.
                if navigateur.preuve(page, sel.get("signes_quota", []), timeout=800):
                    if not (self._sources_videos(page, sel) - avant):
                        return "quota"
            time.sleep(1.5)
        return "rien"

    def _prises_par_ressemblance(self, page, ctx, adresses: list[str]) -> list[str]:
        """Les prises, la plus proche de l'image de départ de la scène d'abord.

        Grok sort parfois deux prises ; « la première » dans l'ordre des adresses
        ne veut rien dire. Lire une prise ne coûte rien.
        """
        image = Path(ctx.fichiers_joints[0])
        if not image.exists():
            return adresses
        essai = ctx.chemin_sortie.with_name(f".{ctx.chemin_sortie.stem}_prise{ctx.chemin_sortie.suffix}")
        notes: list[tuple[float, int, str]] = []
        try:
            for rang, src in enumerate(adresses):
                ecart = None
                try:
                    reponse = page.request.get(src)
                    if reponse.ok:
                        essai.write_bytes(reponse.body())
                        ecart = media.ecart_debut(essai, image)
                except Exception:
                    ecart = None
                notes.append((ecart if ecart is not None else float("inf"), rang, src))
        finally:
            essai.unlink(missing_ok=True)
        notes.sort()
        if notes and notes[0][0] != float("inf"):
            ctx.journaliser(
                f"Grok a produit {len(adresses)} prises : celle qui démarre le plus près "
                f"de l'image de la scène est retenue (écart {notes[0][0]:.0f})."
            )
        return [src for _, _, src in notes]

    def _autre_scene(self, ctx: base.ContexteTache, video: Path) -> tuple | None:
        autres = ctx.extras.get("images_scenes") or {}
        if not autres or not ctx.fichiers_joints:
            return None
        return media.scene_plus_proche(video, ctx.fichiers_joints[0], autres)

    def _mettre_de_cote_si_autre(self, ctx: base.ContexteTache, src: str) -> bool:
        """Si la vidéo enregistrée démarre sur une autre scène : à part, et True."""
        autre = self._autre_scene(ctx, ctx.chemin_sortie)
        if autre is None:
            return False
        numero, ecart_autre, ecart_notre = autre
        cote = ctx.chemin_sortie.with_name(
            f"{ctx.chemin_sortie.stem}_scene{numero}-probable_"
            f"{time.strftime('%Y%m%d-%H%M%S')}{ctx.chemin_sortie.suffix}"
        )
        ctx.chemin_sortie.replace(cote)
        ctx.extras["doute"] = (str(cote), numero)
        ctx.journaliser(
            f"Vidéo écartée : elle démarre sur l'image de la scène {numero} (écart "
            f"{ecart_autre:.0f} contre {ecart_notre:.0f} avec la nôtre). C'est sans "
            f"doute une prise de la scène {numero} arrivée en retard. Gardée à part "
            f"sous {cote.name} ; attente de la bonne vidéo, rien n'a été relancé."
        )
        return True

    _JS_VIDEOS_DE_LA_REPONSE = """(marqueur) => {
        const sans = s => (s || '').split('?')[0];
        const feuilles = [...document.querySelectorAll('body *')].filter(
            e => e.children.length === 0 && (e.textContent || '').includes(marqueur));
        if (!feuilles.length) return null;
        const message = feuilles[feuilles.length - 1];
        const largeurMax = window.innerWidth * 0.6;
        let bloc = message.parentElement;
        while (bloc && bloc !== document.body) {
            // On reste dans le fil de discussion : un bloc plus large que 60 %
            // de la fenêtre déborde sur la galerie et les panneaux voisins.
            if (bloc.getBoundingClientRect().width > largeurMax) return [];
            const suivantes = [...bloc.querySelectorAll('video')].filter(
                v => message.compareDocumentPosition(v) & Node.DOCUMENT_POSITION_FOLLOWING);
            if (suivantes.length) {
                return [...new Set(suivantes.map(v => sans(v.currentSrc || v.src)).filter(Boolean))];
            }
            bloc = bloc.parentElement;
        }
        return [];
    }"""

    def _videos_de_la_reponse(self, page, marqueur: str) -> list[str] | None:
        """Adresses des vidéos placées APRÈS notre message dans le fil (None = message introuvable).

        Le 16 novembre 2026, Grok Imagine a sorti plusieurs prises : l'agent a
        mis la bonne (en français) dans sa réponse, et Videroo a pris une prise
        brute apparue dans la galerie (en anglais). Seule la vidéo de la réponse
        à NOTRE message est la nôtre.
        """
        if not marqueur.strip():
            return None
        try:
            return page.evaluate(self._JS_VIDEOS_DE_LA_REPONSE, marqueur.strip())
        except Exception:
            return None

    def _attendre_video_de_la_reponse(
        self, page, marqueur: str, avant: set[str], fin: float, ctx
    ) -> list[str] | None:
        """La vidéo de la réponse, dès qu'elle y est (None si le fil est illisible)."""
        connues = {a.split("?")[0] for a in avant}
        premier_passage = True
        while True:
            trouvees = self._videos_de_la_reponse(page, marqueur)
            if trouvees is None:
                if premier_passage:
                    ctx.journaliser(
                        "Message introuvable dans le fil Grok : la vidéo sera choisie "
                        "parmi les nouvelles, et contrôlée par son image de départ."
                    )
                return None
            neuves = [a for a in trouvees if a not in connues]
            if neuves:
                return neuves
            if time.time() >= fin:
                return []
            premier_passage = False
            time.sleep(3)

    def _telecharger(
        self, page, sel, ctx: base.ContexteTache, avant: set[str] | None = None,
        ecartees: set[str] | None = None, reponse: list[str] | None = None,
    ) -> base.ResultatGeneration | None:
        """Enregistre LA vidéo de cette scène, et pas une voisine.

        Ta conversation du projet « Video » garde les plans des scènes déjà
        faites : cinq vidéos y étaient déjà le 7 novembre 2026. « Prendre une
        vidéo de la page » y rendrait un ancien plan, enregistré sous le nom de
        la scène en cours et annoncé comme réussi — le pire des résultats, parce
        qu'il a l'air juste. On ne descend donc que ce qui est apparu APRÈS
        l'envoi, et son adresse directe passe en premier : elle désigne une
        vidéo précise, alors que le bouton de téléchargement n'est rattaché à
        aucune. Le bouton ne sert plus que pour les pages où la vidéo n'a pas
        d'adresse utilisable (« blob: »).
        """
        ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        apparues = self._sources_videos(page, sel) - (avant or set())
        adresses_directes = sorted(
            src for src in apparues if not src.startswith(("blob:", "data:"))
        )
        if reponse is not None:
            # Le fil est lisible : seule la vidéo de la réponse compte. Liste vide
            # = l'agent n'a encore rien mis dans sa réponse : on attend encore.
            adresses_directes = [
                a for a in reponse
                if not a.startswith(("blob:", "data:")) and a not in (ecartees or set())
            ]
            if not adresses_directes:
                return None

        if len(adresses_directes) > 1 and ctx.fichiers_joints:
            adresses_directes = self._prises_par_ressemblance(page, ctx, adresses_directes)
        for src in adresses_directes:
            try:
                reponse = page.request.get(src)
                if not reponse.ok:
                    continue
                ctx.chemin_sortie.write_bytes(reponse.body())
                if ctx.chemin_sortie.stat().st_size > 0:
                    if ecartees is not None and self._mettre_de_cote_si_autre(ctx, src):
                        ecartees.add(src)
                        continue
                    return base.ResultatGeneration(
                        base.TERMINE,
                        "Vidéo téléchargée depuis son adresse directe.",
                        chemin=str(ctx.chemin_sortie),
                        url=page.url,
                    )
            except Exception:
                continue

        if adresses_directes and ecartees is not None and set(adresses_directes) <= ecartees:
            return None  # toutes écartées : on attend la bonne

        if not adresses_directes:
            bouton = navigateur.premier_visible(
                page, sel.get("bouton_telecharger", []), timeout=8000
            )
            if bouton is not None:
                try:
                    with page.expect_download(timeout=180000) as attente:
                        bouton.click()
                    attente.value.save_as(str(ctx.chemin_sortie))
                    if ecartees is not None and self._mettre_de_cote_si_autre(ctx, "bouton"):
                        return base.ResultatGeneration(
                            base.A_VERIFIER,
                            "La vidéo téléchargée démarre sur l'image d'une autre scène : "
                            "gardée à part, pas rattachée. Rien n'a été relancé.",
                            instruction="Regarde la conversation Grok, puis clique "
                            "« Récupérer le résultat ».",
                            url=page.url,
                        )
                    return base.ResultatGeneration(
                        base.TERMINE,
                        "Vidéo téléchargée via le bouton de téléchargement.",
                        chemin=str(ctx.chemin_sortie),
                        url=page.url,
                    )
                except Exception as exc:
                    ctx.journaliser(f"Téléchargement par bouton impossible : {exc}")

        return base.ResultatGeneration(
            base.A_VERIFIER,
            "Vidéo visible mais impossible a télécharger automatiquement.",
            instruction="Téléchargé-la manuellement depuis la page, puis clique "
            "« Importer le résultat ».",
            url=page.url,
        )
