"""L'atelier : la vue simple de haut en bas, et la récupération des vidéos.

Aucun quota IA n'est consommé ici : les medias sont fabriqués par FFmpeg.
"""
import json
import unittest
import zipfile
from unittest import mock
from pathlib import Path

import tests
from videroo import db, fichiers, montage, runner, services
from videroo.adapters import ecran


class BaseAtelier(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("Atelier")
        services.appliquer_pack(
            self.projet_id, services.analyser_pack(tests.pack_variantes())
        )
        self.pr = services.projet(self.projet_id)
        self.scenes = db.q(
            "SELECT * FROM scenes WHERE projet_id=? ORDER BY numero", (self.projet_id,)
        )
        self.personnages = db.q(
            "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre, id",
            (self.projet_id,),
        )

    def donner_une_video(self, scene):
        chemin = fichiers.chemin_video_scene(self.pr["dossier"], scene["numero"])
        montage.fabriquer_clip_test(chemin, duree=2.0, texte=f"S{scene['numero']}")
        services.importer_media(
            self.projet_id, "video", scene["id"], chemin.name, chemin.read_bytes()
        )


class TestFilDeProduction(BaseAtelier):
    def test_le_fil_contient_les_planches_puis_les_scenes(self):
        fil = services.fil_production(self.projet_id)
        self.assertEqual(len(fil["planches"]), len(self.personnages))
        self.assertEqual(len(fil["scenes"]), len(self.scenes))
        for scene in fil["scenes"]:
            self.assertEqual(scene["image"]["fournisseur"], "chatgpt")
            self.assertEqual(scene["video"]["fournisseur"], "grok")

    def test_une_scene_non_validee_est_bloquee_avec_son_motif(self):
        fil = services.fil_production(self.projet_id)
        image = fil["scenes"][0]["image"]
        self.assertEqual(image["etat"], "bloque")
        self.assertIn("non validée", image["message"])

    def test_un_media_obtenu_passe_la_carte_en_termine(self):
        self.donner_une_video(self.scenes[0])
        fil = services.fil_production(self.projet_id)
        video = fil["scenes"][0]["video"]
        self.assertEqual(video["etat"], "termine")
        self.assertTrue(video["media"].startswith(f"/media/{self.projet_id}/"))
        self.assertTrue(video["telechargement"])
        self.assertEqual(fil["videos_pretes"], 1)

    def test_une_tache_a_verifier_reste_a_verifier_avec_son_message(self):
        perso = self.personnages[0]
        tache_id = runner.demander(
            self.projet_id, "planche", perso["id"],
            fournisseur="chatgpt", mode="simulation", origine="auto",
        )
        db.exec1(
            "UPDATE taches SET statut='a_verifier', message=? WHERE id=?",
            ("Rien n'est revenu.", tache_id),
        )
        fil = services.fil_production(self.projet_id)
        planche = next(p for p in fil["planches"] if p["id"] == perso["id"])
        self.assertEqual(planche["etat"], "a_verifier")
        self.assertEqual(planche["message"], "Rien n'est revenu.")
        self.assertEqual(planche["tache_id"], tache_id)

    def test_la_tache_en_file_est_signalee_sans_etre_relancee(self):
        perso = self.personnages[0]
        runner.demander(
            self.projet_id, "planche", perso["id"],
            fournisseur="chatgpt", mode="simulation", origine="auto",
        )
        fil = services.fil_production(self.projet_id)
        planche = next(p for p in fil["planches"] if p["id"] == perso["id"])
        self.assertIn(planche["etat"], ("en_attente", "en_cours"))


class TestMiniature(BaseAtelier):
    """La miniature TikTok : dernière étape de la chaîne, prompt de Yacine."""

    def test_le_modele_garde_le_texte_mot_pour_mot(self):
        from videroo import ressources

        modele = ressources.modele_miniature()
        for repere in (
            "miniature TikTok verticale au format 9:16",
            "les 10 % du haut et les 25 % du bas",
            "entre 12 % et 48 % de la hauteur",
            "Aucun autre texte que le titre et le badge",
        ):
            self.assertIn(repere, modele)

    def test_le_titre_et_la_partie_sont_remplaces(self):
        from videroo import runner

        services.maj_projet(self.projet_id, {"titre": "LE PHARE", "grok_episode": 3})
        prompt = runner.prompt_miniature(self.projet_id)
        self.assertIn("« LE PHARE »", prompt)
        self.assertIn("PARTIE 3", prompt)
        self.assertNotIn("{titre}", prompt)
        self.assertNotIn("{episode}", prompt)

    def test_un_titre_deja_entre_guillemets_ne_les_double_pas(self):
        """Le pack V6 livre le titre guillemets compris.

        Le modèle ajoutait les siens : « « IL A TOUT DONNÉ… » ». Comme il
        demande d'écrire le titre « exactement ainsi », ChatGPT les aurait
        dessinés dans la miniature.
        """
        from videroo import runner

        services.maj_projet(self.projet_id, {"titre": "« LE PHARE »"})
        prompt = runner.prompt_miniature(self.projet_id)
        self.assertIn("« LE PHARE »", prompt)
        self.assertNotIn("« « ", prompt)
        self.assertNotIn(" » »", prompt)

    def test_un_prompt_ecrit_a_la_main_a_la_priorite(self):
        from videroo import runner

        services.maj_projet(self.projet_id, {"instructions_miniature": "Ma consigne à moi."})
        self.assertEqual(runner.prompt_miniature(self.projet_id), "Ma consigne à moi.")

    def test_la_miniature_ferme_la_chaine(self):
        from videroo import runner

        # tout est obtenu sauf la miniature
        for perso in self.personnages:
            chemin = fichiers.chemin_planche(
                self.pr["dossier"], perso["code"], perso["surnom"], perso["etat"] or "UNIQUE"
            )
            tests.fabriquer_image_test(chemin, texte=perso["code"])
            services.importer_media(
                self.projet_id, "planche", perso["id"], chemin.name, chemin.read_bytes()
            )
        for scene in self.scenes:
            img = fichiers.chemin_image_scene(self.pr["dossier"], scene["numero"])
            tests.fabriquer_image_test(img, texte=f"S{scene['numero']}")
            services.importer_media(
                self.projet_id, "image", scene["id"], img.name, img.read_bytes()
            )
            self.donner_une_video(scene)
        self.assertEqual(
            runner.prochaine_cible(self.projet_id), ("miniature", 0, "chatgpt")
        )

    def donner_une_miniature(self):
        source = Path(self.pr["dossier"]) / "miniature" / "source-essai.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        tests.fabriquer_image_test(source, texte="MINI")
        services.importer_media(
            self.projet_id, "miniature", 0, source.name, source.read_bytes()
        )

    def test_la_miniature_obtenue_est_telechargeable(self):
        self.donner_une_miniature()
        services.maj_projet(self.projet_id, {"titre": "LE PHARE", "grok_episode": 3})
        chemin, nom = services.miniature_a_telecharger(self.projet_id)
        self.assertTrue(Path(chemin).exists())
        self.assertIn("miniature", nom)
        self.assertIn("partie-03", nom)
        self.assertTrue(nom.endswith(".png"))

    def test_sans_miniature_le_telechargement_est_refuse_avec_un_motif(self):
        with self.assertRaises(services.ErreurMetier) as capture:
            services.miniature_a_telecharger(self.projet_id)
        self.assertIn("aucune miniature", str(capture.exception).lower())

    def test_le_fil_porte_le_lien_de_telechargement_seulement_quand_elle_existe(self):
        self.assertFalse(services.fil_production(self.projet_id)["miniature"]["telechargement"])
        self.donner_une_miniature()
        fil = services.fil_production(self.projet_id)
        self.assertEqual(fil["miniature"]["etat"], "termine")
        self.assertEqual(
            fil["miniature"]["telechargement"],
            f"/projets/{self.projet_id}/telecharger/miniature",
        )

    def test_les_planches_sont_jointes_a_la_miniature(self):
        from videroo import runner

        perso = self.personnages[0]
        chemin = fichiers.chemin_planche(
            self.pr["dossier"], perso["code"], perso["surnom"], perso["etat"] or "UNIQUE"
        )
        tests.fabriquer_image_test(chemin, texte=perso["code"])
        services.importer_media(
            self.projet_id, "planche", perso["id"], chemin.name, chemin.read_bytes()
        )
        tache_id = runner.demander(
            self.projet_id, "miniature", 0,
            fournisseur="chatgpt", mode="simulation", origine="auto",
        )
        tache = db.q1("SELECT * FROM taches WHERE id=?", (tache_id,))
        contexte = runner.construire_contexte(
            tache, Path(self.pr["dossier"]) / "miniature" / "essai.png"
        )
        self.assertTrue(contexte.fichiers_joints, "sans planche jointe, « identiques » ne veut rien dire")
        self.assertIn("PARTIE", contexte.prompt)

    def test_la_miniature_apparait_dans_le_fil(self):
        fil = services.fil_production(self.projet_id)
        self.assertEqual(fil["miniature"]["genre"], "miniature")
        self.assertEqual(fil["miniature"]["fournisseur"], "chatgpt")


class TestTelechargementDesVideos(BaseAtelier):
    def test_nom_de_fichier_lisible_pour_capcut(self):
        self.donner_une_video(self.scenes[0])
        chemin, nom = services.video_a_telecharger(
            self.projet_id, self.scenes[0]["id"]
        )
        self.assertTrue(Path(chemin).exists())
        self.assertIn("scene-01", nom)
        self.assertTrue(nom.endswith(".mp4"))

    def test_sans_video_le_telechargement_est_refuse_avec_un_motif(self):
        with self.assertRaises(services.ErreurMetier) as capture:
            services.video_a_telecharger(self.projet_id, self.scenes[0]["id"])
        self.assertIn("aucune vidéo", str(capture.exception).lower())

    def test_archive_de_toutes_les_videos_sans_reencodage(self):
        for scene in self.scenes[:2]:
            self.donner_une_video(scene)
        avant = [
            Path(s["video_path"]).stat().st_size
            for s in db.q(
                "SELECT video_path FROM scenes WHERE projet_id=? AND video_path<>''"
                " ORDER BY numero",
                (self.projet_id,),
            )
        ]
        chemin, nom = services.archiver_videos(self.projet_id)
        self.assertTrue(nom.endswith("-videos.zip"))
        with zipfile.ZipFile(chemin) as archive:
            noms = archive.namelist()
            tailles = [archive.getinfo(n).file_size for n in noms]
        self.assertEqual(len(noms), 2)
        self.assertEqual(sorted(tailles), sorted(avant))  # octets identiques

    def test_archive_refusee_quand_il_n_y_a_rien(self):
        with self.assertRaises(services.ErreurMetier):
            services.archiver_videos(self.projet_id)


class TestPageAtelier(BaseAtelier):
    def client(self):
        from fastapi.testclient import TestClient

        from videroo.app import app

        return TestClient(app)

    def test_les_trois_etapes_sont_la(self):
        with self.client() as client:
            page = client.get(f"/projets/{self.projet_id}/atelier").text
        self.assertIn("Les planches de référence", page)
        self.assertIn("Les scènes", page)
        self.assertIn("Récupérer les vidéos pour CapCut", page)
        self.assertIn("Télécharger toutes les vidéos", page)

    def test_le_bouton_tout_est_valide_est_la_et_desactive_au_depart(self):
        """Le bouton existe dans la page, mais il n'est pas cliquable tant que
        tout n'est pas obtenu : c'est le script qui l'ouvre, pas le hasard."""
        with self.client() as client:
            page = client.get(f"/projets/{self.projet_id}/atelier").text
        self.assertIn("Tout est validé", page)
        self.assertIn('id="btn-tout-valide"', page)
        self.assertIn('<button type="submit" id="btn-tout-valide" disabled>', page)
        self.assertIn(f'action="/projets/{self.projet_id}/statut"', page)
        self.assertIn('name="statut" value="termine"', page)
        self.assertIn('name="retour_vers" value="projets"', page)

    def test_un_bouton_desactive_se_voit_dans_la_feuille_de_style(self):
        with self.client() as client:
            css = client.get("/static/style.css").text
        self.assertIn("button:disabled", css)
        self.assertIn("cursor:not-allowed", css)

    def test_tout_est_valide_termine_le_projet_et_ramene_aux_projets(self):
        with self.client() as client:
            reponse = client.post(
                f"/projets/{self.projet_id}/statut",
                data={"statut": "termine", "retour_vers": "projets"},
                follow_redirects=False,
            )
        self.assertEqual(reponse.status_code, 303)
        self.assertTrue(reponse.headers["location"].startswith("/?"))
        self.assertEqual(
            db.q1("SELECT statut FROM projets WHERE id=?", (self.projet_id,))["statut"],
            "termine",
        )

    def test_une_page_de_retour_inconnue_ne_sort_pas_de_l_application(self):
        with self.client() as client:
            reponse = client.post(
                f"/projets/{self.projet_id}/statut",
                data={"statut": "termine", "retour_vers": "https://exemple.test/vol"},
                follow_redirects=False,
            )
        self.assertEqual(reponse.status_code, 303)
        self.assertTrue(
            reponse.headers["location"].startswith(f"/projets/{self.projet_id}/generations")
        )

    def test_telechargement_de_la_miniature_par_la_page(self):
        source = Path(self.pr["dossier"]) / "miniature" / "source-essai.png"
        source.parent.mkdir(parents=True, exist_ok=True)
        tests.fabriquer_image_test(source, texte="MINI")
        services.importer_media(
            self.projet_id, "miniature", 0, source.name, source.read_bytes()
        )
        with self.client() as client:
            reponse = client.get(f"/projets/{self.projet_id}/telecharger/miniature")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.headers["content-type"], "image/png")
        self.assertIn("miniature", reponse.headers["content-disposition"])
        self.assertGreater(len(reponse.content), 0)

    def test_sans_miniature_la_page_renvoie_un_motif_sans_planter(self):
        with self.client() as client:
            reponse = client.get(
                f"/projets/{self.projet_id}/telecharger/miniature",
                follow_redirects=False,
            )
        self.assertEqual(reponse.status_code, 303)
        self.assertIn("atelier", reponse.headers["location"])
        self.assertIn("err=", reponse.headers["location"])

    def test_la_feuille_de_style_porte_son_empreinte(self):
        """Sans ça, Chrome sert l'ancienne feuille et la page s'affiche de travers."""
        with self.client() as client:
            page = client.get(f"/projets/{self.projet_id}/atelier").text
        self.assertRegex(page, r'/static/style\.css\?v=\d+')

    def test_l_empreinte_change_avec_le_fichier(self):
        import os
        import time

        from videroo.app import RACINE, empreinte_style

        feuille = RACINE / "static" / "style.css"
        avant = empreinte_style()
        plus_tard = feuille.stat().st_mtime + 5
        os.utime(feuille, (plus_tard, plus_tard))
        try:
            self.assertNotEqual(empreinte_style(), avant)
        finally:
            os.utime(feuille, (plus_tard - 5, plus_tard - 5))

    def test_le_suivi_vertical_a_ses_quatre_etages(self):
        with self.client() as client:
            page = client.get(f"/projets/{self.projet_id}/atelier").text
        tuyau = page.split('<div class="tuyau"', 1)[1].split("</div>\n\n<p", 1)[0]
        for titre, numero in (("Prompts", "01"), ("ChatGPT", "02"),
                              ("Grok", "03"), ("Projet", "04")):
            self.assertIn(f"<h3>{titre}</h3>", tuyau)
            self.assertIn(f'<span class="numero">{numero}</span>', tuyau)
        # trois flèches entre quatre étages, et elles descendent
        self.assertEqual(tuyau.count('class="fleche"'), 3)
        self.assertIn("↓", tuyau)

    def test_l_onglet_montage_a_disparu_au_profit_de_l_atelier(self):
        with self.client() as client:
            page = client.get(f"/projets/{self.projet_id}/atelier").text
        onglets = page.split('<div class="onglets">')[1].split("</div>")[0]
        self.assertIn("Atelier", onglets)
        self.assertNotIn("Montage", onglets)

    def test_le_fil_est_lisible_en_json(self):
        with self.client() as client:
            fil = client.get(f"/local/projets/{self.projet_id}/fil").json()
        self.assertEqual(fil["projet"]["id"], self.projet_id)
        self.assertEqual(len(fil["scenes"]), len(self.scenes))

    def test_action_inconnue_refusee_sans_rien_lancer(self):
        with self.client() as client:
            bilan = client.post(
                f"/local/projets/{self.projet_id}/action", json={"action": "sauter"}
            ).json()
        self.assertFalse(bilan["ok"])
        self.assertIn("inconnue", bilan["message"])
        self.assertEqual(db.q("SELECT id FROM taches WHERE projet_id=?", (self.projet_id,)), [])

    def test_une_seule_generation_automatique_par_element(self):
        perso = self.personnages[0]
        charge = {
            "action": "generer", "genre": "planche", "cible_id": perso["id"],
            "fournisseur": "chatgpt", "mode": "simulation",
        }
        with self.client() as client:
            premier = client.post(f"/local/projets/{self.projet_id}/action", json=charge).json()
            second = client.post(f"/local/projets/{self.projet_id}/action", json=charge).json()
        self.assertTrue(premier["ok"], premier["message"])
        self.assertFalse(second["ok"])
        self.assertEqual(
            len(db.q(
                "SELECT id FROM taches WHERE projet_id=? AND cible_type='planche'"
                " AND cible_id=?",
                (self.projet_id, perso["id"]),
            )),
            1,
        )

    def test_telechargement_d_une_video_par_la_page(self):
        self.donner_une_video(self.scenes[0])
        with self.client() as client:
            reponse = client.get(
                f"/projets/{self.projet_id}/telecharger/scene/{self.scenes[0]['id']}"
            )
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("scene-01", reponse.headers["content-disposition"])
        self.assertGreater(len(reponse.content), 0)

    def test_archive_telechargeable_par_la_page(self):
        self.donner_une_video(self.scenes[0])
        with self.client() as client:
            reponse = client.get(f"/projets/{self.projet_id}/telecharger/videos")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.headers["content-type"], "application/zip")


