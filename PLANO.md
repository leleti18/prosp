# Sistema SDR com IA — Plano de Projeto

## Objetivo

Sistema para prospecção e qualificação automatizada de leads (negócios locais que precisam de serviços de automação, sites, social media, etc.), com enriquecimento e scoring por IA, geração de mensagem personalizada, e disparo via WhatsApp ou Instagram — com canal de disparo escolhível por conta (Meta API oficial ou Evolution API).

Uso inicial: pessoal (uma conta só). Arquitetura pensada desde já para multi-tenant, permitindo comercialização futura sem refatoração.

---

## APIs e contas necessárias

| # | Serviço | Função | Custo estimado |
|---|---------|--------|-----------------|
| 1 | Google Cloud + Places API | Busca de negócios (nome, telefone, site) | Crédito grátis mensal (~US$200), depois pay-as-you-go |
| 2 | OpenRouter | Enriquecimento, scoring, geração de mensagem (Haiku/Sonnet) | Centavos por lead |
| 3 | Supabase | Banco de leads, config de conta, histórico de mensagens | Grátis até certo volume |
| 4 | Meta Business Suite + WhatsApp Cloud API | Disparo oficial de WhatsApp (opcional) | Grátis até certo volume, depois por conversa |
| 5 | Evolution API (self-hosted) | Disparo via WhatsApp Web / QR code (opcional) | Custo de VPS (~US$5-10/mês) |
| 6 | Instagram Graph API (via Meta Business) | Disparo/leitura de Instagram Business | Grátis |
| 7 | dados.gov.br | Dump de CNPJ (dados abertos, sem API key) | Grátis |

**Para começar (mínimo viável):** Google Places API + Supabase + OpenRouter.
As demais (Meta API, Evolution API, CNPJ) podem ser plugadas depois de a Fase 1 estar validada.

---

## Fontes de leads

1. **Google Places API (oficial)** — Text Search por categoria + região → resultado já inclui `website`, `nationalPhoneNumber`, `rating`, `userRatingCount` via fieldMask. Regra do lead quente: campo `website` vazio ou ausente.
2. **Dados abertos CNPJ (Receita Federal)** — dump mensal em dados.gov.br, filtrado por CNAE + município + situação "ativa". Cruzar com Google Places para enriquecer e para cobrir empresas que não aparecem no Maps.
3. **Checagem pública de Instagram** — não fazer scraping em massa. Usar como camada de qualificação manual/leve, só para leads que já passaram pelo filtro de "sem site".
4. **Diretórios comerciais locais** (Guia Mais, associações comerciais) — scraping leve de páginas HTML públicas, baixo risco.

---

## Arquitetura por fases

### Fase 1 — Captação e banco ✅ (concluída)
- Schema Supabase: `accounts`, `leads`, `lead_sources` (`supabase/schema.sql`)
- Script Python: Google Places (Text Search com fieldMask) → filtra sem site → grava no Supabase (`scripts/capture_google_places.py`)
- Script separado (pendente): parser do CSV de CNPJ → filtra por CNAE/cidade → grava no Supabase (dedup por telefone/CNPJ)

### Fase 2 — Painel interno (Streamlit) ✅ (concluída)
- App Streamlit lendo direto do Supabase (`app.py`, reusa `scripts/config.py`)
- Tela de leads: listar, filtrar por status/categoria/cidade, busca por nome
- Visualização de detalhes do lead (dados do Google Places, score e motivo quando existirem)
- Cresce incrementalmente: Fase 3 adiciona coluna de score/motivo/dor; Fase 4 adiciona aprovação de draft de mensagem; Fase 6 adiciona status de envio/resposta
- Roda local (`python -m streamlit run app.py`), sem necessidade de deploy nem autenticação nesta fase (uso pessoal)

### Fase 3 — Enriquecimento e scoring ✅ (concluída)
- Agente (`anthropic/claude-haiku-4.5` via OpenRouter) lê os dados do lead → retorna JSON estruturado: `{score, motivo, dor_provavel}` (`scripts/score_leads.py`)
- Salva no Supabase junto ao lead, muda status para `enriched`
- Painel (Fase 2) já exibe score/motivo/dor por lead

### Fase 4 — Geração de mensagem ✅ (concluída)
- Agente (`anthropic/claude-haiku-4.5` via OpenRouter) recebe lead + score/motivo/dor + tom de voz da conta → gera texto de abordagem (`scripts/generate_messages.py`)
- Mensagem fica em status `draft_ready` até aprovação manual no painel
- Painel (`app.py`) ganha aba "Aprovacao de Mensagens": ver draft, editar, aprovar ou descartar

