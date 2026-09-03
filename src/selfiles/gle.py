"""
Gera SVG das paginas do arquivo GL1.gle (XML do AcSELerator QuickSet).

Cada pagina vira um SVG com:
  - Caixas para SYMBOLs (entrada/saida/Relay Word bit)
  - Formas para portas logicas (AND, OR, NOT, PLT, ...)
  - Polylines para conexoes
  - Cada SYMBOL com data-attr para colorir via JavaScript com valores ao vivo

Uso:
    python3 gle_to_svg.py /caminho/GL1.gle.xml [--page "TRIP"]
"""

from __future__ import annotations

import argparse
import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# Espacamento padrao entre portas verticais (em px do canvas GLE)
PORT_SPACING = 12
PORT_BOT_PAD = 6     # padding apos a ultima porta

# Offset da PRIMEIRA porta em relacao ao `top` do elemento (em px).
# Esses valores foram medidos diretamente nas conexoes do GLE -- elementos
# "altos" como PLT e PCNDTIMER tem uma header row pro label, entao a primeira
# porta fica 2 grades abaixo (24px); ja AND/OR usam 1 grade (12px).
PORT_FIRST_OFFSET = {
    "AND": 12, "OR": 12, "NOT": 12, "MULT": 12,
    "EQ": 12, "NE": 12, "LT": 12, "LE": 12, "GT": 12, "GE": 12,
    "ADD": 12, "SUB": 12, "DIV": 12,
    "RTRIG": 12,
    "AST": 24, "PLT": 24, "PCNDTIMER": 24,
    "PCN": 24, "ALT": 24,
    # 7xx (SELOGIC):
    "LATCH": 24, "TIMER": 24, "COUNTER": 24,
    "Connector": 6,
}
DEFAULT_FIRST_OFFSET = 12

# Tamanho minimo (largura, altura) por tipo de elemento.
# A altura real eh ajustada para acomodar todas as portas atuais (contadas
# a partir das conexoes da pagina).
ELEMENT_MIN_SIZE = {
    "SYMBOL": (66, 12),
    "AND": (36, 24),
    "OR": (36, 24),
    "NOT": (24, 18),
    "PLT": (54, 30),       # cabe rotulo "PLT04"
    "PCNDTIMER": (66, 36), # cabe rotulo "PCT03"
    "PCN": (66, 36),       # contador SEL-4xx: 3 in / 2 out
    "ALT": (54, 30),       # auxiliary latch SEL-4xx (mesma forma do PLT)
    # 7xx (SELOGIC):
    "LATCH":   (54, 30),   # _LT## (S/R latch, 2 in / 1 out)
    "TIMER":   (66, 36),   # _SV## (timer, 3 in / 1 out)
    "COUNTER": (66, 36),   # _SC## (counter, 3 in / 1+ out)
    "RTRIG": (36, 18),
    "EQ": (36, 24),
    "NE": (36, 24),
    "LT": (36, 24),
    "LE": (36, 24),
    "GT": (36, 24),
    "GE": (36, 24),
    "ADD": (36, 24),
    "SUB": (36, 24),
    "DIV": (36, 24),
    "MULT": (30, 18),
    "AST": (52, 24),       # cabe rotulo "AST01"
    "Connector": (24, 12),
}

# Numero default de portas por tipo (caso a pagina nao mostre conexoes).
DEFAULT_PORTS = {
    "SYMBOL": (1, 1),
    "AND": (2, 1),
    "OR": (2, 1),
    "NOT": (1, 1),
    "PLT": (2, 1),       # Set, Reset
    "PCNDTIMER": (3, 1), # Input, Pickup, Dropout
    "PCN": (3, 2),       # in, Preset Value, Reset -> Q + outras
    "ALT": (2, 1),       # Set, Reset (S/R latch como PLT)
    "RTRIG": (1, 1),
    "EQ": (2, 1),
    "NE": (2, 1),
    "LT": (2, 1),
    "LE": (2, 1),
    "GT": (2, 1),
    "GE": (2, 1),
    "MULT": (2, 1),
    "ADD": (2, 1),
    "SUB": (2, 1),
    "DIV": (2, 1),
    "AST": (3, 2),
    # 7xx (SELOGIC):
    "LATCH":   (2, 1),  # _LT##: S, R
    "TIMER":   (3, 1),  # _SV##: in, PU, DO
    "COUNTER": (3, 1),  # _SC##: in, PV, R
    "Connector": (1, 1),
}


def parse_gle(path: Path) -> ET.Element:
    raw = path.read_bytes().decode("latin-1")
    raw = raw.replace('encoding="utf-8"', 'encoding="iso-8859-1"', 1)
    return ET.fromstring(raw)


def count_actual_ports(page: ET.Element) -> tuple[dict, dict]:
    """
    Conta portas realmente conectadas por elemento (input e output).
    Retorna (in_counts, out_counts): dict[element_id] -> int.
    """
    in_counts: defaultdict[str, int] = defaultdict(int)
    out_counts: defaultdict[str, int] = defaultdict(int)
    for conn in page.findall(".//connection"):
        src = conn.find("source_port")
        dst = conn.find("sink_port")
        if src is not None:
            sid = src.get("element_id")
            sp = int(src.get("port_number", "0"))
            if sid is not None:
                out_counts[sid] = max(out_counts[sid], sp + 1)
        if dst is not None:
            did = dst.get("element_id")
            dp = int(dst.get("port_number", "0"))
            if did is not None:
                in_counts[did] = max(in_counts[did], dp + 1)
    return in_counts, out_counts


