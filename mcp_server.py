"""Модуль 3. MCP-сервер: инструменты агента поверх локальной базы знаний.

Инструменты детерминированные — они читают карточки, граф и векторную БД и проверяют
совместимость предметов с героем. LLM решает, какие инструменты вызвать и как объяснить результат,
но сами списки предметов берёт отсюда.

Запуск отдельно (для любого MCP-клиента, например MCP Inspector):  .venv/bin/python mcp_server.py
Агент (agent.py) запускает его сам и общается по stdio.
"""
import json
import sys

from mcp.server.mcpserver import MCPServer

import main
from compat import explain, hero_profile
from rag import ABBREVIATIONS, Retriever
from situations import SITUATIONS

server = MCPServer(
    'dota-helper',
    instructions='Инструменты базы знаний Dota 2: герои, предметы, матчапы, подбор и проверка предметов.',
    log_level='WARNING',
)
_retriever = None


def retriever():
    # stdout занят протоколом MCP, поэтому логи индексации — в stderr.
    global _retriever
    if _retriever is None:
        _retriever = Retriever(main.kb, log=lambda m: print(m, file=sys.stderr))
    return _retriever


def dump(data):
    return json.dumps(data, ensure_ascii=False)


def full_name(name):
    """Сокращения игроков (PA, BKB) → полные названия из базы."""
    return ABBREVIATIONS.get(name.strip().lower(), name.strip())


def find_hero(name):
    return main.kb.find_hero(full_name(name))


def resolve_heroes(names):
    found, unknown = [], []
    for n in names or []:
        card = find_hero(n)
        (found if card else unknown).append(card['name'] if card else n)
    return found, unknown


def build(hero, allies, enemies, situations, position):
    """Подбор из main.py с распознаванием сокращений; LLM внутри инструмента не вызывается."""
    return main.build_recommendation(full_name(hero), [full_name(x) for x in allies or []],
                                     [full_name(x) for x in enemies], situations or [], position, use_llm=False)


def short(text, limit=260):
    return text if len(text) <= limit else text[:limit] + '…'


SITUATION_IDS = ', '.join(f'{s["id"]} ({s["title"]})' for s in SITUATIONS)


@server.tool()
def search_knowledge(query: str, kind: str = '') -> str:
    """Семантический поиск по карточкам героев и предметов (векторная БД).
    kind: 'hero', 'item' или пусто — искать везде. Возвращает 4 самые близкие карточки."""
    hits = retriever().vectors.search(query, 4, kind=kind if kind in ('hero', 'item') else None)
    return dump([{'name': h['name'], 'kind': h['kind'], 'score': h['score'], 'excerpt': short(h['text'], 400)} for h in hits])


@server.tool()
def get_hero(name: str) -> str:
    """Карточка героя: роль, атрибут, тип атаки, описание, против кого слаб/силён, основной закуп."""
    card = find_hero(name)
    if not card:
        return dump({'error': f'Герой «{name}» не найден в базе.'})
    return dump({k: card[k] for k in ('name', 'role', 'primary_attr', 'attack_type', 'description',
                                      'weak_against', 'strong_against', 'core_items')})


@server.tool()
def get_item(name: str) -> str:
    """Карточка предмета: цена, тип и когда его покупать."""
    real = main.exact_item(full_name(name))
    if not real:
        return dump({'error': f'Предмета «{name}» нет в базе.'})
    card = main.kb.items[real]
    return dump({'name': real, 'cost': card['cost'], 'type': card['type'], 'when_to_buy': main.reason_from_card(real)})


@server.tool()
def get_matchups(hero: str, enemies: list[str]) -> str:
    """Матчапы героя против врагов по графу знаний: какие враги опасны, а какие удобны."""
    found, unknown = resolve_heroes([hero] + list(enemies))
    if unknown:
        return dump({'error': 'Не найдены герои: ' + ', '.join(unknown)})
    me, foes = found[0], found[1:]
    return dump({'hero': me, **retriever().graph.matchups(me, foes)})


@server.tool(description=(
    'Подбор предметов из базы с проверкой совместимости с героем. Возвращает основной закуп героя, '
    'ситуативные предметы против врагов и предметы под игровые ситуации. '
    f'situations — список id: {SITUATION_IDS}. position: auto, core (1–3) или support (4–5).'
))
def recommend_items(hero: str, enemies: list[str], allies: list[str] | None = None,
                    situations: list[str] | None = None, position: str = 'auto') -> str:
    data = build(hero, allies, enemies, situations, position)
    if not data['ok']:
        return dump({'error': data['error']})
    return dump({
        'hero': data['hero']['name'],
        'position': data['position'],
        'dangerous_enemies': data['bad'],
        'favorable_enemies': data['good'],
        'core_items': [it['name'] for it in data['core']],
        'situational': [{'name': it['name'], 'against': it['against'], 'why': short(it['reason'], 160)}
                        for it in data['situational']],
        'by_situation': [{'situation': s['title'], 'items': [it['name'] for it in s['items']],
                          'already_in_build': s['already']} for s in data['situations']],
    })



@server.tool()
def check_items(hero: str, items: list[str], position: str = 'auto') -> str:
    """Проверяет предметы: есть ли они в базе и подходят ли герою. Используй перед тем, как советовать предмет."""
    card = find_hero(hero)
    if not card:
        return dump({'error': f'Герой «{hero}» не найден в базе.'})
    profile = hero_profile(card, position)
    result = []
    for raw in items:
        real = main.exact_item(full_name(raw))
        if not real:
            result.append({'item': raw, 'ok': False, 'reason': 'такого предмета нет в базе'})
            continue
        why = explain(real, profile)
        result.append({'item': real, 'ok': why is None, 'reason': why or 'подходит'})
    return dump({'hero': card['name'], 'items': result})


@server.tool()
def save_build_report(hero: str, enemies: list[str], allies: list[str] | None = None,
                      situations: list[str] | None = None, position: str = 'auto') -> str:
    """Сохраняет полный билд в текстовый файл build_<герой>.txt. Вызывай, только если пользователь просит сохранить."""
    data = build(hero, allies, enemies, situations, position)
    if not data['ok']:
        return dump({'error': data['error']})
    path = main.save_report(data['hero']['name'], main.format_report(data))
    return dump({'saved': path.name})


if __name__ == '__main__':
    server.run()
