alter table public.user_settings
  add column if not exists gsc_auth_method text not null default 'service_account';

alter table public.user_settings
  drop constraint if exists user_settings_gsc_auth_method_check;

alter table public.user_settings
  add constraint user_settings_gsc_auth_method_check
  check (gsc_auth_method in ('service_account', 'google_oauth'));

-- Runtime-only shared GSC OAuth connection used by Meta.
-- OAuth state/callback ownership remains in the FAQ backend.
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
