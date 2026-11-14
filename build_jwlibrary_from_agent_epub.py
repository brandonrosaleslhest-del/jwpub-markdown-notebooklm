#!/usr/bin/env python3
"""
Generador de Copias de Seguridad (.jwlibrary, Schema v16) basadas en el EPUB del Agente
(Guia_Estudio_Precursores_Agente.epub) y fusionadas con el respaldo personal del usuario
(UserdataBackup_2026-11-14_Samsung_SM-F971B.jwlibrary).

Características clave solicitadas por el usuario:
1. NO repite lo que el libro (pt14_S) ya dice dentro de las notas.
2. Usa SUBRAYADOS (UserMark + BlockRange) en cada párrafo/pregunta del libro con código de colores
   semántico para unir visualmente el texto del libro con la nota del agente en el panel de estudio:
   - Amarillo (1): Preguntas de estudio
   - Verde (2): Puntos de Introducción
   - Azul (3): Puntos de repaso / transición (◆)
   - Rosa (4): Notas al instructor
   - Naranja (5): Experiencias destacadas
   - Morado (6): Proyectos de investigación
3. Coloca la Respuesta directa, el Análisis de los textos bíblicos y el Análisis de referencias / perlas.
4. Para los textos bíblicos que YA están en el párrafo del libro (que JW Library ya abre al tocarlos),
   NO repite el versículo literal, pero SÍ agrega toda la información extra (Notas de estudio nwtsty,
   notas a pie de página, textos bíblicos adicionales citados en el comentario y los extractos completos
   de respaldo de las publicaciones pt14_S, Perspicacia it_S y Guía de Estudio rsg_S).
5. Genera dos presentaciones visuales (ambas conservando las 58 notas y 1,718 subrayados personales del usuario):
   - Backup_JW_Agente_Dos_Tarjetas_Respuesta_y_Respaldo.jwlibrary (No abrumador: Tarjeta 1 compacta con
     Respuesta + Análisis unida al subrayado; Tarjeta 2 justo debajo con el Respaldo literal y Notas extras)
   - Backup_JW_Agente_Una_Tarjeta_Todo_en_Uno.jwlibrary (1 sola tarjeta por punto unida al subrayado,
     con la Respuesta + Análisis arriba y el Anexo de Respaldo y Notas extras abajo)
"""

import datetime
import hashlib
import html as html_mod
import json
import os
import re
import shutil
import sqlite3
import time
import unicodedata
import uuid
import zipfile
from extract_nwtsty import (
    derive_key_iv,
    fast_decrypt,
    strip_tags,
)
from build_jwlibrary_backup import ensure_dbs_extracted


def norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\[pag\.?\s*[\d,\-\s]+\]", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def clean_html_inline(h: str) -> str:
    if not h:
        return ""
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = html_mod.unescape(h)
    h = h.replace("\xa0", " ").replace("\u200b", "").replace("\u2060", "")
    # Clean stray markdown headings inside extracts (e.g. "#### 5. Luche...")
    h = re.sub(r"\s*#{2,6}\s+", "\n▸ ", h)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in h.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def count_jwl_tokens(text: str) -> int:
    """Calcula el índice final de token (EndToken) compatible con el tokenizador de JW Library."""
    if not text:
        return 0
    toks = re.findall(r"\w+(?::\w+)?|[^\w\s]", text)
    return max(0, len(toks) - 1)


