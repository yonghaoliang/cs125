import spotipy
from spotipy.oauth2 import SpotifyOAuth
import random

# --- CONFIGURATION ---
CLIENT_ID = '48cf8953e8064773a01a1a227c84a521'
CLIENT_SECRET = 'af9b479deec240a09fd646478a683781'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'

# --- CONNECT ---
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope="user-library-read"
))