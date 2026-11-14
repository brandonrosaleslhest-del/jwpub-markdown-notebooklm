#!/usr/bin/env python3
"""
Generador del Libro Duplicado (.jwpub) con Respuestas Incrustadas (Opción B)
y Copias de Seguridad (.jwlibrary) fusionadas con el respaldo de iPad del usuario
(UserdataBackup_2026-11-14_iPad.jwlibrary):

1. pt14g_Guia_Con_Respuestas_Incrustadas_S.jwpub
   - Libro duplicado independiente (KeySymbol = 'pt14g', MepsDocumentId = +12000: 1102026860..1102026909)
   - Titulado: "Libro de los precursores — Guía de Estudio (pt14g)"
   - Incrusta directamente debajo de cada pregunta/párrafo (en bloques nativos <div class="blockTxt">)
     la Respuesta Directa, el Análisis de los Textos Bíblicos y el Análisis de las Referencias y Perlas
     generados por el agente.
   - Conserva intactos todos los párrafos <p id="pX" data-pid="X"> con sus enlaces interactivos a la
     Biblia (jwpub://b/...) y referencias (jwpub://p/...), recálcula al byte exacto DocumentParagraph
     (BeginPosition, EndPosition) y Document.ContentLength, y re-encripta con AES-128-CBC + zlib.

2. pt14g_Libro_Duplicado_Paginas_Limpias_S.jwpub
   - Mismo libro duplicado (KeySymbol = 'pt14g', mismos IDs 1102026860..1102026909), pero con las páginas
     sin texto incrustado (por si el usuario desea alternar entre páginas con respuestas incrustadas y páginas limpias).

3. Copias .jwlibrary fusionadas con UserdataBackup_2026-11-14_iPad.jwlibrary (Schema v16):
   - Conservan intactas las 33 notas personales (incluidas las 26 notas en el libro pt14 original),
     los 11,720 subrayados personales (incluidos los 133 subrayados en pt14 original) y los 161 InputFields.
   - Vinculan al libro duplicado 'pt14g' los 1,097 subrayados por colores (UserMark + BlockRange) y las notas:
     a) Backup_iPad_pt14g_Solo_Respaldo_en_Notas.jwlibrary (Ideal para usar con el libro de respuestas incrustadas:
        en la nota lateral muestra directamente las Notas de Estudio nwtsty, Textos Adicionales y Extractos Literales
        de Respaldo sin repetir la respuesta que ya está incrustada en la página).
     b) Backup_iPad_pt14g_Dos_Tarjetas_Respuesta_y_Respaldo.jwlibrary (Tarjeta 1: Respuesta y Análisis;
        Tarjeta 2: Respaldo y Notas Extras).
     c) Backup_iPad_pt14g_Una_Tarjeta_Todo_en_Uno.jwlibrary (1 sola tarjeta completa por párrafo en pt14g).
"""

import ctypes
import datetime
import hashlib
import html as html_mod
import io
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
import zipfile
import zlib

from extract_nwtsty import (
    _cipher,
    _libcrypto,
    derive_key_iv,
    fast_decrypt,
    strip_tags,
)
from build_jwlibrary_backup import ensure_dbs_extracted
from build_jwlibrary_from_agent_epub import (
    count_jwl_tokens,
    parse_agent_epub_chapters,
)

# Configurar encriptación AES-128-CBC en OpenSSL (libcrypto.so.3)
_EVP_EncryptInit_ex = _libcrypto.EVP_EncryptInit_ex
_EVP_EncryptInit_ex.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
]
_EVP_EncryptUpdate = _libcrypto.EVP_EncryptUpdate
_EVP_EncryptUpdate.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_char_p,
    ctypes.c_int,
]
_EVP_EncryptFinal_ex = _libcrypto.EVP_EncryptFinal_ex
_EVP_EncryptFinal_ex.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.POINTER(ctypes.c_int),
]
_enc_ctx = _libcrypto.EVP_CIPHER_CTX_new()


