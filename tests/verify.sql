-- Vérifications de cohérence : chaque contrôle doit retourner 0 anomalie.
\set ON_ERROR_STOP on
create temp table _checks (name text, violations bigint);

insert into _checks
select 'variante_price = dernier prix de l''historique', count(*) from public.variantes v
where v.variante_price is distinct from (select h.price_amount from public.price_historique h
    where h.price_variante_id = v.variante_id order by h.create_at desc, h.price_id desc limit 1);

insert into _checks
select 'toute variante a >= 1 relevé de prix', count(*) from public.variantes v
where not exists (select 1 from public.price_historique h where h.price_variante_id = v.variante_id);

insert into _checks
select 'prix figé commande = prix en vigueur à la date', count(*) from public.commandes_items i
join public.commandes c using (commande_id)
where i.commande_unique_price is distinct from (select h.price_amount from public.price_historique h
    where h.price_variante_id = i.commande_variante_id and h.create_at <= c.commande_date
    order by h.create_at desc, h.price_id desc limit 1);

insert into _checks
select 'commande postérieure à la création du compte', count(*) from public.commandes c
join public.users u on u.user_id = c.commande_user_id where c.commande_date < u.user_create_at;

insert into _checks
select 'commande postérieure à la création de la variante', count(*) from public.commandes_items i
join public.commandes c using (commande_id) join public.variantes v on v.variante_id = i.commande_variante_id
where c.commande_date < v.variante_create_at;

insert into _checks
select 'catégorie principale présente dans category_pivot', count(*) from public.product p
where p.product_category_id is not null and not exists
  (select 1 from public.category_pivot cp where cp.product_id = p.product_id and cp.category_id = p.product_category_id);

insert into _checks
select 'product_categorie = nom de la catégorie principale', count(*) from public.product p
join public.categorie c on c.category_id = p.product_category_id
where p.product_categorie is distinct from c.category_name;

insert into _checks
select 'category_parent_name = nom du parent', count(*) from public.categorie c
join public.categorie p on p.category_id = c.category_parent_id
where c.category_parent_name is distinct from p.category_name;

insert into _checks
select 'commandes invitées <-> commandes_userguest', count(*) from public.commandes c
where (c.commande_user_id is null) <> exists (select 1 from public.commandes_userguest g where g.commande_id = c.commande_id);

insert into _checks
select 'user_guest.commande_id cohérent avec le pivot', count(*) from public.user_guest g
where g.commande_id is not null and not exists
  (select 1 from public.commandes_userguest x where x.user_guest_id = g.user_guest_id and x.commande_id = g.commande_id);

insert into _checks
select 'adresse de commande appartient au client', count(*) from public.commandes c
join public.adress a on a.adress_id = c.commande_adress_id where a.adress_user_id is distinct from c.commande_user_id;

insert into _checks
select 'promotion sans réduction', count(*) from public.promotion p
where not exists (select 1 from public.reduction r where r.promotion_id = p.promotion_id);

insert into _checks
select 'séquences > max(id) (prochain insert OK)', count(*) from (
  select max(user_id) m, nextval(pg_get_serial_sequence('public.users','user_id')) n from public.users) t where t.n <= t.m;

select name, violations, case when violations = 0 then 'OK' else 'ÉCHEC' end as statut from _checks order by 1;
do $$ begin
  if exists (select 1 from _checks where violations > 0) then raise exception 'Cohérence des données : échec'; end if;
end $$;

-- ===================== Tests RLS =====================
insert into auth.users (id) values ('00000000-0000-0000-0000-000000000001'), ('00000000-0000-0000-0000-000000000002');
update public.users set auth_user_id = '00000000-0000-0000-0000-000000000001' where user_id = 1;
update public.users set auth_user_id = '00000000-0000-0000-0000-000000000002' where user_id = 2;

do $$
declare n int; expected int; ok boolean;
begin
  -- anon : catalogue lisible, données perso invisibles
  set local role anon;
  select count(*) into n from public.product;                      assert n > 0, 'anon doit lire le catalogue';
  begin perform 1 from public.users limit 1; raise exception 'anon lit users !';
  exception when insufficient_privilege then null; end;
  begin insert into public.product (product_name, product_ref) values ('x','x'); raise exception 'anon écrit product !';
  exception when insufficient_privilege then null; end;
  reset role;

  -- authenticated = user 1
  perform set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true);
  select count(*) into expected from public.adress where adress_user_id = 1;
  set local role authenticated;
  select count(*) into n from public.users;                        assert n = 1, 'users: 1 ligne visible, obtenu ' || n;
  select count(*) into n from public.adress;                       assert n = expected, 'adress: seulement les siennes';
  select count(*) into n from public.adress where adress_user_id <> 1; assert n = 0, 'fuite adress';
  select count(*) into n from public.commandes where commande_user_id is distinct from 1; assert n = 0, 'fuite commandes';
  select count(*) into n from public.commandes_items i where not exists
      (select 1 from public.commandes c where c.commande_id = i.commande_id and c.commande_user_id = 1);
  assert n = 0, 'fuite commandes_items';
  begin update public.users set user_account_status = false where user_id = 1; raise exception 'update statut autorisé !';
  exception when insufficient_privilege then null; end;
  update public.users set user_pseudo = 'nouveau_pseudo' where user_id = 1;
  get diagnostics n = row_count;                                   assert n = 1, 'update pseudo doit marcher';
  update public.users set user_pseudo = 'pirate' where user_id = 2;
  get diagnostics n = row_count;                                   assert n = 0, 'update autre user doit être ignoré';
  begin insert into public.adress (adress_cp, adress_name, adress_contry, adress_user_id) values ('75001','x','France',2);
        raise exception 'insert adresse pour un tiers autorisé !';
  exception when insufficient_privilege or check_violation then null; end;
  begin perform 1 from public.user_guest limit 1; raise exception 'authenticated lit user_guest !';
  exception when insufficient_privilege then null; end;
  reset role;
  raise notice 'RLS : tous les contrôles OK';
end $$;
