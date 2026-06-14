create unique index if not exists jobs_one_active_per_user_tool_idx
on public.jobs (user_id, tool)
where status in ('pending', 'running', 'cancelling');
