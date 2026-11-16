"""Montage et export reels avec ffmpeg. Aucun service en ligne n'est appele."""
import unittest
from pathlib import Path

import tests
from videroo import db, fichiers, media, montage, services, srt


class TestPlanMontage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tests.reinitialiser()
        cls.dossier = tests.DOSSIER_TEST / "montage"
        cls.dossier.mkdir(parents=True, exist_ok=True)
        cls.avec_audio = montage.fabriquer_clip_test(
            cls.dossier / "a.mp4", duree=4, texte="A"
        )
        cls.sans_audio = montage.fabriquer_clip_test(
            cls.dossier / "b.mp4", duree=3, texte="B", avec_audio=False
        )

    def test_analyse_media(self):
        infos = media.analyser_media(self.avec_audio)
        self.assertTrue(infos.lisible)
        self.assertAlmostEqual(infos.duree, 4.0, delta=0.3)
        self.assertTrue(infos.a_audio)
        self.assertEqual((infos.largeur, infos.hauteur), (720, 1280))
        self.assertFalse(media.analyser_media(self.sans_audio).a_audio)

    def test_fichier_absent_ou_illisible(self):
        self.assertFalse(media.analyser_media("/tmp/videroo_inexistant.mp4").existe)
        faux = self.dossier / "faux.mp4"
        faux.write_bytes(b"pas une video")
        self.assertFalse(media.analyser_media(faux).lisible)

    def test_apercu_de_duree_avec_coupes_et_vitesse(self):
        clips = [
            montage.Clip(1, self.avec_audio, trim_debut=0.5, trim_fin=0.5),
            montage.Clip(2, self.sans_audio),
        ]
        plan = montage.preparer_plan(clips, montage.ReglagesExport(vitesse=1.0))
        self.assertAlmostEqual(plan.duree_brute, 6.0, delta=0.3)
        self.assertAlmostEqual(plan.duree_finale, 6.0, delta=0.3)

        plan_rapide = montage.preparer_plan(
            [montage.Clip(1, self.avec_audio), montage.Clip(2, self.sans_audio)],
            montage.ReglagesExport(vitesse=1.25),
        )
        self.assertAlmostEqual(plan_rapide.duree_finale, 7.0 / 1.25, delta=0.3)

    def test_avertissements_honnetes(self):
        plan = montage.preparer_plan(
            [montage.Clip(1, self.sans_audio)],
            montage.ReglagesExport(duree_cible_min=70.0),
        )
        messages = " | ".join(plan.avertissements)
        self.assertIn("aucune piste audio", messages)
        self.assertIn("ne crée aucun détail supplémentaire", messages)
        self.assertIn("en dessous de ta durée souhaitée", messages)
        self.assertTrue(plan.utilisable)

    def test_bloquants(self):
        plan = montage.preparer_plan(
            [montage.Clip(1, "/tmp/videroo_absent.mp4")], montage.ReglagesExport()
        )
        self.assertFalse(plan.utilisable)
        self.assertIn("absent", " ".join(plan.bloquants))

        trop_coupe = montage.preparer_plan(
            [montage.Clip(1, self.sans_audio, trim_debut=2.0, trim_fin=2.0)],
            montage.ReglagesExport(),
        )
        self.assertFalse(trop_coupe.utilisable)
        self.assertIn("laissent", " ".join(trop_coupe.bloquants))

    def test_export_reel_vertical_avec_audio(self):
        clips = [
            montage.Clip(1, self.avec_audio, trim_debut=0.5, trim_fin=0.5, titre="Un"),
            montage.Clip(2, self.sans_audio, titre="Deux"),
        ]
        cible = self.dossier / "export_simple.mp4"
        resultat = montage.assembler(clips, cible, montage.ReglagesExport())
        self.assertTrue(resultat.reussi, resultat.message + resultat.journal)
        infos = media.analyser_media(cible)
        self.assertEqual((infos.largeur, infos.hauteur), (1080, 1920))
        self.assertAlmostEqual(infos.duree, 6.0, delta=0.6)
        self.assertTrue(infos.a_audio, "le silence doit combler le clip sans audio")
        self.assertEqual(infos.codec_video, "h264")
        self.assertEqual(infos.codec_audio, "aac")

    def test_export_avec_vitesse(self):
        clips = [montage.Clip(1, self.avec_audio)]
        cible = self.dossier / "export_rapide.mp4"
        resultat = montage.assembler(
            clips, cible, montage.ReglagesExport(vitesse=2.0)
        )
        self.assertTrue(resultat.reussi, resultat.message)
        self.assertAlmostEqual(media.analyser_media(cible).duree, 2.0, delta=0.5)

    def test_export_avec_musique_et_sous_titres_incrustes(self):
        fichier_srt = self.dossier / "st.srt"
        srt.ecrire(
            [{"debut": 0.2, "fin": 2.0, "texte": "Premiere ligne"},
             {"debut": 2.0, "fin": 3.8, "texte": "Deuxieme ligne"}],
            fichier_srt,
        )
        cible = self.dossier / "export_complet.mp4"
        resultat = montage.assembler(
            [montage.Clip(1, self.avec_audio)],
            cible,
            montage.ReglagesExport(
                musique_path=self.avec_audio,
                musique_volume=0.2,
                srt_path=str(fichier_srt),
                incruster_srt=True,
            ),
        )
        self.assertTrue(resultat.reussi, resultat.message + resultat.journal)
        infos = media.analyser_media(cible)
        self.assertTrue(infos.a_audio)
        self.assertAlmostEqual(infos.duree, 4.0, delta=0.6)

    def test_extraction_images_cles(self):
        images = media.extraire_images_cles(
            self.avec_audio, self.dossier / "cles", nombre=3
        )
        self.assertEqual(len(images), 3)
        for i in images:
            self.assertTrue(media.analyser_media(i).lisible)


