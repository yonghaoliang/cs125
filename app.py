import sqlite3
from flask import Flask, render_template, request, jsonify, g
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import random
import os
import time

app = Flask(__name__)

# ================= 🚨 spotify token 🚨 =================
CLIENT_ID = ''
CLIENT_SECRET = ''
REDIRECT_URI = 'http://127.0.0.1:8888/callback'

DATABASE = 'mars_player.db'

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
try:
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI, scope="user-library-read",
        requests_timeout=10
    ))
    print("✅ Spotify Connected Successfully!")
except Exception as e:
    print(f"❌ Connection Error: {e}")
    sp = None

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

    # ⚡ 这里调用的是 V7.9，绝对没有 Offset 参数 ⚡
    tracks = fetch_tracks_v7_9(activity, target_count=10)
    return jsonify({"tracks": tracks, "activity": activity, "source": source})

@app.route('/dislike_song', methods=['POST'])
def dislike_song():
    data = request.json
    artist, tid, activity = data.get('artist'), data.get('track_id'), data.get('activity')
    
    db = get_db()
    # 记录拉黑
    db.execute("INSERT INTO blacklist (id, name, type, activity) VALUES (?, ?, ?, ?)", (tid, 'Track', 'track', 'global'))
    
    cur = db.execute("SELECT id FROM blacklist WHERE id=? AND type='artist' AND activity=?", (artist, activity))
    if not cur.fetchone():
        db.execute("INSERT INTO blacklist (id, name, type, activity) VALUES (?, ?, ?, ?)", (artist, artist, 'artist', activity))
    db.commit()
    
    # 补货
    new_tracks = fetch_tracks_v7_9(activity, target_count=1)
    return jsonify({"track": new_tracks[0] if new_tracks else None})

@app.route('/toggle_like', methods=['POST'])
def toggle_like():
    data = request.json
    artist, activity = data.get('artist'), data.get('activity')
    db = get_db()
    
    cur = db.execute("SELECT artist FROM likes WHERE artist=? AND activity=?", (artist, activity))
    if cur.fetchone():
        db.execute("DELETE FROM likes WHERE artist=? AND activity=?", (artist, activity))
        status = "removed"
    else:
        db.execute("INSERT INTO likes (artist, activity) VALUES (?, ?)", (artist, activity))
        db.execute("DELETE FROM blacklist WHERE id=? AND type='artist' AND activity=?", (artist, activity))
        status = "added"
    
    db.commit()
    return jsonify({"status": status})


# ================= ⭐ V7.9 防弹版：零 Offset，无 Market ⭐ =================

def fetch_tracks_v7_9(activity, target_count=10):
    if not sp: return []
    db = get_db()
    final_tracks = []
    seen_titles = set()
    
    # 1. 数据库
    try:
        banned_tracks = {r['id'] for r in db.execute("SELECT id FROM blacklist WHERE type='track'").fetchall()}
        banned_artists = {r['id'] for r in db.execute("SELECT id FROM blacklist WHERE type='artist' AND activity=?", (activity,)).fetchall()}
        liked_artists = [r['artist'] for r in db.execute("SELECT artist FROM likes WHERE activity=?", (activity,)).fetchall()]
    except:
        banned_tracks, banned_artists, liked_artists = set(), set(), []

    # 2. 关键词池 (极其丰富，不需要翻页也能不重复)
    query_map = {
        'gym': [
            "Phonk", "Hardstyle", "Workout Hits", "Gym Motivation", "Travis Scott", "Kanye West", 
            "Eminem", "Drake", "21 Savage", "Metro Boomin", "Drift Phonk", "Aggressive Rap", "Metalcore",
            "High Tempo", "Running 170 BPM", "Power Workout", "Beast Mode", "Crossfit Music"
        ],
        'study': [
            "Lofi Girl", "Chillhop", "Jazz Vibes", "Piano Focus", "Hans Zimmer", "Max Richter", 
            "Ludovico Einaudi", "Ambient", "Brain Food", "Study Beats", "Classical Essentials",
            "Deep Focus", "Reading Soundtrack", "Instrumental Study", "Concentration Music"
        ], 
        'relax': [
            "Ed Sheeran", "Taylor Swift", "John Mayer", "Coldplay", "SZA", "Frank Ocean", 
            "Acoustic Pop", "Coffee Shop Vibes", "Neo Soul", "Bedroom Pop", "Lana Del Rey",
            "Sunday Morning", "Chill Hits", "Sleep", "Rainy Day"
        ],
        'commute': [
            "The Weeknd", "Post Malone", "Dua Lipa", "Harry Styles", "Bad Bunny", "Bruno Mars",
            "Billboard Hot 100", "Road Trip", "Sing Along", "Classic Rock", "2000s Hits",
            "Top 50 USA", "Driving Rock", "Carpool Karaoke"
        ]
    }
    
    # 基础池
    search_pool = query_map.get(activity, ["Pop"])

    # 裂变：把你喜欢的歌手加进去 (增加权重，加3次)
    if liked_artists:
        for _ in range(3): 
            search_pool.extend(liked_artists)
    
    # 彻底打乱
    random.shuffle(search_pool)

    # 3. 循环搜索 (只用 Offset=0)
    max_loops = 10
    for i in range(max_loops):
        if len(final_tracks) >= target_count: break
        
        q_term = search_pool[i % len(search_pool)]
        
        # 随机年份 (2018-2025)
        if random.random() > 0.5:
             year = random.choice(['2018-2020', '2021-2023', '2024-2025'])
             q = f"{q_term} year:{year}"
        else:
             q = q_term

        # ⭐ 核心防弹逻辑：Offset 永远为 0，去掉 market 参数 ⭐
        try:
            print(f"🔎 Safe Searching: '{q}'")
            # 注意：这里没有 offset 参数，也没有 market 参数！
            results = sp.search(q=str(q), limit=10, type='track')
            
            items = results.get('tracks', {}).get('items', [])
            random.shuffle(items) # 拿到10首后，内部再打乱
            
            for item in items:
                tid = item['id']
                name = item['name']
                artist = item['artists'][0]['name']
                
                # 过滤
                if tid in banned_tracks or artist in banned_artists: continue
                if "sound" in name.lower() or "mix" in name.lower(): continue 
                if name in seen_titles: continue 
                
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

    # 4. 绝对保底
    if len(final_tracks) < target_count:
        print("⚠️ Using Fallback...")
        fallback_q = "music"
        try:
            fallback = sp.search(q=fallback_q, limit=10, type='track')
            for item in fallback.get('tracks', {}).get('items', []):
                 if item['name'] not in seen_titles:
                    final_tracks.append({
                        'id': item['id'], 'name': item['name'], 'artist': item['artists'][0]['name'],
                        'image': item['album']['images'][0]['url'], 'link': item['external_urls']['spotify']
                    })
        except: pass

    return final_tracks[:target_count]

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        with app.app_context():
            db = get_db()
            db.execute('CREATE TABLE IF NOT EXISTS schedule (slot TEXT PRIMARY KEY, activity TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS likes (artist TEXT, activity TEXT, UNIQUE(artist, activity))')
            db.execute('CREATE TABLE IF NOT EXISTS blacklist (id TEXT, name TEXT, type TEXT, activity TEXT)')
            db.commit()
            print("✅ Database initialized!")
            
    app.run(debug=True, port=5000)