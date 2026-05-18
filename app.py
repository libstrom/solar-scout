"""
Scout – Takidentifiering & Leadsgenerering
Linus Bergström
"""

import os
import logging
import urllib.parse
import stripe
import pandas as pd
import streamlit as st
from datetime import datetime
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
APP_URL               = _secret("APP_URL", "http://localhost:8501")
GOOGLE_API_KEY        = _secret("GOOGLE_API_KEY")
MAPBOX_TOKEN          = _secret("MAPBOX_TOKEN")

stripe.api_key = STRIPE_SECRET_KEY

# CSV-attribution-header — krav från OSM ODbL § 4.3 (attribution) och
# Lantmäteriets CC-BY-villkor. Se docs/adr/0001-osm-odbl-csv.md för tolkning.
CSV_ATTRIBUTION_HEADER = (
    "# Genererad av solar-scout · "
    "Geodata © Lantmäteriet, CC-BY 4.0 · "
    "Innehåller OSM-data © OpenStreetMap-bidragsgivare, ODbL 1.0\n"
    "# https://www.openstreetmap.org/copyright · "
    "https://creativecommons.org/licenses/by/4.0/\n"
)

@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def _get_cookie_manager():
    if _COOKIES_AVAILABLE:
        return stx.CookieManager(key="solar_scout_auth")
    return None

# ── Auth ──────────────────────────────────────────────────────────────────────

def init_auth():
    """Återställ session från session_state eller browser-cookies.

    Ordning: session_state → cookies → None.
    Cookies överlever Railway-restarts; session_state gör det inte.
    """
    sb = get_supabase()
    access = st.session_state.get("access_token")
    refresh = st.session_state.get("refresh_token")

    # Fallback: läs från cookies om session_state är tom
    if not access or not refresh:
        cm = _get_cookie_manager()
        if cm is not None:
            access = cm.get("access_token")
            refresh = cm.get("refresh_token")
        if not access or not refresh:
            return None

    try:
        sb.auth.set_session(access, refresh)
        return sb.auth.get_user().user
    except Exception:
        pass
    # Token förmodligen expired — försök refresh
    try:
        resp = sb.auth.refresh_session(refresh)
        if resp.session and resp.user:
            st.session_state["access_token"]  = resp.session.access_token
            st.session_state["refresh_token"] = resp.session.refresh_token
            cm = _get_cookie_manager()
            if cm is not None:
                cm.set("access_token", resp.session.access_token)
                cm.set("refresh_token", resp.session.refresh_token)
            return resp.user
    except Exception:
        pass
    # Refresh failed → riktig logout
    st.session_state.pop("access_token", None)
    st.session_state.pop("refresh_token", None)
    cm = _get_cookie_manager()
    if cm is not None:
        cm.remove("access_token")
        cm.remove("refresh_token")
    return None


def do_login(email: str, password: str):
    sb = get_supabase()
    resp = sb.auth.sign_in_with_password({"email": email, "password": password})
    st.session_state["access_token"]  = resp.session.access_token
    st.session_state["refresh_token"] = resp.session.refresh_token
    cm = _get_cookie_manager()
    if cm is not None:
        cm.set("access_token", resp.session.access_token)
        cm.set("refresh_token", resp.session.refresh_token)
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
    st.session_state.pop("access_token", None)
    st.session_state.pop("refresh_token", None)
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
    return lead_count < 10

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


def create_portal_url(customer_id: str) -> str:
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=APP_URL,
    )
    return session.url

# ── Leads ─────────────────────────────────────────────────────────────────────

def save_lead(user_id: str, data: dict):
    sb = get_supabase()
    data["user_id"] = user_id
    sb.table("scout_leads").insert(data).execute()


