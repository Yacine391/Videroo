"""Aller chercher un résultat déjà produit, sans rien renvoyer.

« Après un plantage, vérifier si un résultat existe déjà avant de relancer quoi
que ce soit. » Le 7 novembre 2026 l'application n'en était pas capable : quand
la récolte ratait alors que ChatGPT avait bel et bien fait l'image, le seul
bouton disponible était « Réessayer », qui renvoie un prompt et refait payer la
même image. Ce chemin-là ouvre, lit, télécharge — et rien d'autre.
"""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

import tests
from videroo.adapters import base, chatgpt, grok


class ImageFactice:
    def __init__(self, src):
        self._src = src

    def get_attribute(self, nom):
        return self._src if nom == "src" else None


class PageImages:
    """Une conversation dont la liste d'images change à chaque lecture."""

    def __init__(self, etapes):
        self.etapes = [list(e) for e in etapes]
        self.url = "https://chatgpt.com/c/abc"
        self.octets = {}

    def _courante(self):
        vues = self.etapes[0]
        if len(self.etapes) > 1:
            self.etapes.pop(0)
        return vues

    def reload(self, **_):
        """Rechargée ou non, cette page montre toujours la même chose."""
        self.rechargements = getattr(self, "rechargements", 0) + 1

    def wait_for_timeout(self, _):
        pass

    def locator(self, selecteur):
        page = self

        class Liste:
            def all(self):
                return [ImageFactice(s) for s in page._courante()]

            @property
            def first(self):
                class Vide:
                    @staticmethod
                    def is_visible(timeout=0):
                        return False

                    @staticmethod
                    def inner_text(timeout=0):
                        return ""

                return Vide()

            @staticmethod
            def count():
                return 0

        return Liste()


SEL = {"images_reponse": ["img"], "signes_quota": []}


def octets_factices(_page, adresse):
    """Une adresse blob: refabriquée à chaque rendu, un contenu qui ne bouge pas.

    Sondée le 7 novembre 2026, la galerie de ChatGPT renomme ses images à chaque
    affichage : « b », « b2 » et « b3 » sont la MÊME image. On nomme donc le
    contenu par la première lettre de l'adresse.
    """
    return adresse[0].encode()


def avec_lecture_dimages():
    """Branche la lecture d'image sur octets_factices, le temps d'un test."""
    from videroo.adapters import navigateur as nav
    return mock.patch.object(nav, "octets_de_image", octets_factices)


class TestAttenteDImage(unittest.TestCase):
    def test_une_image_de_plus_est_reconnue(self):
        page = PageImages([["a", "b"], ["a", "b"]] + [["a", "b", "c"]] * 4)
        with avec_lecture_dimages():
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(
                page, SEL, ["a", "b"], 10
            )
        self.assertEqual(trouvee, "c")
        self.assertEqual(souci, "")

    def test_une_ancienne_image_readressee_ne_declenche_rien(self):
        """ChatGPT refabrique l'adresse blob: d'une image à chaque rendu.

        « b », « b2 », « b3 » sont la même image sous trois adresses. Le compte
        ne bouge pas non plus, puisque la galerie plafonne : ni l'adresse ni le
        compte ne peuvent trancher. Seul le contenu le peut.
        """
        page = PageImages([["a", "b"], ["a", "b2"], ["a", "b3"]])
        with avec_lecture_dimages():
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(
                page, SEL, ["a", "b"], 3
            )
        self.assertIsNone(trouvee)
        self.assertEqual(souci, "")

    def test_la_galerie_qui_plafonne_ne_bloque_plus_la_detection(self):
        """Le cas réel du 7 novembre 2026, qui a coûté deux fois 420 s.

        La galerie ne garde que les dernières images : quand la neuve arrive, une
        ancienne sort, et le compte RECULE. Exiger « plus d'images qu'avant »
        rendait la détection impossible alors que l'image était là.
        """
        page = PageImages([["a", "b", "c"]] + [["b", "c", "neuve"]] * 4)
        with avec_lecture_dimages():
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(
                page, SEL, ["a", "b", "c"], 10
            )
        self.assertEqual(trouvee, "neuve")
        self.assertEqual(souci, "")

    def test_une_image_a_moitie_arrivee_nest_pas_prise(self):
        """Deux lectures identiques, sinon on attrape un téléchargement en cours."""
        page = PageImages([["a", "b"], ["a", "pomme"]] + [["a", "poire"]] * 4)
        with avec_lecture_dimages():
            trouvee, _ = chatgpt.AdaptateurChatGPT()._attendre_image(
                page, SEL, ["a", "b"], 10
            )
        self.assertEqual(trouvee, "poire", "on retient celle qui s'est posée")

    def test_un_quota_ne_se_fait_pas_passer_pour_un_delai_depasse(self):
        """Avant, l'arrêt au bout de 5 s ressortait en « rien après 420 s »."""
        class PageQuota(PageImages):
            def locator(self, selecteur):
                if selecteur == "quota":
                    page = self

                    class Signe:
                        @property
                        def first(self):
                            class El:
                                @staticmethod
                                def is_visible(timeout=0):
                                    return True

                                @staticmethod
                                def inner_text(timeout=0):
                                    return "You've reached your limit"

                            return El()

                        @staticmethod
                        def count():
                            return 1

                    return Signe()
                return super().locator(selecteur)

        page = PageQuota([["a"]])
        sel = {"images_reponse": ["img"], "signes_quota": ["quota"]}
        with avec_lecture_dimages():
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(page, sel, ["a"], 30)
        self.assertIsNone(trouvee)
        self.assertIn("limite d'usage", souci)
        self.assertIn("reached your limit", souci)