class TestImagesChatGPT(unittest.TestCase):
    """Comment l'image générée est repérée puis récupérée, sur le vrai site.

    Relevé le 6 novembre 2026 : ChatGPT rend l'image dans une galerie dédiée, avec
    l'alt « Image générée N », et son src est un `blob:` propre à l'onglet. Trois
    planches ont été perdues parce que les anciens sélecteurs ne voyaient rien.
    """

    def setUp(self):
        tests.reinitialiser()

    class FauxElement:
        def __init__(self, src):
            self._src = src

        def get_attribute(self, nom):
            return self._src if nom == "src" else None

    class FauxLocator:
        def __init__(self, elements):
            self._elements = elements

        def all(self):
            return self._elements

    def _page(self, par_selecteur):
        essai = self

        class FaussePage:
            url = "https://chatgpt.com/c/abc"
            evaluations = []

            def locator(self, selecteur):
                return essai.FauxLocator(
                    [essai.FauxElement(s) for s in par_selecteur.get(selecteur, [])]
                )

            def wait_for_timeout(self, duree):
                pass

            def reload(self, **_):
                """Rechargée ou non, cette page montre toujours la même chose."""

            def evaluate(self, script, argument=None):
                self.evaluations.append(argument)
                import base64
                return base64.b64encode(b"PNG-factice").decode()

        return FaussePage()

    def test_les_selecteurs_visent_la_galerie_d_images_generees(self):
        from videroo.adapters import selecteurs

        liste = selecteurs.charger()["chatgpt"]["images_reponse"]
        self.assertIn("[data-testid='generated-image-gallery'] img", liste)
        self.assertTrue(any("Image générée" in s for s in liste))
        self.assertTrue(any("blob:" in s for s in liste))

    def test_l_avatar_du_compte_n_est_pas_pris_pour_une_planche(self):
        from videroo.adapters.chatgpt import AdaptateurChatGPT

        sel = {"images_reponse": ["img"]}
        page = self._page({"img": [
            "https://cdn.auth0.com/avatars/jf.png",
            "blob:https://chatgpt.com/vraie-image",
        ]})
        self.assertEqual(
            AdaptateurChatGPT._sources_images(page, sel),
            ["blob:https://chatgpt.com/vraie-image"],
        )

    @staticmethod
    def _par_contenu():
        """Lit une image : deux adresses d'une même image rendent les mêmes octets.

        C'est tout le problème. ChatGPT refabrique l'adresse `blob:` à chaque
        rendu, donc « ancienne-A-ancienne-adresse » et
        « ancienne-A-nouvelle-adresse » sont la MÊME image. On nomme le contenu
        par ce qui reste une fois le suffixe d'adresse retiré.
        """
        from videroo.adapters import navigateur

        def _octets(_page, adresse):
            nom = adresse.rsplit("/", 1)[-1]
            for suffixe in ("-nouvelle-adresse", "-ancienne-adresse"):
                nom = nom.replace(suffixe, "")
            return nom.encode()

        return mock.patch.object(navigateur, "octets_de_image", _octets)

    def test_c_est_la_derniere_image_qui_est_prise(self):
        """ChatGPT recrée une adresse blob: à chaque chargement de page.

        Comparer les adresses faisait récupérer une planche déjà générée : R3
        CASSISANDRO avait reçu l'image de GOYAVELLA. Et compter ne marche pas
        davantage, la galerie plafonnant à quatre images. Seul le contenu tranche.
        """
        from videroo.adapters.chatgpt import AdaptateurChatGPT

        sel = {"images_reponse": ["img"], "signes_quota": []}
        # deux anciennes images, dont les adresses ont changé, puis la nouvelle
        page = self._page({"img": [
            "blob:https://chatgpt.com/ancienne-A-nouvelle-adresse",
            "blob:https://chatgpt.com/ancienne-B-nouvelle-adresse",
            "blob:https://chatgpt.com/la-vraie-nouvelle",
        ]})
        # Le repère garde les adresses d'AVANT : ce sont celles-là qui ont changé.
        avant = [
            "blob:https://chatgpt.com/ancienne-A-ancienne-adresse",
            "blob:https://chatgpt.com/ancienne-B-ancienne-adresse",
        ]
        with self._par_contenu():
            trouvee, souci = AdaptateurChatGPT()._attendre_image(page, sel, avant, delai=6)
        self.assertEqual(trouvee, "blob:https://chatgpt.com/la-vraie-nouvelle")
        self.assertEqual(souci, "")

    def test_rien_de_neuf_ne_rend_rien(self):
        from videroo.adapters.chatgpt import AdaptateurChatGPT

        sel = {"images_reponse": ["img"], "signes_quota": []}
        page = self._page({"img": ["blob:https://chatgpt.com/a", "blob:https://chatgpt.com/b"]})
        avant = ["blob:https://chatgpt.com/a", "blob:https://chatgpt.com/b"]
        with self._par_contenu():
            self.assertEqual(
                AdaptateurChatGPT()._attendre_image(page, sel, avant, delai=1), (None, "")
            )

    def test_une_image_blob_est_lue_dans_la_page(self):
        from videroo.adapters import navigateur

        page = self._page({})
        octets = navigateur.octets_de_image(page, "blob:https://chatgpt.com/xyz")
        self.assertEqual(octets, b"PNG-factice")
        self.assertEqual(page.evaluations, ["blob:https://chatgpt.com/xyz"])

    def test_une_image_ordinaire_passe_par_une_requete(self):
        from videroo.adapters import navigateur

        class FausseReponse:
            ok = True
            status = 200

            def body(self):
                return b"PNG-http"

        class FaussesRequetes:
            def __init__(self):
                self.demandees = []

            def get(self, src):
                self.demandees.append(src)
                return FausseReponse()

        page = self._page({})
        page.request = FaussesRequetes()
        octets = navigateur.octets_de_image(page, "https://exemple.test/i.png")
        self.assertEqual(octets, b"PNG-http")
        self.assertEqual(page.request.demandees, ["https://exemple.test/i.png"])
        self.assertEqual(page.evaluations, [], "pas de JS pour une adresse ordinaire")


