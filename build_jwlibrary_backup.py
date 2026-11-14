#!/usr/bin/env python3
"""
Generador de Copias de Seguridad (.jwlibrary) para JW Library:
1. Guia_Estudio_Precursores_Notas_por_Parrafo.jwlibrary
   (700 notas: 1 nota completa por cada pregunta/párrafo de pt14_S con todas sus fuentes base)
2. Guia_Estudio_Precursores_Notas_por_Cita.jwlibrary
   (1,245 notas: 1 nota individual por cada texto bíblico o referencia citada en cada párrafo)

Cada nota queda anclada directamente al documento (MepsDocumentId de pt14_S, KeySymbol='pt14',
MepsLanguage=1) y al identificador exacto del párrafo/pregunta (BlockType=1, BlockIdentifier=data-pid),
para que aparezca directamente al lado de cada pregunta dentro del libro en la app JW Library,
además de estar organizada con etiquetas (Tags) por lección.
"""

import datetime
import hashlib
import html as html_mod
import io
import json
import os
import re
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


SCHEMA_TABLES_DDL = [
    """CREATE TABLE "Location"(
LocationId      INTEGER NOT NULL PRIMARY KEY,
BookNumber      INTEGER,
ChapterNumber   INTEGER,
DocumentId      INTEGER,
Track           INTEGER,
IssueTagNumber  INTEGER NOT NULL DEFAULT 0,
KeySymbol       TEXT,
MepsLanguage    INTEGER,
Type            INTEGER NOT NULL,
Title           TEXT,
UNIQUE(BookNumber, ChapterNumber, KeySymbol, MepsLanguage, Type),
UNIQUE(KeySymbol, IssueTagNumber, MepsLanguage, DocumentId, Track, Type),
CHECK ((
Type = 0 AND ( -- Document or Bible chapter
(DocumentId IS NOT NULL AND DocumentId != 0)
OR ( -- Track based. Requires DocumentId or KeySymbol.
Track IS NOT NULL AND (
(KeySymbol IS NOT NULL AND (length(KeySymbol) > 0))
OR (DocumentId IS NOT NULL AND DocumentId != 0)))
OR ( -- Bible book. Requires KeySymbol.
BookNumber IS NOT NULL AND BookNumber != 0
AND KeySymbol IS NOT NULL AND (length(KeySymbol) > 0)
AND (ChapterNumber IS NULL OR ChapterNumber = 0))
OR ( -- Bible chapter. Requires KeySymbol and BookNumber.
ChapterNumber IS NOT NULL AND ChapterNumber != 0
AND BookNumber IS NOT NULL AND BookNumber != 0
AND KeySymbol IS NOT NULL AND (length(KeySymbol) > 0))))
OR Type != 0),
CHECK((
Type = 1 -- Bible
AND (BookNumber IS NULL OR BookNumber = 0)
AND (ChapterNumber IS NULL OR ChapterNumber = 0)
AND (DocumentId IS NULL OR DocumentId = 0)
AND KeySymbol IS NOT NULL AND (length(KeySymbol) > 0)
AND Track IS NULL)
OR Type != 1),
CHECK((
Type IN (2, 3) -- Mediator audio/video
AND (BookNumber IS NULL OR BookNumber = 0)
AND (ChapterNumber IS NULL OR ChapterNumber = 0))
OR Type NOT IN (2, 3)))""",

    """CREATE TABLE UserMark (
UserMarkId      INTEGER NOT NULL PRIMARY KEY,
ColorIndex      INTEGER NOT NULL,
LocationId      INTEGER NOT NULL,
StyleIndex      INTEGER NOT NULL,
UserMarkGuid    TEXT NOT NULL UNIQUE,
Version         INTEGER NOT NULL,
FOREIGN KEY(LocationId) REFERENCES Location(LocationId))""",

    """CREATE TABLE "Note"(
NoteId           INTEGER NOT NULL PRIMARY KEY,
Guid             TEXT NOT NULL UNIQUE,
UserMarkId       INTEGER,
LocationId       INTEGER,
Title            TEXT,
Content          TEXT,
LastModified     TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
Created          TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
BlockType        INTEGER NOT NULL DEFAULT 0,
BlockIdentifier  INTEGER,
CHECK((BlockType = 0 AND BlockIdentifier IS NULL) OR((BlockType BETWEEN 1 AND 2) AND BlockIdentifier IS NOT NULL)),
FOREIGN KEY(UserMarkId) REFERENCES UserMark(UserMarkId),
FOREIGN KEY(LocationId) REFERENCES Location(LocationId))""",

    """CREATE TABLE BlockRange (
BlockRangeId    INTEGER NOT NULL PRIMARY KEY,
BlockType       INTEGER NOT NULL,
Identifier      INTEGER NOT NULL,
StartToken      INTEGER,
EndToken        INTEGER,
UserMarkId      INTEGER NOT NULL,
CHECK (BlockType BETWEEN 1 AND 2),
FOREIGN KEY(UserMarkId) REFERENCES UserMark(UserMarkId))""",

    """CREATE TABLE Bookmark(
BookmarkId              INTEGER NOT NULL PRIMARY KEY,
LocationId              INTEGER NOT NULL,
PublicationLocationId   INTEGER NOT NULL,
Slot                    INTEGER NOT NULL,
Title                   TEXT NOT NULL,
Snippet                 TEXT,
BlockType               INTEGER NOT NULL DEFAULT 0,
BlockIdentifier         INTEGER,
FOREIGN KEY(LocationId) REFERENCES Location(LocationId),
FOREIGN KEY(PublicationLocationId) REFERENCES Location(LocationId),
CONSTRAINT PublicationLocationId_Slot UNIQUE (PublicationLocationId, Slot),
CHECK((BlockType = 0 AND BlockIdentifier IS NULL) OR ((BlockType BETWEEN 1 AND 2) AND BlockIdentifier IS NOT NULL)))""",

    """CREATE TABLE Tag(
TagId          INTEGER NOT NULL PRIMARY KEY,
Type           INTEGER NOT NULL,
Name           TEXT NOT NULL,
UNIQUE(Type, Name),
CHECK(length(Name) > 0),
CHECK(Type IN (0, 1, 2)))""",

    """CREATE TABLE TagMap (
TagMapId          INTEGER NOT NULL PRIMARY KEY,
PlaylistItemId    INTEGER,
LocationId        INTEGER,
NoteId            INTEGER,
TagId             INTEGER NOT NULL,
Position          INTEGER NOT NULL,
FOREIGN KEY(TagId) REFERENCES Tag(TagId),
FOREIGN KEY(PlaylistItemId) REFERENCES PlaylistItem(PlaylistItemId),
FOREIGN KEY(LocationId) REFERENCES Location(LocationId),
FOREIGN KEY(NoteId) REFERENCES Note(NoteId),
CONSTRAINT TagId_Position UNIQUE(TagId, Position),
CONSTRAINT TagId_NoteId UNIQUE(TagId, NoteId),
CONSTRAINT TagId_LocationId UNIQUE(TagId, LocationId),
CHECK(
    (NoteId IS NULL AND LocationId IS NULL AND PlaylistItemId IS NOT NULL) OR
    (LocationId IS NULL AND PlaylistItemId IS NULL AND NoteId IS NOT NULL) OR
    (PlaylistItemId IS NULL AND NoteId IS NULL AND LocationId IS NOT NULL)))""",

    """CREATE TABLE "InputField"(
LocationId  INTEGER NOT NULL,
TextTag     TEXT NOT NULL,
Value       TEXT NOT NULL,
FOREIGN KEY (LocationId) REFERENCES Location (LocationId),
CONSTRAINT LocationId_TextTag PRIMARY KEY (LocationId, TextTag))""",

    """CREATE TABLE IndependentMedia(
IndependentMediaId  INTEGER NOT NULL PRIMARY KEY,
OriginalFilename    TEXT NOT NULL,
FilePath            TEXT NOT NULL UNIQUE,
MimeType            TEXT NOT NULL,
Hash                TEXT NOT NULL,
CHECK(length(OriginalFilename) > 0),
CHECK(length(FilePath) > 0),
CHECK(length(MimeType) > 0),
CHECK(length(Hash) > 0))""",

    """CREATE TABLE PlaylistItemAccuracy(
PlaylistItemAccuracyId  INTEGER NOT NULL PRIMARY KEY,
Description             TEXT NOT NULL UNIQUE)""",

    """CREATE TABLE "PlaylistItem"(
PlaylistItemId           INTEGER NOT NULL PRIMARY KEY,
Label                    TEXT NOT NULL,
StartTrimOffsetTicks     INTEGER,
EndTrimOffsetTicks       INTEGER,
Accuracy                 INTEGER NOT NULL,
EndAction                INTEGER NOT NULL,
ThumbnailFilePath        TEXT,
FOREIGN KEY(Accuracy) REFERENCES PlaylistItemAccuracy(PlaylistItemAccuracyId),
FOREIGN KEY(ThumbnailFilePath) REFERENCES IndependentMedia(FilePath),
CHECK(length(Label) > 0),
CHECK(EndAction IN(0, 1, 2, 3)))""",

    """CREATE TABLE PlaylistItemIndependentMediaMap(
PlaylistItemId      INTEGER NOT NULL,
IndependentMediaId  INTEGER NOT NULL,
DurationTicks       INTEGER NOT NULL,
PRIMARY KEY(PlaylistItemId, IndependentMediaId),
FOREIGN KEY(PlaylistItemId) REFERENCES PlaylistItem(PlaylistItemId),
FOREIGN KEY(IndependentMediaId) REFERENCES IndependentMedia(IndependentMediaId))
WITHOUT ROWID""",

    """CREATE TABLE PlaylistItemLocationMap(
PlaylistItemId      INTEGER NOT NULL,
LocationId          INTEGER NOT NULL,
MajorMultimediaType INTEGER NOT NULL,
BaseDurationTicks   INTEGER,
PRIMARY KEY(PlaylistItemId, LocationId),
FOREIGN KEY(PlaylistItemId) REFERENCES PlaylistItem(PlaylistItemId),
FOREIGN KEY(LocationId) REFERENCES Location(LocationId))
WITHOUT ROWID""",

    """CREATE TABLE PlaylistItemMarker(
PlaylistItemMarkerId        INTEGER NOT NULL PRIMARY KEY,
PlaylistItemId              INTEGER NOT NULL,
Label                       TEXT NOT NULL,
StartTimeTicks              INTEGER NOT NULL,
DurationTicks               INTEGER NOT NULL,
EndTransitionDurationTicks  INTEGER NOT NULL,
UNIQUE(PlaylistItemId, StartTimeTicks),
FOREIGN KEY(PlaylistItemId) REFERENCES PlaylistItem(PlaylistItemId))""",

    """CREATE TABLE PlaylistItemMarkerBibleVerseMap(
PlaylistItemMarkerId        INTEGER NOT NULL,
VerseId                     INTEGER NOT NULL,
PRIMARY KEY(PlaylistItemMarkerId, VerseId),
FOREIGN KEY(PlaylistItemMarkerId) REFERENCES PlaylistItemMarker(PlaylistItemMarkerId))
WITHOUT ROWID""",

    """CREATE TABLE PlaylistItemMarkerParagraphMap(
PlaylistItemMarkerId        INTEGER NOT NULL,
MepsDocumentId              INTEGER NOT NULL,
ParagraphIndex              INTEGER NOT NULL,
MarkerIndexWithinParagraph  INTEGER NOT NULL,
PRIMARY KEY(PlaylistItemMarkerId, MepsDocumentId, ParagraphIndex, MarkerIndexWithinParagraph),
FOREIGN KEY(PlaylistItemMarkerId) REFERENCES PlaylistItemMarker(PlaylistItemMarkerId))
WITHOUT ROWID""",

    """CREATE TABLE "LastModified"(LastModified TEXT NOT NULL)""",

    """CREATE TABLE android_metadata (locale TEXT)""",
]