### Fase 5 — Adaptador de canal ✅ (concluída, sem teste com credenciais reais)
- Módulo `scripts/channel_adapter.py` com interface única: `send(account, to, message)`
- Implementações em `scripts/adapters/`: `meta_adapter.py` (texto livre ou template, conforme `whatsapp_config.template_name`) e `evolution_adapter.py` (schema v2 confirmado via doc oficial)
- Config por conta (`accounts.provider` + `accounts.whatsapp_config`) decide qual adaptador é usado
- Construção de request validada com mocks (URL/headers/body corretos nos 3 modos); envio real ainda não testado — falta credencial Meta (phone_number_id + access_token) ou instância Evolution API rodando

### Fase 6 — Disparo e resposta ✅ (concluída)
- `scripts/dispatch_messages.py`: envia leads com status `approved`, modo `--dry-run` por padrão, `--confirm` pra enviar de verdade; normaliza telefone BR pra formato E.164; grava `sent_at` e evento em `conversation_events`
- Webhook: Supabase Edge Function `supabase/functions/whatsapp-webhook` recebe evento `messages.upsert` da Evolution API, acha a conta pela instancia, acha o lead pelo telefone, classifica a resposta via OpenRouter (`interessado`/`nao_interessado`/`duvida`/`pedido_para_parar`/`fora_de_contexto`), grava em `conversation_events` e atualiza `leads.status` para `replied`
- Webhook configurado na instancia Evolution real (`prospec`) apontando pra function; testado com payload simulado de ponta a ponta (achou lead, classificou, atualizou status)
- Painel (`app.py`) ganhou aba "Respostas" (leads que responderam + historico de conversa) e o historico de conversa aparece tambem nos detalhes de qualquer lead na aba "Leads"

---

## Regras importantes (Meta)

- **WhatsApp Cloud API**: exige número business verificado. Fora da janela de 24h após o último contato do lead, só pode enviar mensagem via **template pré-aprovado** pela Meta.
- **Instagram Graph API**: só funciona para contas Business/Creator, e só responde dentro da janela de 24h após interação do usuário (like, comentário, DM).
- **Evolution API**: não tem essas restrições de janela/template, mas roda por fora do canal oficial — existe risco de bloqueio da conta de WhatsApp se detectado padrão de spam/bot. Precisa de rate-limit e "aquecimento" de número.

---

### Melhoria — Busca de e-mail (extracao de site, gratis) + suporte a Portugal
- Google Places nao tem campo de e-mail nem nocao de "RH" (isso e negocio/local, nao pessoa/cargo) — usuaria escolheu extrair email publico do site do proprio lead em vez de uma API paga tipo Hunter.io/Apollo.io.
- `scripts/find_emails.py`: busca e-mail na home + paginas comuns de contato (`/contato`, `/contact`, `/sobre`, etc) via regex, filtrando falsos positivos (ex: `logo@2x.png` de srcset de imagem), priorizando emails com palavras-chave tipo "rh"/"hr"/"contato" quando ha mais de um candidato.
- `leads.email` novo. Nova aba "Buscar E-mails" em Capturar Leads: lista leads com site e sem email, busca em lote.
- Busca no Google ganhou seletor de regiao/idioma (pt-BR / pt-PT / en) — antes estava fixo em pt-BR, o que nao e ideal pra buscas em Portugal.
- CSV de importacao tambem aceita coluna `email` agora.
- **Bug real encontrado e corrigido durante o teste**: a aba CSV tinha varios `return` que saiam da funcao inteira `page_capture()`, nao so daquela aba — isso silenciosamente impedia a aba "Buscar E-mails" (adicionada depois no codigo) de renderizar na maioria das vezes. Corrigido extraindo cada aba pra sua propria funcao (`render_csv_import_tab`, `render_email_finder_tab`), onde `return` so sai da aba, nao da pagina toda.
- Testado de ponta a ponta com lead real (mock na chamada de rede, logica de extracao/priorizacao validada com HTML sintetico incluindo o falso-positivo de imagem).

### Correcao — Erros de envio ficavam invisiveis
- Bug: `st.error()` mostrado dentro do loop de envio em lote era apagado pelo `st.rerun()` logo em seguida, entao a usuaria nunca via o motivo da falha.
- Corrigido: resultados do lote (sucesso/falha por lead) agora ficam em `st.session_state` e sao exibidos apos o rerun.
- Nova checagem previa (`check_channel_connected`): antes de tentar enviar, confere se a instancia Evolution esta com `state == "open"`; se nao, avisa claramente pra reconectar via Canais em vez de tentar enviar e falhar.
- `describe_send_error()` traduz o erro mais comum da Evolution API (`"exists": false` — numero nao tem WhatsApp, comum em telefones vindos do Google Places que sao fixo) pra uma mensagem clara e acionavel, em vez do JSON cru.