class TestPiecesJointes(unittest.TestCase):
    """Le champ de fichier de ChatGPT est caché : un locator y expire.

    Mesuré le 6 novembre 2026 : deux `input[type=file]` de classe « hidden »,
    l'un acceptant les images. Le handle DOM ne contrôle pas la visibilité.
    """

    def setUp(self):
        tests.reinitialiser()

    def _page(self, poignees=None, locator_ok=False, multiple=False):
        essai = self

        class Poignee:
            def __init__(self, nom):
                self.nom = nom

            def get_attribute(self, attribut):
                return "" if (attribut == "multiple" and multiple) else None

            def set_input_files(self, fichiers):
                if len(fichiers) > 1 and not multiple:
                    raise RuntimeError(
                        "ElementHandle.set_input_files: Error: Non-multiple file "
                        "input can only accept single file"
                    )
                essai.joints.append((self.nom, tuple(fichiers)))

        class LocatorPremier:
            def set_input_files(self, fichiers, timeout=None):
                if not locator_ok:
                    raise RuntimeError("Element is not visible")
                essai.joints.append(("locator", tuple(fichiers)))

        class Locator:
            first = LocatorPremier()

        class FaussePage:
            def query_selector(self, selecteur):
                nom = (poignees or {}).get(selecteur)
                return Poignee(selecteur) if nom else None

            def locator(self, selecteur):
                return Locator()

        self.joints = []
        return FaussePage()

    def test_le_champ_cache_est_servi_par_le_handle(self):
        from videroo.adapters import navigateur

        page = self._page(poignees={"input[type='file'][accept*='image']": True})
        ok, detail = navigateur.joindre_fichiers(
            page,
            ["input[type='file'][accept*='image']", "input[type='file']"],
            [Path("/tmp/x.png")],
        )
        self.assertTrue(ok)
        self.assertIn("champ caché", detail)
        self.assertEqual(self.joints, [("input[type='file'][accept*='image']", ("/tmp/x.png",))])

    def test_repli_sur_le_locator_si_aucun_handle(self):
        from videroo.adapters import navigateur

        page = self._page(poignees={}, locator_ok=True)
        ok, detail = navigateur.joindre_fichiers(
            page, ["input[type='file']"], [Path("/tmp/x.png")]
        )
        self.assertTrue(ok)
        self.assertEqual(self.joints, [("locator", ("/tmp/x.png",))])

    def test_un_echec_nomme_ce_qui_a_ete_essaye(self):
        from videroo.adapters import navigateur

        page = self._page(poignees={}, locator_ok=False)
        ok, detail = navigateur.joindre_fichiers(
            page, ["input[type='file']"], [Path("/tmp/x.png")]
        )
        self.assertFalse(ok)
        self.assertIn("input[type='file']", detail)
        self.assertIn("not visible", detail)

    def test_plusieurs_fichiers_sur_un_champ_simple_partent_un_par_un(self):
        """Le cas de la miniature, qui joint les quatre planches d'un coup.

        Le 7 novembre 2026 : « Non-multiple file input can only accept single
        file ». Une scène ne joint qu'une planche, donc la panne n'est apparue
        qu'à la dernière étape de l'épisode.
        """
        from videroo.adapters import navigateur

        page = self._page(poignees={"input[type='file']": True}, multiple=False)
        planches = [Path(f"/tmp/R{n}.png") for n in (1, 2, 3, 4)]
        ok, detail = navigateur.joindre_fichiers(page, ["input[type='file']"], planches)
        self.assertTrue(ok)
        self.assertEqual(
            self.joints,
            [("input[type='file']", (f"/tmp/R{n}.png",)) for n in (1, 2, 3, 4)],
        )
        self.assertIn("un par un", detail)

    def test_un_champ_multiple_recoit_tout_d_un_coup(self):
        from videroo.adapters import navigateur

        page = self._page(poignees={"input[type='file']": True}, multiple=True)
        planches = [Path("/tmp/R1.png"), Path("/tmp/R2.png")]
        ok, detail = navigateur.joindre_fichiers(page, ["input[type='file']"], planches)
        self.assertTrue(ok)
        self.assertEqual(
            self.joints, [("input[type='file']", ("/tmp/R1.png", "/tmp/R2.png"))]
        )
        self.assertNotIn("un par un", detail)

    def test_sans_fichier_rien_a_faire(self):
        from videroo.adapters import navigateur

        self.assertEqual(navigateur.joindre_fichiers(self._page(), ["x"], []), (True, ""))

    def test_le_champ_image_est_essaye_en_premier(self):
        from videroo.adapters import selecteurs

        liste = selecteurs.charger()["chatgpt"]["champ_fichier"]
        self.assertEqual(liste[0], "input[type='file'][accept*='image']")


