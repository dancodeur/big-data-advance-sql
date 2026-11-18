#!/usr/bin/env python3
"""
Génère le schéma draw.io CORRIGÉ à partir de la base réellement migrée (introspection
PostgreSQL), en le comparant au schéma d'origine (Modelisation_DB.json) pour repérer
les colonnes ajoutées (vert) et corrigées (orange).

    python scripts/generate_drawio.py --dsn "$DATABASE_URL" \
        --original docs/original/Modelisation_DB.json --out docs/Modelisation_DB_corrige.drawio

Le fichier produit est un .drawio standard (mxfile XML) ouvrable dans draw.io / diagrams.net.
"""
from __future__ import annotations

import argparse
import json
import re
from html import escape as html_escape
from xml.sax.saxutils import quoteattr

import psycopg

ROW_H, HEAD_H, TABLE_W = 22, 30, 480

# Disposition : colonnes de gauche à droite, tables empilées de haut en bas
LAYOUT = [
    ["marques", "categorie", "promotion", "reduction"],
    ["product", "category_pivot"],
    ["variantes", "price_historique", "currency", "product_pivot"],
    ["users", "adress", "panier", "panier_items"],
    ["commandes", "commandes_items", "commandes_userguest", "user_guest"],
]
COL_GAP, ROW_GAP = 140, 50

# Corrections de type / contrainte (colonnes présentes dans le schéma d'origine)
CHANGED = {
    ("users", "user_create_at"): "DataTime → timestamptz",
    ("users", "user_password"): "nullable : Supabase Auth gère les mots de passe",
    ("adress", "adress_user_id"): "String → integer + FK",
    ("adress", "adress_type"): "CHECK domicile/travail/autre",
    ("adress", "adress_contry"): "type 'Stri,g' corrigé (nom conservé)",
    ("product", "product_create_at"): "String → timestamptz",
    ("product", "product_marque_id"): "FK ajoutée",
    ("product", "product_category_id"): "FK ajoutée (catégorie principale)",
    ("product", "product_categorie"): "legacy, dérivé par trigger",
    ("categorie", "category_parent_name"): "legacy (voir category_parent_id)",
    ("categorie", "category_child_name"): "legacy",
    ("categorie", "category_created_at"): "défaut now()",
    ("variantes", "variante_price"): "type ajouté ; = dernier prix historique",
    ("variantes", "variante_create_at"): "type → timestamptz",
    ("price_historique", "price_amount"): "type ajouté numeric(12,2)",
    ("price_historique", "create_at"): "String → timestamptz",
    ("panier", "panier_user_id"): "FK ajoutée",
    ("panier_items", "panier_items_variante_id"): "FK ajoutée",
    ("panier_items", "panier_items_quantity"): "CHECK > 0",
    ("commandes", "commande_user_id"): "FK ajoutée (NULL = invité)",
    ("commandes", "commande_date"): "String → timestamptz",
    ("commandes", "commande_status"): "CHECK statuts",
    ("commandes", "commande_adress"): "texte figé (snapshot)",
    ("commandes", "commande_ref"): "UNIQUE",
    ("commandes_items", "commande_variante_id"): "FK ajoutée",
    ("commandes_items", "commande_unique_price"): "type ajouté numeric(12,2)",
    ("commandes_items", "quantity"): "CHECK > 0",
    ("user_guest", "commande_id"): "FK ajoutée (redondant avec le pivot)",
    ("promotion", "promotion_category_id"): "FK ajoutée",
    ("promotion", "promotion_product_id"): "FK ajoutée (≥ 1 cible)",
    ("reduction", "pourcentage"): "CHECK 0 < x ≤ 100",
    ("marques", "marque_name"): "UNIQUE",
}
ADDED_NOTES = {
    ("users", "auth_user_id"): "lien Supabase Auth (RLS)",
    ("categorie", "category_parent_id"): "hiérarchie réelle (auto-FK)",
    ("commandes", "commande_adress_id"): "lien optionnel vers adress",
    ("product_pivot", "pivot_created_at"): "date d'ajout aux favoris",
}
LEGEND_LINES = [
    "<b>Schéma corrigé — aucune table ni colonne renommée</b>",
    "<font color='#82b366'>■</font> colonne ajoutée &nbsp; <font color='#d79b00'>■</font> colonne corrigée (type / FK / contrainte) &nbsp; □ inchangée",
    "<b>PK</b> clé primaire · <b>FK</b> clé étrangère · <b>UQ</b> unique · <b>?</b> nullable",
    "<br><b>Corrections transverses</b>",
    "• Dates stockées en String / « DataTime » → <i>timestamptz</i> (défaut now())",
    "• Colonnes sans type (prix) → <i>numeric(12,2)</i>, quantités → integer CHECK &gt; 0",
    "• FK manquantes ajoutées (panier, commandes, items, promotion, marque, catégorie…)",
    "• Pivots (Product_Pivot, Category_Pivot) : clé primaire composite + ON DELETE CASCADE",
    "• Arête orpheline « One to Many et Many to One » : relie MARQUES → PRODUCT",
    "• Product_Pivot interprété comme <b>favoris</b> (utilisateur ↔ produit)",
    "• Typos « adress_contry », « Stri,g », « DataTime » : noms de colonnes conservés",
    "<br><b>Cohérence automatique (triggers)</b>",
    "• variantes.variante_price = dernier relevé de price_historique",
    "• product_category_id → alimente category_pivot et product_categorie",
    "<br><b>Sécurité (RLS Supabase)</b>",
    "• Catalogue : lecture publique · données perso : propriétaire seul",
    "• user_guest / commandes_userguest : service_role uniquement",
]
CARD = {  # style d'arête selon nullabilité de la FK
    True: "ERmandOne",
    False: "ERzeroToOne",
}


