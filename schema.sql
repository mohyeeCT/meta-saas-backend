-- CopyPilot — Shared Supabase Schema
-- Single Supabase project (lwyhijtckhdqsdmwjdzp) shared by all 5 backends.
-- This file is the canonical reference. Run in the Supabase SQL editor to
-- recreate from scratch. All statements use IF NOT EXISTS / DO NOTHING so
-- they are safe to re-run against an existing database.
-- Last verified against live DB: 2026-06-09

-- ── Jobs ──────────────────────────────────────────────────────────────────────

create table if not exists public.jobs (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid references auth.users not null,
  created_at     timestamptz default now(),
  updated_at     timestamptz default now(),
  status         text default 'pending'
                   check (status = any (array['pending','running','complete','failed','cancelled','cancelling'])),
  name           text,
  tool           text default 'faq'
                   check (tool = any (array['faq','intro','meta','page-copy','all-in-one'])),
  settings       jsonb default '{}',
  rows           jsonb default '[]',
  results        jsonb default '[]',
  logs           jsonb default '[]',
  total_rows     integer default 0,
  completed_rows integer default 0,
  failed_rows    integer default 0,
  current_step   text default 'Starting...',
  error          text
);

alter table public.jobs enable row level security;

do $$ begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'jobs'
      and policyname = 'Users can only access their own jobs'
  ) then
    create policy "Users can only access their own jobs"
      on public.jobs for all
      using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
  end if;
end $$;

create index if not exists jobs_user_id_idx    on public.jobs (user_id);
create unique index if not exists jobs_one_active_per_user_tool_idx
  on public.jobs (user_id, tool)
  where status in ('pending', 'running', 'cancelling');
create index if not exists jobs_created_at_idx on public.jobs (created_at desc);


-- ── User settings ─────────────────────────────────────────────────────────────

create table if not exists public.user_settings (
  user_id             uuid primary key references auth.users,
  gsc_service_account jsonb,
  gsc_auth_method     text not null default 'service_account'
                        constraint user_settings_gsc_auth_method_check
                        check (gsc_auth_method in ('service_account', 'google_oauth')),
  provider_settings   jsonb default '{}',
  brand_profile       jsonb default '{}',
  updated_at          timestamptz default now()
);

alter table public.user_settings
  add column if not exists gsc_auth_method text not null default 'service_account';

alter table public.user_settings
  drop constraint if exists user_settings_gsc_auth_method_check;

alter table public.user_settings
  add constraint user_settings_gsc_auth_method_check
  check (gsc_auth_method in ('service_account', 'google_oauth'));

alter table public.user_settings enable row level security;

do $$ begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'user_settings'
      and policyname = 'Users can only access their own settings'
  ) then
    create policy "Users can only access their own settings"
      on public.user_settings for all
      using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
  end if;
end $$;

