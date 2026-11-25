import {useState} from "react";
import {wordCount} from "./catalog";
import {Action, useSite, useTask} from "./ui";

const mainActions = new Set(["start", "next", "force", "role", "rd:1", "endturn:0", "yes"]);
const choices = /^(v|gp|pv):/;
const descriptions = {
  "mode:classic": "Шпион знает свою роль, но не видит слово.",
  "mode:hidden": "Шпион получает другое слово и не знает своей роли.",
};

function selected(label) {
  return [label.replace(/^\[[x ]\] /, ""), label.startsWith("[x] ")];
}

function GameAction({action, children, ...props}) {
  const {perform} = useSite();
  return <Action {...props} data-action={action} work={() => perform(action)}>{children}</Action>;
}

function NumericRule({name, rule, digits}) {
  const {perform} = useSite();
  const [busy, run] = useTask();
  return <form className="custom-rule" onSubmit={(event) => {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get("value");
    run(async () => {
      if (BigInt(value) < BigInt(rule.min)) throw new Error("Минимальное значение — " + rule.min);
      await perform("csv:" + name + ":" + value);
    });
  }}>
    <input id={"rule-number-" + name} name="value" type="text" inputMode="numeric" pattern="[0-9]+" maxLength={digits}
      required defaultValue={rule.value} aria-label={name === "max" ? "Своё число игроков" : "Своё число раундов"}/>
    <button className="quiet" type="submit" disabled={busy}>Применить</button>
  </form>;
}

export function SettingsControls({room}) {
  const modes = [];
  const rules = [];
  const extra = [];
  let done;
  for (const items of room.buttons) {
    const [label, action] = items[0] || [];
    if (!action) continue;
    if (action.startsWith("mode:")) modes.push(...items);
    else if (action === "noop") rules.push({label, values: []});
    else if (action.startsWith("sv:") && rules.length) rules.at(-1).values.push(...items);
    else if (action.startsWith("custom:") && rules.length) rules.at(-1).name = action.slice(7);
    else {
      for (const entry of items) {
        if (entry[1] === "ui:lobby") done = entry;
        else extra.push(entry);
      }
    }
  }
  return <>
    <h3 className="settings-subtitle">Режим игры</h3>
    <div className="mode-cards" role="group" aria-label="Режим игры">{modes.map(([label, action]) => {
      const [name, active] = selected(label);
      const description = action.replace(":", "-") + "-description";
      return <GameAction key={action} action={action} className="mode-card" aria-pressed={active} aria-label={name} aria-describedby={description}>
        <span className="mode-symbol" aria-hidden="true"/><strong>{name}</strong>
        <span className="mode-description" id={description}>{descriptions[action]}</span>
      </GameAction>;
    })}</div>
    <div className="rule-controls">{rules.map(({label, values, name}) => {
      const rule = room.settings?.[name];
      return <fieldset className="rule-control" key={label}><legend>{label}</legend>
        <div className="rule-switches">{values.map(([option, action]) => {
          const [caption, active] = selected(option);
          return <GameAction key={action} action={action} className="rule-value" aria-pressed={active}>{caption}</GameAction>;
        })}</div>
        {rule && <NumericRule key={rule.value} name={name} rule={rule} digits={room.settings.digits}/>}
      </fieldset>;
    })}</div>
    <div className="settings-links">{extra.map(([label, action]) => {
      const [name, value] = label.split(": ", 2);
      return <GameAction key={action} action={action} className="settings-link">
        <span>{name}</span>{value && <span className="setting-value">{value}</span>}
      </GameAction>;
    })}</div>
    {done && <GameAction action={done[1]} className="primary settings-done">{done[0]}</GameAction>}
  </>;
}

export function ThemeControls({room}) {
  const cards = [], pages = [], bulk = [], finish = [];
  for (const [label, action] of room.buttons.flat()) {
    if (action.startsWith("th:")) {
      const [name, active] = selected(label);
      const count = name.match(/ \((\d+)\)$/);
      cards.push(<GameAction key={action} action={action} className="theme-card" aria-pressed={active}>
        <span className="theme-check" aria-hidden="true"/><strong>{count ? name.slice(0, count.index) : name}</strong>
        {count && <small className="theme-count">{wordCount(Number(count[1]))}</small>}
      </GameAction>);
    } else {
      const target = action.startsWith("tp:") ? pages : action === "ui:settings" ? finish : bulk;
      target.push(<GameAction key={action} action={action} className={target === finish ? "primary" : "quiet"}>{label}</GameAction>);
    }
  }
  return <>
    <div className="theme-info"><p className="field-hint">Отметьте темы для раундов. В каждом раунде играется одна тема.</p>
      <span className="badge badge-open">Выбрано {room.theme_count}</span></div>
    <div className="theme-cards" role="group" aria-label="Доступные темы">{cards}</div>
    <div className="theme-toolbar"><div className="theme-bulk">{bulk}</div><div className="theme-pages">{pages}</div></div>
    <div className="theme-footer">{finish}</div>
  </>;
}

