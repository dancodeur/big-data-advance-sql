# big-data-advance-sql — base e-commerce (PostgreSQL / Supabase)

Schéma relationnel e-commerce corrigé à partir de `docs/original/Modelisation_DB.json`
(aucune table ni colonne renommée ; corrections de types, clés étrangères, contraintes, index, RLS).

## Contenu

| Chemin | Rôle |
|---|---|
| `docs/Modelisation_DB_corrige.drawio` | Schéma corrigé (draw.io) : vert = colonne ajoutée, orange = colonne corrigée |
| `docs/original/Modelisation_DB.json` | Schéma d'origine |
| `supabase/migrations/20261118120000_init_ecommerce_schema.sql` | Schéma de la BD : 18 tables, contraintes, index, triggers, RLS |
| `sql/schema_tables_only.sql` | Version allégée : uniquement les 18 tables, clés, contraintes et index (sans RLS, policies, droits, triggers) |
| `sql/seed_data/seed_data_0X_sur_04.sql` | Mêmes données que `seed.py --seed 42`, en 4 fichiers SQL à coller dans le SQL Editor Supabase, dans l'ordre (sans mot de passe ni Python) |
| `scripts/rebuild_db.py` | Reconstruit la BD en local (ou sur Supabase) depuis les migrations |
| `scripts/seed.py` | Seeder : peuple les 18 tables avec des données fictives cohérentes |
| `scripts/generate_drawio.py` | Régénère le `.drawio` depuis la base réelle |
| `scripts/local_supabase_stubs.sql` | Faux environnement Supabase (rôles, `auth.uid()`), utilisé en local uniquement |
| `tests/verify.sql`, `tests/run_local.sh` | Contrôles de cohérence + RLS, test de bout en bout |

## Reconstruire la BD en local

```bash
pip install "psycopg[binary]" faker
docker run -d --name pg-shop -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:17

python scripts/rebuild_db.py --dsn postgresql://postgres:postgres@localhost:5432/shop --create-db --seed
# repartir de zéro :
python scripts/rebuild_db.py --dsn postgresql://postgres:postgres@localhost:5432/shop --reset --seed
```

Seeder seul : `python scripts/seed.py --dsn <dsn> --users 200 --products 300 --seed 1 [--reset --yes]`
(refuse de tourner si des tables contiennent déjà des lignes, sauf `--reset`).

## Jeu de cas limites pour les tests

Le seeder ajoute par défaut **66 cas limites, 10 lignes minimum chacun** (`--edge-count N` pour changer le minimum, `0` pour les désactiver) :
paniers vides / à 1 ligne / à 12 lignes / quantité 999, utilisateurs sans panier / adresse / commande / favoris / téléphone, comptes inactifs,
champs à longueur maximale (pseudo 50, email 100, téléphone 255, adresse 255), caractères spéciaux (emoji, apostrophes, injection SQL),
produits sans marque / catégorie / variante / description, nom de 255 caractères, description de 10 000 caractères, produit à 30+ variantes,
prix à 0,00 et prix maximal, historiques de prix longs ou à un seul relevé, commandes de **chaque statut**, commandes du jour, sans ligne, à total nul,
au prix modifié depuis, orphelines (compte supprimé), invitées (1 à 3 commandes, 2 invités sur une commande, invité sans commande),
promotions actives / expirées / bientôt expirées / sans réduction / à 4 réductions, réductions de 100 % et de 0,01 %.

Chaque cas est vérifié en base avant le commit (rollback s'il manque des lignes). Les ids concernés sont exportables :
`python scripts/seed.py --manifest cas_limites.json`. Les lignes de cas limites portent un préfixe repérable (`edge_…`, `EDGE-…`).

## Sur Supabase

```bash
export DATABASE_URL="postgresql://postgres:MOT_DE_PASSE@db.<ref>.supabase.co:5432/postgres"
python scripts/rebuild_db.py --seed
```
Ou coller la migration dans le SQL editor / `supabase db push`.

## Choix de conception à connaître

- `Product_Pivot` = favoris (utilisateur ↔ produit). `commandes.commande_adress` = adresse figée (texte) ; `commande_adress_id` lie optionnellement `adress`.
- `variantes.variante_price` est le prix courant, tenu à jour par trigger depuis `price_historique`.
- `product_category_id` alimente `category_pivot` et `product_categorie` (legacy) par trigger.
- `users.auth_user_id` lie le profil à Supabase Auth ; `user_password` est optionnel (n'y stocker qu'un hash).
- RLS : catalogue en lecture publique, données personnelles réservées au propriétaire, `user_guest` / `commandes_userguest` accessibles au `service_role` uniquement.
- Noms conservés malgré les fautes d'origine (`adress_contry`, …) pour ne rien casser.