def load_original(path: str):
    """tables d'origine -> (nom d'affichage, ensemble de colonnes)"""
    data = json.load(open(path, encoding="utf-8"))
    out = {}
    for cell in data["pages"][0]["cells"]:
        label = (cell.get("label") or "").replace("\xa0", " ")
        if cell.get("type") != "node" or "\n" not in label or not label.split("\n")[1:]:
            continue
        lines = [ln.strip() for ln in label.split("\n")]
        cols = {m.group(1) for ln in lines if (m := re.match(r"^\*\s*(\w+)", ln))}
        if cols:
            name = lines[0].strip()
            out[name.lower()] = (name, cols)
    return out


def short_type(t: str) -> str:
    return (t.replace("character varying", "varchar").replace("timestamp with time zone", "timestamptz")
             .replace("boolean", "bool"))


def introspect(dsn: str):
    q_cols = """
      select c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
      from pg_attribute a join pg_class c on c.oid = a.attrelid
      join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relkind = 'r' and a.attnum > 0 and not a.attisdropped
      order by c.relname, a.attnum"""
    q_con = """
      select c.relname, k.contype, k.conkey, k.confkey, rn.nspname, rc.relname, k.conrelid, k.confrelid
      from pg_constraint k join pg_class c on c.oid = k.conrelid
      join pg_namespace n on n.oid = c.relnamespace
      left join pg_class rc on rc.oid = k.confrelid left join pg_namespace rn on rn.oid = rc.relnamespace
      where n.nspname = 'public' and k.contype in ('p', 'u', 'f')"""
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute(q_cols)
        tables: dict[str, list[dict]] = {}
        for rel, col, typ, notnull in cur.fetchall():
            tables.setdefault(rel, []).append(dict(name=col, type=short_type(typ), notnull=notnull,
                                                   pk=False, uq=False, fk=None))
        # nom de colonne par (oid, attnum)
        cur.execute("""select a.attrelid, a.attnum, a.attname from pg_attribute a
                       join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace
                       where n.nspname='public' and a.attnum > 0 and not a.attisdropped""")
        names = {(o, n): name for o, n, name in cur.fetchall()}
        cur.execute(q_con)
        edges = []
        for rel, ctype, conkey, confkey, rschema, rrel, conrelid, confrelid in cur.fetchall():
            cols = [names[(conrelid, k)] for k in conkey]
            for col in tables[rel]:
                if col["name"] in cols:
                    if ctype == "p":
                        col["pk"] = True
                    elif ctype == "u" and len(cols) == 1:
                        col["uq"] = True
                    elif ctype == "f":
                        col["fk"] = f"{rschema}.{rrel}"
                        if rschema == "public":
                            col["fk_col"] = names[(confrelid, confkey[cols.index(col["name"])])]
            if ctype == "f" and rschema == "public":
                edges.append((rel, cols[0], rrel, names[(confrelid, confkey[0])]))
    return tables, edges