class TestRepereReleveAvantLEnvoi(unittest.TestCase):
    """Sans repère lisible, on ne devine pas : on s'arrête et on le dit.

    Les adresses `blob:` relevées avant l'envoi peuvent être périmées une fois le
    prompt parti. Si plus aucune ne se lit, la dernière image de la galerie
    ressemble trait pour trait à une image neuve — et la rattacher à la scène
    serait une erreur silencieuse.
    """

    def test_sans_empreinte_de_depart_on_refuse_au_lieu_de_prendre(self):
        page = PageImages([["a", "b"]])
        from videroo.adapters import navigateur as nav

        def _illisible(*_):
            raise RuntimeError("blob révoqué")

        with mock.patch.object(nav, "octets_de_image", _illisible):
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(
                page, SEL, ["a", "b"], 10
            )
        self.assertIsNone(trouvee)
        self.assertIn("empreinte", souci)

    def test_un_repere_fourni_est_utilise_tel_quel(self):
        """C'est le chemin réel : le repère est pris AVANT l'envoi."""
        import hashlib

        page = PageImages([["a", "b"]] + [["a", "b", "neuve"]] * 4)
        deja = {hashlib.sha256(c.encode()).hexdigest() for c in ("a", "b")}
        with avec_lecture_dimages():
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(
                page, SEL, ["a", "b"], 10, deja
            )
        self.assertEqual(trouvee, "neuve")
        self.assertEqual(souci, "")


class TestImageQuiSePose(unittest.TestCase):
    """Une image doit tenir en place avant d'être retenue."""

    def test_une_adresse_qui_change_encore_nest_jamais_retenue(self):
        page = PageImages([["a", f"etape{i}"] for i in range(9)])
        with avec_lecture_dimages():
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(
                page, SEL, ["a"], 6
            )
        self.assertIsNone(trouvee, "rien ne s'est posé : on n'attrape rien")
        self.assertEqual(souci, "")


class TestCoutDeLaSurveillance(unittest.TestCase):
    """Attendre ne doit pas encombrer le navigateur qu'on surveille."""

    def test_une_adresse_inchangee_nest_pas_relue(self):
        """Relire 2 Mo toutes les 1,2 s pendant 420 s, c'est 350 lectures.

        La même adresse blob: désigne le même contenu : une seule lecture suffit.
        """
        lectures = []

        def _compter(_page, adresse):
            lectures.append(adresse)
            return adresse[0].encode()

        # La page montre « a », puis « neuve » qui ne bouge plus pendant 5 tours.
        page = PageImages([["a"]] + [["a", "neuve"]] * 5)
        from videroo.adapters import navigateur as nav
        with mock.patch.object(nav, "octets_de_image", _compter):
            trouvee, _ = chatgpt.AdaptateurChatGPT()._attendre_image(page, SEL, ["a"], 10)
        self.assertEqual(trouvee, "neuve")
        self.assertEqual(
            lectures.count("neuve"), 1,
            f"une seule lecture attendue pour « neuve », {lectures.count('neuve')} faites",
        )


