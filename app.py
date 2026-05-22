"""
Scout – Takidentifiering & Leadsgenerering
Linus Bergström
"""

import io
import os
import logging
import urllib.parse
import stripe
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from supabase import create_client, Client

try:
    import extra_streamlit_components as stx
    _COOKIES_AVAILABLE = True
except ImportError:
    _COOKIES_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [app] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("solar_scout.app")

# ── Konfiguration ─────────────────────────────────────────────────────────────

def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

SUPABASE_URL          = _secret("SUPABASE_URL")
SUPABASE_ANON_KEY     = _secret("SUPABASE_ANON_KEY")
STRIPE_SECRET_KEY     = _secret("STRIPE_SECRET_KEY")
STRIPE_PRICE_STARTER  = _secret("STRIPE_PRICE_STARTER")   # 499 kr/mån  – 1 seat
STRIPE_PRICE_TEAM     = _secret("STRIPE_PRICE_TEAM")       # 1 990 kr/mån – 5 seats
STRIPE_PRICE_GROWTH   = _secret("STRIPE_PRICE_GROWTH")     # 3 990 kr/mån – 15 seats
STRIPE_PRICE_PACK_BASIC   = _secret("STRIPE_PRICE_PACK_BASIC")   # 25-pack  1 250 kr
STRIPE_PRICE_PACK_PREMIUM = _secret("STRIPE_PRICE_PACK_PREMIUM") # 50-pack  3 750 kr
STRIPE_PRICE_PACK_PRO     = _secret("STRIPE_PRICE_PACK_PRO")     # 200-pack 12 000 kr
APP_URL               = _secret("APP_URL", "http://localhost:8501")
GOOGLE_API_KEY        = _secret("GOOGLE_API_KEY")
MAPBOX_TOKEN          = _secret("MAPBOX_TOKEN")

stripe.api_key = STRIPE_SECRET_KEY

# CSV-attribution-header — krav från OSM ODbL § 4.3 (attribution) och
# Lantmäteriets CC-BY-villkor. Se docs/adr/0001-osm-odbl-csv.md för tolkning.
CSV_ATTRIBUTION_HEADER = (
    "# Genererad av solar-scout · "
    "Geodata © Lantmäteriet, CC-BY 4.0\n"
    "# Innehåller OSM-data © OpenStreetMap-bidragsgivare, ODbL 1.0\n"
    "# https://www.openstreetmap.org/copyright · "
    "https://creativecommons.org/licenses/by/4.0/\n"
)


def _to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Leads")
        ws = writer.sheets["Leads"]
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = max(
                len(str(col[0].value or "")),
                max((len(str(c.value or "")) for c in col[1:]), default=0),
            ) + 4
    return buf.getvalue()


@st.cache_resource
def _anon_supabase() -> Client:
    """Process-wide anon client — only for unauthenticated ops (login, signup)."""
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def get_supabase() -> Client:
    """Return per-session authenticated Supabase client.

    Each Streamlit session gets its own client with set_session() called once
    in init_auth(). This prevents concurrent users from overwriting each
    other's auth state on the shared cache_resource client.
    """
    return st.session_state.get("_sb_user_client") or _anon_supabase()


def _sv_error(exc: Exception) -> str:
    """Översätt vanliga Supabase-felmeddelanden till svenska."""
    msg = str(exc).lower()
    if "email" in msg and ("password" in msg or "phone" in msg):
        return "Ange e-postadress och lösenord."
    if "invalid login credentials" in msg or "invalid_credentials" in msg:
        return "Fel e-postadress eller lösenord."
    if "email not confirmed" in msg:
        return "E-postadressen är inte bekräftad. Kolla din inkorg."
    if "user already registered" in msg or "already been registered" in msg:
        return "Det finns redan ett konto med den e-postadressen."
    if "password should be at least" in msg:
        return "Lösenordet måste vara minst 8 tecken."
    if "rate limit" in msg or "too many requests" in msg:
        return "För många försök — vänta en stund och försök igen."
    if "network" in msg or "connection" in msg:
        return "Nätverksfel — kontrollera din internetanslutning."
    return str(exc)


def _get_cookie_manager():
    return st.session_state.get("_cookie_manager")

# ── Auth ──────────────────────────────────────────────────────────────────────

def init_auth():
    """Återställ session från session_state eller browser-cookies.

    Ordning: session_state → cookies → None.
    Cookies överlever Railway-restarts; session_state gör det inte.
    """
    # Fast path: redan validerad denna session — returnera cachad användare utan
    # nätverksanrop. Radioknappar, flikar och andra widgets triggar reruns men
    # ska ALDRIG logga ut användaren.
    cached_user = st.session_state.get("_auth_user")
    if cached_user and st.session_state.get("access_token"):
        return cached_user

    access = st.session_state.get("access_token")
    refresh = st.session_state.get("refresh_token")

    # Fallback: läs från cookies om session_state är tom.
    # CookieManager behöver en extra render-cykel efter Railway-restart.
    if not access or not refresh:
        cm = _get_cookie_manager()
        if cm is not None:
            access = cm.get("access_token")
            refresh = cm.get("refresh_token")
        if not access or not refresh:
            attempts = st.session_state.get("_cookie_load_attempted", 0)
            if attempts < 3:
                st.session_state["_cookie_load_attempted"] = attempts + 1
                st.rerun()
            return None
    st.session_state.pop("_cookie_load_attempted", None)

    # Validera mot Supabase (körs bara vid första rendern eller efter token-refresh).
    user_sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    try:
        user_sb.auth.set_session(access, refresh)
        user = user_sb.auth.get_user().user
        st.session_state["_auth_user"]      = user
        st.session_state["_sb_user_client"] = user_sb
        return user
    except Exception:
        pass
    # Token expired — försök refresh
    try:
        resp = user_sb.auth.refresh_session(refresh)
        if resp.session and resp.user:
            st.session_state["access_token"]    = resp.session.access_token
            st.session_state["refresh_token"]   = resp.session.refresh_token
            st.session_state["_auth_user"]      = resp.user
            st.session_state["_sb_user_client"] = user_sb
            cm = _get_cookie_manager()
            if cm is not None:
                _exp = datetime.now() + timedelta(days=30)
                cm.set("access_token", resp.session.access_token, expires_at=_exp)
                cm.set("refresh_token", resp.session.refresh_token, expires_at=_exp)
            return resp.user
    except Exception:
        pass
    # Refresh misslyckades → riktig utloggning
    for k in ("access_token", "refresh_token", "_auth_user", "_sb_user_client"):
        st.session_state.pop(k, None)
    cm = _get_cookie_manager()
    if cm is not None:
        cm.remove("access_token")
        cm.remove("refresh_token")
    return None


def do_login(email: str, password: str):
    user_sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    resp = user_sb.auth.sign_in_with_password({"email": email, "password": password})
    user_sb.auth.set_session(resp.session.access_token, resp.session.refresh_token)
    st.session_state["access_token"]    = resp.session.access_token
    st.session_state["refresh_token"]   = resp.session.refresh_token
    st.session_state["_auth_user"]      = resp.user
    st.session_state["_sb_user_client"] = user_sb
    cm = _get_cookie_manager()
    if cm is not None:
        _exp = datetime.now() + timedelta(days=30)
        cm.set("access_token", resp.session.access_token, expires_at=_exp)
        cm.set("refresh_token", resp.session.refresh_token, expires_at=_exp)
    return resp.user


def do_signup(email: str, password: str):
    sb = get_supabase()
    resp = sb.auth.sign_up({"email": email, "password": password})
    user = resp.user
    try:
        sb.table("profiles").upsert({"user_id": str(user.id), "email": email}).execute()
    except Exception:
        pass
    return user


def do_logout():
    sb = get_supabase()
    try:
        sb.auth.sign_out()
    except Exception:
        pass
    for k in ("access_token", "refresh_token", "_auth_user", "_sb_user_client"):
        st.session_state.pop(k, None)
    cm = _get_cookie_manager()
    if cm is not None:
        cm.remove("access_token")
        cm.remove("refresh_token")

# ── Profil & subscription ─────────────────────────────────────────────────────

def get_profile(user_id: str) -> dict:
    sb = get_supabase()
    resp = sb.table("profiles").select("*").eq("user_id", user_id).maybe_single().execute()
    return resp.data or {}


def is_admin(profile: dict) -> bool:
    return profile.get("role") == "admin"

def has_access(profile: dict, lead_count: int = 0) -> bool:
    if is_admin(profile):
        return True
    if profile.get("scout_subscription_status") == "active":
        return True
    if profile.get("credits_balance", 0) > 0:
        return True
    return lead_count < 10

def add_credits(user_id: str, amount: int):
    """Add credits to user balance."""
    sb = get_supabase()
    try:
        resp = sb.table("profiles").select("credits_balance").eq("user_id", user_id).maybe_single().execute()
        current = (resp.data or {}).get("credits_balance", 0) or 0
        sb.table("profiles").update({"credits_balance": current + amount}).eq("user_id", user_id).execute()
    except Exception as exc:
        _log.error("add_credits failed user=%s: %s", user_id, exc)


def decrement_credits(user_id: str) -> int:
    """Decrement credits by 1. Returns new balance, or -1 on error."""
    sb = get_supabase()
    try:
        resp = sb.table("profiles").select("credits_balance").eq("user_id", user_id).maybe_single().execute()
        current = (resp.data or {}).get("credits_balance", 0) or 0
        new_bal = max(0, current - 1)
        sb.table("profiles").update({"credits_balance": new_bal}).eq("user_id", user_id).execute()
        return new_bal
    except Exception as exc:
        _log.error("decrement_credits failed user=%s: %s", user_id, exc)
        return -1

