"""Une zone de saisie recouverte n'est pas une zone absente.

Le 7 novembre 2026, la scène 1 de « IL A TOUT DONNÉ À SA PETITE SŒUR » a échoué
sur « Zone de saisie Grok introuvable » alors que la zone était bien là : la
recherche du réglage de durée avait ouvert un menu de la barre latérale, et un
menu ouvert absorbe les clics de toute la page. Ces tests verrouillent les trois
corrections : refermer, dire la vérité, et viser le bon profil Chrome.
"""
import tempfile
import unittest
from pathlib import Path

import tests
from videroo.adapters import base, grok, navigateur, selecteurs


class ElementFactice:
    def __init__(self, page, selecteur, visible=True, clic_bloque=False):
        self.page = page
        self.selecteur = selecteur
        self._visible = visible
        self.clic_bloque = clic_bloque
        self.texte = None

    @property
    def first(self):
        return self

    def is_visible(self, **kw):
        return self._visible

    def click(self, **kw):
        if self.clic_bloque:
            raise RuntimeError(
                "Timeout 8000ms exceeded.\n<div> intercepts pointer events"
            )
        self.page.clics.append(self.selecteur)

    def fill(self, texte, **kw):
        self.texte = texte
        self.page.saisi = texte


class PageFactice:
    """Juste ce que navigateur.saisir et fermer_surcouches utilisent."""

    def __init__(self, visibles=(), bloquer_clic=False):
        self.visibles = set(visibles)
        self.bloquer_clic = bloquer_clic
        self.clics: list[str] = []
        self.touches: list[str] = []
        self.saisi = None
        self.url = "https://grok.com/c/abc"

    def locator(self, selecteur):
        return ElementFactice(
            self,
            selecteur,
            visible=selecteur in self.visibles,
            clic_bloque=self.bloquer_clic and selecteur not in navigateur.SURCOUCHES,
        )

    @property
    def keyboard(self):
        page = self

        class Clavier:
            def press(self, touche):
                page.touches.append(touche)
                # Échap referme : plus aucune surcouche ensuite.
                if touche == "Escape":
                    page.visibles -= {s for s, _ in navigateur.SURCOUCHES}

            def insert_text(self, texte):
                page.saisi = texte

        return Clavier()

    def wait_for_timeout(self, ms):
        pass


ZONE = "textarea[aria-label*='Ask']"
MENU = "[role='menu'][data-state='open']"


class TestSaisieHonnete(unittest.TestCase):
    def test_zone_vraiment_absente(self):
        ok, pourquoi = navigateur.saisir(PageFactice(), [ZONE], "bonjour")
        self.assertFalse(ok)
        self.assertIn("aucune zone de saisie visible", pourquoi)
        self.assertIn(ZONE, pourquoi)

    def test_zone_presente_mais_recouverte(self):
        page = PageFactice(visibles=[ZONE, MENU], bloquer_clic=True)
        ok, pourquoi = navigateur.saisir(page, [ZONE], "bonjour")
        self.assertFalse(ok)
        # Le mot « introuvable » enverrait réparer un sélecteur qui marche.
        self.assertNotIn("introuvable", pourquoi)
        self.assertIn("est bien là", pourquoi)
        self.assertIn("menu", pourquoi)

    def test_saisie_nominale(self):
        page = PageFactice(visibles=[ZONE])
        ok, detail = navigateur.saisir(page, [ZONE], "bonjour")
        self.assertTrue(ok)
        self.assertEqual(page.saisi, "bonjour")
        self.assertEqual(detail, ZONE)


