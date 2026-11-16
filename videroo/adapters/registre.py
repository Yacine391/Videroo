"""Enregistrement des adaptateurs disponibles."""
from __future__ import annotations

from .. import runner
from .assiste import AdaptateurAssiste
from .chatgpt import AdaptateurChatGPT
from .demo import AdaptateurDemo
from .grok import AdaptateurGrok

MODES = {
    "assiste": "Assisté — l'application prépare la page et le prompt, tu lances et tu importes",
    "auto": "Automatique — l'application pilote le site (une seule génération par élément)",
    "demo": "Démonstration — fichiers factices marqués SIMULATION, aucun quota consommé",
}


def installer() -> None:
    runner.enregistrer_adaptateur("chatgpt", "assiste", AdaptateurAssiste("chatgpt"))
    runner.enregistrer_adaptateur("grok", "assiste", AdaptateurAssiste("grok"))
    runner.enregistrer_adaptateur("chatgpt", "auto", AdaptateurChatGPT())
    runner.enregistrer_adaptateur("grok", "auto", AdaptateurGrok())
    runner.enregistrer_adaptateur("chatgpt", "demo", AdaptateurDemo())
    runner.enregistrer_adaptateur("grok", "demo", AdaptateurDemo())
