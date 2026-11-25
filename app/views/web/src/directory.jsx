import {useState} from "react";
import {api, useResource} from "./client";
import {Action, Avatar, Contact, Dialog, Status, useSite, useTask} from "./ui";

function LobbyCard({lobby}) {
  const {room, goto, requirePlayer, join} = useSite();
  const current = room?.code === lobby.code;
  return <article className={"lobby" + (lobby.friends?.length ? " lobby-with-friends" : "")}>
    <div className="lobby-details">
      <h2>{lobby.title}</h2>
      <div className="lobby-meta">
        <span className={"badge" + (lobby.locked ? "" : " badge-open")}>{lobby.locked ? "С паролем" : "Открыто"}</span>
        <span>{lobby.players} из {lobby.capacity} игроков</span>
      </div>
      {!!lobby.friends?.length && <p className="lobby-friends">Друзья здесь · {lobby.friends.join(", ")}</p>}
      {!!lobby.avatars?.length && <div className="lobby-avatars">
        {lobby.avatars.map((contact, index) => <Contact key={contact.friend_ref || index} reference={contact.friend_ref}
          name={contact.name} className="avatar-link" aria-label={"Профиль " + contact.name} title={contact.name}>
          <Avatar name={contact.name} initial={contact.initial} image={contact.image} cosmetics={contact.cosmetics} lazy/>
        </Contact>)}
      </div>}
    </div>
    <Action className="secondary" work={async () => {
      if (!requirePlayer()) return;
      if (current) goto("game");
      else await join({code: lobby.code}, lobby.locked);
    }}>{current ? "Вернуться" : "Войти"}</Action>
  </article>;
}

export function Directory() {
  const {room, goto, showDialog, requirePlayer, join, friendsEnabled, friendsVersion} = useSite();
  const {data, error} = useResource("/api/lobbies", {poll: 12000, version: friendsVersion});
  const [busy, run] = useTask();
  const friends = data?.lobbies.filter((lobby) => lobby.friends?.length);
  function enter(event) {
    event.preventDefault();
    const code = new FormData(event.currentTarget).get("code").trim().toUpperCase();
    if (requirePlayer()) run(() => join({code}));
  }

  return <section id="directory">
    <div className="section-head"><h1>Активные лобби</h1><div className="share">
      {room ? <button className="quiet" id="return-room" type="button" onClick={() => goto("game")}>Моя комната</button>
        : <button className="primary" id="create" type="button" onClick={() => requirePlayer() && showDialog({type: "create"})}>Создать лобби</button>}
    </div></div>
    <div className="directory-layout">
      <div className="lobby-lists">
        {friendsEnabled && <>
          <section id="friends-lobbies-section" className="friends-lobbies-section" aria-labelledby="friends-lobbies-title">
            <h2 id="friends-lobbies-title">Лобби друзей</h2>
            <div id="friends-lobbies" className="lobbies">
              {friends?.length ? friends.map((lobby) => <LobbyCard key={lobby.code} lobby={lobby}/>)
                : <p className="empty">{data ? "Сейчас у друзей нет доступных лобби." : "Загружаем лобби…"}</p>}
            </div>
          </section>
          <h2 id="all-lobbies-title">Все лобби</h2>
        </>}
        <div id="lobbies" className="lobbies" aria-live="polite">
          {!data ? <Status message={error || "Загружаем лобби…"} error={error}/>
            : data.lobbies.length ? data.lobbies.map((lobby) => <LobbyCard key={lobby.code} lobby={lobby}/>)
              : <div className="directory-empty"><h2>Пока нет доступных лобби</h2><p>Создайте комнату или войдите по приглашению.</p></div>}
        </div>
      </div>
      <aside className="panel code-panel">
        <h2>Вход по коду</h2>
        <form id="code-form" className="code-form" onSubmit={enter}>
          <label htmlFor="code">Код комнаты</label>
          <input id="code" name="code" placeholder="AB12CD" maxLength={6} required autoComplete="off" autoCapitalize="characters" spellCheck={false}/>
          <button type="submit" className="secondary" disabled={busy}>Войти по коду</button>
        </form>
      </aside>
    </div>
  </section>;
}

