"use client";

import { useEffect, useRef, useState } from "react";
import type { MessageEntry } from "@/lib/types";

interface ChatWindowProps {
  messages: MessageEntry[];
  activePeerUsername: string | null;
  onSendMessage: (body: string) => void;
  onSendCode: (code: string, language: string) => void;
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ChatWindow({
  messages,
  activePeerUsername,
  onSendMessage,
  onSendCode,
}: ChatWindowProps) {
  const [input, setInput] = useState("");
  const [isCodeMode, setIsCodeMode] = useState(false);
  const [codeLanguage, setCodeLanguage] = useState("javascript");
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Focus input when peer changes
  useEffect(() => {
    inputRef.current?.focus();
  }, [activePeerUsername]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;

    if (isCodeMode) {
      onSendCode(trimmed, codeLanguage);
    } else {
      onSendMessage(trimmed);
    }
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  // ── No peer selected ──
  if (!activePeerUsername) {
    return (
      <main className="flex-1 flex items-center justify-center bg-[#111218]">
        <div className="text-center max-w-sm">
          <div className="w-20 h-20 mx-auto mb-6 rounded-3xl bg-gradient-to-br from-violet-500/10 to-indigo-500/10 border border-white/[0.04] flex items-center justify-center">
            <svg className="w-10 h-10 text-violet-400/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-zinc-300 mb-2">
            Select a peer to start chatting
          </h2>
          <p className="text-sm text-zinc-600">
            Choose someone from the sidebar to begin an encrypted conversation.
          </p>
        </div>
      </main>
    );
  }

  const languages = [
    "javascript", "typescript", "python", "rust", "go", "java",
    "c", "cpp", "html", "css", "json", "bash", "sql", "plaintext",
  ];

  return (
    <main className="flex-1 flex flex-col bg-[#111218] min-w-0">
      {/* ── Chat Header ───────────────────────────────────── */}
      <header className="h-16 shrink-0 flex items-center justify-between px-6 border-b border-white/[0.06] bg-[#111218]/80 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-violet-500/20 to-indigo-500/20 border border-violet-500/20 flex items-center justify-center text-violet-300 font-semibold text-sm">
            {activePeerUsername.charAt(0).toUpperCase()}
          </div>
          <div>
            <h1 className="text-sm font-semibold text-white">{activePeerUsername}</h1>
            <p className="text-[11px] text-emerald-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
              Online • End-to-End Encrypted
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <span className="text-[11px] text-zinc-600 bg-white/[0.03] rounded-md px-2.5 py-1 font-mono">
            🔒 E2EE
          </span>
        </div>
      </header>

      {/* ── Messages ──────────────────────────────────────── */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-6 py-4 space-y-1 scrollbar-thin"
      >
        {messages.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-zinc-600">
              No messages yet. Say hello! 👋
            </p>
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.self ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[70%] rounded-2xl px-4 py-2.5 ${
                  msg.self
                    ? "bg-violet-600/90 text-white rounded-br-md"
                    : "bg-white/[0.06] text-zinc-200 rounded-bl-md"
                }`}
              >
                {!msg.self && (
                  <p className="text-[11px] font-medium text-violet-400 mb-0.5">
                    {msg.from_user}
                  </p>
                )}

                {msg.type === "code_snippet" ? (
                  <div className="mt-1">
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[10px] uppercase tracking-wider text-zinc-400 font-mono">
                        {msg.language}
                      </span>
                    </div>
                    <pre className="bg-black/30 rounded-lg p-3 overflow-x-auto text-[13px] font-mono leading-relaxed text-emerald-300">
                      <code>{msg.code}</code>
                    </pre>
                  </div>
                ) : (
                  <p className="text-[14px] leading-relaxed break-words whitespace-pre-wrap">
                    {msg.body}
                  </p>
                )}

                <p
                  className={`text-[10px] mt-1 ${
                    msg.self ? "text-violet-200/50" : "text-zinc-600"
                  }`}
                >
                  {formatTime(msg.ts)}
                </p>
              </div>
            </div>
          ))
        )}
      </div>

      {/* ── Input Area ────────────────────────────────────── */}
      <form
        onSubmit={handleSubmit}
        className="shrink-0 border-t border-white/[0.06] p-4 bg-[#111218]"
      >
        {/* Code mode controls */}
        {isCodeMode && (
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[11px] text-zinc-500">Language:</span>
            <select
              value={codeLanguage}
              onChange={(e) => setCodeLanguage(e.target.value)}
              className="text-xs bg-white/[0.05] border border-white/[0.08] rounded-md px-2 py-1 text-zinc-300 outline-none focus:border-violet-500/50"
            >
              {languages.map((lang) => (
                <option key={lang} value={lang}>
                  {lang}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="flex items-end gap-3">
          {/* Code mode toggle */}
          <button
            type="button"
            onClick={() => setIsCodeMode(!isCodeMode)}
            className={`shrink-0 w-9 h-9 rounded-lg flex items-center justify-center transition-colors ${
              isCodeMode
                ? "bg-violet-500/20 text-violet-400 border border-violet-500/30"
                : "bg-white/[0.04] text-zinc-500 hover:text-zinc-300 border border-transparent"
            }`}
            title="Toggle code mode"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75L22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3l-4.5 16.5" />
            </svg>
          </button>

          {/* Input */}
          <div className="flex-1 relative">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                isCodeMode
                  ? "Paste your code here…"
                  : `Message ${activePeerUsername}…`
              }
              rows={isCodeMode ? 4 : 1}
              className={`w-full resize-none bg-white/[0.04] border border-white/[0.08] rounded-xl px-4 py-2.5 text-sm text-zinc-200 placeholder-zinc-600 outline-none focus:border-violet-500/40 focus:ring-1 focus:ring-violet-500/20 transition-all ${
                isCodeMode ? "font-mono text-[13px]" : ""
              }`}
            />
          </div>

          {/* Send button */}
          <button
            type="submit"
            disabled={!input.trim()}
            className="shrink-0 w-9 h-9 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:bg-white/[0.04] disabled:text-zinc-600 text-white flex items-center justify-center transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
            </svg>
          </button>
        </div>
      </form>
    </main>
  );
}