def load_leads(user_id: str) -> pd.DataFrame:
    sb = get_supabase()
    resp = (
        sb.table("scout_leads")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
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

def page_auth():
    st.title("Scout")
    st.caption("Takidentifiering & Leadsgenerering · Linus Bergström")
    st.divider()

    tab_in, tab_up, tab_pw = st.tabs(["Logga in", "Skapa konto", "Glömt lösenord"])

    with tab_in:
        with st.form("login_form"):
            email    = st.text_input("E-postadress")
            password = st.text_input("Lösenord", type="password")
            submitted = st.form_submit_button("Logga in", type="primary", use_container_width=True)
        if submitted:
            try:
                do_login(email, password)
                st.rerun()
            except Exception as e:
                st.error(f"Fel: {e}")

    with tab_up:
        with st.form("signup_form"):
            email    = st.text_input("E-postadress")
            password = st.text_input("Välj lösenord (minst 8 tecken)", type="password")
            submitted = st.form_submit_button("Skapa konto", type="primary", use_container_width=True)
        if submitted:
            try:
                do_signup(email, password)
                st.success("Konto skapat! Logga in i fliken ovan.")
            except Exception as e:
                st.error(f"Fel: {e}")

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
                st.error(f"Fel: {e}")


def page_paywall(user, lead_count: int = 0):
    st.title("Scout")
    st.caption("Takidentifiering & Leadsgenerering · Linus Bergström")
    st.divider()

    if lead_count < 10:
        remaining = 10 - lead_count
        st.info(f"Du har {remaining} gratis leads kvar av 10. Aktivera Scout för obegränsad tillgång.")
        st.progress(lead_count / 10)

    params = st.query_params

    # Återvänd från lyckad betalning
    if params.get("subscribed") == "true":
        profile = get_profile(str(user.id))
        if has_access(profile):
            st.query_params.clear()
            st.rerun()
        else:
            st.info("Betalning mottagen — aktiverar ditt konto (tar 10–30 sek)...")
            if st.button("Kontrollera igen", type="primary"):
                st.rerun()
            st.caption("Stäng inte webbläsaren.")
            return

    if params.get("canceled") == "true":
        st.query_params.clear()
        st.warning("Betalningen avbröts — inget debiterades.")

    st.markdown("### Välj plan — 14 dagar gratis, avsluta när som helst")
    st.caption("Kreditkort krävs vid registrering. Inget debiteras förrän trialen löper ut.")
    st.divider()

    plans = [
        {
            "name": "Starter",
            "price": "499 kr/mån",
            "seats": "1 användare",
            "features": ["Obegränsade fastighetssökningar", "Satellitvy", "Leadslista + CSV-export", "Google & Hitta.se-länkar"],
            "price_id": STRIPE_PRICE_STARTER,
            "cta": "Starta Starter →",
        },
        {
            "name": "Team",
            "price": "1 990 kr/mån",
            "seats": "Upp till 5 användare",
            "features": ["Allt i Starter", "Delade leadlistor", "Teamöversikt", "Prioriterad support"],
            "price_id": STRIPE_PRICE_TEAM,
            "cta": "Starta Team →",
        },
        {
            "name": "Growth",
            "price": "3 990 kr/mån",
            "seats": "Upp till 15 användare",
            "features": ["Allt i Team", "Territoriedelning per rep", "Statistik per användare", "Onboarding-samtal"],
            "price_id": STRIPE_PRICE_GROWTH,
            "cta": "Starta Growth →",
        },
    ]

    cols = st.columns(3)
    for col, plan in zip(cols, plans):
        with col:
            st.markdown(f"**{plan['name']}**")
            st.markdown(f"### {plan['price']}")
            st.caption(plan["seats"])
            st.divider()
            for f in plan["features"]:
                st.markdown(f"✓ {f}")
            st.divider()
            if plan["price_id"] and STRIPE_SECRET_KEY:
                try:
                    url = create_checkout_url(user.email, str(user.id), plan["price_id"])
                    st.link_button(plan["cta"], url, type="primary", use_container_width=True)
                except Exception as e:
                    st.error(f"Stripe-fel: {e}")
            else:
                st.button(plan["cta"], disabled=True, use_container_width=True)

    st.divider()
    if st.button("Logga ut", use_container_width=True):
        do_logout()
        st.rerun()


ANTHROPIC_API_KEY = _secret("SOLAR_SCOUT_ANTHROPIC_KEY") or _secret("ANTHROPIC_API_KEY")


def page_scanner(user):
    st.subheader("AI Scanner — Hitta solcellstak automatiskt")

    mode = st.radio(
        "Sök på",
        ["Ort/stad (ange namn)", "Rita område på karta"],
        horizontal=True,
    )

    south = west = north = east = None
    city_name = ""

    if mode == "Ort/stad (ange namn)":
        city_name = st.text_input(
            "Ort eller stad:",
            placeholder="t.ex. Helsingborg, Landskrona",
        )
    else:
        try:
            from streamlit_folium import st_folium
            import folium
            from folium.plugins import Draw

            m = folium.Map(location=[56.0, 13.0], zoom_start=13)
            Draw(
                draw_options={
                    "rectangle": True,
                    "polygon": False,
                    "circle": False,
                    "marker": False,
                    "polyline": False,
                    "circlemarker": False,
                },
                edit_options={"edit": False},
            ).add_to(m)
            output = st_folium(m, width="100%", height=450, returned_objects=["last_active_drawing"])

            drawing = (output or {}).get("last_active_drawing")
            if drawing:
                coords = drawing["geometry"]["coordinates"][0]
                lats = [c[1] for c in coords]
                lngs = [c[0] for c in coords]
                south, north = min(lats), max(lats)
                west, east   = min(lngs), max(lngs)
                st.success(f"Område valt: {south:.4f},{west:.4f} → {north:.4f},{east:.4f}")
        except ImportError:
            st.error("streamlit-folium är inte installerat. Kör: pip install streamlit-folium folium")
            return

    img_source = "mapbox" if MAPBOX_TOKEN else ("google" if GOOGLE_API_KEY else None)
    if not img_source:
        st.error("Varken MAPBOX_TOKEN eller GOOGLE_API_KEY är satt.")
        return

    ai_available = bool(ANTHROPIC_API_KEY)
    if not ai_available:
        st.warning("ANTHROPIC_API_KEY saknas — kör i OSM-läge (endast kända solcellstak från OpenStreetMap).")

    if mode == "Rita område på karta" and None not in (south, west, north, east):
        from scanner import _bbox_tiles, ZOOM as _ZOOM
        tc = len(_bbox_tiles(south, west, north, east))
        est_min = max(1, tc // 20)
        st.caption(
            f"Valda området: ~{tc} brickor à 107 m — "
            f"{'OSM direkt' if not ai_available else f'beräknad tid ~{est_min} min med AI'}."
        )

    max_leads = st.number_input("Max antal leads (lämna 0 för obegränsat)", min_value=0, max_value=500, value=30, step=5)
    max_leads = max_leads if max_leads > 0 else None

    start = st.button("Starta scanning", type="primary", use_container_width=True)

    if start:
        if mode == "Ort/stad (ange namn)" and not city_name:
            st.warning("Ange en ort.")
            return
        if mode == "Rita område på karta" and None in (south, west, north, east):
            st.warning("Rita ett område på kartan först.")
            return

        # Clear any previous scan results
        st.session_state.pop("scanner_sb_rows", None)
        st.session_state.pop("scanner_display_rows", None)

        from scanner import scan_city, scan_bbox, Lead

        progress_bar  = st.progress(0.0, text="Startar...")
        status_text   = st.empty()
        found_leads: list[Lead] = []
        found_count_ph = st.empty()

        def on_progress(done: int, total: int, result):
            pct = done / total if total else 1.0
            progress_bar.progress(pct, text=f"Analyserar bricka {done}/{total}...")
            if result:
                found_leads.append(result)
                found_count_ph.info(f"Hittade hittills: {len(found_leads)} solcellstak")

        anthr_key = ANTHROPIC_API_KEY if ai_available else None
        _log.info("scan start mode=%s ai=%s max_leads=%s", mode, bool(anthr_key), max_leads)
        try:
            if mode == "Ort/stad (ange namn)":
                status_text.info("Söker upp ort och hämtar byggnadsdata från OSM (kan ta 20–60 s)...")
                leads = scan_city(city_name, GOOGLE_API_KEY or "", anthr_key, on_progress, mapbox_key=MAPBOX_TOKEN or None, max_leads=max_leads)
            else:
                status_text.info("Hämtar byggnadsdata från OSM (kan ta 20–60 s)...")
                leads = scan_bbox(south, west, north, east, GOOGLE_API_KEY or "", anthr_key, on_progress, mapbox_key=MAPBOX_TOKEN or None, max_leads=max_leads)
        except ValueError as e:
            _log.error("scan ValueError: %s", e)
            st.error(str(e))
            return
        except Exception as e:
            _log.error("scan Exception: %s", e, exc_info=True)
            st.error(f"Fel under scanning: {e}")
            return

        progress_bar.progress(1.0, text="Klar!")
        status_text.empty()
        found_count_ph.empty()

        if not leads:
            st.warning("Inga solcellstak hittades i det valda området.")
            with st.expander("🔍 Debug-info (felsökning)"):
                st.caption("Kontrollera Railway-loggarna för detaljerad info. Vanliga orsaker: 1) OSM saknar solar-taggar för området 2) Inga residential_areas hittades 3) AI-nyckel saknas eller tog slut 4) Alla byggnader filtrerades som icke-villor.")
            return

        display_rows = []
        sb_rows = []
        for lead in leads:
            display_rows.append({
                "Adress":    lead.address,
                "Källa":     "OSM" if lead.source == "osm" else "AI",
                "Konfidens": f"{lead.confidence:.0%}",
                "Lat":       round(lead.lat, 5),
                "Lng":       round(lead.lng, 5),
            })
            sb_rows.append({
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
            })

        # Persist results in session_state so they survive button clicks
        st.session_state["scanner_sb_rows"] = sb_rows
        st.session_state["scanner_display_rows"] = display_rows

    # ── Show results (persisted across reruns) ────────────────────────────────
    sb_rows      = st.session_state.get("scanner_sb_rows")
    display_rows = st.session_state.get("scanner_display_rows")

    if not sb_rows:
        return

    already_saved = st.session_state.get("scanner_saved", False)

    st.success(f"Scanning klar! Hittade {len(sb_rows)} tak med solceller.")
    st.divider()

    df = pd.DataFrame(display_rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    if already_saved:
        st.info(f"✅ {len(sb_rows)} leads sparade i Leadslistan.")
    else:
        col_save, col_dl = st.columns(2)
        with col_save:
            if st.button("💾 Spara alla till Leadslista", type="primary", use_container_width=True):
                sb = get_supabase()
                saved = 0
                for row in sb_rows:
                    data = {k: v for k, v in row.items()}
                    data["user_id"] = str(user.id)
                    try:
                        sb.table("scout_leads").insert(data).execute()
                        saved += 1
                    except Exception as exc:
                        _log.warning("lead insert failed: %s", exc)
                st.session_state["scanner_saved"] = True
                st.success(f"{saved} leads sparade!")
                st.balloons()
                st.rerun()

        with col_dl:
            date_str = datetime.now().strftime("%y%m%d")
            export = pd.DataFrame(sb_rows).drop(columns=["lat", "lng"], errors="ignore")
            csv_bytes = (CSV_ATTRIBUTION_HEADER + export.to_csv(index=False)).encode("utf-8")
            st.download_button(
                "⬇ Ladda ner CSV",
                csv_bytes,
                file_name=f"Scanner_Leads_{date_str}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    if st.button("🔄 Ny scanning", use_container_width=True):
        st.session_state.pop("scanner_sb_rows", None)
        st.session_state.pop("scanner_display_rows", None)
        st.session_state.pop("scanner_saved", None)
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
        st.error(f"Google API-fel: {e}")
        return

    with st.spinner("Söker adress..."):
        try:
            results = gmaps.geocode(search_query)
        except Exception as e:
            st.error(f"Fel: {e}")
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
        st.success("Inget att granska just nu!")
        st.caption("Leads med SOLAR=UNSURE från AI-scanningar hamnar här.")
        return

    total = len(queue)
    lead = queue[0]
    done = max(0, total - len(queue))  # always 0 here — shown via badge elsewhere

    st.markdown(f"### 🔍 Granska lead  &nbsp; <span style='color:grey;font-size:0.85em'>{total} kvar</span>", unsafe_allow_html=True)
    st.progress(0.0 if total == 0 else (1 - total / (total + done)))

    # Satellite image via LM WMS (free, no storage limit)
    lat, lng = lead.get("lat"), lead.get("lng")
    if lat and lng:
        try:
            from scanner import _fetch_lm_wms
            img_bytes = _fetch_lm_wms(lat, lng)
            if img_bytes:
                st.image(img_bytes, use_container_width=True)
            elif MAPBOX_TOKEN:
                st.image(
                    f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
                    f"/{lng},{lat},20/640x480?access_token={MAPBOX_TOKEN}",
                    use_container_width=True,
                )
            else:
                st.caption("(Bild ej tillgänglig)")
        except Exception:
            st.caption("(Kunde inte hämta bild)")

    st.markdown(f"**{lead.get('address', '–')}**")

    reasoning = lead.get("ai_reasoning", "")
    if reasoning:
        st.info(f"AI: *\"{reasoning}\"*")

    if lead.get("maps_url"):
        st.link_button("Öppna i Google Maps", lead["maps_url"])

    st.write("")
    col_ja, col_nej = st.columns(2)
    lead_id = int(lead["id"])
    with col_ja:
        if st.button("✅ Ja, solceller", type="primary", use_container_width=True, key="review_yes"):
            sb.table("scout_leads").update({
                "user_confirmed": True,
                "needs_review": False,
                "has_solar": "Ja",
            }).eq("id", lead_id).execute()
            st.rerun()
    with col_nej:
        if st.button("❌ Nej, hoppa", use_container_width=True, key="review_no"):
            sb.table("scout_leads").update({
                "user_confirmed": False,
                "needs_review": False,
            }).eq("id", lead_id).execute()
            st.rerun()


def page_leads(user):  # noqa: keep user param for confirm_lead calls
    st.subheader("Leadslista")
    df = load_leads(str(user.id))

    if df.empty:
        st.info("Inga leads ännu. Gå till fliken 'Scouta Tak'.")
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
        ["address", "has_solar", "samtomt_solar_extra", "air_to_air", "air_to_water", "notes", "created_at"]
        if c in df.columns]
    rename_map = {
        "address": "Adress", "has_solar": "Solceller",
        "samtomt_solar_extra": "Samtomt sol",
        "air_to_air": "L/L", "air_to_water": "L/V",
        "notes": "Noteringar", "created_at": "Sparad",
    }
    display_df = df[display_cols].rename(columns=rename_map)
    if "Samtomt sol" in display_df.columns:
        display_df["Samtomt sol"] = display_df["Samtomt sol"].apply(_samtomt_icon)
    st.dataframe(
        display_df,
        use_container_width=True, hide_index=True,
    )

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

    with st.expander("🗑 Ta bort en lead"):
        lead_id = st.number_input("Lead-ID (se id-kolumnen i databasen)", min_value=1, step=1)
        if st.button("Ta bort", type="secondary"):
            delete_lead(int(lead_id))
            st.success(f"Lead {lead_id} borttagen.")
            st.rerun()

    st.divider()
    export_df = df.drop(columns=["id", "user_id"], errors="ignore").rename(
        columns={"samtomt_solar_extra": "Samtomt sol"}
    )
    date_str  = datetime.now().strftime("%y%m%d")
    csv_bytes = (CSV_ATTRIBUTION_HEADER + export_df.to_csv(index=False)).encode("utf-8")
    st.download_button(
        "⬇ Ladda ner CSV",
        csv_bytes,
        file_name=f"Scout_Leads_{date_str}.csv",
        mime="text/csv",
        type="primary",
        use_container_width=True,
    )

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
            lat, lng = row.get("lat"), row.get("lng")
            if lat and lng and MAPBOX_TOKEN:
                img_url = (
                    f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
                    f"/{lng},{lat},20/400x300"
                    f"?access_token={MAPBOX_TOKEN}"
                )
                st.image(img_url, use_container_width=True)
            else:
                st.caption("(Ingen bild — Mapbox-nyckel saknas)")

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
        st.write(f"**Konto skapat:** {signup_date}")

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


def page_app(user, profile):
    with st.sidebar:
        st.markdown("**Scout**")
        st.caption("Linus Bergström")
        st.divider()
        st.caption(f"Inloggad som:\n{user.email}")
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
        page_scanner(user)

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

    # Hantera offentliga sidor (innan auth-check)
    if st.query_params.get("page") == "privacy":
        page_privacy()
        return

    user = init_auth()

    if not user:
        page_auth()
        return

    profile = get_profile(str(user.id))
    lead_count = len(load_leads(str(user.id)))

    if not has_access(profile, lead_count):
        page_paywall(user, lead_count)
        return

    page_app(user, profile)


if __name__ == "__main__":
    main()
