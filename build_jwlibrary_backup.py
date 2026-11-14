#!/usr/bin/env python3
"""
Generador y Fusionador de Copias de Seguridad (.jwlibrary) para JW Library (Schema v16):
Une las notas, subrayados, marcadores, etiquetas y cuadros de texto existentes del usuario
(UserdataBackup_2026-11-14_Samsung_SM-F971B.jwlibrary) con las notas de estudio completas
del libro de los precursores (pt14_S + nwtsty_S + rsg_S + it_S):

1. Backup_Fusionado_Samsung_Notas_por_Parrafo.jwlibrary
   (Todo el respaldo personal del usuario + 700 notas consolidadas por pregunta/párrafo)
2. Backup_Fusionado_Samsung_Notas_por_Cita.jwlibrary
   (Todo el respaldo personal del usuario + 1,245 notas individuales por cita bíblica/referencia)
3. Guia_Estudio_Precursores_Notas_por_Parrafo.jwlibrary (Solo las 700 notas de pt14_S en Schema v16)
4. Guia_Estudio_Precursores_Notas_por_Cita.jwlibrary (Solo las 1,245 notas de pt14_S en Schema v16)
"""

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
from extract_nwtsty import (
    derive_key_iv,
    fast_decrypt,
    strip_tags,
)
from build_dossiers import BOOK_DISPLAY_NAMES