class TestSobrieteNavigateur(unittest.TestCase):
    """Un seul Chrome ouvert à la fois : la machine n'a que 16 Go partagés."""

    def setUp(self):
        tests.reinitialiser()

    def _session(self):
        from unittest import mock

        from videroo.adapters import navigateur

        session = navigateur.SessionNavigateur()
        ouverts = []

        class FauxContexte:
            def __init__(self, nom):
                self.nom = nom

            def close(self):
                ouverts.remove(self.nom)

        class FauxChromium:
            def launch_persistent_context(self, **kw):
                nom = Path(kw["user_data_dir"]).name
                ouverts.append(nom)
                return FauxContexte(nom)

        class FauxPw:
            chromium = FauxChromium()

            def stop(self):
                pass

        session._pw = FauxPw()
        patch = mock.patch.object(session, "_demarrer_playwright", side_effect=lambda: setattr(session, "_pw", FauxPw()) or session._pw)
        return session, ouverts, patch

    def test_ouvrir_un_second_profil_ferme_le_premier(self):
        session, ouverts, patch = self._session()
        with patch:
            session._ouvrir(affichage="fenetre", profil="premier")
            self.assertEqual(len(session._contextes), 1)
            session._ouvrir(affichage="fenetre", profil="second")
        self.assertEqual(list(session._contextes), ["second"])
        self.assertEqual(ouverts, ["second"], "le premier Chrome doit être fermé")

    def test_rouvrir_le_meme_profil_ne_relance_rien(self):
        session, ouverts, patch = self._session()
        with patch:
            session._ouvrir(affichage="fenetre", profil="unique")
            session._ouvrir(affichage="fenetre", profil="unique")
        self.assertEqual(ouverts, ["unique"])

    def test_les_arguments_evitent_le_superflu(self):
        session, _, patch = self._session()
        vus = []

        class FauxChromium:
            def launch_persistent_context(self, **kw):
                vus.append(kw["args"])
                raise RuntimeError("stop")

        class FauxPw:
            chromium = FauxChromium()

            def stop(self):
                pass

        session._pw = FauxPw()
        from unittest import mock

        from videroo.adapters import navigateur

        with mock.patch.object(session, "_demarrer_playwright", return_value=FauxPw()):
            with self.assertRaises(navigateur.NavigateurIndisponible):
                session._ouvrir(affichage="fenetre", profil="essai")
        for attendu in ("--disable-gpu", "--disable-extensions", "--mute-audio"):
            self.assertIn(attendu, vus[0])

    def test_l_inventaire_dit_sur_quel_ecran_tourne_chrome(self):
        from videroo.adapters import navigateur

        for chrome in navigateur.chromes_videroo():
            self.assertEqual(
                chrome["sur_ton_ecran"], chrome["affichage"] in ("", ":0", ":1")
            )

    def test_le_fil_porte_l_etat_du_navigateur(self):
        fil = services.fil_production(self.projet_id) if hasattr(self, "projet_id") else None
        if fil is None:
            projet_id = services.creer_projet("Sobriété")
            fil = services.fil_production(projet_id)
        self.assertIn("navigateur", fil)
        self.assertIn("chromes", fil["navigateur"])
        self.assertEqual(
            fil["navigateur"]["memoire_mo"],
            sum(c["memoire_mo"] for c in fil["navigateur"]["chromes"]),
        )


