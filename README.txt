Шпион · Telegram-бот
====================

Онлайн-игра в комнате и игра на одном устройстве. Бот хранит
наборы слов, результаты и настройки в MySQL.

Готовой базы данных в репозитории нет. MySQL создаёт пустую базу,
а Alembic создаёт и обновляет таблицы. В отдельном starter_packs.sql
есть 8 исходных наборов и 160 слов. Их можно загрузить по желанию
или создать свои наборы в меню бота.

1. Получите токен и скачайте проект

В Telegram откройте @BotFather (https://t.me/BotFather), отправьте
/newbot и выполните подсказки. Сохраните выданный токен для файла .env.

В PowerShell:

    git clone https://github.com/wawgame123/SpyFallTelegramBot.git
    cd SpyFallTelegramBot
    Copy-Item .env.example .env
    notepad .env

В BOT_TOKEN вставьте выданный токен. ADMIN_ID пока оставьте пустым.
ADMIN_USERNAME можно указать для справки. Права администратора
бот проверяет по ADMIN_ID. PROXY_URL оставьте пустым, если прокси
для доступа к Telegram не нужен. Токен и пароли храните в .env.

2. Запуск через Docker Compose

Установите Docker с Compose. В .env задайте разные длинные пароли
из букв и цифр в MYSQL_PASSWORD и MYSQL_ROOT_PASSWORD. Первый пароль
также используется в адресе подключения контейнера бота.
DATABASE_URL в .env при этом можно оставить как есть. Если локальная
MySQL уже заняла порт 3306, укажите MYSQL_HOST_PORT=3307.

В PowerShell из папки проекта:

    docker compose up -d --build
    docker compose logs -f bot

После запуска бота таблицы создаются автоматически. MySQL хранит
данные в томе mysql_data. Остановить контейнеры с сохранением данных:

    docker compose down

3. Запуск без Docker

Понадобятся Python 3.12 и MySQL 8. Запустите MySQL. В PowerShell
откройте клиент MySQL администратором:

    mysql -u root -p

В клиенте MySQL создайте базу и отдельного пользователя. Замените
CHANGE_ME своим длинным паролем из букв и цифр:

    CREATE DATABASE spy_game CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    CREATE USER 'spy'@'127.0.0.1' IDENTIFIED BY 'CHANGE_ME';
    GRANT ALL PRIVILEGES ON spy_game.* TO 'spy'@'127.0.0.1';
    EXIT;

Укажите тот же пароль вместо CHANGE_ME в DATABASE_URL в .env:

    DATABASE_URL=mysql+pymysql://spy:CHANGE_ME@127.0.0.1:3306/spy_game?charset=utf8mb4

Создайте окружение и запустите бота в PowerShell из папки проекта:

    py -3.12 -m venv venv
    .\venv\Scripts\python.exe -m pip install -r requirements.txt
    .\venv\Scripts\python.exe -m app.main

Остановка локального бота выполняется сочетанием Ctrl+C.
Для одного BOT_TOKEN запускайте один экземпляр бота.

4. Включение администратора

Откройте своего бота в Telegram и отправьте /start, затем /whoami.
Скопируйте показанный числовой ID в ADMIN_ID в файле .env.

Для Docker примените новые значения из .env в PowerShell:

    docker compose up -d --force-recreate bot

Для локального запуска остановите бота и повторите команду
.\venv\Scripts\python.exe -m app.main в PowerShell.
Затем отправьте /start. В меню появится кнопка «Админ».

5. Импорт исходных наборов (по желанию)

Выполняйте импорт после создания таблиц. Если бот ещё ни разу
не запускался, сначала примените миграции.

Для Docker в PowerShell из папки проекта:

    docker compose up -d --wait db
    docker compose run --rm --no-deps --build bot python -m alembic upgrade head
    docker compose cp .\starter_packs.sql db:/tmp/starter_packs.sql
    docker compose exec db mysql --default-character-set=utf8mb4 -u spy -p spy_game --execute="SOURCE /tmp/starter_packs.sql"
    docker compose up -d --force-recreate bot

На запрос пароля MySQL введите значение MYSQL_PASSWORD из .env.

Для локальной MySQL в PowerShell из папки проекта:

    .\venv\Scripts\python.exe -m alembic upgrade head
    mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u spy -p spy_game --execute="SOURCE starter_packs.sql"

Если локальный бот уже работает, остановите и запустите его снова,
чтобы загрузить наборы. Повторный импорт добавляет только недостающие
категории, наборы и слова. В файле нет учётных записей, истории игр
и пользовательских наборов.
