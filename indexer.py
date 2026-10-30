"""Модуль 1. Индексация базы знаний: векторная БД (ChromaDB) + граф знаний (SQLite).

Векторная БД: каждая карточка героя и предмета — отдельный документ, эмбеддинг считает
модель bge-m3 через Ollama. Если Ollama недоступна, используется запасной векторизатор
по символьным n-граммам — у каждого векторизатора своя коллекция, они не смешиваются.

Граф знаний: узлы — герои и предметы, рёбра — связи из карточек:
  герой  WEAK_AGAINST     герой    (Слаб против)
  герой  STRONG_AGAINST   герой    (Силён против)
  герой  BUILDS           предмет  (Основной закуп)
  герой  COUNTERED_BY     предмет  (Контр-предметы)
  предмет EFFECTIVE_AGAINST герой  (Эффективен против героев)

Запуск: py indexer.py           — построить индексы, если база изменилась
        py indexer.py --rebuild — перестроить принудительно
"""
import hashlib
import json
import math
import re
import sqlite3
import sys
from pathlib import Path

import llm
from analys import DotaKnowledgeBase

ROOT = Path(__file__).resolve().parent
INDEX_DIR = ROOT / 'index'

RELATIONS = {
    'WEAK_AGAINST': 'слаб против',
    'STRONG_AGAINST': 'силён против',
    'BUILDS': 'собирает',
    'COUNTERED_BY': 'контрится предметом',
    'EFFECTIVE_AGAINST': 'эффективен против',
}


# ---------- Векторизаторы ----------

class OllamaEmbedder:
    name = 'bge-m3'

    def __init__(self):
        self.name = llm.EMBED_MODEL

    def __call__(self, texts):
        result = []
        for i in range(0, len(texts), 16):
            result.extend(llm.embed(texts[i:i + 16]))
        return result


class HashEmbedder:
    """Запасной векторизатор без нейросети: хеширование символьных 3-грамм слов.
    Хуже понимает смысл, но ловит совпадения названий и терминов."""
    name = 'hash'
    dim = 1024

    def __call__(self, texts):
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for word in re.findall(r'[a-zа-яё0-9]+', text.lower()):
                padded = f' {word} '
                for i in range(len(padded) - 2):
                    h = int(hashlib.md5(padded[i:i + 3].encode()).hexdigest()[:8], 16)
                    vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


def pick_embedder():
    return OllamaEmbedder() if llm.has_model(llm.EMBED_MODEL) else HashEmbedder()


# ---------- Документы ----------

def collection_name(embedder):
    return 'dota_cards_' + re.sub(r'[^a-zA-Z0-9]+', '_', embedder.name).strip('_')


def card_documents(kb):
    docs = []
    for hero in kb.heroes.values():
        docs.append({'id': f'hero:{hero["name"]}', 'kind': 'hero', 'name': hero['name'], 'text': hero['raw_text']})
    for item in kb.items.values():
        docs.append({'id': f'item:{item["name"]}', 'kind': 'item', 'name': item['name'], 'text': item['raw_text']})
    return docs


def data_fingerprint(kb):
    h = hashlib.sha256()
    for d in sorted(card_documents(kb), key=lambda d: d['id']):
        h.update(d['id'].encode())
        h.update(d['text'].encode())
    return h.hexdigest()[:16]


# ---------- Векторная БД ----------

def open_collection(embedder, create=False):
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(path=str(INDEX_DIR / 'chroma'), settings=Settings(anonymized_telemetry=False))
    name = collection_name(embedder)
    if create:
        try:
            client.delete_collection(name)
        except Exception:
            pass
        return client.create_collection(name, embedding_function=None, configuration={'hnsw': {'space': 'cosine'}})
    return client.get_collection(name, embedding_function=None)


def build_vectors(kb, embedder):
    docs = card_documents(kb)
    collection = open_collection(embedder, create=True)
    embeddings = embedder([d['text'] for d in docs])
    collection.add(
        ids=[d['id'] for d in docs],
        documents=[d['text'] for d in docs],
        metadatas=[{'kind': d['kind'], 'name': d['name']} for d in docs],
        embeddings=embeddings,
    )
    return len(docs)


class VectorIndex:
    def __init__(self, embedder=None):
        self.embedder = embedder or pick_embedder()
        self.collection = open_collection(self.embedder)

    def count(self):
        return self.collection.count()

    def search(self, query, k=5, kind=None):
        """Ближайшие карточки к запросу: [{id, kind, name, score, text}]."""
        res = self.collection.query(
            query_embeddings=self.embedder([query]),
            n_results=k,
            where={'kind': kind} if kind else None,
        )
        hits = []
        for doc_id, text, meta, dist in zip(res['ids'][0], res['documents'][0], res['metadatas'][0], res['distances'][0]):
            hits.append({'id': doc_id, 'kind': meta['kind'], 'name': meta['name'], 'score': round(1 - dist, 3), 'text': text})
        return hits


# ---------- Граф знаний ----------

