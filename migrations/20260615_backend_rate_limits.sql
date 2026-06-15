create table if not exists public.rate_limit_counters (
  user_id uuid not null references auth.users on delete cascade,
  tool text not null,
  action text not null,
  window_start timestamptz not null,
  request_count integer not null default 1 check (request_count > 0),
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
