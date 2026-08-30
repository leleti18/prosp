import base64
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
from capture_google_places import fetch_all_places, save_leads, to_lead_row  # noqa: E402
from channel_adapter import send as channel_send  # noqa: E402
from config import GOOGLE_PLACES_API_KEY, get_supabase_client  # noqa: E402
from dispatch_messages import normalize_phone_br  # noqa: E402
from evolution_instance import (  # noqa: E402
    create_instance,
    extract_qrcode_base64,
    get_connection_state,
    get_qrcode,
)
from find_emails import find_email_for_website  # noqa: E402
from generate_messages import generate_message  # noqa: E402
from score_leads import score_lead  # noqa: E402

st.set_page_config(page_title="SDR - Leads", page_icon=":material/target:", layout="wide")


def _auth_credentials():
    email = os.environ.get("AUTH_EMAIL") or st.secrets.get("AUTH_EMAIL")
    password = os.environ.get("AUTH_PASSWORD") or st.secrets.get("AUTH_PASSWORD")
    return email, password


def require_login():
    if st.session_state.get("authenticated"):
        return

    auth_email, auth_password = _auth_credentials()
    if not auth_email or not auth_password:
        st.error(
            "Login nao configurado: defina AUTH_EMAIL e AUTH_PASSWORD no .env "
            "(local) ou em Settings > Secrets (Streamlit Cloud)."
        )
        st.stop()

    st.title("Login")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar")

    if submitted:
        if email.strip().lower() == auth_email.strip().lower() and password == auth_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Email ou senha incorretos.")

    st.stop()


STATUS_ORDER = ["new", "enriched", "draft_ready", "approved", "sent", "replied", "discarded"]

SERVICE_META = {
    "site": {"label": "Site", "color": "blue"},
    "automacao": {"label": "Automacao", "color": "violet"},
    "social_media": {"label": "Social Media", "color": "orange"},
    "outro": {"label": "Outro", "color": "gray"},
}

STATUS_META = {
    "new": {"label": "Novo", "color": "gray", "hex": ("#1E2420", "#9CA8A0")},
    "enriched": {"label": "Enriquecido", "color": "blue", "hex": ("#0F2E22", "#34D399")},
    "draft_ready": {"label": "Draft pronto", "color": "orange", "hex": ("#2E2810", "#FBBF24")},
    "approved": {"label": "Aprovado", "color": "violet", "hex": ("#15301F", "#4ADE80")},
    "sent": {"label": "Enviado", "color": "blue", "hex": ("#0E2E2A", "#2DD4BF")},
    "replied": {"label": "Respondeu", "color": "green", "hex": ("#0E3319", "#22C55E")},
    "discarded": {"label": "Descartado", "color": "red", "hex": ("#301616", "#F87171")},
}

CLASSIFICATION_META = {
    "interessado": "green",
    "nao_interessado": "red",
    "duvida": "orange",
    "pedido_para_parar": "red",
    "fora_de_contexto": "gray",
}


def status_label(status):
    return STATUS_META.get(status, {}).get("label", status or "-")


def service_label(service_type):
    if service_type == "sem_tag":
        return "Sem tag ainda"
    return SERVICE_META.get(service_type, {}).get("label", service_type or "-")


def lead_display_name(lead):
    return lead.get("name") or f"(sem nome · {lead.get('phone') or 'sem telefone'})"


def describe_send_error(exception):
    detail = str(exception)
    response = getattr(exception, "response", None)
    if response is None:
        return detail

    try:
        body = response.json()
    except Exception:
        try:
            detail = response.text or detail
        except Exception:
            pass
        return detail

    messages = (body.get("response") or {}).get("message")
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, dict) and item.get("exists") is False:
                number = item.get("number", "esse numero")
                return (
                    f"O numero {number} nao tem WhatsApp cadastrado (a Evolution API "
                    f"confirmou isso). Nao da pra enviar mensagem pra esse contato por "
                    f"aqui — considere descartar o lead ou buscar outro meio de contato."
                )

    return body.get("error") or json.dumps(body, ensure_ascii=False)


def check_channel_connected(channel):
    """Para canais Evolution, confere se a instancia esta com o WhatsApp conectado.
    Retorna (ok, mensagem). Para outros providers, sempre retorna ok=True (nao ha
    essa nocao de conexao pra checar previamente)."""
    if channel.get("provider") != "evolution":
        return True, ""

    config = channel.get("config") or {}
    try:
        state = get_connection_state(
            config.get("base_url"), config.get("api_key"), config.get("instance")
        )
    except Exception as e:
        return False, (
            f"Nao consegui verificar a conexao do canal '{channel['name']}': "
            f"{describe_send_error(e)}"
        )

    if not state or state.get("instance", {}).get("state") != "open":
        return False, (
            f"O canal '{channel['name']}' parece desconectado. Va em Canais e clique em "
            f"'Gerar QR Code / verificar conexao' pra reconectar antes de enviar."
        )
    return True, ""


def read_csv_flexible(uploaded_file):
    raw_bytes = uploaded_file.read()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(encoding)
            return pd.read_csv(io.StringIO(text), sep=None, engine="python")
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    raise ValueError("Nao foi possivel ler o arquivo (encoding/formato desconhecido).")


