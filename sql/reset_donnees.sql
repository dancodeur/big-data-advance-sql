-- =====================================================================
-- VIDER les 18 tables de la boutique avant de recharger les données de test
-- (à lancer UNE fois, avant seed_data_01 ... seed_data_04).
-- N'efface que ces 18 tables. Les compteurs d'identifiants repartent de 1.
-- Si une autre table de ta base pointe vers elles, PostgreSQL refuse et
-- n'efface rien (aucun risque pour tes autres tables).
-- =====================================================================
TRUNCATE TABLE
  reduction, promotion, commandes_userguest, user_guest, commandes_items, commandes,
  panier_items, panier, product_pivot, price_historique, currency, variantes,
  category_pivot, product, categorie, marques, adress, users
RESTART IDENTITY;