class TestFermetureDesSurcouches(unittest.TestCase):
    def test_echap_referme_le_menu(self):
        page = PageFactice(visibles=[ZONE, MENU])
        ferme = navigateur.fermer_surcouches(page)
        self.assertEqual(ferme, "un menu")
        self.assertEqual(page.touches, ["Escape"])
        self.assertEqual(navigateur.surcouche_ouverte(page), "")

    def test_rien_ouvert_rien_touche(self):
        """Pas d'Échap gratuit : il effacerait un brouillon pour rien."""
        page = PageFactice(visibles=[ZONE])
        self.assertEqual(navigateur.fermer_surcouches(page), "")
        self.assertEqual(page.touches, [])

    def test_une_zone_recouverte_redevient_saisissable(self):
        page = PageFactice(visibles=[ZONE, MENU], bloquer_clic=True)
        self.assertFalse(navigateur.saisir(page, [ZONE], "x")[0])
        navigateur.fermer_surcouches(page)
        page.bloquer_clic = False
        self.assertTrue(navigateur.saisir(page, [ZONE], "x")[0])


class TestReglagesScopesAuComposeur(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        self.sel = selecteurs.charger()["grok"]

    def test_ouvrir_reglages_ne_vise_que_la_barre_de_saisie(self):
        """Un « Options » de la barre latérale ne doit plus être cliquable."""
        for s in self.sel["ouvrir_reglages"]:
            self.assertTrue(
                s.startswith("form:has("),
                f"« {s} » peut cliquer n'importe où dans la page",
            )

    def test_le_reglage_rate_ne_laisse_aucun_panneau_ouvert(self):
        page = PageFactice(visibles=[MENU])
        grok.AdaptateurGrok()._regler_duree(page, self.sel, 6)
        self.assertEqual(navigateur.surcouche_ouverte(page), "")


class TestProfilGrok(unittest.TestCase):
    def test_grok_ouvre_le_profil_du_compte_choisi(self):
        """Sinon le compte affiché n'est pas le compte réellement utilisé."""
        source = (grok.__file__).replace(".pyc", ".py")
        with open(source, encoding="utf-8") as fh:
            contenu = fh.read()
        self.assertIn('page("grok", url, ctx.profil)', contenu)


if __name__ == "__main__":
    unittest.main()


class PageAdresse:
    """Une page dont l'URL ne devient une conversation qu'au bout d'un moment."""

    def __init__(self, etapes):
        self.etapes = list(etapes)

    @property
    def url(self):
        return self.etapes.pop(0) if len(self.etapes) > 1 else self.etapes[0]


class TestAdresseDeConversation(unittest.TestCase):
    def test_on_attend_la_vraie_adresse(self):
        page = PageAdresse(
            ["https://grok.com/", "https://grok.com/", "https://grok.com/c/abc"]
        )
        self.assertEqual(grok.adresse_du_chat(page, secondes=5), "https://grok.com/c/abc")

    def test_rien_plutot_quune_adresse_fausse(self):
        """« grok.com/ » n'est pas un chat : la retenir perdrait les règles."""
        page = PageAdresse(["https://grok.com/"])
        self.assertEqual(grok.adresse_du_chat(page, secondes=1), "")

    def test_la_forme_imagine_est_reconnue(self):
        """Sinon on attendrait 15 s un « /c/… » qui ne viendra jamais."""
        imagine = "https://grok.com/imagine/agent/848e4b06?conversation=91a6190d"
        page = PageAdresse(["https://grok.com/imagine", imagine, imagine])
        self.assertEqual(grok.adresse_du_chat(page, secondes=5), imagine)


class TestReglesLieesAuProfil(unittest.TestCase):
    """Les règles vivent dans un chat, donc dans un profil Chrome."""

    def setUp(self):
        tests.reinitialiser()

    def _projet(self, **colonnes):
        from videroo import db, services

        pid = services.creer_projet("Règles Grok")
        if colonnes:
            sql = ", ".join(f"{c}=?" for c in colonnes)
            db.exec1(f"UPDATE projets SET {sql} WHERE id=?", (*colonnes.values(), pid))
        return db.q1("SELECT * FROM projets WHERE id=?", (pid,))

    def test_colonne_disponible(self):
        self.assertIn("grok_profil_regles", self._projet().keys())

    def test_regles_du_bon_profil(self):
        p = self._projet(
            grok_regles_installees=1,
            grok_chat_url="https://grok.com/c/abc",
            grok_profil_regles="grok-yacine",
        )
        self.assertTrue(grok.regles_presentes(p, "grok-yacine"))

    def test_autre_profil_autre_session_donc_pas_de_regles(self):
        p = self._projet(
            grok_regles_installees=1,
            grok_chat_url="https://grok.com/c/abc",
            grok_profil_regles="principal",
        )
        self.assertFalse(grok.regles_presentes(p, "grok-yacine"))

    def test_sans_adresse_de_chat_les_regles_ne_sont_nulle_part(self):
        """Le cas réel du 7 novembre : case cochée, adresse « grok.com/ »."""
        p = self._projet(
            grok_regles_installees=1, grok_chat_url="https://grok.com/"
        )
        self.assertFalse(grok.regles_presentes(p, "grok-yacine"))


class PagePreuve:
    """Une page où un seul texte est présent, pour vérifier ce qu'on cite."""

    def __init__(self, visible, texte):
        self.visible, self.texte = visible, texte

    def locator(self, selecteur):
        page = self

        class Element:
            @property
            def first(self):
                return self

            def is_visible(self, **kw):
                return selecteur == page.visible

            def inner_text(self, **kw):
                return page.texte

        return Element()

    def wait_for_timeout(self, ms):
        pass


class TestQuotaVerifiable(unittest.TestCase):
    """Déclarer un compte épuisé le met de côté des jours : il faut une preuve."""

    def setUp(self):
        tests.reinitialiser()
        self.sel = selecteurs.charger()["grok"]

    def test_la_preuve_cite_le_texte_vu(self):
        page = PagePreuve("text=rate limit", "You hit the rate limit, retry in 2h")
        vu = navigateur.preuve(page, ["text=rate limit"])
        self.assertIn("text=rate limit", vu)
        self.assertIn("rate limit, retry", vu)

    def test_rien_vu_rien_affirme(self):
        page = PagePreuve("autre", "")
        self.assertEqual(navigateur.preuve(page, ["text=rate limit"], timeout=300), "")

    def test_unlimited_ne_doit_plus_passer_pour_un_quota(self):
        """« text=limit » trouvait « Unlimited » et grillait un compte pour rien."""
        self.assertNotIn("text=limit", self.sel["signes_quota"])
        self.assertNotIn("text=upgrade", self.sel["signes_quota"])
        for signe in self.sel["signes_quota"]:
            mot = signe.removeprefix("text=")
            self.assertNotIn(
                mot.lower(),
                "unlimited upgrade your plan",
                f"« {signe} » se déclenche sur un texte d'abonnement ordinaire",
            )


class TestChatDedieDuCompte(unittest.TestCase):
    """Chez Grok, seule la conversation du projet « Video » sait animer."""

    def setUp(self):
        tests.reinitialiser()
        from videroo import comptes

        self.comptes = comptes

    def test_adresse_web_exigee(self):
        with self.assertRaises(self.comptes.ErreurCompte):
            self.comptes.verifier_adresse_chat("la conversation du projet Video")

    def test_adresse_acceptee_et_nettoyee(self):
        adresse = "  https://grok.com/c/936a026d-19a8-41df-bc70-5b4203bcb5fa  "
        self.assertEqual(
            self.comptes.verifier_adresse_chat(adresse),
            "https://grok.com/c/936a026d-19a8-41df-bc70-5b4203bcb5fa",
        )

    def test_vide_reste_vide(self):
        self.assertEqual(self.comptes.verifier_adresse_chat(""), "")

    def test_adresse_imagine_acceptee(self):
        """La forme réelle de tes conversations du projet « Video »."""
        adresse = (
            "https://grok.com/imagine/agent/802915e8-6936-4aa2-a8a5-52e90a76f177"
            "?conversation=81bd3722-0a4b-4f79-9bf3-8722d7cbfcd7"
        )
        self.assertEqual(self.comptes.verifier_adresse_chat(adresse), adresse)

    def test_accueil_grok_refuse(self):
        """« https://grok.com/ » ouvre un chat neuf à chaque fois.

        L'accepter comme chat dédié serait le pire des deux mondes : l'appli
        croirait tes règles déjà posées et enverrait quand même chaque scène
        dans une conversation vierge.
        """
        for adresse in ("https://grok.com/", "https://grok.com/imagine"):
            with self.subTest(adresse=adresse):
                with self.assertRaises(self.comptes.ErreurCompte):
                    self.comptes.verifier_adresse_chat(adresse)

    def test_enregistrement_sur_le_compte(self):
        from videroo import db

        self.comptes.assurer_comptes_par_defaut()
        cid = db.q1("SELECT id FROM comptes_ia WHERE site='grok'")["id"]
        self.comptes.modifier(cid, chat_url="https://grok.com/c/abc")
        self.assertEqual(self.comptes.compte(cid)["chat_url"], "https://grok.com/c/abc")


class TestAnnonceDeSerie(unittest.TestCase):
    """« Nouvelle série » doit être dit dans chaque chat qui l'ignore."""

    def test_le_rid_ne_fait_pas_un_chat_neuf(self):
        a = "https://grok.com/c/936a026d?rid=eef97118"
        b = "https://grok.com/c/936a026d?rid=c1475ec9"
        self.assertTrue(grok.meme_conversation(a, b))

    def test_une_autre_conversation_est_un_autre_chat(self):
        self.assertFalse(
            grok.meme_conversation("https://grok.com/c/aaa", "https://grok.com/c/bbb")
        )

    def test_jamais_annoncee_nulle_part(self):
        self.assertFalse(grok.meme_conversation("", "https://grok.com/c/aaa"))

    def test_deux_conversations_du_meme_agent_imagine(self):
        """Le piège des adresses « /imagine/agent/… ».

        Chez toi, l'identifiant de la conversation est dans la question de
        l'adresse, pas dans son chemin. Comparer les chemins seuls prendrait la
        conversation de Dariya pour celle de Yacine dès que l'agent est le même :
        la série ne serait pas annoncée, et surtout le garde-fou « ce n'est pas
        mon chat » laisserait passer un prompt envoyé au mauvais endroit.
        """
        agent = "https://grok.com/imagine/agent/848e4b06"
        self.assertFalse(
            grok.meme_conversation(f"{agent}?conversation=91a6190d",
                                   f"{agent}?conversation=ce3f1823")
        )
        self.assertTrue(
            grok.meme_conversation(f"{agent}?conversation=91a6190d",
                                   f"{agent}?conversation=91a6190d&rid=ef12")
        )

    def test_le_message_annonce_bien_la_serie(self):
        from videroo.adapters import base as abase

        ctx = abase.ContexteTache(
            projet_id=1, tache_id=1, cible_type="video", cible_id=1,
            libelle="", prompt="Anime ce plan.",
            dossier_projet=Path("/tmp"), chemin_sortie=Path("/tmp/x.mp4"),
            serie="il_a_tout_donne", episode=1, duree=6,
            extras={"scene_numero": 1},
        )
        premier = grok.message_scene(ctx, True)
        self.assertTrue(premier.startswith('Nouvelle série "il_a_tout_donne" :'))
        suivant = grok.message_scene(ctx, False)
        self.assertIn('Série "il_a_tout_donne" — Épisode 1 — scène 1', suivant)

    def test_la_partie_2_ne_dit_pas_nouvelle_serie(self):
        from videroo.adapters import base as abase

        ctx = abase.ContexteTache(
            projet_id=1, tache_id=1, cible_type="video", cible_id=1,
            libelle="", prompt="Anime ce plan.",
            dossier_projet=Path("/tmp"), chemin_sortie=Path("/tmp/x.mp4"),
            serie="il_a_tout_donne", episode=2, duree=6,
            extras={"scene_numero": 1},
        )
        premier = grok.message_scene(ctx, True)
        self.assertTrue(premier.startswith('"il_a_tout_donne" - Épisode 2 :'))
        self.assertNotIn("Nouvelle série", premier)
        self.assertIn("Video/il_a_tout_donne/Episode 2/", premier)


class VideoFactice:
    def __init__(self, src):
        self._src = src

    def get_attribute(self, nom):
        return self._src if nom == "src" else None


class ReponseFactice:
    def __init__(self, contenu):
        self.ok = True
        self._contenu = contenu

    def body(self):
        return self._contenu


class RequeteFactice:
    def __init__(self, journal):
        self.journal = journal

    def get(self, adresse):
        self.journal.append(adresse)
        return ReponseFactice(f"octets de {adresse}".encode())


class PageVideos:
    """Une page de conversation qui contient déjà les plans des scènes passées."""

    def __init__(self, sources):
        self.url = "https://grok.com/imagine/agent/848e4b06?conversation=91a6190d"
        self.sources = list(sources)
        self.telechargements = []
        self.request = RequeteFactice(self.telechargements)
        self.boutons_cliques = []

    def locator(self, selecteur):
        page = self

        class Liste:
            def all(self):
                return [VideoFactice(s) for s in page.sources]

            @property
            def first(self):
                raise AssertionError(
                    "le bouton de téléchargement ne doit pas être cherché quand une "
                    "adresse directe neuve existe : il n'est rattaché à aucune vidéo"
                )

        return Liste()


class TestVideoDeLaBonneScene(unittest.TestCase):
    """La vidéo rendue doit être celle de CETTE scène.

    Ta conversation du projet « Video » garde les plans déjà faits : cinq vidéos
    y étaient le 7 novembre 2026. Rendre l'une d'elles sous le nom de la scène en
    cours serait le pire des résultats, parce qu'il aurait l'air réussi.
    """

    def setUp(self):
        self.anciennes = {
            f"https://assets.grok.com/users/20fec6b4/generated_{n}.mp4" for n in range(5)
        }
        self.neuve = "https://assets.grok.com/users/20fec6b4/generated_neuve.mp4"
        self.sortie = Path(tempfile.mkdtemp()) / "scene-01_video.mp4"

    def _ctx(self):
        from videroo.adapters import base as abase

        return abase.ContexteTache(
            projet_id=1, tache_id=1, cible_type="video", cible_id=1,
            libelle="", prompt="", dossier_projet=self.sortie.parent,
            chemin_sortie=self.sortie,
        )

    def test_seule_la_nouvelle_est_enregistree(self):
        page = PageVideos(self.anciennes | {self.neuve})
        sel = {"videos_reponse": ["video"], "bouton_telecharger": ["a[download]"]}
        resultat = grok.AdaptateurGrok()._telecharger(page, sel, self._ctx(), self.anciennes)
        self.assertEqual(resultat.etat, base.TERMINE)
        self.assertEqual(page.telechargements, [self.neuve])
        self.assertIn("generated_neuve", self.sortie.read_text())

    def test_aucune_nouvelle_ne_rend_jamais_une_ancienne(self):
        """Mieux vaut « à vérifier » qu'un ancien plan annoncé comme réussi."""
        page = PageVideos(self.anciennes)
        sel = {"videos_reponse": ["video"], "bouton_telecharger": []}
        resultat = grok.AdaptateurGrok()._telecharger(page, sel, self._ctx(), self.anciennes)
        self.assertEqual(resultat.etat, base.A_VERIFIER)
        self.assertEqual(page.telechargements, [])
        self.assertFalse(self.sortie.exists())


class PageQuiCharge:
    """Une conversation dont les anciennes vidéos arrivent par vagues."""

    def __init__(self, vagues):
        self.vagues = list(vagues)
        self.url = "https://grok.com/imagine/agent/848e4b06?conversation=91a6190d"

    def locator(self, selecteur):
        page = self

        class Liste:
            def all(self):
                vues = page.vagues[0]
                if len(page.vagues) > 1:
                    page.vagues.pop(0)
                return [VideoFactice(s) for s in vues]

        return Liste()


class TestRepereAvantEnvoi(unittest.TestCase):
    """Le repère doit attendre que la conversation ait fini de charger.

    226 vidéos chez « Yacine », 177 chez « Dariya » le 7 novembre 2026 : elles
    n'arrivent pas toutes dans la même seconde. Un repère posé trop tôt ferait
    passer une vidéo ancienne pour celle de la scène, et la scène serait
    annoncée comme réussie avec le mauvais plan.
    """

    def setUp(self):
        self.sel = {"videos_reponse": ["video"]}
        self.journal = []

        from videroo.adapters import base as abase

        self.ctx = abase.ContexteTache(
            projet_id=1, tache_id=1, cible_type="video", cible_id=1,
            libelle="", prompt="", dossier_projet=Path("/tmp"),
            chemin_sortie=Path("/tmp/x.mp4"),
            journaliser=self.journal.append,
        )

    def test_on_attend_la_fin_du_chargement(self):
        page = PageQuiCharge([{"a"}, {"a", "b"}, {"a", "b", "c"}, {"a", "b", "c"}])
        repere = grok.AdaptateurGrok()._videos_au_depart(page, self.sel, self.ctx, 20)
        self.assertEqual(repere, {"a", "b", "c"})
        self.assertFalse(self.journal, "chargement terminé : rien à signaler")

    def test_un_chargement_sans_fin_est_annonce(self):
        """On ne prétend pas que le repère est sûr quand il ne l'est pas."""
        compteur = iter(range(1, 500))
        page = PageQuiCharge([{str(n) for n in range(next(compteur))} for _ in range(40)])
        repere = grok.AdaptateurGrok()._videos_au_depart(page, self.sel, self.ctx, 3)
        self.assertTrue(repere)
        self.assertTrue(self.journal, "un repère incertain doit être dit")
        self.assertIn("chargeait encore", self.journal[0])


class TestRepriseDeSerie(unittest.TestCase):
    def test_le_message_rappelle_la_serie_et_la_scene(self):
        from videroo.adapters import base as abase
        ctx = abase.ContexteTache(
            projet_id=1, tache_id=1, cible_type="video", cible_id=1, libelle="",
            prompt="Anime ce plan.", dossier_projet=Path("/tmp"), chemin_sortie=Path("/tmp/x.mp4"),
            serie="le_garcon_a_la_fenetre", episode=1, duree=6, extras={"scene_numero": 8},
        )
        texte = grok.message_scene(ctx, False, reprise=True)
        self.assertTrue(texte.startswith(
            'Reprenons la série "le_garcon_a_la_fenetre" — Épisode 1 : il me manque la scène 8.'))


class TestLangueDesRepliques(unittest.TestCase):
    """16 novembre 2026 : la scène 1 du « Mariage forcé » est sortie en anglais."""

    def test_chaque_message_grok_exige_le_francais(self):
        from pathlib import Path

        from videroo.adapters import base, grok

        ctx = base.ContexteTache(
            projet_id=1, tache_id=1, cible_type="video", cible_id=1, libelle="x",
            prompt="« Maman… pourquoi tu me donnes le bouquet ? »",
            dossier_projet=Path("."), chemin_sortie=Path("x.mp4"), duree=10,
            serie="le_mariage_force", episode=1, extras={"scene_numero": 1},
        )
        for premiere in (True, False):
            texte = grok.message_scene(ctx, premiere)
            self.assertIn("EN FRANÇAIS, mot pour mot", texte)
            self.assertIn("« Maman… pourquoi tu me donnes le bouquet ? »", texte)
