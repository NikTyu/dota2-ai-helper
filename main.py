"""Dota 2 helper: проверяемый билд из локальной базы, LLM ранжирует, но не придумывает предметы."""
import json
import re
from pathlib import Path

import llm
from analys import DotaKnowledgeBase
from compat import fits, hero_profile
from situations import BY_ID as SITUATIONS

ollama = llm.ollama

ROOT = Path(__file__).resolve().parent
kb = DotaKnowledgeBase(str(ROOT / 'data'), str(ROOT / 'data' / 'items'))
MODEL = llm.MODEL

START_ITEMS = ['Tango', 'Iron Branch', 'Quelling Blade', 'Circlet', 'Slippers of Agility',
               'Gauntlets of Strength', 'Mantle of Intelligence', 'Magic Stick',
               'Enchanted Mango', 'Faerie Fire', 'Blood Grenade', 'Observer Ward', 'Sentry Ward']


def exact_item(name):
    """Только точное совпадение с записью в базе; fuzzy-match опасен для билда."""
    key = kb._normalize(name)
    real = kb._norm_item_map.get(key)
    return real if real in kb.items else None


def reason_from_card(name):
    """Достаём причину из базы, а не просим LLM фантазировать."""
    raw = kb.items[name]['raw_text']
    m = re.search(r'^- Ситуация для покупки:\s*(.+)$', raw, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return 'Подробное объяснение отсутствует в локальной карточке предмета.'


def enemy_targets_from_card(name, enemies):
    raw = kb.items[name]['raw_text']
    m = re.search(r'^- Эффективен против героев:\s*(.+)$', raw, re.MULTILINE)
    if not m:
        return []
    names = {kb._normalize(x) for x in re.split(r'[,;]', m.group(1))}
    return [e for e in enemies if kb._normalize(e) in names]


def is_basic(name):
    """Стартовые расходники и голые компоненты (Clarity, Ultimate Orb...) — не ситуативный закуп.
    Защитные (Magic Wand, Infused Raindrops, Cloak) оставляем."""
    item_type = kb.items[name]['type'].lower()
    return ('стартов' in item_type or 'компонент' in item_type) and 'защит' not in item_type


def llm_rank(candidate_names, hero, enemies):
    """Модель может лишь переставить номера реальных предметов, не писать новые.
    Возвращает (список, использовалась_ли_модель)."""
    if not ollama or len(candidate_names) < 2:
        return candidate_names, False
    options = [{'id': i, 'name': name, 'reason': reason_from_card(name)[:220]}
               for i, name in enumerate(candidate_names)]
    try:
        reply = llm.chat(
            model=MODEL,
            messages=[
                {'role': 'system', 'content': 'Ты ранжируешь предметы Dota 2. Верни ТОЛЬКО JSON вида {"ids":[0,2,1]}. Можно использовать исключительно целые id из входного списка, без новых предметов.'},
                {'role': 'user', 'content': json.dumps({'hero': hero, 'enemies': enemies, 'items': options}, ensure_ascii=False)}
            ],
            format='json',
            options={'temperature': 0, 'num_ctx': llm.NUM_CTX, 'num_predict': 200}
        )
        data = json.loads(reply['message']['content'])
        ids = data.get('ids', [])
        if not isinstance(ids, list):
            return candidate_names, False
        order = []
        for i in ids:
            if type(i) is int and 0 <= i < len(candidate_names) and i not in order:
                order.append(i)
        # Даже если модель вернула мусор, не потеряем исходные позиции.
        order.extend(i for i in range(len(candidate_names)) if i not in order)
        return [candidate_names[i] for i in order], True
    except Exception as ex:
        print(f'[Предупреждение] Ollama не смогла ранжировать предметы: {ex}. Используется база знаний.')
        return candidate_names, False


def item_info(name, against=None):
    """Карточка предмета для вывода: название, цена, тип и причина из базы."""
    card = kb.items[name]
    info = {'name': name, 'cost': card['cost'], 'type': card['type'], 'reason': reason_from_card(name)}
    if against is not None:
        info['against'] = sorted(against)
    return info


def situation_items(situation_ids, profile, taken):
    """Предметы под выбранные игровые ситуации: только из базы и подходящие герою."""
    result = []
    for sid in situation_ids:
        sit = SITUATIONS.get(sid)
        if not sit:
            continue
        items, already = [], []
        for raw in sit['support' if profile['support'] else 'core']:
            item = exact_item(raw)
            if not item or not fits(item, profile):
                continue
            if item in taken:
                already.append(item)
            elif item not in items:
                items.append(item)
        result.append({'id': sid, 'title': sit['title'], 'description': sit['description'],
                       'items': [item_info(x) for x in items], 'already': already})
    return result


def build_recommendation(my_hero, allies, enemies, situations=(), position='auto', use_llm=True):
    """Структурированный результат анализа; общий для CLI и веб-интерфейса.
    situations — id из situations.py, position — 'auto', 'core' или 'support'."""
    kb.reload()
    me = kb.find_hero(my_hero)
    if not me:
        return {'ok': False, 'error': f'Герой «{my_hero}» не найден в базе. Проверь написание.'}
    unknown = [n for n in allies + enemies if not kb.find_hero(n)]
    if unknown:
        return {'ok': False, 'error': 'Не найдены герои: ' + ', '.join(unknown) + '. Исправь имена, чтобы анализ не был выдуманным.'}
    allies = [kb.find_hero(x)['name'] for x in allies]
    enemies = [kb.find_hero(x)['name'] for x in enemies]

    core = []
    for item in me['core_items']:
        real = exact_item(item)
        if real and real not in core:
            core.append(real)

    start = [x for x in START_ITEMS if exact_item(x)]
    # На старте только основные, реально существующие в базе расходники/компоненты.
    start = start[:5]

    # Совместимость с героем проверяется до подсчёта очков и до LLM: модели не из чего выбрать лишнее.
    profile = hero_profile(me, position)
    scores = {}
    targets = {}
    for e in enemies:
        card = kb.find_hero(e)
        for raw in card['counter_items']:
            item = exact_item(raw)
            if item and item not in start and item not in core and fits(item, profile) and not is_basic(item):
                scores[item] = scores.get(item, 0) + 2
                targets.setdefault(item, set()).add(e)
    for item in kb.items:
        matched = enemy_targets_from_card(item, enemies)
        if matched and item not in start and item not in core and fits(item, profile) and not is_basic(item):
            scores[item] = scores.get(item, 0) + len(matched)
            targets.setdefault(item, set()).update(matched)

    situational = sorted(scores, key=lambda x: (-scores[x], x))[:12]
    situational, llm_used = llm_rank(situational, me['name'], enemies) if use_llm else (situational, False)
    situational = situational[:6]

    weak = {kb._normalize(x) for x in me['weak_against']}
    strong = {kb._normalize(x) for x in me['strong_against']}
    bad = [e for e in enemies if kb._normalize(e) in weak or kb._normalize(me['name']) in {kb._normalize(x) for x in kb.find_hero(e)['strong_against']}]
    good = [e for e in enemies if kb._normalize(e) in strong or kb._normalize(me['name']) in {kb._normalize(x) for x in kb.find_hero(e)['weak_against']}]

    by_situation = situation_items(situations, profile, set(start) | set(core))

    return {
        'ok': True,
        'hero': {'name': me['name'], 'role': me['role'], 'primary_attr': me['primary_attr'],
                 'attack_type': me['attack_type'], 'description': me['description']},
        'allies': allies,
        'enemies': enemies,
        'bad': bad,
        'good': good,
        'start': [item_info(x) for x in start],
        'core': [item_info(x) for x in core],
        'situational': [item_info(x, targets.get(x, [])) for x in situational],
        'llm_used': llm_used,
        'position': 'support' if profile['support'] else 'core',
        'situations': by_situation,
    }


def format_report(data):
    """Текстовый отчёт в прежнем формате build_<hero>.txt."""
    me = data['hero']
    lines = [f'СБОРКА: {me["name"]}', f'Роль: {me["role"]}',
             f'Союзники: {", ".join(data["allies"]) or "не указаны"}',
             f'Противники: {", ".join(data["enemies"]) or "не указаны"}',
             f'Опасные матчапы по базе: {", ".join(data["bad"]) or "не выявлены"}',
             f'Удобные матчапы по базе: {", ".join(data["good"]) or "не выявлены"}',
             '', '1. СТАРТОВЫЙ ЗАКУП (варианты, не покупать всё сразу):']
    for it in data['start']:
        lines.append(f'• {it["name"]} — {it["reason"]}')
    lines.append('\n2. ОСНОВНЫЕ ПРЕДМЕТЫ (в порядке из карточки героя):')
    for it in data['core']:
        lines.append(f'• {it["name"]} — {it["reason"]}')
    if not data['core']:
        lines.append('Нет подтверждённых предметов в карточке героя.')
    lines.append('\n3. СИТУАТИВНЫЕ ПРЕДМЕТЫ (по базе, НЕ обязательны все):')
    for it in data['situational']:
        lines.append(f'• {it["name"]} — против: {", ".join(it["against"])}. {it["reason"]}')
    if not data['situational']:
        lines.append('Недостаточно данных для обоснованного выбора.')
    if data.get('situations'):
        pos = 'саппорт' if data['position'] == 'support' else 'кор'
        lines.append(f'\n4. ПО СИТУАЦИИ В ИГРЕ (позиция: {pos}):')
        for sit in data['situations']:
            lines.append(f'— {sit["title"]}: {sit["description"]}')
            for it in sit['items']:
                lines.append(f'  • {it["name"]} — {it["reason"]}')
            if sit['already']:
                lines.append(f'  Уже в закупе выше: {", ".join(sit["already"])}.')
            if not sit['items'] and not sit['already']:
                lines.append('  Подходящих предметов в базе не найдено.')
    lines.append('\nВажно: список основан на локальных файлах, их актуальность относительно текущего патча не проверялась. Тайминги не выдумываются.')
    return '\n'.join(lines)


def save_report(hero_name, text):
    filename = ROOT / ('build_' + re.sub(r'[^a-z0-9]+', '_', hero_name.lower()).strip('_') + '.txt')
    filename.write_text(text, encoding='utf-8')
    return filename


def run_dota_agent(my_hero, allies, enemies):
    data = build_recommendation(my_hero, allies, enemies)
    if not data['ok']:
        return data['error']
    result = format_report(data)
    save_report(data['hero']['name'], result)
    return result


if __name__ == '__main__':
    print(f'Dota 2 AI Agent | героев: {len(kb.heroes)} | предметов: {len(kb.items)}')
    print('Введи exit для выхода. Имена героев лучше вводить на английском.')
    while True:
        try:
            hero = input('\nТвой герой: ').strip()
            if hero.lower() in ('exit', 'quit', 'выход'):
                break
            if not hero:
                continue
            allies = [x.strip() for x in input('Союзники через запятую: ').split(',') if x.strip()]
            enemies = [x.strip() for x in input('Враги через запятую: ').split(',') if x.strip()]
            print('\n' + run_dota_agent(hero, allies, enemies))
        except (KeyboardInterrupt, EOFError):
            break
        except Exception as exc:
            print(f'Ошибка: {exc}')
