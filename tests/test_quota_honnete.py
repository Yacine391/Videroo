"""Limite atteinte chez un fournisseur : dire vrai, attendre, repartir sans payer deux fois.

Nuit du 16 au 17 novembre 2026, « Le mariage forcé » partie 4. ChatGPT répond
à la scène 1 : « Vous avez atteint la limite de demandes de génération
d'images de l'offre Plus […] dans 1 heure. » Videroo affiche alors « à
vérifier », propose « Récupérer le résultat », rattache à la scène 1 la
miniature de la partie 3, puis annonce « rien n'a été envoyé ». Rien de tout
cela n'était vrai. Ces tests fixent ce qui doit l'être.
"""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import tests
from tests.test_comptes_multiples import AdaptateurQuota
from videroo import comptes, db, fichiers, runner, services
from videroo.adapters import base, chatgpt
from videroo.adapters import navigateur as nav

REFUS_CHATGPT = (
    "Vous avez atteint la limite de demandes de génération d’images de l’offre "
    "Plus. Vous pourrez créer d’autres images après la réinitialisation de la "
    "limite, dans 1 heure."
)


class TestHeureDeRetourAnnoncee(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        comptes.assurer_comptes_par_defaut()
        self.compte = comptes.liste("chatgpt")[0]

    def test_le_delai_vient_du_message_du_fournisseur(self):
        self.assertEqual(comptes.delai_annonce(REFUS_CHATGPT), timedelta(hours=1))
        self.assertEqual(comptes.delai_annonce("Try again in 23 minutes."),
                         timedelta(minutes=23))
        self.assertEqual(comptes.delai_annonce("dans 2 heures et 15 minutes"),
                         timedelta(hours=2, minutes=15))

    def test_sans_annonce_on_n_invente_rien(self):
        self.assertIsNone(comptes.delai_annonce("Vous avez atteint votre limite hebdomadaire."))
        self.assertIsNone(comptes.delai_annonce("Déclaré à court par toi, depuis l'atelier."))

    def test_le_compte_revient_a_l_heure_annoncee(self):
        apres = comptes.marquer_quota_epuise(self.compte["id"], REFUS_CHATGPT)
        self.assertFalse(apres["quota_dispo"])
        self.assertEqual(apres["retour_source"], "annonce")
        retour, source = comptes.retour_prevu(apres)
        attendu = datetime.now() + timedelta(hours=1)
        self.assertLess(abs((retour - attendu).total_seconds()), 300)
        # L'heure passée, il est de nouveau disponible — sans attendre le délai global.
        db.exec1("UPDATE comptes_ia SET quota_retour_le=? WHERE id=?",
                 ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"),
                  self.compte["id"]))
        self.assertTrue(comptes.compte(self.compte["id"])["quota_dispo"])

    def test_sans_annonce_l_heure_est_presentee_comme_une_estimation(self):
        apres = comptes.marquer_quota_epuise(self.compte["id"], "limite atteinte")
        self.assertEqual(apres["retour_source"], "estimation")
        self.assertIn("ne dit pas quand", runner.phrase_retour("chatgpt", None))

    def test_rearmer_efface_l_heure_annoncee(self):
        comptes.marquer_quota_epuise(self.compte["id"], REFUS_CHATGPT)
        apres = comptes.rearmer(self.compte["id"])
        self.assertTrue(apres["quota_dispo"])
        self.assertIsNone(apres["quota_retour_le"])


