#!/usr/bin/env python3
"""
Generador de Archivos Optimizados para Audio y NotebookLM / IAs
===============================================================
Lee la fuente verificada (Guia_Estudio_Precursores_Agente.epub + RefPublication de rsg_S.db)
y genera:
  1. 01_Fuentes_Limpias_Para_NotebookLM/ (36 archivos .txt por lección + partes para lecciones muy largas)
     - 0 % etiquetas XML/HTML, 0 % jerga técnica de base de datos ('dossier', 'it_S', 'rsg_S', 'nwtsty_S',
       'pt14_S', 'Sin extractos...', 'Cobertura del Índice...', '[Pág. X]'), nombres bíblicos y de
       publicaciones expandidos al español natural.
  2. 02_Guiones_Maestro_Alumno_MultiVoz/ (36 guiones completos Maestro / Alumno en .txt)
     - Introducción del tema por el Maestro (Testigo de Jehová) -> Alumno formula las preguntas de
       cada lección en orden exacto -> Maestro da la respuesta directa y explica el porqué con los
       textos bíblicos y referencias descargadas. Sin información externa ni inventada.
  3. 03_Partes_Para_Google_AI_Studio/ (Guiones Maestro / Alumno ya divididos en bloques <= 6,400 caracteres)
     - Cortados únicamente entre preguntas completas para pegar directo en https://aistudio.google.com/generate-speech
       (modo Multi-speaker) sin alcanzar el límite de 8,192 tokens de entrada ni el tope de ~10 min de audio.
  4. Guia_Estudio_Maestro_Alumno_Audio.epub
     - EPUB 3 limpio y listo para escuchar de corrido en ElevenReader (iOS/Android), Apple Books o Edge.
  5. 00_GUIA_Y_PROMPTS_EXACTOS.txt (incluido en las carpetas y el ZIP)
"""

import datetime
import html as html_mod
import os
import re
import shutil
import sqlite3
import unicodedata
import uuid
import zipfile
from html.parser import HTMLParser

EPUB_IN = "/home/antigravity/Guia_Estudio_Precursores_Agente.epub"
RSG_DB = "/tmp/dbs/rsg_S.db"
OUT_ROOT = "/home/antigravity/pt14_S_audio_estudio"
AUDIO_EPUB_OUT = "/home/antigravity/Guia_Estudio_Maestro_Alumno_Audio.epub"

# Mapa completo de abreviaturas bíblicas en español -> nombre completo para lectura/voz natural
# Nota: para "Sal." / "Sl" usamos "Salmo" antes de N:M (ej. "Salmo 77, versículo 12") que suena más natural al hablar.
BIBLE_BOOK_ABBR = {
    "gén": "Génesis", "gé": "Génesis", "gn": "Génesis", "génesis": "Génesis",
    "éx": "Éxodo", "éxo": "Éxodo", "ex": "Éxodo", "éxodo": "Éxodo",
    "lev": "Levítico", "le": "Levítico", "lv": "Levítico", "levítico": "Levítico",
    "núm": "Números", "nú": "Números", "nm": "Números", "números": "Números",
    "deut": "Deuteronomio", "dt": "Deuteronomio", "deuteronomio": "Deuteronomio",
    "jos": "Josué", "josué": "Josué",
    "juec": "Jueces", "jue": "Jueces", "jueces": "Jueces",
    "rut": "Rut",
    "1 sam": "1 Samuel", "1sa": "1 Samuel", "1 samuel": "1 Samuel",
    "2 sam": "2 Samuel", "2sa": "2 Samuel", "2 samuel": "2 Samuel",
    "1 rey": "1 Reyes", "1re": "1 Reyes", "1 reyes": "1 Reyes",
    "2 rey": "2 Reyes", "2re": "2 Reyes", "2 reyes": "2 Reyes",
    "1 crón": "1 Crónicas", "1cr": "1 Crónicas", "1 crónicas": "1 Crónicas",
    "2 crón": "2 Crónicas", "2cr": "2 Crónicas", "2 crónicas": "2 Crónicas",
    "esd": "Esdras", "esdras": "Esdras",
    "neh": "Nehemías", "ne": "Nehemías", "nehemías": "Nehemías",
    "est": "Ester", "ester": "Ester",
    "job": "Job",
    "sal": "Salmo", "sl": "Salmo", "salmo": "Salmo", "salmos": "Salmos",
    "prov": "Proverbios", "pro": "Proverbios", "pr": "Proverbios", "proverbios": "Proverbios",
    "ecl": "Eclesiastés", "ec": "Eclesiastés", "eclesiastés": "Eclesiastés",
    "cant": "El Cantar de los Cantares",
    "is": "Isaías", "isa": "Isaías", "isaías": "Isaías",
    "jer": "Jeremías", "jeremías": "Jeremías",
    "lam": "Lamentaciones", "lamentaciones": "Lamentaciones",
    "ezeq": "Ezequiel", "eze": "Ezequiel", "ez": "Ezequiel", "ezequiel": "Ezequiel",
    "dan": "Daniel", "da": "Daniel", "daniel": "Daniel",
    "os": "Oseas", "oseas": "Oseas",
    "joel": "Joel",
    "am": "Amós", "amós": "Amós",
    "abd": "Abdías", "abdías": "Abdías",
    "jon": "Jonás", "jonás": "Jonás",
    "miq": "Miqueas", "miqueas": "Miqueas",
    "nah": "Nahúm", "nahúm": "Nahúm",
    "hab": "Habacuc", "habacuc": "Habacuc",
    "sof": "Sofonías", "sofonías": "Sofonías",
    "ag": "Ageo", "ageo": "Ageo",
    "zac": "Zacarías", "zacarías": "Zacarías",
    "mal": "Malaquías", "malaquías": "Malaquías", "malaquias": "Malaquías",
    "mat": "Mateo", "mt": "Mateo", "mateo": "Mateo",
    "mar": "Marcos", "mr": "Marcos", "marcos": "Marcos",
    "luc": "Lucas", "lu": "Lucas", "lc": "Lucas", "lucas": "Lucas",
    "jn": "Juan", "juan": "Juan",
    "hech": "Hechos", "hch": "Hechos", "hechos": "Hechos",
    "rom": "Romanos", "ro": "Romanos", "romanos": "Romanos",
    "1 cor": "1 Corintios", "1co": "1 Corintios", "1 corintios": "1 Corintios",
    "2 cor": "2 Corintios", "2co": "2 Corintios", "2 corintios": "2 Corintios",
    "gál": "Gálatas", "gal": "Gálatas", "gálatas": "Gálatas",
    "efes": "Efesios", "efe": "Efesios", "ef": "Efesios", "efesios": "Efesios",
    "filip": "Filipenses", "fili": "Filipenses", "flp": "Filipenses", "filipenses": "Filipenses",
    "col": "Colosenses", "colosenses": "Colosenses",
    "1 tes": "1 Tesalonicenses", "1te": "1 Tesalonicenses", "1 tesalonicenses": "1 Tesalonicenses",
    "2 tes": "2 Tesalonicenses", "2te": "2 Tesalonicenses", "2 tesalonicenses": "2 Tesalonicenses",
    "1 tim": "1 Timoteo", "1ti": "1 Timoteo", "1 timoteo": "1 Timoteo",
    "2 tim": "2 Timoteo", "2ti": "2 Timoteo", "2 timoteo": "2 Timoteo",
    "tit": "Tito", "tito": "Tito",
    "flm": "Filemón", "filemón": "Filemón",
    "heb": "Hebreos", "hebreos": "Hebreos",
    "sant": "Santiago", "snt": "Santiago", "stgo": "Santiago", "santiago": "Santiago",
    "1 ped": "1 Pedro", "1pe": "1 Pedro", "1 pedro": "1 Pedro",
    "2 ped": "2 Pedro", "2pe": "2 Pedro", "2 pedro": "2 Pedro",
    "1 juan": "1 Juan", "1jn": "1 Juan",
    "2 juan": "2 Juan", "2jn": "2 Juan",
    "3 juan": "3 Juan", "3jn": "3 Juan",
    "jud": "Judas", "judas": "Judas",
    "apoc": "Apocalipsis", "ap": "Apocalipsis", "rev": "Apocalipsis", "apocalipsis": "Apocalipsis",
    "1 macabeos": "1 Macabeos",
}

MONTHS_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}

# Mapa fijo de publicaciones que aparecen en pt14_S / rsg_S / it_S
PUB_TITLES = {
    "ba": "el folleto Un libro para todo el mundo",
    "be": "el libro Benefíciese de la Escuela del Ministerio Teocrático",
    "bh": "el libro ¿Qué enseña realmente la Biblia?",
    "bhs": "el libro ¿Qué nos enseña la Biblia?",
    "bm": "el folleto La Biblia y su mensaje",
    "bt": "el libro Demos testimonio completo sobre el Reino de Dios",
    "cf": "el libro Ven y sé mi seguidor",
    "cl": "el libro Acerquémonos a Jehová",
    "ct": "el libro ¿Existe un Creador que se interese por nosotros?",
    "dp": "el libro Prestemos atención a las profecías de Daniel",
    "fy": "el libro El secreto de la felicidad familiar",
    "gm": "el libro La Biblia... ¿la Palabra de Dios, o palabra del hombre?",
    "gt": "el libro El hombre más grande de todos los tiempos",
    "ia": "el libro Ejemplos de fe",
    "ie": "el folleto ¿Qué nos sucede cuando morimos?",
    "ijwbq": "la sección Preguntas sobre la Biblia",
    "ijwbv": "la sección Textos bíblicos explicados",
    "ijwfq": "la sección Preguntas frecuentes sobre los testigos de Jehová",
    "ijwia": "la sección Ejemplos de fe",
    "ijwyp": "la sección Los jóvenes preguntan",
    "ip-1": "el libro Las profecías de Isaías, tomo 1",
    "ip-2": "el libro Las profecías de Isaías, tomo 2",
    "it-1": "la obra Perspicacia para comprender las Escrituras, tomo 1",
    "it-2": "la obra Perspicacia para comprender las Escrituras, tomo 2",
    "it": "la obra Perspicacia para comprender las Escrituras",
    "jd": "el libro Vivamos muy pendientes del día de Jehová",
    "jl": "el folleto ¿Quiénes hacen la voluntad de Jehová en nuestros días?",
    "jr": "el libro Dios nos habla mediante Jeremías",
    "jv": "el libro Los testigos de Jehová, proclamadores del Reino de Dios",
    "jy": "el libro Jesús: el camino, la verdad y la vida",
    "kl": "el libro El conocimiento que lleva a vida eterna",
    "kr": "el libro El Reino de Dios ya está gobernando",
    "lff": "el curso ¡Disfrute de la vida para siempre!",
    "lffi": "el folleto ¡Disfrute de la vida para siempre!",
    "lmd": "el folleto Hacer discípulos: una obra de amor",
    "lr": "el libro Aprendamos del Gran Maestro",
    "lv": "el libro Manténganse en el amor de Dios",
    "lvs": "el libro Continúe en el amor de Dios",
    "mrt": "la serie Otros temas",
    "nwt": "la Traducción del Nuevo Mundo",
    "od": "el libro Organizados para hacer la voluntad de Jehová",
    "pe": "el libro Usted puede vivir para siempre en el paraíso en la Tierra",
    "re": "el libro Apocalipsis... ¡se acerca su magnífica culminación!",
    "rr": "el libro La adoración pura de Jehová: ¡por fin restaurada!",
    "rs": "el libro Razonamiento a partir de las Escrituras",
    "si": "el libro Toda Escritura es inspirada de Dios y provechosa",
    "th": "el folleto Seamos mejores lectores y maestros",
    "ti": "el folleto ¿Debería creer usted en la Trinidad?",
    "tp": "el libro Verdadera paz y seguridad... ¿cómo puede usted hallarla?",
    "wcg": "el libro Seamos valientes al andar con Dios",
    "we": "el folleto Cuando muere un ser querido",
    "ws": "el libro Seguridad mundial bajo el Príncipe de Paz",
    "wt": "el libro Adoremos al único Dios verdadero",
    "yp": "el libro Lo que los jóvenes preguntan. Respuestas prácticas",
    "yp1": "el libro Lo que los jóvenes preguntan. Respuestas prácticas, tomo 1",
    "yp2": "el libro Lo que los jóvenes preguntan. Respuestas prácticas, tomo 2",
}

# Precompilar regex para libros bíblicos seguidos de capítulo o capítulo:versículo
_sorted_bkeys = sorted(BIBLE_BOOK_ABBR.keys(), key=len, reverse=True)
_bkeys_pat = "|".join(re.escape(k) for k in _sorted_bkeys)
# Importante: permitir ":" seguido de espacio (ej. "Juan 17:3: ...") y palabras en minúsculas tras el versículo;
# bloquear dígitos contiguos (evita partir "28:7" en "2"), ":" seguido de dígito, "/", letras pegadas ("2Co") y otro libro ("1, 2 Ped.").
_v_num = r"\d{1,3}(?:[ab](?![A-Za-záéíóúÁÉÍÓÚñÑ]))?(?![0-9A-Za-záéíóúÁÉÍÓÚñÑ/]|:(?=\d))"
_v_tail = (
    r"\d{1,3}(?:[ab](?![A-Za-záéíóúÁÉÍÓÚñÑ]))?"
    r"(?![0-9A-Za-záéíóúÁÉÍÓÚñÑ/]|:(?=\d)|"
    r"\s*(?:Pedro|Corintios|Tesalonicenses|Timoteo|Reyes|Samuel|Crónicas|Macabeos|Juan|"
    r"Ped|Cor|Tes|Tim|Sam|Crón|Rey|Jn|Co|Pe|Ti|Te)(?:\.|\b))"
)
_cite_one = rf"\d{{1,3}}:{_v_num}(?:\s*[-–,]\s*{_v_tail})*"
# Cadena opcional de capítulos adicionales en el mismo libro: "; 11:5", ", 11:5", " y 11:5", " a 8:11", "-6:1", " al 6:1", "; nota; 6:2-8"
_cite_sep = r"(?:\s*;\s*(?:nota\s*;\s*)?|\s*,\s*|\s+y\s+|\s+al?\s+|\s*[-–]\s*)"
BIBLE_CITE_RE = re.compile(
    r"(?<![A-Za-záéíóúÁÉÍÓÚñÑ0-9])("
    + _bkeys_pat
    + r")(\.?)\s+("
    + _cite_one
    + r"(?:"
    + _cite_sep
    + _cite_one
    + r")*)",
    flags=re.IGNORECASE,
)

