""" 
Scout – Takidentifiering & Leadsgenerering
Linus Bergström
"""

import io
import os
import re
import time
import threading
import logging
import urllib.parse
import httpx
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