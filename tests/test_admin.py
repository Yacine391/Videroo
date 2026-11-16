"""Espace admin : réglages persistés, comptes, thread unique du navigateur."""
import threading
import unittest

import tests
from videroo import comptes, config, db, reglages, services
from videroo.adapters import navigateur


class TestReglages(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def test_valeurs_par_defaut(self):
        self.assertEqual(reglages.lire("mode_defaut"), "auto")
        self.assertEqual(reglages.durees_grok(), (6, 10, 15))
        self.assertGreater(reglages.lire_int("delai_video"), 0)
        self.assertEqual(
            reglages.affichage_navigateur(),
            "ecran_virtuel",
            "par défaut aucune fenêtre ne doit apparaître sur le bureau",
        )

    def test_enregistrement_et_relecture(self):
        reglages.definir("delai_image", "600")
        self.assertEqual(reglages.lire_int("delai_image"), 600)
        reglages.definir("navigateur_affichage", "fenetre")
        self.assertEqual(reglages.affichage_navigateur(), "fenetre")
        reglages.definir("export_preset", "slow")
        self.assertEqual(reglages.lire("export_preset"), "slow")

    def test_valeurs_refusees(self):
        for cle, mauvaise in [
            ("delai_image", "abc"),
            ("delai_image", "5"),          # sous le minimum
            ("export_preset", "turbo"),    # hors des choix
            ("durees_grok", "6,10,x"),
            ("url_chatgpt", "chatgpt.com"),  # sans https
        ]:
            with self.assertRaises(reglages.ValeurRefusee, msg=f"{cle}={mauvaise}"):
                reglages.definir(cle, mauvaise)

    def test_durees_grok_pilotent_le_controle(self):
        from videroo import runner

        projet_id = services.creer_projet("Durées")
        services.appliquer_pack(
            projet_id, services.analyser_pack(tests.pack_variantes())
        )
        services.valider_projet(projet_id)
        scene = db.q1(
            "SELECT * FROM scenes WHERE projet_id=? AND numero=1", (projet_id,)
        )
        db.exec1("UPDATE scenes SET duree=8 WHERE id=?", (scene["id"],))
        # 8 s n'est pas accepte par defaut
        self.assertNotIn(8, reglages.durees_grok())
        reglages.definir("durees_grok", "6,8,10,15")
        self.assertIn(8, reglages.durees_grok())

    def test_reinitialisation(self):
        reglages.definir("delai_image", "600")
        reglages.reinitialiser()
        self.assertEqual(
            reglages.lire_int("delai_image"), reglages.PAR_CLE["delai_image"]["defaut"]
        )

    def test_affichage_des_champs(self):
        champs = reglages.tous()
        self.assertEqual(len(champs), len(reglages.CHAMPS))
        self.assertTrue(all("valeur" in c and "groupe" in c for c in champs))
        self.assertTrue(all(c["groupe"] in reglages.GROUPES for c in champs))
        self.assertTrue(all(c["par_defaut"] for c in champs), "tout doit être par défaut")
        reglages.definir("delai_image", "600")
        modifie = {c["cle"]: c["par_defaut"] for c in reglages.tous()}
        self.assertFalse(modifie["delai_image"])


class TestComptes(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        comptes.assurer_comptes_par_defaut()

    def test_sites_connus(self):
        self.assertEqual(set(comptes.SITES), {"chatgpt", "grok"})

    def test_adresse_par_defaut_puis_personnalisee(self):
        self.assertIn("chatgpt", comptes.url_site("chatgpt"))
        reglages.definir("url_chatgpt", "https://chatgpt.com/g/mon-chat")
        self.assertEqual(comptes.url_site("chatgpt"), "https://chatgpt.com/g/mon-chat")

    def test_etat_inconnu_avant_verification(self):
        c = comptes.liste("grok")[0]
        self.assertEqual(c["etat"], "")
        self.assertEqual(c["libelle_etat"], "non vérifié")
        self.assertIsNone(c["verifie_le"])

    def test_tableau_groupe_par_site(self):
        tableau = comptes.tableau()
        self.assertEqual(set(tableau), {"chatgpt", "grok"})
        for lignes in tableau.values():
            self.assertTrue(lignes)
            for ligne in lignes:
                for clef in ("id", "libelle", "profil", "etat", "quota_dispo"):
                    self.assertIn(clef, ligne)

    def test_compte_inconnu_refuse(self):
        with self.assertRaises(comptes.ErreurCompte):
            comptes.compte(9999)
        with self.assertRaises(comptes.ErreurCompte):
            comptes.ajouter("midjourney", "Test")



class TestThreadNavigateur(unittest.TestCase):
    """Playwright refuse le partage entre threads : tout passe par un seul thread."""

    def setUp(self):
        tests.reinitialiser()

    def test_toutes_les_operations_sur_le_meme_thread(self):
        session = navigateur.session()
        noms = set()

        def releve():
            return threading.current_thread().name

        noms.add(session.executer(releve))
        resultats: list[str] = []
        fils = [
            threading.Thread(target=lambda: resultats.append(session.executer(releve)))
            for _ in range(5)
        ]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        noms.update(resultats)
        self.assertEqual(len(noms), 1, f"un seul thread attendu, vu : {noms}")
        self.assertEqual(next(iter(noms)), "videroo-navigateur")

    def test_les_erreurs_remontent_a_l_appelant(self):
        session = navigateur.session()

        def casse():
            raise ValueError("boum")

        with self.assertRaises(ValueError):
            session.executer(casse)
        # la boucle survit a l'erreur
        self.assertEqual(session.executer(lambda: 42), 42)

    def test_page_refusee_hors_du_thread(self):
        with self.assertRaises(RuntimeError):
            navigateur.session().page("chatgpt", "https://example.com")

    def test_etat_lisible_sans_navigateur_ouvert(self):
        etat = navigateur.session().etat()
        self.assertIn("playwright", etat)
        self.assertIn("profil", etat)
        self.assertFalse(etat["occupe"])


if __name__ == "__main__":
    unittest.main()


class TestFormulaireReglages(unittest.TestCase):
    """Un envoi partiel ne doit pas éteindre une case à cocher absente."""

    def setUp(self):
        tests.reinitialiser()

    def test_case_absente_du_formulaire_non_modifiee(self):
        from fastapi.testclient import TestClient

        from videroo.app import app

        with TestClient(app) as client:
            reglages.definir("exiger_reglage_duree", "1")
            client.post("/admin/reglages", data={"delai_image": "300"})
            self.assertTrue(
                reglages.lire_bool("exiger_reglage_duree"),
                "sans témoin, la case reste à sa valeur",
            )
            self.assertEqual(reglages.lire_int("delai_image"), 300)
            client.post(
                "/admin/reglages",
                data={"exiger_reglage_duree__present": "1"},
            )
            self.assertFalse(
                reglages.lire_bool("exiger_reglage_duree"), "décochée avec témoin"
            )


class TestDetectionConnexion(unittest.TestCase):
    """La zone de saisie prime : un « Sign in » caché ne doit pas tromper."""

    def setUp(self):
        tests.reinitialiser()

    def _sonder_avec(self, url, zone_saisie_visible, signe_connexion_visible):
        from unittest import mock

        class PageFactice:
            def __init__(self, adresse):
                self.url = adresse

            def wait_for_load_state(self, *a, **kw):
                pass

            def wait_for_timeout(self, *a, **kw):
                pass

        def present_factice(page, selecteurs, timeout=0):
            sel = selecteurs or []
            if not sel:
                return None
            cle = sel[0]
            if "contenteditable" in str(sel) or "textarea" in str(sel) or "prompt" in str(cle):
                return cle if zone_saisie_visible else None
            return cle if signe_connexion_visible else None

        with mock.patch.object(
            comptes.navigateur, "present", side_effect=present_factice
        ), mock.patch.object(
            comptes.navigateur, "session"
        ) as fausse_session:
            fausse_session.return_value.page.return_value = PageFactice(url)
            return comptes._sonder("grok", "principal")

    def test_saisie_et_connexion_ensemble_ne_tranchent_pas(self):
        """Mesuré sur grok.com : la page est identique connectée ou non."""
        resultat = self._sonder_avec("https://grok.com/", True, True)
        self.assertEqual(resultat["etat"], "incertain")

    def test_sans_zone_de_saisie_et_avec_ecran_de_connexion(self):
        resultat = self._sonder_avec("https://grok.com/", False, True)
        self.assertEqual(resultat["etat"], "deconnecte")

    def test_adresse_de_connexion_tranche_immediatement(self):
        resultat = self._sonder_avec("https://accounts.x.ai/sign-in", True, False)
        self.assertEqual(resultat["etat"], "deconnecte")
        self.assertIn("redirigé", resultat["detail"])

    def test_ni_l_un_ni_l_autre_reste_incertain(self):
        resultat = self._sonder_avec("https://grok.com/", False, False)
        self.assertEqual(resultat["etat"], "incertain")

    def test_ouvrir_pour_connexion_efface_l_etat_perime(self):
        from unittest import mock

        comptes.assurer_comptes_par_defaut()
        compte_id = comptes.liste("grok")[0]["id"]
        comptes.marquer(compte_id, "deconnecte")
        with mock.patch.object(comptes.navigateur, "session") as fausse:
            fausse.return_value.executer.return_value = "https://grok.com/"
            comptes.ouvrir_pour_connexion(compte_id)
        self.assertEqual(
            comptes.compte(compte_id)["etat"],
            "",
            "l'ancien état ne doit plus s'afficher après réouverture",
        )


class TestDetectionAmbigue(unittest.TestCase):
    """Un site identique connecté ou non doit donner « à confirmer », pas une certitude."""

    def setUp(self):
        tests.reinitialiser()

    def _sonder(self, zone_saisie, signe_connexion, url="https://grok.com/"):
        from unittest import mock

        class PageFactice:
            def __init__(self, adresse):
                self.url = adresse

            def wait_for_load_state(self, *a, **kw):
                pass

            def wait_for_timeout(self, *a, **kw):
                pass

        def present_factice(page, selecteurs, timeout=0):
            sel = selecteurs or []
            if not sel:
                return None
            est_saisie = any(
                mot in str(sel) for mot in ("contenteditable", "textarea", "prompt")
            )
            if est_saisie:
                return sel[0] if zone_saisie else None
            return sel[0] if signe_connexion else None

        with mock.patch.object(
            comptes.navigateur, "present", side_effect=present_factice
        ), mock.patch.object(comptes.navigateur, "session") as fausse:
            fausse.return_value.page.return_value = PageFactice(url)
            return comptes._sonder("grok", "principal")

    def test_saisie_et_connexion_ensemble_donnent_a_confirmer(self):
        resultat = self._sonder(zone_saisie=True, signe_connexion=True)
        self.assertEqual(resultat["etat"], "incertain")
        self.assertIn("pareil connecté ou non", resultat["detail"])

    def test_saisie_seule_vaut_connecte(self):
        self.assertEqual(
            self._sonder(zone_saisie=True, signe_connexion=False)["etat"], "connecte"
        )

    def test_connexion_seule_vaut_deconnecte(self):
        self.assertEqual(
            self._sonder(zone_saisie=False, signe_connexion=True)["etat"], "deconnecte"
        )

    def test_rien_du_tout_donne_a_confirmer(self):
        self.assertEqual(
            self._sonder(zone_saisie=False, signe_connexion=False)["etat"], "incertain"
        )

    def test_adresse_de_connexion_prime(self):
        resultat = self._sonder(
            zone_saisie=True, signe_connexion=False, url="https://accounts.x.ai/sign-in"
        )
        self.assertEqual(resultat["etat"], "deconnecte")


class TestConfirmationManuelle(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()
        comptes.assurer_comptes_par_defaut()
        self.chatgpt = comptes.liste("chatgpt")[0]["id"]
        self.grok = comptes.liste("grok")[0]["id"]

    def test_marquer_connecte(self):
        resultat = comptes.marquer(self.chatgpt, "connecte")
        self.assertEqual(resultat["etat"], "connecte")
        self.assertEqual(resultat["etat_source"], "manuel")
        self.assertEqual(resultat["libelle_etat"], "connecté")

    def test_marquer_deconnecte(self):
        comptes.marquer(self.grok, "connecte")
        comptes.marquer(self.grok, "deconnecte")
        self.assertEqual(comptes.compte(self.grok)["etat"], "deconnecte")

    def test_etat_refuse(self):
        with self.assertRaises(comptes.ErreurCompte):
            comptes.marquer(self.grok, "peut-etre")
        with self.assertRaises(comptes.ErreurCompte):
            comptes.marquer(9999, "connecte")

    def test_la_confirmation_est_visible_dans_le_tableau(self):
        comptes.marquer(self.grok, "connecte")
        ligne = comptes.tableau()["grok"][0]
        self.assertEqual(ligne["etat_source"], "manuel")
        self.assertEqual(ligne["libelle_etat"], "connecté")


class TestModeNavigateur(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def test_deux_modes_proposes(self):
        champ = reglages.PAR_CLE["navigateur_mode"]
        valeurs = [c[0] for c in champ["choix"]]
        self.assertEqual(valeurs, ["profil_dedie", "chrome_existant"])
        self.assertEqual(champ["defaut"], "profil_dedie")

    def test_adresse_de_debogage_reglable(self):
        self.assertEqual(reglages.lire("cdp_url"), "http://127.0.0.1:9222")
        reglages.definir("cdp_url", "http://127.0.0.1:9333")
        self.assertEqual(reglages.lire("cdp_url"), "http://127.0.0.1:9333")

    def test_commande_chrome_affichee(self):
        self.assertIn("--remote-debugging-port=9222", navigateur.COMMANDE_CHROME)
        self.assertIn("commande_chrome", navigateur.session().etat())

    def test_branchement_impossible_explique_quoi_faire(self):
        reglages.definir("navigateur_mode", "chrome_existant")
        reglages.definir("cdp_url", "http://127.0.0.1:9")
        session = navigateur.SessionNavigateur()
        with self.assertRaises(navigateur.NavigateurIndisponible) as ctx:
            session.ouvrir(affichage="hors_ecran")
        message = str(ctx.exception)
        self.assertIn("Ferme complètement Chrome", message)
        self.assertIn("--remote-debugging-port=9222", message)
        reglages.definir("navigateur_mode", "profil_dedie")


class TestFenetresRestees(unittest.TestCase):
    """Un arrêt brutal laisse des Chrome qui verrouillent les profils."""

    def setUp(self):
        tests.reinitialiser()

    def test_ne_voit_que_nos_profils(self):
        from unittest import mock

        racine = str(config.DOSSIER_DONNEES.resolve())
        faux_procs = {
            "100": f"chrome\x00--user-data-dir={racine}/profils/grok-yacine\x00",
            "101": "chrome\x00--user-data-dir=/home/yacine/.config/google-chrome\x00",
            "102": "python\x00-m\x00uvicorn\x00",
        }

        class Entree:
            def __init__(self, nom):
                self.name = nom

        import builtins

        vrai_open = builtins.open

        def faux_open(chemin, *a, **kw):
            texte = str(chemin)
            if texte.startswith("/proc/") and texte.endswith("/cmdline"):
                pid = texte.split("/")[2]
                if pid not in faux_procs:
                    raise OSError("disparu")
                import io

                return io.BytesIO(faux_procs[pid].encode())
            return vrai_open(chemin, *a, **kw)

        with mock.patch("os.scandir", return_value=[Entree(p) for p in faux_procs]), \
                mock.patch("builtins.open", faux_open):
            trouves = navigateur.fenetres_restees_ouvertes()
        self.assertEqual(len(trouves), 1, "seul le Chrome de Videroo doit être vu")
        self.assertEqual(trouves[0]["pid"], 100)
        self.assertIn("grok-yacine", trouves[0]["profil"])

    def test_rien_a_fermer_ne_casse_pas(self):
        from unittest import mock

        with mock.patch.object(navigateur, "fenetres_restees_ouvertes", return_value=[]):
            self.assertEqual(navigateur.fermer_fenetres_restees_ouvertes(), 0)

    def test_profil_occupe_reconnu(self):
        self.assertTrue(
            navigateur._profil_occupe(
                RuntimeError("ProcessSingleton: profile is already in use")
            )
        )
        self.assertFalse(navigateur._profil_occupe(RuntimeError("autre chose")))


class TestModeInvisible(unittest.TestCase):
    """Les générations tournent en arrière-plan ; la fenêtre ne sert qu'à se connecter."""

    def setUp(self):
        tests.reinitialiser()

    def test_invisible_par_defaut(self):
        self.assertFalse(
            reglages.lire_bool("navigateur_visible"),
            "aucune fenêtre ne doit s'ouvrir pendant les générations",
        )

    def test_hors_ecran_par_defaut_lance_chrome_normalement(self):
        """Chrome normal, mais placé loin de l'écran : Cloudflare l'accepte."""
        from unittest import mock

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
        premier = appels[0]
        self.assertFalse(premier["headless"], "pas de headless : Cloudflare le repère")
        self.assertTrue(any("window-position" in a for a in premier["args"]))

    def test_le_mode_invisible_utilise_le_chrome_installe(self):
        """--headless=new évite de télécharger un binaire séparé."""
        from unittest import mock

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
                session._ouvrir(affichage="invisible", profil="essai")
        premier = appels[0]
        self.assertFalse(premier["headless"], "on garde le binaire Chrome")
        self.assertIn("--headless=new", premier["args"])
        # repli automatique sur hors écran, que Cloudflare accepte
        self.assertTrue(
            any("window-position" in a for a in appels[-1]["args"]),
            "si le sans-fenêtre échoue, on doit retomber hors écran",
        )

    def test_mode_visible_n_ajoute_pas_le_drapeau(self):
        from unittest import mock

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
                session._ouvrir(affichage="fenetre", profil="essai")
        self.assertNotIn("--headless=new", appels[0]["args"])
        self.assertFalse(appels[0]["headless"])
        self.assertFalse(any("window-position" in a for a in appels[0]["args"]))


class TestEnvoiVerifie(unittest.TestCase):
    """Un prompt tapé mais non parti doit être détecté tout de suite."""

    def setUp(self):
        tests.reinitialiser()

    def _page(self, vide_apres_clic=True, vide_apres_entree=True, bouton=True):
        essai = self

        class Element:
            def __init__(self, nom):
                self.nom = nom

            def wait_for(self, **kw):
                if self.nom == "bouton" and not bouton:
                    raise RuntimeError("absent")

            def is_visible(self, **kw):
                return not (self.nom == "bouton" and not bouton)

            def click(self, **kw):
                if self.nom == "bouton":
                    essai.clics += 1
                    if vide_apres_clic:
                        essai.contenu = ""

            def input_value(self, **kw):
                return essai.contenu

            def inner_text(self, **kw):
                return essai.contenu

            @property
            def first(self):
                return self

        class Clavier:
            def press(self, touche):
                essai.touches.append(touche)
                if touche == "Enter" and vide_apres_entree:
                    essai.contenu = ""

        class Page:
            keyboard = Clavier()
            url = "https://chatgpt.com/"

            def locator(self, selecteur):
                return Element("bouton" if "button" in selecteur else "zone")

            def wait_for_timeout(self, ms):
                pass

        self.contenu = "mon prompt"
        self.clics = 0
        self.touches = []
        return Page()

    ZONES = ["div[contenteditable='true']"]
    BOUTONS = ["button[data-testid='send-button']"]

    def test_envoi_par_bouton_confirme(self):
        page = self._page(vide_apres_clic=True)
        parti, comment = navigateur.envoyer(page, self.ZONES, self.BOUTONS)
        self.assertTrue(parti)
        self.assertEqual(comment, "bouton d'envoi")
        self.assertEqual(self.touches, [], "pas besoin de la touche Entrée")

    def test_repli_sur_entree_quand_le_bouton_ne_fait_rien(self):
        page = self._page(vide_apres_clic=False, vide_apres_entree=True)
        parti, comment = navigateur.envoyer(page, self.ZONES, self.BOUTONS)
        self.assertTrue(parti, "c'est exactement le cas observé : clic sans effet")
        self.assertEqual(comment, "touche Entrée")
        self.assertIn("Enter", self.touches)

    def test_echec_franc_si_rien_ne_part(self):
        page = self._page(vide_apres_clic=False, vide_apres_entree=False)
        parti, comment = navigateur.envoyer(page, self.ZONES, self.BOUTONS)
        self.assertFalse(parti, "mieux vaut le dire que d'attendre une image fantôme")
        self.assertEqual(comment, "")

    def test_sans_bouton_on_tente_quand_meme_entree(self):
        page = self._page(bouton=False, vide_apres_entree=True)
        parti, comment = navigateur.envoyer(page, self.ZONES, self.BOUTONS)
        self.assertTrue(parti)
        self.assertEqual(comment, "touche Entrée")

    def test_lecture_de_la_zone(self):
        page = self._page()
        self.assertEqual(navigateur.texte_zone(page, self.ZONES), "mon prompt")


class TestRechercheRapide(unittest.TestCase):
    """Un sélecteur obsolète ne doit pas coûter son délai complet."""

    def setUp(self):
        tests.reinitialiser()

    def _page(self, qui_marche):
        class Element:
            def __init__(self, nom):
                self.nom = nom

            def is_visible(self):
                return self.nom == qui_marche

            @property
            def first(self):
                return self

        class Page:
            def locator(self, s):
                return Element(s)

            def wait_for_timeout(self, ms):
                pass

        return Page()

    def test_le_dernier_selecteur_est_trouve_tout_de_suite(self):
        import time

        selecteurs = ["#obsolete-1", "#obsolete-2", "#obsolete-3", "div.ProseMirror"]
        debut = time.monotonic()
        element = navigateur.premier_visible(
            self._page("div.ProseMirror"), selecteurs, timeout=20000
        )
        duree = time.monotonic() - debut
        self.assertIsNotNone(element)
        self.assertLess(
            duree, 1.0, "les sélecteurs morts ne doivent pas coûter leur délai"
        )

    def test_present_renvoie_le_selecteur_qui_a_marche(self):
        trouve = navigateur.present(
            self._page("#bon"), ["#mauvais", "#bon"], timeout=2000
        )
        self.assertEqual(trouve, "#bon")

    def test_aucun_selecteur_valide_respecte_la_limite(self):
        import time

        debut = time.monotonic()
        self.assertIsNone(
            navigateur.premier_visible(self._page("#rien"), ["#a", "#b"], timeout=1000)
        )
        self.assertLess(time.monotonic() - debut, 3.0)

    def test_ordre_des_selecteurs_chatgpt(self):
        from videroo.adapters import selecteurs as sel_mod

        zone = sel_mod.DEFAUT["chatgpt"]["zone_saisie"]
        self.assertEqual(
            zone[0],
            "div.ProseMirror[contenteditable='true']",
            "le sélecteur vérifié sur le site doit passer en premier",
        )
        bouton = sel_mod.DEFAUT["chatgpt"]["bouton_envoyer"]
        self.assertIn("Envoyer", bouton[0])