-- Server-only credentials. No anon/authenticated RLS policy is intentional.
create table if not exists public.user_credentials (
  user_id uuid primary key references auth.users on delete cascade,
  provider_settings jsonb not null default '{}',
  gsc_service_account jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.user_credentials enable row level security;
revoke all on table public.user_credentials from anon, authenticated;
grant all on table public.user_credentials to service_role;

-- Shared Google Search Console OAuth runtime connection.
-- OAuth callback/state ownership lives in the FAQ backend.
create table if not exists public.gsc_oauth_connections (
  user_id uuid primary key references auth.users on delete cascade,
  refresh_token_ciphertext text not null,
  google_sub text not null,
  google_email text not null,
  scopes text[] not null default array[]::text[],
  status text not null default 'connected'
    check (status in ('connected', 'reconnect_required')),
  connected_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_error_code text
);

alter table public.gsc_oauth_connections enable row level security;
revoke all on table public.gsc_oauth_connections from anon, authenticated;
grant all on table public.gsc_oauth_connections to service_role;


-- ── Brand profiles ────────────────────────────────────────────────────────────

create table if not exists public.brand_profiles (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid references auth.users not null,
  name       text not null,
  data       jsonb default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

alter table public.brand_profiles enable row level security;

do $$ begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'brand_profiles'
      and policyname = 'users own their brand profiles'
  ) then
    create policy "users own their brand profiles"
      on public.brand_profiles for all
      using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
  end if;
end $$;

create index if not exists brand_profiles_user_id_idx on public.brand_profiles (user_id);


-- ── Job templates ─────────────────────────────────────────────────────────────

create table if not exists public.job_templates (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid references auth.users not null,
  tool       text not null default 'faq',
  name       text not null,
  settings   jsonb default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

alter table public.job_templates enable row level security;

do $$ begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'job_templates'
      and policyname = 'users own their templates'
  ) then
    create policy "users own their templates"
      on public.job_templates for all
      using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
  end if;
end $$;

create index if not exists job_templates_user_id_idx on public.job_templates (user_id);


-- ── Indexer jobs ──────────────────────────────────────────────────────────────
-- Used by the URL indexer tool (separate from the 5 copy tools).

create table if not exists public.indexer_jobs (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid references auth.users not null,
  status         text default 'running',
  name           text,
  settings       jsonb default '{}',
  urls           jsonb default '[]',
  results        jsonb default '[]',
  total_urls     integer default 0,
  submitted_urls integer default 0,
  failed_urls    integer default 0,
  queued_urls    integer default 0,
  current_step   text default '',
  error          text,
  created_at     timestamptz default now(),
  updated_at     timestamptz default now()
);

alter table public.indexer_jobs enable row level security;

do $$ begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'indexer_jobs'
      and policyname = 'users own their jobs'
  ) then
    create policy "users own their jobs"
      on public.indexer_jobs for all
      using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
  end if;
end $$;

create index if not exists indexer_jobs_user_id_idx on public.indexer_jobs (user_id);


-- ── Indexer quota ─────────────────────────────────────────────────────────────

create table if not exists public.indexer_quota (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid references auth.users not null,
  date       date not null,
  count      integer default 0,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

alter table public.indexer_quota enable row level security;

do $$ begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'indexer_quota'
      and policyname = 'users own their quota'
  ) then
    create policy "users own their quota"
      on public.indexer_quota for all
      using (auth.uid() = user_id)
      with check (auth.uid() = user_id);
  end if;
end $$;

create index if not exists indexer_quota_user_id_idx on public.indexer_quota (user_id);

-- Server-only atomic counters for expensive backend actions.
create table if not exists public.rate_limit_counters (
  user_id uuid not null references auth.users on delete cascade, tool text not null, action text not null,
  window_start timestamptz not null, request_count integer not null default 1 check (request_count > 0),
  primary key (user_id, tool, action, window_start)
);
alter table public.rate_limit_counters enable row level security;
revoke all on table public.rate_limit_counters from anon, authenticated;
grant all on table public.rate_limit_counters to service_role;

create or replace function public.check_rate_limit(p_user_id uuid, p_tool text, p_action text, p_limit integer, p_window_seconds integer default 600)
returns table (allowed boolean, current_count integer, retry_after_seconds integer)
language plpgsql security invoker set search_path = ''
as $$
declare v_now timestamptz := clock_timestamp(); v_window_start timestamptz; v_count integer;
begin
  if p_limit < 1 or p_window_seconds < 1 then raise exception 'Rate-limit values must be positive'; end if;
  v_window_start := to_timestamp(floor(extract(epoch from v_now) / p_window_seconds) * p_window_seconds);
  insert into public.rate_limit_counters as counters (user_id, tool, action, window_start, request_count)
  values (p_user_id, p_tool, p_action, v_window_start, 1)
  on conflict (user_id, tool, action, window_start) do update set request_count = counters.request_count + 1
  returning request_count into v_count;
  return query select v_count <= p_limit, v_count, greatest(1, ceil(extract(epoch from (v_window_start + make_interval(secs => p_window_seconds) - v_now)))::integer);
end;
$$;
revoke all on function public.check_rate_limit(uuid, text, text, integer, integer) from public, anon, authenticated;
grant execute on function public.check_rate_limit(uuid, text, text, integer, integer) to service_role;