# Algunas citas sin ':' como "Sal. 2", "Is. 53", "Gál. 5" (excluir "núm." que en artículos de Perspicacia significa "Número 1 / 2")
_chap_only_exclude = {"núm", "nú", "nm", "est", "cant", "ag", "os", "am", "tit", "jud", "rev", "ap", "jon", "hab"}
BIBLE_CHAP_ONLY_RE = re.compile(
    r"(?<![A-Za-záéíóúÁÉÍÓÚñÑ0-9])("
    + "|".join(re.escape(k) for k in _sorted_bkeys if len(k) >= 2 and k not in _chap_only_exclude)
    + r")\.\s+(\d{1,3})(?!\s*:)(?![A-Za-záéíóúÁÉÍÓÚñÑ])(?=\b)",
)


class LessonHTMLParser(HTMLParser):
    """Extrae ordenadamente los encabezados y cada <article class="study-unit"> del XHTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []  # ("heading", lvl, text) | ("unit", dict)
        self.stop = False

        self.in_heading = None
        self.heading_buf = []

        self.in_unit = False
        self.unit_cls = ""
        self.cur_unit = None

        self.cur_section = None  # "question-box", "answer-section", "bib-section", "pearls-section"
        self.sec_depth = 0
        self.skip_label_depth = 0

        self.in_block = None  # "p" or "li"
        self.block_buf = []

    def _flush_block(self):
        if self.cur_section and self.in_block:
            txt = "".join(self.block_buf)
            txt = txt.replace("\xa0", " ").replace("\u200b", "").replace("\u2060", "")
            txt = re.sub(r"[ \t\r\n]+", " ", txt).strip()
            if txt:
                self.cur_unit[self.cur_section].append(txt)
        self.in_block = None
        self.block_buf = []

    def handle_starttag(self, tag, attrs):
        if self.stop:
            return
        d = dict(attrs)
        cls = d.get("class", "")
        el_id = d.get("id", "")

        if tag == "aside" or (tag == "section" and ("footnotes" in cls or el_id == "referencias-dossier")):
            self.stop = True
            return

        if not self.in_unit and tag in ("h2", "h3", "h4", "h5"):
            self.in_heading = tag
            self.heading_buf = []
            return

        if tag == "article" and "study-unit" in cls:
            self.in_unit = True
            self.unit_cls = cls
            self.cur_unit = {
                "unit_class": cls,
                "question-box": [],
                "answer-section": [],
                "bib-section": [],
                "pearls-section": [],
            }
            self.cur_section = None
            self.sec_depth = 0
            return

        if self.in_unit:
            if tag == "div":
                if self.cur_section is None:
                    for key in ("question-box", "answer-section", "bib-section", "pearls-section"):
                        if key in cls:
                            self.cur_section = key
                            self.sec_depth = 1
                            return
                else:
                    self.sec_depth += 1
                    if "box-label" in cls or "sub-label" in cls:
                        self.skip_label_depth = 1
                    elif self.skip_label_depth > 0:
                        self.skip_label_depth += 1
                    return

            if self.cur_section and self.skip_label_depth == 0:
                if tag in ("p", "li"):
                    self._flush_block()
                    self.in_block = tag
                    self.block_buf = []
                elif tag == "br" and self.in_block:
                    self.block_buf.append(" ")

    def handle_endtag(self, tag):
        if self.stop:
            return

        if self.in_heading == tag:
            txt = "".join(self.heading_buf).replace("\xa0", " ").replace("\u200b", "").replace("\u2060", "")
            txt = re.sub(r"\s+", " ", txt).strip()
            self.events.append(("heading", tag, txt))
            self.in_heading = None
            self.heading_buf = []
            return

        if self.in_unit:
            if tag == "article":
                self._flush_block()
                self.events.append(("unit", self.cur_unit))
                self.in_unit = False
                self.cur_unit = None
                self.cur_section = None
                return

            if self.cur_section:
                if tag in ("p", "li") and self.in_block == tag:
                    self._flush_block()
                elif tag == "div":
                    if self.skip_label_depth > 0:
                        self.skip_label_depth -= 1
                    self.sec_depth -= 1
                    if self.sec_depth == 0:
                        self._flush_block()
                        self.cur_section = None

    def handle_data(self, data):
        if self.stop:
            return
        if self.in_heading:
            self.heading_buf.append(data)
        elif self.in_unit and self.cur_section and self.skip_label_depth == 0:
            if self.in_block:
                self.block_buf.append(data)


def strip_all_page_markers(text: str) -> str:
    return re.sub(r"\[\s*P[aá]g\.?\s*\d+(?:\s*[-–,]\s*\d+)*\s*\]\s*", "", text, flags=re.IGNORECASE)


def clean_unicode_for_tts_and_reading(text: str) -> str:
    """Limpia caracteres hebreos/griegos crudos, macrones y marcadores tipográficos."""
    if not text:
        return ""
    # Reemplazar palabras griegas puntuales detectadas en el corpus
    text = text.replace("κατ’ οἴκον", "kat oíkon").replace("κατ' οἴκον", "kat oíkon")
    text = text.replace("ὑπογραμμός", "hypogrammós")
    # Quitar letras hebreas sueltas o dar contexto natural si las precede un artículo
    text = re.sub(r"\bun\s+[\u0590-\u05FF]+(?:\s+[\u0590-\u05FF]+)*\b", "un término hebreo", text)
    text = re.sub(r"\bel\s+[\u0590-\u05FF]+(?:\s+[\u0590-\u05FF]+)*\b", "el término hebreo", text)
    text = re.sub(r"\bla\s+[\u0590-\u05FF]+(?:\s+[\u0590-\u05FF]+)*\b", "la palabra hebrea", text)
    text = re.sub(r"\(\s*[\u0590-\u05FF]+\s*\)", "", text)
    text = re.sub(r"[\u0590-\u05FF]+[\s,]*\(?YHWH\)?", "YHWH", text)
    text = re.sub(r"[\u0590-\u05FF\u0370-\u03FF\u1F00-\u1FFF]+", "", text)

    # Normalizar macrones latinos usados en transliteraciones para que el TTS en español no tropiece
    macron_map = {
        "ē": "e", "Ē": "E",
        "ō": "o", "Ō": "O",
        "ā": "a", "Ā": "A",
        "ī": "i", "Ī": "I",
        "ū": "u", "Ū": "U",
        "ḗ": "é", "Ḗ": "É",
        "ṓ": "ó", "Ṓ": "Ó",
        "ý": "y", "Ý": "Y",
        "ḥ": "h", "Ḥ": "H",
        "Ǎ": "A", "ǎ": "a",
        "æ": "ae", "Æ": "AE",
        "ʼ": "", "ʽ": "", "ʹ": "",
        "\u202f": " ", "\u2009": " ", "\xa0": " ", "\u200b": "", "\u2060": "",
        "™": "", "®": "", "©": "", "❓": "", "✔": "", "▪": "", "●": "", "→": " — ",
    }
    for k, v in macron_map.items():
        text = text.replace(k, v)

    # Normalizar aproximaciones "~110 años" -> "aproximadamente 110 años", "aprox." -> "aproximadamente", "LXX" -> "la Septuaginta"
    text = re.sub(r"~\s*(\d)", r"aproximadamente \1", text)
    text = re.sub(r"\baprox\.\s*", "aproximadamente ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:en\s+)?la\s+LXX\b", "la Septuaginta", text)
    text = re.sub(r"\bLXX\b", "la Septuaginta", text)

    # Quitar "[19] ", "[20] ", "[8] " insertados al principio de versículos citados
    text = re.sub(r"(?<!\w)\[\d{1,3}\]\s*", "", text)
    # Quitar "[...]" por puntos suspensivos limpios
    text = re.sub(r"\[\s*\.\.\.\s*\]", "...", text)
    text = text.replace("…", "...")
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def is_pure_audit_item(text: str) -> bool:
    """Detecta párrafos/viñetas que son 100% notas técnicas de auditoría del dossier sin contenido bíblico."""
    t = text.strip()
    tl = t.lower()
    if not t:
        return True

    # Descartar fragmentos residuales sin contenido real (ej. ".", ":")
    alnum_count = sum(1 for ch in t if ch.isalnum())
    if alnum_count < 3:
        return True

    inner = t.lstrip("•- ").strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner_body = inner[1:-1].strip().lower()
        # Si tiene un punto y coma con segunda cláusula doctrinal sustanciosa, NO descartar aquí;
        # dejar que scrub_meta_audit_noise rescate la segunda cláusula.
        if ";" not in inner_body:
            audit_starts = (
                "sin extracto", "sin bloque", "sin nota", "sin texto", "sin referencias",
                "no hay extracto", "no hay nota", "no contiene extracto",
                "cobertura del índice", "índice rsg", "el índice rsg", "el índice guía",
                "nota de formato", "nota documental", "nota editorial",
                "it_s ", "rsg_s ", "pt14_s ",
            )
            if inner_body.startswith(audit_starts) and len(inner_body) < 350:
                return True

    pure_patterns = [
        r"^\(?\s*Sin\s+(?:bloques?|extractos?|notas?|textos?|referencias?)\b[^;()]*?(?:dossier|adjunt[oa]s?|este bloque|nwtsty_S)[^;()]*\.?\s*\)?\.?$",
        r"^\(?\s*(?:Cobertura\s+del\s+[ÍI]ndice|Nota\s+editorial\s+sobre\s+el\s+[ÍI]ndice)[^.]*.*$",
        r"^\(?\s*Referencia bíblica\s+[^;]+;\s*el dossier no adjunta[^)]+\)\.?$",
    ]
    for pat in pure_patterns:
        if re.match(pat, inner, flags=re.IGNORECASE):
            return True

    if any(k in tl for k in ("cobertura del índice rsg_s", "cobertura del índice guía")) and len(t) < 350 and "—" not in t and ":" in t and "entradas" in tl:
        return True

    return False


def _replace_top_level_parens(text: str, handler) -> str:
    """Reemplaza paréntesis balanceados (...) de nivel superior usando `handler(inner_str)`."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "(":
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            if depth == 0:
                inner = text[i + 1 : j - 1]
                rep = handler(inner)
                out.append(rep)
                i = j
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _looks_like_real_content_clause(seg: str) -> bool:
    """Devuelve True si el segmento contiene explicación doctrinal o citas concretas útiles."""
    sl = seg.lower()
    if any(k in sl for k in (
        "cobertura del índice", "cobertura de los índices", "cobertura de índice",
        "solo como títulos bibliográficos", "únicamente como título",
        "constan solo como títulos", "figuran únicamente como títulos", "figuran solo como títulos",
        "quedan solo como títulos", "quedan solo bibliográficas", "solo bibliográficas",
        "solo como referencias bibliográficas", "cumplimiento estricto", "aparece reproducido dos veces",
        "no se repiten extractos nuevos", "sin repetir extractos nuevos", "repite exactamente las",
        "cubren ", "referencias extraída", "referencia extraída", "referencias distintas extraída",
        "incluye además el índice", "incluye el índice", "incluye los respectivos",
        "incluye los índices", "solo adjunta el índice", "no constan extractos",
        "sin texto adicional adjunto", "encabezado de cierre",
        "reproduce literalmente los versículos", "ya examinados en la pregunta",
        "corte interno", "dos cortes dentro", "se trunca", "figuran marcadas",
        "vuelven a listar", "se repite tanto en", "constan extractadas",
        "están extraídas", "está extraída", "están cubiertas", "están incluidas",
        "ya extractada", "ya extractado", "coincide con la única", "no se adjuntan extractos",
        "contiene únicamente el título",
    )):
        return False
    if re.match(r"^\s*(?:sin\s+(?:bloques?|extractos?|notas?|textos?|referencias?)|no hay extractos?|solo figura|solo aparece|figura únicamente|ni extractos ni|nota:\s*(?:el|la|los|este)\s+(?:extracto|it-[12]|w\d|.*?(?:trunca|corte|repite))|it_s\s*x\d+|rsg_s\s*x\d+|\d+\s+extractos?\s+(?:de\s+)?(?:it_s|rsg_s))", sl):
        return False
    if re.search(r"\b(?:analiza|cita|menciona|señala|destaca|incluye|aplica|responde|muestra|recuerda|explica|indica|confirma|los pasajes integrados|línea cronológica|revis[aó]|textos? integrad|consultar)\b", sl):
        return True
    if re.search(r"\b(?:pág|págs|cap|caps|párr|párrs|lec|lecc|art|artículo)\b", sl):
        return True
    if ":" in seg and re.search(r"\d:\d", seg):
        return True
    return False


def scrub_meta_audit_noise(text: str) -> str:
    """Elimina cualquier jerga técnica del dossier/bases de datos y deja solo doctrina y citas reales."""
    if not text:
        return ""

    t = strip_all_page_markers(text)

    # 0. Eliminar rutas de archivo, rangos/números de línea markdown (L31, L408, ◆ L118...) y avisos sueltos de cobertura / truncamiento
    t = re.sub(r"\(\s*02_Dossier_Indice\.md\s*\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\b02_Dossier_Indice\.md\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\(\s*L\d{1,4}\s*(?:[-–]\s*L?\d{1,4})?\s*\)", "", t)
    t = re.sub(r"\b(en\s+)?Gu[ií]a\s+(\d+[AB]?)\b", lambda m: ("en la " if m.group(1) else "") + f"Lección {m.group(2)}", t, flags=re.IGNORECASE)
    t = re.sub(r"(\b\d{1,2}[AB])\s*[◆•]?\s*\bL\d{1,4}\b", r"Lección \1", t)
    t = re.sub(r"\b(Punto\s+introductorio|Nota\s+al\s+instructor|Pauta\s+final|EXPERIENCIA|SUGERENCIA)\s+\bL\d{1,4}\b", r"\1", t)
    t = re.sub(r"\b(referencia\s+inmediata|inmediatamente\s+siguiente)\s+de\s+\bL\d{1,4}\b", r"\1 de la lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:de|en)\s+\bL\d{1,4}\b", "de la lección", t, flags=re.IGNORECASE)
    t = re.sub(r"[◆•]?\s*\bL\d{1,4}\b", "", t)

    t = re.sub(r"\bEn\s+(?:it-[12]|Perspicacia)\s+aparece\s+aquí\s+la\s+l[ií]nea\s+de\s+tabla\s*\|[^.\n]*", "", t, flags=re.IGNORECASE)
    # Caso especial con paréntesis desbalanceado por cita truncada: (nota: ... queda truncado al final en "(Mat. ").
    t = re.sub(
        r"\(\s*nota:\s*[^()]*trunca[^\n\"”]*[\"“][^\n\"”]*[\"”]\s*\)\.?",
        "",
        t,
        flags=re.IGNORECASE,
    )
    # Corchetes de auditoría, extracción parcial o truncamiento
    t = re.sub(
        r"\[\s*[^\[\]]*?(?:trunca|\d+\s+extractos?|extracto\s+truncado|extra[ií]d[oa]\s+(?:el|los)|solo\s+p[aá]rr\.?\s*\d+\s+adjunto|salto\s+de|coincidente|pt14_S|rsg_S|it_S|nwtsty_S)[^\[\]]*\]",
        "",
        t,
        flags=re.IGNORECASE,
    )
    # Limpiar caso especial de Lección 18 ("Conclusión (encabezado de cierre...)") y nota de Lección 10A sobre paréntesis faltante
    t = re.sub(r"\(\s*encabezado\s+de\s+cierre\s+[^)]*\)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\.\s*\(\s*Nota:\s*se\s+conserva\s+la\s+falta\s+de\s+paréntesis\s+de\s+cierre[^)\n]*\)\.?", ")", t, flags=re.IGNORECASE)
    # Limpiar fórmula de conteo de concordancia en Lección 2B
    t = re.sub(
        r"\blistados\s+bajo\s+CONSOLAR\s+y\s+CONSUELO\s+en\s+el\s+[íi]ndice\s+de\s+este\s+dossier\b",
        "agrupados bajo los términos “consolar” y “consuelo” en esta sección",
        t,
        flags=re.IGNORECASE,
    )

    # Oraciones completas de cobertura / auditoría del índice / notas editoriales / avisos de truncamiento
    t = re.sub(r"\s*\(?\s*Cobertura\s+de(?:l|\s+los?)\s+[ÍI]ndices?\b.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*\(?\s*Nota\s+editorial\s+sobre\s+el\s+[ÍI]ndice\b.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*Nota\s+sobre\s+extractos\s+truncados\b.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*Adem[aá]s\s+quedan\s+truncados\s+al\s+final\b.*$", "", t, flags=re.IGNORECASE)

    # Normalizar referencias a Dossier con concordancia femenina correcta en español ("la Lección X")
    t = re.sub(r"\bdentro\s+del\s+(?:mismo\s+|propio\s+)?Dossiers?\s+(\d+[AB]?)\b", r"en la Lección \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\ben\s+(?:este|el)\s+(?:mismo\s+|propio\s+)?Dossiers?\s+(\d+[AB]?)\b", r"en la Lección \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\bdel\s+(?:mismo\s+|propio\s+)?Dossiers?\s+(\d+[AB]?)\b", r"de la Lección \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\bDossiers\s+(\d+[AB]?(?:\s*,\s*\d+[AB]?)*(?:\s+y\s+\d+[AB]?)?)\b", r"las Lecciones \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:el\s+)?Dossier\s+(\d+[AB]?)\b", r"la Lección \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:dentro\s+del|en\s+este|en\s+el)\s+(?:mismo\s+|propio\s+)?dossier\b", "en esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\bdel\s+(?:mismo\s+|propio\s+)?dossier(?:\s+del\s+curso)?\b", "de esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:el\s+)?dossier\b", "esta guía de estudio", t, flags=re.IGNORECASE)

    # 1. Procesar paréntesis balanceados ANTES de suavizar "Índice Guía de Estudio" para detectar bloques auditados
    def clean_paren_block(inside: str) -> str:
        raw_in = inside.strip()
        il = raw_in.lower()

        # Bloques puramente de conteo o aviso sobre Índice Guía de Estudio sin doctrina nueva
        if re.match(
            r"^(?:incluye\s+(?:adem[aá]s\s+)?(?:el|los|los\s+respectivos)\s+[íi]ndice|"
            r"(?:el\s+)?[íi]ndice\s*(?:gu[ií]a\s+de\s+estudio|rsg_s)|"
            r"nota:\s*(?:el\s+)?[íi]ndice\s+gu[ií]a\s+de\s+estudio|"
            r"en\s+este\s+bloque\s+[^;]*adjunta\s+el\s+[íi]ndice)",
            il,
        ):
            return ""
        if any(k in il for k in (
            "referencias extraída", "referencia extraída", "referencias distintas extraída",
            "únicamente como título bibliográfico", "únicamente como títulos bibliográficos",
            "solo como título bibliográfico", "solo como títulos bibliográficos",
            "solo por título bibliográfico", "títulos bibliográficos sin extracto",
            "sin nuevos extractos", "no incluye sección de",
            "constan extractadas", "figuran extraídas", "están extraídas", "está extraída",
            "están extraídos", "están cubiertas", "están incluidas arriba",
        )) and not re.search(r"\b(?:analiza|muestra|explica|enseña|destaca|revela)\b", il):
            return ""
        if re.match(r"^(?:todas?\s+(?:las?\s+)?(?:\d+\s+)?|las?\s+\d+\s+|ambas\s+|la\s+[úu]nica\s+)(?:referencias?|entradas?)\b", il) and not re.search(r"\b(?:p[aá]g|p[aá]rr|cap|lec|art)\b", il):
            return ""

        # Caso especial "consultar ..." suelto
        m_cons = re.match(r"^(?:consultar(?:\s+directamente)?|remite\s+a\s+la\s+lectura\s+directa\s+de)\s+(.+)$", raw_in, flags=re.IGNORECASE)
        if m_cons and "sin " not in il:
            dest = m_cons.group(1).strip().rstrip(".")
            if raw_in.lower().startswith("consultar"):
                return f"Para este punto la lección remite a consultar {dest}"
            return f"La lección remite a la lectura directa de {dest}"

        audit_trigger = any(k in il for k in (
            "trunca", "corte interno", "dos cortes", "se corta",
            "sin extracto", "sin bloque", "sin nota", "sin texto", "sin referencias",
            "no hay extracto", "no hay nota", "no contiene extracto", "no constan extractos",
            "no se adjuntan extractos", "contiene únicamente el título", "figuran marcadas",
            "se repite tanto", "vuelven a listar", "constan extractad", "están extraíd",
            "está extraíd", "están cubiertas", "están incluid", "ya extractad",
            "extractado en", "extractada en", "extractado bajo", "extractada bajo",
            "extractado previamente", "extractada previamente", "extractadas inmediatamente",
            "coincide con la única", "reproduce literalmente los versículos", "ya examinados en",
            "del párr.", "it_s", "rsg_s", "pt14_s", "nwtsty", "solo bibliográf", "solo como títulos",
            "únicamente como título", "título bibliográfico", "títulos bibliográficos",
            "cobertura de", "entradas del índice", "referencias del índice",
            "indicadas en el índice", "en el índice", "del índice de ", "de ese índice",
            "ese índice", "del índice.", "del índice)", "extractado arriba", "extractada arriba",
            "reproducido dos veces", "reproducidos arriba", "figuran extractadas arriba",
            "cumplimiento estricto", "aportes adicionales no listados", "en el bloque de",
            "en esta guía de estudio", "de esta guía de estudio", "guía de estudio",
            "referencias extraída", "referencia extraída",
        ))

        if not audit_trigger:
            return f"({raw_in})"

        # Limpiar separando por ';' y rescatando toda cláusula doctrinal/bíblica válida
        segs = [s.strip() for s in raw_in.split(";")]
        kept = []
        started_with_sin = bool(re.match(r"^(?:sin\s+|no hay\s+|en el bloque de\s+|para [^,;]+(?:no hay|no constan|esta gu[ií]a)|en este bloque\b|reproduce literalmente\b)", il))
        for idx_seg, seg in enumerate(segs):
            sc = re.sub(r"^\s*del\s+p[aá]rr\.?\s*\d+\s+en\s*$", "", seg, flags=re.IGNORECASE).strip()
            if not sc:
                continue
            # Limpiar avisos de truncamiento/cortes/repeticiones/metadatos dentro del segmento
            sc = re.sub(
                r",?\s*\(?\s*(?:nota:\s*)?(?:aunque\s+|y\s+)?(?:(?:el|este|los)\s+)?extractos?\s+(?:de\s+[^,;()]+\s+)?(?:queda|quedando|termina|aparece|presenta|se\s+corta|se\s+trunca|contiene\s+[úu]nicamente)\b[^;]*",
                "",
                sc,
                flags=re.IGNORECASE,
            )
            sc = re.sub(
                r",?\s*(?:\(?\s*nota:\s*)?(?:y\s+)?(?:el\s+(?:p[aá]rr\.?\s*\d+|de\s+[^,;()]+)|[^,;()]{3,70})\s+(?:queda|quedando|termina|se)\s+trunca\w*\b[^;]*",
                "",
                sc,
                flags=re.IGNORECASE,
            )
            sc = re.sub(
                r",?\s*\bnota:\s*(?:este\s+extracto\s+)?presenta\s+(?:un|dos)\s+cortes?\b[^;]*",
                "",
                sc,
                flags=re.IGNORECASE,
            )
            sc = re.sub(
                r",?\s*(?:aunque\s+)?(?:extracto\s+)?truncad[oa]s?\s+(?:al\s+(?:final|inicio)|en\s+[^;]+)?",
                "",
                sc,
                flags=re.IGNORECASE,
            )
            sc = re.sub(
                r",?\s*(?:ya\s+)?extractad[oa]s?\s+(?:en|bajo|arriba|previamente|inmediatamente)\b[^;]*",
                "",
                sc,
                flags=re.IGNORECASE,
            )
            sc = re.sub(
                r",?\s*(?:incluido íntegramente en [^;]+|extractado previamente en [^;]+|aparece reproducido dos veces[^;]*|sin texto de nota[^;]*|con (?:indicador|referencia) (?:de|a) nota [^,;]+ sin texto[^;]*)",
                "",
                sc,
                flags=re.IGNORECASE,
            )
            # Limpiar cláusulas enteras de metadatos de repetición / ausencia / reproducción
            if re.match(
                r"^(?:sin\s+(?:bloques?|extractos?|notas?|textos?|referencias?)|"
                r"no hay\s+extractos?|ni\s+extractos\b|para [^;]*(?:no constan|no hay) extractos|"
                r"en el bloque de [^,;]+ no hay|reproduce literalmente los versículos|"
                r"nota:\s*(?:el|la|los|este)\s+(?:extractos?|it-[12]|[^;]*(?:se repite|vuelven a listar|figuran marcadas|termina en|contin[úu]a en|se corta|se trunca)))",
                sc.lower(),
            ):
                continue
            # Quitar conectores heredados si había cláusula previa eliminada
            if started_with_sin or (idx_seg > 0 and not kept):
                sc = re.sub(r"^(?:no\s+obstante|sin\s+embargo|pero|aun\s+as[ií]|y)\s*,?\s*", "", sc, flags=re.IGNORECASE)

            # Suavizar etiquetas internas para que nunca rompan citas como "analiza Heb. 8:2"
            sc = re.sub(r"\btodo\s+responde\s+(?:a\s+)?pt14_S\b", "todo se explica con la referencia de esta lección", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\bresponde\s+pt14_S\b", "responde la referencia de esta lección", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\bde\s+las\s+referencias?\s+(?:de\s+|en\s+)?pt14_S\b", "de las referencias de esta lección", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\breferencias?\s+directas?\s+(?:de\s+|en\s+)?pt14_S\b", "referencia de la lección", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\bla\s+referencia\s+pt14_S\b", "la referencia de la lección", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\ben\s+pt14_S\b", "en esta lección", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\bde\s+pt14_S\b", "de esta lección", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\bpt14_S\b", "esta lección", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\b(?:en|de)\s+esta\s+gu[ií]a\s+de\s+estudio\b", "", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\s+(?:indicadas|listados?|enumera(?:dos)?)\s+en\s+(?:el|ese)\s+[íi]ndice\b", "", sc, flags=re.IGNORECASE)
            sc = re.sub(r"\bfuera\s+de\s+ese\s+[íi]ndice\b", "como complemento adicional", sc, flags=re.IGNORECASE)

            sc = sc.strip(" ,;:-—")
            if not sc or not _looks_like_real_content_clause(sc):
                continue
            kept.append(sc)

        if not kept:
            return ""
        joined = "; ".join(kept)
        # Si empezó por "Sin extractos..." y rescatamos una oración explicativa completa, quitar paréntesis
        if started_with_sin or len(joined) > 130 or re.match(r"^(?:no obstante|únicamente|a partir de|en términos|como dato|los pasajes|justo a continuación|este punto|el capítulo|consultar)", joined, flags=re.IGNORECASE):
            joined = re.sub(r"^consultar\b", "Conviene consultar", joined, flags=re.IGNORECASE)
            return joined[0].upper() + joined[1:] if joined else ""
        return f"({joined})"

    t = _replace_top_level_parens(t, clean_paren_block)
    t = re.sub(
        r"\bNi\s+extractos\s+ni\s+[íi]ndices?\s+para\s+[^\n\)]*?(?:\.(?=\s+[A-ZÁÉÍÓÚÑ«“\(]|\s*$)|$)",
        "",
        t,
        flags=re.IGNORECASE,
    )

    # 2. Limpiar oraciones / frases de auditoría del Índice Guía de Estudio y truncamiento fuera de paréntesis
    t = re.sub(
        r"—?\s*(?:aunque\s+|y\s+)?(?:el\s+extracto\s+|este\s+extracto\s+)?quedando\s+truncad[oa]\s+[^—.!?\n]*—?",
        " ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r",?\s*(?:extracto\s+)?(?:queda|termina|aparece)\s+truncad[oa]\s+(?:al\s+(?:final|inicio)|en\s+)[^.;()\n]*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r",?\s*\bextracto\s+truncado\b[^.;()\n]*", "", t, flags=re.IGNORECASE)
    t = re.sub(r",?\s*(?:extractad[oa]\s+(?:arriba|en\s+[^.;()\n]{3,40}|previamente))", "", t, flags=re.IGNORECASE)

    # Separar oraciones reales respetando abreviaturas (pág., párr., Mat., Luc., 1 Ped., ...) para limpiar conteos bibliográficos
    def _split_true_sentences(src: str) -> list[str]:
        abbr_ends = {
            "pág", "págs", "párr", "párrs", "cap", "caps", "lec", "lecs", "lecc",
            "núm", "art", "sec", "apén", "rec", "vol", "v", "vv", "cf", "cfr",
            "ej", "lit", "gr", "heb", "aram", "tom", "e.c",
        }
        parts = []
        last = 0
        n = len(src)
        for i, ch in enumerate(src):
            if ch not in ".!?":
                continue
            nxt = src[i + 1 :]
            if i + 1 < n and src[i + 1] not in " \t\n":
                continue
            # Proteger puntos suspensivos "..." y abreviaturas seguidas de dígito ("Mat. 4:4", "pág. 12")
            if ch == ".":
                if i >= 2 and src[i - 2 : i + 1] == "...":
                    continue
                after_ws = nxt.lstrip()
                if after_ws and after_ws[0].isdigit():
                    continue
                if after_ws and after_ws[0].islower():
                    continue
                m_w = re.search(r"([A-Za-záéíóúÁÉÍÓÚñÑ.]{1,12})$", src[:i])
                if m_w and m_w.group(1).lower() in abbr_ends:
                    continue
            seg = src[last : i + 1].strip()
            if seg:
                parts.append(seg)
            last = i + 1
        tail = src[last:].strip()
        if tail:
            parts.append(tail)
        return parts

    _audit_kws_sent = (
        "título bibliográfico", "títulos bibliográficos", "solo título", "solo títulos",
        "referencia bibliográfica", "referencias bibliográficas",
        "solo como títulos", "únicamente como títulos", "títulos de referencia",
        "sin extracto", "sin bloque", "ni extractos ni", "reproducido aquí",
        "referencias extraída", "referencia extraída", "referencias distintas extraída",
        "aparecen únicamente como", "figuran únicamente como", "constan solo como",
        "solo figura el índice", "solo aparece el índice", "figura únicamente el índice",
        "solo adjunta el índice", "ya figura extraído", "ya figura extraída",
        "no incluye sección de", "no se adjuntan extractos", "constan extractadas",
        "han sido extraídas", "en los bloques precedentes",
        "están cubiertas", "entradas sin extracto", "de 8 entradas", "de 3 referencias",
        "de 1 referencia", "únicamente el índice",
    )
    cleaned_sents = []
    for sent in _split_true_sentences(t):
        sl = sent.lower()
        if any(k in sl for k in _audit_kws_sent):
            is_pure_tally = bool(re.match(
                r"^(?:el\s+|los\s+|en\s+los?\s+(?:respectivos?\s+)?)?(?:[íi]ndices?\b|rsg_s\b)|"
                r"^(?:para\b|en\s+este\s+bloque\b|nota\s*:|solo\s+figura|solo\s+aparece|figura\s+[úu]nicamente|ni\s+extractos\b|este\s+extracto\s+coincide\b)|"
                r"^(?:todas?\s+(?:las?\s+)?(?:\d+\s+)?|dos\s+|su\s+[úu]nica\s+)?(?:referencias?|entradas?)\s+(?:de(?:l|\s+(?:las?|el))|listadas|del\s+[íi]ndice|adjuntas)|"
                r"^(?:[1-3]\s+)?[a-záéíóúñ.]+\s+\d+:\d+[^\n;]{0,35}\b(?:enumera|lista|listas?|no\s+incluye\s+secci[oó]n)",
                sl,
            ))
            if is_pure_tally:
                continue
            sent_cut = re.sub(
                r"(?:;|,\s*(?:adem[aá]s\s+de|siendo|quedando|donde|mientras\s+que|apart[ae]\s+de|de\s+las\s+cuales|junto\s+con\s+otros|y\s+(?:\w+\s+){0,3}(?:figuran?|constan?|aparecen?|quedan?)))\s*"
                r"[^;\n]*?\b(?:t[ií]tulos?\s+bibliogr[aá]ficos?|referencias?\s+bibliogr[aá]ficas?|sin\s+extracto\w*|solo\s+(?:como\s+)?t[ií]tulos?|únicamente\s+como\s+t[ií]tulos?|no\s+incluye\s+secci[oó]n|constan\s+extractad\w*).*$",
                ".",
                sent,
                flags=re.IGNORECASE,
            )
            if sent_cut != sent:
                sent = sent_cut
            elif re.search(r"\b(?:enumera|lista[ns]?)\s+(?:\w+\s+){0,4}(?:referencias|entradas)\b", sl):
                continue
        cleaned_sents.append(sent)
    t = " ".join(cleaned_sents)

    t = re.sub(r"\bsu\s+[úu]nica\s+entrada\s+en\s+el\s+[íi]ndice\s+coincide\s+con\b", "esto coincide con", t, flags=re.IGNORECASE)
    t = re.sub(r"\bque\s+adem[aá]s\s+encabeza\s+el\s+[íi]ndice\s+de\s+este\s+vers[ií]culo\b", "referencia clave de este versículo", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:las?\s+\d+|dos)\s+(?:entradas|referencias)\s+del\s+(?:[ÍI]ndices?\s+(?:Gu[ií]a\s+de\s+Estudio)?|[íi]ndices?)\s+(?:corresponden\s+a|figuran)\b", "las referencias corresponden a", t, flags=re.IGNORECASE)

    # Normalizar menciones restantes de Índice Guía de Estudio / rsg_S evitando artículos duplicados ("su las...")
    t = re.sub(r"\ben\s+el\s+[ÍI]ndices?\s+se\s+adjunta\b", "en las referencias se incluye", t, flags=re.IGNORECASE)
    def repl_rsg_index(m):
        pre = (m.group(1) or "").lower()
        if pre.startswith("del"):
            return "de las referencias de estudio "
        if pre.startswith("al"):
            return "a las referencias de estudio "
        if pre.startswith("su"):
            return "sus referencias de estudio "
        if "respectiv" in pre:
            return "las respectivas referencias de estudio "
        return "las referencias de estudio "
    t = re.sub(
        r"\b(el\s+|los\s+(?:respectivos?\s+)?|las\s+(?:respectivas?\s+)?|del\s+|al\s+|sus?\s+)?(?:[ÍI]ndices?\s*(?:Gu[ií]a\s+de\s+Estudio\s*(?:\(\s*rsg_S\s*\))?|rsg_S)|rsg_S)(?=\s|[,.;:\)\]]|$|[A-Za-záéíóúÁÉÍÓÚñÑ])",
        repl_rsg_index,
        t,
        flags=re.IGNORECASE,
    )

    # 3. Limpiar etiquetas técnicas internas remanentes en prosa normal (plural "Notas" antes de singular "Nota")
    t = re.sub(r"\btodo\s+responde\s+(?:a\s+)?pt14_S\b", "todo se explica con la referencia de esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\bresponde\s+pt14_S\b", "responde la referencia de esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\bde\s+las\s+referencias?\s+(?:de\s+|en\s+)?pt14_S\b", "de las referencias de esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\bReferencia(?:s)?\s+directa(?:s)?\s+(?:de\s+|en\s+)?pt14_S\b", "Referencia de la lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:la\s+)?referencia\s+pt14_S\b", "la referencia de la lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\ben\s+pt14_S\b", "en esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\bde\s+pt14_S\b", "de esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\bpt14_S\b", "esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\bNotas\s+de\s+estudio\s+nwtsty(?:_S)?\b", "Notas de estudio de la Biblia", t)
    t = re.sub(r"\bnotas\s+de\s+estudio\s+nwtsty(?:_S)?\b", "notas de estudio de la Biblia", t, flags=re.IGNORECASE)
    t = re.sub(r"\bNota\s+de\s+estudio\s+nwtsty(?:_S)?\b", "Nota de estudio de la Biblia", t, flags=re.IGNORECASE)
    t = re.sub(r"\bnotas\s+nwtsty(?:_S)?\b", "notas de estudio", t, flags=re.IGNORECASE)
    t = re.sub(r"\bnota\s+nwtsty(?:_S)?\b", "nota de estudio", t, flags=re.IGNORECASE)
    t = re.sub(r"\bnwtsty(?:_S)?\b", "la Biblia de Estudio", t, flags=re.IGNORECASE)
    t = re.sub(r"\bit_S\b", "Perspicacia", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:de|en)\s+esta\s+gu[ií]a\s+de\s+estudio\b", "de esta lección", t, flags=re.IGNORECASE)
    t = re.sub(r"\bdel\s+extracto\s+esta\s+lección\b", "en el texto de esta lección", t, flags=re.IGNORECASE)

    # 4. Limpiar restos tipográficos y puntuación huérfana (como ". Según...", ": Se emplea...", "()")
    t = re.sub(r"\(\s*[;,:.\s\-]*\)", "", t)
    t = re.sub(r"\[\s*[;,:.\s\-]*\]", "", t)
    t = re.sub(r"\s+([.,;:?!])", r"\1", t)
    t = re.sub(r"([:;])\.", r"\1", t)
    t = re.sub(r"([.,;:])\s*\1+", r"\1", t)
    t = re.sub(r"([¿¡])\s+", r"\1", t)
    t = re.sub(r"\s+\)", ")", t)
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"^[\s•\-.,;:—–]+", "", t).strip()
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t


def expand_pub_dates_and_titles(text: str) -> str:
    """Expande abreviaturas de publicaciones (w15 15/12, it-2, kr, mwb18.09, g 1/14, km 2/00, wp16, yb01, etc.)."""
    t = text

    # Pre-expandir "cf." / "cfr." (compárese con) ANTES de símbolos de publicación para que "cf." nunca choque con el libro "cf"
    t = re.sub(r"\bcfr?\.\s*", "compárese con ", t, flags=re.IGNORECASE)

    def yr_4(yy_str: str) -> int:
        yy = int(yy_str)
        return 1900 + yy if yy >= 30 else 2000 + yy

    # 1. La Atalaya con fecha completa D/M: "w15 15/12", "W96 15/12"
    def repl_w_dm(m):
        y = yr_4(m.group(1))
        d = int(m.group(2))
        mo = MONTHS_ES.get(int(m.group(3)), m.group(3))
        return f"La Atalaya del {d} de {mo} de {y}"
    t = re.sub(r"\b[wW](\d{2})\s+(\d{1,2})/(\d{1,2})\b", repl_w_dm, t)

    # 2. La Atalaya edición mensual YYYY.MM: "w21.02", "W18.09"
    def repl_w_ym(m):
        y = yr_4(m.group(1))
        mo = MONTHS_ES.get(int(m.group(2)), m.group(2))
        return f"La Atalaya de {mo} de {y}"
    t = re.sub(r"\b[wW](\d{2})\.(\d{2})\b", repl_w_ym, t)

    # 3. La Atalaya con nombre de mes: "w18 febrero", "W19 julio"
    _meses_pat = "|".join(MONTHS_ES.values())
    def repl_w_mes(m):
        y = yr_4(m.group(1))
        mo = m.group(2).lower()
        return f"La Atalaya de {mo} de {y}"
    t = re.sub(rf"\b[wW](\d{{2}})\s+({_meses_pat})\b", repl_w_mes, t, flags=re.IGNORECASE)

    # 4. La Atalaya edición público: "wp16 núm. 2" / "wp21"
    t = re.sub(
        r"\b[wW]p(\d{2})\s+n[uú]m\.?\s*(\d+)\b",
        lambda m: f"La Atalaya (edición para el público), número {m.group(2)} de {yr_4(m.group(1))}",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b[wW]p(\d{2})\b",
        lambda m: f"La Atalaya (edición para el público) de {yr_4(m.group(1))}",
        t,
    )

    # 5. La Atalaya solo año ("w01" suelto)
    t = re.sub(r"\bw(\d{2})\b", lambda m: f"La Atalaya de {yr_4(m.group(1))}", t)

    # 6. Guía de actividades mwbYY.MM o mwbYY mes
    t = re.sub(
        r"\bmwb(\d{2})\.(\d{2})\b",
        lambda m: f"Guía de actividades de {MONTHS_ES.get(int(m.group(2)), m.group(2))} de {yr_4(m.group(1))}",
        t,
    )
    t = re.sub(
        rf"\bmwb(\d{{2}})\s+({_meses_pat})\b",
        lambda m: f"Guía de actividades de {m.group(2).lower()} de {yr_4(m.group(1))}",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bmwb(\d{2})\b", lambda m: f"Guía de actividades de {yr_4(m.group(1))}", t)

    # 7. ¡Despertad!: "g01 22/10" (año+día/mes) vs "g 1/14" (mes/año) vs "g 22/12"
    t = re.sub(
        r"\bg(\d{2})\s+(\d{1,2})/(\d{1,2})\b",
        lambda m: f"la revista ¡Despertad! del {int(m.group(2))} de {MONTHS_ES.get(int(m.group(3)), m.group(3))} de {yr_4(m.group(1))}",
        t,
    )
    def repl_g_short(m):
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 6 <= b <= 26:
            return f"la revista ¡Despertad! de {MONTHS_ES.get(a, str(a))} de {2000 + b}"
        return f"la revista ¡Despertad! del {a} de {MONTHS_ES.get(b, str(b))}"
    t = re.sub(r"\bg\s+(\d{1,2})/(\d{1,2})\b", repl_g_short, t)
    t = re.sub(r"\bg(\d{2})\b", lambda m: f"la revista ¡Despertad! de {yr_4(m.group(1))}", t)

    # 8. Nuestro Ministerio del Reino: "km 2/00" / "km 12/13"
    t = re.sub(
        r"\bkm\s+(\d{1,2})/(\d{2})\b",
        lambda m: f"Nuestro Ministerio del Reino de {MONTHS_ES.get(int(m.group(1)), m.group(1))} de {yr_4(m.group(2))}",
        t,
    )

    # 9. Anuarios: "yb01", "yb95"
    t = re.sub(r"\byb(\d{2})\b", lambda m: f"el Anuario de {yr_4(m.group(1))}", t)

    # 10. Libros/folletos fijos por símbolo:
    # Símbolos inequívocos (3+ letras exclusivas JW o con guion) -> expandir siempre que aparezcan aislados
    _unambiguous_syms = {
        "it-1", "it-2", "ip-1", "ip-2", "ijwbq", "ijwbv", "ijwfq", "ijwia", "ijwyp",
        "lff", "lffi", "lmd", "lvs", "bhs", "wcg", "nwt", "mrt", "yp1", "yp2",
    }
    _all_syms = sorted(PUB_TITLES.keys(), key=len, reverse=True)
    _unamb_pat = "|".join(re.escape(k) for k in _all_syms if k in _unambiguous_syms)
    _amb_pat = "|".join(re.escape(k) for k in _all_syms if k not in _unambiguous_syms)

    # Regla A: Símbolos cortos o potencialmente ambiguos SOLO cuando van en minúsculas y seguidos de un localizador editorial JW explícito
    _jw_loc = (
        r"(?i:caps?\.|cap[ií]tulos?|p[aá]gs?\.|p[aá]ginas?|p[aá]rrs?\.|p[aá]rrafos?|"
        r"lecc?s?\.|lecci[oó]n|sec\.|secci[oó]n|art\.|art[ií]culos?|ap[eé]n\.|ap[eé]ndice|"
        r"rec\.|recuadro|notas?|tomo|vol\.|n[uú]m\.|puntos?|tema|introducci[oó]n|glosario|en\s+línea|“|\"|\d)"
    )
    t = re.sub(
        rf"(?<![A-Za-záéíóúÁÉÍÓÚñÑ0-9\-·])({_amb_pat})(?=\s*,?\s*{_jw_loc})",
        lambda m: PUB_TITLES[m.group(1)],
        t,
    )
    # También si va justo entre paréntesis/corchetes/punto y coma sin palabra española común (excepto 'si', 're', 'ti', 'ba', 'ie', 'pe', 'ia', 'we')
    _safe_cite_syms = "|".join(
        re.escape(k) for k in _all_syms
        if k not in _unambiguous_syms and k not in {"si", "re", "ti", "ba", "ie", "pe", "ia", "we"}
    )
    t = re.sub(
        rf"(?<=[(\[;])\s*({_safe_cite_syms})(?=\s*[,;:\)\]])",
        lambda m: PUB_TITLES[m.group(1)],
        t,
    )

    # Regla B: Símbolos inequívocos JW (lff, lmd, it-1, it-2, ip-2, nwt, ijwbq...) siempre expandidos (incluyendo Lmd/Lff con mayúscula inicial)
    def repl_unamb_sym(m):
        raw = m.group(1)
        exp = PUB_TITLES[raw.lower()]
        if raw and raw[0].isupper():
            return exp[0].upper() + exp[1:]
        return exp
    t = re.sub(
        rf"(?<![A-Za-záéíóúÁÉÍÓÚñÑ0-9\-·])({_unamb_pat})(?![A-Za-záéíóúÁÉÍÓÚñÑ0-9\-·])",
        repl_unamb_sym,
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"(Perspicacia[^()\n]{1,90}[,;]\s*)si(?=\s*[;\)\]])",
        lambda m: m.group(1) + PUB_TITLES["si"],
        t,
    )

    # Arreglar duplicaciones de sustantivo genérico, conjunciones ('e la obra' -> 'y la obra') y contracciones
    t = re.sub(
        r"\b(?:[Ll]a\s+gu[ií]a|[Ee]l\s+libro|[Ee]l\s+curso|[Ee]l\s+folleto|[Ll]a\s+obra|[Ll]a\s+revista)\s+(el libro|el folleto|la obra|el curso|la revista|la sección|la serie)\b",
        r"\1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\b([Ee])\s+(el libro|el folleto|el curso|la obra|la sección|la serie|la revista|la Traducción|La Atalaya|Nuestro Ministerio)\b", lambda m: ("Y " if m.group(1) == "E" else "y ") + m.group(2), t)
    t = re.sub(r"\b(de|a)\s+(el)\s+(libro|folleto|curso|Anuario)\b", lambda m: ("del " if m.group(1).lower() == "de" else "al ") + m.group(3), t, flags=re.IGNORECASE)
    t = re.sub(r"\b(del|al)\s+el\s+(libro|folleto|curso|Anuario)\b", r"\1 \2", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:este|el)\s+(esta\s+(?:gu[ií]a|lecci[oó]n))\b", r"\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(en|de)\s+este\s+mismo\s+la\s+(Lecci[oó]n)\b", r"\1 esta misma \2", t, flags=re.IGNORECASE)
    return t


def _format_single_ch_verse(ch: str, vs: str, include_cap_word: bool) -> str:
    ch = ch.strip()
    vs = vs.strip()
    vs_spoken = _format_verse_list_es(vs)
    prefix_cap = "capítulo " if include_cap_word else ""
    word_v = "versículo" if _is_single_verse(vs) else "versículos"
    return f"{prefix_cap}{ch}, {word_v} {vs_spoken}"


def expand_bible_names_and_abbrev(text: str, spoken_verses: bool) -> str:
    """
    Expande abreviaturas bíblicas (ej. '2 Tim. 2:15' -> '2 Timoteo 2:15').
    Si spoken_verses=True (para TTS), formatea además 'C:V1-V2' como 'C, versículos V1 al V2',
    soporta cadenas multicapítulo ('5:14-6:1', '3:6 y 11:5', '7:53 a 8:11'), horas de reloj,
    duraciones de video y abreviaturas editoriales.
    """
    t = text

    if spoken_verses:
        # 0. Horas y duraciones de video antes de citas bíblicas para que no se confundan
        t = re.sub(
            r"\b(\d{1,2}):00\s*a\.?\s*m\.?\s*(?:al?|[-–])\s*(\d{1,2}):00\s*a\.?\s*m\.?",
            lambda m: f"{int(m.group(1))} a {int(m.group(2))} de la mañana",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            r"\b(\d{1,2}):00\s*(?:al?|[-–])\s*(\d{1,2}):00\b",
            lambda m: f"de las {int(m.group(1))} a las {int(m.group(2))} horas",
            t,
        )
        t = re.sub(
            r"\b0?(\d{1,2}):00\s*[-–]\s*amanecer\b",
            lambda m: f"de las {int(m.group(1))} de la madrugada al amanecer",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(r"\b(\d{1,2}):00\b", lambda m: f"{int(m.group(1))} horas", t)

        # Tratados de la Misná ("Middot 3:1", "Meguilá 4:1, 2")
        t = re.sub(
            r"\b(Middot|Meguilá)\s+(\d{1,3}):(\d{1,3}(?:\s*[-–,]\s*\d{1,3})*)",
            lambda m: f"{m.group(1)} " + _format_single_ch_verse(m.group(2), m.group(3), include_cap_word=True),
            t,
        )

        # Duraciones de videos mencionadas en lecciones 15A/17A/17B
        if any(k in t.lower() for k in ("video", "videos", "biografía", "tarjeta de contacto")):
            def repl_vid_time(m):
                mins, secs = int(m.group(1)), int(m.group(2))
                return f"{mins} minuto{'s' if mins != 1 else ''} con {secs} segundo{'s' if secs != 1 else ''}"
            # Solo en paréntesis tras títulos de videos o en listas de duraciones ("5:11, 5:20 y 2:55")
            t = re.sub(
                r"\((\d{1,2}:[0-5]\d(?:\s*(?:,|y)\s*\d{1,2}:[0-5]\d)*)\)",
                lambda m: "(" + re.sub(r"(\d{1,2}):([0-5]\d)", repl_vid_time, m.group(1)) + ")",
                t,
            )

    def repl_bible(m):
        raw_bk = m.group(1)
        dot = m.group(2)
        cites = m.group(3)
        # Evitar falsos positivos de 2 letras en minúsculas sin punto ("es 2...", "os 1...")
        kl = raw_bk.lower()
        if len(kl) <= 2 and not dot and raw_bk.islower():
            return m.group(0)
        full_bk = BIBLE_BOOK_ABBR.get(kl, raw_bk)
        if not spoken_verses:
            return f"{full_bk} {cites}"

        # Separar cadenas de varios capítulos dentro del mismo libro ("3:6 y 11:5", "5:14-6:1", "7:53 a 8:11", "3:14; nota; 6:2-8")
        tokens = re.split(
            r"(\s*;\s*(?:nota\s*;\s*)?|\s*,\s*|\s+y\s+|\s+al?\s+|\s*[-–]\s*)(?=\d{1,3}:)",
            cites.strip(),
        )
        out_parts = []
        for idx_tok, tok in enumerate(tokens):
            if idx_tok % 2 == 0:
                # Es un segmento C:V
                if ":" in tok:
                    ch, vs = tok.split(":", 1)
                    out_parts.append(_format_single_ch_verse(ch, vs, include_cap_word=(idx_tok > 0)))
                else:
                    out_parts.append(tok.strip())
            else:
                sep = tok.strip().lower()
                if "nota" in sep:
                    out_parts.append(" (nota) y ")
                elif sep in ("-", "–", "a", "al"):
                    out_parts.append(" al ")
                elif sep == "y":
                    out_parts.append(" y ")
                elif sep == ";":
                    out_parts.append("; y " if idx_tok == len(tokens) - 2 else "; ")
                elif sep == ",":
                    out_parts.append(" y " if idx_tok == len(tokens) - 2 else ", ")
                else:
                    out_parts.append(f" {sep} ")
        return f"{full_bk} " + "".join(out_parts)

    t = BIBLE_CITE_RE.sub(repl_bible, t)

    # Citas de capítulo sin versículo con punto explícito ("Sal. 77", "Is. 53")
    def repl_chap_only(m):
        raw_bk = m.group(1)
        num = m.group(2)
        if len(raw_bk) <= 2 and raw_bk.islower():
            return m.group(0)
        full_bk = BIBLE_BOOK_ABBR.get(raw_bk.lower(), raw_bk)
        return f"{full_bk} capítulo {num}" if spoken_verses else f"{full_bk} {num}"
    t = BIBLE_CHAP_ONLY_RE.sub(repl_chap_only, t)

    if spoken_verses:
        # Citas breves sueltas de mismo libro que quedaron en el hilo (ej. "en 15:19, 17:14 y 17:16", "(2:13)", "tóte en 4:5", "en 4:8")
        def repl_orphan_cv(m):
            prefix = m.group(1) or ""
            ch = m.group(2)
            vs = m.group(3)
            return prefix + _format_single_ch_verse(ch, vs, include_cap_word=True)
        t = re.sub(
            r"(\ben\s+|\()(\d{1,3}):(\d{1,3}(?:\s*[-–,]\s*\d{1,3})*)",
            lambda m: (m.group(1) + _format_single_ch_verse(m.group(2), m.group(3), include_cap_word=True))
            if not re.search(r"minuto|segundo", t[m.end() : m.end() + 15].lower())
            else m.group(0),
            t,
        )
        t = re.sub(
            r"(,\s*|\s+y\s+)(\d{1,3}):(\d{1,3})\b",
            lambda m: m.group(1) + _format_single_ch_verse(m.group(2), m.group(3), include_cap_word=True),
            t,
        )

        # Abreviaturas editoriales comunes en TTS
        abbr_map = [
            (r"\bpágs\.\s*", "páginas "),
            (r"\bpág\.\s*", "página "),
            (r"\bpárrs\.\s*", "párrafos "),
            (r"\bpárr\.\s*", "párrafo "),
            (r"\bcaps\.\s*", "capítulos "),
            (r"\bcap\.\s*", "capítulo "),
            (r"\blecs?\.\s*", "lección "),
            (r"\bapén\.\s*", "apéndice "),
            (r"\brec\.\s*", "recuadro "),
            (r"\bsec\.\s*", "sección "),
            (r"\bnúm\.\s*", "número "),
            (r"\bvv\.\s*", "versículos "),
            (r"\bv\.\s*", "versículo "),
            (r"\bcfr?\.\s*", "compárese con "),
            (r"\blit\.\s*", "literalmente "),
            (r"\bgr\.\s*", "en griego "),
            (r"\bheb\.\s*", "en hebreo "),
            (r"\baram\.\s*", "en arameo "),
            (r"\betc\.", "etcétera"),
            (r"\ba\.\s*E\.\s*C\.", "antes de nuestra era"),
            (r"\bE\.\s*C\.", "de nuestra era"),
        ]
        for pat, rep in abbr_map:
            t = re.sub(pat, rep, t)

        # Rangos numéricos después de páginas/párrafos/capítulos/versículos: "14-19" -> "14 al 19", "16, 17" -> "16 y 17"
        t = re.sub(
            r"\b(páginas?|párrafos?|capítulos?|versículos?|lección)\s+(\d+)\s*[-–]\s*(\d+)\b",
            r"\1 \2 al \3",
            t,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            r"\b(páginas?|párrafos?|capítulos?|versículos?)\s+(\d+),\s*(\d+)\b",
            r"\1 \2 y \3",
            t,
            flags=re.IGNORECASE,
        )
        # También rangos residuales como "14-19" entre comas o espacio después de números
        t = re.sub(r"\b(\d{1,3})\s*[-–]\s*(\d{1,3})\b", r"\1 al \2", t)

        # Limpiar puntuación doble
        t = re.sub(r"([.,;:])\s*\1+", r"\1", t)
        t = re.sub(r"[ \t]+", " ", t).strip()

    return t


def _is_single_verse(vs_str: str) -> bool:
    return bool(re.fullmatch(r"\d+[a-zA-Z]?", vs_str.strip()))


def _format_verse_list_es(vs_str: str) -> str:
    # Ej. "16, 17" -> "16 y 17", "14-19" -> "14 al 19", "4, 6-8, 12" -> "4, 6 al 8 y 12"
    tokens = [x.strip() for x in vs_str.split(",") if x.strip()]
    out = []
    for tok in tokens:
        if re.search(r"[-–]", tok):
            out.append(re.sub(r"\s*[-–]\s*", " al ", tok))
        else:
            out.append(tok)
    if len(out) <= 1:
        return out[0] if out else vs_str
    return ", ".join(out[:-1]) + " y " + out[-1]


def full_clean_text(text: str, spoken_verses: bool) -> str:
    t = scrub_meta_audit_noise(text)
    t = clean_unicode_for_tts_and_reading(t)
    t = expand_pub_dates_and_titles(t)
    t = expand_bible_names_and_abbrev(t, spoken_verses=spoken_verses)
    t = re.sub(r"([:;])\.", r"\1", t)
    t = re.sub(r"^[\s•\-.,;:—–]+", "", t).strip()
    return re.sub(r"[ \t]+", " ", t).strip()


def clean_question_for_student(raw_q: str, unit_class: str, headings: dict, spoken_verses: bool) -> str:
    """Construye la pregunta hablada natural para el Alumno sin revelar respuestas internas ni ruido."""
    q = strip_all_page_markers(raw_q or "").strip()
    # Quitar spoiler "(Respuesta de la lección: ...)" en Lesson 6A Situaciones 3a-3h
    q = re.sub(r"\(\s*Respuesta\s+de\s+la\s+lección\s*:[^)]*\)\s*\.?", "", q, flags=re.IGNORECASE).strip()
    q = re.sub(r"^[•✔◆\-\s]+", "", q).strip()
    q = re.sub(r"^Nota al instructor\s*:\s*", "", q, flags=re.IGNORECASE).strip()
    q = re.sub(r"^EXPERIENCIA\s*:\s*", "", q, flags=re.IGNORECASE).strip()

    h3 = strip_all_page_markers(headings.get("h3", ""))
    h4 = strip_all_page_markers(headings.get("h4", ""))
    h5 = strip_all_page_markers(headings.get("h5", ""))

    # Expandir si es una viñeta o subpregunta fragmentaria cuyo enunciado padre está en h4 o h3
    is_frag = bool(q) and (len(q) < 55 and "?" not in q or (len(q) < 70 and (q[0].islower() or not q.startswith("¿"))))
    if is_frag:
        stem = ""
        for cand in (h4, h3):
            if "—" in cand:
                after = cand.split("—", 1)[1].strip()
                if "?" in after or len(after) > 25:
                    stem = after
                    break
        if stem and stem.lower() not in q.lower():
            # Quitar lista larga de textos entre paréntesis del stem para no repetir todos
            stem_clean = re.sub(r"\([^)]+\)\s*$", "", stem).strip(" :.")
            if stem_clean.endswith("?"):
                if q.endswith("?"):
                    q = f"{stem_clean} En concreto: ¿qué hay de {q[0].lower() + q[1:]}"
                else:
                    q = f"{stem_clean} En concreto, respecto a {q}."
            else:
                if q.endswith("?") and not q.startswith("¿"):
                    q = f"{stem_clean}: ¿qué ocurre con {q[0].lower() + q[1:]}"
                else:
                    q = f"{stem_clean}: {q}."

    # Limpiar separador " ? • " de sub-viñetas
    q = re.sub(r"\?\s*•\s*", "? En cuanto a ", q)
    q = re.sub(r":\s*•\s*", ": ", q)
    q = re.sub(r"\s*•\s*", ". ", q)

    q_clean = full_clean_text(q, spoken_verses=spoken_verses)

    # Adaptar según tipo de unidad
    if "unit-instructor" in unit_class:
        return f"En esta parte hay una nota para el instructor: {ensure_sentence_end(q_clean)} ¿Cómo debemos aplicar esta pauta?"
    if "unit-experience" in unit_class:
        if "?" in q_clean:
            return q_clean
        return f"El manual comparte una experiencia: {ensure_sentence_end(q_clean)} ¿Qué enseñanza práctica nos deja?"
    if "unit-review" in unit_class:
        if "?" in q_clean:
            return q_clean
        return f"En este punto de repaso la lección destaca lo siguiente: {ensure_sentence_end(q_clean)} ¿Por qué es tan importante y qué respaldo bíblico tenemos?"
    if "unit-project" in unit_class:
        return f"La lección propone un proyecto de investigación: {ensure_sentence_end(q_clean)} ¿Cómo podemos llevarlo a cabo?"

    # Si es "Lee Salmo ..." / "Lee 1 Corintios ..."
    if re.match(r"^Lee\s+", q_clean, flags=re.IGNORECASE):
        return f"El estudio nos invita a leer este pasaje: {q_clean.rstrip(' .:')}. ¿Qué aprendemos de estos versículos?"

    # Situaciones de Lección 6A ("Ora en voz alta...", "Da clases de la Biblia...")
    if "Situación " in h5 or "Situación " in h4:
        return f"Veamos este caso práctico: una hermana {q_clean[0].lower() + q_clean[1:].rstrip(' .:')}. ¿Tiene que cubrirse la cabeza en esa situación y por qué?"

    # Si todavía no termina en pregunta (e.g. "Pregunta" escrita como instrucción o punto del taller)
    if "?" not in q_clean:
        return f"En el siguiente punto de la lección se plantea esto: {q_clean.rstrip(' .:')}. ¿Cuál es la explicación y su fundamento en las referencias?"

    return q_clean


def ensure_sentence_end(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    if s[-1] not in ".!?»”\":;—":
        s += "."
    return s


def _has_real_alnum(s: str) -> bool:
    return sum(1 for c in s if c.isalnum()) >= 4


def format_teacher_explanation(unit: dict, spoken_verses: bool) -> list[str]:
    """Devuelve lista de párrafos limpios con Respuesta + Análisis Bíblico + Perlas/Referencias."""
    ans_list = []
    for p in unit["answer-section"]:
        if is_pure_audit_item(p):
            continue
        pc = full_clean_text(p, spoken_verses=spoken_verses)
        if pc and _has_real_alnum(pc) and not is_pure_audit_item(pc):
            ans_list.append(ensure_sentence_end(pc))

    bib_list = []
    for b in unit["bib-section"]:
        if is_pure_audit_item(b):
            continue
        bc = full_clean_text(b, spoken_verses=spoken_verses)
        if bc and _has_real_alnum(bc) and not is_pure_audit_item(bc):
            bib_list.append(ensure_sentence_end(bc))

    prl_list = []
    for pr in unit["pearls-section"]:
        if is_pure_audit_item(pr):
            continue
        prc = full_clean_text(pr, spoken_verses=spoken_verses)
        if prc and _has_real_alnum(prc) and not is_pure_audit_item(prc):
            prl_list.append(ensure_sentence_end(prc))

    paras = []
    if ans_list:
        paras.append(" ".join(ans_list))
    if bib_list:
        paras.append("Analicemos los textos bíblicos de respaldo: " + " ".join(bib_list))
    if prl_list:
        paras.append("Además, nuestras referencias de apoyo añaden lo siguiente: " + " ".join(prl_list))
    return paras


def slugify_es(text: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s


def _split_oversized_paragraph(para: str, cap: int) -> list[str]:
    """Divide un párrafo enorme únicamente en límites de oración o cláusula <= cap."""
    if len(para) <= cap:
        return [para]
    sents = re.split(r"(?<=[.!?»”])\s+", para)
    if len(sents) == 1:
        sents = re.split(r"(?<=[;:])\s+", para)
    chunks = []
    cur = ""
    for s in sents:
        if not s:
            continue
        cand = (cur + " " + s).strip() if cur else s
        if cur and len(cand) > cap:
            chunks.append(cur)
            cur = s
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    return chunks


def _split_long_block(blk: str, cap: int = 5600) -> list[tuple[str, bool]]:
    """
    Si un bloque excede `cap`, lo divide limpiamente entre párrafos u oraciones.
    Retorna lista de tuplas (texto_chunk, es_continuacion_maestro).
    """
    if len(blk) <= cap:
        return [(blk, False)]

    paras_raw = [p.strip() for p in blk.split("\n\n") if p.strip()]
    paras = []
    for idx_r, p in enumerate(paras_raw):
        p_cap = cap
        if idx_r == 1 and paras_raw[0].startswith("Alumno:"):
            p_cap = max(1200, cap - len(paras_raw[0]) - 2)
        paras.extend(_split_oversized_paragraph(p, cap=p_cap))

    out = []
    cur_p = []
    cur_len = 0
    is_cont = False
    for p in paras:
        sep = 2 if cur_p else 0
        if cur_p and (cur_len + sep + len(p) > cap):
            out.append(("\n\n".join(cur_p), is_cont))
            p_body = p[len("Maestro:"):].strip() if p.startswith("Maestro:") else p
            cur_p = [f"Maestro: {p_body}"]
            cur_len = len(cur_p[0])
            is_cont = True
        else:
            cur_p.append(p)
            cur_len += sep + len(p)
    if cur_p:
        out.append(("\n\n".join(cur_p), is_cont))
    return out


def split_script_parts(turns: list[str], lesson_title: str, max_chars: int = 6200) -> list[str]:
    """
    Divide los turnos del guion Maestro/Alumno en partes <= max_chars para Google AI Studio
    cortando entre pares completos (Alumno -> Maestro) o, si una introducción o respuesta
    individual excede el límite, entre párrafos/oraciones completas con transición natural.
    """
    # Agrupar en bloques atómicos: Intro (primer turno de Maestro solo), o par (Alumno + Maestro)
    raw_blocks = []
    i = 0
    while i < len(turns):
        t = turns[i]
        if t.startswith("Alumno:") and i + 1 < len(turns) and turns[i + 1].startswith("Maestro:"):
            raw_blocks.append(t + "\n\n" + turns[i + 1])
            i += 2
        else:
            raw_blocks.append(t)
            i += 1

    # Expandir únicamente bloques individuales > max_chars en sub-bloques <= 5,600 chars
    blocks = []
    for rb in raw_blocks:
        blocks.extend(_split_long_block(rb, cap=min(5600, max_chars - 300)))

    parts = []
    cur_blocks = []
    cur_len = 0
    part_idx = 1

    for blk, is_cont in blocks:
        sep_len = 2 if cur_blocks else 0
        if cur_blocks and (cur_len + sep_len + len(blk) > max_chars):
            parts.append("\n\n".join(cur_blocks))
            part_idx += 1
            if is_cont:
                header = (
                    f"Maestro: Continuamos con el repaso de {lesson_title}, parte {part_idx}.\n\n"
                    f"Alumno: Muy bien, ¿qué más debemos tener presente sobre este punto?"
                )
            else:
                header = (
                    f"Maestro: Continuamos con el repaso de {lesson_title}, parte {part_idx}.\n\n"
                    f"Alumno: Muy bien, sigamos con las siguientes preguntas."
                )
            cur_blocks = [header, blk]
            cur_len = len(header) + 2 + len(blk)
        else:
            cur_blocks.append(blk)
            cur_len += sep_len + len(blk)

    if cur_blocks:
        parts.append("\n\n".join(cur_blocks))

    return parts


def split_clean_source_parts(sections: list[str], header: str, max_chars: int = 28000) -> list[str]:
    """Divide fuentes largas para NotebookLM si superan ~28,000 caracteres."""
    parts = []
    cur = [header]
    cur_len = len(header)
    for sec in sections:
        if len(cur) > 1 and (cur_len + 2 + len(sec) > max_chars):
            parts.append("\n\n".join(cur))
            cur = [f"{header} (Continuación — Parte {len(parts) + 1})", sec]
            cur_len = len(cur[0]) + 2 + len(sec)
        else:
            cur.append(sec)
            cur_len += 2 + len(sec)
    if len(cur) > 1 or not parts:
        parts.append("\n\n".join(cur))
    return parts


def build_audio_epub(lessons_data: list[dict], out_epub_path: str):
    """Construye un EPUB 3 ultralimpio (solo diálogo Maestro / Alumno) para ElevenReader / Apple Books."""
    work_dir = f"/tmp/epub_audio_{os.getpid()}"
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(os.path.join(work_dir, "META-INF"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "OEBPS", "text"), exist_ok=True)

    with open(os.path.join(work_dir, "mimetype"), "w", encoding="utf-8") as f:
        f.write("application/epub+zip")

    with open(os.path.join(work_dir, "META-INF", "container.xml"), "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '  <rootfiles>\n'
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            '  </rootfiles>\n'
            '</container>\n'
        )

    manifest_items = []
    spine_items = []
    nav_links = []

    # Portada
    cover_xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="es" lang="es">\n'
        '<head><meta charset="utf-8"/><title>Guía de Estudio en Diálogo — Maestro y Alumno</title></head>\n'
        '<body>\n'
        '<h1>Guía de Estudio en Diálogo (Maestro y Alumno)</h1>\n'
        '<p>Repaso audible por lecciones del curso Cumple completamente tu ministerio. '
        'Cada lección comienza con una introducción del tema por el Maestro, seguida por todas las preguntas '
        'de la lección en orden con su respuesta directa y su explicación basada en los textos bíblicos y referencias descargadas.</p>\n'
        '</body></html>\n'
    )
    with open(os.path.join(work_dir, "OEBPS", "text", "portada.xhtml"), "w", encoding="utf-8") as f:
        f.write(cover_xhtml)
    manifest_items.append('<item id="portada" href="text/portada.xhtml" media-type="application/xhtml+xml"/>')
    spine_items.append('<itemref idref="portada"/>')
    nav_links.append('<li><a href="text/portada.xhtml">Introducción</a></li>')

    for idx, ldata in enumerate(lessons_data, 1):
        fname = f"lec_{idx:02d}.xhtml"
        title_esc = html_mod.escape(ldata["title_clean"])
        body_paras = [f"<h1>{title_esc}</h1>"]
        for turn in ldata["turns"]:
            for sub_p in turn.split("\n\n"):
                sub_p = sub_p.strip()
                if not sub_p:
                    continue
                if sub_p.startswith("Maestro:"):
                    rest = html_mod.escape(sub_p[len("Maestro:"):].strip())
                    body_paras.append(f"<p><strong>Maestro:</strong> {rest}</p>")
                elif sub_p.startswith("Alumno:"):
                    rest = html_mod.escape(sub_p[len("Alumno:"):].strip())
                    body_paras.append(f"<p><strong>Alumno:</strong> {rest}</p>")
                else:
                    body_paras.append(f"<p>{html_mod.escape(sub_p)}</p>")

        xhtml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="es" lang="es">\n'
            f'<head><meta charset="utf-8"/><title>{title_esc}</title></head>\n'
            '<body>\n' + "\n".join(body_paras) + '\n</body></html>\n'
        )
        with open(os.path.join(work_dir, "OEBPS", "text", fname), "w", encoding="utf-8") as f:
            f.write(xhtml)
        item_id = f"lec_{idx:02d}"
        manifest_items.append(f'<item id="{item_id}" href="text/{fname}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="{item_id}"/>')
        nav_links.append(f'<li><a href="text/{fname}">{title_esc}</a></li>')

    nav_xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="es" lang="es">\n'
        '<head><meta charset="utf-8"/><title>Índice</title></head>\n'
        '<body><nav epub:type="toc" id="toc"><h1>Índice de lecciones</h1><ol>\n'
        + "\n".join(nav_links) +
        '\n</ol></nav></body></html>\n'
    )
    with open(os.path.join(work_dir, "OEBPS", "nav.xhtml"), "w", encoding="utf-8") as f:
        f.write(nav_xhtml)

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    book_uuid = str(uuid.uuid4())
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="uid">urn:uuid:{book_uuid}</dc:identifier>\n'
        '    <dc:title>Guía de Estudio Precursores — Repaso Audible Maestro y Alumno</dc:title>\n'
        '    <dc:language>es</dc:language>\n'
        f'    <meta property="dcterms:modified">{now_iso}</meta>\n'
        '  </metadata>\n'
        '  <manifest>\n'
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
        + "\n    ".join(manifest_items) +
        '\n  </manifest>\n'
        '  <spine>\n'
        + "\n    ".join(spine_items) +
        '\n  </spine>\n'
        '</package>\n'
    )
    with open(os.path.join(work_dir, "OEBPS", "content.opf"), "w", encoding="utf-8") as f:
        f.write(opf)

    with zipfile.ZipFile(out_epub_path, "w") as zout:
        zout.write(os.path.join(work_dir, "mimetype"), "mimetype", compress_type=zipfile.ZIP_STORED)
        for root, _, files in os.walk(work_dir):
            for fn in sorted(files):
                full_p = os.path.join(root, fn)
                rel_p = os.path.relpath(full_p, work_dir)
                if rel_p == "mimetype":
                    continue
                zout.write(full_p, rel_p, compress_type=zipfile.ZIP_DEFLATED)
    shutil.rmtree(work_dir, ignore_errors=True)


def write_prompts_guide(guide_path: str, prompt_nlm_short: str):
    content = f"""GUÍA EXACTA Y PROMPTS PARA CREAR TUS REPASOS EN AUDIO (NOTEBOOKLM Y ALTERNATIVAS)
================================================================================

1. RESPUESTA SOBRE NOTEBOOKLM (https://notebooklm.google.com)
------------------------------------------------------------
¿Se puede hacer en NotebookLM?
  SÍ, pero debes saber por qué antes no te gustó y cómo configurarlo bien:
  - NotebookLM usa por defecto un formato tipo podcast ("Análisis profundo") con 2 anfitriones que
    resumen el documento completo en ~10-15 minutos. Si le subes un Dossier crudo (~400 KB con texto
    de bases de datos y códigos como it_S, rsg_S, nwtsty_S), se distrae mencionando "el dossier" y se
    salta la mayoría de las preguntas.
  - Además, el cuadro "Personalizar" (ícono de lápiz junto a Resumen de audio) tiene un LÍMITE ESTRICTO
    DE 500 CARACTERES, y en español no existe el botón de duración "Más largo".

CONFIGURACIÓN EXACTA EN NOTEBOOKLM:
  Paso 1: Crea un cuaderno y sube los archivos limpios de la carpeta "01_Fuentes_Limpias_Para_NotebookLM".
          (Si la lección es larga y está dividida en Parte 1 / Parte 2 en "01B_Partes_Para_NotebookLM_Lecciones_Largas",
          usa esas partes para que NotebookLM no comprima tantas preguntas en pocos minutos).
  Paso 2: En la columna izquierda ("Fuentes"), DEJA MARCADA ÚNICAMENTE 1 SOLA LECCIÓN (o 1 Parte)
          antes de generar el audio. ¡Nunca generes el audio con varias lecciones activas a la vez!
  Paso 3: En el panel derecho ("Studio" -> "Resumen de audio"), haz clic en el ícono del LÁPIZ ("Personalizar"):
          - Formato: Análisis profundo
          - Idioma: Español
          - Pega este PROMPT EXACTO ({len(prompt_nlm_short)} caracteres, cabe completo en el límite de 500):

{prompt_nlm_short}

  NOTA IMPORTANTE SOBRE NOTEBOOKLM:
  Aun con este prompt, NotebookLM redacta su propio guion en vivo y tiende a agrupar preguntas si la fuente
  es extensa. Si quieres FIDELIDAD EXACTA DEL 100 % (que el Alumno haga TODAS las preguntas de la lección
  en orden exacto sin omitir ninguna y el Maestro responda citando cada referencia sin inventar nada), usa
  la Opción 2 o la Opción 3 de abajo con los archivos que ya preparé para ti.

--------------------------------------------------------------------------------
2. LA MEJOR ALTERNATIVA WEB GRATUITA (100 % EXACTA Y CON 2 VOCES: MAESTRO Y ALUMNO)
   Google AI Studio — Speech Generation (https://aistudio.google.com/generate-speech)
--------------------------------------------------------------------------------
Por qué es mejor para lo que buscas:
  - Totalmente gratis con tu cuenta de Google en navegador (PC, Mac o iPad).
  - Modo "Multi-speaker audio" (2 voces distintas en español: Maestro y Alumno).
  - CERO alucinaciones ni omisiones: lee literalmente el guion preparado con todas tus preguntas y respuestas.
  - Te permite descargar el archivo de audio (.wav / .mp3) para escucharlo en el auto o caminando.

Cómo usarlo en 4 pasos:
  Paso 1: Abre https://aistudio.google.com/generate-speech
  Paso 2: En el panel derecho cambia "Single-speaker" a "Multi-speaker".
          - Speaker 1 Name: Maestro  (elige una voz masculina serena y cálida, ej. Orus o Charon)
          - Speaker 2 Name: Alumno   (elige otra voz clara y natural, ej. Puck, Fenrir o Kore)
  Paso 3: En "Style instructions" (si aparece arriba del cuadro), escribe:
          Lee en español latinoamericano natural, con ritmo pausado, cálido y respetuoso, como un hermano
          cristiano experimentado repasando una lección bíblica con un estudiante.
  Paso 4: Abre cualquier archivo de la carpeta "03_Partes_Para_Google_AI_Studio/" (ya vienen divididos en
          bloques de <= 6,200 caracteres para que no se corten en el límite de 10 minutos ni de tokens),
          copia todo su texto, pégalo y pulsa Run / Generate.

--------------------------------------------------------------------------------
3. LA OPCIÓN MÁS CÓMODA PARA ESCUCHAR EN IPAD / TELÉFONO SIN COPIAR Y PEGAR
   App ElevenReader (https://elevenreader.io — iOS / iPadOS / Android)
--------------------------------------------------------------------------------
  - Te da 10 horas gratis al mes de voces neurales ultranaturales en español.
  - Importa el archivo único "Guia_Estudio_Maestro_Alumno_Audio.epub".
  - Elige cualquier lección en el índice y escúchala seguida mientras manejas o caminas.
  - Alternativas parecidas gratuitas:
      * Microsoft Edge ("Leer en voz alta" abriendo cualquier archivo .txt o el .epub).
      * iPad / iPhone (Apple Books con el .epub + Accesibilidad "Contenido leído").
      * ElevenLabs Studio (https://elevenlabs.io/app/studio) para generar audiolibros multipista MP3.

--------------------------------------------------------------------------------
4. PROMPT MAESTRO COMPLETO PARA USAR CON OTRA IA (GEMINI, CHATGPT, CLAUDE O CHAT DE NOTEBOOKLM)
--------------------------------------------------------------------------------
Si quieres entregarle un archivo de "01_Fuentes_Limpias_Para_NotebookLM/" a Gemini, ChatGPT, Claude
o al Chat de NotebookLM para pedirle un guion adaptado (ej. más breve o enfocado en ciertos textos):

  Eres un asistente de estudio para la Escuela del Servicio de Precursor de los Testigos de Jehová.
  Tu única fuente de verdad es el archivo de la lección adjunto. TIENES PROHIBIDO agregar doctrinas
  externas, citar versículos que no figuren en la fuente, mencionar códigos de archivo o inventar datos.

  Transforma el archivo adjunto en un guion listo para audio con exactamente dos hablantes:
  - "Maestro:" (un hermano testigo de Jehová experimentado, respetuoso, claro y didáctico).
  - "Alumno:" (un estudiante aplicado que pregunta siguiendo el orden exacto de la lección).

  Estructura obligatoria:
  1. Empieza con "Maestro:" saludando brevemente e introduciendo el tema completo de la lección según
     la sección de Introducción o Texto temático del archivo adjunto.
  2. Luego, para CADA pregunta o punto de estudio de la lección en su orden exacto:
     - "Alumno:" formula la pregunta de la lección con naturalidad.
     - "Maestro:" da primero la respuesta directa y luego explica el porqué respaldándose exclusivamente
       en el análisis bíblico y las referencias descargadas en la fuente, mencionando los libros bíblicos
       y publicaciones por su nombre completo en español.
"""
    with open(guide_path, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    prompt_nlm_short = (
        "Formato estricto de repaso teocrático con dos voces: 1) Un maestro testigo de Jehová "
        "introduce el tema de la lección. 2) Un alumno hace en orden las preguntas exactas de la lección. "
        "3) El maestro da primero la respuesta directa y luego explica el porqué citando solo los "
        "versículos y publicaciones de la fuente. Prohibido inventar textos, añadir información externa "
        "o usar tono de pódcast informal. Español natural."
    )
    assert len(prompt_nlm_short) <= 500, f"Prompt NotebookLM excede 500 chars: {len(prompt_nlm_short)}"

    dir_nlm = os.path.join(OUT_ROOT, "01_Fuentes_Limpias_Para_NotebookLM")
    dir_nlm_split = os.path.join(OUT_ROOT, "01B_Partes_Para_NotebookLM_Lecciones_Largas")
    dir_scripts = os.path.join(OUT_ROOT, "02_Guiones_Maestro_Alumno_MultiVoz")
    dir_aistudio = os.path.join(OUT_ROOT, "03_Partes_Para_Google_AI_Studio")

    shutil.rmtree(OUT_ROOT, ignore_errors=True)
    for d in (dir_nlm, dir_nlm_split, dir_scripts, dir_aistudio):
        os.makedirs(d, exist_ok=True)

    lessons_for_epub = []
    stats = {"units": 0, "aistudio_parts": 0, "nlm_splits": 0}

    with zipfile.ZipFile(EPUB_IN, "r") as z:
        xhtml_files = [
            f for f in z.namelist()
            if f.startswith("OEBPS/text/")
            and f not in ("OEBPS/text/portada.xhtml", "OEBPS/text/indice_general.xhtml")
        ]

        for idx, xf in enumerate(xhtml_files, 1):
            raw_xhtml = z.read(xf).decode("utf-8")
            m_title = re.search(r"<title>(.*?)</title>", raw_xhtml, flags=re.DOTALL)
            raw_title = html_mod.unescape(strip_all_page_markers(m_title.group(1))) if m_title else xf
            title_clean = re.sub(r"\s+", " ", raw_title).strip()
            slug = slugify_es(title_clean)[:55].rstrip("_")

            parser = LessonHTMLParser()
            parser.feed(raw_xhtml)

            # Reconstruir secuencia con contexto de encabezados
            headings = {"h2": "", "h3": "", "h4": "", "h5": ""}
            units_with_ctx = []
            for ev in parser.events:
                if ev[0] == "heading":
                    _, lvl, htxt = ev
                    htxt = strip_all_page_markers(htxt)
                    headings[lvl] = htxt
                    if lvl == "h2":
                        headings["h3"] = headings["h4"] = headings["h5"] = ""
                    elif lvl == "h3":
                        headings["h4"] = headings["h5"] = ""
                    elif lvl == "h4":
                        headings["h5"] = ""
                elif ev[0] == "unit":
                    units_with_ctx.append((dict(headings), ev[1]))

            stats["units"] += len(units_with_ctx)

            # ------------------------------------------------------------------
            # A) Construir archivo limpio para NotebookLM / Gemini / ChatGPT
            # ------------------------------------------------------------------
            nlm_sections = []
            last_h2 = ""
            last_h3 = ""
            for hctx, u in units_with_ctx:
                lines = []
                if hctx["h2"] and hctx["h2"] != last_h2:
                    last_h2 = hctx["h2"]
                    h2_clean = full_clean_text(last_h2, spoken_verses=False)
                    if h2_clean:
                        lines.append(f"=== {h2_clean.upper()} ===")
                if hctx["h3"] and hctx["h3"] != last_h3:
                    last_h3 = hctx["h3"]
                    h3_clean = full_clean_text(last_h3, spoken_verses=False)
                    if h3_clean:
                        lines.append(f"--- {h3_clean} ---")

                q_label = hctx["h5"] or hctx["h4"] or hctx["h3"] or "Punto de estudio"
                q_label_clean = full_clean_text(q_label, spoken_verses=False)
                q_raw = " ".join(u["question-box"]).strip()
                q_raw = re.sub(r"\(\s*Respuesta\s+de\s+la\s+lección\s*:[^)]*\)\s*\.?", "", q_raw, flags=re.IGNORECASE)
                q_clean = full_clean_text(q_raw, spoken_verses=False)

                lines.append(f"[{q_label_clean}]")
                if q_clean:
                    lines.append(f"Pregunta o punto de la lección: {q_clean}")

                teacher_paras = format_teacher_explanation(u, spoken_verses=False)
                if teacher_paras:
                    lines.append("Respuesta y explicación con fundamento bíblico y referencias:")
                    for tp in teacher_paras:
                        lines.append(tp)

                nlm_sections.append("\n".join(lines))

            nlm_header = f"{title_clean}\nCurso: Escuela del Servicio de Precursor (Guía limpia de estudio basada únicamente en las Escrituras y las referencias descargadas)."
            nlm_full = nlm_header + "\n\n" + "\n\n".join(nlm_sections) + "\n"
            nlm_fname = f"{idx:02d}_{slug}.txt"
            with open(os.path.join(dir_nlm, nlm_fname), "w", encoding="utf-8") as f:
                f.write(nlm_full)

            # Si supera ~26,000 caracteres, crear también partes para NotebookLM en 01B
            if len(nlm_full) > 26000:
                nlm_parts = split_clean_source_parts(nlm_sections, title_clean, max_chars=24000)
                for p_i, p_txt in enumerate(nlm_parts, 1):
                    pfname = f"{idx:02d}_{slug}_Parte_{p_i}_de_{len(nlm_parts)}.txt"
                    with open(os.path.join(dir_nlm_split, pfname), "w", encoding="utf-8") as f:
                        f.write(p_txt + "\n")
                    stats["nlm_splits"] += 1

            # ------------------------------------------------------------------
            # B) Construir Guion de Diálogo Maestro / Alumno (TTS + Audio EPUB)
            # ------------------------------------------------------------------
            # Separar los puntos de apertura (INTRODUCCIÓN / En este taller verás) antes de la sección PREGUNTAS
            intro_chunks = []
            qa_units = []
            seen_questions_section = False
            is_review_day = "Repaso del día" in title_clean
            is_workshop = "Taller " in title_clean
            is_closing_or_back = idx in (35, 36)

            for hctx, u in units_with_ctx:
                h2_u = hctx["h2"].upper()
                h3_l = hctx["h3"].lower()
                h4_l = hctx["h4"].lower()
                if "PREGUNTAS" in h2_u or "pregunta " in h4_l or "situación " in h4_l or "situación " in hctx["h5"].lower():
                    seen_questions_section = True

                # En días de repaso, el Texto temático (h3 "Texto temático") abre cada lección repasada;
                # en lecciones normales, lo que está bajo INTRODUCCIÓN o "En este taller verás" abre el tema.
                is_intro_unit = (
                    (not seen_questions_section and not is_review_day and not is_closing_or_back and ("INTRODUCCIÓN" in h2_u or "EN ESTE TALLER VERÁS" in h2_u) and not (is_workshop and "INTRODUCCIÓN" in h2_u))
                )
                # Caso especial Taller: "En este taller verás:" -> objetivos introductorios
                if is_workshop and "EN ESTE TALLER VERÁS" in h2_u:
                    is_intro_unit = True

                if is_intro_unit:
                    intro_chunks.append((hctx, u))
                else:
                    qa_units.append((hctx, u))

            # Redactar la apertura del Maestro
            title_spoken = full_clean_text(title_clean, spoken_verses=True)
            intro_parts = [f"Bienvenidos a este repaso de estudio. Hoy examinaremos {title_spoken}."]
            if intro_chunks:
                if is_workshop:
                    intro_parts.append("Al inicio de este taller veremos los siguientes objetivos prácticos:")
                else:
                    intro_parts.append("Para introducir el tema de la lección, repasemos primero los puntos de apertura y su base bíblica.")

                for ic_ctx, ic_u in intro_chunks:
                    pt_raw = " ".join(ic_u["question-box"]).strip()
                    pt_clean = full_clean_text(re.sub(r"^[•✔◆\-\s]+", "", pt_raw), spoken_verses=True)
                    t_paras = format_teacher_explanation(ic_u, spoken_verses=True)
                    if is_workshop:
                        if pt_clean:
                            intro_parts.append(ensure_sentence_end(pt_clean))
                        intro_parts.extend(t_paras)
                    else:
                        # Para no repetir literalmente si Respuesta directa ya cubre, combinamos concisamente
                        if t_paras:
                            intro_parts.extend(t_paras)
                        elif pt_clean:
                            intro_parts.append(ensure_sentence_end(pt_clean))

            turns = []
            pending_transition = ""

            if qa_units:
                # Si estamos por empezar preguntas en una lección normal
                first_h2 = qa_units[0][0]["h2"]
                first_h3 = qa_units[0][0]["h3"]
                if not is_review_day and not is_closing_or_back and not is_workshop:
                    intro_parts.append("Con esta base en mente, pasemos ahora a las preguntas y puntos de estudio de la lección.")

            turns.append("Maestro: " + "\n\n".join(intro_parts))

            prev_h2 = ""
            prev_h3 = ""
            for q_idx, (hctx, u) in enumerate(qa_units):
                h2_s = hctx["h2"]
                h3_s = hctx["h3"]

                # Caso especial Día de Repaso: cada sublección tiene Texto temático como unidad previa a Pregunta 1, 2...
                if is_review_day and h3_s.lower().startswith("texto temático") and "pregunta" not in hctx["h4"].lower():
                    h2_spoken = full_clean_text(h2_s, spoken_verses=True)
                    h3_spoken = full_clean_text(h3_s, spoken_verses=True)
                    t_paras = format_teacher_explanation(u, spoken_verses=True)
                    block_intro = f"Pasemos ahora a repasar {h2_spoken}, con su {h3_spoken}."
                    if t_paras:
                        block_intro += "\n\n" + "\n\n".join(t_paras)
                    # Anexarlo al turno del Maestro anterior (o si fue el primer turno, combinar en Maestro)
                    if turns and turns[-1].startswith("Maestro:"):
                        turns[-1] = turns[-1] + "\n\n" + block_intro
                    else:
                        turns.append("Maestro: " + block_intro)
                    prev_h2 = h2_s
                    prev_h3 = h3_s
                    continue

                # Transición de apartado h2 / h3 al final del turno previo del Maestro para mantener alternancia estricta
                trans_bits = []
                if h2_s and h2_s != prev_h2 and h2_s.upper() != "PREGUNTAS" and not is_review_day:
                    h2_sp = full_clean_text(h2_s, spoken_verses=True).strip()
                    h2_sp = re.sub(r"\bte\s+ayudar[áa]\s+a\s*:$", "te ayudará a lograr estos objetivos", h2_sp, flags=re.IGNORECASE).rstrip(" .:")
                    if h2_sp and h2_sp.upper() != "INTRODUCCIÓN":
                        trans_bits.append(f"Pasamos ahora al apartado {h2_sp}.")
                if h3_s and h3_s != prev_h3 and not h3_s.lower().startswith("texto temático"):
                    h3_sp = full_clean_text(h3_s, spoken_verses=True).strip().rstrip(" .:")
                    if h3_sp:
                        trans_bits.append(f"Veamos la sección titulada: {h3_sp}.")
                if trans_bits and turns and turns[-1].startswith("Maestro:"):
                    turns[-1] = turns[-1] + " " + " ".join(trans_bits)
                prev_h2 = h2_s
                prev_h3 = h3_s

                # Pregunta del Alumno
                q_raw = " ".join(u["question-box"]).strip()
                q_student = clean_question_for_student(q_raw, u["unit_class"], hctx, spoken_verses=True)
                turns.append(f"Alumno: {q_student}")

                # Explicación del Maestro
                t_paras = format_teacher_explanation(u, spoken_verses=True)
                if t_paras:
                    turns.append("Maestro: " + "\n\n".join(t_paras))
                else:
                    turns.append("Maestro: Este punto se responde directamente con la reflexión práctica de la lección.")

            # Cierre amable al final del último turno del Maestro
            closing_line = f"Con esto completamos el repaso de {title_spoken}."
            if turns and turns[-1].startswith("Maestro:"):
                turns[-1] = turns[-1] + f"\n\n{closing_line}"
            else:
                turns.append(f"Maestro: {closing_line}")

            full_script = "\n\n".join(turns) + "\n"
            scr_fname = f"{idx:02d}_{slug}_Guion_Maestro_Alumno.txt"
            with open(os.path.join(dir_scripts, scr_fname), "w", encoding="utf-8") as f:
                f.write(full_script)

            # Partes divididas para Google AI Studio (<= 6,200 chars por archivo)
            ai_parts = split_script_parts(turns, title_spoken, max_chars=6200)
            for p_i, p_txt in enumerate(ai_parts, 1):
                if len(ai_parts) == 1:
                    afname = f"{idx:02d}_{slug}_AIStudio.txt"
                else:
                    afname = f"{idx:02d}_{slug}_AIStudio_Parte_{p_i:02d}_de_{len(ai_parts):02d}.txt"
                with open(os.path.join(dir_aistudio, afname), "w", encoding="utf-8") as f:
                    f.write(p_txt.strip() + "\n")
                stats["aistudio_parts"] += 1

            lessons_for_epub.append({
                "title_clean": title_clean,
                "turns": turns,
            })

            print(
                f"[{idx:02d}] {title_clean[:50]:50s} | "
                f"NLM={len(nlm_full)//1024:3d}KB | "
                f"Guion={len(full_script)//1024:3d}KB | "
                f"Partes AIStudio={len(ai_parts)}"
            )

    # Escribir guía y prompts en todos los directorios relevantes
    for gdir in (OUT_ROOT, dir_nlm, dir_scripts, dir_aistudio):
        write_prompts_guide(os.path.join(gdir, "00_GUIA_Y_PROMPTS_EXACTOS.txt"), prompt_nlm_short)

    # Construir EPUB limpio para ElevenReader / Apple Books
    build_audio_epub(lessons_for_epub, AUDIO_EPUB_OUT)

    # Empaquetar ZIPs limpios y descargables
    zip_nlm = "/home/antigravity/pt14_S_Fuentes_Limpias_NotebookLM.zip"
    zip_audio = "/home/antigravity/pt14_S_Guiones_Audio_Maestro_Alumno.zip"

    with zipfile.ZipFile(zip_nlm, "w", zipfile.ZIP_DEFLATED) as zn:
        for root, _, files in os.walk(dir_nlm):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                zn.write(p, arcname=os.path.join("01_Fuentes_Limpias_Para_NotebookLM", os.path.relpath(p, dir_nlm)))
        for root, _, files in os.walk(dir_nlm_split):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                zn.write(p, arcname=os.path.join("01B_Partes_Para_NotebookLM_Lecciones_Largas", os.path.relpath(p, dir_nlm_split)))
        zn.write(os.path.join(OUT_ROOT, "00_GUIA_Y_PROMPTS_EXACTOS.txt"), arcname="00_GUIA_Y_PROMPTS_EXACTOS.txt")

    with zipfile.ZipFile(zip_audio, "w", zipfile.ZIP_DEFLATED) as za:
        for root, _, files in os.walk(dir_scripts):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                za.write(p, arcname=os.path.join("02_Guiones_Maestro_Alumno_MultiVoz", os.path.relpath(p, dir_scripts)))
        for root, _, files in os.walk(dir_aistudio):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                za.write(p, arcname=os.path.join("03_Partes_Para_Google_AI_Studio", os.path.relpath(p, dir_aistudio)))
        za.write(os.path.join(OUT_ROOT, "00_GUIA_Y_PROMPTS_EXACTOS.txt"), arcname="00_GUIA_Y_PROMPTS_EXACTOS.txt")

    print("\n=== RESUMEN FINAL ===")
    print(f"Unidades procesadas: {stats['units']}")
    print(f"Partes largas NotebookLM: {stats['nlm_splits']}")
    print(f"Partes listas Google AI Studio: {stats['aistudio_parts']}")
    print(f"EPUB limpio audio: {AUDIO_EPUB_OUT} ({os.path.getsize(AUDIO_EPUB_OUT)/1024:.1f} KB)")
    print(f"ZIP NotebookLM: {zip_nlm} ({os.path.getsize(zip_nlm)/1024:.1f} KB)")
    print(f"ZIP Guiones Audio: {zip_audio} ({os.path.getsize(zip_audio)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
