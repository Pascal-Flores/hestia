"""French number normalization: only the text sent to the TTS is transformed."""
import pytest

from hestia.speech import normalize_for_speech


@pytest.mark.parametrize(
    "source, expected",
    [
        ("42", "quarante-deux"),
        ("Il est 14:45.", "Il est quatorze heures quarante-cinq."),
        ("Rendez-vous à 1h05.", "Rendez-vous à une heure cinq."),
        ("Rendez-vous à 9h.", "Rendez-vous à neuf heures."),
        ("Il fait -3,5°C.", "Il fait moins trois virgule cinq degrés Celsius."),
        ("La température est de 1°C.", "La température est de un degré Celsius."),
        ("La somme est 12 345.", "La somme est douze mille trois cent quarante-cinq."),
        ("La consommation baisse de 20%.", "La consommation baisse de vingt pour cent."),
        ("Le 20/11/2026.", "Le vingt novembre deux mille vingt-six."),
        ("Le 2026-11-20.", "Le vingt novembre deux mille vingt-six."),
        ("Le 1er novembre.", "Le premier novembre."),
        ("La 2ème pièce.", "La deuxième pièce."),
        ("La 1ère fois.", "La première fois."),
        ("3,50 €", "trois euros et cinquante centimes"),
        ("1 €", "un euro"),
        ("3,05 litres", "trois virgule zéro cinq litres"),
        ("Plus de 1000 exemplaires.", "Plus de mille exemplaires."),
        ("0,75 %", "zéro virgule soixante-quinze pour cent"),
        ("La température : 20 °C.", "La température : vingt degrés Celsius."),
        ("bonjour", "bonjour"),
    ],
)
def test_fr_speech_numbers(source, expected):
    assert normalize_for_speech(source) == expected


def test_original_text_is_not_modified():
    source = "Le thermostat est à 19 degrés et l'énergie vaut 3,14 kWh."
    assert normalize_for_speech(source) == "Le thermostat est à dix-neuf degrés et l'énergie vaut trois virgule quatorze kWh."
    assert "19" in source
    assert "3,14" in source


def test_preserves_identifiers_and_website_urls():
    source = "Documentation : https://example.com/v2.1 ; mail contact42@example.com"
    assert normalize_for_speech(source) == source


def test_non_numeric_text_stays_same():
    assert normalize_for_speech("La porte est fermée.") == "La porte est fermée."
    with pytest.raises(TypeError):
        normalize_for_speech(42)
