#!/usr/bin/env python3
"""
TV Italia - Server IPTV locale
Estrae gli URL HLS reali (con token fresco) dai relinker ufficiali
e serve un file M3U via HTTP per la smart TV.

Uso:
  python tv_italia.py

Poi sulla smart TV (SmartOne IPTV o browser):
  http://<IP_DEL_PC>:8888/playlist.m3u
"""

import re
import json
import time
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import quote

# ============================================================
# CONFIGURAZIONE CANALI
# ============================================================

RAI_CHANNELS = [
    # (numero DTT, nome, cont_id)
    (1,  "Rai 1",        "2606803"),
    (2,  "Rai 2",        "308718"),
    (3,  "Rai 3",        "308709"),
    (21, "Rai 4",        "746966"),
    (23, "Rai 5",        "395276"),
    (24, "Rai Movie",    "747002"),
    (25, "Rai Premium",  "746992"),
    (42, "Rai Gulp",     "746953"),
    (43, "Rai YoYo",     "746899"),
    (54, "Rai Storia",   "746990"),
    (57, "Rai Scuola",   "747011"),
    (100,"Rai News 24",  "1"),
    (101,"Rai Sport",    "358071"),
]

# Mediaset: nuovo CDN (aprile 2026)
MEDIASET_CHANNELS = [
    # (numero DTT, nome, codice canale)
    (4,  "Rete 4",         "r4"),
    (5,  "Canale 5",       "c5"),
    (6,  "Italia 1",       "i1"),
    (20, "20 Mediaset",    "lb"),
    (22, "Iris",           "ki"),
    (27, "Twentyseven",    "ts"),
    (30, "La 5",           "ka"),
    (34, "Cine34",         "b6"),
    (35, "Focus",          "fu"),
    (39, "Top Crime",      "lt"),
    (49, "Italia 2",       "i2"),
    (51, "TGCOM24",        "kf"),
    (60, "Mediaset Extra",  "kq"),
]

# Discovery/WBD: nuovi URL CloudFront (aprile 2026)
DISCOVERY_CHANNELS = [
    # (numero DTT, nome, URL)
    (31, "Real Time",       "https://d3562mgijzx0zq.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-kizqtzpvvl3i8/Realtime_IT.m3u8"),
    (52, "DMAX",            "https://d2j2nqgg7bzth.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-02k1gv1j0ufwn/DMAX_IT.m3u8"),
    (62, "Food Network",    "https://dk3okdd5036kz.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-o4pw0nc02sthz/Foodnetwork_IT.m3u8"),
]

# Altri canali con URL diretti (aggiornati aprile 2026)
OTHER_CHANNELS = [
    # (numero DTT, nome, URL, gruppo)
    (7,  "La7",           "https://viamotionhsi.netplus.ch/live/eds/la7/browser-HLS8/la7.m3u8",  "La7"),
    (28, "TV 2000",       "https://hls-live-tv2000.akamaized.net/hls/live/2028510/tv2000/master.m3u8", "Altro"),
    (29, "La7 Cinema",    "https://viamotionhsi.netplus.ch/live/eds/la7d/browser-HLS8/la7d.m3u8", "La7"),
    (71, "QVC",           "https://qrg.akamaized.net/hls/live/2017383/lsqvc1it/master.m3u8", "Shopping"),
]

