import difflib
import json
import os
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple


# Словарь канонических исправлений частых опечаток в файлах героев
HERO_ALIASES = {
    "nyx assasin": "Nyx Assassin",
    "clockwork": "Clockwerk",
    "ancient apparation": "Ancient Apparition",
    "anti mage": "Anti-Mage",
    "antimage": "Anti-Mage",
    "алхимик": "Alchemist",
    "абаддон": "Abaddon",
    "антимаг": "Anti-Mage",
    "арк варден": "Arc Warden",
    "аппарат": "Ancient Apparition",
}


def _clean_item_list(raw_text: str) -> List[str]:
    """Очищает строку предметов от пояснений (как в Alchemist.txt) и возвращает чистый список названий."""
    if not raw_text:
        return []
    items = []
    # Сначала делим по точке с запятой (если есть блоки с пояснениями через тире)
    for block in raw_text.split(";"):
        # Отрезаем пояснение после тире " — " или " - "
        item_part = re.split(r"\s+[—–-]\s+", block.strip())[0]
        for item in item_part.split(","):
            clean_name = item.strip().rstrip(".")
            if clean_name:
                items.append(clean_name)
    return items


def _clean_hero_list(raw_text: str) -> List[str]:
    """Разбирает строку с перечислением героев через запятую и исправляет известные опечатки."""
    if not raw_text:
        return []
    heroes = []
    for h in raw_text.split(","):
        # Убираем скобки и лишние пробелы
        name = re.sub(r"\(.*?\)", "", h).strip().rstrip(".")
        if not name:
            continue
        alias_match = HERO_ALIASES.get(name.lower())
        heroes.append(alias_match if alias_match else name)
    return heroes


def parse_hero_file(filepath: str) -> Dict:
    """Парсит текстовую карточку героя в структурированный словарь (узел графа/базы знаний)."""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    data = {
        "file_path": filepath,
        "raw_text": text.strip(),
        "name": os.path.splitext(os.path.basename(filepath))[0],
        "description": "",
        "role": "",
        "attack_type": "",
        "primary_attr": "",
        "weak_against": [],
        "strong_against": [],
        "core_items": [],
        "counter_items": [],  # Предметы, которые контрят ЭТОГО героя
    }

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("Описание:"):
            data["description"] = line.split("Описание:", 1)[1].strip()
        elif line.startswith("Герой:"):
            raw_hero = line.split("Герой:", 1)[1].strip()
            # Если формат "Алхимик (Alchemist)", берем английское имя из скобок
            m = re.search(r"\(([^)]+)\)", raw_hero)
            hero_name = m.group(1).strip() if m else raw_hero.split("/")[0].strip()
            data["name"] = HERO_ALIASES.get(hero_name.lower(), hero_name)
        elif line.startswith("Роль:"):
            data["role"] = line.split("Роль:", 1)[1].strip()
        elif line.startswith("Тип атаки:"):
            data["attack_type"] = line.split("Тип атаки:", 1)[1].strip()
        elif line.startswith("Основной атрибут:"):
            data["primary_attr"] = line.split("Основной атрибут:", 1)[1].strip()
        elif line.startswith("Слаб против:"):
            data["weak_against"] = _clean_hero_list(line.split("Слаб против:", 1)[1])
        elif line.startswith("Силён против:") or line.startswith("Силен против:"):
            val = re.split(r"Сил[её]н против:", line, maxsplit=1)[1]
            data["strong_against"] = _clean_hero_list(val)
        elif line.startswith("Основной закуп:"):
            data["core_items"] = _clean_item_list(line.split("Основной закуп:", 1)[1])
        elif line.startswith("Контр-предметы:") or line.startswith("Предметы против"):
            val = line.split(":", 1)[1]
            data["counter_items"] = _clean_item_list(val)

    return data


def parse_item_file(filepath: str) -> Dict:
    """Парсит текстовую карточку предмета по утвержденному шаблону."""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    item_name = os.path.splitext(os.path.basename(filepath))[0]
    cost = ""
    item_type = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Предмет:"):
            raw_title = line.split("Предмет:", 1)[1].strip()
            item_name = raw_title.split("/")[0].strip()
        elif line.startswith("Стоимость:"):
            cost = line.split("Стоимость:", 1)[1].strip()
        elif line.startswith("Тип:"):
            item_type = line.split("Тип:", 1)[1].strip()

    return {
        "name": item_name,
        "cost": cost,
        "type": item_type,
        "raw_text": text.strip(),
    }


