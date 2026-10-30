"""Модуль 2. RAG-пайплайн: вопрос → поиск в векторной БД и графе знаний → обогащённый запрос → ответ LLM.

1. Распознаём в вопросе героев и предметы (точные названия и популярные сокращения: PA, BKB…).
2. Векторный поиск (ChromaDB) находит карточки, близкие к вопросу по смыслу.
3. Граф знаний добавляет факты о распознанных героях: матчапы и предметы против них.
4. Контекст передаётся модели, которая отвечает только по нему.
"""
import re

import llm
from analys import DotaKnowledgeBase
from compat import fits, hero_profile
from indexer import ROOT, KnowledgeGraph, VectorIndex, ensure_index

# Сокращения, которыми игроки обычно называют героев и предметы.
ABBREVIATIONS = {
    'am': 'Anti-Mage', 'антимаг': 'Anti-Mage', 'pa': 'Phantom Assassin', 'ta': 'Templar Assassin',
    'cm': 'Crystal Maiden', 'sf': 'Shadow Fiend', 'wk': 'Wraith King', 'od': 'Outworld Destroyer',
    'qop': 'Queen of Pain', 'np': "Nature's Prophet", 'ck': 'Chaos Knight', 'pl': 'Phantom Lancer',
    'tb': 'Terrorblade', 'lc': 'Legion Commander', 'dk': 'Dragon Knight', 'ss': 'Shadow Shaman',
    'ww': 'Winter Wyvern', 'aa': 'Ancient Apparition', 'bh': 'Bounty Hunter', 'bb': 'Bristleback',
    'void': 'Faceless Void', 'jugg': 'Juggernaut', 'mk': 'Monkey King', 'sk': 'Sand King',
    'kotl': 'Keeper of the Light', 'wr': 'Windranger', 'wd': 'Witch Doctor', 'ds': 'Dark Seer',
    'es': 'Earthshaker', 'storm': 'Storm Spirit', 'ember': 'Ember Spirit', 'ench': 'Enchantress',
    'bkb': 'Black King Bar', 'mkb': 'Monkey King Bar', 'skadi': 'Eye of Skadi', 'deso': 'Desolator',
    'aghs': "Aghanim's Scepter", 'аганим': "Aghanim's Scepter", 'shard': "Aghanim's Shard", 'шард': "Aghanim's Shard",
    'blink': 'Blink Dagger', 'блинк': 'Blink Dagger', 'manta': 'Manta Style', 'манта': 'Manta Style',
    'linken': "Linken's Sphere", 'линка': "Linken's Sphere", 'vessel': 'Spirit Vessel', 'hex': 'Scythe of Vyse',
    'хекс': 'Scythe of Vyse', 'euls': "Eul's Scepter of Divinity", 'еул': "Eul's Scepter of Divinity",
    'glimmer': 'Glimmer Cape', 'force': 'Force Staff', 'форс': 'Force Staff', 'бкб': 'Black King Bar',
    'мкб': 'Monkey King Bar', 'скади': 'Eye of Skadi', 'дезолятор': 'Desolator', 'бф': 'Battle Fury',
    'bf': 'Battle Fury', 'mom': 'Mask of Madness', 'sny': 'Sange and Yasha', 'basher': 'Skull Basher',
    'abyssal': 'Abyssal Blade', 'halberd': "Heaven's Halberd", 'shiva': "Shiva's Guard", 'шива': "Shiva's Guard",
    'pipe': 'Pipe of Insight', 'пайп': 'Pipe of Insight', 'gem': 'Gem of True Sight', 'гем': 'Gem of True Sight',
    'dust': 'Dust of Appearance', 'даст': 'Dust of Appearance', 'мека': 'Mekansm', 'meka': 'Mekansm',
    'treads': 'Power Treads', 'тредсы': 'Power Treads', 'midas': 'Hand of Midas', 'мидас': 'Hand of Midas',
}
ENEMY_MARKERS = ('против', 'vs', 'враг', 'врагов', 'врагами', 'противник', 'противников')
ALLY_MARKERS = ('союзник', 'союзники', 'союзниками', 'вместе', 'тиммейт')


def _words(text):
    return re.findall(r"[a-zа-яё0-9']+", text.lower().replace('-', ' '))