export function GameControls({buttons, phase}) {
  const rows = [], utility = [];
  for (const items of buttons) {
    if (!items.length) continue;
    const [label, action] = items[0];
    if (items.length === 1 && action === "noop") {
      rows.push({label});
      continue;
    }
    const choiceType = action.match(choices)?.[1];
    const grouped = choiceType && items.every(([, command]) =>
      command.startsWith(choiceType + ":") && !command.endsWith(":cancel"));
    const previous = rows.at(-1);
    const row = grouped && previous?.type === choiceType ? previous : {type: grouped ? choiceType : null, items: []};
    for (const [caption, command] of items) {
      const main = command === "role" ? phase === "dealing" : mainActions.has(command) || command.startsWith("rematch:");
      const danger = command === "leave" || command === "end";
      const [name, active] = selected(caption);
      const style = grouped ? "play-choice" : main ? "primary main-action" : danger ? "quiet danger"
        : command.startsWith("ui:") ? "quiet action-detail" : "secondary";
      (grouped || main ? row.items : utility).push(<GameAction key={command} action={command} className={style}
        aria-pressed={grouped ? active : undefined}>{grouped ? name : caption}</GameAction>);
    }
    if (row !== previous && row.items.length) rows.push(row);
  }
  return <>{rows.map((row, index) => row.label ? <h3 className="action-label" key={row.label}>{row.label}</h3>
    : <div className={row.type ? "action-options" : "action-row"} key={index}>{row.items}</div>)}
    {!!utility.length && <div className="action-utility">{utility}</div>}
  </>;
}

export function TurnControls({room}) {
  const [propose, setPropose] = useState(false);
  const actions = new Set(room.buttons.flat().map(([, action]) => action));
  if (!room.can_chat || !actions.has("endturn:0") || !actions.has("endturn:1")) {
    return <GameControls buttons={room.buttons} phase={room.phase}/>;
  }
  return <>
    <div className="turn-controls">
      <label className="check vote-proposal"><input type="checkbox" id="vote-proposal" checked={propose}
        onChange={(event) => setPropose(event.target.checked)} aria-describedby="vote-proposal-hint"/>
        <span>Предложить голосование</span>
      </label>
      <small className="field-hint" id="vote-proposal-hint">Голосование начнётся после круга, если его предложит большинство.</small>
      <GameAction action={propose ? "endturn:1" : "endturn:0"} className="primary turn-finish">Закончить ход</GameAction>
      {room.discussion.hidden && room.discussion.circle === 1 && <small className="field-hint">Угадывать слово можно после первого круга.</small>}
    </div>
    <GameControls phase={room.phase} buttons={room.buttons.map((row) => row.filter(([, action]) => !action.startsWith("endturn:")))}/>
  </>;
}

const normalized = (word) => word.toLocaleLowerCase("ru-RU").replaceAll("ё", "е");

export function GuessControls({sheet, buttons, filters, setFilters}) {
  const {query, remaining} = filters;
  const marked = new Set(sheet.crossed);
  const search = normalized(query.trim().replace(/\s+/g, " "));
  const words = sheet.words.map((name, index) => ({name, index, active: marked.has(index)}))
    .filter(({name, active}) => !(remaining && active) && normalized(name).includes(search));
  return <>
    <div className="guess-tools">
      <label className="guess-search"><span>Поиск слова</span><input id="guess-search" type="search" maxLength={64}
        placeholder="Введите часть слова" autoComplete="off" value={query} onChange={(event) => setFilters({...filters, query: event.target.value})}/></label>
      <label className="check guess-remaining"><input id="guess-remaining" type="checkbox" checked={remaining}
        onChange={(event) => setFilters({...filters, remaining: event.target.checked})}/><span>Только оставшиеся</span></label>
    </div>
    <p className="guess-count" role="status">Осталось {sheet.words.length - marked.size} из {sheet.words.length}{search && " · найдено " + words.length}</p>
    {words.length ? <ul className="guess-words" aria-label="Варианты ответа">{words.map(({name, index, active}) => {
      const title = (active ? "Вернуть «" : "Вычеркнуть «") + name + "»";
      return <li key={index} className={"guess-word" + (active ? " crossed-word" : "")}>
        <GameAction action={"gp:" + index} className="guess-choice" disabled={active}>{name}</GameAction>
        <GameAction action={"gx:" + index + ":" + (active ? "0" : "1")} className="guess-mark" title={title} aria-label={title}
          aria-pressed={active}>{active ? "↶" : "×"}</GameAction>
      </li>;
    })}</ul> : <p className="empty">{search ? "По этому запросу ничего нет." : "Все слова вычеркнуты. Снимите фильтр, чтобы их вернуть."}</p>}
    <div className="guess-navigation">{buttons.flat().filter(([, action]) => action === "cancel" || action === "skip")
      .map(([title, action]) => <GameAction key={action} action={action} className="quiet">{title}</GameAction>)}</div>
  </>;
}