class TestFormulationsDeRefus(unittest.TestCase):
    """Chaque formulation relevée chez les fournisseurs, citée telle quelle."""

    def setUp(self):
        tests.reinitialiser()
        from videroo.adapters import selecteurs
        self.chatgpt = selecteurs.charger()["chatgpt"]

    def test_quota_epuise_17_novembre(self):
        texte = "Vous avez épuisé votre quota d’images pour le moment, réessayez dans 3 heures."
        cite = chatgpt.AdaptateurChatGPT._refus_dans(texte, self.chatgpt)
        self.assertEqual(cite, f"« {texte} »", "cité mot pour mot, accents compris")
        self.assertEqual(comptes.delai_annonce(cite), timedelta(hours=3))

    def test_limite_de_demandes_16_novembre(self):
        cite = chatgpt.AdaptateurChatGPT._refus_dans(REFUS_CHATGPT, self.chatgpt)
        self.assertIn("atteint la limite de demandes", cite)
        self.assertIn("dans 1 heure", cite)

    def test_un_prompt_ordinaire_n_est_pas_un_refus(self):
        for texte in (
            "Sa méfiance est celle d’une amie qui constate deux limites franchies.",
            "Elle a épuisé sa patience. Quota de répliques : trois.",
        ):
            self.assertEqual(chatgpt.AdaptateurChatGPT._refus_dans(texte, self.chatgpt), "")

    def test_la_suite_de_notre_message_grok_n_est_pas_un_refus(self):
        message = ("LANGUE : français, mot pour mot.\n"
                   "Nour, à voix basse : « Réessayez plus tard, il dort. »")
        apres_premiere_ligne = ("Nour, à voix basse : « Réessayez plus tard, il dort. » "
                                "Voici ta vidéo.")
        reste = nav.sans_notre_message(apres_premiere_ligne, message)
        self.assertEqual(reste, "Voici ta vidéo.")


