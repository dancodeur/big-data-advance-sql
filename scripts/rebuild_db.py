#!/usr/bin/env python3
"""
Reconstruit la base e-commerce en local (ou sur n'importe quel PostgreSQL / Supabase)
à partir des migrations SQL du dépôt, puis la peuple avec le seeder si demandé.

Exemples :
    # PostgreSQL local (Docker : docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:17)
    python scripts/rebuild_db.py --dsn postgresql://postgres:postgres@localhost:5432/shop --create-db --seed

    # Repartir de zéro (supprime les 18 tables + le schéma "private" puis recrée)
    python scripts/rebuild_db.py --dsn postgresql://postgres:postgres@localhost:5432/shop --reset --seed

    # Sur Supabase (les rôles et le schéma auth existent déjà : aucun stub n'est créé)
    export DATABASE_URL="postgresql://postgres:MOT_DE_PASSE@db.<ref>.supabase.co:5432/postgres"
    python scripts/rebuild_db.py --seed

Sur un PostgreSQL "nu", le script crée automatiquement un faux environnement Supabase
(rôles anon / authenticated / service_role, schéma auth, auth.uid()) pour que la
migration — qui s'appuie dessus pour la RLS — s'applique à l'identique.

Dépendances : pip install "psycopg[binary]" faker
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
STUBS = ROOT / "scripts" / "local_supabase_stubs.sql"
SEED = ROOT / "scripts" / "seeder.py"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5432/shop"

TABLES = [
    "reduction", "promotion", "commandes_userguest", "user_guest", "commandes_items", "commandes",
    "panier_items", "panier", "product_pivot", "price_historique", "currency", "variantes",
    "category_pivot", "product", "categorie", "marques", "adress", "users",
]


def has_supabase_env(cur: psycopg.Cursor) -> bool:
    cur.execute("select to_regclass('auth.users') is not null and to_regprocedure('auth.uid()') is not null")
    return bool(cur.fetchone()[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", DEFAULT_DSN),
                    help=f"chaîne de connexion (défaut : DATABASE_URL ou {DEFAULT_DSN})")
    ap.add_argument("--create-db", action="store_true", help="crée la base cible si elle n'existe pas")
    ap.add_argument("--reset", action="store_true", help="supprime les tables du schéma avant de recréer")
    ap.add_argument("--seed", action="store_true", help="peuple la base avec scripts/seeder.py")
    ap.add_argument("--seed-args", default="", help="arguments passés au seeder, ex: \"--users 200 --seed 1\"")
    args = ap.parse_args()

    if not MIGRATIONS:
        print("Aucune migration trouvée dans supabase/migrations/", file=sys.stderr)
        return 1

    if args.create_db:
        target = conninfo_to_dict(args.dsn).get("dbname") or "postgres"
        admin_dsn = make_conninfo(args.dsn, dbname="postgres")
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            exists = admin.execute("select 1 from pg_database where datname = %s", (target,)).fetchone()
            if not exists:
                admin.execute(sql.SQL("create database {}").format(sql.Identifier(target)))
                print(f"Base « {target} » créée.")

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        cur = conn.cursor()

        if not has_supabase_env(cur):
            print("Environnement Supabase absent -> création des stubs locaux (rôles, auth.users, auth.uid()).")
            cur.execute(STUBS.read_text(encoding="utf-8"))

        if args.reset:
            print("Reset : suppression des tables et du schéma private.")
            for t in TABLES:
                cur.execute(sql.SQL("drop table if exists public.{} cascade").format(sql.Identifier(t)))
            cur.execute("drop schema if exists private cascade")

        for mig in MIGRATIONS:
            print(f"Migration : {mig.name}")
            try:
                with conn.transaction():
                    cur.execute(mig.read_text(encoding="utf-8"))
            except psycopg.errors.DuplicateTable:
                print("  -> les tables existent déjà. Relance avec --reset pour repartir de zéro.", file=sys.stderr)
                return 1

        cur.execute("select count(*) from information_schema.tables where table_schema='public' "
                    "and table_name = any(%s)", (TABLES,))
        print(f"Schéma prêt : {cur.fetchone()[0]}/{len(TABLES)} tables.")

    if args.seed:
        cmd = [sys.executable, "-I", str(SEED), "--dsn", args.dsn, "--reset", "--yes", *args.seed_args.split()]
        print("Seeding avec scripts/seeder.py")
        return subprocess.call(cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