class DotaKnowledgeBase:
    """Модуль 1 и Модуль 2: Индексация карточек героев и предметов (Графовый + Поисковый индекс)."""

    def __init__(self, data_dir: str = "data", items_dir: str = "data/items"):
        self.data_dir = data_dir
        self.items_dir = items_dir
        self.heroes: Dict[str, Dict] = {}
        self.items: Dict[str, Dict] = {}
        self._norm_hero_map: Dict[str, str] = {}
        self._norm_item_map: Dict[str, str] = {}
        self.reload()

    @staticmethod
    def _normalize(name: str) -> str:
        return re.sub(r"[^a-zа-я0-9]", "", name.lower())

    def reload(self) -> None:
        """Индексирует все .txt файлы героев и предметов в память и строит граф связей."""
        self.heroes.clear()
        self.items.clear()
        self._norm_hero_map.clear()
        self._norm_item_map.clear()

        if os.path.isdir(self.data_dir):
            for fname in sorted(os.listdir(self.data_dir)):
                if not fname.endswith(".txt"):
                    continue
                fpath = os.path.join(self.data_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                # Пропускаем файлы предметов, если они лежат в общей папке (определяются по заголовку "Предмет:")
                with open(fpath, "r", encoding="utf-8") as f:
                    first_lines = f.read(200)
                if "Предмет:" in first_lines and "Стоимость:" in first_lines:
                    item_card = parse_item_file(fpath)
                    self.items[item_card["name"]] = item_card
                    self._norm_item_map[self._normalize(item_card["name"])] = item_card["name"]
                    self._norm_item_map[self._normalize( os.path.splitext(fname)[0] )] = item_card["name"]
                else:
                    hero_card = parse_hero_file(fpath)
                    canonical = hero_card["name"]
                    self.heroes[canonical] = hero_card
                    self._norm_hero_map[self._normalize(canonical)] = canonical
                    self._norm_hero_map[self._normalize(os.path.splitext(fname)[0])] = canonical

        if os.path.isdir(self.items_dir):
            for fname in sorted(os.listdir(self.items_dir)):
                if not fname.endswith(".txt"):
                    continue
                fpath = os.path.join(self.items_dir, fname)
                if os.path.isfile(fpath):
                    item_card = parse_item_file(fpath)
                    self.items[item_card["name"]] = item_card
                    self._norm_item_map[self._normalize(item_card["name"])] = item_card["name"]
                    self._norm_item_map[self._normalize(os.path.splitext(fname)[0])] = item_card["name"]

    def find_hero(self, query_name: str) -> Optional[Dict]:
        """Находит карточку героя даже при опечатках, разном регистре или отсутствии дефиса (Anti Mage -> Anti-Mage)."""
        if not query_name:
            return None
        q_clean = query_name.strip()
        if q_clean.lower() in HERO_ALIASES:
            q_clean = HERO_ALIASES[q_clean.lower()]

        norm_q = self._normalize(q_clean)
        if norm_q in self._norm_hero_map:
            return self.heroes[self._norm_hero_map[norm_q]]

        # Нечеткий поиск (защита от опечаток пользователя при вводе в консоль)
        matches = difflib.get_close_matches(norm_q, list(self._norm_hero_map.keys()), n=1, cutoff=0.75)
        if matches:
            return self.heroes[self._norm_hero_map[matches[0]]]
        return None

    def find_item(self, item_name: str) -> Optional[Dict]:
        """Находит карточку предмета по названию."""
        if not item_name:
            return None
        norm_i = self._normalize(item_name)
        if norm_i in self._norm_item_map:
            return self.items[self._norm_item_map[norm_i]]
        matches = difflib.get_close_matches(norm_i, list(self._norm_item_map.keys()), n=1, cutoff=0.8)
        if matches:
            return self.items[self._norm_item_map[matches[0]]]
        return None


def analyze_draft(
    allies: List[str],
    enemies: List[str],
    my_hero: str = "",
    data_dir: str = "data",
) -> str:
    """Анализирует драфт по реальным файлам 128 героев из папки data/ (граф контрпиков и контр-предметов)."""
    kb = DotaKnowledgeBase(data_dir=data_dir)
    my_card = kb.find_hero(my_hero) if my_hero else None

    resolved_allies = []
    ally_cards = []
    for a in allies:
        card = kb.find_hero(a)
        if card:
            resolved_allies.append(card["name"])
            ally_cards.append(card)
        elif a.strip():
            resolved_allies.append(a.strip())

    resolved_enemies = []
    enemy_cards = []
    for e in enemies:
        card = kb.find_hero(e)
        if card:
            resolved_enemies.append(card["name"])
            enemy_cards.append(card)
        elif e.strip():
            resolved_enemies.append(e.strip())

    direct_counters_to_me = []
    good_targets_for_me = []
    enemy_counter_items_map: Dict[str, List[str]] = {}
    item_priority_counter: Counter = Counter()

    my_canonical = my_card["name"] if my_card else my_hero.strip()
    my_weak_norm = {kb._normalize(h) for h in (my_card["weak_against"] if my_card else [])}
    my_strong_norm = {kb._normalize(h) for h in (my_card["strong_against"] if my_card else [])}

    for e_card in enemy_cards:
        e_name = e_card["name"]
        e_norm = kb._normalize(e_name)
        e_strong_norm = {kb._normalize(h) for h in e_card["strong_against"]}
        e_weak_norm = {kb._normalize(h) for h in e_card["weak_against"]}

        # Проверяем двустороннюю связь в графе: враг в моем "Слаб против" ИЛИ я в его "Силён против"
        if e_norm in my_weak_norm or (my_canonical and kb._normalize(my_canonical) in e_strong_norm):
            direct_counters_to_me.append(e_name)

        # Проверяем, кого контрит наш герой: враг в моем "Силён против" ИЛИ я в его "Слаб против"
        if e_norm in my_strong_norm or (my_canonical and kb._normalize(my_canonical) in e_weak_norm):
            good_targets_for_me.append(e_name)

        if e_card["counter_items"]:
            enemy_counter_items_map[e_name] = e_card["counter_items"]
            for item in e_card["counter_items"]:
                item_priority_counter[item] += 1

    # Командные предметы союзников (чтобы не дублировать ауры)
    aura_keywords = {"Mekansm", "Pipe of Insight", "Guardian Greaves", "Solar Crest", "Assault Cuirass", "Shiva's Guard", "Vladmir's Offering"}
    ally_aura_hints = []
    for a_card in ally_cards:
        shared = [it for it in a_card["core_items"] if it in aura_keywords]
        if shared:
            ally_aura_hints.append(f"{a_card['name']} (Роль: {a_card['role']}) обычно собирает: {', '.join(shared)}")

    lines = [
        "--- ТОЧНЫЙ АНАЛИЗ ДРАФТА ПО БАЗЕ ЗНАНИЙ ---",
        f"• Ваш герой: {my_canonical if my_canonical else 'Не указан'}",
        f"• Союзники: {', '.join(resolved_allies) if resolved_allies else 'Не указаны'}",
        f"• Враги: {', '.join(resolved_enemies) if resolved_enemies else 'Не указаны'}",
        "",
    ]

    # Блок 1: Личный матчап вашего героя против врагов
    lines.append("1. ОЦЕНКА МАТЧАПА ДЛЯ ВАШЕГО ГЕРОЯ:")
    if direct_counters_to_me:
        lines.append(
            f"- ОПАСНЫЕ КОНТРПИКИ ПРОТИВ ВАС: {', '.join(direct_counters_to_me)} "
            f"(против них обязательно нужны защитные слоты или позиционка)."
        )
    else:
        lines.append("- Прямых жестких контрпиков вашему герою среди известных карточек врагов не обнаружено.")

    if good_targets_for_me:
        lines.append(f"- УДОБНЫЕ ЦЕЛИ ДЛЯ ВАС (вы их контрите): {', '.join(good_targets_for_me)}.")
    lines.append("")

    # Блок 2: Разбор каждого врага из его файла
    lines.append("2. УГРОЗЫ ВРАЖЕСКИХ ГЕРОЕВ И ЧЕМ ИХ КОНТРИТЬ (ИЗ БАЗЫ):")
    if enemy_cards:
        for e_card in enemy_cards:
            c_items = ", ".join(e_card["counter_items"]) if e_card["counter_items"] else "по ситуации"
            lines.append(
                f"- {e_card['name']} ({e_card['primary_attr']}, {e_card['attack_type']}): {e_card['description']} "
                f"| Предметы ПРОТИВ {e_card['name']}: {c_items}."
            )
    else:
        lines.append("- Файлы вражеских героев не найдены в папке data/.")
    lines.append("")

    # Блок 3: Рейтинг самых эффективных контр-предметов на этот матч
    lines.append("3. ПРИОРИТЕТНЫЕ КОНТР-ПРЕДМЕТЫ НА ЭТОТ МАТЧ (ПО ЧАСТОТЕ ПОЛЕЗНОСТИ):")
    if item_priority_counter:
        for item_name, count in item_priority_counter.most_common(8):
            targets = [e_name for e_name, items in enemy_counter_items_map.items() if item_name in items]
            lines.append(f"- {item_name} — закрывает сразу {count} враг(ов): {', '.join(targets)}")
    else:
        lines.append("- Нет данных о специфических контр-предметах.")
    lines.append("")

    # Блок 4: Синергия с союзниками
    if ally_aura_hints:
        lines.append("4. УЧЕТ ЗАКУПА СОЮЗНИКОВ (ЧТОБЫ НЕ ДУБЛИРОВАТЬ АУРЫ):")
        for hint in ally_aura_hints:
            lines.append(f"- {hint}")

    return "\n".join(lines)