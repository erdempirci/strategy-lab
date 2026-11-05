-- Strategy Lab · Masa (trading desk) journal
-- Run once in Supabase: SQL Editor → New query → paste → Run.
-- Only the API talks to these tables, with the service-role key (server side).
-- RLS is on with no policies, so the public anon key can read or write nothing.

create table if not exists desk_settings (
  key         text primary key,
  value       jsonb not null,
  updated_at  timestamptz not null default now()
);

create table if not exists desk_strategies (
  id             uuid primary key default gen_random_uuid(),
  name           text not null,
  symbol         text not null,
  timeframe      text not null,
  spec           jsonb not null,
  stop_atr_mult  double precision not null,
  take_profit_rr double precision,
  verdict        text not null check (verdict in ('robust', 'promising')),
  discovery      jsonb not null default '{}'::jsonb,
  active         boolean not null default true,
  activated_at   timestamptz not null,
  created_at     timestamptz not null default now()
);

create table if not exists desk_decisions (
  id             uuid primary key default gen_random_uuid(),
  strategy_id    uuid not null references desk_strategies(id) on delete cascade,
  strategy_name  text,
  symbol         text not null,
  timeframe      text not null,
  signal_time    timestamptz not null,
  verdict        text not null check (verdict in ('AL', 'BEKLE', 'GEÇ')),
  risk_status    text,
  late_bars      integer not null default 0,
  volume_ratio   double precision,
  plan           jsonb,
  roles          jsonb not null,
  trigger        text,
  decided_at     timestamptz not null default now(),
  unique (strategy_id, signal_time)
);

create table if not exists desk_trades (
  id            uuid primary key default gen_random_uuid(),
  strategy_id   uuid not null references desk_strategies(id) on delete cascade,
  kind          text not null check (kind in ('desk', 'shadow')),
  signal_time   timestamptz not null,
  entry_time    timestamptz not null,
  entry_price   double precision not null,
  exit_time     timestamptz,
  exit_price    double precision,
  exit_reason   text,
  r_multiple    double precision,
  return_pct    double precision not null,
  stop_price    double precision,
  target_price  double precision,
  created_at    timestamptz not null default now(),
  unique (strategy_id, kind, entry_time)
);

create table if not exists desk_events (
  id          uuid primary key default gen_random_uuid(),
  kind        text not null,
  dedupe_key  text unique,
  payload     jsonb not null,
  created_at  timestamptz not null default now()
);

create index if not exists desk_decisions_time on desk_decisions (signal_time desc);
create index if not exists desk_events_kind_time on desk_events (kind, created_at desc);

alter table desk_settings   enable row level security;
alter table desk_strategies enable row level security;
alter table desk_decisions  enable row level security;
alter table desk_trades     enable row level security;
alter table desk_events     enable row level security;