def deduce_symbol_widths(page: ET.Element) -> dict[str, int]:
    """
    Para cada SYMBOL com saida (source), deduz sua largura real a partir
    do primeiro waypoint da conexao (que comeca na borda direita do simbolo).
    SYMBOLs sem saida ficam sem largura aqui -- caem no fallback por texto.
    """
    el_left = {}
    for el in page.findall(".//element"):
        if el.get("type") == "SYMBOL":
            el_left[el.get("id")] = int(el.get("left", "0"))
    widths: dict[str, int] = {}
    for c in page.findall(".//connection"):
        src = c.find("source_port")
        if src is None:
            continue
        sid = src.get("element_id")
        # `element_id` e um atributo do XML: pode faltar, e ai a busca no
        # dicionario so podia dar errado.
        if sid is None or sid not in el_left:
            continue
        pts = c.findall(".//point")
        if not pts:
            continue
        fx = int(pts[0].get("x", "0"))
        w = fx - el_left[sid]
        if 12 <= w <= 200:
            # Pega o maior valor visto (raras vezes a 1a wp e dentro do bloco)
            widths[sid] = max(widths.get(sid, 0), w)
    return widths


def find_junctions(page: ET.Element) -> set[tuple[int, int]]:
    """
    Pontos onde 3+ conexoes se encontram (T ou + junction).
    Sao locais onde o sinal se ramifica: precisam de um dot para
    distinguir de "linhas que apenas se cruzam".
    """
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for c in page.findall(".//connection"):
        pts = c.findall(".//point")
        seen = set()
        for pt in pts:
            xy = (int(pt.get("x", "0")), int(pt.get("y", "0")))
            if xy not in seen:
                counts[xy] += 1
                seen.add(xy)
    return {xy for xy, n in counts.items() if n >= 3}


def compute_size(info: dict, n_in: int, n_out: int) -> tuple[int, int]:
    """Tamanho final (w, h) considerando o tipo e n. de portas."""
    t = info["type"]
    min_w, min_h = ELEMENT_MIN_SIZE.get(t, (36, 24))
    if t == "SYMBOL":
        return (min_w, min_h)
    # Altura: do top ate apos a ultima porta + padding
    max_ports = max(n_in, n_out, 1)
    first_off = PORT_FIRST_OFFSET.get(t, DEFAULT_FIRST_OFFSET)
    h_for_ports = first_off + (max_ports - 1) * PORT_SPACING + PORT_BOT_PAD
    return (min_w, max(min_h, h_for_ports))


def element_info(el: ET.Element) -> dict:
    etype = el.get("type", "?")
    le = el.find("logic_element")
    name = ""
    alias = ""
    # Modificadores de porta: {port_index: "NOT" | "RTRIG" | "FTRIG"} para cada
    # grupo (inputs e outputs).
    input_mods: dict[int, str] = {}
    output_mods: dict[int, str] = {}
    # Comentarios por porta (descricao livre do sinal).
    input_comments: dict[int, str] = {}
    output_comments: dict[int, str] = {}
    if le is not None:
        name = le.get("physical_instance_name", "") or ""
        alias = le.get("alias", "") or ""
        port_groups = le.findall("ports")
        for grp_i, pg in enumerate(port_groups[:2]):
            tgt_mods = input_mods if grp_i == 0 else output_mods
            tgt_cmt = input_comments if grp_i == 0 else output_comments
            for p in pg.findall("port"):
                idx = int(p.get("index", "0"))
                op = p.find("port_operator")
                if op is not None:
                    tgt_mods[idx] = op.get("operator_type", "")
                c = p.find("comment")
                if c is not None and c.text and c.text.strip():
                    tgt_cmt[idx] = c.text.strip()
    return {
        "id": el.get("id"),
        "type": etype,
        "left": int(el.get("left", "0")),
        "top": int(el.get("top", "0")),
        "name": name,
        "alias": alias,
        "input_mods": input_mods,
        "output_mods": output_mods,
        "input_comments": input_comments,
        "output_comments": output_comments,
        # O `<label>` e' filho DIRETO do `<element>`, e nao do
        # `<logic_element>` -- um Connector nao tem logic_element nenhum, e e'
        # por isso que o nome dele nao chegava ao desenho.
        "label": (el.findtext("label") or "").strip(),
    }


def page_bounds(page: ET.Element, in_counts: dict, out_counts: dict,
                sym_widths: dict[str, int]) -> tuple[int, int, int]:
    """Devolve (max_x, max_y, min_x) considerando o espaco dos comentarios.

    min_x pode ser negativo: comentarios de portas no lado direito (output)
    sao renderizados a ESQUERDA da caixa e podem ultrapassar x=0.
    """
    max_x = max_y = 0
    min_x = 0
    for el in page.findall(".//element"):
        info = element_info(el)
        if info["type"] == "SYMBOL":
            w = sym_widths.get(info["id"]) or _estimate_symbol_width(info["name"])
            h = ELEMENT_MIN_SIZE["SYMBOL"][1]
        else:
            n_in = in_counts.get(info["id"], DEFAULT_PORTS.get(info["type"], (1, 1))[0])
            n_out = out_counts.get(info["id"], DEFAULT_PORTS.get(info["type"], (1, 1))[1])
            w, h = compute_size(info, n_in, n_out)
        max_x = max(max_x, info["left"] + w)
        max_y = max(max_y, info["top"] + h)
        # Comentarios de portas estendem o canvas (texto fica do MESMO lado
        # da porta, acima da linha):
        # - output_comments (lado direito) -> texto a DIREITA da caixa
        # - input_comments (lado esquerdo) -> texto a ESQUERDA da caixa
        for txt in info.get("output_comments", {}).values():
            max_x = max(max_x, info["left"] + w + _estimate_comment_width(txt))
        for txt in info.get("input_comments", {}).values():
            min_x = min(min_x, info["left"] - _estimate_comment_width(txt))
    for pt in page.findall(".//connection/points/point"):
        max_x = max(max_x, int(pt.get("x", "0")))
        max_y = max(max_y, int(pt.get("y", "0")))
    return max_x + 20, max_y + 20, min_x - 10 if min_x < 0 else 0


