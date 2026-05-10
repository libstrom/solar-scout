"""
Scout – Takidentifiering & Leadsgenerering
Linus Bergström
"""

import os
import stripe
import pandas as pd
import streamlit as st
from datetime import datetime
from supabase import create_client, Client

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

stripe.api_key = STRIPE_SECRET_KEY

@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# ── Auth ──────────────────────────────────────────────────────────────────────

def init_auth():
    """Återställ session från session_state om tokens finns."""
    sb = get_supabase()
    if st.session_state.get("access_token"):
        try:
            sb.auth.set_session(
                st.session_state["access_token"],
                st.session_state["refresh_token"],
            )
            return sb.auth.get_user().user
        except Exception:
            st.session_state.pop("access_token", None)
            st.session_state.pop("refresh_token", None)
    return None


def do_login(email: str, password: str):
    sb = get_supabase()
    resp = sb.auth.sign_in_with_password({"email": email, "password": password})
    st.session_state["access_token"]  = resp.session.access_token
    st.session_state["refresh_token"] = resp.session.refresh_token
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

# ── Profil & subscription ─────────────────────────────────────────────────────

def get_profile(user_id: str) -> dict:
    sb = get_supabase()
    resp = sb.table("profiles").select("*").eq("user_id", user_id).maybe_single().execute()
    return resp.data or {}


def is_subscribed(profile: dict) -> bool:
    return profile.get("scout_subscription_status") == "active"

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

# ── Sidor ─────────────────────────────────────────────────────────────────────

def page_auth():
    st.title("Scout")
    st.caption("Takidentifiering & Leadsgenerering · Linus Bergström")
    st.divider()

    tab_in, tab_up = st.tabs(["Logga in", "Skapa konto"])

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


def page_paywall(user):
    st.title("Scout")
    st.caption("Takidentifiering & Leadsgenerering · Linus Bergström")
    st.divider()

    params = st.query_params

    # Återvänd från lyckad betalning
    if params.get("subscribed") == "true":
        profile = get_profile(str(user.id))
        if is_subscribed(profile):
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
            "features": ["Obegränsade fastighetssökningar", "Satellitvy", "Leadslista + CSV-export", "MrKoll & Hitta.se-länkar"],
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


def page_scout(user):
    st.subheader("Scouta Tak")

    search_query = st.text_input(
        "Adress eller koordinater:",
        placeholder="t.ex. Stationsvägen 17, Kågeröd",
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

        search_enc = address.replace(" ", "+")
        mrkoll_url = f"https://mrkoll.se/resultat?address={search_enc}"
        hitta_url  = f"https://www.hitta.se/sök?vad={search_enc}"

        st.write("**Hämta ägarinfo:**")
        lc1, lc2 = st.columns(2)
        lc1.link_button("MrKoll", mrkoll_url)
        lc2.link_button("Hitta.se", hitta_url)

        if st.button("💾 Spara lead", type="primary", use_container_width=True):
            save_lead(str(user.id), {
                "address":      address,
                "has_solar":    has_solar,
                "air_to_air":   str(air_to_air),
                "air_to_water": str(air_to_water),
                "notes":        notes,
                "mrkoll_url":   mrkoll_url,
                "maps_url":     f"https://www.google.com/maps/search/?api=1&query={lat},{lng}",
            })
            st.success(f"Sparad: {address}")
            st.balloons()


def page_leads(user):
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

    filter_solar = st.radio(
        "Filtrera", ["Alla", "Med solceller", "Utan solceller"], horizontal=True
    )
    if filter_solar == "Med solceller":
        df = df[df["has_solar"] == "Ja"]
    elif filter_solar == "Utan solceller":
        df = df[df["has_solar"] != "Ja"]

    display_cols = [c for c in
        ["address", "has_solar", "air_to_air", "air_to_water", "notes", "created_at"]
        if c in df.columns]
    st.dataframe(
        df[display_cols].rename(columns={
            "address": "Adress", "has_solar": "Solceller",
            "air_to_air": "L/L", "air_to_water": "L/V",
            "notes": "Noteringar", "created_at": "Sparad",
        }),
        use_container_width=True, hide_index=True,
    )

    with st.expander("Ta bort en lead"):
        lead_id = st.number_input("Lead-ID (se id-kolumnen i databasen)", min_value=1, step=1)
        if st.button("Ta bort", type="secondary"):
            delete_lead(int(lead_id))
            st.success(f"Lead {lead_id} borttagen.")
            st.rerun()

    st.divider()
    export_df = df.drop(columns=["id", "user_id"], errors="ignore")
    date_str  = datetime.now().strftime("%y%m%d")
    st.download_button(
        "⬇ Ladda ner CSV",
        export_df.to_csv(index=False).encode("utf-8"),
        file_name=f"Scout_Leads_{date_str}.csv",
        mime="text/csv",
        type="primary",
        use_container_width=True,
    )


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

    tab_scout, tab_leads = st.tabs(["🏠 Scouta Tak", "📋 Leads"])

    with tab_scout:
        page_scout(user)

    with tab_leads:
        page_leads(user)


# ── Huvudprogram ──────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Scout · Linus Bergström",
        page_icon="🔍",
        layout="wide",
    )

    user = init_auth()

    if not user:
        page_auth()
        return

    profile = get_profile(str(user.id))

    if not is_subscribed(profile):
        page_paywall(user)
        return

    page_app(user, profile)


if __name__ == "__main__":
    main()