def fast_encrypt(html_str: str, key: bytes, iv: bytes) -> bytes:
    """Comprime con zlib (nivel 6) y encripta con AES-128-CBC (PKCS#7 padding estándar de JWPUB)."""
    comp = zlib.compress(html_str.encode("utf-8"), 6)
    _EVP_EncryptInit_ex(_enc_ctx, _cipher, None, key, iv)
    out_buf = ctypes.create_string_buffer(len(comp) + 32)
    out_len1 = ctypes.c_int(0)
    out_len2 = ctypes.c_int(0)
    _EVP_EncryptUpdate(_enc_ctx, out_buf, ctypes.byref(out_len1), comp, len(comp))
    ptr2 = ctypes.cast(ctypes.addressof(out_buf) + out_len1.value, ctypes.c_char_p)
    _EVP_EncryptFinal_ex(_enc_ctx, ptr2, ctypes.byref(out_len2))
    return out_buf.raw[: out_len1.value + out_len2.value]


MEPS_ID_OFFSET = 12000  # 1102014860..1102014909 -> 1102026860..1102026909
NEW_SYMBOL = "pt14g"
NEW_TITLE = "“Cumple completamente tu ministerio” — Guía de Estudio"
NEW_SHORT_TITLE = "Precursores (Guía de Estudio)"
NEW_DISPLAY_TITLE = "Libro de los precursores — Guía de Estudio (pt14g)"

# Borde lateral semántico (se adapta a modo claro, sepia y oscuro en JW Library)
COLOR_HEX_MAP = {
    1: "#d4a017",  # Amarillo (Preguntas)
    2: "#3b8a3e",  # Verde (Puntos principales)
    3: "#3a7bd5",  # Azul (Repaso ◆)
    4: "#c85a82",  # Rosa (Nota al instructor)
    5: "#d97724",  # Naranja (Experiencia)
    6: "#7b52ab",  # Morado (Proyecto)
}


def format_embedded_html_block(units_for_pid: list) -> str:
    """
    Construye el bloque HTML nativo (<div class="blockTxt"> sin atributo data-pid)
    que se incrusta directamente debajo del párrafo/pregunta en la página del libro duplicado.
    """
    blocks = []
    for u in units_for_pid:
        border_hex = COLOR_HEX_MAP.get(u["color_idx"], "#4a6da7")
        heading_esc = html_mod.escape(u["heading"])
        main_body = u["main_body"]
        if not main_body:
            continue

        # Separar las secciones de main_body (Respuesta Directa, Análisis de los Textos, Perlas)
        inner_ps = []
        sections = main_body.split("\n\n")
        for sec in sections:
            lines = [ln.strip() for ln in sec.strip().split("\n") if ln.strip()]
            if not lines:
                continue
            hdr = lines[0]
            rest = lines[1:]
            if hdr.startswith("✅ RESPUESTA DIRECTA"):
                body_html = "<br/>".join(html_mod.escape(x) for x in rest)
                inner_ps.append(
                    f'<p class="sn"><strong>✅ Respuesta ({heading_esc}):</strong> {body_html}</p>'
                )
            elif hdr.startswith("📖 ANÁLISIS DE LOS TEXTOS"):
                body_html = "<br/>".join(html_mod.escape(x) for x in rest)
                inner_ps.append(
                    f'<p class="sn"><strong>📖 Análisis de los textos:</strong><br/>{body_html}</p>'
                )
            elif hdr.startswith("💎 ANÁLISIS DE LAS REFERENCIAS Y PERLAS"):
                body_html = "<br/>".join(html_mod.escape(x) for x in rest)
                inner_ps.append(
                    f'<p class="sn"><strong>💎 Análisis de referencias y perlas:</strong><br/>{body_html}</p>'
                )
            else:
                body_html = "<br/>".join(html_mod.escape(x) for x in lines)
                inner_ps.append(f'<p class="sn">{body_html}</p>')

        if inner_ps:
            blocks.append(
                f'\r\n<div class="blockTxt" style="border-left: 3px solid {border_hex}; padding-left: 10px; margin: 6px 0 14px 0;">\r\n'
                + "\r\n".join(inner_ps)
                + "\r\n</div>"
            )
    return "".join(blocks)


