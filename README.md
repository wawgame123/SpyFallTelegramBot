# Шпион · Telegram-бот и браузерная игра

Онлайн-игра в общей комнате для браузера и Telegram и игра на одном устройстве в боте.
Сайт на React показывает активные лобби, рейтинг и наборы слов. После входа можно
играть, добавлять друзей, сохранять наборы в избранное и настраивать аватар.
Основная база — MySQL. SQLite доступна как дополнительный вариант запуска.

Готовой базы данных в репозитории нет. При запуске Alembic создаёт таблицы. Чтобы сразу получить 8 исходных наборов и 160 слов, выполните [импорт наборов](#исходные-наборы). Также можно создать свои наборы через меню бота.

## Токен и конфигурация

В Telegram откройте [@BotFather](https://t.me/BotFather), отправьте `/newbot` и сохраните выданный токен.

В PowerShell:

```powershell
git clone https://github.com/wawgame123/SpyFallTelegramBot.git
cd SpyFallTelegramBot
Copy-Item .env.example .env
notepad .env
```

В `BOT_TOKEN` вставьте токен. `ADMIN_ID` пока оставьте пустым, `ADMIN_USERNAME` можно указать для справки. `PROXY_URL` нужен, если для доступа к Telegram используется прокси. Токен и пароли храните в `.env`.

## SQLite

### Локально

В `.env` установите:

```dotenv
DATABASE_URL=sqlite:///spy_game.sqlite3
```

В PowerShell из папки проекта:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m app.main
```

Файл `spy_game.sqlite3` появится в папке проекта. Для SQLite достаточно Python 3.12, отдельный сервер базы не нужен. Остановка бота выполняется сочетанием Ctrl+C.

### Docker Compose

Запустите в PowerShell из папки проекта:

```powershell
docker compose -f compose.sqlite.yml up -d --build
docker compose -f compose.sqlite.yml logs -f bot
```

Файл базы хранится в томе `sqlite_data`. Контейнер можно остановить с сохранением данных:

```powershell
docker compose -f compose.sqlite.yml down
```

## MySQL

### Docker Compose

Установите Docker с Compose. В `.env` задайте разные длинные пароли из букв и цифр в `MYSQL_PASSWORD` и `MYSQL_ROOT_PASSWORD`. Compose сам задаст адрес MySQL для контейнера бота. Если локальная MySQL уже заняла порт 3306, укажите `MYSQL_HOST_PORT=3307`.

В PowerShell из папки проекта:

```powershell
docker compose up -d --build
docker compose logs -f bot
```

Данные хранятся в томе `mysql_data`. Чтобы остановить контейнеры с сохранением данных, выполните `docker compose down`.

### Локально

Понадобятся Python 3.12 и MySQL 8. В PowerShell откройте клиент MySQL администратором:

```powershell
mysql -u root -p
```

В клиенте MySQL создайте базу и отдельного пользователя. Замените `CHANGE_ME` своим длинным паролем из букв и цифр:

```sql
CREATE DATABASE spy_game CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'spy'@'127.0.0.1' IDENTIFIED BY 'CHANGE_ME';
GRANT ALL PRIVILEGES ON spy_game.* TO 'spy'@'127.0.0.1';
EXIT;
```

Укажите тот же пароль вместо `CHANGE_ME` в `.env`:

```dotenv
DATABASE_URL=mysql+pymysql://spy:CHANGE_ME@127.0.0.1:3306/spy_game?charset=utf8mb4
```

В PowerShell из папки проекта:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m app.main
```

## Администратор

Откройте своего бота в Telegram и отправьте `/start`, затем `/whoami`. Скопируйте показанный числовой ID в `ADMIN_ID` в файле `.env`.

В PowerShell примените новые значения:

- **Локально** — остановите бота и запустите `.\venv\Scripts\python.exe -m app.main`.
- **Docker с SQLite** — `docker compose -f compose.sqlite.yml up -d --force-recreate bot`.
- **Docker с MySQL** — `docker compose up -d --force-recreate bot`.

После `/start` появится кнопка «Админ». Для одного токена запускайте один экземпляр бота.

## Сайт на локальном компьютере

Установите Node.js 24. В PowerShell из папки проекта соберите сайт:

```powershell
npm.cmd --prefix app/views/web ci
npm.cmd --prefix app/views/web run build
```

В `.env` добавьте:

```dotenv
WEB_PUBLIC_URL=http://127.0.0.1:8080
WEB_HOST=127.0.0.1
WEB_PORT=8080
```

Запустите `python -m app.main` из виртуального окружения и откройте
[локальный сайт](http://127.0.0.1:8080/). Чтобы войти, отправьте боту
в личном чате `/web` и введите одноразовый код на сайте. Код действует
три минуты. Вход запоминается на 30 дней, включая перезапуски приложения.
Сайт и бот используют одну и ту же базу и объединяют игроков в одной комнате.

Docker собирает React автоматически. Команды Compose выше запускают бота и базу.
Для доступа к локальному сайту из браузера на Windows используйте запуск из
PowerShell по инструкции этого раздела.

Исходники разделены по MVC. Игровая логика и база находятся в `app/models`.
Обработчики Telegram и веб-API находятся в `app/controllers`. Представления
бота и компоненты сайта находятся в `app/views`.

## Исходные наборы

Импорт добавляет только недостающие категории, наборы и слова. Файлы SQL содержат встроенные наборы без учётных записей, истории игр и пользовательских наборов.

### SQLite

Используйте [starter_packs.sqlite.sql](starter_packs.sqlite.sql). Для локального запуска остановите работающего бота и выполните в PowerShell из папки проекта:

```powershell
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe -c "import sqlite3; from pathlib import Path; db = sqlite3.connect('spy_game.sqlite3'); db.executescript(Path('starter_packs.sqlite.sql').read_text(encoding='utf-8')); db.close()"
.\venv\Scripts\python.exe -m app.main
```

Если вы изменили имя базы в `DATABASE_URL`, укажите тот же файл в `sqlite3.connect(...)`.

Для Docker в PowerShell:

```powershell
docker compose -f compose.sqlite.yml build bot
docker compose -f compose.sqlite.yml stop bot
docker compose -f compose.sqlite.yml run --rm bot python -m alembic upgrade head
docker compose -f compose.sqlite.yml run --rm bot python -c "import sqlite3; from pathlib import Path; db = sqlite3.connect('/data/spy_game.sqlite3'); db.executescript(Path('starter_packs.sqlite.sql').read_text(encoding='utf-8')); db.close()"
docker compose -f compose.sqlite.yml up -d bot
```

### MySQL

Используйте [starter_packs.sql](starter_packs.sql). Для локальной базы выполните в PowerShell из папки проекта:

```powershell
.\venv\Scripts\python.exe -m alembic upgrade head
mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u spy -p spy_game --execute="SOURCE starter_packs.sql"
```

Если бот уже запущен локально, перезапустите его, чтобы загрузить наборы.

Для Docker в PowerShell:

```powershell
docker compose up -d --wait db
docker compose run --rm --no-deps --build bot python -m alembic upgrade head
docker compose cp .\starter_packs.sql db:/tmp/starter_packs.sql
docker compose exec db mysql --default-character-set=utf8mb4 -u spy -p spy_game --execute="SOURCE /tmp/starter_packs.sql"
docker compose up -d --force-recreate bot
```

На запрос пароля MySQL введите значение `MYSQL_PASSWORD` из `.env`.

## Тесты

В папке [tests](tests/) находятся тесты игровых правил, комнат, рейтинга,
истории, наборов слов, миграций и обработчиков бота. Тесты используют
временную SQLite и имитацию Telegram. Для них не нужен реальный токен,
доступ к Telegram или запущенная MySQL.

В PowerShell из папки проекта после создания окружения `venv`:

```powershell
npm.cmd --prefix app/views/web ci
npm.cmd --prefix app/views/web run build
.\venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\venv\Scripts\python.exe -m pytest -q
```

[GitHub Actions](.github/workflows/tests.yml) запускает тесты на Python 3.12
и 3.14 при каждом пуше и открытии или обновлении pull request. Там же
собирается Docker-образ и проверяется запуск бота и сайта с MySQL и SQLite.
Проверка использует имитацию Telegram и импортирует исходные наборы.
