-- Стартовые наборы для SQLite. Сначала примените миграции Alembic.
-- Повторный импорт добавляет только недостающие строки.
PRAGMA foreign_keys = ON;

CREATE TEMPORARY TABLE starter_pack_import (
    slug VARCHAR(32) NOT NULL,
    title VARCHAR(100) NOT NULL,
    description VARCHAR(255) NOT NULL,
    category VARCHAR(64) NOT NULL,
    position INT NOT NULL
);

CREATE TEMPORARY TABLE starter_word_import (
    slug VARCHAR(32) NOT NULL,
    word VARCHAR(64) NOT NULL
);

INSERT INTO starter_pack_import (slug, title, description, category, position) VALUES
    ('animals', 'Животные', 'Дикие и домашние животные', 'Животные', 0),
    ('food', 'Еда и напитки', 'Блюда и напитки', 'Еда', 1),
    ('places', 'Города и страны', 'Города и страны мира', 'География', 2),
    ('movies', 'Фильмы и сериалы', 'Известные фильмы и сериалы', 'Кино', 3),
    ('games', 'Видеоигры', 'Популярные видеоигры', 'Игры', 4),
    ('tech', 'Технологии', 'Техника и IT', 'Технологии', 5),
    ('jobs', 'Профессии', 'Разные профессии', 'Профессии', 6),
    ('sport', 'Спорт', 'Виды спорта и атрибуты', 'Спорт', 7);

