import os
import time
import json
import datetime as dt
import requests
import streamlit as st
import pandas as pd

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, db


# ----------------------------
# CONFIG (à adapter)
# ----------------------------
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "https://project-final-463aa-default-rtdb.europe-west1.firebasedatabase.app/")
FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT", "serviceAccountKey.json")

# Node-RED HTTP endpoint (Node-RED publie ensuite vers MQTT)
NODERED_CMD_URL = os.getenv("NODERED_CMD_URL", "http://172.161.163.190:1880/api/cmd")

# Rafraîchissement
AUTO_REFRESH_SEC = float(os.getenv("AUTO_REFRESH_SEC", "1.5"))
HISTORY_POINTS = int(os.getenv("HISTORY_POINTS", "120"))  # historique léger (mémoire) pour graphe temps réel

NODES = ["node1", "node2"]


# ----------------------------
# Firebase init (singleton)
# ----------------------------
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT)
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
    return True


def fb_get_last(node: str) -> dict | None:
    """
    Lit /iot/<node>/last depuis Firebase Realtime Database.
    """
    ref = db.reference(f"/iot/{node}/last")
    val = ref.get()
    return val


def send_cmd(node: str, payload: dict) -> tuple[bool, str]:
    """
    Envoie une commande à Node-RED (HTTP), qui republie vers MQTT iot/<node>/cmd.
    """
    body = {
        "node": node,        # pour router côté Node-RED
        "payload": payload   # JSON commande
    }
    try:
        r = requests.post(NODERED_CMD_URL, json=body, timeout=3)
        if r.status_code >= 200 and r.status_code < 300:
            return True, f"OK ({r.status_code})"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


def ms_to_datetime(ts_ms: int | float | None):
    if ts_ms is None:
        return None
    try:
        return dt.datetime.fromtimestamp(float(ts_ms) / 1000.0)
    except Exception:
        return None


# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="IoT Multizone Dashboard", layout="wide")

st.title("Dashboard IoT Multizone (Node1 / Node2)")
st.caption("Temps réel (Firebase) + Commandes via Node-RED → MQTT")

init_firebase()

with st.sidebar:
    st.header("Sélection")
    node = st.selectbox("Nœud", NODES, index=1)  # par défaut node2
    st.divider()

    st.subheader("Auto refresh")
    auto = st.toggle("Activer", value=True)
    st.write(f"Période: ~{AUTO_REFRESH_SEC:.1f}s")

    st.divider()
    st.subheader("Paramètres")
    st.text_input("Firebase DB URL", FIREBASE_DB_URL, disabled=True)
    st.text_input("Node-RED CMD URL", NODERED_CMD_URL, disabled=True)

# Session state pour mini-historique (courbe temps réel)
if "hist" not in st.session_state:
    st.session_state["hist"] = {n: pd.DataFrame(columns=["time","temperature","humidity","luminosity","sound","fan_state"]) for n in NODES}


def push_history(node_name: str, last: dict):
    df = st.session_state["hist"][node_name]
    t = ms_to_datetime(last.get("ts")) or dt.datetime.now()
    row = {
        "time": t,
        "temperature": last.get("temperature"),
        "humidity": last.get("humidity"),
        "luminosity": last.get("luminosity"),
        "sound": last.get("sound"),
        "fan_state": last.get("fan_state"),
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = df.dropna(subset=["time"]).sort_values("time")
    # limite taille
    if len(df) > HISTORY_POINTS:
        df = df.iloc[-HISTORY_POINTS:].copy()
    st.session_state["hist"][node_name] = df


# ---- Live fetch ----
last = fb_get_last(node) or {}
if last:
    push_history(node, last)

colA, colB = st.columns([1.1, 0.9], gap="large")

with colA:
    st.subheader(f"Mesures — {node.upper()}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Temp (°C)", f"{last.get('temperature', '—')}")
    c2.metric("Hum (%)", f"{last.get('humidity', '—')}")
    c3.metric("Lum (ADC)", f"{last.get('luminosity', '—')}")
    c4.metric("Son (ADC)", f"{last.get('sound', '—')}")
    fan_val = last.get("fan_state", None)
    c5.metric("Ventilateur", "ON" if fan_val == 1 else ("OFF" if fan_val == 0 else "—"))

    ts_dt = ms_to_datetime(last.get("ts"))
    st.caption(f"Dernière mise à jour: {ts_dt.strftime('%Y-%m-%d %H:%M:%S') if ts_dt else '—'}")

    st.divider()
    st.subheader("Graphiques (mini-historique en mémoire)")

    hist_df = st.session_state["hist"][node]
    if len(hist_df) >= 2:
        st.line_chart(hist_df.set_index("time")[["temperature", "humidity"]])
        st.line_chart(hist_df.set_index("time")[["luminosity", "sound"]])
    else:
        st.info("En attente de quelques points pour tracer les courbes…")

with colB:
    st.subheader("Commandes (bidirectionnel via Node-RED → MQTT)")

    st.markdown("### LED RGB")
    r = st.slider("R", 0, 255, 0)
    g = st.slider("G", 0, 255, 120)
    b = st.slider("B", 0, 255, 255)
    if st.button("Appliquer couleur", use_container_width=True):
        ok, msg = send_cmd(node, {"cmd": "set_rgb", "r": r, "g": g, "b": b})
        st.success(msg) if ok else st.error(msg)

    st.markdown("### Night Mode")
    night = st.toggle("Activer Night Mode", value=False)
    if st.button("Envoyer Night Mode", use_container_width=True):
        ok, msg = send_cmd(node, {"cmd": "night_mode", "enable": bool(night)})
        st.success(msg) if ok else st.error(msg)

    st.markdown("### Forcer l’envoi des données")
    if st.button("Force Send Data", use_container_width=True):
        ok, msg = send_cmd(node, {"cmd": "force_send"})
        st.success(msg) if ok else st.error(msg)

    st.markdown("### Ventilateur")
    thr = st.number_input("Seuil (°C)", min_value=10.0, max_value=60.0, value=27.0, step=0.5)
    hyst = st.number_input("Hystérésis (°C)", min_value=0.0, max_value=10.0, value=1.0, step=0.5)

    cfan1, cfan2 = st.columns(2)
    with cfan1:
        if st.button("Envoyer seuil", use_container_width=True):
            ok, msg = send_cmd(node, {"cmd": "fan_set_threshold", "threshold": float(thr), "hyst": float(hyst)})
            st.success(msg) if ok else st.error(msg)
    with cfan2:
        force_on = st.button("Forcer ON", use_container_width=True)
        force_off = st.button("Forcer OFF", use_container_width=True)
        if force_on:
            ok, msg = send_cmd(node, {"cmd": "fan_force", "enable": True})
            st.success(msg) if ok else st.error(msg)
        if force_off:
            ok, msg = send_cmd(node, {"cmd": "fan_force", "enable": False})
            st.success(msg) if ok else st.error(msg)

st.divider()
with st.expander("Debug: dernière trame brute (Firebase)"):
    st.code(json.dumps(last, indent=2, ensure_ascii=False))

# Auto-refresh simple
if auto:
    time.sleep(AUTO_REFRESH_SEC)
    st.rerun()