SCHEMA_INDEXES_DDL = [
    "CREATE INDEX IX_BlockRange_UserMarkId ON BlockRange(UserMarkId)",
    "CREATE INDEX IX_Location_KeySymbol_MepsLanguage_BookNumber_ChapterNumber ON Location(KeySymbol, MepsLanguage, BookNumber, ChapterNumber)",
    "CREATE INDEX IX_Location_MepsLanguage_DocumentId ON Location(MepsLanguage, DocumentId)",
    "CREATE INDEX IX_Note_LastModified_LocationId ON Note(LastModified, LocationId)",
    "CREATE INDEX IX_Note_LocationId_BlockIdentifier ON Note(LocationId, BlockIdentifier)",
    "CREATE INDEX IX_PlaylistItemIndependentMediaMap_IndependentMediaId ON PlaylistItemIndependentMediaMap(IndependentMediaId)",
    "CREATE INDEX IX_PlaylistItemLocationMap_LocationId ON PlaylistItemLocationMap(LocationId)",
    "CREATE INDEX IX_PlaylistItem_ThumbnailFilePath ON PlaylistItem(ThumbnailFilePath)",
    "CREATE INDEX IX_TagMap_LocationId_TagId_Position ON TagMap(LocationId, TagId, Position)",
    "CREATE INDEX IX_TagMap_NoteId_TagId_Position ON TagMap(NoteId, TagId, Position)",
    "CREATE INDEX IX_TagMap_PlaylistItemId_TagId_Position ON TagMap(PlaylistItemId, TagId, Position)",
    "CREATE INDEX IX_TagMap_TagId ON TagMap(TagId)",
    "CREATE INDEX IX_UserMark_LocationId ON UserMark(LocationId)",
]


