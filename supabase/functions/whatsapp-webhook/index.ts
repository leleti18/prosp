// Webhook que recebe eventos da Evolution API (mensagens recebidas do lead),
// classifica a resposta via OpenRouter, e atualiza o Supabase (CRM leve).
//
// Configurar na Evolution API: webhook da instancia apontando para a URL
// desta function, escutando o evento "messages.upsert".

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const OPENROUTER_API_KEY = Deno.env.get("OPENROUTER_API_KEY")!;
const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
const MODEL = "anthropic/claude-haiku-4.5";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

function onlyDigits(value: string): string {
  return (value ?? "").replace(/\D/g, "");
}

function extractJson(content: string): string {
  const match = content.match(/\{[\s\S]*\}/);
  if (!match) throw new Error(`Nenhum JSON encontrado na resposta: ${content}`);
  return match[0];
}

async function classifyReply(messageText: string): Promise<string> {
  const response = await fetch(OPENROUTER_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${OPENROUTER_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: MODEL,
      messages: [
        {
          role: "system",
          content:
            'Classifique a resposta de um lead de prospeccao B2B em uma destas ' +
            'categorias exatas: "interessado", "nao_interessado", "duvida", ' +
            '"pedido_para_parar", "fora_de_contexto". Responda SOMENTE um JSON: ' +
            '{"classificacao": "<categoria>"}',
        },
        { role: "user", content: messageText },
      ],
      response_format: { type: "json_object" },
      temperature: 0,
    }),
  });
  const data = await response.json();
  const content = data.choices[0].message.content;
  const parsed = JSON.parse(extractJson(content));
  return parsed.classificacao;
}

Deno.serve(async (req) => {
  const body = await req.json();

  if (body.event !== "messages.upsert") {
    return new Response("ignorado (evento nao tratado)", { status: 200 });
  }

  const data = body.data;
  if (data?.key?.fromMe) {
    return new Response("ignorado (eco de mensagem enviada por nos)", { status: 200 });
  }

  const remoteJid: string = data?.key?.remoteJid ?? "";
  const senderPhone = onlyDigits(remoteJid.split("@")[0]);
  const messageText: string =
    data?.message?.conversation ?? data?.message?.extendedTextMessage?.text ?? "";

  if (!senderPhone || !messageText) {
    return new Response("sem telefone ou texto na mensagem", { status: 200 });
  }

  const instanceName: string = body.instance;
  const { data: account } = await supabase
    .from("accounts")
    .select("id")
    .eq("whatsapp_config->>instance", instanceName)
    .single();

  if (!account) {
    return new Response(`conta nao encontrada para instancia ${instanceName}`, { status: 200 });
  }

  const { data: leads } = await supabase
    .from("leads")
    .select("id, phone")
    .eq("account_id", account.id)
    .not("phone", "is", null);

  const lead = (leads ?? []).find(
    (l) => onlyDigits(l.phone).slice(-8) === senderPhone.slice(-8),
  );

  if (!lead) {
    return new Response(`lead nao encontrado para o telefone ${senderPhone}`, { status: 200 });
  }

  const classification = await classifyReply(messageText);

  await supabase.from("conversation_events").insert({
    lead_id: lead.id,
    direction: "inbound",
    content: messageText,
    classification,
  });

  await supabase
    .from("leads")
    .update({ status: "replied", replied_at: new Date().toISOString() })
    .eq("id", lead.id);

  return new Response("ok", { status: 200 });
});
