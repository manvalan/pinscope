"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { Input } from "@/components/ui/input";
import type { Collaborator } from "@/lib/types";
import { cn } from "@/lib/utils";

interface MentionInputProps {
  value: string;
  onChange: (value: string) => void;
  onMention: (userId: string) => void;
  collaborators: Collaborator[];
  placeholder?: string;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  className?: string;
}

export function MentionInput({
  value,
  onChange,
  onMention,
  collaborators,
  placeholder,
  onKeyDown: externalKeyDown,
  className,
}: MentionInputProps) {
  const [showDropdown, setShowDropdown] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [mentionStart, setMentionStart] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const filtered = collaborators.filter((c) => {
    const q = query.toLowerCase();
    return (
      (c.name && c.name.toLowerCase().includes(q)) ||
      (c.email && c.email.toLowerCase().includes(q))
    );
  });

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newValue = e.target.value;
      onChange(newValue);

      const cursor = e.target.selectionStart ?? newValue.length;
      // Find the last @ before cursor that isn't preceded by a non-space char
      const before = newValue.slice(0, cursor);
      const atIndex = before.lastIndexOf("@");
      if (atIndex >= 0 && (atIndex === 0 || before[atIndex - 1] === " ")) {
        const partial = before.slice(atIndex + 1);
        if (!partial.includes(" ")) {
          setMentionStart(atIndex);
          setQuery(partial);
          setShowDropdown(true);
          setActiveIndex(0);
          return;
        }
      }
      setShowDropdown(false);
    },
    [onChange],
  );

  const selectCollaborator = useCallback(
    (c: Collaborator) => {
      const name = c.name || c.email || "user";
      const before = value.slice(0, mentionStart);
      const after = value.slice(
        mentionStart + 1 + query.length,
      );
      onChange(`${before}@${name}${after ? after : " "}`);
      onMention(c.user_id);
      setShowDropdown(false);
      inputRef.current?.focus();
    },
    [value, mentionStart, query, onChange, onMention],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (showDropdown && filtered.length > 0) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setActiveIndex((i) => (i + 1) % filtered.length);
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          setActiveIndex((i) => (i - 1 + filtered.length) % filtered.length);
          return;
        }
        if (e.key === "Enter") {
          e.preventDefault();
          selectCollaborator(filtered[activeIndex]);
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          setShowDropdown(false);
          return;
        }
      }
      externalKeyDown?.(e);
    },
    [showDropdown, filtered, activeIndex, selectCollaborator, externalKeyDown],
  );

  // Close dropdown on outside click
  useEffect(() => {
    if (!showDropdown) return;
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showDropdown]);

  return (
    <div className={cn("relative", className)}>
      <Input
        ref={inputRef}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className="text-xs"
      />
      {showDropdown && filtered.length > 0 && (
        <div
          ref={dropdownRef}
          className="absolute bottom-full left-0 mb-1 w-64 rounded-lg border border-border bg-popover p-1 shadow-md z-50"
        >
          {filtered.map((c, i) => (
            <button
              key={c.user_id}
              type="button"
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs cursor-pointer",
                i === activeIndex
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent/50",
              )}
              onMouseDown={(e) => {
                e.preventDefault();
                selectCollaborator(c);
              }}
              onMouseEnter={() => setActiveIndex(i)}
            >
              {c.image_url ? (
                <img
                  src={c.image_url}
                  alt=""
                  className="h-5 w-5 rounded-full"
                />
              ) : (
                <div className="h-5 w-5 rounded-full bg-muted flex items-center justify-center text-[10px] font-medium">
                  {(c.name || c.email || "?")[0].toUpperCase()}
                </div>
              )}
              <div className="min-w-0">
                {c.name && (
                  <span className="font-medium">{c.name}</span>
                )}
                {c.email && (
                  <span className="text-muted-foreground ml-1">
                    {c.email}
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
