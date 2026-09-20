import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import feedparser

ROOT = Path(__file__).parent
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
MAX_SEEN = 2000


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def has_cyrillic(text):
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


_translator = None


def translate_to_ru(text):
    """Free offline EN→RU translation. Keeps the original if a model is unavailable."""
    global _translator
    if not text or has_cyrillic(text):
        return text
    try:
        if _translator is None:
            import argostranslate.package
            import argostranslate.translate
            installed = argostranslate.translate.get_installed_languages()
            source = next((x for x in installed if x.code == "en"), None)
            target = next((x for x in installed if x.code == "ru"), None)
            if not source or not target:
                argostranslate.package.update_package_index()
                packages = argostranslate.package.get_available_packages()
                package = next(x for x in packages if x.from_code == "en" and x.to_code == "ru")
                argostranslate.package.install_from_path(package.download())
                installed = argostranslate.translate.get_installed_languages()
                source = next(x for x in installed if x.code == "en")
                target = next(x for x in installed if x.code == "ru")
            _translator = source.get_translation(target)
        return _translator.translate(text)
    except Exception as error:
        print(f"Translation unavailable: {error}", file=sys.stderr)
        return text


def trim(text, limit=650):
    text = clean(text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def make_post(entry, channel):
    title = translate_to_ru(trim(entry.get("title", "Новости"), 150))
    summary = translate_to_ru(trim(entry.get("summary", entry.get("description", ""))))
    emojis = ["⚡", "👀", "🤯", "📱", "🚀", "🫠"]
    emoji = emojis[sum(ord(x) for x in title) % len(emojis)] if title else channel.get("emoji", "📰")
    topic = channel.get("topic", "Новости")
    lead = f"{emoji} <b>{html.escape(title)}</b>"
    body = html.escape(summary) if summary else "Появились свежие детали — разбираемся, что к чему."
    link = entry.get("link", "")
    ending = "\n\n👀 Вот это уже интересно. Следим за развитием."
    if link:
        ending += f'\n<a href="{html.escape(link, quote=True)}">Подробнее</a>'
    return f"{lead}\n\nКороче: {body}{ending}\n\n#{re.sub(r'[^А-Яа-яЁёA-Za-z0-9]', '', topic).lower() or 'новости'}"


def request_json(url, payload=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json", "User-Agent": "GitHubNewsPublisher/1.0"},
        method="POST" if payload else "GET",
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode())


def send_message(chat_id, text):
    result = request_json(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
    )
    if not result.get("ok"):
        raise RuntimeError(result)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def main():
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in GitHub Secrets.")
    config = load_json(ROOT / "channels.json", {"channels": []})
    state_path = ROOT / "state.json"
    state = load_json(state_path, {"seen": []})
    seen = set(state.get("seen", []))
    sent = 0
    for channel in config.get("channels", [])[:3]:
        posted_for_channel = False
        for url in channel.get("sources", []):
            try:
                feed = feedparser.parse(url)
                entries = list(reversed(feed.entries[:10]))
            except Exception as error:
                print(f"RSS failed ({url}): {error}", file=sys.stderr)
                continue
            for entry in entries:
                link = entry.get("link", "")
                key = f"{channel.get('chat_id')}|{link or entry.get('id', '')}"
                if not key or key in seen:
                    continue
                try:
                    send_message(channel["chat_id"], make_post(entry, channel))
                except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as error:
                    print(f"Telegram failed: {error}", file=sys.stderr)
                    continue
                seen.add(key)
                sent += 1
                posted_for_channel = True
                break  # at most one new post per channel per run
            if posted_for_channel:
                break
    state_path.write_text(json.dumps({"seen": list(seen)[-MAX_SEEN:]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Published: {sent}")


if __name__ == "__main__":
    main()