class BaseChaine(unittest.TestCase):
    """Un projet prêt, une chaîne lancée, un seul compte ChatGPT."""

    def setUp(self):
        tests.reinitialiser()
        comptes.assurer_comptes_par_defaut()
        self.compte = comptes.liste("chatgpt")[0]
        self.projet_id = services.creer_projet("Quota")
        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_variantes()))
        services.valider_projet(self.projet_id)
        self.perso = db.q1(
            "SELECT * FROM personnages WHERE projet_id=? ORDER BY ordre LIMIT 1",
            (self.projet_id,),
        )

    def lancer_la_chaine(self, adaptateur):
        runner.enregistrer_adaptateur("chatgpt", "quota", adaptateur)
        db.exec1("UPDATE projets SET pipeline=1, pipeline_mode='quota' WHERE id=?",
                 (self.projet_id,))
        tache = runner.demander(self.projet_id, "planche", self.perso["id"],
                                fournisseur="chatgpt", mode="quota")
        return runner.Worker.executer(tache)

    def quota_revenu(self):
        db.exec1("UPDATE comptes_ia SET quota_retour_le=? WHERE site='chatgpt'",
                 ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"),))


class AdaptateurQuiRefuse(base.Adaptateur):
    """Le prompt part, le fournisseur répond par un refus daté."""

    def __init__(self):
        self.appels = 0
        self.refuse = True

    def generer(self, ctx):
        self.appels += 1
        ctx.envoye = True
        if self.refuse:
            return base.ResultatGeneration(
                base.QUOTA,
                f"ChatGPT a refusé la demande sur « {ctx.compte_libelle} » : "
                f"« {REFUS_CHATGPT} » Aucune image n'a été produite.",
            )
        ctx.chemin_sortie.parent.mkdir(parents=True, exist_ok=True)
        tests.fabriquer_image_test(ctx.chemin_sortie, texte="OK")
        return base.ResultatGeneration(base.TERMINE, "Produit.", chemin=str(ctx.chemin_sortie))


class TestLaChaineAttendLeRetourAnnonce(BaseChaine):
    def test_refus_date_la_chaine_reste_en_marche_et_le_dit(self):
        adaptateur = AdaptateurQuiRefuse()
        self.assertEqual(self.lancer_la_chaine(adaptateur), base.QUOTA)
        self.assertEqual(services.projet(self.projet_id)["pipeline"], 1)
        etat = runner.etat_production(self.projet_id)
        self.assertEqual(etat["blocages"], [], "ce n'est pas une décision à prendre")
        self.assertTrue(etat["reprise_auto"])
        self.assertEqual(services.projet(self.projet_id)["attend_quota"], 1)
        # Grok attend aussi : sa vidéo dépend d'une image, donc de cette planche.
        self.assertEqual({a["fournisseur"] for a in etat["attentes_quota"]}, {"chatgpt", "grok"})
        motif = next(a for a in etat["attentes_quota"] if a["fournisseur"] == "chatgpt")["motif"]
        self.assertIn("Retour annoncé par le fournisseur", motif)
        self.assertIn("rien n'a été payé", motif)
        tache = db.q1("SELECT * FROM taches WHERE projet_id=?", (self.projet_id,))
        self.assertIn("atteint la limite", tache["message"], "on cite le fournisseur")
        self.assertNotIn("Rien n'a été envoyé", tache["message"])

    def test_tant_que_le_quota_n_est_pas_revenu_rien_ne_repart(self):
        adaptateur = AdaptateurQuiRefuse()
        self.lancer_la_chaine(adaptateur)
        self.assertEqual(runner.reveiller_apres_quota("chatgpt"), [])
        self.assertEqual(runner.planifier(self.projet_id), [])
        self.assertEqual(adaptateur.appels, 1)

    def test_au_retour_la_chaine_repart_seule_une_fois(self):
        adaptateur = AdaptateurQuiRefuse()
        self.lancer_la_chaine(adaptateur)
        self.quota_revenu()
        adaptateur.refuse = False
        relancees = runner.reveiller_apres_quota("chatgpt")
        self.assertEqual(len(relancees), 1)
        self.assertEqual(runner.reveiller_apres_quota("chatgpt"), [], "pas deux fois")
        self.assertEqual(runner.Worker.executer(relancees[0]), base.TERMINE)
        self.assertTrue(fichiers.fichier_utilisable(
            db.q1("SELECT ref_path FROM personnages WHERE id=?", (self.perso["id"],))["ref_path"]
        ))
        statuts = [t["statut"] for t in runner.taches_cible(
            self.projet_id, "planche", self.perso["id"])]
        self.assertEqual(statuts, ["quota", "termine"])

    def test_arreter_annule_l_attente(self):
        adaptateur = AdaptateurQuiRefuse()
        self.lancer_la_chaine(adaptateur)
        runner.arreter_production(self.projet_id)
        self.quota_revenu()
        self.assertEqual(runner.reveiller_apres_quota("chatgpt"), [])

    def test_refus_repetes_la_chaine_cesse_d_insister(self):
        adaptateur = AdaptateurQuiRefuse()
        self.lancer_la_chaine(adaptateur)
        for _ in range(runner.REFUS_QUOTA_MAX - 1):
            self.quota_revenu()
            relancees = runner.reveiller_apres_quota("chatgpt")
            self.assertEqual(len(relancees), 1)
            runner.Worker.executer(relancees[0])
        self.quota_revenu()
        self.assertEqual(runner.reveiller_apres_quota("chatgpt"), [])
        etat, motif = runner.etat_cible(self.projet_id, "planche", self.perso["id"])
        self.assertEqual(etat, runner.BLOQUE)
        self.assertIn("n'insiste plus", motif)
        self.assertEqual(adaptateur.appels, runner.REFUS_QUOTA_MAX)


class TestQuotaSansHeureConnue(BaseChaine):
    def test_la_chaine_s_arrete_et_ne_repartira_pas_toute_seule(self):
        """Sans annonce du fournisseur, pas de reprise surprise trois jours plus tard."""
        statut = self.lancer_la_chaine(AdaptateurQuota({self.compte["profil"]}))
        self.assertEqual(statut, base.QUOTA)
        self.assertEqual(services.projet(self.projet_id)["pipeline"], 0)
        etat = runner.etat_production(self.projet_id)
        self.assertFalse(etat["reprise_auto"])
        self.assertTrue(etat["attentes_quota"])
        self.assertEqual(services.projet(self.projet_id)["attend_quota"], 0)
        comptes.rearmer(self.compte["id"])
        self.assertEqual(runner.reveiller_apres_quota("chatgpt"), [])

    def test_lancer_apres_le_retour_reprend_sur_le_meme_element(self):
        adaptateur = AdaptateurQuota({self.compte["profil"]})
        self.lancer_la_chaine(adaptateur)
        comptes.rearmer(self.compte["id"])
        adaptateur.profils_satures = set()
        etat, _ = runner.etat_cible(self.projet_id, "planche", self.perso["id"])
        self.assertEqual(etat, runner.A_FAIRE, "ni « à vérifier », ni bloqué")
        bilan = runner.demarrer_production(self.projet_id, "quota")
        self.assertEqual(len(bilan["taches"]), 1)
        self.assertEqual(runner.Worker.executer(bilan["taches"][0]), base.TERMINE)

    def test_lancer_sans_quota_ne_cree_aucune_tache_et_dit_pourquoi(self):
        self.lancer_la_chaine(AdaptateurQuota({self.compte["profil"]}))
        avant = db.q1("SELECT COUNT(*) n FROM taches")["n"]
        bilan = runner.demarrer_production(self.projet_id, "quota")
        self.assertEqual(bilan["taches"], [])
        self.assertEqual(db.q1("SELECT COUNT(*) n FROM taches")["n"], avant)
        phrase = runner.phrase_des_blocages(bilan["etat"])
        self.assertIn("attend le quota", phrase)
        self.assertEqual(services.projet(self.projet_id)["pipeline"], 0)


class TestEtatsAffiches(BaseChaine):
    def _element(self):
        fil = services.fil_production(self.projet_id)
        return next(p for p in fil["planches"] if p["id"] == self.perso["id"])

    def test_la_carte_dit_limite_atteinte_et_ne_propose_pas_de_recuperer(self):
        self.lancer_la_chaine(AdaptateurQuiRefuse())
        element = self._element()
        self.assertEqual(element["etat"], "quota")
        self.assertFalse(element["envoye"], "rien à aller lire chez le fournisseur")
        page = (Path(services.__file__).parent / "templates" / "atelier.html").read_text()
        self.assertIn("quota: 'limite atteinte'", page)
        self.assertIn("e.etat === 'quota'", page)

    def test_arret_avant_envoi_ne_parle_pas_de_recuperer(self):
        """Connexion demandée : rien n'est parti, il n'y a rien à récupérer."""
        tache = db.exec1(
            "INSERT INTO taches (projet_id, cible_type, cible_id, libelle, fournisseur,"
            " mode, origine, statut, envoye, message, cree_le) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.projet_id, "planche", self.perso["id"], "Planche", "chatgpt", "auto",
             "auto", "a_verifier", 0, "ChatGPT demande une connexion.", db.maintenant()),
        )
        etat, motif = runner.etat_cible(self.projet_id, "planche", self.perso["id"])
        self.assertEqual(etat, runner.BLOQUE)
        self.assertIn("AVANT tout envoi", motif)
        self.assertNotIn("Récupérer le résultat", motif)
        self.assertFalse(self._element()["envoye"])
        self.assertTrue(tache)