def clean_csv_value(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def safe_float(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


CUSTOM_CSS = """
<style>
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 12px !important;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: #22C55E !important;
    box-shadow: 0 0 0 1px rgba(34, 197, 94, 0.25);
}
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {
    border-radius: 10px !important;
    transition: transform 0.1s ease, box-shadow 0.15s ease;
}
.stButton > button:hover, .stFormSubmitButton > button:hover, .stDownloadButton > button:hover {
    box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.35);
}
button[kind="primary"] {
    box-shadow: 0 0 12px rgba(34, 197, 94, 0.35);
}
h1, h2, h3 {
    letter-spacing: -0.01em;
}
</style>
"""


def inject_custom_css():
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def decode_qr_image(qr_base64_field):
    if qr_base64_field.startswith("data:image"):
        qr_base64_field = qr_base64_field.split(",", 1)[1]
    return base64.b64decode(qr_base64_field)


def safe_int(value):
    try:
        if value is None or pd.isna(value):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=30)
def load_account(account_id):
    supabase = get_supabase_client()
    result = supabase.table("accounts").select("*").eq("id", account_id).single().execute()
    return result.data


@st.cache_data(ttl=30)
def load_accounts():
    supabase = get_supabase_client()
    result = supabase.table("accounts").select("*").execute()
    return result.data or []


@st.cache_data(ttl=30)
def load_channels(account_id):
    supabase = get_supabase_client()
    result = (
        supabase.table("channels")
        .select("*")
        .eq("account_id", account_id)
        .eq("is_active", True)
        .order("created_at")
        .execute()
    )
    return result.data or []


@st.cache_data(ttl=30)
def load_all_channels(account_id):
    supabase = get_supabase_client()
    result = (
        supabase.table("channels")
        .select("*")
        .eq("account_id", account_id)
        .order("created_at")
        .execute()
    )
    return result.data or []


@st.cache_data(ttl=30)
def load_leads():
    supabase = get_supabase_client()
    result = (
        supabase.table("leads")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


@st.cache_data(ttl=30)
def load_conversation_events():
    supabase = get_supabase_client()
    result = (
        supabase.table("conversation_events")
        .select("*")
        .order("created_at")
        .execute()
    )
    return result.data or []


def update_lead(lead_id, fields):
    supabase = get_supabase_client()
    supabase.table("leads").update(fields).eq("id", lead_id).execute()
    st.cache_data.clear()


def render_conversation(lead_id, conversation_events):
    lead_events = [e for e in conversation_events if e["lead_id"] == lead_id]
    if not lead_events:
        return
    st.markdown("**Historico de conversa**")
    for event in lead_events:
        if event["direction"] == "outbound":
            st.markdown(f":blue-badge[Enviamos] {event['content']}")
        else:
            color = CLASSIFICATION_META.get(event.get("classification"), "gray")
            classification = event.get("classification") or "sem classificacao"
            st.markdown(f":{color}-badge[Lead respondeu · {classification}] {event['content']}")


def page_dashboard():
    st.title("Dashboard")
    st.caption("Visao geral do funil de prospeccao (CRM).")

    leads = load_leads()

    if not leads:
        st.info("Nenhum lead ainda. Va em 'Capturar Leads' pra buscar os primeiros.")
        return

    counts = {status: 0 for status in STATUS_ORDER}
    for lead in leads:
        if lead.get("status") in counts:
            counts[lead["status"]] += 1

    metric_cols = st.columns(len(STATUS_ORDER))
    for col, status in zip(metric_cols, STATUS_ORDER):
        with col:
            st.metric(STATUS_META[status]["label"], counts[status])

    st.divider()
    st.caption("Cada coluna e uma etapa do funil. Os leads aparecem aqui assim que mudam de status.")

    board_cols = st.columns(len(STATUS_ORDER))
    for col, status in zip(board_cols, STATUS_ORDER):
        meta = STATUS_META[status]
        with col:
            st.badge(meta["label"], color=meta["color"])
            stage_leads = [lead for lead in leads if lead.get("status") == status]
            for lead in stage_leads:
                with st.container(border=True):
                    st.markdown(f"**{lead_display_name(lead)}**")
                    st.caption(lead.get("city") or "sem cidade")
                    if lead.get("score") is not None:
                        st.caption(f"Score: {lead['score']}")
                    if lead.get("service_type"):
                        service_meta = SERVICE_META.get(lead["service_type"], {})
                        st.badge(
                            service_meta.get("label", lead["service_type"]),
                            color=service_meta.get("color", "gray"),
                        )


def page_capture():
    st.title("Capturar Leads")

    accounts = load_accounts()
    if not accounts:
        st.error("Nenhuma conta cadastrada. Rode scripts/create_account.py primeiro.")
        return

    if len(accounts) == 1:
        account_id = accounts[0]["id"]
    else:
        account_id = st.selectbox(
            "Conta",
            [a["id"] for a in accounts],
            format_func=lambda aid: next(a["name"] for a in accounts if a["id"] == aid),
        )

    tab_google, tab_csv, tab_emails = st.tabs([
        ":material/search: Buscar no Google",
        ":material/upload_file: Importar CSV",
        ":material/alternate_email: Buscar E-mails",
    ])

    with tab_google:
        st.caption(
            "Isso chama a API do Google de verdade e pode gerar custo (dentro do credito "
            "gratis mensal). Cada busca aqui e uma consulta nova, nao um filtro."
        )

        if not GOOGLE_PLACES_API_KEY:
            st.error("GOOGLE_PLACES_API_KEY nao configurada no .env. Configure antes de buscar.")
        else:
            with st.form("captura_form"):
                category_input = st.text_input(
                    "Categoria do negocio",
                    placeholder='Ex: "petshop", "clinica odontologica", "advogado"',
                )
                city_input = st.text_input(
                    "Cidade", placeholder='Ex: "Curitiba, PR" ou "Lisboa, Portugal"'
                )
                region_code = st.selectbox(
                    "Regiao/idioma da busca",
                    ["pt-BR", "pt-PT", "en"],
                    format_func=lambda c: {
                        "pt-BR": "Brasil (pt-BR)", "pt-PT": "Portugal (pt-PT)", "en": "Ingles (en)",
                    }[c],
                )
                max_results_input = st.number_input(
                    "Maximo de resultados", min_value=5, max_value=60, value=20, step=5
                )
                only_without_website = st.checkbox(
                    "Somente leads sem site (recomendado p/ WhatsApp)", value=True,
                    help=(
                        "Desmarque se voce quer leads QUE TEM site — por exemplo pra depois "
                        "buscar o e-mail deles na aba 'Buscar E-mails'."
                    ),
                )
                submitted = st.form_submit_button(
                    "Buscar no Google", icon=":material/search:", type="primary",
                    width="stretch",
                )

            if submitted:
                if not category_input or not city_input:
                    st.warning("Preencha categoria e cidade antes de buscar.")
                else:
                    query = f"{category_input} em {city_input}"
                    with st.spinner(f"Buscando '{query}' no Google Places..."):
                        places = fetch_all_places(query, max_results_input, region_code)
                        total_found = len(places)
                        if only_without_website:
                            places = [p for p in places if not p.get("websiteUri")]

                        raw_by_place_id = {p["id"]: p for p in places if p.get("id")}
                        lead_rows = [
                            to_lead_row(p, account_id, category_input) for p in places
                        ]
                        supabase = get_supabase_client()
                        saved = save_leads(supabase, lead_rows, raw_by_place_id)

                    st.success(
                        f"Encontrados: {total_found} · "
                        f"{'Sem site: ' + str(len(places)) + ' · ' if only_without_website else ''}"
                        f"Gravados/atualizados no Supabase: {len(saved)}"
                    )
                    st.cache_data.clear()

    with tab_csv:
        render_csv_import_tab(account_id)

    with tab_emails:
        render_email_finder_tab()


def render_csv_import_tab(account_id):
        st.caption(
            "Ja tem leads de outra fonte (planilha, outro sistema)? Importe aqui pra "
            "centralizar tudo no mesmo funil."
        )

        lead_type = st.selectbox(
            "Tipo de lead neste arquivo",
            ["empresa", "pessoa"],
            format_func=lambda t: "Empresa (negocio local)" if t == "empresa" else (
                "Pessoa fisica (ex: lista de maternidade)"
            ),
            key="csv_lead_type",
        )
        is_person_import = lead_type == "pessoa"

        if is_person_import:
            st.caption(
                "Pessoa fisica: **telefone** e obrigatorio, nome e opcional (algumas linhas "
                "podem nao ter nome). Nao passa pela etapa de enriquecimento por IA."
            )
        else:
            st.caption("Empresa: **nome** e obrigatorio.")

        template_df = pd.DataFrame([{
            "nome": "", "telefone": "", "email": "", "website": "", "endereco": "",
            "cidade": "", "estado": "", "categoria": "", "cnpj": "", "rating": "", "avaliacoes": "",
        }])
        st.download_button(
            "Baixar modelo CSV",
            data=template_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="modelo_leads.csv",
            mime="text/csv",
            icon=":material/download:",
        )
        st.caption(
            "Colunas aceitas: nome, telefone, email, website, endereco, cidade, "
            "estado, categoria, cnpj, rating, avaliacoes."
        )

        uploaded_file = st.file_uploader("Escolha um arquivo CSV", type=["csv"])

        only_without_website_csv = True
        if not is_person_import:
            only_without_website_csv = st.checkbox(
                "Somente importar leads sem site", value=True, key="csv_only_no_site"
            )

        if uploaded_file is None:
            return

        try:
            df = read_csv_flexible(uploaded_file)
        except Exception as e:
            st.error(f"Nao consegui ler o CSV: {e}")
            return

        df.columns = [str(c).strip().lower() for c in df.columns]
        if not is_person_import and "nome" not in df.columns:
            st.error("O CSV precisa ter uma coluna 'nome' pra leads do tipo empresa.")
            return
        if "telefone" not in df.columns:
            st.error("O CSV precisa ter uma coluna 'telefone'.")
            return

        st.caption(f"{len(df)} linha(s) no arquivo")
        st.dataframe(df, width="stretch", hide_index=True)

        if not st.button(
            "Importar leads do CSV", icon=":material/upload:", type="primary", width="stretch"
        ):
            return

        rows = df.to_dict("records")
        if only_without_website_csv and "website" in df.columns:
            rows = [r for r in rows if not clean_csv_value(r.get("website"))]

        lead_rows = []
        skipped = 0
        for row in rows:
            name = clean_csv_value(row.get("nome"))
            phone = clean_csv_value(row.get("telefone"))

            if is_person_import:
                if not phone:
                    skipped += 1
                    continue
            else:
                if not name:
                    skipped += 1
                    continue

            lead_rows.append({
                "account_id": account_id,
                "lead_type": lead_type,
                "name": name,
                "phone": phone,
                "email": clean_csv_value(row.get("email")),
                "website": clean_csv_value(row.get("website")),
                "formatted_address": clean_csv_value(row.get("endereco")),
                "city": clean_csv_value(row.get("cidade")),
                "state": clean_csv_value(row.get("estado")),
                "category": clean_csv_value(row.get("categoria")),
                "cnpj": clean_csv_value(row.get("cnpj")),
                "rating": safe_float(row.get("rating")),
                "user_ratings_total": safe_int(row.get("avaliacoes")),
            })

        if not lead_rows:
            st.warning(
                "Nenhuma linha valida pra importar (verifique a coluna "
                + ("'telefone'." if is_person_import else "'nome'.")
            )
            return

        supabase = get_supabase_client()
        result = (
            supabase.table("leads")
            .upsert(lead_rows, on_conflict="account_id,phone")
            .execute()
        )
        saved = result.data or []

        source_rows = [
            {"lead_id": lead["id"], "source_type": "manual", "raw_data": {}}
            for lead in saved
        ]
        if source_rows:
            supabase.table("lead_sources").insert(source_rows).execute()

        st.success(
            f"Importados/atualizados: {len(saved)}"
            + (f" · Ignorados: {skipped}" if skipped else "")
        )
        st.cache_data.clear()


def render_email_finder_tab():
        st.caption(
            "Busca e-mail publico no site dos leads que TEM website e ainda nao tem e-mail "
            "cadastrado. Extrai da home e de paginas comuns de contato via regex simples — "
            "nao usa nenhuma API paga, entao pode nao achar (nem todo site publica e-mail)."
        )

        all_leads = load_leads()
        candidates = [
            lead for lead in all_leads if lead.get("website") and not lead.get("email")
        ]

        st.caption(f"{len(candidates)} lead(s) com site e sem e-mail cadastrado")

        if not candidates:
            st.info(
                "Nenhum lead com site pendente. Na aba 'Buscar no Google', desmarque "
                "'Somente leads sem site' pra capturar leads que tem site."
            )
        else:
            preview_df = pd.DataFrame([
                {"Nome": lead_display_name(lead), "Website": lead.get("website")}
                for lead in candidates
            ])
            st.dataframe(preview_df, width="stretch", hide_index=True)

            email_limit = st.number_input(
                "Quantos processar agora", min_value=1, max_value=len(candidates),
                value=min(20, len(candidates)), key="email_limit",
            )

            if st.button(
                "Buscar e-mails", icon=":material/alternate_email:", type="primary",
                width="stretch",
            ):
                supabase = get_supabase_client()
                results = []
                progress = st.progress(0.0)
                status_area = st.empty()

                for i, lead in enumerate(candidates[:email_limit]):
                    status_area.text(f"Verificando {lead['website']}...")
                    try:
                        email = find_email_for_website(lead["website"])
                    except Exception:
                        email = None
                    if email:
                        supabase.table("leads").update(
                            {"email": email}
                        ).eq("id", lead["id"]).execute()
                        results.append({"Nome": lead_display_name(lead), "Email": email})
                    else:
                        results.append(
                            {"Nome": lead_display_name(lead), "Email": "nao encontrado"}
                        )
                    progress.progress((i + 1) / email_limit)

                status_area.empty()
                st.cache_data.clear()
                st.success(f"{len(results)} lead(s) processado(s).")
                st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)


def page_enrich():
    st.title("Enriquecer Leads")
    st.caption(
        "A IA analisa cada lead novo e atribui um score (1-10), o motivo, a dor provavel "
        "e a tag de servico (site / automacao / social media / outro). Isso e o que "
        "alimenta a mensagem personalizada na etapa seguinte."
    )

    leads = load_leads()
    pending = [
        lead for lead in leads
        if lead.get("status") == "new" and lead.get("lead_type", "empresa") == "empresa"
    ]

    st.caption(f"{len(pending)} lead(s) aguardando enriquecimento")
    st.caption(
        "Leads do tipo 'pessoa fisica' nao passam por essa etapa — vao direto pra "
        "'Aprovacao e Envio'."
    )

    if not pending:
        st.info("Nenhum lead de empresa novo. Va em 'Capturar Leads' pra buscar mais.")
        return

    preview_df = pd.DataFrame([
        {
            "Nome": lead_display_name(lead),
            "Categoria": lead.get("category"),
            "Cidade": lead.get("city"),
        }
        for lead in pending
    ])
    st.dataframe(preview_df, width="stretch", hide_index=True)

    limit = st.number_input(
        "Quantos processar agora", min_value=1, max_value=len(pending),
        value=min(20, len(pending)),
    )

    if not st.button(
        "Enriquecer leads pendentes",
        icon=":material/auto_awesome:", type="primary", width="stretch",
    ):
        return

    supabase = get_supabase_client()
    results = []
    progress = st.progress(0.0)
    status_area = st.empty()

    for i, lead in enumerate(pending[:limit]):
        status_area.text(f"Analisando {lead_display_name(lead)}...")
        try:
            result = score_lead(lead)
            supabase.table("leads").update({
                "score": result["score"],
                "motivo": result["motivo"],
                "dor_provavel": result["dor_provavel"],
                "service_type": result["service_type"],
                "status": "enriched",
            }).eq("id", lead["id"]).execute()
            results.append({
                "Nome": lead_display_name(lead),
                "Score": result["score"],
                "Servico": service_label(result["service_type"]),
                "Dor provavel": result["dor_provavel"],
            })
        except Exception as e:
            results.append({
                "Nome": lead_display_name(lead), "Score": "erro", "Servico": "-",
                "Dor provavel": str(e),
            })
        progress.progress((i + 1) / limit)

    status_area.empty()
    st.cache_data.clear()
    st.success(f"{len(results)} lead(s) processado(s).")
    st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)


