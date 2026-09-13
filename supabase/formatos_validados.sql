-- Banco de formatos validados (saída do pipeline Nicho → Formato).
-- Aditivo: roda separado do schema.sql, não altera nenhuma tabela existente.
-- Ver prompts/prompt-orquestrador-nicho-formato.md (seção "Saída para o Supabase").

create table if not exists formatos_validados (
    id uuid primary key default gen_random_uuid(),
    account_id uuid references accounts(id) on delete cascade,
    nicho text not null,
    perfil text,
    perfil_url text,
    seguidores_faixa text,
    formato_nome text not null,
    tipo_conteudo text check (tipo_conteudo in ('reels', 'carrossel', 'estatico', 'story')),
    hook_tipo text,
    hook_exemplo text,
    estrutura text,
    duracao_estimada text,
    cta_tipo text,
    cta_exemplo text,
    cadencia_observada text,
    faceless_compat text check (faceless_compat in ('alta', 'media', 'baixa')),
    sinal_validacao text,
    evidencia_observada text,
    confianca text check (confianca in ('alta', 'media', 'baixa')),
    repetido_em_n_perfis integer,
    monetizacao_alvo text,
    esforco_producao text check (esforco_producao in ('baixo', 'medio', 'alto')),
    observado_em date,
    fonte_url text,
    created_at timestamptz not null default now()
);

create index if not exists formatos_validados_nicho_idx on formatos_validados (nicho);
create index if not exists formatos_validados_account_idx on formatos_validados (account_id);
