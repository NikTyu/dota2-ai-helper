"""Тесты агента: индексация (модуль 1), RAG (модуль 2), MCP-инструменты и цикл агента (модуль 3).

Ollama для тестов не нужна: индекс строится запасным векторизатором во временной папке,
а ответы модели подменяются заготовками. Нужны пакеты из requirements.txt:
    .venv/bin/python -m unittest discover tests
"""
import asyncio
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_DEPS = all(importlib.util.find_spec(m) for m in ('chromadb', 'mcp'))
TMP = None
PATCHES = []


def setUpModule():
    global TMP
    if not HAS_DEPS:
        return
    import indexer
    TMP = Path(tempfile.mkdtemp())
    PATCHES.extend([
        mock.patch.object(indexer, 'INDEX_DIR', TMP),
        mock.patch.object(indexer, 'pick_embedder', lambda: indexer.HashEmbedder()),
    ])
    for p in PATCHES:
        p.start()


def tearDownModule():
    for p in PATCHES:
        p.stop()
    if TMP:
        shutil.rmtree(TMP, ignore_errors=True)


def retriever():
    import rag
    if not hasattr(retriever, 'cached'):
        retriever.cached = rag.Retriever(log=lambda m: None)
    return retriever.cached


@unittest.skipUnless(HAS_DEPS, 'нужны пакеты из requirements.txt')
class IndexTest(unittest.TestCase):
    def test_vector_index(self):
        r = retriever()
        self.assertEqual(r.vectors.count(), len(r.kb.heroes) + len(r.kb.items))
        self.assertEqual(r.vectors.search('Phoenix', 3)[0]['name'], 'Phoenix')
        self.assertTrue(all(h['kind'] == 'item' for h in r.vectors.search('Black King Bar', 3, kind='item')))

    def test_graph(self):
        g = retriever().graph
        stats = g.stats()
        self.assertEqual(stats['nodes']['hero'], 127)
        self.assertEqual(set(stats['edges']), {'WEAK_AGAINST', 'STRONG_AGAINST', 'BUILDS', 'COUNTERED_BY', 'EFFECTIVE_AGAINST'})
        m = g.matchups('Phoenix', ['Juggernaut', 'Wraith King', 'Axe'])
        self.assertEqual(m, {'dangerous': ['Juggernaut'], 'favorable': ['Wraith King']})
        self.assertIn('Spirit Vessel', g.counters('Huskar'))
        self.assertIn('Nyx Assassin', g.out('Tinker', 'WEAK_AGAINST'))


@unittest.skipUnless(HAS_DEPS, 'нужны пакеты из requirements.txt')
class RagTest(unittest.TestCase):
    def test_parse_draft(self):
        d = retriever().parse_draft('Я играю на Phoenix против Huskar и PA, союзник Axe. Нужен ли BKB?')
        self.assertEqual(d['hero'], 'Phoenix')
        self.assertEqual(d['enemies'], ['Huskar', 'Phantom Assassin'])
        self.assertEqual(d['allies'], ['Axe'])
        self.assertEqual(d['items'], ['Black King Bar'])

    def test_facts_filtered_for_hero(self):
        ctx = retriever().retrieve('Я Phoenix против Huskar')
        fact = next(f for f in ctx['facts'] if f.startswith('Против Huskar'))
        self.assertNotIn('Eye of Skadi', fact)
        self.assertIn('Spirit Vessel', fact)

    def test_mentioned_cards_in_context(self):
        ctx = retriever().retrieve('Я Phoenix против Huskar')
        names = {c['name'] for c in ctx['cards']}
        self.assertTrue({'Phoenix', 'Huskar'} <= names)


def call(tool, args):
    from mcp import Client
    import mcp_server

    async def run():
        async with Client(mcp_server.server) as client:
            res = await client.call_tool(tool, args)
            return json.loads(res.content[0].text)
    mcp_server._retriever = retriever()
    return asyncio.run(run())


