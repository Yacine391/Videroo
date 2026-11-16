"""Export de chaque scène isolée en MP4, avec encodage FFmpeg réel."""
import unittest
import urllib.parse
import zipfile
from pathlib import Path

import tests
from videroo import db, fichiers, media, montage, services


class BaseExportScenes(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("Export par scène")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_variantes())
        )
        self.pr = services.projet(self.projet_id)
        self.scenes = db.q(
            "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (self.projet_id,)
        )
        # deux scènes sur trois ont une vidéo
        for scene in self.scenes[:2]:
            chemin = fichiers.chemin_video_scene(self.pr["dossier"], scene["numero"])
            montage.fabriquer_clip_test(
                chemin, duree=3.0, texte=f"S{scene['numero']}"
            )
            services.importer_media(
                self.projet_id, "video", scene["id"], chemin.name, chemin.read_bytes()
            )


class TestExportUneScene(BaseExportScenes):
    def test_mp4_lisible_au_format_vertical(self):
        resultat = services.exporter_scene(self.projet_id, self.scenes[0]["id"])
        self.assertTrue(resultat["reussi"], resultat["message"])
        infos = media.analyser_media(resultat["chemin"])
        self.assertTrue(infos.lisible)
        self.assertEqual((infos.largeur, infos.hauteur), (1080, 1920))
        self.assertTrue(infos.a_audio)
        self.assertEqual(infos.codec_video, "h264")
        self.assertAlmostEqual(infos.duree, 3.0, delta=0.5)

    def test_nom_de_fichier_stable_et_range(self):
        resultat = services.exporter_scene(self.projet_id, self.scenes[0]["id"])
        chemin = Path(resultat["chemin"])
        self.assertEqual(chemin.parent.name, "scenes")
        self.assertEqual(chemin.parent.parent.name, "export")
        self.assertTrue(chemin.name.startswith("scene-01_"))
        # le chemin est mémorisé pour le téléchargement
        enregistre = db.q1(
            "SELECT export_path FROM scenes WHERE id=?", (self.scenes[0]["id"],)
        )["export_path"]
        self.assertEqual(enregistre, str(chemin))

    def test_les_coupes_et_la_vitesse_sont_appliquees(self):
        services.maj_scene(self.scenes[0]["id"], {"trim_debut": 0.5, "trim_fin": 0.5})
        services.maj_projet(self.projet_id, {"vitesse": 2.0})
        resultat = services.exporter_scene(self.projet_id, self.scenes[0]["id"])
        self.assertTrue(resultat["reussi"], resultat["message"])
        # 3 s - 1 s de coupes = 2 s, puis vitesse x2 = 1 s
        self.assertAlmostEqual(
            media.analyser_media(resultat["chemin"]).duree, 1.0, delta=0.4
        )

    def test_sans_musique_par_defaut(self):
        musique = Path(self.pr["dossier"]) / "musique" / "fond.wav"
        montage.fabriquer_clip_test(musique.with_suffix(".mp4"), duree=2)
        services.maj_projet(self.projet_id, {})
        db.exec1(
            "UPDATE projets SET musique_path=? WHERE id=?",
            (str(musique.with_suffix(".mp4")), self.projet_id),
        )
        reglages = services.reglages_export(self.projet_id)
        self.assertIsNotNone(reglages.musique_path, "le projet a bien une musique")
        resultat = services.exporter_scene(self.projet_id, self.scenes[0]["id"])
        self.assertTrue(resultat["reussi"], resultat["message"])

    def test_refus_si_la_scene_n_a_pas_de_video(self):
        with self.assertRaises(services.ErreurMetier) as ctx:
            services.exporter_scene(self.projet_id, self.scenes[2]["id"])
        self.assertIn("aucune vidéo à exporter", str(ctx.exception))

    def test_refus_si_la_scene_est_d_un_autre_projet(self):
        autre = services.creer_projet("Autre")
        services.appliquer_pack(autre, services.analyser_pack(tests.pack_variantes()))
        with self.assertRaises(services.ErreurMetier):
            services.exporter_scene(autre, self.scenes[0]["id"])

    def test_sous_titres_incrustes_depuis_les_dialogues(self):
        resultat = services.exporter_scene(
            self.projet_id, self.scenes[0]["id"], incruster_srt=True
        )
        self.assertTrue(resultat["reussi"], resultat["message"])
        fichier_srt = (
            Path(self.pr["dossier"]) / "export" / "scenes" / "scene-01.srt"
        )
        self.assertTrue(fichier_srt.exists())
        self.assertIn("-->", fichier_srt.read_text(encoding="utf-8"))


