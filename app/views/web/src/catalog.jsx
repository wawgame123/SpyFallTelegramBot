import {useEffect, useRef, useState} from "react";
import {api, useResource} from "./client";
import {CosmeticsEditor} from "./cosmetics";
import {Action, Avatar, Contact, Dialog, Status, useSite, useTask} from "./ui";

const number = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 1});
const plural = new Intl.PluralRules("ru-RU");
const metrics = {points: "Очки", wins: "Победы", spy: "Победы шпиона", rate: "Доля побед"};

export function wordCount(count) {
  return count + " " + ({one: "слово", few: "слова"}[plural.select(count)] || "слов");
}

function RatingLine({entry, sort}) {
  return <tr className={entry.mine ? "my-rank" : undefined}>
    <td>{entry.place}</td>
    <th scope="row"><div className="rank-player">
      <Avatar name={entry.name} image={entry.image} cosmetics={entry.cosmetics} lazy/>
      <span><Contact name={entry.name} reference={entry.friend_ref}/>{entry.mine && <small className="rank-tag">Вы</small>}</span>
    </div></th>
    <td>{entry.games}</td><td>{sort === "rate" ? number.format(entry.rate) + "%" : entry[sort]}</td>
  </tr>;
}

export function Ranking() {
  const [period, setPeriod] = useState("all");
  const [sort, setSort] = useState("points");
  const {data, error} = useResource("/api/ranking?" + new URLSearchParams({sort, period}));
  const empty = sort === "rate" ? "Для рейтинга по доле побед нужно сыграть хотя бы пять раундов за выбранный период."
    : "За этот период ещё нет сыгранных раундов.";
  return <section id="ranking">
    <div className="section-head"><h1>Таблица лидеров</h1></div>
    <div id="rank-filters" className="filters">
      <label>Период <select id="rank-period" value={period} onChange={(event) => setPeriod(event.target.value)}>
        <option value="all">Всё время</option><option value="week">7 дней</option><option value="month">30 дней</option>
      </select></label>
      <label>Показатель <select id="rank-sort" value={sort} onChange={(event) => setSort(event.target.value)}>
        {Object.entries(metrics).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
    </div>
    <Status id="rank-status" error={error} message={error || (!data ? "Загружаем рейтинг…" : !data.rows.length ? empty : "")}/>
    {!!data?.rows.length && <table id="rank-table" className="rank-table" aria-label="Места в рейтинге">
      <thead><tr><th scope="col">№</th><th scope="col">Игрок</th><th scope="col">Раунды</th><th scope="col">{metrics[sort]}</th></tr></thead>
      <tbody id="rank-rows">{data.rows.map((entry) => <RatingLine key={entry.place} entry={entry} sort={sort}/>)}</tbody>
      {data.own_rank && <tfoot id="rank-mine"><RatingLine entry={data.own_rank} sort={sort}/></tfoot>}
    </table>}
  </section>;
}

export function Profile({close}) {
  const {player} = useSite();
  const [editing, setEditing] = useState(false);
  const editorTitle = useRef(null);
  const editButton = useRef(null);
  const {data, error} = useResource("/api/profile");
  const stats = data?.stats;
  const values = [
    ["Очки", stats?.points], ["Раунды", stats?.games], ["Победы", stats?.wins],
    ["Доля побед", number.format(stats?.win_rate ?? 0) + "%"], ["За шпиона", stats?.spy_wins],
    ["За мирного", stats?.civilian_wins], ["Поражения", stats?.losses], ["Ничьи", stats?.draws], ["Лобби", stats?.lobbies],
  ];
  useEffect(() => {
    if (editing) editorTitle.current?.focus();
  }, [editing]);

  function backToProfile() {
    setEditing(false);
    requestAnimationFrame(() => editButton.current?.focus());
  }

  return <Dialog id="profile-dialog" label="profile-title" onClose={close}>
    {editing ? <>
      <h2 id="profile-title" ref={editorTitle} tabIndex={-1} className="cosmetics-title">Украшения аватара</h2>
      <CosmeticsEditor player={player} close={backToProfile}/>
    </> : <>
      <div className="profile-top">
        <Avatar name={data?.name || player.name} image={data?.avatar || player.avatar} cosmetics={player.cosmetics} className="avatar profile-avatar"/>
        <div><h2 id="profile-title">Профиль игрока</h2><p id="profile-name">{data?.name || player.name}</p></div>
      </div>
      <button ref={editButton} type="button" className="secondary profile-cosmetics" onClick={() => setEditing(true)}>Украшения аватара</button>
      <Status id="profile-status" className="field-hint" error={error}
        message={error || (!data ? "Загружаем статистику…" : !stats ? "Пока нет сыгранных онлайн-раундов." : "")}/>
      {data && <dl id="profile-stats" className="profile-stats">{values.map(([label, value]) => <div key={label} className="profile-stat">
        <dt>{label}</dt><dd>{typeof value === "string" ? value : number.format(value ?? 0)}</dd>
      </div>)}</dl>}
    </>}
    <button id="profile-close" className="quiet" type="button" aria-label="Закрыть профиль" onClick={close}>×</button>
  </Dialog>;
}

function packSummary(pack) {
  const visibility = pack.hidden ? "Скрыт модерацией" : pack.public ? "Общий" : "Личный";
  return [wordCount(pack.words_count), visibility, pack.owner || pack.category].filter(Boolean).join(" · ");
}

function PackDetail({id, edit}) {
  const {requirePlayer} = useSite();
  const path = "/api/packs/" + id;
  const {data, error} = useResource(path);
  const [favorite, setFavorite] = useState(null);
  const saved = favorite ?? data?.favorite;

  if (!data) return <Status className="panel pack-panel" message={error || "Загружаем набор…"} error={error}/>;
  return <article id="pack-detail" className="panel pack-panel">
    <div className="pack-head"><div><h2>{data.title}</h2><p className="field-hint">{packSummary(data)}</p></div>
      <div className="share">
        <Action className="secondary" work={async () => {
          if (requirePlayer()) setFavorite((await api(path + "/favorite", {})).favorite);
        }}>{saved ? "Убрать из избранного" : "В избранное"}</Action>
        {data.editable && <button type="button" className="secondary" onClick={() => edit(data)}>Редактировать</button>}
      </div>
    </div>
    {data.description && <p className="pack-description">{data.description}</p>}
    {data.words.length ? <ul className="pack-words">{data.words.map((word, index) => <li key={index}>{word}</li>)}</ul>
      : <p className="empty">В наборе пока нет слов.</p>}
  </article>;
}

function PackEditor({pack, cancel, saved}) {
  const [error, setError] = useState("");
  const [busy, run] = useTask(setError);
  function submit(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    run(async () => {
      const result = await api("/api/packs" + (pack ? "/" + pack.id : ""), {
        title: form.get("title"), words: form.get("words"), public: form.has("public"), revision: pack?.revision,
      });
      saved(result.id);
    });
  }
  return <form id="pack-editor" className="panel pack-panel" onSubmit={submit}>
    <h2>{pack ? "Редактировать набор" : "Новый набор"}</h2>
    <label htmlFor="pack-title">Название</label>
    <input id="pack-title" name="title" maxLength={60} required defaultValue={pack?.title} autoFocus/>
    <label htmlFor="pack-words">Слова</label>
    <p id="pack-words-hint" className="field-hint">По одному на строку или через запятую. До 500 слов, до 40 символов каждое.</p>
    <textarea id="pack-words" name="words" rows={10} aria-describedby="pack-words-hint" defaultValue={pack?.words.join("\n")}/>
    <label className="check"><input id="pack-public" name="public" type="checkbox"
      defaultChecked={!!pack?.public} disabled={pack?.hidden && !pack.public}/> Опубликовать для всех игроков</label>
    <p className="field-hint">Для публикации нужно не менее 10 слов. Личный набор виден только тебе.</p>
    <p className="form-error" role="alert">{error}</p>
    <div className="dialog-buttons">
      <button type="button" className="quiet" onClick={cancel} disabled={busy}>Отмена</button>
      <button type="submit" className="primary" disabled={busy}>Сохранить набор</button>
    </div>
  </form>;
}

export function Packs() {
  const {player, requirePlayer} = useSite();
  const [scope, setScope] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [view, setView] = useState(null);
  const [version, setVersion] = useState(0);
  const {data, error} = useResource(view ? null : "/api/packs?" + new URLSearchParams({scope, q: search, page}), {version});
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  useEffect(() => { if (data && page >= pages) setPage(pages - 1); }, [data, page, pages]);

  function save(id) {
    setScope("my");
    setSearch("");
    setPage(0);
    setView({id});
  }
  function back() {
    setView(null);
    setVersion((value) => value + 1);
  }

  return <section id="packs">
    <div className="section-head"><h1>Наборы слов</h1>
      {!view && <button id="new-pack" type="button" className="primary" onClick={() => requirePlayer() && setView({editor: true})}>Создать набор</button>}
    </div>
    {view ? <>
      <button id="pack-back" type="button" className="quiet" onClick={back}>К списку наборов</button>
      {view.editor ? <PackEditor pack={view.pack} cancel={() => view.pack ? setView({id: view.pack.id}) : back()} saved={save}/>
        : <PackDetail key={view.id} id={view.id} edit={(pack) => setView({editor: true, pack})}/>}
    </> : <div id="pack-browser">
      <form id="pack-filters" className="filters" onSubmit={(event) => {
        event.preventDefault();
        setSearch(new FormData(event.currentTarget).get("q").trim());
        setPage(0);
        setVersion((value) => value + 1);
      }}>
        <label>Раздел <select id="pack-scope" value={scope} onChange={(event) => { setScope(event.target.value); setPage(0); }}>
          <option value="all">Все доступные</option><option value="builtin">Встроенные</option><option value="public">Общие</option>
          {player && <><option value="my">Мои наборы</option><option value="fav">Избранное</option></>}
        </select></label>
        <label className="search-field">Название или слово
          <input id="pack-search" name="q" type="search" maxLength={60} defaultValue={search} autoComplete="off"/>
        </label>
        <button className="secondary" type="submit">Найти</button>
      </form>
      <div id="pack-list" className="lobbies" aria-live="polite">
        {!data?.packs.length && <Status message={error || (!data ? "Загружаем наборы…" : search ? "По этому запросу наборов нет."
          : scope === "my" ? "Здесь появятся наборы, которые ты создашь." : "В этом разделе пока нет наборов.")} error={error}/>}
        {data?.packs.map((pack) => <button key={pack.id} type="button" className="lobby pack-card" onClick={() => setView({id: pack.id})}>
          <strong>{pack.title}</strong><span className="field-hint">{packSummary(pack)}</span>
        </button>)}
      </div>
      {pages > 1 && <div id="pack-pagination" className="pagination">
        <button id="pack-prev" className="quiet" type="button" disabled={!page} onClick={() => setPage(page - 1)}>Назад</button>
        <span>{page + 1} из {pages}</span>
        <button id="pack-next" className="quiet" type="button" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Далее</button>
      </div>}
    </div>}
  </section>;
}
