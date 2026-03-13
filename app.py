#MySQL database
import pymysql

import sqlite3
from flask import Flask, render_template, request, jsonify, g, redirect, url_for, session
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import random
import os
import time
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

#MYSQL
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root', 
    'password': os.getenv('MYSQL_PASSWORD'),  
    'database': 'mars_player_db',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

#MYSQL connect
def get_db():
    if 'db' not in g:
        g.db = pymysql.connect(**DB_CONFIG)
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# =================  spotify token  =================
CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID') 
CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')
REDIRECT_URI = os.getenv('SPOTIPY_REDIRECT_URI')

#SQLITE===================================================
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DATABASE = os.path.join(BASE_DIR, 'mars_player.db')

# #only need to run one time
# def init_db_v2():
#     with sqlite3.connect(DATABASE) as conn:
#         cursor = conn.cursor()
        
#         cursor.execute('CREATE TABLE IF NOT EXISTS schedule (slot TEXT PRIMARY KEY, activity TEXT)')
        
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS likes (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT, 
#                 track_id TEXT, 
#                 artist TEXT, 
#                 activity TEXT, 
#                 user_id TEXT
#             )
#         ''')
        
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS blacklist (
#                 id TEXT PRIMARY KEY, 
#                 name TEXT, 
#                 type TEXT, 
#                 activity TEXT, 
#                 user_id TEXT
#             )
#         ''')
        
#         try:

#             cursor.execute("ALTER TABLE likes ADD COLUMN track_id TEXT")
#             print("Successfully added track_id to likes table")
#         except sqlite3.OperationalError:

#             pass

#         conn.commit()

# --- SQLite Database connection ---
# def get_db():
#     db = getattr(g, '_database', None)
#     if db is None:
#         db = g._database = sqlite3.connect(DATABASE)
#         db.row_factory = sqlite3.Row
#     return db

# @app.teardown_appcontext
# def close_connection(exception):
#     db = getattr(g, '_database', None)
#     if db is not None:
#         db.close()

# --- Spotify connection ---
# try:
#     sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
#         client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
#         redirect_uri=REDIRECT_URI, scope="user-library-read",
#         requests_timeout=10
#     ))
#     print(" Spotify Connected Successfully!")
# except Exception as e:
#     print(f" Connection Error: {e}")
#     sp = None

def get_spotify_auth():
    return SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope="user-library-read",
        show_dialog=True
    )

def get_sp():
    auth_manager = get_spotify_auth()
    token_info = auth_manager.cache_handler.get_cached_token()

    if token_info and auth_manager.validate_token(token_info):
        print("Accessing as Logged-in User")
        return spotipy.Spotify(auth_manager=auth_manager)
    
    print("Accessing as Guest")
    from spotipy.oauth2 import SpotifyClientCredentials
    return spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ))

def get_uid(sp):
    """Safely get the current Spotify User ID."""
    if isinstance(sp.auth_manager, SpotifyOAuth):
        try:
            return sp.current_user()['id']
        except Exception as err:
            print(f"DEBUG: Login failed - {err}") 
    return None
# ================= router =================

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT slot, activity FROM schedule")
            rows = cursor.fetchall()
            return jsonify({row['slot']: row['activity'] for row in rows})
    except Exception as e:
        print(f"Schedule Load Error: {e}")
        return jsonify({})

@app.route('/login')
def login():
    auth_manager = get_spotify_auth()
    auth_url = auth_manager.get_authorize_url()
    return redirect(auth_url)

@app.route('/callback')
def callback():
    auth_manager = get_spotify_auth()
    code = request.args.get("code")
    if code:
        auth_manager.get_access_token(code)
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.clear()
    if os.path.exists(".cache"):
        os.remove(".cache")
    return redirect(url_for('home'))

@app.route('/save_schedule', methods=['POST'])
def save_schedule():
    data = request.json
    db = get_db()
    try:
        with db.cursor() as cursor:
            for slot, activity in data.items():
                sql = """
                    INSERT INTO schedule (slot, activity) 
                    VALUES (%s, %s) 
                    ON DUPLICATE KEY UPDATE activity = %s
                """
                cursor.execute(sql, (slot, activity, activity))
        db.commit()
        return jsonify({"status": "saved"})
    except Exception as e:
        print(f"Schedule Save Error: {e}")
        db.rollback()
        return jsonify({"status": "error"}), 500

@app.route('/get_music', methods=['POST'])
def get_music():
    data = request.json
    override = data.get('override')
    db = get_db() 
    
    if override:
        activity = override
        source = "Manual Override"
    else:
        h = time.localtime().tm_hour
        slot = 'morning' if 5 <= h < 12 else 'afternoon' if 12 <= h < 18 else 'evening'
        
        with db.cursor() as cursor:
            cursor.execute("SELECT activity FROM schedule WHERE slot = %s", (slot,))
            row = cursor.fetchone()
            
        activity = row['activity'] if row else 'relax'
        source = f"Schedule ({slot})"

    sp_instance = get_sp()
    uid = get_uid(sp_instance)

    is_logged_in = uid is not None

    tracks = fetch_tracks_v7_9(activity, target_count=10, sp=sp_instance)
    return jsonify({
        "tracks": tracks, 
        "activity": activity, 
        "source": source,
        "is_logged_in": is_logged_in 
    })