def build_graph(kb, path=None):
    path = path or INDEX_DIR / 'graph.sqlite'
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE nodes (name TEXT PRIMARY KEY, kind TEXT NOT NULL, role TEXT, attr TEXT, attack TEXT);
        CREATE TABLE edges (src TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL, PRIMARY KEY (src, rel, dst));
        CREATE INDEX edges_dst ON edges (dst, rel);
    ''')

    def hero_name(raw):
        # Только точное совпадение: нечёткий поиск мог бы связать не тех героев.
        return kb._norm_hero_map.get(kb._normalize(raw))

    def item_name(raw):
        return kb._norm_item_map.get(kb._normalize(raw))

    for h in kb.heroes.values():
        db.execute('INSERT INTO nodes VALUES (?, ?, ?, ?, ?)', (h['name'], 'hero', h['role'], h['primary_attr'], h['attack_type']))
    for it in kb.items.values():
        db.execute('INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?, ?)', (it['name'], 'item', it['type'], None, None))

    edges = set()
    skipped = 0
    for h in kb.heroes.values():
        for field, rel, resolve in (('weak_against', 'WEAK_AGAINST', hero_name), ('strong_against', 'STRONG_AGAINST', hero_name),
                                    ('core_items', 'BUILDS', item_name), ('counter_items', 'COUNTERED_BY', item_name)):
            for raw in h[field]:
                dst = resolve(raw)
                if dst:
                    edges.add((h['name'], rel, dst))
                else:
                    skipped += 1
    for it in kb.items.values():
        m = re.search(r'^- Эффективен против героев:\s*(.+)$', it['raw_text'], re.MULTILINE)
        for raw in re.split(r'[,;]', m.group(1)) if m else []:
            dst = hero_name(raw.strip().rstrip('.'))
            if dst:
                edges.add((it['name'], 'EFFECTIVE_AGAINST', dst))
            elif raw.strip():
                skipped += 1
    db.executemany('INSERT INTO edges VALUES (?, ?, ?)', sorted(edges))
    db.commit()
    db.close()
    return len(edges), skipped


class KnowledgeGraph:
    def __init__(self, path=None):
        self.db = sqlite3.connect(path or INDEX_DIR / 'graph.sqlite', check_same_thread=False)

    def node(self, name):
        row = self.db.execute('SELECT name, kind, role, attr, attack FROM nodes WHERE name = ?', (name,)).fetchone()
        return dict(zip(('name', 'kind', 'role', 'attr', 'attack'), row)) if row else None

    def out(self, src, rel):
        return [r[0] for r in self.db.execute('SELECT dst FROM edges WHERE src = ? AND rel = ? ORDER BY dst', (src, rel))]

    def into(self, dst, rel):
        return [r[0] for r in self.db.execute('SELECT src FROM edges WHERE dst = ? AND rel = ? ORDER BY src', (dst, rel))]

    def matchups(self, hero, enemies):
        """Связь считается в обе стороны: враг в моём «Слаб против» ИЛИ я в его «Силён против»."""
        weak = set(self.out(hero, 'WEAK_AGAINST')) | set(self.into(hero, 'STRONG_AGAINST'))
        strong = set(self.out(hero, 'STRONG_AGAINST')) | set(self.into(hero, 'WEAK_AGAINST'))
        return {'dangerous': [e for e in enemies if e in weak], 'favorable': [e for e in enemies if e in strong]}

    def counters(self, enemy):
        """Предметы против героя: из его карточки и из карточек предметов."""
        return sorted(set(self.out(enemy, 'COUNTERED_BY')) | set(self.into(enemy, 'EFFECTIVE_AGAINST')))

    def stats(self):
        nodes = dict(self.db.execute('SELECT kind, COUNT(*) FROM nodes GROUP BY kind').fetchall())
        edges = dict(self.db.execute('SELECT rel, COUNT(*) FROM edges GROUP BY rel').fetchall())
        return {'nodes': nodes, 'edges': edges}


# ---------- Сборка индексов ----------

def read_meta():
    try:
        return json.loads((INDEX_DIR / 'meta.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def ensure_index(kb=None, rebuild=False, log=print):
    """Строит векторный индекс и граф, если база карточек или векторизатор поменялись."""
    kb = kb or DotaKnowledgeBase(str(ROOT / 'data'), str(ROOT / 'data' / 'items'))
    embedder = pick_embedder()
    fingerprint = data_fingerprint(kb)
    meta = read_meta()
    built = meta.get('collections', {})
    INDEX_DIR.mkdir(exist_ok=True)

    if rebuild or meta.get('fingerprint') != fingerprint or not (INDEX_DIR / 'graph.sqlite').exists():
        edges, skipped = build_graph(kb)
        log(f'Граф знаний: {len(kb.heroes)} героев, {len(kb.items)} предметов, {edges} связей '
            f'(не распознано имён в карточках: {skipped}).')
        built = {}
    name = collection_name(embedder)
    if rebuild or built.get(name) != fingerprint:
        log(f'Векторная БД: считаю эмбеддинги ({embedder.name}) для {len(kb.heroes) + len(kb.items)} карточек…')
        count = build_vectors(kb, embedder)
        built[name] = fingerprint
        log(f'Векторная БД: {count} документов в коллекции {name}.')
    (INDEX_DIR / 'meta.json').write_text(json.dumps({'fingerprint': fingerprint, 'collections': built}, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    return embedder


if __name__ == '__main__':
    used = ensure_index(rebuild='--rebuild' in sys.argv)
    print(f'Готово. Векторизатор: {used.name}. Граф: {KnowledgeGraph().stats()}')