class TestOrdreDesVideos(unittest.TestCase):
    def test_lordre_de_la_page_est_conserve(self):
        """« La dernière » n'a de sens que dans un ordre ; un ensemble n'en a pas."""
        class VideoFactice:
            def __init__(self, src):
                self._src = src

            def get_attribute(self, nom):
                return self._src if nom == "src" else None

        class Page:
            @staticmethod
            def locator(selecteur):
                class Liste:
                    @staticmethod
                    def all():
                        return [VideoFactice(s) for s in ("v1", "v2", "v3")]

                return Liste()

        ordonnees = grok.AdaptateurGrok._videos_ordonnees(
            Page(), {"videos_reponse": ["video"]}
        )
        self.assertEqual(ordonnees, ["v1", "v2", "v3"])
        self.assertEqual(ordonnees[-1], "v3")


class TestRecuperationSansGeneration(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.sortie = Path(tempfile.mkdtemp()) / "scene-02_image.png"

    def _ctx(self):
        return base.ContexteTache(
            projet_id=1, tache_id=1, cible_type="image", cible_id=1,
            libelle="", prompt="", dossier_projet=self.sortie.parent,
            chemin_sortie=self.sortie,
        )

    def test_un_fournisseur_sans_recuperation_le_dit_au_lieu_de_planter(self):
        resultat = base.Adaptateur().recuperer(self._ctx())
        self.assertEqual(resultat.etat, base.INTERVENTION)
        self.assertIn("Importer le résultat", resultat.instruction)

    def test_la_recuperation_refuse_pendant_une_generation(self):
        """Deux choses qui se disputent le navigateur, c'est la panne assurée."""
        from videroo import db, runner, services

        pid = services.creer_projet("Récupération")
        db.exec1(
            "INSERT INTO taches (projet_id, cible_type, cible_id, libelle, fournisseur,"
            " mode, origine, statut, empreinte, cree_le) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, "image", 1, "x", "chatgpt", "auto", "auto", "en_cours", "e",
             db.maintenant()),
        )
        with self.assertRaises(runner.GenerationRefusee) as piege:
            runner.recuperer_resultat(pid, "image", 1)
        self.assertIn("en cours", str(piege.exception))


class AdaptateurQuiRend(base.Adaptateur):
    """Un faux fournisseur qui depose les octets qu'on lui donne."""

    nom = "faux"

    def __init__(self, octets: bytes):
        self.octets = octets

    def recuperer(self, ctx):
        ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        ctx.chemin_sortie.write_bytes(self.octets)
        return base.ResultatGeneration(
            base.TERMINE, "image lue dans la conversation", str(ctx.chemin_sortie)
        )


class SessionDirecte:
    """Pas de navigateur dans les tests : on appelle la fonction sur place."""

    @staticmethod
    def executer(fonction, *args, **kwargs):
        return fonction(*args, **kwargs)