class TestRecupererUnRefus(BaseChaine):
    def test_le_refus_lu_chez_le_fournisseur_reclasse_la_tache(self):
        """« Récupérer » sur une tâche « à vérifier » dont la réponse était un refus."""
        tache = db.exec1(
            "INSERT INTO taches (projet_id, cible_type, cible_id, libelle, fournisseur,"
            " mode, origine, statut, envoye, cree_le) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.projet_id, "planche", self.perso["id"], "Planche", "chatgpt", "auto",
             "auto", "a_verifier", 1, db.maintenant()),
        )

        class LitUnRefus(base.Adaptateur):
            def recuperer(self, ctx):
                return base.ResultatGeneration(
                    base.QUOTA,
                    f"ChatGPT a répondu à ce prompt par un refus : « {REFUS_CHATGPT} » "
                    "Aucune image n'a été produite : il n'y a rien à récupérer.",
                )

        class SessionDirecte:
            @staticmethod
            def executer(fonction, *args, **kwargs):
                return fonction(*args, **kwargs)

        with mock.patch.object(runner, "adaptateur", return_value=LitUnRefus()), \
             mock.patch.object(nav, "session", return_value=SessionDirecte()):
            bilan = runner.recuperer_resultat(self.projet_id, "planche", self.perso["id"])
        self.assertFalse(bilan["ok"])
        self.assertEqual(bilan["etat"], base.QUOTA)
        self.assertEqual(db.q1("SELECT statut FROM taches WHERE id=?", (tache,))["statut"], "quota")
        etat, _ = runner.etat_cible(self.projet_id, "planche", self.perso["id"])
        self.assertEqual(etat, runner.A_FAIRE, "un compte est disponible : rien ne bloque")


