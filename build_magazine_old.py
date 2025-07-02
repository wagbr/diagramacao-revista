#!/usr/bin/env python3
"""build_magazine.py – gera o PDF completo da *Revista Ateísta*.

Fluxo:
1. Carrega os CSVs mais recentes em `data/` (edições & artigos_enriched).
2. Identifica a edição de maior `numero` e pega seu `_id`.
3. Filtra artigos com `status == "Aprovado"` CUJO campo `edicao` aponta para
   essa edição (normalizado por `clean_id`).
4. Converte BBCode → HTML seguro.
5. Gera capa (imagem base + logo + título) e contracapa simples.
6. Renderiza `magazine.html.j2` + `magazine.css` via Jinja2.
7. Compila PDF com WeasyPrint.

Requisitos (pip):
    pandas jinja2 weasyprint pillow bbcode bleach python-slugify

Exemplo de uso (PowerShell):
    python build_magazine.py \
        --data-dir data \
        --assets-dir assets \
        --template-dir templates \
        --cover-image assets/cover_base.jpg \
        --logo assets/logo.png \
        --out-file revista_ateista.pdf
"""

from __future__ import annotations

import argparse
import ast
import csv
import shutil
import tempfile
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageDraw, ImageFont
from slugify import slugify
from weasyprint import HTML, CSS
import bbcode
import bleach
from bleach.css_sanitizer import CSSSanitizer

# ---------------------------------------------------------------------------
# Sanitização BBCode → HTML
# ---------------------------------------------------------------------------

BB_PARSER = bbcode.Parser()
BLEACH_ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS).union(
    {"p", "span", "div", "h1", "h2", "h3", "br", "hr", "img"}
)
BLEACH_ALLOWED_ATTRS = {"*": ["class"], "img": ["src", "alt"]}
CSS_SANITIZER = CSSSanitizer(allowed_css_properties=["color", "font-size", "text-align"])  # básico

# suprime warning "NoCssSanitizerWarning"
warnings.filterwarnings("ignore", category=bleach.sanitizer.NoCssSanitizerWarning)


def bbcode_to_html(text: str) -> str:
    raw_html = BB_PARSER.format(text or "")
    clean_html = bleach.clean(
        raw_html,
        tags=BLEACH_ALLOWED_TAGS,
        attributes=BLEACH_ALLOWED_ATTRS,
        css_sanitizer=CSS_SANITIZER,
    )
    return clean_html

# ---------------------------------------------------------------------------
# Utilidades diversas
# ---------------------------------------------------------------------------

def clean_id(value: Any) -> str | None:
    """Normaliza referências Bubble: lista, objeto ou string simples."""
    if value is None:
        return None
    if isinstance(value, str) and value.startswith("["):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list) and parsed:
                return clean_id(parsed[0])
        except Exception:
            return value
    if isinstance(value, dict):
        return value.get("unique_id") or value.get("_id")
    return str(value)

# ---------------------------------------------------------------------------
# Capa & contracapa
# ---------------------------------------------------------------------------

