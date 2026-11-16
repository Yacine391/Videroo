"""Une vidéo appartient à la scène dont elle anime l'image de départ.

Le 12 novembre 2026, Grok a sorti deux prises pour la scène 5 du projet 38 ; la
seconde, arrivée pendant la scène 6, a été prise pour celle-ci, et toutes les
vidéos suivantes se sont décalées d'une scène. Une vidéo Grok démarre sur son
image de départ : c'est ce qui permet de la rendre à sa scène.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from videroo import media


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg absent")
class TestVideoDeLaScene(unittest.TestCase):
    def setUp(self):
        self.dossier = Path(tempfile.mkdtemp())
        # Deux « scènes » au cadrage opposé : moitié gauche claire / moitié haute claire.
        self.a = self._image("a.png", "if(lt(X,W/2),230,20)")
        self.b = self._image("b.png", "if(lt(Y,H/2),230,20)")
        self.video_a = self.dossier / "video_a.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(self.a), "-t", "1",
             "-pix_fmt", "yuv420p", "-vf", "scale=720:1280", str(self.video_a)],
            check=True,
        )

    def tearDown(self):
        shutil.rmtree(self.dossier, ignore_errors=True)

    def _image(self, nom, formule):
        chemin = self.dossier / nom
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             "color=c=black:s=360x640:d=1", "-vf", f"format=gray,geq=lum='{formule}'",
             "-frames:v", "1", str(chemin)],
            check=True,
        )
        return chemin

    def test_la_prise_dune_autre_scene_est_reconnue(self):
        """Vidéo de la scène 5 arrivée pendant la scène 6 : refusée, scène 5 nommée."""
        verdict = media.scene_plus_proche(self.video_a, self.b, {5: self.a})
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict[0], 5)

    def test_la_bonne_video_passe(self):
        self.assertIsNone(media.scene_plus_proche(self.video_a, self.a, {6: self.b}))

    def test_dans_le_doute_on_ne_refuse_pas(self):
        """Deux plans du même décor : écarts proches, la vidéo est gardée."""
        presque_a = self._image("a2.png", "if(lt(X,W/2),225,25)")
        self.assertIsNone(media.scene_plus_proche(self.video_a, self.a, {6: presque_a}))

    def test_fichier_illisible_ne_bloque_rien(self):
        faux = self.dossier / "faux.mp4"
        faux.write_bytes(b"pas une video")
        self.assertIsNone(media.scene_plus_proche(faux, self.a, {5: self.b}))


class TestMiseDeCote(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg absent")
    def test_la_video_ecartee_est_gardee_a_part(self):
        from unittest import mock

        from videroo.adapters import base, grok

        dossier = Path(tempfile.mkdtemp())
        try:
            sortie = dossier / "scene-06_video.mp4"
            sortie.write_bytes(b"x")
            ctx = base.ContexteTache(
                projet_id=1, tache_id=1, cible_type="video", cible_id=6, libelle="s6",
                prompt="", dossier_projet=dossier, chemin_sortie=sortie,
                fichiers_joints=[dossier / "scene-06_image.png"],
                extras={"images_scenes": {5: str(dossier / "scene-05_image.png")}},
            )
            with mock.patch.object(media, "scene_plus_proche", return_value=(5, 6.4, 12.9)):
                self.assertTrue(grok.AdaptateurGrok()._mettre_de_cote_si_autre(ctx, "src"))
            self.assertFalse(sortie.exists(), "la vidéo d'une autre scène est restée rattachée")
            gardees = list(dossier.glob("scene-06_video_scene5-probable_*.mp4"))
            self.assertEqual(len(gardees), 1, "la vidéo écartée n'a pas été gardée")
            self.assertEqual(ctx.extras["doute"][1], 5)
        finally:
            shutil.rmtree(dossier, ignore_errors=True)


class TestReglagesSansQuitterLeChat(unittest.TestCase):
    """16 novembre 2026 : « button:has-text('9:16') » a cliqué la discussion
    « Anime vertical 9:16 avec kiwi accusateur… » de la barre latérale ; le
    prompt de la scène 1 est parti dans ce vieux chat."""

    class Element:
        def __init__(self, texte, dans_la_navigation):
            self.texte, self.nav, self.clique = texte, dans_la_navigation, False

        def is_visible(self):
            return True

        def inner_text(self, timeout=0):
            return self.texte

        def evaluate(self, _js):
            return not self.nav

        def click(self, timeout=0):
            self.clique = True

    class Page:
        def __init__(self, elements, url="https://grok.com/imagine/agent/a?conversation=b"):
            self.elements, self.url = elements, url

        def locator(self, _s):
            page = self

            class Liste:
                def all(self):
                    return page.elements

            return Liste()

        def wait_for_timeout(self, _ms):
            pass

    def test_un_titre_de_discussion_nest_pas_un_bouton_de_reglage(self):
        from videroo.adapters import navigateur

        titre = self.Element("Anime vertical 9:16 avec kiwi accusateur", True)
        vrai = self.Element("9:16", False)
        self.assertIs(navigateur.petit_bouton(self.Page([titre, vrai]), ["x"], timeout=0), vrai)
        seul_titre = self.Element("9:16", True)  # court, mais dans la navigation
        self.assertIsNone(navigateur.petit_bouton(self.Page([seul_titre]), ["x"], timeout=0))

    def test_le_format_ne_clique_jamais_la_barre_laterale(self):
        from videroo.adapters import grok

        titre = self.Element("Anime vertical 9:16 avec kiwi accusateur", True)
        page = self.Page([titre])
        grok.AdaptateurGrok()._regler_format(page, {"reglage_format": ["button:has-text('9:16')"]})
        self.assertFalse(titre.clique, "la discussion de la barre latérale a été cliquée")

    def test_un_chat_qui_change_avant_lenvoi_arrete_tout(self):
        from videroo.adapters import base, grok

        depart = "https://grok.com/imagine/agent/a?conversation=b"
        page = self.Page([], url="https://grok.com/c/df22fe45")
        resultat = grok.AdaptateurGrok._chat_change(page, depart)
        self.assertIsNotNone(resultat)
        self.assertEqual(resultat.etat, base.ECHEC)
        self.assertIn("rien n'a été envoyé", resultat.message)
        self.assertIsNone(grok.AdaptateurGrok._chat_change(self.Page([], url=depart), depart))


class TestAttenteFinDeTravail(unittest.TestCase):
    def test_on_attend_que_grok_ait_fini_avant_le_repere(self):
        from unittest import mock

        from videroo.adapters import base, grok, navigateur

        etats = iter(["travaille", "travaille", None])
        journal = []
        ctx = base.ContexteTache(
            projet_id=1, tache_id=1, cible_type="video", cible_id=1, libelle="x",
            prompt="", dossier_projet=Path("."), chemin_sortie=Path("x.mp4"),
            journaliser=journal.append,
        )
        with mock.patch.object(navigateur, "present", side_effect=lambda *a, **k: next(etats)), \
             mock.patch.object(grok.time, "sleep"):
            grok.AdaptateurGrok()._attendre_que_grok_ait_fini(
                object(), {"signes_travail": ["text=/Travaille depuis/i"]}, ctx
            )
        self.assertTrue(any("travaille encore" in m for m in journal))


class TestVideoDeLaReponse(unittest.TestCase):
    """16 novembre 2026 : la prise brute de la galerie (en anglais) a été prise à
    la place de la vidéo que l'agent a mise dans sa réponse (en français)."""

    def test_seule_la_video_de_la_reponse_est_telechargee(self):
        from unittest import mock

        from videroo.adapters import base, grok

        dossier = Path(tempfile.mkdtemp())
        try:
            telechargees = []

            class Reponse:
                ok = True

                def __init__(self, src):
                    self.src = src

                def body(self):
                    return f"video {self.src}".encode()

            class Requete:
                def get(self, src):
                    telechargees.append(src)
                    return Reponse(src)

            class Page:
                url = "https://grok.com/imagine/agent/a?conversation=b"
                request = Requete()

            ctx = base.ContexteTache(
                projet_id=1, tache_id=1, cible_type="video", cible_id=1, libelle="s1",
                prompt="", dossier_projet=dossier, chemin_sortie=dossier / "scene-01_video.mp4",
            )
            adaptateur = grok.AdaptateurGrok()
            with mock.patch.object(grok.AdaptateurGrok, "_sources_videos",
                                   return_value={"https://x/galerie.mp4", "https://x/reponse.mp4"}):
                resultat = adaptateur._telecharger(
                    Page(), {}, ctx, set(), set(), reponse=["https://x/reponse.mp4"]
                )
                self.assertEqual(resultat.etat, base.TERMINE)
                self.assertEqual(telechargees, ["https://x/reponse.mp4"])
                # Réponse encore vide : on n'attrape pas la galerie, on attend.
                self.assertIsNone(adaptateur._telecharger(Page(), {}, ctx, set(), set(), reponse=[]))
        finally:
            shutil.rmtree(dossier, ignore_errors=True)


class TestVideoRapide(unittest.TestCase):
    """16 novembre 2026 : une vidéo de 6 s rendue en 25 s a été prise pour une
    ancienne vidéo ; l'attente a couru 15 minutes alors qu'elle était là."""

    def test_une_video_rapide_dans_la_reponse_est_acceptee(self):
        from unittest import mock

        from videroo.adapters import grok

        adaptateur = grok.AdaptateurGrok()
        with mock.patch.object(grok.AdaptateurGrok, "_sources_videos",
                               return_value={"https://x/vieille.mp4", "https://x/neuve.mp4"}), \
             mock.patch.object(grok.AdaptateurGrok, "_videos_de_la_reponse",
                               return_value=["https://x/neuve.mp4"]), \
             mock.patch.object(grok.time, "sleep"):
            etat = adaptateur._attendre_video(
                object(), {}, {"https://x/vieille.mp4"}, 60, None, marqueur="scène 22 :"
            )
        self.assertEqual(etat, "video")