class TestMarqueursDesPrompts(unittest.TestCase):
    """Les prompts d'un épisode commencent par le même bloc commun."""

    def setUp(self):
        tests.reinitialiser()
        self.projet_id = services.creer_projet("Marqueurs")
        services.appliquer_pack(self.projet_id, services.analyser_pack(tests.pack_variantes()))
        commun = "Créer un plan de fiction photoréaliste verticale 9:16, épisode 4.\n"
        self.scenes = db.q("SELECT id, numero FROM scenes WHERE projet_id=? ORDER BY numero",
                           (self.projet_id,))
        for s in self.scenes:
            db.exec1("UPDATE scenes SET prompt_image=? WHERE id=?",
                     (commun + f"Plan numéro {s['numero']} : Nour se tient sur le seuil.", s["id"]))

    def test_le_marqueur_est_propre_a_la_scene(self):
        vus = set()
        for s in self.scenes:
            mien, autres = runner.marqueurs_chatgpt(self.projet_id, "image", s["id"])
            self.assertIn(f"Plan numéro {s['numero']} :", mien)
            self.assertNotIn("Créer un plan de fiction", mien, "le bloc commun ne distingue rien")
            self.assertNotIn(mien, autres)
            vus.add(mien)
        self.assertEqual(len(vus), len(self.scenes))

    def test_le_contexte_chatgpt_porte_le_marqueur(self):
        scene = self.scenes[0]
        t = {"projet_id": self.projet_id, "id": None, "cible_type": "image",
             "cible_id": scene["id"], "libelle": "Image", "fournisseur": "chatgpt"}
        cible = runner.chemin_attendu(self.projet_id, "image", scene["id"])
        ctx = runner.construire_contexte(t, cible)
        self.assertIn("Plan numéro 1 :", ctx.extras["marqueur"])
        self.assertTrue(ctx.extras["autres_marqueurs"])


class PageRecuperation:
    """Ce que `recuperer` lit : une liste d'images, rien d'autre."""

    def __init__(self, images):
        self.images = images
        self.url = "https://chatgpt.com/c/abc"

    def locator(self, _selecteur):
        page = self

        class Liste:
            def all(self):
                class Img:
                    def __init__(self, src):
                        self.src = src

                    def get_attribute(self, nom):
                        return self.src if nom == "src" else None

                return [Img(s) for s in page.images]

            @property
            def first(self):
                class Vide:
                    @staticmethod
                    def is_visible(timeout=0):
                        return False

                return Vide()

        return Liste()

    def wait_for_timeout(self, _):
        pass


