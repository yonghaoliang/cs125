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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'mars_player.db')

#only need to run one time
def init_db_v2():
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()
        
        cursor.execute('CREATE TABLE IF NOT EXISTS schedule (slot TEXT PRIMARY KEY, activity TEXT)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                track_id TEXT, 
                artist TEXT, 
                activity TEXT, 
                user_id TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                id TEXT PRIMARY KEY, 
                name TEXT, 
                type TEXT, 
                activity TEXT, 
                user_id TEXT
            )
        ''')
        
        try:

            cursor.execute("ALTER TABLE likes ADD COLUMN track_id TEXT")
            print("Successfully added track_id to likes table")
        except sqlite3.OperationalError:

            pass

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

    tid = data.get('track_id')
    track_name = data.get('name', 'Unknown Track')
    activity = data.get('activity')
    
    db = get_db()
    

    db.execute(
        "INSERT INTO blacklist (id, name, type, activity, user_id) VALUES (?, ?, 'track', 'global', ?)", 
        (tid, track_name, uid)
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
    # 🕵️‍♂️ 确保这里能拿到 track_id
    tid = data.get('track_id') 
    artist = data.get('artist', 'Unknown')
    activity = data.get('activity', 'relax')
    
    print(f"DEBUG SAVE: Saving Track [{tid}] for User [{uid}]")

    db = get_db()
    try:
        # 检查是否已存在
        cur = db.execute("SELECT id FROM likes WHERE track_id=? AND user_id=?", (tid, uid))
        if cur.fetchone():
            db.execute("DELETE FROM likes WHERE track_id=? AND user_id=?", (tid, uid))
            status = "removed"
        else:
            db.execute("INSERT INTO likes (track_id, artist, activity, user_id) VALUES (?, ?, ?, ?)", 
                       (tid, artist, activity, uid))
            status = "added"
        db.commit()
        print(f"✅ DB Success: {status}")
        return jsonify({"status": status})
    except Exception as e:
        print(f"❌ SQL Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/get_liked_songs', methods=['GET'])
def get_liked_songs():
    sp = get_sp()
    uid = get_uid(sp)
    if not uid:
        return jsonify([])

    db = get_db()
    cur = db.execute("SELECT track_id FROM likes WHERE user_id = ?", (uid,))
    rows = cur.fetchall()
    
    if not rows:
        return jsonify([])

    liked_songs = []
    for row in rows:
        tid = row['track_id']
        try:
            # 使用 track 单曲查询，比 tracks 批量查询更不容易触发 403
            track_data = sp.track(tid) 
            liked_songs.append({
                'id': tid,
                'name': track_data['name'],
                'artist': track_data['artists'][0]['name'],
                'image': track_data['album']['images'][0]['url'] if track_data['album']['images'] else '',
                'link': track_data['external_urls']['spotify']
            })
        except Exception as e:
            print(f"⚠️ Skip track {tid} due to error: {e}")
            continue
            
    return jsonify(liked_songs)


# =================  Offset，No Market ⭐ =================

def fetch_tracks_v7_9(activity, target_count=10, sp=None):
    if not sp: return []
    db = get_db()
    uid = get_uid(sp)
    
    final_tracks = []
    seen_titles = set()

    # --- 1. 获取个性化数据 (基于歌曲 ID) ---
    try:
        # 获取点赞歌曲的 ID (用于判断红心是否亮起)
        liked_rows = db.execute(
            "SELECT track_id FROM likes WHERE user_id = ?", 
            (uid,)
        ).fetchall()
        liked_track_ids = {row['track_id'] for row in liked_rows}

        # 获取拉黑歌曲的 ID (全局拉黑)
        banned_rows = db.execute(
            "SELECT id FROM blacklist WHERE type = 'track' AND user_id = ?", 
            (uid,)
        ).fetchall()
        banned_tracks = {row['id'] for row in banned_rows}
        
   
        liked_artist_rows = db.execute(
            "SELECT DISTINCT artist FROM likes WHERE user_id = ?", 
            (uid,)
        ).fetchall()
        liked_artists = [row['artist'] for row in liked_artist_rows]

    except Exception as e:
        print(f"❌ Database error in recommendation: {e}")
        liked_track_ids, banned_tracks, liked_artists = set(), set(), []

    query_map = {
        'gym': ["Phonk", "Hardstyle", "Workout Hits", "Gym Motivation", "Travis Scott"],
        'study': ["Lofi Girl", "Chillhop", "Jazz Vibes", "Piano Focus", "Ambient"], 
        'relax': ["Ed Sheeran", "Taylor Swift", "John Mayer", "Coldplay", "SZA"],
        'commute': ["The Weeknd", "Post Malone", "Dua Lipa", "Harry Styles", "Bad Bunny"]
    }
    
    search_pool = query_map.get(activity, ["Pop"])
    # 将喜欢的歌手加入搜索池，增加搜到他们其他歌曲的概率
    if liked_artists:
        search_pool.extend(liked_artists)
    
    random.shuffle(search_pool)

    # --- 3. 搜索循环 ---
    max_loops = 10
    for i in range(max_loops):
        if len(final_tracks) >= target_count: break
        
        q_term = search_pool[i % len(search_pool)]
        q = f"{q_term} year:2020-2026" if random.random() > 0.5 else q_term

        try:
            results = sp.search(q=str(q), limit=10, type='track')
            items = results.get('tracks', {}).get('items', [])
            random.shuffle(items)
            
            for item in items:
                tid = item['id']
                name = item['name']
                artist = item['artists'][0]['name']
                
                # 核心过滤：只检查这首歌的 ID 是否在黑名单
                if tid in banned_tracks:
                    continue
                
                if name in seen_titles:
                    continue 
                
                # 核心判断：只有当前这首歌的 ID 在喜欢列表里，红心才亮
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
            print(f"⚠️ Search error: {e}")
            continue

    # --- 4. 绝对保底 ---
    if len(final_tracks) < target_count:
        try:
            fallback = sp.search(q="trending", limit=target_count, type='track')
            for item in fallback.get('tracks', {}).get('items', []):
                 tid = item['id']
                 if tid not in banned_tracks and item['name'] not in seen_titles:
                    final_tracks.append({
                        'id': tid, 
                        'name': item['name'], 
                        'artist': item['artists'][0]['name'],
                        'image': item['album']['images'][0]['url'], 
                        'link': item['external_urls']['spotify'],
                        'is_liked': tid in liked_track_ids
                    })
        except: pass

    return final_tracks[:target_count]

if __name__ == '__main__':
    init_db_v2() 
    print("Database system initialized with Multi-user support!")            
    app.run(debug=True, port=5000)