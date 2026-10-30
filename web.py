"""Веб-интерфейс Dota 2 helper: локальный сервер на стандартной библиотеке Python.

Запуск: .venv/bin/python web.py  (Windows: .venv\\Scripts\\python web.py), затем http://127.0.0.1:8000
Вкладка «Драфт» работает и без зависимостей; вкладке «Агент» нужны пакеты из requirements.txt.
"""
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import llm
import main
from main import build_recommendation, format_report, kb, save_report
from situations import SITUATIONS

STATIC = main.ROOT / 'static'
HOST = os.getenv('DOTA_HOST', '127.0.0.1')
PORT = int(os.getenv('DOTA_PORT', '8000'))
TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.js': 'application/javascript; charset=utf-8', '.svg': 'image/svg+xml',
         '.png': 'image/png'}
# Один анализ за раз: kb.reload() и запись файла не рассчитаны на параллельные запросы.
LOCK = threading.Lock()
# Агент один на сервер: индексы грузятся один раз, вопросы обрабатываются по очереди.
AGENT = {'agent': None, 'status': 'loading', 'error': None, 'log': []}
AGENT_LOCK = threading.Lock()


def ollama_status():
    if llm.ollama is None:
        return 'not_installed'
    return 'online' if llm.installed_models() is not None else 'offline'


def start_agent():
    """Загружаем агента в фоне: первая индексация занимает до минуты."""
    def log(message):
        AGENT['log'].append(message)
        print(message)

    def run():
        try:
            from agent import Agent
            AGENT['agent'] = Agent(log=log, build_lock=LOCK)
            AGENT['status'] = 'ready'
        except ImportError as ex:
            AGENT['status'], AGENT['error'] = 'no_deps', f'Не установлены зависимости агента ({ex.name}). ' \
                'Установи их: python -m pip install -r requirements.txt и запускай web.py из .venv.'
        except Exception as ex:
            AGENT['status'], AGENT['error'] = 'error', f'Агент не запустился: {ex}'
    threading.Thread(target=run, daemon=True).start()


def agent_status():
    info = {'status': AGENT['status'], 'error': AGENT['error'], 'log': AGENT['log'][-3:],
            'model': llm.MODEL, 'embed_model': llm.EMBED_MODEL}
    a = AGENT['agent']
    if a:
        info.update({'llm_ready': a.llm_ready(), 'embedder': a.retriever.embedder.name,
                     'vectors': a.retriever.vectors.count(), 'graph': a.retriever.graph.stats()})
    return info


def img_url(kind, name):
    """Путь к картинке из static/img (её качает download_images.py) или None."""
    rel = f'img/{kind}/{kb._normalize(name)}.png'
    return '/' + rel if (STATIC / rel).is_file() else None


def add_images(data):
    data['hero']['img'] = img_url('heroes', data['hero']['name'])
    groups = [data['start'], data['core'], data['situational']] + [s['items'] for s in data['situations']]
    for group in groups:
        for item in group:
            item['img'] = img_url('items', item['name'])


def meta():
    with LOCK:
        heroes = [{'name': h['name'], 'role': h['role'], 'primary_attr': h['primary_attr'],
                   'attack_type': h['attack_type'], 'img': img_url('heroes', h['name'])} for h in kb.heroes.values()]
        items_count = len(kb.items)
        known = {kb._normalize(h['name']): h['name'] for h in heroes}
    heroes.sort(key=lambda h: h['name'])
    situations = [{'id': s['id'], 'title': s['title'], 'description': s['description'], 'min': s['min'],
                   # Только герои, которые действительно есть в базе.
                   'triggers': [known[n] for n in map(kb._normalize, s['triggers']) if n in known]}
                  for s in SITUATIONS]
    return {'heroes': heroes, 'items_count': items_count, 'situations': situations,
            'model': main.MODEL, 'ollama': ollama_status(), 'agent': agent_status()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/api/meta':
            return self.send_json(meta())
        if url.path == '/api/agent':
            return self.send_json(agent_status())
        if url.path == '/api/item':
            name = parse_qs(url.query).get('name', [''])[0]
            with LOCK:
                card = kb.items.get(name)
            if not card:
                return self.send_json({'error': 'Предмет не найден'}, 404)
            return self.send_json({'name': card['name'], 'cost': card['cost'], 'type': card['type'],
                                   'text': card['raw_text'], 'img': img_url('items', card['name'])})
        rel = 'index.html' if url.path in ('', '/') else url.path.lstrip('/')
        path = (STATIC / rel).resolve()
        if STATIC.resolve() not in path.parents or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', TYPES.get(path.suffix, 'application/octet-stream'))
        self.send_header('Content-Length', str(len(body)))
        if path.suffix == '.png':
            self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/ask':
            return self.ask()
        if path != '/api/build':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            req = json.loads(self.rfile.read(length) or b'{}')
            hero = str(req.get('hero', '')).strip()
            allies = [str(x).strip() for x in req.get('allies', []) if str(x).strip()]
            enemies = [str(x).strip() for x in req.get('enemies', []) if str(x).strip()]
            situations = [str(x) for x in req.get('situations', [])]
            position = req.get('position', 'auto')
            if position not in ('auto', 'core', 'support'):
                position = 'auto'
        except (ValueError, TypeError, AttributeError):
            return self.send_json({'ok': False, 'error': 'Некорректный запрос.'}, 400)
        if not hero:
            return self.send_json({'ok': False, 'error': 'Выбери своего героя.'}, 400)
        with LOCK:
            data = build_recommendation(hero, allies, enemies, situations, position)
            if data['ok']:
                add_images(data)
                data['report'] = format_report(data)
                data['file'] = save_report(data['hero']['name'], data['report']).name
        self.send_json(data)

    def ask(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            req = json.loads(self.rfile.read(length) or b'{}')
            question = str(req.get('question', '')).strip()[:1000]
            draft = req.get('draft') if isinstance(req.get('draft'), dict) else None
        except (ValueError, TypeError, AttributeError):
            return self.send_json({'ok': False, 'error': 'Некорректный запрос.'}, 400)
        if not question:
            return self.send_json({'ok': False, 'error': 'Напиши вопрос.'}, 400)
        agent = AGENT['agent']
        if not agent:
            return self.send_json({'ok': False, 'error': AGENT['error'] or 'Агент ещё загружается, подожди немного.'}, 503)
        with AGENT_LOCK:
            data = agent.ask(question, draft=draft)
        for item in data['items']:
            item['img'] = img_url('items', item['name'])
        for card in data['sources']:
            card['img'] = img_url('heroes' if card['kind'] == 'hero' else 'items', card['name'])
        self.send_json(data)


if __name__ == '__main__':
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f'http://{HOST}:{PORT}'
    print(f'Dota 2 AI Helper | героев: {len(kb.heroes)} | предметов: {len(kb.items)}')
    print(f'Интерфейс: {url}  (Ctrl+C — остановить)')
    start_agent()
    if not os.getenv('DOTA_NO_BROWSER'):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
