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

# --- 路由 ---
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
        source = "Manual"
    else:
        h = time.localtime().tm_hour
        slot = 'morning' if 5 <= h < 12 else 'afternoon' if 12 <= h < 18 else 'evening'
        cur = get_db().execute("SELECT activity FROM schedule WHERE slot = ?", (slot,))
        row = cur.fetchone()
        activity = row['activity'] if row else 'relax'
        source = f"Schedule ({slot})"

    # 调用 V7.2 引擎
    tracks = fetch_tracks_v7_2(activity, target_count=10)
    return jsonify({"tracks": tracks, "activity": activity, "source": source})

@app.route('/dislike_song', methods=['POST'])
def dislike_song():
    data = request.json
    artist, tid, activity = data.get('artist'), data.get('track_id'), data.get('activity')
    
    db = get_db()
    # 1. 拉黑歌曲
    db.execute("INSERT INTO blacklist (id, name, type, activity) VALUES (?, ?, ?, ?)", (tid, 'Track', 'track', 'global'))
    
    # 2. 如果连续遇到不喜欢的艺人，拉黑该艺人
    cur = db.execute("SELECT id FROM blacklist WHERE id=? AND type='artist' AND activity=?", (artist, activity))
    if not cur.fetchone():
        db.execute("INSERT INTO blacklist (id, name, type, activity) VALUES (?, ?, ?, ?)", (artist, artist, 'artist', activity))
    db.commit()
    
    # 补货
    new_tracks = fetch_tracks_v7_2(activity, target_count=1)
    return jsonify({"track": new_tracks[0] if new_tracks else None})

@app.route('/toggle_like', methods=['POST'])
def toggle_like():
    data = request.json
    artist, activity = data.get('artist'), data.get('activity')
    db = get_db()
    
    # 检查是否已经喜欢了
    cur = db.execute("SELECT artist FROM likes WHERE artist=? AND activity=?", (artist, activity))
    if cur.fetchone():
        # 如果已经喜欢，再次点击则是取消喜欢
        db.execute("DELETE FROM likes WHERE artist=? AND activity=?", (artist, activity))
        status = "removed"
    else:
        # 添加喜欢
        db.execute("INSERT INTO likes (artist, activity) VALUES (?, ?)", (artist, activity))
        # 既然喜欢了，就把他从黑名单里放出来（如果误拉黑的话）
        db.execute("DELETE FROM blacklist WHERE id=? AND type='artist' AND activity=?", (artist, activity))
        status = "added"
    
    db.commit()
    print(f"❤️ User liked/unliked: {artist} in {activity} mode -> {status}")
    return jsonify({"status": status})

# --- ⭐ V7.2 核心：VIP 通道 + 混合推荐 ⭐ ---
def fetch_tracks_v7_2(activity, target_count=10):
    if not sp: return []
    db = get_db()
    final_tracks = []
    
    # 1. 准备黑名单
    banned_tracks = {r['id'] for r in db.execute("SELECT id FROM blacklist WHERE type='track'").fetchall()}
    banned_artists = {r['id'] for r in db.execute("SELECT id FROM blacklist WHERE type='artist' AND activity=?", (activity,)).fetchall()}
    
    # 2. 准备关键词
    query_map = {
        'gym': ["Workout 2024", "Phonk", "Hardstyle", "Gym Motivation", "Hip Hop Workout"],
        'study': ["Lofi Girl", "Deep Focus", "Piano Study", "Classical Essentials"], 
        'relax': ["Chill Hits", "Acoustic Pop", "Ed Sheeran", "Coffee Shop"],
        'commute': ["Top 50 USA", "Billboard Hot 100", "Viral Hits", "Road Trip"]
    }
    
    # --- ⭐ STEP 0: VIP 通道 (优先抓取喜欢的艺人) ⭐ ---
    # 查找当前场景下有没有喜欢的艺人
    liked_artists_rows = db.execute("SELECT artist FROM likes WHERE activity=?", (activity,)).fetchall()
    liked_artists = [row['artist'] for row in liked_artists_rows]
    
    # 如果有喜欢的艺人，我们先拿 30% 的名额给他们 (比如 3 首歌)
    if liked_artists:
        # 随机挑 1-2 个喜欢的艺人
        chosen_vips = random.sample(liked_artists, min(len(liked_artists), 2))
        
        for vip in chosen_vips:
            if len(final_tracks) >= 3: break # VIP 名额限制
            try:
                # 专门搜这个艺人
                print(f"🌟 Boosting VIP Artist: {vip}")
                results = sp.search(q=f"artist:{vip}", limit=5, type='track', market='US')
                items = results.get('tracks', {}).get('items', [])
                random.shuffle(items)
                
                for item in items:
                    tid = item['id']
                    if tid not in banned_tracks:
                        final_tracks.append({
                            'id': tid, 'name': item['name'], 'artist': item['artists'][0]['name'],
                            'image': item['album']['images'][0]['url'] if item['album']['images'] else '',
                            'link': item['external_urls']['spotify']
                        })
                        break # 每个 VIP 艺人只取 1 首，避免刷屏
            except: pass

    # --- STEP 1: 探索通道 (原来的逻辑) ---
    # 剩下的名额 (10 - 已有的VIP歌曲)，去公共池子里捞
    queries = query_map.get(activity, ["Top 50 Global"])
    random.shuffle(queries)
    
    max_loops = 3
    for i in range(max_loops):
        if len(final_tracks) >= target_count: break
        
        q = queries[i % len(queries)]
        try_offset = random.choice([0, 10, 20]) # 随机翻页
        
        try:
            results = sp.search(q=q, limit=10, offset=try_offset, type='track', market='US')
            items = results.get('tracks', {}).get('items', [])
            random.shuffle(items)
            
            for item in items:
                tid = item['id']
                artist = item['artists'][0]['name']
                name = item['name']
                
                if tid in banned_tracks or artist in banned_artists: continue
                if "sounds" in artist.lower(): continue
                
                # 查重 (既不重复VIP的歌，也不重复自己的歌)
                if not any(t['id'] == tid for t in final_tracks):
                    final_tracks.append({
                        'id': tid, 'name': name, 'artist': artist,
                        'image': item['album']['images'][0]['url'] if item['album']['images'] else '',
                        'link': item['external_urls']['spotify']
                    })
                
                if len(final_tracks) >= target_count: break
        except: continue

    # --- STEP 2: 混合 ---
    # 把 VIP 歌曲和探索歌曲混在一起，这样用户不会觉得突兀
    random.shuffle(final_tracks)
    
    # --- STEP 3: 保底 ---
    if len(final_tracks) < target_count:
        try:
            fallback = sp.search(q="Top 50 USA", limit=10, type='track', market='US')
            for item in fallback.get('tracks', {}).get('items', []):
                tid = item['id']
                if not any(t['id'] == tid for t in final_tracks):
                    final_tracks.append({
                        'id': tid, 'name': item['name'], 'artist': item['artists'][0]['name'],
                        'image': item['album']['images'][0]['url'], 'link': item['external_urls']['spotify']
                    })
                if len(final_tracks) >= target_count: break
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