def get_schema_triggers_ddl():
    triggers = []
    for tbl in ["Tag", "TagMap", "Note", "Bookmark", "UserMark", "BlockRange", "IndependentMedia"]:
        triggers.append(
            f"CREATE TRIGGER TR_Update_LastModified_Insert_{tbl} INSERT ON {tbl} "
            f"BEGIN UPDATE LastModified SET LastModified = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'); END"
        )
        triggers.append(
            f"CREATE TRIGGER TR_Update_LastModified_Update_{tbl} UPDATE ON {tbl} "
            f"BEGIN UPDATE LastModified SET LastModified = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'); END"
        )
        triggers.append(
            f"CREATE TRIGGER TR_Update_LastModified_Delete_{tbl} DELETE ON {tbl} "
            f"BEGIN UPDATE LastModified SET LastModified = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'); END"
        )
    triggers.append(
        "CREATE TRIGGER TR_Raise_Error_Before_Insert_LastModified BEFORE INSERT ON LastModified "
        "BEGIN SELECT RAISE (FAIL, 'INSERT INTO LastModified not allowed'); END"
    )
    triggers.append(
        "CREATE TRIGGER TR_Raise_Error_Before_Delete_LastModified BEFORE DELETE ON LastModified "
        "BEGIN SELECT RAISE (FAIL, 'DELETE FROM LastModified not allowed'); END"
    )
    return triggers


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


