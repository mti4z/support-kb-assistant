"""Language helpers: English and Spanish.

The knowledge base has the same articles in both languages. A question is
answered from the articles of its own language, found with a simple word-based
detector (good enough for short support questions, and it has no dependencies).
"""
import re
import unicodedata

LANGS = ("en", "es")
TS_CONFIG = {"en": "english", "es": "spanish"}

_ES = set(
    "el la los las un una unos unas de del al que y en es por para con sin no se "
    "mi mis tu su sus me te lo le les como cual cuales cuando donde quien porque "
    "esta este esto estos estan estoy tengo tiene tienen puedo puede pueden quiero "
    "necesito hay muy mas pero si ya hago hacer cuanto cuantos cuantas mucho nada "
    "todo todos desde hasta sobre entre ni solo tambien aca alli ahi cuando "
    "olvide perdi borre recibi cambie conecto entro dejo soy uso usar".split()
)
_EN = set(
    "the a an of to in on for and or is are was were be do does did can could my "
    "your our his her its their i me you we they it this that these those with "
    "without not no how what where when why who which have has had will would "
    "should keep keeps from at by as if so but just get got cannot cant dont "
    "doesnt wont isnt arent there here some any all up out off about into".split()
)


def strip_accents(text: str) -> str:
    """"Conexión" -> "Conexion". People often type Spanish without accents."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def detect_language(text: str) -> str:
    """Return "es" or "en". Ties and unknown text count as English."""
    raw = text.lower()
    score = 2 * len(re.findall(r"[¿¡ñ]", raw))  # ¿ ¡ ñ
    words = re.findall(r"[a-zà-ÿ]+", raw)
    for w in words:
        plain = strip_accents(w)
        if plain in _ES and plain not in _EN:
            score += 1
        if w in _EN and plain not in _ES:
            score -= 1
    # accented vowels are a strong Spanish signal in short questions
    score += len(re.findall(r"[áéíóú]", raw))
    return "es" if score > 0 else "en"