class TestEcranVirtuel(unittest.TestCase):
    def test_l_etat_dit_la_verite_sur_l_installation(self):
        etat = ecran.etat()
        self.assertEqual(etat["installe"], ecran.disponible())
        self.assertEqual(etat["commande_installation"], "sudo apt install xvfb")

    def test_sans_xvfb_l_erreur_nomme_la_commande_a_taper(self):
        if ecran.disponible():
            self.skipTest("Xvfb est installé sur cette machine.")
        with self.assertRaises(ecran.EcranIndisponible) as capture:
            ecran.environnement()
        self.assertIn("sudo apt install xvfb", str(capture.exception))

    def test_le_mode_ecran_virtuel_lance_chrome_sur_cet_ecran(self):
        from unittest import mock

        from videroo.adapters import navigateur

        if not ecran.disponible():
            self.skipTest("Xvfb absent : rien à démarrer.")
        session = navigateur.SessionNavigateur()
        appels = []

        class FauxChromium:
            def launch_persistent_context(self, **kw):
                appels.append(kw)
                raise RuntimeError("stop")

        class FauxPw:
            chromium = FauxChromium()

            def stop(self):
                pass

        session._pw = FauxPw()
        try:
            with mock.patch.object(session, "_demarrer_playwright", return_value=FauxPw()):
                with self.assertRaises(navigateur.NavigateurIndisponible):
                    session._ouvrir(affichage="ecran_virtuel", profil="essai")
        finally:
            ecran.arreter()
        premier = appels[0]
        self.assertTrue(premier["env"]["DISPLAY"].startswith(":"))
        self.assertNotIn("WAYLAND_DISPLAY", premier["env"])
        self.assertFalse(premier["headless"], "un vrai Chrome : Cloudflare repère le headless")

    def test_chrome_recoit_l_ordre_d_utiliser_x11(self):
        """Sans ça, Chrome choisit Wayland, ignore DISPLAY et s'affiche sur le bureau.

        Mesuré le 6 novembre 2026 : l'écran virtuel restait vide — zéro fenêtre —
        pendant que la fenêtre apparaissait sur l'écran de l'utilisateur.
        """
        from unittest import mock

        from videroo.adapters import navigateur

        if not ecran.disponible():
            self.skipTest("Xvfb absent : rien à démarrer.")
        session = navigateur.SessionNavigateur()
        appels = []

        class FauxChromium:
            def launch_persistent_context(self, **kw):
                appels.append(kw)
                raise RuntimeError("stop")

        class FauxPw:
            chromium = FauxChromium()

            def stop(self):
                pass

        session._pw = FauxPw()
        try:
            with mock.patch.object(session, "_demarrer_playwright", return_value=FauxPw()):
                with self.assertRaises(navigateur.NavigateurIndisponible):
                    session._ouvrir(affichage="ecran_virtuel", profil="essai")
        finally:
            ecran.arreter()
        self.assertIn("--ozone-platform=x11", appels[0]["args"])
        self.assertEqual(ecran.ARGUMENTS, ["--ozone-platform=x11"])

    def test_hors_ecran_reste_hors_ecran(self):
        """Le choix explicite de l'utilisateur n'est pas remplacé par l'écran virtuel."""
        from unittest import mock

        from videroo.adapters import navigateur

        session = navigateur.SessionNavigateur()
        appels = []

        class FauxChromium:
            def launch_persistent_context(self, **kw):
                appels.append(kw)
                raise RuntimeError("stop")

        class FauxPw:
            chromium = FauxChromium()

            def stop(self):
                pass

        session._pw = FauxPw()
        with mock.patch.object(session, "_demarrer_playwright", return_value=FauxPw()):
            with self.assertRaises(navigateur.NavigateurIndisponible):
                session._ouvrir(affichage="hors_ecran", profil="essai")
        self.assertNotIn("env", appels[0])
        self.assertTrue(any("window-position" in a for a in appels[0]["args"]))

    def test_l_environnement_ecarte_wayland(self):
        if not ecran.disponible():
            self.skipTest("Xvfb absent : rien à démarrer.")
        try:
            env = ecran.environnement()
            self.assertNotIn("WAYLAND_DISPLAY", env)
            self.assertTrue(env["DISPLAY"].startswith(":"))
        finally:
            ecran.arreter()


if __name__ == "__main__":
    unittest.main()


class TestEtapeDeValidation(unittest.TestCase):
    """Le titre de l'étape ne doit pas compter les notes comme des problèmes.

    Deux lignes « info » affichées comme « 2 point(s) relevé(s) » donnent
    l'impression que le pack est cassé alors qu'il n'y a rien à corriger.
    """

    def client(self):
        from fastapi.testclient import TestClient

        from videroo.app import app

        return TestClient(app)

    def page(self, pack: str) -> str:
        tests.reinitialiser()
        projet_id = services.creer_projet("Validation")
        services.appliquer_pack(projet_id, services.analyser_pack(pack))
        with self.client() as client:
            return client.get(f"/projets/{projet_id}/histoire").text

    def test_un_pack_propre_dit_rien_a_corriger(self):
        page = self.page(tests.pack_exemple())
        self.assertIn("rien à corriger", page)
        self.assertNotIn("point(s) à corriger", page)

    def test_un_vrai_manque_est_compte(self):
        ampute = tests.pack_exemple().replace("LA RÈGLE DU MÉCHANT", "NOTE INTERNE")
        page = self.page(ampute)
        self.assertIn("point(s) à corriger", page)
        self.assertNotIn("rien à corriger", page)