def html_to_note_plain_text(raw_html: str) -> str:
    """Convierte HTML de extractos/notas en texto plano limpio con saltos de línea para JW Library."""
    if not raw_html:
        return ""
    s = raw_html.replace("\r", "")
    s = re.sub(r"<figure[^>]*>.*?</figure>", "", s, flags=re.DOTALL)
    s = re.sub(r"<span[^>]*class=\"[^\"]*pageNum[^\"]*\"[^>]*>.*?</span>", "", s, flags=re.DOTALL)
    s = re.sub(r"<li[^>]*>", "\n• ", s)
    s = re.sub(r"</(p|div|h[1-6]|li|tr|blockquote)>", "\n", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    s = s.replace("\xa0", " ").replace("\u200b", "").replace("\u2060", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in s.split("\n")]
    cleaned_lines = [line for line in lines if line]
    return "\n\n".join(cleaned_lines)


def ensure_dbs_extracted():
    os.makedirs("/tmp/dbs", exist_ok=True)
    for sym in ["pt14_S", "nwtsty_S", "rsg_S"]:
        db_path = f"/tmp/dbs/{sym}.db"
        if not os.path.exists(db_path):
            with zipfile.ZipFile(f"/home/antigravity/{sym}.jwpub", "r") as z:
                with zipfile.ZipFile(io.BytesIO(z.read("contents")), "r") as cz:
                    with open(db_path, "wb") as f:
                        f.write(cz.read(f"{sym}.db"))


def build_merged_or_standalone_jwlibrary(
    base_jwlibrary_path: str,
    pt_docs_data: list,
    mode: str,
    out_path: str,
    keep_existing_user_data: bool = True,
):
    """
    Toma el archivo .jwlibrary original del usuario (con su schema v16 exacto y WAL replay),
    conserva (o limpia si es standalone) sus datos personales, e inserta las notas de pt14_S.
    """
    work_dir = f"/tmp/jwl_work_{os.getpid()}_{mode}_{'merged' if keep_existing_user_data else 'clean'}"
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)

    with zipfile.ZipFile(base_jwlibrary_path, "r") as z:
        z.extractall(work_dir)

    db_path = os.path.join(work_dir, "userData.db")
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today_str = now_iso.split("T")[0]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Replay WAL and switch to DELETE journal mode so all data is in userData.db
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
        # Eliminar únicamente la nota de prueba #60 que el usuario creó para mostrarnos la ubicación en pt14
        cur.execute(
            "DELETE FROM Note WHERE Guid = '4ea8880f-44bd-4d9c-afe8-bfc2af29f7e2'"
        )

    # Obtener contadores máximos actuales
    max_loc_id = cur.execute("SELECT COALESCE(MAX(LocationId), 0) FROM Location").fetchone()[0]
    max_note_id = cur.execute("SELECT COALESCE(MAX(NoteId), 0) FROM Note").fetchone()[0]
    max_tag_id = cur.execute("SELECT COALESCE(MAX(TagId), 0) FROM Tag").fetchone()[0]
    max_tagmap_id = cur.execute("SELECT COALESCE(MAX(TagMapId), 0) FROM TagMap").fetchone()[0]

    next_loc_id = max_loc_id + 1
    next_note_id = max_note_id + 1
    next_tag_id = max_tag_id + 1
    next_tagmap_id = max_tagmap_id + 1

    # Función auxiliar para obtener o crear un Tag (Type=1)
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

    # Crear etiqueta general para el libro
    main_book_tag_id = get_or_create_tag("Escuela de Precursores (pt14)")

    # Mapa de Locations existentes para pt14 (DocumentId -> LocationId)
    pt14_loc_map = {}
    for lid, doc_id_val in cur.execute(
        "SELECT LocationId, DocumentId FROM Location WHERE KeySymbol = 'pt14' AND MepsLanguage = 1 AND Type = 0"
    ):
        if doc_id_val is not None:
            pt14_loc_map[int(doc_id_val)] = lid

    inserted_notes = 0

    for doc_info in pt_docs_data:
        meps_doc_id = doc_info["meps_doc_id"]
        clean_title = doc_info["clean_title"]
        tag_name = doc_info["tag_name"]
        paragraphs = doc_info["paragraphs"]

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

        for p_item in paragraphs:
            pid = p_item["pid"]
            if mode == "para":
                entries_to_insert = [(p_item["para_title"], p_item["para_content"])]
            else:
                entries_to_insert = p_item["cit_notes"]

            for n_title, n_content in entries_to_insert:
                nid = next_note_id
                next_note_id += 1
                cur.execute(
                    """INSERT INTO Note (
                        NoteId, Guid, UserMarkId, LocationId, Title, Content,
                        LastModified, Created, BlockType, BlockIdentifier
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 1, ?)""",
                    (nid, str(uuid.uuid4()), loc_id, n_title, n_content, now_iso, now_iso, pid),
                )
                inserted_notes += 1

                for tid_to_link in (main_book_tag_id, lesson_tag_id):
                    pos = tag_pos_counter[tid_to_link]
                    tag_pos_counter[tid_to_link] += 1
                    tmid = next_tagmap_id
                    next_tagmap_id += 1
                    cur.execute(
                        """INSERT INTO TagMap (
                            TagMapId, PlaylistItemId, LocationId, NoteId, TagId, Position
                        ) VALUES (?, NULL, NULL, ?, ?, ?)""",
                        (tmid, nid, tid_to_link, pos),
                    )

    cur.execute("UPDATE LastModified SET LastModified = ?", (now_iso,))
    conn.commit()

    cur.execute("PRAGMA foreign_keys = ON")
    fk_errors = cur.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise RuntimeError(f"Foreign key check failed in {out_path}: {fk_errors}")

    total_notes_in_db = cur.execute("SELECT COUNT(*) FROM Note").fetchone()[0]
    total_usermarks_in_db = cur.execute("SELECT COUNT(*) FROM UserMark").fetchone()[0]
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
    return inserted_notes, total_notes_in_db, total_usermarks_in_db


