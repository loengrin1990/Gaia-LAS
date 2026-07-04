# GitHub Setup

Эта инструкция описывает безопасный путь публикации Gaia в private GitHub repository.

## Что нужно сделать владельцу

1. Создать аккаунт на https://github.com, если его еще нет.
2. Включить двухфакторную аутентификацию: Settings -> Password and authentication -> Two-factor authentication.
3. Создать новый репозиторий:
   - Owner: твой аккаунт.
   - Repository name: `gaia-local-analytics` или другое короткое имя.
   - Visibility: `Private`.
   - Не добавлять README, .gitignore или license на сайте, потому что они уже есть локально.
4. Скопировать SSH или HTTPS URL репозитория.

## Что можно будет сделать локально после проверки

```bash
cd Local_Analytics_System
git init
git add .gitignore LICENSE GITHUB_SETUP.md README.md REVIEW_GUIDE.md pyproject.toml requirements.txt config.example.json config.review.example.json app.py gaia tests docs
git commit -m "Initial Gaia local analytics package"
git branch -M main
git remote add origin <REPOSITORY_URL>
git push -u origin main
```

## Что нельзя добавлять в git

- `config.json` - локальный рабочий конфиг.
- `runs/` - загруженные файлы, журналы и результаты запусков.
- `outputs/` - презентации и производные материалы.
- backup-папки, архивы, временные файлы.
- Obsidian vault, проектная память, исходники клиентов, masked/review artifacts.

## Проверка перед первым push

```bash
git status --short
git diff --cached --stat
git diff --cached --name-only
python3 -B -m unittest discover -s tests
python3 -B -m gaia.config
```

Перед push нужно глазами проверить `git diff --cached --name-only`: в списке не должно быть `runs/`, `outputs/`, `config.json`, рабочих PDF/DOCX/XLSX или материалов Obsidian.