def group_bounds(group: ET.Element, in_counts: dict, out_counts: dict,
                 sym_widths: dict[str, int]) -> tuple[int, int, int, int] | None:
    """
    Bounding box dos elementos dentro do <group>, ja considerando o tamanho
    real de cada elemento. Retorna (x0, y0, x1, y1) ou None se vazio.
    """
    x0 = y0 = 10**9
    x1 = y1 = -(10**9)
    found = False
    for el in group.iter("element"):
        info = element_info(el)
        if info["type"] == "SYMBOL":
            w = sym_widths.get(info["id"]) or _estimate_symbol_width(info["name"])
            h = ELEMENT_MIN_SIZE["SYMBOL"][1]
        elif info["type"] == "Text":
            # Elementos Text nao tem tamanho conhecido; usa minimo
            w, h = ELEMENT_MIN_SIZE.get("SYMBOL", (66, 12))
        else:
            n_in = in_counts.get(info["id"], DEFAULT_PORTS.get(info["type"], (1, 1))[0])
            n_out = out_counts.get(info["id"], DEFAULT_PORTS.get(info["type"], (1, 1))[1])
            w, h = compute_size(info, n_in, n_out)
        x0 = min(x0, info["left"])
        y0 = min(y0, info["top"])
        x1 = max(x1, info["left"] + w)
        y1 = max(y1, info["top"] + h)
        found = True
    if not found:
        return None
    return x0, y0, x1, y1


def render_group(group: ET.Element, in_counts: dict, out_counts: dict,
                 sym_widths: dict[str, int]) -> str:
    """
    Renderiza o frame pontilhado + label de um <group> em torno dos seus
    elementos. O retangulo e dimensionado a partir do bounding box dos
    elementos internos + paddings; o label fica acima dos blocos, centrado
    horizontalmente dentro do frame.
    """
    b = group_bounds(group, in_counts, out_counts, sym_widths)
    if b is None:
        return ""
    x0, y0, x1, y1 = b
    fx = x0 - GROUP_PAD_LEFT
    fy = y0 - GROUP_PAD_TOP
    fw = (x1 + GROUP_PAD_RIGHT) - fx
    fh = (y1 + GROUP_PAD_BOTTOM) - fy

    lbl_el = group.find("label")
    label = (lbl_el.text or "").strip() if lbl_el is not None else ""
    gid = html.escape(group.get("id", ""))

    parts = [
        f'<g class="group-grp" data-group-id="{gid}">',
        f'<rect class="group-frame" x="{fx}" y="{fy}" '
        f'width="{fw}" height="{fh}" rx="2"/>',
    ]

    cb_size = 8
    cb_pad = 3   # gap entre checkbox e texto do label
    cy = fy + GROUP_LABEL_BAND / 2 + 2

    if label:
        text_safe = html.escape(label)
        text_w = max(20, int(len(label) * 4.8) + 8)
        # Banda total = checkbox + gap + texto. Centralizada no frame.
        band_w = cb_size + cb_pad + text_w
        band_left = fx + fw / 2 - band_w / 2
        cb_x = band_left
        cb_y = cy - cb_size / 2
        text_cx = band_left + cb_size + cb_pad + text_w / 2
        # Fundo branco unico cobrindo checkbox + label (interrompe o tracejado)
        parts.append(
            f'<rect class="group-label-bg" '
            f'x="{band_left:.1f}" y="{cy - GROUP_LABEL_BAND/2:.1f}" '
            f'width="{band_w}" height="{GROUP_LABEL_BAND}"/>'
        )
        parts.append(
            f'<g class="group-checkbox" data-group-id="{gid}">'
            f'<rect class="group-check-box" x="{cb_x:.1f}" y="{cb_y:.1f}" '
            f'width="{cb_size}" height="{cb_size}" rx="1"/>'
            f'<path class="group-check-mark" '
            f'd="M{cb_x+1.5:.1f},{cb_y+4.2:.1f} '
            f'L{cb_x+3.5:.1f},{cb_y+6.2:.1f} '
            f'L{cb_x+6.5:.1f},{cb_y+1.5:.1f}"/>'
            f'</g>'
        )
        parts.append(
            f'<text class="group-label" x="{text_cx:.1f}" y="{cy:.1f}">{text_safe}</text>'
        )
    else:
        # Sem label: checkbox centrado em cima da linha tracejada.
        cb_x = fx + fw / 2 - cb_size / 2
        cb_y = cy - cb_size / 2
        bg_pad = 2
        parts.append(
            f'<rect class="group-label-bg" '
            f'x="{cb_x - bg_pad:.1f}" y="{cy - GROUP_LABEL_BAND/2:.1f}" '
            f'width="{cb_size + 2*bg_pad}" height="{GROUP_LABEL_BAND}"/>'
        )
        parts.append(
            f'<g class="group-checkbox" data-group-id="{gid}">'
            f'<rect class="group-check-box" x="{cb_x:.1f}" y="{cb_y:.1f}" '
            f'width="{cb_size}" height="{cb_size}" rx="1"/>'
            f'<path class="group-check-mark" '
            f'd="M{cb_x+1.5:.1f},{cb_y+4.2:.1f} '
            f'L{cb_x+3.5:.1f},{cb_y+6.2:.1f} '
            f'L{cb_x+6.5:.1f},{cb_y+1.5:.1f}"/>'
            f'</g>'
        )
    parts.append("</g>")
    return "\n".join(parts)


