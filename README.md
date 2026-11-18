# big-data-advance-sql — base e-commerce (PostgreSQL / Supabase)

Schéma relationnel e-commerce corrigé à partir de `docs/original/Modelisation_DB.json`
(aucune table ni colonne renommée ; corrections de types, clés étrangères, contraintes, index, RLS).

## Contenu

| Chemin | Rôle |
|---|---|
| `docs/Modelisation_DB_corrige.drawio` | Schéma corrigé (draw.io) : vert = colonne ajoutée, orange = colonne corrigée |
| `docs/original/Modelisation_DB.json` | Schéma d'origine |
| `supabase/migrations/20261118120000_init_ecommerce_schema.sql` | Schéma de la BD : 18 tables, contraintes, index, triggers, RLS |
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
