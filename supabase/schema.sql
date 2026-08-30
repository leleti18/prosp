create extension if not exists pgcrypto;

create table accounts (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    provider text not null default 'evolution' check (provider in ('meta', 'evolution')),
    whatsapp_config jsonb not null default '{}'::jsonb,
    instagram_config jsonb not null default '{}'::jsonb,
    tone_of_voice text,
    alt_contact text,
    person_offer_description text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table channels (
    id uuid primary key default gen_random_uuid(),
    account_id uuid not null references accounts(id) on delete cascade,
    name text not null,
    provider text not null check (provider in ('meta', 'evolution')),
    config jsonb not null default '{}'::jsonb,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create index channels_account_idx on channels (account_id);

create table leads (
    id uuid primary key default gen_random_uuid(),
    account_id uuid not null references accounts(id) on delete cascade,
    name text,
    lead_type text not null default 'empresa' check (lead_type in ('empresa', 'pessoa')),
    phone text,
    email text,
    website text,
    formatted_address text,
    city text,
    state text,
    category text,
    rating numeric,
    user_ratings_total integer,
    google_place_id text,
    cnpj text,
    status text not null default 'new' check (status in (
        'new', 'enriched', 'draft_ready', 'approved', 'sent', 'replied', 'discarded'
    )),
    score integer,
    motivo text,
    dor_provavel text,
    service_type text check (service_type in ('site', 'automacao', 'social_media', 'outro')),
    message_draft text,
    message_status text,
    sent_at timestamptz,
    replied_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (account_id, google_place_id),
    unique (account_id, phone)
);

create index leads_account_status_idx on leads (account_id, status);

create table lead_sources (
    id uuid primary key default gen_random_uuid(),
    lead_id uuid not null references leads(id) on delete cascade,
    source_type text not null check (source_type in ('google_places', 'cnpj', 'directory', 'manual')),
    source_ref text,
    raw_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index lead_sources_lead_idx on lead_sources (lead_id);

create table conversation_events (
    id uuid primary key default gen_random_uuid(),
    lead_id uuid not null references leads(id) on delete cascade,
    direction text not null check (direction in ('outbound', 'inbound')),
    content text not null,
    classification text,
    created_at timestamptz not null default now()
);

create index conversation_events_lead_idx on conversation_events (lead_id);