def build(tables, edges, original) -> str:
    cells, ids = [], iter(range(1000, 100000))
    row_ids: dict[tuple[str, str], str] = {}
    pos: dict[str, tuple[int, int, int]] = {}
    x = 0
    for stack in LAYOUT:
        y = 0
        for t in stack:
            h = HEAD_H + ROW_H * len(tables[t])
            pos[t] = (x, y, h)
            y += h + ROW_GAP
        x += TABLE_W + COL_GAP
    missing = set(tables) - set(pos)
    assert not missing, f"tables absentes de LAYOUT : {missing}"

    for t, cols in tables.items():
        tx, ty, th = pos[t]
        disp, orig_cols = original.get(t, (t.upper(), set()))
        tid = f"t_{t}"
        cells.append(
            f'<mxCell id="{tid}" value={quoteattr("<b>" + html_escape(disp) + "</b>")} '
            'style="swimlane;fontStyle=0;childLayout=stackLayout;horizontal=1;startSize=30;horizontalStack=0;'
            'resizeParent=1;resizeParentMax=0;resizeLast=0;collapsible=0;marginBottom=0;html=1;'
            'fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=13;" vertex="1" parent="1">'
            f'<mxGeometry x="{tx}" y="{ty}" width="{TABLE_W}" height="{th}" as="geometry"/></mxCell>')
        for i, c in enumerate(cols):
            rid = f"r_{t}_{c['name']}"
            row_ids[(t, c["name"])] = rid
            added = c["name"] not in orig_cols
            note = ADDED_NOTES.get((t, c["name"])) if added else CHANGED.get((t, c["name"]))
            fill = "#d5e8d4" if added else ("#ffe6cc" if note else "none")
            tags = []
            if c["pk"]:
                tags.append("<font color='#b85450'><b>PK</b></font>")
            if c["fk"]:
                tags.append("<font color='#6c8ebf'><b>FK</b></font>")
            if c["uq"]:
                tags.append("<font color='#9673a6'><b>UQ</b></font>")
            tag = "&nbsp;".join(tags) or "&nbsp;&nbsp;&nbsp;"
            # une note longue passe sur 2 lignes : on garde la hauteur fixe, la note va en infobulle
            value = (f"{tag} {html_escape(c['name'])} "
                     f"<font color='#666666'>{html_escape(c['type'])}{'' if c['notnull'] else ' ?'}</font>")
            if note:
                value += f" <font style='font-size:9px' color='#999999'>· {html_escape(note)}</font>"
            cells.append(
                f'<UserObject label={quoteattr(value)} tooltip={quoteattr(note or "")} id="{rid}">'
                f'<mxCell style="text;strokeColor=none;fillColor={fill};align=left;verticalAlign=middle;'
                'spacingLeft=6;spacingRight=4;overflow=hidden;rotatable=0;html=1;fontSize=11;'
                'points=[[0,0.5],[1,0.5]];portConstraint=eastwest;whiteSpace=nowrap;" vertex="1" '
                f'parent="{tid}"><mxGeometry y="{HEAD_H + i * ROW_H}" width="{TABLE_W}" height="{ROW_H}" '
                'as="geometry"/></mxCell></UserObject>')

    nullable = {(t, c["name"]): not c["notnull"] for t, cs in tables.items() for c in cs}
    for n, (src_t, src_c, dst_t, dst_c) in enumerate(edges):
        end = CARD[not nullable[(src_t, src_c)]]
        cells.append(
            f'<mxCell id="e{n}" style="edgeStyle=entityRelationEdgeStyle;fontSize=12;html=1;'
            f'endArrow={end};startArrow=ERmany;endFill=0;startFill=0;strokeColor=#555555;rounded=0;" '
            f'edge="1" parent="1" source="{row_ids[(src_t, src_c)]}" target="{row_ids[(dst_t, dst_c)]}">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>')

    lx = x
    cells.append(
        f'<mxCell id="legend" value={quoteattr("<br>".join(LEGEND_LINES))} '
        'style="text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;overflow=hidden;fontSize=12;'
        'fillColor=#fff2cc;strokeColor=#d6b656;spacing=10;rounded=1;" vertex="1" parent="1">'
        f'<mxGeometry x="{lx}" y="0" width="560" height="560" as="geometry"/></mxCell>')

    width = lx + 640
    height = max(y for _, y, _ in pos.values()) + 900
    return (
        '<mxfile host="app.diagrams.net" agent="generate_drawio.py" version="24.0.0">'
        '<diagram id="schema-corrige" name="Schéma corrigé">'
        f'<mxGraphModel dx="1600" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
        f'arrows="1" fold="1" page="1" pageScale="1" pageWidth="{width}" pageHeight="{height}" math="0" shadow="0">'
        '<root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + '</root></mxGraphModel></diagram></mxfile>')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--original", default="docs/original/Modelisation_DB.json")
    ap.add_argument("--out", default="docs/Modelisation_DB_corrige.drawio")
    a = ap.parse_args()
    tables, edges = introspect(a.dsn)
    xml = build(tables, edges, load_original(a.original))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"{a.out} : {len(tables)} tables, {len(edges)} relations")


if __name__ == "__main__":
    main()