class TestRecupererDansLaReponseSeulement(unittest.TestCase):
    """La conversation contient la miniature de la partie 3 : ce n'est pas la scène 1."""

    def setUp(self):
        tests.reinitialiser()
        self.sortie = Path(tempfile.mkdtemp()) / "scene-01_image.png"
        self.adaptateur = chatgpt.AdaptateurChatGPT()

    def _recuperer(self, images, reponse, marqueur="Nuit du mariage, seuil de la chambre"):
        ctx = base.ContexteTache(
            projet_id=1, tache_id=1, cible_type="image", cible_id=1, libelle="",
            prompt="", dossier_projet=self.sortie.parent, chemin_sortie=self.sortie,
            extras={"marqueur": marqueur, "autres_marqueurs": []},
        )
        page = PageRecuperation(images)

        class Session:
            @staticmethod
            def page(*_a, **_k):
                return page

        with mock.patch.object(nav, "session", return_value=Session()), \
             mock.patch.object(nav, "present", return_value=None), \
             mock.patch.object(nav, "apres_notre_message", return_value=reponse), \
             mock.patch.object(nav, "octets_de_image", lambda _p, src: src.encode()), \
             mock.patch.object(chatgpt.time, "sleep", lambda _s: None):
            return self.adaptateur.recuperer(ctx)

    def test_un_refus_en_reponse_ne_ramene_pas_l_image_d_avant(self):
        resultat = self._recuperer(
            ["blob:miniature-partie-3"], {"texte": REFUS_CHATGPT, "images": []}
        )
        self.assertEqual(resultat.etat, base.QUOTA)
        self.assertIn("atteint la limite", resultat.message)
        self.assertIn("rien à récupérer", resultat.message)
        self.assertFalse(self.sortie.exists(), "la miniature n'a pas été enregistrée")

    def test_l_image_de_la_reponse_est_prise_pas_la_derniere_de_la_page(self):
        resultat = self._recuperer(
            ["blob:miniature-partie-3", "blob:scene-1", "blob:scene-2"],
            {"texte": "Voici l'image.", "images": ["blob:scene-1"]},
        )
        self.assertEqual(resultat.etat, base.TERMINE)
        self.assertEqual(self.sortie.read_bytes(), b"blob:scene-1")
        self.assertIn("dans la réponse à ton prompt", resultat.message)

    def test_prompt_sans_reponse_image_ne_prend_rien(self):
        resultat = self._recuperer(
            ["blob:miniature-partie-3"], {"texte": "Je réfléchis…", "images": []}
        )
        self.assertEqual(resultat.etat, base.A_VERIFIER)
        self.assertIn("aucune image en réponse", resultat.message)
        self.assertFalse(self.sortie.exists())

    def test_prompt_introuvable_on_ne_prend_rien_au_hasard(self):
        resultat = self._recuperer(["blob:miniature-partie-3"], None)
        self.assertEqual(resultat.etat, base.A_VERIFIER)
        self.assertIn("introuvable", resultat.message)
        self.assertFalse(self.sortie.exists())


PAGE_CONVERSATION = """<!doctype html><html><body><main>
<div><div data-testid="generated-image-gallery"><img alt="Image générée" src="blob:miniature-partie-3"></div>
<p>Voici la miniature. Hier : vous avez atteint la limite, dans 3 heures.</p></div>
<div><div><img src="blob:ref-nour"><img src="blob:ref-amine"></div>
<div style="white-space:pre-wrap">Créer un plan de fiction photoréaliste verticale 9:16.
Nuit du mariage, seuil de la chambre. Une amie constate deux limites franchies.</div></div>
<div><p>Vous avez atteint la limite de demandes de génération d’images de l’offre Plus.
Vous pourrez créer d’autres images après la réinitialisation de la limite, dans 1 heure.</p></div>
<div><div style="white-space:pre-wrap">Créer un plan de fiction photoréaliste verticale 9:16.
Même nuit, chambre de Nesrine, coiffeuse éclairée.</div></div>
<div><div data-testid="generated-image-gallery"><img alt="Image générée" src="blob:scene-2"></div>
<p>Voici l'image.</p></div>
</main>
<form><div contenteditable="true">brouillon : Nuit du mariage, seuil de la chambre</div></form>
</body></html>"""