class TestRecuperationQuiRendLaPlanche(unittest.TestCase):
    """Le 7 novembre 2026, « Recuperer le resultat » a rendu la planche.

    L'image de la scene 1 n'avait jamais ete produite : la derniere image de la
    conversation ChatGPT etait donc la derniere planche. La recuperation l'a
    prise et l'a rattachee a la scene, et Grok se serait mis a animer la
    planche de reference. On compare desormais le CONTENU du fichier obtenu a
    tous les medias deja rattaches au projet.
    """

    def setUp(self):
        from videroo import db, fichiers, services

        tests.reinitialiser()
        self.projet_id = services.creer_projet("Doublon")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_variantes())
        )
        self.pr = services.projet(self.projet_id)
        self.perso = db.q(
            "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre, id",
            (self.projet_id,),
        )[-1]
        self.scene = db.q(
            "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (self.projet_id,)
        )[0]
        # La planche existe ; l'image de la scene, non.
        planche = fichiers.chemin_planche(
            self.pr["dossier"], self.perso["code"], self.perso["surnom"],
            self.perso["etat"] or "UNIQUE",
        )
        tests.fabriquer_image_test(planche, texte=self.perso["code"])
        services.importer_media(
            self.projet_id, "planche", self.perso["id"], planche.name,
            planche.read_bytes(),
        )
        self.octets_planche = Path(
            db.q1("SELECT ref_path FROM personnages WHERE id=?",
                  (self.perso["id"],))["ref_path"]
        ).read_bytes()

    def _recuperer_avec(self, octets):
        from videroo import runner
        from videroo.adapters import navigateur as nav

        with mock.patch.object(runner, "adaptateur",
                               return_value=AdaptateurQuiRend(octets)), \
             mock.patch.object(nav, "session", return_value=SessionDirecte()):
            return runner.recuperer_resultat(
                self.projet_id, "image", self.scene["id"]
            )

    def test_la_planche_nest_pas_rattachee_a_la_scene(self):
        from videroo import db

        bilan = self._recuperer_avec(self.octets_planche)
        self.assertFalse(bilan["ok"])
        self.assertIn(self.perso["surnom"], bilan["message"])
        apres = db.q1("SELECT image_path FROM scenes WHERE id=?", (self.scene["id"],))
        self.assertFalse(
            apres["image_path"],
            "la planche a quand meme ete rattachee a la scene",
        )

    def test_le_fichier_pris_a_tort_est_efface(self):
        """Une copie exacte ne laisse rien derriere elle, mais l'original reste."""
        from videroo import db, runner

        self._recuperer_avec(self.octets_planche)
        cible = runner.chemin_attendu(self.projet_id, "image", self.scene["id"])
        self.assertFalse(cible.exists(), "le doublon occupe encore la place de la scene")
        self.assertEqual(
            list(cible.parent.glob(f"{cible.stem}_doublon_*")),
            [],
            "le doublon a ete garde alors qu'il est identique a un media deja la",
        )
        planche = db.q1(
            "SELECT ref_path FROM personnages WHERE id=?", (self.perso["id"],)
        )
        self.assertTrue(
            Path(planche["ref_path"]).exists(),
            "l'original a disparu avec le doublon",
        )

    def _tache_a_verifier(self) -> int:
        """Une tache laissee « a verifier », comme apres un delai depasse."""
        from videroo import db

        return db.exec1(
            "INSERT INTO taches (projet_id, cible_type, cible_id, libelle,"
            " fournisseur, mode, origine, statut, cree_le)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (self.projet_id, "image", self.scene["id"], "Image scene 1",
             "chatgpt", "auto", "manuel", "a_verifier", db.maintenant()),
        )

    def test_la_recuperation_a_vide_est_notee_sur_la_tache(self):
        """Une tache « a verifier » doit garder la trace des recuperations vides."""
        from videroo import db

        tache = self._tache_a_verifier()

        self._recuperer_avec(self.octets_planche)

        apres = db.q1(
            "SELECT recup_doublons, recup_doublon_de FROM taches WHERE id=?", (tache,)
        )
        self.assertEqual(apres["recup_doublons"], 1)
        self.assertIn(self.perso["surnom"], apres["recup_doublon_de"])

    def test_apres_une_recuperation_a_vide_on_ne_reclame_plus_de_recuperation(self):
        """Reclamer une 4e recuperation serait une boucle sans issue.

        Le 7 novembre 2026, la video de la scene 7 a ete « recuperee » trois
        fois : chaque fois Grok rendait la video de la scene 6, octet pour
        octet. L'atelier reclamait pourtant encore « Recuperer le resultat ».
        """
        from videroo import db, runner

        tache = self._tache_a_verifier()
        self._recuperer_avec(self.octets_planche)

        etat, motif = runner.etat_cible(self.projet_id, "image", self.scene["id"])
        self.assertEqual(etat, runner.BLOQUE)
        self.assertNotIn("Récupérer le résultat", motif)
        self.assertIn("régénération explicite", motif)
        self.assertIn(self.perso["surnom"], motif)

    def test_rien_na_ete_genere_ni_relance(self):
        from videroo import db

        self._recuperer_avec(self.octets_planche)
        self.assertEqual(
            db.q("SELECT id FROM taches WHERE projet_id=? AND cible_type='image'",
                 (self.projet_id,)),
            [],
        )

    def test_une_vraie_image_neuve_est_bien_rattachee(self):
        """Le garde-fou ne doit pas bloquer le cas normal."""
        from videroo import db

        bilan = self._recuperer_avec(self.octets_planche + b"-autre-image")
        self.assertTrue(bilan["ok"], bilan["message"])
        apres = db.q1("SELECT image_path FROM scenes WHERE id=?", (self.scene["id"],))
        self.assertTrue(apres["image_path"])

    def test_recuperer_la_planche_elle_meme_reste_possible(self):
        """Son propre fichier ne compte pas comme un doublon de lui-meme."""
        from videroo import runner
        from videroo.adapters import navigateur as nav

        with mock.patch.object(
            runner, "adaptateur", return_value=AdaptateurQuiRend(self.octets_planche)
        ), mock.patch.object(nav, "session", return_value=SessionDirecte()):
            bilan = runner.recuperer_resultat(
                self.projet_id, "planche", self.perso["id"]
            )
        self.assertTrue(bilan["ok"], bilan["message"])


