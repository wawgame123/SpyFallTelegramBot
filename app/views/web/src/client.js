import {useEffect, useState} from "react";

let miniSession = "";

export function setMiniSession(session) {
  miniSession = session;
}

export function authHeaders() {
  return miniSession ? {Authorization: "Bearer " + miniSession} : {};
}

export async function api(path, payload, signal) {
  const headers = authHeaders();
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    headers, signal, credentials: "same-origin", method: payload === undefined ? "GET" : "POST",
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "Запрос не выполнен");
    error.password = data.password;
    throw error;
  }
  return data;
}

export function useResource(path, {poll = 0, version = 0} = {}) {
  const [result, setResult] = useState(null);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let request = 0;
    async function load(background = false) {
      if (background && document.hidden) return;
      const sequence = ++request;
      try {
        const data = await api(path, undefined, controller.signal);
        if (!controller.signal.aborted && sequence === request) setResult({path, version, data});
      } catch (error) {
        if (!controller.signal.aborted && sequence === request && !background) setResult({path, version, error: error.message});
      }
    }
    load();
    const refresh = () => load(true);
    const interval = poll ? setInterval(refresh, poll) : null;
    if (poll) document.addEventListener("visibilitychange", refresh);
    return () => {
      controller.abort();
      if (interval) clearInterval(interval);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [path, poll, version]);
  return result?.path === path && result?.version === version ? result : {};
}

export async function telegramClient() {
  if (window.Telegram?.WebApp) return window.Telegram.WebApp;
  if (!/tgWebApp(?:Data|Version)=/.test(location.hash)) return null;
  await new Promise((resolve) => {
    const sdk = document.createElement("script");
    const timeout = setTimeout(resolve, 3500);
    sdk.src = "https://telegram.org/js/telegram-web-app.js";
    sdk.onload = sdk.onerror = () => { clearTimeout(timeout); resolve(); };
    document.head.append(sdk);
  });
  return window.Telegram?.WebApp;
}

export async function watchRoom(signal, refresh, notice) {
  let retries = 0;
  while (!signal.aborted) {
    try {
      const response = await fetch("/api/room/events", {signal, headers: authHeaders()});
      if (response.status === 401) {
        notice("Сеанс завершился. Войдите снова.", true);
        return;
      }
      if (response.status === 404) {
        await refresh();
        return;
      }
      if (!response.ok || !response.body) throw new Error("Связь прервалась");
      retries = 0;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        while (!signal.aborted) {
          const {done, value} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          const newline = buffer.lastIndexOf("\n\n");
          if (newline !== -1) {
            const events = buffer.slice(0, newline);
            buffer = buffer.slice(newline + 2);
            if (events.includes("data: change")) await refresh();
          }
        }
      } finally {
        reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    } catch {
      if (!signal.aborted) notice("Связь прервалась. Переподключаемся…");
    }
    if (!signal.aborted) {
      retries++;
      await new Promise((resolve) => {
        const timer = setTimeout(() => {
          signal.removeEventListener("abort", stop);
          resolve();
        }, Math.min(10000, retries * 1000));
        function stop() { clearTimeout(timer); resolve(); }
        signal.addEventListener("abort", stop, {once: true});
      });
    }
  }
}