# CSS embutido nas SVGs
CSS = """
.bg              { fill: #fefefe; }
.element-symbol  { fill: #fff;    stroke: #555; stroke-width: 0.8; }
.element-symbol.active   { fill: #b8f0b8; stroke: #1a6a1a; }
.element-symbol.inactive { fill: #f5f5f5; stroke: #888; }
.element-gate    { fill: #fff8d8; stroke: #a08020; stroke-width: 0.8; }
.element-logic   { fill: #d8e8ff; stroke: #2050a0; stroke-width: 0.8; }
.element-timer   { fill: #ffe8cc; stroke: #c06000; stroke-width: 0.8; }
.element-latch   { fill: #f0d8f0; stroke: #802080; stroke-width: 0.8; }
/* Contador (PCN nos 4xx, COUNTER nos 7xx). `render_pcn` e
   `render_counter_7xx` sempre pediram esta classe -- e ate aqui ela nao
   existia em lugar nenhum, entao o `rect` caia no fill preto padrao do
   SVG e o bloco inteiro virava uma caixa preta com o rotulo #222 por
   cima. Aparece em 8 dos 418 .gle do corpus, entre eles uma pagina
   chamada "CONTADORES", cuja unica funcao e mostrar contadores. */
.element-counter { fill: #e2f0dc; stroke: #3f7a2e; stroke-width: 0.8; }
.element-text    { font-family: monospace; font-size: 7px; fill: #111; text-anchor: middle; dominant-baseline: middle; pointer-events: none; }
.gate-label      { font-family: monospace; font-size: 7.5px; font-weight: bold; fill: #222; text-anchor: middle; dominant-baseline: middle; }
.connection      { fill: none; stroke: #404040; stroke-width: 0.6; }
.connection.active   { stroke: #16a316; stroke-width: 1.8; }
.junction-dot    { fill: #202020; stroke: none; }
.junction-dot.active { fill: #16a316; }
.port-pin        { stroke: #666; stroke-width: 0.6; }
.port-bubble     { fill: #fff; stroke: #555; stroke-width: 0.6; }
.port-edge       { font-family: monospace; font-size: 7px; fill: #c00; font-weight: bold; text-anchor: start; dominant-baseline: middle; pointer-events: none; }
.port-sublabel   { font-family: monospace; font-size: 5px; fill: #555; dominant-baseline: middle; }
.port-comment    { font-family: monospace; font-size: 6px; fill: #444; dominant-baseline: alphabetic; pointer-events: none; }
.element-symbol.const { fill: #eef2ff; stroke: #5566a0; stroke-width: 0.8; }
.element-symbol.const + .element-text, .const-text { fill: #1a2a6a; font-weight: bold; }
.element-analog        { fill: #fff4d9; stroke: #b07a18; stroke-width: 0.8; }
.element-analog.has-value { fill: #fde8b5; stroke: #8a5a06; }
.analog-name           { font-family: monospace; font-size: 7px; fill: #4a3500; text-anchor: middle; dominant-baseline: middle; pointer-events: none; font-weight: bold; }
.analog-value          { font-family: monospace; font-size: 6.5px; fill: #6a4500; text-anchor: middle; dominant-baseline: middle; pointer-events: none; }
.analog-value.live     { fill: #1a3a8a; font-weight: bold; }
.group-frame      { fill: none; stroke: #6a6a6a; stroke-width: 0.6; stroke-dasharray: 3,2; }
.group-label      { font-family: monospace; font-size: 8px; font-weight: bold; fill: #333; text-anchor: middle; dominant-baseline: middle; }
.group-label-bg   { fill: #fefefe; stroke: none; }
.group-checkbox       { cursor: pointer; }
.group-check-box      { fill: #fff; stroke: #555; stroke-width: 0.6; }
.group-check-mark     { fill: none; stroke: #2050a0; stroke-width: 1.4; stroke-linecap: round; stroke-linejoin: round; visibility: hidden; pointer-events: none; }
.group-grp.checked .group-check-mark { visibility: visible; }
.group-grp.checked .group-frame      { stroke: #2050a0; stroke-width: 1.0; }
.group-grp.checked .group-check-box  { stroke: #2050a0; }
"""

# Padding (em px do canvas GLE) entre o frame do <group> e os blocos internos.
GROUP_PAD_LEFT = 10
GROUP_PAD_RIGHT = 10
GROUP_PAD_BOTTOM = 10
# Espaco extra acima do bloco topo para acomodar o label dentro do frame.
GROUP_PAD_TOP = 22
# Altura reservada para o label (centralizado nessa faixa).
GROUP_LABEL_BAND = 14

# SYMBOLs cujo "nome" e um literal numerico (0, 1, 10, 600, ...) sao
# CONSTANTES (entradas de setpoint pra timers/comparadores). Nao devem
# ser consultados na Relay Word nem coloridos como "desconhecido".
INT_LITERAL_RE = re.compile(r"^-?\d+(\.\d+)?$")


def is_const_symbol_name(name: str) -> bool:
    return bool(INT_LITERAL_RE.fullmatch((name or "").strip()))

# Largura aproximada de uma letra monospace fs=6 (px).
PORT_COMMENT_CHAR_W = 3.5
# Folga entre a borda do elemento e o inicio/fim do texto do comentario.
PORT_COMMENT_GAP = 4


def _estimate_comment_width(text: str) -> int:
    """Largura aproximada do texto do comentario, em px."""
    return int(len(text or "") * PORT_COMMENT_CHAR_W) + PORT_COMMENT_GAP


def _port_comment_svg(comment: str, anchor_x: float, port_y: float,
                      side: str) -> str:
    """
    Texto do comentario na MESMA lateral da porta, logo ACIMA da linha do
    pino/fio (para nao sobrepor a conexao).
      `side="right"` (porta no lado direito): texto a DIREITA do anchor_x,
        text-anchor=start (cresce pra direita).
      `side="left"`  (porta no lado esquerdo): texto a ESQUERDA do anchor_x,
        text-anchor=end (cresce pra esquerda).
    """
    if not comment:
        return ""
    # Baseline 1.5px acima da linha do pino -- texto sobe, conexao livre.
    y_text = port_y - 1.5
    if side == "right":
        x = anchor_x + PORT_COMMENT_GAP
        ta = "start"
    else:
        x = anchor_x - PORT_COMMENT_GAP
        ta = "end"
    return (
        f'<text class="port-comment" x="{x}" y="{y_text}" '
        f'text-anchor="{ta}">{html.escape(comment)}</text>'
    )


def _estimate_symbol_width(name: str) -> int:
    """Largura aproximada para um SYMBOL com base no comprimento do texto."""
    n = max(len(name or ""), 2)
    # Monospace fs=7: ~5.2px/char + 14px de padding
    return max(30, int(n * 5.5) + 14)