export function LobbyDialog({dialog, close}) {
  const {join, refreshRoom, notice} = useSite();
  const [error, setError] = useState("");
  const [busy, run] = useTask(setError);
  const creating = dialog.type === "create";
  const label = creating ? "create-title" : "join-title";
  function submit(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setError("");
    run(async () => {
      if (creating) {
        await api("/api/lobbies", {password: data.get("password"), listed: data.has("listed")});
        close();
        notice("");
        await refreshRoom(true);
      } else await join({...dialog.payload, password: data.get("password")}, false, true);
    });
  }

  return <Dialog id={creating ? "create-dialog" : "join-dialog"} label={label} onClose={close}>
    <form id={creating ? "create-form" : "join-form"} onSubmit={submit}>
      <h2 id={label}>{creating ? "Создать лобби" : "Пароль лобби"}</h2>
      {creating && <label className="check"><input type="checkbox" name="listed" defaultChecked/> Показывать в общем списке</label>}
      <label htmlFor="lobby-password">Пароль {creating && <span>по желанию</span>}</label>
      <input id="lobby-password" name="password" type="password" maxLength={64} minLength={creating ? 4 : undefined}
        autoComplete={creating ? "new-password" : "current-password"} required={!creating} placeholder={creating ? "От 4 символов" : undefined}/>
      <p className="form-error" role="alert">{error}</p>
      <div className="dialog-buttons">
        <button type="button" className="quiet" onClick={close}>Отмена</button>
        <button type="submit" className="primary" disabled={busy}>{creating ? "Создать" : "Войти"}</button>
      </div>
    </form>
  </Dialog>;
}

export function Friends() {
  const {copy, friendsVersion} = useSite();
  const {data, error} = useResource("/api/friends", {version: friendsVersion});
  const link = data && location.origin + "/?" + new URLSearchParams({friend: data.invite});
  return <section id="friends">
    <div className="section-head"><h1>Друзья</h1>
      <Action id="friend-invite" className="secondary" disabled={!link} work={() => copy(link, "Ссылка на ваш профиль")}>Скопировать мою ссылку</Action>
    </div>
    <Status id="friends-status" error={error} message={error || (!data ? "Загружаем список…" : !data.friends.length
      ? "Откройте игрока в рейтинге или лобби. Можно также обменяться личными ссылками." : "")}/>
    <div id="friend-list" className="friend-list">{data?.friends.map((contact) => <article key={contact.friend_ref} className="friend-item">
      <Avatar name={contact.name} image={contact.image} cosmetics={contact.cosmetics}/><strong>{contact.name}</strong>
      <Contact className="quiet" name="Открыть" reference={contact.friend_ref} aria-label={"Открыть профиль " + contact.name}/>
    </article>)}</div>
  </section>;
}

export function FriendDialog({reference, close}) {
  const {refreshFriends} = useSite();
  const path = "/api/friends/player?" + new URLSearchParams({ref: reference});
  const {data, error} = useResource(path);
  const [added, setAdded] = useState(null);
  const [actionError, setActionError] = useState("");
  const [busy, run] = useTask(setActionError);
  const inList = added ?? data?.added;
  return <Dialog id="friend-dialog" label="friend-title" onClose={close}>
    <div className="friend-dialog-head">
      <Avatar name={data?.name} image={data?.image} cosmetics={data?.cosmetics} className="avatar profile-avatar"/>
      <h2 id="friend-title">{data?.name || "Игрок"}</h2>
    </div>
    <Status id="friend-status" className="field-hint" error={actionError || error}
      message={actionError || error || (!data ? "Открываем профиль…" : inList ? "В вашем списке друзей" : "")}/>
    <div className="dialog-buttons">
      <button className="quiet" type="button" onClick={close}>Закрыть</button>
      {data && <button id="friend-change" type="button" className={inList ? "quiet danger" : "primary"} disabled={busy}
        onClick={() => run(async () => {
          setActionError("");
          const result = await api("/api/friends", {ref: data.friend_ref, add: !inList});
          setAdded(result.added);
          refreshFriends();
        })}>{inList ? "Удалить из друзей" : "Добавить в друзья"}</button>}
    </div>
  </Dialog>;
}