def package_jwlibrary(locations, notes, tags, tag_maps, out_path: str):
    """Construye userData.db (schema v14) y manifest.json, y empaqueta el archivo .jwlibrary."""
    tmp_db_path = f"/tmp/userdata_build_{os.getpid()}.db"
    if os.path.exists(tmp_db_path):
        os.remove(tmp_db_path)

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today_str = now_iso.split("T")[0]

    conn = sqlite3.connect(tmp_db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = DELETE")
    cur.execute("PRAGMA foreign_keys = OFF")
    cur.execute("PRAGMA user_version = 14")

    for ddl in SCHEMA_TABLES_DDL:
        cur.execute(ddl)
    for idx in SCHEMA_INDEXES_DDL:
        cur.execute(idx)

    cur.execute("BEGIN TRANSACTION")

    # Seed default rows expected by JW Library
    cur.executemany(
        "INSERT INTO PlaylistItemAccuracy (PlaylistItemAccuracyId, Description) VALUES (?, ?)",
        [(1, "Accurate"), (2, "NeedsUserVerification")],
    )
    cur.execute("INSERT INTO LastModified (LastModified) VALUES (?)", (now_iso,))
    cur.execute("INSERT INTO android_metadata (locale) VALUES (?)", ("es_ES",))

    # Insert Locations
    cur.executemany(
        """INSERT INTO Location (
            LocationId, BookNumber, ChapterNumber, DocumentId, Track,
            IssueTagNumber, KeySymbol, MepsLanguage, Type, Title
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        locations,
    )

    # Insert Tags
    cur.executemany(
        "INSERT INTO Tag (TagId, Type, Name) VALUES (?, ?, ?)",
        tags,
    )

    # Insert Notes
    cur.executemany(
        """INSERT INTO Note (
            NoteId, Guid, UserMarkId, LocationId, Title, Content,
            LastModified, Created, BlockType, BlockIdentifier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        notes,
    )

    # Insert TagMaps
    cur.executemany(
        """INSERT INTO TagMap (
            TagMapId, PlaylistItemId, LocationId, NoteId, TagId, Position
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        tag_maps,
    )

    conn.commit()

    # Add triggers after inserting data
    for trig in get_schema_triggers_ddl():
        cur.execute(trig)

    cur.execute("PRAGMA foreign_keys = ON")
    fk_errors = cur.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise RuntimeError(f"Foreign key check failed: {fk_errors}")

    conn.close()

    with open(tmp_db_path, "rb") as f:
        db_bytes = f.read()
    os.remove(tmp_db_path)

    db_hash = hashlib.sha256(db_bytes).hexdigest()
    manifest = {
        "name": os.path.basename(out_path),
        "creationDate": today_str,
        "version": 1,
        "type": 0,
        "userDataBackup": {
            "lastModifiedDate": now_iso,
            "deviceName": "Guia_Estudio_Precursores_pt14",
            "databaseName": "userData.db",
            "hash": db_hash,
            "schemaVersion": 14,
        },
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("userData.db", db_bytes)


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

    # 5. Recorrer los documentos de pt14_S y construir ambas variantes de notas
    docs = conn_pt.execute(
        "SELECT DocumentId, MepsDocumentId, Title, ContextTitle, Content FROM Document ORDER BY DocumentId"
    ).fetchall()

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    locations = []
    # Tag 1 = Favorite (tipo 0), Tag 2 = Escuela de Precursores (pt14) (tipo 1)
    tags = [
        (1, 0, "Favorite"),
        (2, 1, "Escuela de Precursores (pt14)"),
    ]
    next_tag_id = 3

    # Variante A: 1 nota consolidada por párrafo/pregunta
    notes_by_para = []
    tag_maps_by_para = []
    tag_pos_by_para = {2: 0}

    # Variante B: 1 nota individual por cada cita bíblica / referencia
    notes_by_cit = []
    tag_maps_by_cit = []
    tag_pos_by_cit = {2: 0}

    loc_id_counter = 1
    note_para_id = 1
    tagmap_para_id = 1
    note_cit_id = 1
    tagmap_cit_id = 1

    for doc_id, meps_doc_id, title, ctx_title, blob in docs:
        clean_title = (title or f"Documento {doc_id}").replace("\xa0", " ").replace("\u200b", "").replace("\u2060", "").strip()
        clean_ctx = (ctx_title or "").replace("\xa0", " ").replace("\u200b", "").strip()
        tag_name = clean_ctx if clean_ctx else clean_title[:40]

        # Registrar Location para este capítulo de pt14_S
        loc_id = loc_id_counter
        loc_id_counter += 1
        locations.append((
            loc_id,
            None,          # BookNumber
            None,          # ChapterNumber
            meps_doc_id,   # DocumentId (MepsDocumentId en pt14_S)
            None,          # Track
            0,             # IssueTagNumber
            "pt14",        # KeySymbol
            1,             # MepsLanguage (1 = Español)
            0,             # Type (0 = Document)
            clean_title,   # Title
        ))

        # Crear Tag para esta lección si no existe
        existing_tag_id = None
        for tid, ttype, tname in tags:
            if ttype == 1 and tname == tag_name:
                existing_tag_id = tid
                break
        if existing_tag_id is None:
            existing_tag_id = next_tag_id
            next_tag_id += 1
            tags.append((existing_tag_id, 1, tag_name))
            tag_pos_by_para[existing_tag_id] = 0
            tag_pos_by_cit[existing_tag_id] = 0

        html = fast_decrypt(blob, key_pt, iv_pt)
        for m in re.finditer(r'<([a-zA-Z0-9]+)[^>]*\bdata-pid="(\d+)"[^>]*>(.*?)</\1>', html, flags=re.DOTALL):
            pid = int(m.group(2))
            inner_html = m.group(3)
            para_plain = strip_tags(re.sub(r'<span[^>]*class="[^"]*pageNum[^"]*"[^>]*>.*?</span>', "", inner_html))

            # Buscar todas las referencias en el orden en que aparecen en el párrafo
            ref_tokens = list(re.finditer(r'<a[^>]*?(?:data-bid="(\d+)-(\d+)"|data-xtid="(\d+)")[^>]*>(.*?)</a>', inner_html, flags=re.DOTALL))
            if not ref_tokens:
                continue

            para_blocks = []
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

                    # Agregar nota individual en Variante B
                    cit_title = f"📖 {ref_hdr} — {para_plain[:90]}"
                    cit_content = f"Pregunta / Párrafo:\n{para_plain}\n\n{'─'*32}\n\n{block_txt}"
                    notes_by_cit.append((
                        note_cit_id,
                        str(uuid.uuid4()),
                        None,
                        loc_id,
                        cit_title,
                        cit_content,
                        now_iso,
                        now_iso,
                        1,    # BlockType = 1 (Paragraph)
                        pid,  # BlockIdentifier = data-pid
                    ))
                    for tid_to_link in (2, existing_tag_id):
                        tag_maps_by_cit.append((
                            tagmap_cit_id,
                            None,
                            None,
                            note_cit_id,
                            tid_to_link,
                            tag_pos_by_cit[tid_to_link],
                        ))
                        tag_pos_by_cit[tid_to_link] += 1
                        tagmap_cit_id += 1
                    note_cit_id += 1

                elif xtid_str:
                    xtid = int(xtid_str)
                    key = ("x", xtid)
                    if key in seen_para_keys or xtid not in pt_extracts:
                        continue
                    seen_para_keys.add(key)
                    cap_clean, ext_txt = pt_extracts[xtid]
                    block_txt = f"📚 Referencia directa ({cap_clean}):\n{ext_txt}"
                    para_blocks.append(block_txt)

                    # Agregar nota individual en Variante B
                    cit_title = f"📚 {link_label or cap_clean} — {para_plain[:90]}"
                    cit_content = f"Pregunta / Párrafo:\n{para_plain}\n\n{'─'*32}\n\n{block_txt}"
                    notes_by_cit.append((
                        note_cit_id,
                        str(uuid.uuid4()),
                        None,
                        loc_id,
                        cit_title,
                        cit_content,
                        now_iso,
                        now_iso,
                        1,
                        pid,
                    ))
                    for tid_to_link in (2, existing_tag_id):
                        tag_maps_by_cit.append((
                            tagmap_cit_id,
                            None,
                            None,
                            note_cit_id,
                            tid_to_link,
                            tag_pos_by_cit[tid_to_link],
                        ))
                        tag_pos_by_cit[tid_to_link] += 1
                        tagmap_cit_id += 1
                    note_cit_id += 1

            if para_blocks:
                para_title = para_plain[:120] + ("..." if len(para_plain) > 120 else "")
                para_content = (
                    f"📌 {tag_name} — {clean_title}\n"
                    f"Párrafo / Pregunta (p{pid}):\n{para_plain}\n\n"
                    + f"\n\n{'═'*32}\n\n".join(para_blocks)
                )
                notes_by_para.append((
                    note_para_id,
                    str(uuid.uuid4()),
                    None,
                    loc_id,
                    para_title,
                    para_content,
                    now_iso,
                    now_iso,
                    1,    # BlockType = 1 (Paragraph)
                    pid,  # BlockIdentifier = data-pid
                ))
                for tid_to_link in (2, existing_tag_id):
                    tag_maps_by_para.append((
                        tagmap_para_id,
                        None,
                        None,
                        note_para_id,
                        tid_to_link,
                        tag_pos_by_para[tid_to_link],
                    ))
                    tag_pos_by_para[tid_to_link] += 1
                    tagmap_para_id += 1
                note_para_id += 1

    out_para = "/home/antigravity/Guia_Estudio_Precursores_Notas_por_Parrafo.jwlibrary"
    package_jwlibrary(locations, notes_by_para, tags, tag_maps_by_para, out_para)

    out_cit = "/home/antigravity/Guia_Estudio_Precursores_Notas_por_Cita.jwlibrary"
    package_jwlibrary(locations, notes_by_cit, tags, tag_maps_by_cit, out_cit)

    dt = time.time() - t0
    print(f"Archivos .jwlibrary generados en {dt:.2f}s:")
    print(f"  1) {out_para} ({len(notes_by_para)} notas, {os.path.getsize(out_para)/1024/1024:.2f} MB)")
    print(f"  2) {out_cit} ({len(notes_by_cit)} notas, {os.path.getsize(out_cit)/1024/1024:.2f} MB)")


if __name__ == "__main__":
    main()