def render_symbol(info: dict, width: int | None = None,
                  analog_group_key: str | None = None) -> str:
    """
    SVG para um SYMBOL: caixa com texto.
    `width`: se providos (deduzidos das conexoes), usa esse valor exato pra
    o desenho casar com os endpoints reais das linhas do GLE.
    Atributo data-bit usado pelo dashboard pra colorir bits ativos.
    Se `analog_group_key` for fornecido, o SYMBOL e renderizado como bloco
    analogico (cor distinta, sem coloring bit-0/bit-1) e ganha um
    placeholder <text class="analog-value">--</text> que o JS substitui pela
    leitura ao vivo de fm_data['analogs'][NAME].
    """
    _, h = ELEMENT_MIN_SIZE["SYMBOL"]
    name = info["name"] or info["alias"] or ""
    if width is None:
        w = _estimate_symbol_width(name)
    else:
        w = width
    x, y = info["left"], info["top"]
    safe_name = html.escape(name)
    is_const = is_const_symbol_name(name)
    is_analog = analog_group_key is not None and not is_const
    if is_analog:
        classes = "element-symbol element-analog"
    elif is_const:
        classes = "element-symbol const"
    else:
        classes = "element-symbol"
    data = ""
    if is_const:
        data = f' data-const="{html.escape(name)}"'
    elif is_analog:
        data = (f' data-analog="{html.escape(name)}"'
                f' data-analog-group="{html.escape(analog_group_key or "")}"')
    elif name:
        data = f' data-bit="{html.escape(name)}"'
    # Comentarios das portas (SYMBOL tem 1 porta unica de cada lado, idx=0):
    # comentario fica do MESMO lado da porta, acima da linha do fio.
    #   porta no lado direito (output) -> texto a DIREITA da caixa.
    #   porta no lado esquerdo (input) -> texto a ESQUERDA da caixa.
    cy = y + h / 2
    cmt_svg = ""
    out_cmt = info.get("output_comments", {}).get(0, "")
    if out_cmt:
        cmt_svg += _port_comment_svg(out_cmt, x + w, cy, "right")
    in_cmt = info.get("input_comments", {}).get(0, "")
    if in_cmt:
        cmt_svg += _port_comment_svg(in_cmt, x, cy, "left")
    # Modificadores de porta no SYMBOL (NOT bubble, RTRIG/FTRIG seta).
    # idx=0 e o unico, mas pode estar tanto no grupo de input (lado esquerdo)
    # quanto no de output (lado direito).
    op_svg = ""
    omod = info.get("output_mods", {}).get(0, "")
    if omod == "NOT":
        op_svg += f'<circle class="port-bubble" cx="{x + w + 2}" cy="{cy}" r="2"/>'
    elif omod == "RTRIG":
        op_svg += f'<text class="port-edge" x="{x + w - 8}" y="{cy + 0.5}">↑</text>'
    elif omod == "FTRIG":
        op_svg += f'<text class="port-edge" x="{x + w - 8}" y="{cy + 0.5}">↓</text>'
    imod = info.get("input_mods", {}).get(0, "")
    if imod == "NOT":
        op_svg += f'<circle class="port-bubble" cx="{x - 2}" cy="{cy}" r="2"/>'
    elif imod == "RTRIG":
        op_svg += f'<text class="port-edge" x="{x + 2}" y="{cy + 0.5}">↑</text>'
    elif imod == "FTRIG":
        op_svg += f'<text class="port-edge" x="{x + 2}" y="{cy + 0.5}">↓</text>'
    # Bloco analogico: caixa do mesmo tamanho (preserva alinhamento dos fios),
    # com o NOME dentro e o VALOR como tag logo abaixo da caixa. O placeholder
    # mostra '---' ate o JS substituir pela leitura ao vivo de
    # values.analogs[NAME]. Quando o rele nao expoe esse canal, o JS deixa
    # '---' (caso "N/A"). Texto abaixo da caixa transborda do bounding box do
    # SVG mas e absorvido pelo padding/overflow do viewer (rect.bg cobre tudo
    # ate as bordas do viewBox; valores ficam visiveis sobre o fundo).
    if is_analog:
        inner = (
            f'<rect class="{classes}" x="{x}" y="{y}" width="{w}" height="{h}"/>'
            f'<text class="analog-name" x="{x + w / 2}" y="{y + h / 2}">{safe_name}</text>'
            f'<text class="analog-value" x="{x + w / 2}" y="{y + h + 5}">---</text>'
        )
    else:
        inner = (
            f'<rect class="{classes}" x="{x}" y="{y}" width="{w}" height="{h}"/>'
            f'<text class="element-text" x="{x + w / 2}" y="{y + h / 2}">{safe_name}</text>'
        )
    return (
        f'<g class="symbol-grp" id="el-{info["id"]}"{data}>'
        f'{inner}'
        f'{op_svg}{cmt_svg}'
        f'</g>'
    )


def _port_y(top: int, port_index: int, element_type: str) -> int:
    """
    Posicao Y do port_index dentro de uma caixa que comeca em y=top.
    Offset da primeira porta depende do tipo (PLT/PCNDTIMER tem 24, outros 12).
    """
    first_off = PORT_FIRST_OFFSET.get(element_type, DEFAULT_FIRST_OFFSET)
    return top + first_off + port_index * PORT_SPACING


