from __future__ import annotations

from .models import TaskProfile


DEFAULT_PROFILE_ID = "general"


PROFILES = [
    TaskProfile(
        id="general",
        title="Общий анализ",
        description="Универсальный разбор.",
        template=(
            "Сделай прикладной аналитический ответ.\n"
            "Структура ответа: краткий вывод, ключевые наблюдения, риски, открытые вопросы, следующие шаги.\n"
            "Если данных недостаточно, явно отдели проверенные факты от предположений."
        ),
    ),
    TaskProfile(
        id="decision_brief",
        title="Записка для решения",
        description="Управленческий формат.",
        template=(
            "Подготовь записку для принятия решения.\n"
            "Структура ответа: контекст, варианты решения, плюсы и минусы, рекомендуемый вариант, условия принятия, риски.\n"
            "Пиши так, чтобы результат можно было быстро перенести в рабочий документ."
        ),
    ),
    TaskProfile(
        id="risk_review",
        title="Риск-анализ",
        description="Реестр рисков.",
        template=(
            "Проведи риск-анализ материалов.\n"
            "Структура ответа: реестр рисков таблицей, вероятность, влияние, признаки риска, меры снижения, владельцы проверки.\n"
            "Особо отмечай риски ПД, слабые источники и места, где нужна локальная проверка исходников."
        ),
    ),
    TaskProfile(
        id="memory_candidates",
        title="Кандидаты в память",
        description="Подготовка к Scribe.",
        template=(
            "Выдели кандидаты для обновления проектной памяти.\n"
            "Структура ответа: решения, правила, статусы, риски, открытые вопросы, источники для проверки.\n"
            "Не включай ПД, длинные цитаты, разовые детали и сырой текст. Отмечай, что требует проверки перед записью в память."
        ),
    ),
    TaskProfile(
        id="extract_actions",
        title="План действий",
        description="Задачи и критерии готовности.",
        template=(
            "Собери исполнимый план действий.\n"
            "Структура ответа: задачи, цель задачи, входные данные, критерий готовности, зависимости, риски, вопросы.\n"
            "Если владелец неизвестен, используй роль или область ответственности, а не выдумывай имя."
        ),
    ),
]


PROFILE_BY_ID = {profile.id: profile for profile in PROFILES}


def get_profile(profile_id: str | None) -> TaskProfile:
    if not profile_id:
        return PROFILE_BY_ID[DEFAULT_PROFILE_ID]
    return PROFILE_BY_ID.get(profile_id, PROFILE_BY_ID[DEFAULT_PROFILE_ID])


def profile_payloads() -> list[dict[str, str]]:
    return [
        {
            "id": profile.id,
            "title": profile.title,
            "description": profile.description,
        }
        for profile in PROFILES
    ]