@unittest.skipUnless(HAS_DEPS, 'нужны пакеты из requirements.txt')
class McpTest(unittest.TestCase):
    def test_tools_listed(self):
        from mcp import Client
        import mcp_server

        async def run():
            async with Client(mcp_server.server) as client:
                return {t.name for t in (await client.list_tools()).tools}
        self.assertEqual(asyncio.run(run()), {'search_knowledge', 'get_hero', 'get_item', 'get_matchups',
                                              'recommend_items', 'check_items', 'save_build_report'})

    def test_recommend_without_skadi(self):
        data = call('recommend_items', {'hero': 'Phoenix', 'enemies': ['Huskar', 'PA'], 'position': 'core'})
        names = data['core_items'] + [x['name'] for x in data['situational']]
        self.assertNotIn('Eye of Skadi', names)

    def test_check_items(self):
        phoenix = {x['item']: x['ok'] for x in call('check_items', {'hero': 'Phoenix', 'items': ['skadi', 'Tranquil Boots', 'Super Sword']})['items']}
        self.assertEqual(phoenix, {'Eye of Skadi': False, 'Tranquil Boots': True, 'Super Sword': False})
        jugg = call('check_items', {'hero': 'jugg', 'items': ['Eye of Skadi']})['items'][0]
        self.assertTrue(jugg['ok'])

    def test_unknown_hero(self):
        self.assertIn('error', call('get_matchups', {'hero': 'Fooo', 'enemies': ['Axe']}))


def fake_reply(content='', tool=None, args=None):
    calls = [SimpleNamespace(function=SimpleNamespace(name=tool, arguments=args))] if tool else None
    return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))


@unittest.skipUnless(HAS_DEPS, 'нужны пакеты из requirements.txt')
class AgentTest(unittest.TestCase):
    def make_agent(self):
        import agent
        import mcp_server
        mcp_server._retriever = retriever()
        with mock.patch('agent.Retriever', return_value=retriever()):
            return agent.Agent(log=lambda m: None, inprocess=True)

    def test_tool_loop_and_validation(self):
        import agent
        replies = iter([
            fake_reply(tool='recommend_items', args={'hero': 'Phoenix', 'enemies': ['Huskar']}),
            fake_reply('Основное: Spirit Vessel.\nСитуативно: Eye of Skadi, Scythe of Vyse.'),
            fake_reply('Основное: Spirit Vessel.\nСитуативно: Scythe of Vyse.'),  # после проверки
        ])
        a = self.make_agent()
        with mock.patch.object(agent.llm, 'chat', lambda **kw: next(replies)), \
                mock.patch.object(agent.Agent, 'llm_ready', lambda self: True):
            res = a.ask('Я Phoenix против Huskar, что собрать?')
        self.assertEqual(res['mode'], 'agent')
        tools = [s for s in res['trace'] if s['type'] == 'tool']
        self.assertEqual(tools[0]['name'], 'recommend_items')
        self.assertNotIn('Eye of Skadi', tools[0]['result'])
        validation = next(s for s in res['trace'] if s['type'] == 'validation')
        self.assertEqual(validation['removed'], ['Eye of Skadi'])
        self.assertNotIn('Eye of Skadi', res['answer'])
        self.assertTrue(all(i['ok'] for i in res['items']))

    def test_mentions_in_explanations_are_not_recommendations(self):
        a = self.make_agent()
        text = ('Основное:\n- **Scythe of Vyse** — не даёт врагу нажать Manta Style или Eye of Skadi.\n'
                '- Ghost Scepter (против Phantom Assassin)\nВ прозе упомянем Butterfly.')
        self.assertEqual(a.recommended_items(text), ['Scythe of Vyse', 'Ghost Scepter'])

    def test_fallback_without_llm(self):
        import agent
        a = self.make_agent()
        with mock.patch.object(agent.Agent, 'llm_ready', lambda self: False):
            res = a.ask('Я Phoenix против Huskar')
        self.assertEqual(res['mode'], 'fallback')
        self.assertIn('Spirit Vessel', res['answer'])
        self.assertNotIn('Eye of Skadi', res['answer'].split('Похожие карточки')[0])


if __name__ == '__main__':
    unittest.main()
