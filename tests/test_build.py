"""Тесты подбора предметов. Запуск из папки dota_2_helper: py -m unittest discover tests"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from compat import (ATTACK_ITEMS, BASIC_BOOTS, CASTER_CORE_ITEMS, CORE_ONLY, FRONTLINE_ITEMS, MELEE_ONLY, RANGED_ONLY,  # noqa: E402
                    SUPPORT_ITEMS, fits, hero_profile)  # noqa: E402
from situations import SITUATIONS  # noqa: E402

kb = main.kb


def build(hero, enemies, allies=(), situations=(), position='auto'):
    # Ollama отключаем, чтобы результат был детерминированным и не зависел от машины.
    with mock.patch.object(main, 'ollama', None):
        return main.build_recommendation(hero, list(allies), list(enemies), list(situations), position)


def names(group):
    return [it['name'] for it in group]


def all_items(data):
    result = names(data['start']) + names(data['core']) + names(data['situational'])
    for sit in data['situations']:
        result += names(sit['items'])
    return result


class PhoenixTest(unittest.TestCase):
    def test_no_skadi_against_huskar(self):
        data = build('Phoenix', ['Huskar'])
        self.assertTrue(data['ok'])
        self.assertNotIn('Eye of Skadi', names(data['situational']))

    def test_no_skadi_even_as_core(self):
        data = build('Phoenix', ['Huskar', 'Anti-Mage'], situations=['heal'], position='core')
        self.assertNotIn('Eye of Skadi', all_items(data))

    def test_no_second_boots(self):
        data = build('Phoenix', ['Huskar', 'Anti-Mage', 'Juggernaut'])
        self.assertIn('Tranquil Boots', names(data['core']))
        self.assertFalse(set(names(data['situational'])) & BASIC_BOOTS)

    def test_no_caster_core_or_starters(self):
        data = build('Phoenix', ['Huskar', 'Anti-Mage', 'Juggernaut'])
        found = names(data['situational'])
        self.assertFalse(set(found) & CASTER_CORE_ITEMS)
        self.assertFalse([x for x in found if main.is_basic(x)])

    def test_no_attack_items(self):
        data = build('Phoenix', ['Huskar', 'Phantom Assassin', 'Slark', 'Troll Warlord', 'Phantom Lancer'])
        self.assertFalse(set(names(data['situational'])) & ATTACK_ITEMS)


class JuggernautTest(unittest.TestCase):
    def test_profile(self):
        profile = hero_profile(kb.heroes['Juggernaut'])
        self.assertTrue(profile['carry'] and profile['attacker'] and profile['melee'])
        self.assertTrue(fits('Eye of Skadi', profile))

    def test_no_support_items(self):
        data = build('Juggernaut', ['Lion', 'Zeus', 'Lina', 'Skywrath Mage'], situations=['magic', 'behind'])
        self.assertFalse(set(all_items(data)) & SUPPORT_ITEMS)

    def test_no_ranged_items(self):
        data = build('Juggernaut', ['Huskar'], situations=['late', 'physical'])
        self.assertFalse(set(all_items(data)) & RANGED_ONLY)


class RubickTest(unittest.TestCase):
    def test_support_gets_no_core_items(self):
        data = build('Rubick', ['Phantom Assassin', 'Huskar', 'Slark', 'Windranger'])
        self.assertEqual(data['position'], 'support')
        found = set(names(data['situational']))
        self.assertFalse(found & ATTACK_ITEMS)
        self.assertFalse(found & CORE_ONLY)

    def test_support_pool_used(self):
        data = build('Rubick', ['Riki'], situations=['invis', 'magic'])
        self.assertTrue(any(sit['items'] for sit in data['situations']))
        self.assertFalse(set(all_items(data)) & (ATTACK_ITEMS | CORE_ONLY))


class AxeTest(unittest.TestCase):
    def test_offlaner_without_attack_items(self):
        data = build('Axe', ['Huskar', 'Phantom Assassin'])
        self.assertTrue(data['ok'])
        self.assertFalse(set(names(data['situational'])) & {'Eye of Skadi', 'Butterfly', 'Daedalus'})

    def test_melee_gets_no_ranged_items(self):
        data = build('Axe', ['Sniper', 'Drow Ranger'], situations=['physical', 'late'])
        self.assertFalse(set(all_items(data)) & RANGED_ONLY)


class TinkerTest(unittest.TestCase):
    def test_no_frontline_items(self):
        data = build('Tinker', ['Huskar', 'Phantom Assassin', 'Troll Warlord'], situations=['physical'])
        self.assertFalse(set(all_items(data)) & FRONTLINE_ITEMS)


class KnowledgeBaseTest(unittest.TestCase):
    def test_tinker_card(self):
        tinker = kb.find_hero('Tinker')
        self.assertIsNotNone(tinker)
        self.assertEqual(tinker['name'], 'Tinker')
        self.assertNotEqual(tinker['raw_text'], kb.heroes['Storm Spirit']['raw_text'])

    def test_rule_items_exist(self):
        for item in ATTACK_ITEMS | CORE_ONLY | SUPPORT_ITEMS | FRONTLINE_ITEMS | CASTER_CORE_ITEMS | BASIC_BOOTS | MELEE_ONLY | RANGED_ONLY:
            self.assertIsNotNone(main.exact_item(item), item)

    def test_situation_items_exist(self):
        for sit in SITUATIONS:
            for item in sit['core'] + sit['support']:
                self.assertIsNotNone(main.exact_item(item), f'{sit["id"]}: {item}')

    def test_situation_triggers_exist(self):
        for sit in SITUATIONS:
            for hero in sit['triggers']:
                self.assertIn(kb._normalize(hero), kb._norm_hero_map, f'{sit["id"]}: {hero}')

    def test_only_items_from_base(self):
        ids = [s['id'] for s in SITUATIONS]
        for hero in ['Phoenix', 'Juggernaut', 'Rubick', 'Axe', 'Tinker', 'Sniper', 'Crystal Maiden']:
            for position in ('auto', 'core', 'support'):
                data = build(hero, ['Huskar', 'Riki', 'Phantom Lancer', 'Lion', 'Zeus'], situations=ids, position=position)
                for item in all_items(data):
                    self.assertIn(item, kb.items, f'{hero}/{position}: {item}')


class FallbackTest(unittest.TestCase):
    def test_without_ollama(self):
        data = build('Phoenix', ['Huskar', 'Anti-Mage'], allies=['Rubick', 'Axe'])
        self.assertTrue(data['ok'])
        self.assertFalse(data['llm_used'])
        self.assertTrue(data['core'])

    def test_unknown_hero(self):
        data = build('Fooo', [])
        self.assertFalse(data['ok'])

    def test_cli_report_saved(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(main, 'ROOT', Path(tmp)), \
                mock.patch.object(main, 'ollama', None):
            text = main.run_dota_agent('Phoenix', ['Rubick'], ['Huskar'])
            saved = Path(tmp) / 'build_phoenix.txt'
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.read_text(encoding='utf-8'), text)
            bullets = [line for line in text.splitlines() if line.startswith('• ')]
            self.assertFalse([line for line in bullets if line.startswith('• Eye of Skadi')])


if __name__ == '__main__':
    unittest.main()