### Melhoria — Suporte a leads de pessoa fisica (ex: lista de maternidade)
- `leads.name` agora aceita nulo (nem toda pessoa fisica importada tem nome); `leads.lead_type` (`empresa`/`pessoa`) e `accounts.person_offer_description` sao novos.
- Pessoa fisica **nao passa** pela etapa de Enriquecer Leads (sem score/service_type) — vai direto de `new` pra "Aprovacao e Envio" com um card simplificado (sem metrica de score, sem seletor de servico).
- `scripts/generate_messages.py` ganhou uma trilha de prompt separada pra pessoa fisica, focada no tema configurado em `accounts.person_offer_description` (maternidade / educacao infantil / homeschool por padrao) — usa o nome quando existe, saudacao neutra quando nao existe (nunca inventa nome).
- Importacao CSV (`Capturar Leads`) ganhou selecao de "Tipo de lead": pra pessoa fisica, telefone e obrigatorio e nome e opcional; pra empresa, nome continua obrigatorio.
- Nova secao "Configuracoes da conta" na pagina Canais: tom de voz, contato alternativo e a oferta pra pessoa fisica ficam editaveis ali (antes so dava pra setar via script).
- Filtro "Tipo de lead" (Empresa/Pessoa fisica) adicionado na aba Leads, e coluna "Tipo" na tabela.
- Testado de ponta a ponta com 2 leads reais (um com nome, um sem) — mensagens geradas corretamente diferenciadas, sem inventar nome quando ausente.

### Melhoria — Redesign visual (tema escuro, preto e verde)
- `.streamlit/config.toml`: tema mudou de claro/roxo pra escuro (`base = "dark"`) com verde (`#22C55E`) como cor primaria, fundo quase preto.
- Paleta dos chips de status na tabela de Leads (pandas Styler) redesenhada pra fundo escuro: verde predominante nas etapas de progresso (enriquecido/aprovado/enviado/respondeu, cada um com um tom diferente de verde/teal), amarelo pro draft pendente, vermelho so pro descartado, cinza pro novo — mantendo distincao semantica em vez de tudo verde.
- CSS leve injetado (`inject_custom_css()`): cantos arredondados e destaque verde ao passar o mouse nos cards (`st.container(border=True)`), botoes com cantos arredondados e brilho verde sutil, botao primario com glow verde.

### Melhoria — Conectar numero via QR Code direto no painel
- `scripts/evolution_instance.py`: funcoes pra consultar estado da instancia (`get_connection_state`), criar uma nova (`create_instance`) e buscar QR code de uma existente desconectada (`get_qrcode`), tudo via API real da Evolution.
- Pagina "Canais": ao preencher base URL/API key/instancia do provider Evolution, um botao "Gerar QR Code / verificar conexao" checa se a instancia ja existe e esta aberta, cria uma nova se nao existir, ou busca QR pra reconectar uma existente — mostra o QR como imagem na pagina, com botao pra confirmar conexao depois de escanear.
- Testado com mocks nos 3 cenarios (instancia nova, existente desconectada, ja conectada); a leitura de estado foi validada contra a instancia real "prospec" (so leitura, sem alterar a conexao — gerar QR nela desconectaria o WhatsApp real ja funcionando, entao esse teste fica pra quando houver uma instancia nova de verdade).

### Melhoria — Multiplos canais (numeros) + envio em lote
- Nova tabela `channels`: uma conta pode ter varios canais (numeros/instancias WhatsApp conectados), cada um com seu proprio `provider` + `config`. `channel_adapter.send()` agora recebe um canal, nao mais a conta inteira — `accounts.whatsapp_config` foi migrado pro primeiro canal ("Canal principal") e fica como legado.
- Nova pagina "Canais": lista canais cadastrados (ativar/desativar), e formulario pra adicionar um novo (campos mudam conforme o provider escolhido — Evolution API ou Meta Cloud API).
- Pagina "Aprovacao e Envio": a secao de aprovados ganhou checkbox por lead, selecao do canal (numero) a usar, e um campo de intervalo entre mensagens (padrao 4s) pra reduzir risco de bloqueio por padrao de bot — motivado pelo alerta ja existente no brief sobre esse risco na Evolution API. Um botao unico envia todos os selecionados em sequencia com o intervalo escolhido.
- `scripts/dispatch_messages.py` (CLI) tambem atualizado pra usar `--channel-id` opcional em vez de assumir a conta.
- Testado com mocks: selecao de canal, contagem de selecionados no label do botao, e chamada de envio com o canal/telefone/mensagem corretos.