# ── Stripe ────────────────────────────────────────────────────────────────────

def create_checkout_url(email: str, user_id: str, price_id: str) -> str:
    session = stripe.checkout.Session.create(
        customer_email=email,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{APP_URL}?subscribed=true",
        cancel_url=f"{APP_URL}?canceled=true",
        metadata={"user_id": user_id},
        allow_promotion_codes=True,
        subscription_data={"trial_period_days": 14},
    )
    return session.url


def create_credit_checkout_url(email: str, user_id: str, price_id: str, credits: int) -> str:
    session = stripe.checkout.Session.create(
        customer_email=email,
        mode="payment",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{APP_URL}?paid_session={{CHECKOUT_SESSION_ID}}&credits={credits}",
        cancel_url=f"{APP_URL}?canceled=true",
        client_reference_id=user_id,
        metadata={"user_id": user_id, "credits": str(credits)},
    )
    return session.url


def create_portal_url(customer_id: str) -> str:
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=APP_URL,
    )
    return session.url

# ── E-post ────────────────────────────────────────────────────────────────────

def _send_meeting_email(address: str, note: str, lat: float | None, lng: float | None) -> bool:
    """Skicka mail till Linus när David bokar ett möte. Kräver RESEND_API_KEY i secrets."""
    api_key = _secret("RESEND_API_KEY")
    if not api_key:
        _log.warning("RESEND_API_KEY saknas — mail skickas ej")
        return False
    maps = f"https://maps.google.com/?q={lat},{lng}" if lat and lng else ""
    body = (
        f"David har bokat ett möte!\n\n"
        f"Adress: {address}\n"
        f"Notering: {note or '–'}\n"
        f"{('Karta: ' + maps) if maps else ''}\n\n"
        f"Logga in på solar-scout.streamlit.app för att se leadet."
    )
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from":    "Solar Scout <onboarding@resend.dev>",
                "to":      ["linus.bergstrom@enspectaenergi.se"],
                "subject": f"☀️ Möte bokat — {address}",
                "text":    body,
            },
            timeout=8,
        )
        resp.raise_for_status()
        _log.info("mötes-mail skickat för %s", address)
        return True
    except Exception as _e:
        _log.warning("mail misslyckades: %s", _e)
        return False


_LEAD_STATUSES = {
    "ej_kontaktad": "📋 Ej kontaktad",
    "kontaktad":    "📞 Kontaktad",
    "mote_bokat":   "📅 Möte bokat",
    "ej_intresserad": "❌ Ej intresserad",
    "kund":         "✅ Kund",
}

# ── Leads ─────────────────────────────────────────────────────────────────────

def save_lead(user_id: str, data: dict, profile: dict | None = None):
    sb = get_supabase()
    data["user_id"] = user_id
    sb.table("scout_leads").insert(data).execute()
    # Deduct 1 credit if user is on credit plan (not admin or active subscription)
    if profile is None:
        profile = get_profile(user_id)
    if isinstance(profile, dict):
        _bal = profile.get("credits_balance") or 0
        if (not is_admin(profile) and
                profile.get("scout_subscription_status") != "active" and
                _bal > 0):
            decrement_credits(user_id)


def load_leads(user_id: str, include_false_positives: bool = False) -> pd.DataFrame:
    sb = get_supabase()
    q = sb.table("scout_leads").select("*").eq("user_id", user_id)
    if not include_false_positives:
        q = q.eq("false_positive", False)
    resp = q.order("created_at", desc=True).execute()
    return pd.DataFrame(resp.data) if resp.data else pd.DataFrame()


def delete_lead(lead_id: int):
    sb = get_supabase()
    sb.table("scout_leads").delete().eq("id", lead_id).execute()


def confirm_lead(lead_id: int, confirmed: bool):
    sb = get_supabase()
    sb.table("scout_leads").update({"user_confirmed": confirmed}).eq("id", lead_id).execute()


def get_accuracy_stats(user_id: str) -> dict:
    sb = get_supabase()
    resp = (
        sb.table("scout_leads")
        .select("user_confirmed")
        .eq("user_id", user_id)
        .eq("scan_source", "ai")
        .execute()
    )
    rows = resp.data or []
    reviewed = [r for r in rows if r["user_confirmed"] is not None]
    confirmed = [r for r in reviewed if r["user_confirmed"] is True]
    return {
        "total_ai": len(rows),
        "reviewed": len(reviewed),
        "confirmed": len(confirmed),
        "denied": len(reviewed) - len(confirmed),
        "pct": round(len(confirmed) / len(reviewed) * 100) if reviewed else None,
    }

# ── Sidor ─────────────────────────────────────────────────────────────────────

def _handle_credit_redirect(user_id: str):
    """Called after login — if ?paid_session= is in URL, verify Stripe payment and add credits."""
    params = st.query_params
    session_id = params.get("paid_session")
    if not session_id or not STRIPE_SECRET_KEY:
        return
    credits_str = params.get("credits", "0")
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status == "paid":
            credits = int(credits_str)
            if credits > 0:
                add_credits(user_id, credits)
                st.success(f"✅ {credits} credits tillagda på ditt konto!")
    except Exception as exc:
        _log.error("credit_redirect failed: %s", exc)
    finally:
        st.query_params.pop("paid_session", None)
        st.query_params.pop("credits", None)


def page_auth():
    st.title("Scout")
    st.caption("Takidentifiering & Leadsgenerering · Linus Bergström")
    st.divider()

    tab_in, tab_up, tab_pw = st.tabs(["Logga in", "Skapa konto", "Glömt lösenord"])

    with tab_in:
        with st.form("login_form"):
            email    = st.text_input("E-postadress",  key="login_email")
            password = st.text_input("Lösenord", type="password", key="login_password")
            submitted = st.form_submit_button("Logga in", type="primary", use_container_width=True)
        # Patch autocomplete attributes so browsers offer to save/fill credentials.
        # components.html runs in an iframe and can reach window.parent.document.
        import streamlit.components.v1 as _stc
        _stc.html("""
        <script>
        (function() {
            function patch() {
                var p = window.parent.document;
                var em = p.querySelector('input[type="text"]');
                var pw = p.querySelector('input[type="password"]');
                if (em) { em.setAttribute('autocomplete', 'email'); em.setAttribute('name', 'email'); }
                if (pw) { pw.setAttribute('autocomplete', 'current-password'); pw.setAttribute('name', 'password'); }
            }
            patch();
            setTimeout(patch, 300);
        })();
        </script>
        """, height=0)
        if submitted:
            try:
                do_login(email, password)
                st.rerun()
            except Exception as e:
                st.error(_sv_error(e))

    with tab_up:
        with st.form("signup_form"):
            email    = st.text_input("E-postadress",  key="signup_email")
            password = st.text_input("Välj lösenord (minst 8 tecken)", type="password", key="signup_password")
            submitted = st.form_submit_button("Skapa konto", type="primary", use_container_width=True)
        if submitted:
            try:
                do_signup(email, password)
                st.success("Konto skapat! Logga in i fliken ovan.")
            except Exception as e:
                st.error(_sv_error(e))

    with tab_pw:
        st.caption("Vi skickar en länk till din e-post så att du kan sätta ett nytt lösenord.")
        with st.form("reset_form"):
            email = st.text_input("E-postadress")
            submitted = st.form_submit_button("Skicka återställningslänk", type="primary", use_container_width=True)
        if submitted:
            try:
                sb = get_supabase()
                sb.auth.reset_password_email(email, {"redirect_to": f"{APP_URL}?reset=true"})
                st.success("Länk skickad! Kolla din e-post (även skräppost).")
            except Exception as e:
                st.error(_sv_error(e))


