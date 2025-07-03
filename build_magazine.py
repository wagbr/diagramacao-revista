#!/usr/bin/env python3
"""
build_magazine.py – PDF final da Revista Ateísta
(agora com subtitle_pt passado corretamente)
"""

from __future__ import annotations
import argparse, ast, random, shutil, tempfile, warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from slugify import slugify
from weasyprint import HTML, CSS
import bbcode, bleach
from bleach.css_sanitizer import CSSSanitizer
import textwrap

# ---------- BBCode → HTML ---------------------------------------------------
BB = bbcode.Parser()
ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p","span","div","h1","h2","h3","br","hr","img"
}
ALLOWED_ATTR = {"*":["class"],"img":["src","alt"]}
SAN = CSSSanitizer(allowed_css_properties=["color","font-size","text-align"])
warnings.filterwarnings("ignore", category=bleach.sanitizer.NoCssSanitizerWarning)

def bb2html(txt:str)->str:
    return bleach.clean(BB.format(txt or ""), tags=ALLOWED_TAGS,
                        attributes=ALLOWED_ATTR, css_sanitizer=SAN)

# ---------- Helpers ---------------------------------------------------------
def clean_id(v:Any)->str|None:
    if v is None: return None
    if isinstance(v,str) and v.startswith("["):
        try:
            lst=ast.literal_eval(v)
            if isinstance(lst,list) and lst: return clean_id(lst[0])
        except Exception: return v
    if isinstance(v,dict):
        return v.get("unique_id") or v.get("_id")
    return str(v)

def latest_csv(folder:Path,prefix:str)->Path:
    return sorted(folder.glob(f"{prefix}_*.csv"))[-1]

# ---------- Cover & Back ----------------------------------------------------
def wrap_text(draw: ImageDraw.ImageDraw, text: str,
              font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """
    Quebra o texto em múltiplas linhas para não ultrapassar max_width.
    Retorna lista de linhas.
    """
    words = text.split()
    lines: list[str] = []
    current = []

    for w in words:
        test_line = " ".join(current + [w])
        w_box = draw.textbbox((0, 0), test_line, font=font)[2]
        if w_box <= max_width:
            current.append(w)
        else:
            lines.append(" ".join(current))
            current = [w]
    if current:
        lines.append(" ".join(current))
    return lines

def draw_blur_shadow(base: Image.Image, pos: tuple[int, int], text: str,
                     font: ImageFont.FreeTypeFont,
                     *, fill="white", shadow="black",
                     blur_radius: int = 6, offset: tuple[int, int] = (4,4)) -> None:
    """
    Desenha `text` em `base`, com sombra desfocada (GaussianBlur).

    blur_radius – quanto maior, mais “spread” a sombra.
    offset      – deslocamento da sombra em relação ao texto.
    """
    x, y = pos
    ox, oy = offset

    # 1) cria camada RGBA transparente
    shadow_layer = Image.new("RGBA", base.size, (0,0,0,0))
    shadow_draw  = ImageDraw.Draw(shadow_layer)

    # 2) escreve o texto em preto na posição deslocada
    shadow_draw.text((x + ox, y + oy), text, font=font, fill=shadow)

    # 3) aplica blur na camada
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur_radius))

    # 4) compõe sobre a imagem base
    base.alpha_composite(shadow_layer)

    # 5) escreve o texto principal
    draw = ImageDraw.Draw(base)
    draw.text((x, y), text, font=font, fill=fill)

# ---- função principal da capa ----------------------------------------
def compose_cover(base_img: Path, title: str, subtitle: str | None = None) -> Path:
    base = Image.open(base_img).convert("RGBA")
    draw = ImageDraw.Draw(base)

    font_title = ImageFont.truetype("fonts/Lora-Bold.ttf", 196)
    font_sub   = ImageFont.truetype("fonts/Lora-Bold.ttf", 196)

    # ----- título em linhas -------------
    max_width = int(base.width * 0.8)  # 80 % da capa
    lines = wrap_text(draw, title, font_title, max_width)

    # Altura total do bloco
    line_height = font_title.getbbox("Ay")[3]  # altura da linha
    block_h = len(lines) * line_height + (len(lines)-1) * 10  # 10 px entre linhas
    start_y = int(base.height * 0.65) - block_h // 2

    for i, line in enumerate(lines):
        w_line = draw.textbbox((0, 0), line, font=font_title)[2]
        x_line = (base.width - w_line) // 2
        y_line = start_y + i * (line_height + 10)
        draw_blur_shadow(base, (x_line, y_line), line, font_title,
                         fill="white", shadow="black", blur_radius=8, offset=(4,4))

    # ----- subtítulo opcional -----------
    if subtitle:
        sw, sh = draw.textbbox((0, 0), subtitle, font=font_sub)[2:]
        sx = (base.width - sw) // 2
        sy = start_y + block_h + 20
        draw_blur_shadow(base, (sx, sy), subtitle, font_sub,
                         fill="white", shadow="black", blur_radius=4, offset=(2,2))

    out = base_img.parent / "cover_generated.png"
    base.save(out)
    return out