### Melhoria — Importar leads de outras fontes via CSV
- Pagina "Capturar Leads" agora tem duas abas internas: "Buscar no Google" (como antes) e "Importar CSV" (nova), pra centralizar leads vindos de qualquer outra fonte (planilha, outro sistema) no mesmo funil.
- Modelo de CSV pra download com as colunas esperadas (nome obrigatoria; telefone, website, endereco, cidade, estado, categoria, cnpj, rating, avaliacoes opcionais).
- Leitura tolerante a encoding (utf-8-sig / cp1252 / latin-1) e delimitador (`,` ou `;`), comum em CSV exportado de Excel no Brasil.
- Dedup por telefone (`upsert on_conflict=account_id,phone`) — reimportar o mesmo CSV atualiza em vez de duplicar. Mesmo filtro "somente sem site" da captura via Google, aplicado aqui tambem.
- Testado com CSV real em memoria (2 linhas, 1 com site e 1 sem) — filtrou e importou corretamente so a linha sem site.

### Melhoria — Etapa de enriquecimento visivel no painel
- Nova pagina "Enriquecer Leads" (entre Capturar e Leads no menu): mostra os leads com status `new`, deixa escolher quantos processar, e roda a IA (`scripts/score_leads.py`, funcao `score_lead()` reaproveitada) mostrando o resultado (score, tag de servico, dor provavel) por lead conforme processa.
- Antes disso so existia via `python scripts/score_leads.py` no terminal, sem nenhuma tela — por isso a etapa "sumia" do fluxo visto pela usuaria.
- Testado com um lead real ("Mecanica Automotiva J.A") via clique no botao: score 7, tag "site", dor especifica gerada corretamente.

### Melhoria — Navegacao por paginas de verdade (nao abas) + envio direto no painel
- Trocado `st.tabs` por `st.navigation`/`st.Page`: no Streamlit, `st.tabs` executa o codigo de TODAS as abas a cada rerun, entao a sidebar de filtros (definida "dentro" da aba Leads) aparecia fixa em qualquer aba. Com paginas de verdade, so o codigo da pagina ativa roda, entao a sidebar de filtros so existe na pagina Leads.
- Menu reordenado na ordem do fluxo real: Dashboard -> Capturar Leads -> Leads -> Aprovacao e Envio -> Respostas.
- Pagina "Aprovacao e Envio" ganhou uma segunda secao: leads ja aprovados aparecem com um botao "Enviar agora" que dispara de verdade via `channel_adapter.send()` (mesma logica do `dispatch_messages.py`), atualiza status pra `sent` e grava em `conversation_events` — antes isso so existia via linha de comando.
- Cada pagina e uma funcao Python (`page_dashboard`, `page_capture`, etc.) no mesmo `app.py`, nao arquivos separados — simplicidade mantida.

### Melhoria — Captura de leads ao vivo pelo painel
- Nova aba "Capturar Leads" em `app.py`: campos de categoria (texto livre) + cidade + max resultados + toggle "so sem site", com botao que chama a API real do Google Places (reaproveitando `scripts/capture_google_places.py`) e grava direto no Supabase.
- Deixa claro na UI que isso e uma busca nova (custo real de API), diferente do filtro da aba Leads (que so filtra o que ja foi capturado, sem custo).
- Testado de ponta a ponta com busca real ("petshop em Curitiba, PR") — 20 encontrados, 5 sem site, gravados corretamente.

### Melhoria — Tag de serviço + mensagem diferenciada + CTA de contato alternativo
- `leads.service_type` (site / automacao / social_media / outro): a IA sugere automaticamente na Fase 3 (`scripts/score_leads.py`), com base na dor provavel do lead — cada lead recebe UM servico principal.
- `accounts.alt_contact`: numero de telefone configurado por conta, usado como CTA leve e opcional no fim da mensagem ("pode responder aqui ou chamar no numero X").
- `scripts/generate_messages.py` agora tem uma mensagem especifica por tipo de servico (nao generica), e a funcao `generate_message()` e reaproveitada pelo painel.
- Painel (`app.py`): aba "Aprovacao de Mensagens" ganhou selectbox pra escolher/trocar o servico manualmente + botao "Gerar/Regerar mensagem" que chama a IA de novo com o servico escolhido. Filtro por servico na aba "Leads". Badge de servico nos cards do funil e no detalhe do lead.
- Testado de ponta a ponta: regeneracao manual (trocando pra "automacao") e tagueamento automatico em 2 leads novos (um virou "site", outro "social_media" — mensagens saem de fato diferentes).

## Decisões de escopo tomadas

- **Frontend**: painel interno em Streamlit (não um app web completo tipo Next.js), lendo direto do Supabase. Prioriza velocidade de construção para uso pessoal; não nasce pronto pra multi-tenant/comercialização — se isso vier a ser necessário, reavaliar migração para app web com autenticação nessa altura.
- **Credenciais**: chaves de API vivem em `.env` (fora de versionamento), nunca em texto puro solto no projeto.

## Recomendação de execução

Construir e validar **uma fase por vez**, testando com dados reais antes de avançar.
Fase 1 concluída e testada. Próxima: Fase 2 (painel Streamlit).
