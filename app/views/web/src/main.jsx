import {Activity, useCallback, useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {api, setMiniSession, telegramClient, watchRoom} from "./client";
import {Packs, Profile, Ranking} from "./catalog";
import {Directory, FriendDialog, Friends, LobbyDialog} from "./directory";
import {Game} from "./game";
import {Theme} from "./theme";
import {Action, Avatar, Dialog, SiteContext, Status, useTask} from "./ui";
import "./game.css";

const query = new URLSearchParams(location.search);
const invite = query.get("join");
const friendInvite = query.get("friend");
const sections = [["directory", "Лобби"], ["ranking", "Рейтинг"], ["packs", "Наборы слов"], ["friends", "Друзья"]];

function removeInvite(key) {
  const url = new URL(location.href);
  url.searchParams.delete(key);
  history.replaceState(null, "", url.pathname + url.search + url.hash);
}

function Login({config}) {
  const [error, setError] = useState("");
  const [busy, run] = useTask(setError);
  const params = new URLSearchParams();
  if (invite) params.set("join", invite);
  if (friendInvite) params.set("friend", friendInvite);
  function submit(event) {
    event.preventDefault();
    const code = new FormData(event.currentTarget).get("code");
    setError("");
    run(async () => {
      await api("/api/auth/local", {code});
      location.reload();
    });
  }
  return <section id="welcome" className="welcome">
    <div>
      <h1>Вход в игру</h1>
      {config.login ? <a id="login" className="primary cta" href={"/auth/login" + (params.size ? "?" + params : "")}>
        Войти через Telegram <span aria-hidden="true">↗</span>
      </a> : config.local_login ? <form id="local-login" className="local-login" onSubmit={submit}>
        <p id="local-login-help">Отправьте боту <code>/web</code> и введите одноразовый код из ответа.</p>
        <label htmlFor="local-code">Код из Telegram</label>
        <input id="local-code" name="code" maxLength={32} autoComplete="one-time-code" spellCheck={false} required aria-describedby="local-login-help"/>
        <p id="local-login-error" className="form-error" role="alert">{error}</p>
        <button className="primary" type="submit" disabled={busy}>Войти</button>
      </form> : <p id="login-note" role="status">Вход через браузер ещё не настроен.</p>}
    </div>
    <div className="example" aria-label="Правила игры">
      <div className="example-head"><span className="lamp" aria-hidden="true"/> Как проходит игра</div>
      <div className="example-row"><b>Получите карточку</b><p>В классике шпион узнаёт тему. В скрытом режиме он получает другое слово из набора.</p></div>
      <div className="example-row"><b>Давайте подсказки</b><p>Говорите по очереди и следите за ответами.</p></div>
      <div className="example-row"><b>Найдите шпиона</b><p>Предложите голосование, когда подозреваете игрока.</p></div>
    </div>
  </section>;
}

function Fullscreen({telegram}) {
  const [full, setFull] = useState(false);
  useEffect(() => {
    if (!telegram) return;
    const update = () => setFull(Boolean(telegram.isFullscreen));
    update();
    telegram.onEvent?.("fullscreenChanged", update);
    return () => telegram.offEvent?.("fullscreenChanged", update);
  }, [telegram]);
  if (!telegram?.initData || !telegram.isVersionAtLeast?.("8.0") || !telegram.requestFullscreen) return null;
  return <button id="fullscreen" type="button" className="quiet" onClick={() => {
    if (full) telegram.exitFullscreen();
    else telegram.requestFullscreen();
  }}>{full ? "Выйти из полноэкранного режима" : "На весь экран"}</button>;
}

function Footer({bot}) {
  return <footer>
    <a href="https://github.com/wawgame123/SpyFallTelegramBot" target="_blank" rel="noopener noreferrer">
      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.65 7.65 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/>
      </svg>GitHub
    </a>
    <a id="telegram" href={"https://t.me/" + encodeURIComponent((bot || "spyfalldiplombot").replace(/^@/, ""))}
      target="_blank" rel="noopener noreferrer">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path fill="currentColor" d="M21.5 3c.7-.3 1.4.3 1.2 1L19 21c-.2.8-1 .9-1.6.5l-5.5-4.1-2.7 2.6c-.3-.3-.8.3-1-.2L6.6 14 1.4 12.3c-.8-.3-.8-1.3 0-1.6ZM8 13.4l1.2 4.1 1.4-2.8 7.5-7.2c.4-.4 0-.6-.4-.4Z"/>
      </svg>Бот в Telegram
    </a>
  </footer>;
}

function App() {
  const [config, setConfig] = useState(null);
  const [telegram, setTelegram] = useState(null);
  const [section, setSection] = useState("directory");
  const [room, setRoom] = useState(null);
  const [dialog, showDialog] = useState(null);
  const [message, setMessage] = useState(null);
  const [friendsVersion, setFriendsVersion] = useState(0);
  const roomRequest = useRef(0);
  const pendingReveal = useRef(false);
  const notice = useCallback((text, error = false) => setMessage({text, error}), []);
  const player = config?.player;
  const friendsEnabled = Boolean(player && config.friends);

  function goto(next) {
    notice("");
    setSection(next);
  }

  function requirePlayer() {
    if (player) return true;
    goto("welcome");
    requestAnimationFrame(() => document.querySelector("#local-code, #login")?.focus());
    return false;
  }

  const refreshRoom = useCallback(async (show = false) => {
    if (show) pendingReveal.current = true;
    const sequence = ++roomRequest.current;
    let next;
    try {
      next = (await api("/api/room")).room;
    } catch (error) {
      if (sequence === roomRequest.current) pendingReveal.current = false;
      throw error;
    }
    if (sequence !== roomRequest.current) return;
    const reveal = pendingReveal.current;
    pendingReveal.current = false;
    setRoom((previous) => {
      if (!next) return null;
      if (previous?.id === next.id && previous.revision === next.revision
        && previous.appearance_revision === next.appearance_revision) return previous;
      return {...next, offset: previous?.id === next.id ? previous.offset : next.server_time * 1000 - Date.now()};
    });
    if (next && reveal) setSection("game");
    if (!next) setSection((area) => area === "game" ? "directory" : area);
  }, []);

  const join = useCallback(async (payload, ask = false, submitted = false) => {
    if (ask) {
      showDialog({type: "join", payload});
      return;
    }
    try {
      await api("/api/join", payload);
    } catch (error) {
      if (error.password && !submitted) {
        showDialog({type: "join", payload});
        return;
      }
      throw error;
    }
    showDialog(null);
    if (payload.invite) removeInvite("join");
    notice("");
    await refreshRoom(true);
  }, [notice, refreshRoom]);

  async function perform(action, confirmation, version = room) {
    let result;
    try {
      result = await api("/api/room/action", {room: version.id, revision: version.revision, action, confirmation});
    } catch (error) {
      await refreshRoom().catch(() => {});
      throw error;
    }
    if (result.confirm) {
      showDialog({type: "message", title: "Выйти из комнаты?", text: result.confirm, confirm: async () => {
        showDialog(null);
        await perform("leave_confirm", result.confirm, version);
      }});
      return;
    }
    notice("");
    if (result.alert) showDialog({type: "message", title: action === "role" ? "Карточка игрока" : "Сообщение игры", text: result.alert});
    await refreshRoom();
  }

  async function copy(text, title) {
    try {
      await navigator.clipboard.writeText(text);
      notice("Ссылка скопирована.");
    } catch {
      showDialog({type: "message", title, text});
    }
  }

  function openFriend(reference) {
    if (requirePlayer()) showDialog({type: "friend", reference});
  }

  useEffect(() => {
    const controller = new AbortController();
    async function start() {
      const client = await telegramClient();
      if (controller.signal.aborted) return;
      setTelegram(client);
      client?.ready?.();
      client?.expand?.();
      if (client?.initData) {
        try {
          const {session} = await api("/api/auth/mini", {initData: client.initData}, controller.signal);
          setMiniSession(session);
        } catch (error) {
          if (controller.signal.aborted) return;
          notice(error.message, true);
        }
      }
      const config = await api("/api/me", undefined, controller.signal);
      if (controller.signal.aborted) return;
      setConfig(config);
      if (!config.player) {
        if (invite || friendInvite) setSection("welcome");
        return;
      }
      if (invite) {
        await join({invite}).catch((error) => notice(error.message, true));
      }
      await refreshRoom(!friendInvite);
      if (friendInvite && config.friends) {
        removeInvite("friend");
        setSection("friends");
        showDialog({type: "friend", reference: friendInvite});
      }
    }
    start().catch((error) => { if (!controller.signal.aborted) notice(error.message, true); });
    return () => controller.abort();
  }, [join, notice, refreshRoom]);

  useEffect(() => {
    if (!room?.id) return;
    const controller = new AbortController();
    watchRoom(controller.signal, refreshRoom, notice);
    return () => controller.abort();
  }, [room?.id, refreshRoom, notice]);

  useEffect(() => {
    if (!player) return;
    function refresh() {
      if (!document.hidden) refreshRoom().catch((error) => notice(error.message, true));
    }
    document.addEventListener("visibilitychange", refresh);
    return () => document.removeEventListener("visibilitychange", refresh);
  }, [player, refreshRoom, notice]);

  const close = () => showDialog(null);
  const active = section === "game" ? "directory" : section;
  function updateCosmetics(cosmetics) {
    setConfig((current) => ({...current, player: {...current.player, cosmetics}}));
    setFriendsVersion((version) => version + 1);
  }

  return <SiteContext.Provider value={{player, room, friendsEnabled, friendsVersion, goto, notice, join, perform, updateCosmetics,
    refreshRoom, showDialog, copy, openFriend, requirePlayer, refreshFriends: () => setFriendsVersion((version) => version + 1)}}>
    <a className="skip" href="#main">Перейти к содержимому</a>
    <header className="bar">
      <a className="brand" href="/" aria-label="Шпион, главная"><span className="seal" aria-hidden="true"><i/></span>Шпион<span className="brand-dot">.</span></a>
      <Theme telegram={telegram}/>
      {player ? <button id="account" type="button" className="account" aria-haspopup="dialog"
        aria-label={"Открыть профиль " + player.name} onClick={() => showDialog({type: "profile"})}>
        <Avatar name={player.name} image={player.avatar} cosmetics={player.cosmetics}/><span id="account-name">{player.name}</span>
      </button> : config && <button id="sign-in" type="button" className="quiet" onClick={requirePlayer}>Войти через Telegram</button>}
      <Fullscreen telegram={telegram}/>
      {player && <Action id="logout" className="quiet" work={async () => { await api("/api/logout", {}); location.assign("/"); }}>Выйти из аккаунта</Action>}
    </header>
    <main id="main">
      {config && <>
        <nav id="site-nav" className="site-nav" aria-label="Разделы сайта">
          {sections.filter(([id]) => id !== "friends" || friendsEnabled).map(([id, title]) => <button type="button" key={id}
            data-section={id} aria-current={active === id ? "page" : undefined} onClick={() => goto(id)}>{title}</button>)}
        </nav>
        {section === "welcome" && <Login config={config}/>}
        {section === "directory" && <Directory/>}
        {room && <Activity mode={section === "game" ? "visible" : "hidden"}><Game key={room.id} room={room}/></Activity>}
        <Activity mode={section === "ranking" ? "visible" : "hidden"}><Ranking/></Activity>
        <Activity mode={section === "packs" ? "visible" : "hidden"}><Packs/></Activity>
        {section === "friends" && friendsEnabled && <Friends/>}
      </>}
      {!config && !message?.error && <Status message="Подключаемся к игре…"/>}
      <Status id="notice" className="notice" message={message?.text} error={message?.error}/>
    </main>
    <Footer bot={config?.bot}/>
    {dialog?.type === "profile" && <Profile close={close}/>}
    {dialog?.type === "friend" && <FriendDialog key={dialog.reference} reference={dialog.reference} close={close}/>}
    {(dialog?.type === "join" || dialog?.type === "create") && <LobbyDialog key={dialog.type} dialog={dialog} close={close}/>}
    {dialog?.type === "message" && <Dialog id="message-dialog" label="message-title" onClose={close}>
      <h2 id="message-title">{dialog.title}</h2><p id="message-body">{dialog.text}</p>
      <div className="dialog-buttons"><button className="quiet" onClick={close} type="button">{dialog.confirm ? "Отмена" : "Закрыть"}</button>
        {dialog.confirm && <Action className="primary" work={dialog.confirm}>Да, выйти</Action>}
      </div>
    </Dialog>}
  </SiteContext.Provider>;
}

createRoot(document.getElementById("app")).render(<App/>);
