#!/usr/bin/env python3
"""
Generador de EPUB 3 Interactivo: Guia_Estudio_Precursores_Interactiva.epub
Integra las 4 bases de datos (.jwpub):
  - pt14_S.db   (Libro de los Precursores + sus 280 extractos directos)
  - nwtsty_S.db (Biblia de Estudio: texto literal, notas a pie de página y notas de estudio)
  - rsg_S.db    (Guía de Estudio: índice de referencias y extractos principales por versículo)
  - it_S.db     (Perspicacia: extractos enciclopédicos por versículo)

Cada cita bíblica y cada referencia a otra publicación en el texto es un enlace interactivo
EPUB 3 (epub:type="noteref") conectado a una ficha completa (<aside epub:type="footnote">)
que se abre como ventana emergente (pop-up) o salta con enlace de retorno al punto exacto.
"""

import html as html_mod
import io
import os
import re
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from extract_nwtsty import (
     derive_key_iv,
    fast_decrypt,
    strip_tags,
)
from build_dossiers import BOOK_DISPLAY_NAMES


from html.parser import HTMLParser

VOID_TAGS = {
    "br", "hr", "img", "meta", "link", "input", "area",
    "base", "col", "embed", "param", "source", "track", "wbr",
}


class XHTMLBalancer(HTMLParser):
    """Balancea etiquetas HTML truncadas en extractos para garantizar XHTML 100% válido."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []

    def _fmt_attrs(self, attrs):
        parts = []
        for k, v in attrs:
            if k and not k.startswith("xmlns"):
                val = html_mod.escape(v or "", quote=True)
                parts.append(f' {k}="{val}"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        attr_str = self._fmt_attrs(attrs)
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{attr_str}/>")
        else:
            self.stack.append(tag)
            self.out.append(f"<{tag}{attr_str}>")

    def handle_startendtag(self, tag, attrs):
        attr_str = self._fmt_attrs(attrs)
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{attr_str}/>")
        else:
            self.out.append(f"<{tag}{attr_str}></{tag}>")

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break

    def handle_data(self, data):
        self.out.append(html_mod.escape(data, quote=False))

    def get_xhtml(self):
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
        return "".join(self.out)


def clean_inner_html_for_xhtml(raw_html: str) -> str:
    """
    Limpia un fragmento HTML de extractos/notas para que sea XHTML 100% válido
    y sin enlaces rotos jwpub://.
    """
    if not raw_html:
        return ""
    s = raw_html.replace("\r", "")
    # Eliminar imágenes o videos internos jwpub-media:// o webpubvid://
    s = re.sub(r"<figure[^>]*>.*?</figure>", "", s, flags=re.DOTALL)
    s = re.sub(r"<img[^>]*/>", "", s)
    s = re.sub(r"<img[^>]*>", "", s)
    s = re.sub(r"<video[^>]*>.*?</video>", "", s, flags=re.DOTALL)
    # Reemplazar enlaces jwpub:// por <span class="cited-ref">...</span>
    s = re.sub(
        r'<a[^>]*href="jwpub://[^"]*"[^>]*>(.*?)</a>',
        r'<span class="cited-ref">\1</span>',
        s,
        flags=re.DOTALL,
    )
    # Reemplazar pageNum spans por marcador discreto
    s = re.sub(
        r'<span[^>]*class="[^"]*pageNum[^"]*"[^>]*data-no="(\d+)"[^>]*>.*?</span>',
        r'<span class="page-marker">[pág. \1]</span> ',
        s,
        flags=re.DOTALL,
    )
    # Limpiar caracteres de control / ancho cero
    s = s.replace("\u200b", "").replace("\u2060", "")
    # Balancear etiquetas HTML truncadas (ej. <p> sin cerrar al final de un extracto)
    balancer = XHTMLBalancer()
    balancer.feed(s)
    res = balancer.get_xhtml()
    # Limpiar <p></p> vacíos residuales
    res = re.sub(r"<p>\s*</p>", "", res)
    return res.strip()


def main():
    t0 = time.time()
    epub_path = "/home/antigravity/Guia_Estudio_Precursores_Interactiva.epub"

    conn_pt = sqlite3.connect("/tmp/dbs/pt14_S.db")
    conn_nwt = sqlite3.connect("/tmp/dbs/nwtsty_S.db")
    conn_rsg = sqlite3.connect("/tmp/dbs/rsg_S.db")

    key_pt, iv_pt, _ = derive_key_iv(1, "pt14", 2023, "0")
    key_nwt, iv_nwt, _ = derive_key_iv(1, "nwtsty", 2026, "0")
    key_rsg, iv_rsg, _ = derive_key_iv(1, "rsg", 2026, "0")

    # Extraer imagen de portada de pt14_S.jwpub
    with zipfile.ZipFile("/home/antigravity/pt14_S.jwpub", "r") as z:
        with zipfile.ZipFile(io.BytesIO(z.read("contents")), "r") as cz:
            cover_bytes = cz.read("1102014909_S_cvr.jpg")

    # Mapa de MepsDocumentId -> DocumentId en pt14_S (para enlaces internos del Índice)
    meps_to_doc_id = {}
    for did, meps_id in conn_pt.execute("SELECT DocumentId, MepsDocumentId FROM Document"):
        meps_to_doc_id[str(meps_id)] = did

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
        ext_xhtml = clean_inner_html_for_xhtml(fast_decrypt(blob, key_pt, iv_pt))
        pt_extracts[ext_id] = (cap_clean, ext_xhtml)

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
        vc_xhtml = clean_inner_html_for_xhtml(fast_decrypt(vc_blob, key_nwt, iv_nwt))
        if vc_xhtml:
            nwt_study_notes.setdefault(vid, []).append(vc_xhtml)

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
            ext_xhtml = clean_inner_html_for_xhtml(fast_decrypt(blob, key_rsg, iv_rsg))
            rsg_extracts[xtid] = (cap_clean, ext_xhtml)

    # 5. Generar cada capítulo XHTML de pt14_S
    docs = conn_pt.execute(
        "SELECT DocumentId, Title, ContextTitle, Content FROM Document ORDER BY DocumentId"
    ).fetchall()

    chapter_files = []  # (doc_id, filename, nav_title, xhtml_bytes)

    for doc_id, title, ctx_title, blob in docs:
        raw_doc_html = fast_decrypt(blob, key_pt, iv_pt)
        clean_ctx = (ctx_title or "").replace("\xa0", " ").replace("\u200b", "").strip()
        clean_title = (title or f"Documento {doc_id}").replace("\xa0", " ").replace("\u200b", "").replace("\u2060", "").strip()
        if clean_ctx and clean_ctx.lower() != clean_title.lower():
            nav_title = f"{clean_ctx}: {clean_title}"
        else:
            nav_title = clean_title

        body_html = raw_doc_html.replace("\r", "").replace("\u200b", "").replace("\u2060", "")

        # Si es el documento 0 (Portada), poner la imagen de portada local
        if doc_id == 0:
            body_html = body_html.replace('src="jwpub-media://1102014909_S_cvr.jpg"', 'src="images/cover.jpg"')
        else:
            body_html = re.sub(r'<img[^>]*src="jwpub-media://[^"]*"[^>]*/>', "", body_html)

        # Reemplazar marcadores de página
        body_html = re.sub(
            r'<span[^>]*class="[^"]*pageNum[^"]*"[^>]*data-no="(\d+)"[^>]*>.*?</span>',
            r'<span class="page-marker">[Pág. \1]</span> ',
            body_html,
            flags=re.DOTALL,
        )

        # Reemplazar enlaces internos a otros capítulos (ej. en el Índice Doc 2): href="jwpub://p/S:1102014863/"
        def replace_internal_doc_link(m):
            meps_id = m.group(1)
            inner_txt = m.group(2)
            target_did = meps_to_doc_id.get(meps_id)
            if target_did is not None:
                return f'<a class="toc-link" href="doc_{target_did:02d}.xhtml">{inner_txt}</a>'
            return inner_txt

        body_html = re.sub(
            r'<a[^>]*href="jwpub://p/S:(\d+)/?"[^>]*>(.*?)</a>',
            replace_internal_doc_link,
            body_html,
            flags=re.DOTALL,
        )

        footnotes_aside_list = []
        used_ids = set()

        # Transformar citas bíblicas: <a href="jwpub://b/NWTR/..." data-bid="B-E" class="b">Texto</a>
        def replace_bible_link(m):
            full_tag = m.group(0)
            bid_m = re.search(r'data-bid="(\d+)-(\d+)"', full_tag)
            inner_txt = m.group(1)
            if not bid_m:
                return inner_txt
            blk, elem = int(bid_m.group(1)), int(bid_m.group(2))
            cit_range = pt_citations.get((doc_id, blk, elem))
            if not cit_range:
                return inner_txt

            fv, lv = cit_range
            fn_id = f"bfn-{blk}-{elem}"
            src_id = f"bsrc-{blk}-{elem}"

            if fn_id not in used_ids:
                used_ids.add(fn_id)
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
                rsg_idx_list = []
                seen_local_xtid = set()

                for vid in range(fv, lv + 1):
                    if vid in nwt_verses:
                        _, _, v_no, v_t = nwt_verses[vid]
                        v_parts.append(f'<span class="v-num">{v_no}</span> {html_mod.escape(v_t)}')
                    if vid in nwt_footnotes:
                        v_no = nwt_verses[vid][2] if vid in nwt_verses else ""
                        for f_t in nwt_footnotes[vid]:
                            fn_parts.append(f"<li><strong>v. {v_no}:</strong> {html_mod.escape(f_t)}</li>")
                    if vid in nwt_study_notes:
                        for sn_xhtml in nwt_study_notes[vid]:
                            sn_parts.append(f'<div class="study-note-item">{sn_xhtml}</div>')
                    if vid in raw_rsg_entries_by_vid:
                        for xtid, t_clean, r_clean, is_it, inc_ext in raw_rsg_entries_by_vid[vid]:
                            rsg_idx_list.append(f"{html_mod.escape(r_clean)} (<em>{html_mod.escape(t_clean)}</em>)")
                            if inc_ext and xtid in rsg_extracts and xtid not in seen_local_xtid:
                                seen_local_xtid.add(xtid)
                                cap_c, ext_xhtml = rsg_extracts[xtid]
                                card_html = (
                                    f'<div class="ref-extract-box">'
                                    f'<p class="ref-extract-title">{"📘 Perspicacia" if is_it else "📗 Guía de Estudio"}: '
                                    f'<strong>{html_mod.escape(r_clean)}</strong> — {html_mod.escape(t_clean)}</p>'
                                    f'{ext_xhtml}</div>'
                                )
                                if is_it:
                                    it_parts.append(card_html)
                                else:
                                    rsg_parts.append(card_html)

                aside_inner = [
                    f'<aside id="{fn_id}" epub:type="footnote" class="footnote-card">',
                    f'<div class="fn-header">📖 <strong>{html_mod.escape(ref_hdr)}</strong> <span>(Traducción del Nuevo Mundo — Edición de Estudio)</span></div>',
                    f'<p class="verse-quote">{" ".join(v_parts)}</p>',
                ]
                if fn_parts:
                    aside_inner.append(f'<div class="fn-subbox"><strong>Notas a pie de página (nwtsty):</strong><ul>{"".join(fn_parts)}</ul></div>')
                if sn_parts:
                    aside_inner.append(f'<div class="fn-subbox"><strong>Notas de estudio (nwtsty):</strong>{"".join(sn_parts)}</div>')
                if it_parts:
                    aside_inner.append(f'<div class="fn-subbox"><strong>Información en Perspicacia (it_S):</strong>{"".join(it_parts)}</div>')
                if rsg_parts:
                    aside_inner.append(f'<div class="fn-subbox"><strong>Información en la Guía de Estudio (rsg_S):</strong>{"".join(rsg_parts)}</div>')
                if rsg_idx_list:
                    unique_idx = list(dict.fromkeys(rsg_idx_list))[:10]
                    aside_inner.append(f'<p class="rsg-index-line"><strong>Índice Guía de Estudio ({html_mod.escape(ref_hdr)}):</strong> {"; ".join(unique_idx)}</p>')
                aside_inner.append(f'<p class="fn-back"><a href="#{src_id}">↩ Volver al punto de estudio</a></p>')
                aside_inner.append("</aside>")
                footnotes_aside_list.append("\n".join(aside_inner))

                return f'<a class="bible-ref" epub:type="noteref" id="{src_id}" href="#{fn_id}">📖 {inner_txt}</a>'
            else:
                return f'<a class="bible-ref" epub:type="noteref" href="#{fn_id}">📖 {inner_txt}</a>'

        body_html = re.sub(
            r'<a[^>]*href="jwpub://b/[^"]*"[^>]*>(.*?)</a>',
            replace_bible_link,
            body_html,
            flags=re.DOTALL,
        )

        # Transformar citas a extractos de publicaciones: <a class="xt" data-xtid="X" href="jwpub://p/...">Texto</a>
        def replace_extract_link(m):
            full_tag = m.group(0)
            xt_m = re.search(r'data-xtid="(\d+)"', full_tag)
            inner_txt = m.group(1)
            if not xt_m:
                return inner_txt
            xtid = int(xt_m.group(1))
            if xtid not in pt_extracts:
                return inner_txt

            fn_id = f"xfn-{xtid}"
            src_id = f"xsrc-{xtid}"
            if fn_id not in used_ids:
                used_ids.add(fn_id)
                cap_clean, ext_xhtml = pt_extracts[xtid]
                aside_inner = [
                    f'<aside id="{fn_id}" epub:type="footnote" class="footnote-card pub-card">',
                    f'<div class="fn-header pub-header">📚 <strong>{html_mod.escape(cap_clean)}</strong> <span>(Referencia directa de la lección)</span></div>',
                    f'<div class="pub-extract-body">{ext_xhtml}</div>',
                    f'<p class="fn-back"><a href="#{src_id}">↩ Volver al punto de estudio</a></p>',
                    "</aside>",
                ]
                footnotes_aside_list.append("\n".join(aside_inner))
                return f'<a class="pub-ref" epub:type="noteref" id="{src_id}" href="#{fn_id}">📚 {inner_txt}</a>'
            else:
                return f'<a class="pub-ref" epub:type="noteref" href="#{fn_id}">📚 {inner_txt}</a>'

        body_html = re.sub(
            r'<a[^>]*data-xtid="\d+"[^>]*>(.*?)</a>',
            replace_extract_link,
            body_html,
            flags=re.DOTALL,
        )

        # Limpiar cualquier otro enlace jwpub:// residual si existiera
        body_html = re.sub(r'<a[^>]*href="jwpub://[^"]*"[^>]*>(.*?)</a>', r"\1", body_html, flags=re.DOTALL)

        footnotes_section = ""
        if footnotes_aside_list:
            footnotes_section = (
                '\n<hr class="fn-divider"/>\n'
                '<section epub:type="footnotes" class="footnotes-section">\n'
                '<h2 class="fn-section-title">Fuentes Base Literales (Textos Bíblicos, Notas de Estudio, Perspicacia y Guía de Estudio)</h2>\n'
                + "\n".join(footnotes_aside_list)
                + "\n</section>"
            )

        xhtml_str = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="es" lang="es">
<head>
  <meta charset="utf-8" />
  <title>{html_mod.escape(nav_title)}</title>
  <link rel="stylesheet" type="text/css" href="styles.css" />
</head>
<body>
<section epub:type="bodymatter" class="lesson-body">
{body_html}
</section>
{footnotes_section}
</body>
</html>
"""
        # Validar que el XHTML sea XML 100% bien formado
        try:
            ET.fromstring(xhtml_str.encode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Error XML en doc_{doc_id:02d}.xhtml: {e}")

        fname = f"doc_{doc_id:02d}.xhtml"
        chapter_files.append((doc_id, fname, nav_title, xhtml_str.encode("utf-8")))
        print(f"Capítulo XHTML validado: {fname} ({len(footnotes_aside_list)} fichas emergentes)")

    # 6. Crear styles.css
    styles_css = """
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Georgia, serif;
  line-height: 1.65;
  margin: 0;
  padding: 0.8em 1em;
  color: #1e293b;
}
h1 {
  font-size: 1.5em;
  color: #0f172a;
  border-bottom: 2px solid #2563eb;
  padding-bottom: 0.3em;
  margin-top: 0.4em;
}
h2 {
  font-size: 1.25em;
  color: #1e3a8a;
  margin-top: 1.4em;
}
h3 {
  font-size: 1.1em;
  color: #0f766e;
  margin-top: 1.2em;
}
.contextTtl {
  font-size: 0.9em;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #2563eb;
  font-weight: bold;
  margin-bottom: 0.2em;
}
.page-marker {
  font-size: 0.78em;
  color: #64748b;
  font-weight: normal;
  background: #f1f5f9;
  padding: 1px 5px;
  border-radius: 4px;
}
p.se, p.s5, p.sa {
  background: #f8fafc;
  border-left: 3.5px solid #3b82f6;
  padding: 0.55em 0.8em;
  margin: 0.75em 0;
  border-radius: 0 6px 6px 0;
}
.reviewPoint, .openingContent, .blockTxt {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  padding: 0.7em 1em;
  margin: 1em 0;
}
a.bible-ref {
  color: #1d4ed8;
  background: #dbeafe;
  text-decoration: none;
  padding: 1px 6px;
  border-radius: 5px;
  font-weight: 600;
  white-space: nowrap;
}
a.pub-ref {
  color: #047857;
  background: #d1fae5;
  text-decoration: none;
  padding: 1px 6px;
  border-radius: 5px;
  font-weight: 600;
}
a.toc-link {
  color: #1d4ed8;
  text-decoration: none;
  font-weight: 500;
}
.fn-divider {
  margin: 2.5em 0 1.5em 0;
  border: none;
  border-top: 2px dashed #cbd5e1;
}
.fn-section-title {
  font-size: 1.15em;
  color: #334155;
}
.footnote-card {
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-left: 4px solid #2563eb;
  border-radius: 8px;
  padding: 0.8em 1em;
  margin: 1em 0;
}
.footnote-card.pub-card {
  border-left-color: #059669;
}
.fn-header {
  font-size: 0.95em;
  color: #1e3a8a;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 0.35em;
  margin-bottom: 0.6em;
}
.fn-header.pub-header {
  color: #065f46;
}
.fn-header span {
  font-size: 0.82em;
  color: #64748b;
  font-weight: normal;
}
.verse-quote {
  font-size: 1.02em;
  background: #f8fafc;
  padding: 0.6em 0.8em;
  border-radius: 6px;
  margin: 0.5em 0;
}
.v-num {
  font-weight: bold;
  color: #2563eb;
}
.fn-subbox {
  margin-top: 0.65em;
  padding-top: 0.5em;
  border-top: 1px dotted #cbd5e1;
  font-size: 0.92em;
}
.ref-extract-box {
  background: #f8fafc;
  border-left: 3px solid #0d9488;
  padding: 0.45em 0.75em;
  margin: 0.5em 0;
  border-radius: 0 5px 5px 0;
}
.ref-extract-title {
  margin: 0 0 0.3em 0;
  font-size: 0.9em;
  color: #0f766e;
}
.cited-ref {
  color: #1d4ed8;
  font-weight: 600;
}
.rsg-index-line {
  font-size: 0.82em;
  color: #475569;
  margin-top: 0.5em;
}
.fn-back a {
  display: inline-block;
  margin-top: 0.4em;
  font-size: 0.85em;
  font-weight: bold;
  color: #2563eb;
  text-decoration: none;
}
"""

    # 7. Crear nav.xhtml (EPUB 3 Navigation Document) y toc.ncx (EPUB 2 fallback)
    nav_items = "\n".join(
        f'      <li><a href="{fname}">{html_mod.escape(nav_title)}</a></li>'
        for _, fname, nav_title, _ in chapter_files
    )
    nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="es" lang="es">
<head>
  <meta charset="utf-8" />
  <title>Índice General</title>
  <link rel="stylesheet" type="text/css" href="styles.css" />
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Índice de la Guía de Estudio Interactiva</h1>
    <ol>
{nav_items}
    </ol>
  </nav>
</body>
</html>
"""

    book_uuid = str(uuid.uuid4())
    ncx_points = "\n".join(
        f"""    <navPoint id="navPoint-{idx}" playOrder="{idx}">
      <navLabel><text>{html_mod.escape(nav_title)}</text></navLabel>
      <content src="{fname}"/>
    </navPoint>"""
        for idx, (_, fname, nav_title, _) in enumerate(chapter_files, start=1)
    )
    toc_ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{book_uuid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>“Cumple completamente tu ministerio” — Guía de Estudio Interactiva (pt14 + nwtsty + it + rsg)</text>
  </docTitle>
  <navMap>
{ncx_points}
  </navMap>
</ncx>
"""

    manifest_items = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="css" href="styles.css" media-type="text/css"/>',
        '    <item id="cover-img" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
    ]
    spine_items = []
    for doc_id, fname, _, _ in chapter_files:
        item_id = f"doc_{doc_id:02d}"
        manifest_items.append(f'    <item id="{item_id}" href="{fname}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'    <itemref idref="{item_id}"/>')

    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId" xml:lang="es">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">urn:uuid:{book_uuid}</dc:identifier>
    <dc:title>“Cumple completamente tu ministerio” (2 Timoteo 4:5) — Guía de Estudio Interactiva con Fuentes Base</dc:title>
    <dc:language>es</dc:language>
    <dc:creator>Escuela del Servicio de Precursor (pt14_S + nwtsty_S + it_S + rsg_S)</dc:creator>
    <dc:description>Edición interactiva EPUB 3 con textos bíblicos literales, notas de estudio de la Traducción del Nuevo Mundo, referencias directas de la lección, Perspicacia y Guía de Estudio en ventanas emergentes al tocar cada cita.</dc:description>
    <meta property="dcterms:modified">2026-11-14T09:00:00Z</meta>
    <meta name="cover" content="cover-img"/>
  </metadata>
  <manifest>
{chr(10).join(manifest_items)}
  </manifest>
  <spine toc="ncx">
{chr(10).join(spine_items)}
  </spine>
</package>
"""

    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    # 8. Empaquetar archivo .epub siguiendo estrictamente el estándar OCF EPUB 3
    with zipfile.ZipFile(epub_path, "w") as zf:
        # El primer archivo DEBE ser 'mimetype' sin comprimir (ZIP_STORED)
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", content_opf.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", toc_ncx.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/styles.css", styles_css.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/images/cover.jpg", cover_bytes, compress_type=zipfile.ZIP_DEFLATED)
        for _, fname, _, xbytes in chapter_files:
            zf.writestr(f"OEBPS/{fname}", xbytes, compress_type=zipfile.ZIP_DEFLATED)

    elapsed = time.time() - t0
    print(f"\n¡EPUB 3 Interactivo generado exitosamente en {elapsed:.2f}s!")
    print(f"Ruta: {epub_path} ({os.path.getsize(epub_path) / (1024*1024):.2f} MB)")


if __name__ == "__main__":
    main()
