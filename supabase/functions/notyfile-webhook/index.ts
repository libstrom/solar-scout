import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY") || "";
const NOTYFILE_TOKEN = Deno.env.get("NOTYFILE_TOKEN") || "";
const WEBHOOK_SECRET = Deno.env.get("NOTYFILE_WEBHOOK_SECRET") || "";
const NOTYFILE_BASE = "https://api.notyfile.se/v1";

const ALERT_EMAILS = [
  "linus.bergstrom@enspectaenergi.se",
  "fenomenetmusic@gmail.com",
];

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);

Deno.serve(async (req) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  if (WEBHOOK_SECRET) {
    const sig =
      req.headers.get("X-Notyfile-Secret") ||
      req.headers.get("Authorization") ||
      "";
    if (!sig.includes(WEBHOOK_SECRET)) {
      return new Response("Unauthorized", { status: 401 });
    }
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return new Response("Invalid JSON", { status: 400 });
  }

  const eventType = String(body.event ?? body.event_type ?? body.type ?? "okänd");
  console.log("[notyfile-webhook] Event:", eventType, JSON.stringify(body).slice(0, 400));

  // Resolve customer object — payload may nest it, or we fetch it by id
  const customer = await resolveCustomer(body);
  if (!customer) {
    console.log("[notyfile-webhook] Kunde inte hitta kunddata i payload");
    return json({ ok: true, matched: false, reason: "no_customer" });
  }

  const street = String(customer.customer_adress ?? customer.address ?? "").trim();
  if (!street) {
    console.log("[notyfile-webhook] Kunden har ingen adress — customer_id:", customer.id);
    return json({ ok: true, matched: false, reason: "no_address" });
  }

  const zip = String(customer.customer_postal_code ?? customer.postnummer ?? "").trim();
  const city = String(customer.customer_city ?? customer.city ?? "").trim();
  const incomingAddress = [street, zip, city].filter(Boolean).join(", ");

  const matches = await findMatchingLeads(street);
  if (matches.length === 0) {
    console.log("[notyfile-webhook] Ingen träff för:", incomingAddress);
    return json({ ok: true, matched: false, address: incomingAddress });
  }

  const customerName = String(customer.customer_name ?? "Okänd kund");
  const customerId = String(customer.id ?? "");

  for (const lead of matches) {
    await sendMatchEmail(customerName, customerId, incomingAddress, eventType, lead);
  }

  console.log(`[notyfile-webhook] ${matches.length} träff(ar) för: ${incomingAddress}`);
  return json({ ok: true, matched: true, count: matches.length, address: incomingAddress });
});

// ── resolve customer ─────────────────────────────────────────────────────────

async function resolveCustomer(
  body: Record<string, unknown>
): Promise<Record<string, unknown> | null> {
  // 1. Payload contains inline customer object with address
  const inline =
    (body.customer as Record<string, unknown>) ??
    (body.deal?.customer as Record<string, unknown>) ??
    (body.meeting?.customer as Record<string, unknown>) ??
    null;

  if (inline?.customer_adress || inline?.address) return inline;

  // 2. Extract customer_id from various payload shapes and fetch from Notyfile
  const customerId =
    inline?.id ??
    body.customer_id ??
    (body.deal as Record<string, unknown>)?.customer_id ??
    (body.meeting as Record<string, unknown>)?.customer_id ??
    body.id;

  if (!customerId || !NOTYFILE_TOKEN) return inline ?? null;

  try {
    const res = await fetch(`${NOTYFILE_BASE}/customers/${customerId}`, {
      headers: { Authorization: `Bearer ${NOTYFILE_TOKEN}` },
    });
    if (!res.ok) return inline ?? null;
    const data = await res.json();
    // Notyfile wraps response: { results: { data: { ... } } }
    return data?.results?.data ?? data ?? null;
  } catch (err) {
    console.error("[notyfile-webhook] Kunde inte hämta kund:", err);
    return inline ?? null;
  }
}

// ── address matching ─────────────────────────────────────────────────────────

function normalize(s: string): string {
  return s
    .toLowerCase()
    .replace(/[,.\-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function streetKey(s: string): string {
  return normalize(s.split(",")[0]);
}

async function findMatchingLeads(street: string): Promise<Record<string, unknown>[]> {
  const { data, error } = await supabase
    .from("scout_leads")
    .select("id, address, status, lat, lng, maps_url")
    .not("address", "is", null)
    .neq("address", "");

  if (error || !data) {
    console.error("[notyfile-webhook] DB-fel:", error);
    return [];
  }

  const inKey = streetKey(street);
  if (inKey.length < 4) return [];

  return data.filter((lead) => {
    const leadKey = streetKey(String(lead.address ?? ""));
    return leadKey === inKey || leadKey.startsWith(inKey) || inKey.startsWith(leadKey);
  });
}

// ── email ────────────────────────────────────────────────────────────────────

const EVENT_LABELS: Record<string, string> = {
  "affär skapad": "Affär skapad",
  "möte skapad": "Möte skapad",
  deal_created: "Affär skapad",
  meeting_created: "Möte skapad",
};

async function sendMatchEmail(
  customerName: string,
  customerId: string,
  address: string,
  eventType: string,
  lead: Record<string, unknown>
) {
  if (!RESEND_API_KEY) {
    console.error("[notyfile-webhook] RESEND_API_KEY saknas");
    return;
  }

  const eventLabel = EVENT_LABELS[eventType.toLowerCase()] ?? eventType;
  const mapsLink =
    lead.maps_url ||
    (lead.lat && lead.lng ? `https://maps.google.com/?q=${lead.lat},${lead.lng}` : null);
  const notyfileLink = customerId ? `https://app.notyfile.se/customers/${customerId}` : null;

  const rows = [
    ["Händelse", eventLabel],
    ["Kund i Notyfile", customerName],
    ["Adress", address],
    ["Lead-status i Solar Scout", String(lead.status ?? "okänd")],
    mapsLink ? ["Satellitbild", `<a href="${mapsLink}">Google Maps</a>`] : null,
    notyfileLink ? ["Notyfile", `<a href="${notyfileLink}">Öppna kund</a>`] : null,
  ]
    .filter(Boolean)
    .map(
      (r) =>
        `<tr><td style="padding:6px 12px 6px 0;font-weight:bold;white-space:nowrap">${r![0]}</td>` +
        `<td style="padding:6px 0">${r![1]}</td></tr>`
    )
    .join("\n");

  const html = `
<h2 style="color:#f59e0b">☀️ Provision-alert: Solar Scout-lead matchar Notyfile-kund</h2>
<p>En kund i Notyfile har samma adress som ett lead i Solar Scout-databasen.</p>
<table style="border-collapse:collapse;font-family:sans-serif;font-size:14px">
${rows}
</table>
<p style="color:#6b7280;font-size:12px;margin-top:24px">Skickat automatiskt av Solar Scout &bull; Notyfile webhook-integration</p>
`;

  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: "Solar Scout <onboarding@resend.dev>",
      to: ALERT_EMAILS,
      subject: `☀️ PROVISION — ${eventLabel}: ${customerName} · ${address}`,
      html,
    }),
  });

  if (!res.ok) {
    console.error("[notyfile-webhook] Resend-fel:", res.status, await res.text().catch(() => ""));
  }
}

// ── util ─────────────────────────────────────────────────────────────────────

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