def page_leads():
    st.title("Leads")

    leads = load_leads()
    conversation_events = load_conversation_events()

    if not leads:
        st.info("Nenhum lead ainda. Va em 'Capturar Leads' pra buscar os primeiros.")
        return

    services = sorted({lead.get("service_type") or "sem_tag" for lead in leads})

    with st.sidebar:
        st.header("Filtros")
        with st.form("filtros_form"):
            selected_statuses = st.multiselect(
                "Status (etapa no funil)",
                STATUS_ORDER,
                default=STATUS_ORDER,
                format_func=status_label,
                help=(
                    "Em que ponto do funil o lead esta: Novo (recem capturado) -> "
                    "Enriquecido (score calculado) -> Draft pronto (mensagem gerada) -> "
                    "Aprovado (voce revisou e liberou o envio) -> Enviado -> Respondeu, "
                    "ou Descartado. Ver tambem o Dashboard."
                ),
            )
            selected_types = st.multiselect(
                "Tipo de lead", ["empresa", "pessoa"], default=["empresa", "pessoa"],
                format_func=lambda t: "Pessoa fisica" if t == "pessoa" else "Empresa",
            )
            selected_services = st.multiselect(
                "Servico a oferecer", services, default=services, format_func=service_label
            )
            category_search = st.text_input("Categoria (digite qualquer coisa)")
            city_search = st.text_input("Cidade (digite qualquer coisa)")
            name_search = st.text_input("Buscar por nome")
            st.form_submit_button(
                "Buscar", icon=":material/search:", type="primary", width="stretch"
            )

    filtered = [
        lead
        for lead in leads
        if lead.get("status") in selected_statuses
        and lead.get("lead_type", "empresa") in selected_types
        and (lead.get("service_type") or "sem_tag") in selected_services
        and category_search.lower() in (lead.get("category") or "").lower()
        and city_search.lower() in (lead.get("city") or "").lower()
        and name_search.lower() in (lead.get("name") or "").lower()
    ]

    st.caption(f"{len(filtered)} lead(s) apos filtro")

    table_df = pd.DataFrame([
        {
            "Nome": lead_display_name(lead),
            "Tipo": "Pessoa" if lead.get("lead_type") == "pessoa" else "Empresa",
            "Telefone": lead.get("phone"),
            "Email": lead.get("email"),
            "Cidade": lead.get("city"),
            "Estado": lead.get("state"),
            "Categoria": lead.get("category"),
            "Rating": lead.get("rating"),
            "Avaliacoes": lead.get("user_ratings_total"),
            "Status": status_label(lead.get("status")),
            "Servico": service_label(lead.get("service_type") or "sem_tag"),
            "Score": lead.get("score"),
        }
        for lead in filtered
    ])

    status_style_map = {meta["label"]: meta["hex"] for meta in STATUS_META.values()}

    def style_status(value):
        bg, fg = status_style_map.get(value, ("#eceef1", "#4b5563"))
        return f"background-color: {bg}; color: {fg}; border-radius: 4px; font-weight: 600;"

    styled_df = table_df.style.map(style_status, subset=["Status"])

    st.dataframe(
        styled_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Rating": st.column_config.ProgressColumn(
                "Rating", min_value=0, max_value=5, format="%.1f"
            ),
        },
    )

    st.divider()
    st.subheader("Detalhes do lead")

    lead_options = {
        f"{lead_display_name(lead)} ({lead.get('city') or 'sem cidade'})": lead
        for lead in filtered
    }

    if not lead_options:
        return

    selected_label = st.selectbox("Selecione um lead", list(lead_options.keys()))
    lead = lead_options[selected_label]

    with st.container(border=True):
        top_col, badge_col = st.columns([4, 1])
        with top_col:
            st.markdown(f"### {lead_display_name(lead)}")
        with badge_col:
            meta = STATUS_META.get(lead.get("status"), {})
            st.badge(meta.get("label", lead.get("status")), color=meta.get("color", "gray"))
            if lead.get("service_type"):
                service_meta = SERVICE_META.get(lead["service_type"], {})
                st.badge(
                    service_meta.get("label", lead["service_type"]),
                    color=service_meta.get("color", "gray"),
                )

        col1, col2 = st.columns(2)
        with col1:
            st.write("**Telefone:**", lead.get("phone"))
            st.write("**Email:**", lead.get("email") or "-")
            st.write("**Endereco:**", lead.get("formatted_address"))
            st.write("**Website:**", lead.get("website") or "sem site")
            st.write("**Categoria:**", lead.get("category"))
            st.write("**Enviado em:**", lead.get("sent_at") or "-")
            st.write("**Respondido em:**", lead.get("replied_at") or "-")
        with col2:
            st.write("**Rating:**", lead.get("rating"))
            st.write("**Avaliacoes:**", lead.get("user_ratings_total"))
            st.write("**Score:**", lead.get("score") or "ainda nao calculado")
            st.write("**Motivo:**", lead.get("motivo") or "-")
            st.write("**Dor provavel:**", lead.get("dor_provavel") or "-")

        if lead.get("message_draft"):
            st.write("**Draft de mensagem:**")
            st.text_area(
                "Draft de mensagem",
                lead["message_draft"],
                disabled=True,
                label_visibility="collapsed",
                key=f"readonly_draft_{lead['id']}",
            )

        render_conversation(lead["id"], conversation_events)


