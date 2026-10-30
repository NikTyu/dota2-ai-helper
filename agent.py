"""ИИ-агент: RAG (модуль 2) + инструменты по протоколу MCP (модуль 3) + проверка ответа по базе.

Как отвечает агент:
1. RAG: находит в векторной БД и графе знаний карточки и факты по вопросу.
2. Запускает MCP-сервер (mcp_server.py) отдельным процессом и передаёт модели список его инструментов.
3. Модель (qwen3:8b через Ollama) сама решает, какие инструменты вызвать; агент выполняет вызовы
   через MCP и возвращает результаты модели — пока она не сформулирует ответ (не больше MAX_STEPS шагов).
4. Проверка: все предметы, названные в ответе, сверяются с базой и с героем. Если модель назвала
   неподходящий предмет, она один раз переписывает ответ без него.
Если Ollama недоступна, агент отвечает без модели: подбор из базы + найденные карточки.

Запуск в терминале: .venv/bin/python agent.py
"""
import asyncio
import contextlib
import json
import re
import sys
import time

import llm
import main
from compat import explain, hero_profile
from analys import DotaKnowledgeBase
from indexer import ROOT
from rag import Retriever

MAX_STEPS = 6
# num_predict ограничивает ответ: без него модель изредка зацикливается и пишет минутами.
OPTIONS = {'temperature': 0, 'num_ctx': llm.NUM_CTX, 'num_predict': 900}

SYSTEM = """Ты — ИИ-агент «Dota 2 Helper». Помогаешь игроку выбрать предметы под конкретный матч.
У тебя есть инструменты к локальной базе знаний. Порядок работы:
1. Определи из вопроса героя игрока, союзников и врагов (в контексте указано, кого распознал поиск).
2. Если известны герой и враги — вызови recommend_items. Если игрок описывает ситуацию в игре (отстаёт, много магии, невидимки и т. п.) — передай подходящие situations.
3. Если предмет под ситуацию уже есть в основном закупе (already_in_build) — обязательно скажи об этом: это тоже ответ.
4. Прежде чем советовать предмет, которого нет в результатах recommend_items, проверь его через check_items.
5. Для вопросов о героях и предметах используй get_hero, get_item, get_matchups, search_knowledge.
6. save_build_report вызывай только если игрок просит сохранить билд.
Правила ответа: пиши по-русски; называй только предметы из результатов инструментов и контекста; не придумывай цифры и тайминги; объясняй пользу предмета по полю why и карточкам, а не по догадкам.
Формат: одна-две фразы вывода, затем списки «Основное» и «Ситуативно» (у каждого ситуативного предмета через тире — чем он поможет), при необходимости «Матчапы»."""


def simplify_schema(schema):
    """Ollama понимает простые JSON-схемы: убираем anyOf с null, title и default."""
    props = {}
    for name, prop in schema.get('properties', {}).items():
        if 'anyOf' in prop:
            prop = next((p for p in prop['anyOf'] if p.get('type') != 'null'), prop['anyOf'][0])
        props[name] = {k: v for k, v in prop.items() if k not in ('title', 'default')}
    return {'type': 'object', 'properties': props, 'required': schema.get('required', [])}


def short(text, limit=600):
    return text if len(text) <= limit else text[:limit] + '…'


