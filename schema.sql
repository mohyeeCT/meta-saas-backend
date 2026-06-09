-- Run this in your Supabase SQL editor before deploying

-- Jobs table: stores job metadata and results
create table public.jobs (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid references auth.users not null,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now(),
  status        text default 'pending' check (status in ('pending', 'running', 'complete', 'failed')),
  name          text,
  settings      jsonb default '{}',
  rows          jsonb default '[]',
  results       jsonb default '[]',
  total_rows    integer default 0,
  completed_rows integer default 0,
  error         text
);

alter table public.jobs enable row level security;

create policy "Users can only access their own jobs"
  on public.jobs for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create index jobs_user_id_idx on public.jobs (user_id);
create index jobs_created_at_idx on public.jobs (created_at desc);


-- User settings table: stores GSC service account per user
create table public.user_settings (
  user_id               uuid primary key references auth.users,
  gsc_service_account   jsonb,
  updated_at            timestamptz default now()
);

alter table public.user_settings enable row level security;

create policy "Users can only access their own settings"
  on public.user_settings for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);


-- Brand profiles table: named sets of tone/messaging used across all tools
create table if not exists public.brand_profiles (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid references auth.users not null,
  name        text not null,
  data        jsonb default '{}',
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);

alter table public.brand_profiles enable row level security;

create policy "Users can only access their own brand profiles"
  on public.brand_profiles for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create index if not exists brand_profiles_user_id_idx on public.brand_profiles (user_id);



-- Job templates table
create table if not exists public.job_templates (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid references auth.users not null,
  tool        text not null,
  name        text not null,
  settings    jsonb default '{}',
  created_at  timestamptz default now()
);

alter table public.job_templates enable row level security;

create policy "Users can only access their own job templates"
  on public.job_templates for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create index if not exists job_templates_user_id_idx on public.job_templates (user_id);