def main():
    t0 = time.time()
    ensure_dbs_extracted()

    conn_pt = sqlite3.connect("/tmp/dbs/pt14_S.db")
    conn_nwt = sqlite3.connect("/tmp/dbs/nwtsty_S.db")
    conn_rsg = sqlite3.connect("/tmp/dbs/rsg_S.db")

    key_pt, iv_pt, _ = derive_key_iv(1, "pt14", 2023, "0")
    key_nwt, iv_nwt, _ = derive_key_iv(1, "nwtsty", 2026, "0")
    key_rsg, iv_rsg, _ = derive_key_iv(1, "rsg", 2026, "0")

    # 1. Citas bíblicas de pt14_S
    pt_citations = {}
    all_needed_vids = set()
    for doc_id, blk, elem, fv, lv in conn_pt.execute(
        "SELECT DocumentId, BlockNumber, ElementNumber, FirstBibleVerseId, LastBibleVerseId FROM BibleCitation"
    ):
        pt_citations[(doc_id, blk, elem)] = (fv, lv)
        for v in range(fv, lv + 1):
            all_needed_vids.add(v)

    # 2. Extractos directos de pt14_S
    pt_extracts = {}
    for ext_id, cap, blob in conn_pt.execute("SELECT ExtractId, Caption, Content FROM Extract"):
        cap_clean = strip_tags(cap or f"Referencia {ext_id}")
        ext_txt = html_to_note_plain_text(fast_decrypt(blob, key_pt, iv_pt))
        pt_extracts[ext_id] = (cap_clean, ext_txt)

    # 3. Versículos, notas al pie y notas de estudio de nwtsty_S
    vid_list_sql = ",".join(map(str, sorted(all_needed_vids)))
    nwt_verses = {}
    for vid, blob in conn_nwt.execute(
        f"SELECT BibleVerseId, Content FROM BibleVerse WHERE BibleVerseId IN ({vid_list_sql})"
    ):
        v_html = fast_decrypt(blob, key_nwt, iv_nwt)
        m = re.search(r'id="v(\d+)-(\d+)-(\d+)"', v_html)
        b_num, c_num, v_num = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)
        nwt_verses[vid] = (b_num, c_num, v_num, strip_tags(v_html))

    nwt_footnotes = {}
    for vid, fn_blob in conn_nwt.execute(
        f"SELECT BibleVerseId, Content FROM Footnote WHERE BibleVerseId IN ({vid_list_sql}) ORDER BY FootnoteId"
    ):
        fn_txt = strip_tags(fast_decrypt(fn_blob, key_nwt, iv_nwt))
        if fn_txt:
            nwt_footnotes.setdefault(vid, []).append(fn_txt)

    nwt_study_notes = {}
    for vid, vc_blob in conn_nwt.execute(
        f"""
        SELECT vcm.BibleVerseId, vc.Content
        FROM VerseCommentaryMap vcm
        JOIN VerseCommentary vc ON vcm.VerseCommentaryId = vc.VerseCommentaryId
        WHERE vcm.BibleVerseId IN ({vid_list_sql})
        ORDER BY vc.VerseCommentaryId
        """
    ):
        vc_txt = strip_tags(fast_decrypt(vc_blob, key_nwt, iv_nwt))
        if vc_txt:
            nwt_study_notes.setdefault(vid, []).append(vc_txt)

    # 4. Información adicional de Perspicacia (it_S) y Guía de Estudio (rsg_S)
    needed_rsg_xtids = set()
    raw_rsg_entries_by_vid = {}
    for vid, vc_blob in conn_rsg.execute(
        f"""
        SELECT vcm.BibleVerseId, vc.Content
        FROM VerseCommentaryMap vcm
        JOIN VerseCommentary vc ON vcm.VerseCommentaryId = vc.VerseCommentaryId
        WHERE vcm.BibleVerseId IN ({vid_list_sql})
        ORDER BY vc.VerseCommentaryId
        """
    ):
        h = fast_decrypt(vc_blob, key_rsg, iv_rsg)
        items = re.findall(
            r'data-xtid="(\d+)"[^>]*>(.*?)</a>.*?</div[^>]*>\s*<div[^>]*>\s*<p[^>]*><em>(.*?)</em>',
            h,
            flags=re.DOTALL,
        )
        parsed_items = []
        non_it_count = 0
        for xtid_str, title_html, ref_html in items:
            xtid = int(xtid_str)
            t_clean = strip_tags(title_html)
            r_clean = strip_tags(ref_html)
            is_it = ("it-1" in r_clean) or ("it-2" in r_clean)
            inc_ext = False
            if is_it:
                inc_ext = True
            elif non_it_count < 2:
                inc_ext = True
                non_it_count += 1
            if inc_ext:
                needed_rsg_xtids.add(xtid)
            parsed_items.append((xtid, t_clean, r_clean, is_it, inc_ext))
        if parsed_items:
            raw_rsg_entries_by_vid.setdefault(vid, []).extend(parsed_items)

    rsg_extracts = {}
    if needed_rsg_xtids:
        xtid_sql = ",".join(map(str, sorted(needed_rsg_xtids)))
        for xtid, cap, blob in conn_rsg.execute(
            f"SELECT ExtractId, Caption, Content FROM Extract WHERE ExtractId IN ({xtid_sql})"
        ):
            cap_clean = strip_tags(cap or f"Referencia {xtid}")
            ext_txt = html_to_note_plain_text(fast_decrypt(blob, key_rsg, iv_rsg))
            rsg_extracts[xtid] = (cap_clean, ext_txt)

    def build_bible_citation_text_block(fv: int, lv: int):
        first_v = nwt_verses.get(fv)
        last_v = nwt_verses.get(lv)
        b_name = BOOK_DISPLAY_NAMES.get(first_v[0], "") if first_v else ""
        c_num = first_v[1] if first_v else ""
        vs_num = first_v[2] if first_v else ""
        ve_num = last_v[2] if last_v else vs_num
        ref_hdr = f"{b_name} {c_num}:{vs_num}" + (f"-{ve_num}" if ve_num != vs_num else "")

        v_parts = []
        fn_parts = []
        sn_parts = []
        it_parts = []
        rsg_parts = []
        seen_local_xtid = set()

        for vid in range(fv, lv + 1):
            if vid in nwt_verses:
                _, _, v_no, v_t = nwt_verses[vid]
                v_parts.append(f"[{v_no}] {v_t}")
            if vid in nwt_footnotes:
                v_no = nwt_verses[vid][2] if vid in nwt_verses else ""
                for f_t in nwt_footnotes[vid]:
                    fn_parts.append(f"• Nota al pie (v. {v_no}): {f_t}")
            if vid in nwt_study_notes:
                for sn_t in nwt_study_notes[vid]:
                    sn_parts.append(f"• Nota de estudio (nwtsty): {sn_t}")
            if vid in raw_rsg_entries_by_vid:
                for xtid, t_clean, r_clean, is_it, inc_ext in raw_rsg_entries_by_vid[vid]:
                    if inc_ext and xtid in rsg_extracts and xtid not in seen_local_xtid:
                        seen_local_xtid.add(xtid)
                        _, ext_txt = rsg_extracts[xtid]
                        if is_it:
                            it_parts.append(f"📘 Perspicacia ({r_clean} — {t_clean}):\n{ext_txt}")
                        else:
                            rsg_parts.append(f"📗 Guía de Estudio ({r_clean} — {t_clean}):\n{ext_txt}")

        lines = [
            f"📖 {ref_hdr} (TNM Estudio)",
            " ".join(v_parts),
        ]
        if fn_parts:
            lines.append("\n".join(fn_parts))
        if sn_parts:
            lines.append("\n".join(sn_parts))
        if it_parts:
            lines.append("\n\n".join(it_parts))
        if rsg_parts:
            lines.append("\n\n".join(rsg_parts))

        return ref_hdr, "\n\n".join(lines)

    # 5. Precalcular estructura de notas por capítulo y párrafo
    docs = conn_pt.execute(
        "SELECT DocumentId, MepsDocumentId, Title, ContextTitle, Content FROM Document ORDER BY DocumentId"
    ).fetchall()

    pt_docs_data = []
    for doc_id, meps_doc_id, title, ctx_title, blob in docs:
        clean_title = (title or f"Documento {doc_id}").replace("\xa0", " ").replace("\u200b", "").replace("\u2060", "").strip()
        clean_ctx = (ctx_title or "").replace("\xa0", " ").replace("\u200b", "").strip()
        tag_name = clean_ctx if clean_ctx else clean_title[:40]

        html = fast_decrypt(blob, key_pt, iv_pt)
        paragraphs = []

        for m in re.finditer(r'<([a-zA-Z0-9]+)[^>]*\bdata-pid="(\d+)"[^>]*>(.*?)</\1>', html, flags=re.DOTALL):
            pid = int(m.group(2))
            inner_html = m.group(3)
            para_plain = strip_tags(re.sub(r'<span[^>]*class="[^"]*pageNum[^"]*"[^>]*>.*?</span>', "", inner_html))

            ref_tokens = list(re.finditer(r'<a[^>]*?(?:data-bid="(\d+)-(\d+)"|data-xtid="(\d+)")[^>]*>(.*?)</a>', inner_html, flags=re.DOTALL))
            if not ref_tokens:
                continue

            para_blocks = []
            cit_notes = []
            seen_para_keys = set()

            for rt in ref_tokens:
                bid_blk, bid_elem, xtid_str, link_inner = rt.group(1), rt.group(2), rt.group(3), rt.group(4)
                link_label = strip_tags(link_inner)

                if bid_blk and bid_elem:
                    key = ("b", int(bid_blk), int(bid_elem))
                    if key in seen_para_keys:
                        continue
                    seen_para_keys.add(key)
                    cit_range = pt_citations.get((doc_id, int(bid_blk), int(bid_elem)))
                    if not cit_range:
                        continue
                    fv, lv = cit_range
                    ref_hdr, block_txt = build_bible_citation_text_block(fv, lv)
                    para_blocks.append(block_txt)
                    cit_title = f"📖 {ref_hdr} — {para_plain[:90]}"
                    cit_content = f"Pregunta / Párrafo:\n{para_plain}\n\n{'─'*32}\n\n{block_txt}"
                    cit_notes.append((cit_title, cit_content))

                elif xtid_str:
                    xtid = int(xtid_str)
                    key = ("x", xtid)
                    if key in seen_para_keys or xtid not in pt_extracts:
                        continue
                    seen_para_keys.add(key)
                    cap_clean, ext_txt = pt_extracts[xtid]
                    block_txt = f"📚 Referencia directa ({cap_clean}):\n{ext_txt}"
                    para_blocks.append(block_txt)
                    cit_title = f"📚 {link_label or cap_clean} — {para_plain[:90]}"
                    cit_content = f"Pregunta / Párrafo:\n{para_plain}\n\n{'─'*32}\n\n{block_txt}"
                    cit_notes.append((cit_title, cit_content))

            if para_blocks:
                para_title = para_plain[:120] + ("..." if len(para_plain) > 120 else "")
                para_content = (
                    f"📌 {tag_name} — {clean_title}\n"
                    f"Párrafo / Pregunta (p{pid}):\n{para_plain}\n\n"
                    + f"\n\n{'═'*32}\n\n".join(para_blocks)
                )
                paragraphs.append({
                    "pid": pid,
                    "para_title": para_title,
                    "para_content": para_content,
                    "cit_notes": cit_notes,
                })

        pt_docs_data.append({
            "doc_id": doc_id,
            "meps_doc_id": meps_doc_id,
            "clean_title": clean_title,
            "tag_name": tag_name,
            "paragraphs": paragraphs,
        })

    base_user_backup = "/home/antigravity/UserdataBackup_2026-11-14_Samsung_SM-F971B.jwlibrary"

    targets = [
        ("para", True, "/home/antigravity/Backup_Fusionado_Samsung_Notas_por_Parrafo.jwlibrary"),
        ("cit", True, "/home/antigravity/Backup_Fusionado_Samsung_Notas_por_Cita.jwlibrary"),
        ("para", False, "/home/antigravity/Guia_Estudio_Precursores_Notas_por_Parrafo.jwlibrary"),
        ("cit", False, "/home/antigravity/Guia_Estudio_Precursores_Notas_por_Cita.jwlibrary"),
    ]

    for mode, keep_user, out_p in targets:
        ins_n, tot_n, tot_um = build_merged_or_standalone_jwlibrary(
            base_user_backup, pt_docs_data, mode, out_p, keep_existing_user_data=keep_user
        )
        sz_mb = os.path.getsize(out_p) / 1024 / 1024
        print(
            f"Generado: {os.path.basename(out_p)} ({sz_mb:.2f} MB) -> "
            f"{ins_n} notas nuevas agregadas | Total en DB: {tot_n} notas, {tot_um} subrayados"
        )

    print(f"Proceso completado en {time.time() - t0:.2f}s")


if __name__ == "__main__":
    main()