RAKUTEN_CHANNELS = [
    (150, "Rakuten Action Movies", "https://87f2e2e5e7624e3bad85da1ca2ed31a7.mediatailor.eu-west-1.amazonaws.com/v1/master/0547f18649bd788bec7b67b746e47670f558b6b2/production-LiveChannel-6067/master.m3u8"),
    (151, "Rakuten Comedy Movies", "https://b8bc6c4b9be64bd6aeb3b92aa8521ed4.mediatailor.eu-west-1.amazonaws.com/v1/master/0547f18649bd788bec7b67b746e47670f558b6b2/production-LiveChannel-6184/master.m3u8"),
    (152, "Rakuten Drama Movies",  "https://f84e0b1628464fab846160df957f269e.mediatailor.eu-west-1.amazonaws.com/v1/master/0547f18649bd788bec7b67b746e47670f558b6b2/production-LiveChannel-6094/master.m3u8"),
    (153, "Rakuten Family Movies", "https://3315fc3e7276420f895e19cf807dbee1.mediatailor.eu-west-1.amazonaws.com/v1/master/0547f18649bd788bec7b67b746e47670f558b6b2/production-LiveChannel-6215/master.m3u8"),
    (154, "Rakuten Top Movies",    "https://d4a4999341764c67a67e9ec9eb3790ab.mediatailor.eu-west-1.amazonaws.com/v1/master/0547f18649bd788bec7b67b746e47670f558b6b2/production-LiveChannel-5984/master.m3u8"),
]

LG_CHANNELS = [
    (200, "Sportitalia", "https://amg01370-italiansportcom-sportitalia-rakuten-3hmdb.amagi.tv/hls/amagi_hls_data_rakutenAA-sportitalia-rakuten/CDN/master.m3u8"),
]

# ============================================================
# ESTRAZIONE URL
# ============================================================

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")


