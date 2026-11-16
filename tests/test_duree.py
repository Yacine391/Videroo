"""La bonne scène avec le bon temps : contrôle et rattrapage des durées."""
import unittest

import tests
from videroo import db, fichiers, media, montage, reglages, runner, services
from videroo.adapters import base, grok, selecteurs


class BaseDuree(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("Durées")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_variantes())
        )
        self.pr = services.projet(self.projet_id)
        self.scene = db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=1", (self.projet_id,)
        )
        self.assertEqual(self.scene["duree"], 6)

    def poser_video(self, duree: float):
        chemin = fichiers.chemin_video_scene(self.pr["dossier"], self.scene["numero"])
        montage.fabriquer_clip_test(chemin, duree=duree, texte="S1")
        services.importer_media(
            self.projet_id, "video", self.scene["id"], chemin.name, chemin.read_bytes()
        )
        return db.q1("SELECT * FROM scenes WHERE id=?", (self.scene["id"],))


class TestControleDeDuree(BaseDuree):
    def test_duree_juste_marquee_conforme(self):
        ligne = self.poser_video(6.0)
        self.assertEqual(ligne["video_conforme"], 1)
        self.assertAlmostEqual(ligne["video_ecart"], 0.0, delta=0.3)
        self.assertEqual(services.scenes_non_conformes(self.projet_id), [])

    def test_duree_trop_longue_signalee(self):
        ligne = self.poser_video(9.0)
        self.assertEqual(ligne["video_conforme"], 0)
        self.assertAlmostEqual(ligne["video_ecart"], 3.0, delta=0.3)
        non_conformes = services.scenes_non_conformes(self.projet_id)
        self.assertEqual(len(non_conformes), 1)
        self.assertEqual(non_conformes[0]["numero"], 1)
        self.assertFalse(non_conformes[0]["rattrapee"])

    def test_la_tolerance_est_reglable(self):
        reglages.definir("tolerance_duree", "4")
        ligne = self.poser_video(9.0)
        self.assertEqual(ligne["video_conforme"], 1, "9 s pour 6 s passe avec ±4 s")

    def test_le_fichier_est_conserve_malgre_l_ecart(self):
        ligne = self.poser_video(9.0)
        self.assertTrue(fichiers.fichier_utilisable(ligne["video_path"]))
        tache = db.q1(
            "SELECT statut FROM taches WHERE projet_id=? ORDER BY id DESC LIMIT 1",
            (self.projet_id,),
        )
        if tache:
            self.assertNotEqual(tache["statut"], "echec")

    def test_journal_explicite(self):
        self.poser_video(9.0)
        messages = " ".join(
            l["message"]
            for l in db.q("SELECT message FROM journal WHERE projet_id=?", (self.projet_id,))
        )
        self.assertIn("Durée non conforme", messages)
        self.assertIn("conservée", messages)

    def test_controle_final_liste_l_ecart(self):
        self.poser_video(9.0)
        manquants = " | ".join(services.controle_final(self.projet_id)["manquants"])
        self.assertIn("au lieu de 6 s voulues", manquants)


class TestRattrapageDeDuree(BaseDuree):
    def test_ramener_a_la_duree_voulue(self):
        self.poser_video(9.0)
        bilan = services.ajuster_duree_scene(self.projet_id, self.scene["id"])
        self.assertAlmostEqual(bilan["trim_fin"], 3.0, delta=0.3)
        ligne = db.q1("SELECT * FROM scenes WHERE id=?", (self.scene["id"],))
        utile = ligne["video_duree"] - ligne["trim_debut"] - ligne["trim_fin"]
        self.assertAlmostEqual(utile, 6.0, delta=0.1)

    def test_l_export_respecte_la_duree_apres_ajustement(self):
        self.poser_video(9.0)
        services.ajuster_duree_scene(self.projet_id, self.scene["id"])
        resultat = services.exporter_scene(self.projet_id, self.scene["id"])
        self.assertTrue(resultat["reussi"], resultat["message"])
        self.assertAlmostEqual(
            media.analyser_media(resultat["chemin"]).duree, 6.0, delta=0.4
        )

    def test_le_fichier_d_origine_n_est_pas_touche(self):
        ligne = self.poser_video(9.0)
        avant = media.analyser_media(ligne["video_path"]).duree
        services.ajuster_duree_scene(self.projet_id, self.scene["id"])
        apres = media.analyser_media(ligne["video_path"]).duree
        self.assertAlmostEqual(avant, apres, delta=0.01)

    def test_marquee_rattrapee_apres_ajustement(self):
        self.poser_video(9.0)
        services.ajuster_duree_scene(self.projet_id, self.scene["id"])
        non_conformes = services.scenes_non_conformes(self.projet_id)
        self.assertTrue(non_conformes[0]["rattrapee"])
        manquants = " | ".join(services.controle_final(self.projet_id)["manquants"])
        self.assertNotIn("au lieu de 6 s voulues", manquants)

    def test_video_trop_courte_refusee(self):
        self.poser_video(3.0)
        with self.assertRaises(services.ErreurMetier) as ctx:
            services.ajuster_duree_scene(self.projet_id, self.scene["id"])
        self.assertIn("On ne peut pas la rallonger", str(ctx.exception))

    def test_sans_video_refuse(self):
        autre = db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=2", (self.projet_id,)
        )
        with self.assertRaises(services.ErreurMetier):
            services.ajuster_duree_scene(self.projet_id, autre["id"])


