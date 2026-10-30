"""Совместимость предмета с героем: отсекаем то, что герою не подходит, ДО ранжирования.

Пример проблемы: Eye of Skadi эффективен против Huskar, но это предмет для героев, которые
бьют с руки, — Phoenix его не собирает. Профиль героя строится только по его карточке:
позиция (из роли или выбора игрока), тип атаки, основной атрибут и основной закуп.
"""
import re

# Предметы для героев, которые наносят урон атаками. Они же служат признаком такого героя,
# если встречаются в его основном закупе.
ATTACK_ITEMS = {
    'Eye of Skadi', 'Butterfly', 'Daedalus', 'Monkey King Bar', 'Satanic', 'Desolator', 'Abyssal Blade',
    'Skull Basher', 'Battle Fury', 'Mjollnir', 'Maelstrom', 'Diffusal Blade', 'Disperser', 'Manta Style',
    'Divine Rapier', 'Moon Shard', 'Mask of Madness', 'Armlet of Mordiggian', 'Echo Sabre', 'Harpoon',
    'Crystalys', 'Demon Edge', 'Javelin', 'Hurricane Pike', 'Dragon Lance', 'Silver Edge', 'Radiance',
    'Sange and Yasha', 'Morbid Mask',
}
# Не для саппортов 4–5: дорогие предметы коров и оффлейнеров.
CORE_ONLY = {
    'Bloodthorn', 'Assault Cuirass', 'Heart of Tarrasque', "Heaven's Halberd", 'Crimson Guard', 'Blade Mail',
    "Aghanim's Blessing", "Linken's Sphere", 'Hand of Midas',
}
# Командные и сейв-предметы саппортов: керри первой позиции их не собирает.
SUPPORT_ITEMS = {
    'Glimmer Cape', 'Mekansm', 'Guardian Greaves', 'Boots of Bearing', 'Holy Locket', 'Pavise',
    'Urn of Shadows', 'Tranquil Boots', 'Arcane Boots', 'Pipe of Insight', 'Aether Lens', 'Infused Raindrops',
}
# «Кор-предмет для атакующих магов» — только для коров с основным атрибутом «Интеллект».
CASTER_CORE_ITEMS = {'Witch Blade', 'Parasma'}
# Предметы для героев передней линии: силовики или герои ближнего боя.
FRONTLINE_ITEMS = {"Heaven's Halberd", 'Crimson Guard', 'Blade Mail', 'Vanguard'}
# Базовые ботинки: второй паре в закупе не место, если в карточке героя ботинки уже есть.
# Улучшения (Boots of Travel, Guardian Greaves, Boots of Bearing) сюда не входят.
BASIC_BOOTS = {'Boots of Speed', 'Phase Boots', 'Power Treads', 'Arcane Boots', 'Tranquil Boots'}
MELEE_ONLY = {'Battle Fury', 'Echo Sabre', 'Harpoon'}
RANGED_ONLY = {'Hurricane Pike', 'Dragon Lance'}
SUPPORT_ROLES = ('саппорт', 'поддержка', 'полная поддержка')


def _norm(name):
    return re.sub(r'[^a-zа-я0-9]', '', name.lower())


_ATTACK = {_norm(x) for x in ATTACK_ITEMS}
_CORE_ONLY = {_norm(x) for x in CORE_ONLY}
_SUPPORT = {_norm(x) for x in SUPPORT_ITEMS}
_FRONTLINE = {_norm(x) for x in FRONTLINE_ITEMS}
_CASTER = {_norm(x) for x in CASTER_CORE_ITEMS}
_BOOTS = {_norm(x) for x in BASIC_BOOTS}
_MELEE = {_norm(x) for x in MELEE_ONLY}
_RANGED = {_norm(x) for x in RANGED_ONLY}


def first_role(hero):
    return hero.get('role', '').split(',')[0].split('(')[0].strip().lower()


def is_support(hero):
    """Определяем по первой роли из карточки героя."""
    return first_role(hero) in SUPPORT_ROLES


def hero_profile(hero, position='auto'):
    """position: 'auto' — по карточке, 'core' или 'support' — выбор игрока."""
    support = is_support(hero) if position == 'auto' else position == 'support'
    carry = not support and first_role(hero) == 'керри'
    own = {_norm(x) for x in hero.get('core_items', [])}
    attacker = not support and (carry or 'ловк' in hero.get('primary_attr', '').lower() or bool(own & _ATTACK))
    attack_type = hero.get('attack_type', '').lower()
    return {
        'own': own,
        'frontline': 'сил' in hero.get('primary_attr', '').lower() or 'ближ' in attack_type,
        'int_core': not support and 'интел' in hero.get('primary_attr', '').lower(),
        'has_boots': bool(own & _BOOTS),
        'support': support,
        'carry': carry,
        'attacker': attacker,
        'melee': 'ближ' in attack_type,
        'ranged': 'даль' in attack_type,
    }


def explain(item, profile):
    """Почему предмет не подходит герою; None — подходит."""
    key = _norm(item)
    if key in profile.get('own', ()):
        return None  # предмет из основного закупа самого героя
    if key in _ATTACK and not profile['attacker']:
        return 'предмет для героев, которые наносят урон атаками'
    if key in _CORE_ONLY and profile['support']:
        return 'дорогой предмет коров, не для саппорта'
    if key in _SUPPORT and profile['carry']:
        return 'саппортский предмет, керри его не собирает'
    if key in _CASTER and not profile['int_core']:
        return 'предмет для кор-героев с интеллектом'
    if key in _FRONTLINE and not profile['frontline']:
        return 'предмет для силовиков и героев ближнего боя'
    if key in _BOOTS and profile['has_boots']:
        return 'ботинки уже есть в основном закупе героя'
    if key in _MELEE and not profile['melee']:
        return 'только для героев ближнего боя'
    if key in _RANGED and not profile['ranged']:
        return 'только для героев дальнего боя'
    return None


def fits(item, profile):
    """Подходит ли предмет герою с таким профилем."""
    return explain(item, profile) is None