def page_paywall(user, lead_count: int = 0):
    st.title("Scout")
    st.caption("Takidentifiering & Leadsgenerering · Linus Bergström")
    st.divider()

    if lead_count < 10:
        remaining = 10 - lead_count
        st.info(f"Du har {remaining} gratis leads kvar av 10. Köp ett credit-pack för fler leads.")
        st.progress(lead_count / 10)

    params = st.query_params

    if params.get("canceled") == "true":
        st.query_params.clear()
        st.warning("Betalningen avbröts — inget debiterades.")

    # Legacy subscription return
    if params.get("subscribed") == "true":
        profile = get_profile(str(user.id))
        if has_access(profile):
            st.query_params.clear()
            st.rerun()
        else:
            st.info("Betalning mottagen — aktiverar ditt konto (tar 10–30 sek)...")
            if st.button("Kontrollera igen", type="primary"):
                st.rerun()
            return

    st.markdown("### Köp credit-pack — betala per lead")
    st.caption("1 credit = 1 sparad lead. Credits förfaller inte. Ingen månadsavgift.")
    st.divider()

    packs = [
        {
            "name": "Basic",
            "credits": 25,
            "price": "1 250 kr",
            "per_lead": "50 kr/lead",
            "price_id": STRIPE_PRICE_PACK_BASIC,
            "cta": "Köp 25 credits →",
        },
        {
            "name": "Premium",
            "credits": 50,
            "price": "3 750 kr",
            "per_lead": "75 kr/lead",
            "price_id": STRIPE_PRICE_PACK_PREMIUM,
            "cta": "Köp 50 credits →",
            "highlight": True,
        },
        {
            "name": "Pro",
            "credits": 200,
            "price": "12 000 kr",
            "per_lead": "60 kr/lead",
            "price_id": STRIPE_PRICE_PACK_PRO,
            "cta": "Köp 200 credits →",
        },
    ]

    cols = st.columns(3)
    for col, pack in zip(cols, packs):
        with col:
            if pack.get("highlight"):
                st.markdown("**⭐ Mest populär**")
            st.markdown(f"**{pack['name']}**")
            st.markdown(f"### {pack['credits']} credits")
            st.markdown(f"**{pack['price']}** · {pack['per_lead']}")
            st.divider()
            st.caption("✓ Obegränsad giltighetstid")
            st.caption("✓ Adress + satellitbild per lead")
            st.caption("✓ CSV-export")
            st.divider()
            if pack["price_id"] and STRIPE_SECRET_KEY:
                try:
                    url = create_credit_checkout_url(user.email, str(user.id), pack["price_id"], pack["credits"])
                    st.link_button(pack["cta"], url, type="primary", use_container_width=True)
                except Exception as e:
                    st.error(f"Stripe-fel: {e}")
            else:
                st.button(pack["cta"], disabled=True, use_container_width=True, help="Stripe ej konfigurerat")

    st.divider()

    with st.expander("Månadsabonnemang (för team med hög volym)"):
        st.caption("Obegränsade leads, flera seats, 14 dagars gratis trial.")
        plans = [
            {"name": "Starter", "price": "499 kr/mån", "seats": "1 användare", "price_id": STRIPE_PRICE_STARTER, "cta": "Starta Starter →"},
            {"name": "Team", "price": "1 990 kr/mån", "seats": "Upp till 5 användare", "price_id": STRIPE_PRICE_TEAM, "cta": "Starta Team →"},
            {"name": "Growth", "price": "3 990 kr/mån", "seats": "Upp till 15 användare", "price_id": STRIPE_PRICE_GROWTH, "cta": "Starta Growth →"},
        ]
        sub_cols = st.columns(3)
        for col, plan in zip(sub_cols, plans):
            with col:
                st.markdown(f"**{plan['name']}** — {plan['price']}")
                st.caption(plan["seats"])
                if plan["price_id"] and STRIPE_SECRET_KEY:
                    try:
                        url = create_checkout_url(user.email, str(user.id), plan["price_id"])
                        st.link_button(plan["cta"], url, use_container_width=True)
                    except Exception as e:
                        st.error(f"Stripe-fel: {e}")
                else:
                    st.button(plan["cta"], disabled=True, use_container_width=True)

    st.divider()
    if st.button("Logga ut", use_container_width=True):
        do_logout()
        st.rerun()


ANTHROPIC_API_KEY = _secret("SOLAR_SCOUT_ANTHROPIC_KEY") or _secret("ANTHROPIC_API_KEY")


def _lead_to_display_row(lead) -> dict:
    return {
        "Adress":    lead.address,
        "Källa":     "OSM" if lead.source == "osm" else "AI",
        "Konfidens": f"{lead.confidence:.0%}",
        "Lat":       round(lead.lat, 5),
        "Lng":       round(lead.lng, 5),
    }


def _lead_to_sb_row(lead) -> dict:
    return {
        "address":             lead.address,
        "has_solar":           "Ja",
        "air_to_air":          "False",
        "air_to_water":        "False",
        "notes":               f"Detekterad via {lead.source.upper()} (konfidens {lead.confidence:.0%})",
        "google_search_url":   f"https://www.google.com/search?q=vem+bor+p%C3%A5+{urllib.parse.quote(lead.address)}",
        "hitta_url":           f"https://www.hitta.se/s%C3%B6k?vad={urllib.parse.quote(lead.address)}",
        "maps_url":            f"https://www.google.com/maps/search/?api=1&query={lead.lat},{lead.lng}",
        "image_url":           getattr(lead, "image_url", ""),
        "lat":                 lead.lat,
        "lng":                 lead.lng,
        "scan_source":         lead.source,
        "building_type":       getattr(lead, "building_type", ""),
        "samtomt_solar_extra": getattr(lead, "samtomt_solar_extra", False),
        "solar_location":      getattr(lead, "solar_location", "roof"),
        "needs_review":        getattr(lead, "needs_review", False),
        "ai_reasoning":        getattr(lead, "ai_reasoning", ""),
        "tile_key":            getattr(lead, "tile_key", ""),
    }


