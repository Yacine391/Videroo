"""Automatisation de l'interface web ChatGPT (images des planches et des scènes)."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .. import reglages
from . import base, navigateur, selecteurs


class AdaptateurChatGPT(base.Adaptateur):
    #: Lectures identiques d'affilée avant de déclarer une image posée.
    LECTURES_POUR_SE_POSER = 3
    nom = "chatgpt-auto"

    def generer(self, ctx: base.ContexteTache) -> base.ResultatGeneration:
        depart = time.monotonic()

        def chrono(etape: str) -> None:
            ctx.journaliser(f"⏱ {etape} à {time.monotonic() - depart:.0f} s")

        sel = selecteurs.charger()["chatgpt"]
        url = ctx.url_chat or reglages.lire("url_chatgpt") or sel["url"]

        try:
            page = navigateur.session().page("chatgpt", url, ctx.profil)
        except Exception as exc:
            quoi, action = navigateur.raison_navigateur(exc)
            return base.ResultatGeneration(
                base.INTERVENTION,
                f"Navigateur dédié indisponible : {quoi}. Rien n'a été envoyé.",
                instruction=action,
            )

        chrono("page ChatGPT prête")
        if navigateur.present(page, sel.get("signes_connexion", []), timeout=2500):
            return base.ResultatGeneration(
                base.INTERVENTION,
                "ChatGPT demande une connexion.",
                instruction="Connecte-toi a ton compte ChatGPT dans le navigateur dédié "
                "déjà ouvert, puis clique sur Reprendre.",
                url=url,
            )
        vu = navigateur.preuve(page, sel.get("signes_quota", []), timeout=1200)
        if vu:
            return base.ResultatGeneration(
                base.QUOTA,
                f"ChatGPT signale une limite d'usage sur « {ctx.compte_libelle} » : "
                f"{vu}. Rien n'a été envoyé.",
                instruction="Attends la fin de ta limite, ou ajoute un autre compte "
                "ChatGPT dans Admin.",
                url=url,
            )

        avant = self._images_stables(page, sel, ctx)
        # Tout de suite, pendant que ces adresses blob: valent encore quelque
        # chose : joindre un fichier et envoyer le prompt font re-rendre la
        # galerie, et une adresse périmée ne se lit plus.
        deja_vues = self._empreintes(page, avant)
        # Les médias déjà rangés dans le projet ne sont jamais le résultat : ils
        # s'ajoutent au repère. Sans repère lisible on ne le fabrique pas avec
        # eux, pour que le refus « empreintes illisibles » reste possible.
        connues = set(ctx.extras.get("connues") or {})
        repere = (deja_vues | connues) if (deja_vues or not avant) else deja_vues

        if ctx.fichiers_joints:
            manquants = [str(c) for c in ctx.fichiers_joints if not Path(c).exists()]
            if manquants:
                return base.ResultatGeneration(
                    base.ECHEC,
                    f"Fichiers de référence absents : {manquants}. Rien n'a été envoyé.",
                )
            joint, detail = navigateur.joindre_fichiers(
                page, sel.get("champ_fichier", []), [Path(c) for c in ctx.fichiers_joints]
            )
            if not joint:
                return base.ResultatGeneration(
                    base.ECHEC,
                    "Impossible de joindre les images de référence. Aucun prompt "
                    f"envoyé, aucun quota consommé. Motif : {detail}",
                    instruction="Répare le sélecteur « champ_fichier » dans Admin > "
                    "Sélecteurs, ou passe en mode assisté.",
                )
            ctx.journaliser(
                f"{len(ctx.fichiers_joints)} image(s) de référence jointe(s) via "
                f"{detail} : " + ", ".join(Path(c).name for c in ctx.fichiers_joints)
            )
            time.sleep(3)  # laisser l'envoi des pieces jointes se terminer
            chrono("références jointes")

        # ChatGPT écrit souvent encore sa phrase après l'image précédente. Tant que
        # c'est le cas, le bouton d'envoi est un bouton « Stop » : le prompt ne part
        # pas (constaté le 10 novembre 2026, scène 2 jamais reçue). On attend qu'il
        # ait fini, sans rien envoyer.
        #
        # Sur un « Réessayer » que TU as cliqué, une réponse qui tourne encore est
        # celle qui s'est figée (le 12 novembre 2026 : « En cours depuis 54 min »,
        # aucune image). L'attendre quatre minutes pour finir en échec te faisait
        # perdre du temps : on l'arrête, c'est ce que tu as demandé. Une chaîne
        # automatique, elle, n'arrête jamais rien.
        manuel = ctx.extras.get("origine") == "manuel"
        for _ in range(15 if manuel else 120):
            if not navigateur.present(page, sel.get("bouton_stop", []), timeout=500):
                break
            time.sleep(2)
        else:
            if manuel and self._arreter_reponse(page, sel):
                ctx.journaliser(
                    "ChatGPT tournait encore sur l'ancienne demande : réponse figée "
                    "arrêtée (tu as cliqué « Réessayer »). Le prompt part maintenant, "
                    "une seule fois."
                )
            else:
                return base.ResultatGeneration(
                    base.ECHEC,
                    "ChatGPT répond encore à la demande précédente : rien n'a été "
                    "envoyé, aucun quota consommé.",
                    instruction="S'il est figé, clique « Réessayer » : l'application "
                    "arrêtera la réponse figée puis enverra le prompt une fois.",
                    url=page.url,
                )
        navigateur.fermer_surcouches(page)
        saisi, pourquoi = navigateur.saisir(page, sel.get("zone_saisie", []), ctx.prompt)
        if not saisi:
            return base.ResultatGeneration(
                base.ECHEC,
                f"Prompt non saisi dans ChatGPT, rien n'a été envoyé : {pourquoi}.",
                instruction="Si la zone est bien là mais recouverte, ferme ce qui est "
                "ouvert dans la page puis clique « Réessayer ». Si elle est vraiment "
                "absente, répare « zone_saisie » dans Admin > Sélecteurs.",
                url=page.url,
            )

        envoye, comment = navigateur.envoyer(
            page, sel.get("zone_saisie", []), sel.get("bouton_envoyer", [])
        )
        if not envoye:
            return base.ResultatGeneration(
                base.ECHEC,
                "Le prompt est saisi dans ChatGPT mais il n'est pas parti : la zone de "
                "saisie ne s'est pas vidée. Rien n'a été consommé.",
                instruction="La page est ouverte avec le prompt prêt : envoie-le "
                "toi-même, puis clique « Importer le résultat ». Si ça se répète, "
                "répare « bouton_envoyer » dans Admin > Sélecteurs.",
                url=page.url,
            )
        # Point de non-retour : a partir d'ici le quota peut avoir ete depense,
        # donc plus aucune relance automatique n'est permise sur cet element.
        ctx.envoye = True
        chrono(f"prompt envoyé ({comment})")
        ctx.journaliser(
            f"Prompt envoyé à ChatGPT ({comment}). UNE SEULE génération demandée."
        )

        delai = reglages.lire_int("delai_image")
        ctx.journaliser(
            f"{len(avant)} image(s) déjà dans la conversation avant l'envoi ; "
            "c'est la suivante qui sera récupérée."
        )
        nouvelle, souci = self._attendre_image(
            page, sel, avant, delai, repere
        )
        if nouvelle is None and not souci and self._repond_encore(page, sel):
            # Une image lente n'est pas une image perdue : tant que ChatGPT montre
            # qu'il travaille, on lui laisse une seconde fois le même délai. Rien
            # n'est renvoyé, et ça t'évite un « Récupérer » pour une image en route.
            ctx.journaliser(
                f"ChatGPT travaille encore après {delai} s : attente prolongée de "
                f"{delai} s, rien n'est renvoyé."
            )
            nouvelle, souci = self._attendre_image(
                page, sel, avant, delai, repere
            )
        chrono("fin de l'attente de l'image")
        if nouvelle is None:
            if not souci and self._repond_encore(page, sel):
                minutes = (time.monotonic() - depart) / 60
                return base.ResultatGeneration(
                    base.A_VERIFIER,
                    f"ChatGPT est figé : sa réponse tourne depuis {minutes:.0f} min "
                    "sans produire d'image. L'application n'a rien relancé.",
                    instruction="Clique « Réessayer » : l'application arrêtera cette "
                    "réponse figée puis renverra le prompt UNE fois.",
                    url=page.url,
                )
            if souci:
                return base.ResultatGeneration(
                    base.A_VERIFIER,
                    f"Attente interrompue : {souci}. Le prompt est parti ; l'image est "
                    "peut-être quand même en route.",
                    instruction="Regarde la page, puis clique « Récupérer le résultat » : "
                    "l'application ira lire la conversation sans rien renvoyer.",
                    url=page.url,
                )
            return base.ResultatGeneration(
                base.A_VERIFIER,
                f"Aucune image recuperee après {delai} s. L'application ne relance "
                "RIEN : la génération est peut-être encore en cours chez ChatGPT.",
                instruction="Clique « Récupérer le résultat » : l'application lira la "
                "conversation et ramènera la dernière image, sans rien renvoyer ni "
                "consommer de quota.",
                url=page.url,
            )

        try:
            donnees = navigateur.octets_de_image(page, nouvelle)
        except Exception as exc:
            return base.ResultatGeneration(
                base.A_VERIFIER,
                f"Image affichée mais téléchargement impossible ({exc}). Elle est bien "
                "là, chez ChatGPT : ton quota n'est pas perdu.",
                instruction="Enregistre l'image manuellement depuis la page, puis "
                "clique « Importer le résultat ».",
                url=page.url,
            )

        ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        ctx.chemin_sortie.write_bytes(donnees)
        if ctx.chemin_sortie.stat().st_size == 0:
            return base.ResultatGeneration(
                base.A_VERIFIER, "Fichier téléchargé vide.", url=page.url
            )
        apres_rechargement = " (vue seulement après rechargement de la page)" if souci else ""
        return base.ResultatGeneration(
            base.TERMINE,
            f"Image enregistrée ({ctx.chemin_sortie.stat().st_size // 1024} Ko) "
            f"en {time.monotonic() - depart:.0f} s{apres_rechargement}.",
            chemin=str(ctx.chemin_sortie),
            url=page.url,
        )

    def recuperer(self, ctx: base.ContexteTache) -> base.ResultatGeneration:
        """Récupère la dernière image de la conversation, sans rien envoyer.

        Le 7 novembre 2026, l'application a tué son propre navigateur pendant
        qu'il attendait une image : ChatGPT l'avait bel et bien produite, elle
        est restée dans la conversation, et le seul moyen de la récupérer était
        de relancer la génération — donc de payer deux fois. Ce chemin-là
        n'écrit rien, n'envoie rien, ne clique sur rien : il ouvre, il lit, il
        télécharge.
        """
        sel = selecteurs.charger()["chatgpt"]
        url = ctx.chat_compte or ctx.url_chat or reglages.lire("url_chatgpt") or sel["url"]
        try:
            page = navigateur.session().page("chatgpt", url, ctx.profil)
        except Exception as exc:
            quoi, action = navigateur.raison_navigateur(exc)
            return base.ResultatGeneration(
                base.INTERVENTION,
                f"Navigateur dédié indisponible : {quoi}. Rien n'a été envoyé.",
                instruction=action,
            )
        if navigateur.present(page, sel.get("signes_connexion", []), timeout=2500):
            return base.ResultatGeneration(
                base.INTERVENTION,
                "ChatGPT demande une connexion : impossible de lire la conversation.",
                instruction="Connecte-toi dans le navigateur dédié, puis réessaie la "
                "récupération.",
                url=page.url,
            )

        # Laisser la conversation finir de charger : une image qui n'est pas
        # encore affichee n'est pas une image absente.
        if self._repond_encore(page, sel):
            return base.ResultatGeneration(
                base.A_VERIFIER,
                "ChatGPT n'a pas fini : sa réponse est toujours en cours, il n'y a "
                "pas encore d'image à récupérer. Rien n'a été pris.",
                instruction="Si ça dure depuis longtemps, ChatGPT est figé : clique "
                "« Réessayer », l'application arrêtera la réponse figée et renverra "
                "le prompt une fois.",
                url=page.url,
            )
        presentes = self._images_stables(page, sel, ctx)
        if not presentes:
            return base.ResultatGeneration(
                base.A_VERIFIER,
                "Aucune image trouvée dans cette conversation ChatGPT. Rien n'a été "
                "envoyé, aucun quota consommé.",
                instruction="Vérifie que c'est bien la bonne conversation. Si l'image "
                "est ailleurs, enregistre-la et utilise « Importer le résultat ».",
                url=page.url,
            )
        rang = max(1, int(ctx.extras.get("rang", 1)))
        if rang > len(presentes):
            return base.ResultatGeneration(
                base.A_VERIFIER,
                f"Cette conversation ne contient que {len(presentes)} image(s) : "
                f"impossible de remonter à la {rang}ᵉ en partant de la fin.",
                url=page.url,
            )
        try:
            donnees = navigateur.octets_de_image(page, presentes[-rang])
        except Exception as exc:
            return base.ResultatGeneration(
                base.A_VERIFIER,
                f"Dernière image repérée mais téléchargement impossible ({exc}).",
                instruction="Enregistre-la manuellement depuis la page, puis clique "
                "« Importer le résultat ».",
                url=page.url,
            )
        ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        ctx.chemin_sortie.write_bytes(donnees)
        taille = ctx.chemin_sortie.stat().st_size
        if taille == 0:
            ctx.chemin_sortie.unlink(missing_ok=True)
            return base.ResultatGeneration(
                base.A_VERIFIER,
                "Le fichier récupéré était vide : rien n'a été enregistré.",
                url=page.url,
            )
        position = "dernière" if rang == 1 else f"{rang}ᵉ en partant de la fin"
        return base.ResultatGeneration(
            base.TERMINE,
            f"Image récupérée sans aucune génération ({taille // 1024} Ko) : la "
            f"{position} des {len(presentes)} images de la conversation. Regarde-la : "
            "rien ne garantit que ce soit celle de cette scène.",
            chemin=str(ctx.chemin_sortie),
            url=page.url,
        )

    @staticmethod
    def _repond_encore(page, sel) -> bool:
        """ChatGPT montre-t-il qu'il travaille encore (bouton « Stop ») ?"""
        try:
            return bool(navigateur.present(page, sel.get("bouton_stop", []), timeout=800))
        except Exception:
            return False

    @classmethod
    def _arreter_reponse(cls, page, sel, secondes: float = 20) -> bool:
        """Clique « Stop » et attend que la réponse soit vraiment arrêtée."""
        bouton = navigateur.premier_visible(page, sel.get("bouton_stop", []), timeout=1500)
        if bouton is None:
            return not cls._repond_encore(page, sel)
        try:
            bouton.click(timeout=3000)
        except Exception:
            return False
        fin = time.time() + secondes
        while time.time() < fin:
            if not cls._repond_encore(page, sel):
                return True
            time.sleep(1)
        return False

    def _images_stables(self, page, sel, ctx, attente_max: float = 20) -> list[str]:
        """Les images de la conversation, une fois l'affichage calmé."""
        vues = self._sources_images(page, sel)
        fin = time.time() + attente_max
        stables = 0
        while time.time() < fin and stables < 3:
            time.sleep(1.2)
            maintenant = self._sources_images(page, sel)
            stables = stables + 1 if maintenant == vues else 0
            vues = maintenant
        return vues

    # Avatars du compte et pictogrammes : jamais des images générées.
    IGNOREES = ("cdn.auth0.com", "/avatars/", "data:image/svg")

    @classmethod
    def _sources_images(cls, page, sel) -> list[str]:
        """Les images générées, DANS L'ORDRE de la page.

        Deux règles, et toutes les deux ont été payées cher.

        L'ORDRE d'abord. L'ancienne version balayait les sélecteurs l'un après
        l'autre et collait les résultats bout à bout : l'ordre rendu était celui
        des SÉLECTEURS, pas celui de la page. « La dernière image » tombait donc
        sur la dernière trouvaille du dernier sélecteur. Le 7 novembre 2026,
        c'était la planche de référence R1 jointe au prompt — pas l'image de la
        scène.

        LE PREMIER QUI TROUVE ensuite. La liste va du plus précis au plus large,
        et elle finit par un filet (« toute image blob: de la page ») qui ramasse
        aussi les pièces jointes. Les mélanger faisait compter les références
        comme des générations. On s'arrête donc au premier sélecteur qui voit
        quelque chose : le filet ne sert que si les autres sont aveugles.
        """
        for selecteur in sel.get("images_reponse", []):
            try:
                elements = page.locator(selecteur).all()
            except Exception:
                continue
            trouvees: list[str] = []
            for element in elements:
                try:
                    src = element.get_attribute("src")
                except Exception:
                    continue
                if src and src not in trouvees:
                    if not any(motif in src for motif in cls.IGNOREES):
                        trouvees.append(src)
            if trouvees:
                return trouvees
        return []

    def _attendre_image(
        self, page, sel, avant: list[str], delai: int,
        deja_vues: set[str] | None = None,
    ) -> tuple[str | None, str]:
        """Attend une image dont le CONTENU est nouveau, et rend (adresse, motif).

        Ni compter, ni se fier aux adresses. Sondée le 7 novembre 2026, la
        conversation ne montre aucun fil de messages (`data-message-author-role`
        : zéro) : c'est une galerie qui ne garde montées que les quatre images
        les plus récentes, la plus neuve en dernier. Deux conséquences, et les
        deux anciens garde-fous tombent avec elles :

        * le compte plafonne — le journal annonce « 5 image(s) » à 13 h 56 puis
          « 4 image(s) » à 14 h 04 sans que rien n'ait été supprimé. Exiger
          « plus d'images qu'avant » était une condition qui ne pouvait pas
          devenir vraie : l'attente a couru deux fois 420 s pendant que l'image
          attendue était là, posée au bout de la galerie ;
        * les adresses `blob:` sont refabriquées à chaque rendu. Une image qui
          revient à l'écran change d'adresse sans avoir changé d'image, donc
          « adresse inconnue » ne prouve rien non plus.

        Ce qui ne ment pas, c'est l'image elle-même. On relève l'empreinte des
        images présentes avant d'attendre, puis on ne retient la dernière que si
        son contenu ne figure pas parmi elles — et qu'il ne bouge plus d'une
        lecture à l'autre, le temps que le téléchargement se termine. Lire une
        image déjà affichée ne renvoie rien à ChatGPT et ne coûte aucun quota.

        On rend aussi POURQUOI on s'arrête. Avant, un quota repéré en cours
        d'attente ressortait en « aucune image après 420 s » alors qu'on s'était
        arrêté au bout de cinq : le message envoyait chercher une image qui
        n'arriverait jamais.
        """
        if deja_vues is None:
            deja_vues = self._empreintes(page, avant)
        if avant and not deja_vues:
            # Sans repère, la dernière image de la galerie ressemble trait pour
            # trait à une image neuve : on s'arrête plutôt que de rattacher une
            # ancienne image à cette scène.
            return None, (
                "impossible de relever l'empreinte des images déjà présentes"
            )
        fin = time.time() + delai
        tour = 0
        candidate: tuple[str, str, int] | None = None
        while time.time() < fin:
            if navigateur.ARRET.is_set():
                return None, "Videroo s'est arrêté pendant l'attente"
            trouvee, candidate = self._image_au_contenu_neuf(page, sel, deja_vues, candidate)
            if trouvee:
                return trouvee, ""
            if tour % 4 == 0:
                vu = navigateur.preuve(page, sel.get("signes_quota", []), timeout=400)
                if vu:
                    return None, f"ChatGPT affiche une limite d'usage ({vu})"
            tour += 1
            time.sleep(1.2)

        # Dernière chance : ChatGPT ne rattache pas toujours l'image à la vue en
        # cours. Recharger ne renvoie rien et ne consomme aucun quota.
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
        except Exception as exc:
            return None, f"rechargement impossible ({navigateur.premiere_ligne(exc)})"
        # Même exigence qu'avant le rechargement : une image se pose, ou on ne la
        # prend pas. La page est neuve, elle se pose donc tout de suite ; quelques
        # lectures suffisent, et aucune ne renvoie quoi que ce soit à ChatGPT.
        candidate = None
        for _ in range(self.LECTURES_POUR_SE_POSER + 2):
            trouvee, candidate = self._image_au_contenu_neuf(page, sel, deja_vues, candidate)
            if trouvee:
                return trouvee, "page rechargée"
            page.wait_for_timeout(600)
        return None, ""

    @classmethod
    def _image_au_contenu_neuf(
        cls, page, sel, deja_vues: set[str],
        candidate: tuple[str, str, int] | None,
    ) -> tuple[str | None, tuple[str, str, int] | None]:
        """(adresse retenue, candidate à reporter au tour suivant).

        `candidate` est le triplet (adresse, empreinte, lectures) déjà pesé. On
        ne relit l'image QUE si son adresse a changé : la même adresse `blob:`
        désigne le même contenu, et relire 2 Mo à chaque tour pendant sept
        minutes encombrerait le navigateur pour rien.

        Une adresse qui se répète est une image qui s'est posée. On en exige
        plusieurs d'affilée : c'est une marge de sécurité, pas une observation —
        une image en train d'arriver peut rester une seconde sans bouger, et
        l'attente dure de toute façon des minutes.
        """
        presentes = cls._sources_images(page, sel)
        if not presentes:
            return None, None
        derniere = presentes[-1]

        if candidate is not None and candidate[0] == derniere:
            # Même adresse qu'au tour précédent : contenu déjà pesé, rien à relire.
            if candidate[1] in deja_vues:
                return None, candidate
            vues = candidate[2] + 1
            if vues >= cls.LECTURES_POUR_SE_POSER:
                return derniere, None
            return None, (derniere, candidate[1], vues)

        empreinte = cls._empreinte(page, derniere)
        if empreinte is None:
            return None, None
        return None, (derniere, empreinte, 1)

    @classmethod
    def _empreintes(cls, page, adresses: list[str]) -> set[str]:
        return {e for e in (cls._empreinte(page, a) for a in adresses) if e}

    @staticmethod
    def _empreinte(page, adresse: str) -> str | None:
        """L'empreinte du contenu d'une image affichée, ou None si illisible.

        Illisible n'est pas une panne : une image encore en train d'arriver n'a
        pas de contenu à lire, et c'est précisément ce qu'on veut savoir.
        """
        try:
            return hashlib.sha256(navigateur.octets_de_image(page, adresse)).hexdigest()
        except Exception:
            return None
