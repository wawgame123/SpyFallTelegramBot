import {useRef, useState} from "react";
import {clampArc, clampHatLift, constrainFace, limits, poseFor} from "./avatar-placement";
import {api, useResource} from "./client";
import {accessoryUrl, Avatar, Status, useSite, useTask} from "./ui";

const emptyLook = {hat: null, face: null, outfit: null, placement: {}};
const slots = {
  hat: {title: "Головные уборы", clear: "Без шапки"},
  face: {title: "Украшения для лица", clear: "Без украшения"},
  outfit: {title: "Одежда", clear: "Без одежды"},
};

function CosmeticOptions({slot, items, value, onChange}) {
  const {title, clear} = slots[slot];
  const options = [{id: null, label: clear}, ...items];
  return <fieldset className="cosmetic-field">
    <legend>{title}</legend>
    <div className="cosmetic-options">{options.map(({id, label}) => <label className="cosmetic-option" key={id || "none"}>
      <input type="radio" className="sr-only" name={slot} checked={id === value} onChange={() => onChange(id)}/>
      <span className="cosmetic-choice">
        <span className="cosmetic-swatch" data-slot={slot} aria-hidden="true">
          {id ? <img src={accessoryUrl(id)} alt="" loading="lazy"/> : <i/>}
        </span>
        <span className="cosmetic-name">{label}</span>
      </span>
    </label>)}</div>
  </fieldset>;
}

function PoseRange({label, value, min, max, unit = "", onChange}) {
  return <label className="cosmetic-range">
    <span>{label}</span><output>{value}{unit}</output>
    <input type="range" min={min} max={max} value={value} aria-label={label}
      onChange={(event) => onChange(Number(event.target.value))}/>
  </label>;
}

function PlacementControls({slot, pose, change, reset}) {
  const set = (key) => (value) => change({[key]: value});
  return <details className="cosmetic-properties">
    <summary>Свойства</summary>
    <div className="cosmetic-tools">
      <div className="cosmetic-axes">
        {slot === "hat" ? <>
          <PoseRange label="По дуге" value={pose.arc} min={-limits.arc} max={limits.arc}
            unit="°" onChange={set("arc")}/>
          <PoseRange label="Высота" value={pose.lift} min={-limits.hatLift} max={limits.hatLift}
            unit="%" onChange={set("lift")}/>
        </> : <>
          <PoseRange label="Влево / вправо" value={pose.x} min={-limits.radius} max={limits.radius} onChange={set("x")}/>
          <PoseRange label="Вверх / вниз" value={pose.y} min={-limits.radius} max={limits.radius} onChange={set("y")}/>
        </>}
      </div>
      <div className="cosmetic-axes">
        <PoseRange label="Поворот" value={pose.rotation} min={-limits.rotation} max={limits.rotation} unit="°"
          onChange={set("rotation")}/>
        <PoseRange label="Размер" value={pose.scale} min={limits.minScale} max={limits.maxScale} unit="%"
          onChange={set("scale")}/>
      </div>
      <div className="cosmetic-tool-buttons">
        <button type="button" className="secondary" aria-pressed={pose.mirror}
          onClick={() => change({mirror: !pose.mirror})}>Отразить</button>
        <button type="button" className="quiet" onClick={reset}>Сбросить положение</button>
      </div>
    </div>
  </details>;
}

function pointerPosition(event, bounds) {
  return {
    x: (event.clientX - bounds.left - bounds.width / 2) * 100 / bounds.width,
    y: (event.clientY - bounds.top - bounds.height / 2) * 100 / bounds.height,
  };
}

function angleFromTop({x, y}) {
  return Math.atan2(x, Math.max(0, -y)) * 180 / Math.PI;
}