def parse_agent_epub_chapters(epub_path: str, conn_pt: sqlite3.Connection, key_pt: bytes, iv_pt: bytes):
    """
    Extrae las 36 lecciones/capítulos del EPUB del agente y vincula cada unidad de estudio
    exactamente con su párrafo (data-pid) en pt14_S.db.
    """
    docs = conn_pt.execute(
        "SELECT DocumentId, MepsDocumentId, ContextTitle, Title, Content FROM Document WHERE DocumentId >= 3 ORDER BY DocumentId"
    ).fetchall()

    chapters_data = []

    with zipfile.ZipFile(epub_path, "r") as z:
        xhtml_files = [
            f for f in z.namelist()
            if f.startswith("OEBPS/text/") and f not in ("OEBPS/text/portada.xhtml", "OEBPS/text/indice_general.xhtml")
        ]
        assert len(docs) == len(xhtml_files), f"Mismatch docs={len(docs)} vs xhtml={len(xhtml_files)}"

        for (did, meps_id, ctx_title, title, blob), xf in zip(docs, xhtml_files):
            clean_title = (title or f"Documento {did}").replace("\xa0", " ").replace("\u200b", "").replace("\u2060", "").strip()
            clean_ctx = (ctx_title or "").replace("\xa0", " ").replace("\u200b", "").strip()
            tag_name = clean_ctx if clean_ctx else clean_title[:40]

            doc_html = fast_decrypt(blob, key_pt, iv_pt)
            paras = []
            pid_to_text = {}
            for m in re.finditer(r'<([a-zA-Z0-9]+)[^>]*\bdata-pid="(\d+)"[^>]*>(.*?)</\1>', doc_html, flags=re.DOTALL):
                pid = int(m.group(2))
                p_clean = strip_tags(re.sub(r'<span[^>]*class="[^"]*pageNum[^"]*"[^>]*>.*?</span>', "", m.group(3)))
                paras.append((pid, p_clean, norm(p_clean)))
                pid_to_text[pid] = p_clean

            raw_xhtml = z.read(xf).decode("utf-8")

            # 1. Parse all <aside id="ref-X"> footnotes in this chapter
            asides = {}
            for m in re.finditer(r'<aside id="(ref-\d+)" class="([^"]+)"[^>]*>(.*?)</aside>', raw_xhtml, flags=re.DOTALL):
                rid, cls, inner = m.group(1), m.group(2), m.group(3)
                bm = re.search(r'<span class="fn-badge">(.*?)</span>', inner)
                tm = re.search(r'<div class="fn-title">(.*?)</div>', inner)
                badge = clean_html_inline(bm.group(1)) if bm else ""
                atitle = clean_html_inline(tm.group(1)) if tm else ""
                aparas = []
                for pm in re.finditer(r'<p(?:\s+class="([^"]+)")?>(.*?)</p>', inner, flags=re.DOTALL):
                    pcls = pm.group(1) or ""
                    if pcls == "fn-back":
                        continue
                    ptxt = clean_html_inline(pm.group(2))
                    if ptxt and not any(
                        ptxt.startswith(prefix)
                        for prefix in (
                            "*(Fuente literal",
                            "*(Extracto literal",
                            "*(El dossier",
                            "*(Esta publicación",
                            "*(Referencia bíblica",
                            "*(El texto correspondiente",
                        )
                    ):
                        aparas.append(ptxt)
                asides[rid] = (cls, badge, atitle, aparas)

            # 2. Parse all <article class="study-unit..."> and preceding headings
            raw_units = re.findall(
                r'(?:<h([2345])[^>]*>((?:(?!</h\1>).)*)</h\1>\s*)?<article class="study-unit(?:\s+([^"]*))?">(.*?)</article>',
                raw_xhtml,
                flags=re.DOTALL,
            )

            last_pid = 0
            seen_pids = set()
            parsed_units = []

            for h_lvl, h_raw, u_cls, u_inner in raw_units:
                u_cls = u_cls or ""
                heading = clean_html_inline(h_raw) if h_raw else "Punto de estudio"
                qm = re.search(r'<div class="question-box[^"]*">(.*?)</div>\s*(?=<div|$)', u_inner, flags=re.DOTALL)
                am = re.search(r'<div class="answer-section">(.*?)</div>\s*(?=<div|$)', u_inner, flags=re.DOTALL)
                bm = re.search(r'<div class="bib-section">(.*?)</div>\s*(?=<div|$)', u_inner, flags=re.DOTALL)
                pm = re.search(r'<div class="pearls-section">(.*?)</div>\s*(?=<div|$)', u_inner, flags=re.DOTALL)

                q_html = qm.group(1) if qm else ""
                q_p_match = re.search(r"<p>(.*?)</p>", q_html, flags=re.DOTALL)
                q_txt = strip_tags(q_p_match.group(1)) if q_p_match else strip_tags(q_html)

                # Match to exact data-pid in pt14_S.db
                has_bullet_sep = ("•" in q_txt and not q_txt.strip().startswith("•"))
                tail_txt = q_txt.split("•")[-1].strip() if has_bullet_sep else q_txt
                qn = norm(q_txt)
                tn = norm(tail_txt)
                q_words = set(qn.split())
                best_pid = None
                best_score = -1.0

                for pid, p_clean, pn in paras:
                    if not pn:
                        continue
                    if has_bullet_sep and (tn == pn or (tn.endswith(pn) and len(pn) >= 6)):
                        sc = 3.0
                    elif qn == pn:
                        sc = 2.8
                    elif not has_bullet_sep and qn.startswith(pn) and len(pn) >= 25:
                        sc = 2.5
                    elif qn.endswith(pn) and len(pn) >= 6:
                        sc = 2.2
                    elif qn in pn or pn in qn:
                        sc = 1.0 + min(len(qn), len(pn)) / max(len(qn), len(pn))
                    else:
                        p_words = set(pn.split())
                        sc = len(q_words & p_words) / max(1, min(len(q_words), len(p_words)))
                    if pid > last_pid and sc >= 0.75:
                        sc += 0.6
                    if pid not in seen_pids and sc >= 0.75:
                        sc += 0.3
                    if sc > best_score:
                        best_score = sc
                        best_pid = pid

                seen_pids.add(best_pid)
                if best_pid is not None and best_pid > last_pid:
                    last_pid = best_pid

                # Check if q_txt spans two consecutive paragraphs joined by " / "
                covered_pids = [best_pid]
                if " / " in q_txt and (best_pid + 1) in pid_to_text:
                    second_part_norm = norm(q_txt.split(" / ", 1)[1])
                    next_pn = norm(pid_to_text[best_pid + 1])
                    if next_pn and (next_pn in second_part_norm or second_part_norm in next_pn):
                        covered_pids.append(best_pid + 1)
                        seen_pids.add(best_pid + 1)

                # Choose semantic highlight ColorIndex:
                # 1=Yellow (Questions), 2=Green (Intro), 3=Blue (Review ◆), 4=Pink (Instructor), 5=Orange (Experience), 6=Purple (Project)
                h_lower = heading.lower()
                if "unit-instructor" in u_cls or "instructor" in h_lower:
                    color_idx = 4
                elif "unit-review" in u_cls or "◆" in q_txt or "transición" in h_lower:
                    color_idx = 3
                elif "unit-experience" in u_cls or "experiencia" in h_lower:
                    color_idx = 5
                elif "unit-project" in u_cls or "proyecto" in h_lower:
                    color_idx = 6
                elif h_lower.startswith("punto ") and "pregunta" not in h_lower:
                    color_idx = 2
                else:
                    color_idx = 1

                # Extract Answer, Bible Analysis, and Pearls
                ans_p = re.findall(r"<p>(.*?)</p>", am.group(1), flags=re.DOTALL) if am else []
                ans_txt = "\n\n".join(clean_html_inline(x) for x in ans_p if clean_html_inline(x))

                bib_items = []
                if bm:
                    for li in re.findall(r"<li>(.*?)</li>", bm.group(1), flags=re.DOTALL):
                        c_li = clean_html_inline(li)
                        if c_li:
                            bib_items.append(f"• {c_li}")
                    if not bib_items:
                        for p_b in re.findall(r"<p>(.*?)</p>", bm.group(1), flags=re.DOTALL):
                            c_pb = clean_html_inline(p_b)
                            if c_pb:
                                bib_items.append(f"• {c_pb}")
                bib_txt = "\n".join(bib_items)

                prl_items = []
                if pm:
                    for li in re.findall(r"<li>(.*?)</li>", pm.group(1), flags=re.DOTALL):
                        c_li = clean_html_inline(li)
                        if c_li:
                            prl_items.append(f"• {c_li}")
                    if not prl_items:
                        for p_p in re.findall(r"<p>(.*?)</p>", pm.group(1), flags=re.DOTALL):
                            c_pp = clean_html_inline(p_p)
                            if c_pp:
                                prl_items.append(f"• {c_pp}")
                prl_txt = "\n".join(prl_items)

                # Identify Bible verses already in the book paragraph (question-box)
                book_bible_keys = set()
                for rref in re.findall(r'href="#(ref-\d+)"', q_html):
                    if rref in asides and "fn-bible" in asides[rref][0]:
                        _, abadge, atitle, aparas = asides[rref]
                        book_bible_keys.add(atitle)
                        if "·" in abadge:
                            book_bible_keys.add(abadge.split("·", 1)[1].strip())
                        if aparas:
                            book_bible_keys.add(aparas[0].split(":", 1)[0].strip())

                # Collect backing information (study notes, extra verses, and publication extracts)
                all_refs = re.findall(r'href="#(ref-\d+)"', u_inner)
                nwt_extra_notes = []
                extra_bible_quotes = []
                pub_backing_extracts = []
                seen_extra = set()

                for rref in all_refs:
                    if rref not in asides:
                        continue
                    acls, abadge, atitle, aparas = asides[rref]
                    if not aparas:
                        continue
                    b_short = abadge.split("·")[0].strip()
                    b_after_dot = abadge.split("·", 1)[1].strip() if "·" in abadge else atitle

                    if "fn-bible" in acls:
                        first_verse_hdr = aparas[0].split(":", 1)[0].strip() if aparas else ""
                        is_in_book = (
                            atitle in book_bible_keys
                            or b_after_dot in book_bible_keys
                            or first_verse_hdr in book_bible_keys
                        )
                        if is_in_book:
                            # Skip aparas[0] (literal verse already in book!), keep only study notes/footnotes
                            for ep in aparas[1:]:
                                ep_clean = re.sub(r"^[^:]+:\s*", "", ep)
                                key = ("nwt_note", ep_clean)
                                if key not in seen_extra:
                                    seen_extra.add(key)
                                    nwt_extra_notes.append(f"• [{b_after_dot}] {ep}")
                        else:
                            # Extra verse cited in commentary (not in the book's paragraph)
                            if b_short == "Cita bíblica referida":
                                continue
                            key = ("extra_bible", b_after_dot)
                            if key not in seen_extra:
                                seen_extra.add(key)
                                extra_bible_quotes.append(f"📖 {atitle}:\n" + "\n".join(aparas))
                    else:
                        if b_short in ("Notas nwtsty_S del dossier", "Notas a pie de página (nwtsty_S)"):
                            for ep in aparas:
                                ep_clean = re.sub(r"^[^:]+:\s*", "", ep)
                                if any(ep_clean in x for ktype, x in seen_extra if ktype == "nwt_note"):
                                    continue
                                key = ("nwt_note", ep_clean)
                                if key not in seen_extra:
                                    seen_extra.add(key)
                                    nwt_extra_notes.append(f"• {ep}")
                        elif b_short in ("Referencia de la lección (pt14_S)", "Perspicacia (it_S)", "Guía de Estudio (rsg_S)"):
                            key = ("pub", atitle)
                            if key not in seen_extra:
                                seen_extra.add(key)
                                if "Perspicacia" in b_short:
                                    src_tag = "📘 Perspicacia"
                                elif "Guía" in b_short:
                                    src_tag = "📗 Guía de Estudio"
                                else:
                                    src_tag = "📚 Ref. de la lección"
                                pub_backing_extracts.append(f"{src_tag}: {atitle}\n" + "\n\n".join(aparas))

                # Build main analysis body (never repeats book question)
                main_parts = []
                if ans_txt:
                    main_parts.append(f"✅ RESPUESTA DIRECTA\n{ans_txt}")
                if bib_txt:
                    main_parts.append(f"📖 ANÁLISIS DE LOS TEXTOS\n{bib_txt}")
                if prl_txt:
                    main_parts.append(f"💎 ANÁLISIS DE LAS REFERENCIAS Y PERLAS\n{prl_txt}")
                main_body = "\n\n".join(main_parts)

                # Build backing / extra sources body
                backing_parts = []
                if nwt_extra_notes:
                    backing_parts.append("📝 NOTAS DE ESTUDIO Y AL PIE (nwtsty)\n" + "\n".join(nwt_extra_notes))
                if extra_bible_quotes:
                    backing_parts.append("📖 TEXTOS ADICIONALES CITADOS EN EL COMENTARIO\n" + "\n\n".join(extra_bible_quotes))
                if pub_backing_extracts:
                    backing_parts.append(
                        "📚 EXTRACTOS LITERALES DE RESPALDO\n"
                        + f"\n\n{'─'*28}\n\n".join(pub_backing_extracts)
                    )
                backing_body = "\n\n".join(backing_parts)

                # Concise Note title: "[Heading] — First words of direct answer"
                short_ans = re.sub(r"\s+", " ", ans_txt).strip()
                if len(short_ans) > 75:
                    short_ans = short_ans[:72].rsplit(" ", 1)[0] + "..."
                main_title = f"💡 {heading}: {short_ans}" if short_ans else f"💡 {heading}"
                backing_title = f"📎 Respaldo y Notas Extras — {heading}"

                # Compute token ranges for covered_pids
                block_ranges = []
                for cpid in covered_pids:
                    ptxt_for_tok = pid_to_text.get(cpid, "")
                    end_tok = count_jwl_tokens(ptxt_for_tok)
                    block_ranges.append((cpid, 0, end_tok))

                parsed_units.append({
                    "pid": best_pid,
                    "heading": heading,
                    "color_idx": color_idx,
                    "block_ranges": block_ranges,
                    "main_title": main_title,
                    "main_body": main_body,
                    "backing_title": backing_title,
                    "backing_body": backing_body,
                })

            chapters_data.append({
                "doc_id": did,
                "meps_doc_id": meps_id,
                "clean_title": clean_title,
                "tag_name": tag_name,
                "units": parsed_units,
            })

    return chapters_data


