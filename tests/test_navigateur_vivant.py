"""Le navigateur ne doit pas se tirer dessus, ni mentir sur la panne.

Incident du 7 novembre 2026. Le ménage des « fenêtres restées ouvertes », lancé
au démarrage de la chaîne, a tué d'un SIGKILL le Chrome qui attendait justement
une image chez ChatGPT. Conséquences en cascade :

  06:24:14  prompt envoyé à ChatGPT, attente de l'image
  06:25:37  « 2 fenêtre(s) Chrome oubliée(s) fermée(s) avant de lancer »
  06:31:15  « Aucune image recuperee après 420 s »   ← contre une page morte
  06:32:33  « Navigateur dédié indisponible … Installe le navigateur »

Deux fautes distinctes, et deux réparations distinctes. Une fenêtre pilotée à cet
instant n'est pas une fenêtre oubliée. Et un navigateur fermé n'est pas un
navigateur absent : envoyer installer Playwright alors qu'il tourne depuis des
heures fait réparer ce qui n'est pas cassé.
"""
from __future__ import annotations

import unittest
from unittest import mock

import tests
from videroo import config
from videroo.adapters import navigateur


class ContexteMort:
    """Un contexte dont le Chrome a été tué sous les pieds."""

    def new_page(self):
        raise RuntimeError(
            "BrowserContext.new_page: Target page, context or browser has been closed"
        )


class PageFactice:
    def __init__(self):
        self.url = ""
        self.delai = None

    def set_default_timeout(self, delai):
        self.delai = delai

    def goto(self, url, **_):
        self.url = url


class ContexteVivant:
    def __init__(self):
        self.pages = []

    def new_page(self):
        page = PageFactice()
        self.pages.append(page)
        return page


