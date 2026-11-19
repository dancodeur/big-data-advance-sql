-- =====================================================================
--  NEXORA · DÉFIS SQL        (SQL avancé · PostgreSQL · M2)
--  Base : e-commerce (tables commandes, commandes_items, adress, users)
-- =====================================================================
--  Hypothèses communes (à connaître pour expliquer les résultats) :
--   • Commande "validée" / "payée" = statut payee, expediee ou livree
--     (on exclut en_attente, annulee, remboursee).
--   • Montant d'une commande = SUM(prix unitaire x quantité) de ses lignes
--     (commandes_items) ; il n'y a pas de colonne "total" dans la table.
--   • Le pays / la ville de livraison vient de adress, via
--     commandes.commande_adress_id (les commandes sans adresse liée, ex.
--     commandes invité, ne comptent pas dans les défis 1 et 2).
--   • Il n'y a pas de colonne "ville" : adress_name vaut "rue, ville",
--     donc la ville = ce qui suit la dernière virgule.
-- =====================================================================


-- =====================================================================
-- DÉFI SQL 01 — Panier moyen par pays
-- Quel est le montant moyen d'une commande validée dans chaque pays de
-- livraison ? Comparez le nombre de commandes et le chiffre d'affaires.
-- =====================================================================
WITH totaux AS (                       -- 1 ligne = 1 commande validée + son montant
  SELECT c.commande_id,
         a.adress_contry                          AS pays,
         SUM(i.commande_unique_price * i.quantity) AS montant
  FROM commandes c
  JOIN adress          a ON a.adress_id  = c.commande_adress_id
  JOIN commandes_items i ON i.commande_id = c.commande_id
  WHERE c.commande_status IN ('payee', 'expediee', 'livree')
  GROUP BY c.commande_id, a.adress_contry
)
SELECT pays,
       COUNT(*)                 AS nb_commandes,
       ROUND(SUM(montant), 2)   AS chiffre_affaires,
       ROUND(AVG(montant), 2)   AS panier_moyen
FROM totaux
GROUP BY pays
ORDER BY chiffre_affaires DESC;


-- =====================================================================
-- DÉFI SQL 02 — Panier moyen par ville
-- Quelles sont les 20 villes au panier moyen le plus élevé, parmi celles
-- ayant au moins 30 commandes ?
-- =====================================================================
-- (Avec les données de test actuelles : 8 villes ressortent. Si tu as encore
--  l'ANCIENNE version des données et que le résultat est vide, recharge les
--  nouvelles données ou lance sql/optionnel_villes_realistes.sql.)
WITH totaux AS (
  SELECT c.commande_id,
         TRIM(split_part(a.adress_name, ',', -1))  AS ville,   -- après la dernière virgule
         SUM(i.commande_unique_price * i.quantity) AS montant
  FROM commandes c
  JOIN adress          a ON a.adress_id  = c.commande_adress_id
  JOIN commandes_items i ON i.commande_id = c.commande_id
  WHERE c.commande_status IN ('payee', 'expediee', 'livree')
  GROUP BY c.commande_id, TRIM(split_part(a.adress_name, ',', -1))
)
SELECT ville,
       COUNT(*)                 AS nb_commandes,
       ROUND(AVG(montant), 2)   AS panier_moyen
FROM totaux
GROUP BY ville
HAVING COUNT(*) >= 30                  -- filtre sur le groupe (pas WHERE)
ORDER BY panier_moyen DESC
LIMIT 20;


-- =====================================================================
-- DÉFI SQL 03 — Clients les plus fidèles
-- Quels sont les 50 clients avec le plus de commandes payées sur 12 mois ?
-- Affichez aussi leur dépense totale.
-- =====================================================================
WITH totaux AS (
  SELECT c.commande_id,
         c.commande_user_id,
         SUM(i.commande_unique_price * i.quantity) AS montant
  FROM commandes c
  JOIN commandes_items i ON i.commande_id = c.commande_id
  WHERE c.commande_status IN ('payee', 'expediee', 'livree')
    AND c.commande_date >= now() - INTERVAL '12 months'
    AND c.commande_user_id IS NOT NULL            -- clients inscrits uniquement
  GROUP BY c.commande_id, c.commande_user_id
)
SELECT u.user_id,
       u.user_pseudo,
       COUNT(*)               AS nb_commandes,
       ROUND(SUM(t.montant), 2) AS depense_totale
FROM totaux t
JOIN users u ON u.user_id = t.commande_user_id
GROUP BY u.user_id, u.user_pseudo
ORDER BY nb_commandes DESC, depense_totale DESC, u.user_id
LIMIT 50;


-- =====================================================================
-- DÉFI SQL 04 — Variantes jamais achetées
-- Quelles variantes actives du catalogue n'ont jamais été achetées ?
-- =====================================================================
-- Hypothèses : la table variantes n'a pas de colonne "actif" ; on considère
-- une variante active si son prix est > 0. "Achetée" = présente dans une
-- commande validée (payee, expediee, livree), comme dans les défis 1 à 3.
-- NOT EXISTS = "il n'existe aucune ligne de commande validée pour cette variante".
SELECT v.variante_id,
       p.product_name   AS produit,
       v.variante_size  AS taille,
       v.variante_color AS couleur,
       v.variante_price AS prix