def _input_pins_svg(x: int, y: int, n: int, mods: dict[int, str],
                    etype: str, side_w: int = 4) -> str:
    """
    Pinos de entrada: tracinhos + bubble (NOT) ou arrow (RTRIG/FTRIG).
    bubble = circulo pequeno fora da borda; arrow = setinha dentro da borda.
    """
    out = []
    for i in range(n):
        py = _port_y(y, i, etype)
        mod = mods.get(i, "")
        if mod == "NOT":
            # Tracinho ate o bubble; bubble centrado em (x - 2)
            out.append(
                f'<line class="port-pin" x1="{x - side_w}" y1="{py}" '
                f'x2="{x - 4}" y2="{py}"/>'
                f'<circle class="port-bubble" cx="{x - 2}" cy="{py}" r="2"/>'
            )
        else:
            out.append(
                f'<line class="port-pin" x1="{x - side_w}" y1="{py}" '
                f'x2="{x}" y2="{py}"/>'
            )
            if mod == "RTRIG":
                # Seta DENTRO do bloco (posicao 1), logo a direita da borda
                # de entrada. O nome do input fica na posicao 2 (port-comment
                # do lado de fora).
                out.append(
                    f'<text class="port-edge" x="{x + 2}" y="{py + 0.5}">↑</text>'
                )
            elif mod == "FTRIG":
                out.append(
                    f'<text class="port-edge" x="{x + 2}" y="{py + 0.5}">↓</text>'
                )
    return "".join(out)


def _output_pins_svg(x_right: int, y: int, n: int, mods: dict[int, str],
                     etype: str, side_w: int = 4) -> str:
    out = []
    for i in range(n):
        py = _port_y(y, i, etype)
        mod = mods.get(i, "")
        if mod == "NOT":
            out.append(
                f'<circle class="port-bubble" cx="{x_right + 2}" cy="{py}" r="2"/>'
                f'<line class="port-pin" x1="{x_right + 4}" y1="{py}" '
                f'x2="{x_right + side_w}" y2="{py}"/>'
            )
        else:
            out.append(
                f'<line class="port-pin" x1="{x_right}" y1="{py}" '
                f'x2="{x_right + side_w}" y2="{py}"/>'
            )
    return "".join(out)


def render_gate(info: dict, n_in: int, n_out: int, label: str,
                css_class: str = "element-gate",
                sublabels: list[str] | None = None,
                output_bit: str | None = None,
                connector: str | None = None) -> str:
    """
    Desenha gate generico:
      - rect com altura proporcional a max(n_in, n_out), cantos retos
      - pinos na esquerda (inputs) e direita (outputs), com bubble (NOT)
        ou seta (RTRIG/FTRIG) conforme `port_operator` do GLE
      - label central + sublabels opcionais (para PCNDTIMER, PLT, AST)
    """
    w, h = compute_size(info, n_in, n_out)
    x, y = info["left"], info["top"]
    rect = (
        f'<rect class="{css_class}" x="{x}" y="{y}" '
        f'width="{w}" height="{h}"/>'
    )
    etype = info["type"]
    pins_in = _input_pins_svg(x, y, n_in, info.get("input_mods", {}), etype)
    pins_out = _output_pins_svg(x + w, y, n_out, info.get("output_mods", {}), etype)
    # Label central (sublabels desviam pra cima)
    if sublabels:
        label_y = y + 10
    else:
        label_y = y + h / 2
    main_label = (
        f'<text class="gate-label" x="{x + w / 2}" y="{label_y}">'
        f'{html.escape(label)}</text>'
    )
    # Sublabels (uma por porta de entrada). Posicionados na "posicao 2",
    # apos o slot reservado pra seta RTRIG/FTRIG (posicao 1 em x+2), para
    # que nao haja sobreposicao quando a porta tem edge trigger. Mantido
    # uniforme em TODOS os blocos para alinhamento visual consistente.
    sub_svg = ""
    if sublabels:
        for i, sub in enumerate(sublabels[:n_in]):
            py = _port_y(y, i, etype)
            sub_svg += (
                f'<text class="port-sublabel" x="{x + 8}" y="{py + 0.5}">'
                f'{html.escape(sub)}</text>'
            )
    # Comentarios por porta: ficam do MESMO lado da porta, acima da linha
    # do fio. input -> texto a ESQUERDA da caixa (right-aligned). output ->
    # texto a DIREITA da caixa (left-aligned).
    cmt_svg = ""
    for idx, txt in info.get("input_comments", {}).items():
        if idx >= n_in:
            continue
        py = _port_y(y, idx, etype)
        cmt_svg += _port_comment_svg(txt, x, py, "left")
    for idx, txt in info.get("output_comments", {}).items():
        if idx >= n_out:
            continue
        py = _port_y(y, idx, etype)
        cmt_svg += _port_comment_svg(txt, x + w, py, "right")
    out_attr = (
        f' data-output-bit="{html.escape(output_bit)}"' if output_bit else ""
    )
    # A chave de pareamento da rede de conector. Duas pontas com o mesmo
    # `data-connector` sao o mesmo sinal, e nada no XML as liga alem disto.
    conn_attr = (
        f' data-connector="{html.escape(connector)}"' if connector else ""
    )
    return (
        f'<g class="gate-grp" id="el-{info["id"]}" '
        f'data-type="{html.escape(etype)}"{out_attr}{conn_attr}>'
        f'{rect}{pins_in}{pins_out}{main_label}{sub_svg}{cmt_svg}'
        f'</g>'
    )


def render_pcndtimer(info: dict, n_in: int, n_out: int) -> str:
    """
    PCNDTIMER: 3 entradas (in, pickup, dropout), 1 saida. Visual de timer.
    Nos reles SEL-4xx, o nome instancia (ex: PCT03) tem saida em <nome>Q.
    """
    raw_name = info.get("name") or ""
    label = raw_name or "TIMER"
    output_bit = (raw_name + "Q") if raw_name else None
    return render_gate(
        info, max(n_in, 3), max(n_out, 1),
        label=label, css_class="element-timer",
        sublabels=["in", "PU", "DO"],
        output_bit=output_bit,
    )


def render_plt(info: dict, n_in: int, n_out: int) -> str:
    """
    PLT: latch S/R.
    Nos reles SEL-4xx, o nome instancia (ex: _PLT04) corresponde a saida PLT04
    (sem o underscore inicial) na Relay Word.
    """
    raw_name = info.get("name") or ""
    name = raw_name.lstrip("_")
    label = name or "PLT"
    output_bit = name if name else None
    return render_gate(
        info, max(n_in, 2), max(n_out, 1),
        label=label, css_class="element-latch",
        sublabels=["S", "R"],
        output_bit=output_bit,
    )


