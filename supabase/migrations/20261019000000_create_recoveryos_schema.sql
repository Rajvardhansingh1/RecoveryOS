-- RecoveryOS schema. Mirrors backend/app/db/tables.py (used for SQLite tests).
-- RLS is enabled on every table with no policies: the Supabase REST API exposes nothing,
-- and only the backend's direct database role can read or write.

create table public.runs (
  id          varchar(36) primary key,
  status      varchar(32) not null,
  repo        varchar(200) not null,
  faults      jsonb not null default '[]'::jsonb,
  error_code  varchar(64),
  created_at  timestamptz not null,
  updated_at  timestamptz not null
);

create table public.steps (
  id               bigint generated always as identity primary key,
  run_id           varchar(36) not null references public.runs(id) on delete cascade,
  name             varchar(64) not null,
  status           varchar(32) not null,
  attempts         integer not null default 0,
  produced_by      varchar(32),
  provider         varchar(64),
  model            varchar(200),
  fallback_reason  varchar(200),
  llm_attempts     integer not null default 0,
  tokens           integer not null default 0,
  output           jsonb,
  idempotency_key  varchar(64),
  error_code       varchar(64),
  started_at       timestamptz,
  finished_at      timestamptz,
  constraint uq_steps_run_name unique (run_id, name)
);

create table public.checkpoints (
  id          bigint generated always as identity primary key,
  run_id      varchar(36) not null references public.runs(id) on delete cascade,
  seq         integer not null,
  step_name   varchar(64) not null,
  state_hash  varchar(64) not null,
  state       jsonb not null,
  created_at  timestamptz not null,
  constraint uq_checkpoints_run_seq unique (run_id, seq)
);

create table public.approvals (
  id          varchar(36) primary key,
  run_id      varchar(36) not null references public.runs(id) on delete cascade,
  plan_hash   varchar(64) not null,
  status      varchar(32) not null,
  decided_by  varchar(100),
  created_at  timestamptz not null,
  decided_at  timestamptz,
  expires_at  timestamptz
);
create index approvals_run_idx on public.approvals (run_id, created_at desc);

create table public.audit_events (
  id         bigint generated always as identity primary key,
  run_id     varchar(36) not null references public.runs(id) on delete cascade,
  seq        integer not null,
  ts         timestamptz not null,
  event      varchar(64) not null,
  step_name  varchar(64),
  detail     jsonb not null default '{}'::jsonb,
  constraint uq_audit_run_seq unique (run_id, seq)
);

create table public.idempotency_keys (
  key        varchar(64) primary key,
  run_id     varchar(36) not null references public.runs(id) on delete cascade,
  step_name  varchar(64) not null,
  result     jsonb not null,
  created_at timestamptz not null
);

-- The simulated outside world used by the mock GitHub adapter.
create table public.remote_effects (
  id         bigint generated always as identity primary key,
  run_id     varchar(36) not null,
  key        varchar(64) not null,
  action     varchar(64) not null,
  kind       varchar(32) not null,
  payload    jsonb not null,
  created_at timestamptz not null
);
create index remote_effects_key_idx on public.remote_effects (key);
create index remote_effects_run_idx on public.remote_effects (run_id);

-- Audit rows are append-only: block updates and deletes except through run cascade.
create function public.audit_events_no_update() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'audit_events is append-only';
end $$;
create trigger audit_events_no_update before update on public.audit_events
  for each row execute function public.audit_events_no_update();

alter table public.runs enable row level security;
alter table public.steps enable row level security;
alter table public.checkpoints enable row level security;
alter table public.approvals enable row level security;
alter table public.audit_events enable row level security;
alter table public.idempotency_keys enable row level security;
alter table public.remote_effects enable row level security;

revoke all on all tables in schema public from anon, authenticated;