FROM variantes v
JOIN product   p ON p.product_id = v.variante_product_id
WHERE v.variante_price > 0
  AND NOT EXISTS (
        SELECT 1
        FROM commandes_items i
        JOIN commandes c ON c.commande_id = i.commande_id
        WHERE i.commande_variante_id = v.variante_id
          AND c.commande_status IN ('payee', 'expediee', 'livree')
      )
ORDER BY p.product_name, v.variante_size, v.variante_color;


-- =====================================================================
-- DÉFI SQL 05 — Classement des marques par chiffre d'affaires
-- Classez les marques par chiffre d'affaires en tenant compte des
-- quantités et des prix réellement payés.
-- =====================================================================
-- "Prix réellement payé" = commandes_items.commande_unique_price (le prix figé
-- au moment de la commande), et NON le prix actuel de la variante, qui a pu
-- changer depuis. CA = SUM(prix payé x quantité), sur les commandes validées
-- (payee, expediee, livree). Les produits sans marque ne sont pas classés.
SELECT RANK() OVER (ORDER BY SUM(i.commande_unique_price * i.quantity) DESC) AS rang,
       m.marque_name                                      AS marque,
       SUM(i.quantity)                                    AS quantites_vendues,
       COUNT(DISTINCT c.commande_id)                      AS nb_commandes,
       ROUND(SUM(i.commande_unique_price * i.quantity), 2) AS chiffre_affaires
FROM commandes_items i
JOIN commandes c ON c.commande_id = i.commande_id
JOIN variantes v ON v.variante_id = i.commande_variante_id
JOIN product   p ON p.product_id  = v.variante_product_id
JOIN marques   m ON m.marque_id   = p.product_marque_id
WHERE c.commande_status IN ('payee', 'expediee', 'livree')
GROUP BY m.marque_id, m.marque_name
ORDER BY rang, marque;


-- =====================================================================
-- DÉFI SQL 07 — Clients à relancer (inactifs)
-- Quels clients ayant déjà acheté n'ont rien commandé depuis 180 jours ?
-- =====================================================================
-- "Ayant déjà acheté" = au moins une commande validée (payee, expediee, livree).
-- On regarde la DERNIÈRE commande validée de chaque client (MAX) : si elle date de
-- plus de 180 jours, le client est inactif. HAVING filtre après le regroupement.
SELECT u.user_id,
       u.user_pseudo,
       u.user_email,
       COUNT(*)                                      AS nb_commandes,
       MAX(c.commande_date)::date                    AS derniere_commande,
       (CURRENT_DATE - MAX(c.commande_date)::date)   AS jours_depuis
FROM users u
JOIN commandes c ON c.commande_user_id = u.user_id
WHERE c.commande_status IN ('payee', 'expediee', 'livree')
GROUP BY u.user_id, u.user_pseudo, u.user_email
HAVING MAX(c.commande_date) < now() - INTERVAL '180 days'
ORDER BY derniere_commande;


-- =====================================================================
-- DÉFI SQL 08 — Catégorie n°1 de chaque pays
-- Quelle catégorie réalise le plus de ventes dans chaque pays ?
-- Un produit peut appartenir à plusieurs catégories.
-- =====================================================================
-- • Ventes = chiffre d'affaires (prix payé x quantité) des commandes validées
--   (payee, expediee, livree) ; le pays vient de l'adresse liée à la commande.
-- • Un produit pouvant avoir plusieurs catégories, on passe par la table de liaison
--   category_pivot : une vente compte dans CHAQUE catégorie de son produit (le total
--   de toutes les catégories dépasse donc le chiffre d'affaires réel : c'est voulu).
-- • Les produits sans catégorie n'apparaissent pas.
-- • RANK() numérote les catégories par pays ; on garde le rang 1 (en cas d'égalité
--   parfaite, les deux catégories ressortent).
WITH ventes AS (
  SELECT a.adress_contry                                  AS pays,
         cat.category_name                                AS categorie,
         SUM(i.commande_unique_price * i.quantity)        AS chiffre_affaires,
         SUM(i.quantity)                                  AS quantites
  FROM commandes c
  JOIN adress          a   ON a.adress_id       = c.commande_adress_id
  JOIN commandes_items i   ON i.commande_id     = c.commande_id
  JOIN variantes       v   ON v.variante_id     = i.commande_variante_id
  JOIN category_pivot  cp  ON cp.product_id     = v.variante_product_id
  JOIN categorie       cat ON cat.category_id   = cp.category_id
  WHERE c.commande_status IN ('payee', 'expediee', 'livree')
  GROUP BY a.adress_contry, cat.category_id, cat.category_name
),
classement AS (
  SELECT *,
         RANK() OVER (PARTITION BY pays ORDER BY chiffre_affaires DESC) AS rang
  FROM ventes
)
SELECT pays,
       categorie,
       ROUND(chiffre_affaires, 2) AS chiffre_affaires,
       quantites
FROM classement
WHERE rang = 1
ORDER BY chiffre_affaires DESC;
