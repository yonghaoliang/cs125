import sqlite3
from flask import Flask, render_template, request, jsonify, g
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import random
import os
import time

app = Flask(__name__)

# ================= 🚨 你的密钥 🚨 =================
CLIENT_ID = '48cf8953e8064773a01a1a227c84a521' 
CLIENT_SECRET = 'af9b479deec240a09fd646478a683781'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'

DATABASE = 'mars_player.db'

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

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/get_schedule', methods=['GET'])
def get_schedule():
    try:
        cur = get_db().execute("SELECT slot, activity FROM schedule")
        return jsonify({row['slot']: row['activity'] for row in cur.fetchall()})
    except: return jsonify({})

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
        source = "Manual"
    else:
        h = time.localtime().tm_hour
        slot = 'morning' if 5 <= h < 12 else 'afternoon' if 12 <= h < 18 else 'evening'
        cur = get_db().execute("SELECT activity FROM schedule WHERE slot = ?", (slot,))
        row = cur.fetchone()
        activity = row['activity'] if row else 'relax'
        source = f"Schedule ({slot})"

    tracks = fetch_tracks_v7_3(activity, target_count=10)
    return jsonify({"tracks": tracks, "activity": activity, "source": source})

@app.route('/dislike_song', methods=['POST'])
def dislike_song():
    data = request.json
    artist, tid, activity = data.get('artist'), data.get('track_id'), data.get('activity')
    db = get_db()
    db.execute("INSERT INTO blacklist (id, name, type, activity) VALUES (?, ?, ?, ?)", (tid, 'Track', 'track', 'global'))
    cur = db.execute("SELECT id FROM blacklist WHERE id=? AND type='artist' AND activity=?", (artist, activity))
    if not cur.fetchone():
        db.execute("INSERT INTO blacklist (id, name, type, activity) VALUES (?, ?, ?, ?)", (artist, artist, 'artist', activity))
    db.commit()
    new_tracks = fetch_tracks_v7_3(activity, target_count=1)
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

# --- ⭐ V7.3 核心：关键词多元化 + 强制去重 ⭐ ---
def fetch_tracks_v7_3(activity, target_count=10):
    if not sp: return []
    db = get_db()
    final_tracks = []
    seen_titles = set() # 用来记录已经拿到过的歌名，防止重复
    
    banned_tracks = {r['id'] for r in db.execute("SELECT id FROM blacklist WHERE type='track'").fetchall()}
    banned_artists = {r['id'] for r in db.execute("SELECT id FROM blacklist WHERE type='artist' AND activity=?", (activity,)).fetchall()}

    # ⭐ 1. 关键词升级：不再用笼统词，改用具体的流派和艺人 ⭐
    # 这样搜出来的歌就不会全都叫 "Gym Motivation" 了
    query_map = {
        'gym': [
            "genre:hip-hop", "Eminem", "Phonk", "Hardstyle", 
            "Travis Scott", "Metallica", "Tech House", "Workout Hits", "Kanye West"
        ],
        'study': [
            "Hans Zimmer", "Ludovico Einaudi", "Lo-Fi Beats", 
            "Jazz Vibes", "Piano", "Ambient", "Deep Focus", "Mozart"
        ], 
        'relax': [
            "Ed Sheeran", "John Mayer", "Coldplay", "Taylor Swift", 
            "Acoustic", "R&B", "Neo Soul", "Chill Pop"
        ],
        'commute': [
            "The Weeknd", "Dua Lipa", "Post Malone", "Harry Styles", 
            "Billboard Hot 100", "Road Trip", "Classic Rock", "2000s Hits"
        ]
    }

    # --- VIP 通道 (保持不变) ---
    liked_artists_rows = db.execute("SELECT artist FROM likes WHERE activity=?", (activity,)).fetchall()
    liked_artists = [row['artist'] for row in liked_artists_rows]
    if liked_artists:
        chosen_vips = random.sample(liked_artists, min(len(liked_artists), 2))
        for vip in chosen_vips:
            if len(final_tracks) >= 3: break
            try:
                results = sp.search(q=f"artist:{vip}", limit=5, type='track', market='US')
                items = results.get('tracks', {}).get('items', [])
                random.shuffle(items)
                for item in items:
                    if item['id'] not in banned_tracks and item['name'] not in seen_titles:
                        final_tracks.append({
                            'id': item['id'], 'name': item['name'], 'artist': item['artists'][0]['name'],
                            'image': item['album']['images'][0]['url'] if item['album']['images'] else '',
                            'link': item['external_urls']['spotify']
                        })
                        seen_titles.add(item['name']) # 记录歌名
                        break
            except: pass

    # --- 探索通道 (升级版) ---
    # 随机打乱关键词顺序，每次搜不一样的词
    queries = query_map.get(activity, ["Pop"])
    random.shuffle(queries)

    max_loops = 5 # 多循环几次，因为过滤条件变严了
    for i in range(max_loops):
        if len(final_tracks) >= target_count: break
        
        q = queries[i % len(queries)] # 轮换关键词
        
        # 偶尔加个年份限制，让结果更新鲜
        if random.random() > 0.7:
            q = q + " year:2023-2025"

        try_offset = random.choice([0, 10, 20])
        
        try:
            results = sp.search(q=q, limit=10, offset=try_offset, type='track', market='US')
            items = results.get('tracks', {}).get('items', [])
            random.shuffle(items)
            
            for item in items:
                tid = item['id']
                artist = item['artists'][0]['name']
                name = item['name']
                
                if tid in banned_tracks or artist in banned_artists: continue
                if "sounds" in artist.lower() or "loop" in name.lower(): continue

                # ⭐ 强制去重逻辑 ⭐
                # 如果这个歌名之前已经有了 (比如 "Lofi Study" 已经有过一首了)，这首就不要了
                if name in seen_titles: continue
                # 模糊去重：如果新歌名包含旧歌名，或者旧包含新 (比如 "Rain" 和 "Rain Sound")，也不要
                # is_similar = any(name in t or t in name for t in seen_titles)
                # if is_similar: continue 

                if not any(t['id'] == tid for t in final_tracks):
                    final_tracks.append({
                        'id': tid, 'name': name, 'artist': artist,
                        'image': item['album']['images'][0]['url'] if item['album']['images'] else '',
                        'link': item['external_urls']['spotify']
                    })
                    seen_titles.add(name) # 记录歌名
                
                if len(final_tracks) >= target_count: break
        except: continue

    # 保底
    if len(final_tracks) < target_count:
        try:
            fallback = sp.search(q="Top 50 USA", limit=10, type='track', market='US')
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
    app.run(debug=True, port=5000)