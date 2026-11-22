"""
Instant YouTube Transcript Extractor for OpenShorts.
Fetches automatic or uploaded captions directly from YouTube in seconds,
bypassing local Whisper audio transcription completely.
Includes word-level timestamps required for snapping cuts and animated captions.
"""
import json
import re
import urllib.request
import yt_dlp

def fetch_youtube_transcript(url, preferred_langs=('en', 'en-US', 'en-GB', 'en-orig', 'ur', 'hi', 'es')):
    """
    Fetches transcript segments directly from YouTube without downloading video/audio.
    Returns:
        dict with {
            "language": str,
            "segments": list of dicts: {"start": float, "end": float, "text": str, "words": [{"word": str, "start": float, "end": float}]},
            "title": str,
            "duration": float,
            "source": "youtube_direct_captions"
        }
        or None if no transcript is available.
    """
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'web']}},
        'js_runtimes': {'node': {}},
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
        subs = info.get('subtitles') or {}
        auto_caps = info.get('automatic_captions') or {}
        
        # Determine chosen language
        chosen_lang = None
        chosen_entries = None
        
        # 1. Try uploaded human subtitles in preferred languages
        for lang in preferred_langs:
            if lang in subs:
                chosen_lang = lang
                chosen_entries = subs[lang]
                break
                
        # 2. Try auto-captions in preferred languages
        if not chosen_entries:
            for lang in preferred_langs:
                if lang in auto_caps:
                    chosen_lang = lang
                    chosen_entries = auto_caps[lang]
                    break
                    
        # 3. Fallback to any English variant or first available
        if not chosen_entries:
            for lang, entries in {**subs, **auto_caps}.items():
                if lang.startswith('en'):
                    chosen_lang = lang
                    chosen_entries = entries
                    break
                    
        if not chosen_entries and (subs or auto_caps):
            all_caps = {**subs, **auto_caps}
            chosen_lang = list(all_caps.keys())[0]
            chosen_entries = all_caps[chosen_lang]
            
        if not chosen_entries:
            return None
            
        # Find JSON3 format first (has word/phrase timings), fallback to vtt/srv
        target_entry = next((f for f in chosen_entries if f.get('ext') == 'json3'), None)
        if not target_entry:
            target_entry = next((f for f in chosen_entries if f.get('ext') in ('vtt', 'srv3', 'ttml')), chosen_entries[0])
            
        target_url = target_entry.get('url')
        if not target_url:
            return None
            
        req = urllib.request.Request(
            target_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode('utf-8')
            
        segments = []
        if target_entry.get('ext') == 'json3' or content.strip().startswith('{'):
            data = json.loads(content)
            events = data.get('events', [])
            raw_segments = []
            for ev in events:
                if 'segs' in ev and 'tStartMs' in ev:
                    # Strip zero-width chars and normalize spaces
                    raw_text = "".join(s.get('utf8', '') for s in ev['segs'])
                    clean_text = re.sub(r'[\u200b-\u200d\ufeff]', '', raw_text)
                    clean_text = " ".join(clean_text.split()).strip()
                    if not clean_text or clean_text == '\n':
                        continue
                        
                    start = round(ev['tStartMs'] / 1000.0, 3)
                    dur = round(ev.get('dDurationMs', 0) / 1000.0, 3)
                    end = round(start + dur, 3)
                    
                    words = []
                    for s in ev['segs']:
                        piece = s.get('utf8', '')
                        piece = re.sub(r'[\u200b-\u200d\ufeff]', '', piece).strip()
                        if not piece or piece == '\n':
                            continue
                        clean_words = piece.split()
                        offset = s.get('tOffsetMs', 0) / 1000.0
                        w_start = round(start + offset, 3)
                        for cw in clean_words:
                            cw_clean = cw.strip()
                            if cw_clean:
                                # Whisper convention: leading space denotes word boundary
                                words.append({
                                    'word': f" {cw_clean}",
                                    'start': w_start,
                                    'end': round(w_start + 0.35, 3)
                                })
                    
                    # Adjust ends of words
                    for w_idx in range(len(words)):
                        if w_idx < len(words) - 1:
                            words[w_idx]['end'] = min(words[w_idx + 1]['start'], round(words[w_idx]['start'] + 0.5, 3))
                        else:
                            words[w_idx]['end'] = max(words[w_idx]['start'] + 0.1, end)
                            
                    # Fallback word spacing if individual pieces lacked timing
                    if not words:
                        tokens = clean_text.split()
                        step = max(0.01, dur / max(1, len(tokens)))
                        for i, tok in enumerate(tokens):
                            words.append({
                                'word': f" {tok}",
                                'start': round(start + i * step, 3),
                                'end': round(start + (i + 1) * step, 3)
                            })
                            
                    raw_segments.append({
                        'start': start,
                        'end': end,
                        'text': clean_text,
                        'words': words
                    })
            
            # Deduplicate rolling / overlapping YouTube auto-caption updates
            for seg in raw_segments:
                if segments:
                    prev = segments[-1]
                    if seg['start'] == prev['start'] and seg['text'] == prev['text']:
                        continue
                    if seg['text'] == prev['text'] and abs(seg['start'] - prev['start']) < 0.2:
                        continue
                segments.append(seg)
        elif '--> ' in content:
            # WebVTT parser
            time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})')
            
            def parse_vtt_ts(ts):
                parts = ts.strip().split(':')
                if len(parts) == 3:
                    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                return float(parts[0]) * 60 + float(parts[1])
                
            lines = content.splitlines()
            i = 0
            while i < len(lines):
                m = time_pattern.search(lines[i])
                if m:
                    s_time = parse_vtt_ts(m.group(1))
                    e_time = parse_vtt_ts(m.group(2))
                    text_lines = []
                    i += 1
                    while i < len(lines) and lines[i].strip():
                        clean = re.sub(r'<[^>]+>', '', lines[i]).strip()
                        if clean:
                            text_lines.append(clean)
                        i += 1
                    txt = " ".join(text_lines).strip()
                    clean_txt = re.sub(r'[\u200b-\u200d\ufeff]', '', txt)
                    clean_txt = " ".join(clean_txt.split()).strip()
                    if clean_txt:
                        tokens = clean_txt.split()
                        dur = max(0.1, e_time - s_time)
                        step = max(0.01, dur / max(1, len(tokens)))
                        words = [{
                            'word': f" {tok}",
                            'start': round(s_time + j * step, 3),
                            'end': round(s_time + (j + 1) * step, 3)
                        } for j, tok in enumerate(tokens)]
                        
                        seg = {
                            'start': s_time,
                            'end': e_time,
                            'text': clean_txt,
                            'words': words
                        }
                        if not segments or not (segments[-1]['text'] == clean_txt and abs(segments[-1]['start'] - s_time) < 0.2):
                            segments.append(seg)
                else:
                    i += 1
                    
        if not segments:
            return None
            
        return {
            "language": chosen_lang,
            "segments": segments,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "source": "youtube_direct_captions"
        }
    except Exception as e:
        print(f"   ℹ️ YouTube direct transcript failed ({type(e).__name__}: {e})")
        return None