def page_scanner(user, profile: dict | None = None, lead_count: int = 0):
    st.subheader("AI Scanner — Hitta solcellstak automatiskt")

    profile = profile or {}
    credits = profile.get("credits_balance", 0) or 0
    on_credit_plan = (not is_admin(profile) and
                      profile.get("scout_subscription_status") != "active")
    free_quota_remaining = lead_count < 10
    if on_credit_plan and credits == 0 and not free_quota_remaining:
        st.warning("Du har inga credits kvar. Köp ett credit-pack för att scanna.")
        st.info("Gå till fliken **Konto** för att köpa credits.")
        return

    south = west = north = east = None
    city_name = ""

    ai_available = bool(ANTHROPIC_API_KEY)

    # Load existing leads for map markers + last-scan context (lightweight: lat/lng/address/date only)
    try:
        _map_rows = (
            get_supabase().table("scout_leads")
            .select("lat,lng,address,created_at")
            .eq("user_id", str(user.id))
            .order("created_at", desc=True)
            .limit(300)
            .execute()
            .data or []
        )
    except Exception:
        _map_rows = []

    # ── Two-column layout: controls | map ──────────────────────────────────
    col_ctrl, col_map = st.columns([1, 2], gap="large")

    with col_ctrl:
        city_name = st.text_input(
            "Ort eller stad",
            placeholder="t.ex. Malmö, Nässjö, Lund, Huskvarna",
            help="Ange ort ELLER rita ett rektangel på kartan. Ritad yta har förtur.",
        )

        if not ai_available:
            st.warning("ANTHROPIC_API_KEY saknas — kör i OSM-läge.")

        max_leads = st.number_input(
            "Max antal leads",
            min_value=0, max_value=500, value=30, step=5,
            help="0 = obegränsat. Rekommenderat: 30–50 för snabb scanning.",
        )
        max_leads = max_leads if max_leads > 0 else None

        # Last-scan summary
        if _map_rows:
            from datetime import datetime, timezone
            try:
                last_dt = datetime.fromisoformat(
                    _map_rows[0]["created_at"].replace("Z", "+00:00")
                )
                days_ago = (datetime.now(timezone.utc) - last_dt).days
                age_str = "idag" if days_ago == 0 else (
                    "igår" if days_ago == 1 else f"{days_ago} d sedan"
                )
            except Exception:
                age_str = ""
            with st.expander(f"📍 {len(_map_rows)} leads · senast {age_str}"):
                last_addr = _map_rows[0].get("address", "—")
                st.caption(f"Senaste träff: {last_addr}")
                if st.button("Zooma till leads på kartan", key="jump_to_leads"):
                    st.session_state["scanner_zoom_to_leads"] = True
                    st.rerun()

        start = st.button("Starta scanning", type="primary", use_container_width=True)

    with col_map:
        try:
            from streamlit_folium import st_folium
            import folium
            from folium.plugins import Draw

            # Default: Skåne overview. Zoom to leads if user clicked the button.
            if st.session_state.pop("scanner_zoom_to_leads", False) and _map_rows:
                _lats = [r["lat"] for r in _map_rows if r.get("lat")]
                _lngs = [r["lng"] for r in _map_rows if r.get("lng")]
                center = [sum(_lats) / len(_lats), sum(_lngs) / len(_lngs)] if _lats else [55.8, 13.3]
                zoom = 12
            else:
                center = [55.8, 13.3]   # Skåne
                zoom = 9

            m = folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap")

            # Rectangle draw only — polygon/circle/marker add complexity without value
            Draw(
                position="topleft",
                draw_options={
                    "rectangle": {
                        "shapeOptions": {"color": "#2563eb", "weight": 2, "fillOpacity": 0.08}
                    },
                    "polygon":      False,
                    "circle":       False,
                    "marker":       False,
                    "polyline":     False,
                    "circlemarker": False,
                },
                edit_options={"edit": True, "remove": True},
            ).add_to(m)

            # Existing lead markers
            for _row in _map_rows[:200]:
                if _row.get("lat") and _row.get("lng"):
                    folium.CircleMarker(
                        location=[_row["lat"], _row["lng"]],
                        radius=5,
                        color="#f59e0b",
                        fill=True,
                        fill_opacity=0.75,
                        tooltip=_row.get("address", ""),
                    ).add_to(m)

            output = st_folium(
                m,
                width="100%",
                height=420,
                returned_objects=["last_active_drawing"],
                key="scanner_map",
            )

            drawing = (output or {}).get("last_active_drawing")
            if drawing:
                coords = drawing["geometry"]["coordinates"][0]
                lats  = [c[1] for c in coords]
                lngs  = [c[0] for c in coords]
                south, north = min(lats), max(lats)
                west,  east  = min(lngs), max(lngs)
                from scanner import _bbox_tiles, ZOOM as _ZOOM
                tc = len(_bbox_tiles(south, west, north, east))
                st.caption(
                    f"Valt område · ~{tc} brickor · "
                    f"{south:.4f},{west:.4f} → {north:.4f},{east:.4f}"
                )

        except ImportError:
            st.warning("streamlit-folium saknas — rita-läge otillgängligt. Ange ort i fältet till vänster.")

    use_bbox = south is not None
    if start:
        if not city_name and not use_bbox:
            st.warning("Ange en ort eller rita ett område på kartan.")
            return

        # Clear any previous scan results and state flags
        st.session_state.pop("scanner_sb_rows", None)
        st.session_state.pop("scanner_display_rows", None)
        st.session_state.pop("scanner_saved", None)
        st.session_state.pop("scanner_leads_with_ids", None)
        st.session_state.pop("scan_reviewed", None)

        from scanner import scan_city, scan_bbox, Lead

        # Fetch already-scanned tile_keys for this user to skip duplicates
        try:
            _existing = get_supabase().table("scout_leads").select("tile_key").eq(
                "user_id", str(user.id)
            ).execute()
            _skip_tile_keys = frozenset(
                r["tile_key"] for r in (_existing.data or []) if r.get("tile_key")
            )
            _log.info("scan dedup: %d existing tile_keys", len(_skip_tile_keys))
        except Exception as _e:
            _log.warning("scan dedup fetch failed: %s", _e)
            _skip_tile_keys = frozenset()

        progress_bar   = st.progress(0.0, text="Startar...")
        status_text    = st.empty()
        found_leads: list[Lead] = []
        live_leads_ph  = st.empty()
        scan_debug: list[str] = []
        scan_errors: list[str] = []
        total_buildings_est = [0]
        cumulative_done = [0]

        current_address = [""]

        def _render_live_leads():
            with live_leads_ph.container():
                st.caption(f"☀️ **{len(found_leads)} solcellstak hittade hittills** — ✅/❌ granskning möjlig efter scan")
                for _live_lead in found_leads:
                    with st.container(border=True):
                        _col_link, _col_info = st.columns([1, 2])
                        with _col_link:
                            _url = _live_lead.image_url or (
                                f"https://minkarta.lantmateriet.se/map/ortofoto"
                                f"#zoom=19&lat={_live_lead.lat}&lon={_live_lead.lng}"
                                if _live_lead.lat and _live_lead.lng else None
                            )
                            if _url:
                                st.link_button("🛰 Visa tak", _url, use_container_width=True)
                            else:
                                st.caption("(ingen bild)")
                        with _col_info:
                            _badge = "🗺 OSM" if _live_lead.source == "osm" else "🤖 AI"
                            st.markdown(f"**{_live_lead.address or '–'}**  {_badge}")
                            _conf = f"{_live_lead.confidence:.0%}"
                            _unsure = " ⚠️ Osäker" if _live_lead.needs_review else ""
                            st.caption(f"Konfidens: {_conf}{_unsure}")
                            if _live_lead.ai_reasoning:
                                st.caption(f"_{_live_lead.ai_reasoning}_")

        def on_progress(done: int, total: int, result):
            cumulative_done[0] += 1
            n = cumulative_done[0]
            # Capture the lead first — progress_bar.progress() must not prevent
            # this even if it raises (e.g. on a second scan in the same session).
            if result:
                found_leads.append(result)
                current_address[0] = result.address or ""
                # Progressive save — persists each AI lead immediately so a crash never loses data
                if result.source == "ai":
                    try:
                        row = {**_lead_to_sb_row(result), "user_id": str(user.id)}
                        get_supabase().table("scout_leads").insert(row).execute()
                    except Exception as _ins_exc:
                        _log.warning("progressive lead insert failed: %s", _ins_exc)
                        scan_errors.append(f"DB-sparning misslyckades ({result.address}): {_ins_exc}")
                _render_live_leads()
            known_total = total_buildings_est[0]
            if known_total > 0:
                frac = min(n / known_total, 0.97)
                pct = int(frac * 100)
                addr_str = f" — {current_address[0]}" if current_address[0] else ""
                progress_bar.progress(frac, text=f"🔍 {pct}% · Byggnad {n} av {known_total}{addr_str}")
            else:
                # Unknown total — asymptotic: moves fast early, slows near 95 %
                frac = min(0.02 + 0.93 * (1 - 1 / (1 + n / 15)), 0.97)
                progress_bar.progress(frac, text=f"🔍 Analyserar byggnad {n}...")

        def on_phase(phase: str, count: int):
            if phase == "osm_leads":
                scan_debug.append(f"OSM solar-taggade: {count}")
            elif phase in ("buildings_found", "area_buildings"):
                total_buildings_est[0] += count
                scan_debug.append(f"Byggnader att AI-analysera: {total_buildings_est[0]}")
                if count == 0:
                    status_text.warning("Inga villabyggnader hittades i OSM för detta område.")
                else:
                    status_text.info(
                        f"Hittade {total_buildings_est[0]} byggnader — AI-analyserar nu (kan ta flera min)..."
                    )
            elif phase == "ai_done":
                scan_debug.append(f"AI bekräftade solceller: {count}")

        anthr_key = ANTHROPIC_API_KEY if ai_available else None
        _log.info("scan start mode=%s ai=%s max_leads=%s", "bbox" if use_bbox else "city", bool(anthr_key), max_leads)
        leads = None
        scan_crashed = False
        try:
            if not use_bbox:
                status_text.info("Söker upp ort och hämtar byggnadsdata från OSM (kan ta 20–60 s)...")
                leads = scan_city(
                    city_name, GOOGLE_API_KEY or "", anthr_key, on_progress,
                    mapbox_key=MAPBOX_TOKEN or None, max_leads=max_leads,
                    phase_callback=on_phase, skip_tile_keys=_skip_tile_keys,
                    user_id=str(user.id),
                )
            else:
                status_text.info("Hämtar byggnadsdata från OSM (kan ta 20–60 s)...")
                leads = scan_bbox(
                    south, west, north, east, GOOGLE_API_KEY or "", anthr_key, on_progress,
                    mapbox_key=MAPBOX_TOKEN or None, max_leads=max_leads,
                    phase_callback=on_phase, skip_tile_keys=_skip_tile_keys,
                    user_id=str(user.id),
                )
        except ValueError as e:
            _log.error("scan ValueError: %s", e)
            st.error(str(e))
            return
        except Exception as e:
            _log.error("scan Exception: %s", e, exc_info=True)
            scan_crashed = True
            scan_errors.append(f"Scanning avbröts oväntat: {e}")

        progress_bar.progress(1.0, text="Klar!")
        status_text.empty()
        live_leads_ph.empty()  # Full results UI renders below — no duplicate

        # On crash: fall back to the AI leads already saved progressively
        if scan_crashed:
            if not found_leads:
                st.error("Scanning kraschade och inga leads hittades. Se felloggen nedan.")
                with st.expander("Felllogg"):
                    for err in scan_errors:
                        st.caption(f"• {err}")
                return
            leads = found_leads
            st.warning(f"⚠️ Scanning avbröts men {len(found_leads)} AI-leads är redan sparade i Leads-fliken.")

        if not leads:
            st.warning("Inga solcellstak hittades i det valda området.")
            with st.expander("🔍 Vad hände under scanningen?"):
                if scan_debug:
                    for line in scan_debug:
                        st.caption(f"• {line}")
                    if any("Byggnader att AI-analysera: 0" in l or "area_buildings" in l for l in scan_debug):
                        st.info("Inga villabyggnader hittades av OSM. Prova ett tätare villaområde.")
                    elif any("AI bekräftade solceller: 0" in l for l in scan_debug):
                        st.info("AI analyserade byggnader men hittade inga solceller.")
                else:
                    st.caption("Möjliga orsaker: AI-nyckel saknas, Overpass-timeout, eller alla byggnader filtrerades.")
            return

        display_rows = [_lead_to_display_row(l) for l in leads]
        sb_rows      = [_lead_to_sb_row(l) for l in leads]

        # Save OSM leads now (AI leads were already saved progressively in on_progress)
        sb_client = get_supabase()
        osm_saved = 0
        for lead in leads:
            if lead.source != "ai":
                try:
                    sb_client.table("scout_leads").insert(
                        {**_lead_to_sb_row(lead), "user_id": str(user.id)}
                    ).execute()
                    osm_saved += 1
                except Exception as exc:
                    _log.warning("osm lead insert failed: %s", exc)

        _log.info("scan done: %d total leads (%d AI progressive, %d OSM saved) for user %s",
                  len(leads), len(found_leads), osm_saved, user.id)
        st.session_state["scanner_sb_rows"]      = sb_rows
        st.session_state["scanner_display_rows"] = display_rows
        st.session_state["scanner_saved"]        = True

        # Fetch leads with DB-assigned IDs so we can call confirm_lead()
        try:
            _resp = get_supabase().table("scout_leads").select(
                "id, address, scan_source, image_url, lat, lng, user_confirmed"
            ).eq("user_id", str(user.id)).in_(
                "address", [r["address"] for r in sb_rows]
            ).execute()
            st.session_state["scanner_leads_with_ids"] = _resp.data or []
        except Exception as _fetch_exc:
            _log.warning("fetch leads with ids failed: %s", _fetch_exc)
            st.session_state["scanner_leads_with_ids"] = []

        if "scan_reviewed" not in st.session_state:
            st.session_state["scan_reviewed"] = set()

        if scan_errors:
            with st.expander(f"⚠️ {len(scan_errors)} fel under scanning (leads sparades ändå)"):
                for err in scan_errors:
                    st.caption(f"• {err}")

    # ── Show results (persisted across reruns) ────────────────────────────────
    sb_rows      = st.session_state.get("scanner_sb_rows")
    display_rows = st.session_state.get("scanner_display_rows")

    if not sb_rows:
        return

    leads_with_ids: list[dict] = st.session_state.get("scanner_leads_with_ids", [])
    if "scan_reviewed" not in st.session_state:
        st.session_state["scan_reviewed"] = set()

    ai_count  = sum(1 for r in sb_rows if r.get("scan_source") == "ai")
    osm_count = sum(1 for r in sb_rows if r.get("scan_source") != "ai")

    st.success(
        f"Scanning klar! Hittade {len(sb_rows)} tak med solceller "
        f"({ai_count} AI, {osm_count} från OSM)."
    )

    # Excel download — always visible, above review cards
    date_str = datetime.now().strftime("%y%m%d")
    export = pd.DataFrame(sb_rows).drop(columns=["lat", "lng"], errors="ignore")
    st.download_button(
        "⬇ Ladda ner Excel",
        _to_excel_bytes(export),
        file_name=f"Scanner_Leads_{date_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    st.divider()

    # Build lookup: address → lead row with id
    _id_by_addr: dict[str, dict] = {}
    for _lr in leads_with_ids:
        _addr = _lr.get("address", "")
        if _addr and _addr not in _id_by_addr:
            _id_by_addr[_addr] = _lr

    # ── AI leads — reviewable cards ───────────────────────────────────────────
    ai_rows = [r for r in sb_rows if r.get("scan_source") == "ai"]
    if ai_rows:
        st.subheader(f"Granska AI-leads ({ai_count} st)")
        reviewed_set: set = st.session_state["scan_reviewed"]
        all_reviewed = True
        for _row in ai_rows:
            _addr    = _row.get("address", "–")
            _lr      = _id_by_addr.get(_addr, {})
            _lead_id = _lr.get("id")
            _img_url = _row.get("image_url") or (
                f"https://minkarta.lantmateriet.se/map/ortofoto#zoom=19&lat={_row['lat']}&lon={_row['lng']}"
                if _row.get("lat") and _row.get("lng") else None
            )

            already_reviewed = _lead_id is not None and _lead_id in reviewed_set
            if not already_reviewed:
                all_reviewed = False

            with st.container(border=True):
                col_img, col_info = st.columns([1, 2])
                with col_img:
                    if _img_url:
                        st.link_button("🛰 Visa tak", _img_url, use_container_width=True)
                    else:
                        st.caption("(ingen bild)")
                with col_info:
                    st.markdown(f"**{_addr}**")
                    if already_reviewed:
                        st.caption("✓ granskad")
                    elif _lead_id is not None:
                        _btn_key_ok  = f"review_ok_{_lead_id}"
                        _btn_key_bad = f"review_bad_{_lead_id}"
                        _c1, _c2 = st.columns(2)
                        with _c1:
                            if st.button("✅ Rätt tak", key=_btn_key_ok, use_container_width=True):
                                confirm_lead(_lead_id, True)
                                st.session_state["scan_reviewed"].add(_lead_id)
                                st.rerun()
                        with _c2:
                            if st.button("❌ Fel tak", key=_btn_key_bad, use_container_width=True):
                                confirm_lead(_lead_id, False)
                                st.session_state["scan_reviewed"].add(_lead_id)
                                st.rerun()
                    else:
                        st.caption("(ID saknas — granska i Leads-fliken)")

        if all_reviewed and ai_rows:
            st.success("Alla AI-leads granskade! Tack — AI:n lär sig nu av dina svar.")

    # ── OSM leads — collapsed list ────────────────────────────────────────────
    osm_rows = [r for r in sb_rows if r.get("scan_source") != "ai"]
    if osm_rows:
        st.divider()
        with st.expander(f"OSM-bekräftade (alltid rätt): {osm_count} leads"):
            for _or in osm_rows:
                st.caption(f"• {_or.get('address', '–')}")

    st.divider()
    st.info(f"✅ {len(sb_rows)} leads sparade i Leads-fliken.")

    if st.button("🔄 Ny scanning", use_container_width=True):
        st.session_state.pop("scanner_sb_rows", None)
        st.session_state.pop("scanner_display_rows", None)
        st.session_state.pop("scanner_saved", None)
        st.session_state.pop("scanner_leads_with_ids", None)
        st.session_state.pop("scan_reviewed", None)
        st.rerun()


def page_scout(user):
    st.subheader("Scouta Tak")

    search_query = st.text_input(
        "Adress eller koordinater:",
        placeholder="t.ex. Storgatan 14, Helsingborg",
    )

    if not search_query:
        return

    if not GOOGLE_API_KEY:
        st.error("GOOGLE_API_KEY saknas i miljövariabler.")
        return

    import googlemaps
    try:
        gmaps = googlemaps.Client(key=GOOGLE_API_KEY)
    except Exception as e:
        st.error(f"Google API-fel — kontrollera att GOOGLE_API_KEY är korrekt.")
        return

    with st.spinner("Söker adress..."):
        try:
            results = gmaps.geocode(search_query)
        except Exception as e:
            st.error("Adresssökning misslyckades — kontrollera din internetanslutning eller API-nyckel.")
            return

    if not results:
        st.error("Adressen hittades inte. Prova mer specifik sökning.")
        return

    loc      = results[0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]
    address  = results[0]["formatted_address"]

    if MAPBOX_TOKEN:
        img_url = (
            f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
            f"/{lng},{lat},20/700x500"
            f"?access_token={MAPBOX_TOKEN}"
        )
    else:
        img_url = (
            f"https://maps.googleapis.com/maps/api/staticmap"
            f"?center={lat},{lng}&zoom=20&size=700x500"
            f"&maptype=satellite&key={GOOGLE_API_KEY}"
        )

    col_img, col_form = st.columns([1.5, 1])

    with col_img:
        st.image(img_url, use_container_width=True, caption=address)
        st.link_button(
            "Öppna i Google Maps (fågelperspektiv)",
            f"https://www.google.com/maps/@{lat},{lng},60m/data=!3m1!1e3",
        )

    with col_form:
        st.subheader("Registrera fastighet")
        has_solar    = st.radio("Solceller på taket?", ["Nej", "Ja"])
        air_to_air   = st.checkbox("Potential Luft/Luft-pump")
        air_to_water = st.checkbox("Potential Luft/Vatten-pump")
        notes        = st.text_area("Observationer (takskick, uppvärmning, mätarskåp...):")

        google_search_url = f"https://www.google.com/search?q=vem+bor+p%C3%A5+{urllib.parse.quote(address)}"
        hitta_url         = f"https://www.hitta.se/s%C3%B6k?vad={urllib.parse.quote(address)}"

        st.write("**Hämta ägarinfo:**")
        lc1, lc2 = st.columns(2)
        lc1.link_button("Google", google_search_url)
        lc2.link_button("Hitta.se", hitta_url)

        if st.button("💾 Spara lead", type="primary", use_container_width=True):
            save_lead(str(user.id), {
                "address":           address,
                "has_solar":         has_solar,
                "air_to_air":        str(air_to_air),
                "air_to_water":      str(air_to_water),
                "notes":             notes,
                "google_search_url": google_search_url,
                "hitta_url":         hitta_url,
                "maps_url":          f"https://www.google.com/maps/search/?api=1&query={lat},{lng}",
            })
            st.success(f"Sparad: {address}")
            st.balloons()


def page_review(user):
    """Kort-för-kort granskning av UNSURE-leads — AI var osäker, du avgör."""
    sb = get_supabase()
    try:
        resp = (
            sb.table("scout_leads")
            .select("id,address,lat,lng,ai_reasoning,building_type,maps_url")
            .eq("user_id", str(user.id))
            .eq("needs_review", True)
            .is_("user_confirmed", "null")
            .order("created_at", desc=False)
            .execute()
        )
        queue = resp.data or []
    except Exception:
        # needs_review column may not exist yet in DB
        queue = []

    if not queue:
        st.markdown("""
        <div style='text-align:center;padding:3rem 1rem;'>
            <div style='font-size:3rem'>✅</div>
            <h3 style='color:#2e7d32'>Klar! Inget kvar att granska.</h3>
            <p style='color:#666'>Leads med SOLAR=UNSURE från AI-scanningar hamnar här.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    total = len(queue)
    lead = queue[0]
    lead_id = int(lead["id"])
    lat, lng = lead.get("lat"), lead.get("lng")

    # ── Rubrik med räknare ──────────────────────────────────────────────────
    st.markdown(
        f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:0.5rem'>"
        f"<span style='font-size:1.1rem;font-weight:600'>🔍 Granska tak</span>"
        f"<span style='background:#f0f0f0;border-radius:12px;padding:2px 12px;"
        f"font-size:0.85rem;color:#555'>{total} kvar</span></div>",
        unsafe_allow_html=True,
    )

    # ── Satellitbild — stor ─────────────────────────────────────────────────
    _img_bytes_review = None
    if lat and lng:
        try:
            from scanner import _fetch_lm_wms
            _img_bytes_review = _fetch_lm_wms(lat, lng, size_m=30)
            if _img_bytes_review:
                st.image(_img_bytes_review, use_container_width=True)
            elif MAPBOX_TOKEN:
                st.image(
                    f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
                    f"/{lng},{lat},20/640x640?access_token={MAPBOX_TOKEN}",
                    use_container_width=True,
                )
            else:
                st.caption("(Bild ej tillgänglig)")
        except Exception:
            st.caption("(Kunde inte hämta bild)")

    # ── Adress + AI-resonemang ──────────────────────────────────────────────
    st.markdown(f"### {lead.get('address', '–')}")
    reasoning = lead.get("ai_reasoning", "")
    if reasoning:
        st.caption(f"🤖 AI: *{reasoning[:180]}{'…' if len(reasoning)>180 else ''}*")

    if lead.get("maps_url"):
        st.link_button("📍 Öppna i Google Maps", lead["maps_url"])

    st.write("")

    # Granntomt-scan triggered by previous rejection
    _gt_key = f"review_granntomt_{lead_id}"
    if st.session_state.get(_gt_key):
        _gt = st.session_state.pop(_gt_key)
        if _gt.get("lat") and _gt.get("lng"):
            with st.spinner("🔍 Skannar granntomter..."):
                try:
                    from scanner import scan_nearby_buildings
                    _existing = (
                        sb.table("scout_leads")
                        .select("tile_key")
                        .eq("user_id", str(user.id))
                        .execute()
                    ).data or []
                    _skip = frozenset(r["tile_key"] for r in _existing if r.get("tile_key"))
                    _nearby = scan_nearby_buildings(
                        _gt["lat"], _gt["lng"],
                        google_key=GOOGLE_API_KEY or "",
                        anthropic_key=ANTHROPIC_API_KEY,
                        exclude_tile_key=_gt.get("tile_key", ""),
                        skip_tile_keys=_skip,
                    )
                    for _nl in _nearby:
                        try:
                            sb.table("scout_leads").insert(
                                {**_lead_to_sb_row(_nl), "user_id": str(user.id)}
                            ).execute()
                        except Exception:
                            pass
                    if _nearby:
                        st.success(f"☀️ Hittade {len(_nearby)} lead(s) på granntomter — se Leads-fliken!")
                    else:
                        st.info("Inga solceller hittades på granntomterna.")
                except Exception as _ge:
                    st.warning(f"Granntomt-scan misslyckades: {_ge}")

    reject_reason = st.selectbox(
        "Avvisningsorsak",
        ["Inga solceller", "Granntomt", "Solfångare", "Eternite"],
        key="review_reject_reason",
        label_visibility="collapsed",
    )

    # ── Tinder-knappar ──────────────────────────────────────────────────────
    col_nej, col_ja = st.columns(2)
    with col_nej:
        if st.button("❌  Avvisa", use_container_width=True, key="review_no"):
            try:
                sb.table("scout_leads").update({
                    "user_confirmed": False,
                    "needs_review": False,
                    "false_positive": True,
                    "reject_reason": reject_reason,
                }).eq("id", lead_id).execute()
            except Exception:
                pass
            # Spara bild för dynamisk few-shot (NO-exempel)
            try:
                _save_img = _img_bytes_review
                if not _save_img:
                    from scanner import _fetch_lm_wms
                    _save_img = _fetch_lm_wms(lat, lng) if lat and lng else None
                if _save_img:
                    sb.storage.from_("lead-images").upload(
                        f"{user.id}/{lead_id}.jpg",
                        _save_img,
                        {"content-type": "image/jpeg", "upsert": "true"},
                    )
                    _no_url = sb.storage.from_("lead-images").get_public_url(
                        f"{user.id}/{lead_id}.jpg"
                    )
                    sb.table("scout_leads").update({"confirmed_image_url": _no_url}).eq("id", lead_id).execute()
            except Exception:
                pass
            if reject_reason == "Granntomt" and lat and lng:
                _tile_key = lead.get("tile_key", "") or f"bld/{lead_id}"
                st.session_state[_gt_key] = {"lat": lat, "lng": lng, "tile_key": _tile_key}
            st.rerun()
    with col_ja:
        if st.button("✅  Ja, solceller!", type="primary", use_container_width=True, key="review_yes"):
            try:
                sb.table("scout_leads").update({
                    "user_confirmed": True,
                    "needs_review": False,
                    "has_solar": "Ja",
                }).eq("id", lead_id).execute()
            except Exception:
                pass
            # Spara bild till Supabase Storage (används för dynamisk few-shot)
            try:
                _save_img = _img_bytes_review
                if not _save_img:
                    from scanner import _fetch_lm_wms
                    _save_img = _fetch_lm_wms(lat, lng) if lat and lng else None
                if _save_img:
                    sb.storage.from_("lead-images").upload(
                        f"{user.id}/{lead_id}.jpg",
                        _save_img,
                        {"content-type": "image/jpeg", "upsert": "true"},
                    )
                    _public_url = sb.storage.from_("lead-images").get_public_url(
                        f"{user.id}/{lead_id}.jpg"
                    )
                    sb.table("scout_leads").update({"confirmed_image_url": _public_url}).eq("id", lead_id).execute()
            except Exception:
                pass
            st.rerun()


def page_leads(user):  # noqa: keep user param for confirm_lead calls
    st.subheader("Leadslista")
    df = load_leads(str(user.id))

    if df.empty:
        st.info("Inga leads ännu. Kör en scanning i fliken 'AI Scanner' för att hitta tak.")
        return

    total = len(df)
    solar = (df["has_solar"] == "Ja").sum() if "has_solar" in df.columns else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Totalt scoutable", total)
    m2.metric("Med solceller", solar)
    m3.metric("Utan solceller", total - solar)

    col_filter1, col_filter2 = st.columns(2)
    with col_filter1:
        filter_solar = st.radio(
            "Filtrera", ["Alla", "Med solceller", "Utan solceller"], horizontal=True
        )
    with col_filter2:
        hide_samtomt = st.checkbox(
            "Visa bara leads utan samtomt-sol",
            value=False,
            help="Dölj leads där solceller redan hittades på annan del av tomten (t.ex. garage)",
        )

    if filter_solar == "Med solceller":
        df = df[df["has_solar"] == "Ja"]
    elif filter_solar == "Utan solceller":
        df = df[df["has_solar"] != "Ja"]

    if hide_samtomt and "samtomt_solar_extra" in df.columns:
        df = df[~df["samtomt_solar_extra"].astype(bool)]

    def _samtomt_icon(val) -> str:
        try:
            return "✓" if bool(val) else "–"
        except Exception:
            return "–"

    display_cols = [c for c in
        ["address", "has_solar", "samtomt_solar_extra", "air_to_air", "air_to_water", "notes", "image_url", "created_at"]
        if c in df.columns]
    rename_map = {
        "address": "Adress", "has_solar": "Solceller",
        "samtomt_solar_extra": "Samtomt sol",
        "air_to_air": "L/L", "air_to_water": "L/V",
        "notes": "Noteringar", "image_url": "Satellitbild",
        "created_at": "Sparad",
    }
    df_reset = df.reset_index(drop=True)
    display_df = df_reset[display_cols].rename(columns=rename_map)
    if "Samtomt sol" in display_df.columns:
        display_df["Samtomt sol"] = display_df["Samtomt sol"].apply(_samtomt_icon)

    # Checkbox "❌ Fel" per rad — markera falska positiver → AI lär sig
    editable_df = display_df.copy()
    editable_df.insert(0, "❌ Fel", False)

    column_config: dict = {
        "❌ Fel": st.column_config.CheckboxColumn(
            "❌ Fel",
            width="small",
            help="Markera om AI:n hade fel — inga solceller här. Listan uppdateras och AI:n lär sig till nästa scan.",
        ),
    }
    if "Satellitbild" in display_df.columns:
        column_config["Satellitbild"] = st.column_config.LinkColumn(
            "Satellitbild",
            display_text="🛰 Visa tak",
            help="Öppnar LM WMS-bild direkt i webbläsaren",
        )

    edited = st.data_editor(
        editable_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config=column_config,
        disabled=[c for c in display_df.columns],
        key="leads_editor",
    )

    # Hantera felmarkering — inga bekräftelsesteg, enkel klick räcker
    if "id" in df_reset.columns:
        wrong_mask = edited["❌ Fel"] == True  # noqa: E712
        n_wrong = int(wrong_mask.sum())
        if n_wrong > 0:
            wrong_addrs = edited.loc[wrong_mask, "Adress"].tolist() if "Adress" in edited.columns else []
            preview = ", ".join(wrong_addrs[:3]) + ("…" if len(wrong_addrs) > 3 else "")
            st.warning(f"**{n_wrong} lead(s) markerade som fel:** {preview}")
            col_cancel, col_confirm = st.columns(2)
            with col_cancel:
                if st.button("Avbryt", key="btn_fp_cancel", use_container_width=True):
                    st.rerun()
            with col_confirm:
                if st.button(
                    f"✅ Bekräfta — AI lär sig av {n_wrong} fel",
                    type="primary", key="btn_fp_confirm", use_container_width=True,
                ):
                    sb = get_supabase()
                    for lid in df_reset.loc[wrong_mask, "id"]:
                        sb.table("scout_leads").update({
                            "false_positive": True,
                            "user_confirmed": False,
                        }).eq("id", int(lid)).execute()
                    st.success(f"✅ {n_wrong} lead(s) markerade som fel. AI:n använder detta nästa scan.")
                    st.rerun()

    with st.expander("➕ Lägg till manuell lead"):
        m_addr  = st.text_input("Adress", placeholder="Ljunggatan 12, Malmö")
        m_note  = st.text_input("Notering", placeholder="t.ex. solceller på garage, pool, dåligt tak")
        m_solar = st.checkbox("Har solceller", value=True)
        if st.button("Spara manuell lead", type="secondary") and m_addr:
            sb = get_supabase()
            sb.table("scout_leads").insert({
                "user_id":    str(user.id),
                "address":    m_addr,
                "has_solar":  "Ja" if m_solar else "Nej",
                "notes":      m_note,
                "scan_source": "manual",
                "air_to_air": "False",
                "air_to_water": "False",
            }).execute()
            st.success(f"Lead '{m_addr}' sparad.")
            st.rerun()


    st.divider()
    export_df = df.drop(columns=["id", "user_id"], errors="ignore").rename(
        columns={"samtomt_solar_extra": "Samtomt sol"}
    )
    date_str  = datetime.now().strftime("%y%m%d")
    st.download_button(
        "⬇ Ladda ner Excel",
        _to_excel_bytes(export_df),
        file_name=f"Scout_Leads_{date_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )

    # ── Bekräftade leads med status + notering ────────────────────────────────
    if "user_confirmed" in df.columns:
        confirmed_df = df[df["user_confirmed"] == True]  # noqa: E712
        if not confirmed_df.empty:
            st.divider()
            st.subheader("Bekräftade leads")

            # Sammanfattning per status
            if "status" in confirmed_df.columns:
                status_counts = confirmed_df["status"].value_counts()
                booked = int(status_counts.get("mote_bokat", 0))
                if booked:
                    st.success(f"📅 {booked} möte{'n' if booked > 1 else ''} bokat")

            st.caption(f"{len(confirmed_df)} leads bekräftade")
            _sb = get_supabase()
            for _, c_row in confirmed_df.iterrows():
                lid = int(c_row["id"])
                addr = c_row.get("address", "–")
                with st.expander(f"**{addr}**", expanded=False):
                    _gs_url = c_row.get("google_search_url") or f"https://www.google.com/search?q=vem+bor+p%C3%A5+{urllib.parse.quote(addr)}"
                    _ht_url = c_row.get("hitta_url") or f"https://www.hitta.se/s%C3%B6k?vad={urllib.parse.quote(addr)}"
                    _lnk1, _lnk2, _lnk3 = st.columns(3)
                    _lnk1.link_button("🔍 Google", _gs_url, use_container_width=True)
                    _lnk2.link_button("📖 Hitta.se", _ht_url, use_container_width=True)
                    if c_row.get("maps_url"):
                        _lnk3.link_button("📍 Maps", c_row["maps_url"], use_container_width=True)
                    st.divider()
                    col_s, col_fp = st.columns([2, 1])
                    with col_s:
                        cur_status = c_row.get("status") or "ej_kontaktad"
                        new_status = st.selectbox(
                            "Status",
                            options=list(_LEAD_STATUSES.keys()),
                            format_func=lambda k: _LEAD_STATUSES[k],
                            index=list(_LEAD_STATUSES.keys()).index(cur_status)
                                  if cur_status in _LEAD_STATUSES else 0,
                            key=f"status_{lid}",
                        )
                    with col_fp:
                        if st.button("❌ Inte solceller", key=f"fp_{lid}", use_container_width=True):
                            try:
                                _sb.table("scout_leads").update({
                                    "false_positive": True,
                                    "has_solar": "Nej",
                                    "user_confirmed": False,
                                }).eq("id", lid).execute()
                            except Exception:
                                pass
                            st.rerun()

                    cur_note = c_row.get("david_note") or c_row.get("notes") or ""
                    new_note = st.text_area(
                        "Notering (synlig för Linus)",
                        value=cur_note,
                        placeholder="t.ex. grannhuset har sol, dåligt tak, intresserad av batteri...",
                        key=f"note_{lid}",
                        height=80,
                    )

                    if st.button("Spara", key=f"save_{lid}", type="primary"):
                        update = {"status": new_status}
                        if new_note != cur_note:
                            update["david_note"] = new_note
                        try:
                            _sb.table("scout_leads").update(update).eq("id", lid).execute()
                        except Exception:
                            pass
                        # Skicka mail till Linus om möte precis bokades
                        if new_status == "mote_bokat" and cur_status != "mote_bokat":
                            sent = _send_meeting_email(
                                addr, new_note,
                                c_row.get("lat"), c_row.get("lng"),
                            )
                            if sent:
                                st.success("📧 Mail skickat till Linus!")
                            else:
                                st.info("Status sparad. (Konfigurera SMTP för automatiskt mail)")
                        else:
                            st.success("Sparat.")
                        st.rerun()

    # ── Granska AI-leads ───────────────────────────────────────────────────────
    ai_df = df[df.get("scan_source", pd.Series(dtype=str)) == "ai"] if "scan_source" in df.columns else pd.DataFrame()
    if ai_df.empty:
        return

    st.divider()
    st.subheader("Granska AI-detekterade tak")

    stats = get_accuracy_stats(str(user.id))
    if stats["reviewed"] > 0:
        c1, c2, c3 = st.columns(3)
        c1.metric("Granskade", f"{stats['reviewed']}/{stats['total_ai']}")
        c2.metric("Bekräftade", stats["confirmed"])
        c3.metric("Träffsäkerhet", f"{stats['pct']} %" if stats["pct"] is not None else "–")

    unreviewed = ai_df[ai_df.get("user_confirmed", pd.Series(dtype=object)).isna()] if "user_confirmed" in ai_df.columns else ai_df
    if unreviewed.empty:
        st.success("Alla AI-leads granskade!")
        return

    st.caption(f"{len(unreviewed)} leads väntar på granskning — stämmer AI:ns bedömning?")

    for _, row in unreviewed.iterrows():
        st.divider()
        col_img, col_info = st.columns([1, 1])

        with col_img:
            img_shown = False
            stored_url = row.get("image_url", "")
            if stored_url:
                try:
                    st.image(stored_url, use_container_width=True)
                    img_shown = True
                except Exception:
                    pass
            if not img_shown:
                lat, lng = row.get("lat"), row.get("lng")
                if lat and lng and MAPBOX_TOKEN:
                    st.image(
                        f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
                        f"/{lng},{lat},20/400x300?access_token={MAPBOX_TOKEN}",
                        use_container_width=True,
                    )
                elif lat and lng:
                    try:
                        from scanner import _fetch_lm_wms
                        img_bytes = _fetch_lm_wms(lat, lng)
                        if img_bytes:
                            st.image(img_bytes, use_container_width=True)
                        else:
                            st.caption("(Bild ej tillgänglig)")
                    except Exception:
                        st.caption("(Bild ej tillgänglig)")

        with col_info:
            st.markdown(f"**{row.get('address', '–')}**")
            st.caption(f"Byggnadstyp: {row.get('building_type', '–')}")
            st.caption(f"Konfidens: {row.get('notes', '')}")
            if row.get("maps_url"):
                st.link_button("Öppna i Google Maps", row["maps_url"])
            st.write("")
            b1, b2 = st.columns(2)
            with b1:
                if st.button("✅ Rätt tak", key=f"ok_{row['id']}", use_container_width=True, type="primary"):
                    confirm_lead(int(row["id"]), True)
                    # Spara bild till Supabase Storage vid bekräftelse
                    try:
                        _r_lat, _r_lng = row.get("lat"), row.get("lng")
                        from scanner import _fetch_lm_wms
                        _r_img = _fetch_lm_wms(_r_lat, _r_lng) if _r_lat and _r_lng else None
                        if _r_img:
                            _sb = get_supabase()
                            _sb.storage.from_("lead-images").upload(
                                f"{user.id}/{int(row['id'])}.jpg",
                                _r_img,
                                {"content-type": "image/jpeg", "upsert": "true"},
                            )
                            _r_url = _sb.storage.from_("lead-images").get_public_url(
                                f"{user.id}/{int(row['id'])}.jpg"
                            )
                            _sb.table("scout_leads").update({"confirmed_image_url": _r_url}).eq("id", int(row["id"])).execute()
                    except Exception:
                        pass
                    st.rerun()
            with b2:
                if st.button("❌ Fel tak", key=f"fel_{row['id']}", use_container_width=True):
                    confirm_lead(int(row["id"]), False)
                    st.rerun()


def do_cascading_delete(user_id: str):
    """Radera all användardata kaskaderande.

    Ordning:
      1. scout_leads (alla rader för user_id)
      2. profiles (raden för user_id)
      3. Supabase auth-user (kräver service-role-nyckel)

    Notera: sb.auth.admin.delete_user kräver en admin-klient med
    service-role-nyckel. I produktion bör detta flyttas till en
    serverside-funktion (Supabase Edge Function eller dedikerad backend)
    eftersom anon-nyckeln inte har behörighet att radera auth-användare.
    """
    sb = get_supabase()

    # 1. Radera leads
    try:
        sb.table("scout_leads").delete().eq("user_id", user_id).execute()
    except Exception as e:
        st.warning(f"Kunde inte radera alla leads: {e}")

    # 2. Radera profilraden
    try:
        sb.table("profiles").delete().eq("user_id", user_id).execute()
    except Exception as e:
        st.warning(f"Kunde inte radera profilen: {e}")

    # 3. Radera auth-användaren (kräver admin-rättigheter — kan misslyckas
    #    med anon-nyckeln; bör flyttas till server-side i produktion)
    auth_deleted = False
    try:
        sb.auth.admin.delete_user(user_id)
        auth_deleted = True
    except Exception as e:
        st.info(
            f"Auth-användaren kunde inte raderas automatiskt ({e}). "
            "Du loggas ut nu — kontakta gdpr@solar-scout.example för att "
            "fullfölja raderingen av inloggningsuppgifterna."
        )

    # 4. Logga ut oavsett
    do_logout()
    return auth_deleted


def page_account(user, profile):
    st.subheader("⚙ Konto")

    st.markdown("**Kontouppgifter**")
    st.write(f"**E-post:** {user.email}")

    # Försök hämta registreringsdatum från profil eller user-objekt
    signup_date = (
        profile.get("created_at")
        or getattr(user, "created_at", None)
    )
    if signup_date:
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(str(signup_date).replace("Z", "+00:00"))
            SWEDISH_MONTHS = ["jan","feb","mar","apr","maj","jun",
                              "jul","aug","sep","okt","nov","dec"]
            formatted = f"{dt.day} {SWEDISH_MONTHS[dt.month-1]} {dt.year}"
        except Exception:
            formatted = str(signup_date)
        st.write(f"**Konto skapat:** {formatted}")

    st.divider()
    st.markdown("### Radera mitt konto")
    st.warning(
        "Detta raderar **permanent** ditt konto, alla dina sparade leads "
        "och din profil. Åtgärden kan inte ångras."
    )

    # Bekräftelseflöde via session_state
    if not st.session_state.get("delete_account_confirm_open"):
        if st.button(
            "Radera mitt konto + all min data",
            type="secondary",
            use_container_width=True,
        ):
            st.session_state["delete_account_confirm_open"] = True
            st.rerun()
        return

    st.error(
        "**Bekräfta radering.** Skriv exakt **RADERA** i fältet nedan för "
        "att bekräfta att du vill ta bort kontot och all data."
    )
    confirm_text = st.text_input(
        "Skriv RADERA för att bekräfta",
        key="delete_account_confirm_input",
    )

    col_cancel, col_delete = st.columns(2)
    with col_cancel:
        if st.button("Avbryt", use_container_width=True):
            st.session_state.pop("delete_account_confirm_open", None)
            st.session_state.pop("delete_account_confirm_input", None)
            st.rerun()
    with col_delete:
        if st.button(
            "Bekräfta radering",
            type="primary",
            use_container_width=True,
            disabled=(confirm_text != "RADERA"),
        ):
            do_cascading_delete(str(user.id))
            st.session_state.pop("delete_account_confirm_open", None)
            st.session_state.pop("delete_account_confirm_input", None)
            st.success("Ditt konto och all data har raderats. Hej då!")
            st.rerun()


def page_app(user, profile, lead_count: int = 0):
    with st.sidebar:
        st.markdown("## Scout")
        st.divider()
        st.markdown(f"**{user.email}**")
        credits = profile.get("credits_balance", 0) or 0
        if not is_admin(profile) and profile.get("scout_subscription_status") != "active":
            st.metric("Credits kvar", credits)
        st.divider()

        customer_id = profile.get("scout_stripe_customer_id")
        if customer_id and STRIPE_SECRET_KEY:
            try:
                portal_url = create_portal_url(customer_id)
                st.link_button("📄 Fakturor & prenumeration", portal_url, use_container_width=True)
            except Exception:
                pass

        if st.button("Logga ut", use_container_width=True):
            do_logout()
            st.rerun()

    # Count needs_review leads for badge
    review_count = 0
    try:
        sb = get_supabase()
        r = (sb.table("scout_leads")
             .select("id", count="exact")
             .eq("user_id", str(user.id))
             .eq("needs_review", True)
             .is_("user_confirmed", "null")
             .execute())
        review_count = r.count or 0
    except Exception:
        pass

    review_label = f"👁 Granska ({review_count})" if review_count else "👁 Granska"
    tab_scanner, tab_scout, tab_leads, tab_review, tab_account = st.tabs(
        ["🔍 AI Scanner", "🏠 Scouta Tak", "📋 Leads", review_label, "⚙ Konto"]
    )

    with tab_scanner:
        page_scanner(user, profile, lead_count)

    with tab_scout:
        page_scout(user)

    with tab_leads:
        page_leads(user)

    with tab_review:
        page_review(user)

    with tab_account:
        page_account(user, profile)

    st.divider()
    st.caption(
        "Geodata © Lantmäteriet, CC-BY 4.0 · "
        "Satellitbilder © Mapbox · "
        "OpenStreetMap-data © OSM-bidragsgivare, ODbL · "
        "[Integritetspolicy](?page=privacy)"
    )


# ── Integritetspolicy ─────────────────────────────────────────────────────────

def page_privacy():
    st.title("Integritetspolicy")
    # TODO: Byt ut "Linus Bergström" mot det slutgiltiga företagsnamnet när
    # bolagsregistrering är klar.
    # TODO: Byt ut gdpr@solar-scout.example mot en riktig kontaktadress innan
    # produktionssläpp.
    st.markdown(
        """
Denna integritetspolicy beskriver hur Scout (”tjänsten”) behandlar
personuppgifter i enlighet med EU:s dataskyddsförordning (GDPR), särskilt
informationsplikten i Art. 13.

### Personuppgiftsansvarig

Personuppgiftsansvarig för behandlingen är **Linus Bergström**
(placeholder — slutgiltigt företagsnamn fylls i innan produktionssläpp).
Kontakt för förfrågningar från registrerade: **gdpr@solar-scout.example**
(placeholder — byts ut före lansering).

### Ändamål med behandlingen

Tjänsten används för **AI-baserad detektion av solpaneler på fastigheter
via satellitbilder** för att generera leads till batteri-uppsälj till
fastigheter som redan har solceller. Behandlingen sker för att möjliggöra
direktmarknadsföring av batterilösningar (B2C/B2B) via våra användare
(batteri-säljare).

### Kategorier av personuppgifter som behandlas

- **Adresser** till fastigheter som scannats eller scoutats
- **Geografiska koordinater** (latitud och longitud) för byggnader
- **Satellitbild av byggnaden** (hämtad från Mapbox/Google)
- Härledd information om huruvida solceller finns på taket samt
  byggnadstyp

Vi samlar normalt inte in namn eller personnummer; sådana uppgifter kan
dock härledas av användaren via externa sökningar (Google, Hitta.se).

### Rättslig grund

Behandlingen sker med stöd av **berättigat intresse** (GDPR Art. 6.1 f) —
att kunna marknadsföra relevanta energilösningar till fastighetsägare
vars tak redan utrustats med solceller.

### Mottagare av personuppgifterna

Mottagare är **Scouts användare (batteri-säljare)** som loggar in i
tjänsten. Ingen vidare-överföring till tredje part sker, utöver de
tekniska underleverantörer som krävs för drift (Supabase, Stripe,
Mapbox, Google Maps, Anthropic).

### Lagringstid

Personuppgifter (leads) lagras **tills användaren själv raderar dem**,
eller upp till **12 månader vid inaktivitet** på kontot, varefter de kan
komma att raderas automatiskt.

### Dina rättigheter (GDPR Art. 15–22)

Som registrerad har du rätt att:

- **Tillgång** (Art. 15) — få information om vilka uppgifter vi har om dig
- **Rättelse** (Art. 16) — få felaktiga uppgifter rättade
- **Radering** (Art. 17) — bli ”bortglömd”
- **Begränsning** (Art. 18) — begära att behandlingen begränsas
- **Dataportabilitet** (Art. 20) — få ut dina uppgifter i läsbart format
- **Invändning** (Art. 21) — invända mot behandling baserad på
  berättigat intresse, särskilt vid direktmarknadsföring
- **Undantag från automatiserat beslutsfattande** (Art. 22) — inte bli
  föremål för enbart automatiserade beslut som har rättsliga följder

Skicka din förfrågan till **gdpr@solar-scout.example**.

### Direktmarknadsföring — Robinsonlistan / NIX

Om du vill slippa direktmarknadsföring i allmänhet kan du registrera dig
hos **Swedma/Robinsonlistan** (för postal direktreklam) eller
**NIX-Telefon** (för telefonförsäljning). Notera dock att registrering
där inte automatiskt påverkar behandlingen i denna tjänst — använd
istället din invändningsrätt enligt ovan.

### Klagomål

Du har rätt att lämna klagomål till **Integritetsskyddsmyndigheten
(IMY)** om du anser att behandlingen strider mot GDPR.
        """
    )
    st.divider()
    st.link_button("← Tillbaka", "/")


# ── Huvudprogram ──────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Scout · Linus Bergström",
        page_icon="🔍",
        layout="wide",
    )

    if _COOKIES_AVAILABLE:
        st.session_state["_cookie_manager"] = stx.CookieManager(key="solar_scout_auth")

    # Hantera offentliga sidor (innan auth-check)
    if st.query_params.get("page") == "privacy":
        page_privacy()
        return

    user = init_auth()

    if not user:
        page_auth()
        return

    _handle_credit_redirect(str(user.id))

    profile = get_profile(str(user.id))
    lead_count = len(load_leads(str(user.id)))

    if not has_access(profile, lead_count):
        page_paywall(user, lead_count)
        return

    page_app(user, profile, lead_count)


def _report_crash(exc: Exception) -> None:
    """Skapa GitHub Issue automatiskt vid okänt app-krasch."""
    import traceback, httpx as _hx
    token = _secret("GITHUB_TOKEN")
    if not token:
        return
    try:
        _hx.post(
            "https://api.github.com/repos/libstrom/solar-scout/issues",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={
                "title": f"[Auto-crash] {type(exc).__name__}: {str(exc)[:120]}",
                "body": f"Automatiskt rapporterat från Streamlit Cloud.\n\n```\n{traceback.format_exc()}\n```",
                "labels": ["bug", "needs-triage"],
            },
            timeout=5,
        )
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as _e:
        _report_crash(_e)
        raise
