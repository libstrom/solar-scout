import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY") || "";
const WEBHOOK_SECRET = Deno.env.get("NOTYFILE_WEBHOOK_SECRET") || "";

const ALERT_EMAILS = [
  "linus.bergstrom@enspectaenergi.se",
  "fenomenetmusic@gmail.com",
];

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);

Deno.serve(async (req) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }

  // Optional shared secret — set NOTYFILE_WEBHOOK_SECRET in Supabase if Notyfile supports it
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

  // Notyfile may wrap customer under a "customer" key or send it flat
  const customer = (body.customer as Record<string, unknown>) ?? body;

  const street = String(customer.customer_adress ?? customer.address ?? "").trim();
  if (!street) {
    console.log("[notyfile-webhook] Payload har ingen adress:", JSON.stringify(body).slice(0, 300));
    return json({ ok: true, matched: false, reason: "no_address" });
  }

  const zip = String(customer.customer_postal_code ?? customer.postnummer ?? "").trim();
  const city = String(customer.customer_city ?? customer.city ?? "").trim();
  const incomingAddress = [street, zip, city].filter(Boolean).join(", ");

  const matches = await findMatchingLeads(street, zip, city);

  if (matches.length === 0) {
    console.log("[notyfile-webhook] Ingen träff för:", incomingAddress);
    return json({ ok: true, matched: false, address: incomingAddress });
  }

  const customerName = String(
    customer.customer_name ?? body.customer_name ?? "Okänd kund"
  );
  const customerId = String(customer.id ?? body.id ?? body.customer_id ?? "");

  for (const lead of matches) {
    await sendMatchEmail(customerName, customerId, incomingAddress, lead);
  }

  console.log(`[notyfile-webhook] ${matches.length} träff(ar) för: ${incomingAddress}`);
  return json({ ok: true, matched: true, count: matches.length, address: incomingAddress });
});

// ── address helpers ─────────────────────────────────────────────────────────

function normalize(s: string): string {
  return s
    .toLowerCase()
    .replace(/[,.\-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Extract "streetname housenumber" — drop zip/city so we compare street only */
function streetKey(s: string): string {
  // Take text before the first comma (or full string if no comma)
  return normalize(s.split(",")[0]);
}

async function findMatchingLeads(
  street: string,
  _zip: string,
  _city: string
): Promise<Record<string, unknown>[]> {
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
  if (inKey.length < 4) return []; // too short to match reliably

  return data.filter((lead) => {
    const leadKey = streetKey(String(lead.address ?? ""));
    return leadKey === inKey || leadKey.startsWith(inKey) || inKey.startsWith(leadKey);
  });
}

// ── email ────────────────────────────────────────────────────────────────────

async function sendMatchEmail(
  customerName: string,
  customerId: string,
  address: string,
  lead: Record<string, unknown>
) {
  if (!RESEND_API_KEY) {
    console.error("[notyfile-webhook] RESEND_API_KEY saknas — mail skickas ej");
    return;
  }

  const mapsLink =
    lead.maps_url ||
    (lead.lat && lead.lng
      ? `https://maps.google.com/?q=${lead.lat},${lead.lng}`
      : null);

  const notyfileLink = customerId
    ? `https://app.notyfile.se/customers/${customerId}`
    : null;

  const rows = [
    ["Kund i Notyfile", customerName],
    ["Adress", address],
    ["Lead-status i Solar Scout", String(lead.status ?? "okänd")],
    mapsLink ? ["Satellitbild", `<a href="${mapsLink}">Google Maps</a>`] : null,
    notyfileLink
      ? ["Notyfile", `<a href="${notyfileLink}">Öppna kund</a>`]
      : null,
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
<p>En kund som just registrerats/uppdaterats i Notyfile har samma adress som ett lead i Solar Scout-databasen.</p>
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
      subject: `☀️ PROVISION — Notyfile-kund matchar Solar Scout: ${address}`,
      html,
    }),
  });

  if (!res.ok) {
    console.error(
      "[notyfile-webhook] Resend-fel:",
      res.status,
      await res.text().catch(() => "")
    );
  }
}

// ── util ─────────────────────────────────────────────────────────────────────

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
