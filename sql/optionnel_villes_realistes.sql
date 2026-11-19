-- =====================================================================
-- OPTIONNEL : des villes "réalistes" pour le DÉFI SQL 02
-- (inutile si tu as rechargé les nouvelles données de sql/seed_data/ : elles contiennent déjà
--  de vraies villes. Sert seulement à rattraper l'ANCIENNE version des données.)
-- =====================================================================
-- Pourquoi ? Le seeder tire les villes au hasard (Faker) : chaque ville
-- n'a que ~10 commandes, donc "au moins 30 commandes" ne renvoie rien.
-- Ce script remplace la ville des adresses FRANÇAISES par 5 vraies villes
-- (Paris, Lyon, Marseille, Toulouse, Lille) et met à jour l'adresse copiée
-- dans les commandes (commandes.commande_adress).
--
-- Ne touche à aucune structure (pas de renommage de table/colonne).
-- Peut être lancé plusieurs fois sans danger (résultat identique).
-- À lancer APRÈS le chargement des données, dans le SQL Editor ou psql.
-- =====================================================================
BEGIN;

WITH nouvelle AS (
  SELECT adress_id,
         CASE
           WHEN adress_id % 10 IN (0, 1, 2) THEN 'Paris'      -- 30 %
           WHEN adress_id % 10 IN (3, 4)    THEN 'Lyon'       -- 20 %
           WHEN adress_id % 10 IN (5, 6)    THEN 'Marseille'  -- 20 %
           WHEN adress_id % 10 = 7          THEN 'Toulouse'   -- 10 %
           WHEN adress_id % 10 = 8          THEN 'Lille'      -- 10 %
           ELSE NULL                                          -- 10 % : on laisse tel quel
         END AS ville
  FROM adress
  WHERE adress_contry = 'France'
)
UPDATE adress a
SET adress_name = regexp_replace(a.adress_name, '[^,]*$', ' ' || n.ville)
FROM nouvelle n
WHERE n.adress_id = a.adress_id
  AND n.ville IS NOT NULL;

-- Recopie l'adresse "photo" dans les commandes liées à une adresse
UPDATE commandes c
SET commande_adress = a.adress_name || ', ' || a.adress_cp || ', ' || a.adress_contry
FROM adress a
WHERE a.adress_id = c.commande_adress_id
  AND a.adress_contry = 'France';

COMMIT;
