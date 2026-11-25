import {useEffect, useLayoutEffect, useRef, useState} from "react";

const key = "spyfall-theme";
const modes = [["system", "Как в системе"], ["light", "Светлая"], ["dark", "Тёмная"]];
const media = matchMedia("(prefers-color-scheme: light)");
const normalize = (value) => value === "light" || value === "dark" ? value : "system";

export function Theme({telegram}) {
  const [mode, setMode] = useState(() => {
    try { return normalize(localStorage.getItem(key)); } catch { return "system"; }
  });
  const [open, setOpen] = useState(false);
  const container = useRef(null);
  const trigger = useRef(null);
  const menu = useRef(null);
  const options = useRef([]);
  const selected = modes.findIndex(([value]) => value === mode);

  useLayoutEffect(() => {
    function apply() {
      const system = telegram?.initData ? telegram.colorScheme : media.matches ? "light" : "dark";
      const theme = mode === "system" ? system : mode;
      document.documentElement.dataset.theme = theme;
      document.querySelector('meta[name="theme-color"]').content = theme === "light" ? "#e9f8f0" : "#0c111b";
    }
    apply();
    media.addEventListener("change", apply);
    telegram?.onEvent?.("themeChanged", apply);
    return () => {
      media.removeEventListener("change", apply);
      telegram?.offEvent?.("themeChanged", apply);
    };
  }, [mode, telegram]);

  useEffect(() => {
    function sync(event) { if (event.key === key || event.key === null) setMode(normalize(event.newValue)); }
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    function position() {
      const anchor = trigger.current.getBoundingClientRect();
      menu.current.dataset.align = anchor.right - menu.current.offsetWidth < 8 ? "start" : "end";
    }
    position();
    options.current[selected]?.focus();
    window.addEventListener("resize", position);
    return () => window.removeEventListener("resize", position);
  }, [open, selected]);

  useEffect(() => {
    if (!open) return;
    function outside(event) {
      if (!container.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
    };
  }, [open]);

  function close() {
    setOpen(false);
    trigger.current.focus();
  }

  function select(value) {
    setMode(value);
    try { localStorage.setItem(key, value); } catch {}
    close();
  }

  function onKey(event) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      close();
      return;
    }
    const index = options.current.indexOf(document.activeElement);
    const last = modes.length - 1;
    const target = {ArrowDown: (index + 1) % modes.length,
      ArrowUp: index <= 0 ? last : index - 1, Home: 0, End: last}[event.key];
    if (target === undefined) return;
    event.preventDefault();
    if (open) options.current[target]?.focus();
    else setOpen(true);
  }

  return <div ref={container} className="theme-choice" onKeyDown={onKey}>
    <button ref={trigger} id="theme-mode" type="button" className="theme-trigger" aria-label="Тема оформления"
      title="Тема оформления" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? "theme-menu" : undefined}
      onClick={() => setOpen(!open)}>
      <svg className="theme-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
        <path d="M20.8 14.7A9 9 0 0 1 9.3 3.2 9 9 0 1 0 20.8 14.7Z"/>
      </svg>
      <svg className="theme-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
        <circle cx="12" cy="12" r="4"/>
        <path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4m0-14.2-1.4 1.4M6.3 17.7l-1.4 1.4"/>
      </svg>
    </button>
    {open && <div id="theme-menu" ref={menu} className="theme-menu" role="menu" aria-label="Тема оформления">
      {modes.map(([value, label], index) => <button key={value} ref={(button) => { options.current[index] = button; }}
        type="button" className="theme-option" role="menuitemradio" aria-checked={mode === value}
        tabIndex={-1} onClick={() => select(value)}>
        <span>{label}</span><span aria-hidden="true">{mode === value ? "✓" : ""}</span>
      </button>)}
    </div>}
  </div>;
}
