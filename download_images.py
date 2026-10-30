"""Скачивает портреты героев и иконки предметов из локальной базы в static/img/.

Соответствие имён берётся из справочника OpenDota, сами картинки — с CDN Valve.
Запуск: py download_images.py  (повторный запуск докачивает только недостающие файлы)
"""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from analys import DotaKnowledgeBase

ROOT = Path(__file__).resolve().parent

CDN = 'https://cdn.cloudflare.steamstatic.com'
OPENDOTA = 'https://api.opendota.com/api/constants/'
IMG_DIR = ROOT / 'static' / 'img'
# Названия в карточках, которые расходятся с официальными.
NAME_FIX = {'Outworld Destroyer': 'Outworld Devourer'}


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'dota-2-helper'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def index_by_name(constants, key):
    result = {}
    for entry in constants.values():
        if entry.get(key) and entry.get('img'):
            result.setdefault(DotaKnowledgeBase._normalize(entry[key]), entry['img'].split('?')[0])
    return result


def plan(names, index, folder):
    jobs, missing = [], []
    for name in names:
        path = index.get(DotaKnowledgeBase._normalize(NAME_FIX.get(name, name)))
        if not path:
            missing.append(name)
            continue
        jobs.append((CDN + path, folder / (DotaKnowledgeBase._normalize(name) + '.png')))
    return jobs, missing


def download(job):
    url, target = job
    if target.exists() and target.stat().st_size > 0:
        return 'skip'
    try:
        target.write_bytes(fetch(url))
        return 'ok'
    except Exception as ex:
        return f'error {url}: {ex}'


def main():
    kb = DotaKnowledgeBase(str(ROOT / 'data'), str(ROOT / 'data' / 'items'))
    try:
        heroes = index_by_name(json.loads(fetch(OPENDOTA + 'heroes')), 'localized_name')
        items = index_by_name(json.loads(fetch(OPENDOTA + 'items')), 'dname')
    except Exception as ex:
        print(f'Не удалось получить справочник OpenDota: {ex}. Проверь интернет и запусти ещё раз.')
        return 1

    jobs = []
    for names, index, sub in ((kb.heroes, heroes, 'heroes'), (kb.items, items, 'items')):
        folder = IMG_DIR / sub
        folder.mkdir(parents=True, exist_ok=True)
        part, missing = plan(names, index, folder)
        jobs += part
        if missing:
            print(f'[{sub}] нет картинки для: {", ".join(missing)}')

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(download, jobs))
    errors = [r for r in results if r.startswith('error')]
    print(f'Скачано: {results.count("ok")}, уже были: {results.count("skip")}, ошибок: {len(errors)}')
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