class TestMenageQuiEpargneLeTravailEnCours(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    @staticmethod
    def _fenetres():
        return [
            {"pid": 111, "profil": config.chemin_profil("principal").name},
            {"pid": 222, "profil": "grok-dariya"},
        ]

    def test_le_profil_en_cours_nest_pas_tue(self):
        """Le profil « principal » vit dans « profil_navigateur ».

        Comparer les noms de profil aux noms de dossier ne protégerait jamais le
        profil principal : c'est pourtant celui qui travaillait.
        """
        with mock.patch.object(navigateur, "fenetres_restees_ouvertes", self._fenetres), \
             mock.patch.object(navigateur, "profils_en_cours", lambda: {"profil_navigateur"}), \
             mock.patch("os.kill") as tue:
            fermees = navigateur.fermer_fenetres_restees_ouvertes()
        vises = {appel.args[0] for appel in tue.call_args_list}
        self.assertEqual(fermees, 1)
        self.assertEqual(vises, {222}, "seule la fenêtre inutilisée doit être fermée")

    def test_le_sigkill_de_secours_ne_rattrape_pas_les_epargnees(self):
        """Le second passage tuait tout ce qui restait, protection comprise."""
        with mock.patch.object(navigateur, "fenetres_restees_ouvertes", self._fenetres), \
             mock.patch.object(navigateur, "profils_en_cours", lambda: {"profil_navigateur"}), \
             mock.patch("os.kill") as tue, mock.patch("time.sleep"):
            navigateur.fermer_fenetres_restees_ouvertes()
        self.assertNotIn(111, {appel.args[0] for appel in tue.call_args_list})

    def test_sans_rien_en_cours_tout_est_ferme(self):
        with mock.patch.object(navigateur, "fenetres_restees_ouvertes", self._fenetres), \
             mock.patch.object(navigateur, "profils_en_cours", set), \
             mock.patch("os.kill") as tue, mock.patch("time.sleep"):
            fermees = navigateur.fermer_fenetres_restees_ouvertes()
        self.assertEqual(fermees, 2)
        self.assertEqual({appel.args[0] for appel in tue.call_args_list}, {111, 222})

    def test_profils_en_cours_rend_des_noms_de_dossier(self):
        class SessionFactice:
            @staticmethod
            def profils_ouverts():
                return ["principal", "grok-yacine"]

        with mock.patch.object(navigateur, "sessions_ouvertes",
                               lambda: {"a": SessionFactice()}):
            self.assertEqual(
                navigateur.profils_en_cours(), {"profil_navigateur", "grok-yacine"}
            )


class TestReouvertureApresFermeture(unittest.TestCase):
    def setUp(self):
        tests.reinitialiser()

    def test_un_contexte_mort_est_rouvert_une_fois(self):
        """Sans ça, toutes les scènes suivantes échouent sur un objet fantôme."""
        session = navigateur.SessionNavigateur()
        session._contextes = {"principal": ContexteMort()}
        vivant = ContexteVivant()

        def _rouvrir(affichage=None, profil=None):
            session._contextes[profil or "principal"] = vivant

        session._ouvrir = _rouvrir
        page = session._page("chatgpt", "https://chatgpt.com/", "principal")
        self.assertIs(page, vivant.pages[0])
        self.assertEqual(page.url, "https://chatgpt.com/")
        self.assertEqual(session._contextes["principal"], vivant)

    def test_une_vraie_panne_nest_pas_maquillee_en_reouverture(self):
        """Rouvrir sur n'importe quelle erreur cacherait le vrai problème."""
        class ContexteCasse:
            def new_page(self):
                raise RuntimeError("Executable doesn't exist at /chrome")

        session = navigateur.SessionNavigateur()
        session._contextes = {"principal": ContexteCasse()}
        session._ouvrir = lambda *a, **k: self.fail("ne doit pas tenter de rouvrir")
        with self.assertRaises(RuntimeError):
            session._page("chatgpt", "https://chatgpt.com/", "principal")


class TestMessageHonnete(unittest.TestCase):
    """Dire ce qui s'est passé, pas ce qui serait commode à dire."""

    def test_navigateur_ferme_nenvoie_pas_installer(self):
        exc = RuntimeError(
            "BrowserContext.new_page: Target page, context or browser has been closed"
        )
        quoi, action = navigateur.raison_navigateur(exc)
        self.assertIn("fermé", quoi)
        self.assertNotIn("playwright install", action.lower())
        self.assertIn("Réessayer", action)

    def test_playwright_absent_envoie_bien_installer(self):
        with mock.patch.object(navigateur, "playwright_disponible",
                               lambda: (False, "Playwright n'est pas installé.")):
            quoi, action = navigateur.raison_navigateur(RuntimeError("peu importe"))
        self.assertIn("pas installé", quoi)
        self.assertIn("playwright install", action.lower())

    def test_une_panne_inconnue_reste_citee_telle_quelle(self):
        quoi, action = navigateur.raison_navigateur(RuntimeError("écran :99 introuvable"))
        self.assertEqual(quoi, "écran :99 introuvable")
        self.assertNotIn("playwright install", action.lower())

    def test_les_messages_reels_de_playwright_sont_reconnus(self):
        for message in (
            "BrowserContext.new_page: Target page, context or browser has been closed",
            "Page.goto: Target closed",
            "Browser closed",
            "Connection closed while reading from the driver",
        ):
            with self.subTest(message=message):
                self.assertTrue(navigateur._contexte_ferme(RuntimeError(message)))
        self.assertFalse(
            navigateur._contexte_ferme(RuntimeError("Timeout 45000ms exceeded"))
        )


if __name__ == "__main__":
    unittest.main()


class TestDiagnosticDeLancement(unittest.TestCase):
    """Le motif annoncé doit être le vrai, pas le dernier essayé.

    Chaque ouverture essaie le Chrome du système PUIS le chromium livré avec
    Playwright, qui n'est pas installé ici. C'est donc toujours lui qui parle en
    dernier : le 7 novembre 2026, un profil simplement déjà ouvert ailleurs
    ressortait en « le moteur de navigation manque », et la réparation proposée
    n'avait aucun rapport.
    """

    MOTEUR = "BrowserType.launch_persistent_context: Executable doesn't exist at /chrome"

    def test_le_vrai_motif_passe_devant_le_moteur_absent(self):
        vrai = RuntimeError("Timed out waiting for the browser to start")
        message = navigateur._pourquoi_pas_de_navigateur(
            "principal", [vrai, RuntimeError(self.MOTEUR)], RuntimeError(self.MOTEUR)
        )
        self.assertIn("Timed out", message)
        self.assertNotIn("playwright install", message)

    def test_moteur_vraiment_absent_le_dit_encore(self):
        echecs = [RuntimeError(self.MOTEUR), RuntimeError(self.MOTEUR)]
        message = navigateur._pourquoi_pas_de_navigateur("principal", echecs, echecs[-1])
        self.assertIn("playwright install", message)

    def test_un_profil_occupe_est_reconnu(self):
        for texte in ("ProcessSingleton", "SingletonLock", "profile is already in use"):
            with self.subTest(texte=texte):
                self.assertTrue(navigateur._profil_occupe(RuntimeError(texte)))


class PageQuiMent:
    """Une page dont le Chrome est mort, mais qui l'ignore encore.

    `is_closed()` est un drapeau local tenu par Playwright : il reste a False
    tant que le pilote n'a pas appris la mort du navigateur. C'est exactement ce
    qui s'est passe le 7 novembre 2026 a 13:55:38.
    """

    def __init__(self, url="https://chatgpt.com/"):
        self.url = url
        self.evaluations = 0

    @staticmethod
    def is_closed():
        return False

    def evaluate(self, _):
        self.evaluations += 1
        raise RuntimeError(
            "Page.evaluate: Target page, context or browser has been closed"
        )

    def set_default_timeout(self, _):
        pass

    def goto(self, url, **_):
        self.url = url


class TestPageMorteDetecteeAvantUsage(unittest.TestCase):
    """« Prete a 0 s » puis mort trois secondes plus tard : plus jamais."""

    def setUp(self):
        tests.reinitialiser()

    def _session_avec_page_morte(self):
        session = navigateur.SessionNavigateur()
        morte = PageQuiMent()
        session._contextes = {"principal": object()}
        # _page range les pages sous (profil, cle).
        session._pages = {("principal", "chatgpt"): morte}
        return session, morte

    def test_une_page_morte_est_remplacee_au_lieu_detre_rendue(self):
        session, morte = self._session_avec_page_morte()
        vivant = ContexteVivant()

        def _rouvrir(affichage=None, profil=None):
            session._contextes[profil or "principal"] = vivant

        session._ouvrir = _rouvrir
        page = session._page("chatgpt", "https://chatgpt.com/", "principal")
        self.assertIsNot(page, morte, "la page morte ne doit jamais être rendue")
        self.assertIs(page, vivant.pages[0])
        self.assertEqual(morte.evaluations, 1, "il faut un aller-retour réel")

    def test_une_page_vivante_est_rendue_sans_rouvrir(self):
        session = navigateur.SessionNavigateur()

        class PageVivante(PageQuiMent):
            def evaluate(self, _):
                self.evaluations += 1
                return 1

        vive = PageVivante()
        session._contextes = {"principal": ContexteVivant()}
        session._pages = {("principal", "chatgpt"): vive}
        session._ouvrir = lambda *a, **k: self.fail("ne doit pas rouvrir")
        page = session._page("chatgpt", "https://chatgpt.com/", "principal")
        self.assertIs(page, vive)
        self.assertEqual(vive.evaluations, 1)


class TestAttenteDuProfilLibre(unittest.TestCase):
    """Tuer un Chrome ne le fait pas disparaitre dans la seconde."""

    def test_on_attend_que_le_dossier_soit_rendu(self):
        restes = [
            [{"pid": 1, "profil": "chatgpt-jules-iidi"}],
            [{"pid": 1, "profil": "chatgpt-jules-iidi"}],
            [],
        ]

        def _fenetres():
            return restes.pop(0) if len(restes) > 1 else restes[0]

        with mock.patch.object(navigateur, "fenetres_restees_ouvertes", _fenetres), \
             mock.patch("time.sleep"):
            restants = navigateur._attendre_profils_libres({"chatgpt-jules-iidi"})
        self.assertEqual(restants, set())

    def test_un_chrome_tetu_est_signale_pas_ignore(self):
        with mock.patch.object(
            navigateur, "fenetres_restees_ouvertes",
            lambda: [{"pid": 1, "profil": "chatgpt-jules-iidi"}],
        ), mock.patch("time.sleep"), mock.patch("time.time", side_effect=[0, 1, 99, 99]):
            restants = navigateur._attendre_profils_libres({"chatgpt-jules-iidi"}, 8)
        self.assertEqual(restants, {"chatgpt-jules-iidi"})


class TestLeMenageNommeCeQuIlFerme(unittest.TestCase):
    """Des comptes ne disent pas QUI est tombé — et c'est ce qu'on cherche."""

    def setUp(self):
        tests.reinitialiser()

    def test_les_profils_fermes_et_epargnes_sont_nommes(self):
        from videroo import db

        fenetres = [
            {"pid": 111, "profil": "profil_navigateur"},
            {"pid": 222, "profil": "grok-dariya"},
        ]
        with mock.patch.object(navigateur, "fenetres_restees_ouvertes", lambda: fenetres), \
             mock.patch.object(navigateur, "profils_en_cours", lambda: {"profil_navigateur"}), \
             mock.patch.object(navigateur, "_attendre_profils_libres", lambda *a, **k: set()), \
             mock.patch("os.kill"):
            navigateur.fermer_fenetres_restees_ouvertes()
        dernier = db.q("SELECT message FROM journal ORDER BY id DESC LIMIT 1")[0]["message"]
        self.assertIn("grok-dariya", dernier, "il faut nommer la fenêtre fermée")
        self.assertIn("profil_navigateur", dernier, "il faut nommer l'épargnée")


class TestEcranPartage(unittest.TestCase):
    """16 novembre 2026 : fermer le Chrome de Grok a éteint l'écran virtuel, et le
    Chrome de ChatGPT, qui travaillait dessus, est mort avec lui."""

    def test_lecran_reste_allume_tant_quune_autre_session_lutilise(self):
        from unittest import mock

        from videroo.adapters import ecran, navigateur

        grok, chatgpt = navigateur.SessionNavigateur(), navigateur.SessionNavigateur()
        chatgpt._contextes["chatgpt-jules"] = object()
        grok._contextes["grok-dariya"] = mock.Mock()
        with mock.patch.dict(navigateur._sessions, {"grok": grok, "chatgpt": chatgpt}, clear=True), \
             mock.patch.object(ecran, "arreter") as arreter, \
             mock.patch.object(navigateur.db, "journaliser"):
            grok._fermer("grok-dariya")
            arreter.assert_not_called()
            chatgpt._contextes.clear()
            grok._contextes["grok-dariya"] = mock.Mock()
            grok._fermer("grok-dariya")
            arreter.assert_called_once()


class TestMenageAvecDeVraisChemins(unittest.TestCase):
    """16 novembre 2026 : /proc donne des CHEMINS complets ; la protection
    comparait des noms de dossier. Rien n'était protégé."""

    def test_le_chrome_qui_travaille_est_epargne(self):
        from unittest import mock

        from videroo import config
        from videroo.adapters import navigateur

        racine = str(config.DOSSIER_DONNEES.resolve())
        cmdlines = {
            "111": f"chrome\0--user-data-dir={racine}/profil_navigateur\0".encode(),
            "222": f"chrome\0--user-data-dir={racine}/profils/grok-dariya\0".encode(),
        }

        class Entree:
            def __init__(self, nom):
                self.name = nom

        reel_open = open

        def faux_open(chemin, mode="r", *a, **k):
            for pid, contenu in cmdlines.items():
                if chemin == f"/proc/{pid}/cmdline":
                    import io
                    return io.BytesIO(contenu)
            return reel_open(chemin, mode, *a, **k)

        with mock.patch("os.scandir", return_value=[Entree("111"), Entree("222")]), \
             mock.patch("builtins.open", faux_open):
            fenetres = navigateur.fenetres_restees_ouvertes()
        self.assertEqual({f["profil"] for f in fenetres}, {"profil_navigateur", "grok-dariya"})
        with mock.patch.object(navigateur, "fenetres_restees_ouvertes", return_value=fenetres), \
             mock.patch.object(navigateur, "profils_en_cours", lambda: {"profil_navigateur"}), \
             mock.patch("os.kill") as tue, mock.patch("time.sleep"), \
             mock.patch.object(navigateur, "_attendre_profils_libres", return_value=set()):
            navigateur.fermer_fenetres_restees_ouvertes()
        self.assertEqual({a.args[0] for a in tue.call_args_list}, {222})


class TestArretBorne(unittest.TestCase):
    """16 novembre 2026 : l'arrêt a attendu 15 min une attente Grok ; l'ancien
    serveur a continué d'agir sur la chaîne à côté du nouveau."""

    def test_une_session_occupee_ne_retient_pas_larret(self):
        import threading as th
        import time as tm
        from unittest import mock

        from videroo.adapters import ecran, navigateur

        bloque = th.Event()

        class Occupee:
            def fermer(self):
                bloque.wait(30)  # occupée par une longue attente

        debut = tm.monotonic()
        with mock.patch.dict(navigateur._sessions, {"grok": Occupee()}, clear=True), \
             mock.patch.object(navigateur, "fenetres_restees_ouvertes",
                               return_value=[{"pid": 4242, "profil": "principal"}]), \
             mock.patch("os.kill") as tue, mock.patch.object(ecran, "arreter"):
            navigateur.fermer_toutes(delai=0.5)
        bloque.set()
        self.assertLess(tm.monotonic() - debut, 5)
        tue.assert_called()

    def test_le_signal_darret_interrompt_lattente_video(self):
        from unittest import mock

        from videroo.adapters import grok, navigateur

        navigateur.ARRET.set()
        try:
            with mock.patch.object(grok, "_page_fermee", return_value=False):
                etat = grok.AdaptateurGrok()._attendre_video(object(), {}, set(), 600)
        finally:
            navigateur.ARRET.clear()
        self.assertEqual(etat, "arret")