class Retriever:
    def __init__(self, kb=None, log=print):
        self.kb = kb or DotaKnowledgeBase(str(ROOT / 'data'), str(ROOT / 'data' / 'items'))
        self.embedder = ensure_index(self.kb, log=log)
        self.vectors = VectorIndex(self.embedder)
        self.graph = KnowledgeGraph()
        self._names = {}
        for name, kind in [(h, 'hero') for h in self.kb.heroes] + [(i, 'item') for i in self.kb.items]:
            self._names[tuple(_words(name.replace("'", '')))] = (name, kind)
        for short, name in ABBREVIATIONS.items():
            kind = 'hero' if name in self.kb.heroes else 'item' if name in self.kb.items else None
            if kind:
                self._names.setdefault((short,), (name, kind))

    # ---------- распознавание сущностей ----------

    def link_entities(self, text):
        """Герои и предметы, упомянутые в тексте, в порядке появления: [(name, kind, position)]."""
        words = [w.replace("'", '') for w in _words(text)]
        found, seen, i = [], set(), 0
        longest = max(len(k) for k in self._names)
        while i < len(words):
            for n in range(min(longest, len(words) - i), 0, -1):
                hit = self._names.get(tuple(words[i:i + n]))
                if hit:
                    if hit[0] not in seen:
                        found.append((hit[0], hit[1], i))
                        seen.add(hit[0])
                    i += n
                    break
            else:
                i += 1
        return found, words

    def parse_draft(self, text):
        """Грубо разбираем вопрос: первый герой — свой, после «против» — враги, после «союзник» — союзники."""
        entities, words = self.link_entities(text)
        markers = []
        for pos, w in enumerate(words):
            if w in ENEMY_MARKERS:
                markers.append((pos, 'enemies'))
            elif w in ALLY_MARKERS:
                markers.append((pos, 'allies'))
        draft = {'hero': None, 'allies': [], 'enemies': [], 'items': []}
        for name, kind, pos in entities:
            if kind == 'item':
                draft['items'].append(name)
                continue
            side = None
            for mpos, mside in markers:
                if mpos < pos:
                    side = mside
            if side:
                draft[side].append(name)
            elif draft['hero'] is None:
                draft['hero'] = name
            else:
                draft['allies'].append(name)
        return draft

    # ---------- поиск ----------

    def retrieve(self, question, k=5, draft=None):
        """Контекст для LLM: найденные карточки и факты из графа."""
        draft = draft or self.parse_draft(question)
        hits = self.vectors.search(question, k)
        # Карточки явно упомянутых сущностей добавляем, даже если векторный поиск их не поднял.
        mentioned = [h for h in [draft['hero']] + draft['allies'] + draft['enemies'] if h] + draft['items']
        present = {h['name'] for h in hits}
        for name in mentioned[:6]:
            if name not in present:
                kind = 'hero' if name in self.kb.heroes else 'item'
                card = (self.kb.heroes if kind == 'hero' else self.kb.items)[name]
                hits.append({'id': f'{kind}:{name}', 'kind': kind, 'name': name, 'score': None, 'text': card['raw_text']})
        return {'draft': draft, 'cards': hits, 'facts': self.graph_facts(draft)}

    def graph_facts(self, draft):
        g = self.graph
        facts = []
        hero = draft['hero']
        if hero:
            facts.append(f'{hero} слаб против: {", ".join(g.out(hero, "WEAK_AGAINST")) or "нет данных"}.')
            facts.append(f'{hero} силён против: {", ".join(g.out(hero, "STRONG_AGAINST")) or "нет данных"}.')
            facts.append(f'{hero} обычно собирает: {", ".join(g.out(hero, "BUILDS")) or "нет данных"}.')
            if draft['enemies']:
                m = g.matchups(hero, draft['enemies'])
                facts.append(f'Опасные для {hero} враги: {", ".join(m["dangerous"]) or "не выявлены"}; '
                             f'удобные: {", ".join(m["favorable"]) or "не выявлены"}.')
        # Предметы против врагов сразу фильтруем под своего героя: иначе модель возьмёт Skadi для Phoenix.
        profile = hero_profile(self.kb.heroes[hero], draft.get('position', 'auto')) if hero else None
        for enemy in draft['enemies']:
            items = g.out(enemy, 'COUNTERED_BY')
            if profile:
                # Неподходящие предметы в контекст не пишем: модель склонна пересказывать всё, что видит.
                ok = [x for x in items if fits(x, profile)]
                facts.append(f'Против {enemy} подходят {hero} предметы: {", ".join(ok) or "нет подходящих в базе"}.')
            else:
                facts.append(f'Против {enemy} в базе указаны предметы: {", ".join(items) or "нет данных"}.')
        return facts

    @staticmethod
    def format_context(ctx, max_chars=900):
        lines = []
        if ctx['facts']:
            lines.append('ФАКТЫ ИЗ ГРАФА ЗНАНИЙ:')
            lines += [f'- {f}' for f in ctx['facts']]
        lines.append('КАРТОЧКИ ИЗ БАЗЫ (векторный поиск):')
        for i, c in enumerate(ctx['cards'], 1):
            kind = 'Герой' if c['kind'] == 'hero' else 'Предмет'
            text = c['text'] if len(c['text']) <= max_chars else c['text'][:max_chars] + '…'
            lines.append(f'[{i}] {kind} {c["name"]}:\n{text}')
        return '\n'.join(lines)


RAG_SYSTEM = (
    'Ты — помощник по Dota 2. Отвечай по-русски, кратко и по делу, ТОЛЬКО на основе контекста из базы знаний. '
    'Называй только героев и предметы, которые есть в контексте. Если данных не хватает — так и скажи. '
    'Не придумывай тайминги, цифры и предметы.'
)


def answer(retriever, question):
    """Чистый RAG без инструментов: поиск → контекст → ответ модели."""
    ctx = retriever.retrieve(question)
    context = Retriever.format_context(ctx)
    reply = llm.chat(
        messages=[{'role': 'system', 'content': RAG_SYSTEM},
                  {'role': 'user', 'content': f'Контекст:\n{context}\n\nВопрос: {question}'}],
        options={'temperature': 0, 'num_ctx': llm.NUM_CTX, 'num_predict': 900},
    )
    return reply.message.content, ctx


if __name__ == '__main__':
    r = Retriever()
    while True:
        try:
            q = input('\nВопрос (exit — выход): ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ('exit', 'quit', 'выход'):
            break
        if q:
            text, ctx = answer(r, q)
            print('\nИсточники:', ', '.join(c['name'] for c in ctx['cards']))
            print(text)