class TestApresNotreMessageDansUnVraiNavigateur(unittest.TestCase):
    """Le JavaScript lui-même, sur une page qui ressemble à la conversation réelle."""

    SCENE_1 = "Nuit du mariage, seuil de la chambre"
    SCENE_2 = "Même nuit, chambre de Nesrine"
    IMAGES = ["[data-testid='generated-image-gallery'] img", "img[alt^='Image générée']"]

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls._pw = sync_playwright().start()
            try:
                cls._navigateur = cls._pw.chromium.launch(channel="chrome", headless=True)
            except Exception:
                cls._navigateur = cls._pw.chromium.launch(headless=True)
        except Exception as exc:  # pas de navigateur sur cette machine
            raise unittest.SkipTest(f"navigateur indisponible : {exc}")
        cls.page = cls._navigateur.new_page()
        cls.page.set_content(PAGE_CONVERSATION)

    @classmethod
    def tearDownClass(cls):
        cls._navigateur.close()
        cls._pw.stop()

    def test_le_refus_est_dans_la_reponse_a_la_scene_1_sans_image(self):
        r = nav.apres_notre_message(self.page, self.SCENE_1, [self.SCENE_2], self.IMAGES)
        self.assertIn("atteint la limite de demandes", r["texte"])
        self.assertIn("dans 1 heure", r["texte"])
        self.assertEqual(r["images"], [], "ni la miniature d'avant, ni l'image de la scène 2")
        self.assertNotIn("dans 3 heures", r["texte"], "le vieux refus est AVANT notre message")
        self.assertNotIn("coiffeuse", r["texte"], "borné par le message de la scène 2")

    def test_l_image_de_la_scene_2_est_dans_sa_reponse(self):
        r = nav.apres_notre_message(self.page, self.SCENE_2, [self.SCENE_1], self.IMAGES)
        self.assertEqual(r["images"], ["blob:scene-2"])
        self.assertNotIn("atteint la limite", r["texte"])

    def test_sans_borne_tout_ce_qui_suit_est_rendu(self):
        r = nav.apres_notre_message(self.page, self.SCENE_1, [], self.IMAGES)
        self.assertEqual(r["images"], ["blob:scene-2"])

    def test_le_brouillon_de_la_zone_de_saisie_n_est_pas_notre_message(self):
        """Le dernier porteur du marqueur est la bulle envoyée, pas le brouillon."""
        r = nav.apres_notre_message(self.page, self.SCENE_1, [self.SCENE_2], self.IMAGES)
        self.assertIsNotNone(r)
        self.assertIn("atteint la limite", r["texte"])

    def test_notre_prompt_rendu_en_morceaux_n_est_pas_la_reponse(self):
        """Si ChatGPT découpait un jour notre prompt en paragraphes, la suite du
        prompt (« … atteint la limite de la scène ») ne doit pas passer pour sa réponse."""
        self.page.set_content("""<main>
<div><p>Plan vertical 9:16. Nuit du mariage, seuil de la chambre.</p>
<p>Elle a atteint la limite de sa patience, dans 1 heure tout explose.</p></div>
<div><p>Voici l'image.</p></div></main>""")
        prompt = ("Plan vertical 9:16. Nuit du mariage, seuil de la chambre.\n"
                  "Elle a atteint la limite de sa patience, dans 1 heure tout explose.")
        r = nav.apres_notre_message(self.page, self.SCENE_1, [], self.IMAGES, prompt)
        self.assertEqual(r["texte"], "Voici l'image.")
        self.page.set_content(PAGE_CONVERSATION)

    def test_un_renvoi_ne_relit_pas_le_refus_d_hier(self):
        """17 novembre 2026 : le prompt de la scène 1 est déjà dans la conversation,
        suivi du refus de la nuit (« dans 41 minutes »). Juste après le renvoi, tant
        que le NOUVEAU message n'est pas affiché, ce refus-là n'est pas la réponse."""
        from videroo.adapters import selecteurs
        sel = selecteurs.charger()["chatgpt"]
        hier = """<div><div style="white-space:pre-wrap">Plan vertical 9:16.
Nuit du mariage, seuil de la chambre.</div></div>
<div><h6>ChatGPT a dit :</h6><p>Vous avez atteint la limite de demandes de génération d’images de l’offre Plus. Vous pourrez créer d’autres images après la réinitialisation de la limite, dans 41 minutes.</p></div>"""
        nouveau = """<div><div style="white-space:pre-wrap">Plan vertical 9:16.
Nuit du mariage, seuil de la chambre.</div></div>"""
        ad = chatgpt.AdaptateurChatGPT()
        self.page.set_content("<main>" + hier + "</main><footer><p>ChatGPT peut faire des erreurs.</p></footer>")
        deja = nav.apres_notre_message(self.page, self.SCENE_1, [], self.IMAGES)
        self.assertEqual(deja["occurrences"], 1)
        minimum = deja["occurrences"] + 1
        # Le nouvel envoi n'est pas encore affiché : rien n'est conclu.
        self.assertEqual(ad._refus_dans_la_reponse(self.page, sel, self.SCENE_1, [], "", minimum), "")
        # Il s'affiche, la réponse n'est pas encore là : toujours rien.
        self.page.set_content("<main>" + hier + nouveau + "</main>")
        self.assertEqual(ad._refus_dans_la_reponse(self.page, sel, self.SCENE_1, [], "", minimum), "")
        # Un NOUVEAU refus sous le nouvel envoi : celui-là compte, cité proprement.
        self.page.set_content("<main>" + hier + nouveau + """<div><h6>ChatGPT a dit :</h6>
<p>Vous avez épuisé votre quota d’images pour le moment, réessayez dans 3 heures.</p></div></main>
<footer><p>ChatGPT peut faire des erreurs.</p></footer>""")
        refus = ad._refus_dans_la_reponse(self.page, sel, self.SCENE_1, [], "", minimum)
        self.assertEqual(refus, "« Vous avez épuisé votre quota d’images pour le moment, réessayez dans 3 heures. »")
        self.assertEqual(comptes.delai_annonce(refus), timedelta(hours=3))
        self.page.set_content(PAGE_CONVERSATION)

    def test_grok_ne_relit_pas_le_refus_sous_l_ancien_message(self):
        from videroo.adapters import grok, selecteurs
        tests.reinitialiser()
        sel = selecteurs.charger()["grok"]
        ligne = 'Série "pets" — Épisode 7 — scène 8 :'
        ancien = f"<div><p>{ligne}</p><p>Durée : 10 s.</p></div><div><p>Vous avez atteint votre limite hebdomadaire.</p></div>"
        ctx = base.ContexteTache(projet_id=1, tache_id=1, cible_type="video", cible_id=1,
            libelle="", prompt="", dossier_projet=Path(tempfile.mkdtemp()),
            chemin_sortie=Path(tempfile.mkdtemp()) / "v.mp4",
            extras={"message_envoye": f"{ligne}\nDurée : 10 s."})
        ad = grok.AdaptateurGrok()
        self.page.set_content("<main>" + ancien + "</main>")
        ctx.extras["occurrences_avant"] = ad._occurrences(self.page, ligne)
        self.assertEqual(ctx.extras["occurrences_avant"], 1)
        self.assertEqual(ad._refus_dans_la_reponse(self.page, sel, ligne, ctx), "")
        self.page.set_content("<main>" + ancien + f"<div><p>{ligne}</p><p>Durée : 10 s.</p></div>"
                              "<div><p>Vous avez atteint votre limite hebdomadaire.</p></div></main>")
        refus = ad._refus_dans_la_reponse(self.page, sel, ligne, ctx)
        self.assertIn("Vous avez atteint votre limite hebdomadaire.", refus)
        self.assertNotIn("Durée", refus, "la suite de notre message n'est pas citée")
        self.page.set_content(PAGE_CONVERSATION)

    def test_marqueur_absent_rend_none(self):
        self.assertIsNone(nav.apres_notre_message(self.page, "phrase jamais envoyée", [], self.IMAGES))
        self.assertIsNone(nav.apres_notre_message(self.page, "   ", [], self.IMAGES))

    def test_le_refus_cite_passe_le_detecteur_et_donne_le_delai(self):
        from videroo.adapters import selecteurs
        r = nav.apres_notre_message(self.page, self.SCENE_1, [self.SCENE_2], self.IMAGES)
        cite = chatgpt.AdaptateurChatGPT._refus_dans(r["texte"], selecteurs.charger()["chatgpt"])
        self.assertIn("atteint la limite", cite)
        self.assertEqual(comptes.delai_annonce(cite), timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