def page_approval():
    st.title("Aprovacao e Envio")

    leads = load_leads()

    pending = [
        lead for lead in leads
        if lead.get("status") in ("enriched", "draft_ready")
        or (lead.get("status") == "new" and lead.get("lead_type") == "pessoa")
    ]
    ready_to_send = [lead for lead in leads if lead.get("status") == "approved"]

    st.subheader("Aguardando mensagem / aprovacao")
    st.caption(f"{len(pending)} lead(s) nesta etapa")

    if not pending:
        st.info(
            "Nenhum lead pendente. Empresas precisam passar por 'Enriquecer Leads' primeiro; "
            "pessoas fisicas aparecem aqui direto apos a captura/importacao."
        )

    service_options = list(SERVICE_META.keys())

    for lead in pending:
        is_person = lead.get("lead_type") == "pessoa"

        with st.container(border=True):
            header_col, score_col = st.columns([3, 1])
            with header_col:
                st.markdown(
                    f"**{lead_display_name(lead)}** — {lead.get('city') or 'sem cidade'}"
                )
                st.caption(lead.get("dor_provavel") or "")
            with score_col:
                if is_person:
                    st.badge("Pessoa fisica", color="violet")
                else:
                    st.metric("Score", lead.get("score") or "-")

            if is_person:
                selected_service = None
                if st.button(
                    "Gerar/Regerar mensagem",
                    key=f"generate_{lead['id']}",
                    icon=":material/auto_awesome:",
                ):
                    account = load_account(lead["account_id"])
                    with st.spinner("Gerando mensagem..."):
                        mensagem = generate_message(
                            lead,
                            tone_of_voice=account.get("tone_of_voice") if account else None,
                            service_type=None,
                            alt_contact=account.get("alt_contact") if account else None,
                            person_offer_description=(
                                account.get("person_offer_description") if account else None
                            ),
                        )
                    update_lead(
                        lead["id"],
                        {
                            "message_draft": mensagem,
                            "message_status": "draft",
                            "status": "draft_ready",
                        },
                    )
                    st.rerun()
            else:
                service_col, generate_col = st.columns([2, 1])
                with service_col:
                    current_service = lead.get("service_type") or "outro"
                    selected_service = st.selectbox(
                        "Servico a oferecer nesta mensagem",
                        service_options,
                        index=service_options.index(current_service)
                        if current_service in service_options
                        else service_options.index("outro"),
                        format_func=service_label,
                        key=f"service_{lead['id']}",
                    )
                with generate_col:
                    st.write("")
                    if st.button(
                        "Gerar/Regerar mensagem",
                        key=f"generate_{lead['id']}",
                        icon=":material/auto_awesome:",
                        width="stretch",
                    ):
                        account = load_account(lead["account_id"])
                        with st.spinner("Gerando mensagem..."):
                            mensagem = generate_message(
                                lead,
                                tone_of_voice=account.get("tone_of_voice") if account else None,
                                service_type=selected_service,
                                alt_contact=account.get("alt_contact") if account else None,
                            )
                        update_lead(
                            lead["id"],
                            {
                                "service_type": selected_service,
                                "message_draft": mensagem,
                                "message_status": "draft",
                                "status": "draft_ready",
                            },
                        )
                        st.rerun()

            if not lead.get("message_draft"):
                st.info("Ainda sem mensagem gerada. Clique em Gerar mensagem.")
                continue

            draft_key = f"draft_text_{lead['id']}"
            edited_text = st.text_area(
                "Mensagem",
                lead.get("message_draft") or "",
                key=draft_key,
                label_visibility="collapsed",
                height=120,
            )

            approve_col, discard_col, save_col = st.columns(3)
            with approve_col:
                if st.button(
                    "Aprovar", key=f"approve_{lead['id']}", type="primary",
                    icon=":material/check:", width="stretch",
                ):
                    update_lead(
                        lead["id"],
                        {
                            "message_draft": edited_text,
                            "message_status": "approved",
                            "status": "approved",
                        },
                    )
                    st.rerun()
            with discard_col:
                if st.button(
                    "Descartar", key=f"discard_{lead['id']}",
                    icon=":material/close:", width="stretch",
                ):
                    update_lead(
                        lead["id"],
                        {"message_status": "discarded", "status": "discarded"},
                    )
                    st.rerun()
            with save_col:
                if st.button(
                    "Salvar edicao", key=f"save_{lead['id']}",
                    icon=":material/save:", width="stretch",
                ):
                    update_lead(lead["id"], {"message_draft": edited_text})
                    st.rerun()

    st.divider()
    st.subheader("Aprovados, prontos pra enviar")
    st.caption(f"{len(ready_to_send)} lead(s) nesta etapa")

    if not ready_to_send:
        st.info("Nenhum lead aprovado no momento.")
        return

    if "bulk_send_results" in st.session_state:
        results = st.session_state.pop("bulk_send_results")
        for r in results:
            if r["ok"]:
                st.success(f"Enviado: {r['name']}")
            else:
                st.error(f"Falha ao enviar para {r['name']}: {r['error']}")

    account_id = ready_to_send[0]["account_id"]
    channels = load_channels(account_id)

    if not channels:
        st.error("Nenhum canal cadastrado. Va em 'Canais' pra cadastrar um numero antes de enviar.")
        return

    top_col1, top_col2 = st.columns([2, 1])
    with top_col1:
        selected_channel_id = st.selectbox(
            "Enviar pelo canal (numero)",
            [c["id"] for c in channels],
            format_func=lambda cid: next(c["name"] for c in channels if c["id"] == cid),
        )
    with top_col2:
        delay_seconds = st.number_input(
            "Intervalo entre mensagens (segundos)",
            min_value=0, max_value=60, value=4,
            help=(
                "Espera esse tempo entre um envio e outro no lote, pra reduzir risco de "
                "bloqueio do numero por padrao de bot."
            ),
        )
    selected_channel = next(c for c in channels if c["id"] == selected_channel_id)

    selected_lead_ids = []
    for lead in ready_to_send:
        with st.container(border=True):
            check_col, info_col = st.columns([1, 6])
            with check_col:
                checked = st.checkbox(
                    "Selecionar para envio",
                    key=f"select_send_{lead['id']}",
                    label_visibility="collapsed",
                )
            with info_col:
                st.markdown(
                    f"**{lead_display_name(lead)}** — {lead.get('city') or 'sem cidade'} · "
                    f"{lead.get('phone')}"
                )
            st.text_area(
                "Mensagem aprovada",
                lead.get("message_draft") or "",
                disabled=True,
                label_visibility="collapsed",
                key=f"approved_draft_{lead['id']}",
            )
            if checked:
                selected_lead_ids.append(lead["id"])

    st.caption(f"{len(selected_lead_ids)} selecionado(s)")

    if st.button(
        f"Enviar selecionados ({len(selected_lead_ids)}) via {selected_channel['name']}",
        icon=":material/send:", type="primary", width="stretch",
        disabled=not selected_lead_ids,
    ):
        connected, connection_message = check_channel_connected(selected_channel)
        if not connected:
            st.error(connection_message)
            return

        supabase = get_supabase_client()
        leads_by_id = {lead["id"]: lead for lead in ready_to_send}
        progress = st.progress(0.0)
        status_area = st.empty()
        results = []

        for i, lead_id in enumerate(selected_lead_ids):
            lead = leads_by_id[lead_id]
            to = normalize_phone_br(lead.get("phone"))
            name = lead_display_name(lead)
            status_area.text(f"Enviando para {name} ({to})...")
            try:
                channel_send(selected_channel, to, lead["message_draft"])
                supabase.table("leads").update({
                    "status": "sent",
                    "message_status": "sent",
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", lead["id"]).execute()
                supabase.table("conversation_events").insert({
                    "lead_id": lead["id"],
                    "direction": "outbound",
                    "content": lead["message_draft"],
                }).execute()
                results.append({"ok": True, "name": name})
            except Exception as e:
                results.append({"ok": False, "name": name, "error": describe_send_error(e)})
            progress.progress((i + 1) / len(selected_lead_ids))
            if delay_seconds > 0 and i < len(selected_lead_ids) - 1:
                time.sleep(delay_seconds)

        status_area.empty()
        st.cache_data.clear()
        st.session_state["bulk_send_results"] = results
        st.rerun()


def page_replies():
    st.title("Respostas")

    leads = load_leads()
    conversation_events = load_conversation_events()

    replied = [lead for lead in leads if lead.get("status") == "replied"]
    st.caption(f"{len(replied)} lead(s) que ja responderam")

    if not replied:
        st.info("Nenhuma resposta recebida ainda.")

    for lead in replied:
        with st.container(border=True):
            st.markdown(f"**{lead_display_name(lead)}** — {lead.get('city') or 'sem cidade'}")
            st.caption(f"Respondido em: {lead.get('replied_at') or '-'}")
            render_conversation(lead["id"], conversation_events)


def page_channels():
    st.title("Canais")
    st.caption(
        "Cada canal e um numero de WhatsApp conectado (via Meta ou Evolution API). "
        "Cadastre quantos precisar e escolha qual usar na hora de enviar."
    )

    accounts = load_accounts()
    if not accounts:
        st.error("Nenhuma conta cadastrada. Rode scripts/create_account.py primeiro.")
        return

    if len(accounts) == 1:
        account_id = accounts[0]["id"]
    else:
        account_id = st.selectbox(
            "Conta",
            [a["id"] for a in accounts],
            format_func=lambda aid: next(a["name"] for a in accounts if a["id"] == aid),
        )

    account = load_account(account_id)

    st.subheader("Configuracoes da conta")
    with st.form("account_settings_form"):
        tone_of_voice = st.text_input(
            "Tom de voz",
            value=(account.get("tone_of_voice") or "") if account else "",
            placeholder="Ex: descontraido e direto, sem formalidade excessiva",
        )
        alt_contact = st.text_input(
            "Contato alternativo (CTA nas mensagens)",
            value=(account.get("alt_contact") or "") if account else "",
            placeholder="Ex: 5541999999999",
        )
        person_offer_description = st.text_area(
            "Oferta para leads pessoa fisica",
            value=(account.get("person_offer_description") or "") if account else "",
            placeholder=(
                "Ex: material de educacao infantil e apoio para familias, incluindo quem "
                "considera ou pratica homeschool"
            ),
            help="Usado como base da mensagem gerada pra leads do tipo 'pessoa fisica'.",
        )
        if st.form_submit_button(
            "Salvar configuracoes", icon=":material/save:", type="primary", width="stretch"
        ):
            supabase = get_supabase_client()
            supabase.table("accounts").update({
                "tone_of_voice": tone_of_voice or None,
                "alt_contact": alt_contact or None,
                "person_offer_description": person_offer_description or None,
            }).eq("id", account_id).execute()
            st.cache_data.clear()
            st.success("Configuracoes salvas.")
            st.rerun()

    st.divider()

    channels = load_all_channels(account_id)

    st.subheader("Canais cadastrados")
    if not channels:
        st.info("Nenhum canal cadastrado ainda.")

    for channel in channels:
        with st.container(border=True):
            name_col, status_col, toggle_col = st.columns([3, 1, 1])
            with name_col:
                st.markdown(f"**{channel['name']}**")
                st.caption(f"Provider: {channel['provider']}")
            with status_col:
                st.badge(
                    "Ativo" if channel["is_active"] else "Inativo",
                    color="green" if channel["is_active"] else "gray",
                )
            with toggle_col:
                toggle_label = "Desativar" if channel["is_active"] else "Ativar"
                if st.button(toggle_label, key=f"toggle_{channel['id']}", width="stretch"):
                    supabase = get_supabase_client()
                    supabase.table("channels").update(
                        {"is_active": not channel["is_active"]}
                    ).eq("id", channel["id"]).execute()
                    st.cache_data.clear()
                    st.rerun()

    st.divider()
    st.subheader("Adicionar novo canal")

    provider = st.selectbox(
        "Provider", ["evolution", "meta"], key="new_channel_provider",
        format_func=lambda p: "Evolution API (self-hosted)" if p == "evolution" else "Meta WhatsApp Cloud API",
    )

    name = st.text_input("Nome do canal", placeholder='Ex: "Celular Vendas 2"')

    if provider == "evolution":
        base_url = st.text_input(
            "Base URL da instancia", placeholder="http://seu-servidor:8080"
        )
        api_key = st.text_input("API key", type="password")
        instance = st.text_input("Nome da instancia")

        if st.button(
            "Gerar QR Code / verificar conexao",
            icon=":material/qr_code:", width="stretch",
        ):
            if not base_url or not api_key or not instance:
                st.warning("Preencha base URL, API key e instancia primeiro.")
            else:
                st.session_state["evo_qr_base64"] = None
                st.session_state["evo_connected"] = False
                with st.spinner("Consultando instancia..."):
                    try:
                        state = get_connection_state(base_url, api_key, instance)
                    except Exception as e:
                        state = "error"
                        st.error(f"Erro ao consultar a instancia: {e}")

                    if state == "error":
                        pass
                    elif state and state.get("instance", {}).get("state") == "open":
                        st.session_state["evo_connected"] = True
                    else:
                        try:
                            if state is None:
                                result = create_instance(base_url, api_key, instance)
                            else:
                                result = get_qrcode(base_url, api_key, instance)
                            st.session_state["evo_qr_base64"] = extract_qrcode_base64(result)
                        except Exception as e:
                            st.error(f"Erro ao gerar QR code: {e}")

        if st.session_state.get("evo_connected"):
            st.success("Instancia conectada!")
        elif st.session_state.get("evo_qr_base64"):
            st.image(
                decode_qr_image(st.session_state["evo_qr_base64"]),
                caption="Escaneie no WhatsApp: Aparelhos conectados > Conectar um aparelho",
                width=300,
            )
            if st.button("Ja escaneei, verificar conexao", icon=":material/refresh:"):
                try:
                    state = get_connection_state(base_url, api_key, instance)
                    if state and state.get("instance", {}).get("state") == "open":
                        st.session_state["evo_connected"] = True
                        st.session_state["evo_qr_base64"] = None
                        st.rerun()
                    else:
                        st.info(
                            "Ainda nao conectado. Espere alguns segundos apos escanear "
                            "e tente de novo."
                        )
                except Exception as e:
                    st.error(f"Erro: {e}")
    else:
        phone_number_id = st.text_input("Phone Number ID (Meta)")
        access_token = st.text_input("Access Token (Meta)", type="password")
        template_name = st.text_input(
            "Nome do template aprovado (opcional)",
            help="Deixe vazio pra enviar como texto livre (so funciona dentro da janela de 24h).",
        )
        template_language = st.text_input("Idioma do template", value="pt_BR")

    if not st.button(
        "Salvar canal", icon=":material/add:", type="primary", width="stretch"
    ):
        return

    if not name:
        st.warning("De um nome pro canal.")
        return

    if provider == "evolution":
        if not base_url or not api_key or not instance:
            st.warning("Preencha base URL, API key e nome da instancia.")
            return
        config = {"base_url": base_url, "api_key": api_key, "instance": instance}
    else:
        if not phone_number_id or not access_token:
            st.warning("Preencha Phone Number ID e Access Token.")
            return
        config = {"phone_number_id": phone_number_id, "access_token": access_token}
        if template_name:
            config["template_name"] = template_name
            config["template_language"] = template_language or "pt_BR"

    supabase = get_supabase_client()
    supabase.table("channels").insert({
        "account_id": account_id, "name": name, "provider": provider, "config": config,
    }).execute()
    st.cache_data.clear()
    st.success(f"Canal '{name}' cadastrado.")
    st.session_state.pop("evo_qr_base64", None)
    st.session_state.pop("evo_connected", None)
    st.rerun()


if __name__ == "__main__":
    require_login()
    inject_custom_css()

    with st.sidebar:
        if st.button("Sair", icon=":material/logout:", width="stretch"):
            st.session_state["authenticated"] = False
            st.rerun()

    header_col, refresh_col = st.columns([5, 1])
    with header_col:
        st.caption("SDR com IA")
    with refresh_col:
        if st.button("Atualizar dados", icon=":material/refresh:", width="stretch"):
            st.cache_data.clear()

    navigation = st.navigation([
        st.Page(page_dashboard, title="Dashboard", icon=":material/dashboard:", default=True),
        st.Page(page_capture, title="Capturar Leads", icon=":material/travel_explore:"),
        st.Page(page_enrich, title="Enriquecer Leads", icon=":material/auto_awesome:"),
        st.Page(page_leads, title="Leads", icon=":material/list:"),
        st.Page(page_approval, title="Aprovacao e Envio", icon=":material/edit_note:"),
        st.Page(page_replies, title="Respostas", icon=":material/forum:"),
        st.Page(page_channels, title="Canais", icon=":material/smartphone:"),
    ])
    navigation.run()
