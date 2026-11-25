import {useEffect, useMemo, useRef, useState} from "react";
import {api} from "./client";
import {GameControls, GuessControls, SettingsControls, ThemeControls, TurnControls} from "./game-actions";
import {Action, Avatar, Contact, useSite, useTask} from "./ui";

const phases = {
  lobby: "Лобби", dealing: "Выдача ролей", discussion: "Обсуждение",
  suspect_vote: "Голосование", runoff_vote: "Переголосование", last_chance: "Последний шанс",
  result: "Итоги раунда", final: "Игра завершена",
  practice_vote: "Голосование за пробный раунд", practice_result: "Итоги пробного раунда",
};

function Timer({deadline, offset}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!deadline) return;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [deadline]);
  if (!deadline) return null;
  const seconds = Math.max(0, Math.ceil(deadline - (now + offset) / 1000));
  const label = [Math.floor(seconds / 60), seconds % 60].map((value) => String(value).padStart(2, "0")).join(":");
  return <span id="timer" className={"timer" + (seconds <= 10 ? " timer-urgent" : "")} aria-label="Время до конца хода">{label}</span>;
}

function textNodes(container) {
  return Array.from(container.childNodes).map((child, index) => {
    if (child.nodeType === Node.TEXT_NODE) return child.textContent;
    if (child.nodeType !== Node.ELEMENT_NODE) return null;
    if (child.tagName === "B") return <b key={index}>{textNodes(child)}</b>;
    if (child.tagName === "CODE") return <code key={index}>{textNodes(child)}</code>;
    return <span key={index}>{textNodes(child)}</span>;
  });
}

function RoomText({text, skipHeading}) {
  const blocks = useMemo(() => {
    const parts = (text || "").replace(/ · ⏳ Осталось около \d+ с|Осталось около \d+ с\. /g, "").split(/\n{2,}/);
    if (skipHeading && parts.length > 1) parts.shift();
    const parser = new DOMParser();
    // Template contents remain inert. Only text and explicitly rendered formatting become React nodes.
    const template = parser.parseFromString("<template></template>", "text/html").querySelector("template");
    return parts.filter(Boolean).map((part) => {
      template.innerHTML = part;
      return textNodes(template.content);
    });
  }, [text, skipHeading]);
  return blocks.map((part, index) => <div className="room-copy" key={index}>{part}</div>);
}

function LobbyBoard({lobby, code}) {
  const ready = lobby.players.filter((member) => member.ready).length;
  const facts = [
    ["Код комнаты", code], ["Мест", lobby.players.length + " / " + lobby.capacity],
    ["Режим", lobby.mode], ["Раунды", lobby.played ? lobby.played + " / " + lobby.rounds : lobby.rounds],
    ["Время хода", lobby.turn + " с"], ["Темы", lobby.themes],
    ...(lobby.test_role ? [["Тестовая роль хоста", lobby.test_role]] : []),
  ];
  return <div className="lobby-desk">
    <dl className="lobby-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <div className="roster"><div className="roster-heading"><h2>Игроки</h2>
      <span className="roster-count">{ready} из {lobby.players.length} готовы</span></div>
      <ul className="roster-seats">{lobby.players.map((member, index) => <li key={member.friend_ref || index} className="roster-seat">
        <Avatar name={member.name} image={member.image} cosmetics={member.cosmetics}/>
        <div className="seat-details"><Contact name={member.name} reference={member.friend_ref}/>
          <span className="seat-status" data-ready={member.ready || undefined}>{member.host ? "Хост" : member.ready ? "Готов" : "Ожидает"}</span>
        </div>
        {!!lobby.played && <span className="seat-score">{member.score} оч.</span>}
      </li>)}</ul>
    </div>
    {lobby.themes_notice && <p className="field-hint">Некоторые наборы больше недоступны. Выберите замену в настройках.</p>}
  </div>;
}

function TurnBoard({turn, canChat}) {
  return <div className="turn-card turn-arrival">
    <div className="turn-context"><span>{turn.practice ? "Пробный раунд" : "Раунд " + turn.round + " из " + turn.rounds}</span>
      <span>{turn.theme}</span><span>Круг {turn.circle}</span></div>
    <h2>{canChat ? "Ваш ход" : "Ход игрока " + turn.speaker}</h2>
    {turn.next && <p className="turn-next">Далее — {turn.next}</p>}
    {turn.order.length > 1 && <ol className="turn-order" aria-label="Очередь игроков">{turn.order.map((seat, index) =>
      <li key={index} aria-current={seat.speaking ? "step" : undefined}>{seat.name}</li>)}</ol>}
  </div>;
}

