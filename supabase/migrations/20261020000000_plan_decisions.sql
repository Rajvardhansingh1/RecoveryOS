-- Owner suggestions and plan revisions (the plan can be revised until the owner decides).
-- RLS on, no policies, API roles revoked, as for every other table.

create table public.suggestions (
  id          bigint generated always as identity primary key,
  run_id      varchar(36) not null references public.runs(id) on delete cascade,
  seq         integer not null,
  text        varchar(1000) not null,
  created_at  timestamptz not null,
  constraint uq_suggestions_run_seq unique (run_id, seq)
);

create table public.plan_revisions (
  id          bigint generated always as identity primary key,
  run_id      varchar(36) not null references public.runs(id) on delete cascade,
  revision    integer not null,
  plan_hash   varchar(64) not null,
  plan        jsonb not null,
  created_at  timestamptz not null,
  constraint uq_plan_revisions_run_rev unique (run_id, revision)
);

alter table public.suggestions enable row level security;
alter table public.plan_revisions enable row level security;

revoke all on public.suggestions, public.plan_revisions from anon, authenticated;
