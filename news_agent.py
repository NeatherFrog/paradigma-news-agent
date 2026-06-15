"""
PARADIGMA News Agent (autonomous EN money-online / crypto / AI channel).

Поток:
  1) Тянет свежие заголовки из топовых RSS-источников (sources.json).
  2) Отдаёт их в Claude (твой Anthropic API): он выбирает самую значимую новость
     и пишет humanized-пост с СОБСТВЕННЫМ мнением и микроаналитикой.
  3) Постит пост в Telegram-канал.

ENV (Railway -> Variables):
  ANTHROPIC_API_KEY, BOT_TOKEN, CHANNEL_ID
  MODEL (опц., по умолч. claude-sonnet-4-6)
  DATA_DIR (опц., путь к volume для seen.json — чтобы не повторяться между запусками)
  INCLUDE_SOURCE (опц., "1" = добавлять ссылку на источник)
"""
import os, json, time, datetime, html, re, sys
import requests
import feedparser

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
MODEL = os.environ.get("MODEL", "claude-sonnet-4-6")
DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
INCLUDE_SOURCE = os.environ.get("INCLUDE_SOURCE", "1") == "1"

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")

SYSTEM_PROMPT = """You are the voice behind Paradigma — a sharp operator writing an English Telegram channel about MAKING MONEY ONLINE: crypto, AI tools, online business, automation, side income, and the opportunities/risks around them. You are NOT an AI assistant. You write like a real person who actually builds and earns online.

Your job: take ONE news item and write a short post with YOUR OWN opinion and micro-analysis — not a news summary.

EVERY post must include your read of it:
- what it actually means (cut through the headline)
- who wins and who loses
- where the opportunity or the risk is for someone trying to make money online
- how you think it plays out next (a confident, forward-looking take)

VOICE: confident, analytical, slightly contrarian, direct, human, specific. Assume the reader is smart and wants the angle, not the recap.

NEVER sound: educational, corporate, LinkedIn, marketing copy, or like ChatGPT.

FORBIDDEN words/phrases (never use any): moreover, furthermore, additionally, however, game changer, game-changer, leverage, unlock, unlocking, maximize, optimize, transformative, robust, seamless, innovative, cutting-edge, revolutionize, revolutionary, in conclusion, ultimately, "in today's world", "it is important to note", "the future is bright", "this is not just", "dive in", "in the world of".

STYLE:
- Mix short and long sentences. Human pacing, no robotic symmetry.
- Concrete nouns and numbers. No fake certainty, no invented stats.
- Crypto-twitter phrasing welcome when natural: "feels like", "looks more like", "the weird part is", "nobody talks about", "the market is basically saying", "that's the actual story", "here's the play".
- 2 to 4 short paragraphs, roughly 350-750 characters. Plain text for Telegram.
- At most ONE emoji, only if it earns its place. Hashtags only if genuinely useful (usually none).
- Do NOT copy phrasing from the source. It's YOUR take.
- End on a sharp forward-looking line or a concrete implication for someone making money online.

FINAL CHECK: remove AI cadence, textbook structure, perfect logic chains, generic summaries. If it reads like AI or like a news report, rewrite it as an opinion.

You will get a numbered list of fresh headlines. Pick the SINGLE most relevant one for a make-money-online audience and write the post.

Respond with ONLY valid JSON, no other text:
{"chosen_index": <int>, "post": "<the telegram post text>"}"""


def log(msg):
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}", flush=True)


def load_seen():
    try:
        return set(json.load(open(SEEN_PATH, encoding="utf-8")))
    except Exception:
        return set()


def save_seen(seen):
    try:
        seen = sorted(seen)[-500:]  # держим последние 500
        json.dump(seen, open(SEEN_PATH, "w", encoding="utf-8"))
    except Exception as e:
        log(f"warn: не смог сохранить seen.json: {e}")


def clean(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def fetch_headlines(cfg, seen):
    cutoff = time.time() - cfg.get("lookback_hours", 30) * 3600
    items = []
    for url in cfg.get("feeds", []):
        try:
            feed = feedparser.parse(url)
            src = clean(feed.feed.get("title", url))
            for e in feed.entries[:15]:
                link = e.get("link", "")
                if not link or link in seen:
                    continue
                ts = None
                for k in ("published_parsed", "updated_parsed"):
                    if e.get(k):
                        ts = time.mktime(e[k]); break
                if ts and ts < cutoff:
                    continue
                items.append({
                    "title": clean(e.get("title", "")),
                    "summary": clean(e.get("summary", ""))[:400],
                    "link": link, "source": src, "ts": ts or 0,
                })
        except Exception as ex:
            log(f"warn: фид не прочитан {url}: {ex}")
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[: cfg.get("max_headlines_to_consider", 22)]


def ask_claude(headlines):
    listing = "\n".join(
        f'{i}. [{h["source"]}] {h["title"]} — {h["summary"][:160]}'
        for i, h in enumerate(headlines)
    )
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 800, "system": SYSTEM_PROMPT,
              "messages": [{"role": "user", "content": f"Fresh headlines:\n{listing}"}]},
        timeout=90,
    )
    data = r.json()
    if "content" not in data:
        raise RuntimeError(f"Anthropic API: {data}")
    text = "".join(b.get("text", "") for b in data["content"])
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"нет JSON в ответе: {text[:300]}")
    obj = json.loads(m.group(0))
    return int(obj["chosen_index"]), obj["post"].strip()


def post_to_tg(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHANNEL_ID, "text": text, "disable_web_page_preview": False},
        timeout=30,
    )
    return r.json()


def main():
    missing = [k for k, v in {"ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
               "BOT_TOKEN": BOT_TOKEN, "CHANNEL_ID": CHANNEL_ID}.items() if not v]
    if missing:
        log(f"ERROR: нет переменных: {', '.join(missing)}"); sys.exit(1)

    cfg = json.load(open(os.path.join(HERE, "sources.json"), encoding="utf-8"))
    seen = load_seen()
    headlines = fetch_headlines(cfg, seen)
    if not headlines:
        log("Свежих новостей нет — пропускаю."); return
    log(f"Собрано заголовков: {len(headlines)}")

    idx, post = ask_claude(headlines)
    idx = max(0, min(idx, len(headlines) - 1))
    chosen = headlines[idx]
    log(f"Выбрано: [{chosen['source']}] {chosen['title']}")

    if INCLUDE_SOURCE and chosen["link"]:
        post = f"{post}\n\nSource: {chosen['link']}"

    res = post_to_tg(post)
    if res.get("ok"):
        seen.add(chosen["link"]); save_seen(seen)
        log(f"OK Запостил. msg_id={res['result'].get('message_id')}")
    else:
        log(f"ERROR Telegram: {res}"); sys.exit(1)


if __name__ == "__main__":
    main()
