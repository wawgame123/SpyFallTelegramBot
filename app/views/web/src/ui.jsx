import {createContext, useContext, useEffect, useRef, useState} from "react";
import {accessoryGeometry, poseFor} from "./avatar-placement";

export const SiteContext = createContext(null);
export function useSite() { return useContext(SiteContext); }

export function useTask(onError) {
  const {notice} = useSite();
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  async function run(work) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    try {
      await work();
    } catch (error) {
      if (onError) onError(error.message);
      else notice(error.message, true);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return [busy, run];
}

export function Action({work, disabled, children, ...props}) {
  const [busy, run] = useTask();
  return <button type="button" {...props} disabled={disabled || busy} onClick={() => run(work)}>{children}</button>;
}

export function Status({id, message, error, className = "empty"}) {
  return <p id={id} className={className + (error ? " error" : "")} role="status" hidden={!message}>{message}</p>;
}

export function Dialog({id, label, onClose, children, className}) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return <dialog id={id} ref={ref} aria-labelledby={label} className={className}
    onCancel={(event) => { event.preventDefault(); onClose(); }}>{children}</dialog>;
}

export function accessoryUrl(id) {
  return typeof id === "string" && /^[a-z][a-z0-9-]*$/.test(id)
    ? import.meta.env.BASE_URL + "cosmetics/" + id + ".webp" : null;
}

export function Avatar({name = "", initial, image, cosmetics, lazy, className = "avatar",
  avatarRef, activeSlot, dragHandlers}) {
  const [loaded, setLoaded] = useState(null);
  const [failed, setFailed] = useState(null);
  const hat = cosmetics?.hat && accessoryGeometry("hat", poseFor(cosmetics, "hat"));
  const face = cosmetics?.face && accessoryGeometry("face", poseFor(cosmetics, "face"));
  const outfit = accessoryUrl(cosmetics?.outfit);

  function layer(slot, geometry) {
    const url = accessoryUrl(cosmetics?.[slot]);
    if (!url || !geometry) return null;
    return <span className="avatar-decoration" data-slot={slot} style={geometry.style}
      data-active={dragHandlers && activeSlot === slot ? "true" : undefined} {...dragHandlers}>
      <img src={url} alt="" draggable={false} className={"avatar-accessory avatar-" + slot}
        loading={lazy ? "lazy" : undefined}/>
    </span>;
  }

  return <span ref={avatarRef} className={className} style={hat?.reserve} data-outfit={outfit ? "true" : undefined} aria-hidden="true">
    {outfit && <img src={outfit} alt="" className="avatar-outfit" draggable={false} loading={lazy ? "lazy" : undefined}/>}
    <span className="avatar-portrait">
      {initial || Array.from(name.trim())[0]?.toLocaleUpperCase("ru") || "?"}
      {image && failed !== image && <img src={image} alt="" className={"avatar-photo" + (loaded === image ? " avatar-loaded" : "")}
        loading={lazy ? "lazy" : undefined} onLoad={() => setLoaded(image)} onError={() => setFailed(image)}/>}
    </span>
    {face && <span className="avatar-face-frame">{layer("face", face)}</span>}
    {layer("hat", hat)}
  </span>;
}

export function Contact({name, reference, children, className = "friend-name", ...props}) {
  const {openFriend} = useSite();
  return reference ? <button type="button" className={className} {...props} onClick={() => openFriend(reference)}>
    {children ?? name}
  </button> : <>{children ?? <strong>{name}</strong>}</>;
}