if __name__ == "__main__":
    unittest.main()


class PageParSelecteur:
    """Une page où chaque sélecteur voit sa propre liste, dans l'ordre donné."""

    def __init__(self, par_selecteur, apres_rechargement=None):
        self.par_selecteur = dict(par_selecteur)
        self.apres_rechargement = apres_rechargement
        self.url = "https://chatgpt.com/c/abc"
        self.rechargements = 0

    def locator(self, selecteur):
        page = self

        class Liste:
            @staticmethod
            def all():
                return [ImageFactice(s) for s in page.par_selecteur.get(selecteur, [])]

        return Liste()

    def reload(self, **_):
        self.rechargements += 1
        if self.apres_rechargement is not None:
            self.par_selecteur = dict(self.apres_rechargement)

    def wait_for_timeout(self, _):
        pass


class TestOrdreDesImages(unittest.TestCase):
    """L'ordre rendu doit être celui de la PAGE, pas celui des sélecteurs."""

    SEL = {
        "images_reponse": [
            "[data-testid='generated-image-gallery'] img",
            "main img[src^='blob:']",
        ],
    }

    def test_le_filet_large_ne_pollue_pas_les_selecteurs_precis(self):
        """Le 7 novembre 2026, « la dernière image » était la planche R1 jointe.

        Le filet « toute image blob: » ramasse aussi les pièces jointes. Les
        coller après les images générées faisait finir la liste par une pièce
        jointe : c'est elle qui était récupérée.
        """
        page = PageParSelecteur({
            "[data-testid='generated-image-gallery'] img": ["gen1", "gen2"],
            "main img[src^='blob:']": ["gen1", "gen2", "piece-jointe-R1"],
        })
        trouvees = chatgpt.AdaptateurChatGPT._sources_images(page, self.SEL)
        self.assertEqual(trouvees, ["gen1", "gen2"])
        self.assertEqual(trouvees[-1], "gen2", "la dernière doit être une génération")

    def test_le_filet_sert_quand_les_precis_sont_aveugles(self):
        page = PageParSelecteur({"main img[src^='blob:']": ["a", "b"]})
        self.assertEqual(
            chatgpt.AdaptateurChatGPT._sources_images(page, self.SEL), ["a", "b"]
        )

    def test_les_avatars_restent_ecartes(self):
        page = PageParSelecteur({
            "[data-testid='generated-image-gallery'] img":
                ["https://cdn.auth0.com/avatars/jf.png", "vraie"],
        })
        self.assertEqual(
            chatgpt.AdaptateurChatGPT._sources_images(page, self.SEL), ["vraie"]
        )


class TestRechargementDeLaDerniereChance(unittest.TestCase):
    """ChatGPT ne rattache pas toujours l'image à la vue en cours.

    Le 7 novembre 2026, l'application a regardé 420 s sans rien voir ; la même
    image est apparue dès la page rechargée. Recharger n'envoie rien et ne
    consomme aucun quota : c'est la dernière chose à essayer avant d'abandonner.
    """

    SEL = {"images_reponse": ["img"], "signes_quota": []}

    def test_limage_apparait_apres_rechargement(self):
        page = PageParSelecteur({"img": ["a"]}, apres_rechargement={"img": ["a", "b"]})
        with avec_lecture_dimages():
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(page, self.SEL, ["a"], 2)
        self.assertEqual(trouvee, "b")
        self.assertEqual(souci, "page rechargée")
        self.assertEqual(page.rechargements, 1)

    def test_on_ne_recharge_quune_fois_et_on_abandonne_proprement(self):
        page = PageParSelecteur({"img": ["a"]}, apres_rechargement={"img": ["a"]})
        with avec_lecture_dimages():
            trouvee, souci = chatgpt.AdaptateurChatGPT()._attendre_image(page, self.SEL, ["a"], 2)
        self.assertIsNone(trouvee)
        self.assertEqual(souci, "")
        self.assertEqual(page.rechargements, 1)


