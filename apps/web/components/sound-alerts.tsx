"use client";

import { Tooltip } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { apiRequest } from "@/lib/client-api";

const POLL_MS = 5000;
const KEY = "ae_sound_alerts";

type Activity = {
  now: string;
  signals: { id: string; tip: string; date: string }[];
  trades: {
    id: number;
    event: string;
    strategy: string;
    contract: string | null;
    side: string | null;
    ts: string;
  }[];
};

/** A short tone sequence: [frequency Hz, start s, length s][]. Web Audio, so no sound files to load. */
export function play(ctx: AudioContext, notes: [number, number, number][]) {
  const t0 = ctx.currentTime;
  for (const [freq, at, len] of notes) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t0 + at);
    gain.gain.exponentialRampToValueAtTime(0.25, t0 + at + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + len);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t0 + at);
    osc.stop(t0 + at + len + 0.05);
  }
}

export const TIP_SOUND: [number, number, number][] = [
  [660, 0, 0.18],
  [880, 0.18, 0.18],
  [1320, 0.36, 0.3],
]; // rising chime: a Telegram tip arrived
export const ENTRY_SOUND: [number, number, number][] = [
  [988, 0, 0.12],
  [988, 0.16, 0.25],
]; // double beep: a position opened
export const EXIT_SOUND: [number, number, number][] = [
  [784, 0, 0.15],
  [523, 0.17, 0.3],
]; // falling two-tone: a position closed

/** Plays a sound when a Telegram tip arrives or a strategy opens or closes a position (polls the API while any page
 * of the app is open). Browsers only allow sound after a click on the page, so the first click anywhere enables it. */
export function SoundAlerts() {
  const { getToken } = useAuth();
  const token = useRef(getToken);
  const [on, setOn] = useState(true);
  const [missed, setMissed] = useState(0); // alerts that could not sound: the page has not been clicked yet
  const ctx = useRef<AudioContext | null>(null);
  const since = useRef<string | null>(null);
  const opened = useRef<number | null>(null); // the server's time when the page started listening
  const heard = useRef(new Set<string>()); // what was already played (the API looks back a little: no repeats)

  useEffect(() => {
    const remembered = setTimeout(() => {
      try {
        setOn(localStorage.getItem(KEY) !== "off");
      } catch {
        /* private window: keep the default */
      }
    }, 0);
    const unlock = () => {
      if (!ctx.current) ctx.current = new AudioContext();
      void ctx.current.resume();
      setMissed(0);
    };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      clearTimeout(remembered);
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const q = since.current ? `?since=${encodeURIComponent(since.current)}` : "";
        const a = await apiRequest<Activity>("GET", `/v1/me/activity${q}`, undefined, token.current);
        if (!alive) return;
        since.current = a.now;
        opened.current ??= Date.parse(a.now) - 5000;
        const fresh = <T extends { id: string | number }>(items: T[], at: (x: T) => string) =>
          items.filter((x) => {
            const id = String(x.id);
            if (heard.current.has(id) || Date.parse(at(x)) < (opened.current ?? 0)) return false;
            heard.current.add(id);
            return true;
          });
        const tips = fresh(a.signals, (x) => x.date);
        const trades = fresh(a.trades, (x) => x.ts);
        const audio = ctx.current;
        if (!on) return;
        if (!audio || audio.state !== "running") {
          if (tips.length || trades.length) setMissed((n) => n + tips.length + trades.length);
          return;
        }
        if (tips.length) play(audio, TIP_SOUND);
        const entries = trades.filter((t) => t.event === "entry").length;
        const exits = trades.length - entries;
        const delay = tips.length ? 900 : 0;
        if (entries) setTimeout(() => play(audio, ENTRY_SOUND), delay);
        if (exits) setTimeout(() => play(audio, EXIT_SOUND), delay + (entries ? 600 : 0));
      } catch {
        /* the API is restarting or offline: try again on the next tick */
      }
    };
    void tick();
    const t = setInterval(() => void tick(), POLL_MS);
    // a background tab is checked about once a minute at most: check at once when it comes back to the front
    const back = () => document.visibilityState === "visible" && void tick();
    document.addEventListener("visibilitychange", back);
    return () => {
      alive = false;
      clearInterval(t);
      document.removeEventListener("visibilitychange", back);
    };
  }, [on]);

  function toggle() {
    const next = !on;
    setOn(next);
    try {
      localStorage.setItem(KEY, next ? "on" : "off");
    } catch {
      /* not remembered: fine */
    }
    if (next) {
      if (!ctx.current) ctx.current = new AudioContext();
      void ctx.current.resume().then(() => ctx.current && play(ctx.current, ENTRY_SOUND));
    }
  }

  return (
    <Tooltip
      label={
        missed > 0
          ? `${missed} alert(s) could not sound: click anywhere on the page to allow sound`
          : on
            ? "Sounds on: tips and trades (click to mute)"
            : "Sounds off (click to turn on)"
      }
    >
      <button
        type="button"
        onClick={toggle}
        aria-label={on ? "Mute tip and trade sounds" : "Turn on tip and trade sounds"}
        aria-pressed={on}
        className="relative rounded-lg p-2 text-muted hover:bg-surface-2"
      >
        {on ? (
          <Volume2 className="size-[18px]" aria-hidden />
        ) : (
          <VolumeX className="size-[18px]" aria-hidden />
        )}
        {missed > 0 && (
          <span className="absolute -top-0.5 -right-0.5 rounded-full bg-loss px-1 text-[10px] font-semibold text-white">
            {missed}
          </span>
        )}
      </button>
    </Tooltip>
  );
}
