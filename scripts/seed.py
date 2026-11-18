#!/usr/bin/env python3
"""
Seeder du schéma e-commerce (Supabase / PostgreSQL).

Génère des données fictives COHÉRENTES pour les 18 tables, dans l'ordre des clés
étrangères, en deux couches :

1. Jeu « normal » : utilisateurs, catalogue, historique de prix, paniers, commandes,
   promotions. Le prix figé d'une ligne de commande est le prix en vigueur à la date de
   la commande ; variantes.variante_price = dernier relevé de price_historique.

2. Jeu de CAS LIMITES pour les tests (--edge-count, 10 lignes minimum par cas) :
   panier vide, utilisateur sans panier / sans adresse / sans commande / sans favoris,
   compte inactif, champs à longueur maximale, caractères spéciaux, produit sans marque /
   sans catégorie / sans variante, prix à 0 et prix maximal, historique de prix long ou à
   un seul relevé, commandes de chaque statut, commandes invitées, commandes orphelines,
   promotions actives / expirées / sans réduction, réductions de 100 % et de 0,01 %, etc.
   La liste complète s'affiche à la fin du seed et peut être exportée avec --manifest.

Avant le commit, une auto-vérification interroge la base : si un cas limite a moins de
--edge-count lignes, tout est annulé (rollback).

Usage :
    export DATABASE_URL="postgresql://postgres:MOT_DE_PASSE@db.<ref>.supabase.co:5432/postgres"
    python scripts/seed.py --users 60 --products 80 --seed 42
    python scripts/seed.py --reset --yes                 # vide les 18 tables puis re-peuple
    python scripts/seed.py --edge-count 25 --manifest cas_limites.json
    python scripts/seed.py --edge-count 0                # sans les cas limites

Dépendances : pip install "psycopg[binary]" faker
Le seeder refuse de tourner si une table contient déjà des lignes (sauf --reset).
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import itertools
import json
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
STATUSES = [s for s, _ in ORDER_STATUS_WEIGHTS]
EXTRA_CURRENCIES = ["CHF", "CAD", "JPY", "AUD", "SEK", "NOK", "DKK", "PLN", "CZK", "CNY", "NZD", "HUF"]
SPECIAL_TEXTS = [
    "Zoë Ålvarez", "O'Brien", 'Jean-Pierre "JP"', "名前テスト", "Müller ß", "Ñandú",
    "😀 smile", "a  b   c", "<script>x</script>", "Robert'); DROP TABLE users;--",
    "100% bio & vegan", "été/hiver \\ test", "Ωmega ✓", "tab\there",
]

# ---------------------------------------------------------------------------
# Cas limites : (clé, description, requête SQL renvoyant les identifiants concernés)
# La requête est exécutée en base AVANT le commit : elle sert de contrôle ET de manifeste.
# ---------------------------------------------------------------------------
SCENARIOS: list[tuple[str, str, str]] = [
    # --- utilisateurs
    ("users_panier_vide", "Utilisateur avec un panier existant mais sans aucune ligne",
     "select u.user_id from users u join panier p on p.panier_user_id = u.user_id "
     "where not exists (select 1 from panier_items i where i.panier_id = p.panier_id)"),
    ("users_sans_panier", "Utilisateur sans aucun panier",
     "select user_id from users u where not exists (select 1 from panier p where p.panier_user_id = u.user_id)"),
    ("paniers_un_article", "Panier contenant exactement 1 ligne",
     "select panier_id from panier_items group by panier_id having count(*) = 1"),
    ("paniers_12_articles", "Panier avec 10 lignes ou plus",
     "select panier_id from panier_items group by panier_id having count(*) >= 10"),
    ("paniers_quantite_max", "Ligne de panier avec quantité >= 999",
     "select panier_items_id from panier_items where panier_items_quantity >= 999"),
    ("users_sans_adresse", "Utilisateur sans aucune adresse",
     "select user_id from users u where not exists (select 1 from adress a where a.adress_user_id = u.user_id)"),
    ("users_3_adresses", "Utilisateur avec les 3 types d'adresse (domicile, travail, autre)",
     "select adress_user_id from adress group by adress_user_id having count(distinct adress_type) = 3"),
    ("adresses_champs_limites", "Adresse avec cp=50, nom=255 et pays=255 caractères",
     "select adress_id from adress where length(adress_cp) = 50 and length(adress_name) = 255 "
     "and length(adress_contry) = 255"),
    ("users_compte_inactif", "Compte désactivé (user_account_status = false)",
     "select user_id from users where not user_account_status"),
    ("users_sans_telephone", "Utilisateur sans téléphone (NULL)",
     "select user_id from users where user_phone is null"),
    ("users_champs_limites", "Pseudo de 50 caractères, email de 100, téléphone de 255",
     "select user_id from users where length(user_pseudo) = 50 and length(user_email) = 100 "
     "and length(user_phone) = 255"),
    ("users_caracteres_speciaux", "Pseudo avec accents, apostrophes, guillemets, emoji, injection SQL…",
     "select user_id from users where user_pseudo !~ '^[A-Za-z0-9_]+$'"),
    ("users_sans_commande", "Utilisateur n'ayant jamais commandé",
     "select user_id from users u where not exists (select 1 from commandes c where c.commande_user_id = u.user_id)"),
    ("users_sans_favoris", "Utilisateur sans aucun favori",
     "select user_id from users u where not exists (select 1 from product_pivot f where f.user_id = u.user_id)"),
    ("users_beaucoup_favoris", "Utilisateur avec 30 favoris ou plus",
     "select user_id from product_pivot group by user_id having count(*) >= 30"),
    ("users_beaucoup_commandes", "Utilisateur avec 10 commandes ou plus",
     "select commande_user_id from commandes where commande_user_id is not null "
     "group by commande_user_id having count(*) >= 10"),
    ("users_cree_recemment", "Compte créé dans les 3 dernières heures",
     "select user_id from users where user_create_at > now() - interval '3 hours'"),
    # --- catalogue
    ("devises_inutilisees", "Devise sans aucun relevé de prix",
     "select currency_id from currency c where not exists (select 1 from price_historique h where h.currency_id = c.currency_id)"),
    ("marques_sans_produit", "Marque sans aucun produit",
     "select marque_id from marques m where not exists (select 1 from product p where p.product_marque_id = m.marque_id)"),
    ("categories_racine_vide", "Catégorie racine sans enfant ni produit",
     "select category_id from categorie c where category_parent_id is null "
     "and not exists (select 1 from categorie e where e.category_parent_id = c.category_id) "
     "and not exists (select 1 from category_pivot p where p.category_id = c.category_id) "
     "and not exists (select 1 from product p where p.product_category_id = c.category_id)"),
    ("categories_feuilles_vides", "Sous-catégorie sans enfant ni produit",
     "select category_id from categorie c where category_parent_id is not null "
     "and not exists (select 1 from categorie e where e.category_parent_id = c.category_id) "
     "and not exists (select 1 from category_pivot p where p.category_id = c.category_id)"),
    ("categories_profondeur_3", "Catégorie de niveau 3 (petit-enfant d'une racine)",
     "select c.category_id from categorie c join categorie p on p.category_id = c.category_parent_id "
     "where p.category_parent_id is not null"),
    ("produits_sans_marque", "Produit sans marque (NULL)",
     "select product_id from product where product_marque_id is null"),
    ("produits_sans_categorie", "Produit sans catégorie principale ni ligne de pivot",
     "select product_id from product p where product_category_id is null "
     "and not exists (select 1 from category_pivot c where c.product_id = p.product_id)"),
    ("produits_sans_variante", "Produit sans aucune variante",
     "select product_id from product p where not exists (select 1 from variantes v where v.variante_product_id = p.product_id)"),
    ("produits_multi_categories", "Produit rattaché à 4 catégories ou plus",
     "select product_id from category_pivot group by product_id having count(*) >= 4"),
    ("produits_nom_255", "Nom de produit de 255 caractères",
     "select product_id from product where length(product_name) = 255"),
    ("produits_caracteres_speciaux", "Nom de produit avec guillemets, accents, emoji, HTML…",
     "select product_id from product where product_name ~ $$['\"<>&%😀«]$$"),
    ("produits_sans_description", "Produit sans description (NULL)",
     "select product_id from product where product_desc is null"),
    ("produits_description_longue", "Description de 10 000 caractères ou plus",
     "select product_id from product where length(product_desc) >= 10000"),
    ("produits_jamais_commandes", "Produit jamais commandé",
     "select product_id from product p where not exists (select 1 from variantes v join commandes_items i "
     "on i.commande_variante_id = v.variante_id where v.variante_product_id = p.product_id)"),
    ("produits_nombreuses_variantes", "Produit avec 30 variantes ou plus",
     "select variante_product_id from variantes group by variante_product_id having count(*) >= 30"),
    ("variantes_sans_taille_ni_couleur", "Variante sans taille ni couleur (NULL, NULL)",
     "select variante_id from variantes where variante_size is null and variante_color is null"),
    ("variantes_prix_zero", "Variante gratuite (prix = 0,00)",
     "select variante_id from variantes where variante_price = 0"),
    ("variantes_prix_max", "Variante au prix maximal numeric(12,2) = 9 999 999 999,99",
     "select variante_id from variantes where variante_price >= 9999999999.99"),
    ("variantes_historique_long", "Variante avec 10 relevés de prix ou plus",
     "select price_variante_id from price_historique group by price_variante_id having count(*) >= 10"),
    ("variantes_un_seul_releve", "Variante avec un seul relevé de prix",
     "select price_variante_id from price_historique group by price_variante_id having count(*) = 1"),
    # --- commandes
    *[(f"commandes_statut_{s}", f"Commande au statut « {s} »",
       f"select commande_id from commandes where commande_status = '{s}'") for s in STATUSES],
    ("commandes_aujourdhui", "Commande passée dans les 2 dernières heures",
     "select commande_id from commandes where commande_date > now() - interval '2 hours'"),
    ("commandes_sans_ligne", "Commande sans aucune ligne",
     "select commande_id from commandes c where not exists (select 1 from commandes_items i where i.commande_id = c.commande_id)"),
    ("commandes_gros_panier", "Commande avec 10 lignes ou plus",
     "select commande_id from commandes_items group by commande_id having count(*) >= 10"),
    ("commandes_quantite_elevee", "Ligne de commande avec quantité >= 500",
     "select commande_items_id from commandes_items where quantity >= 500"),
    ("commandes_total_zero", "Commande dont le total est de 0,00",
     "select commande_id from commandes_items group by commande_id "
     "having sum(commande_unique_price * quantity) = 0"),
    ("commandes_prix_modifie", "Ligne dont le prix figé diffère du prix courant de la variante",
     "select i.commande_items_id from commandes_items i join variantes v on v.variante_id = i.commande_variante_id "
     "where i.commande_unique_price <> v.variante_price"),
    ("commandes_sans_adresse_id", "Commande de membre avec adresse figée en texte mais sans lien vers adress",
     "select commande_id from commandes where commande_user_id is not null "
     "and commande_adress_id is null and commande_adress is not null"),
    ("commandes_sans_adresse", "Commande de membre sans aucune adresse (ni texte, ni lien)",
     "select commande_id from commandes where commande_user_id is not null "
     "and commande_adress_id is null and commande_adress is null"),
    ("commandes_orphelines", "Commande sans utilisateur ni invité (compte supprimé)",
     "select commande_id from commandes c where commande_user_id is null "
     "and not exists (select 1 from commandes_userguest g where g.commande_id = c.commande_id)"),
    ("commandes_invitees", "Commande passée par un invité",
     "select commande_id from commandes_userguest"),
    ("commandes_multi_invites", "Commande liée à 2 invités ou plus",
     "select commande_id from commandes_userguest group by commande_id having count(*) >= 2"),
    ("invites_multi_commandes", "Invité avec 3 commandes ou plus",
     "select user_guest_id from commandes_userguest group by user_guest_id having count(*) >= 3"),
    ("invites_sans_commande", "Invité sans aucune commande",
     "select user_guest_id from user_guest g where g.commande_id is null "
     "and not exists (select 1 from commandes_userguest x where x.user_guest_id = g.user_guest_id)"),
    # --- promotions
    ("promotions_actives", "Promotion en cours",
     "select promotion_id from promotion where promotion_expire_date > now()"),
    ("promotions_expirees", "Promotion expirée",
     "select promotion_id from promotion where promotion_expire_date <= now()"),
    ("promotions_expire_bientot", "Promotion qui expire dans moins de 2 heures",
     "select promotion_id from promotion where promotion_expire_date between now() and now() + interval '2 hours'"),
    ("promotions_categorie_seule", "Promotion ciblant uniquement une catégorie",
     "select promotion_id from promotion where promotion_category_id is not null and promotion_product_id is null"),
    ("promotions_produit_seul", "Promotion ciblant uniquement un produit",
     "select promotion_id from promotion where promotion_product_id is not null and promotion_category_id is null"),
    ("promotions_categorie_et_produit", "Promotion ciblant une catégorie ET un produit",
     "select promotion_id from promotion where promotion_category_id is not null and promotion_product_id is not null"),
    ("promotions_plusieurs_reductions", "Promotion avec 3 réductions ou plus",
     "select promotion_id from reduction group by promotion_id having count(*) >= 3"),
    ("promotions_sans_reduction", "Promotion sans aucune réduction",
     "select promotion_id from promotion p where not exists (select 1 from reduction r where r.promotion_id = p.promotion_id)"),
    ("reductions_100_pct", "Réduction de 100 %",
     "select reduction_id from reduction where pourcentage = 100"),
    ("reductions_minimales", "Réduction minimale de 0,01 %",
     "select reduction_id from reduction where pourcentage = 0.01"),
]


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


def pad(text: str, size: int, filler: str = "x") -> str:
    """Tronque / complète `text` pour obtenir exactement `size` caractères."""
    return (text + filler * size)[:size]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="chaîne de connexion (ou DATABASE_URL)")
    ap.add_argument("--seed", type=int, default=42, help="graine aléatoire (reproductible)")
    ap.add_argument("--users", type=int, default=60)
    ap.add_argument("--guests", type=int, default=12)
    ap.add_argument("--products", type=int, default=80)
    ap.add_argument("--edge-count", type=int, default=10,
                    help="lignes minimum par cas limite (défaut 10, 0 = aucun cas limite)")
    ap.add_argument("--manifest", help="écrit en JSON, pour chaque cas limite, les identifiants concernés")
    ap.add_argument("--reset", action="store_true", help="vide les 18 tables avant de peupler")
    ap.add_argument("--yes", action="store_true", help="confirme --reset sans question")
    args = ap.parse_args()
    if not args.dsn:
        ap.error("fournir --dsn ou la variable DATABASE_URL")
    E = max(args.edge_count, 0)

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

        # =====================================================================
        # currency
        # =====================================================================
        currencies = [(1, "EUR"), (2, "USD"), (3, "GBP")]
        EUR = 1
        extra = EXTRA_CURRENCIES + [f"XT{n:02d}" for n in range(1, max(E, 1) + 1)]
        currencies += [(4 + i, code) for i, code in enumerate(extra[:E])]      # devises jamais utilisées
        cur.executemany("insert into public.currency (currency_id, currency_name) values (%s, %s)", currencies)

        # =====================================================================
        # marques
        # =====================================================================
        brands = [(i, name, f"MQ-{i:03d}") for i, name in enumerate(BRANDS, 1)]     # marques « normales »
        normal_brands = list(brands)
        for i in range(1, E + 1):                                                  # marques sans produit
            brands.append((len(brands) + 1, f"EDGE Marque sans produit {i:02d}", f"MQ-EDGE-{i:03d}"))
        cur.executemany("insert into public.marques (marque_id, marque_name, marque_ref) values (%s,%s,%s)", brands)

        # =====================================================================
        # categorie (arbre parent -> enfants -> petits-enfants)
        # =====================================================================
        cat_rows, leaf, parents, cid = [], {}, {}, 0
        cat_created = now - timedelta(days=800)
        for parent_name in CATALOGUE:
            cid += 1
            parents[parent_name] = cid
            cat_rows.append((cid, parent_name, None, None, cat_created, None))
        child_names = {}
        for parent_name, children in CATALOGUE.items():
            for child_name, spec in children.items():
                cid += 1
                cat_rows.append((cid, child_name, parent_name, None, cat_created, parents[parent_name]))
                leaf[cid] = (*spec, parents[parent_name], child_name)
                child_names[cid] = child_name
        for i in range(1, E + 1):                       # racines sans enfant ni produit
            cid += 1
            cat_rows.append((cid, f"EDGE Racine vide {i:02d}", None, None, cat_created, None))
        parent_cycle = list(parents.items())
        for i in range(1, E + 1):                       # sous-catégories vides
            pname, pid_ = parent_cycle[(i - 1) % len(parent_cycle)]
            cid += 1
            cat_rows.append((cid, f"EDGE Feuille vide {i:02d}", pname, None, cat_created, pid_))
        child_cycle = list(child_names.items())
        for i in range(1, E + 1):                       # niveau 3
            c_id, c_name = child_cycle[(i - 1) % len(child_cycle)]
            cid += 1
            cat_rows.append((cid, f"EDGE Niveau 3 - {i:02d}", c_name, None, cat_created, c_id))
        cur.executemany(
            "insert into public.categorie (category_id, category_name, category_parent_name, "
            "category_child_name, category_created_at, category_parent_id) values (%s,%s,%s,%s,%s,%s)", cat_rows)
        cat_names = {r[0]: r[1] for r in cat_rows}
        leaf_ids = list(leaf)
        all_cat_ids = leaf_ids + list(parents.values())

        # =====================================================================
        # users : jeu normal puis un groupe par cas limite
        # =====================================================================
        users, used_pseudo, used_email = [], set(), set()
        meta: dict[int, str] = {}                     # user_id -> tag du cas limite (absent = utilisateur normal)
        edge_users: dict[str, list[int]] = {}
        uid_c = itertools.count(1)

        def add_user(pseudo, email, phone, status, created, tag=None):
            uid = next(uid_c)
            users.append((uid, pseudo, email, fake_hash(rng, "demo-" + pseudo[:20]), phone, status, created))
            if tag:
                meta[uid] = tag
                edge_users.setdefault(tag, []).append(uid)
            return uid

        for _ in range(args.users):
            first, last = fake.first_name(), fake.last_name()
            while True:
                pseudo = f"{slug(first)}{rng.randint(1, 999)}"[:50]
                email = (f"{slug(first)}.{slug(last)}{rng.randint(1, 99)}@"
                         f"{rng.choice(['gmail.com', 'outlook.fr', 'yahoo.fr', 'free.fr', 'orange.fr'])}")
                if pseudo not in used_pseudo and email not in used_email:
                    break
            used_pseudo.add(pseudo); used_email.add(email)
            add_user(pseudo, email, fake.phone_number() if rng.random() < 0.8 else None, rng.random() < 0.92,
                     rand_dt(rng, now - timedelta(days=720), now - timedelta(days=10)))
        normal_uids = [u[0] for u in users]

        EDGE_USER_TAGS = ["panier_vide", "sans_panier", "panier_un_article", "panier_gros", "sans_adresse",
                          "3_adresses", "compte_inactif", "sans_telephone", "champs_limites",
                          "caracteres_speciaux", "sans_commande", "sans_favoris", "beaucoup_favoris",
                          "beaucoup_commandes", "cree_recemment"]
        if E:
            for tag in EDGE_USER_TAGS:
                for i in range(1, E + 1):
                    created = rand_dt(rng, now - timedelta(days=720), now - timedelta(days=30))
                    pseudo, email = f"edge_{tag}_{i:02d}", f"edge.{tag}.{i:02d}@test.local"
                    phone, status = fake.phone_number(), True
                    if tag == "sans_telephone":
                        phone = None
                    elif tag == "compte_inactif":
                        status = False
                    elif tag == "cree_recemment":
                        created = now - timedelta(minutes=rng.randint(2, 90))
                    elif tag == "champs_limites":
                        pseudo = pad(f"limite{i:02d}_", 50, "a")
                        email = pad(f"limite{i:02d}", 100 - len("@test.local"), "a") + "@test.local"
                        phone = pad("06", 255, "0")
                    elif tag == "caracteres_speciaux":
                        base = SPECIAL_TEXTS[(i - 1) % len(SPECIAL_TEXTS)]
                        pseudo = f"{base} #{i:02d}"[:50]
                        email = f"o'brien+{i:02d}@test.local"
                    add_user(pseudo, email, phone, status, created, tag)
        cur.executemany(
            "insert into public.users (user_id, user_pseudo, user_email, user_password, user_phone, "
            "user_account_status, user_create_at) values (%s,%s,%s,%s,%s,%s,%s)", users)
        user_created = {u[0]: u[6] for u in users}

        # =====================================================================
        # adress
        # =====================================================================
        adresses, user_adresses, aid = [], {}, 0
        for uid in [u[0] for u in users]:
            tag = meta.get(uid)
            if tag == "sans_adresse":
                continue
            if tag == "3_adresses":
                types = ["domicile", "travail", "autre"]
            elif tag:
                types = ["domicile"]
            else:
                types = ["domicile"] + rng.sample(["travail", "autre"], k=rng.choices([0, 1, 2], [55, 35, 10])[0])
            for t in types:
                aid += 1
                street = fake.street_address().replace("\n", " ")
                cp, name = fake.postcode(), f"{street}, {fake.city()}"
                country = "France" if rng.random() < 0.93 else rng.choice(["Belgique", "Suisse", "Luxembourg"])
                if tag == "champs_limites":
                    cp, name, country = pad("7", 50, "7"), pad(f"{aid} rue ", 255, "x"), pad("Pays ", 255, "y")
                elif tag == "caracteres_speciaux":
                    name = f"{aid} rue de l'Éléphant — Bât. B (3ᵉ étage) 😀"
                    country = "Côte d'Ivoire"
                adresses.append((aid, cp, name, t, country, uid))
                user_adresses.setdefault(uid, []).append(aid)
        cur.executemany(
            "insert into public.adress (adress_id, adress_cp, adress_name, adress_type, adress_contry, adress_user_id) "
            "values (%s,%s,%s,%s,%s,%s)", adresses)
        adress_by_id = {a[0]: a for a in adresses}

        # =====================================================================
        # product / variantes / price_historique
        # =====================================================================
        catalog_start, catalog_end = now - timedelta(days=700), now - timedelta(days=420)
        products, product_created, product_cat = [], {}, {}
        variantes, prices, var_history = [], [], {}
        pid_c, vid_c, price_c = itertools.count(1), itertools.count(1), itertools.count(1)
        used_names: set[str] = set()
        shop_pids, edge_variants = [], {}          # shop_pids : produits « normaux »; edge_variants[tag] -> ids

        def new_product(name, desc, cat_id, brand_id, legacy=None, tag=None, ref=None):
            pid = next(pid_c)
            created = rand_dt(rng, catalog_start, catalog_end)
            if cat_id is not None:
                legacy = cat_names[cat_id]            # libellé legacy = nom de la catégorie principale
            products.append((pid, name, ref or f"REF-{pid:05d}", desc, legacy, created, brand_id, cat_id))
            product_created[pid] = created
            product_cat[pid] = cat_id
            return pid, created

        def gen_history(v_created, base, n_points):
            times = {v_created}
            while len(times) < n_points:
                times.add(rand_dt(rng, v_created, now - timedelta(days=3)))
            amount, hist = float(base) + rng.choice([-2, 0, 0, 3]), []
            for t in sorted(times):
                hist.append((t, round(max(1.0, amount) - 0.01, 2)))    # prix psychologiques x9,99
                amount = round(amount * rng.uniform(0.85, 1.12))
            return hist

        def add_variant(pid, size, color, hist, v_created, tag=None):
            vid = next(vid_c)
            var_history[vid] = hist
            variantes.append((vid, size, color, hist[-1][1], v_created, pid))
            for t, amt in hist:
                prices.append((next(price_c), amt, t, EUR, vid))
            if tag:
                edge_variants.setdefault(tag, []).append(vid)
            return vid

        def default_options(decl):
            if decl == "taille":
                colors = rng.sample(COLORS, k=rng.randint(1, 3))
                sizes = sorted(rng.sample(SIZES, k=rng.randint(3, 5)), key=SIZES.index)
                return [(s, c) for s in sizes for c in colors]
            if decl == "pointure":
                colors = rng.sample(COLORS, k=rng.randint(1, 2))
                sizes = sorted(rng.sample(SHOE_SIZES, k=rng.randint(4, 6)), key=int)
                return [(s, c) for s in sizes for c in colors]
            return [(None, c) for c in rng.sample(COLORS, k=rng.randint(1, 3))]

        def default_variants(pid, created, cat_id, tag=None, options=None, n_points=None, base=None):
            _, (pmin, pmax), decl, *_ = leaf[cat_id] if cat_id in leaf else (None, (10, 80), "unique")
            base = base or round(rng.uniform(pmin, pmax))
            for size, color in (options or default_options(decl)):
                v_created = created + timedelta(hours=rng.randint(1, 72))
                pts = n_points or rng.choices([1, 2, 3, 4, 5], [25, 30, 25, 12, 8])[0]
                add_variant(pid, size, color, gen_history(v_created, base, pts), v_created, tag)

        # --- produits « normaux »
        for _ in range(args.products):
            for _try in range(200):
                cat_id = rng.choice(leaf_ids)
                types, _pr, _decl, parent_id, cat_name = leaf[cat_id]
                brand_id, brand_name = rng.choice(normal_brands)[:2]
                name = f"{rng.choice(types)} {rng.choice(ADJECTIVES)} {brand_name}"
                if name not in used_names:
                    break
            else:
                name = f"{name} #{len(products) + 1}"
            used_names.add(name)
            desc = f"{name} : {fake.sentence(nb_words=14)} {fake.sentence(nb_words=10)}"
            pid, created = new_product(name, desc, cat_id, brand_id)
            shop_pids.append(pid)
            default_variants(pid, created, cat_id)
        shop_variantes = [v[0] for v in variantes]          # variantes vendables (jeu normal)

        # --- produits « cas limites » : un groupe de E produits par cas
        edge_products: dict[str, list[int]] = {}
        long_desc = " ".join(fake.sentence(nb_words=12) for _ in range(160))
        long_desc = (long_desc * (10_000 // len(long_desc) + 1))[:10_500]
        extra_pivot: set[tuple[int, int]] = set()

        def edge_product(tag, i, *, name=None, desc="", cat="random", brand="random", legacy=None):
            cat_id = rng.choice(leaf_ids) if cat == "random" else cat
            brand_id = rng.choice(normal_brands)[0] if brand == "random" else brand
            name = name or f"EDGE {tag} {i:02d}"
            pid, created = new_product(name, desc if desc != "" else f"Produit de test « {tag} ». {fake.sentence(nb_words=10)}",
                                       cat_id, brand_id, legacy)
            edge_products.setdefault(tag, []).append(pid)
            return pid, created, cat_id

        if E:
            for i in range(1, E + 1):
                pid, cr, c = edge_product("sans_marque", i, brand=None)
                default_variants(pid, cr, c)
                pid, cr, c = edge_product("sans_categorie", i, cat=None, legacy="Divers")
                default_variants(pid, cr, c)
                edge_product("sans_variante", i)
                pid, cr, c = edge_product("multi_categories", i)
                default_variants(pid, cr, c)
                for other in rng.sample(all_cat_ids, k=5):
                    extra_pivot.add((pid, other))
                pid, cr, c = edge_product("nom_255", i, name=pad(f"EDGE nom 255 car. {i:02d} ", 255, "x"))
                default_variants(pid, cr, c)
                special = SPECIAL_TEXTS[(i - 1) % len(SPECIAL_TEXTS)]
                pid, cr, c = edge_product(
                    "caracteres_speciaux", i, name=f"Café «Crème» d'été — {special} 😀 #{i:02d}"[:255],
                    desc=f"Ligne 1\nLigne 2\ttabulée\n'; DROP TABLE product; --\n<b>HTML</b> & \"guillemets\" 100% ✓ {special}")
                default_variants(pid, cr, c)
                pid, cr, c = edge_product("sans_description", i, desc=None)
                default_variants(pid, cr, c)
                pid, cr, c = edge_product("description_longue", i, desc=long_desc)
                default_variants(pid, cr, c)
                pid, cr, c = edge_product("sans_taille_couleur", i)
                default_variants(pid, cr, c, tag="sans_taille_ni_couleur", options=[(None, None)])
                pid, cr, c = edge_product("nombreuses_variantes", i)
                default_variants(pid, cr, c, options=[(s, col) for s in SIZES for col in COLORS[:6]])
                pid, cr, c = edge_product("historique_long", i)
                default_variants(pid, cr, c, tag="historique_long", options=default_options("taille")[:2],
                                 n_points=rng.randint(12, 18))
                pid, cr, c = edge_product("un_seul_releve", i)
                default_variants(pid, cr, c, tag="un_seul_releve", n_points=1)
                # prix zéro : relevé payant puis passage à 0,00 (avant toute commande)
                pid, cr, c = edge_product("prix_zero", i)
                v_created = cr + timedelta(hours=2)
                hist = [(v_created, 5.99), (v_created + timedelta(days=rng.randint(1, 20)), 0.00)]
                add_variant(pid, "Unique", "Noir", hist, v_created, "prix_zero")
                # prix maximal numeric(12,2)
                pid, cr, c = edge_product("prix_max", i)
                v_created = cr + timedelta(hours=2)
                add_variant(pid, "Unique", "Or", [(v_created, 9999999999.99)], v_created, "prix_max")

        cur.executemany(
            "insert into public.product (product_id, product_name, product_ref, product_desc, "
            "product_categorie, product_create_at, product_marque_id, product_category_id) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s)", products)

        # category_pivot : catégorie principale de chaque produit + parent + catégories liées
        # (on conflict do nothing : compatible avec le trigger de la migration, qui l'insère aussi)
        pivot_extra = set(extra_pivot)
        pivot_extra |= {(pid, c) for pid, c in product_cat.items() if c is not None}
        for pid in shop_pids:
            pivot_extra.add((pid, leaf[product_cat[pid]][3]))
            if rng.random() < 0.25:
                pivot_extra.add((pid, rng.choice(leaf_ids)))
        cur.executemany("insert into public.category_pivot (product_id, category_id) values (%s,%s) "
                        "on conflict do nothing", sorted(pivot_extra))

        cur.executemany(
            "insert into public.variantes (variante_id, variante_size, variante_color, variante_price, "
            "variante_create_at, variante_product_id) values (%s,%s,%s,%s,%s,%s)", variantes)
        cur.executemany(
            "insert into public.price_historique (price_id, price_amount, create_at, currency_id, price_variante_id) "
            "values (%s,%s,%s,%s,%s)", prices)
        variante_created = {v[0]: v[4] for v in variantes}

        def price_at(variante_id: int, when: datetime) -> float:
            hist = var_history[variante_id]
            idx = bisect.bisect_right([t for t, _ in hist], when) - 1
            return hist[max(idx, 0)][1]

        # =====================================================================
        # product_pivot (favoris)
        # =====================================================================
        all_pids = [p[0] for p in products]
        fav = []
        for uid in [u[0] for u in users]:
            tag = meta.get(uid)
            if tag == "beaucoup_favoris":
                chosen = rng.sample(all_pids, k=min(35, len(all_pids)))
            elif tag:
                chosen = []                                   # cas limites : aucun favori par défaut
            else:
                chosen = rng.sample(shop_pids, k=min(len(shop_pids), rng.choices([0, 1, 3, 5, 8], [20, 25, 30, 15, 10])[0]))
            for pid in chosen:
                fav.append((pid, uid, rand_dt(rng, max(user_created[uid], product_created[pid]), now)))
        cur.executemany("insert into public.product_pivot (product_id, user_id, pivot_created_at) values (%s,%s,%s)", fav)

        # =====================================================================
        # panier / panier_items
        # =====================================================================
        paniers, panier_items = [], []
        pn_c = itertools.count(1)
        for uid in [u[0] for u in users]:
            tag = meta.get(uid)
            if tag in ("sans_panier",) or (tag and tag not in ("panier_vide", "panier_un_article", "panier_gros")):
                continue
            if tag is None and rng.random() >= 0.6:
                continue
            pan_id = len(paniers) + 1
            paniers.append((pan_id, uid))
            if tag == "panier_vide":
                continue
            if tag == "panier_un_article":
                chosen, qtys = rng.sample(shop_variantes, k=1), [1]
            elif tag == "panier_gros":
                chosen = rng.sample(shop_variantes, k=min(12, len(shop_variantes)))
                qtys = [999] + [rng.choice([1, 2]) for _ in chosen[1:]]
            else:
                chosen = rng.sample(shop_variantes, k=rng.randint(1, 5))
                qtys = [rng.choices([1, 2, 3], [75, 20, 5])[0] for _ in chosen]
            for v, q in zip(chosen, qtys):
                panier_items.append((next(pn_c), pan_id, v, q))
        cur.executemany("insert into public.panier (panier_id, panier_user_id) values (%s,%s)", paniers)
        cur.executemany("insert into public.panier_items (panier_items_id, panier_id, panier_items_variante_id, "
                        "panier_items_quantity) values (%s,%s,%s,%s)", panier_items)

        # =====================================================================
        # commandes / commandes_items / user_guest / commandes_userguest
        # =====================================================================
        orders, items, guests, guest_links = [], [], [], []
        cmd_c, item_c = itertools.count(1), itertools.count(1)
        order_floor = now - timedelta(days=365)
        statuses, weights = zip(*ORDER_STATUS_WEIGHTS)
        old_users = [u for u in normal_uids if user_created[u] < now - timedelta(days=60)] or normal_uids
        zero_variants = list(edge_variants.get("prix_zero", []))

        def make_order(user_id, when, adress_txt, adress_id, status=None, variants=None, qty=None, ref=None, n_items=None):
            cmd = next(cmd_c)
            if status is None:
                status = rng.choices(statuses, weights)[0]
                if (now - when).days < 5 and status in ("livree", "remboursee"):
                    status = rng.choice(["payee", "expediee"])
            orders.append((cmd, user_id, ref or f"CMD-{when:%Y%m%d}-{cmd:06d}", when, status, adress_txt, adress_id))
            if variants is None:
                k = n_items or rng.choices([1, 2, 3, 4], [45, 30, 17, 8])[0]
                variants = rng.sample(shop_variantes, k=min(k, len(shop_variantes)))
            for v in variants:
                items.append((next(item_c), cmd, v, price_at(v, when),
                              qty if qty is not None else rng.choices([1, 2, 3], [78, 17, 5])[0]))
            return cmd

        def member_order(uid, when, **kw):
            addrs = user_adresses.get(uid)
            if addrs:
                a_id = rng.choice(addrs)
                a = adress_by_id[a_id]
                return make_order(uid, when, f"{a[2]}, {a[1]}, {a[4]}", a_id, **kw)
            return make_order(uid, when, fake.address().replace("\n", ", "), None, **kw)

        def when_for(uid, min_age_days=0.0, max_age_days=365):
            start = max(user_created[uid], now - timedelta(days=max_age_days))
            return rand_dt(rng, start, now - timedelta(days=min_age_days, hours=2))

        def guest_order(guest_id, when, **kw):
            street = fake.street_address().replace("\n", " ")
            c = make_order(None, when, f"{street}, {fake.postcode()} {fake.city()}, France", None, **kw)
            guest_links.append((c, guest_id))
            return c

        # --- jeu normal
        for uid in normal_uids + edge_users.get("beaucoup_commandes", []):
            n = 12 if meta.get(uid) == "beaucoup_commandes" else rng.choices([0, 1, 2, 3, 5], [28, 30, 22, 14, 6])[0]
            for _ in range(n):
                member_order(uid, when_for(uid))
        guest_id = itertools.count(1)
        guest_first: dict[int, int] = {}
        for _ in range(args.guests):
            g = next(guest_id)
            for _ in range(rng.choices([1, 2], [75, 25])[0]):
                c = guest_order(g, rand_dt(rng, order_floor, now - timedelta(hours=2)))
                guest_first.setdefault(g, c)
            guests.append((g, fake.phone_number(), guest_first[g]))

        # --- cas limites
        if E:
            age = {"en_attente": (0, 2), "payee": (1, 5), "expediee": (3, 15), "livree": (20, 365),
                   "annulee": (20, 365), "remboursee": (20, 365)}
            for status in STATUSES:                                  # E commandes par statut
                lo, hi = age[status]
                for i in range(1, E + 1):
                    uid = rng.choice(old_users)
                    member_order(uid, when_for(uid, lo, hi), status=status, ref=f"EDGE-{status.upper()}-{i:03d}")
            for i in range(1, E + 1):                                # commandes passées à l'instant
                uid = rng.choice(old_users)
                member_order(uid, now - timedelta(minutes=rng.randint(1, 50)), status="en_attente",
                             ref=f"EDGE-AUJOURDHUI-{i:03d}")
            for i in range(1, E + 1):                                # sans ligne
                uid = rng.choice(old_users)
                member_order(uid, when_for(uid, 20), status="annulee", variants=[], ref=f"EDGE-SANS-LIGNE-{i:03d}")
            for i in range(1, E + 1):                                # gros panier
                uid = rng.choice(old_users)
                member_order(uid, when_for(uid, 20), n_items=12, ref=f"EDGE-GROS-PANIER-{i:03d}")
            for i in range(1, E + 1):                                # quantités élevées
                uid = rng.choice(old_users)
                member_order(uid, when_for(uid, 20), n_items=rng.randint(1, 2), qty=500, ref=f"EDGE-QUANTITE-{i:03d}")
            for i in range(1, E + 1):                                # total à zéro
                uid = rng.choice(old_users)
                member_order(uid, when_for(uid, 20), status="livree", variants=[zero_variants[(i - 1) % len(zero_variants)]],
                             qty=1, ref=f"EDGE-TOTAL-ZERO-{i:03d}")
            for i in range(1, E + 1):                                # prix modifié depuis la commande
                for _try in range(100):
                    uid = rng.choice(old_users)
                    when = when_for(uid, 20)
                    changed = [v for v in shop_variantes if price_at(v, when) != var_history[v][-1][1]]
                    if changed:
                        break
                else:
                    raise RuntimeError("impossible de trouver une variante dont le prix a changé")
                member_order(uid, when, variants=[rng.choice(changed)], ref=f"EDGE-PRIX-MODIFIE-{i:03d}")
            for i in range(1, E + 1):                                # adresse figée sans lien vers adress
                uid = rng.choice([u for u in old_users if user_adresses.get(u)] or old_users)
                a = adress_by_id[rng.choice(user_adresses[uid])] if user_adresses.get(uid) else None
                txt = f"{a[2]}, {a[1]}, {a[4]}" if a else fake.address().replace("\n", ", ")
                make_order(uid, when_for(uid, 20), txt, None, ref=f"EDGE-ADRESSE-TEXTE-{i:03d}")
            for i in range(1, E + 1):                                # aucune adresse du tout
                uid = rng.choice(old_users)
                make_order(uid, when_for(uid, 20), None, None, ref=f"EDGE-SANS-ADRESSE-{i:03d}")
            for i in range(1, E + 1):                                # compte supprimé : ni user ni invité
                when = rand_dt(rng, order_floor, now - timedelta(days=20))
                make_order(None, when, f"{fake.street_address()}, {fake.postcode()} {fake.city()}".replace("\n", " "),
                           None, ref=f"EDGE-ORPHELINE-{i:03d}")
            for i in range(1, E + 1):                                # invités avec 3 commandes
                g = next(guest_id)
                first = None
                for k in range(3):
                    c = guest_order(g, rand_dt(rng, order_floor, now - timedelta(hours=2)),
                                    ref=f"EDGE-INVITE-MULTI-{i:03d}-{k + 1}")
                    first = first or c
                guests.append((g, fake.phone_number(), first))
            for i in range(1, E + 1):                                # commande liée à 2 invités
                g1, g2 = next(guest_id), next(guest_id)
                c = guest_order(g1, rand_dt(rng, order_floor, now - timedelta(hours=2)), ref=f"EDGE-DOUBLE-INVITE-{i:03d}")
                guest_links.append((c, g2))
                guests += [(g1, fake.phone_number(), c), (g2, fake.phone_number(), c)]
            for i in range(1, E + 1):                                # invités sans commande
                guests.append((next(guest_id), fake.phone_number(), None))

        cur.executemany(
            "insert into public.commandes (commande_id, commande_user_id, commande_ref, commande_date, "
            "commande_status, commande_adress, commande_adress_id) values (%s,%s,%s,%s,%s,%s,%s)", orders)
        cur.executemany(
            "insert into public.commandes_items (commande_items_id, commande_id, commande_variante_id, "
            "commande_unique_price, quantity) values (%s,%s,%s,%s,%s)", items)
        cur.executemany("insert into public.user_guest (user_guest_id, user_guest_number, commande_id) values (%s,%s,%s)",
                        sorted(guests))
        cur.executemany("insert into public.commandes_userguest (id, commande_id, user_guest_id) values (%s,%s,%s)",
                        [(i, c, g) for i, (c, g) in enumerate(guest_links, 1)])

        # =====================================================================
        # promotion / reduction
        # =====================================================================
        promos, reductions = [], []
        pr_c, red_c = itertools.count(1), itertools.count(1)

        def add_promo(created, expire, cat, prod, percents):
            pr = next(pr_c)
            promos.append((pr, cat, prod, expire, created))
            for p in percents:
                reductions.append((next(red_c), p, pr))

        for _ in range(10):                                          # jeu normal
            created = rand_dt(rng, now - timedelta(days=120), now - timedelta(days=20))
            expire = created + timedelta(days=rng.choice([14, 30, 45, 90, 180]))
            kind = rng.choice(["category", "product", "both"])
            add_promo(created, expire,
                      rng.choice(all_cat_ids) if kind in ("category", "both") else None,
                      rng.choice(shop_pids) if kind in ("product", "both") else None,
                      [rng.choice([5, 10, 15, 20, 25, 30, 40, 50]) for _ in range(rng.choices([1, 2], [85, 15])[0])])

        def active_window():
            created = now - timedelta(days=rng.randint(1, 60))
            return created, now + timedelta(days=rng.randint(5, 120))

        def expired_window():
            return now - timedelta(days=rng.randint(120, 200)), now - timedelta(days=rng.randint(1, 100))

        if E:
            for i in range(1, E + 1):
                add_promo(*active_window(), rng.choice(all_cat_ids), None, [rng.choice([10, 20, 30])])       # active
                add_promo(*expired_window(), None, rng.choice(shop_pids), [rng.choice([10, 20, 30])])        # expirée
                created = now - timedelta(days=10)
                add_promo(created, now + timedelta(minutes=rng.randint(5, 50)), rng.choice(all_cat_ids), None, [15])  # expire bientôt
                add_promo(*active_window(), rng.choice(all_cat_ids), None, [rng.choice([5, 10])])             # catégorie seule
                add_promo(*active_window(), None, rng.choice(shop_pids), [rng.choice([5, 10])])               # produit seul
                add_promo(*active_window(), rng.choice(all_cat_ids), rng.choice(shop_pids), [25])             # catégorie + produit
                add_promo(*active_window(), rng.choice(all_cat_ids), None, [5, 10, 15, 20])                   # 4 réductions
                add_promo(*active_window(), None, rng.choice(shop_pids), [])                                  # sans réduction
                add_promo(*active_window(), rng.choice(all_cat_ids), None, [100])                             # 100 %
                add_promo(*active_window(), None, rng.choice(shop_pids), [0.01])                              # 0,01 %

        cur.executemany("insert into public.promotion (promotion_id, promotion_category_id, promotion_product_id, "
                        "promotion_expire_date, promotion_created_date) values (%s,%s,%s,%s,%s)", promos)
        cur.executemany("insert into public.reduction (reduction_id, pourcentage, promotion_id) values (%s,%s,%s)", reductions)

        # =====================================================================
        # séquences
        # =====================================================================
        for table, pk in IDENTITY_PK.items():
            cur.execute(
                f"select setval(pg_get_serial_sequence('public.{table}', '{pk}'), "
                f"coalesce((select max({pk}) from public.{table}), 1), "
                f"(select count(*) > 0 from public.{table}))")

        # =====================================================================
        # auto-vérification des cas limites (avant commit)
        # =====================================================================
        manifest, failures = {}, []
        if E:
            for key, desc, query in SCENARIOS:
                cur.execute(query)
                ids = sorted({r[0] for r in cur.fetchall()})
                manifest[key] = {"description": desc, "count": len(ids), "ids": ids}
                if len(ids) < E:
                    failures.append(f"{key}: {len(ids)} < {E}")
        if failures:
            conn.rollback()
            print("ÉCHEC : cas limites insuffisants, rien n'a été enregistré :\n  " + "\n  ".join(failures))
            return 2
        conn.commit()

        print("Seed terminé :")
        for t in TABLES_FK_ORDER:
            cur.execute(f"select count(*) from public.{t}")
            print(f"  {t:<22}{cur.fetchone()[0]:>6}")
        if E:
            print(f"\nCas limites (minimum {E} lignes chacun, {len(manifest)} cas) :")
            width = max(len(k) for k in manifest)
            for key, info in manifest.items():
                print(f"  {key:<{width}}  {info['count']:>5}  {info['description']}")
        if args.manifest:
            with open(args.manifest, "w", encoding="utf-8") as f:
                json.dump({"edge_count": E, "seed": args.seed, "cases": manifest}, f, ensure_ascii=False, indent=2)
            print(f"\nManifeste écrit dans {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
