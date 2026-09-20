-- ============================================================================
-- AutoVision 卡密授权底座 · Supabase(Postgres) 建表与函数
-- 部署：Supabase 网页后台 → SQL Editor 整段粘贴执行。
-- 之后：Authentication → Policies → 对下面两张表「Disable access」(打开 RLS)；
--       Edge Function 用 service_role 访问，天然绕过 RLS；anon key 无法读表。
-- ============================================================================

create extension if not exists pgcrypto;

-- 卡密：一次性，激活即绑定一台设备并起算到期。
-- code 明文列允许为空（gen_cards.py 只写入 code_hash，库内不存明文码）。
create table if not exists public.card_keys (
  id           bigint generated always as identity primary key,
  code         text,
  code_hash    text        not null unique,
  card_type    text        not null default 'month',
  duration_s   bigint      not null,
  status       text        not null default 'unused',
  bound_device text,
  activated_at timestamptz,
  note         text,
  created_at   timestamptz not null default now()
);
create index if not exists card_keys_status_idx on public.card_keys (status);

-- 幂等修复：若表在旧版本已按 code not null 建出，这里放开非空，
-- 否则 gen_cards.py 只插 code_hash 的语句会因 code 为 NULL 而失败。
alter table public.card_keys alter column code drop default;
alter table public.card_keys alter column code drop not null;

-- 生效授权：一台设备一条；作废(revoked)即下一次续签失败→到期自然断。
create table if not exists public.licenses (
  id            bigint generated always as identity primary key,
  device_id     text        not null unique,
  code_hash     text        not null,
  card_type     text        not null,
  activated_at  timestamptz not null default now(),
  expires_at    timestamptz not null,
  last_renew_at timestamptz not null default now(),
  revoked       boolean     not null default false
);

-- ----------------------------------------------------------------------------
-- 原子激活：校验卡密→查重(一机一卡)→绑定设备→写到期。并发抢同一卡只有一人成功。
-- 卡密无效原因直接回绝，Edge Function 不泄露"码是否存在"细节给匿名调用方。
-- ----------------------------------------------------------------------------
create or replace function public.sp_activate(p_code text, p_device text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_hash   text := encode(digest(p_code, 'sha256'), 'hex');
  v_card   record;
  v_exists bigint;
  v_now    timestamptz := now();
  v_exp    timestamptz;
begin
  if p_device is null or length(trim(p_device)) = 0 then
    return jsonb_build_object('ok', false, 'error', 'no_device');
  end if;

  -- 该设备已有未到期授权：拒绝再激活(防一机多卡堆叠/白嫖)。
  select id into v_exists
    from public.licenses
   where device_id = p_device and expires_at > v_now and revoked = false
   limit 1;
  if v_exists is not null then
    return jsonb_build_object('ok', false, 'error', 'device_already_activated');
  end if;

  -- 锁定并原子置用：并发下只有一个事务能把 unused→used。
  select id, duration_s, card_type into v_card
    from public.card_keys
   where code_hash = v_hash and status = 'unused'
   for update skip locked
   limit 1;
  if v_card.id is null then
    return jsonb_build_object('ok', false, 'error', 'invalid_code');
  end if;

  v_exp  := v_now + make_interval(secs => v_card.duration_s::int);
  update public.card_keys
     set status = 'used', bound_device = p_device, activated_at = v_now
   where id = v_card.id;

  -- 清理该设备旧的失效授权行，再插新行(device_id 唯一)。
  delete from public.licenses where device_id = p_device;
  insert into public.licenses(device_id, code_hash, card_type, activated_at, expires_at, last_renew_at)
    values (p_device, v_hash, v_card.card_type, v_now, v_exp, v_now);

  return jsonb_build_object('ok', true, 'expires_at', extract(epoch from v_exp)::bigint,
                            'card_type', v_card.card_type);
end;
$$;

-- ----------------------------------------------------------------------------
-- 续签核验：授权仍在有效期且未作废 → 发一张短期凭证(token_exp)。
-- 关键：token 有效期 = min(现在+TOKEN_S, 授权到期)，所以总天数由服务端到期时间封顶，
--       用户改本地时间只会更早触发续签，无法续命；拉黑后下次续签即 REFUSED。
-- ----------------------------------------------------------------------------
create or replace function public.sp_renew(p_device text, p_token_s bigint)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_lic record;
  v_now timestamptz := now();
  v_tok timestamptz;
begin
  select expires_at, revoked, card_type into v_lic
    from public.licenses
   where device_id = p_device
   limit 1;
  if v_lic.card_type is null then
    return jsonb_build_object('ok', false, 'error', 'not_found');
  end if;
  if v_lic.revoked then
    return jsonb_build_object('ok', false, 'error', 'revoked');
  end if;
  if v_lic.expires_at <= v_now then
    return jsonb_build_object('ok', false, 'error', 'license_expired',
                              'expires_at', extract(epoch from v_lic.expires_at)::bigint);
  end if;

  v_tok := least(v_now + make_interval(secs => p_token_s::int), v_lic.expires_at);
  update public.licenses set last_renew_at = v_now where device_id = p_device;

  return jsonb_build_object('ok', true,
    'expires_at', extract(epoch from v_lic.expires_at)::bigint,
    'token_exp',  extract(epoch from v_tok)::bigint,
    'server_now', extract(epoch from v_now)::bigint);
end;
$$;

-- 供 Edge Function 内部调用（service_role 绕过 RLS）。不暴露给 anon。
revoke execute on function public.sp_activate(text, text)         from public, anon, authenticated;
revoke execute on function public.sp_renew(text, bigint)          from public, anon, authenticated;