def render_ast(info: dict, n_in: int, n_out: int) -> str:
    """AST: 3 inputs, 2 outputs. Output principal em <nome>Q."""
    raw_name = info.get("name") or ""
    label = raw_name or "AST"
    output_bit = (raw_name + "Q") if raw_name else None
    return render_gate(
        info, max(n_in, 3), max(n_out, 2),
        label=label, css_class="element-logic",
        output_bit=output_bit,
    )


def render_rtrig(info: dict, n_in: int, n_out: int) -> str:
    return render_gate(
        info, max(n_in, 1), max(n_out, 1),
        label="↑R", css_class="element-timer",
    )


def render_pcn(info: dict, n_in: int, n_out: int) -> str:
    """PCN (Counter): 3 entradas (in, preset value, reset), >=1 saidas.
    A saida principal na Relay Word eh <nome>Q (ex.: PCN03 -> PCN03Q)."""
    raw_name = info.get("name") or ""
    label = raw_name or "PCN"
    output_bit = (raw_name + "Q") if raw_name else None
    return render_gate(
        info, max(n_in, 3), max(n_out, 2),
        label=label, css_class="element-counter",
        sublabels=["in", "PV", "R"],
        output_bit=output_bit,
    )


def render_latch_7xx(info: dict, n_in: int, n_out: int) -> str:
    """SEL-7xx LATCH (SELOGIC Latch): 2 entradas (S/R), 1 saida.
    physical_instance_name '_LT##' -> bit Relay Word 'LT##' (sem Q)."""
    raw_name = info.get("name") or ""
    name = raw_name.lstrip("_")
    label = name or "LATCH"
    output_bit = name if name else None
    return render_gate(
        info, max(n_in, 2), max(n_out, 1),
        label=label, css_class="element-latch",
        sublabels=["S", "R"],
        output_bit=output_bit,
    )


def render_timer_7xx(info: dict, n_in: int, n_out: int) -> str:
    """SEL-7xx TIMER (SELOGIC Variable Timer): 3 entradas (in/PU/DO), 1 saida.
    physical_instance_name '_SV##' -> bit Relay Word 'SV##T' (saida do timer com
    delay aplicado; SV## bare e o input pre-timer)."""
    raw_name = info.get("name") or ""
    name = raw_name.lstrip("_")
    label = name or "TIMER"
    output_bit = (name + "T") if name else None
    return render_gate(
        info, max(n_in, 3), max(n_out, 1),
        label=label, css_class="element-timer",
        sublabels=["in", "PU", "DO"],
        output_bit=output_bit,
    )


def render_counter_7xx(info: dict, n_in: int, n_out: int) -> str:
    """SEL-7xx COUNTER (SELOGIC Counter): 3 entradas (in/PV/R), 1+ saidas.
    physical_instance_name '_SC##' -> bit Relay Word 'SC##QU' (counter atingiu
    o preset, count-up). SC##QD eh a saida count-down (nao usada como default)."""
    raw_name = info.get("name") or ""
    name = raw_name.lstrip("_")
    label = name or "COUNTER"
    output_bit = (name + "QU") if name else None
    return render_gate(
        info, max(n_in, 3), max(n_out, 1),
        label=label, css_class="element-counter",
        sublabels=["in", "PV", "R"],
        output_bit=output_bit,
    )


def render_alt(info: dict, n_in: int, n_out: int) -> str:
    """ALT (Auxiliary Latch SEL-4xx): S/R latch, 2 entradas (Set, Reset),
    1 saida. Convencao SEL: physical_instance_name vem como `_ALT01` no GLE
    e a saida na Relay Word eh `ALT01` (sem underscore, sem Q). Mesma forma
    visual e mesma logica de bit usadas em render_plt."""
    raw_name = info.get("name") or ""
    name = raw_name.lstrip("_")
    label = name or "ALT"
    output_bit = name if name else None
    return render_gate(
        info, max(n_in, 2), max(n_out, 1),
        label=label, css_class="element-latch",
        sublabels=["S", "R"],
        output_bit=output_bit,
    )


def render_element(info: dict, n_in: int, n_out: int,
                   symbol_width: int | None = None,
                   analog_group_key: str | None = None) -> str:
    t = info["type"]
    if t == "SYMBOL":
        return render_symbol(info, width=symbol_width,
                             analog_group_key=analog_group_key)
    if t == "AND":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "&", "element-logic")
    if t == "OR":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "≥1", "element-logic")
    if t == "NOT":
        return render_gate(info, max(n_in, 1), max(n_out, 1), "¬", "element-logic")
    if t == "PCNDTIMER":
        return render_pcndtimer(info, n_in, n_out)
    if t == "RTRIG":
        return render_rtrig(info, n_in, n_out)
    if t == "PLT":
        return render_plt(info, n_in, n_out)
    if t == "PCN":
        return render_pcn(info, n_in, n_out)
    if t == "ALT":
        return render_alt(info, n_in, n_out)
    if t == "EQ":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "=", "element-logic")
    if t == "NE":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "≠", "element-logic")
    if t == "LT":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "<", "element-logic")
    if t == "LE":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "≤", "element-logic")
    if t == "GT":
        return render_gate(info, max(n_in, 2), max(n_out, 1), ">", "element-logic")
    if t == "GE":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "≥", "element-logic")
    if t == "AST":
        return render_ast(info, n_in, n_out)
    if t == "MULT":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "×", "element-logic")
    if t == "ADD":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "+", "element-logic")
    if t == "SUB":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "−", "element-logic")
    if t == "DIV":
        return render_gate(info, max(n_in, 2), max(n_out, 1), "÷", "element-logic")
    if t == "LATCH":
        return render_latch_7xx(info, n_in, n_out)
    if t == "TIMER":
        return render_timer_7xx(info, n_in, n_out)
    if t == "COUNTER":
        return render_counter_7xx(info, n_in, n_out)
    if t == "Connector":
        # O nome no lugar do `→`: e' o que deixa uma pessoa ver que as duas
        # pontas sao a mesma rede, e o que da ao avaliador a chave pra ligar.
        label = info.get("label") or ""
        return render_gate(info, max(n_in, 1), max(n_out, 1),
                           label or "→", "element-gate",
                           connector=label or None)
    if t in ("Block", "Text"):
        return ""
    return ""