def get_rai_hls(cont_id: str, timeout: int = 10) -> str | None:
    """
    Chiama il relinker Rai con output=16, cattura il 302 redirect.
    L'header Location contiene l'URL HLS con token.
    """
    url = f"https://mediapolis.rai.it/relinker/relinkerServlet.htm?cont={cont_id}&output=16"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    
    try:
        # Non seguire il redirect
        import http.client
        import ssl
        from urllib.parse import urlparse
        
        parsed = urlparse(url)
        
        # Prima richiesta al relinker (potrebbe essere dietro proxy/envoy)
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=timeout, context=ctx)
        conn.request("GET", parsed.path + "?" + parsed.query, 
                     headers={"User-Agent": USER_AGENT})
        resp = conn.getresponse()
        
        # Se 302, prendi Location
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.getheader("Location")
            if location and "video_no_available" not in location:
                # Pulisci eventuali suffissi strani
                location = location.split("mp3:")[0] if "mp3:" in location else location
                conn.close()
                return location
        
        # Se 200, potrebbe essere JSON o HTML con URL dentro
        body = resp.read().decode("utf-8", errors="ignore")
        conn.close()
        
        # Cerca URL HLS nel body
        m = re.search(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', body)
        if m:
            return m.group(1)
        
        # Cerca URL nel JSON
        m = re.search(r'"url"\s*:\s*"([^"]+)"', body)
        if m:
            return m.group(1)
            
    except Exception as e:
        print(f"  [ERRORE Rai] cont={cont_id}: {e}")
    
    return None


def get_mediaset_hls(channel_code: str) -> str:
    """
    Costruisce l'URL Mediaset con il nuovo CDN (live02-seg).
    """
    return f"https://live02-seg.msf.cdn.mediaset.net/live/ch-{channel_code}/{channel_code}-clr.isml/index.m3u8"


def test_url(url: str, timeout: int = 5) -> bool:
    """Testa se un URL risponde con 200."""
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        resp = urlopen(req, timeout=timeout)
        return resp.status == 200
    except:
        return False


def get_pluto_tv_channels() -> list:
    """Recupera la lista dei canali live da Pluto TV (Italia) tramite redirector."""
    try:
        url = "https://api.pluto.tv/v2/channels"
        req = Request(url, headers={"User-Agent": USER_AGENT})
        resp = urlopen(req, timeout=15)
        raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        print(f"  [PLUTO] Canali ricevuti: {len(data)}")
        
        channels = []
        num = 1000
        for c in data:
            name = c.get('name', 'Pluto TV Channel')
            channel_id = c.get('_id', '')
            
            if c.get('isStitched') and channel_id:
                hls_url = f"https://jmp2.uk/plu-{channel_id}.m3u8"
                channels.append((num, name, hls_url, "Pluto TV"))
                num += 1
        print(f"  [PLUTO] Canali aggiunti alla playlist: {len(channels)}")
        if channels:
            print(f"  [PLUTO] Primo URL: {channels[0][2]}")
        return channels
    except Exception as e:
        print(f"  [ERRORE Pluto TV]: {e}")
        return []

# ============================================================
# GENERAZIONE M3U
# ============================================================

def generate_m3u() -> str:
    """Genera il file M3U con URL freschi."""
    lines = ["#EXTM3U"]
    
    print("\n Generazione playlist...")
    print("=" * 50)
    
    # --- RAI ---
    print("\n RAI")
    for num, name, cont_id in RAI_CHANNELS:
        print(f"  {num:3d} {name:20s} ... ", end="", flush=True)
        hls_url = get_rai_hls(cont_id)
        if hls_url:
            print(f"OK")
            lines.append(f'#EXTINF:-1 tvg-chno="{num}" tvg-name="{name}" group-title="RAI",{name}')
            lines.append(hls_url)
        else:
            # Fallback: usa il relinker diretto (funziona in alcuni player)
            print(f" fallback relinker")
            lines.append(f'#EXTINF:-1 tvg-chno="{num}" tvg-name="{name}" group-title="RAI",{name}')
            lines.append(f"https://mediapolis.rai.it/relinker/relinkerServlet.htm?cont={cont_id}&output=16")
    
    # --- MEDIASET ---
    print("\n MEDIASET")
    for num, name, code in MEDIASET_CHANNELS:
        hls_url = get_mediaset_hls(code)
        print(f"  {num:3d} {name:20s} -> {hls_url[:60]}...")
        lines.append(f'#EXTINF:-1 tvg-chno="{num}" tvg-name="{name}" group-title="Mediaset",{name}')
        lines.append(hls_url)
    
    # --- DISCOVERY ---
    print("\n DISCOVERY")
    for num, name, url in DISCOVERY_CHANNELS:
        print(f"  {num:3d} {name:20s}")
        lines.append(f'#EXTINF:-1 tvg-chno="{num}" tvg-name="{name}" group-title="Discovery",{name}')
        lines.append(url)
    
    # --- ALTRI ---
    print("\n ALTRI")
    for num, name, url, group in OTHER_CHANNELS:
        print(f"  {num:3d} {name:20s}")
        lines.append(f'#EXTINF:-1 tvg-chno="{num}" tvg-name="{name}" group-title="{group}",{name}')
        lines.append(url)
        
    # --- RAKUTEN TV ---
    print("\n RAKUTEN TV")
    for num, name, url in RAKUTEN_CHANNELS:
        print(f"  {num:3d} {name:20s}")
        lines.append(f'#EXTINF:-1 tvg-chno="{num}" tvg-name="{name}" group-title="Rakuten TV",{name}')
        lines.append(url)
        
    # --- LG CHANNELS ---
    print("\n LG CHANNELS")
    for num, name, url in LG_CHANNELS:
        print(f"  {num:3d} {name:20s}")
        lines.append(f'#EXTINF:-1 tvg-chno="{num}" tvg-name="{name}" group-title="LG Channels",{name}')
        lines.append(url)

    # --- PLUTO TV (DISABILITATO) ---
    print("\n PLUTO TV - DISABILITATO")
    
    print(f"\n Playlist generata: {len([l for l in lines if l.startswith('#EXTINF')])} canali")
    return "\n".join(lines) + "\n"


# ============================================================
# SERVER HTTP
# ============================================================

TV_HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>TV Italia Web Player</title>
    <style>
        body { margin: 0; background: #000; color: #fff; font-family: sans-serif; height: 100vh; overflow: hidden; }
        #player-container { width: 100vw; height: 100vh; background: #000; position: relative; }
        video { width: 100%; height: 100%; object-fit: contain; background: #000; }

        #touch-overlay {
            display: none;
            position: absolute;
            top: 0; left: 0;
            width: 100%; height: 100%;
            z-index: 10;
        }
        @media (pointer: coarse) {
            #touch-overlay { display: block; }
            video { pointer-events: none; }
        }
        
        #osd {
            position: absolute;
            bottom: 10%;
            left: 5%;
            background: rgba(0, 0, 0, 0.8);
            border-left: 5px solid #00aaff;
            padding: 20px 30px;
            border-radius: 8px;
            font-size: 2.5rem;
            color: #fff;
            opacity: 0;
            transition: opacity 0.3s ease-in-out;
            pointer-events: none;
            z-index: 1000;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            gap: 20px;
        }
        
        .osd-num { font-weight: bold; color: #00aaff; font-size: 3rem; }
        .osd-name { font-weight: normal; }
        
        #error-msg {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: rgba(255, 0, 0, 0.8);
            padding: 20px;
            border-radius: 8px;
            font-size: 1.5rem;
            display: none;
            z-index: 1001;
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
</head>
<body>
    <div id="player-container">
        <video id="video" controls autoplay></video>
        <div id="touch-overlay"></div>
        <div id="osd">
            <span class="osd-num" id="osd-num"></span>
            <span class="osd-name" id="osd-name"></span>
        </div>
        <div id="error-msg">Errore caricamento canali</div>
    </div>

    <script>
        let hls = null;
        let channelsData = [];
        let currentIndex = 0;
        let osdTimeout = null;
        
        const video = document.getElementById('video');
        const osd = document.getElementById('osd');
        const osdNum = document.getElementById('osd-num');
        const osdName = document.getElementById('osd-name');
        const errorMsg = document.getElementById('error-msg');
        
        async function loadChannels(isBackgroundRefresh = false) {
            try {
                const response = await fetch('/playlist.m3u');
                const text = await response.text();
                const lines = text.split('\\n');
                let channels = [];
                let currentChannel = null;
                
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i].trim();
                    if (line.startsWith('#EXTINF')) {
                        const nameMatch = line.match(/tvg-name="([^"]+)"/);
                        const numMatch = line.match(/tvg-chno="([^"]+)"/);
                        const fallbackName = line.split(',').pop();
                        currentChannel = {
                            name: nameMatch ? nameMatch[1] : fallbackName,
                            num: numMatch ? numMatch[1] : "",
                            url: ""
                        };
                    } else if (line && !line.startsWith('#') && currentChannel) {
                        currentChannel.url = line;
                        channels.push(currentChannel);
                        currentChannel = null;
                    }
                }
                
                channelsData = channels;
                
                if (!isBackgroundRefresh) {
                    if (channelsData.length > 0) {
                        try {
                            const res = await fetch('/last_channel');
                            const textVal = await res.text();
                            const lastIdx = parseInt(textVal, 10);
                            if (!isNaN(lastIdx) && lastIdx >= 0 && lastIdx < channelsData.length) {
                                currentIndex = lastIdx;
                            }
                        } catch (e) {
                            console.log("Impossibile recuperare l'ultimo canale");
                        }
                        playCurrentChannel();
                    }
                } else {
                    console.log("Playlist aggiornata in background");
                }
            } catch (e) {
                console.error("Errore nel caricamento della playlist", e);
                if (!isBackgroundRefresh) {
                    errorMsg.style.display = 'block';
                }
            }
        }
        
        function showOSD() {
            if (!channelsData[currentIndex]) return;
            const ch = channelsData[currentIndex];
            osdNum.textContent = ch.num;
            osdName.textContent = ch.name;
            osd.style.opacity = '1';
            
            if (osdTimeout) clearTimeout(osdTimeout);
            osdTimeout = setTimeout(() => {
                osd.style.opacity = '0';
            }, 4000);
        }
        
        function playCurrentChannel() {
            if (channelsData.length === 0) return;
            const url = channelsData[currentIndex].url;
            showOSD();
            
            // Salva l'ultimo canale visualizzato lato server
            fetch('/set_channel?index=' + currentIndex).catch(e => console.log("Errore salvataggio canale", e));
            
            if (Hls.isSupported()) {
                if (hls) hls.destroy();
                hls = new Hls();
                hls.loadSource(url);
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, function() {
                    video.play().catch(e => console.log("Autoplay bloccato", e));
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                video.src = url;
                video.addEventListener('loadedmetadata', function() {
                    video.play().catch(e => console.log("Autoplay bloccato", e));
                });
            }
        }
        
        function changeChannel(direction) {
            if (channelsData.length === 0) return;
            
            if (direction === 'up') {
                currentIndex++;
                if (currentIndex >= channelsData.length) currentIndex = 0;
            } else if (direction === 'down') {
                currentIndex--;
                if (currentIndex < 0) currentIndex = channelsData.length - 1;
            }
            playCurrentChannel();
        }
        
        window.addEventListener('keydown', (e) => {
            const key = e.key || e.code;
            const keyCode = e.keyCode || e.which;
            
            // Array di keyCodes per coprire tutte le marche di TV:
            // 33: PageUp (LG WebOS, PC)
            // 427: ChannelUp (Samsung Tizen)
            // 166: ChannelUp (Android TV / Google TV)
            // 227: ChannelUp (Vecchi Samsung / Altri)
            const isChannelUp = key === 'ArrowUp' || key === 'ChannelUp' || key === 'PageUp' || 
                                [33, 427, 166, 227].includes(keyCode);
                                
            // 34: PageDown (LG WebOS, PC)
            // 428: ChannelDown (Samsung Tizen)
            // 167: ChannelDown (Android TV / Google TV)
            // 228: ChannelDown (Vecchi Samsung / Altri)
            const isChannelDown = key === 'ArrowDown' || key === 'ChannelDown' || key === 'PageDown' || 
                                  [34, 428, 167, 228].includes(keyCode);
            
            if (isChannelUp) {
                changeChannel('up');
                e.preventDefault();
            }
            else if (isChannelDown) {
                changeChannel('down');
                e.preventDefault();
            }
        });
        
        // Swipe gesture per smartphone (tramite overlay touch)
        const touchOverlay = document.getElementById('touch-overlay');
        let touchStartY = null;
        let touchStartX = null;
        let touchMoved = false;

        touchOverlay.addEventListener('touchstart', (e) => {
            touchStartY = e.touches[0].clientY;
            touchStartX = e.touches[0].clientX;
            touchMoved = false;
        }, { passive: true });

        touchOverlay.addEventListener('touchmove', (e) => {
            if (touchStartY === null) return;
            const dy = Math.abs(e.touches[0].clientY - touchStartY);
            if (dy > 10) touchMoved = true;
        }, { passive: true });

        touchOverlay.addEventListener('touchend', (e) => {
            if (touchStartY === null) return;
            const deltaY = e.changedTouches[0].clientY - touchStartY;
            const deltaX = e.changedTouches[0].clientX - touchStartX;
            touchStartY = null;
            touchStartX = null;

            // Swipe verticale: cambio canale
            if (Math.abs(deltaY) >= 50 && Math.abs(deltaY) > Math.abs(deltaX)) {
                if (deltaY < 0) changeChannel('up');
                else changeChannel('down');
                return;
            }

            // Tap: play/pausa
            if (!touchMoved) {
                if (video.paused) video.play().catch(() => {});
                else video.pause();
            }
        }, { passive: true });

        // Per sicurezza aggiungiamo un focus forzato alla finestra per catturare gli eventi
        window.focus();
        
        loadChannels();
        
        setInterval(() => loadChannels(true), 3600 * 1000);
    </script>
</body>
</html>
"""

class TVHandler(BaseHTTPRequestHandler):
    """Handler HTTP che serve la playlist M3U."""
    
    playlist_cache = None
    playlist_time = 0
    CACHE_DURATION = 3600  # Rigenera ogni ora (i token Rai durano ~8h)
    lock = threading.Lock()
    client_channels = {}  # Mappa IP client -> indice ultimo canale
    
    def do_GET(self):
        if self.path in ("/playlist.m3u", "/playlist.m3u8", "/"):
            with self.lock:
                now = time.time()
                if (self.playlist_cache is None or 
                    now - self.playlist_time > self.CACHE_DURATION):
                    self.playlist_cache = generate_m3u()
                    self.playlist_time = now
            
            content = self.playlist_cache.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpegurl; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.write(content) if hasattr(self, 'write') else self.wfile.write(content)
        
        elif self.path == "/tv":
            content = TV_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.write(content) if hasattr(self, 'write') else self.wfile.write(content)
            
        elif self.path == "/last_channel":
            client_ip = self.client_address[0]
            idx = str(TVHandler.client_channels.get(client_ip, 0)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(idx)))
            self.end_headers()
            self.wfile.write(idx)

        elif self.path.startswith("/set_channel?index="):
            client_ip = self.client_address[0]
            try:
                idx = int(self.path.split("=")[1])
                TVHandler.client_channels[client_ip] = idx
            except ValueError:
                pass
            self.send_response(200)
            self.end_headers()
            
        elif self.path == "/refresh":
            with self.lock:
                self.playlist_cache = generate_m3u()
                self.playlist_time = time.time()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Playlist rigenerata!\n")
        
        elif self.path == "/status":
            info = {
                "canali": len([l for l in (self.playlist_cache or "").split("\n") 
                              if l.startswith("#EXTINF")]),
                "ultimo_aggiornamento": time.ctime(self.playlist_time) if self.playlist_time else "mai",
                "prossimo_aggiornamento_tra": f"{max(0, self.CACHE_DURATION - (time.time() - self.playlist_time)):.0f}s"
            }
            content = json.dumps(info, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(content)
        
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Log più pulito."""
        print(f"  [REQ] {self.client_address[0]} -> {args[0]}")


def get_local_ip() -> str:
    """Trova l'IP locale della macchina."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def main():
    PORT = 8888
    local_ip = get_local_ip()
    
    print("================================================")
    print("         TV ITALIA - Server IPTV          ")
    print("================================================")
    print()
    
    # Genera la prima playlist
    TVHandler.playlist_cache = generate_m3u()
    TVHandler.playlist_time = time.time()
    
    # Salva anche su disco
    with open("italia-fta.m3u", "w", encoding="utf-8") as f:
        f.write(TVHandler.playlist_cache)
    print("\n Playlist salvata in: italia-fta.m3u")
    
    # Avvia server
    server = HTTPServer(("0.0.0.0", PORT), TVHandler)
    
    print(f"\n Server attivo!")
    print(f"   Playlist:  http://{local_ip}:{PORT}/playlist.m3u")
    print(f"   Web Player:http://{local_ip}:{PORT}/tv")
    print(f"   Rigenera:  http://{local_ip}:{PORT}/refresh")
    print(f"   Stato:     http://{local_ip}:{PORT}/status")
    print(f"\n   Sulla smart TV usa il browser a: http://{local_ip}:{PORT}/tv")
    print(f"   O in app usa: http://{local_ip}:{PORT}/playlist.m3u")
    print(f"\n   I token vengono rigenerati ogni {TVHandler.CACHE_DURATION//60} minuti.")
    print(f"   Premi Ctrl+C per fermare.\n")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n Server fermato.")
        server.server_close()


if __name__ == "__main__":
    main()