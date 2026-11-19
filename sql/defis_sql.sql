-- DÉFI SQL 01
SELECT a.adress_contry                                               AS pays,
       COUNT(DISTINCT c.commande_id)                                 AS nb_commandes,
       ROUND(SUM(i.commande_unique_price * i.quantity), 2)           AS chiffre_affaires,
       ROUND(SUM(i.commande_unique_price * i.quantity)
             / COUNT(DISTINCT c.commande_id), 2)                     AS panier_moyen
FROM commandes c
JOIN adress a          ON a.adress_id   = c.commande_adress_id
JOIN commandes_items i ON i.commande_id = c.commande_id
WHERE c.commande_status IN ('payee', 'expediee', 'livree')
GROUP BY a.adress_contry
ORDER BY chiffre_affaires DESC;


-- DÉFI SQL 02
SELECT TRIM(split_part(a.adress_name, ',', -1))                      AS ville,
       ROUND(SUM(i.commande_unique_price * i.quantity)
             / COUNT(DISTINCT c.commande_id), 2)                     AS panier_moyen
FROM commandes c
JOIN adress a          ON a.adress_id   = c.commande_adress_id
JOIN commandes_items i ON i.commande_id = c.commande_id
WHERE c.commande_status IN ('payee', 'expediee', 'livree')
GROUP BY TRIM(split_part(a.adress_name, ',', -1))
HAVING COUNT(DISTINCT c.commande_id) >= 30
ORDER BY panier_moyen DESC
LIMIT 20;


-- DÉFI SQL 03
SELECT u.user_id,
       u.user_pseudo,
       COUNT(DISTINCT c.commande_id)                                 AS nb_commandes,
       ROUND(SUM(i.commande_unique_price * i.quantity), 2)           AS depense_totale
FROM users u
JOIN commandes c       ON c.commande_user_id = u.user_id
JOIN commandes_items i ON i.commande_id      = c.commande_id
WHERE c.commande_status IN ('payee', 'expediee', 'livree')
  AND c.commande_date >= now() - INTERVAL '12 months'
GROUP BY u.user_id, u.user_pseudo
ORDER BY nb_commandes DESC, depense_totale DESC
LIMIT 50;


-- DÉFI SQL 04
SELECT v.variante_id,
       p.product_name   AS produit,
       v.variante_size  AS taille,
       v.variante_color AS couleur
FROM variantes v
JOIN product p ON p.product_id = v.variante_product_id
WHERE v.variante_price > 0
  AND NOT EXISTS (SELECT 1
                  FROM commandes_items i
                  JOIN commandes c ON c.commande_id = i.commande_id
                  WHERE i.commande_variante_id = v.variante_id
                    AND c.commande_status IN ('payee', 'expediee', 'livree'))
ORDER BY p.product_name, v.variante_size, v.variante_color;


-- DÉFI SQL 05
SELECT m.marque_name                                                 AS marque,
       ROUND(SUM(i.commande_unique_price * i.quantity), 2)           AS chiffre_affaires
FROM commandes_items i
JOIN commandes c ON c.commande_id  = i.commande_id
JOIN variantes v ON v.variante_id  = i.commande_variante_id
JOIN product   p ON p.product_id   = v.variante_product_id
JOIN marques   m ON m.marque_id    = p.product_marque_id
WHERE c.commande_status IN ('payee', 'expediee', 'livree')
GROUP BY m.marque_id, m.marque_name
ORDER BY chiffre_affaires DESC;


-- DÉFI SQL 07
SELECT u.user_id,
       u.user_pseudo,
       MAX(c.commande_date)::date AS derniere_commande
FROM users u
JOIN commandes c ON c.commande_user_id = u.user_id
WHERE c.commande_status IN ('payee', 'expediee', 'livree')
GROUP BY u.user_id, u.user_pseudo
HAVING MAX(c.commande_date) < now() - INTERVAL '180 days'
ORDER BY derniere_commande;


-- DÉFI SQL 08
SELECT DISTINCT ON (pays) pays, categorie
FROM (
  SELECT a.adress_contry                                             AS pays,
         cat.category_name                                           AS categorie,
         SUM(i.commande_unique_price * i.quantity)                   AS ca
  FROM commandes c
  JOIN adress a          ON a.adress_id     = c.commande_adress_id
  JOIN commandes_items i ON i.commande_id   = c.commande_id
  JOIN variantes v       ON v.variante_id   = i.commande_variante_id
  JOIN category_pivot cp ON cp.product_id   = v.variante_product_id
  JOIN categorie cat     ON cat.category_id = cp.category_id
  WHERE c.commande_status IN ('payee', 'expediee', 'livree')
  GROUP BY a.adress_contry, cat.category_id, cat.category_name
) t
ORDER BY pays, ca DESC, categorie;


-- DÉFI SQL 18
WITH ventes AS (
  SELECT a.adress_contry                                                          AS pays,
         date_trunc('month', c.commande_date AT TIME ZONE 'Europe/Paris')::date   AS mois,
         SUM(i.commande_unique_price * i.quantity)                                 AS ca
  FROM commandes c
  JOIN adress a          ON a.adress_id   = c.commande_adress_id
  JOIN commandes_items i ON i.commande_id = c.commande_id
  WHERE c.commande_status IN ('payee', 'expediee', 'livree')
  GROUP BY 1, 2
),
grille AS (
  SELECT p.pays, m.mois, COALESCE(v.ca, 0) AS ca
  FROM (SELECT DISTINCT pays FROM ventes) p
  CROSS JOIN (SELECT generate_series(MIN(mois), MAX(mois), INTERVAL '1 month')::date AS mois
              FROM ventes) m
  LEFT JOIN ventes v ON v.pays = p.pays AND v.mois = m.mois
)
SELECT pays,
       mois,
       ROUND(ca, 2)                                                          AS ca_mois,
       ROUND(LAG(ca) OVER w, 2)                                              AS ca_mois_precedent,
       ROUND(ca - LAG(ca) OVER w, 2)                                         AS variation_eur,
       ROUND((ca - LAG(ca) OVER w) / NULLIF(LAG(ca) OVER w, 0) * 100, 1)     AS variation_pourcentage
FROM grille
WINDOW w AS (PARTITION BY pays ORDER BY mois)
ORDER BY pays, mois;


-- DÉFI SQL 19
WITH achats AS (
  SELECT commande_user_id AS user_id,
         commande_date,
         MIN(commande_date) OVER (PARTITION BY commande_user_id) AS premier_achat
  FROM commandes
  WHERE commande_status IN ('payee', 'expediee', 'livree')
    AND commande_user_id IS NOT NULL
)
SELECT COUNT(DISTINCT user_id) AS nb_clients,
       ROUND(100.0 * COUNT(DISTINCT user_id) FILTER (WHERE commande_date > premier_achat
             AND commande_date <= premier_achat + INTERVAL '30 days') / COUNT(DISTINCT user_id), 1) AS pourcentage_30_jours,
       ROUND(100.0 * COUNT(DISTINCT user_id) FILTER (WHERE commande_date > premier_achat
             AND commande_date <= premier_achat + INTERVAL '60 days') / COUNT(DISTINCT user_id), 1) AS pourcentage_60_jours,
       ROUND(100.0 * COUNT(DISTINCT user_id) FILTER (WHERE commande_date > premier_achat
             AND commande_date <= premier_achat + INTERVAL '90 days') / COUNT(DISTINCT user_id), 1) AS pourcentage_90_jours
FROM achats;