@app.route('/dislike_song', methods=['POST'])
def dislike_song():
    sp = get_sp()
    uid = get_uid(sp)
    if not uid:
        return jsonify({"status": "error", "message": "Login required"}), 401

    data = request.json
    tid = data.get('track_id')
    track_name = data.get('name', 'Unknown Track')
    activity = data.get('activity')
    
    db = get_db()
    try:
        with db.cursor() as cursor:
            sql = "INSERT IGNORE INTO blacklist (id, name, type, activity, user_id) VALUES (%s, %s, 'track', 'global', %s)"
            cursor.execute(sql, (tid, track_name, uid))
        db.commit()
        
        new_tracks = fetch_tracks_v7_9(activity, target_count=1, sp=sp)
        return jsonify({"track": new_tracks[0] if new_tracks else None})
    except Exception as e:
        print(f"Blacklist Error: {e}")
        db.rollback()
        return jsonify({"status": "error"}), 500

@app.route('/toggle_like', methods=['POST'])
def toggle_like():
    sp = get_sp()
    uid = get_uid(sp)
    if not uid:
        return jsonify({"status": "error", "message": "Login required"}), 401

    data = request.json
    tid = data.get('track_id') 
    artist = data.get('artist', 'Unknown')
    activity = data.get('activity', 'relax')
    # To make Sonic Archives look better, we can store more information
    name = data.get('name', 'Unknown')
    image = data.get('image', '')

    db = get_db()
    try:
        with db.cursor() as cursor:
            # Check if it already exists
            cursor.execute("SELECT id FROM liked_songs WHERE track_id=%s AND user_id=%s", (tid, uid))
            if cursor.fetchone():
                cursor.execute("DELETE FROM liked_songs WHERE track_id=%s AND user_id=%s", (tid, uid))
                status = "removed"
            else:
                # Insert data into the table you just created
                sql = """INSERT INTO liked_songs (user_id, track_id, track_name, artist_name, album_image, activity) 
                         VALUES (%s, %s, %s, %s, %s, %s)"""
                cursor.execute(sql, (uid, tid, name, artist, image, activity))
                status = "added"
        db.commit() # MySQL commit
        return jsonify({"status": status})
    except Exception as e:
        print(f"MySQL Error: {e}")
        db.rollback()
        return jsonify({"status": "error"}), 500

@app.route('/get_liked_songs', methods=['GET'])
def get_liked_songs():
    sp = get_sp()
    uid = get_uid(sp)
    if not uid:
        return jsonify([])

    db = get_db()
    try:
        with db.cursor() as cursor:
            sql = """
                SELECT 
                    track_id AS id, 
                    track_name AS name, 
                    artist_name AS artist, 
                    album_image AS image 
                FROM liked_songs 
                WHERE user_id = %s
                ORDER BY created_at DESC
            """
            cursor.execute(sql, (uid,))
            liked_songs = cursor.fetchall()
            
            for song in liked_songs:
                song['link'] = f"https://open.spotify.com/track/{song['id']}"
                
            return jsonify(liked_songs)
            
    except Exception as e:
        print(f" Error fetching liked songs from MySQL: {e}")
        return jsonify([])


# =================  Offset，No Market  =================

def fetch_tracks_v7_9(activity, target_count=10, sp=None):
    if not sp: return []
    db = get_db()
    uid = get_uid(sp)
    
    final_tracks = []
    seen_titles = set()

  
    try:
        with db.cursor() as cursor:
            #  track ID
            cursor.execute("SELECT track_id FROM liked_songs WHERE user_id = %s", (uid,))
            liked_track_ids = {row['track_id'] for row in cursor.fetchall()}

            #  ID 
            cursor.execute("SELECT id FROM blacklist WHERE user_id = %s", (uid,))
            banned_tracks = {row['id'] for row in cursor.fetchall()}
            
            cursor.execute("SELECT DISTINCT artist_name FROM liked_songs WHERE user_id = %s", (uid,))
            liked_artists = [row['artist_name'] for row in cursor.fetchall()]

    except Exception as e:
        print(f" MySQL error in recommendation: {e}")
        liked_track_ids, banned_tracks, liked_artists = set(), set(), []

    query_map = {
        'gym': ["Phonk", "Hardstyle", "Workout Hits", "Gym Motivation", "Travis Scott"],
        'study': ["Lofi Girl", "Chillhop", "Jazz Vibes", "Piano Focus", "Ambient"], 
        'relax': ["Ed Sheeran", "Taylor Swift", "John Mayer", "Coldplay", "SZA"],
        'commute': ["The Weeknd", "Post Malone", "Dua Lipa", "Harry Styles", "Bad Bunny"]
    }
    
    search_pool = query_map.get(activity, ["Pop"])
    if liked_artists:
        search_pool.extend(liked_artists)
    
    random.shuffle(search_pool)


    max_loops = 10
    for i in range(max_loops):
        if len(final_tracks) >= target_count: break
        
        q_term = search_pool[i % len(search_pool)]
        # Adding a year filter makes the content fresher.
        q = f"{q_term} year:2020-2026" if random.random() > 0.5 else q_term

        try:
            results = sp.search(q=str(q), limit=10, type='track')
            items = results.get('tracks', {}).get('items', [])
            random.shuffle(items)
            
            for item in items:
                tid = item['id']
                name = item['name']
                artist = item['artists'][0]['name']
                
                if tid in banned_tracks or name in seen_titles:
                    continue 
                
                is_liked = tid in liked_track_ids 

                final_tracks.append({
                    'id': tid, 
                    'name': name, 
                    'artist': artist,
                    'image': item['album']['images'][0]['url'] if item['album']['images'] else '',
                    'link': item['external_urls']['spotify'],
                    'is_liked': is_liked  
                })
                seen_titles.add(name)
                
                if len(final_tracks) >= target_count: break
        except Exception as e:
            print(f"Search error: {e}")
            continue

    return final_tracks[:target_count]

if __name__ == '__main__':
    print("Team Mars Server running on MySQL...")          
    app.run(debug=True, port=5000)