def inject_embedded_answers_into_html(doc_html: str, units: list) -> str:
    """
    Inserta los bloques <div class="blockTxt"> inmediatamente después de la etiqueta de cierre
    del párrafo <tag ... data-pid="X">...</tag> correspondiente, sin alterar ningún data-pid.
    """
    if not units:
        return doc_html

    # Agrupar unidades por el último pid cubierto (para preguntas de 2 párrafos seguidos)
    by_target_pid = {}
    for u in units:
        target_pid = u["block_ranges"][-1][0] if u["block_ranges"] else u["pid"]
        by_target_pid.setdefault(target_pid, []).append(u)

    def repl(m):
        full_elem = m.group(0)
        pid = int(m.group(2))
        if pid not in by_target_pid:
            return full_elem
        embedded_html = format_embedded_html_block(by_target_pid[pid])
        return full_elem + embedded_html

    return re.sub(
        r'<([a-zA-Z0-9]+)\b[^>]*\bdata-pid="(\d+)"[^>]*>.*?</\1>',
        repl,
        doc_html,
        flags=re.DOTALL,
    )


def build_duplicated_jwpub(
    orig_jwpub_path: str,
    chapters_data: list,
    embed_answers_in_pages: bool,
    out_jwpub_path: str,
):
    """
    Construye un archivo .jwpub duplicado (Symbol='pt14g', MepsDocumentId + 12000)
    con o sin respuestas incrustadas en las páginas.
    """
    work_dir = f"/tmp/jwpub_dup_{os.getpid()}_{'embed' if embed_answers_in_pages else 'clean'}"
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)

    with zipfile.ZipFile(orig_jwpub_path, "r") as z_outer:
        manifest = json.loads(z_outer.read("manifest.json").decode("utf-8"))
        contents_bytes = z_outer.read("contents")

    inner_dir = os.path.join(work_dir, "inner")
    os.makedirs(inner_dir, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(contents_bytes), "r") as z_inner:
        z_inner.extractall(inner_dir)

    old_db_path = os.path.join(inner_dir, "pt14_S.db")
    new_db_name = f"{NEW_SYMBOL}_S.db"
    new_db_path = os.path.join(inner_dir, new_db_name)
    shutil.move(old_db_path, new_db_path)

    key_old, iv_old, _ = derive_key_iv(1, "pt14", 2023, "0")
    key_new, iv_new, _ = derive_key_iv(1, NEW_SYMBOL, 2023, "0")

    # Mapa de doc_id -> units de capítulos
    units_by_doc_id = {ch["doc_id"]: ch["units"] for ch in chapters_data}

    conn = sqlite3.connect(new_db_path)
    cur = conn.cursor()

    # 1. Obtener el conjunto de los 39 MepsDocumentId originales del libro
    orig_doc_meps_ids = set(
        r[0] for r in cur.execute("SELECT MepsDocumentId FROM Document").fetchall()
    )

    # 2. Actualizar tabla Publication con el nuevo símbolo 'pt14g' y títulos
    cur.execute(
        """
        UPDATE Publication
        SET Title = ?,
            ShortTitle = ?,
            DisplayTitle = ?,
            ReferenceTitle = ?,
            UndatedReferenceTitle = ?,
            Symbol = ?,
            UndatedSymbol = ?,
            UniqueSymbol = ?,
            EnglishSymbol = ?,
            UniqueEnglishSymbol = ?,
            RootSymbol = ?
        """,
        (
            NEW_TITLE,
            NEW_SHORT_TITLE,
            NEW_DISPLAY_TITLE,
            NEW_SHORT_TITLE,
            NEW_SHORT_TITLE,
            NEW_SYMBOL,
            NEW_SYMBOL,
            NEW_SYMBOL,
            NEW_SYMBOL,
            NEW_SYMBOL,
            NEW_SYMBOL,
        ),
    )

    # Actualizar título raíz en PublicationViewItem y PublicationViewItemField
    cur.execute(
        "UPDATE PublicationViewItem SET Title = ? WHERE PublicationViewItemId IN (1, 2)",
        (NEW_TITLE,),
    )
    cur.execute(
        "UPDATE PublicationViewItemField SET Value = ? WHERE PublicationViewItemId IN (1, 2) AND Type = 'name'",
        (NEW_TITLE,),
    )

    # 3. Actualizar InternalLink y Hyperlink internos al nuevo MepsDocumentId (+12000)
    for il_id, link_str, cap_str, meps_id in cur.execute(
        "SELECT InternalLinkId, Link, Caption, MepsDocumentId FROM InternalLink"
    ).fetchall():
        new_meps_id = meps_id + MEPS_ID_OFFSET if meps_id in orig_doc_meps_ids else meps_id
        new_link = re.sub(
            r"1102014([89]\d\d)",
            lambda m: str(int(m.group(0)) + MEPS_ID_OFFSET)
            if int(m.group(0)) in orig_doc_meps_ids
            else m.group(0),
            link_str or "",
        )
        new_cap = (cap_str or "").replace("pt14 ", f"{NEW_SYMBOL} ")
        cur.execute(
            "UPDATE InternalLink SET Link = ?, Caption = ?, MepsDocumentId = ? WHERE InternalLinkId = ?",
            (new_link, new_cap, new_meps_id, il_id),
        )

    for hl_id, link_str, meps_id in cur.execute(
        "SELECT HyperlinkId, Link, MepsDocumentId FROM Hyperlink"
    ).fetchall():
        new_meps_id = (
            meps_id + MEPS_ID_OFFSET
            if (meps_id is not None and meps_id in orig_doc_meps_ids)
            else meps_id
        )
        new_link = re.sub(
            r"1102014([89]\d\d)",
            lambda m: str(int(m.group(0)) + MEPS_ID_OFFSET)
            if int(m.group(0)) in orig_doc_meps_ids
            else m.group(0),
            link_str or "",
        )
        if new_link != link_str or new_meps_id != meps_id:
            cur.execute(
                "UPDATE Hyperlink SET Link = ?, MepsDocumentId = ? WHERE HyperlinkId = ?",
                (new_link, new_meps_id, hl_id),
            )

    # 4. Actualizar Document (MepsDocumentId, Content, ContentLength) y DocumentParagraph (BeginPosition, EndPosition)
    docs = cur.execute("SELECT DocumentId, MepsDocumentId, Content FROM Document").fetchall()
    for did, old_meps_id, blob in docs:
        new_meps_id = old_meps_id + MEPS_ID_OFFSET
        doc_html = fast_decrypt(blob, key_old, iv_old)

        # Actualizar enlaces internos jwpub://p/S:1102014xxx/ en el HTML
        doc_html = re.sub(
            r"(jwpub://p/S:)(1102014[89]\d\d)(/)",
            lambda m: f"{m.group(1)}{int(m.group(2)) + MEPS_ID_OFFSET}{m.group(3)}"
            if int(m.group(2)) in orig_doc_meps_ids
            else m.group(0),
            doc_html,
        )

        # Si es la opción B (respuestas incrustadas), inyectar los bloques <div class="blockTxt">
        if embed_answers_in_pages and did in units_by_doc_id:
            doc_html = inject_embedded_answers_into_html(doc_html, units_by_doc_id[did])

        new_hb = doc_html.encode("utf-8")
        new_clen = len(new_hb)
        new_blob = fast_encrypt(doc_html, key_new, iv_new)

        cur.execute(
            "UPDATE Document SET MepsDocumentId = ?, Content = ?, ContentLength = ? WHERE DocumentId = ?",
            (new_meps_id, new_blob, new_clen, did),
        )

        # Recalcular BeginPosition y EndPosition exactos para cada data-pid en DocumentParagraph
        pid_spans = {}
        for m in re.finditer(
            rb'<([a-zA-Z0-9]+)\b[^>]*\bdata-pid="(\d+)"[^>]*>.*?</\1>',
            new_hb,
            flags=re.DOTALL,
        ):
            pid = int(m.group(2))
            pid_spans[pid] = (m.start(), m.end())

        for dp_id, p_idx, b_old, e_old in cur.execute(
            "SELECT DocumentParagraphId, ParagraphIndex, BeginPosition, EndPosition FROM DocumentParagraph WHERE DocumentId = ?",
            (did,),
        ).fetchall():
            if b_old is None:
                continue
            assert p_idx in pid_spans, f"Falta data-pid={p_idx} en DocumentId={did}"
            b_new, e_new = pid_spans[p_idx]
            cur.execute(
                "UPDATE DocumentParagraph SET BeginPosition = ?, EndPosition = ? WHERE DocumentParagraphId = ?",
                (b_new, e_new, dp_id),
            )

    # 5. Re-encriptar Extract.Content con la nueva llave AES de 'pt14g'
    for ext_id, ext_blob in cur.execute("SELECT ExtractId, Content FROM Extract").fetchall():
        if ext_blob:
            ext_html = fast_decrypt(ext_blob, key_old, iv_old)
            new_ext_blob = fast_encrypt(ext_html, key_new, iv_new)
            cur.execute("UPDATE Extract SET Content = ? WHERE ExtractId = ?", (new_ext_blob, ext_id))

    conn.commit()
    cur.execute("VACUUM")
    conn.close()

    # 6. Empaquetar 'contents' (ZIP interno) y recalcular hashes SHA-1 y SHA-256 en manifest.json
    with open(new_db_path, "rb") as f:
        db_sha1 = hashlib.sha1(f.read()).hexdigest()

    contents_buf = io.BytesIO()
    expanded_size = 0
    with zipfile.ZipFile(contents_buf, "w", zipfile.ZIP_DEFLATED) as z_in_out:
        for fname in sorted(os.listdir(inner_dir)):
            fpath = os.path.join(inner_dir, fname)
            expanded_size += os.path.getsize(fpath)
            z_in_out.write(fpath, fname)

    new_contents_bytes = contents_buf.getvalue()
    contents_sha256 = hashlib.sha256(new_contents_bytes).hexdigest()

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["name"] = os.path.basename(out_jwpub_path)
    manifest["hash"] = contents_sha256
    manifest["timestamp"] = now_iso
    manifest["expandedSize"] = expanded_size

    pub_m = manifest["publication"]
    pub_m["fileName"] = new_db_name
    pub_m["title"] = NEW_TITLE
    pub_m["shortTitle"] = NEW_SHORT_TITLE
    pub_m["displayTitle"] = NEW_DISPLAY_TITLE
    pub_m["referenceTitle"] = NEW_SHORT_TITLE
    pub_m["undatedReferenceTitle"] = NEW_SHORT_TITLE
    pub_m["titleRich"] = NEW_TITLE
    pub_m["displayTitleRich"] = NEW_DISPLAY_TITLE
    pub_m["referenceTitleRich"] = NEW_SHORT_TITLE
    pub_m["undatedReferenceTitleRich"] = NEW_SHORT_TITLE
    pub_m["symbol"] = NEW_SYMBOL
    pub_m["uniqueEnglishSymbol"] = NEW_SYMBOL
    pub_m["uniqueSymbol"] = NEW_SYMBOL
    pub_m["undatedSymbol"] = NEW_SYMBOL
    pub_m["englishSymbol"] = NEW_SYMBOL
    pub_m["rootSymbol"] = NEW_SYMBOL
    pub_m["hash"] = db_sha1
    pub_m["timestamp"] = now_iso

    with zipfile.ZipFile(out_jwpub_path, "w", zipfile.ZIP_DEFLATED) as z_out:
        z_out.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        # 'contents' suele almacenarse en el zip externo
        z_out.writestr("contents", new_contents_bytes)

    shutil.rmtree(work_dir, ignore_errors=True)
    return os.path.getsize(out_jwpub_path)