class TestSousTitres(unittest.TestCase):
    def test_aller_retour_srt(self):
        lignes = [
            {"debut": 0.0, "fin": 2.5, "texte": "Bonjour"},
            {"debut": 2.5, "fin": 5.25, "texte": "Au revoir"},
        ]
        cible = tests.DOSSIER_TEST / "aller_retour.srt"
        srt.ecrire(lignes, cible)
        relues = srt.lire(cible.read_text(encoding="utf-8"))
        self.assertEqual(len(relues), 2)
        self.assertAlmostEqual(relues[1]["fin"], 5.25, places=2)
        self.assertEqual(relues[0]["texte"], "Bonjour")

    def test_timings_depuis_les_dialogues(self):
        scenes = [
            {"duree": 6, "duree_utile": 6, "dialogues": [{"texte": "Un"}, {"texte": "Deux"}]},
            {"duree": 10, "duree_utile": 10, "dialogues": [{"texte": "Trois"}]},
        ]
        lignes = srt.depuis_scenes(scenes)
        self.assertEqual(len(lignes), 3)
        self.assertAlmostEqual(lignes[0]["fin"], 3.0, places=2)
        self.assertAlmostEqual(lignes[2]["debut"], 6.0, places=2)
        self.assertAlmostEqual(lignes[2]["fin"], 16.0, places=2)


class TestExportDuProjet(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("Export complet")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_exemple())
        )
        self.pr = services.projet(self.projet_id)
        for s in db.q(
            "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero LIMIT 3", (self.projet_id,)
        ):
            chemin = fichiers.chemin_video_scene(self.pr["dossier"], s["numero"])
            montage.fabriquer_clip_test(chemin, duree=float(s["duree"]), texte=f"S{s['numero']}")
            services.importer_media(
                self.projet_id, "video", s["id"], chemin.name, chemin.read_bytes()
            )

    def test_plan_dans_l_ordre_du_scenario(self):
        plan = services.plan_montage(self.projet_id)
        self.assertEqual([c["numero"] for c in plan["clips"]], [1, 2, 3])
        self.assertEqual(plan["scenes_total"], 10)
        self.assertEqual(plan["scenes_sans_video"], 7)
        self.assertAlmostEqual(plan["duree_finale"], 18.0, delta=1.0)

    def test_coupes_prises_en_compte(self):
        s1 = db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=1", (self.projet_id,)
        )
        services.maj_scene(s1["id"], {"trim_debut": 1.0, "trim_fin": 0.5})
        plan = services.plan_montage(self.projet_id)
        self.assertAlmostEqual(plan["duree_finale"], 16.5, delta=1.0)

    def test_export_mp4_lisible(self):
        resultat = services.exporter(self.projet_id)
        self.assertTrue(resultat["reussi"], resultat["message"])
        infos = media.analyser_media(resultat["chemin"])
        self.assertEqual((infos.largeur, infos.hauteur), (1080, 1920))
        self.assertTrue(infos.a_audio)
        self.assertAlmostEqual(infos.duree, 18.0, delta=1.2)
        self.assertEqual(
            services.projet(self.projet_id)["export_path"], resultat["chemin"]
        )

    def test_export_avec_sous_titres_et_srt(self):
        services.proposer_sous_titres(self.projet_id)
        self.assertGreater(len(services.sous_titres(self.projet_id)), 0)
        chemin_srt = services.exporter_srt(self.projet_id)
        self.assertTrue(Path(chemin_srt).exists())
        resultat = services.exporter(self.projet_id, incruster_srt=True)
        self.assertTrue(resultat["reussi"], resultat["message"])

    def test_controle_final_liste_les_manques(self):
        controle = services.controle_final(self.projet_id)
        manquants = " | ".join(controle["manquants"])
        self.assertIn("Miniature absente", manquants)
        self.assertIn("Scène 4 : vidéo absente", manquants)
        self.assertIn("Planche de référence absente", manquants)
        self.assertEqual(len(controle["scenes"]), 10)

    def test_export_refuse_sans_aucune_video(self):
        autre = services.creer_projet("Vide")
        services.appliquer_pack(autre, services.analyser_pack(tests.pack_exemple()))
        with self.assertRaises(services.ErreurMetier):
            services.exporter(autre)


if __name__ == "__main__":
    unittest.main()