function Chat({room}) {
  const {refreshRoom, notice} = useSite();
  const [busy, run] = useTask();
  const log = useRef(null);
  const lobby = Boolean(room.lobby);
  const activity = (room.lobby || room.discussion).activity;
  const entries = JSON.stringify(activity);
  const label = lobby ? "Сообщение" : "Подсказка";
  useEffect(() => { log.current.scrollTop = log.current.scrollHeight; }, [entries]);

  function submit(event) {
    event.preventDefault();
    const field = event.currentTarget.elements.namedItem("text");
    const text = field.value;
    if (!text.trim()) return;
    run(async () => {
      await api("/api/room/chat", {room: room.id, text, phase: lobby ? "lobby" : "discussion"});
      if (field.value === text) field.value = "";
      notice("");
      await refreshRoom();
    });
  }

  function sendOnEnter(event) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!busy && !event.repeat) event.currentTarget.form.requestSubmit();
  }

  return <section className="room-chat" aria-labelledby="chat-title">
    <h2 id="chat-title">{lobby ? "Чат лобби" : "Чат комнаты"}</h2>
    {!activity.length && <p className="field-hint">Сообщений пока нет.</p>}
    <ol id="chat-log" ref={log} aria-live="polite">{activity.map(([author, text, image, cosmetics], index) =>
      author ? <li key={JSON.stringify([author, text, image, index])} className="chat-message chat-arrival">
        <Avatar name={author} image={image} cosmetics={cosmetics}/>
        <div><strong>{author}</strong><p>{text}</p></div>
      </li> : <li key={index} className="chat-event"><span aria-hidden="true">•</span><p>{text}</p></li>,
    )}</ol>
    {room.can_chat ? <form id="chat-form" onSubmit={submit}>
      <label className="sr-only" htmlFor="clue">{label}</label>
      <textarea id="clue" name="text" rows={2} maxLength={200} required onKeyDown={sendOnEnter}
        placeholder={lobby ? "Напишите сообщение" : "Напишите подсказку"} aria-describedby="chat-shortcuts"/>
      <button className="primary" type="submit" disabled={busy}>Отправить</button>
      <small id="chat-shortcuts" className="field-hint">Enter — отправить · Shift+Enter — новая строка</small>
    </form> : <p id="chat-readonly" className="field-hint">Подсказку можно отправить в свой ход.</p>}
  </section>;
}

export function Game({room}) {
  const {copy} = useSite();
  const [guessFilters, setGuessFilters] = useState(null);
  const guessKey = room.guess && JSON.stringify([room.guess.round, room.guess.practice]);
  const filters = guessFilters?.key === guessKey ? guessFilters : {key: guessKey, query: "", remaining: false};
  const settings = room.buttons.some((items) => items[0]?.[1].startsWith("mode:"));
  const themes = Number.isInteger(room.theme_count);
  const actions = room.buttons.flat().map(([, action]) => action);
  const choosing = actions.some((action) => action.startsWith("v:"));
  const confirming = actions.includes("yes");
  const {lobby, discussion, guess} = room;
  const view = settings ? "settings" : themes ? "themes" : guess || choosing || confirming ? "choose"
    : discussion ? "discussion" : lobby ? "lobby" : "play";
  const title = settings ? "Настройки комнаты" : themes ? "Темы игры" : guess ? "Выбор слова"
    : confirming ? "Подтверждение ответа" : choosing ? "Выбор игрока"
      : discussion ? (room.can_chat ? "Завершение хода" : "Ваши действия") : lobby ? "Комната" : "Действия";
  const turn = discussion && JSON.stringify([discussion.round, discussion.practice, discussion.circle, discussion.speaker]);

  return <section id="game">
    <div className="section-head"><h1 id="room-title">{room.title}</h1><div className="share">
      <Action className="secondary" id="share" work={() => copy(room.share, "Ссылка на комнату")}>Скопировать приглашение</Action>
      {room.telegram && <a id="bot-panel" className="quiet" href={room.telegram}>Открыть в боте</a>}
    </div></div>
    <div id="game-grid" className="game-grid" data-view={view}>
      {!themes && <article id="room-board" className="panel room-board">
        <div className="room-toolbar"><span id="room-phase" className="room-phase" data-phase={room.phase}>{phases[room.phase] || "Комната"}</span>
          {room.deadline && <Timer deadline={room.deadline} offset={room.offset}/>}
        </div>
        <div id="room-text" aria-live="polite">
          {lobby ? <LobbyBoard lobby={lobby} code={room.code}/>
            : discussion ? <TurnBoard key={turn} turn={discussion} canChat={room.can_chat}/>
              : guess ? <p className="field-hint">{guess.hint}</p> : <RoomText text={room.text} skipHeading={settings}/>}
        </div>
        {(lobby || discussion) && <Chat key={room.phase} room={room}/>}
      </article>}
      <aside className="panel actions">
        <h2 id="actions-title">{title}</h2>
        <div id="actions">{settings ? <SettingsControls room={room}/> : themes ? <ThemeControls room={room}/>
          : guess ? <GuessControls sheet={guess} buttons={room.buttons} filters={filters} setFilters={setGuessFilters}/>
            : discussion ? <TurnControls key={turn} room={room}/> : <GameControls buttons={room.buttons} phase={room.phase}/>}</div>
      </aside>
    </div>
  </section>;
}