export function CosmeticsEditor({player, close}) {
  const {updateCosmetics} = useSite();
  const initial = {...emptyLook, ...player.cosmetics};
  const [draft, setDraft] = useState(initial);
  const [slot, setSlot] = useState("hat");
  const [saveError, setSaveError] = useState("");
  const [busy, run] = useTask(setSaveError);
  const {data, error} = useResource("/api/cosmetics");
  const preview = useRef(null);
  const gesture = useRef(null);
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const pose = poseFor(draft, slot);

  function choose(id) {
    setDraft((current) => {
      const placement = {...current.placement};
      delete placement[slot];
      return {...current, [slot]: id, placement};
    });
  }

  function changePose(active, values) {
    setDraft((current) => {
      const next = {...poseFor(current, active), ...values};
      if (active === "face") Object.assign(next, constrainFace(next.x, next.y));
      return {...current, placement: {...current.placement, [active]: next}};
    });
  }

  function resetPose() {
    setDraft((current) => {
      const placement = {...current.placement};
      delete placement[slot];
      return {...current, placement};
    });
  }

  function startDrag(event) {
    if (event.button !== 0 || !event.isPrimary) return;
    const active = event.currentTarget.dataset.slot;
    const bounds = preview.current.getBoundingClientRect();
    const point = pointerPosition(event, bounds);
    const original = poseFor(draft, active);
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = {id: event.pointerId, active, bounds, point, original};
    setSlot(active);
  }

  function moveDrag(event) {
    const drag = gesture.current;
    if (!drag || drag.id !== event.pointerId) return;
    const {active, bounds, point, original} = drag;
    const now = pointerPosition(event, bounds);
    if (active === "hat") {
      const arc = Math.round(original.arc + angleFromTop(now) - angleFromTop(point));
      const lift = Math.round(original.lift + Math.hypot(now.x, now.y) - Math.hypot(point.x, point.y));
      changePose(active, {arc: clampArc(arc), lift: clampHatLift(lift)});
    } else {
      changePose(active, {
        x: Math.round(original.x + now.x - point.x),
        y: Math.round(original.y + now.y - point.y),
      });
    }
  }

  function endDrag(event) {
    if (gesture.current?.id === event.pointerId) gesture.current = null;
  }

  function submit(event) {
    event.preventDefault();
    if (!changed) return;
    setSaveError("");
    run(async () => {
      const result = await api("/api/cosmetics", draft);
      updateCosmetics(result.cosmetics);
      close();
    });
  }

  return <form className="cosmetics-editor" onSubmit={submit}>
    <div className="cosmetic-editor-body">
      <div className="cosmetic-stage" data-editing={slot} data-outfit={draft.outfit ? "true" : undefined}>
        <div className="cosmetic-orbit" aria-hidden="true"/>
        <Avatar name={player.name} image={player.avatar} cosmetics={draft}
          className="avatar cosmetic-preview-avatar" avatarRef={preview} activeSlot={slot}
          dragHandlers={{onPointerDown: startDrag, onPointerMove: moveDrag, onPointerUp: endDrag,
            onPointerCancel: endDrag, onLostPointerCapture: endDrag}}/>
      </div>
      <div className="cosmetic-tabs" role="group" aria-label="Категория украшений">
        {Object.entries(slots).map(([key, option]) => <button key={key} type="button" className="secondary"
          aria-pressed={slot === key} onClick={() => setSlot(key)}>{option.title}</button>)}
      </div>
      {slot !== "outfit" && !!draft[slot] && <PlacementControls slot={slot} pose={pose}
        change={(values) => changePose(slot, values)} reset={resetPose}/>}
      <Status className="field-hint" error={error} message={error || (!data ? "Загружаем украшения…" : "")}/>
      {data?.[slot] && <CosmeticOptions key={slot} slot={slot} items={data[slot]} value={draft[slot]} onChange={choose}/>}
    </div>
    <div className="cosmetic-editor-footer">
      <Status className="field-hint" error={saveError} message={saveError}/>
      <div className="dialog-buttons">
        <button type="button" className="quiet" disabled={busy} onClick={close}>Назад к профилю</button>
        <button type="submit" className="primary" disabled={!data || !changed || busy}>Сохранить</button>
      </div>
    </div>
  </form>;
}