def compose_cover(base_img: Path, logo_img: Path, title: str, subtitle: str | None) -> Path:
    base = Image.open(base_img).convert("RGBA")
    logo = Image.open(logo_img).convert("RGBA")
    logo_w = int(base.width * 0.15)
    logo = logo.resize((logo_w, int(logo_w * logo.height / logo.width)))

    margin = int(base.width * 0.05)
    base.alpha_composite(logo, dest=(margin, margin))

    draw = ImageDraw.Draw(base)
    try:
        font_title = ImageFont.truetype("arial.ttf", size=int(base.width * 0.07))
        font_sub = ImageFont.truetype("arial.ttf", size=int(base.width * 0.04))
    except OSError:
        font_title = font_sub = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), title, font=font_title)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_x = (base.width - text_w) // 2
    text_y = int(base.height * 0.65)
    draw.text((text_x, text_y), title, fill="white", font=font_title)

    if subtitle:
        bbox_s = draw.textbbox((0, 0), subtitle, font=font_sub)
        sub_w, sub_h = bbox_s[2] - bbox_s[0], bbox_s[3] - bbox_s[1]
        draw.text(((base.width - sub_w) // 2, text_y + text_h + 10), subtitle, fill="white", font=font_sub)

    out = base_img.parent / "cover_generated.png"
    base.save(out)
    return out


def blank_back_cover(color: str = "#8b0000") -> Path:
    cover = Image.new("RGB", (2480, 3508), color=color)
    path = Path(tempfile.mkdtemp()) / "back_cover.png"
    cover.save(path)
    return path

# ---------------------------------------------------------------------------
# Estilos por tipo de artigo
# ---------------------------------------------------------------------------

ARTICLE_STYLE = {
    "Opinião": {"acc": "#d4235a", "cols": 1},
    "Fatos": {"acc": "#d62839", "cols": 2},
    "Editorial": {"acc": "#222", "cols": 1},
    "Poesia": {"acc": "#9f42e0", "cols": 1, "align": "center"},
    "Divulgação científica": {"acc": "#0077ff", "cols": 2},
    "Humor": {"acc": "#00b66d", "cols": 2},
    "Eventos": {"acc": "#e68e00", "cols": 2},
    "Filosofia": {"acc": "#005f99", "cols": 2},
    "Contra-apologética": {"acc": "#951dff", "cols": 2},
}

# ---------------------------------------------------------------------------
# Função de renderização/compilação
# ---------------------------------------------------------------------------

def build_pdf(
    articles: List[Dict[str, Any]],
    edition: Dict[str, Any],
    template_dir: Path,
    css_path: Path,
    cover_path: Path,
    back_cover_path: Path,
    out_file: Path,
):
    env = Environment(loader=FileSystemLoader(template_dir), autoescape=select_autoescape())
    tpl = env.get_template("magazine.html.j2")

    for art in articles:
        art["slug"] = slugify(art["titulo"][:60])

    html = tpl.render(
        cover_image=cover_path.name,
        back_cover_image=back_cover_path.name,
        logo_path=Path(args.logo).name,
        ighlights=highlights,
        edition=edition,
        articles=articles,
        style_map=ARTICLE_STYLE,
        generated_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )

    tmp = Path(tempfile.mkdtemp())
    shutil.copy(cover_path, tmp / cover_path.name)
    shutil.copy(back_cover_path, tmp / back_cover_path.name)
    shutil.copy(css_path, tmp / css_path.name)

    html_file = tmp / "magazine.html"
    html_file.write_text(html, encoding="utf-8")

    HTML(str(html_file)).write_pdf(str(out_file), stylesheets=[CSS(str(tmp / css_path.name))])

# ---------------------------------------------------------------------------
# Helper para pegar CSV mais recente
# ---------------------------------------------------------------------------

def latest_csv(dir_: Path, prefix: str) -> Path:
    files = sorted(dir_.glob(f"{prefix}_*.csv"))
    if not files:
        raise FileNotFoundError(f"Nenhum CSV '{prefix}_*.csv' em {dir_}")
    return files[-1]

# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Gera PDF da Revista Ateísta")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--assets-dir", type=Path, required=True)
    ap.add_argument("--template-dir", type=Path, default=Path("templates"))
    ap.add_argument("--cover-image", type=Path, required=True)
    ap.add_argument("--logo", type=Path, required=True)
    ap.add_argument("--out-file", type=Path, default=Path("revista_ateista.pdf"))
    args = ap.parse_args()

    ed_csv = latest_csv(args.data_dir, "edicoes")
    art_csv = latest_csv(args.data_dir, "artigos_enriched")

    edicoes = pd.read_csv(ed_csv)
    artigos = pd.read_csv(art_csv)

    # edição mais recente
    latest_num = edicoes["numero"].max()
    edition_row = edicoes[edicoes["numero"] == latest_num].iloc[0].to_dict()
    edicao_id = edition_row["_id"]

    filt = (
        (artigos["status"] == "Aprovado") &
        (artigos["edicao"].apply(clean_id) == edicao_id)
    )
    artigos_filt = artigos[filt].copy()

    article_dicts: List[Dict[str, Any]] = []
    for _, row in artigos_filt.iterrows():
        art = row.to_dict()
        art["conteudo_html"] = bbcode_to_html(art.get("conteudo", ""))
        if art["tipo"] not in ARTICLE_STYLE:
            ARTICLE_STYLE[art["tipo"]] = {"acc": "#333", "cols": 1}
        article_dicts.append(art)

    subtitle = f"Edição nº {latest_num} – {datetime.today():%B %Y}"
    cover_path = compose_cover(args.cover_image, args.logo, edition_row.get("titulo", subtitle), subtitle)
    back_cover_path = blank_back_cover()

    css_path = args.template_dir / "magazine.css"
    
    print(f"➡️ Artigos carregados: {len(article_dicts)}")

    build_pdf(article_dicts, edition_row, args.template_dir, css_path, cover_path, back_cover_path, args.out_file)
    print(f"✅ PDF gerado: {args.out_file}")


if __name__ == "__main__":
    main()
