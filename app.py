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

# ================= 🚨 spotify token 🚨 =================
CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID') 
CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')
REDIRECT_URI = os.getenv('SPOTIPY_REDIRECT_URI')

DATABASE = 'mars_player.db'

#only need to run one time
def init_db_v2():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS schedule (slot TEXT PRIMARY KEY, activity TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS likes (id INTEGER PRIMARY KEY AUTOINCREMENT, artist TEXT, activity TEXT, user_id TEXT)')
        cursor.execute('CREATE TABLE IF NOT EXISTS blacklist (id TEXT, name TEXT, type TEXT, activity TEXT, user_id TEXT)')
        
        try:
            cursor.execute("ALTER TABLE likes ADD COLUMN user_id TEXT")
            print("Added user_id to likes")
        except sqlite3.OperationalError: pass
        
        try:
            cursor.execute("ALTER TABLE blacklist ADD COLUMN user_id TEXT")
            print("Added user_id to blacklist")
        except sqlite3.OperationalError: pass
        conn.commit()

# --- 数据库连接 ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# --- Spotify 连接 ---
# try:
#     sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
#         client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
#         redirect_uri=REDIRECT_URI, scope="user-library-read",
#         requests_timeout=10
#     ))
#     print("✅ Spotify Connected Successfully!")
# except Exception as e:
#     print(f"❌ Connection Error: {e}")
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
# ================= 路由 =================

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        cur = get_db().execute("SELECT slot, activity FROM schedule")
        return jsonify({row['slot']: row['activity'] for row in cur.fetchall()})
    except:
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
    for slot, activity in data.items():
        db.execute("INSERT OR REPLACE INTO schedule (slot, activity) VALUES (?, ?)", (slot, activity))
    db.commit()
    return jsonify({"status": "saved"})

@app.route('/get_music', methods=['POST'])
def get_music():
    data = request.json
    override = data.get('override')
    
    if override:
        activity = override
        source = "Manual Override"
    else:
        h = time.localtime().tm_hour
        slot = 'morning' if 5 <= h < 12 else 'afternoon' if 12 <= h < 18 else 'evening'
        cur = get_db().execute("SELECT activity FROM schedule WHERE slot = ?", (slot,))
        row = cur.fetchone()
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
    artist, tid, activity = data.get('artist'), data.get('track_id'), data.get('activity')
    
    db = get_db()
    db.execute(
        "INSERT INTO blacklist (id, name, type, activity, user_id) VALUES (?, ?, ?, ?, ?)", 
        (tid, 'Track', 'track', 'global', uid)
    )
    
    cur = db.execute(
        "SELECT id FROM blacklist WHERE id=? AND type='artist' AND activity=? AND user_id=?", 
        (artist, activity, uid)
    )
    if not cur.fetchone():
        db.execute(
            "INSERT INTO blacklist (id, name, type, activity, user_id) VALUES (?, ?, ?, ?, ?)", 
            (artist, artist, 'artist', activity, uid)
        )
    db.commit()
    
    new_tracks = fetch_tracks_v7_9(activity, target_count=1, sp=sp)
    return jsonify({"track": new_tracks[0] if new_tracks else None})

@app.route('/toggle_like', methods=['POST'])
def toggle_like():
    sp = get_sp()
    uid = get_uid(sp)
    if not uid:
        return jsonify({"status": "error", "message": "Login required"}), 401

    data = request.json
    artist, activity = data.get('artist'), data.get('activity')
    db = get_db()
    cur = db.execute(
        "SELECT artist FROM likes WHERE artist=? AND activity=? AND user_id=?", 
        (artist, activity, uid)
    )
    
    if cur.fetchone():
        db.execute(
            "DELETE FROM likes WHERE artist=? AND activity=? AND user_id=?", 
            (artist, activity, uid)
        )
        status = "removed"
    else:
        db.execute(
            "INSERT INTO likes (artist, activity, user_id) VALUES (?, ?, ?)", 
            (artist, activity, uid)
        )
        db.execute(
            "DELETE FROM blacklist WHERE id=? AND type='artist' AND activity=? AND user_id=?", 
            (artist, activity, uid)
        )
        status = "added"
    
    db.commit()
    return jsonify({"status": status})


# =================  Offset，No Market ⭐ =================

def fetch_tracks_v7_9(activity, target_count=10, sp=None):
    if not sp: return []
    db = get_db()
    uid = get_uid(sp)
    
    final_tracks = []
    seen_titles = set()

    # --- 1. 获取个性化数据 (统一使用 execute 和正确表名) ---
    try:
        # 获取点赞歌手 (用于增加搜索权重)
        liked_rows = db.execute(
            "SELECT artist FROM likes WHERE activity = ? AND user_id = ?", 
            (activity, uid)
        ).fetchall()
        liked_artists = [row['artist'] for row in liked_rows]

        # 获取拉黑歌手 (来自统一的 blacklist 表)
        banned_artist_rows = db.execute(
            "SELECT id FROM blacklist WHERE type = 'artist' AND (activity = ? OR activity = 'global') AND user_id = ?", 
            (activity, uid)
        ).fetchall()
        banned_artists = {row['id'] for row in banned_artist_rows}

        # 获取拉黑歌曲
        banned_track_rows = db.execute(
            "SELECT id FROM blacklist WHERE type = 'track' AND user_id = ?", 
            (uid,)
        ).fetchall()
        banned_tracks = {row['id'] for row in banned_track_rows}
    except Exception as e:
        print(f"❌ Database error in recommendation: {e}")
        liked_artists, banned_artists, banned_tracks = [], set(), set()

    # --- 2. 关键词池 (保持你的丰富词库) ---
    query_map = {
        'gym': ["Phonk", "Hardstyle", "Workout Hits", "Gym Motivation", "Travis Scott", "Kanye West"],
        'study': ["Lofi Girl", "Chillhop", "Jazz Vibes", "Piano Focus", "Ambient", "Brain Food"], 
        'relax': ["Ed Sheeran", "Taylor Swift", "John Mayer", "Coldplay", "SZA", "Frank Ocean"],
        'commute': ["The Weeknd", "Post Malone", "Dua Lipa", "Harry Styles", "Bad Bunny", "Bruno Mars"]
    }
    
    search_pool = query_map.get(activity, ["Pop"])

    if liked_artists:
        for _ in range(3): 
            search_pool.extend(liked_artists)
    
    random.shuffle(search_pool)

    # --- search loop ---
    max_loops = 10
    for i in range(max_loops):
        if len(final_tracks) >= target_count: break
        
        q_term = search_pool[i % len(search_pool)]
        
        if random.random() > 0.5:
             year = random.choice(['2018-2020', '2021-2023', '2024-2025'])
             q = f"{q_term} year:{year}"
        else:
             q = q_term

        try:
            print(f"🔎 Safe Searching: '{q}'")
            results = sp.search(q=str(q), limit=10, type='track')
            items = results.get('tracks', {}).get('items', [])
            random.shuffle(items)
            
            for item in items:
                tid = item['id']
                name = item['name']
                artist = item['artists'][0]['name']
                
                # 核心过滤：检查 ID 是否在拉黑集合中
                if tid in banned_tracks or artist in banned_artists:
                    continue
                if "sound" in name.lower() or "mix" in name.lower():
                    continue 
                if name in seen_titles:
                    continue 
                
                final_tracks.append({
                    'id': tid, 'name': name, 'artist': artist,
                    'image': item['album']['images'][0]['url'] if item['album']['images'] else '',
                    'link': item['external_urls']['spotify']
                })
                seen_titles.add(name)
                
                if len(final_tracks) >= target_count: break
        except Exception as e:
            print(f"⚠️ Search error for '{q}': {e}")
            continue

    # --- 4. 绝对保底 ---
    if len(final_tracks) < target_count:
        try:
            fallback = sp.search(q="trending", limit=10, type='track')
            for item in fallback.get('tracks', {}).get('items', []):
                 if item['name'] not in seen_titles and len(final_tracks) < target_count:
                    final_tracks.append({
                        'id': item['id'], 'name': item['name'], 'artist': item['artists'][0]['name'],
                        'image': item['album']['images'][0]['url'], 'link': item['external_urls']['spotify']
                    })
        except: pass

    return final_tracks[:target_count]

if __name__ == '__main__':
    init_db_v2() 
    print("Database system initialized with Multi-user support!")            
    app.run(debug=True, port=5000)