class TestExportDeToutesLesScenes(BaseExportScenes):
    def test_chaque_scene_avec_video_est_exportee(self):
        bilan = services.exporter_toutes_les_scenes(self.projet_id)
        self.assertEqual(bilan["reussis"], 2)
        self.assertEqual(bilan["echecs"], [])
        self.assertEqual(bilan["ignorees"], [3])
        for resultat in bilan["resultats"]:
            self.assertTrue(media.analyser_media(resultat["chemin"]).lisible)

    def test_etat_affichable(self):
        services.exporter_toutes_les_scenes(self.projet_id)
        liste = services.scenes_exportees(self.projet_id)
        self.assertEqual(len(liste), 3)
        self.assertTrue(liste[0]["exporte"])
        self.assertTrue(liste[1]["exporte"])
        self.assertFalse(liste[2]["exporte"])
        self.assertFalse(liste[2]["a_video"])
        self.assertEqual(liste[0]["infos"]["largeur"], 1080)

    def test_archive_zip(self):
        services.exporter_toutes_les_scenes(self.projet_id)
        chemin = services.archiver_scenes(self.projet_id)
        self.assertTrue(Path(chemin).exists())
        with zipfile.ZipFile(chemin) as archive:
            noms = archive.namelist()
        self.assertEqual(len(noms), 2)
        self.assertTrue(all(n.endswith(".mp4") for n in noms))
        self.assertTrue(any(n.startswith("scene-01") for n in noms))

    def test_archive_refusee_si_rien_n_est_exporte(self):
        with self.assertRaises(services.ErreurMetier) as ctx:
            services.archiver_scenes(self.projet_id)
        self.assertIn("Aucune scène exportée", str(ctx.exception))

    def test_l_episode_complet_reste_possible(self):
        """L'export par scène ne remplace pas l'assemblage."""
        services.exporter_toutes_les_scenes(self.projet_id)
        resultat = services.exporter(self.projet_id)
        self.assertTrue(resultat["reussi"], resultat["message"])
        self.assertAlmostEqual(
            media.analyser_media(resultat["chemin"]).duree, 6.0, delta=0.8
        )


if __name__ == "__main__":
    unittest.main()


class TestInterfaceExportScenes(BaseExportScenes):
    """Les boutons doivent réellement apparaître dans les pages."""

    def test_boutons_presents_dans_montage_et_scenes(self):
        from fastapi.testclient import TestClient

        from videroo.app import app

        with TestClient(app) as client:
            montage_html = client.get(f"/projets/{self.projet_id}/montage").text
            self.assertIn("Export scène par scène", montage_html)
            self.assertIn("Exporter toutes les scènes", montage_html)
            self.assertIn("Télécharger toutes les scènes", montage_html)

            scenes_html = client.get(f"/projets/{self.projet_id}/scenes").text
            # deux scènes ont une vidéo : deux boutons d'export
            self.assertEqual(scenes_html.count("Exporter cette scène en MP4"), 2)

    def test_telechargement_apres_export(self):
        from fastapi.testclient import TestClient

        from videroo.app import app

        with TestClient(app) as client:
            reponse = client.post(
                f"/projets/{self.projet_id}/scenes/{self.scenes[0]['id']}/exporter",
                data={"retour_vers": "montage"},
                follow_redirects=False,
            )
            self.assertEqual(reponse.status_code, 303)
            self.assertIn(
                "exportée", urllib.parse.unquote(reponse.headers["location"])
            )

            fichier = Path(
                db.q1(
                    "SELECT export_path FROM scenes WHERE id=?", (self.scenes[0]["id"],)
                )["export_path"]
            ).name
            telechargement = client.get(
                f"/media/{self.projet_id}/export/scenes/{fichier}"
            )
            self.assertEqual(telechargement.status_code, 200)
            self.assertGreater(len(telechargement.content), 1000)

            page = client.get(f"/projets/{self.projet_id}/montage").text
            self.assertIn(fichier, page)
            self.assertIn("Réexporter", page)

    def test_archive_telechargeable(self):
        from fastapi.testclient import TestClient

        from videroo.app import app

        services.exporter_toutes_les_scenes(self.projet_id)
        with TestClient(app) as client:
            reponse = client.post(
                f"/projets/{self.projet_id}/scenes-archive", follow_redirects=False
            )
            self.assertEqual(reponse.status_code, 303)
            self.assertIn(".zip", reponse.headers["location"])
            fichier = client.get(reponse.headers["location"])
            self.assertEqual(fichier.status_code, 200)
            self.assertTrue(fichier.content.startswith(b"PK"))
