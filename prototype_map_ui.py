"""
PROTOTYPE — throwaway, delete or absorb when done.
Question: Which map/scan UI layout gives least friction?

Run: streamlit run prototype_map_ui.py
Switch variant with the radio button at the top.
"""

import sys
from unittest.mock import MagicMock
for _m in ("supabase", "stripe", "googlemaps", "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_m, MagicMock())

import streamlit as st
import folium
from streamlit_folium import st_folium

st.set_page_config(layout="wide", page_title="MAP UI PROTOTYPE", page_icon="🗺️")

# ── Mock data ──────────────────────────────────────────────────────────────────
LEADS = [
    {"id": 1, "address": "Storgatan 4, Nässjö",    "lat": 57.6530, "lng": 14.6940, "status": "ej_kontaktad", "source": "ai",  "note": ""},
    {"id": 2, "address": "Björkvägen 12, Nässjö",   "lat": 57.6512, "lng": 14.6985, "status": "kontaktad",    "source": "ai",  "note": "Ring på fm"},
    {"id": 3, "address": "Tallstigen 8, Nässjö",    "lat": 57.6558, "lng": 14.6905, "status": "mote_bokat",   "source": "osm", "note": ""},
    {"id": 4, "address": "Ekvägen 3, Eksjö",        "lat": 57.6671, "lng": 14.9725, "status": "ej_kontaktad", "source": "ai",  "note": ""},
    {"id": 5, "address": "Granvägen 17, Vetlanda",  "lat": 57.4290, "lng": 15.0800, "status": "ej_kontaktad", "source": "ai",  "note": ""},
]

STATUS_COLOR = {
    "ej_kontaktad": "#6c757d",
    "kontaktad":    "#fd7e14",
    "mote_bokat":   "#198754",
    "kund":         "#0d6efd",
    "ej_intresserad": "#dc3545",
}
STATUS_LABEL = {
    "ej_kontaktad":   "Ej kontaktad",
    "kontaktad":      "Kontaktad",
    "mote_bokat":     "Möte bokat",
    "kund":           "Kund",
    "ej_intresserad": "Ej intresserad",
}

IMG_PLACEHOLDER = "https://placehold.co/96x96/2d6a4f/ffffff?text=Tak"

CENTER = [57.655, 14.700]


def _map(height=420, draw=True):
    m = folium.Map(
        location=CENTER,
        zoom_start=12,
        tiles="https://api.maptiler.com/maps/satellite/{z}/{x}/{y}.jpg?key=demo",
        attr="© MapTiler © OpenStreetMap",
    )
    # Fallback to CartoDB dark if satellite key not available
    folium.TileLayer(
        tiles="CartoDB positron",
        name="Ljus",
        overlay=False,
    ).add_to(m)
    folium.TileLayer(
        tiles="CartoDB dark_matter",
        name="Mörk",
        overlay=False,
    ).add_to(m)
    folium.LayerControl().add_to(m)

    for lead in LEADS:
        color = STATUS_COLOR.get(lead["status"], "#999")
        folium.CircleMarker(
            location=[lead["lat"], lead["lng"]],
            radius=10,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            tooltip=folium.Tooltip(
                f"<b>{lead['address']}</b><br>"
                f"<img src='{IMG_PLACEHOLDER}' style='width:80px;border-radius:4px'><br>"
                f"<span style='color:{color}'>{STATUS_LABEL[lead['status']]}</span>"
            ),
            popup=folium.Popup(lead["address"], max_width=200),
        ).add_to(m)

    if draw:
        from folium.plugins import Draw
        Draw(
            draw_options={"rectangle": True, "polygon": True, "circle": False,
                          "polyline": False, "marker": False, "circlemarker": False},
            edit_options={"edit": False},
        ).add_to(m)

    return m


def _leads_table(compact=False):
    for lead in LEADS:
        col_img, col_info, col_status = st.columns([1, 3, 2])
        with col_img:
            st.image(IMG_PLACEHOLDER, width=72)
        with col_info:
            st.markdown(f"**{lead['address']}**")
            if not compact:
                st.caption(f"Källa: {lead['source'].upper()}")
            lead["note"] = st.text_input(
                "Notering", value=lead["note"],
                key=f"note_{lead['id']}", label_visibility="collapsed",
                placeholder="Snabbnotering…",
            )
        with col_status:
            lead["status"] = st.selectbox(
                "Status", list(STATUS_LABEL.keys()),
                index=list(STATUS_LABEL.keys()).index(lead["status"]),
                format_func=lambda s: STATUS_LABEL[s],
                key=f"status_{lead['id']}", label_visibility="collapsed",
            )
        st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT SWITCHER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown(
    "<div style='background:#1a1a2e;color:#e2e8f0;padding:8px 16px;border-radius:6px;"
    "margin-bottom:12px;font-size:12px'>⚠️ PROTOTYPE — throwaway code</div>",
    unsafe_allow_html=True,
)

variant = st.radio(
    "Layoutvariant",
    ["A — Status quo (5 flikar)", "B — Konsoliderad (4 flikar)", "C — Karta-först (sidebar)"],
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# VARIANT A — Status quo
# ══════════════════════════════════════════════════════════════════════════════
if variant.startswith("A"):
    st.caption("**Variant A** — Nuläge. 5 flikar inkl. 'Scouta Tak' som egen flik. Karta inuti Scanner-fliken.")
    tabs = st.tabs(["🔍 AI Scanner", "🏠 Scouta Tak", "📋 Leads", "🔎 Granska", "⚙ Konto"])

    with tabs[0]:
        st.subheader("AI Scanner")
        col1, col2 = st.columns([2, 1])
        with col1:
            city = st.text_input("Stad", "Nässjö")
            st.button("🚀 Starta scan", type="primary")
        with col2:
            st.metric("Leads hittade", 5)
        st_folium(_map(), height=380, use_container_width=True)

    with tabs[1]:
        st.subheader("Scouta enskild adress")
        st.info("📌 Egna fliken bara för adresssökning. Används sällan.")
        addr = st.text_input("Adress", "Storgatan 4, Nässjö")
        st.button("Sök")

    with tabs[2]:
        st.subheader("Leads")
        _leads_table()

    with tabs[3]:
        st.info("Granska-flik (ej prototypad)")

    with tabs[4]:
        st.info("Konto-flik (ej prototypad)")


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT B — Konsoliderad
# ══════════════════════════════════════════════════════════════════════════════
elif variant.startswith("B"):
    st.caption("**Variant B** — 'Scouta Tak' borttaget som flik → inuti Scanner som expander. Satellit-tile. 4 flikar.")
    tabs = st.tabs(["🔍 Scanner", "📋 Leads (David)", "🔎 Granska", "⚙ Konto"])

    with tabs[0]:
        col_left, col_right = st.columns([1, 3])
        with col_left:
            st.subheader("Scan")
            city = st.text_input("Stad / område", "Nässjö")
            st.button("🚀 Starta scan", type="primary", use_container_width=True)
            st.metric("Leads", 5)
            st.metric("Kostnad ~", "4 kr")
            with st.expander("🏠 Scouta enskild adress"):
                st.text_input("Adress", "Storgatan 4, Nässjö", key="scout_addr")
                st.button("Sök adress")
        with col_right:
            st_folium(_map(height=520), height=520, use_container_width=True)

    with tabs[1]:
        col_filter, col_list = st.columns([1, 3])
        with col_filter:
            st.subheader("Filter")
            st.selectbox("Stad", ["Alla", "Nässjö", "Eksjö", "Vetlanda"])
            st.selectbox("Status", ["Alla"] + list(STATUS_LABEL.values()))
            st.selectbox("Sortera", ["Nyast", "Äldst", "Stad A–Ö"])
            st.button("📥 Exportera Excel", use_container_width=True)
        with col_list:
            st.subheader("Leads")
            _leads_table()

    with tabs[2]:
        st.info("Granska (ej prototypad)")
    with tabs[3]:
        st.info("Konto (ej prototypad)")


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT C — Karta-först (sidebar)
# ══════════════════════════════════════════════════════════════════════════════
elif variant.startswith("C"):
    st.caption("**Variant C** — Karta dominerar (70%). Leads som sidopanel. Inga scanner-tabs, allt i ett.")

    with st.sidebar:
        st.subheader("☀️ Solar Scout")
        st.divider()
        mode = st.radio("Läge", ["Scan", "Leads", "Granska"], label_visibility="collapsed")

        if mode == "Scan":
            city = st.text_input("Stad", "Nässjö")
            st.button("🚀 Starta scan", type="primary", use_container_width=True)
            with st.expander("🏠 Enskild adress"):
                st.text_input("Adress", key="sc_addr")
                st.button("Sök")
            st.divider()
            st.metric("Leads denna scan", 5)
            st.metric("Kostnad", "~4 kr")

        elif mode == "Leads":
            st.selectbox("Stad", ["Alla", "Nässjö", "Eksjö"])
            st.selectbox("Status", ["Alla"] + list(STATUS_LABEL.values()))
            st.button("📥 Excel", use_container_width=True)
            st.divider()
            for lead in LEADS:
                with st.container():
                    c1, c2 = st.columns([1, 3])
                    with c1:
                        st.image(IMG_PLACEHOLDER, width=52)
                    with c2:
                        st.caption(lead["address"])
                        lead["status"] = st.selectbox(
                            "", list(STATUS_LABEL.keys()),
                            index=list(STATUS_LABEL.keys()).index(lead["status"]),
                            format_func=lambda s: STATUS_LABEL[s],
                            key=f"cs_{lead['id']}", label_visibility="collapsed",
                        )

        else:
            st.info("Granska (ej prototypad)")

    # Main area = full-width map
    st_folium(_map(height=700, draw=(mode == "Scan")), height=700, use_container_width=True)