def build_jwlibrary_archive(
    base_jwlibrary_path: str,
    chapters_data: list,
    two_cards_mode: bool,
    out_path: str,
    keep_existing_user_data: bool = True,
):
    work_dir = f"/tmp/jwl_agent_{os.getpid()}_{'2cards' if two_cards_mode else '1card'}_{'merged' if keep_existing_user_data else 'clean'}"
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)

    with zipfile.ZipFile(base_jwlibrary_path, "r") as z:
        z.extractall(work_dir)

    db_path = os.path.join(work_dir, "userData.db")
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today_str = now_iso.split("T")[0]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cur.execute("PRAGMA journal_mode = DELETE")
    cur.execute("PRAGMA foreign_keys = OFF")

    if not keep_existing_user_data:
        for tbl in [
            "TagMap", "Note", "BlockRange", "UserMark", "Bookmark", "InputField",
            "PlaylistItemMarkerBibleVerseMap", "PlaylistItemMarkerParagraphMap",
            "PlaylistItemMarker", "PlaylistItemIndependentMediaMap",
            "PlaylistItemLocationMap", "PlaylistItem", "IndependentMedia",
            "Location", "Tag",
        ]:
            cur.execute(f'DELETE FROM "{tbl}"')
        cur.execute("INSERT INTO Tag (TagId, Type, Name) VALUES (1, 0, 'Favorite')")
    else:
        # Remove the dummy test note #60 and dummy highlight #1828 in pt14 cover if present
        cur.execute("DELETE FROM Note WHERE Guid = '4ea8880f-44bd-4d9c-afe8-bfc2af29f7e2'")
        cur.execute("DELETE FROM BlockRange WHERE UserMarkId IN (SELECT UserMarkId FROM UserMark WHERE UserMarkGuid = '43df6ff3-2afa-4446-91b3-9dfe99f1292f')")
        cur.execute("DELETE FROM UserMark WHERE UserMarkGuid = '43df6ff3-2afa-4446-91b3-9dfe99f1292f'")

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
            max_pos = cur.execute("SELECT COALESCE(MAX(Position), -1) FROM TagMap WHERE TagId = ?", (tid,)).fetchone()[0]
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

    tag_respuestas_id = get_or_create_tag("1. Respuestas y Análisis (pt14)")
    tag_respaldo_id = get_or_create_tag("2. Fuentes y Respaldo (pt14)") if two_cards_mode else None

    pt14_loc_map = {}
    for lid, doc_id_val in cur.execute(
        "SELECT LocationId, DocumentId FROM Location WHERE KeySymbol = 'pt14' AND MepsLanguage = 1 AND Type = 0"
    ):
        if doc_id_val is not None:
            pt14_loc_map[int(doc_id_val)] = lid

    inserted_notes = 0
    inserted_usermarks = 0

    for ch in chapters_data:
        meps_doc_id = ch["meps_doc_id"]
        clean_title = ch["clean_title"]
        tag_name = ch["tag_name"]

        if meps_doc_id in pt14_loc_map:
            loc_id = pt14_loc_map[meps_doc_id]
            cur.execute(
                "UPDATE Location SET Title = ? WHERE LocationId = ? AND (Title IS NULL OR Title = '')",
                (clean_title, loc_id),
            )
        else:
            loc_id = next_loc_id
            next_loc_id += 1
            cur.execute(
                """INSERT INTO Location (
                    LocationId, BookNumber, ChapterNumber, DocumentId, Track,
                    IssueTagNumber, KeySymbol, MepsLanguage, Type, Title, Specialty, Edition
                ) VALUES (?, NULL, NULL, ?, NULL, 0, 'pt14', 1, 0, ?, NULL, NULL)""",
                (loc_id, meps_doc_id, clean_title),
            )
            pt14_loc_map[meps_doc_id] = loc_id

        lesson_tag_id = get_or_create_tag(tag_name)

        for u in ch["units"]:
            pid = u["pid"]
            color_idx = u["color_idx"]

            # 1. Create UserMark + BlockRange(s) to highlight the paragraph in the book and link the Note
            um_id = next_um_id
            next_um_id += 1
            cur.execute(
                """INSERT INTO UserMark (
                    UserMarkId, ColorIndex, LocationId, StyleIndex, UserMarkGuid, Version
                ) VALUES (?, ?, ?, 0, ?, 1)""",
                (um_id, color_idx, loc_id, str(uuid.uuid4())),
            )
            inserted_usermarks += 1

            for cpid, st_tok, end_tok in u["block_ranges"]:
                br_id = next_br_id
                next_br_id += 1
                cur.execute(
                    """INSERT INTO BlockRange (
                        BlockRangeId, BlockType, Identifier, StartToken, EndToken, UserMarkId
                    ) VALUES (?, 1, ?, ?, ?, ?)""",
                    (br_id, cpid, st_tok, end_tok, um_id),
                )

            if two_cards_mode:
                # Card 1: Main Answer + Analysis linked to the highlight (um_id)
                nid1 = next_note_id
                next_note_id += 1
                cur.execute(
                    """INSERT INTO Note (
                        NoteId, Guid, UserMarkId, LocationId, Title, Content,
                        LastModified, Created, BlockType, BlockIdentifier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (nid1, str(uuid.uuid4()), um_id, loc_id, u["main_title"], u["main_body"], now_iso, now_iso, pid),
                )
                inserted_notes += 1
                for tid_link in (tag_respuestas_id, lesson_tag_id):
                    pos = tag_pos_counter[tid_link]
                    tag_pos_counter[tid_link] += 1
                    tmid = next_tagmap_id
                    next_tagmap_id += 1
                    cur.execute(
                        "INSERT INTO TagMap (TagMapId, PlaylistItemId, LocationId, NoteId, TagId, Position) VALUES (?, NULL, NULL, ?, ?, ?)",
                        (tmid, nid1, tid_link, pos),
                    )

                # Card 2: Supporting Extracts & Extra Bible Notes anchored to the same paragraph (if non-empty)
                if u["backing_body"]:
                    nid2 = next_note_id
                    next_note_id += 1
                    cur.execute(
                        """INSERT INTO Note (
                            NoteId, Guid, UserMarkId, LocationId, Title, Content,
                            LastModified, Created, BlockType, BlockIdentifier
                        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 1, ?)""",
                        (nid2, str(uuid.uuid4()), loc_id, u["backing_title"], u["backing_body"], now_iso, now_iso, pid),
                    )
                    inserted_notes += 1
                    for tid_link in (tag_respaldo_id, lesson_tag_id):
                        pos = tag_pos_counter[tid_link]
                        tag_pos_counter[tid_link] += 1
                        tmid = next_tagmap_id
                        next_tagmap_id += 1
                        cur.execute(
                            "INSERT INTO TagMap (TagMapId, PlaylistItemId, LocationId, NoteId, TagId, Position) VALUES (?, NULL, NULL, ?, ?, ?)",
                            (tmid, nid2, tid_link, pos),
                        )
            else:
                # Single unified card linked to the highlight (um_id)
                if u["backing_body"]:
                    unified_body = (
                        u["main_body"]
                        + f"\n\n{'━'*26}\n📎 RESPALDO Y NOTAS EXTRAS (CONSULTA)\n{'━'*26}\n\n"
                        + u["backing_body"]
                    )
                else:
                    unified_body = u["main_body"]

                nid = next_note_id
                next_note_id += 1
                cur.execute(
                    """INSERT INTO Note (
                        NoteId, Guid, UserMarkId, LocationId, Title, Content,
                        LastModified, Created, BlockType, BlockIdentifier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (nid, str(uuid.uuid4()), um_id, loc_id, u["main_title"], unified_body, now_iso, now_iso, pid),
                )
                inserted_notes += 1
                for tid_link in (tag_respuestas_id, lesson_tag_id):
                    pos = tag_pos_counter[tid_link]
                    tag_pos_counter[tid_link] += 1
                    tmid = next_tagmap_id
                    next_tagmap_id += 1
                    cur.execute(
                        "INSERT INTO TagMap (TagMapId, PlaylistItemId, LocationId, NoteId, TagId, Position) VALUES (?, NULL, NULL, ?, ?, ?)",
                        (tmid, nid, tid_link, pos),
                    )

    cur.execute("UPDATE LastModified SET LastModified = ?", (now_iso,))
    conn.commit()

    cur.execute("PRAGMA foreign_keys = ON")
    fk_errors = cur.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise RuntimeError(f"Foreign key check failed in {out_path}: {fk_errors}")

    tot_notes = cur.execute("SELECT COUNT(*) FROM Note").fetchone()[0]
    tot_um = cur.execute("SELECT COUNT(*) FROM UserMark").fetchone()[0]
    conn.close()

    with open(db_path, "rb") as f:
        db_bytes = f.read()

    db_hash = hashlib.sha256(db_bytes).hexdigest()
    manifest = {
        "name": os.path.basename(out_path),
        "creationDate": today_str,
        "version": 1,
        "type": 0,
        "userDataBackup": {
            "lastModifiedDate": now_iso,
            "deviceName": "Samsung_SM-F971B",
            "databaseName": "userData.db",
            "hash": db_hash,
            "schemaVersion": 16,
        },
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")

    thumb_path = os.path.join(work_dir, "default_thumbnail.png")
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("userData.db", db_bytes)
        if os.path.exists(thumb_path):
            zf.write(thumb_path, "default_thumbnail.png")

    shutil.rmtree(work_dir, ignore_errors=True)
    return inserted_notes, inserted_usermarks, tot_notes, tot_um


def main():
    t0 = time.time()
    ensure_dbs_extracted()
    conn_pt = sqlite3.connect("/tmp/dbs/pt14_S.db")
    key_pt, iv_pt, _ = derive_key_iv(1, "pt14", 2023, "0")

    epub_path = "/home/antigravity/Guia_Estudio_Precursores_Agente.epub"
    base_user_backup = "/home/antigravity/UserdataBackup_2026-11-14_Samsung_SM-F971B.jwlibrary"

    chapters_data = parse_agent_epub_chapters(epub_path, conn_pt, key_pt, iv_pt)
    total_units = sum(len(c["units"]) for c in chapters_data)
    print(f"Capítulos analizados del EPUB del agente: {len(chapters_data)} ({total_units} unidades de estudio vinculadas 1:1 a data-pid)")

    outputs = [
        (True, True, "/home/antigravity/Backup_JW_Agente_Dos_Tarjetas_Respuesta_y_Respaldo.jwlibrary"),
        (False, True, "/home/antigravity/Backup_JW_Agente_Una_Tarjeta_Todo_en_Uno.jwlibrary"),
    ]

    for two_cards, keep_user, out_p in outputs:
        ins_n, ins_um, tot_n, tot_um = build_jwlibrary_archive(
            base_user_backup, chapters_data, two_cards, out_p, keep_existing_user_data=keep_user
        )
        sz_mb = os.path.getsize(out_p) / 1024 / 1024
        print(
            f"Generado: {os.path.basename(out_p)} ({sz_mb:.2f} MB) -> "
            f"+{ins_n} notas nuevas, +{ins_um} subrayados en pt14 | Total DB: {tot_n} notas, {tot_um} subrayados"
        )

    print(f"Completado exitosamente en {time.time() - t0:.2f}s")


if __name__ == "__main__":
    main()