def build_ipad_merged_jwlibrary(
    base_ipad_jwlibrary_path: str,
    chapters_data: list,
    mode: str,
    out_path: str,
):
    """
    mode:
      - 'backing_only': 1 nota por párrafo en pt14g enfocada en el Respaldo y Notas Extras
                        (ideal cuando las respuestas y análisis ya están incrustados en las páginas de pt14g).
      - 'two_cards':    2 notas por párrafo en pt14g (Tarjeta 1: Respuesta y Análisis; Tarjeta 2: Respaldo y Notas Extras).
      - 'one_card':     1 nota unificada por párrafo en pt14g (Respuesta + Análisis + Respaldo todo en uno).
    """
    work_dir = f"/tmp/jwl_ipad_{os.getpid()}_{mode}"
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)

    with zipfile.ZipFile(base_ipad_jwlibrary_path, "r") as z:
        z.extractall(work_dir)

    db_path = os.path.join(work_dir, "userData.db")
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today_str = now_iso.split("T")[0]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cur.execute("PRAGMA journal_mode = DELETE")
    cur.execute("PRAGMA foreign_keys = OFF")

    next_loc_id = cur.execute("SELECT COALESCE(MAX(LocationId), 0) + 1 FROM Location").fetchone()[0]
    next_um_id = cur.execute("SELECT COALESCE(MAX(UserMarkId), 0) + 1 FROM UserMark").fetchone()[0]
    next_br_id = cur.execute("SELECT COALESCE(MAX(BlockRangeId), 0) + 1 FROM BlockRange").fetchone()[0]
    next_note_id = cur.execute("SELECT COALESCE(MAX(NoteId), 0) + 1 FROM Note").fetchone()[0]
    next_tag_id = cur.execute("SELECT COALESCE(MAX(TagId), 0) + 1 FROM Tag").fetchone()[0]
    next_tagmap_id = cur.execute("SELECT COALESCE(MAX(TagMapId), 0) + 1 FROM TagMap").fetchone()[0]

    tag_cache = {}
    tag_pos_counter = {}
    for tid, ttype, tname in cur.execute("SELECT TagId, Type, Name FROM Tag"):
        if ttype == 1:
            tag_cache[tname] = tid
            max_pos = cur.execute(
                "SELECT COALESCE(MAX(Position), -1) FROM TagMap WHERE TagId = ?", (tid,)
            ).fetchone()[0]
            tag_pos_counter[tid] = max_pos + 1

    def get_or_create_tag(tname: str) -> int:
        nonlocal next_tag_id
        if tname in tag_cache:
            return tag_cache[tname]
        tid = next_tag_id
        next_tag_id += 1
        cur.execute("INSERT INTO Tag (TagId, Type, Name) VALUES (?, 1, ?)", (tid, tname))
        tag_cache[tname] = tid
        tag_pos_counter[tid] = 0
        return tid

    def attach_tag(note_id: int, tid: int):
        nonlocal next_tagmap_id
        pos = tag_pos_counter[tid]
        tag_pos_counter[tid] = pos + 1
        cur.execute(
            "INSERT INTO TagMap (TagMapId, PlaylistItemId, LocationId, NoteId, TagId, Position) VALUES (?, NULL, NULL, ?, ?, ?)",
            (next_tagmap_id, note_id, tid, pos),
        )
        next_tagmap_id += 1

    tag_respuestas_id = get_or_create_tag("1. Respuestas y Análisis (pt14g)")
    tag_respaldo_id = get_or_create_tag("2. Respaldo y Notas Extras (pt14g)")

    added_notes = 0
    added_usermarks = 0

    for ch in chapters_data:
        new_meps_doc_id = ch["meps_doc_id"] + MEPS_ID_OFFSET
        clean_title = ch["clean_title"]
        tag_name = f"pt14g · {ch['tag_name']}"
        units = ch["units"]
        if not units:
            continue

        # Crear Location para el capítulo en el libro duplicado 'pt14g'
        loc_id = next_loc_id
        next_loc_id += 1
        cur.execute(
            """
            INSERT INTO Location (
                LocationId, BookNumber, ChapterNumber, DocumentId, Track,
                IssueTagNumber, KeySymbol, MepsLanguage, Type, Title, Specialty, Edition
            ) VALUES (?, NULL, NULL, ?, NULL, 0, ?, 1, 0, ?, NULL, NULL)
            """,
            (loc_id, new_meps_doc_id, NEW_SYMBOL, clean_title),
        )

        lesson_tag_id = get_or_create_tag(tag_name)

        for u in units:
            pid = u["pid"]
            color_idx = u["color_idx"]
            block_ranges = u["block_ranges"]
            main_title = u["main_title"]
            main_body = u["main_body"]
            backing_title = u["backing_title"]
            backing_body = u["backing_body"]

            # Crear UserMark + BlockRange para subrayar el párrafo en pt14g
            um_id = next_um_id
            next_um_id += 1
            um_guid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"jwl-um-pt14g-{new_meps_doc_id}-{pid}-{u['heading']}"))
            cur.execute(
                """
                INSERT INTO UserMark (
                    UserMarkId, ColorIndex, LocationId, StyleIndex, UserMarkGuid, Version
                ) VALUES (?, ?, ?, 0, ?, 1)
                """,
                (um_id, color_idx, loc_id, um_guid),
            )
            added_usermarks += 1

            for cpid, st_tok, end_tok in block_ranges:
                cur.execute(
                    """
                    INSERT INTO BlockRange (
                        BlockRangeId, BlockType, Identifier, StartToken, EndToken, UserMarkId
                    ) VALUES (?, 1, ?, ?, ?, ?)
                    """,
                    (next_br_id, cpid, st_tok, end_tok, um_id),
                )
                next_br_id += 1

            if mode == "backing_only":
                # En la Opción B, la respuesta y el análisis ya están incrustados en la página del libro pt14g.
                # Por tanto, la tarjeta lateral muestra directamente el Respaldo y Notas Extras (sin repetir lo de la página).
                # Si una unidad no tiene citas extra ni extractos, ponemos su título conciso con una nota breve.
                note_id = next_note_id
                next_note_id += 1
                note_guid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"jwl-note-pt14g-bonly-{new_meps_doc_id}-{pid}-{u['heading']}"))
                if backing_body:
                    title_use = backing_title
                    body_use = backing_body
                else:
                    title_use = main_title
                    body_use = "*(Respuesta y análisis incrustados directamente en la página del libro)*"
                cur.execute(
                    """
                    INSERT INTO Note (
                        NoteId, Guid, UserMarkId, LocationId, Title, Content,
                        LastModified, Created, BlockType, BlockIdentifier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (note_id, note_guid, um_id, loc_id, title_use, body_use, now_iso, now_iso, pid),
                )
                attach_tag(note_id, tag_respaldo_id)
                attach_tag(note_id, lesson_tag_id)
                added_notes += 1

            elif mode == "two_cards":
                # Tarjeta 1: Respuesta Directa + Análisis
                note1_id = next_note_id
                next_note_id += 1
                note1_guid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"jwl-note-pt14g-c1-{new_meps_doc_id}-{pid}-{u['heading']}"))
                cur.execute(
                    """
                    INSERT INTO Note (
                        NoteId, Guid, UserMarkId, LocationId, Title, Content,
                        LastModified, Created, BlockType, BlockIdentifier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (note1_id, note1_guid, um_id, loc_id, main_title, main_body, now_iso, now_iso, pid),
                )
                attach_tag(note1_id, tag_respuestas_id)
                attach_tag(note1_id, lesson_tag_id)
                added_notes += 1

                # Tarjeta 2: Respaldo y Notas Extras
                if backing_body:
                    note2_id = next_note_id
                    next_note_id += 1
                    note2_guid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"jwl-note-pt14g-c2-{new_meps_doc_id}-{pid}-{u['heading']}"))
                    cur.execute(
                        """
                        INSERT INTO Note (
                            NoteId, Guid, UserMarkId, LocationId, Title, Content,
                            LastModified, Created, BlockType, BlockIdentifier
                        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (note2_id, note2_guid, loc_id, backing_title, backing_body, now_iso, now_iso, pid),
                    )
                    attach_tag(note2_id, tag_respaldo_id)
                    attach_tag(note2_id, lesson_tag_id)
                    added_notes += 1

            else:  # 'one_card'
                full_body = main_body
                if backing_body:
                    full_body += f"\n\n{'━'*26}\n📎 RESPALDO Y NOTAS EXTRAS\n{'━'*26}\n\n{backing_body}"
                note1_id = next_note_id
                next_note_id += 1
                note1_guid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"jwl-note-pt14g-1c-{new_meps_doc_id}-{pid}-{u['heading']}"))
                cur.execute(
                    """
                    INSERT INTO Note (
                        NoteId, Guid, UserMarkId, LocationId, Title, Content,
                        LastModified, Created, BlockType, BlockIdentifier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (note1_id, note1_guid, um_id, loc_id, main_title, full_body, now_iso, now_iso, pid),
                )
                attach_tag(note1_id, tag_respuestas_id)
                if backing_body:
                    attach_tag(note1_id, tag_respaldo_id)
                attach_tag(note1_id, lesson_tag_id)
                added_notes += 1

    cur.execute("UPDATE LastModified SET LastModified = ?", (now_iso,))
    cur.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    cur.execute("VACUUM")
    conn.close()

    for wal_ext in ("-wal", "-shm"):
        p = db_path + wal_ext
        if os.path.exists(p):
            os.remove(p)

    with open(db_path, "rb") as f:
        db_sha256 = hashlib.sha256(f.read()).hexdigest()

    manifest_path = os.path.join(work_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    manifest["name"] = os.path.basename(out_path)
    manifest["creationDate"] = today_str
    manifest["userDataBackup"]["lastModifiedDate"] = today_str
    manifest["userDataBackup"]["hash"] = db_sha256
    manifest["userDataBackup"]["databaseName"] = "userData.db"

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.write(manifest_path, "manifest.json")
        zout.write(db_path, "userData.db")
        thumb_path = os.path.join(work_dir, "default_thumbnail.png")
        if os.path.exists(thumb_path):
            zout.write(thumb_path, "default_thumbnail.png")

    conn_check = sqlite3.connect(db_path)
    tot_notes = conn_check.execute("SELECT COUNT(*) FROM Note").fetchone()[0]
    tot_um = conn_check.execute("SELECT COUNT(*) FROM UserMark").fetchone()[0]
    conn_check.close()
    shutil.rmtree(work_dir, ignore_errors=True)
    return added_notes, added_usermarks, tot_notes, tot_um, os.path.getsize(out_path)


def main():
    t0 = time.time()
    ensure_dbs_extracted()
    epub_path = "/home/antigravity/Guia_Estudio_Precursores_Agente.epub"
    pt14_db_path = "/tmp/dbs/pt14_S.db"
    orig_jwpub_path = "/home/antigravity/upload/pt14_S.jwpub"
    base_ipad_jwl = "/home/antigravity/UserdataBackup_2026-11-14_iPad.jwlibrary"

    conn_pt = sqlite3.connect(pt14_db_path)
    key_pt, iv_pt, _ = derive_key_iv(1, "pt14", 2023, "0")
    chapters_data = parse_agent_epub_chapters(epub_path, conn_pt, key_pt, iv_pt)
    conn_pt.close()
    total_units = sum(len(ch["units"]) for ch in chapters_data)
    print(f"Unidades extraídas del EPUB del agente: {total_units} en {len(chapters_data)} capítulos")

    # 1. Generar los dos archivos .jwpub duplicados ('pt14g')
    jwpub_embed = "/home/antigravity/pt14g_Guia_Con_Respuestas_Incrustadas_S.jwpub"
    sz_embed = build_duplicated_jwpub(orig_jwpub_path, chapters_data, True, jwpub_embed)
    print(f"Generado JWPUB Opción B (respuestas incrustadas): {os.path.basename(jwpub_embed)} ({sz_embed / (1024*1024):.2f} MB)")

    jwpub_clean = "/home/antigravity/pt14g_Libro_Duplicado_Paginas_Limpias_S.jwpub"
    sz_clean = build_duplicated_jwpub(orig_jwpub_path, chapters_data, False, jwpub_clean)
    print(f"Generado JWPUB Opción A (páginas limpias): {os.path.basename(jwpub_clean)} ({sz_clean / (1024*1024):.2f} MB)")

    # 2. Generar las copias .jwlibrary fusionadas con UserdataBackup_2026-11-14_iPad.jwlibrary
    configs = [
        ("backing_only", "/home/antigravity/Backup_iPad_pt14g_Solo_Respaldo_en_Notas.jwlibrary"),
        ("two_cards", "/home/antigravity/Backup_iPad_pt14g_Dos_Tarjetas_Respuesta_y_Respaldo.jwlibrary"),
        ("one_card", "/home/antigravity/Backup_iPad_pt14g_Una_Tarjeta_Todo_en_Uno.jwlibrary"),
    ]
    for mode, out_p in configs:
        n_add, um_add, n_tot, um_tot, sz = build_ipad_merged_jwlibrary(
            base_ipad_jwl, chapters_data, mode, out_p
        )
        print(
            f"Generado ({mode}): {os.path.basename(out_p)} ({sz / (1024*1024):.2f} MB) "
            f"-> +{n_add} notas en pt14g, +{um_add} subrayados en pt14g | Total DB iPad: {n_tot} notas, {um_tot} subrayados"
        )

    print(f"Completado en {time.time() - t0:.2f}s")


if __name__ == "__main__":
    main()