class TestVideoApparueTropTot(unittest.TestCase):
    """Le 7 novembre 2026, la scène 10 a reçu la vidéo de la scène 9.

    Elle a été « trouvée » sept secondes après l'envoi : Grok ne produit rien
    en sept secondes. C'était une ancienne vidéo de la conversation, affichée
    par la page en descendant parmi 184 vidéos.
    """

    def _attendre(self, suite, plancher=1.0, delai=4):
        from videroo import config
        from videroo.adapters import grok

        adaptateur = grok.AdaptateurGrok()
        lectures = iter(suite)
        dernier = [set()]

        def sources(self_, page, sel):
            try:
                dernier[0] = set(next(lectures))
            except StopIteration:
                pass
            return dernier[0]

        with mock.patch.object(grok.AdaptateurGrok, "_sources_videos", sources), \
             mock.patch.object(config, "DELAI_MINIMUM_VIDEO", plancher):
            return adaptateur._attendre_video(None, {}, set(), delai)

    def test_une_video_trop_precoce_nest_pas_prise_pour_la_notre(self):
        # Une seule ancienne vidéo se montre, et plus rien ensuite.
        self.assertEqual(self._attendre([{"ancienne"}]), "rien")

    def test_la_vraie_video_apparue_apres_le_plancher_est_bien_retenue(self):
        suite = [{"ancienne"}] * 2 + [{"ancienne", "la-notre"}] * 20
        self.assertEqual(self._attendre(suite, plancher=1.0, delai=8), "video")


class TestChatGPTFige(unittest.TestCase):
    """Le 12 novembre 2026, ChatGPT est resté « En cours depuis 54 min » sans image.

    Rien ne doit partir tout seul ; mais quand TU cliques « Réessayer », la
    réponse figée est arrêtée au lieu de bloquer le nouvel envoi.
    """

    def test_stop_clique_puis_reponse_arretee(self):
        from videroo.adapters import navigateur as nav

        etat = {"tourne": True}

        class Bouton:
            def click(self, timeout=0):
                etat["tourne"] = False

        with mock.patch.object(nav, "premier_visible", return_value=Bouton()), \
             mock.patch.object(nav, "present",
                               side_effect=lambda *a, **k: "stop" if etat["tourne"] else None):
            self.assertTrue(chatgpt.AdaptateurChatGPT._arreter_reponse(object(), {}, 3))
        self.assertFalse(etat["tourne"])

    def test_stop_sans_effet_est_signale(self):
        from videroo.adapters import navigateur as nav

        class Bouton:
            def click(self, timeout=0):
                pass

        with mock.patch.object(nav, "premier_visible", return_value=Bouton()), \
             mock.patch.object(nav, "present", return_value="stop"):
            self.assertFalse(chatgpt.AdaptateurChatGPT._arreter_reponse(object(), {}, 1))

    def test_le_contexte_dit_qui_a_lance_et_ce_qui_est_deja_range(self):
        from videroo import db, fichiers, runner, services

        tests.reinitialiser()
        pid = services.creer_projet("Fige")
        services.appliquer_pack(pid, services.analyser_pack(tests.pack_variantes()))
        scenes = db.q("SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (pid,))
        image1 = runner.chemin_attendu(pid, "image", scenes[0]["id"])
        tests.fabriquer_image_test(image1, texte="S1")
        services.importer_media(pid, "image", scenes[0]["id"], image1.name, image1.read_bytes())
        tache = {"projet_id": pid, "id": 1, "cible_type": "image",
                 "cible_id": scenes[1]["id"], "libelle": "x",
                 "fournisseur": "chatgpt", "origine": "auto"}
        ctx = runner.construire_contexte(
            tache, runner.chemin_attendu(pid, "image", scenes[1]["id"])
        )
        self.assertEqual(ctx.extras["origine"], "auto")
        self.assertIn("l'image de la scène 1", ctx.extras["connues"].values())
