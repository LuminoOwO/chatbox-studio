"""Languages the app offers: Whisper code, display name, NLLB-200 (FLORES) code."""

LANGUAGES = [
    ("en", "English", "eng_Latn"),
    ("de", "German", "deu_Latn"),
    ("fr", "French", "fra_Latn"),
    ("es", "Spanish", "spa_Latn"),
    ("it", "Italian", "ita_Latn"),
    ("pt", "Portuguese", "por_Latn"),
    ("nl", "Dutch", "nld_Latn"),
    ("pl", "Polish", "pol_Latn"),
    ("cs", "Czech", "ces_Latn"),
    ("sv", "Swedish", "swe_Latn"),
    ("da", "Danish", "dan_Latn"),
    ("no", "Norwegian", "nob_Latn"),
    ("fi", "Finnish", "fin_Latn"),
    ("hu", "Hungarian", "hun_Latn"),
    ("ro", "Romanian", "ron_Latn"),
    ("hr", "Croatian", "hrv_Latn"),
    ("bg", "Bulgarian", "bul_Cyrl"),
    ("el", "Greek", "ell_Grek"),
    ("ru", "Russian", "rus_Cyrl"),
    ("uk", "Ukrainian", "ukr_Cyrl"),
    ("tr", "Turkish", "tur_Latn"),
    ("ar", "Arabic", "arb_Arab"),
    ("he", "Hebrew", "heb_Hebr"),
    ("hi", "Hindi", "hin_Deva"),
    ("ja", "Japanese", "jpn_Jpan"),
    ("ko", "Korean", "kor_Hang"),
    ("zh", "Chinese (Simplified)", "zho_Hans"),
    ("th", "Thai", "tha_Thai"),
    ("vi", "Vietnamese", "vie_Latn"),
    ("id", "Indonesian", "ind_Latn"),
    ("ms", "Malay", "zsm_Latn"),
    ("tl", "Filipino", "tgl_Latn"),
    ("ca", "Catalan", "cat_Latn"),
    ("sk", "Slovak", "slk_Latn"),
    ("sl", "Slovenian", "slv_Latn"),
    ("sr", "Serbian", "srp_Cyrl"),
    ("lt", "Lithuanian", "lit_Latn"),
    ("lv", "Latvian", "lvs_Latn"),
    ("et", "Estonian", "est_Latn"),
    ("is", "Icelandic", "isl_Latn"),
    ("cy", "Welsh", "cym_Latn"),
    ("af", "Afrikaans", "afr_Latn"),
    ("fa", "Persian", "pes_Arab"),
    ("ur", "Urdu", "urd_Arab"),
    ("bn", "Bengali", "ben_Beng"),
    ("ta", "Tamil", "tam_Taml"),
    ("sw", "Swahili", "swh_Latn"),
]

# English first (the default), then all others alphabetically - no language is preferred.
LANGUAGES = LANGUAGES[:1] + sorted(LANGUAGES[1:], key=lambda x: x[1])

NAME = {code: name for code, name, _ in LANGUAGES}
NLLB = {code: flores for code, _, flores in LANGUAGES}


def name(code: str) -> str:
    if code == "room":
        return "the room language"
    return NAME.get(code, code)
