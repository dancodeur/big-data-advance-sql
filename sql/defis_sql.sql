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
-- (Si le résultat est vide : lancer d'abord sql/optionnel_villes_realistes.sql,
--  car les villes générées au hasard ont chacune moins de 30 commandes.)
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
