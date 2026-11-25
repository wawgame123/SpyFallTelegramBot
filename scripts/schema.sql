-- Схема базы данных бота «Шпион» для MySQL 8.
-- Файл только для справки: бот сам создаёт и обновляет таблицы при запуске (Alembic).
-- Создан скриптом scripts/export_schema.py из app/models/database/models.py.

CREATE TABLE avatar_appearances (
	user_id BIGINT NOT NULL,
	hat VARCHAR(24),
	face VARCHAR(24),
	placement JSON,
	outfit VARCHAR(24),
	PRIMARY KEY (user_id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE game_sessions (
	id INTEGER NOT NULL AUTO_INCREMENT,
	room_id INTEGER,
	room_code VARCHAR(8) NOT NULL,
	title VARCHAR(100) NOT NULL,
	host_id BIGINT NOT NULL,
	status VARCHAR(16) NOT NULL,
	is_test BOOL NOT NULL DEFAULT '0',
	started_at DATETIME NOT NULL,
	ended_at DATETIME,
	PRIMARY KEY (id)
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_game_sessions_room_id ON game_sessions (room_id);
CREATE INDEX ix_game_sessions_started_at ON game_sessions (started_at);
CREATE INDEX ix_game_sessions_status ON game_sessions (status);

CREATE TABLE local_games (
	owner_id BIGINT NOT NULL,
	state LONGTEXT NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (owner_id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE pack_categories (
	id INTEGER NOT NULL AUTO_INCREMENT,
	title VARCHAR(64) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (title)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE rooms (
	id INTEGER NOT NULL AUTO_INCREMENT,
	code VARCHAR(8) NOT NULL,
	invite_token VARCHAR(32) NOT NULL,
	host_id BIGINT NOT NULL,
	state LONGTEXT NOT NULL,
	deadline DOUBLE,
	last_activity_at DATETIME NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
) DEFAULT CHARSET=utf8mb4;
CREATE UNIQUE INDEX ix_rooms_code ON rooms (code);
CREATE INDEX ix_rooms_deadline ON rooms (deadline);
CREATE UNIQUE INDEX ix_rooms_invite_token ON rooms (invite_token);
CREATE INDEX ix_rooms_last_activity_at ON rooms (last_activity_at);
CREATE INDEX ix_rooms_updated_at ON rooms (updated_at);

CREATE TABLE user_penalties (
	user_id BIGINT NOT NULL,
	strikes INTEGER NOT NULL,
	banned_until DOUBLE NOT NULL,
	last_leave_at DOUBLE NOT NULL,
	reason VARCHAR(16) NOT NULL DEFAULT 'leave',
	early_guess_warnings JSON,
	PRIMARY KEY (user_id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE user_settings (
	user_id BIGINT NOT NULL,
	tz_offset_min INTEGER NOT NULL,
	emoji BOOL NOT NULL DEFAULT '0',
	colors BOOL NOT NULL DEFAULT '0',
	PRIMARY KEY (user_id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE web_friends (
	user_id BIGINT NOT NULL,
	friend_id BIGINT NOT NULL,
	name VARCHAR(80) NOT NULL,
	PRIMARY KEY (user_id, friend_id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE web_sessions (
	digest VARCHAR(64) NOT NULL,
	scope VARCHAR(64) NOT NULL,
	user_id BIGINT NOT NULL,
	name VARCHAR(80) NOT NULL,
	expires_at BIGINT NOT NULL,
	PRIMARY KEY (digest)
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_web_sessions_expires_at ON web_sessions (expires_at);
CREATE INDEX ix_web_sessions_scope ON web_sessions (scope);

CREATE TABLE match_rounds (
	id INTEGER NOT NULL AUTO_INCREMENT,
	session_id INTEGER NOT NULL,
	round_no INTEGER NOT NULL,
	theme VARCHAR(100) NOT NULL,
	word VARCHAR(64) NOT NULL,
	pack_id INTEGER,
	pack_title VARCHAR(100),
	spy_user_id BIGINT NOT NULL,
	spy_name VARCHAR(40) NOT NULL,
	outcome VARCHAR(24) NOT NULL,
	finished_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_session_round UNIQUE (session_id, round_no),
	FOREIGN KEY(session_id) REFERENCES game_sessions (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_match_rounds_finished_at ON match_rounds (finished_at);
CREATE INDEX ix_match_rounds_session_id ON match_rounds (session_id);

CREATE TABLE question_packs (
	id INTEGER NOT NULL AUTO_INCREMENT,
	slug VARCHAR(32) NOT NULL,
	title VARCHAR(100) NOT NULL,
	description VARCHAR(255) NOT NULL,
	category_id INTEGER NOT NULL,
	owner_id BIGINT,
	owner_name VARCHAR(40),
	is_public BOOL NOT NULL,
	is_hidden BOOL NOT NULL DEFAULT '0',
	position INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(category_id) REFERENCES pack_categories (id)
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_question_packs_category_id ON question_packs (category_id);
CREATE INDEX ix_question_packs_owner_id ON question_packs (owner_id);
CREATE UNIQUE INDEX ix_question_packs_slug ON question_packs (slug);

CREATE TABLE room_members (
	user_id BIGINT NOT NULL,
	room_id INTEGER NOT NULL,
	joined_at DATETIME NOT NULL,
	PRIMARY KEY (user_id),
	FOREIGN KEY(room_id) REFERENCES rooms (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_room_members_room_id ON room_members (room_id);

CREATE TABLE session_players (
	session_id INTEGER NOT NULL,
	user_id BIGINT NOT NULL,
	name VARCHAR(40) NOT NULL,
	score INTEGER NOT NULL,
	PRIMARY KEY (session_id, user_id),
	FOREIGN KEY(session_id) REFERENCES game_sessions (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_session_players_user_id ON session_players (user_id);

CREATE TABLE pack_reports (
	id INTEGER NOT NULL AUTO_INCREMENT,
	pack_id INTEGER NOT NULL,
	reporter_id BIGINT NOT NULL,
	reason VARCHAR(40) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_pack_reporter UNIQUE (pack_id, reporter_id),
	FOREIGN KEY(pack_id) REFERENCES question_packs (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_pack_reports_pack_id ON pack_reports (pack_id);

CREATE TABLE pack_words (
	id INTEGER NOT NULL AUTO_INCREMENT,
	pack_id INTEGER NOT NULL,
	word VARCHAR(64) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_pack_word UNIQUE (pack_id, word),
	FOREIGN KEY(pack_id) REFERENCES question_packs (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_pack_words_pack_id ON pack_words (pack_id);

CREATE TABLE round_players (
	round_id INTEGER NOT NULL,
	user_id BIGINT NOT NULL,
	name VARCHAR(40) NOT NULL,
	`role` VARCHAR(8) NOT NULL,
	result VARCHAR(8) NOT NULL,
	score_delta INTEGER NOT NULL,
	PRIMARY KEY (round_id, user_id),
	FOREIGN KEY(round_id) REFERENCES match_rounds (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;
CREATE INDEX ix_round_players_user_id ON round_players (user_id);

CREATE TABLE user_favorites (
	user_id BIGINT NOT NULL,
	pack_id INTEGER NOT NULL,
	PRIMARY KEY (user_id, pack_id),
	FOREIGN KEY(pack_id) REFERENCES question_packs (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;