INSERT INTO starter_word_import (slug, word) VALUES
    ('animals', 'слон'),
    ('animals', 'жираф'),
    ('animals', 'пингвин'),
    ('animals', 'кенгуру'),
    ('animals', 'дельфин'),
    ('animals', 'волк'),
    ('animals', 'медведь'),
    ('animals', 'лиса'),
    ('animals', 'заяц'),
    ('animals', 'крокодил'),
    ('animals', 'попугай'),
    ('animals', 'хамелеон'),
    ('animals', 'ёж'),
    ('animals', 'белка'),
    ('animals', 'верблюд'),
    ('animals', 'тигр'),
    ('animals', 'сова'),
    ('animals', 'акула'),
    ('animals', 'осьминог'),
    ('animals', 'панда'),
    ('food', 'пицца'),
    ('food', 'борщ'),
    ('food', 'суши'),
    ('food', 'пельмени'),
    ('food', 'шоколад'),
    ('food', 'мороженое'),
    ('food', 'блины'),
    ('food', 'омлет'),
    ('food', 'лазанья'),
    ('food', 'кофе'),
    ('food', 'чай'),
    ('food', 'сыр'),
    ('food', 'арбуз'),
    ('food', 'гамбургер'),
    ('food', 'салат'),
    ('food', 'торт'),
    ('food', 'макароны'),
    ('food', 'шашлык'),
    ('food', 'компот'),
    ('food', 'хлеб'),
    ('places', 'Париж'),
    ('places', 'Токио'),
    ('places', 'Египет'),
    ('places', 'Бразилия'),
    ('places', 'Лондон'),
    ('places', 'Венеция'),
    ('places', 'Норвегия'),
    ('places', 'Мексика'),
    ('places', 'Дубай'),
    ('places', 'Рим'),
    ('places', 'Испания'),
    ('places', 'Австралия'),
    ('places', 'Стамбул'),
    ('places', 'Прага'),
    ('places', 'Канада'),
    ('places', 'Индия'),
    ('places', 'Берлин'),
    ('places', 'Нью-Йорк'),
    ('places', 'Греция'),
    ('places', 'Китай'),
    ('movies', 'Титаник'),
    ('movies', 'Матрица'),
    ('movies', 'Гарри Поттер'),
    ('movies', 'Шрек'),
    ('movies', 'Аватар'),
    ('movies', 'Джокер'),
    ('movies', 'Интерстеллар'),
    ('movies', 'Властелин колец'),
    ('movies', 'Терминатор'),
    ('movies', 'Хатико'),
    ('movies', 'Форсаж'),
    ('movies', 'Бриллиантовая рука'),
    ('movies', 'Дюна'),
    ('movies', 'Оппенгеймер'),
    ('movies', 'Бэтмен'),
    ('movies', 'Зверополис'),
    ('movies', 'Игра престолов'),
    ('movies', 'Шерлок'),
    ('movies', 'Друзья'),
    ('movies', 'Начало'),
    ('games', 'Майнкрафт'),
    ('games', 'Тетрис'),
    ('games', 'Марио'),
    ('games', 'Dota 2'),
    ('games', 'Counter-Strike'),
    ('games', 'Fortnite'),
    ('games', 'Скайрим'),
    ('games', 'Сталкер'),
    ('games', 'Пакман'),
    ('games', 'Зельда'),
    ('games', 'GTA'),
    ('games', 'Покемон'),
    ('games', 'Симс'),
    ('games', 'Ведьмак'),
    ('games', 'Overwatch'),
    ('games', 'World of Tanks'),
    ('games', 'Dark Souls'),
    ('games', 'Rust'),
    ('games', 'Among Us'),
    ('games', 'Roblox'),
    ('tech', 'компьютер'),
    ('tech', 'смартфон'),
    ('tech', 'роутер'),
    ('tech', 'дрон'),
    ('tech', 'принтер'),
    ('tech', 'клавиатура'),
    ('tech', 'наушники'),
    ('tech', 'процессор'),
    ('tech', 'браузер'),
    ('tech', 'вирус'),
    ('tech', 'сервер'),
    ('tech', 'нейросеть'),
    ('tech', 'робот'),
    ('tech', 'спутник'),
    ('tech', 'камера'),
    ('tech', 'монитор'),
    ('tech', 'планшет'),
    ('tech', 'батарея'),
    ('tech', 'антенна'),
    ('tech', 'облако'),
    ('jobs', 'врач'),
    ('jobs', 'пилот'),
    ('jobs', 'повар'),
    ('jobs', 'учитель'),
    ('jobs', 'пожарный'),
    ('jobs', 'строитель'),
    ('jobs', 'программист'),
    ('jobs', 'дизайнер'),
    ('jobs', 'водитель'),
    ('jobs', 'юрист'),
    ('jobs', 'фермер'),
    ('jobs', 'архитектор'),
    ('jobs', 'пекарь'),
    ('jobs', 'парикмахер'),
    ('jobs', 'журналист'),
    ('jobs', 'астронавт'),
    ('jobs', 'судья'),
    ('jobs', 'электрик'),
    ('jobs', 'ветеринар'),
    ('jobs', 'сантехник'),
    ('sport', 'футбол'),
    ('sport', 'хоккей'),
    ('sport', 'теннис'),
    ('sport', 'плавание'),
    ('sport', 'бокс'),
    ('sport', 'шахматы'),
    ('sport', 'баскетбол'),
    ('sport', 'волейбол'),
    ('sport', 'лыжи'),
    ('sport', 'гольф'),
    ('sport', 'сёрфинг'),
    ('sport', 'борьба'),
    ('sport', 'фехтование'),
    ('sport', 'регби'),
    ('sport', 'бадминтон'),
    ('sport', 'гимнастика'),
    ('sport', 'марафон'),
    ('sport', 'биатлон'),
    ('sport', 'скейтборд'),
    ('sport', 'боулинг');

BEGIN IMMEDIATE;

INSERT INTO pack_categories (title)
SELECT DISTINCT s.category
FROM starter_pack_import AS s
LEFT JOIN pack_categories AS c ON c.title = s.category
WHERE c.id IS NULL;

INSERT INTO question_packs (
    slug, title, description, category_id, is_public, is_hidden, position, created_at, updated_at
)
SELECT s.slug, s.title, s.description, c.id, 1, 0, s.position, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM starter_pack_import AS s
JOIN pack_categories AS c ON c.title = s.category
LEFT JOIN question_packs AS p ON p.slug = s.slug
WHERE p.id IS NULL;

INSERT INTO pack_words (pack_id, word)
SELECT p.id, s.word
FROM starter_word_import AS s
JOIN question_packs AS p ON p.slug = s.slug
LEFT JOIN pack_words AS w ON w.pack_id = p.id AND w.word = s.word
WHERE p.owner_id IS NULL AND w.id IS NULL;

COMMIT;

DROP TABLE temp.starter_word_import;
DROP TABLE temp.starter_pack_import;
