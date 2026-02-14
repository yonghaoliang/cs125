import spotipy
from spotipy.oauth2 import SpotifyOAuth
import random

# --- CONFIGURATION ---
CLIENT_ID = '3704f4b49fc344d3a7d9a4a46f24a6ea' 
CLIENT_SECRET = 'e7e685df26f64d09b48ed55683544d54'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'

# --- CONNECT ---
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope="user-library-read"
))

def get_recommendations(activity):
    """
    Asks Spotify for songs that match a specific activity using Smart Search.
    """
    print(f"\n--- FINDING SONGS FOR: {activity.upper()} ---")

    # 1. Map the activity to a "Search Query"
    if activity == "gym":
        query = "workout gym high energy"
    elif activity == "study":
        query = "lofi study focus"
    elif activity == "party":
        query = "party dance hits"
    else:
        query = "top hits"

    # 2. Search for tracks (The New Way)
    # We search for tracks that match our keywords
    results = sp.search(q=query, type='track', limit=10)
    
    # 3. Pick 5 random ones from the top 10 results
    # (This makes it feel different every time you run it)
    items = results['tracks']['items']
    random.shuffle(items)
    selected_tracks = items[:5]

    # 4. Print the results nicely
    for track in selected_tracks:
        name = track['name']
        artist = track['artists'][0]['name']
        link = track['external_urls']['spotify']
        print(f"🎵 {name} - {artist}")
        print(f"   (Link: {link})")

# --- TEST IT ---
get_recommendations("gym")