def blank_back(color="#d62839")->Path:
    back=Image.new("RGB",(2480,3508),color)
    p=Path(tempfile.mkdtemp())/"back.png"
    back.save(p)
    return p

# ---------- Style map -------------------------------------------------------
STYLE:Dict[str,Dict[str,Any]]={
    "Opinião":{"acc":"#0d0d0d","cols":1},
    "Fatos":{"acc":"#0d0d0d","cols":2},
    "Editorial":{"acc":"#0d0d0d","cols":1},
    "Poesia":{"acc":"#0d0d0d","cols":1,"align":"center"},
    "Divulgação científica":{"acc":"#0d0d0d","cols":2},
    "Humor":{"acc":"#0d0d0d","cols":2},
    "Eventos":{"acc":"#0d0d0d","cols":2},
    "Filosofia":{"acc":"#0d0d0d","cols":2},
    "Contra-apologética":{"acc":"#0d0d0d","cols":3},
}

# ---------- Build PDF -------------------------------------------------------
def build_pdf(articles:list[dict], edition:dict, tpl_dir:Path, css:Path,
              cover:Path, back:Path, logo:Path, highlights:list[dict],
              subtitle_pt:str, out_file:Path):

    env=Environment(loader=FileSystemLoader(tpl_dir),
                    autoescape=select_autoescape())
    tpl=env.get_template("magazine.html.j2")
    for a in articles:
        a["slug"]=slugify(a["titulo"][:60])

    html=tpl.render(
        edition=edition,
        articles=articles,
        style_map=STYLE,
        cover_image=cover.name,
        back_cover_image=back.name,
        logo_path=logo.name,
        highlights=highlights,
        subtitle_pt=subtitle_pt,
    )

    tmp=Path(tempfile.mkdtemp())
    for p in (cover,back,css,logo): shutil.copy(p,tmp/p.name)

    # copia fontes
    fonts_src=Path("assets/fonts")
    (tmp/"fonts").mkdir(exist_ok=True)
    for ttf in fonts_src.glob("*.ttf"):
        shutil.copy(ttf,tmp/"fonts"/ttf.name)

    (tmp/"magazine.html").write_text(html,encoding="utf-8")
    HTML(str(tmp/"magazine.html")).write_pdf(str(out_file),
                                            stylesheets=[CSS(str(tmp/css.name))])

# ---------- Main ------------------------------------------------------------
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-dir",type=Path,required=True)
    ap.add_argument("--template-dir",type=Path,default=Path("templates"))
    ap.add_argument("--cover-image",type=Path,required=True)
    ap.add_argument("--logo",type=Path,required=True)
    ap.add_argument("--out-file",type=Path,default=Path("revista_ateista.pdf"))
    args=ap.parse_args()

    ed_df=pd.read_csv(latest_csv(args.data_dir,"edicoes"))
    art_df=pd.read_csv(latest_csv(args.data_dir,"artigos_enriched"))

    latest_num=ed_df["numero"].max()
    edition=ed_df[ed_df["numero"]==latest_num].iloc[0].to_dict()
    ed_id=edition["_id"]

    pt_meses=["janeiro","fevereiro","março","abril","maio","junho",
              "julho","agosto","setembro","outubro","novembro","dezembro"]
    subtitle_pt=f"Edição nº {latest_num} – {pt_meses[datetime.today().month-1].capitalize()} de {datetime.today().year}"

    # Artigos aprovados da edição
    mask=(art_df["status"]=="Aprovado")&(art_df["edicao"].apply(clean_id)==ed_id)
    arts=art_df[mask].copy()

    articles=[]
    for _,row in arts.iterrows():
        d=row.to_dict(); d["conteudo_html"]=bb2html(d.get("conteudo",""))
        articles.append(d)
        if d["tipo"] not in STYLE: STYLE[d["tipo"]]={"acc":"#333","cols":1}

    # Editorial primeiro
    articles=sorted(articles,key=lambda a:0 if a["tipo"]=="Editorial" else 1)

    # Highlights
    cand=[a for a in articles if a["tipo"]!="Editorial" and len(a["titulo"])<=50]
    highlights=[{"title":p["titulo"],"page":"?"} for p in random.sample(cand,min(3,len(cand)))]

    cover = compose_cover(
    args.cover_image,
    title=edition.get("titulo", f"Edição nº {latest_num}"),
    subtitle=""
    )   
    back=blank_back()
    css=args.template_dir/"magazine.css"

    build_pdf(articles,edition,args.template_dir,css,cover,back,args.logo,
              highlights,subtitle_pt,args.out_file)
    print("✅ PDF gerado:",args.out_file)

if __name__=="__main__":
    main()