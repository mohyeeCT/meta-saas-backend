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
insert into public.user_credentials (user_id, provider_settings, gsc_service_account)
select user_id, jsonb_strip_nulls(jsonb_build_object('api_key', provider_settings -> 'api_key', 'dfs_password', provider_settings -> 'dfs_password', 'jina_api_key', provider_settings -> 'jina_api_key')), gsc_service_account
from public.user_settings
where gsc_service_account is not null or provider_settings ?| array['api_key', 'dfs_password', 'jina_api_key']
on conflict (user_id) do update set provider_settings = public.user_credentials.provider_settings || excluded.provider_settings, gsc_service_account = coalesce(excluded.gsc_service_account, public.user_credentials.gsc_service_account), updated_at = now();
update public.user_settings set provider_settings = provider_settings - 'api_key' - 'dfs_password' - 'jina_api_key', gsc_service_account = null, updated_at = now()
where gsc_service_account is not null or provider_settings ?| array['api_key', 'dfs_password', 'jina_api_key'];
