#!/usr/bin/env python3
"""
Seeder du schéma e-commerce (Supabase / PostgreSQL).

Génère des données fictives COHÉRENTES pour les 18 tables, dans l'ordre des
clés étrangères :
  - le prix figé d'une ligne de commande = prix en vigueur à la date de commande
    (lu dans l'historique des prix) ;
  - variantes.variante_price = dernier prix de price_historique ;
  - les commandes sont postérieures à la création du compte et du produit ;
  - les commandes invitées n'ont pas de commande_user_id.

Usage :
    export DATABASE_URL="postgresql://postgres:MOT_DE_PASSE@db.<ref>.supabase.co:5432/postgres"
    python scripts/seed.py --users 60 --products 80 --seed 42
    python scripts/seed.py --reset --yes          # vide les 18 tables puis re-peuple

Dépendances : pip install "psycopg[binary]" faker
Le seeder refuse de tourner si une table contient déjà des lignes (sauf --reset).
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import os
import random
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import psycopg
from faker import Faker

TABLES_FK_ORDER = [
    "currency", "marques", "categorie", "users", "adress", "product", "category_pivot",
    "variantes", "price_historique", "product_pivot", "panier", "panier_items",
    "commandes", "commandes_items", "user_guest", "commandes_userguest",
    "promotion", "reduction",
]
# Tables à identifiant auto-incrémenté (nom de colonne PK)
IDENTITY_PK = {
    "currency": "currency_id", "marques": "marque_id", "categorie": "category_id",
    "users": "user_id", "adress": "adress_id", "product": "product_id",
    "variantes": "variante_id", "price_historique": "price_id", "panier": "panier_id",
    "panier_items": "panier_items_id", "commandes": "commande_id",
    "commandes_items": "commande_items_id", "user_guest": "user_guest_id",
    "commandes_userguest": "id", "promotion": "promotion_id", "reduction": "reduction_id",
}

CATALOGUE = {
    # catégorie parente -> {sous-catégorie: (types de produits, fourchette de prix, déclinaison)}
    "Vêtements": {
        "T-shirts": (["T-shirt", "Polo", "Débardeur"], (12, 45), "taille"),
        "Pantalons": (["Jean", "Chino", "Jogging", "Pantalon cargo"], (35, 110), "taille"),
        "Vestes": (["Veste", "Blouson", "Manteau", "Doudoune"], (60, 280), "taille"),
    },
    "Chaussures": {
        "Baskets": (["Baskets", "Sneakers", "Baskets montantes"], (49, 160), "pointure"),
        "Bottes": (["Bottines", "Boots", "Chelsea boots"], (70, 220), "pointure"),
    },
    "Accessoires": {
        "Sacs": (["Sac à dos", "Sac bandoulière", "Tote bag"], (25, 140), "unique"),
        "Montres": (["Montre", "Montre connectée"], (45, 320), "unique"),
    },
    "Sport": {
        "Running": (["Short de running", "Legging", "Maillot technique"], (18, 70), "taille"),
        "Fitness": (["Tapis de yoga", "Haltères", "Bande élastique"], (9, 85), "unique"),
    },
    "Maison": {
        "Décoration": (["Coussin", "Plaid", "Vase", "Bougie"], (8, 60), "unique"),
        "Cuisine": (["Poêle", "Casserole", "Mug", "Théière"], (10, 95), "unique"),
    },
}
BRANDS = [
    "Nordik", "Altavia", "Maison Lumen", "Kestrel", "Océane", "Brisa", "Fjord & Co",
    "Atelier Vega", "Rivage", "Solène", "Piquet", "Orion Wear", "Haussmann", "Zéphyr",
    "Mistral", "Cobalt", "Ardoise", "Lunaris",
]
ADJECTIVES = [
    "Classique", "Urbain", "Essentiel", "Premium", "Léger", "Confort", "Signature",
    "Heritage", "Pro", "Eco", "Studio", "Active", "Nomade", "Origine", "Édition limitée",
]
COLORS = ["Noir", "Blanc", "Bleu marine", "Gris", "Beige", "Vert", "Bordeaux", "Camel", "Rouge", "Bleu ciel"]
SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
SHOE_SIZES = [str(n) for n in range(37, 47)]
ORDER_STATUS_WEIGHTS = [("en_attente", 6), ("payee", 14), ("expediee", 14),
                        ("livree", 55), ("annulee", 7), ("remboursee", 4)]


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fake_hash(rng: random.Random, plain: str) -> str:
    """Hash PBKDF2 factice (jamais de mot de passe en clair)."""
    salt = rng.getrandbits(64).to_bytes(8, "big")
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, 10_000)
    return f"pbkdf2_sha256$10000${salt.hex()}${digest.hex()}"


def rand_dt(rng: random.Random, start: datetime, end: datetime) -> datetime:
    span = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, max(span, 0)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="chaîne de connexion (ou DATABASE_URL)")
    ap.add_argument("--seed", type=int, default=42, help="graine aléatoire (reproductible)")
    ap.add_argument("--users", type=int, default=60)
    ap.add_argument("--guests", type=int, default=12)
    ap.add_argument("--products", type=int, default=80)
    ap.add_argument("--reset", action="store_true", help="vide les 18 tables avant de peupler")
    ap.add_argument("--yes", action="store_true", help="confirme --reset sans question")
    args = ap.parse_args()
    if not args.dsn:
        ap.error("fournir --dsn ou la variable DATABASE_URL")

    rng = random.Random(args.seed)
    fake = Faker("fr_FR")
    Faker.seed(args.seed)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    with psycopg.connect(args.dsn) as conn:
        cur = conn.cursor()
        if args.reset:
            if not args.yes:
                if input("Vider les 18 tables (TRUNCATE ... CASCADE) ? tape OUI : ") != "OUI":
                    print("Annulé.")
                    return 1
            cur.execute("truncate " + ", ".join(f"public.{t}" for t in TABLES_FK_ORDER)
                        + " restart identity cascade")
        else:
            for t in TABLES_FK_ORDER:
                cur.execute(f"select exists (select 1 from public.{t})")
                if cur.fetchone()[0]:
                    print(f"public.{t} contient déjà des lignes. Utilise --reset pour repartir de zéro.")
                    return 1

        # ---------------- currency ----------------
        currencies = [(1, "EUR"), (2, "USD"), (3, "GBP")]
        cur.executemany("insert into public.currency (currency_id, currency_name) values (%s, %s)", currencies)
        EUR = 1

        # ---------------- marques ----------------
        brands = [(i, name, f"MQ-{i:03d}") for i, name in enumerate(BRANDS, 1)]
        cur.executemany("insert into public.marques (marque_id, marque_name, marque_ref) values (%s,%s,%s)", brands)

        # ---------------- categorie (arbre parent -> enfants) ----------------
        cat_rows, leaf = [], {}          # leaf: category_id -> (types, prix, déclinaison, parent_id, nom)
        parents, cid = {}, 0
        cat_created = now - timedelta(days=800)
        for parent_name in CATALOGUE:
            cid += 1
            parents[parent_name] = cid
            cat_rows.append((cid, parent_name, None, None, cat_created, None))
        for parent_name, children in CATALOGUE.items():
            for child_name, spec in children.items():
                cid += 1
                cat_rows.append((cid, child_name, parent_name, None, cat_created, parents[parent_name]))
                leaf[cid] = (*spec, parents[parent_name], child_name)
        cur.executemany(
            "insert into public.categorie (category_id, category_name, category_parent_name, "
            "category_child_name, category_created_at, category_parent_id) values (%s,%s,%s,%s,%s,%s)", cat_rows)

        # ---------------- users ----------------
        users, used_pseudo, used_email = [], set(), set()
        for uid in range(1, args.users + 1):
            first, last = fake.first_name(), fake.last_name()
            while True:
                pseudo = f"{slug(first)}{rng.randint(1, 999)}"[:50]
                email = f"{slug(first)}.{slug(last)}{rng.randint(1, 99)}@{rng.choice(['gmail.com', 'outlook.fr', 'yahoo.fr', 'free.fr', 'orange.fr'])}"
                if pseudo not in used_pseudo and email not in used_email:
                    break
            used_pseudo.add(pseudo); used_email.add(email)
            created = rand_dt(rng, now - timedelta(days=720), now - timedelta(days=10))
            phone = fake.phone_number() if rng.random() < 0.8 else None
            status = rng.random() < 0.92
            users.append((uid, pseudo, email, fake_hash(rng, "demo-" + pseudo), phone, status, created))
        cur.executemany(
            "insert into public.users (user_id, user_pseudo, user_email, user_password, user_phone, "
            "user_account_status, user_create_at) values (%s,%s,%s,%s,%s,%s,%s)", users)
        user_created = {u[0]: u[6] for u in users}

        # ---------------- adress ----------------
        adresses, user_adresses, aid = [], {}, 0
        for uid, *_ in users:
            types = ["domicile"] + rng.sample(["travail", "autre"], k=rng.choices([0, 1, 2], [55, 35, 10])[0])
            for t in types:
                aid += 1
                street = fake.street_address().replace("\n", " ")
                adresses.append((aid, fake.postcode(), f"{street}, {fake.city()}", t,
                                 "France" if rng.random() < 0.93 else rng.choice(["Belgique", "Suisse", "Luxembourg"]), uid))
                user_adresses.setdefault(uid, []).append(aid)
        cur.executemany(
            "insert into public.adress (adress_id, adress_cp, adress_name, adress_type, adress_contry, adress_user_id) "
            "values (%s,%s,%s,%s,%s,%s)", adresses)
        adress_by_id = {a[0]: a for a in adresses}

        # ---------------- product / category_pivot ----------------
        catalog_start, catalog_end = now - timedelta(days=700), now - timedelta(days=420)
        products, product_leaf, used_names = [], {}, set()
        leaf_ids = list(leaf)
        for pid in range(1, args.products + 1):
            for _ in range(200):
                cat_id = rng.choice(leaf_ids)
                types, price_range, decl, parent_id, cat_name = leaf[cat_id]
                brand_id, brand_name = rng.choice(brands)[:2]
                name = f"{rng.choice(types)} {rng.choice(ADJECTIVES)} {brand_name}"
                if name not in used_names:
                    break
            else:
                name = f"{name} #{pid}"
            used_names.add(name)
            created = rand_dt(rng, catalog_start, catalog_end)
            desc = f"{name} : {fake.sentence(nb_words=14)} {fake.sentence(nb_words=10)}"
            # product_categorie (libellé legacy) est rempli par le trigger depuis product_category_id
            products.append((pid, name, f"REF-{pid:05d}", desc, None, created, brand_id, cat_id))
            product_leaf[pid] = cat_id
        cur.executemany(
            "insert into public.product (product_id, product_name, product_ref, product_desc, "
            "product_categorie, product_create_at, product_marque_id, product_category_id) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s)", products)
        # Le trigger a déjà inséré (produit, catégorie principale) dans category_pivot.
        # On ajoute la catégorie parente + parfois une catégorie liée.
        pivot_extra = set()
        for pid, cat_id in product_leaf.items():
            pivot_extra.add((pid, leaf[cat_id][3]))
            if rng.random() < 0.25:
                other = rng.choice(leaf_ids)
                pivot_extra.add((pid, other))
        cur.executemany("insert into public.category_pivot (product_id, category_id) values (%s,%s) "
                        "on conflict do nothing", sorted(pivot_extra))
        product_created = {p[0]: p[5] for p in products}

        # ---------------- variantes + price_historique ----------------
        variantes, prices, var_history = [], [], {}
        vid = pid_price = 0
        for pid, cat_id in product_leaf.items():
            types, (pmin, pmax), decl, *_ = leaf[cat_id]
            base = round(rng.uniform(pmin, pmax))
            if decl == "taille":
                colors = rng.sample(COLORS, k=rng.randint(1, 3))
                sizes = sorted(rng.sample(SIZES, k=rng.randint(3, 5)), key=SIZES.index)
                options = [(s, c) for s in sizes for c in colors]
            elif decl == "pointure":
                colors = rng.sample(COLORS, k=rng.randint(1, 2))
                sizes = sorted(rng.sample(SHOE_SIZES, k=rng.randint(4, 6)), key=int)
                options = [(s, c) for s in sizes for c in colors]
            else:
                options = [(None, c) for c in rng.sample(COLORS, k=rng.randint(1, 3))]
            for size, color in options:
                vid += 1
                v_created = product_created[pid] + timedelta(hours=rng.randint(1, 72))
                # marche aléatoire de prix : 1 à 5 relevés, finissant en prix courant
                n_points = rng.choices([1, 2, 3, 4, 5], [25, 30, 25, 12, 8])[0]
                amount = float(base) + rng.choice([-2, 0, 0, 3])
                times = sorted({v_created} | {rand_dt(rng, v_created, now - timedelta(days=3)) for _ in range(n_points - 1)})
                history = []
                for t in times:
                    history.append((t, round(max(1.0, amount) - 0.01, 2)))   # prix psychologiques x9,99
                    amount *= rng.uniform(0.85, 1.12)
                    amount = round(amount)
                var_history[vid] = history
                variantes.append((vid, size, color, history[-1][1], v_created, pid))
                for t, amt in history:
                    pid_price += 1
                    prices.append((pid_price, amt, t, EUR, vid))
        cur.executemany(
            "insert into public.variantes (variante_id, variante_size, variante_color, variante_price, "
            "variante_create_at, variante_product_id) values (%s,%s,%s,%s,%s,%s)", variantes)
        cur.executemany(
            "insert into public.price_historique (price_id, price_amount, create_at, currency_id, price_variante_id) "
            "values (%s,%s,%s,%s,%s)", prices)
        variante_ids = [v[0] for v in variantes]
        variante_created = {v[0]: v[4] for v in variantes}

        def price_at(variante_id: int, when: datetime) -> float:
            hist = var_history[variante_id]
            idx = bisect.bisect_right([t for t, _ in hist], when) - 1
            return hist[max(idx, 0)][1]

        # ---------------- product_pivot (favoris) ----------------
        fav = []
        for uid, *_ in users:
            for pid in rng.sample(list(product_leaf), k=rng.choices([0, 1, 3, 5, 8], [20, 25, 30, 15, 10])[0]):
                fav.append((pid, uid, rand_dt(rng, max(user_created[uid], product_created[pid]), now)))
        cur.executemany("insert into public.product_pivot (product_id, user_id, pivot_created_at) values (%s,%s,%s)", fav)

        # ---------------- panier / panier_items ----------------
        paniers, panier_items, pnid = [], [], 0
        cart_users = [u[0] for u in users if rng.random() < 0.6]
        for i, uid in enumerate(cart_users, 1):
            paniers.append((i, uid))
            for vid_ in rng.sample(variante_ids, k=rng.randint(1, 5)):
                pnid += 1
                panier_items.append((pnid, i, vid_, rng.choices([1, 2, 3], [75, 20, 5])[0]))
        cur.executemany("insert into public.panier (panier_id, panier_user_id) values (%s,%s)", paniers)
        cur.executemany("insert into public.panier_items (panier_items_id, panier_id, panier_items_variante_id, "
                        "panier_items_quantity) values (%s,%s,%s,%s)", panier_items)

        # ---------------- commandes / commandes_items ----------------
        orders, items = [], []
        order_start_floor = now - timedelta(days=365)
        cmd_id = item_id = 0
        statuses, weights = zip(*ORDER_STATUS_WEIGHTS)

        def make_order(user_id, when, adress_txt, adress_id):
            nonlocal cmd_id, item_id
            cmd_id += 1
            status = rng.choices(statuses, weights)[0]
            if (now - when).days < 5 and status in ("livree", "remboursee"):
                status = rng.choice(["payee", "expediee"])
            ref = f"CMD-{when:%Y%m%d}-{cmd_id:06d}"
            orders.append((cmd_id, user_id, ref, when, status, adress_txt, adress_id))
            for vid_ in rng.sample(variante_ids, k=rng.choices([1, 2, 3, 4], [45, 30, 17, 8])[0]):
                item_id += 1
                items.append((item_id, cmd_id, vid_, price_at(vid_, when), rng.choices([1, 2, 3], [78, 17, 5])[0]))
            return cmd_id

        for uid, *_ in users:
            for _ in range(rng.choices([0, 1, 2, 3, 5], [28, 30, 22, 14, 6])[0]):
                when = rand_dt(rng, max(user_created[uid], order_start_floor), now - timedelta(hours=2))
                a_id = rng.choice(user_adresses[uid])
                a = adress_by_id[a_id]
                make_order(uid, when, f"{a[2]}, {a[1]}, {a[4]}", a_id)

        guests, link = [], []
        for gid in range(1, args.guests + 1):
            first_cmd = None
            for _ in range(rng.choices([1, 2], [75, 25])[0]):
                when = rand_dt(rng, order_start_floor, now - timedelta(hours=2))
                street = fake.street_address().replace("\n", " ")
                c = make_order(None, when, f"{street}, {fake.postcode()} {fake.city()}, France", None)
                link.append((c, gid))
                first_cmd = first_cmd or c
            guests.append((gid, fake.phone_number(), first_cmd))
        cur.executemany(
            "insert into public.commandes (commande_id, commande_user_id, commande_ref, commande_date, "
            "commande_status, commande_adress, commande_adress_id) values (%s,%s,%s,%s,%s,%s,%s)", orders)
        cur.executemany(
            "insert into public.commandes_items (commande_items_id, commande_id, commande_variante_id, "
            "commande_unique_price, quantity) values (%s,%s,%s,%s,%s)", items)

        # ---------------- user_guest / commandes_userguest ----------------
        cur.executemany("insert into public.user_guest (user_guest_id, user_guest_number, commande_id) values (%s,%s,%s)", guests)
        cur.executemany("insert into public.commandes_userguest (id, commande_id, user_guest_id) "
                        "values (%s,%s,%s)", [(i, c, g) for i, (c, g) in enumerate(link, 1)])

        # ---------------- promotion / reduction ----------------
        promos, reductions, rid = [], [], 0
        for prid in range(1, 11):
            created = rand_dt(rng, now - timedelta(days=120), now - timedelta(days=20))
            expire = created + timedelta(days=rng.choice([14, 30, 45, 90, 180]))
            kind = rng.choice(["category", "product", "both"])
            cat = rng.choice(list(leaf) + list(parents.values())) if kind in ("category", "both") else None
            prod = rng.choice(list(product_leaf)) if kind in ("product", "both") else None
            promos.append((prid, cat, prod, expire, created))
            for _ in range(rng.choices([1, 2], [85, 15])[0]):
                rid += 1
                reductions.append((rid, rng.choice([5, 10, 15, 20, 25, 30, 40, 50]), prid))
        cur.executemany("insert into public.promotion (promotion_id, promotion_category_id, promotion_product_id, "
                        "promotion_expire_date, promotion_created_date) values (%s,%s,%s,%s,%s)", promos)
        cur.executemany("insert into public.reduction (reduction_id, pourcentage, promotion_id) values (%s,%s,%s)", reductions)

        # ---------------- remise à niveau des séquences ----------------
        for table, pk in IDENTITY_PK.items():
            cur.execute(
                f"select setval(pg_get_serial_sequence('public.{table}', '{pk}'), "
                f"coalesce((select max({pk}) from public.{table}), 1), "
                f"(select count(*) > 0 from public.{table}))")

        conn.commit()

        print("Seed terminé :")
        for t in TABLES_FK_ORDER:
            cur.execute(f"select count(*) from public.{t}")
            print(f"  {t:<22}{cur.fetchone()[0]:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