def render_connection(conn: ET.Element, sink_mods: dict | None = None,
                       source_mods: dict | None = None) -> str:
    pts = conn.findall(".//point")
    if len(pts) < 2:
        return ""
    coords = " ".join(f"{pt.get('x')},{pt.get('y')}" for pt in pts)
    src = conn.find("source_port")
    dst = conn.find("sink_port")
    src_id = src.get("element_id") if src is not None else ""
    dst_id = dst.get("element_id") if dst is not None else ""
    sp = src.get("port_number", "0") if src is not None else "0"
    dp = dst.get("port_number", "0") if dst is not None else "0"
    # Modificadores das portas (NOT/RTRIG/FTRIG) -- pre-calculados pelo caller.
    sink_mod = ""
    if sink_mods and dst_id:
        sink_mod = sink_mods.get((dst_id, int(dp)), "")
    src_mod = ""
    if source_mods and src_id:
        src_mod = source_mods.get((src_id, int(sp)), "")
    return (
        f'<polyline class="connection" '
        f'data-src="{src_id}" data-src-port="{sp}" '
        f'data-dst="{dst_id}" data-dst-port="{dp}" '
        f'data-sink-mod="{sink_mod}" '
        f'data-src-mod="{src_mod}" '
        f'points="{coords}"/>'
    )


def render_page(page: ET.Element, title_prefix: str = "",
                relay_model=None) -> str:
    name = page.get("name", "")
    in_counts, out_counts = count_actual_ports(page)
    sym_widths = deduce_symbol_widths(page)
    junctions = find_junctions(page)
    max_x, max_y, min_x = page_bounds(page, in_counts, out_counts, sym_widths)
    # Expande o canvas se algum frame de <group> extrapolar (paddings).
    for grp in page.findall(".//group"):
        b = group_bounds(grp, in_counts, out_counts, sym_widths)
        if b is None:
            continue
        gx0, gy0, gx1, gy1 = b
        min_x = min(min_x, gx0 - GROUP_PAD_LEFT)
        max_x = max(max_x, gx1 + GROUP_PAD_RIGHT + 10)
        max_y = max(max_y, gy1 + GROUP_PAD_BOTTOM + 10)
    vb_w = max_x - min_x
    vb_h = max_y
    title = html.escape(f"{title_prefix}{name}".strip())

    # Modificadores por porta: (element_id, port_number) -> "NOT|RTRIG|FTRIG"
    sink_mods: dict[tuple[str, int], str] = {}
    source_mods: dict[tuple[str, int], str] = {}
    for el in page.findall(".//element"):
        info = element_info(el)
        for idx, mod in info["input_mods"].items():
            sink_mods[(info["id"], idx)] = mod
        for idx, mod in info["output_mods"].items():
            source_mods[(info["id"], idx)] = mod

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x} 0 {vb_w} {vb_h}" '
        f'preserveAspectRatio="xMinYMin meet" class="gle-page" data-page-name="{html.escape(name)}">',
        f'<style>{CSS}</style>',
        f'<rect class="bg" x="{min_x}" y="0" width="{vb_w}" height="{vb_h}"/>',
        f'<title>{title}</title>',
    ]
    # Frames dos <group> -- desenhados ANTES das conexoes/elementos para
    # ficarem por tras. Nao alteram a posicao dos blocos; so envolvem-os.
    for grp in page.findall(".//group"):
        s = render_group(grp, in_counts, out_counts, sym_widths)
        if s:
            parts.append(s)
    # Conexoes primeiro (vao por tras)
    for conn in page.findall(".//connection"):
        s = render_connection(conn, sink_mods=sink_mods, source_mods=source_mods)
        if s:
            parts.append(s)
    # Dots nas juncoes (sobrepoem as conexoes)
    for jx, jy in junctions:
        parts.append(
            f'<circle class="junction-dot" '
            f'data-jx="{jx}" data-jy="{jy}" '
            f'cx="{jx}" cy="{jy}" r="1.6"/>'
        )
    # Elementos
    for el in page.findall(".//element"):
        info = element_info(el)
        defaults = DEFAULT_PORTS.get(info["type"], (1, 1))
        n_in = in_counts.get(info["id"], defaults[0])
        n_out = out_counts.get(info["id"], defaults[1])
        sw = sym_widths.get(info["id"]) if info["type"] == "SYMBOL" else None
        # Marca SYMBOLs analogicos (AMV/PMV/MV/MAG/...) pra render_symbol
        # desenhar com aparencia distinta + placeholder de valor.
        agk = None
        if info["type"] == "SYMBOL" and relay_model is not None:
            grp = relay_model.analog_group_for(info.get("name") or "")
            agk = grp.key if grp is not None else None
        s = render_element(info, n_in, n_out, symbol_width=sw,
                           analog_group_key=agk)
        if s:
            parts.append(s)
    parts.append("</svg>")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gle_path", help="Caminho para GL1.gle (XML)")
    ap.add_argument("--page", help="Renderiza apenas esta pagina (default: todas)")
    ap.add_argument("--out", default="gle_svg", help="Diretorio de saida")
    args = ap.parse_args()

    root = parse_gle(Path(args.gle_path))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    pages = root.findall(".//page")
    rendered = 0
    for p in pages:
        name = p.get("name", "")
        if args.page and name != args.page:
            continue
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name) or f"page_{rendered}"
        svg = render_page(p)
        (out_dir / f"{safe}.svg").write_text(svg, encoding="utf-8")
        rendered += 1
    print(f"{rendered} pagina(s) salvas em {out_dir}/")


if __name__ == "__main__":
    main()
