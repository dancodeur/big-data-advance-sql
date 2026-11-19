#!/usr/bin/env python3
"""
Exporte les données d'une base déjà remplie (par seeder.py) en N fichiers SQL
« INSERT » à coller dans le SQL Editor de Supabase (dans l'ordre 01, 02, ...).

    python scripts/export_seed_sql.py --dsn "host=/tmp port=54331 dbname=src2 user=postgres" \
        --out sql/seed_data --parts 4 --seed 42

Prérequis : pg_dump dans le PATH. Chaque fichier est autonome (instructions complètes,
jamais coupées en deux) ; la dernière partie remet à jour les compteurs d'identifiants.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import psycopg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", default="sql/seed_data")
    ap.add_argument("--parts", type=int, default=4)
    ap.add_argument("--seed", default="42", help="uniquement pour l'en-tête des fichiers")
    ap.add_argument("--rows-per-insert", type=int, default=100)
    args = ap.parse_args()

    dump = subprocess.run(
        ["pg_dump", "--data-only", "--no-owner", "--no-privileges", f"--rows-per-insert={args.rows_per_insert}",
         "--schema=public", "--dbname", args.dsn],
        check=True, capture_output=True, text=True).stdout

    # découpe en instructions INSERT complètes (une instruction = jusqu'à la ligne finissant par ';')
    stmts, cur = [], []
    for line in dump.splitlines():
        if line.startswith("INSERT INTO") or cur:
            cur.append(line)
            if line.endswith(";"):
                stmts.append("\n".join(cur)); cur = []
    assert not cur, "instruction INSERT incomplète"

    total = sum(len(s) for s in stmts)
    parts, buf, size = [], [], 0
    for s in stmts:
        if size >= total / args.parts and len(parts) < args.parts - 1:
            parts.append(buf); buf, size = [], 0
        buf.append(s); size += len(s)
    parts.append(buf)

    with psycopg.connect(args.dsn) as conn:
        seqs = conn.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema='public' and pg_get_serial_sequence('public.'||quote_ident(table_name), column_name) is not null "
            "order by table_name").fetchall()
    setvals = [f"select setval(pg_get_serial_sequence('public.{t}','{c}'), (select max({c}) from public.{t}));"
               for t, c in seqs]

    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("seed_data_*.sql"):
        old.unlink()
    n = len(parts)
    for i, p in enumerate(parts, 1):
        tables = list(dict.fromkeys(re.match(r"INSERT INTO public\.(\w+)", s).group(1) for s in p))
        head = (f"-- Données de test — partie {i} sur {n}  (tables : {', '.join(tables)})\n"
                "-- À exécuter dans l'ordre (01, 02, ...) dans le SQL Editor Supabase, APRÈS la création des tables.\n"
                f"-- Généré par scripts/seeder.py --seed {args.seed} puis scripts/export_seed_sql.py.\n\n")
        body = "\n\n".join(p) + "\n"
        if i == n:
            body += "\n-- Remise à jour des compteurs d'identifiants\n" + "\n".join(setvals) + "\n"
        f = out / f"seed_data_{i:02d}_sur_{n:02d}.sql"
        f.write_text(head + body, encoding="utf-8")
        print(f"{f}  {f.stat().st_size // 1024} Ko  ({len(p)} instructions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