class Agent:
    def __init__(self, log=print, inprocess=False, build_lock=None):
        # Своя копия базы: веб-сервер перечитывает main.kb при каждом подборе в режиме драфта.
        kb = DotaKnowledgeBase(str(ROOT / 'data'), str(ROOT / 'data' / 'items'))
        self.retriever = Retriever(kb, log=log)
        self.inprocess = inprocess  # для тестов: MCP-сервер в том же процессе, протокол тот же
        self.build_lock = build_lock or contextlib.nullcontext()

    def _server(self):
        if self.inprocess:
            import mcp_server
            return mcp_server.server
        from mcp.client.stdio import StdioServerParameters
        return StdioServerParameters(command=sys.executable, args=[str(ROOT / 'mcp_server.py')], cwd=str(ROOT))

    def llm_ready(self):
        return llm.has_model(llm.MODEL)

    # ---------- основной цикл ----------

    def ask(self, question, draft=None):
        started = time.time()
        ctx = self.retriever.retrieve(question, draft=self._merge_draft(question, draft))
        result = {
            'question': question,
            'draft': ctx['draft'],
            'facts': ctx['facts'],
            'sources': [{'name': c['name'], 'kind': c['kind'], 'score': c['score']} for c in ctx['cards']],
            'trace': [{'type': 'retrieve', 'cards': [c['name'] for c in ctx['cards']], 'facts': len(ctx['facts'])}],
            'model': llm.MODEL,
        }
        if not self.llm_ready():
            result.update(self._fallback(ctx, f'Модель {llm.MODEL} недоступна в Ollama'))
        else:
            try:
                result.update(asyncio.run(self._run(question, ctx, result['trace'])))
            except Exception as ex:
                result.update(self._fallback(ctx, f'Ошибка агента: {ex}'))
        result['elapsed'] = round(time.time() - started, 1)
        return result

    def _merge_draft(self, question, draft):
        """Драфт из интерфейса важнее того, что удалось распознать в тексте вопроса."""
        parsed = self.retriever.parse_draft(question)
        if not draft or not draft.get('hero'):
            return parsed
        merged = {'hero': draft['hero'], 'allies': list(draft.get('allies') or []),
                  'enemies': list(draft.get('enemies') or []), 'items': parsed['items'],
                  'position': draft.get('position', 'auto')}
        return merged

    async def _run(self, question, ctx, trace):
        from mcp import Client

        context = Retriever.format_context(ctx, max_chars=600)
        grounding = [context]  # тексты, на которые модель имеет право опираться
        d = ctx['draft']
        draft_line = ''
        if d['hero']:
            draft_line = (f'\nДрафт: герой игрока — {d["hero"]}; союзники — {", ".join(d["allies"]) or "не указаны"}; '
                          f'враги — {", ".join(d["enemies"]) or "не указаны"}; позиция — {d.get("position", "auto")}.')
        messages = [
            {'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': f'Контекст из базы знаний:\n{context}\n{draft_line}\n\nВопрос игрока: {question}'},
        ]
        hero = d['hero']
        async with Client(self._server()) as client:
            tools = [{'type': 'function', 'function': {'name': t.name, 'description': t.description,
                                                       'parameters': simplify_schema(t.input_schema)}}
                     for t in (await client.list_tools()).tools]
            trace.append({'type': 'mcp', 'tools': [t['function']['name'] for t in tools]})
            answer = None
            for _ in range(MAX_STEPS):
                reply = await asyncio.to_thread(llm.chat, messages=messages, tools=tools,
                                                options=OPTIONS)
                msg = reply.message
                messages.append(msg)
                if not msg.tool_calls:
                    answer = msg.content or ''
                    break
                for call in msg.tool_calls:
                    name, args = call.function.name, dict(call.function.arguments or {})
                    res = await client.call_tool(name, args)
                    text = res.content[0].text if res.content else ''
                    trace.append({'type': 'tool', 'name': name, 'args': args, 'result': short(text), 'error': res.is_error})
                    messages.append({'role': 'tool', 'content': text, 'tool_name': name})
                    grounding.append(text)
                    if args.get('hero') and not hero:
                        card = self.retriever.kb.find_hero(args['hero'])
                        hero = card['name'] if card else None
            if answer is None:
                # Модель исчерпала шаги — просим ответить по уже собранным данным.
                messages.append({'role': 'user', 'content': 'Сформулируй итоговый ответ по уже полученным данным.'})
                reply = await asyncio.to_thread(llm.chat, messages=messages, options=OPTIONS)
                answer = reply.message.content or ''

        answer, checked = await self._validate(answer, hero, d.get('position', 'auto'), messages, trace, grounding)
        return {'ok': True, 'mode': 'agent', 'answer': answer, 'hero': hero, 'items': checked}

    async def _validate(self, answer, hero, position, messages, trace, grounding):
        """Сверяем предметы из ответа с базой и с данными инструментов; неподходящие — один раунд исправления."""
        known = {name for text in grounding for name, kind, _ in self.retriever.link_entities(text)[0] if kind == 'item'}
        checked = self._check(answer, hero, position, known)
        bad = [c for c in checked if not c['ok']]
        if bad:
            note = '; '.join(f'{c["name"]} — {c["reason"]}' for c in bad)
            trace.append({'type': 'validation', 'removed': [c['name'] for c in bad], 'note': note})
            messages.append({'role': 'assistant', 'content': answer})
            messages.append({'role': 'user', 'content': f'Проверка по базе: эти предметы нельзя советовать ({hero or "герой не указан"}): {note}. '
                                                        'Перепиши ответ без них и не добавляй новых предметов.'})
            reply = await asyncio.to_thread(llm.chat, messages=messages, options=OPTIONS)
            answer = reply.message.content or answer
            checked = self._check(answer, hero, position, known)
        else:
            trace.append({'type': 'validation', 'removed': [], 'note': 'все предметы из ответа подходят герою'})
        return answer, checked

    def recommended_items(self, text):
        """Предметы, которые ответ именно советует: в пунктах списка и строках «Заголовок: A, B»
        (до пояснения после тире) и выделенные жирным. Упоминания в пояснениях
        («Hex не даёт нажать BKB») не считаются советом."""
        parts = []
        for line in text.splitlines():
            m = re.match(r'^\s*(?:[-*•]|\d+[.)])\s+(.*)$', line)  # пункт списка
            m = m or re.match(r'^\s*[\w «»"*]{2,40}:\s*(.+)$', line)  # «Ситуативно: A, B»
            if m:
                for chunk in m.group(1).split(','):
                    parts.append(re.split(r'\s[—–-]\s|\(', chunk)[0])
        parts += re.findall(r'\*\*(.+?)\*\*', text)
        found = []
        for part in parts:
            for name, kind, _ in self.retriever.link_entities(part)[0]:
                if kind == 'item' and name not in found:
                    found.append(name)
        return found

    def _check(self, text, hero, position, known=None):
        card = self.retriever.kb.heroes.get(hero) if hero else None
        profile = hero_profile(card, position) if card else None
        checked = []
        for name in self.recommended_items(text):
            why = explain(name, profile) if profile else None
            if why is None and known is not None and name not in known:
                why = 'не встречается ни в результатах инструментов, ни в найденных карточках'
            checked.append({'name': name, 'ok': why is None, 'reason': why or 'есть в базе' + (', подходит герою' if profile else '')})
        return checked

    def _fallback(self, ctx, reason):
        """Ответ без модели: детерминированный подбор из базы + найденные карточки."""
        d = ctx['draft']
        lines = [f'{reason}. Ответ собран без LLM, напрямую из базы знаний.']
        items = []
        if d['hero']:
            with self.build_lock:
                data = main.build_recommendation(d['hero'], d['allies'], d['enemies'], [], d.get('position', 'auto'), use_llm=False)
            if data['ok']:
                lines.append(f'\nГерой: {data["hero"]["name"]} ({data["hero"]["role"]}).')
                if data['bad'] or data['good']:
                    lines.append(f'Опасные враги: {", ".join(data["bad"]) or "не выявлены"}; удобные: {", ".join(data["good"]) or "не выявлены"}.')
                lines.append('\nОсновное: ' + ', '.join(it['name'] for it in data['core']))
                if data['situational']:
                    lines.append('Ситуативно: ' + ', '.join(f'{it["name"]} (против {", ".join(it["against"])})' for it in data['situational']))
                items = [{'name': it['name'], 'ok': True, 'reason': 'подобрано по базе'} for it in data['core'] + data['situational']]
        lines.append('\nПохожие карточки в базе: ' + ', '.join(c['name'] for c in ctx['cards']))
        lines += [f'— {f}' for f in ctx['facts']]
        return {'ok': True, 'mode': 'fallback', 'answer': '\n'.join(lines), 'hero': d['hero'], 'items': items}


def print_trace(result):
    for step in result['trace']:
        if step['type'] == 'retrieve':
            print(f'  [RAG] карточки: {", ".join(step["cards"])}; фактов из графа: {step["facts"]}')
        elif step['type'] == 'mcp':
            print(f'  [MCP] инструменты: {", ".join(step["tools"])}')
        elif step['type'] == 'tool':
            print(f'  [tool] {step["name"]}({json.dumps(step["args"], ensure_ascii=False)})')
        elif step['type'] == 'validation':
            print(f'  [проверка] {step["note"]}')


if __name__ == '__main__':
    agent = Agent()
    print(f'Dota 2 AI Agent | модель: {llm.MODEL} ({"готова" if agent.llm_ready() else "недоступна — режим без LLM"})')
    print('Задай вопрос, например: «Я Phoenix против Huskar и PA, линию проиграл — что собрать?» (exit — выход)')
    while True:
        try:
            q = input('\nВопрос: ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ('exit', 'quit', 'выход'):
            break
        if not q:
            continue
        res = agent.ask(q)
        print_trace(res)
        print(f'\n{res["answer"]}\n\n({res["mode"]}, {res["elapsed"]} с)')
