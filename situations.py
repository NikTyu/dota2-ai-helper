"""Игровые ситуации: какие предметы рассматривать, если игра складывается определённым образом.

Пулы предметов задаются отдельно для коров (1–3) и саппортов (4–5) и подтверждаются по
локальной базе в main.py — предметов, которых нет в data/items, в ответе не будет.
Списки triggers — герои, при которых ситуация подсвечивается в интерфейсе как вероятная
(min — сколько таких врагов нужно для подсказки). Выбор ситуации всегда остаётся за игроком.
"""

SITUATIONS = [
    {
        'id': 'behind',
        'title': 'Отстаём',
        'description': 'Проиграна линия или игра идёт не в нашу пользу: важнее выжить и не отдать фраги, чем строить дорогой урон.',
        'core': ['Magic Wand', 'Black King Bar', 'Aeon Disk', 'Ghost Scepter', 'Force Staff', 'Manta Style'],
        'support': ['Magic Wand', 'Glimmer Cape', 'Force Staff', 'Ghost Scepter', 'Smoke of Deceit', 'Observer Ward'],
        'triggers': [], 'min': 0,
    },
    {
        'id': 'ahead',
        'title': 'Выигрываем',
        'description': 'Есть преимущество по золоту: имеет смысл ускорить давление и закончить игру, пока враги слабее.',
        'core': ['Blink Dagger', 'Desolator', 'Orchid Malevolence', 'Assault Cuirass', 'Daedalus', 'Shadow Blade'],
        'support': ['Blink Dagger', 'Aether Lens', 'Rod of Atos', "Aghanim's Shard", 'Smoke of Deceit', 'Observer Ward'],
        'triggers': [], 'min': 0,
    },
    {
        'id': 'magic',
        'title': 'Много магии',
        'description': 'У врагов несколько героев с сильным магическим прокастом.',
        'core': ['Black King Bar', 'Pipe of Insight', 'Mage Slayer', 'Glimmer Cape', 'Aeon Disk', 'Cloak'],
        'support': ['Glimmer Cape', 'Pipe of Insight', 'Infused Raindrops', 'Aeon Disk', 'Cloak'],
        'triggers': ['Lion', 'Lina', 'Zeus', 'Skywrath Mage', 'Leshrac', 'Queen of Pain', 'Storm Spirit', 'Puck',
                     'Pugna', 'Necrophos', 'Invoker', 'Jakiro', 'Nyx Assassin', 'Earthshaker', 'Shadow Fiend',
                     'Witch Doctor', 'Death Prophet', 'Ember Spirit', 'Void Spirit', 'Grimstroke', 'Dark Willow',
                     'Keeper of the Light', 'Lich', 'Ancient Apparition', 'Outworld Destroyer'],
        'min': 2,
    },
    {
        'id': 'physical',
        'title': 'Много физ. урона',
        'description': 'Против вас играют сильные керри, которые убивают с руки.',
        'core': ['Assault Cuirass', "Shiva's Guard", 'Crimson Guard', 'Blade Mail', "Heaven's Halberd", 'Ghost Scepter', 'Butterfly'],
        'support': ['Ghost Scepter', 'Pavise', 'Solar Crest', 'Force Staff', 'Ethereal Blade'],
        'triggers': ['Phantom Assassin', 'Juggernaut', 'Troll Warlord', 'Slark', 'Ursa', 'Sven', 'Faceless Void',
                     'Monkey King', 'Drow Ranger', 'Sniper', 'Terrorblade', 'Lifestealer', 'Anti-Mage', 'Luna',
                     'Gyrocopter', 'Weaver', 'Clinkz', 'Templar Assassin', 'Riki', 'Chaos Knight', 'Phantom Lancer',
                     'Kez', 'Muerta', 'Medusa', 'Lycan', 'Naga Siren'],
        'min': 2,
    },
    {
        'id': 'disable',
        'title': 'Много контроля',
        'description': 'Враги умеют надолго выключать героя станами, хексами и сайленсами.',
        'core': ['Black King Bar', "Linken's Sphere", 'Manta Style', 'Lotus Orb', 'Aeon Disk'],
        'support': ['Lotus Orb', 'Glimmer Cape', "Eul's Scepter of Divinity", 'Force Staff', 'Wind Waker', 'Aeon Disk'],
        'triggers': ['Lion', 'Shadow Shaman', 'Bane', 'Faceless Void', 'Magnus', 'Enigma', 'Tidehunter', 'Earthshaker',
                     'Sand King', 'Centaur Warrunner', 'Beastmaster', 'Ogre Magi', 'Disruptor', 'Kunkka', 'Mars',
                     'Legion Commander', 'Doom', 'Batrider', 'Pudge', 'Axe', 'Silencer', 'Dragon Knight', 'Slardar',
                     'Jakiro', 'Elder Titan', 'Primal Beast'],
        'min': 2,
    },
    {
        'id': 'invis',
        'title': 'Невидимость',
        'description': 'У врагов есть герои с невидимостью — без детекта их не поймать.',
        'core': ['Dust of Appearance', 'Sentry Ward', 'Gem of True Sight'],
        'support': ['Dust of Appearance', 'Sentry Ward', 'Gem of True Sight'],
        'triggers': ['Riki', 'Clinkz', 'Bounty Hunter', 'Weaver', 'Nyx Assassin', 'Mirana', 'Invoker',
                     'Treant Protector', 'Templar Assassin', 'Void Spirit'],
        'min': 1,
    },
    {
        'id': 'illusions',
        'title': 'Иллюзии и саммоны',
        'description': 'Много юнитов на карте: нужен урон по площади.',
        'core': ['Mjollnir', 'Battle Fury', 'Maelstrom', 'Radiance', "Shiva's Guard", 'Crimson Guard'],
        'support': ["Shiva's Guard", 'Mekansm', 'Veil of Discord'],
        'triggers': ['Phantom Lancer', 'Naga Siren', 'Chaos Knight', 'Terrorblade', 'Meepo', 'Broodmother',
                     'Lone Druid', "Nature's Prophet", 'Beastmaster', 'Visage', 'Chen', 'Lycan', 'Arc Warden',
                     'Dark Seer', 'Shadow Demon', 'Venomancer', 'Enigma', 'Warlock', 'Undying'],
        'min': 1,
    },
    {
        'id': 'heal',
        'title': 'Хил и реген',
        'description': 'Враги много лечатся или вампирят — нужно срезать лечение.',
        'core': ['Spirit Vessel', 'Eye of Skadi', "Shiva's Guard"],
        'support': ['Spirit Vessel', 'Urn of Shadows', "Shiva's Guard"],
        'triggers': ['Huskar', 'Alchemist', 'Necrophos', 'Wraith King', 'Lifestealer', 'Oracle', 'Abaddon',
                     'Timbersaw', 'Morphling', 'Dawnbreaker', 'Omniknight', 'Dazzle', 'Treant Protector',
                     'Bristleback', 'Death Prophet'],
        'min': 1,
    },
    {
        'id': 'evasion',
        'title': 'Уклонение',
        'description': 'Враги уклоняются от атак или слепят — нужен истинный удар.',
        'core': ['Monkey King Bar', 'Bloodthorn', 'Javelin'],
        'support': ['Bloodthorn'],
        'triggers': ['Phantom Assassin', 'Windranger', 'Brewmaster', 'Tinker', 'Keeper of the Light'],
        'min': 1,
    },
    {
        'id': 'late',
        'title': 'Затяжная игра',
        'description': 'Игра уходит в поздние минуты: слоты стоит заменять на самые сильные предметы.',
        'core': ["Aghanim's Blessing", 'Moon Shard', 'Refresher Orb', 'Satanic', 'Butterfly', 'Heart of Tarrasque', 'Boots of Travel'],
        'support': ["Aghanim's Scepter", 'Refresher Orb', 'Octarine Core', "Shiva's Guard", 'Boots of Travel'],
        'triggers': [], 'min': 0,
    },
]

BY_ID = {s['id']: s for s in SITUATIONS}