class PageFactice:
    """Imite juste ce que l'adaptateur utilise, sans navigateur."""

    def __init__(self, reussites: dict[str, bool]):
        self.reussites = reussites
        self.clics: list[str] = []
        self.choix: list[str] = []
        self.url = "https://grok.com/"

    def locator(self, selecteur):
        page = self

        class Element:
            def wait_for(self, **kw):
                if not page.reussites.get(selecteur):
                    raise RuntimeError("absent")

            def click(self, **kw):
                page.clics.append(selecteur)

            def select_option(self, **kw):
                if not page.reussites.get(selecteur):
                    raise RuntimeError("absent")
                page.choix.append(f"{selecteur}:{kw}")

            @property
            def first(self):
                return self

            # Ce que petit_bouton vérifie : visible, texte court, hors navigation.
            def all(self):
                return [self] if page.reussites.get(selecteur) else []

            def is_visible(self):
                return bool(page.reussites.get(selecteur))

            def inner_text(self, **kw):
                return "10s"

            def evaluate(self, _js):
                return True

        return Element()

    def wait_for_timeout(self, _ms):
        pass


class TestReglageDeDureeDansGrok(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.sel = selecteurs.charger()["grok"]
        self.adaptateur = grok.AdaptateurGrok()

    def test_les_selecteurs_de_duree_existent(self):
        for clef in ("reglage_duree", "options_duree", "reglage_format", "ouvrir_reglages"):
            self.assertIn(clef, self.sel, clef)
        self.assertTrue(
            any("{duree}" in m for m in self.sel["options_duree"]),
            "les options doivent être des modèles paramétrés par la durée",
        )

    def test_reglage_par_bouton(self):
        cible = "button:has-text('10s')"
        page = PageFactice({cible: True})
        ok, detail = self.adaptateur._regler_duree(page, self.sel, 10)
        self.assertTrue(ok)
        self.assertIn("10s", detail)
        self.assertEqual(page.clics, [cible])

    def test_reglage_par_menu(self):
        cible = self.sel["reglage_duree"][0]
        page = PageFactice({cible: True})
        ok, detail = self.adaptateur._regler_duree(page, self.sel, 15)
        self.assertTrue(ok)
        self.assertIn("menu", detail)

    def test_echec_signale_sans_planter(self):
        page = PageFactice({})
        ok, detail = self.adaptateur._regler_duree(page, self.sel, 6)
        self.assertFalse(ok)
        self.assertEqual(detail, "")

    def test_duree_absente_ne_tente_rien(self):
        page = PageFactice({"button:has-text('6s')": True})
        ok, _ = self.adaptateur._regler_duree(page, self.sel, None)
        self.assertFalse(ok)
        self.assertEqual(page.clics, [])

    def test_format_vertical_choisi_si_present(self):
        cible = self.sel["reglage_format"][0]
        page = PageFactice({cible: True})
        self.assertTrue(self.adaptateur._regler_format(page, self.sel))


class TestRefusSiDureeNonReglable(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def test_option_de_prudence(self):
        self.assertFalse(reglages.lire_bool("exiger_reglage_duree"))
        reglages.definir("exiger_reglage_duree", "1")
        self.assertTrue(reglages.lire_bool("exiger_reglage_duree"))

    def test_duree_hors_liste_refusee_avant_envoi(self):
        projet_id = services.creer_projet("Durée interdite")
        services.appliquer_pack(
            projet_id, services.analyser_pack(tests.pack_variantes())
        )
        services.valider_projet(projet_id)
        pr = services.projet(projet_id)
        for p in db.q("SELECT * FROM personnages WHERE projet_id=?", (projet_id,)):
            chemin = fichiers.chemin_planche(
                pr["dossier"], p["code"], p["surnom"], p["etat"]
            )
            tests.fabriquer_image_test(chemin)
            db.exec1("UPDATE personnages SET ref_path=? WHERE id=?", (str(chemin), p["id"]))
        scene = db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=1", (projet_id,)
        )
        image = fichiers.chemin_image_scene(pr["dossier"], 1)
        tests.fabriquer_image_test(image)
        db.exec1("UPDATE scenes SET image_path=?, duree=7 WHERE id=?", (str(image), scene["id"]))
        with self.assertRaises(runner.GenerationRefusee) as ctx:
            runner.demander(projet_id, "video", scene["id"], fournisseur="grok", mode="test")
        self.assertIn("non prise en charge", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
